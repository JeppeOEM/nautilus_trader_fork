---
title: 'DW-217/DW-218: RankingBoard ingest guards and stale slow rows'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
final_revision: '4ffa56665c8dec9e005af15849c95bfe28904467'
baseline_revision: '5792c82c16b99461b439dec6cd41e4b4bd182229'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** `RankingBoard.slow_rows` writes a current-`ts` `metrics.db` row for an instrument that is stale (>30 s silent, <1 h), with its last price/pct/book metrics presented as current (DW-217, DATA-01). `RankingBoard.ingest` has no ordering guard beyond the price series: a duplicate or out-of-order `snapshots:raw` entry is fed to OFI/OBI, the rolling window and `VolatilityTracker` again; a NaN mid would pass `mid <= 0`; and a catalog backfill series is neither deduplicated nor checked for non-positive/non-finite prices (DW-218, DATA-02/DATA-07).

**Approach:** `slow_rows` emits rows for fresh instruments only. `ingest` gains one per-instrument strictly-ascending `ts_event` gate in front of every feed, including the freshness stamp. The mid check also refuses a non-finite mid. `PriceSeriesStore.backfill` validates its series (non-finite/non-positive prices, duplicate ts, sort) and reports every drop as a ledger detail.

## Boundaries & Constraints

**Always:** Every drop is returned as a detail string that the engine ledgers (DATA-07), never silently discarded. The board stays pure, with no clock and no I/O. Freshness is still compared against arrival time. The live price-series out-of-order guard stays as it is.

**Block If:** none. Every decision is settled below.

