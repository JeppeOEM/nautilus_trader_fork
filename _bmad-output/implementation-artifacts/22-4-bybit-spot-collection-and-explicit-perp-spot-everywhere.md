---
status: awaiting-operator
operator_actions:
  - "Run Bybit collector locally for ~60 s and confirm BTCUSDT-SPOT.BYBIT and BTCUSDT-LINEAR.BYBIT 1s snapshot rows exist and spot has no mark/index/funding/OI rows. [done locally 2026-09-21: both ids write 1 s rows with no gaps or duplicate ts_event; no -SPOT.BYBIT directory exists under mark_price_update, index_price_update, funding_rate_update or custom_open_interest]"
  - "Confirm GET /api/candles?instrument_id=BTCUSDT-SPOT.BYBIT returns venue=BYBIT, market=spot, and the screener and chart badge show and filter both. [API half done locally 2026-09-21: GET /api/candles/BTCUSDT-SPOT.BYBIT (path parameter, not query) returns venue=BYBIT market=spot, -LINEAR returns market=perp, /api/rankings lists both; screener and chart badge UI NOT checked]"
  - Deploy on the VPS with make up and confirm Dozzle is clean for 10 minutes with both WS connections.
followup_review_recommended: false
final_revision: a4e6df6b8561fd93fdbc940f53109c0e238b8f3e
baseline_revision: 03030081018bc96aa53885dea57d666c4fd47912
---

# Story 22.4: Bybit spot collection and explicit perp/spot everywhere

Status: awaiting-operator

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want Bybit spot pairs collected alongside linear perps and every API response and screen to say whether an instrument is perp or spot,
so that a `BTCUSDT-SPOT.BYBIT` row is never mistaken for `BTCUSDT-LINEAR.BYBIT`.

## Acceptance Criteria

1. **`BybitClient` gains a SPOT connection.** Bybit runs one public WebSocket per product type and its spot ticker carries no bid/ask, funding or open interest (docs `websocket/public/ticker`), so: `subscribe(iid)` routes by suffix via `nautilus_pyo3.bybit_product_type_from_symbol`; spot subscribes `publicTrade` + `orderbook.50` only; `fetch_instruments` returns LINEAR + SPOT; the OI REST poll and the ticker subscribe remain linear-only by construction.
2. **`market_kind` is a pure, tested function.** `common/venues.py` gains `market_kind(instrument_id) -> "perp" | "spot" | "unknown"` from Nautilus's id suffixes (`-PERP`, `-LINEAR`, `-INVERSE` ⇒ perp; `-SPOT` ⇒ spot), unit-tested against every real id shape for all three venues.
3. **`market` sits next to `venue` everywhere.** Every `data_api` response that carries `venue` (candles, snapshots, indicators, indicator_series, rankings) and `ranking_engine`'s rank entries carry `market`; `frontend/src/api/schema.ts` has the field; the screener gets a `market` column + filter (same pattern as story 19.5's venue column); the chart header shows a venue/market badge.

## Tasks / Subtasks

