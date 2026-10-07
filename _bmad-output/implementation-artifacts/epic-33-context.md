# Epic 33 Context: Everything the archive holds reaches the trader: liquidations, derivatives, per-bar order flow, alerts that watch more than a price, and the TradingView chart features still missing

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The backend captures far more than the frontend shows. This epic adds Bybit linear liquidation capture and folds per-bar order-flow and liquidation aggregates into the candle store once. It serves derivatives (funding, OI, mark, index, basis) and liquidations as read models over the API and `/ws/live`. From there the numbers reach the chart (panes, markers, order-flow indicators), the screener (sortable columns, presets), richer alerts, research and a liquidation-cascade paper bot. The epic also closes the TradingView feature gaps the operator uses: scale modes, chart types, compare, more drawings, missing indicators, symbol search, fullscreen and shortcuts. It removes dead code that pretends to be a feature.

## Stories

- Story 33.1: Bybit liquidations captured over a second socket into one shared `Liquidation` type (done)
- Story 33.2: Hyperliquid liquidations: wire investigation (done: refuted, no feed)
- Story 33.3: Per-bar order flow and liquidation aggregates in the candle store, folded once (done)
- Story 33.4: Derivatives and liquidations as read models: API routes, live push, ranking fields (done)
- Story 33.14: The liquidation cascade bot, backtested and run as a paper bot (done)
- Story 33.5: Chart panes for OI, funding, basis and liquidations, and the mark/index overlay (done)
- Story 33.6: Order-flow indicators from the stored per-bar aggregates (done)
- Story 33.7: Rankings sorts every column, gains derivatives/flow/range columns and saved filter presets (done)
- Story 33.8: Alert conditions beyond a price cross, and an Alerts page that creates and edits (done)
- Story 33.9: Price-scale modes, chart types, and a compare symbol on the percent scale (done)
- Story 33.10: Drawing tools II, with magnet snapping, undo/redo, lock and hide-all (done)
- Story 33.11: Missing indicators, candle patterns as markers, dead code removed (done)
- Story 33.12: Symbol search, watchlist, fullscreen chart with its panes, shortcuts, time zone, session breaks, countdown, last-price label
- Story 33.13: Liquidation and forced-flow research and the strategy filter

## Requirements & Constraints

- **Liquidations exist for Bybit LINEAR only.** Hyperliquid, spot and dYdX have none, and the operator declined a Hyperliquid node feed. `price_units` is always the **bankruptcy price**; the planned `price_kind`/`confirmed` columns were never added. Any AC that assumes Hyperliquid rows or a `price_kind` (33.13's cross-venue lead-lag, mark-priced rows in implied leverage) handles the empty/single-kind case explicitly, with a `Known limit:`, and never invents rows.
- **Gaps and failures (DATA-01/07):** a gap is flagged, never filled. An undecodable frame, unknown kind or inexact value is ledgered, never dropped or rounded. A quiet market never reads as a dead feed.
- **Precision (DATA-04):** precision comes from the instrument definition, never a value's digits. Values stay integer units with per-row precisions until the API/UI edge; the frontend formats only through `lib/units.ts`.
- **Null vs 0:** migrated candle columns are null on old rows. Liquidation columns are null for a venue with no feed and 0 for an empty bucket. Spot derivatives return empty with `market: "spot"`; their UI is disabled and screener cells show `—`, never 0.
- **Added fields only (AD-D12):** messages, stored schemas, TOML stores and API items only gain keys; existing stored data loads unchanged and `rankings:live` replay bytes still pass.
- **Boundaries:** no new dependency (NFR12: dialogs, shortcut sheet and SVG hand-rolled). `nautilus_trader/` and `crates/` untouchable (FORK-01). Check `nautilus_trader.indicators` before any custom indicator.
- **Every story (MR4, OPS-01, DESIGN-03):**
  - Wrong-data risks go in `docs/DATA_INTEGRITY_AUDIT.md`; it runs to D-217, so the next row is **D-218**.
  - New types and read models go in `docs/DATA_DICTIONARY.md`. Existing: §1.26 liquidations, §1.27 `derivs:raw`, §2.4 footprint (removed in 33.4), §2.6 book features, §2.10 ranking columns, §2.11 alerts, §2.12 research, §2.15 per-bar aggregates, §2.16 derivatives read models, §2.17 scale/chart types/compare, §2.18 drawings, §2.19 `kernel/ta.py` indicators and candle-pattern markers. The next new section is **§2.20**.
  - VPS steps go in `docs/DEPLOY_CHECKLIST.md` as deferred operator actions; the Docs page (`kbData.ts`) stays truthful.
- **Verification:**
  - Backend: `cd platform && python3 -m pytest <touched>/tests -q` (system `nautilus_trader`, no Rust build).
  - Frontend: `cd platform/frontend && npm test && npm run lint && npm run build`.
  - A `data_api` response change regenerates `frontend/src/api/schema.ts` (`data_api/export_openapi.py`, then `npm run codegen`) in the same commit.
- **Tests:** TEST-01/03/04 (hand-computed fixtures from published references, real `Price`/`Quantity`, warnings are failures) and MEM-01 (bounded reads).

## Technical Decisions

- **`kernel/liquidation.py`:** `Liquidation(Data)` in catalog dir `custom_liquidation`; `side` is the liquidated position (`long` = a forced sell); `size_units`/`price_units` with per-row precisions, `venue_event_id` as dedup key; `to_dict`/`from_dict` is the `liquidations:raw` row format; `notional_units()` approximates notional at the bankruptcy price.
- **One fold (SIGNAL-01):** candles store `buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n`; `buy_v + sell_v == v` exactly. Organic delta is `(buy_v − liq_short_v) − (sell_v − liq_long_v)`. Flow indicators read bar columns, never a raw-second replay.
- **One computer per metric (SSOT-02):** `candles` folds; `views/derivatives.py` is the derivatives read model; `ranking` publishes, `views/ranking_columns.py` defines screener columns; `kernel/indicators.py` holds `basis_bps`, `funding_annualised` and `LiquidationCascade`. Research's `cascade_episodes` replays `LiquidationCascade` and never redefines a cascade; the 33.6 organic-delta formula is reused at second resolution.
- **API and live:** `GET /api/coin/{iid}/funding|open-interest|mark-index|liquidations|liquidation-bars` follow the `/api/candles` cursor contract, are bounded by `MAX_QUERY_SPAN_SECONDS` and carry gap markers. `/ws/live` has `derivs:{iid}` and `liquidations:{iid}`. `GET /api/markets` lists instruments (used by 33.9 compare).
- **Persistence:** durable preferences (filter presets, watchlist, layouts, drawings) are TOML in `views/preferences.py`'s one preferences directory, never `localStorage`. `localStorage` is only for per-viewer conveniences.
- **Indicators (33.11):** custom streaming `Indicator`s in `kernel/ta.py` (O(1), `update_raw`, `initialized`) only where Nautilus lacks one; ADX reuses `DirectionalMovement`. Register each in `views/indicator_picker.py`'s catalog and, where a direction exists, the `IndicatorSignalStrategy` signal table.
- **Charts never fabricate (AD-F6):** chart-type transforms (Heikin Ashi, Hollow, Line/Area/Baseline) feed only the main series; indicators, drawings, alerts and profiles stay on real OHLC. Compare aligns on bar time with gaps, no interpolation. Time zone changes display only; bar `t` stays UTC.
- **Dead code (33.11, shipped):** `platform/tests/test_boundaries.py` has a dead-module check over `views/`, `kernel/` and `frontend/src/hooks`: every export needs a non-test importer. New hooks/functions in 33.12/33.13 (watchlist, search, `lib/time.ts`, research frames) must ship with real callers.

## UX & Interaction Patterns

- **Spec amendments:** the original chart/screener spec excluded much of what 33.8–33.12 build. Each story amends `spec-multi-exchange-screener-chart.md` in place with a dated `[amended <date>: Story 33.x]` note citing the operator's 2026-10-05 review (precedents: 33.7's screener sections, 33.8's alerts section, 33.9's §A2 and exclusions entries, 33.10's §A3 and "Extra drawing tools" entry, 33.11's candle-pattern exclusion). Still un-amended and owned by the remaining story:
  - 33.12: §A1/§A9, the "Right sidebar" and "Watchlist and Screener" exclusions (watchlist rail), recording that multi-chart grids, synced crosshair and CSV export were dropped. Still excluded: candle colour customisation, Volume candles, Renko/Kagi/P&F/Range bars, order entry, pitchforks/Gann/Elliott, object tree.
