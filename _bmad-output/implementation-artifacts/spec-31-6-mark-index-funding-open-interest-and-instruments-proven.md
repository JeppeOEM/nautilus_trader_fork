---
title: 'Story 31.6: Mark, index, funding, open interest and instrument definitions proven'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_revision: '1d8204ee09'
final_revision: '537ee49163'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
  - '{project-root}/platform/docs/DATA_DICTIONARY.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Nothing checks the stored mark/index price, funding rate and open interest rows, or the stored instrument definitions, against the venue. Funding carry, basis and OI signals read them. What the planning measured on the soak (13:00-17:00Z): the streams agree today, but by rules nobody wrote down. Every stream except Bybit OI stores **changes only**, and the Rust adapters filter them. Bybit delta funding rows carry null `interval`/`next_funding_ns`. Bybit OI's `ts_event` is the poll's local wall clock. Hyperliquid's `lot_size` is Nautilus's default `1`, not a venue value.

**Approach:** Add `python3 -m verification.derivs --venue V --day D`, mirroring `verification.book`/`trades`, built on a pure domain module. Per plan instrument and per type, it:
1. validates the reference against the recorder's REST polls;
2. matches every stored row to the recorder's frames exactly;
3. classifies every reference update as stored or not, with the expected ratio stated;
4. checks the `ts_event` rule and the precision labels;
5. asserts spot has no derivative rows;
6. compares every instrument-definition field with every venue poll of the day, reporting any venue-side change.

Then run it on the soak and register every finding.

## Boundaries & Constraints

**Always:**
- **Independence (DATA-02):** non-test `verification` code never imports `capture`, `kernel.open_interest`, `kernel.catalog_files`, `nautilus_pyo3` or `nautilus_trader`.
  - Catalog rows are read raw with pyarrow in a new `verification/infrastructure/derivs_reader.py`.
  - Fixed values: `value` is `binary(16)`. Decode it with `trade_check.fixed_raw`/`decode_fixed` (10^16 scale).
  - Funding `rate` is JSON-quoted decimal text.
  - `open_interest` is decimal text.
  - Instrument numerics are decimal strings.
  - Every value becomes a `Decimal`, never a float.
  - `test_boundaries.py` registers `verification.derivs` in `VERIFICATION_ROOTS` and in the import-smoke probe.