- [x] Task 1 — `common/venues.py`: `market_kind` (AC: #2)
  - [x] Pure function, no I/O: strip the `.VENUE` suffix (reuse `ml_signals.venue.venue_of`'s `rpartition(".")` rule or duplicate the two lines — do not import `ml_signals` into `common`), then match the symbol's last `-` segment: `PERP`/`LINEAR`/`INVERSE` → `"perp"`, `SPOT` → `"spot"`, anything else → `"unknown"`. Never raises.
  - [x] `common/tests/test_venues.py`: `BTC-USD-PERP.DYDX`, `BTCUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT`, `BTCUSD-INVERSE.BYBIT`, `BTC-USD-PERP.HYPERLIQUID`, `HYPE-USDC-SPOT.HYPERLIQUID`, HIP-3 `km:US500-USD-PERP.HYPERLIQUID`, an option-style id → `unknown`, a malformed id (no dot) → `unknown`.
- [x] Task 2 — `bybit_collector/client.py`: second public WS (AC: #1)
  - [x] `self._ws_linear` (today's `_ws`) and `self._ws_spot = BybitWebSocketClient.new_public(product_type=BybitProductType.SPOT, environment=..., heartbeat=20)`; a small `_ws_for(iid)` helper: `bybit_product_type_from_symbol(iid.split(".")[0])` → LINEAR/SPOT → the matching client (raise on any other product type: this collector never subscribes inverse/option).
  - [x] `fetch_instruments()` → `request_instruments(LINEAR) + request_instruments(SPOT)` (spot are `CurrencyPair`; `instruments_from_pyo3` handles both — verify by writing them to the catalog in the test).
  - [x] `connect()` caches each instrument into the WS whose product type it belongs to, then connects both; `disconnect()` closes both; `subscribe(iid)`: trades + `orderbook.50` on the routed WS, `subscribe_ticker` **only** when LINEAR; `unsubscribe(iid)` and `resync_orderbook(iid)` route the same way. Docstring: why spot has no ticker/mark/funding/OI (docs cite), so nobody "fixes" it later.
  - [x] `bybit_collector/open_interest.py` stays `category=linear`; its `-LINEAR.BYBIT` id construction already excludes spot; the collector's `wanted` filter (`bybit_collector/collector.py` OI loop) keeps that true by construction. Add one assertion-style test that a spot id never appears in `parse_open_interest` output.
  - [x] `bybit_collector/config.toml`: add `"BTCUSDT-SPOT.BYBIT"`, `"ETHUSDT-SPOT.BYBIT"`.
- [x] Task 3 — `market` in the backend (AC: #3)
  - [x] Pydantic response models: add `market: str` beside `venue` in `CandlesResponse` (`data_api/routes/candles.py:173,177`), `SnapshotSeriesResponse` (`snapshots.py:194,198`), `IndicatorValuesResponse` (`indicators.py:367,380`), `IndicatorSeriesResponse` (`indicator_series.py:163,167`); the value is `market_kind(instrument_id)`. Rankings rows come from `ranking_engine/engine.py:441` — add `"market": market_kind(iid)` next to `"venue"`/`"venue_kind"`; `data_api/routes/rankings.py`'s `RankingsResponse` passes rows through, confirm the field survives its model.
  - [x] Regenerate the OpenAPI schema (`frontend/openapi.json`) the way the repo does today, then `cd frontend && npm run codegen` → `schema.ts` (auto-generated header; never hand-edit).
  - [x] Tests: extend the existing `data_api/tests/{test_candles,test_snapshots,test_indicator_series,test_indicators_config}.py` assertions that check `venue` to also check `market` for a `-SPOT.BYBIT` and a `-LINEAR.BYBIT` id; `ranking_engine/tests/test_engine.py`: rank entry has `market`.
- [x] Task 4 — `market` in the frontend (AC: #3)
  - [x] Screener: mirror 19.5 exactly — `RankingsPage.tsx:176-177` filter fields (`{ key: "market", label: "Market (perp/spot)", text: true }`), the Performance-tab cell at `:390-391`, `filters.ts` needs nothing new (text `=` filter already exists). Tests in `RankingsPage.test.tsx`/`filters.test.ts`: a spot and a linear row for the same symbol are distinct rows and `market = spot` filters to one.
  - [x] Chart header badge in `ChartPage.tsx`: ChartPage shows **no** venue today (grep is empty) — add one compact badge `BYBIT · spot` / `DYDX · perp` from the candles response's `venue`/`market`, styled with the terminal/ANSI identity from story 15.9 (reuse an existing badge/tag class if one exists; no new dependency).
  - [x] SSOT-04 says ranking-page changes land in `bot_tui` too; 19.5 deferred the venue column there. Add `venue` and `market` as two `fit()`'d columns (TUI-02) to the bot_tui rankings table **if** the row fits at 80 columns; otherwise register the gap in this story's Completion Notes — do not squeeze or truncate other columns for it.
  - [x] `vitest` + `tsc -b` clean (the Docker build runs `tsc -b`; a type error breaks the image, see commit `cb128d5d95`).
- [ ] Task 5 — live verification
  - [x] (done locally 2026-09-21, see operator_actions note) Local ~60 s Bybit run: `custom_dydx_second_snapshot/BTCUSDT-SPOT.BYBIT/` rows at 1 s **and** `BTCUSDT-LINEAR.BYBIT`; spot rows have trades + book, no mark/index/funding/OI rows for spot ids in the catalog; `snapshots:raw` carries both.
  - [ ] `GET /api/candles?instrument_id=BTCUSDT-SPOT.BYBIT&...` → `venue="BYBIT", market="spot"`; the screener shows and filters both; chart badge reads `BYBIT · spot`.
  - [ ] VPS `make up`; Dozzle clean for 10 min with both WS connections.

## Dev Notes

### Two WebSockets is Bybit's rule, not ours

`BybitWebSocketClient.new_public(product_type=...)` binds one connection to one product type (`nautilus_pyo3.pyi:7445`), matching Bybit's per-product public stream URLs. Routing by the Nautilus symbol suffix through `bybit_product_type_from_symbol` (`nautilus_pyo3.pyi:7668`) is the adapter's own convention — use it, don't parse suffixes by hand in the client.

### Spot has fewer streams — by documentation, not omission

Research §A: Bybit's spot `tickers` has no bid/ask, funding or OI; mark/index price are perp concepts. So a spot instrument produces only `DydxSecondSnapshot` rows (book + trades) and its catalog has no `mark_price_update`/`funding_rate_update`/`custom_open_interest` entries. That is correct data, not missing data — say so in the client docstring and in `docs/DATA_DICTIONARY.md` (one line) so a future DATA-05 audit doesn't flag it.

### `market` is derived, never stored (SIGNAL-01, story 19.1 precedent)

Like `venue`, `market` is a pure function of the instrument id computed at the API edge and in `ranking_engine`'s rank entries. No new Parquet column, no schema migration.

### Ranking engine caveat (known, out of scope)

`ranking_engine`'s `_VOLUME_24H` is filled by a dYdX-only volume loop (`engine.py:210, 773`), so Bybit/Hyperliquid rows rank with `volume24h = 0` (19.5 Completion Notes). This story makes rows distinguishable, not correctly ranked; leave the volume source as is and mention it in Completion Notes if it surprises you during verification.

### Sandbox/live_paper is untouched here

Spot *trading* (Sandbox account type for `CurrencyPair`) is story 22.6's open item. This story only collects spot data.

### Project Structure Notes

- Modified: `troll/common/venues.py` (+ `common/tests/test_venues.py`), `troll/bybit_collector/{client,open_interest}.py`, `config.toml`, tests; `troll/data_api/routes/{candles,snapshots,indicators,indicator_series}.py` (+ tests), `troll/ranking_engine/engine.py` (+ test), `troll/frontend/openapi.json`, `troll/frontend/src/api/schema.ts` (generated), `troll/frontend/src/pages/{RankingsPage,ChartPage}.tsx` (+ tests), optionally `troll/bot_tui/*` rankings pane, `troll/docs/DATA_DICTIONARY.md`.
- Unchanged: `collector_core/**` (the core already handles any instrument the client subscribes), `dydx_collector/**`, `hyperliquid_collector/**`, catalog schemas.
- The working tree at story-creation time carried uncommitted edits to `data_api/routes/{indicators,rankings}.py`, `frontend/src/{api,pages}/*`, `ml_signals/screener_columns*` from another session — rebase on whatever landed; do not revert them.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.4] — ACs.
- [Source: research 2026-09-20 §A (Bybit column), §B3, §C6] — spot streams, routing, `market_kind`, response fields.
- [Source: troll/bybit_collector/client.py] — current single-WS client (read in full).
- [Source: nautilus_trader/core/nautilus_pyo3.pyi:6858 `BybitProductType`, :7445 `new_public`, :7668 `bybit_product_type_from_symbol`, :7339 `request_orderbook_snapshot`].
- [Source: troll/data_api/routes/{candles:173-177, snapshots:194-198, indicators:367-380, indicator_series:163-167}.py; troll/ranking_engine/engine.py:432-448] — where `venue` is emitted today.
- [Source: troll/frontend/package.json "codegen"; frontend/src/api/schema.ts header] — generated types.
- [Source: _bmad-output/implementation-artifacts/19-5-rankings-screener-venue-column-and-filter.md] — the column/filter pattern and its bot_tui deferral.
- [Source: troll/ml_signals/venue.py] — `venue_of`; [Source: troll/common/venues.py] — `venue_kind` registry.
- [Source: troll/CLAUDE.md SSOT-03/04/05, TUI-02, SIGNAL-01, DATA-05] — rules applied.

## Dev Agent Record

### Agent Model Used

Claude Sonnet 5

### Debug Log References

### Completion Notes List

- Tasks 1-4 done. Task 5 (live verification: local Bybit run, API/screener/badge check, VPS `make up`) NOT done -- needs network/VPS/docker; left unticked for the operator.
- Tests: common, bybit_collector, data_api, ranking_engine = 222 pass, 1 fail (`data_api/tests/test_rankings.py::test_rankings_live_message_reflected_by_rest_and_ws_relay`, needs a live Redis on 127.0.0.1:6379; environmental). Frontend: `npm run codegen`, vitest (23 files, 290 tests) and `tsc -b` clean.
- Spot instrument catalog-write from `fetch_instruments` (CurrencyPair via `instruments_from_pyo3`) is not unit-tested (needs network); covered by Task 5. Client routing tested offline (`test_client_routes_by_product_type`).
- Chart badge is fed by `useCandles` now returning `venueMarket` from the candles response; new `.venue-badge` class in `index.css`.
- bot_tui gap (registered per spec): the Coins-pane row is already 192 columns wide (27 + 11 x 15 RANKING_COLS), far beyond 80, so `venue`/`market` columns were NOT added to bot_tui (19.5's venue deferral stands). SSOT-04 follow-up.
- `ruff` is not installed in this environment; imports ordered by hand, no formatter run.
- `_VOLUME_24H` is dYdX-only (known, out of scope): Bybit rows rank with volume24h = 0.

### File List

- troll/common/venues.py, troll/common/tests/test_venues.py
- troll/bybit_collector/client.py, config.toml, tests/test_collector.py
- troll/data_api/routes/{candles,snapshots,indicators,indicator_series}.py
- troll/data_api/tests/{test_candles,test_snapshots,test_indicator_series,test_indicators_config}.py
- troll/ranking_engine/engine.py, troll/ranking_engine/tests/test_engine.py
- troll/frontend/openapi.json, troll/frontend/src/api/schema.ts (generated)
- troll/frontend/src/hooks/useCandles.ts (+ useCandles/usePickerIndicatorValues/useSnapshotSeries tests: `market` in fixtures)
- troll/frontend/src/pages/{RankingsPage,ChartPage}.tsx, RankingsPage.test.tsx, ChartPage.test.tsx, troll/frontend/src/index.css
- troll/docs/DATA_DICTIONARY.md

## Review Triage Log

### 2026-09-20 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 0
- defer: 0
- reject: many (stale badge: hook remounts via `key` per instrument/timeframe; INVERSE/OPTION never fetched; `market: str` matches `venue: str` precedent; style nits; ranking volume already documented as out of scope)
- addressed_findings:
  - none

## Auto Run Result

Status: awaiting-operator

- Implemented: `market_kind` (common/venues.py), Bybit SPOT WS + routing in `bybit_collector/client.py`, spot ids in config, `market` in candles/snapshots/indicators/indicator_series responses and ranking entries, regenerated openapi/schema.ts, screener Market column+filter, chart venue/market badge.
- Deferred/gap: bot_tui venue/market columns (row already ~192 cols wide, see Completion Notes).
- Verification: Python (common, bybit_collector, data_api, ranking_engine) 222 pass, 1 fail (needs live Redis, fails pre-change); vitest 290 pass; `tsc -b` clean.
- Residual risk: spot instrument catalog write and live WS behaviour untested until Task 5.
