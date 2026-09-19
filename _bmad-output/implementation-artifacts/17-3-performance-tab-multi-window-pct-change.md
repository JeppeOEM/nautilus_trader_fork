# Story 17.3: Performance tab — multi-window % change

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want the Performance tab to show a coin's % change across multiple lookback windows,
so that I can assess momentum without leaving the screener.

## Acceptance Criteria

1. **`pct_1h`/`pct_24h` need no new work — confirmed already computed by `ranking_engine` (`_PRICE_SERIES`, a 25-hour in-memory ring buffer, `price_series.py`'s `PRICE_LOOKBACK_HOURS = 25.0`) and already displayed today in `RankingsPage.tsx`'s existing `RANKING_COLS`.** This story adds two new windows beyond what exists: `pct_1w` and `pct_1m`.
2. **`pct_1w`/`pct_1m` are computed by `ranking_engine` (not `data_api`), per AD-F2/SSOT-02 — `data_api` never computes a signal, only relays.** They are derived from `metrics_store`'s SQLite-persisted price history (up to `retain_days=31`), which `_PRICE_SERIES`'s 25-hour buffer cannot serve.
3. **A coin with fewer than 7 (or 30) days of accumulated `metrics_store` history shows `pct_1w` (or `pct_1m`) as `None`/absent — never `0` or a fabricated value** (DATA-01). This is the honest, expected state until the store has run long enough, not a bug.
4. **1M (YTD/1Y) beyond `pct_1m` is explicitly OUT of scope for this story** — `metrics_store.write()`'s `retain_days=31` default is the hard ceiling; YTD/1Y would require a deliberate retention-window extension (a separate, larger decision, not made here).
5. **New columns land in both `RankingsPage.tsx`'s Performance tab AND `bot_tui`'s Coins pane, per SSOT-04** ("any new rankings-page column or per-coin metric this epic ships must also land in `bot_tui`, backed by the same shared source").
6. **Each Performance cell is colored/signed by direction (positive/negative)**, matching the existing `pct_1h`/`pct_24h` cells' treatment — no other interaction.
7. **Test:** `ranking_engine`'s new bulk historical-price query and the `pct_1w`/`pct_1m` computation are covered by a real-data test (TEST-01: financial calculation) — no mocking of `metrics_store`.

## Tasks / Subtasks

- [ ] Task 1 — `metrics_store.py`: bulk historical-price query (AC: #2, #3)
  - [ ] Add `price_near_days_ago(db_path: str, days: float) -> dict[str, float]` to `troll/ranking_engine/metrics_store.py`, alongside the existing `latest()`/`history()`/`nearest()` — one query returning, per instrument_id, the `price` of its snapshot closest to (at or before) `now - days*86400s`, using the existing `idx_iid_ts` index for performance. An instrument with no row that old is simply absent from the returned dict (not a `0`/`None` entry) — the caller (Task 2) treats a missing key as "not enough history yet."
  - [ ] Extend `COLS` with `"pct_1w"` and `"pct_1m"` — the existing `_migrate()` function already handles adding new columns to a live table via `ALTER TABLE` (confirmed: its own docstring says "Add columns to COLS and the INSERT below to extend the schema with new metrics" — this is exactly that extension point, no new migration mechanism needed).

- [ ] Task 2 — `ranking_engine/engine.py`: compute and publish `pct_1w`/`pct_1m` (AC: #1, #2, #3)
  - [ ] In `_slow_loop_once` (`engine.py`, the function that already writes `pct_1h`/`pct_24h` into `_SLOW_METRICS` from `_PRICE_SERIES.stats()`), add a call to `metrics_store.price_near_days_ago(METRICS_DB_PATH, 7)` and `..., 30)` once per cycle (bulk, not per-instrument — matches the existing bulk-per-cycle pattern, not N queries), compute `pct_1w = (current_price - price_7d_ago) / price_7d_ago` (and the 30-day equivalent for `pct_1m`) per instrument, store into `_SLOW_METRICS[iid]["pct_1w"]`/`["pct_1m"]` — `None` when the instrument is missing from the bulk query's result (not enough history).
  - [ ] In `_current_ranks()` (`engine.py:415-445`), add `"pct_1w": slow.get("pct_1w")` and `"pct_1m": slow.get("pct_1m")` to the per-row dict alongside the existing `"pct_1h"`/`"pct_24h"` lines — same pattern, same file, same function.
  - [ ] `metrics_store.write()`'s existing per-cycle persisted-row dict (`_merge_rank_into_snapshots`, called from `_slow_loop_once`) must also include `pct_1w`/`pct_1m` so they land in the new `COLS` from Task 1 — otherwise Task 1's schema columns stay perpetually `NULL`.

- [ ] Task 3 — Frontend: Performance tab columns (AC: #5, #6)
  - [ ] Add `pct_1w`/`pct_1m` entries to `RankingsPage.tsx`'s `RANKING_COLS` array (reusing the existing `fmtPercent`/`fmtSigned` formatters, same as `pct_1h`/`pct_24h`) — per Story 17.1's design decision, these land under the Performance tab alongside the existing 13 columns, not a separate column set.
  - [ ] **SSOT-04 compliance (AC #5): also add `pct_1w`/`pct_1m` to `bot_tui`'s Coins pane rendering, backed by the same `rankings:live` fields — do not add these columns to the web UI only.** Locate `bot_tui`'s existing per-column rendering (mirrors `RankingsPage.tsx`'s `RANKING_COLS` pattern, per `troll/ml_signals/ranking_columns.py`'s own docstring: "the web dashboard and bot_tui's Coins pane both render one row per instrument from the exact same `ranking_engine`-published rank entry").

- [ ] Task 4 — Codegen + Tests (AC: #7)
  - [ ] Regenerate `schema.ts`/`openapi.json` if `pct_1w`/`pct_1m` are added to any Pydantic response model (`/api/rankings`); `/ws/live`'s `rankings:live` relay is hand-written (AD-F5 exception) — update its documented field list comment.
  - [ ] `ranking_engine`'s test suite: write real rows via `metrics_store.write()` spanning >30 days of synthetic timestamps into a temp SQLite path, assert `price_near_days_ago(db_path, 7)`/`(db_path, 30)` return the correct closest-price-at-or-before value per instrument, and that an instrument with <7/<30 days of rows is correctly absent from the result (not `0`).
  - [ ] A second test asserts `_slow_loop_once`'s (or the extracted pct-computation function's) output: given a known current price and a known N-days-ago price, `pct_1w`/`pct_1m` compute the correct signed percentage, and are `None` when the bulk query has no historical price for that instrument.

## Dev Notes

- **Why `pct_1w`/`pct_1m` can't reuse `_PRICE_SERIES` (the mechanism `pct_1h`/`pct_24h` use):** `_PRICE_SERIES` is a `PriceSeriesStore` with `lookback_hours=PRICE_LOOKBACK_HOURS=25.0` (`ranking_engine/price_series.py`) — a 25-hour in-memory ring buffer, sized just past `pct_24h`'s own window. It structurally cannot answer "price 7 or 30 days ago." `metrics_store`'s SQLite table (retained up to `retain_days=31`) is the only place with enough history — hence Task 1's new bulk query there instead.
- **Why this computation belongs in `ranking_engine`, not `data_api`:** AD-F2 binds every `data_api` route to "queries, relays, and persists config only — never computes a new signal." `ranking_engine` is the sole authorized computer of ranking/price-derived metrics (AD-9, SSOT-02) — the same reason `pct_1h`/`pct_24h` are computed there today, not in `data_api` or the frontend.
- **31-day ceiling is a real, load-bearing constraint, not a rounding choice:** `pct_1m` (30 days) sits right at the edge of `retain_days=31`. Until `metrics_store` has been continuously running for 30+ days on a given deployment, `pct_1m` will legitimately be `None` for every instrument — this is expected, not a bug to "fix" by fabricating an early value.
- **YTD/1Y are out of scope (AC #4) — do not implement them as part of this story.** If wanted later, that requires deciding a new `retain_days` value (memory/disk tradeoff for the SQLite store) as its own explicit decision.
- **`troll/CLAUDE.md` constraints that apply:** SSOT-02 (single computer, single running instance), SSOT-04 (any new rankings column lands in both dashboard/web and `bot_tui`), DATA-01 (never fabricate a missing value), AD-F2 (facade computes nothing).

### Project Structure Notes

- Modified: `troll/ranking_engine/metrics_store.py` (new `price_near_days_ago`, extended `COLS`), `troll/ranking_engine/engine.py` (`_slow_loop_once`, `_current_ranks`, the persisted-row merge), `troll/frontend/src/pages/RankingsPage.tsx` (`RANKING_COLS`), `troll/bot_tui`'s Coins-pane rendering module (exact path TBD by the dev agent — locate via the same `ranking_columns.py`-mirroring pattern `RankingsPage.tsx` uses), `troll/frontend/src/api/schema.ts`/`openapi.json` if applicable.
- Not modified: `troll/ranking_engine/price_series.py` (untouched — `pct_1h`/`pct_24h`'s mechanism is not the one extended here), `troll/data_api/routes/rankings.py` (no new computation added there, per AD-F2).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Epic 17, Story 17.3] — this story's origin (FR53).
- [Source: _bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md#B0, #B3] — the Performance-tab/History shared-data-path design and the explicit retention-window decision this story makes concrete.
- [Source: troll/ranking_engine/price_series.py] — full file read this session; `PriceSeriesStore`, `PRICE_LOOKBACK_HOURS = 25.0` (imported from `ml_signals/metrics_computer.py:38`), confirming why 1w/1m can't reuse this mechanism.
- [Source: troll/ranking_engine/metrics_store.py] — full file read this session; `COLS`, `_migrate()`'s extension mechanism, `write()`'s `retain_days=31` default, `idx_iid_ts` index this story's new bulk query reuses.
- [Source: troll/ranking_engine/engine.py:415-445,526-552,605-618] — `_current_ranks()`, `_slow_loop_once`'s existing `pct_1h`/`pct_24h` wiring via `_SLOW_METRICS`, `_build_rankings_message()` — the exact functions/dict this story extends.
- [Source: troll/frontend/src/pages/RankingsPage.tsx] — full file read this session (Story 17.1); `RANKING_COLS`, `fmtPercent`, the existing `pct_1h`/`pct_24h` column entries this story's new columns mirror.
- [Source: troll/ml_signals/ranking_columns.py] — SSOT-03 docstring confirming `bot_tui` and the web dashboard both render from the same `ranking_engine`-published entry — the basis for this story's SSOT-04 requirement.
- [Source: _bmad-output/implementation-artifacts/17-1-tab-shell-performance-technicals-tabs-on-the-rankings-page.md] — established that all Performance-tab columns (existing + new) render under `activeTab === "performance"`, unchanged by this story.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List

### Review Findings

Epic 17 review (2026-09-19; combined diff of 17-1, 17-3..17-6; Blind/Edge/Acceptance layers).

- [x] [Review][Patch] Mount-time Technicals fetch could overwrite a fast header edit [troll/frontend/src/pages/RankingsPage.tsx] -- fixed (savedLocally ref)
- [x] [Review][Patch] PUT technicals-columns accepted non-object params and >50 entries [troll/data_api/routes/rankings.py] -- fixed + test
- [x] [Review][Patch] 1w/1m store lookup failure stalled the whole slow-loop cycle [troll/ranking_engine/engine.py] -- fixed (logged, fields None)
- [x] [Review][Patch] Stale placeholder values under shifted columns / stale filter field / per-coin 400 blast radius -- already fixed in 42b63f5719, verified
- [x] [Review][Defer] Non-atomic screener_columns.toml write, params value validity, cache single-key/no single-flight, `=` on floats, tech filter hides all rows while values load -- deferred, see deferred-work.md
