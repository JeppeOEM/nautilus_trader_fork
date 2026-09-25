---
title: 'Story 22.12: Exchange-time bucketing for trades on every venue and the book on Bybit/Hyperliquid'
type: 'feature'
created: '2026-09-21'
status: done
baseline_revision: 'bc3221b85c1ec8a98f7c2a06d532e43b3fd01ca3'
final_revision: '0d1c11d8e587039163e9328fb4670f9d5476aef0'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/22-12-venue-timed-1s-sampling-for-bybit-and-hyperliquid.md'
  - '{project-root}/troll/CLAUDE.md'
warnings: [oversized]
operator_actions:
  - "On nifelheim, deploy this branch and rebuild the two venue collectors (`docker compose up -d --build bybit_collector hyperliquid_collector`). Their config.toml files are bind-mounted from the repo, so `book_time_source = \"venue\"` and `hold_back_seconds` take effect with the deploy."
  - "On the VPS, run `python3 -m collector_core.measure_lag --venue bybit --seconds 10800` and `--venue hyperliquid --seconds 10800` in the collector image (`--network host`). Record each venue's per-kind distributions in troll/docs/DATA_INTEGRITY_AUDIT.md D-63."
  - "Set `hold_back_seconds` in troll/bybit_collector/config.toml and troll/hyperliquid_collector/config.toml to each venue's VPS TradeTick p99.9 rounded up to 0.5 s (or 0.0, with the reason written in the comment). Replace the provisional dev-box comment, then redeploy."
  - "After one full UTC day per venue has been rebuilt by `make nightly VENUE=BYBIT` / `VENUE=HYPERLIQUID`, record each venue's compare_klines pass rate (instruments and minutes) in DATA_INTEGRITY_AUDIT.md D-63/D-51. Root-cause every remaining mismatch (a missing trade is a 22.14 gap; a book-related one is a new finding). Never add a tolerance."
  - "For the first day after deploy, watch the `collector.late_trade`, `collector.pending_deltas` and `collector.book_sequence` counts in /api/errors for Bybit and Hyperliquid. A steady late-trade rate means the hold-back is too short; any pending_deltas or book_sequence entry is a DATA-02 finding to root-cause in D-63."
  - "Story 23.3 makes the counts above durable across restarts: run the day-long clean-run check in platform/docs/DEPLOY_CHECKLIST.md §6."
---

<intent-contract>

## Intent

**Problem:** The live 1 s row is arrival-timed on every venue. A Bybit/Hyperliquid book sample shows whatever had *arrived* by mid-second, and trades are folded into their arrival second. So live rows and the exchange-timed nightly rebuild (22.13) disagree at every boundary, and nothing measures how late venue messages arrive.

**Approach:** Add a `book_time_source = "venue"` mode, used by Bybit and Hyperliquid. In it the core holds deltas in `ts_event` order and closes exchange second `S` at wall `S + 1 + hold_back_seconds`, using the book as of the last delta with `ts_event < S+1` and the trades with `ts_event` in `[S, S+1)`. A trade that arrives after its second closed is archived, counted and left for the rebuild. dYdX stays `"arrival"`, running exactly today's code. A report-only `measure_lag` CLI sizes the hold-back. The ≥ 3 h VPS runs and the kline pass rates are owed by the operator, so the story ends `awaiting-operator`.

## Boundaries & Constraints

**Always:**
- `"arrival"` mode behaves exactly as today, and every existing test passes unchanged.
- `hold_back_seconds > 0` and `"venue"` mode are config errors unless their preconditions hold: the hold-back needs `"venue"`, and `"venue"` needs `snapshot_interval_seconds == 1.0`.
- A venue row for second `S` has `ts_event = S·1e9 + 0.5e9` (same floor second and phase as arrival rows, which `rebuild_seconds`/`candle_store` rely on) and `ts_init` = the wall time it was sampled.
- Pending deltas are ordered by `(ts_event, arrival order)`. Nothing is held longer than `hold_back_seconds + 5 s` after its `ts_init`. Past that it overflows: `collector.pending_deltas` ledger, book state dropped, resync queued (only when the client has `resync_orderbook`).
- A late or clock-ahead trade is always archived (buffered for the flush), never folded live, and counted. Each count reaches `error_ledger` at most once per instrument per report cycle (every flush, 60 s).
- Staleness means different things per consumer:
  - the venue book-age gate compares second end `(S+1)·1e9` with the last applied delta's `ts_event`;
  - feed-dead and the OBS-01 watchdog compare on arrival;
  - `ranking_engine` freshness is already stamped on receipt.
