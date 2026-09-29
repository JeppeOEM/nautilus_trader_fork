---
title: 'Story 31.4: Trades proven id by id against the venue'
type: 'feature'
created: '2026-09-29'
status: 'done'
baseline_revision: 'f7c9e3ea38'
final_revision: 'ec37445d39'
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

**Problem:** Story 31.2's conservation tool proves that every reference trade *id* is archived or explained. Nothing yet proves three further things:
- that an archived trade's price, size, aggressor side and `ts_event` equal what the venue published;
- that an archive-only id is legitimate;
- that the eight trade columns of each snapshot second (OHLC, buy/sell volume, buy/sell count) equal an independent fold of the venue's own trades.

The NO_AGGRESSOR→sell convention (`kernel/fold.py:97`) has also never been checked against either venue's wire.

**Approach:** Add `python3 -m verification.trades --venue V --day D --stage live|rebuilt`. For each plan instrument, over one closed UTC day, it works one hour at a time:
1. It matches the recorder's reference trades (WS plus the Bybit REST poll) with the raw `trade_tick` Parquet, id by id, field by field.
2. It folds every exchange second in `Decimal` from the reference trades, with its own fold.
3. It compares that fold with the stored snapshot row and classifies every difference.

Everything is reported, and anything unexplained is a non-zero count and a failing exit.

## Boundaries & Constraints

**Always:**
- **Independence (DATA-02):** no import of `capture`, `candles`, `kernel.fold`, `kernel.second_snapshot`, `nautilus_pyo3` or `nautilus_trader` from non-test `verification` code.
  - The reference trades come from the raw frames, parsed by `verification.domain.conservation`. Extend `ReferenceTrade` with `price: Decimal`, `size: Decimal` (from the wire strings, never `float`), `side` (BUYER=1 / SELLER=2 / NO_AGGRESSOR=0, restated as constants), `side_token: str` and `order: tuple[int, ...]`.
  - The archive is read raw with pyarrow. Price and size are little-endian signed 128-bit fixed binaries at 10^16. The precision is `price_precision`/`size_precision` in the file's schema metadata. Decode as `Decimal(int.from_bytes(b, "little", signed=True)).scaleb(-16)`.
  - The snapshot's eight trade columns are stored integers at the row's own `price_precision`/`size_precision`.
- **Exact comparison:**
  - Price and size compare as numeric `Decimal` equality. An archived value whose raw is not a whole unit at its file's precision is counted `off_precision`.
  - Aggressor side is compared by its enum value.
  - `ts_event` must equal the venue's ms × 10^6 exactly. For Hyperliquid this is D-62's millisecond truth; for Bybit, `T`/`time` is whole ms too.
  - There is no tolerance anywhere. Latency is the only bounded quantity.
- **Id classes per instrument-day:**
  - `seen`, `matched`.
  - `missing`: reference only. Split into `missing_explained` and `missing_unexplained` by 31.2's `Explanations` over the same coverage record and archive-gap markers as conservation; every trade-loss ledger site writes one of those lines, per §1.16.
  - `extra`: archive only. `extra_explained` when the id is in a `trades_backfilled` coverage line **and** its `ts_event` lies in a recorder connection gap of that trade channel; otherwise `extra_unexplained`.
  - `duplicated`: an id stored more than once.
  - Per-field `mismatch_{price,size,side,ts_event}`.
  - `reference_conflict`: WS and REST disagree for one id.
  - `off_precision`.
- **Recorder connection gap:** built from the trade channel's own lines, which carry every connection line of their endpoint.
  - A gap is `[close/error ts_ns, next open ts_ns]`.
  - A `startup` open with no open gap before it starts a gap at that channel's previous line (a crash).
  - Each gap is widened by a restated `RECORDER_GAP_MARGIN_NS = 5 s` on both sides: subscribe time and venue-clock skew. The value is justified in a comment.
- **Latency:** the distribution of `ts_init − recv_ns` is taken over matched WS-seen ids that were not backfilled, with `recv_ns` from the first WS frame carrying the id.
  - It is a ms-resolution `Counter`. Report min/p50/p99/max.
  - `|Δ| > 60 s` counts `implausible_latency`, and that fails. Both processes stamp from the same host clock, and 60 s is the catalog read-span margin, restated.