**Never:** Do not change the `rankings:live` payload shape or the `metrics.db` column set. Do not modify `nautilus_trader/` or `crates/`. Do not add a ledger entry for an instrument merely being stale (that is already visible as `stale_instrument_ids`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| In-order snapshot | ts_event > instrument's last ingested ts_event | fed to every tracker, freshness stamped | none |
| Duplicate snapshot | ts_event == last ingested | nothing fed: no freshness, price, OFI/OBI, rolling, volatility | one detail returned |
| Out-of-order snapshot | ts_event < last ingested | same as duplicate | one detail returned |
| One-sided snapshot then older two-sided | ts 5 (one-sided), then ts 4 | ts 4 dropped (gate is over every ingested snapshot) | detail |
| Non-finite mid | mid NaN/inf | no book tracker fed | detail |
| Stale instrument at slow loop | last_seen > 30 s, < 1 h ago | no row for it in `slow_rows` output (so none in metrics.db) | none |
| Backfill with bad points | series with dup ts / price <= 0 / NaN / unsorted | sorted, bad points removed, rest seeded/merged | one detail per drop kind |
| Clean backfill | valid ascending series | unchanged behaviour, no details | none |

</intent-contract>

## Code Map

- `platform/ranking/domain/board.py` -- `InstrumentMetrics`, `RankingBoard.ingest`/`backfill`/`slow_rows`
- `platform/ranking/domain/price_series.py` -- `PriceSeriesStore.backfill` (merge of catalog series with live points)
- `platform/ranking/domain/volatility.py` -- `VolatilityTracker.update`: assumes ascending ts; its sole caller is `ingest`
- `platform/ranking/application/engine.py` -- `_backfill_new_instruments` ledgers backfill details at `ranking_engine.price_backfill`
- `platform/ranking/infrastructure/catalog_prices.py` -- the `PriceHistory` adapter (already sorts; no change needed beyond the domain validation)
- `platform/ranking/tests/test_board.py`, `test_price_series.py`, `test_engine.py`, `support.py` -- tests and builders

## Tasks & Acceptance

**Execution:**
- [x] `platform/ranking/domain/board.py` -- add `InstrumentMetrics.last_event_ns` (None until the first snapshot). In `ingest`, before stamping freshness, drop a snapshot whose `ts_event <= last_event_ns` and return one detail. Otherwise set `last_event_ns`, stamp freshness and proceed. Extend the mid check to `not math.isfinite(mid) or mid <= 0`. `slow_rows` builds rows only for `inst.is_fresh(now_ns)`. `backfill` returns `list[str]`. Update the docstrings (the DATA-01 reason for skipping stale rows; the gate's invariant).
- [x] `platform/ranking/domain/price_series.py` -- `backfill` sorts the series and drops non-finite/non-positive prices and duplicate ts before merging. It returns `list[str]` details (one per drop kind, with count and first ts) plus the existing live/Parquet mismatch detail.
- [x] `platform/ranking/domain/volatility.py` -- document the ascending-ts precondition and the gate that holds it (`RankingBoard.ingest`).
- [x] `platform/ranking/application/engine.py` -- ledger every backfill detail.
- [x] `platform/ranking/tests/*` -- tests for each matrix row. Adapt the existing tests that re-ingest the same `ts_event` for an instrument (e.g. `mark_fresh` builders) to advance the ts, and the backfill tests to the list return.

**Acceptance Criteria:**
- Given the full ranking test suite, when run, then all pass with no warnings, and `ruff` and `mypy` are clean on the touched files.
- Given a duplicate snapshot via `RankingEngine.ingest_snapshot_batch`, when handled, then exactly one `ranking_engine.snapshot_entry` ledger entry is recorded and the ranks are unchanged.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 1, low 2)
- defer: 1 (high 0, medium 0, low 1)
- reject: 12 (high 0, medium 0, low 12)
- addressed_findings:
  - `[medium]` `[patch]` One far-future `ts_event` (broken clock, corrupt entry) became the gate's last ts and froze the instrument until age-out. Added `MAX_FUTURE_SKEW_NS` (60 s past arrival), checked in `_refused_by_gate` before the instrument is created, so a refused first snapshot leaves no fresh, empty row. Three tests added.
  - `[low]` `[patch]` Backfill duplicates with conflicting prices were lumped in with identical overlap. The detail now counts conflicting ones separately (DATA-02).
  - `[low]` `[patch]` The backfill docstring claimed every drop is reported. Reworded, and the one unreported pre-existing case is documented as a `Known limit:` with an upgrade path.

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4 (high 0, medium 1, low 3)
- defer: 0
- reject: 14 (high 0, medium 0, low 14)
- addressed_findings:
  - `[medium]` `[patch]` `MAX_FUTURE_SKEW_NS` (60 s) exceeded `STALE_NS` (30 s). An accepted lead of 31-60 s refused every real snapshot behind it unstamped, so a live instrument left the ranks, the exact freeze the bound was meant to prevent. Lowered it to 10 s. Added a test proving an accepted lead at the bound never unranks the instrument and that the gate takes real snapshots again past it, plus an assertion that the bound stays under `STALE_NS`.
  - `[low]` `[patch]` The skew comment said `ts_event` is on the same host clock as arrival. Venue mode stamps S + 0.5 s on the venue's clock, so the comment now says a lead is cross-clock skew.
  - `[low]` `[patch]` The `ingest` docstring named "a Redis redelivery, a replay" as the duplicate source. `snapshots:raw` is pub/sub and never redelivers. It now names the real sources: a second publisher (a cutover overlap) or a restarted collector re-publishing a second.
  - `[low]` `[patch]` A backward `ts_event` step (NTP, a `book_time_source` switch) refuses snapshots until the old high-water mark passes, and nothing in the code said so. Added a `Known limit:` in the `ingest` docstring with its upgrade path (reset the mark after a bounded refusal span, and ledger the reset).

## Design Notes

Gate the whole snapshot, freshness included: a redelivered or replayed old snapshot is not evidence that the market is live (DATA-01), and gating once at the top keeps one invariant ("every feed sees strictly ascending ts_event per instrument") instead of one guard per tracker. NaN cannot actually reach the board since Story 30.2, because `DydxSecondSnapshot` holds int units and the floats are `int / 10^p`. The `isfinite` check mirrors the `close_price` guard and costs nothing. Skipping (not nulling) stale slow rows makes the stale period a gap in metrics.db history, so no row exists that carries `ts` with no current value. The cached `inst.slow` keeps its own `SLOW_METRICS_MAX_AGE_NS` rule.

## Verification

**Commands:**
- `cd platform && python3 -m pytest ranking -q -W error` -- expected: all pass
- `cd platform && ruff check ranking && ruff format --check ranking && mypy ranking` -- expected: clean


## Auto Run Result

**Summary:** RankingBoard holds one per-instrument ingest gate. It drops a snapshot whole when its `ts_event` does not advance the instrument's last one, or when it lies more than `MAX_FUTURE_SKEW_NS` (10 s, under `STALE_NS`) past arrival. A dropped snapshot feeds nothing: no freshness stamp, price point, OFI/OBI, rolling window or volatility. Each drop comes back as one ledger detail. A non-finite mid is refused alongside a non-positive one. The catalog backfill series is sorted, and non-finite/non-positive prices and duplicate timestamps are dropped and reported, with conflicting duplicates counted separately. `slow_rows` writes no `metrics.db` row for a stale instrument (DATA-01). This resolves DW-217 and DW-218.

This follow-up review pass lowered the future-skew bound from 60 s to 10 s. At 60 s, an accepted lead over 30 s could still take a live instrument out of the ranks. The pass also corrected the clock and duplicate-source premises in the comments and documented the backward-step behaviour as a `Known limit:`.

**Files changed (whole story):**
- `platform/ranking/domain/board.py`: ingest gate (`last_event_ns`, `MAX_FUTURE_SKEW_NS`, `_refused_by_gate`), finite-mid check, `_ingest_close_price` extracted, fresh-only `slow_rows`, `backfill` returns a list. This pass: bound 10 s, comments, `Known limit:`.
- `platform/ranking/domain/price_series.py`: `_validated_series` (sort, bad-price and duplicate-ts drops); `backfill` returns `list[str]`.
- `platform/ranking/domain/volatility.py`: ascending-ts precondition documented.
- `platform/ranking/application/engine.py`: ledgers every backfill detail.
- `platform/ranking/tests/{support,test_board,test_price_series,test_engine,test_replay}.py`: matrix-row tests. This pass adds `test_an_accepted_lead_never_takes_a_live_instrument_out_of_the_ranks`.

**Review (this pass):** 4 patches applied, 0 deferred, 14 rejected. Rejected items include:
- the per-drop ledger volume (spec-mandated: one detail per drop, DATA-07)
- arrival-time freshness with no lower age bound (spec: freshness is compared against arrival)
- the cached `inst.slow` on return from stale (bounded by `SLOW_METRICS_MAX_AGE_NS`, per Design Notes)
- the live price-series guard, now redundant (spec: it stays as is)
- the `Known limit:` backfill omission, which is pre-existing and was handled in the prior pass

**Verification:**
- From `platform/`: `python3 -m pytest -p no:cacheprovider -o addopts="" --rootdir=. ranking -q -W error -W ignore::pytest.PytestConfigWarning` gives 188 passed.
- `.venv/bin/ruff check ranking` and `ruff format --check ranking` are clean.
- `.venv/bin/mypy ranking` reports only the 2 unused-ignore errors in `ranking/tests/test_ports.py`, which this change does not touch and which predate it.

**Residual risks:**
- A real venue/host clock lead over 10 s now drops every snapshot for that venue's instruments, each one ledgered, until the clock is fixed. This is visible and correct for a broken clock.
- A backward `ts_event` step is still refused until the old high-water mark passes (`Known limit:` in `ingest`).
