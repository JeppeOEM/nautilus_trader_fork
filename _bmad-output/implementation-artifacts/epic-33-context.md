# Epic 33 Context: Everything the archive holds reaches the trader: liquidations, derivatives, per-bar order flow, alerts that watch more than a price, and the TradingView chart features still missing

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

The backend captures far more than the frontend shows. Mark, index, funding and open interest are archived and verified every night, but no route, pane, screener column or alert reads them. The 1 s snapshot holds buy/sell volume and counts, but the candle store keeps only `v`. Liquidations are not captured at all. This epic adds liquidation capture (Bybit, then Hyperliquid as far as the wire allows). It folds per-bar order-flow and liquidation aggregates into the candle store once, and serves derivatives and liquidations as read models over the API and `/ws/live`. Those numbers then reach the chart (panes, markers, order-flow indicators), the screener (sortable columns, presets), richer alerts, research and a liquidation-cascade paper bot. The epic also fills in the missing TradingView chart features (scale modes, chart types, compare, more drawings, missing indicators, symbol search, multi-chart, shortcuts) and removes dead code that pretends to be a feature.

## Stories

- Story 33.1: Bybit liquidations captured over a second socket into one shared `Liquidation` type
- Story 33.2: Hyperliquid liquidations: the wire investigation first, then the feed that holds
- Story 33.3: Per-bar order flow and liquidation aggregates in the candle store, folded once
- Story 33.4: Derivatives and liquidations as read models: API routes, live push, ranking fields
- Story 33.5: Chart panes for open interest, funding, basis and liquidations, and the mark/index overlay
- Story 33.6: Order-flow indicators from the stored per-bar aggregates
- Story 33.7: Rankings sorts by every column, gains derivatives/flow/range columns and saved filter presets
- Story 33.8: Alert conditions beyond a price cross, and an Alerts page that creates and edits
- Story 33.9: Price-scale modes, chart types, and a compare symbol on the percent scale
- Story 33.10: Drawing tools II, with magnet snapping, undo/redo, lock and hide-all
- Story 33.11: Missing indicators, candle patterns as markers, dead code removed
- Story 33.12: Symbol search, watchlist, multi-chart layouts, shortcuts, export, time zone and chart conveniences
- Story 33.13: Liquidation and forced-flow research and the strategy filter
- Story 33.14: The liquidation cascade bot (backtested and run as a paper bot)

## Requirements & Constraints

- **Liquidation wire facts:**
  - Bybit publishes `allLiquidation.{symbol}` on the linear and inverse streams only (no spot), every 500 ms. Wire side `S=Buy` means a **long** was liquidated, so the forced order is a sell. `p` is the **bankruptcy price**, not the fill price.
  - The Rust Bybit handler drops unknown topics. Bybit has no liquidation history endpoint, so any outage is unrecoverable and the coverage record must say so.
  - Hyperliquid has no market-wide liquidation feed, and a logged-in user does not help: user channels show only that user's own liquidations. Story 33.2 must prove a hypothesis on captured frames before building anything: ≥ 99 % confirmed plus a measured false-negative rate. Otherwise it records the refutation. Third-party liquidation APIs are forbidden.
- **Gaps and decode failures:** a gap is flagged, never filled. An undecodable frame, an unknown topic or an inexact value is ledgered, never dropped quietly and never rounded (DATA-01/DATA-07).
  - A quiet market must never read as a dead feed. Liquidation-feed liveness is the socket state, not row arrival.
- **Precision and integers:** precision comes from the instrument definition, never from a value's digit count. Liquidation size and price are integer units like the snapshot. Never route a value through `float`. Floats appear only at the API edge (DATA-04).
- **Null vs 0:** candle columns added by a migration are **null** on pre-existing rows (unknown), never 0. Liquidation columns are null for a venue with no feed and 0 for a venue with the feed but no liquidation in the bucket. Both distinctions are tested.
- **Added fields only (AD-D12):** published messages, stored schemas and API items only gain fields. `rankings:live`'s recorded replay bytes stay valid with the new keys stripped, `/api/candles` keeps every existing key, and `metrics_store.COLS` only gets columns appended.
- **Spot instruments:** a spot instrument returns empty derivatives pages (`market: "spot"`, no 404). The UI shows them disabled, or `—` in the screener, never 0.
- **Boundaries:**
  - No new dependency (NFR12): use `nautilus_pyo3.WebSocketClient`, stdlib `json` and inline SVG.
  - Never touch `nautilus_trader/` or `crates/` (FORK-01).
  - Nautilus built-ins come first: check `nautilus_trader.indicators` before writing a custom indicator.
- **Every story (MR4, OPS-01):**
  - Register wrong-data risks in `docs/DATA_INTEGRITY_AUDIT.md`. The next row is D-147.
  - Register types and read models in `docs/DATA_DICTIONARY.md`: new §1.26 for liquidations, new §2.14 for per-bar aggregates and derivatives read models, and amendments to §3.2/§3.3.
  - Put VPS steps in `docs/DEPLOY_CHECKLIST.md` as deferred operator actions.
  - Keep Docs-page claims true (DESIGN-03).
- **Verification per story:**
  - Backend: `cd platform && python3 -m pytest <touched packages>/tests -q`, using the system `nautilus_trader` (no Rust build).
  - Frontend: `cd platform/frontend && npm test && npm run lint && npm run build`.
  - A `data_api` response change regenerates `frontend/src/api/schema.ts` (`data_api/export_openapi.py`, then `npm run codegen`) in the same commit.
