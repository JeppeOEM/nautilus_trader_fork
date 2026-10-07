# Epic 33 Context: Everything the archive holds reaches the trader: liquidations, derivatives, per-bar order flow, alerts that watch more than a price, and the TradingView chart features still missing

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The backend captures far more than the frontend shows. This epic adds Bybit linear liquidation capture and folds per-bar order-flow and liquidation aggregates into the candle store once. It serves derivatives (funding, OI, mark, index, basis) and liquidations as read models over the API and `/ws/live`. From there the numbers reach the chart (panes, markers, order-flow indicators), the screener (sortable columns, presets), richer alerts, research and a liquidation-cascade paper bot. The epic also closes the TradingView feature gaps the operator uses: scale modes, chart types, compare, more drawings, missing indicators, symbol search, fullscreen and shortcuts. It deletes dead code that pretends to be a feature.

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
- Story 33.10: Drawing tools II, with magnet snapping, undo/redo, lock and hide-all
- Story 33.11: Missing indicators, candle patterns as markers, dead code removed
- Story 33.12: Symbol search, watchlist, fullscreen chart with its panes, shortcuts, time zone, session breaks, countdown, last-price label
- Story 33.13: Liquidation and forced-flow research and the strategy filter

## Requirements & Constraints

- **Liquidations exist for Bybit LINEAR only.** Hyperliquid, spot and dYdX have none, and the operator declined a Hyperliquid node feed. `price_units` is always the **bankruptcy price**. The planned `price_kind`/`confirmed` columns were never added. An AC that assumes Hyperliquid rows or a `price_kind` (cross-venue lead-lag, mark-priced rows, implied leverage) handles the empty case and never invents rows.
- **Gaps and failures (DATA-01/07):** a gap is flagged, never filled. An undecodable frame, an unknown kind or an inexact value is ledgered, never dropped or rounded. A quiet market never reads as a dead feed. An alert whose indicator or drawing is gone becomes `invalid` and is ledgered, never skipped.
- **Precision (DATA-04):** precision comes from the instrument definition, never a value's digits. Values stay integer units with per-row precisions until the API/UI edge. The frontend formats only through `lib/units.ts`.
- **Null vs 0:** migrated candle columns are null on old rows. Liquidation columns are null for a venue with no feed and 0 for an empty bucket. Spot derivatives return empty with `market: "spot"` (never 404). Their UI is disabled and screener cells show `—`, never 0.
- **Added fields only (AD-D12):** messages, stored schemas, TOML stores and API items only gain keys. Existing stored data must load unchanged (for example, an alert without `condition` reads as `price_cross`). `rankings:live` replay bytes must still pass.
- **Boundaries:** no new dependency (NFR12; dialogs, shortcuts and SVG are hand-rolled inline). `nautilus_trader/` and `crates/` are untouchable (FORK-01). Check `nautilus_trader.indicators` before any custom indicator.
- **Every story (MR4, OPS-01, DESIGN-03):**
  - Add risks to `docs/DATA_INTEGRITY_AUDIT.md`. It runs to D-209, so the next row is **D-210**.
  - Register types and read models in `docs/DATA_DICTIONARY.md`: §1.26 liquidations, §1.27 `derivs:raw`, §2.10 ranking columns, §2.11 alerts, §2.12 research, §2.15 per-bar aggregates, §2.16 derivatives read models, §2.17 scale modes/chart types/compare (33.9); a new story takes the next free section.
  - Put VPS steps in `docs/DEPLOY_CHECKLIST.md` as deferred operator actions, and keep the Docs page truthful.
- **Verification:**
  - Backend: `cd platform && python3 -m pytest <touched>/tests -q` (system `nautilus_trader`, no Rust build).
  - Frontend: `cd platform/frontend && npm test && npm run lint && npm run build`.
  - A `data_api` response change regenerates `frontend/src/api/schema.ts` (`data_api/export_openapi.py`, then `npm run codegen`) in the same commit.
- **Tests:** TEST-01/03/04 (hand-computed fixtures, real `Price`/`Quantity`, warnings are failures) and MEM-01 (bounded reads).

## Technical Decisions

- **`kernel/liquidation.py`:**
  - `Liquidation(Data)` lives in catalog dir `custom_liquidation`.
  - `side` is the liquidated position: `long` means a forced sell.
  - Fields: `size_units`/`price_units` with per-row precisions, plus `venue_event_id` as the dedup key.
  - The row format on Redis `liquidations:raw` is `to_dict`/`from_dict`. `notional_units()` approximates the notional at the bankruptcy price.
- **One fold (SIGNAL-01):** candles store `buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n` as stored inputs.
  - `buy_v + sell_v == v` holds exactly.
  - Organic delta is `(buy_v − liq_short_v) − (sell_v − liq_long_v)`.
  - Flow indicators read bar columns, never a raw-second replay.
- **One computer per metric (SSOT-02):**
  - `candles` folds and `views/derivatives.py` is the derivatives read model.
  - `ranking` publishes `rankings:live` fields, and `views/ranking_columns.py` defines the screener columns.
  - `kernel/indicators.py` holds `basis_bps`, `funding_annualised` and `LiquidationCascade`. Research's `cascade_episodes` replays `LiquidationCascade` and never redefines a cascade.
  - 33.9's cross-venue spread shares `research/application/aligned.py`'s `basis_bps` formula, named in both docstrings.