- Tests follow TEST-01/03: real `OrderBook`/`TradeTick`/`OrderBookDeltas` and a real tmp catalog.

**Block If:** exchange-timing the book requires modifying `nautilus_trader/` or `crates/`.

**Never:**
- touch `troll/dydx_collector/**` behaviour, `ml_signals/candle_store.py`, `data_api/**` or `frontend/**` (verify only, tests allowed);
- drop a late trade from the archive;
- set a hold-back to make reconciliation pass;
- add a tolerance to `compare_klines`;
- write `collector:status` from the core. It stays dYdX-only (epic decision), and dYdX has no late trades by construction, so `collector.late_trade` goes to the ledger and the report log line.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Reordered deltas | delta `ts_event=S+0.95` arrives, then `S+0.9` | at close of S both applied, `S+0.9` first | none |
| Boundary | delta `ts_event=S+1.2` pending at close of S | not in row S; applied at close of S+1 | none |
| Late trade | trade `ts_event` in S, processed after S closed | archived; not in any live row; `late_trade` count +1; rebuild puts it in row S | ledger `collector.late_trade` per report |
| Clock-ahead trade | `ts_event > ts_init + hold_back + 5 s` | archived, excluded live, counted | ledger `collector.venue_clock_ahead` per report |
| Pending overflow | a held delta is older than `hold_back + 5 s` after the last close | pending + book dropped; resync queued if the client can | ledger `collector.pending_deltas` |
| Loop stall | wake-up N seconds late | every due second closed in order, each drained to its own boundary, rows share `ts_init` | lag canary as today |
| Venue book stale | no applied delta with `ts_event` in the 5 s (`stale_book_seconds`) before `S+1` | sample skipped, that second's trades discarded live | warning, as today |
| Arrival mode | dYdX or default config | today's path exactly | today's |
| Invalid config | `hold_back_seconds < 0`, `> 0` with arrival, or venue with interval ≠ 1 | `ValueError` at load | — |

</intent-contract>

## Code Map

- `troll/collector_core/config.py` -- `CoreConfig` fields and validation in `core_config_from_dict`.
- `troll/collector_core/collector.py` -- the paths this story branches:
  - `_process_data` (:525): deltas/trades routing;
  - `_apply_deltas` (:417) and `_clear_book_state` (:407): hooks that Bybit/dYdX override;
  - `_take_batches` (:611): the carry compares a row's `ts_event` with a trade's `ts_init`, and must compare `ts_init`;
  - `_sample_tick` (:710), `_stale_reason` (:782), `_second_loop` (:808);
  - `_report_stale_trades` (:557).
- `troll/bybit_collector/collector.py` -- the `_apply_deltas` sequence canary runs at *application*: in venue mode that is the drain, in `ts_event` order. On a break it sets `_resync_pending`, handled by `_handle_missing_book`.
- `troll/hyperliquid_collector/collector.py` -- full-snapshot messages, no `resync_orderbook`; 12 s stale bound.
- `troll/{bybit,hyperliquid}_collector/config.toml` -- per-venue values.
- `crates/adapters/bybit/src/websocket/parse.rs:216,238`, `crates/adapters/hyperliquid/src/websocket/parse.rs:90,110` -- trade `T`/`time` and book `ts`/`time` are venue ms; `dydx/.../parse.rs:520-539` stamps book `ts_event = ts_init` (D-49).
- `troll/ranking_engine/engine.py:567` -- `_LAST_SEEN[iid] = time.time_ns()` (arrival; 30 s bound).
- `troll/data_api/live_candles.py:198-211` -- buckets by `ts_event`, no wall-clock freshness.
- `troll/frontend/src` -- no live-bar freshness threshold against `Date.now()` exists (`RankingsPage` `RANKING_STALE_MS` uses the engine heartbeat `updated_at`). Nothing to change.
- `troll/collector_core/rebuild_seconds.py` -- `_row_seconds` maps rows by `ts_event // 1 s`, and `fold_day` buckets trades by `ts_event` (AC 1 already holds for trades).
- Test helpers: `collector_core/tests/test_collector.py` (`_collector`, `_deltas`, `_trade`, `_clocked_trade`, `_tick`, `_day_collector`, `_D0`) and `test_rebuild_seconds.py`.

