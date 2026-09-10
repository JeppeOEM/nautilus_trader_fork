---
baseline_commit: f97e4a06929be23a68d40eb525d139236ed42438
---

<!-- Standalone bug-fix story, no epics.md entry -- same precedent as epics 5/6/7/9
     (sprint-status.yaml's own comments document this precedent). Numbered 11 (not folded
     into Epic 10) because Epic 10 is already marked done in sprint-status.yaml, and this
     bug is unrelated to Epic 10's indicator-picker work -- it affects the four fixed rows
     Epic 10 explicitly left out of scope (see dashboard.py:1125's docstring). -->

# Story 11.1: Fix empty book-imbalance/mid-imbalance/depth/spread panes on the chart page

Status: done

## Story

As a user of the `/chart/{id}` page,
I want the "Book imbalance L1 agg", "Mid-layer imbalance", "Depth", and "Spread" panes to actually plot data,
so that these four fixed rows are not permanently blank regardless of instrument or time range.

## Acceptance Criteria

1. **Root cause confirmed as a stale data dependency, not a rendering bug.** `ml_signals/chart_data.py`'s `compute_chart_series()` calls `catalog.order_book_deltas(instrument_ids=[instrument_id], start=start_ns, end=end_ns)` and returns empty series when there are no deltas. `OrderBookDeltas` are only written to the catalog for an instrument when `dydx_collector/config.py`'s `store_order_book_deltas` is `True` (default `False`) -- confirmed via `grep` that no instrument in the live `dydx_collector/config.toml` sets this flag, and the collector's own signal architecture (troll/CLAUDE.md's "Signal Architecture: 1s-Based, Not Event-Driven") stores per-second `DydxSecondSnapshot` book state instead of raw deltas. `chart_data.py` was never migrated to the snapshot-based architecture when it was adopted, so `order_book_deltas()` always returns an empty list for every instrument in production, and these four panes are unconditionally blank.
2. **`compute_chart_series()` is rewritten to read `DydxSecondSnapshot` records** (`dydx_collector/second_snapshot.py`) from the catalog for the requested window, via `catalog.query(data_cls=DydxSecondSnapshot, identifiers=[instrument_id], start=start_ns, end=end_ns)` -- the same query already used by `dashboard._historical_lines_json()` -- instead of replaying raw `OrderBookDeltas` through an `OrderBook`. No new data collection or schema change; this only changes which already-stored data type the chart page reads.
3. **Imbalance/depth values are computed from each snapshot's stored top-N levels** (`bid_prices`/`bid_sizes`/`ask_prices`/`ask_sizes`, already up to 20 levels per SIGNAL-01) via a `book_features.DepthProfile` built directly from those lists (sliced to `levels=10`, matching the prior `OrderBook`-derived default) and `book_features.book_imbalance()` -- reusing the exact same pure functions the old `OrderBook`-based path called internally (`compute_features()` did `depth_profile(book, levels)` then `book_imbalance(profile)`; this story skips the `OrderBook` step and builds the `DepthProfile` straight from the snapshot's lists). No new imbalance/depth math is introduced (SSOT-01/DESIGN-03).
4. **Spread and microprice use the same crossed-book guard as every other snapshot consumer.** A snapshot where `bid_prices[0] >= ask_prices[0]` is skipped entirely (matching `dashboard._price_series_rows`' existing guard and `book_features.top_of_book_series`'s comment on why -- DATA-04, transient crossed states during reconnect replay must not leak a negative spread onto the chart). Microprice is computed via `ml_signals.indicators.microprice()` (the existing SSOT-01 stateless helper, already used elsewhere in `dashboard.py`) rather than re-instantiating the stateful `Microprice` indicator class per snapshot.
5. **Mid-layer imbalance (levels 2-3) is preserved unchanged in meaning**: still `(per_level[1] + per_level[2]) / 2` from `book_imbalance()`'s output, only appended when the profile actually has >= 3 levels (mirrors the old `features.depth.levels >= 3` guard).
6. **No regressions.** `cd troll && python -m pytest ml_signals/tests/ -q` passes, including a new `test_chart_data.py` covering `compute_chart_series()` against a real `ParquetDataCatalog` (TEST-01/TEST-03 -- this function had zero test coverage before this story, confirmed via `grep` across `ml_signals/tests/`).
7. **`_render_chart_page`'s row count/labels/hline placements are untouched** -- this is a data-source fix only, not a UI change. The existing `test_render_chart_page_figure_row_counts_stay_in_lockstep` test (which monkeypatches `compute_chart_series` to an empty-series stub) must keep passing unmodified.