- **API and live:**
  - Routes: `GET /api/coin/{iid}/funding|open-interest|mark-index|liquidations|liquidation-bars`. They follow the `/api/candles` cursor contract, are bounded by `MAX_QUERY_SPAN_SECONDS` and carry gap markers.
  - `/ws/live` has `derivs:{iid}` and `liquidations:{iid}` channels.
  - Durable preferences (filter presets, watchlist, layouts, drawings) are TOML in `views/preferences.py`'s one preferences directory and never `localStorage`. Use `localStorage` only for per-viewer conveniences like the sort key.
- **Alerts (33.8, built):**
  - Each condition is a pure `evaluate(state, inputs) -> bool` in `alerting/domain/conditions.py`.
  - Price and indicator conditions run on the bar observer. Indicator values are cached per bar and evaluated through `views.indicator_picker.replay_entry`.
  - Derivatives conditions run on a second observer of the 33.4 live bus.
  - Trendline geometry is ported to Python with a cross-language fixture.
  - An alert holds one `condition` of 14 kinds; an alert stored without one reads as `price_cross`. Statuses include `invalid` (with a reason). `PUT /api/alerts/{id}` edits and re-arms. Templates support `{{value}}` and `{{condition}}`. Still excluded: AND/OR multi-condition alerts and a templates library.
- **Indicators (33.11):** add custom streaming `Indicator`s in `kernel/ta.py` (O(1) per bar, `update_raw`) only where Nautilus lacks one. ADX reuses `DirectionalMovement`. Register each in the `views/indicator_picker.py` catalog.
- **Charts never fabricate (AD-F6):**
  - Heikin Ashi is a pure transform. Every indicator and drawing stays on the real OHLC.
  - Compare series align on bar time, with gaps and no interpolation.
- **Dead code (33.11):** a dead-module boundary test over `views/`, `kernel/` and `frontend/src/hooks` requires every export to have a non-test importer.

## UX & Interaction Patterns

- **Original spec exclusions:** the original chart/screener spec excluded much of what 33.8–33.12 build: extra chart types, extra drawing tools, watchlist, candle-pattern recognition and exotic alert kinds.
  - The operator's 2026-10-05 review lifts those exclusions. Each story amends `spec-multi-exchange-screener-chart.md` in place with a dated `[amended <date>: Story 33.x]` note citing the decision (precedents: §A7/§A9 for 32.7, §B3/§B4/§B5 for 33.7, the alerts section for 33.8).
  - 33.12 amends §A1/§A9 and records that multi-chart layouts, synced crosshair and CSV export were dropped. These stay excluded, along with the right sidebar, candle colour customisation, Volume Candles and order entry.
- **TradingView operation parity** (pan, zoom, legend gear/eye/×, Esc cancels a tool, replay controls) stays binding. Visual style is the app's own retro theme.
- **Panes:** new panes reuse the existing pane mechanism. That covers the per-pane gap painter, persisted heights and 32.6 per-coin layouts, plus cursor pagination, live-bar follow and the Bar Replay cut.
- **Markers:** liquidation and candle-pattern markers use lightweight-charts 5's series-markers plugin, merged per bar.
- **Tool rail:** the left tool rail is declared as data in `lib/chartTools.ts`, grouped like TradingView. New tools are appended to a group, never added as flat buttons:
  - Lines: ray, extended, vline, channel
  - Fibonacci: fib extension
  - a new Shapes/Annotation group: rect, text, arrow
  - Measure: price/date ranges
  - Magnet, undo/redo, lock and hide-all are rail toggles or actions.
- **Dialogs:** volume overlays live in their own modal on `SettingsDialogShell`. Reuse that shell rather than adding a second dialog system.
- **Fullscreen (33.12):** use the browser Fullscreen API on the one element holding the chart, every connected pane, the legends and the tool rail.
  - Pane heights are kept, and the chart resizes through the existing observer.
  - A refused request is reported inline. Fullscreen is view state and is never persisted.
- **Shortcuts:** list every shortcut on the Docs page (`kbData.ts`).
- **Time zone:** only display changes. Bar `t` stays UTC.

## Cross-Story Dependencies

- **Remaining order:** 33.10 → 33.11 → 33.12 → 33.13. Every other story is done, and Epic 32 is merged into `epic-33`.
- **What feeds what:**
  - 33.4's routes, channels and ranking fields fed 33.8's derivatives conditions (done).
  - 33.3's per-bar columns and 33.6's organic delta feed 33.13.
  - 33.14's `LiquidationCascade` is reused by 33.13's `cascade_episodes` and the OFI strategy's cascade mode.
- **Frontend extends Epic 32's work:** the settings modal (32.3), the left rail, context menu and preferences dir (32.5), pane heights and layouts (32.6), and drawings persistence via `PUT /api/coin/{iid}/drawings`.
- **33.9 (done) → 33.12:** compare currently takes a text field; 33.12's symbol search should become its picker. `GET /api/markets` (same-asset venues via `kernel.venues.same_asset`) and `lib/compare.ts`/`lib/heikinAshi.ts` already exist.
- **33.12:** its `Alt+C`/`Shift+L` shortcuts drive 33.9's compare and log scale; its fullscreen must include 33.9's compare/spread panes. Bar Replay in Lines mode cuts the snapshot series like the candles.
- **33.11:** candle-pattern markers share the series-markers plugin with 33.5's liquidation markers; the dead-module check must not flag exports added by 33.9/33.10.
