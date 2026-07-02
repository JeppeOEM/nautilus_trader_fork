---
baseline_commit: 60b238b5ed970441b1140ae1013021aece75ad1c
---

# Story 1.2: Default the ranking table to volume-sort

Status: in-progress

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the builder,
I want the rankings view to default to descending `volume24H` order,
so that I immediately see the highest-opportunity coins without manually choosing a sort.

## Acceptance Criteria

1. **Default volume-sort on initial render.** Given the dashboard rankings page loads with no user-applied sort, when the initial table renders, then rows are ordered strictly by descending `volume24H` (USD).
2. **Sort stays live, not static.** Given the rankings table, when it refreshes on each 1s poll, then the volume-sorted order updates to reflect newly arrived data, not a one-time computation.
3. **Indicator columns don't affect sort.** Given HFT indicators (OFI, OBI, microprice, spread) and TA indicators computed per coin, when displayed in the table, then they appear as columns but never alter the default sort order.
4. **Click-to-sort-by-any-column, reload resets to default.** Given the user clicks a column header, when they do so, then rows re-sort client-side by that column's raw value; reloading/refreshing the page returns to the volume-sorted default.

## Tasks / Subtasks

- [x] Task 1 — Source `volume24H` data for the dashboard (AC: #1, #2)
  - [x] Add a `_VOLUME_24H: dict[str, float]` module-level cache in `ml_signals/dashboard.py`
  - [x] Add `_fetch_volume_24h(network) -> dict[str, float]` that GETs dYdX's public indexer `/v4/perpetualMarkets` (same endpoint as `dydx_collector/open_interest.py:_fetch_markets_json`, parsing `volume24H` per market into `{iid: float}`) — **write dashboard's own independent stdlib `urllib` fetch function, do NOT import `dydx_collector.open_interest._fetch_markets_json`**: that function performs network I/O, and AD-4 only permits cross-namespace imports of shared data types and *pure, I/O-free* utilities — a network-calling function does not qualify, even though it would be tempting to reuse it
  - [x] Add a background loop (mirror `_slow_loop_task`'s `asyncio.create_task` + cleanup_ctx pattern) that refreshes `_VOLUME_24H` periodically (reuse `DB_WRITE_INTERVAL_SECONDS` cadence or a new small constant — do not introduce a third unrelated interval unless justified)
  - [x] Confirm this loop does not read the catalog unbounded (NFR3) — it only calls the public REST endpoint, no catalog access

- [x] Task 2 — Sort rankings by volume24H server-side (AC: #1, #2, #3)
  - [x] In `_rankings_json()` (`dashboard.py:639-662`), sort `rows` by `_VOLUME_24H.get(iid, 0.0)` descending *before* building the `cells` payload — this must happen on every call (the function already runs fresh on each `/api/rankings` poll), not as a one-time computation
  - [x] Add `("volume24h", "Vol24h", <fmt>, None)` to `RANKING_COLS` (`dashboard.py:82-97`) and the matching `COLS` JS array (`dashboard.py` inline `_INDEX_HTML` script, ~line 197) so the sort key is visible, not just implicit
  - [x] Confirm existing indicator columns (OFI, OBI, microprice, spread, etc.) are unaffected — they remain plain display columns, no change to their computation

- [x] Task 3 — Add raw values to the rankings JSON payload for client-side sorting (AC: #4)
  - [x] Extend each cell in `_rankings_json()`'s output from `{"text", "color"}` to `{"text", "color", "raw"}`, where `raw` is the underlying numeric value (or `None`) — needed because client-side sort-by-any-column can't reliably re-parse formatted strings like `"+1.23%"`

- [x] Task 4 — Click-to-sort-by-any-column on the client (AC: #4)
  - [x] **This does not exist yet** despite the module docstring's claim ("`/` rankings table — all coins sortable by any metric") — `<th>` headers currently have no click handlers at all (confirmed: no `sort` reference anywhere in `dashboard.py` before this story)
  - [x] Add click handlers on `<th>` in `renderRankings()` (`dashboard.py` inline JS, ~line 279-304) that toggle a client-side `currentSort = {key, dir}` JS variable and re-render the already-fetched `rows` array sorted by `cells[key].raw` (ascending/descending toggle on repeat click)
  - [x] When `currentSort` is unset (initial load / after reload), rows render in the order the server sent them (already volume-sorted per Task 2) — do not re-sort client-side in that case
  - [x] `currentSort` is an in-memory JS variable only — no `localStorage`/URL-param persistence, so a reload naturally resets to the server's volume-sorted default (satisfies the reload-reset requirement without extra code)

- [x] Task 5 — Tests (AC: #1-#4)
  - [x] `ml_signals/tests/test_dashboard_metrics.py` or a new `test_dashboard_rankings.py`: `_rankings_json()` returns rows ordered by descending `volume24H` given a populated `_VOLUME_24H` and multiple `_LIVE_FAST` entries
  - [x] Test a coin with no `_VOLUME_24H` entry sorts as if `volume24H = 0.0` (missing data does not crash or float to the top)
  - [x] Test that `_rankings_json()`'s cell payload includes a `raw` numeric value alongside `text`/`color`
  - [x] Test `_fetch_volume_24h` parsing logic against a sample markets_json dict (mirror `test_open_interest.py`'s `_markets_by_volume` helper pattern) — mock/stub the HTTP layer, never make a real network call in a test

## Dev Notes

**Two real gaps found while researching this story (`ml_signals/dashboard.py` read in full before writing tasks):**

1. **No `volume24H` field exists anywhere in the dashboard's data pipeline.** `_LIVE_FAST` (fed from Redis 1s snapshots via `_ingest_batch`) has no volume-24h field — `DydxSecondSnapshot` doesn't carry it (per SIGNAL-01/architecture, per-second snapshots are top-20 book + trade volume only, not 24h aggregates). `_LIVE_SLOW`/`metrics_computer.compute_snapshot` only has `pct_1h`/`pct_24h` (price % change), not volume. The dYdX indexer's `volume24H` field is currently fetched *only* by the collector's `_fetch_markets_json`/`classify_liquidity` (`dydx_collector/open_interest.py`), for liquidity tiering — never surfaced to any reader. This story must add a **new, independent fetch path** in the dashboard itself (Task 1).
2. **No client-side column-sort exists at all**, despite the module docstring (`dashboard.py:24`) claiming "`/` rankings table — all coins sortable by any metric". Verified: no `sort` keyword appears anywhere in `dashboard.py` before this story; `<th>` elements in `renderRankings()` have no click handlers. AC4 requires building this, not just "preserving" it (Task 4).

**Architecture boundary — read this before touching `dydx_collector`:** Per AD-4, `ml_signals` may only cross-import shared data types and *pure, I/O-free* utility functions from `dydx_collector` — never stateful/network-calling code. `dydx_collector.open_interest._fetch_markets_json` does an HTTP GET, so it does not qualify for reuse even though its logic is what you need. Write a small independent copy of the fetch in `ml_signals/dashboard.py` instead (same public indexer endpoint, same `User-Agent` header requirement — dYdX's indexer 403s on urllib's default UA, see `open_interest.py:147-151` for the exact header value needed). This is intentional duplication of ~10 lines to preserve the module boundary, not an oversight.

**Row-ordering mechanics:** `_merged_rows()` (`dashboard.py:131-133`) iterates `_LIVE_FAST` in dict-insertion order today — there is no sort of any kind currently, despite rows visually appearing somewhat volume-correlated by coincidence (larger coins tend to connect/populate first). Do not assume any existing order is intentional.

**Memory discipline (NFR3):** The new volume-24h poll must be a periodic REST call only (like the collector's own OI poll) — never a `catalog.trade_ticks()`-style unbounded read. `ml_signals/catalog_stats.py`/`chart_data.py` already demonstrate the time-bounded catalog access pattern if any catalog read were ever needed here (it isn't, for this story).

### Project Structure Notes

- All changes are confined to `troll/ml_signals/dashboard.py` (+ its tests) — no `dydx_collector` file is touched, consistent with AD-4's module boundary.
- No new files needed for the implementation; one new test file (`test_dashboard_rankings.py`) is acceptable if `test_dashboard_metrics.py` doesn't fit the new coverage, following the one-file-per-concern pattern already used (`test_dashboard_ingest.py`, `test_dashboard_metrics.py`, `test_dashboard_chart.py`).

### References

- [Source: troll/ml_signals/dashboard.py:82-97] `RANKING_COLS` — existing column definitions to extend
- [Source: troll/ml_signals/dashboard.py:126-133] `_merged_live`/`_merged_rows` — current (unsorted) row assembly
- [Source: troll/ml_signals/dashboard.py:639-662] `_rankings_json()` — where sort + raw-value payload changes go
- [Source: troll/ml_signals/dashboard.py:174-402] inline `_INDEX_HTML`/JS — `COLS`, `renderRankings()`, where click-to-sort goes
- [Source: troll/ml_signals/dashboard.py:917-942] `slow_loop_ctx`/`_slow_loop_task` — pattern to mirror for the new volume-poll background task
- [Source: troll/dydx_collector/open_interest.py:109-153] `classify_liquidity`, `_fetch_markets_json` — existing (collector-only) precedent for reading `volume24H` from the same indexer endpoint; do not import, mirror instead
- [Source: troll/ml_signals/tests/test_dashboard_ingest.py:31-38] `_reset_state()` pattern for tests that touch dashboard module globals
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-4] module boundary — shared types + pure I/O-free utilities only
- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.2] original story definition

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5 (claude-sonnet-5)

### Debug Log References

- `PYTHONPATH=. python -m pytest dydx_collector/tests ml_signals/tests --ignore=ml_signals/tests/test_ofi_strategy.py -q` → 155 passed (up from 149 at story start)
- `node --check` on the extracted rankings-page JS → syntax OK
- Node smoke test of `sortRows()`/`setSort()` logic with sample rows: null sort preserves server order; desc/asc sorts both verified correct

### Completion Notes List

- **Two real gaps found while reading `dashboard.py` in full before writing tasks** (documented in Dev Notes): no `volume24H` data existed anywhere in the dashboard's pipeline, and no client-side column-sort existed at all despite the module docstring claiming it did.
- Added `_VOLUME_24H` cache + independent `_fetch_volume_24h_json`/`parse_volume_24h`/`_fetch_volume_24h` (own `urllib` fetch, per AD-4 — does not import `dydx_collector.open_interest._fetch_markets_json` since that performs network I/O) + `_volume_loop_task`/`volume_loop_ctx` background poll (60s cadence, mirrors `_slow_loop_task` pattern).
- `_rankings_json()` now sorts by `_VOLUME_24H.get(iid, 0.0)` descending on every call (not cached/one-time), and every cell now carries a `raw` value alongside `text`/`color` for client-side sorting.
- Added a `Vol24h` column to `RANKING_COLS` and the JS `COLS` array.
- Added click-to-sort on `<th>` headers (`setSort`/`sortRows` JS functions) with asc/desc toggle; `currentSort` is an in-memory-only JS variable, so a page reload naturally resets to the server's volume-sorted default — no extra reset code needed.
- New test file `test_dashboard_rankings.py`: volume-sort ordering, missing-volume-sorts-as-zero, raw-value-in-cells, and `parse_volume_24h` parsing (pure function, no HTTP mocking needed since the network call was split out from the parsing logic).

### File List

- `troll/ml_signals/dashboard.py` — `_VOLUME_24H` cache, volume-24h fetch/poll, `_rankings_json()` sort + raw values, `Vol24h` column, client-side click-to-sort JS
- `troll/ml_signals/tests/test_dashboard_rankings.py` — new: volume-sort and raw-value tests

### Review Findings

- [ ] [Review][Patch] Dashboard always polls dYdX mainnet's volume endpoint, ignoring the collector's configured network — `app["dydx_network"]` is never set anywhere in `make_app()`, so `volume_loop_ctx`'s `app.get("dydx_network", DydxNetwork.MAINNET)` always falls through to the hardcoded default; a testnet deployment silently sorts/labels rows with mainnet volume data [troll/ml_signals/dashboard.py:1042]
- [x] [Review][Dismiss] Cold-start race — `_VOLUME_24H` is empty until the first 60s poll resolves, so rows briefly tie at 0.0 before falling into volume order [troll/ml_signals/dashboard.py:1053] — user accepted as-is, not worth fixing
- [ ] [Review][Patch] `"raw": v` passes Python floats straight to `json.dumps` (default `allow_nan=True`) — if any upstream metric is ever NaN/Infinity (e.g. a zero/bad price print reaching `catalog_stats.price_stats`'s pct-change or volatility calc), the response body contains literal `NaN`/`Infinity` tokens that `fetch().json()` cannot parse, breaking the entire rankings table for that poll; a regression from the pre-diff behavior where every value was string-formatted first [troll/ml_signals/dashboard.py:_rankings_json]
- [ ] [Review][Patch] No test asserts the sort order actually changes between two `_rankings_json()` calls after `_VOLUME_24H` is mutated in between — AC2's "not a one-time computation" isn't directly exercised by any test [troll/ml_signals/tests/test_dashboard_rankings.py]
- [x] [Review][Defer] `_VOLUME_24H` has no staleness/failure indicator, unlike `_LIVE_FAST`'s `stale` flag — if the poll starts failing silently, the sort and `Vol24h` column keep serving arbitrarily old data with no visual cue [troll/ml_signals/dashboard.py:_volume_loop_task] — deferred, not required by any AC in this story; future hardening
- [x] [Review][Defer] `parse_volume_24h` duplicates `dydx_collector/open_interest.py`'s `classify_liquidity` volume-parsing one-liner (`float(market.get("volume24H") or 0)`) with no shared constant or parity test tying the two together — AD-4 only bars reusing network-I/O code, so the pure parsing logic could have been factored out [troll/ml_signals/dashboard.py:parse_volume_24h, troll/dydx_collector/open_interest.py:135] — deferred, pre-existing module boundary tradeoff, not blocking
- [x] [Review][Defer] Client poll interval is `setInterval(pollRankings,2000)` (2s) while the spec text says "1s poll" [troll/ml_signals/dashboard.py:showRankings] — deferred, pre-existing code untouched by this diff