- **Fold (the reference's own):**
  - Second = venue ms // 1000.
  - Order = `(ts_ns, recv_ns of its first WS frame, index in that frame)`. A REST-only trade uses its poll's `recv_ns` and chronological list position.
  - Open = first, close = last, high/low = max/min.
  - Buy = BUYER. Sell = everything else, the documented convention.
  - Units = `Decimal.scaleb(precision)` at the row's precisions. A non-integral value is `off_grid`, which fails.
  - An empty second has OHLC None and zeros.
- **Second classes:** a second is judged when it has a row or reference trades. Compare the row with `ref` (the reference fold) and `arc` (the same fold over the archived trades of that second).

  | Class | Condition | Fails |
  |---|---|---|
  | `exact` | row == ref | no |
  | `live_provisional` | `--stage live`, arc == ref ≠ row | no |
  | `rebuild_mismatch` | `--stage rebuilt`, arc == ref ≠ row, second not rebuild-exempt | yes |
  | `live_kept` | as `rebuild_mismatch`, but inside an archive-gap marker span, or before the instrument's first archived trade; the rebuild keeps live values there by design (§6) | no |
  | `explained_loss` | arc ≠ ref, and every id discrepancy of the second is an explained missing id | no |
  | `archive_differs` | arc ≠ ref, any other case | yes |
  | `missing_row` | ref has trades, no row, no coverage `seconds` run | yes |
  | `missing_row_explained` | ref has trades, no row, a coverage `seconds` run covers it | no |
  | `duplicate_row` | two rows in one second | yes |
- **The stage** is an explicit `--stage live|rebuilt`, because the rebuild rewrites in place and leaves no marker.
  - A live-stage report labels its verdict provisional and never claims the rebuilt guarantee.
  - The live window is between the day's 2 h settle and `nightly_at` 03:07 UTC.
  - A wrong `rebuilt` flag can only false-fail.
- **Refusals and reuse:**
  - Refusals are ledgered at the new site `verification.trades.refused`. Refused: a day not closed, missing inputs, an unreadable plan, a malformed line, a truncated raw hour of the day, or an unknown `--stage`.
  - Missing raw hours and a missing coverage record fail the day, as in conservation.
  - Reuse conservation's `RawReader`, `CoverageFiles`, `Explanations`, `trade_channels`, `wire_index`, `is_closed` and `day_hours`. Do not duplicate them.
- **Wire aggressor evidence:** the report counts `wire_no_aggressor` per instrument, with the distinct tokens. A token that is not `Buy`/`Sell`/`B`/`A` maps to NO_AGGRESSOR, is counted, and does not fail. A missing side key is a `MalformedLine`.
- **Code rules:** memory is one instrument-hour of Python objects plus one instrument-day of Arrow (MEM-01, a `Known limit:` as in conservation). Also the LGPL header, type hints, functions of about 30 lines or fewer, complexity of 10 or less, and a `Known limit:` with an upgrade path for each simplification.

**Block If:**
- None expected. This is a tool plus docs, and the soak data exists. HALT only if the soak's raw frames contradict the documented wire shape: a trade without a side, or a price that is not a decimal string.

**Never:**
- Never modify `nautilus_trader/`, `crates/`, `sprint-status.yaml` or a venue `config.toml`. Never add a dependency.
- Never import production fold or decode code on the reference side. Never absorb a difference into a tolerance.
- Never set `awaiting-operator` or `operator_actions`. Running the tool over a real closed day is Story 31.11's verdict; OPS-01 applies.
- Do not build the book, derivatives, catalog, candles or chaos tools (31.5–31.10).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean day | every reference trade archived with equal fields; rows equal the fold | all failing counts 0; exit 0 | none |
| Planted: trade removed | one archived row deleted | `missing_unexplained` = 1, its second `archive_differs`; exit 1 | none |
| Planted: size +1 unit | one archived size +10^-s | `mismatch_size` = 1, second `archive_differs`; exit 1 | none |
| Planted: ts across second | one `ts_event` moved from S to S+1 | `mismatch_ts_event` = 1, seconds S and S+1 differ; exit 1 | none |
| Backfilled extra in gap | archive-only id in `trades_backfilled`, `ts_event` inside a recorder close→open | `extra_explained` = 1; pass | none |
| Extra outside gap | archive-only id, not backfilled or no gap | `extra_unexplained` = 1; exit 1 | none |
| Live boundary effect | `--stage live`, row misses a late trade that the archive holds | `live_provisional` = 1; pass (provisional) | none |
| Same after rebuild | `--stage rebuilt`, same state | `rebuild_mismatch` = 1; exit 1 | none |
| Row in gap marker | `--stage rebuilt`, row ≠ ref inside a marker span, arc == ref | `live_kept` = 1; pass | none |
| WS/REST disagree | same id, different price | `reference_conflict` = 1; exit 1 | none |
| Unknown side token | Bybit `S: ""` | `wire_no_aggressor` = 1 (token listed); folded to sell | none |
| Open day | `--day` today | refused, ledgered | exit message, status 1 |

</intent-contract>

## Code Map

- `platform/verification/conservation.py` -- the CLI root pattern to mirror: `Refused`, `_inputs`, `_plan`, `main(argv, clock)`, ledger on refusal.
- `platform/verification/application/conservation.py` -- `Inputs`, the `ReferenceRecords`/`Archive`/`CoverageSource` ports, `collect_coverage`, `_channel_trades` (reads H-1..H+1), `is_closed`, `day_hours`, `hour_label`, `_missing_raw`.
- `platform/verification/domain/conservation.py` -- `ReferenceTrade`, `_bybit_ws`/`_bybit_rest`/`_hyperliquid_ws`, `reference_trades`, `Explanations`, `Intervals`, `TradeWindow`, `Backfilled`, `instrument_category`.
- `platform/verification/infrastructure/catalog_reader.py` -- `read_day`/`_read_window` (row-group pruning by `ts_event`), `ArrowArchivedTrades`, `ParquetArchive`, `CoverageFiles`.
- `platform/verification/infrastructure/raw_store.py` -- `RawReader`, `iter_records`, `TruncatedTail`.
- `platform/verification/application/sites.py` -- ledger site constants; `verification/tests/test_sites.py` pins them.
- `platform/kernel/fold.py:88-108` -- production fold, for documentation only. Ties keep arrival order; non-BUYER goes to sell.
- `platform/archive/application/rebuild_day.py` -- the in-place rebuild. Rows before coverage and inside gap markers keep live values.
- `platform/capture/venues/hyperliquid/client.py:117-137` -- `_at_exact_millis` (D-62).
- `platform/tests/test_boundaries.py:~2000-2133` -- the verification roots list and `test_importing_the_verification_roots_loads_no_denied_module`.
- `platform/verification/tests/test_conservation.py` -- the helpers to reuse: raw-line builders, `_write_raw`, a real catalog via `ParquetDataCatalog.write_data`, and `make_snapshot`.
- Soak data: `platform/data/verification/raw/{bybit,hyperliquid}` and `platform/data/catalog/data/{trade_tick,custom_dydx_second_snapshot}` for 5 instruments, since 2026-09-29 ~12:59 UTC.

## Tasks & Acceptance

**Execution:**
- [x] `platform/verification/domain/conservation.py` -- extend `ReferenceTrade` and the three item parsers with price, size, side, side token and order key. Values are strict decimal strings; a non-string or non-decimal value is a `MalformedLine`. Conservation's behaviour is otherwise unchanged.
- [x] `platform/verification/domain/trade_check.py` (new, pure) -- in stdlib `Decimal`:
  - `ArchivedTrade` and `TradeColumns`.
  - `fold_second` (the reference fold, units at given precisions, `OffGrid`).
  - `recorder_gaps(lines)` → `Intervals`.
  - `compare_ids(...)` → `IdCounts`, with examples.
  - `classify_second(...)` → the class.
  - `LatencyHistogram`, and the `InstrumentTrades`/`TradesDayReport` dataclasses with a `passed` property.
- [x] `platform/verification/infrastructure/catalog_reader.py`:
  - `ParquetArchive.trade_values(iid, start, end)`: an Arrow-backed day, sorted by `ts_event`, whose `hour(h)` yields `ArchivedTrade`s decoded per file precision, and which exposes `first_ts_event`.
  - `snapshot_trade_rows(iid, start, end)`: per-hour `second → [TradeColumns with precisions]`.
- [x] `platform/verification/application/trades.py` (new) -- the ports and per-instrument, per-hour orchestration, reusing conservation's helpers. Also `render_text` and `report_json`, and a `Known limit:` for memory and for the plan-is-current rule.
- [x] `platform/verification/trades.py` (new root) -- the CLI (`--venue --day --stage [--json] [--raw-dir] [--catalog]`), exit 0/1, with refusals ledgered.
- [x] `platform/verification/application/sites.py` -- add `TRADES_REFUSED = "verification.trades.refused"`, and update `test_sites.py` if it pins the set.
- [x] `platform/tests/test_boundaries.py` -- register `verification.trades` wherever `verification.conservation` is registered (the roots list and the import-smoke subprocess).
- [x] `platform/verification/tests/test_trades.py` (new) -- run end to end on a real catalog, `write_data` of `TradeTick`s and snapshots, plus raw lines:
  - the clean day passes;
  - every I/O-matrix row, including the three planted defects, each asserting a non-zero count and exit 1;
  - unit tests of `fold_second` (ordering ties, NO_AGGRESSOR→sell, empty, off-grid), `recorder_gaps` (close→open, crash startup, margin) and the archived decode (a negative raw, precision metadata).
- [x] `platform/verification/tests/test_conservation.py` -- still green, and it covers a malformed price string.
- [x] Real-data smoke -- run the tool through `main(..., clock=<fake closed>)` in a scratch script against the soak for today's complete hours, using `--stage live`, for both venues. Record the counts, the NO_AGGRESSOR wire evidence and the latency p50/p99. Investigate every non-zero failing count to root cause (DATA-02): either fix it, or register it OPEN with a follow-up story.
- [x] Docs:
  - `platform/docs/DATA_DICTIONARY.md` -- a new §1.17 on the trades tool (classes, stage, gap rule, fold order, latency bound, repro), and in §1.1 the NO_AGGRESSOR wire evidence.
  - `platform/docs/VERIFICATION_REPORT.md` -- a row "Trades id-by-id + second fold", all 5, pending the 31.11 verdict, with the smoke numbers.
  - `platform/docs/DATA_INTEGRITY_AUDIT.md` -- D-91 onward, one per finding or decision (at least the NO_AGGRESSOR confirmation and the stage decision).

**Acceptance Criteria:**
- Given a closed day's raw frames and catalog, when `python3 -m verification.trades --venue V --day D --stage S` runs, then per instrument it prints every id class, the field mismatches, the latency distribution and every second class, and it exits 1 exactly when a failing count is non-zero or the inputs are incomplete.
- Given each planted defect, when the tests run, then the report is non-zero and the test asserts it.
- Given `tests/test_boundaries.py`, when run, then `verification.trades` imports nothing denied, and it is a registered root.
- Given the soak's wire, when the smoke runs, then the NO_AGGRESSOR→sell convention's relevance is stated with counts per venue in DATA_DICTIONARY and the audit.

## Design Notes

**Why `--stage` is explicit.** `rebuild_seconds` rewrites rows in place with no marker, and `state.json` is a cursor, not a verdict. Inferring the stage could let a rebuilt day pass as `live_provisional`. An explicit `rebuilt` flag on a day that was not rebuilt can only false-fail. Story 31.11's nightly `verify_day` runs after `compare_klines` with `--stage rebuilt`.

**Why `arc` is folded too.** Comparing the row with a fold of the archived trades separates "the rebuild will fix this" from "the archive itself is wrong". `arc` reuses the reference fold on archive values, so no production code is involved.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests tests/test_boundaries.py -q` -- expected: all pass.
- `cd platform && ruff format --check verification tests && ruff check verification tests/test_boundaries.py && mypy verification` -- expected: clean.


## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3: (high 0, medium 1, low 2)
- defer: 0
- reject: 16: (high 0, medium 0, low 16)
- addressed_findings:
  - `[medium]` `[patch]` Hyperliquid's `trades` subscribe answers with the recent trades. The soak's startup frame held trades 55 s older than its `open`. After a recorder restart mid-day, the first WS copy of a trade from inside the recorder gap is therefore a replay, and its `ts_init − recv_ns` false-fails `implausible_latency`. Fixed: `trade_check._latency` skips matched ids whose `ts_event` lies in the instrument's recorder gaps. Added a unit test, the DATA_DICTIONARY §1.17 Latency text and audit D-95.
  - `[low]` `[patch]` A live-stage FAIL printed "PROVISIONAL … rerun with --stage rebuilt", which suggests the rebuild could clear a failure it never corrects. Now only a passing live verdict is labelled provisional, and a live FAIL says none of its failures is one the rebuild corrects. The text-report test was updated and a passing-live test added.
  - `[low]` `[patch]` DATA_DICTIONARY §1.17 claimed failing examples are "in venue-time order", but they are ordered hour by hour, by kind, then by venue time. The doc now states the real deterministic order.
  - Tried and reverted: a shared module-level `Decimal` `Context` (a performance nit). `test_boundaries.py`'s no-module-level-mutable-state rule forbids it, and a per-call context costs microseconds.

## Auto Run Result

Status: done

**Summary:** This was a follow-up review pass on the done Story 31.4 (`python3 -m verification.trades`, committed at 7847469ed4). The Blind Hunter and the Edge Case Hunter reviewed the full change since f7c9e3ea38 and raised 19 findings, deduplicated to 19 items.
- **3 patched:**
  - HL replay latency false-fail (medium), audit D-95;
  - the live FAIL verdict wording (low);
  - the example-order doc claim (low).
- **0 deferred.**
- **16 rejected** as by spec, conservative, or already documented:
  - exit 1 shared by refusals and failures;
  - the `ValueError` refusal catch, the same as conservation's;
  - no clock bound on `--stage live` (D-94);
  - `live_kept` with no archive, as the spec's §6 rule says;
  - conservation's strict parsing, as the spec's task says;
  - `off_grid` masking other columns (the second fails anyway);
  - no examples for `duplicated`;
  - recorder gaps being per channel, as the spec says;
  - the smoke's repro, already documented in VERIFICATION_REPORT;
  - `order[1]` coupling (speculative);
  - the `Context` cost (the patch was reverted under the boundary rule);
  - the `first_trade_ts` floor, which can only false-fail and has a documented `Known limit:`;
  - test-gap wishes;
  - a crashed recorder never restarted, which is a conservative false-fail when the raw is incomplete;
  - an empty plan passing vacuously, as conservation does;
  - the conservation regression, a duplicate of the strict-parsing item.

**Files changed (this pass):**
- `platform/verification/domain/trade_check.py`: `_latency` skips ids inside recorder gaps, and the margin comment is updated.
- `platform/verification/application/trades.py`: `_verdict` marks only a passing live verdict provisional.
- `platform/verification/tests/test_trades.py`: a replay-latency unit test, a passing-live verdict test, and the updated FAIL text assertion.
- `platform/docs/DATA_DICTIONARY.md`: §1.17 Latency exclusion, and the accurate example order.
- `platform/docs/DATA_INTEGRITY_AUDIT.md`: D-95 (FIXED).

**Follow-up review recommended:** false. The fixes are localized: one narrowing of the latency sample, backed by wire evidence and a test, plus wording and docs.

**Verification:**
- `python3 -m pytest -o addopts="" --rootdir=. verification/tests tests/test_boundaries.py -q`: 359 passed.
- `ruff format --check verification tests` and `ruff check verification tests/test_boundaries.py`: clean.
- `mypy verification`: no issues in 39 source files.

**Residual risks:** unchanged from the implementation run.
- No real closed day has run end to end yet; Story 31.11 gives the verdict.
- D-91 and D-92 are OPEN.
- A REST-only same-millisecond tie can false-fail (a Known limit).
- The D-95 fix assumes the recorder's connection lines bound every replay. A venue replay older than the gap's 5 s margin before the `close` would still be taken from the recorder's live copy, which is correct.
