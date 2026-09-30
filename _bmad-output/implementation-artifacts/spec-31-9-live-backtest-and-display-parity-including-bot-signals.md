---
title: 'Story 31.9: Live, backtest and display parity, including the bot''s own signals'
type: 'feature'
created: '2026-09-30'
status: done
baseline_revision: '9674e77797'
final_revision: 'bf127c9c19'
operator_actions:
  - "Decide D-129: should DummyStrategy gate its book (stale/crossed/gap) and clear its MultiLevelOFI state on a gap longer than kernel.indicators.OFI_GAP_NS, like SnapshotStrategy/OFIStrategy/ranking? Recommended: adopt the gate and reset. Otherwise keep the recorded Known limit. The measured divergence is in docs/VERIFICATION_REPORT.md's parity row."
  - "Decide D-133/D-134: the Nautilus Bybit data client builds quotes from the book message's first entries (not the best level) when the depth-50 book is also subscribed, and replays the spot depth-1 stream into the book (FORK-01: not fixable here). Recommended: upgrade Nautilus to a release that keeps quotes and books on their own topics. Alternatives: run Bybit bots without the book subscription, or exclude them from evaluation. Until then no Bybit paper-bot result is trustworthy."
  - "Decide D-135: fix the bot replay's derived deltas, which the catalog query reorders because they share one ts_init. Changing this means changing Story 31.9's conversion contract. Recommended: stamp the i-th delta of a row at ts_init + i ns. Then re-run python3 -m bots.signal_replay and python3 -m verification.bot_parity for both venues."
  - "Decide D-113: upgrade Nautilus to a release that decodes IndexPriceUpdate, or accept that backtests cannot read index prices as a documented Known limit. Story 31.11 records the verdict."
  - "Before Story 31.11, rebuild only the verify ranking_engine and data_api images (--no-deps) and re-run the SSOT live test (VERIFY_STACK=1). Then drop the test's slow_loop_reads_its_clock_first row. At the next VPS deploy, rebuild ranking_engine and data_api (D-130, D-131). See DEPLOY_CHECKLIST.md entry 31-9."
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
  - '{project-root}/platform/docs/DATA_DICTIONARY.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Nothing proves that the value a screen shows, the value a bot acts on and the value a backtest computes are the same. Planning found these gaps:
- The display chain has never been traced value by value for one second. That chain is catalog row → `snapshots:raw` → `RankingBoard` → `rankings:live` → `metrics.db` → the `data_api` response. Along it, integers turn into floats, `metrics.db` is keyed by wall clock, and `/api/snapshots` recomputes `mid` inline.
- `DummyStrategy` reads the full, ungated live book once a second. It logs no signal inputs or outputs, has no gap handling (31.3 deferred that decision here, D-82), and cannot be backtested at all, because Bybit and Hyperliquid store no `OrderBookDelta`s, only 1 s top-20 snapshots.
- `OFIStrategy` keeps no z-score series, so its backtest cannot be compared with `ofi_replay` or the 31.3 reference.

**Approach:** Three proofs, one per AC.
1. A recorded-fixture SSOT trace (in `make test`) plus a live variant for the verify stack.
2. An opt-in per-cycle signal log in `DummyStrategy`, a Bybit + Hyperliquid paper fleet on the verify stack, a catalog replay of the same strategy through `BacktestNode`, and an oracle-side comparator (`python3 -m verification.bot_parity`). The comparator quantifies and classifies every divergence.
3. An `OFIStrategy` backtest whose recorded z-score series is compared with `ofi_readings`/`ofi_replay` and the 31.3 reference.

The gating decision is the operator's, so it is recorded as a `Known limit:` and handed over as an operator action.

## Boundaries & Constraints

