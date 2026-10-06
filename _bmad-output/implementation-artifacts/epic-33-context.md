# Epic 33 Context: Everything the archive holds reaches the trader: liquidations, derivatives, per-bar order flow, alerts that watch more than a price, and the TradingView chart features still missing

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The backend captures far more than the frontend shows. Mark, index, funding and open interest are archived and verified every night, but no route, pane, screener column or alert reads them. The 1 s snapshot holds buy/sell volume and counts, but the candle store keeps only `v`. This epic adds liquidation capture, which ships for Bybit linear only. It folds per-bar order-flow and liquidation aggregates into the candle store once, and serves derivatives and liquidations as read models over the API and `/ws/live`. From there the numbers reach the chart (panes, markers, order-flow indicators), the screener (sortable columns, presets), richer alerts, research and a liquidation-cascade paper bot. The epic also adds the TradingView chart features the chart still lacks (scale modes, chart types, compare, more drawings, missing indicators, symbol search, fullscreen, shortcuts) and deletes dead code that pretends to be a feature.

## Stories

- Story 33.1: Bybit liquidations captured over a second socket into one shared `Liquidation` type (done)
- Story 33.2: Hyperliquid liquidations: the wire investigation first, then the feed that holds (done: refuted, no feed)
- Story 33.3: Per-bar order flow and liquidation aggregates in the candle store, folded once (done)
- Story 33.4: Derivatives and liquidations as read models: API routes, live push, ranking fields
- Story 33.14: The liquidation cascade bot (backtested and run as a paper bot)
- Story 33.5: Chart panes for open interest, funding, basis and liquidations, and the mark/index overlay
- Story 33.6: Order-flow indicators from the stored per-bar aggregates
- Story 33.7: Rankings sorts by every column, gains derivatives/flow/range columns and saved filter presets
- Story 33.8: Alert conditions beyond a price cross, and an Alerts page that creates and edits
- Story 33.9: Price-scale modes, chart types, and a compare symbol on the percent scale
- Story 33.10: Drawing tools II, with magnet snapping, undo/redo, lock and hide-all
- Story 33.11: Missing indicators, candle patterns as markers, dead code removed
- Story 33.12: Symbol search, watchlist, fullscreen chart with its panes, shortcuts, time zone, session breaks, countdown and last-price label
- Story 33.13: Liquidation and forced-flow research and the strategy filter

## Requirements & Constraints

- **Liquidations exist for Bybit LINEAR only.**
  - Hyperliquid writes no rows: both public-data hypotheses were refuted, and the operator declined a node feed. Spot and dYdX also have none.
  - Every `Liquidation` row's `price_units` is the Bybit **bankruptcy price**, not the fill. The planned `price_kind` and `confirmed` columns were never added.
  - A downstream AC that assumes Hyperliquid rows or a `price_kind` column must handle the empty case and must not invent rows. Examples: cross-venue liquidation lead-lag, the "mark" price kind in tooltips, the Hyperliquid `Known limit:` notes.
- **Gaps and decode failures (DATA-01/DATA-07):** a gap is flagged, never filled. An undecodable frame, an unknown topic or an inexact value is ledgered, never dropped quietly and never rounded. A quiet market must never read as a dead feed. Bybit has no liquidation history, so a missed window is coverage `liquidations_unrecoverable`.
- **Precision (DATA-04):** precision comes from the instrument definition, never from a value's digits. Sizes and prices are integer units with per-row precisions. Nothing goes through `float` until the API or UI edge.
- **Null vs 0:** candle columns added by migration are **null** on pre-existing rows, never 0. Liquidation columns are null for a venue with no feed and 0 for a venue with the feed but an empty bucket. Both cases are tested.
- **Added fields only (AD-D12):** published messages, stored schemas and API items only gain keys. `rankings:live`'s recorded replay bytes must still pass with the new keys stripped, `/api/candles` keeps every key, and `metrics_store.COLS` is append-only.
- **Spot:** derivatives pages for a spot instrument come back empty with `market: "spot"`, never a 404. The UI shows the group disabled, and screener cells show `—`, never 0.
- **Boundaries:**
  - No new dependency (NFR12): use `nautilus_pyo3.WebSocketClient`, stdlib `json` and inline SVG.
  - Never touch `nautilus_trader/` or `crates/` (FORK-01).
  - Check `nautilus_trader.indicators` before writing a custom indicator.