- **Test discipline:** follow TEST-01/03/04 (real `Price`/`Quantity`, hand-computed fixtures, warnings are failures) and MEM-01 (bounded reads, no rescans).

## Technical Decisions

- **Shared `Liquidation(Data)` type in `kernel/liquidation.py`:**
  - Modelled on `OpenInterest`; the catalog dir is `custom_liquidation`.
  - Fields: `side` (`LONG|SHORT`, stored as dictionary strings), `size_units`/`price_units` (int64) with per-row `price_precision`/`size_precision`, `venue_event_id` (a dedup key), `ts_event`.
  - Integer conversion reuses `second_snapshot.units_of`.
  - Story 33.2 may add a nullable `confirmed` column. Every reader must respect `price_kind` (`"bankruptcy"` for Bybit, `"mark"` for Hyperliquid) and never conflate the two.
- **Capture path:**
  - The liquidation socket is a separate generic WebSocket with resubscribe-on-reconnect.
  - Rows go straight to the flush buffer through a new shared `CaptureService.ingest_rows(rows, site)`, extracted from `poll_loop`. They never pass through `_on_data`, so they do not count as WS liveness.
  - Rows publish on Redis `liquidations:raw`. Story 33.4 adds `derivs:raw` for funding, OI, mark and index on every venue. A failed publish is ledgered.
- **One fold:** per-bar `buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n` are stored in the candle store by the existing fold (AD-D8: exactly two folds exist, trades→second and seconds→bars). They are **stored inputs**, not signals (SIGNAL-01).
  - `buy_v + sell_v == v` is an invariant enforced by `is_valid_candle`.
  - `pv` is the second-close VWAP numerator, with a `Known limit:` comment.
  - CVD and every order-flow indicator read these bar columns, with no raw-second replay.
- **One computer per metric (SSOT-02):**
  - `candles` folds, `views/derivatives.py` is the one derivatives read model, `ranking` publishes, and the frontend formats only through `lib/units.ts`.
  - Pure helpers live in `kernel/indicators.py`: `basis_bps`, `funding_annualised`, and Story 33.14's `LiquidationCascade`. Research `cascade_episodes` replays that indicator rather than defining a cascade a second time.
- **New API routes:** `GET /api/coin/{iid}/funding|open-interest|mark-index|liquidations|liquidation-bars`.
  - They follow the `/api/candles` `before_ns`/`limit`/`bar_seconds` contract and are bounded by `MAX_QUERY_SPAN_SECONDS`, with gap markers.
  - New `/ws/live` channels `derivs:{iid}` and `liquidations:{iid}` count toward the subscription cap.
- **Sign mapping:** a long liquidation is a forced sell and a short liquidation is a forced buy. Organic delta is `(buy_v − liq_short_v) − (sell_v − liq_long_v)`, and both the docstring and a test state the mapping.
- **Charts never fabricate data (AD-F6):** derived series such as Heikin Ashi are never fed to indicators or drawings. Compare series align on bar time with gaps, never interpolated. Gaps draw visibly.
- **Bots:** Story 33.14 bridges `liquidations:raw` into the live node with a custom `LiveMarketDataClient` subclass. No `TradingNode` internals are touched. The bot is paper only (never `ExecConfig`), and decisions are pure functions with a signal log compatible with bot parity.
- **Dead code removal:** Story 33.4 deletes the legacy `/catalog/chart-series`, `/api/indicator-series`, `useIndicatorSeries.ts`, `compute_chart_series`, `build_footprint` and `compute_features`. Story 33.11 adds a dead-module boundary test.

## UX & Interaction Patterns

- New chart panes plug into the existing pane mechanism (Line/Histogram, a gap painter per pane, persisted heights and per-coin layout). They follow cursor pagination and the live forming bar, and are cut at the replay time in Bar Replay.
- The Indicators dialog gains a pinned Derivatives group. On spot it is disabled with "spot: no derivatives" and nothing is fetched.
- Liquidation markers use lightweight-charts 5's series-markers plugin (merged per bar above a count, sqrt radius scale). Story 33.11 reuses the plugin for candle-pattern markers.
- Per-viewer conveniences (sort key, venue chips) live in `localStorage`. Durable preferences (filter presets, watchlist, layouts) live server-side in the one preferences directory.
- Keyboard shortcuts and dialogs are hand-rolled inline (no library) and documented on the Docs page.

## Cross-Story Dependencies

- **Order:** 33.1 → 33.2 → 33.3 → 33.4 → 33.14 → 33.5 → 33.6 → 33.7 → 33.8 → 33.9 → 33.10 → 33.11 → 33.12 → 33.13.
- **Feeds:**
  - Story 33.1 supplies the type and channel that 33.2, 33.3, 33.4, 33.13 and 33.14 build on.
  - Story 33.3's columns feed 33.4's `liquidation_bars`, 33.6 and 33.12's export.
  - Story 33.4's routes, live channels and ranking fields feed 33.5, 33.7 and 33.8.
  - Story 33.14's `LiquidationCascade` is reused by 33.13.
- **Before vs after Epic 32:**
  - Stories 33.1–33.4 and 33.14 are backend only (`capture`, `kernel`, `candles`, `views`, `data_api`, `ranking`, `verification`, `archive`, `bots`, `research`) and may start before Epic 32 is merged.
  - Stories 33.5 onward touch `platform/frontend/` and must run **after the `epic-32` merge**. They extend 32.x work: pane heights and layouts (32.6), the settings modal (32.3), the left rail and context menu (32.5), the anchored-VWAP drawing (32.7) and the trade footprint (32.8).
  - Story 33.9's compare uses 33.12's symbol search if it has landed, otherwise a text field.
