---
baseline_commit: e95c3d4fbbe239c3bff3e8c38dd171ae913b02e3
---

# Story 1.3: Expose the live watchlist as a queryable, backtest-consumable coin set

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a strategy developer,
I want to fetch the current Watchlist's coin set programmatically,
so that a multi-coin backtest can use it directly without manually editing a per-coin config list.

## Acceptance Criteria

1. **Live, queryable coin-set (FR-7).** Given the live ranking state, when a caller requests the current Watchlist, then a coin-set (list of instrument ID strings) is returned reflecting the live, continuously-refreshed ranking, not a fixed, manually-curated list.
2. **Delisted/unsubscribed coins drop out automatically.** Given a coin's ranked opportunity becomes short-lived and it no longer qualifies (i.e. the collector stops sending fresh snapshots for it — reclassified illiquid, unsubscribed, or disconnected), when the Watchlist is next queried, then that coin is no longer present in the returned set, and a newly-qualifying coin is present, with no manual config edit required.
3. **`BacktestDataConfig`-compatible shape (FR-13).** Given a `BacktestDataConfig`/`BacktestNode` setup, when it is configured to use the Watchlist, then the returned coin-set is accepted as-is (each entry converts directly to an `InstrumentId` usable in a `BacktestDataConfig`) to build its instrument list — no per-coin manual editing required. (Actually wiring a multi-coin `BacktestNode` run end-to-end is Epic 2 Story 2.4's job — this AC only requires proving the returned shape is a correct, drop-in fit.)
4. **No unbounded catalog reads (NFR3).** Given the Watchlist query, when it executes, then it reads only current/live in-memory ranking state (already held by the running dashboard process) — it never touches `ParquetDataCatalog`/historical ticks.

## Tasks / Subtasks

- [ ] Task 1 — Fix the real gap found during research: stale/unsubscribed instruments never leave `_LIVE_FAST` (AC: #2)
  - [ ] **Read `ml_signals/dashboard.py:927-952`'s `_ingest_batch` before writing any code — confirm this is still current.** `_LIVE_FAST[iid] = {...}` is only ever *set*, never deleted, anywhere in the file (confirmed via full-file read — no `del _LIVE_FAST[` or `.pop(` call exists). If the collector unsubscribes/reclassifies a coin as illiquid (`collector.py`'s periodic `classify_liquidity` re-check) or a coin's connection drops, no more `snapshots:1s` messages for it arrive on Redis — but its last-known entry sits in `_LIVE_FAST` forever, so today's `_rankings_json()`/rankings table never actually drops a coin. This directly blocks AC #2 and was not caused by Story 1.2 (pre-existing), but must be fixed here since this story's whole premise depends on it.
  - [ ] Add `_WATCHLIST_STALE_NS = 30_000_000_000` (30s) as a module constant, citing `troll/CLAUDE.md`'s OBS-01 rule ("zero book updates for >30s across a liquid instrument is a pipeline failure, not quiet market") as the source of this threshold — do not invent a new number. This is deliberately more lenient than the collector's own `_STALE_BOOK_NS = 5s` (that gates *snapshot emission*, a much tighter loop) and comfortably longer than Story 1.5/1.7's resync/crossed-book grace windows (order of 1-15s), so a normal resync episode must never cause a coin to falsely drop off the Watchlist mid-recovery.
  - [ ] Add `_is_fresh(iid: str, now_ns: int) -> bool`: `return (now_ns - _LIVE_FAST[iid]["ts"]) <= _WATCHLIST_STALE_NS` (guard for `iid not in _LIVE_FAST` → `False`)
  - [ ] **Scope decision, don't second-guess:** do NOT delete stale entries from `_LIVE_FAST`/`_second_rolling`/`_OFI_INDS`/etc. Deleting-and-later-recreating indicator state risks losing useful rolling history if a coin briefly reclassifies and comes back, and the dict is bounded anyway by dYdX's small, fixed total market count (not an unbounded-growth/MEM-02 concern — MEM-02 is about unbounded *tick/event* accumulation, not a handful of small per-market dict entries). Only **filter by freshness at read time** (Task 2), not at write/storage time.
  - [ ] **Also a scope decision:** do NOT change `_rankings_json()`'s existing behavior (the human-facing rankings table). Per `troll/CLAUDE.md`'s DATA-01 ("flag, don't hide, stale data"), it's arguably *better* for a human dashboard viewer to keep seeing a now-stale row (their eyes can judge it looks frozen) than to have it vanish silently. This story's freshness filter applies only to the new Watchlist-specific output (Task 2), which has a different consumer (an automated backtest script that must never silently include a dead coin).

- [ ] Task 2 — Add a `_watchlist_ids()` function and `/api/watchlist` route (AC: #1, #2, #4)
  - [ ] `_watchlist_ids() -> list[str]` in `ml_signals/dashboard.py`: `now_ns = time.time_ns()`; filter `_merged_rows()` (existing helper, `dashboard.py:144-146`) to only fresh instruments via `_is_fresh`; sort by `_VOLUME_24H.get(iid, 0.0)` descending — same sort key `_rankings_json()` already uses (`dashboard.py:812`, added by Story 1.2), reused for consistency, not reinvented; return just the `instrument_id` strings
  - [ ] New `async def watchlist_json_handler(request: web.Request) -> web.Response` returning `web.Response(text=json.dumps({"instrument_ids": _watchlist_ids()}), content_type="application/json")` — mirrors `rankings_json_handler`'s existing shape (`dashboard.py:972`)
  - [ ] Register `app.router.add_get("/api/watchlist", watchlist_json_handler)` in `make_app()` (`dashboard.py:1206-1238`, alongside the other `add_get` calls)
  - [ ] Confirm `_watchlist_ids()` touches no `ParquetDataCatalog`/catalog_path access at all — it only reads `_LIVE_FAST`/`_VOLUME_24H`, already-in-memory dashboard state (trivially satisfies AC #4/NFR3)

- [ ] Task 3 — Add a lean, importable fetch helper for backtest/notebook consumers (AC: #1, #3)
  - [ ] New file `troll/ml_signals/watchlist.py` (deliberately NOT added to `dashboard.py` — a backtest script or Jupyter notebook importing this must not transitively pull in `dashboard.py`'s aiohttp/plotly/redis/indicator import weight just to fetch a coin list)
  - [ ] `fetch_watchlist(dashboard_url: str = "http://127.0.0.1:8765") -> list[str]`: plain stdlib `urllib.request` GET against `f"{dashboard_url}/api/watchlist"`, `timeout=10`, `json.load(response)["instrument_ids"]` — mirror `dashboard.py:1148-1163`'s `_fetch_volume_24h_json` request-construction pattern (a `Request` object + `urlopen` + `# noqa: S310` for the fixed-scheme URL), but note this hits the *local* dashboard, not an external host, so no special `User-Agent` header is required the way dYdX's indexer needs one
  - [ ] Requires the dashboard process to be running and reachable at `dashboard_url` — this is an explicit precondition (the live Watchlist only exists in the running dashboard's memory), not a bug; document it in the function's docstring so a caller isn't surprised by a connection error when the dashboard isn't up

- [ ] Task 4 — Prove the returned coin-set is BacktestDataConfig-compatible (AC: #3)
  - [ ] Test: given a stubbed `fetch_watchlist()`-shaped `list[str]` (e.g. `["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]`), build `[BacktestDataConfig(catalog_path="troll/dydx_collector/catalog", data_cls=TradeTick, instrument_id=InstrumentId.from_str(iid)) for iid in ids]` — confirmed via a real `.venv` check that `BacktestDataConfig` construction does **not** eagerly touch disk/the catalog path (no I/O at construction time), so this test needs no real catalog fixture, just real Nautilus objects (satisfies "never mock Nautilus internals" — only `fetch_watchlist`'s own HTTP transport is stubbed, not any Nautilus type)
  - [ ] Assert one `BacktestDataConfig` is produced per watchlist entry, with `.instrument_id == InstrumentId.from_str(iid)` for each — this is the literal proof that "accepts the returned coin-set as-is... no manual per-coin editing" holds
  - [ ] **Do not** wire this into `backtest_dydx.py`'s actual `run()` function or attempt a real multi-coin `BacktestNode` execution — that is explicitly Epic 2 Story 2.4's scope ("multi-coin backtest runs across the live Watchlist"), not this story's

- [ ] Task 5 — Tests for Tasks 1-3 (AC: #1, #2, #4)
  - [ ] `_is_fresh`: fresh entry (recent `ts`) → `True`; stale entry (`ts` older than `_WATCHLIST_STALE_NS`) → `False`; missing `iid` → `False`
  - [ ] `_watchlist_ids()`: returns only fresh instruments, sorted by descending `_VOLUME_24H`; a stale entry present in `_LIVE_FAST` (old `ts`) is excluded from the result but a fresh one with no `_VOLUME_24H` entry still sorts (as 0.0, consistent with Story 1.2's existing missing-volume convention) — proves AC #2's "no longer present" without needing to touch `_LIVE_FAST` itself
  - [ ] `fetch_watchlist()`: stub the HTTP layer (e.g. monkeypatch `urllib.request.urlopen` to return a canned JSON body) — never make a real network call in a test, per existing project convention (`test_open_interest.py`'s pattern)
  - [ ] Full regression: `PYTHONPATH=troll .venv/bin/python -m pytest troll/dydx_collector troll/ml_signals -q` must show no new failures beyond the existing, pre-existing `test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure` (unrelated, documented in Stories 1.2/1.5-1.7)
  - [ ] `ruff check`/`ruff format --check` on all touched files — confirm 0 *new* findings via `git stash` diff against the pre-story baseline (dashboard.py already carries pre-existing, documented findings — see Stories 1.2/1.5-1.7's Dev Agent Records; do not attempt to fix those as part of this story)

## Dev Notes

**Real gap found while researching this story (`ml_signals/dashboard.py` read in full, focusing on `_LIVE_FAST`'s full lifecycle before writing tasks):** `_LIVE_FAST[iid]` is set on every ingested snapshot (`_ingest_batch`, `dashboard.py:927`) but **never deleted anywhere in the file**. A coin the collector stops sending data for (reclassified illiquid, unsubscribed, or disconnected) keeps its last-known entry forever — the rankings table and any naive "current watchlist = keys of `_LIVE_FAST`" approach would never actually drop a delisted coin, which directly violates this story's AC #2 and FR-7's "not limited to a fixed... coin set" consequence. This is why Task 1 exists: the fix is a **read-time freshness filter**, not deletion — see Task 1's scope-decision notes for why deletion was considered and rejected.

**Where "the current Watchlist" actually lives, and why the fetch is HTTP, not a direct import or a fresh Redis subscription:** The live ranking state (`_LIVE_FAST`, `_VOLUME_24H`) only exists in the running `ml_signals/dashboard.py` process's memory, fed continuously by its own Redis `snapshots:1s` subscription (per the Gatekeeper architecture spine, AD-3 — dashboard is a trusting reader, not a second gate). A separate process (a `backtest_dydx.py` invocation, a Jupyter notebook) has no other way to see "what's ranked right now" except by asking the dashboard directly — there is no persisted "current ranking" store yet (that's Story 1.4's job, and it's about *historical* ranking, not live). Two alternatives were considered and rejected:
- *Re-subscribing to Redis independently from the backtest script* — this would duplicate `dashboard.py`'s entire live-ranking reconstruction logic (OFI/OBI indicators, volume merge, sort) in a second place, which is exactly the kind of reader-side reimplementation AD-3/AD-4 exist to prevent (even though the "existing thing" being duplicated here is dashboard logic, not collector logic — same smell).
- *A shared Python data structure* — impossible across process boundaries without an IPC mechanism this project doesn't have; the dashboard and a standalone backtest script are separate OS processes.
Hitting the dashboard's own HTTP server (already running for the rankings UI) is the smallest correct fix, and matches this story's AC #1's phrasing ("a caller requests the current Watchlist") exactly.

**Module placement — why `ml_signals/watchlist.py` is a new file, not an addition to `dashboard.py`:** `dashboard.py` imports `aiohttp`, `plotly`, `redis.asyncio`, and every indicator class — heavy for a backtest script or notebook that only wants `fetch_watchlist()`. A separate, dependency-light module (stdlib `urllib` + `json` only) keeps the "strategy developer" consumption path cheap to import, matching AD-4's spirit of small, purpose-specific reader utilities (even though this crosses no `dydx_collector`/`ml_signals` boundary — it's an intra-`ml_signals` module split for import-weight hygiene, not an architecture-boundary requirement).

**AC #3's actual scope — read this before over-building:** this story proves the *shape* of the returned coin-set is a correct, drop-in fit for `BacktestDataConfig(instrument_id=InstrumentId.from_str(iid), ...)` (Task 4's test). It deliberately does **not**: modify `backtest_dydx.py`'s `run()` to accept multiple symbols, wire a real multi-coin `BacktestNode` run, or build any UI for selecting/running such a backtest. That is Epic 2 Story 2.4 ("multi-coin backtest runs across the Watchlist") — confirmed present later in `epics.md`. Building it here would be scope creep across an epic boundary the sprint plan already accounts for.

**`BacktestDataConfig` does not eagerly touch the catalog at construction time** — confirmed directly: `BacktestDataConfig(catalog_path="/nonexistent/path", data_cls=TradeTick, instrument_id=InstrumentId.from_str("BTC-USD-PERP.DYDX"))` succeeds with no I/O. This means Task 4's test needs no real catalog fixture (a fixture pattern that doesn't otherwise exist yet in `ml_signals/tests/` anyway) — just real Nautilus config/identifier objects, consistent with the project's "never mock Nautilus internals" testing rule (TEST-03).

**Architecture paradigm (must not violate):** Gatekeeper — this story adds a new *reader*-side query surface (`/api/watchlist`, `fetch_watchlist`), it does not add a second writer and does not re-implement any invariant check (AD-1, AD-2, AD-3). `dydx_collector` is not touched by this story at all; everything is confined to `ml_signals`.

**Precision rule (AD-5) — not applicable.** This story only threads instrument ID strings and existing sort keys around; it constructs no `Price`/`Quantity`.

### Project Structure Notes

- Changes confined to `troll/ml_signals/` — `troll/dydx_collector/` is not touched (consistent with AD-4's module boundary; this story is entirely on the reader side).
- New file: `troll/ml_signals/watchlist.py` (the lean fetch helper). Modified: `troll/ml_signals/dashboard.py` (freshness helper, `_watchlist_ids()`, new route).
- New test file: `troll/ml_signals/tests/test_watchlist.py` for `fetch_watchlist()` (mirrors the one-file-per-concern pattern: `test_dashboard_ingest.py`, `test_dashboard_metrics.py`, `test_dashboard_chart.py`, `test_dashboard_rankings.py`). `_watchlist_ids()`/`_is_fresh()` tests can go in `test_dashboard_rankings.py` alongside the existing volume-sort tests, since they share the same `_LIVE_FAST`/`_VOLUME_24H` module state and `_reset_state()` helper (`test_dashboard_rankings.py:25-27`).
- No new third-party dependencies — `urllib.request`/`json` (stdlib) for the fetch helper, matching the project's existing pattern for the dYdX indexer poll (`_fetch_volume_24h_json`) and the collector's own open-interest poll (`open_interest.py`).

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story-1.3] original story definition (Given/When/Then ACs)
- [Source: _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md#FR-7] "Live watchlist" functional requirement — queryable live, backtest-consumable
- [Source: _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md#FR-13] "Multi-coin backtest runs" — the consumer this story's output must satisfy the *shape* of, but does not itself implement
- [Source: _bmad-output/planning-artifacts/epics.md] Epic 2 Story 2.4 ("multi-coin-backtest-runs-across-the-live-watchlist") — where actual multi-coin backtest wiring belongs, not here
- [Source: _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md#AD-3, #AD-4] readers-trust-the-gate, module-boundary rules this story must respect
- [Source: troll/CLAUDE.md#OBS-01] "zero updates for >30s = pipeline failure" — source of `_WATCHLIST_STALE_NS`'s 30s threshold
- [Source: troll/CLAUDE.md#MEM-02] non-configured coins are rolling-window only — cited in Task 1's scope decision for why dict entries are filtered at read time, not deleted
- [Source: troll/ml_signals/dashboard.py:110-146] `_LIVE_FAST`, `_merged_live`, `_merged_rows` — existing state and helper this story filters and reuses
- [Source: troll/ml_signals/dashboard.py:804-837] `_rankings_json()` — existing sort-by-volume pattern (Story 1.2) reused for `_watchlist_ids()`'s sort key
- [Source: troll/ml_signals/dashboard.py:927-952] `_ingest_batch` — confirmed `_LIVE_FAST[iid]` is set but never deleted
- [Source: troll/ml_signals/dashboard.py:968-981] `rankings_handler`/`rankings_json_handler` — existing route-handler pattern to mirror for `watchlist_json_handler`
- [Source: troll/ml_signals/dashboard.py:1144-1163] `_fetch_volume_24h_json` — existing stdlib `urllib` fetch pattern to mirror in `ml_signals/watchlist.py`
- [Source: troll/ml_signals/dashboard.py:1206-1238] `make_app()` — where the new `/api/watchlist` route is registered
- [Source: troll/ml_signals/backtest_dydx.py:41-90] `run()` — existing single-symbol `BacktestDataConfig` construction pattern this story's Task 4 test mirrors (converts a string to `InstrumentId` before building the config)
- [Source: troll/ml_signals/tests/test_dashboard_rankings.py:25-27] `_reset_state()` helper pattern to reuse for `_watchlist_ids()`/`_is_fresh()` tests
- [Source: _bmad-output/implementation-artifacts/1-2-default-the-ranking-table-to-volume-sort.md] previous story — established the `_VOLUME_24H`/volume-sort pattern this story reuses, and its own review-fix pass (network wiring, NaN-safe JSON) landed just before this story was drafted

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