**Always:**
- **Signal log (bots):**
  - `DummyStrategyConfig` gains `signal_log_path: str | None = None`. When it is `None`, behaviour and output are exactly as today.
  - When it is set, the strategy appends one JSON line per decision cycle through a small writer in `bots/strategies/signal_log.py`, which opens the file in append mode and closes it `on_stop`. A decision cycle is every 1 s timer event and every bar.
  - Record fields:
    - `kind`: `start` (one per `on_start`, carrying `ts_ns` = the clock at start and the config's thresholds, levels and windows), `book`, `book_skipped` (with a reason: `no_book` or `one_sided`, so a skipped second is visible, DATA-07) or `bar`;
    - `bot_id`, `instrument_id`, `ts_ns` (the event's `ts_event`);
    - `book_ts_ns` (`book.ts_last`);
    - `bids`/`asks`: the top `max(ofi_levels, obi_levels)` levels exactly as the indicators were fed, as `[price, size]` floats;
    - `bar`: `{ts_event, close}` for a bar cycle;
    - `microprice`, `ofi`, `obi`, `mlofi`, `trend`: the value, or `null` while not initialized;
    - `signal`: `long`, `short`, `none` or `not_ready`, a pure function of trend, mlofi and the thresholds;
    - `action`: the side of the order submitted this cycle, or `null`.
  - The host sets the path from env `BOT_SIGNAL_LOG_DIR` to `<dir>/<bot_id>.jsonl` for dummy bots only. `BotConfig` and its TOML stay unchanged.
  - Known limit: opt-in, with one file per bot growing for the run (about 1 KB per cycle). Upgrade path: hourly rotation.
- **Verify fleet:**
  - `bots/config.verify.toml`: one dummy bot per verify instrument (`BTCUSDT-LINEAR`, `ETHUSDT-LINEAR`, `BTCUSDT-SPOT`, `ETHUSDT-SPOT` on `BYBIT`, and `SOL-USD-PERP.HYPERLIQUID`), mainnet data, Sandbox execution only.
    - If a spot bot is refused by the existing rules, keep the instruments that load and record the refusal in the report.
  - `docker-compose.verify.yml` `live-paper`:
    - mounts that file over `/app/bots/config.toml`;
    - sets `BOT_SIGNAL_LOG_DIR=/app/verify_data/bot_signals/live`;
    - mounts `./data/verification:/app/verify_data`.
  - `live-paper` joins `VERIFY_SERVICES` in the `Makefile`. `tests/test_compose_verify.py` pins all of it.
- **Replay (backtest) side:**
  - New root `python3 -m bots.signal_replay --config F --catalog P --live-log DIR --out DIR [--start ISO --end ISO]`.
  - For each dummy bot in F, it runs a `BacktestNode` (NAUT-01..03) over the live run's window. `start` is the live log's latest `start` record `ts_ns` exactly, so the 1 s timer fires on the same nanoseconds; `end` is its last record. It uses:
    - the catalog's instrument and `TradeTick`s (for the LAST-INTERNAL trend bars);
    - one `OrderBookDeltas` per stored snapshot row (CLEAR + every stored level, `F_SNAPSHOT`/`F_LAST`), written to a throwaway catalog, bounded reads (MEM-01);
    - one `QuoteTick` per row from the row's top of book.
  - Both derived types are stamped `ts_event = ts_init =` the row's `ts_init`, which is the project's backtest clock ("backtests replay on it", DATA_DICTIONARY §1.7).
  - The strategy is `DummyStrategy` with the same thresholds, loaded by string path, with `signal_log_path=<out>/<bot_id>.jsonl`. The venue uses the same starting balances.
  - The snapshot → deltas/quote conversion is one pure module, `kernel/snapshot_book.py`. Research's per-row quote derivation (`research/application/quotes.py`) moves there and research imports it: never a second copy (SSOT-01). Bots never imports research (`test_boundaries.py`).
- **Comparator (oracle side, DATA-02):**
  - New root `python3 -m verification.bot_parity --venue V --live-dir D --replay-dir D [--catalog] [--json]`. It is stdlib only plus existing verification infrastructure (`read_window`, `CoverageFiles`, `RefBook.from_stored`), imports no `bots`/`nautilus_trader`, and joins `VERIFICATION_ROOTS`, the runtime probe and DATA-02's tool list.
  - It compares the latest run segment of each bot. Records are paired by equal `ts_ns`; a cycle present on one side only is `live_only` or `replay_only`.
  - For each signal (`microprice`, `ofi`, `obi`, `mlofi`, `trend`) it reports paired count, exact-equal share and max abs difference. For `signal` it reports decision disagreements, and for `action` it reports action disagreements, which are informational (fills differ by construction).
  - Every non-equal paired cycle gets exactly one class, first match wins:
    1. `gap`: a stored row is missing in `[T-3 s, T)`. The coverage reason is carried; a missing second without a reason is `unexplained`.
    2. `book_timing`: the live top-N equals a stored row's top-N at some second in `[T-5 s, T+1 s]`.
    3. `book_source`: the live top-N equals no stored row in that window (full live book vs gated top-20 snapshot).
    4. `quote_cadence`: for `microprice` and `ofi` only, since the live side updates on every quote and the replay once a second.
    5. `bar_source`: for `trend`, the bars' `{ts_event, close}` differ.
    6. `carried_state`: the inputs are equal this cycle but differed within the indicator's memory (`ofi_window` + 1 cycles for mlofi, `trend_lookback` bars for trend).
    7. `unexplained`, which fails.
  - A decision disagreement is attributed to the class of the diverging input, or is `unexplained` when trend and mlofi are both equal.
  - `live_only`/`replay_only` are explained by `book_skipped` or a gap, else they are `unexplained`.
  - Exit 0 iff `unexplained` is 0; exit 1 otherwise or on a refusal (ledgered at `verification.bot_parity.refused`, in `sites.py` and `test_sites.py`); exit 2 on a usage error.
- **Gating decision:** `DummyStrategy`'s behaviour (ungated book, no OFI reset on a gap) is unchanged. It is written in `dummy.py` as a `Known limit:` citing the measured divergence and the upgrade path (gate stale/crossed/gap books and `clear_prev_state()` on `OFI_GAP_NS`, like `SnapshotStrategy`). It is an audit row and an operator action.
  - D-113 (the `IndexPriceUpdate` decoder) is likewise an operator action, with the options stated.
  - Both go into DEPLOY_CHECKLIST's "Deferred operator actions" (OPS-01).
- **SSOT trace (AC1):** `verification/tests/test_ssot_trace.py`, with `COMPOSITION_ROOTS` naming exactly the contexts it uses.
  - **Fixture variant:** the real fixture rows (`fixtures/snapshots`, Bybit BTCUSDT-LINEAR and SOL) are fed as `snapshots:raw` JSON through a real `RankingEngine` with in-memory ports and a real tmp `metrics.db`. The engine's `rankings:live` message is served through the real `/api/rankings` and `/api/metrics/history` (TestClient), and the row is served through `/api/snapshots` from a tmp `write_data` catalog.
    - For the traced second, assert per field and per hop: the stored integers equal the payload integers; `price`/`microprice`/`microprice_lean`/`spread`/`obi_*` equal the reference (`reference_signals`, 31.3 tolerances); windowed and stateful fields equal the reference over the rows so far; `metrics.db` equals the board's state at write time; the API equals the store and bus exactly.
    - Every accounted difference is one named row of a table in the test and in DATA_DICTIONARY §1.22: `metrics.db.price` is the trade close, `ts` is wall clock, `rankings:live` carries no `ts_event`, `spread` is rounded, decode is float, and `/api/snapshots` `mid` is recomputed inline. That last one is compared, not assumed.
  - **Live variant:** skipped unless `VERIFY_STACK=1`, via a module-level `skipif` mark (no marker registration, TEST-04).
    - Subscribes to `127.0.0.1:26379` `snapshots:raw` and `rankings:live`.
    - Checks the stateless fields of each `rankings:live` row against the last batch before it.
    - Finds the traced row in `CATALOG_PATH` after the flush (bounded, ≤ 150 s).
    - Checks `/api/rankings` against the latest bus message and the newest `metrics.db` row against the last `rankings:live` at or before its `ts`.
- **OFI parity (AC3):** `verification/tests/test_ofi_parity.py` (`COMPOSITION_ROOTS` `{RESEARCH}`).
  - A recording subclass of `OFIStrategy` defined in the test, run by a real `BacktestNode` over a `write_data` catalog, appends `(ts_event, ofi value, initialized)` after each `on_data`.
  - It must equal `ofi_readings`' `ofi_z` exactly on every non-NaN reading. Baseline rows (NaN in `ofi_readings`) carry the previous value, a pinned, documented class.
  - It must equal the reference `rolling_ofi_z(..., usd=True, ...)` within `signal_compare.REL_TOL`, with `pinned_zero` for undefined.
  - Row order by `ts_init` must equal order by `ts_event`, else the test fails.
  - The fixture variant runs the strategy's default 10/20/300 and a short z-window. The soak variant is skipped unless `VERIFY_SOAK_CATALOG` and `VERIFY_SOAK_DAY` are set, and runs every stored instrument's day.
- **Runs (agent-executed):**
  - Build and start `verify-live-paper` with `--no-deps` (never recreate the running collectors or recorders) and let it run for at least 1 h.
  - Then run `bots.signal_replay` and `verification.bot_parity` for both venues, the SSOT live variant and the OFI soak variant on 2026-09-29.
  - Record the numbers, runtime and peak RSS in `VERIFICATION_REPORT.md` (row "Live, backtest and display parity"). Record findings in the audit from D-129 onwards and the tool in DATA_DICTIONARY §1.22.
  - Root-cause every `unexplained` count, then fix it or register it OPEN with a follow-up.
- Code rules: LGPL header, full typing, functions of about 30 lines or fewer, complexity ≤ 10, no module-level mutable state, no new dependency, and no tolerance beyond 31.3's stated ones.

**Block If:**
- A live run is impossible from this host: no venue connectivity, or the image cannot build. In that case, finish everything else, make the run an operator action and finalize `awaiting-operator`; do not block.
- Replay phase alignment cannot be achieved with `BacktestNode`. In that case, pair nearest-within-500 ms under a `phase_shift` class and document it; do not block.

**Never:**
- Never modify `nautilus_trader/`, `crates/` or `sprint-status.yaml`, and never write the live catalog.
- Never change `DummyStrategy`'s trading behaviour (the operator decides).
- Never import `bots`/`research` into `verification.subject` or the oracle.
- Never restart the verify collectors or recorders.
- Do not build 31.10/31.11 or `verify_day`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| Identical logs | replay log equal to live | all signals 100 % equal; exit 0 |
| Planted output | one mlofi differs, inputs equal throughout memory | `unexplained` 1; exit 1 |
| Book differs, stored elsewhere | live levels equal the row of T-2 s | `book_timing` |
| Book never stored | live levels match no row in window | `book_source` |
| Missing row | no row at T-1 s, coverage `crossed` | `gap` (crossed) |
| Missing row, no reason | no row, no coverage | `unexplained`; exit 1 |
| Skipped live second | live `book_skipped` `one_sided` | `replay_only` explained |
| Signal log off | `signal_log_path` None | no file; trading unchanged (existing tests) |
| Missing live dir | path absent | refused, ledgered; exit 1 |

</intent-contract>

## Code Map

- `platform/bots/strategies/dummy.py:153-341` -- config, `on_start`, `on_quote_tick`, `on_timer`, `on_bar`, `_maybe_trade`/`_wanted_side`: where records are built.
- `platform/bots/infrastructure/nautilus_host.py:345-356` -- `_strategy_for`: set `signal_log_path` from env.
- `platform/bots/config.toml`, `bots/infrastructure/config.py` -- fleet format; `bots/README.md`.
- `platform/docker-compose.verify.yml:71-74`, `docker-compose.yml:364-420`, `Makefile:212-216`, `tests/test_compose_verify.py` -- verify `live-paper`.
- `platform/research/application/quotes.py:31`, `research/application/backtest_runner.py:103-165,308-371` -- quote derivation to move; `BacktestNode` pattern (derived temp catalog, `ts_init` bounds).
- `platform/kernel/second_snapshot.py:441-626`, `kernel/catalog_files.py` -- decoding and bounded queries.
- `platform/research/strategies/ofi_strategy.py:81-144`, `research/application/microstructure.py:300-353` -- AC3 subject and replay.
- `platform/verification/domain/reference_signals.py:161,207,249,316,352`, `domain/signal_compare.py:40-172`, `tests/signal_cases.py:117,312` -- reference and tolerances.
- `platform/ranking/application/engine.py:98-273`, `ranking/domain/board.py:114-519`, `ranking/infrastructure/metrics_store.py`, `ranking/tests/test_replay.py`, `ranking/tests/test_engine.py:69-100` -- the display chain and fakes.
- `platform/data_api/routes/{rankings,metrics,snapshots}.py`, `views/rankings_bus.py:76-99`, `views/chart_series.py:~650` -- served shapes.
- `platform/verification/{candles,trades}.py`, `application/sites.py`, `infrastructure/catalog_reader.py` -- CLI pattern, sites, raw readers.
- `platform/tests/test_boundaries.py:122-202,2002-2170` -- GRAPH, COMPOSITION_ROOTS, verification roots and probe.
- `platform/docs/VERIFICATION_REPORT.md:104`, `docs/DATA_INTEGRITY_AUDIT.md:236 (D-82), :274 (D-113), next id D-129`, `docs/DATA_DICTIONARY.md` (§1.21 last), `docs/DEPLOY_CHECKLIST.md`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/bots/strategies/signal_log.py` (new), `bots/strategies/dummy.py`, `bots/infrastructure/nautilus_host.py`, `bots/tests/test_signal_log.py` -- the opt-in log and the `Known limit:`. Tests: records per kind, `book_skipped`, the pure `signal`, and no file when off.
- [x] `platform/bots/config.verify.toml`, `docker-compose.verify.yml`, `Makefile`, `tests/test_compose_verify.py`, `bots/README.md` -- the verify fleet.
- [x] `platform/kernel/snapshot_book.py` (new), `kernel/tests/test_snapshot_book.py`, `research/application/quotes.py` and its callers -- one conversion for deltas and quotes.
- [x] `platform/bots/signal_replay.py` (new), `bots/tests/test_signal_replay.py` -- the `BacktestNode` replay over a `write_data` catalog. Asserts that replay cycles land on the live `start` + k s.
- [x] `platform/verification/domain/bot_parity.py`, `application/bot_parity.py`, `verification/bot_parity.py` (new), `application/sites.py`, `tests/test_sites.py`, `tests/test_bot_parity.py` (new) -- the comparator, with every I/O row and planted defects.
- [x] `platform/verification/tests/test_ssot_trace.py`, `verification/tests/test_ofi_parity.py` (new) -- AC1 and AC3.
- [x] `platform/tests/test_boundaries.py` -- roots, probe, `COMPOSITION_ROOTS` entries.
- [x] Live run, replay, comparator, SSOT live and OFI soak runs; then `VERIFICATION_REPORT.md`, `DATA_DICTIONARY.md` §1.22, `DATA_INTEGRITY_AUDIT.md` D-129+ (and the D-82/D-113 follow-up cells), `DEPLOY_CHECKLIST.md` deferred operator actions, `platform/CLAUDE.md` DATA-02 tool list.

**Acceptance Criteria:**
- Given the recorded fixture, when `make test`'s verification suite runs, then `test_ssot_trace` proves every field and derived metric equal or accounted along all six hops, and `test_ofi_parity` proves the `OFIStrategy` backtest series equals `ofi_readings` exactly and the reference within `REL_TOL`.
- Given at least 1 h of the verify paper fleet's live log and its replay, when `python3 -m verification.bot_parity` runs per venue, then it prints per bot and signal the exact-equal share, max abs difference, decision disagreements and class counts, and `VERIFICATION_REPORT.md` records them with each class explained.
- Given the story ships, then the spec's `operator_actions` name the gating/OFI-reset decision and the D-113 decision, and the spec's status is `awaiting-operator`.

## Spec Change Log

## Review Triage Log

### 2026-09-30 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 13: (high 3, medium 5, low 5)
- defer: 0
- reject: 7: (high 0, medium 2, low 5)
- addressed_findings:
  - `[high]` `[patch]` A truncated replay or live log passed, because the window silently shrank to the shorter side. A side more than one interval short is now a failing `truncated` count, and the replay refuses a log that does not reach the window end.
  - `[high]` `[patch]` The replay's own fed book was never checked, so a broken conversion passed as `book_source`. Every replay cycle is now checked against the stored row active at T by `ts_init` (the exact `grid_units` rule); a mismatch is a failing `replay_input`. Planted-defect tests: swapped sides, one level short.
  - `[high]` `[patch]` A `book_skipped` on the other side explained a one-sided cycle anywhere in the window. It now explains one only at cold start (before the first row the replay could hold) or inside a coverage-explained gap; a live skip still explains its own T.
  - `[medium]` `[patch]` `quote_cadence` was assigned without evidence. The replay's microprice must now equal the microprice of its own fed top within `REL_TOL`; otherwise the cycle is `unexplained`.
  - `[medium]` `[patch]` `--start` always ended in a refusal. Start records are now compared without `ts_ns`, the replay start must lie on the live grid at or after the live start, and pairing begins at the replay start.
  - `[medium]` `[patch]` A `book_skipped` record logged the previous cycle's `action`; the action is now reset at the top of `on_timer`/`on_bar`.
  - `[medium]` `[patch]` Fixed several ways the replay failed or used the wrong data:
    - it crashed on an unterminated log line;
    - it used the newest instrument definition instead of the one in force at the window start;
    - it raised tracebacks where it should refuse; refusals are now ledgered per bot at `bots.signal_replay.refused`;
    - it could accept an older segment; it now requires a fresh one.
  - `[medium]` `[patch]` The VERIFICATION_REPORT parity row read as a pass while D-133/D-134 were OPEN. It now says the Bybit bot result is a classification, not a parity proof.
  - `[low]` `[patch]` Five smaller fixes:
    - exec bots no longer get a signal log;
    - the verify `live-paper` mounts only `bot_signals/live`, not the reference recorders' directory;
    - a logged depth deeper than the stored rows is refused;
    - another venue's malformed log no longer refuses this venue;
    - the replay's L1_MBP stale-trade fill simulation is documented as a `Known limit:`.
  - Re-run on the same data: every class count is unchanged, but `replay_input: levels` is 1 per bot, which led to the new finding D-135 (the catalog query reorders equal-`ts_init` derived deltas across a row-group boundary). It is registered OPEN with an operator decision, because the fix changes this spec's binding conversion contract.
  - Rejected:
    - decision disagreements inherit their input's class (by spec; they are reported as their own count);
    - `live-paper` in `VERIFY_SERVICES` restarts an unbounded logger (by spec; about 0.4 GB/day, with a documented Known limit and upgrade path);
    - the exits are not in the `start` record (`action` is informational, and the fleet sets no exits);
    - the live SSOT slack is unconditional (documented; removed by a checklist step);
    - seeding the replay with the row before start (cold start is now explicit and bounded);
    - a bar at the same ns as a timer (the grid sits on a µs offset; hypothetical);
    - the gap between the Sandbox and replay fill models (documented Known limit).

## Design Notes

**Why `ts_init` for the replay:** a backtest may know a row only when it could first be known. Stamping derived book data at the exchange close would flatter the backtest. The resulting lag (about 1 + `hold_back` s) is exactly the "timing" divergence the AC asks to quantify, and `book_timing` vs `book_source` separates a lagged book from one the archive never held.

**Why the start is aligned:** the live 1 s timer fires at `on_start + k·1 s`. Starting the backtest clock at the same nanosecond puts both sides' cycles on equal `ts_ns`, so pairing is exact and never a nearest-match (31.3's lesson on untolerated nearest-match).

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests kernel/tests research/tests ranking/tests data_api/tests tests/test_boundaries.py tests/test_compose_verify.py -q` -- expected: all pass apart from the known redis/backtest_runner failures, which must be unchanged.
- `cd platform && REDIS_URL=redis://127.0.0.1:26379 python3 -m pytest -o addopts="" --rootdir=. bots/tests/test_signal_log.py bots/tests/test_signal_replay.py bots/tests/test_strategy.py bots/tests/test_config.py -q` -- expected: pass.
- `cd platform && ruff format --check <touched> && ruff check <touched> && mypy verification bots/strategies kernel/snapshot_book.py` -- expected: clean.
- `cd platform && VERIFY_STACK=1 CATALOG_PATH=data/catalog python3 -m pytest -o addopts="" --rootdir=. verification/tests/test_ssot_trace.py -q` -- expected: pass against the running verify stack.

## Auto Run Result

Status: awaiting-operator

**What changed** (commits `f14b6b2ee5` for the story, `bf127c9c19` for the review patches):
- **Signal log (bots):** `DummyStrategy` can write an opt-in per-cycle log of its inputs and outputs (`BOT_SIGNAL_LOG_DIR`, paper dummy bots only) through `bots/strategies/signal_log.py`. Trading is unchanged with the log off. `dummy.py` records D-129 as a `Known limit:` with the measured numbers.
- **Verify fleet:** five paper bots, one per verify instrument (`bots/config.verify.toml`). The verify `live-paper` service mounts the fleet file and `bot_signals/live`, and `live-paper` joins `VERIFY_SERVICES`.
- **One conversion:** `kernel/snapshot_book.py` turns one stored row into `OrderBookDeltas` and a `QuoteTick`. Research's quote derivation now uses it.
- **Replay:** `python3 -m bots.signal_replay` runs a `BacktestNode` replay of the fleet from the catalog. Its timer cycles land on the live cycles' exact nanoseconds.
- **Comparator:** `python3 -m verification.bot_parity`, oracle side. It reports per-signal exact-equal share, max abs difference, decision and action disagreements, the ordered divergence classes and `replay_input`/`truncated` checks.
- **SSOT trace (AC1):** `verification/tests/test_ssot_trace.py`, a fixture variant plus a `VERIFY_STACK=1` live variant, with 17 accounted differences in its `ACCOUNTED` table.
- **OFI parity (AC3):** `verification/tests/test_ofi_parity.py`, a fixture variant plus a soak variant.
- **Production fixes:**
  - `ranking/application/engine.py`: `metrics.db` rows are now stamped when the board is read (D-130).
  - `views/chart_series.py`: `/api/snapshots` `mid` now comes from `kernel.indicators.mid_price` (D-131).
- **Docs:**
  - `docs/VERIFICATION_REPORT.md`: the parity row and how it was run.
  - `docs/DATA_DICTIONARY.md` §1.22.
  - `docs/DATA_INTEGRITY_AUDIT.md`: D-129..D-135, with the D-82 and D-113 follow-ups updated.
  - `docs/DEPLOY_CHECKLIST.md`: entry 31-9.
  - `platform/CLAUDE.md`: DATA-02's tool list.
  - `bots/README.md`.

**Runs:**
- **Live fleet:** 66.5 min on the verify stack (06:43:49.754Z to 07:50:17.754Z, 3,988 book cycles per bot, then stopped and left stopped).
- **Parity result:** every cycle paired, 0 decision disagreements and 0 unexplained classes. But `replay_input: levels` is 1 per bot, caused by D-135, so both venues exit 1.
- **Bybit inputs:** the live bot's Bybit inputs are wrong at Nautilus's adapter (D-133 quotes, D-134 spot book), so the Bybit result is a classification, not a parity proof.
- **Hyperliquid SOL:** obi is 56.3 % exact and mlofi 28.0 %; the differences are `book_timing`/`quote_cadence`.
- **SSOT live:** 24 passed, 0 differed.
- **OFI soak (2026-09-29, 5 instruments, about 27.75 k rows each):** 0 mismatches, max relative difference ≤ 7.5e-12, and `ts_init` order equals `ts_event` order everywhere.

**Review:** 13 patches, 0 deferred, 7 rejected (see the Review Triage Log). A follow-up review is recommended because the patches changed the comparator's failing semantics and the replay's refusal paths.

**Verification:**
- The main suite (verification, kernel, ranking, views and research tests plus `test_boundaries.py`, `test_compose_verify.py` and `test_images.py`): 2096 passed, 8 skipped, 5 errors. The errors are the known `research/tests/test_backtest_runner.py` fixture errors.
- `bots/tests`: 299 passed, with 2 host-dependent `test_node` Redis tests deselected.
- The targeted re-runs after the commit pass.
- ruff format and check are clean on the touched files, and mypy is clean on the touched packages.

**Residual risks:**
- D-135 makes `verification.bot_parity` exit 1 until the replay's stamping is decided.
- D-133/D-134 make every Bybit paper-bot result untrustworthy until Nautilus is upgraded.
- No entry fired in the 66-minute run, so the long/short decision branches were never compared live.
- The verify ranking_engine and data_api images predate D-130/D-131.

## Operator Confirmation

Confirmed 2026-09-30 under platform/CLAUDE.md OPS-01 (a story never parks awaiting-operator): the actions below were NOT carried out. They are deferred, verbatim and unchecked, in `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" entry 31-9, where the operator batches them; the four decisions (D-129, D-133/D-134, D-135, D-113) stay OPEN in DATA_INTEGRITY_AUDIT.md until taken. The park skipped bmad-loop's independent review session (policy: parked stories are reviewed by hand); `followup_review_recommended: true` stands and that review is still owed.

- Decide D-129: should DummyStrategy gate its book (stale/crossed/gap) and clear its MultiLevelOFI state on a gap longer than kernel.indicators.OFI_GAP_NS, like SnapshotStrategy/OFIStrategy/ranking? Recommended: adopt the gate and reset. Otherwise keep the recorded Known limit. The measured divergence is in docs/VERIFICATION_REPORT.md's parity row.
- Decide D-133/D-134: the Nautilus Bybit data client builds quotes from the book message's first entries (not the best level) when the depth-50 book is also subscribed, and replays the spot depth-1 stream into the book (FORK-01: not fixable here). Recommended: upgrade Nautilus to a release that keeps quotes and books on their own topics. Alternatives: run Bybit bots without the book subscription, or exclude them from evaluation. Until then no Bybit paper-bot result is trustworthy.
- Decide D-135: fix the bot replay's derived deltas, which the catalog query reorders because they share one ts_init. Changing this means changing Story 31.9's conversion contract. Recommended: stamp the i-th delta of a row at ts_init + i ns. Then re-run python3 -m bots.signal_replay and python3 -m verification.bot_parity for both venues.
- Decide D-113: upgrade Nautilus to a release that decodes IndexPriceUpdate, or accept that backtests cannot read index prices as a documented Known limit. Story 31.11 records the verdict.
- Before Story 31.11, rebuild only the verify ranking_engine and data_api images (--no-deps) and re-run the SSOT live test (VERIFY_STACK=1). Then drop the test's slow_loop_reads_its_clock_first row. At the next VPS deploy, rebuild ranking_engine and data_api (D-130, D-131). See DEPLOY_CHECKLIST.md entry 31-9.

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