- **Reference per venue** (frames: `trade_check.recorder_gaps` semantics; read the day's hours plus the hour before, for the state at 00:00, and the hour after):
  - **Bybit:** `linear.tickers` frames, where the topic's symbol is the instrument's.
    - An update is a field present in the frame (`markPrice`, `indexPrice`, `fundingRate`, `nextFundingTime`, `openInterest`), keyed by the frame's `ts` ms × 10^6.
    - The field's **state** at time t is its last update with key ≤ t since the channel's last connection line; before one exists it is unknown.
  - **Hyperliquid:** `activeAssetCtx` frames with `data.coin` equal to the coin, keyed by `recv_ns`, because the wire carries no time.
    - An update of a field (`markPx`, `oraclePx`, `funding`, `openInterest`) is a frame whose string differs from the previous frame's.
- **Expected ratio** (reported beside the coverage counts, as the documented sampling):

  | Venue and stream | Stored rows |
  |---|---|
  | Bybit mark, index | one per frame carrying the field |
  | Bybit funding | one per frame whose `fundingRate` differs from the last one seen, or whose `nextFundingTime` does (the Rust `funding_cache`, `crates/adapters/bybit/src/python/websocket.rs:1549-1570`). A frame whose `nextFundingTime` changed without a `fundingRate` is `next_time_only`: Rust's parse needs the rate, so the row is not written. That is a documented Known limit, not failing. |
  | Hyperliquid (every field) | one per change of the wire string (`crates/adapters/hyperliquid/src/websocket/handler.rs:868-970`) |
  | Bybit OI | one per `open_interest_poll_seconds`, read from the venue config (a missing key uses the collector's own default, cited) |

- **Stored-row classes, per type.** A failing count > 0 fails the instrument.

  | Class | Condition | Fails |
  |---|---|---|
  | `exact` | Bybit: a reference update of the same key with an equal value (a multiset per key). HL: an unconsumed reference update of equal value with `|recv_ns − ts_init| ≤ HL_MATCH_BOUND_NS`, the earliest such. Bybit OI: see `agree_state`. | no |
  | `agree_state` | No same-key update, but the value equals the reference state at the row's key. HL: equals any frame's value within the bound. Bybit OI, always this class: the value equals the state at some venue time in `[ts_event − OI_POLL_WINDOW_NS, ts_event]`. This covers the collector's own reconnect snapshot. | no |
  | `value_mismatch` | Bybit: a same-key update whose value differs. Record the example (key, stored, reference). | yes |
  | `unmatched` | Nothing agrees. | yes |
  | `reference_unavailable` | The row's time lies in a recorder gap, or the state is unknown. | no |
  | `off_grid` | The fixed raw is not whole at the file's `price_precision`. | yes |
  | `ts_rule` | The row breaks the type's `ts_event` rule (below). | yes |
  | `duplicate` | Two rows of one type share `(ts_event, value)`. | yes |

- **Reference-update classes:**

  | Class | Condition | Fails |
  |---|---|---|
  | `stored` | The update has a stored row. | no |
  | `unchanged` | Bybit funding only: an equal repeat. | no |
  | `next_time_only` | See the expected-ratio table. | no |
  | `not_stored_explained` | The update's second lies in a coverage `seconds` run of the instrument whose reason is in `FEED_LOSS_REASONS = {restart, stale, no_book, not_collected}`. A comment explains why exactly these mean the collector's feed was absent. | no |
  | `not_stored` | Otherwise. | yes |

  Bybit OI replaces these with poll coverage: the expected polls, the stored rows and `poll_gaps`. A gap is > 1.5 × the period between consecutive rows, or from the day's start or to its end. It is failing unless a `restart` run overlaps it.
- **`ts_event` rules**, checked on every row and written into the dictionary:
  - Bybit mark, index and funding: the venue frame `ts` (whole ms).
  - Bybit OI: `ts_event == ts_init`, the collector's local clock after the poll response (`capture/venues/bybit/open_interest.py:44`), never a venue time.
  - Hyperliquid, every type: `ts_event == ts_init`, the adapter's receive clock.
- **Funding value agreement:**
  - `rate` is compared as an exact `Decimal`.
  - Bybit: `interval == fundingIntervalHour × 60` and `next_funding_ns == nextFundingTime × 10^6` when the frame carries them, else null. Null means "not in this frame", a DEVIATION the dictionary documents.
  - Hyperliquid: `interval == 60` (hourly funding, venue docs) and `next_funding_ns` null.
- **Precision labels:**
  - Mark and index: every file with rows in the day has `price_precision` schema metadata. A file without it is refused and named.
  - Exactly one label per (instrument, type) across the day; `labels > 1` fails. It must also equal the instrument definition's `price_precision` in force, otherwise `label_vs_definition` fails.
  - Funding and OI have no label. The report says "none: stored as decimal text".
- **REST validation (reported first):**
  - **Bybit:** each `linear.rest.tickers` poll with status 200 and `retCode == 0`, whose `result.list[0].symbol` is the instrument's. Each field is compared with the WS state at the response's `time` × 10^6.
    - `agree_key`: equal.
    - `agree_bracket`: equal to the update just before or just after.
    - `between_pushes`: neither.
    - `unaligned`: the state is unknown or in a recorder gap.
  - **Hyperliquid:** each `rest.metaAndAssetCtxs` poll (status 200), the ctx at the universe index whose `name` is the coin.
    - `agree`: equal to a frame's value with `recv_ns` in `[sent_ns − HL_MATCH_BOUND_NS, recv_ns + HL_MATCH_BOUND_NS]`.
    - `between_pushes`: otherwise.
  - A type fails as `reference_unvalidated` when judged polls > 0 and nothing agrees, or when rows exist and no poll was judged.
- **Instrument definitions:**
  - **Stored side:** every stored definition of the plan instrument (`crypto_perpetual/` or `currency_pair/`), every file and row.
  - **Venue expectation per poll**, in `verification/domain/instrument_check.py`, written from the venue docs:
    - Bybit linear: `tickSize` → `price_increment`; `priceScale` → `price_precision`; `qtyStep` → `size_increment` and `lot_size`; `size_precision` from the exponent of `qtyStep`; `minOrderQty` → `min_quantity`; `multiplier` 1.
    - Bybit spot: the same, with `basePrecision` in place of `qtyStep`; `price_precision` is the exponent of `tickSize`.
    - Hyperliquid perp: `szDecimals` → `size_precision`, with `size_increment` 10^−szDecimals; `price_precision = max(0, 6 − szDecimals)`, with `price_increment` 10^−that; `min_quantity` none; `multiplier` 1. `lot_size` has no venue counterpart (next bullet).
    - Increments compare as `Decimal` values; precisions compare as ints.
  - Every poll is judged against the definition in force: the latest stored row with `ts_init ≤` the poll's `recv_ns`.
    - `agree`, when every field is equal.
    - `differs` (fails): record the field, stored value and venue value.
    - `before_first_definition` (not failing).
    - No stored definition at all fails with `no_definition`.
  - A change between consecutive venue polls is reported as a `venue_change` (time, field, old, new), together with the definitions' `ts_init` list. That list shows how the collector handled the change: it writes definitions only at `CaptureService.run`, so a mid-run change shows up as `differs`.
  - Hyperliquid `lot_size` is shown as `not_venue_declared (stored 1: Nautilus default)` and is not compared. That is a DEVIATION audit row.
- **Spot:** for every `-SPOT.BYBIT` id directory under `mark_price_update`, `index_price_update`, `funding_rate_update` and `custom_open_interest` (the plan's and any other), rows with `ts_event` in the day are counted as `fabricated`, which fails. The report prints each plan spot id with its "0 rows" line.
- **Bounds:** these are time-alignment bounds, never value tolerances, and each is written beside its measured justification.
  - `HL_MATCH_BOUND_NS = 1 s`: planning measured stored `ts_init − recv_ns` from p1 −57 ms to max 437 ms; the push cadence is median 1.02 s and min 196 ms.
  - `OI_POLL_WINDOW_NS = 2 s`: the measured maximum needed was 623 ms over 96 polls; the median OI update interval is 8.7 s.
- **Refusals** are ledgered at the new site `verification.derivs.refused` (`sites.py` plus `test_sites.py`):
  - the inputs the book tool refuses;
  - a mark/index file without its label;
  - a stored value that is not decimal;
  - a null key column.
  - A missing raw hour of the day and a missing coverage record fail the day, as in conservation.
- **Reuse:** conservation's `RawReader`, `is_closed`, `day_hours`, `missing_raw`, `collect_coverage`, `plan_of`, `directory`, `Refused`; `catalog_reader.read_window` (row-group pruning, null-`ts_event` refusal); `trade_check.recorder_gaps`/`fixed_raw`/`decode_fixed`/`whole_at`.
- **Code rules:** the LGPL header, full typing, functions of about 30 lines or fewer, complexity of 10 or less, no module-level mutable state.
  - Memory holds one instrument-type-day of rows and its reference updates as Python objects. A `Known limit:` gives the measured size and the upgrade path (hour windows).

**Block If:** the raw frames contradict the measured shape: a Bybit ticker frame without `ts`/`data.symbol`, or a Hyperliquid `activeAssetCtx` without `data.coin`/`data.ctx`.

**Never:**
- Never modify `nautilus_trader/`, `crates/`, `sprint-status.yaml` or a venue `config.toml`. Never add a dependency.
- Never use a value tolerance.
- Never set `awaiting-operator`. The real-day verdict belongs to Story 31.11 (OPS-01).
- Do not fix Hyperliquid `lot_size` or the Rust `next_time_only` drop here; register them.
- Do not build 31.7-31.10.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Clean day | the rows equal the frames 1:1 and REST agrees | every failing count is 0; exit 0 | — |
| Planted: Bybit mark +1 tick | one stored mark row altered | `value_mismatch` 1; exit 1 | — |
| Planted: HL OI changed | one stored OI value altered | `unmatched` 1, plus its update `not_stored` 1; exit 1 | — |
| Planted: row removed | one Bybit index row deleted | `not_stored` 1; exit 1 | — |
| Not stored, feed down | as above, second inside a `stale` run | `not_stored_explained`; pass | — |
| Collector snapshot row | a stored ts absent from the reference, value = state | `agree_state`; pass | — |
| Two labels | index files at precision 2 and 3 | labels 2, fails | — |
| Spot fabricated | a `mark_price_update/BTCUSDT-SPOT.BYBIT` row | `fabricated` 1; exit 1 | — |
| Bybit OI poll gap | 900 s between rows, no restart run | `poll_gaps` 1; exit 1 | — |
| OI ts rule | `ts_event != ts_init` | `ts_rule` 1; exit 1 | — |
| Venue tick change | tickSize changes mid-day | `venue_change` reported; later polls `differs`; exit 1 | — |
| Recorder gap | rows inside a close→open gap | `reference_unavailable` | — |
| Label missing | a mark file without `price_precision` | refused, ledgered | exit message, 1 |

</intent-contract>

## Code Map

- `platform/verification/book.py`, `application/book.py` -- the CLI root and orchestration pattern to mirror (inputs, refusals, `check_day`, `render_text`, `report_json`).
- `platform/verification/application/conservation.py:136-330` -- `Inputs`, `InstrumentCoverage` (runs carry `reason`), `collect_coverage`, `day_hours`, `is_closed`, `missing_raw`, `hour_label`.
- `platform/verification/domain/trade_check.py:60-150,298` -- `fixed_raw`, `decode_fixed`, `whole_at`, `recorder_gaps`.
- `platform/verification/infrastructure/catalog_reader.py:94-135` -- `_row_groups`, `read_window`, the `data/<type>/<iid>/*.parquet` layout.
- `platform/verification/domain/subscriptions.py:150-299` -- `bybit_symbol`, `hyperliquid_coin`, the channel names `linear.tickers`, `linear.rest.tickers`, `<cat>.rest.instruments-info`, `activeAssetCtx`, `rest.metaAndAssetCtxs`.
- `platform/capture/venues/bybit/client.py:235-320`, `open_interest.py:42-73`; `hyperliquid/client.py:194-342` -- what capture stores (documentation only, never imported).
- `crates/adapters/bybit/src/python/websocket.rs:1509-1589`, `crates/adapters/bybit/src/common/parse.rs:279-420`, `crates/adapters/hyperliquid/src/websocket/handler.rs:868-970`, `crates/adapters/hyperliquid/src/http/parse.rs:168-220,775-850` -- the filters and definition mappings being verified.
- `platform/tests/test_boundaries.py:1986-2140` -- `VERIFICATION_ROOTS`, the import-smoke probe.
- Soak: `platform/data/verification/raw/{bybit,hyperliquid}` since 2026-09-29 12:59Z; `data/catalog/data/{mark_price_update,index_price_update,funding_rate_update,custom_open_interest,crypto_perpetual,currency_pair}`; `data/coverage/*.jsonl`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/verification/domain/derivs_check.py` (new, pure) -- the frame and poll parsers; `ReferenceStream` (updates, state at t, gaps); `match_keyed`, `match_by_receive`, `match_poll_window`; the update classification with Bybit funding `unchanged`/`next_time_only`; `rest_agreement`; `poll_coverage`; the label check; the per-type/per-instrument/day report dataclasses with `passed`; the constants with their justifications.
- [x] `platform/verification/domain/instrument_check.py` (new, pure) -- `VenueDefinition` from the Bybit linear/spot `instruments-info` and the Hyperliquid meta universe; `StoredDefinition`; `judge_polls` (in force, `agree`/`differs`/`before_first_definition`, `venue_change`).
- [x] `platform/verification/infrastructure/derivs_reader.py` (new) -- `DerivsCatalog`: `prices(type, iid, start, end)` (with each file's label), `funding`, `open_interest`, `definitions(iid)`, `spot_rows(start, end)`; refusals as specified.
- [x] `platform/verification/application/derivs.py` (new) -- the ports, the per-instrument orchestration, `render_text` (REST validation first, then per type coverage with its expected ratio, row classes, labels and `ts` rule; then definitions; then spot) and `report_json`.
- [x] `platform/verification/derivs.py` (new root) -- `--venue --day [--json] [--raw-dir] [--catalog]`, exit 0/1; the refusals and crashes ledgered.
- [x] `platform/verification/application/sites.py`, `verification/tests/test_sites.py` -- `DERIVS_REFUSED`.
- [x] `platform/tests/test_boundaries.py` -- the root and the import smoke.
- [x] `platform/verification/tests/test_derivs.py` (new) -- end to end over a real catalog (`write_data` of real `MarkPriceUpdate`/`IndexPriceUpdate`/`FundingRateUpdate`/`OpenInterest`/instruments) and raw lines: every I/O row. Each planted defect asserts a non-zero failing count and exit 1. Unit tests cover each matcher, the funding cache emulation, REST classes, poll gaps, labels, `ts` rules, and each definition field's mapping per venue.
- [x] Real-data smoke -- `main(..., clock=<closed>)` over the soak day for both venues, with a symlinked raw root of the closed hours (as in 31.5). Record per instrument and type:
  - REST agreement, coverage against the expected ratio, row classes, labels, `ts` rule and spot;
  - the definition verdicts.

  Root-cause every non-zero failing count: fix it, or register it OPEN with a follow-up story.
- [x] Docs:
  - `platform/docs/DATA_DICTIONARY.md`:
    - §1.4/1.5/1.6/1.8/1.10 gain the Bybit/Hyperliquid source, filter, `ts_event` rule, precision and null-field semantics;
    - a new §1.19 for the tool (classes, rules, bounds, repro).
  - `docs/VERIFICATION_REPORT.md`: the mark/index, funding, OI and definition rows filled with the smoke numbers, pending 31.11.
  - `docs/DATA_INTEGRITY_AUDIT.md` from D-103:
    - Bybit delta funding rows with null `interval`/`next_funding_ns`;
    - the Rust `next_time_only` drop;
    - Bybit OI `ts_event` as a poll wall clock;
    - Hyperliquid `lot_size` default;
    - the change-only storage, which consumers must forward-fill;
    - every smoke finding.
  - `platform/CLAUDE.md` DATA-02: add `verification.derivs` to the list of tools, if DATA-02 lists them.

**Acceptance Criteria:**
- Given a closed day, when `python3 -m verification.derivs --venue V --day D` runs, then per instrument and type it prints the REST validation, the coverage with its expected ratio, the row classes, the precision labels and the `ts_event` rule, then the definition verdicts and the spot assertion. It exits 1 exactly when a failing count is non-zero, a reference is unvalidated, or the inputs are incomplete.
- Given `tests/test_boundaries.py`, when run, then `verification.derivs` imports nothing denied.
- Given the story ships, then every smoke finding has an audit row and the dictionary states each stream's `ts_event` rule and storage filter.

## Design Notes

Measured during planning (soak, 2026-09-29 13:00-17:00Z):
- **Bybit BTCUSDT-LINEAR:**
  - mark: 12,230 frames with the field, 12,230 rows, 0 mismatch;
  - index: 30,314 frames, 30,314 rows, 0 mismatch;
  - funding: 99 frames, 99 rows;
  - label 2 everywhere.
- **REST tickers:** all 5 fields equal the WS state at the response `time`, 561 of 561 polls, except ETH index 5 and mark 1, which match the previous update (bracket).
- **Bybit OI:** 48 rows, 300.4-301.2 s apart. Each equals the WS state at `ts_event` or within 623 ms before it, i.e. the poll's latency. REST `openInterest` equals the WS state at `time` in 555 of 555 polls.
- **Hyperliquid SOL:** reference changes equal the rows exactly (mark 3,620, index 4,077, funding 1,411, OI 4,869). The nearest-frame pairing mis-pairs 1-2, which is why `match_by_receive` requires value equality.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests tests/test_boundaries.py -q` -- expected: all pass.
- `cd platform && ruff format --check verification tests && ruff check verification tests/test_boundaries.py && mypy verification` -- expected: clean.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 14: (high 0, medium 4, low 10)
- defer: 0
- reject: 5: (high 0, medium 0, low 5)
- addressed_findings:
  - `[medium]` `[patch]` Hyperliquid midnight edge: capture's clock and the recorder's differ, so an update near 00:00 could read `not_stored`. Rows are now read 1 s beyond the day to consume updates; only the in-day rows are judged.
  - `[medium]` `[patch]` Any `restart` run touching an OI poll gap explained all of it. Restart spans are now subtracted, and what remains must stay within 1.5 × the period.
  - `[medium]` `[patch]` Rows in a recorder gap or its 5 s margins were never compared. The recorded frame is now tried first, and `reference_unavailable` applies only when nothing is recorded to compare against.
  - `[medium]` `[patch]` A Bybit collector-restart funding row, with non-null interval/next and no known reference state, read `unmatched`. It is now `reference_unavailable`; a known differing component still fails.
  - `[low]` `[patch]` Venue oddities no longer refuse the day. These are counted `failed` polls instead: a Hyperliquid frame of another coin (filtered out before the strict parse), an empty Bybit `result.list`, a refusal line, and a coin missing from the Hyperliquid universe.
  - `[low]` `[patch]` Dated futures: `crypto_future` definitions are now read, and an empty ticker field emulates the Rust behaviour (no row written).
  - `[low]` `[patch]` Known limits added:
    - the OI period is taken from today's config;
    - `OI_POLL_WINDOW_NS` assumes zero clock skew. Measured `recv − ts` over 261,598 frames: 80-411 ms, never negative.
  - `[low]` `[patch]` Other fixes:
    - REST bracket neighbours no longer cross a reset or gap;
    - the label is checked against every definition in force over each file's rows;
    - the row-rate and memory sizing are corrected;
    - Hyperliquid updates are sorted before bisecting;
    - a funding `TypeError` is now a refusal.
  - `[low]` `[patch]` 16 new tests for the uncovered paths: off_grid, duplicate, next_time_only, null-component mismatch, the midnight case, another coin, and an empty rate.
  - Rejected:
    - a single agreeing poll validating the reference: this is the spec's and 31.5's rule, and Bybit measured always agreeing or bracketing;
    - the duplicate check against the per-key multiset: the venue never repeats a `ts` per topic (measured 0);
    - a truncated neighbour file: `recorder_gaps` already opens a gap from the last complete line to the restart's `open`;
    - rows polled more often than the period: speculative;
    - resetting the funding emulation at recorder reconnects: the Rust cache resets on the collector's reconnect, not the recorder's.

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 1, low 5)
- defer: 0
- reject: 18: (high 0, medium 0, low 18)
- addressed_findings:
  - `[medium]` `[patch]` Neighbour-day labels: Hyperliquid mark/index rows are read `HL_MATCH_BOUND_NS` past the day, and every file holding one joined the day's label set. A neighbour day's file at another precision failed `labels > 1`. `DerivsCatalog.prices(..., day)` now labels only files with an in-day row, tested (audit D-111).
  - `[low]` `[patch]` A Bybit `-INVERSE` id was judged by the spot definition rules and reported as spot. `check_day` now refuses a category outside `BYBIT_CATEGORIES`, tested.
  - `[low]` `[patch]` `_step` checked only the whole part for ASCII, so a non-ASCII fraction digit passed. The whole text is now checked, tested.
  - `[low]` `[patch]` The `DerivsSource` port now documents that `definitions` are sorted by `ts_init`, which `in_force` and `_precisions_over` rely on.
  - `[low]` `[patch]` §1.19's refusal list now names a non-decimal watched field in the checked instrument's own WS frame, which is refused, not counted.
  - `[low]` `[patch]` Doc consistency:
    - the runtimes now agree between `DATA_DICTIONARY.md` and `VERIFICATION_REPORT.md`;
    - the funding rows' 100th/63rd row, the collector's startup row, is now accounted for.
  - Smoke rerun (hours 12..17) found one `not_stored`: Hyperliquid SOL index at 17:50:52Z. It is root-caused as the 1 s match bound, not a capture loss: capture stored the value +1,048 ms after the recorder, one push later, against p99.9 ≤ 394 ms over ~16.7k paired rows. The bound is an intent-contract constant, so it is registered OPEN as audit D-112 with a follow-up, and the in-code `Known limit` is corrected.
  - Rejected, each with its reason:
    - the spec's own rules: `reference_unavailable` non-failing; HL `agree_state` within the bound; ValueError → refusal, the same as book/trades; `priceScale` as linear precision; every venue change listed;
    - the reset lookup by venue key: the 5 s gap margin covers the ≤ 411 ms receipt skew;
    - the funding emulation across recorder resets and a same-`ts` duplicate: both rejected with evidence last pass;
    - the OI window around resets: a frame's own value at its own key is known;
    - `--venue DYDX`: `VENUES` is Bybit/HL only;
    - an empty plan: a plan that records nothing, the same in every tool;
    - HL spot ids, the neighbour-file truncation, and the feed-loss second edge: speculative or rejected earlier;
    - double JSON parse, `plan` wording, CurrencyPair columns: the smoke reads real spot definitions (601 agree).

## Auto Run Result

Status: done

- **Change** (follow-up review of the done story, on top of 93cffd64f7):
  - Hyperliquid mark/index labels count only files with a row in the day.
  - A Bybit category the recorder does not record is refused.
  - A venue step must be ASCII throughout.
  - The port documents that definitions are sorted.
  - The docs are made consistent.
  - The smoke's one new failing count is registered OPEN (D-112).
- **Files:**
  - `platform/verification/infrastructure/derivs_reader.py`: `prices(..., day)` labels in-day files only.
  - `platform/verification/application/derivs.py`: the port signature and ordering doc; `_refuse_unrecorded`.
  - `platform/verification/domain/instrument_check.py`: the `_step` ASCII check.
  - `platform/verification/domain/derivs_check.py`: the `HL_MATCH_BOUND_NS` Known limit, corrected and citing D-112.
  - `platform/verification/tests/test_derivs.py`: 3 new tests.
  - `platform/docs/DATA_INTEGRITY_AUDIT.md`: D-111 (fixed), D-112 (OPEN).
  - `platform/docs/DATA_DICTIONARY.md`: §1.19 refusals, bounds and cost.
  - `platform/docs/VERIFICATION_REPORT.md`: the funding startup rows, the SOL index row, and the 18:45Z rerun.
- **Review:** 6 patches applied (1 medium, 5 low), 0 deferred, 18 rejected, 0 intent_gap, 0 bad_spec.
- **Smoke rerun:** 18:45Z, raw hours 12..17, symlinked.
  - Bybit: every value failing count is 0, definitions agree 601 each, spot 0 fabricated; it exits 1 on the partial day's inputs only.
  - Hyperliquid: every count is 0 except SOL index `not_stored` 1 (D-112).
- **Verification:**
  - `python3 -m pytest -o addopts="" --rootdir=. verification/tests tests/test_boundaries.py -q`: 500 passed.
  - `ruff format --check verification tests`: clean.
  - `ruff check verification tests/test_boundaries.py`: clean.
  - `mypy verification`: clean.
- **Residual risks:**
  - D-112 (OPEN): a real day will likely show a few Hyperliquid `not_stored` false fails until the match bound is re-measured and changed. That is an intent-contract constant, left for Story 31.11 or a follow-up.
  - The real-day verdict belongs to Story 31.11.
  - The OI period is read from the current config.