- **TradingView operation parity** (pan, zoom, legend gear/eye/×, Esc cancels a tool, replay controls) stays binding; visual style is the app's own retro theme.
- **Panes:** new panes reuse the existing pane mechanism (per-pane gap painter, persisted heights, per-coin layouts, cursor pagination, live-bar follow, Bar Replay cut).
- **Markers:** liquidation (`components/chart/LiquidationMarkers.ts`) and candle-pattern (`lib/patternMarkers.ts`) markers share lightweight-charts 5's series-markers plugin, merged per bar.
- **Tool rail:** declared as data in `lib/chartTools.ts`, grouped like TradingView (Cursor, Lines, Fibonacci, Projection, Shapes/Annotation, Measure, Volume-based); new tools join a group, never a flat button. Magnet, undo/redo, Hide all and Delete all are rail actions.
- **Dialogs:** reuse `SettingsDialogShell`; no second dialog system.
- **Shortcuts:** already taken: Esc (disarm tool), Ctrl/Cmd+Z undo, Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redo, Shift angle-snap while drawing; keys do nothing while typing in a field or a dialog is open. 33.12's map (digits+Enter timeframes, Alt+T/H/F/V/R/C, Shift+L, Shift+F, `/`, `?`, Ctrl+K) must not collide and follows the same focus rule. Every shortcut is listed on the Docs page.
- **Fullscreen (33.12):** browser Fullscreen API on the one element holding the chart, every connected pane (incl. 33.5 derivatives/liquidation panes and 33.9 compare/spread panes), legends and tool rail; heights kept, resize via the existing observer; a refused request is reported inline; view state only, never persisted.

## Cross-Story Dependencies

- **Remaining order:** 33.12 → 33.13. Every other story is done; Epic 32 is merged into `epic-33`.
- **33.12:** symbol search should become 33.9's compare picker (compare is currently a text field; `GET /api/markets`, `lib/compare.ts`, `lib/heikinAshi.ts` exist; `lib/time.ts` does not yet). `Alt+C`/`Shift+L` drive 33.9's compare and log scale. Watchlist live price/24h % come from `rankings:live`. ~~Bar Replay in Lines mode cuts the snapshot series like the candles.~~ **[amended 2026-10-07: operator]** Bar Replay is a candle-chart feature only; Lines mode keeps it disabled ("Replay is available on candle charts"), DW-290. Settings persist in the per-coin layout (`lib/chartLayout.ts`, `data_api/routes/layout.py`).
- **33.13:** builds on 33.3's per-bar columns, 33.6's organic delta, 33.14's `LiquidationCascade` (reused by `cascade_episodes` and `OFIStrategy`'s cascade mode) and 33.1's `Liquidation` rows via `BacktestDataConfig` for `custom_liquidation`; notebook 08's sweep keeps the fill/fee/latency models unchanged.