## Tasks & Acceptance

**Execution:**
- [x] `troll/collector_core/config.py` -- Add `book_time_source: Literal["arrival","venue"] = "arrival"` and `hold_back_seconds: float = 0.0`, parsed in `core_config_from_dict`. Validate:
  - the source is one of the two literals;
  - `hold_back_seconds` is ≥ 0;
  - a hold-back > 0 requires `"venue"`;
  - `"venue"` requires an interval of 1.0.
- [x] `troll/collector_core/collector.py` -- Venue mode, under a `self._venue_time` flag:
  - **Deltas.** `_process_data` sets `_last_book_update_ns[iid]` to arrival (watchdog), then `bisect.insort`s `(ts_event, seq, ts_init, deltas)` into `_pending_deltas[iid]`, counting a delta whose second already closed as late (report line).
  - **Drain.** `_drain_pending_deltas(boundary_ns)` pops due items first, then calls the `_apply_deltas` hook per item and records `_book_event_ns[iid]` when a book exists afterwards.
  - **Overflow.** `_check_pending_overflow(now_ns)` implements the overflow rule from Boundaries.
  - **Trades.** They go into `_venue_trades[iid][ts_event // 1 s]`, or are counted as late/ahead.
  - **Sampling.** `_sample_tick(now_ns, second=None)`: the per-instrument gate moves into `_sample_instrument`, byte-identical for arrival. For venue it drains to `(S+1)·1e9`, uses the venue book-age reason and bucket `S` (older buckets are counted late and discarded), stamps `ts_event = S·1e9 + 0.5e9`, and sets `_last_closed_second`.
  - **Loop.** `_second_loop` branches to `_venue_second_loop`, which closes every due second at `S+1+hold_back` with the lag canary, then runs the overflow check.
  - **Bookkeeping.** `_clear_book_state` also pops the pending list and `_book_event_ns`. `_take_batches` carries rows by `ts_init`. `_report_stale_trades` ledgers the late/ahead counts. `_drop_unsampled_trades` also clears `_venue_trades`.
  - **Docstrings.** Module docstring gains a "two clocks per mode" paragraph.
- [x] `troll/collector_core/measure_lag.py` -- NEW report-only CLI: `--venue {bybit,hyperliquid,dydx} --seconds N [--instrument ...] [--environment]`.
  - Builds that venue's existing client (lazy import) with an `on_data` recorder, connects, subscribes, sleeps N and disconnects.
  - Prints per kind (data class name): n, p50/p99/p99.9/max of `ts_init - ts_event` in ms, plus the suggested hold-back (p99.9 of trades rounded up to 0.5 s).
  - Trades older than `stale_trade_seconds` at arrival are counted as replay and excluded. dYdX book lag is 0 by construction (noted in the output).
  - Pure helpers `percentile`, `suggest_hold_back`, `LagRecorder` are unit-tested.
- [x] `troll/bybit_collector/config.toml`, `troll/hyperliquid_collector/config.toml` -- `book_time_source = "venue"`, plus `hold_back_seconds` with a comment giving the measurement it came from (a local run now; the ≥ 3 h VPS run is an operator action).
- [x] Tests:
  - **NEW `troll/collector_core/tests/test_venue_time.py`** covers:
    - (a) reordered deltas;
    - boundary delta held;
    - (b) a late trade is archived, counted, absent live, and lands in its second after `rebuild_day`;
    - clock-ahead trade;
    - pending overflow → ledger + resync / clear for a snapshot venue;
    - venue stale gate on `ts_event`;
    - watchdog tracker is arrival;
    - catch-up closes every due second;
    - (d) 60 venue rows of one minute → `candle_store` `seconds_observed == 60`;
    - (c) arrival mode leaves `_pending_deltas`/`_venue_trades` untouched;
    - config validation.
  - **`troll/ranking_engine/tests/test_engine.py`** -- a snapshot whose `ts_event` is hold-back old is fresh.
  - **`troll/data_api/tests/test_live_candles.py`** -- `(ts_event=S, ts_init=S+2 s)` lands in bucket S.
  - **NEW `troll/collector_core/tests/test_measure_lag.py`**.
  - **(e)** Existing candle-store equivalence test re-run unchanged.