## Tasks / Subtasks

- [x] Task 1 — Confirm root cause (AC: #1)
  - [x] Confirmed `store_order_book_deltas` defaults to `False` in `dydx_collector/config.py` and is not set to `True` anywhere in `dydx_collector/config.toml` (`grep -rn store_order_book_deltas` across `dydx_collector/`) -- so `catalog.order_book_deltas()` is empty for every instrument in the live catalog.
  - [x] Confirmed `dashboard._historical_lines_json()` already reads `DydxSecondSnapshot` via `catalog.query(data_cls=DydxSecondSnapshot, ...)` for the same catalog -- proving snapshot data does exist and is queryable; `chart_data.py` is the only chart-page data path still trying (and failing) to read raw deltas.
- [x] Task 2 — Rewrite `compute_chart_series()` to read `DydxSecondSnapshot` (AC: #2, #3, #4, #5)
  - [x] Replaced `catalog.order_book_deltas(...)` + `OrderBook`/`BookType` replay with `catalog.query(data_cls=DydxSecondSnapshot, identifiers=[instrument_id], start=start_ns, end=end_ns)`, unwrapping `CustomData` via `.data` (same pattern as `_historical_lines_json`).
  - [x] Sort snapshots by `ts_event`, skip crossed/touched snapshots (`bid_prices[0] >= ask_prices[0]`) and snapshots with an empty side.
  - [x] Build `DepthProfile(bid_prices[:10], bid_sizes[:10], ask_prices[:10], ask_sizes[:10])` per snapshot and call `book_imbalance()`/`profile.total_bid_depth()`/`profile.total_ask_depth()` directly -- no `OrderBook` construction needed since the snapshot already stores the flattened level lists.
  - [x] Microprice via `ml_signals.indicators.microprice({"bid_prices":..., "bid_sizes":..., "ask_prices":..., "ask_sizes":...})`, appended only when not `None`.
  - [x] Removed now-unused imports (`OrderBook`, `BookType`, `InstrumentId`) from `chart_data.py`.
- [x] Task 3 — Tests (AC: #6)
  - [x] New `ml_signals/tests/test_chart_data.py`: writes real `DydxSecondSnapshot` objects into a `tmp_path` `ParquetDataCatalog` via `write_data()`, calls `compute_chart_series()`, and asserts each series (`imbalance`, `mid_imbalance`, `bid_depth`, `ask_depth`, `spread`, `microprice`) has the expected values for a known synthetic book. Also covers: empty catalog window -> all-empty series; a crossed snapshot is skipped; a 2-level-only (thin) snapshot omits `mid_imbalance` but still populates the other rows.
  - [x] `cd troll && python -m pytest ml_signals/tests/ -q` -- full suite passes, no regressions.

### Review Findings

- [x] [Review][Patch] `mid_imbalance`'s guard used `profile.levels` (== `len(bid_prices)`), but `imbalance.per_level` is `zip(bid_sizes, ask_sizes)` and so has length `min(len(bid), len(ask))` — on a thin/illiquid side where bid and ask level counts differ (`DydxSecondSnapshot`'s own docstring: "shorter for illiquid coins"), a bid count `>= 3` with fewer ask levels would index `per_level[2]` past the end of a shorter list → `IndexError`, capable of 500-ing `/chart/{id}` for any coin whose snapshot has mismatched per-side depth. This was a latent bug in the pre-existing `OrderBook`-based code too (same `features.depth.levels >= 3` shape), but this story's data source (real per-second snapshots) makes an unequal bid/ask level count materially more likely than the old code's live `OrderBook.bids()/asks()` reads. `troll/ml_signals/chart_data.py` — fixed: guard now checks `len(imbalance.per_level) >= 3` instead of `profile.levels >= 3`. Covered by new `test_compute_chart_series_thin_ask_side_does_not_crash_or_populate_mid_imbalance`.
- [x] [Review][Patch] `test_compute_chart_series_orders_multiple_snapshots_by_time`'s docstring/comment claimed the assertion proves `compute_chart_series`'s own `sorted()` call is load-bearing, but verified by temporarily removing that `sorted()` call and re-running the test in isolation — it still passed, because `ParquetDataCatalog.query()` already returns chronologically-ordered rows regardless of write-batch order. The test wasn't false — its assertion (chronological output) is still a real, worthwhile guarantee for the chart page — but its framing overclaimed which layer provides it. `troll/ml_signals/tests/test_chart_data.py` — fixed: renamed to `test_compute_chart_series_output_is_chronological_across_write_batches` and reworded the docstring to state the property under test (chronological output survives out-of-order writes) without asserting which layer (catalog read order vs. the function's own defensive `sorted()`) is doing the work.
- [x] [Review][Defer] `test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure` fails on a clean checkout of `baseline_commit` (verified via `git stash`) — pre-existing, unrelated to this story (no code path this story touches is exercised by that test). Not fixed here; flagging so it isn't mistaken for a regression introduced by this change.

## Dev Notes

- **Single-file fix, contained to `troll/ml_signals/chart_data.py`.** No changes to `dydx_collector/`, `dashboard.py`'s rendering code, or `book_features.py`/`indicators.py` (both already have everything needed as public, reusable, pure functions -- DESIGN-03/SSOT-01: reuse, don't reimplement).
- **Why raw deltas were ever the design in the first place:** `chart_data.py`'s original docstring ("Replays OrderBookDelta from the catalog for a time range... Performance note: replaying a large delta range (full day) takes seconds") predates the collector's pivot to 1-second-snapshot-based signal architecture (troll/CLAUDE.md's "Signal Architecture: 1s-Based, Not Event-Driven" section). That pivot changed what's actually persisted (`DydxSecondSnapshot`, opt-in raw deltas off by default) but `chart_data.py` was never updated to match -- an orphaned code path, not a regression introduced recently.
- **This also makes the chart page faster, incidentally**: replaying every raw delta through an `OrderBook` for a 4-hour window is far more expensive than iterating up to 14,400 pre-computed 1-second snapshots. Not the point of this story, but worth noting in case anyone assumes the fix is a performance regression.
- **`book_features.DepthProfile` takes plain lists, not an `OrderBook`** -- confirmed by reading `book_features.py` in full this session. `depth_profile(book, levels)` is just a thin `OrderBook` -> `DepthProfile` adapter; `book_imbalance(profile)`/`liquidity_distance(profile)` never touch `OrderBook` at all. This story bypasses `depth_profile()`/`compute_features()` entirely and constructs `DepthProfile` directly from the snapshot's own lists, since there is no `OrderBook` in this path anymore.
- **Reuse `ml_signals.indicators.microprice()`, not the `Microprice` indicator class.** The stateful class was used in the old delta-replay loop because it was fed one raw tick at a time from an evolving `OrderBook`; now that each snapshot is already a fully-formed top-of-book read, the stateless per-snapshot `microprice(dict)` helper (already the established pattern in `dashboard._price_series_rows`, SSOT-01) is the correct fit -- do not keep the stateful class around for this call site.
- **Crossed-book skip is a filter, never a resync (DATA-03/DATA-04).** A crossed snapshot is simply omitted from these chart series -- there is no book to resync here, `chart_data.py` only ever reads already-persisted read-only snapshots.
- **`troll/CLAUDE.md` constraints that apply:** SIGNAL-01 (signals derived from stored snapshot levels, nothing new stored), SSOT-01 (microprice via the existing shared stateless function), DESIGN-03 (delete the now-dead `OrderBook`-replay path rather than leaving it unreachable behind a flag), TEST-01 (this function does real financial-data computation and touches the catalog -- needs real coverage, not a mock).
- **Do not touch `_render_chart_page`'s figure/layout code** -- `data.get(series_key)` truthiness checks there already handle an empty list gracefully (skips `add_trace`/`add_hline`); nothing about the four-row Plotly layout needs to change for this fix.

### Project Structure Notes

- Modified: `troll/ml_signals/chart_data.py` (full rewrite of `compute_chart_series()`'s data source; public signature unchanged).
- New: `troll/ml_signals/tests/test_chart_data.py`.
- No changes to `dydx_collector/`, `bot_tui/`, `live_paper/`, `ranking_engine/`.

### References

- [Source: troll/ml_signals/chart_data.py] — full file read this session; confirmed it's the sole source for the four affected rows via `dashboard._render_chart_page`.
- [Source: troll/ml_signals/dashboard.py:1114-1237] — `_render_chart_page`, confirms rows 1-4 are fed exclusively by `_chart_data.compute_chart_series()` and that Epic 10 explicitly left these four rows out of scope (line 1125's docstring).
- [Source: troll/ml_signals/dashboard.py:1398-1419] — `_historical_lines_json`, the existing, working `catalog.query(data_cls=DydxSecondSnapshot, ...)` pattern this story reuses verbatim.
- [Source: troll/ml_signals/book_features.py] — `DepthProfile`, `book_imbalance`, `depth_profile`, `compute_features` — full file read this session; confirmed `DepthProfile`/`book_imbalance` need no `OrderBook`.
- [Source: troll/ml_signals/indicators.py:409] — stateless `microprice(snapshot: dict)` helper, the established SSOT-01 pattern already used by `dashboard._price_series_rows`.
- [Source: troll/dydx_collector/config.py, dydx_collector/config.toml] — confirms `store_order_book_deltas` defaults `False` and is unset for every instrument in the live config.
- [Source: troll/dydx_collector/second_snapshot.py] — `DydxSecondSnapshot` schema/fields, Arrow registration.
- [Source: troll/CLAUDE.md] — Signal Architecture section (1s-snapshot-based, not event-driven), SIGNAL-01, SSOT-01, DESIGN-03, DATA-04, TEST-01/TEST-03.
- [Source: _bmad-output/implementation-artifacts/9-1-fix-oscillator-panel-shared-y-axis-scaling.md] — precedent for a standalone bypass-epic bug-fix story against a shipped Epic 8/10 chart-page feature.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `ruff check --fix` + `ruff format` (`.venv/bin/ruff`, this environment's working binary) applied to both changed files for import ordering and docstring formatting (D209/D213/I001) — no logic changes from these.
- `.venv/bin/mypy troll/ml_signals/chart_data.py` — no issues found.
- `cd troll && python -m pytest ml_signals/tests/ dydx_collector/tests/ -q` — 307 passed, 1 pre-existing unrelated failure (`test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure`, confirmed via `git stash` to fail identically at `baseline_commit` with none of this story's changes applied).

### Completion Notes List

- Root cause confirmed: `store_order_book_deltas` defaults `False` and is unset for every instrument in `dydx_collector/config.toml`, so `catalog.order_book_deltas()` was always empty in production — `chart_data.py` was never migrated to the snapshot-based signal architecture when it was adopted, leaving the four fixed panes (imbalance/mid-imbalance/depth/spread) permanently blank.
- Fix: `compute_chart_series()` now reads `DydxSecondSnapshot` via `catalog.query(...)` (same pattern as `dashboard._historical_lines_json`), builds a `book_features.DepthProfile` directly from each snapshot's stored level lists, and reuses the existing `book_imbalance()`/`microprice()` pure functions — no new math introduced, no schema change, no data recollection needed.
- A review pass caught and fixed one real latent bug the new data source made materially more likely to trigger (`per_level` length mismatch on a thin side, see Review Findings) and one misleading test framing (also see Review Findings). Both fixed before marking this story done.
- Not verified in a real browser — no display available in this environment, consistent with every prior chart-page story's (7.1/8.1/8.2/8.4/9.1) documented limitation. Recommend a manual pass: open `/chart/{id}` for any actively-subscribed instrument and confirm all four rows (Book imbalance, Mid-layer imbalance, Depth, Spread) now show data instead of empty panes.

### File List

- Modified: `troll/ml_signals/chart_data.py`
- New: `troll/ml_signals/tests/test_chart_data.py`

## Change Log

- 2026-09-10: Root cause identified (chart_data.py replaying raw OrderBookDeltas, which are never persisted in production since dydx_collector's snapshot-based architecture pivot). Rewrote `compute_chart_series()` to read `DydxSecondSnapshot` from the catalog instead; added `test_chart_data.py` (6 tests) covering the empty-window, thin-book, crossed-book, multi-level, and out-of-order-write cases. Full `ml_signals`/`dydx_collector` test suites pass except one confirmed-pre-existing unrelated failure. Self-review (Blind Hunter/Edge Case Hunter/Acceptance Auditor pass) found and fixed a latent per-level-length IndexError risk and a misleadingly-framed test; both corrected. Status: backlog → done.