- **Every story (MR4, OPS-01, DESIGN-03):**
  - Add risks to `docs/DATA_INTEGRITY_AUDIT.md`. Rows run to D-162, so the next is D-163.
  - Register types and read models in `docs/DATA_DICTIONARY.md`: §1.26 for liquidations, §2.15 for per-bar aggregates (added by 33.3) and the derivatives read models (§2.14 is 32.8's footprint), and amendments to §3.2/§3.3.
  - Put VPS steps in `docs/DEPLOY_CHECKLIST.md` as deferred operator actions.
  - Keep the Docs page truthful.
- **Verification per story:**
  - Backend: `cd platform && python3 -m pytest <touched packages>/tests -q`, using the system `nautilus_trader` with no Rust build.
  - Frontend: `cd platform/frontend && npm test && npm run lint && npm run build`.
  - A `data_api` response change regenerates `frontend/src/api/schema.ts` (`data_api/export_openapi.py`, then `npm run codegen`) in the same commit.
- **Tests:** follow TEST-01/03/04 (hand-computed fixtures, real `Price`/`Quantity`, warnings are failures) and MEM-01 (bounded reads, no rescans).

## Technical Decisions

- **`kernel/liquidation.py` (shipped in 33.1):**
  - `Liquidation(Data)` stores into catalog dir `custom_liquidation`.
  - `side` is `LiquidatedSide` `long|short`, the liquidated position. Wire `Buy` maps to `long`.
  - Other fields: `size_units`/`price_units` (int64), per-row `price_precision`/`size_precision`, `venue_event_id` (the dedup key), `ts_event`/`ts_init`.
  - `to_dict` and `from_dict` are the row format on Redis `liquidations:raw`. `notional_units()` is size × bankruptcy price, an approximation of the fill notional.
- **Capture:**
  - Liquidations arrive over a second generic WebSocket and go straight to the flush buffer via `CaptureService.ingest_rows`. They never count as WS liveness; the socket's state is the feed status.
  - `verification/liquidations.py` matches liquidations to trades as a self-check. It is not an oracle.
  - Story 33.4 adds `derivs:raw` (funding, OI, mark, index, every venue) to `capture/infrastructure/redis_stream.py`. A failed publish is ledgered.
- **One fold (AD-D8 and SIGNAL-01):**
  - The existing candle fold stores `buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n` with per-row precisions. These are stored inputs, not signals.
  - `buy_v + sell_v == v` holds byte for byte, and `is_valid_candle` enforces it.
  - `pv` is a second-close VWAP numerator, with a `Known limit:` comment.
  - CVD and every flow indicator read these bar columns, never a raw-second replay. The one exception is `DepthWithinBps`.
- **One computer per metric (SSOT-02):**
  - `candles` folds and `views/derivatives.py` is the one derivatives read model.
  - `ranking` publishes, and the frontend formats only through `lib/units.ts`.
  - `kernel/indicators.py` holds `basis_bps`, `funding_annualised` and 33.14's `LiquidationCascade`. Research's `cascade_episodes` replays that indicator rather than redefining a cascade.
- **Sign mapping:** a long liquidation is a forced sell and a short liquidation a forced buy. Organic delta is `(buy_v − liq_short_v) − (sell_v − liq_long_v)`.
- **API:**
  - Routes: `GET /api/coin/{iid}/funding|open-interest|mark-index|liquidations|liquidation-bars`. They follow the `/api/candles` `before_ns`/`limit`/`bar_seconds` contract, are bounded by `MAX_QUERY_SPAN_SECONDS` and carry gap markers.
  - `/ws/live` gains `derivs:{iid}` and `liquidations:{iid}` channels. Both count toward `_MAX_SUBSCRIPTIONS`.
  - 33.4 deletes the legacy `/catalog/chart-series`, `/api/indicator-series`, `useIndicatorSeries.ts`, `compute_chart_series`, `build_footprint` and `compute_features`. 33.11 adds a dead-module boundary test.
- **Charts never fabricate (AD-F6):** a derived series such as Heikin Ashi is never fed to indicators or drawings. Compare series align on bar time with gaps and are never interpolated.
- **Bots (33.14):**
  - A custom `LiveMarketDataClient` bridges `liquidations:raw` into the node without touching `TradingNode` internals.
  - The bot is paper only, never `ExecConfig`.
  - Decisions are pure functions in `cascade_rules.py`, and the signal log is parity-compatible with `DummyStrategy`'s.

## UX & Interaction Patterns

- New panes use the existing pane mechanism: Line/Histogram, a gap painter per pane, persisted heights and the per-coin layout of 32.6. They paginate with the candles' cursor, follow the live forming bar and are cut at the replay time in Bar Replay.
- The Indicators dialog gains a pinned Derivatives group. On spot it is disabled with "spot: no derivatives" and nothing is fetched.
- Liquidation and candle-pattern markers use lightweight-charts 5's series-markers plugin, merged per bar above a named count.
- Per-viewer conveniences (sort key, venue chips) go in `localStorage`. Durable preferences (filter presets, watchlist, layouts) go server-side in `views/preferences.py`'s one preferences directory.
- Dialogs and keyboard shortcuts are hand-rolled inline, with no library, and listed on the Docs page.
- The left tool rail is grouped like TradingView and declared as data in `lib/chartTools.ts` (Cursor, Lines, Fibonacci, Projection, Measure, Volume-based; a group button arms its last-used tool, an arrow opens an ARIA flyout). New drawing tools are appended to a group, never added as flat rail buttons: ray, extended, vline and channel under Lines; fib extension under Fibonacci; rect, text and arrow in a new Shapes/Annotation group; price/date ranges under Measure. Magnet, undo/redo, lock and hide-all are rail toggles/actions, not group members.
- Volume overlays (VRVP, session presets, FRVP) live in their own modal next to Indicators, built on `SettingsDialogShell`. Reuse that shell rather than adding a second dialog system.
- 33.12 adds no multi-chart layouts, synced crosshair or CSV export. Fullscreen uses the browser Fullscreen API on the one element holding the chart, every connected pane, the legends and the tool rail. Pane heights are kept, the chart resizes through the existing observer, a refused request is reported inline, and fullscreen is view state that is never persisted.

## Cross-Story Dependencies

- **Order:** 33.4 → 33.14 → 33.5 → 33.6 → 33.7 → 33.8 → 33.9 → 33.10 → 33.11 → 33.12 → 33.13. Stories 33.1 to 33.3 are done.
- **What feeds what:**
  - 33.3's columns feed 33.4's `liquidation_bars` and 33.6.
  - 33.4's routes, live channels and ranking fields feed 33.5, 33.7 and 33.8, plus the History tiles.
  - 33.14's `LiquidationCascade` is reused by 33.13.
- **Epic 32 is merged into `epic-33`.** The frontend stories (33.5 onward) are released and extend:
  - 32.3: settings modal
  - 32.5: left rail, context menu and preferences dir
  - 32.6: pane heights and layouts
  - 32.7: anchored VWAP
  - 32.8: trade footprint
  - the volume-overlays modal and grouped tool rail (quick-dev, merged): 33.10 extends `lib/chartTools.ts`
- 33.9's compare uses 33.12's symbol search if it has landed, otherwise a text field.