- [x] Docs:
  - **`troll/docs/DATA_DICTIONARY.md`** -- per-venue clock semantics for `DydxSecondSnapshot`: trade fields exchange-timed on every venue after the rebuild; book exchange-timed live on Bybit/HL and arrival-timed on dYdX; `ts_init` = sampled; new config keys; `measure_lag`.
  - **`troll/docs/DATA_INTEGRITY_AUDIT.md`**:
    - D-31/D-44 closed for trades by the rebuild (verified by the nightly compare);
    - D-49 stays;
    - D-50 updated: hold-back optional;
    - a new row for the lag capture (local numbers, VPS ≥ 3 h owed) and kline pass rate owed.
  - **`troll/CLAUDE.md` DATA-01** -- one sentence on venue-timed rows.

**Acceptance Criteria:**
- Given the collector image, when the pytest suites below run, then all new tests pass and the only failures are the 22.13 baseline set:
  - `collector_core/tests`
  - `dydx_collector/tests`
  - `bybit_collector/tests`
  - `hyperliquid_collector/tests`
  - `ml_signals/tests`
  - `ranking_engine/tests`
  - `data_api/tests/test_live_candles.py`
- Given mainnet, when `measure_lag` runs for Bybit and Hyperliquid for several minutes, then it prints the per-kind distributions, and those numbers set the TOML hold-back and are recorded in the audit.
- Given a Bybit and a Hyperliquid collector in venue mode against mainnet with a scratch catalog, when they run for ~2 minutes, then:
  - rows are written one per floor second with `ts_event % 1e9 == 5e8` and `ts_init > ts_event`;
  - no `pending_deltas` overflow occurs;
  - the late-trade count is reported.
- Given the ≥ 3 h lag runs and a full rebuilt day per venue require the VPS, when the story ends, then the spec is `awaiting-operator`, and `operator_actions` lists those runs, the TOML update and the per-venue kline pass rates.

## Spec Change Log

## Review Triage Log

### 2026-09-21 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 11 (high 0, medium 3, low 8)
- defer: 0
- reject: 10
- addressed_findings:
  - `[medium]` `[patch]` `query_second_snapshots` (and `chart_data`'s own copy of it) bounded the window on `ts_init`, so venue rows (sampled 1 + hold_back s after their `ts_event`) shifted at window edges. Both now query with the end widened by `_FILE_MARGIN_NS` and filter exactly on `ts_event`; `chart_data` reuses the shared helper.
  - `[medium]` `[patch]` The shipped Hyperliquid hold-back of 2.5 s had never run live. Re-ran for 150 s at 2.5 s: 145 consecutive rows per instrument, 0 late trades, empty ledger. Recorded in D-63.
  - `[medium]` `[patch]` Trades received before the first venue close were ledgered as `collector.late_trade` at every start. They are now counted in `_pre_start_trades` and logged, not ledgered.
  - `[low]` `[patch]` A 60 s catch-up could push `ts_init - ts_event` past the readers' 60 s file margin. The cap is now 30 s, with the reason in a comment.
  - `[low]` `[patch]` A catch-up ledgered one crossed-book episode once per overdue second (same `now_ns`). Episode start is now detected by membership.
  - `[low]` `[patch]` A late message could move `_book_event_ns` backwards. It now takes the max.
  - `[low]` `[patch]` Held deltas discarded by `_clear_book_state` went uncounted. They are now counted with the resync-window drops.
  - `[low]` `[patch]` Applying Bybit deltas in `ts_event` order could trip the `u` regress canary if `ts` ever runs backwards against `u`. Documented in D-63 with the live evidence (zero occurrences); a rising `collector.book_sequence` count is the canary.
  - `[low]` `[patch]` `hold_back_seconds` of nan/inf was accepted. It is now refused (finite check) and tested.
  - `[low]` `[patch]` `measure_lag` accepted non-positive durations and connected outside its try/finally. It now validates its arguments and releases the client on a failed connect.
  - `[low]` `[patch]` The Bybit TOML comment said "below" for the instruments listed above it. Fixed.

## Design Notes

**Why hold deltas instead of snapshotting on arrival.** A sample at wall `S+1` would include deltas stamped `S+1.02` that arrived early, and miss `S+0.98` deltas still in flight. Holding and draining by `ts_event` gives the exchange's book at the second's end. The hold-back just gives in-flight messages time to arrive.

**Row timestamps.** `ts_event` stays mid-second, so a venue row and an arrival row map to the same floor second, and the rebuild, candle store and chart gap logic need no change. `ts_init` is the true sample time, so backtests replaying on `ts_init` never see a book before it could have been known.

**Bybit sequence canary** runs at drain. For one topic, `ts_event` order equals `u` order, so reordering by `ts_event` never trips it on a healthy stream. If it does trip, that is a real finding (loud, as today).

**Book cross-check under venue mode** compares a book up to `1 + hold_back` s old with REST. Its two-round persistence rule already filters latency skew, and a churned level produces different mismatch strings. `Known limit:` a level that is stable but changed inside that window on both rounds could mis-flag. The upgrade path is to compare REST against the book drained to REST's own timestamp.

## Verification

**Commands:**
- `docker run --rm --network host -v "$PWD":/app -w /app -e HOME=/tmp -e USER=collector troll-collector:latest python3 -m pytest collector_core/tests dydx_collector/tests bybit_collector/tests hyperliquid_collector/tests ml_signals/tests ranking_engine/tests data_api/tests/test_live_candles.py -q` (from `troll/`) -- expected: only baseline failures.
- `ruff check`, `ruff format --check`, and `mypy --disallow-incomplete-defs` on changed files -- expected: clean.
- `python3 -m collector_core.measure_lag --venue bybit --seconds 600` and `--venue hyperliquid` in the image with `--network host` -- expected: distributions printed.

## Auto Run Result

Status: awaiting-operator

**Summary:** Bybit and Hyperliquid now build each live 1 s row on exchange time (`book_time_source = "venue"`).
- **Book.** Book messages are held in `ts_event` order and applied up to the end of exchange second `S`, which closes at wall `S + 1 + hold_back_seconds`.
- **Trades.** Trades are bucketed by `ts_event` into `[S, S+1)`. A trade arriving after its second closed is archived and counted (`collector.late_trade`), never folded live; the 22.13 rebuild places it.
- **Row timestamps.** Rows keep `ts_event = S + 0.5 s` (same floor second as before) and `ts_init` = the real sample time.
- **dYdX.** dYdX stays `"arrival"` and runs today's code path unchanged; its book has no venue time (D-49).
- **Guards.** Held messages are bounded at hold-back + 5 s (`collector.pending_deltas` + resync). A stall catches up every due second, up to 30.
- **Lag tool.** A new report-only `measure_lag` CLI sized the provisional hold-backs from 10-minute dev-box runs: Bybit trade p99.9 208 ms → 0.5 s; Hyperliquid 2.31 s → 2.5 s. The ≥ 3 h VPS runs and the kline pass rates are owed (operator_actions).

**Files changed:**
- `troll/collector_core/config.py`: `book_time_source` and `hold_back_seconds`, with validation (finite, ≥ 0, hold-back needs venue mode, venue mode needs a 1 s interval).
- `troll/collector_core/collector.py`: venue mode (held deltas, drain, trade buckets, late/ahead/pre-start accounting, `_venue_second_loop`, overflow bound). The gate is extracted into `_sample_instrument`, the flush carry now keys on `ts_init`, and the crossed-book episode check is catch-up safe.
- `troll/collector_core/measure_lag.py` (new): per-venue, per-kind `ts_init - ts_event` distributions and a suggested hold-back.
- `troll/ml_signals/catalog_stats.py`, `troll/ml_signals/chart_data.py`: snapshot queries are windowed on `ts_event`, not on the catalog's `ts_init` bound.
- `troll/bybit_collector/config.toml`, `troll/hyperliquid_collector/config.toml`: venue mode plus the provisional hold-backs, with the measurement cited.
- Tests:
  - new `collector_core/tests/test_venue_time.py` (27) and `test_measure_lag.py` (5);
  - a receipt-stamped freshness test in `ranking_engine`;
  - a `(ts_event=S, ts_init=S+2 s)` bucket test in `data_api` `test_live_candles`.
- Docs:
  - `troll/docs/DATA_DICTIONARY.md`: per-venue clock semantics;
  - `troll/docs/DATA_INTEGRITY_AUDIT.md`: D-31/D-44/D-50 amended, new D-63 with lag data, live checks and the Bybit `ts`/`u` note;
  - `troll/CLAUDE.md` DATA-01.

**Review:**
- 11 patches applied (3 medium, 8 low), listed in the Review Triage Log.
- 0 deferred.
- 10 rejected:
  - `measure_lag` replay-exclusion bias (the collector drops those trades too);
  - hold-back 0 allowed in venue mode (spec default);
  - late deltas logged rather than ledgered (as specced);
  - dYdX loader ignoring the new keys (pre-existing loader behaviour, already documented);
  - overflow measured from the adapter's `ts_init` (no false positive is possible: a remaining item has `ts_event` ≥ the boundary);
  - test-environment isolation (existing pattern);
  - an untested dYdX builder (exercised live);
  - a ranking test covering existing behaviour (asked for by the spec);
  - `CoreConfig` direct construction (existing pattern);
  - unclosed final seconds at shutdown (reported by the rebuild as orphans).
- The Frontend was verified with no change needed: no live-bar threshold compares against wall time. `ranking_engine` freshness is already stamped on receipt.

**Verification:**
- **Tests** (collector image): collector_core, dydx, bybit and hyperliquid collectors, ml_signals, ranking_engine, and `data_api` `test_live_candles` + `test_data_api`: 621 passed. The only failures are the 22.13 baseline: 4 `test_ofi_strategy*` and 3 collection errors.
- **Lint and types:** `ruff check` and `ruff format --check` are clean on the changed files. `mypy --disallow-incomplete-defs` is clean on `collector.py`, `config.py`, `measure_lag.py` and `chart_data.py`; the only mypy findings are 3 existing ones at `catalog_stats.py:124,149`, outside the changed lines.
- **Live mainnet** (scratch catalog):
  - Bybit at 0.5 s: 125 rows per instrument across 4 instruments, one per floor second, all mid-second, `ts_init > ts_event`, 0 late trades, empty ledger.
  - Hyperliquid at 1.0 s: 126 rows, 24/28 late trades.
  - Hyperliquid at 2.5 s: 145 rows, 0 late trades, empty ledger.
- **Measured lag:** `measure_lag`, 600 s per venue, recorded in D-63.

**Residual risks:**
- The hold-backs are provisional, taken from a dev box that has a ~100 ms apparent clock offset to Bybit.
- Kline pass rates (AC 4) are unmeasured until a rebuilt day exists on the VPS.
- That Bybit's `ts` rises monotonically with `u` is evidenced only by a 130 s live check (a canary covers it).
- In venue mode the REST book cross-check compares a book up to 1 + hold_back s old (documented `Known limit:`).
- With a late message, the live row's book can differ from the exchange's at the boundary until the next close; the nightly rebuild does not touch book columns.

## Operator Confirmation

Confirmed 2026-09-21: the external actions this story owed were carried out.

- On nifelheim, deploy this branch and rebuild the two venue collectors (`docker compose up -d --build bybit_collector hyperliquid_collector`). Their config.toml files are bind-mounted from the repo, so `book_time_source = "venue"` and `hold_back_seconds` take effect with the deploy.
- On the VPS, run `python3 -m collector_core.measure_lag --venue bybit --seconds 10800` and `--venue hyperliquid --seconds 10800` in the collector image (`--network host`). Record each venue's per-kind distributions in troll/docs/DATA_INTEGRITY_AUDIT.md D-63.
- Set `hold_back_seconds` in troll/bybit_collector/config.toml and troll/hyperliquid_collector/config.toml to each venue's VPS TradeTick p99.9 rounded up to 0.5 s (or 0.0, with the reason written in the comment). Replace the provisional dev-box comment, then redeploy.
- After one full UTC day per venue has been rebuilt by `make nightly VENUE=BYBIT` / `VENUE=HYPERLIQUID`, record each venue's compare_klines pass rate (instruments and minutes) in DATA_INTEGRITY_AUDIT.md D-63/D-51. Root-cause every remaining mismatch (a missing trade is a 22.14 gap; a book-related one is a new finding). Never add a tolerance.
- For the first day after deploy, watch the `collector.late_trade`, `collector.pending_deltas` and `collector.book_sequence` counts in /api/errors for Bybit and Hyperliquid. A steady late-trade rate means the hold-back is too short; any pending_deltas or book_sequence entry is a DATA-02 finding to root-cause in D-63.

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
