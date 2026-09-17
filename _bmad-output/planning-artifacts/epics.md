---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/DESIGN.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/EXPERIENCE.md
  - _bmad-output/planning-artifacts/prds/prd-chart-frontend-rewrite-2026-09-13/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md
---

# nautilus_trader_fork - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for nautilus_trader_fork (the `troll/` dYdX Signal Research & Trading Platform), decomposing the requirements from the PRD and Architecture spine into implementable stories. Epics 1–3 (below) cover the original PRD/architecture scope (FR1–FR15) and were fully implemented as of 2026-07-17. This document was reopened on 2026-07-24 to extend coverage for the PRD's volatility Ranking Mode (FR-16) and new Bot Monitoring TUI feature (FR-17–FR-25), backed by a finalized UX design contract (`DESIGN.md`/`EXPERIENCE.md`) — the project's first UX-driven surface. Reopened again on 2026-09-06 to add Epic 8 (FR28–FR30): TradingView-style multi-chart navigation and selectable technical indicators on the web dashboard's coin chart. Numbered Epic 8 (not 5) because epics 5–7 were filed directly as standalone stories bypassing epics.md ceremony (see `sprint-status.yaml`) — Epic 8 continues that same global epic-number sequence to avoid collision with their story-file paths (`5-1-*`, `6-1-*`, `7-1-*`). No PRD/Architecture update precedes this addition (same precedent as FR27); PM should fold FR28–FR30 into the PRD proper once shipped. (FR31, a combined candlestick + bid/ask overlay, was drafted alongside these but the story implementing it — 8.5 — was dropped before dev started; FR31 removed with it — its number is reused below.) Reopened again on 2026-09-08 to add Epic 10 (FR31–FR33): a second, non-Nautilus indicator category on the chart page's picker (Story 8.4), migrating three of the chart page's fixed microstructure-panel rows (OFI, Cancel Pressure, CVD) into it, plus persisted per-instrument indicator configuration. Numbered 10 (not 9) for the same reason Epic 8 skipped 5–7: Epic 9 is itself a standalone bypass-epic bug-fix story (`9-1-fix-oscillator-panel-shared-y-axis-scaling`, see `sprint-status.yaml`), not a real epics.md entry — Epic 10 continues the sequence past its file-path prefix (`9-1-*`). Reopened again on 2026-09-12 to add Epic 12 (FR34–FR35: run dashboard/bot_tui on the user's own machine via a new read-only data API, offloading load from the oversubscribed nifelheim VPS) and Epic 13 (FR36–FR37: stop ranking_engine's recurring Parquet-read memory spike, the mechanism behind its OOM-restart loop). Numbered 12 (not 9) for the same reason Epic 10 was: Epic 11 is itself a standalone bypass-epic bug-fix story (`11-1-fix-empty-imbalance-depth-spread-chart-panes`, see `sprint-status.yaml`), not a real epics.md entry. No PRD/Architecture update precedes this addition (same precedent as FR27–FR33); PM should fold FR34–FR37 into the PRD proper once shipped. Created via direct technical investigation this session (root-caused against real code, real measurements, and an already-logged production incident) rather than the standard PRD-first elicitation flow, at the user's explicit request — same precedent as Epic 11. Reopened again on 2026-09-14 to add Epic 15 (FR38–FR46, NFR6–NFR9): a full rewrite of `troll/ml_signals/dashboard.py` into a React/TypeScript SPA served by an expanded `troll/data_api`, backed this time by a proper PRD (`prds/prd-chart-frontend-rewrite-2026-09-13/prd.md`) and architecture spine (`architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md`), both status `final`. This epic supersedes `epic-14` (a bypass-epic never entered into this document — 14.1/14.2 done, 14.3 becomes moot once the chart page is deleted under this epic's AD-F1 and should be marked superseded in `sprint-status.yaml`, not shipped). Reopened again on 2026-09-14 to add Epic 16 (FR47–FR50, NFR10): an incremental 1-minute rollup cache (`DydxMinuteRollup`), maintained by the collector as new `DydxSecondSnapshot` rows stream in, so wide-window (daily/weekly) candle requests stop rescanning the full raw 1-second archive. Backend/collector-pipeline scope, independent of Epic 15's dashboard rewrite — Epic 15's future `/api/candles` story will depend on this epic's output, but this isn't "replace dashboard.py with React." No PRD/Architecture update precedes this addition; created via direct technical investigation this session (real code read, real read-cost scaling estimated), same precedent as Epic 12/13.

Reopened again on 2026-09-17 to add Epic 17 (FR51–FR56), Epic 18 (FR57–FR60), Epic 19 (FR61–FR66), and Epic 20 (FR67–FR69, deferred): the next phase of Epic 15's chart+screener rewrite. Epic 17 evolves `RankingsPage.tsx` (Story 15.2) into the full tabbed screener (Performance + Technicals tabs), unparking Story 15.8 (31-day metrics history, parked in-progress since 2026-09-16) as part of wiring Performance's multi-window % change and the Rankings→History link. Epic 18 builds the chart features Epic 15 never scoped — drawing tools, Bar Replay, the full Volume Profile family, and a placement/operation-parity pass. Epic 19 adds Bybit and Hyperliquid alongside dYdX, mirroring `dydx_collector`'s direct-asyncio-PyO3 architecture (never `TradingNode`/`DataEngine`, per FORK-02) rather than the `TradingNode`-based `scripts/bybit_recorder/` on the `gg` branch. Epic 20 (Alerts/webhook delivery) is deliberately sequenced last, after Epics 17–19 ship. No PRD/Architecture update precedes this addition — created from a jointly-revised build spec (`_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md`, itself a revision of an external TradingView-clone brief against this codebase, confirmed file-by-file this session), same "direct technical investigation" precedent as Epics 12/13/16.

## Requirements Inventory

### Functional Requirements

FR1: Configurable-interval snapshot capture — the system captures a Snapshot for every subscribed coin at a configurable interval, defaulting to 0.5s.
FR2: Opt-in raw delta capture — the user can flag specific coins for Raw Delta Capture in addition to standard Snapshots, with per-coin retention config including unlimited.
FR3: Fail-closed data integrity gate — the system rejects (never fabricates, clamps, or averages) invalid data at the point of capture: crossed books, stale books, precision-invalid values; every rejection is logged with payload and reason.
FR4: Reconnect & gap resilience — the system recovers from WS/HTTP disconnects without corrupting stored data, and flags resulting gaps (e.g. `None`/null) rather than interpolating across them.
FR5: USD-denominated liquidity capture — open interest is polled separately and liquidity is classified using USD-denominated values (`volume24H` or `openInterest × oraclePrice`), never raw token-unit open interest.
FR6: Coin ranking, sorted by volume — the system ranks all subscribed coins continuously, sorted strictly by descending `volume24H` (USD) for v1; HFT/TA indicators are computed and displayed per coin but do not affect sort order; ranking is inspectable historically.
FR7: Live watchlist — the ranked list is queryable live and usable directly to select a coin set for a multi-coin backtest; not limited to a fixed, manually-curated coin set.
FR8: Research ranking view — the user can inspect how a coin's ranking evolved over time (queryable for any past timestamp within retention), to decide on opt-in raw-delta capture (FR2).
FR9: Jupyter research environment — the user can develop and test indicators/ML signals in Jupyter against catalog data, following Nautilus's own example research-notebook conventions (not a custom framework).
FR10: Single indicator implementation, three consumption contexts — every indicator/signal is implemented exactly once and consumed via identical code in Jupyter research, backtest, and live strategy contexts; no parallel reimplementations.
FR11: Nautilus-native backtesting — the system uses `BacktestNode` + `BacktestDataConfig` exclusively; no custom simulation/matching loop; strategies referenced via `ImportableStrategyConfig` by string path.
FR12: Dual-timeframe strategies — the user can backtest at HFT granularity (raw 0.5s/1s Snapshot data) and at slower timeframes (candlesticks aggregated from the same underlying data, with a configurable aggregation window).
FR13: Multi-coin backtest runs — the user can run a single backtest across the full ranking Watchlist (many coins at once), using the Watchlist's current dynamic output rather than a fixed static coin universe.
FR14: Dummy paper-trading strategy — the system provides a `TradingNode`-based, paper-mode strategy that consumes all signals/indicators produced by the research feature, running against live dYdX market data as a live integration proof.
FR15: Trading-mode isolation — live paper-trading strategy execution lives in a module separate from `dydx_collector`/`ml_signals`'s data path (the one sanctioned `TradingNode`/`Strategy` use in `troll/`, per amended AD-8); enabling real-money execution requires an explicit, separate config step not reachable by default/accidental state in v1.
FR16: Volatility-based ranking mode — the system computes a volatility indicator (stddev of price/returns, configurable lookback default 1h) per coin and ranks coins by relative cross-sectional volatility, as a user-selectable alternative Ranking Mode to FR-6's volume sort; switching modes requires no code change; volatility is implemented once and consumed identically across Jupyter, backtest, live, web dashboard, and TUI.
FR17: Keyboard-only TUI over the shared live feed — a urwid-based terminal UI subscribes to the same live Redis pub/sub feed the web dashboard reads from, navigable entirely by keyboard, with no separate data pipeline for the TUI.
FR18: Bots pane — the TUI shows a live-updating list of running bots with PnL and other per-bot metrics, no manual refresh needed.
FR19: Coin list pane mirroring the dashboard — the TUI shows the Coin Ranking/Watchlist (including FR-16's Ranking Mode) as a live mirror of the web dashboard, both reading the same shared ranking engine so the two never diverge in order.
FR20: Coin-detail view — selecting a coin shows its live-calculated indicators (OFI/OBI/microprice/spread) and full order-book depth (20 levels per side, never truncated as a permanent limitation — collapsed-by-default/expand-on-demand is a UX-level progressive-disclosure choice, not a truncation).
FR21: k9s-style navigation model — a `:` command bar to jump between views, `esc` to pop back a level (never exits), `/` to fuzzy-filter the coin list, and a breadcrumb header showing current location.
FR22: Attention-only color coding — color draws the eye only to what needs attention (stale/dead feed, large PnL swing); baseline/healthy state renders in a quiet/neutral color; text is uniform monospace size throughout, never scaled for emphasis; the coin-list pane's ranking order itself stays uninflected by color.
FR23: Bot start/stop controls — the user can start and stop a bot directly from the TUI, for both paper-mode and live-mode execution, gated by the same isolation/config-gate as FR-15; the control channel never carries a mode parameter.
FR24: Live-only, no in-TUI history for market data — the TUI shows only current/latest market-data state (coin ranking, order book, indicators); no time-scrubbing or historical replay of market data exists in the TUI, which stays the web dashboard's job. (Scope note from the finalized UX design pass: this restriction is scoped to *market data* specifically — bot/trade performance history is a distinct data domain and is in-scope per FR-27 below.)
FR25: Deep-linked browser handoff for coins (Should, not MVP-blocking) — a keybinding on a selected coin opens that coin's graph view in the web dashboard, deep-linked to the exact coin and the TUI's current time-window/zoom context. Implement once the Must-tier panes/navigation (FR-17–FR-24) are stable; does not block MVP sign-off.
FR27 `[NEW — surfaced during UX design pass, not yet in PRD §4.6, PM should fold in]`: Bot-detail trade/PnL history — the Bot-detail view shows a trades blotter (individual fills) and a PnL-over-time chart (day/week/month/all preset toggle, no free-form scrubbing), sourced from the same durable trade/position history both the web dashboard and TUI read — read-only, zero duplicate computation between the two surfaces. A symmetric deep-link keybinding (extending FR-25's pattern to bots) opens the dashboard's fuller view for the same bot.

FR28 `[NEW — 2026-09-06, not yet in PRD, PM should fold in]`: Per-chart settings/navigation panes — on the web dashboard's coin-detail page, each chart (the price/candle chart and the signal chart) has its own dedicated settings toolbar directly above it, exposing only that chart's own display options; no single shared toolbar controls more than one chart.

FR29 `[NEW — 2026-09-06, not yet in PRD, PM should fold in]`: TradingView-style pan/zoom parity, x-axis-linked across all coin-detail charts — click-drag pans (never zooms) and scroll/pinch zooms, on every chart on the coin-detail page, not only the price chart (extends Story 7.1's price-chart-only implementation to the signal chart and any oscillator panel from FR30). The price/candle chart is the "mother" panel: every oscillator/sub-panel it spawns (signal chart, RSI/MACD panel) shares its x-axis range and follows it in lockstep on pan or zoom, from any panel a drag/zoom originates in — panels never drift out of alignment with each other along the timeline.

FR30 `[NEW — 2026-09-06, not yet in PRD, PM should fold in]`: Selectable technical indicators from `nautilus_trader`'s own built-in indicator library — the user can choose from every concrete indicator class in `nautilus_trader.indicators` (confirmed ~45 as of this repo's pinned version: `SimpleMovingAverage`, `ExponentialMovingAverage`, `WeightedMovingAverage`, `HullMovingAverage`, `AdaptiveMovingAverage`, `DoubleExponentialMovingAverage`, `VariableIndexDynamicAverage`, `WilderMovingAverage`, `BollingerBands`, `KeltnerChannel`, `DonchianChannel`, `RelativeStrengthIndex`, `MovingAverageConvergenceDivergence`, `Stochastics`, `CommodityChannelIndex`, `AverageTrueRange`, `VolatilityRatio`, `AroonOscillator`, `DirectionalMovement`, `RateOfChange`, `ChandeMomentumOscillator`, `OnBalanceVolume`, `VolumeWeightedAveragePrice`, and the rest of that module's indicators) and add it to the candlestick chart. **No third-party TA library** — `pandas_ta`/`pandas_ta_classic` are explicitly rejected; every indicator is the exact same `nautilus_trader.indicators.Indicator` class already usable by research/backtest/live contexts (FR10), fed via its own `update_raw`/`handle_bar`, never reimplemented. Indicators whose natural output overlays price (moving averages, Bollinger/Keltner/Donchian bands, VWAP) render as additional traces directly on the candlestick chart; indicators whose output is a bounded oscillator on a different scale (RSI, Stochastics, MACD, CCI, AROON, etc.) render in the oscillator panel from FR29, which follows the candlestick chart's x-axis like every other spawned panel.

FR47 `[NEW — 2026-09-14, not yet in PRD, PM should fold in]`: Incremental 1-minute rollup cache — the collector maintains a `DydxMinuteRollup` type (OHLCV + order-book-derived aggregates: OFI/OBI at levels 5/10, top-of-book at close) per instrument per closed minute, built incrementally from each second's `DydxSecondSnapshot` as it streams in, written through the existing catalog buffer/flush path (`flush_interval_seconds`). The raw 1-second archive is never modified, deleted, or superseded — the rollup is purely a derived, regenerable performance cache.

FR48 `[NEW — 2026-09-14, not yet in PRD, PM should fold in]`: Threshold-based candle source dispatch — candle requests wider than 1h read from the minute rollup instead of rescanning raw 1-second data; requests at or below 1h continue reading raw 1-second data unchanged, exactly as today. Every candle response tags its source (`raw_1s`/`rollup_1m`) explicitly — never left for the caller to infer from which fields happen to be present. Missing rollup coverage for a requested range (e.g. pre-feature history, or a recently-restarted collector) falls back to the raw-1s aggregation path rather than returning an empty/wrong chart.

FR49 `[NEW — 2026-09-14, not yet in PRD, PM should fold in]`: Correct-by-construction OFI continuity — the rollup builder never loses or double-counts an order-flow-imbalance contribution at a minute boundary (the underlying `MultiLevelOFI` indicator instance is never reconstructed on an ordinary minute rollover); continuity state is reset only on a genuine book-rebuild event (resync/resubscribe), via the indicator's existing `clear_prev_state()`.

FR50 `[NEW — 2026-09-14, not yet in PRD, PM should fold in]`: Backfill capability — historical 1-second data already in the catalog (predating this feature, or after a rollup schema change) can be reprocessed into rollup rows via a standalone script, streaming raw 1s in time-bounded chunks per instrument, without touching or risking the raw 1-second archive.

### NonFunctional Requirements

_The PRD has no explicit NFR section; the following are derived from the Vision, Success Metrics, and Architecture spine invariants that constrain how the FRs above must be implemented._

NFR1: Data integrity — zero tolerance for corrupted or fabricated market data; genuinely unavailable data must be visually flagged (gap/break), never papered over with a fabricated flatline or stale value displayed as live (Vision; FR-3/FR-4; SM-C1 counter-metric: a rejected Snapshot is a gate success, not a coverage failure to fix by relaxing validation).
NFR2: Operational reliability — the Dummy Strategy must run continuously in paper mode against live data for at least one week without manual intervention (SM-3).
NFR3: Memory-bounded access — no unbounded catalog reads (e.g. `catalog.trade_ticks()` with no time bounds); all data access is time-bounded or streamed via `BacktestDataConfig`; non-configured coins are rolling-window-in-memory only (architecture AD-6, Consistency Conventions).
NFR4: Fork safety — `nautilus_trader/` and `crates/` are never modified; all `troll/` work is additive so upstream merges stay possible (architecture, all ADs; fork boundary convention).
NFR5: Precision correctness — price/quantity precision changes only via `Decimal.scaleb()` + `Price.from_raw()`/`Quantity.from_raw()`; never `Price(decimal, precision)`/`Quantity(decimal, precision)`, never inferred from digit count, never round-tripped through `float` (architecture AD-5).

NFR6 (PRD NFR-A, Performance): Initial chart-page load and scroll-back history fetches must feel immediate, not merely "eventually consistent" — qualitative by deliberate choice, no numeric SLO; structurally supported by cursor pagination (AD-F3), code-splitting, cache headers, and compression.

NFR7 (PRD NFR-B, Responsive layout): Every page is usable on both desktop and phone/tablet viewports, not desktop-only.

NFR8 (PRD NFR-C, Live-data honesty): No page ever renders a data gap as a flat/interpolated line, and no live value is shown as current once its heartbeat has gone stale — extends NFR1's existing data-integrity discipline into the from-scratch chart-rendering path this epic builds (spine AD-F6).

NFR9 (PRD NFR-D, Feature parity): Every page and capability present in today's `dashboard.py` has a working equivalent in the new frontend before `dashboard.py` is deleted — this epic is a rewrite, not a reduction.

NFR10 `[NEW — 2026-09-14]`: No data loss — the 1-second snapshot archive is never modified, deleted, or superseded by the minute-rollup feature; the rollup is fully regenerable from raw 1-second data at any time, so a bug or schema change in the rollup never risks the underlying market-data record.

NFR11 `[NEW — 2026-09-17]`: No new frontend grid/table-framework dependency — the screener's tab/filter/column-management work (Epic 17) stays on a hand-rendered table, matching `RankingsPage.tsx`'s existing approach; TanStack Table or any equivalent is explicitly rejected for this scope.

### Additional Requirements

- **Brownfield, not greenfield — no starter template.** FR-1 through FR-5 (Data Collection & Integrity) and parts of Coin Ranking largely restate already-adopted architecture (AD-1–AD-7) that is already implemented; epics/stories touching this area should verify current implementation state first rather than assume net-new build.
- **New module required for FR-14/FR-15.** The Dummy Strategy is the first sanctioned use of `TradingNode`/`Strategy` in `troll/`, per the AD-8 amendment (narrowed from a blanket ban to "no live-runtime engine in the data-collection path"). It must live in a module structurally separate from `dydx_collector`/`ml_signals`.
- **Module boundary (AD-4) applies to any new module.** New code (including the paper-trading module) may depend only on shared data types (`DydxMinuteBar`, `DydxSecondSnapshot`, etc.) and pure/side-effect-free utilities from `dydx_collector`/`ml_signals` — never their stateful internals; `dydx_collector` never imports from `ml_signals` or any new module.
- **Deployment/infra:** two-image Docker split (`nautilus-trader-base:1.229.0`, rebuilt rarely; thin `collector.dockerfile` layered on top, rebuilds in seconds) — rebuild order matters (base before thin). Any new long-running service (e.g. a paper-trading process) should follow the same split pattern and read/write volume discipline as the existing `collector`/`dashboard` services.
- **Reader/writer volume discipline:** `dashboard`'s catalog mount is `:ro` as a structural (not just logical) enforcement of AD-3 (readers trust the gate, never re-validate). Any new reader added by these epics should follow the same pattern.
- **Paired-dependency-version convention:** any new dependency pinned in two places that must speak the same protocol (as happened with the `redis` client/broker mismatch) requires cross-referencing comments in both pin locations — applies to any new dependency introduced by these epics (e.g. ta-lib/pandas-based TA libraries mentioned in FR-6).
- **Monitoring/logging:** rejected-data audit trail is `logging.WARNING` only, visible via the existing Dozzle container — no separate quarantine store or flag field. Any new component's error/rejection logging should follow this existing convention rather than introducing a new one.
- **New module `ranking_engine` (architecture AD-9).** Becomes the sole computer/publisher of Coin Ranking — relocates `dashboard.py`'s inline ranking logic and the SQLite `metrics_store` history out of dashboard. Publishes `rankings:live` (JSON: `mode`, `updated_at`, ordered `ranks` list with both `volume24h` and `volatility_score` always present). Mode switches via `ranking:control` (dashboard/TUI → engine only, last-write-wins on near-simultaneous double-switch). Publishes on both rank-change and a fixed heartbeat; readers treat a missed heartbeat as stale, never as "still current."
- **New module `bot_tui` (architecture AD-9/AD-10, structural seed).** Pure reader — reads `snapshots:raw` directly via `ml_signals.indicators` for per-coin live indicators (no reimplementation), reads `rankings:live`, publishes `ranking:control`, and talks to `live_paper` only via `bots:status`/`bots:control` — never imports `live_paper` internals. Not a daemon — an interactive SSH-launched process (`docker compose exec` or on-host), not `restart: always`.
- **`live_paper` control-plane isolation formalized (architecture AD-10).** `bots:control` messages carry only `{bot_id, action: "start"|"stop"}` — never a paper/live mode field; that gate stays solely inside `live_paper`'s own FR-15 config. Both `bots:status`/`bots:control` are shared channels (not per-bot), addressed by `bot_id`.
- **Nautilus `Cache` persistence needed for FR-27, not a bespoke store.** `troll/live_paper/node.py`'s `TradingNodeConfig` currently constructs no `cache=CacheConfig(...)`, defaulting to in-memory-only (trade/position history lost on restart, unreachable externally). `Cache` already exposes the query surface (`cache.orders_closed()`, `cache.positions_closed()`, `cache.position_snapshots()`) and already supports a durable Redis-backed `database` (`CacheConfig(database=DatabaseConfig(type="redis", ...))`) — this stack already runs Redis. The remaining gap: enable that config, and have `live_paper` expose a thin read surface over it (new Redis channel or request/response — exact shape undecided) so `bot_tui`/dashboard read trade/PnL history without touching Nautilus's internal Cache encoding directly (would violate AD-10's internals boundary).
- **Redis channel rename**: `snapshots:1s` → `snapshots:raw` (collector's publish channel name changed).
- **Module boundary (AD-4) extended**: `bot_tui`, `ranking_engine`, `live_paper` now bound by the same shared-types/pure-utilities-only rule as `ml_signals`. `bot_tui` may import `ml_signals.indicators` classes directly (dashboard's existing pattern) but never stateful internals. AD-3's binding widened from a stale 6-item enumerated list to "all of `ml_signals`, open-ended."
- **Deployment**: `live-paper` service already exists in compose (profile-gated, `restart: on-failure:5`); `ranking_engine` needs its own service (Redis read/write, outbound `volume24h` poll, `metrics_store` read-write); `bot_tui` is exec'd/SSH-launched, not a compose daemon — exact YAML flagged Deferred in the architecture spine, not fixed here.
- **New dependency**: `urwid` 4.0.6 pinned (native asyncio event loop, Python 3.9+ floor — fits the 3.12–3.14 pin).
- **Ranking history retention** (`metrics_store`, relocated per AD-9) is not yet tied to the collector's catalog retention — flagged Deferred in the architecture spine; worth a story-level note if a ranking-history story touches this.
- **PRD gap (FR-27):** the trades/PnL-history requirement and its symmetric bot-deep-link keybinding were surfaced during the UX design pass, not originally in PRD §4.6 — PM should fold FR-27 into the PRD proper once these epics/stories ship.
- **Charting library decision stands (Story 7.1, reconfirmed 2026-09-06): stay on Plotly, do not introduce TradingView's Lightweight Charts or any other charting library.** Epic 8 extends Story 7.1's hand-rolled pan/zoom/pagination machinery rather than replacing it.
- **No new dependency for FR30 (revised 2026-09-06) — explicitly rejected `pandas_ta`/`pandas_ta_classic`.** `nautilus_trader.indicators` already ships ~45 concrete TA indicator classes (moving averages, bands, oscillators, volume indicators); FR30 uses those directly. Nothing to add to `troll/troll-requirements.txt`.
- **Indicator placement (FR30) follows FR10 precedent:** the indicator *classes* already live in the one shared place (`nautilus_trader.indicators`) usable by research/backtest/live — this epic adds only a chart-specific registry/dispatch layer (which class + params + which OHLCV fields feed its `update_raw` + which output attribute(s) to read + overlay-vs-oscillator classification) in `ml_signals/`, never a reimplementation of any indicator's math.
- **FR30 data is not net-new collection** — technical indicators are computed from candle OHLC data `_historical_candles_json`/`build_candles()` already produce. No collector or catalog schema change.
- **Read-Only Facade, single backend (AD-F1/AD-F1a).** `data_api` absorbs every in-scope route and the Redis-subscriber logic currently in `dashboard.py`; `dashboard.py`'s HTML-rendering functions (`_page`, `_build_chart_page_html`, `_render_live_page`, `_render_history_page`, `_history_page_from_rows`, etc.) are deleted, not retained. SPA static files served via FastAPI's native `app.frontend()` (ships `fastapi>=0.138.0`, already pinned `0.141.1`) — never a hand-rolled `StaticFiles` mount + catch-all route.
- **Facade computes no new signal (AD-F2).** Every REST route calls an existing `ml_signals`/`ranking_engine`/`dydx_collector` pure function/query — the route body only shapes JSON. The one write path is config persistence (`PUT /api/coin/{iid}/indicators` and similar); `/ws/live` forwards Redis pub/sub messages verbatim plus the one derived `candles:{iid}:{bar_seconds}` channel (AD-F7).
- **Cursor pagination binding on every chart-history route without exception (AD-F3)** — `/api/candles/{instrument_id}` AND `/api/snapshots/{instrument_id}` (Lines-mode history) AND any future scroll-back route. `before_ns` (cursor) + `limit` (bounded, server-enforced max) request; `{"items": [...], "has_more": bool}` response envelope is pinned, not per-route.
- **One chart instance, native multi-pane sync, keyed pane registry (AD-F4).** A coin's chart page is exactly one `lightweight-charts` `createChart()` instance with N panes (`chart.addPane()`) — never multiple `createChart()` instances kept in sync by application code. Panes keyed by indicator id (reusing `_indicator_id(name, params)` from `dashboard.py`), held in a single `Map<indicatorId, IPaneApi>` owned by the one component that calls `createChart()`.
- **Generated contract types, never hand-duplicated (AD-F5).** `data_api` route handlers declare Pydantic response models; frontend TypeScript request/response types are generated from the resulting OpenAPI schema at build time. Exception: `/ws/live` message shapes are hand-written (no OpenAPI coverage for WS) but must cite the exact Redis wire format or `ml_signals` function they mirror in a comment. Codegen tool choice is implementation-owned (Deferred).
- **Live candle edge has one sanctioned path (AD-F7).** `data_api` computes the forming bar server-side via the existing `ml_signals.candles` aggregation function against incoming `snapshots:raw` ticks, publishes on derived `/ws/live` sub-channel `candles:{instrument_id}:{bar_seconds}`. Frontend never aggregates a candle itself from raw snapshot data.
- **Stack pins (verified 2026-09-13):** React 19.3.0, Vite 8.3.0 + `@vitejs/plugin-react` 6.1.1, `@tanstack/react-query` 5.102.8, `lightweight-charts` 5.2.1, FastAPI 0.141.1 (already pinned), TypeScript strict mode. No Redux/Zustand — React Query owns all server state, component state is sufficient (YAGNI/DESIGN-01).
- **Own dockerfile, no shared Node build stage.** `data_api` gets `troll/data_api.dockerfile` (not a Node stage bolted onto `troll/collector.dockerfile`, which stays untouched and keeps serving `collector`/`ranking_engine`/`bot_tui`) — layered on `nautilus-trader-base`, runs `vite build` against `troll/frontend/`, copies `dist/` into the final image; `node_modules`/build tooling never appear in the runtime layer.
- **Deployment:** `docker-compose.yml`'s `dashboard` service is removed; `data_api` absorbs its role, still `network_mode: host`, still `127.0.0.1`-bound (SEC-01 unchanged). SSH-tunnel remote-dev flow simplifies from two tunneled surfaces to one.
- **Module dependencies (AD-4 extended):** `data_api` → `ml_signals`, `ranking_engine`, `dydx_collector` (data types + pure functions only); `frontend` → `data_api` only, via HTTP/WS, never a direct Python import.
- **Deferred to the stories pass (from the architecture spine):** exact 1:1 mapping of every in-scope `dashboard.py` JSON route into `data_api/routes/*.py` (the spine's route-file grouping is a seed, not a mandate); OpenAPI→TypeScript codegen tool choice; `epic-14`'s formal closure (14.1/14.2 done, 14.3 becomes moot once the chart page is deleted, mark superseded in `sprint-status.yaml`, don't ship it); `bot_tui` SSOT-04/05 cross-check — any new rankings-page column or per-coin metric this epic ships must also land in `bot_tui`, backed by the same shared source (bots/live_paper stay TUI-only, no reverse parity needed).
- **Non-Goals restated for story-writing:** no bots/`live_paper` UI in this frontend (stays exclusively `bot_tui`'s domain — never reads `bots:status`/`bots:control`); no new analytical capability beyond parity; no auth/multi-user access; no native mobile app; no CRT/scanline effects.
- **Reuse `ml_signals/indicators.py`'s existing `MultiLevelOFI`/`MultiLevelOBI`/`microprice`/`spread` for the rollup builder (SSOT-01) — never reimplement this math** (Epic 16). Levels 5/10 for OFI/OBI reuse `ranking_engine`'s own existing level convention rather than inventing a third.
- **1-hour rollup tier is explicitly out of scope for Epic 16** — 1-minute rollup rows are all scalars (no per-level depth arrays), so even 120 weekly bars (~2.3 years) is only ~1.2M rows to scan, which stays fast on its own. Revisit only if real-world read latency on `nifelheim` (resource-constrained 2vCPU/3.7GB, see Epic 13's incident) proves insufficient — if ever needed, derive it from already-built 1m rollup rows, never re-touch raw 1s.
- **Epic 16 is forward-compatible with, but does not implement, Epic 15's not-yet-built `/api/candles` route** — its output is a plain time-ordered list, trivially sliceable into AD-F3's `before_ns`/`limit` cursor contract by whichever future Epic 15 story implements that route; the rollup only ever contains closed minutes, so AD-F7's live/forming-bar path is unaffected and keeps using the existing raw-1s live-buffer aggregation.

### UX Design Requirements

_Extracted from the finalized UX design contract at `_bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/` (`DESIGN.md` + `EXPERIENCE.md`, both `status: final`) — the project's first UX-driven surface (the existing web dashboard predates any UX design contract; the Bot Monitoring TUI is what prompted creating one)._

UX-DR1: Terminal color/typography system — inherit the terminal's own default fg/bg as the base (no fixed hex palette); four semantic ANSI-family accent tokens (attention-stale=yellow, attention-critical=red, attention-positive/negative=green/red, attention-neutral) used exclusively for attention states, never decoratively; monospace-only throughout, no size-based emphasis (bold/standout is a secondary reinforcing channel only, never a substitute for color).
UX-DR2: k9s-style navigation shell — breadcrumb header (row 0, plain/uncolored), footer hint bar (replaced by command bar on activation), `:` command bar with a defined vocabulary (`:coins`, `:bots`, `:q`) and an explicit "unknown command" echo state (never a silent no-op), `esc` pop-back-exactly-one-level semantics that never exits the program.
UX-DR3: Coins pane — list row component (rank, instrument ID, active Ranking Mode's score column, per-row stale badge on feed loss), `/` fuzzy-filter scoped to this pane only (substring match against instrument ID, `no matches` empty state, `esc` clears without leaving the pane), `m` keybinding toggling Ranking Mode (volume ↔ volatility).
UX-DR4: Bots pane — list row component (bot_id, sign-colored PnL, strategy/symbol, mode, position/exposure, uptime/last-heartbeat, win-rate-to-date), independent per-row stale badge, `s` start/stop toggle with a one-line footer-echo confirmation and no optimistic local state change (row waits for `bots:status` to confirm).
UX-DR5: Coin-detail full-screen view — live indicators region (Microprice/OFI/OBI/spread via the shared `ml_signals.indicators` code path) plus an order-book depth ladder that opens collapsed to top-of-book by default and toggles via `d` to full 20-level depth; graceful thin-book rendering (no padding rows, `no bids`/`no asks` text); `o` deep-links to the web dashboard at the same coin + time context.
UX-DR6: Bot-detail full-screen view — three bordered regions (live snapshot header; trades blotter; PnL-over-time sparkline with a day/week/month/all preset toggle via `t`, never free-form scrubbing); `o` deep-links to the dashboard's fuller view; each of regions 2–3 independently renders `history unavailable` if the Cache-history read surface is unreachable, without affecting region 1.
UX-DR7: Two independent staleness indicators — a Coins-pane-level stale badge tied to the ranking_engine heartbeat, and a separate per-bot-row stale badge tied to that specific bot's live_paper heartbeat; the two are never unified into one indicator.
UX-DR8: Accessibility floor — every color-carried state also has a non-color marker (glyph, text suffix, or fixed column position), so a color-blind reading still resolves correctly; the product makes no contrast guarantee beyond whatever the builder's own terminal theme provides; fully keyboard-operable, zero mouse-dependent affordances.
UX-DR9: Voice and tone — terse, data/state/keybinding-only strings; no marketing copy, no emoji, no exclamation marks, no encouragement copy.

### FR Coverage Map

FR1: Epic 1 - Configurable-interval snapshot capture
FR2: Epic 1 - Opt-in raw delta capture
FR3: Epic 1 - Fail-closed data integrity gate
FR4: Epic 1 - Reconnect & gap resilience
FR5: Epic 1 - USD-denominated liquidity capture
FR6: Epic 1 - Coin ranking sorted by volume
FR7: Epic 1 - Live watchlist
FR8: Epic 1 - Research ranking view
FR9: Epic 2 - Jupyter research environment
FR10: Epic 2 - Single indicator implementation, three consumption contexts
FR11: Epic 2 - Nautilus-native backtesting
FR12: Epic 2 - Dual-timeframe strategies
FR13: Epic 2 - Multi-coin backtest runs
FR14: Epic 3 - Dummy paper-trading strategy
FR15: Epic 3 - Trading-mode isolation
FR16: Epic 1 - Volatility-based ranking mode
FR17: Epic 4 - Keyboard-only TUI over the shared live feed
FR18: Epic 4 - Bots pane
FR19: Epic 4 - Coin list pane mirroring the dashboard
FR20: Epic 4 - Coin-detail view
FR21: Epic 4 - k9s-style navigation model
FR22: Epic 4 - Attention-only color coding
FR23: Epic 4 - Bot start/stop controls
FR24: Epic 4 - Live-only, no in-TUI history for market data
FR25: Epic 4 - Deep-linked browser handoff for coins
FR27: Epic 4 - Bot-detail trade/PnL history
UX-DR1–UX-DR9: Epic 4 - all Bot Monitoring TUI UX design requirements
FR28: Epic 8 - Per-chart settings/navigation panes
FR29: Epic 8 - TradingView-style pan/zoom parity across all coin-detail charts
FR30: Epic 8 - Selectable technical indicators from nautilus_trader.indicators
FR31: Epic 10 - Custom (non-Nautilus) indicator category on the chart page's indicator picker
FR32: Epic 10 - CVD, Cancel Pressure, and OFI available as picker-addable custom indicators, replacing their fixed microstructure-panel rows
FR33: Epic 10 - Persisted per-instrument chart indicator configuration, committable to source control
FR38: Epic 15 - Live coin-rankings table
FR39: Epic 15 - Candlestick chart with synced indicator panes
FR40: Epic 15 - Incremental, TradingView-style history loading
FR41: Epic 15 - Live edge stays consistent with loaded history
FR42: Epic 15 - Per-coin indicator configuration
FR43: Epic 15 - Lines mode
FR44: Epic 15 - 31-day metrics history view
FR45: Epic 15 - Docs page
FR46: Epic 15 - Terminal/ANSI visual identity

FR34 `[NEW — 2026-09-12, not yet in PRD, PM should fold in]`: Local-machine dashboard + bot_tui — the web dashboard and the bot monitoring TUI can both run on the user's own machine instead of on nifelheim, reached over an SSH tunnel to nifelheim's Redis and a new read-only data API, with zero change to the VPS-hosted collector/ranking_engine/dashboard/bot_tui services' own behavior when `DATA_API_URL` is unset.

FR35 `[NEW — 2026-09-12, not yet in PRD, PM should fold in]`: Remote catalog/metrics read API — a new, single, read-only FastAPI service exposes exactly the historical-chart and metrics-history reads `dashboard.py` currently does via direct local-disk access (SQLite `metrics_store`, Parquet catalog), reusing that existing code verbatim, bound to `127.0.0.1` only (no public port, per SEC-01).

FR36 `[NEW — 2026-09-12, not yet in PRD, PM should fold in]`: ranking_engine's periodic price/pct/volatility computation no longer re-scans the Parquet catalog every cycle — it is served from an in-memory, long-window price series maintained incrementally from the same live `snapshots:raw` feed `ranking_engine` already consumes, seeded once at process startup via a single Parquet backfill read per instrument (not re-read every `DB_WRITE_INTERVAL_SECONDS`).

FR37 `[NEW — 2026-09-12, not yet in PRD, PM should fold in]`: `ranking_engine`'s per-cycle catalog-read concurrency is bounded to a small, fixed worker count (not the prior unbounded-by-instrument-count default), as an independent, immediately-shippable mitigation to peak memory during the existing `compute_all()` cycle while FR36 is built.

FR38: Live coin-rankings table — every subscribed coin's live rank, price, and key metrics in one sortable table, row order matching `ranking_engine`'s published `rankings:live` order exactly (no independent client-side re-sort), stale coins visibly marked, click-through to a coin's chart page.

FR39: Candlestick chart with synced indicator panes — a coin's candlestick chart with zero or more indicator panes (OFI, order-book imbalance, volume, microprice, spread) stacked beneath it, sharing one time axis via the charting library's native multi-pane sync (never custom event-relay code); adding/removing/reconfiguring an indicator never resets zoom/pan; touch parity (drag-to-pan, pinch-to-zoom) on phone/tablet; up to 5 panes each get a distinct, consistently-assigned color from the 16-color palette.

FR40: Incremental, TradingView-style history loading — a chart's candlestick pane scrolls back through full history with older bars loading progressively (cursor-paginated `before_ns`/`limit`, never a full-history fetch); initial load matches today's 120-bar default; `has_more: false` stops further requests at the true start of history; indicator panes co-page their own snapshot data in the same interaction, never lagging the candlestick pane's loaded range.

FR41: Live edge stays consistent with loaded history — the currently-forming candle bar updates live at the right edge, sourced exclusively from the one sanctioned server-aggregated channel (never assembled client-side from raw ticks); a bar-size change or navigation never leaves a stale live bar overlapping freshly-loaded history.

FR42: Per-coin indicator configuration — add/remove/reconfigure indicators on a coin's chart, persisted per coin and restored on next visit, via the Facade's one sanctioned write path (`PUT /api/coin/{iid}/indicators`).

FR43: Lines mode — switch a coin's chart page from candlestick to a direct comparison of raw bid/ask/mid/microprice/price series, carried forward from today's dashboard's Candles/Lines toggle; same cursor-paginated contract as candlestick history; switching modes preserves the currently-viewed time range.

FR44: 31-day metrics history view — a coin's ranking-input metrics (volume, volatility, etc.) plotted over the trailing 31 days; a metric with no data for part of the window renders a visible gap, never an interpolated flat line.

FR45: Docs page — the existing reference/help page content, rebuilt on the new stack at its current URL shape; every section on today's `/docs` page has a corresponding section on the new page (diffable content checklist, not a rewrite); renders in the terminal visual identity like every other page.

FR46: Terminal/ANSI visual identity — every page (rankings, chart, history, docs) renders in a consistent old-school terminal aesthetic: monospace DOS/BIOS-style bitmap font throughout (no proportional-font fallback), ASCII-art-style decorative elements (box-drawing borders/dividers, terminal-style loading/empty states), and the classic 16-color VGA/ANSI palette as the *entire* color system (background, text, borders, semantic states, chart series colors) — no color outside that set anywhere in the frontend; no CRT/scanline effects.

FR47: Epic 16 - Incremental 1-minute rollup cache
FR48: Epic 16 - Threshold-based candle source dispatch (raw 1s vs. rollup, explicit source tag, fallback on missing coverage)
FR49: Epic 16 - Correct-by-construction OFI continuity across minute boundaries
FR50: Epic 16 - Backfill capability for pre-existing/historical 1-second data

FR51: Epic 17 - Rankings page gains Performance + Technicals tabs with a pinned Symbol/Name column
FR52: Epic 17 - Story 15.8 (31-day metrics history) unparked and completed as this epic's shared historical-data dependency
FR53: Epic 17 - Performance tab: multi-window % change columns sharing one historical query path with the History page
FR54: Epic 17 - Rankings/Performance → `/history/:iid` link wired (currently orphaned)
FR55: Epic 17 - Technicals tab: user-managed indicator columns (add/configure/remove/reorder) reusing the chart's existing 37-entry indicator catalog
FR56: Epic 17 - Filter panel: AND-combined `<field> <operator> <value>` conditions, including any added Technicals column

FR57: Epic 18 - Drawing tools: trendline, horizontal line, measurement tool
FR58: Epic 18 - Bar Replay
FR59: Epic 18 - Volume Profile family (FRVP, VRVP, SVP, SVP-HD, PVP) backed by the existing `/api/candles` route
FR60: Epic 18 - Toolbar/legend/pane placement and operation-parity pass, including the top-toolbar symbol-slot reconciliation

FR61: Epic 19 - Explicit `venue` field surfaced through the data model/schema/API
FR62: Epic 19 - `data_api`'s duplicated `CATALOG_PATH` constants consolidated to one shared, multi-venue-capable setting
FR63: Epic 19 - New `troll/bybit_collector/`, mirroring `dydx_collector`'s direct-asyncio-PyO3 architecture
FR64: Epic 19 - New `troll/hyperliquid_collector/`, same architecture
FR65: Epic 19 - Rankings/screener venue column + filter
FR66: Epic 19 - Self-maintained CEX/DEX registry (`troll/common/venues.py`)

FR67: Epic 20 - Alert creation dialog (condition builder, frequency, expiration, message template, webhook URL)
FR68: Epic 20 - Local alert evaluation engine + webhook POST + in-app toast
FR69: Epic 20 - Alerts list view

## Epic List

### Epic 1: Trustworthy Coin Ranking & Watchlist
Builder opens a ranking/watchlist view showing which coins are worth watching right now, backed by continuously-captured, integrity-gated market data, with opt-in deep (raw-delta) capture for coins worth studying further. FR1–FR5 largely restate already-adopted architecture (Gatekeeper gate is built) — stories here verify/close gaps rather than rebuild. FR6–FR8 are the newer surface to confirm/build against the existing dashboard. Extended 2026-07-24 with FR16: a second, user-selectable Ranking Mode (volatility, alongside FR-6's volume) sharing one `ranking_engine` so the dashboard, TUI, and backtests never diverge.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR8, FR16

### Epic 2: Reusable Signal Research & Multi-Coin Backtesting
Builder writes an indicator once in Jupyter (Nautilus notebook conventions) and runs it unmodified in a multi-coin, dual-timeframe `BacktestNode` run across the current Watchlist. Standalone: uses Epic 1's Watchlist as an input, but delivers complete research→backtest value on its own.
**FRs covered:** FR9, FR10, FR11, FR12, FR13

### Epic 3: Live Paper-Trading Integration Proof
Builder starts the Dummy Strategy and watches it place paper orders driven by every signal validated in backtest — closing the full research→backtest→live loop, in a new module structurally isolated from the data-collection path per the amended AD-8. Standalone: consumes Epic 2's signals but is the first and only sanctioned `TradingNode`/`Strategy` usage, delivered end to end including the trading-mode isolation safeguard.
**FRs covered:** FR14, FR15

### Epic 4: Bot Monitoring TUI
Builder SSHes into the box, opens a keyboard-only urwid terminal UI, and at a glance sees per-bot PnL/health and which coins are hot right now — drills into a coin's live indicators/book or a bot's trade history with a keypress, hands off to the web dashboard for deeper graphs, and starts/stops a bot without leaving the terminal. Standalone: reads Epic 1's ranking engine, Epic 2's shared indicators, and Epic 3's `live_paper` as inputs (one story extends `live_paper` with Cache persistence for FR27), but delivers complete monitoring/control value on its own. Backed by a finalized UX design contract (`DESIGN.md`/`EXPERIENCE.md`, 2026-07-24) — the project's first UX-driven surface.
**FRs covered:** FR17, FR18, FR19, FR20, FR21, FR22, FR23, FR24, FR25, FR27, UX-DR1, UX-DR2, UX-DR3, UX-DR4, UX-DR5, UX-DR6, UX-DR7, UX-DR8, UX-DR9

### Epic 8: TradingView-Style Multi-Chart Navigation & Indicator Overlays
Builder opens a coin's chart on the web dashboard and it behaves like a professional charting site: every chart (not just the price chart) pans on drag and zooms on scroll and stays x-axis-linked to the candlestick chart, each chart carries its own settings toolbar instead of one shared bar, the candlestick chart can show any indicator from `nautilus_trader.indicators`' own built-in library (no third-party TA dependency), and Candles mode can overlay live bid/ask lines on top of the OHLC candlesticks. Extends Story 7.1 (drag-to-pan/scroll-zoom, Lines/Candles/Ticks modes) rather than replacing it — stays on Plotly, no new charting library. Numbered 8 (not 5) to avoid colliding with the standalone bypass-epics 5–7 already tracked in `sprint-status.yaml`.
**FRs covered:** FR28, FR29, FR30

### Epic 10: Custom Chart Indicators & Persisted Configuration
Builder adds dYdX-specific microstructure signals — CVD, Cancel Pressure, OFI — to the chart page's indicator picker (Story 8.4) as a second, clearly-separated category alongside `nautilus_trader.indicators`' native library, since none of the three is derivable from OHLCV candles alone or exists anywhere in `nautilus_trader` itself. Each one already has a working implementation on the chart page's fixed 7-row microstructure panel (`ml_signals/chart_data.py`) or in `ml_signals/book_features.py` — this epic re-exposes that existing math through the picker (never reimplementing it) and retires the corresponding fixed row once its picker equivalent lands, so the same signal is never shown in two places at once. Closes with persisting a coin's active indicator selection to a source-control-committable file, so a chart's configuration survives a page reload/redeploy instead of resetting to empty every time. Numbered 10 (not 9) because Epic 9 is itself a standalone bypass-epic bug-fix story, not a real epics.md entry (see `sprint-status.yaml`) — Epic 10 continues the sequence past its file-path prefix.
**FRs covered:** FR31, FR32, FR33

### Epic 12: Local Dashboard + bot_tui, VPS as Data API
nifelheim (2 vCPU/3.7GB/0 swap) is resource-oversubscribed (`troll/.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md`) — collector + dashboard + ranking_engine + bot_tui all running on one box leaves no headroom, contributing to both collector stale-book bursts and ranking_engine's OOM-restart loop. `bot_tui` already talks to nothing but Redis pub/sub (zero code change needed once Redis is SSH-tunneled); `dashboard.py` additionally needs a small new read-only data API for its 4 local-disk (SQLite/Parquet) reads. Moving both to the user's own machine removes two of the four services from nifelheim entirely.
**FRs covered:** FR34, FR35

### Epic 13: ranking_engine Memory/CPU Stabilization
`ranking_engine` OOM-restarts roughly every 2 minutes on nifelheim (confirmed via `docker events`: real host OOM-kill, not an app-level exit). Root mechanism: `compute_all()` re-scans a full 25h Parquet window, 32-way concurrent, every 60s cycle — for the same `DydxSecondSnapshot` data already streaming live through Redis, just discarded after 5 minutes by `ranking_engine`'s own in-memory window. This epic bounds the immediate concurrency (fast, independent mitigation) and then removes the recurring re-scan entirely by keeping a long-window price series in memory, backfilled once at startup.
**FRs covered:** FR36, FR37

### Epic 15: Dashboard React/TypeScript Rewrite
Builder gets the same dashboard capabilities they use every day — live coin rankings, a coin's candlestick chart with synced indicator panes and TradingView-style scroll-back history, 31-day metrics history, docs — rebuilt as a fast React SPA with a terminal/ANSI visual identity, served by a single Read-Only Facade backend (`data_api`) that replaces `troll/ml_signals/dashboard.py` entirely. Single epic (not split by page or by frontend/backend layer): every FR shares the same two core components (`data_api`, `frontend`) end-to-end, backed by a finalized architecture spine (`ARCHITECTURE-SPINE.md`, status final) that already fixes the technical shape — no risk boundary between pages justifies separate epics. Supersedes `epic-14` (bypass-epic, never entered into this document; 14.3 becomes moot and should be marked superseded, not shipped).
**FRs covered:** FR38, FR39, FR40, FR41, FR42, FR43, FR44, FR45, FR46
**NFRs covered:** NFR6, NFR7, NFR8, NFR9

### Epic 16: Minute-Rollup Candle Cache
Builder's chart page can show daily/weekly candles — with order-book-derived signal (OFI/OBI, top-of-book) baked in — without every request rescanning years of raw 1-second data. The collector incrementally builds a small `DydxMinuteRollup` cache as data streams in (O(1)/second, no periodic full rescan); wide-window candle requests read from it instead of raw 1s, with correct-by-construction OFI continuity across minute boundaries and a fallback to raw 1s when rollup coverage is missing. The raw 1-second archive stays fully intact and authoritative — the rollup is a regenerable performance cache, never a replacement. Backend/collector-pipeline scope, standalone: delivers complete value against the existing `dashboard.py`/`data_api` candle route today, and is a dependency for Epic 15's future `/api/candles` story once that lands. No PRD/Architecture update precedes this addition (same precedent as Epic 12/13) — created via direct technical investigation this session.
**FRs covered:** FR47, FR48, FR49, FR50
**NFRs covered:** NFR10

### Epic 17: Screener — Rankings Becomes a Tabbed Performance/Technicals Screener
Builder's Rankings page (Story 15.2, one flat live table today) becomes the full screener: a pinned Symbol/Name column plus Performance and Technicals tabs. Performance's multi-window % change and the still-parked Story 15.8 (31-day metrics history) share one `metrics_store` query path instead of two independent calculations — this epic unparks and completes 15.8 as part of that work, and wires the currently-missing Rankings/Performance → `/history/:iid` link. Technicals reuses the chart's existing 37-entry indicator catalog (`chart_indicators.py`/`custom_indicators.py`) and `IndicatorPicker.tsx` as user-managed table columns — no new indicator math, no curated MVP subset (NFR11 also pins this epic to a hand-rendered table, no grid framework). Standalone: extends Epic 15's `RankingsPage.tsx`/`data_api` foundation, doesn't depend on Epic 18/19.
**FRs covered:** FR51, FR52, FR53, FR54, FR55, FR56
**NFRs covered:** NFR11

### Epic 18: Chart — Drawing Tools, Bar Replay, Volume Profile, Placement Pass
Builder gets the chart features Epic 15 never scoped: trendline/horizontal-line/measurement drawing tools, Bar Replay, and the full Volume Profile family (Fixed Range, Visible Range, Session, Session HD, Periodic) sharing one calculation engine and one rendering Primitive, backed by the existing `/api/candles` route (confirmed sufficient — no new backend endpoint, no raw-tick data needed). Closes with a placement/operation-parity pass reconciling the original brief's toolbar-driven navigation model against this app's actual table-first navigation (Rankings row → `/chart/:iid`, fixed bar size) — resolved as a read-only symbol label + back-to-Rankings link, not a free symbol/timeframe picker, no theme toggle. Standalone: extends the existing `LightweightChart.tsx`/`ChartPage.tsx` chart instance, doesn't depend on Epic 17/19.
**FRs covered:** FR57, FR58, FR59, FR60

### Epic 19: Multi-Exchange Support — Bybit and Hyperliquid
Builder's catalog, `data_api`, and screener stop being dYdX-only. `venue` becomes an explicit field (not just an implicit `instrument_id` suffix) across the schema/API; `data_api`'s six independently-duplicated `CATALOG_PATH` constants consolidate to one shared, multi-collector-capable setting; two new sibling collectors (`troll/bybit_collector/`, `troll/hyperliquid_collector/`) mirror `dydx_collector`'s own direct-asyncio-PyO3 architecture — never `TradingNode`/`DataEngine` (FORK-02) — using the `BybitHttpClient`/`BybitWebSocketClient` and `HyperliquidHttpClient`/`HyperliquidWebSocketClient` PyO3 bindings already present in this fork. Closes with a Rankings venue column/filter and a small self-maintained CEX/DEX registry (dYdX and Hyperliquid are both on-chain perp DEXes; Bybit is a CEX — a real distinction once all three coexist). Standalone at the collector/data layer; the Rankings venue column is the one point of contact with Epic 17's screener work.
**FRs covered:** FR61, FR62, FR63, FR64, FR65, FR66

### Epic 20: Alerts — Webhook Delivery (deferred, built last)
Builder can define a price/indicator condition and get a webhook POST (plus an in-app toast) when it fires — the same generic delivery model TradingView itself uses, since there's no native Telegram integration anywhere; wiring a webhook to an actual Telegram relay bot is the user's own infrastructure, out of scope here. Deliberately sequenced dead last, after Epics 17–19 are done — a backend-first addition with no dependency the earlier epics need. Standalone once started: condition builder + local evaluation engine + alerts list view, evaluated against the same live Redis feed `data_api`'s `ws/live.py`/`redis_bus.py` already run, not a second polling loop.
**FRs covered:** FR67, FR68, FR69

## Epic 1: Trustworthy Coin Ranking & Watchlist

Builder opens a ranking/watchlist view showing which coins are worth watching right now, backed by continuously-captured, integrity-gated market data, with opt-in deep (raw-delta) capture for coins worth studying further. FR1–FR5 largely restate already-adopted architecture (the Gatekeeper gate is already built) — Story 1.1 verifies and closes any gaps rather than rebuilding. FR6–FR8 are newer surface confirmed/built against the existing dashboard.

### Story 1.1: Verify the data-integrity gate end-to-end

As the builder/operator,
I want confirmation that every data-integrity invariant (interval capture, raw-delta opt-in, fail-closed rejection, reconnect/gap resilience, USD-liquidity classification) actually holds in the running collector,
So that I can trust every downstream ranking/backtest/live decision without re-checking data quality myself.

**Acceptance Criteria:**

**Given** the collector config
**When** I inspect the snapshot interval
**Then** it is a config value (not hardcoded)
**And** its default is 0.5s, and changing it requires no code change (FR1)

**Given** a coin flagged for Raw Delta Capture
**When** the collector runs
**Then** raw deltas are captured for that coin only, Snapshot capture for other coins is unaffected, and retention/pruning for that coin's raw-delta data is a per-coin config value including an "unlimited" (never-pruned) setting (FR2)

**Given** an incoming tick that would produce a crossed book, a stale book, or a precision-invalid value
**When** the collector's gate evaluates it
**Then** the item is rejected — never fabricated, clamped, or averaged
**And** a WARNING log line records the full offending payload and the specific rejection reason, and no validity/flag field is added to any schema (FR3)

**Given** a WebSocket/HTTP disconnect and reconnect
**When** the collector resumes
**Then** no previously stored data is corrupted
**And** the resulting gap appears as a visible break (e.g. `None`/null) in any downstream chart/read rather than an interpolated or flat line (FR4)

**Given** open interest and volume data for any coin
**When** liquidity is classified
**Then** classification uses `volume24H` (USD) or `openInterest × oraclePrice`
**And** no code path compares raw token-unit `openInterest` directly against a USD threshold (FR5)

**Given** any gap found while verifying the above
**When** the gap is confirmed
**Then** it is fixed within this story (not deferred), so all FR1–FR5 consequences hold in the current codebase

### Story 1.2: Default the ranking table to volume-sort

As the builder,
I want the rankings view to default to descending `volume24H` order,
So that I immediately see the highest-opportunity coins without manually choosing a sort.

**Acceptance Criteria:**

**Given** the dashboard rankings page loads with no user-applied sort
**When** the initial table renders
**Then** rows are ordered strictly by descending `volume24H` (USD)

**Given** the rankings table
**When** it refreshes on each 1s poll
**Then** the volume-sorted order updates to reflect newly arrived Snapshot data, not a static/one-time computation

**Given** HFT indicators (OFI, OBI, microprice, spread) and TA indicators (e.g. RSI) computed per coin
**When** they are displayed in the rankings table
**Then** they appear as visible columns/values but do not alter the default sort order

**Given** the user manually clicks a different column to sort by
**When** they do so
**Then** the existing client-side sortable-by-any-metric behavior still works
**And** reloading/refreshing the page returns to the volume-sorted default

### Story 1.3: Expose the live watchlist as a queryable, backtest-consumable coin set

As a strategy developer,
I want to fetch the current Watchlist's coin set programmatically,
So that a multi-coin backtest can use it directly without manually editing a per-coin config list.

**Acceptance Criteria:**

**Given** the live ranking state
**When** a caller requests the current Watchlist
**Then** a coin-set (e.g. list of instrument IDs) is returned reflecting the live, continuously-refreshed ranking, not a fixed, manually-curated list

**Given** a coin's ranked opportunity becomes short-lived and it no longer qualifies
**When** the Watchlist is next queried
**Then** that coin is no longer present in the returned set (and a newly-qualifying coin is present) with no manual config edit required

**Given** a `BacktestDataConfig`/`BacktestNode` setup
**When** it is configured to use the Watchlist
**Then** it accepts the returned coin-set as-is to build its instrument list, satisfying FR13's multi-coin requirement without per-coin manual editing

**Given** the Watchlist query
**When** it executes
**Then** it does not load unbounded catalog data (NFR3) — it reads only current/live ranking state, not historical ticks

### Story 1.4: Persist and query historical coin ranking

As a researcher,
I want to see how a coin's ranking evolved over time,
So that I can decide whether it warrants opt-in Raw Delta Capture (FR2).

**Acceptance Criteria:**

**Given** ranking is computed on an ongoing basis
**When** a ranking cycle completes
**Then** the rank (and its contributing volume/indicator values) for each coin at that point in time is persisted, not just overwritten as the latest snapshot

**Given** a past timestamp within the collector's retention window
**When** the user queries ranking history for a specific coin
**Then** the historical rank/value at (or nearest to) that timestamp is returned

**Given** the historical ranking store
**When** it accumulates data over time
**Then** it does not grow unbounded in violation of NFR3 — retention follows the same bounded/rolling-window discipline as other in-memory/catalog data, or is itself a bounded, prunable store

**Given** a coin not currently in the live Watchlist
**When** its past ranking history is queried
**Then** historical data for it is still retrievable if it was previously ranked within the retention window — ranking history is not deleted merely because a coin drops out of the current Watchlist

### Story 1.5: Sequence-verified order book resync

Added post-hoc from a first-principles brainstorming session (`_bmad-output/brainstorming/brainstorm-orderbook-data-quality-2026-07-02/`) that found the existing gate (Story 1.1) only detects corruption symptomatically (crossed book) and never proves recovery. dYdX's WS orderbook channel carries a per-market `message_id` sequence counter that is currently discarded before reaching Python (FR3/FR4 extension).

As the collector,
I want to detect a dropped WebSocket message by its exact sequence number and provably resync the local order book afterward,
So that a gap is caught the instant it happens rather than inferred later from a crossed-book symptom, and the book is known-correct again rather than assumed healed.

**Acceptance Criteria:**

**Given** the Rust dYdX adapter's WS orderbook envelope (`DydxWsChannelDataMsg`/`DydxWsChannelBatchDataMsg`)
**When** it is converted to `DydxWsOutputMessage::Orderbook{Snapshot,Update,Batch}` and crosses the PyO3 boundary
**Then** the message's `message_id` is no longer discarded — it is available to the Python collector per market

**Given** a market with a known last-confirmed `message_id`
**When** the next message for that market arrives
**Then** the collector checks `message_id == last_id + 1` exactly (not just `message_id > last_id` regression), and a gap is detected the instant it fails

**Given** a sequence gap is detected for market X
**When** the collector enters resync mode for X
**Then** it stops applying incoming WS messages to X's local book state and instead buffers them in order, and halts 1s snapshot emission for X (does not touch the taint/discard/parquet-gap mechanics — that is Story 1.6)

**Given** resync mode is active for market X
**When** a REST order book snapshot for X is fetched
**Then** X's local book state is replaced wholesale with the snapshot, then every buffered message is replayed on top of it in order, relying on absolute-per-level update semantics (confirmed: dYdX updates replace a level's size, they are not relative deltas) so replay is idempotent and no precise anchor/cut-point is required

**Given** the snapshot-swap-and-replay sequence
**When** it executes
**Then** it runs as one synchronous block with no `await` between swapping state and finishing the buffered replay, relying on the collector's single-threaded asyncio loop so no WS message for that market can be processed concurrently and slip through unbuffered

**Given** replay of the buffer completes
**When** resync mode exits for market X
**Then** `last_message_id` is reset to the last replayed message's id and live per-message processing resumes normally

### Story 1.6: Taint-window bar discard and bounded raw-capture housekeeping log

Depends on Story 1.5's resync-mode flag. From the same brainstorming session: corrupted 1s bars must never be fabricated, flagged-but-kept, or interpolated — and postmortem diagnosis needs raw context without unbounded storage growth (FR3/FR4 extension, NFR3 memory-bounded discipline).

As the collector,
I want to discard 1s snapshots produced during an active resync window and separately capture bounded raw context around the triggering event,
So that ML/backtest consumers see a genuine parquet gap (never a fabricated or silently-wrong bar) and a human can later diagnose exactly what went wrong.

**Acceptance Criteria:**

**Given** market X is in resync mode (per Story 1.5) for some time window
**When** the 1s snapshot loop would otherwise emit a bar for X during that window
**Then** the bar is discarded entirely — not written to the catalog, not flagged-but-present — leaving a genuine gap in the parquet output

**Given** each market being tracked
**When** WS messages arrive during normal operation
**Then** the collector keeps only a small rolling in-memory ring buffer of raw messages per market (~30-60s), never an unbounded or continuously-archived raw capture

**Given** a sequence gap fires for market X (Story 1.5)
**When** the housekeeping log is written
**Then** it flushes that market's ring buffer (raw messages from shortly before and after the trigger) plus the event timestamp to a separate housekeeping log, so storage cost scales with number of corruption events, not with uptime

### Story 1.7: Crossed-book CRITICAL escalation for steady-state desync

Depends on Story 1.5's resync-mode flag (to distinguish steady-state from an expected transient window). From the same session: a crossed book observed *outside* any known-cause window (not mid-reconnect CLEAR-replay, not mid-resync buffer-replay) means either a local reconstruction bug or dYdX sent bad data with an intact, gap-free sequence — a cause `message_id` checking structurally cannot see. This is flagged as the highest-severity, most-visible event in the system.

As the operator,
I want a crossed book detected during steady-state (no known gap, no active resync, no active reconnect) to be loud and unmistakable,
So that I am alerted to failure causes no existing mechanism predicted, instead of it blending into routine gap-triggered bar discards.

**Acceptance Criteria:**

**Given** a market's local book is observed crossed (`best_bid >= best_ask`)
**When** this occurs while the market is in an expected transient window (mid-reconnect CLEAR-replay, or mid-resync buffer-replay per Story 1.5)
**Then** it is a silent skip exactly as today — no escalation, no snapshot emitted

**Given** a market's local book is observed crossed
**When** this occurs in steady state — no known sequence gap (Story 1.5), not mid-resync, not mid-reconnect
**Then** it is logged at CRITICAL severity to a distinct high-danger event log, separate from routine gap-triggered bar discards (Story 1.6), and is immediately visible (not buried in routine INFO/WARNING volume)

### Story 1.8: Volatility-based ranking mode via a dedicated ranking engine

Added 2026-07-24 for FR16. Extracts `dashboard.py`'s inline ranking logic (`_volume_loop_task`/`_rankings_json`/`_watchlist_ids`/`_current_ranks`) plus the existing SQLite `metrics_store` history persistence into a new `ranking_engine` module — the same Gatekeeper paradigm one layer up, applied to derived ranking data instead of raw market data (architecture AD-9).

As the researcher/builder,
I want a second, user-selectable Ranking Mode based on cross-sectional volatility, computed and published by one shared ranking engine alongside the existing volume mode,
So that I can switch between "what's loud" (volume) and "what's moving" (volatility) with the dashboard, backtests, and any future reader always agreeing on the order, never computing it independently.

**Acceptance Criteria:**

**Given** the existing inline ranking logic in `dashboard.py`
**When** this story is implemented
**Then** it is extracted into a new `ranking_engine` module that becomes the sole computer/publisher of Coin Ranking — both volume and volatility — publishing on Redis channel `rankings:live` a JSON message with `mode` (`"volume"` | `"volatility"`), `updated_at`, and an ordered `ranks` list of `{instrument_id, rank, volume24h, volatility_score}` objects, with both score fields always present regardless of active mode (FR16, architecture AD-9)

**Given** subscribed coins' captured price/return data
**When** the ranking engine computes volatility
**Then** it uses standard deviation of price/returns over a configurable lookback window, defaulting to 1 hour, ranking coins by relative (cross-sectional) volatility against all other subscribed coins — changing the lookback requires no code change (FR16)

**Given** the ranking engine is running with an active Ranking Mode
**When** a mode-switch request is published to Redis channel `ranking:control`
**Then** the active mode changes atomically for every reader — Ranking Mode is global shared state, not per-viewer, and last-write-wins on a near-simultaneous double-switch is accepted (architecture AD-9)

**Given** the ranking engine's publish discipline
**When** it publishes to `rankings:live`
**Then** it publishes on both rank-change and a fixed heartbeat interval, so readers can distinguish a missed heartbeat (stale) from "nothing changed" (architecture AD-9)

**Given** the relocated ranking-history store (SQLite `metrics_store`)
**When** historical ranking queries are made (FR8, Story 1.4)
**Then** they continue to work identically post-extraction — no regression to Story 1.4's historical-ranking-view behavior

## Epic 2: Reusable Signal Research & Multi-Coin Backtesting

Builder writes an indicator once in Jupyter (Nautilus notebook conventions) and runs it unmodified in a multi-coin, dual-timeframe `BacktestNode` run across the current Watchlist. Existing code already covers part of this: `ml_signals/indicators.py` has `Microprice`, `OrderFlowImbalance`, `MultiLevelOBI`, `MultiLevelOFI`, `OnlineLogisticTrend` as proper Nautilus `Indicator` subclasses, and `backtest_dydx.py` already uses `BacktestNode`/`BacktestDataConfig`/`ImportableStrategyConfig`. Gaps found during review: the existing notebook (`dydx_collector/notebooks/dydx_catalog_pandas.ipynb`) calls `catalog.trade_ticks()` with no time bound (violates NFR3), and `backtest_dydx.py` is single-symbol/single-timeframe only (no multi-coin, no raw-Snapshot-granularity path).

### Story 2.1: Bring the Jupyter research environment in line with Nautilus conventions

As a researcher,
I want the research notebook workflow to follow Nautilus's own example research-notebook conventions and respect the catalog's memory-bounded access rules,
So that I can develop indicators against real catalog data without violating the project's data-access discipline or reinventing tooling.

**Acceptance Criteria:**

**Given** the existing `dydx_collector/notebooks/dydx_catalog_pandas.ipynb` notebook
**When** its catalog reads are reviewed
**Then** any `catalog.trade_ticks()`/similar call with no start/end bound is replaced with a time-bounded query, consistent with NFR3 and the project's memory-discipline rule (FR9)

**Given** Nautilus's own shipped example research notebooks (the tutorial/research notebooks in `nautilus_trader`)
**When** the dYdX research notebook's structure is compared against them
**Then** it follows the same conventions (catalog access pattern, no custom notebook framework) rather than ad-hoc pandas-only exploration (FR9)

**Given** a new indicator authored in the research notebook
**When** it is imported
**Then** it is imported from `ml_signals.indicators` (or equivalent shared module) — never redefined inline in the notebook (FR9)

**Given** the notebook
**When** it runs against a multi-week catalog
**Then** it completes without loading the full unbounded catalog into memory (validates NFR3 specifically in the research context, since this differs from the streaming `BacktestDataConfig` path used elsewhere)

### Story 2.2: Single indicator implementation reused across research, backtest, and live

As a strategy developer,
I want every indicator (`Microprice`, `OrderFlowImbalance`, `MultiLevelOBI`, `MultiLevelOFI`, `OnlineLogisticTrend`, and future signals) to have exactly one implementation reused unmodified in Jupyter, backtest, and live contexts,
So that a signal validated in research behaves identically everywhere it's used.

**Acceptance Criteria:**

**Given** the existing indicators in `ml_signals/indicators.py`
**When** they are used in the research notebook, a `BacktestNode`-run strategy, and (once built in Epic 3) the live Dummy Strategy
**Then** the same class/import is used in all three contexts with no parallel/duplicate implementation (FR10)

**Given** a new indicator built for the first time
**When** it is authored
**Then** it is added to `ml_signals/indicators.py` as a Nautilus `Indicator` subclass importable by all three contexts, not defined locally in a notebook or strategy file (FR10)

**Given** an indicator's behavior is validated in the research notebook
**When** the same indicator is instantiated inside a `BacktestNode`-run strategy
**Then** it produces identical output for identical input data (a regression/consistency test asserting this)

**Given** the module-boundary convention (AD-4)
**When** indicators are imported by `ml_signals` consumers
**Then** no consumer reimplements indicator logic locally — it is always imported from the shared module

### Story 2.3: HFT-granularity and configurable-timeframe backtesting on the same underlying data

As a strategy developer,
I want to backtest at raw Snapshot (0.5s/1s) granularity as well as at slower, configurable candlestick timeframes derived from the same capture,
So that I can validate both HFT-style and structural signals without re-collecting data or writing a second backtest path.

**Acceptance Criteria:**

**Given** a strategy that consumes raw `DydxSecondSnapshot`-granularity data
**When** it is backtested
**Then** `BacktestNode` + `BacktestDataConfig` streams the Snapshot data directly, with no full-catalog in-memory load (FR12)

**Given** a strategy that consumes candlestick bars
**When** it is backtested
**Then** the candlestick aggregation window is a config value (e.g. 1s, 1m, 5m) requiring no code change, and bars are derived from the same underlying captured data as the HFT path (FR12)

**Given** both backtest paths
**When** either runs
**Then** no custom simulation/matching loop exists anywhere in `troll/` — both go through `BacktestNode`/`BacktestEngine` only (FR11)

**Given** a strategy referenced for either timeframe
**When** it's configured
**Then** it is referenced via `ImportableStrategyConfig` by string path, enabling parameter sweeps/time-range filtering with no code changes (FR11)

### Story 2.4: Multi-coin backtest runs across the live Watchlist

As a strategy developer,
I want to run a single backtest across every coin currently in the Watchlist, not just one hardcoded symbol,
So that I can validate a strategy's behavior across the full ranked opportunity set at once.

**Acceptance Criteria:**

**Given** the Watchlist API/function built in Story 1.3
**When** a backtest run is configured
**Then** it accepts the Watchlist's current coin-set as its instrument universe instead of a single hardcoded symbol parameter (FR13)

**Given** a `BacktestNode` run configured this way
**When** it executes
**Then** `BacktestDataConfig` is built for each Watchlist coin without manual per-coin config editing (FR13)

**Given** a coin enters or leaves the Watchlist between backtest runs
**When** the backtest is re-run
**Then** its instrument set reflects the current Watchlist automatically — no code change required to add/remove a coin (FR13)

**Given** a multi-coin run
**When** results are produced
**Then** per-coin results remain distinguishable (not silently aggregated into a single undifferentiated result), so ranking-vs-performance can still be analyzed per coin

## Epic 3: Live Paper-Trading Integration Proof

Builder starts the Dummy Strategy and watches it place paper orders driven by every signal validated in backtest — closing the full research→backtest→live loop, in a new module structurally isolated from the data-collection path per the amended AD-8. Confirmed via code review: no live/paper-trading module exists yet (only `example_strategy.py`/`ofi_strategy.py`, both backtest-only) — this epic is genuinely net-new, the first sanctioned `TradingNode`/`Strategy` usage in `troll/`.

### Story 3.1: Scaffold the isolated live/paper-trading module

As the builder/operator,
I want the paper-trading strategy to live in its own module, structurally separate from the data-collection/reading path, with real-money execution unreachable by any default or accidental config,
So that trading logic can safely be the one place `TradingNode`/`Strategy` is used without risking the collector's stability or accidentally trading with real funds.

**Acceptance Criteria:**

**Given** the new live/paper-trading module
**When** its location and imports are reviewed
**Then** it lives outside `dydx_collector/` and `ml_signals/` (a new top-level module under `troll/`), and depends only on shared data types and pure utilities from those packages — never their stateful internals (AD-4) (FR15)

**Given** `dydx_collector` and `ml_signals`'s existing reader modules
**When** they are reviewed after this story
**Then** none of them import `TradingNode`, `Strategy`, or `DataEngine` — that usage is confined entirely to the new module (AD-8 amendment boundary) (FR15)

**Given** the new module's default configuration
**When** it starts with no explicit override
**Then** it runs in paper mode only (FR15)

**Given** a desire to enable real-money (non-paper) execution
**When** the operator attempts it
**Then** it requires an explicit, separate configuration step that is not reachable by any default or accidental config state — a distinct field/file that must be deliberately set, never a flag flippable by a typo or default fallback (FR15)

**Given** the new module
**When** Docker deployment is considered
**Then** it follows the existing two-image split pattern (thin layer on `nautilus-trader-base`), consistent with the architecture spine's deployment convention

### Story 3.2: Dummy Strategy consumes every produced signal and runs live in paper mode

As an operator,
I want to start a `TradingNode`-based Dummy Strategy that wires together every indicator/signal produced by the research side and runs against live dYdX market data in paper mode,
So that I can see the full research → backtest → live loop actually close, proving every validated signal works end to end without risking real capital.

**Acceptance Criteria:**

**Given** the indicators/signals implemented in Epic 2 (`Microprice`, `OrderFlowImbalance`, `MultiLevelOBI`, `MultiLevelOFI`, `OnlineLogisticTrend`, and any others)
**When** the Dummy Strategy starts
**Then** it consumes all of them via the same shared implementation used in research/backtest (FR10) — no reimplementation for the live context (FR14)

**Given** the Dummy Strategy is running
**When** it processes live dYdX market data
**Then** it does so via `TradingNode` in paper mode, placing paper orders driven by the wired-in signals (FR14)

**Given** a new indicator is added to `ml_signals/indicators.py` in the future
**When** the Dummy Strategy is next started (or reloaded per its config)
**Then** the new indicator becomes available to it without a rewrite of the strategy's data-plumbing code (FR10 consequence, restated for FR14)

**Given** the strategy runs continuously
**When** it operates unattended
**Then** it satisfies NFR2 — capable of running in paper mode against live data for at least one week without manual intervention (handles reconnects/errors without crashing)

**Given** the strategy touches `Price`/`Quantity` values from live data
**When** it processes them
**Then** it follows NFR5 (AD-5) precision rules — no `Price(decimal, precision)` re-stamping, no `float` round-tripping

## Epic 4: Bot Monitoring TUI

Builder SSHes into the box, opens a keyboard-only urwid terminal UI, and at a glance sees per-bot PnL/health and which coins are hot right now — drills into a coin's live indicators/book or a bot's trade history with a keypress, hands off to the web dashboard for deeper graphs, and starts/stops a bot without leaving the terminal. Standalone: reads Epic 1's ranking engine, Epic 2's shared indicators, and Epic 3's `live_paper` as inputs, but delivers complete monitoring/control value on its own. Backed by a finalized UX design contract (`_bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/DESIGN.md` + `EXPERIENCE.md`, both `status: final`) — the project's first UX-driven surface.

### Story 4.1: Scaffold the TUI shell and live Coins pane

As the builder,
I want to launch a keyboard-only urwid terminal UI that opens directly to a live-mirrored Coins pane,
So that I have a working, navigable foothold to build every other view on top of.

**Acceptance Criteria:**

**Given** the `bot_tui` module (new, per architecture structural seed)
**When** it starts
**Then** it runs its own asyncio event loop alongside `redis.asyncio` pub/sub subscriptions, launched interactively (e.g. `docker compose exec` or on-host) — never as a `restart: always` daemon (FR17, UX-DR2)

**Given** the running TUI
**When** it opens
**Then** it defaults to the Coins pane, showing a breadcrumb header (`Coins`), a footer hint bar with available keybindings, and the ranked coin list read live from `rankings:live` (Story 1.8) — with no separate data pipeline and no local recomputation of rank (FR17, FR19, UX-DR2)

**Given** the `:` command bar
**When** the builder types `:` then `coins` or `bots` and presses Enter
**Then** it jumps to the named pane; an unrecognized command echoes `unknown command: {input}` and stays open for correction rather than silently no-op'ing (FR21, UX-DR2)

**Given** any non-default view
**When** the builder presses `esc`
**Then** it pops back exactly one level and never exits the program; quitting is only reachable via `:q` (FR21, UX-DR2)

**Given** the Coins pane's live feed
**When** no `rankings:live` message has arrived yet
**Then** it renders a `waiting for rankings:live…` state in the neutral/quiet color, not a blank screen or a skeleton row (EXPERIENCE.md State Patterns: Cold open)

### Story 4.2: Coins pane interaction and attention-only visual polish

As the builder,
I want the Coins pane to be filterable, mode-switchable, and visually quiet except when something needs my attention,
So that I can scan and narrow the ranked list quickly without the display fighting for my eye's attention on healthy state.

**Acceptance Criteria:**

**Given** the Coins pane
**When** the builder presses `/`
**Then** an inline fuzzy-filter opens, narrowing visible rows by substring match against instrument ID as the builder types; `esc` clears the filter without leaving the pane; a filter with no matches renders `no matches` in place of rows rather than hiding the pane (FR21, UX-DR3)

**Given** the Coins pane
**When** the builder presses `m`
**Then** the active Ranking Mode toggles between volume and volatility by publishing to `ranking:control` (Story 1.8) — reflected immediately once `ranking_engine` confirms the switch on `rankings:live` (FR19, UX-DR3)

**Given** the Coins pane's rows
**When** they render
**Then** rank order itself carries zero color — only a per-pane stale badge (tied to the `ranking_engine` heartbeat) uses `{colors.attention-stale}`; every other row renders in the terminal's own inherited default foreground/background (FR22, UX-DR1, UX-DR7)

**Given** the TUI's color and typography implementation generally
**When** any state is rendered
**Then** the base is the terminal's own default fg/bg (no fixed hex palette), monospace is the only typeface used, and size is never used for emphasis anywhere — emphasis is color-first, with bold/standout only ever as a secondary reinforcing channel (UX-DR1, FR22)

**Given** `rankings:live` goes stale (no heartbeat within the configured timeout)
**When** this is detected
**Then** the Coins pane shows its own pane-level stale badge while continuing to render the last-known ranking — never silently frozen and never dropped (UX-DR7, architecture AD-9)

### Story 4.3: Coin-detail drill-down with collapsible order-book depth

As the builder,
I want to drill into a coin's live indicators and order book, with the book collapsed by default and expandable on demand,
So that I can check a coin's health at a glance without a wall of price levels, and go deep only when I actually need to.

**Acceptance Criteria:**

**Given** a highlighted row in the Coins pane
**When** the builder presses `Enter`
**Then** Coin-detail opens as a full-screen replace (not a split-pane, not a modal), showing live indicators (Microprice, spread, OFI, OBI) computed via the shared `ml_signals.indicators` code path — never reimplemented locally (FR20, UX-DR5)

**Given** Coin-detail's order-book region
**When** the view is entered
**Then** it opens collapsed to a single top-of-book row (best bid/ask) every time — it never remembers an expanded state from a prior visit (FR20, UX-DR5)

**Given** the collapsed order-book region
**When** the builder presses `d`
**Then** it expands to the full depth ladder (up to 20 levels/side, bid left / ask right); `d` again re-collapses it — this toggle is scoped entirely to the ladder region and does not affect the breadcrumb, indicators, or what `esc` does (FR20, UX-DR5)

**Given** an expanded ladder on a thin book (fewer than 20 levels on one or both sides)
**When** it renders
**Then** the ladder simply ends short — no padding rows, no placeholder glyphs, no error styling; a side with zero levels renders `no bids`/`no asks` (FR20, UX-DR5)

**Given** Coin-detail is open
**When** the builder presses `o`
**Then** the web dashboard opens in the browser to this exact coin's graph view at the same time-window/zoom context — not a generic landing page (FR25)

**Given** Coin-detail
**When** the builder presses `esc`
**Then** it returns to the Coins pane, preserving scroll position and any active filter, regardless of whether the order-book ladder was collapsed or expanded at the time (FR20)

**Given** Coin-detail's indicators and order-book ladder
**When** either is displayed, at any expansion state
**Then** no time-scrubbing or historical replay control exists anywhere in this view — both always show current/latest market-data state only; historical/graph analysis for market data stays the web dashboard's job via the `o` deep-link above (FR24)

### Story 4.4: Bots pane with start/stop control

As the operator,
I want a live list of running bots with independent per-bot health, and the ability to start/stop one directly,
So that I can see which bots need attention and act on them without leaving the terminal.

**Acceptance Criteria:**

**Given** the Bots pane (`:bots`)
**When** it renders
**Then** it shows one row per bot from `bots:status`: bot_id, sign-colored PnL, strategy/symbol, mode (paper/live), position/exposure, uptime/last-heartbeat, win-rate-to-date (FR18, UX-DR4)

**Given** a specific bot's `bots:status` heartbeat
**When** it is missed within the configured timeout
**Then** that bot's row alone shows a stale badge — a healthy bot next to a crashed one shows exactly one stale row, never a pane-wide flag (FR18, UX-DR7)

**Given** a bot row (in the Bots pane or Bot-detail)
**When** the builder presses `s`
**Then** it publishes `{bot_id, action: "start"|"stop"}` on `bots:control` — never a mode/paper-live parameter — and the row shows a one-line footer echo (`sent: start bot-07`) confirming the command was sent, not that it succeeded; there is no optimistic local state change (FR23, UX-DR4, architecture AD-10)

**Given** the start/stop control's config-gate
**When** the operator attempts to change whether a bot runs paper or real-money
**Then** it is unreachable from this control — that gate lives solely inside `live_paper`'s own separate config (FR15, FR23)

### Story 4.5: Bot-detail live snapshot view

As the operator,
I want to drill into a bot and see its current PnL, position, mode, and health at a glance,
So that I can assess a bot's live state in one keypress before deciding whether to investigate further.

**Acceptance Criteria:**

**Given** a highlighted row in the Bots pane
**When** the builder presses `Enter`
**Then** Bot-detail opens full-screen, showing a bordered live-snapshot-header region with PnL, position/exposure, mode, uptime/last-heartbeat, win-rate-to-date, and strategy/symbol — all sourced live from `bots:status` (FR18, UX-DR6)

**Given** Bot-detail is open
**When** the builder presses `esc`
**Then** it returns to the Bots pane (FR21)

**Given** Bot-detail
**When** the builder presses `s`
**Then** start/stop acts on the open bot without leaving the view, identically to Story 4.4's control (FR23)

**Given** this story ships before Story 4.7
**When** Bot-detail is viewed
**Then** it shows only the live-snapshot-header region — the trades blotter and PnL-over-time regions are added in Story 4.7 and are not required for this story's completion

### Story 4.6: Durable bot trade/position history via Nautilus Cache

Added 2026-07-24 during the UX design pass (surfaces FR27, not yet folded into the PRD proper — flagged for PM follow-up). Confirmed via codebase check: this is not net-new infrastructure — Nautilus's `Cache` already exposes `orders_closed()`/`positions_closed()`/`position_snapshots()` and already supports a durable Redis-backed `database` config; the gap is that `live_paper` doesn't turn it on yet.

As the builder,
I want `live_paper`'s trade and position history to survive a restart and be queryable by something other than the running process itself,
So that both the web dashboard and the TUI can show real trade history without either one reimplementing it or reaching into Nautilus's internals directly.

**Acceptance Criteria:**

**Given** `troll/live_paper/node.py`'s `TradingNodeConfig`
**When** this story is implemented
**Then** it constructs `CacheConfig(database=DatabaseConfig(type="redis", ...))` pointed at the existing Redis instance, so orders/positions/fills persist beyond the process's lifetime instead of defaulting to in-memory-only (FR27)

**Given** `live_paper` is the sole writer of this Cache-backed history
**When** any other module needs trade/PnL history
**Then** it never reaches into the Redis-backed Cache's internal keys/msgpack encoding directly — `live_paper` exposes its own read surface over `cache.orders_closed()`/`cache.positions_closed()`/`cache.position_snapshots()`, the same boundary discipline as the existing `bots:status`/`bots:control` channels (FR27, architecture AD-10)

**Given** the new read surface
**When** it is queried
**Then** it returns individual fills (timestamp, side, price, qty, realized PnL) and can compute/return PnL aggregated by day for a given bot

**Given** the read surface is unreachable (e.g. persistence not yet enabled elsewhere, or a query times out)
**When** a caller queries it
**Then** it fails in a way callers can detect and handle gracefully (e.g. a clear error/timeout), not a silent empty result indistinguishable from "no trades yet"

### Story 4.7: Bot-detail trades blotter and PnL-over-time chart

Depends on Story 4.6's read surface and Story 4.5's Bot-detail view.

As the operator,
I want to see a bot's individual fills and its PnL trend over time from inside Bot-detail,
So that I can diagnose a bad day without leaving the terminal, using the same numbers the web dashboard would show.

**Acceptance Criteria:**

**Given** Bot-detail is open
**When** it renders
**Then** it shows three bordered regions stacked top-to-bottom: (1) the live snapshot header from Story 4.5, (2) a scrollable trades blotter (timestamp, side, price, qty, realized PnL per fill), (3) a PnL-over-time sparkline — both regions 2 and 3 sourced from Story 4.6's read surface (FR27, UX-DR6)

**Given** the PnL-over-time region
**When** the builder presses `t`
**Then** the time-range preset cycles day → week → month → all → day…, never free-form date scrubbing, and the sparkline redraws for the new window (FR27, UX-DR6)

**Given** Bot-detail
**When** the builder presses `o`
**Then** the web dashboard opens to this bot's fuller trades/PnL view for the same bot — both surfaces reading the same underlying Cache history via Story 4.6's read surface, so the numbers always match (FR27, extends FR25's pattern to bots)

**Given** Story 4.6's read surface is unreachable
**When** Bot-detail renders
**Then** regions 2 and 3 each independently render `history unavailable` in the neutral/quiet color, while region 1 (live snapshot) is unaffected since it has no dependency on the history read path (UX-DR6)

## Epic 8: TradingView-Style Multi-Chart Navigation & Indicator Overlays

Builder opens a coin's chart on the web dashboard and it behaves like a professional charting site. Since this epic was first drafted, the drag-to-pan candlestick widget (Story 7.1) was refactored into a shared JS module (`_LIVE_CHART_JS`, dashboard.py) embedded on **both** `/coin/{id}` and `/chart/{id}` — but the two pages have diverged: `/coin/{id}`'s "Lines" mode is a separate, bespoke implementation (bid/ask/mid/microprice/price multi-line, plus click-to-diff A/B markers) that never joined the shared pan-to-load-more/pagination machinery, while `/chart/{id}`'s "Lines" mode (via the shared widget) is a single close-price line derived from candle data, with full pagination but none of the richer bid/ask view. Story 8.1 (revised 2026-09-06, replacing an earlier per-chart-toolbar draft that's now largely moot — both pages already have their own toolbars) consolidates the interactive chart widget onto `/chart/{id}` only, bringing its Lines mode to full parity first. Remaining stories (8.2, 8.4) build the indicator functionality on top of that consolidated widget. Stays on Plotly — no new charting library (Story 7.1 Dev Notes, reconfirmed here).

### Story 8.1: Consolidate the interactive chart widget onto `/chart/{id}` only

As a user of the web dashboard,
I want the full Candles/Lines/Ticks charting experience (including the rich bid/ask/mid/microprice/price Lines view and click-to-diff) to live in exactly one place, `/chart/{id}`,
So that there's one charting surface to learn and extend, instead of two divergent, partially-overlapping implementations on `/coin/{id}` and `/chart/{id}`.

**Acceptance Criteria:**

**Given** `/chart/{id}`'s "Lines" mode (`_renderLineChart`, currently a single `scattergl` trace of candle close prices, dashboard.py:358-368)
**When** this story is implemented
**Then** it is replaced with the same multi-trace rendering `/coin/{id}`'s `renderCoin` currently does for its own Lines branch (dashboard.py:721-749): `bid`/`ask`/`mid`/`microprice`/`price` lines with the same colors/styling, plotted against a rows array shaped `{t,bid,ask,mid,micro,price}` (not the candle `{t,o,h,l,c}` shape) — the shared widget's mode dispatch (`_renderChartRows`, dashboard.py:251-256) routes `'lines'` to this new renderer

**Given** Lines mode needs its own data source (bid/ask/mid/micro/price are order-book-state values, not derivable from the trade-tick-based candle pipeline)
**When** this story adds it
**Then** two new functions mirror the existing live/historical split already used by Candles (`_live_candles_json`/`_historical_candles_json`) and Ticks: `_live_lines_json(iid)` (extracted from `_coin_chart_json`'s existing bid/ask/mid/micro/price computation over `_second_rolling`, dashboard.py — reused, not duplicated) for the current/live window, and a new `_historical_lines_json(iid, start_ms, end_ms)` reading `DydxSecondSnapshot` records from the catalog for the requested window (same catalog-query pattern as `_historical_ticks_json`, dashboard.py:971 area) for paginated/older windows — both returning `{"rows": [...], "truncated": bool}`, matching the existing candles/ticks endpoint contract exactly

**Given** the new endpoints (`GET /data/coin/{id}/lines?start=&end=` mirroring `coin_candles_handler`'s pattern) and the shared JS's fetch dispatch (`_fetchModeWindow`, `_fetchLiveWindow`, dashboard.py:221-232)
**When** mode is `'lines'`
**Then** `_fetchModeWindow`/`_fetchLiveWindow` route to the new lines endpoints instead of falling through to the candles fetch as they do today, so Lines mode gets its own live+historical data and joins `_chartState`/`_loadOlderChunk`'s pan-to-load-more pagination exactly like Candles/Ticks already do (dragging left in Lines mode now loads older bid/ask/mid/micro/price history from the catalog, which it cannot do today)

**Given** click-to-diff (`handleChartClick`/`buildDiff`/the A/B markers, currently wired only on `/coin/{id}`'s `live-chart` in `renderCoin`, dashboard.py:475-... and 730-737)
**When** this story is implemented
**Then** it moves to `/chart/{id}`'s Lines mode: `handleChartClick` reads from `_chartState.rows[idx]` (the new `{t,bid,ask,mid,micro,price}` shape) instead of the old `_chartData` global tied to `/coin/{id}`'s live poll, `_renderLineChart` wires `plotly_click`→`handleChartClick` (mirroring `_wireChartRelayout`'s existing wiring pattern), and `_render_chart_page` gains a `diff-box` div; the feature is unavailable in Candles/Ticks mode (as it always has been — diff was always price-line-specific) and unavailable on `/coin/{id}` after this story (its only home is now `/chart/{id}`)

**Given** `/coin/{id}`'s page (`showCoin`/`renderCoin`, dashboard.py:646-762)
**When** this story is implemented
**Then** the `live-chart` div, its toolbar (Lines/Candles/Ticks buttons, `bar-sel`, date-range inputs, Live button), and all Candles/Ticks/Lines rendering logic in `renderCoin` are removed — `/coin/{id}` keeps only `ind-groups` (indicator table), `price-ticker`, `sig-chart` (OFI10z/OBI10), and the `/chart/{id}` link; `pollCoin`/`renderCoin` still poll `/data/live/{id}` and `/data/coin/{id}` for the indicator table, ticker, and sig-chart (all unaffected by this story) but no longer touch any chart-mode/pagination state

**Given** `_coin_chart_json`'s existing `sig_ts`/`ofi_10_z`/`obi_10` fields (consumed by `/coin/{id}`'s `sig-chart`, unrelated to the price-line data this story extracts out of the same function)
**When** `_coin_chart_json` is refactored to share its bid/ask/mid/micro/price computation with the new `_live_lines_json`
**Then** `sig_ts`/`ofi_10_z`/`obi_10` continue to be returned unchanged from `_coin_chart_json` — this story does not touch sig-chart's data path

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py -q`
**When** the test suite runs after this story
**Then** it passes, with new tests for `_historical_lines_json` (catalog-backed, mirrors `test_historical_candles_json_builds_from_catalog_trades`'s temp-catalog pattern, writing `DydxSecondSnapshot` records instead of `TradeTick`s) and for the extracted `_live_lines_json`/`_coin_chart_json` shared computation (asserting both still return correct bid/ask/mid/micro/price values from a known `_second_rolling` buffer, no behavior change vs. today's `_coin_chart_json` output)

### Story 8.2: Technical indicator computation via `nautilus_trader.indicators`

Depends on nothing (backend-only, additive). Adds the indicator dispatch mechanism this epic's UI stories (8.3) consume. No new dependency — every indicator class already ships in the pinned `nautilus_trader` (confirmed: `SimpleMovingAverage`, `ExponentialMovingAverage`, `WeightedMovingAverage`, `HullMovingAverage`, `AdaptiveMovingAverage`, `DoubleExponentialMovingAverage`, `VariableIndexDynamicAverage`, `WilderMovingAverage`, `BollingerBands`, `KeltnerChannel`, `DonchianChannel`, `RelativeStrengthIndex`, `MovingAverageConvergenceDivergence`, `Stochastics`, `CommodityChannelIndex`, `AverageTrueRange`, `VolatilityRatio`, `AroonOscillator`, `DirectionalMovement`, `RateOfChange`, `ChandeMomentumOscillator`, `OnBalanceVolume`, `VolumeWeightedAveragePrice`, plus the rest of `nautilus_trader.indicators`'s ~45 concrete classes — all confirmed importable in this repo's pinned version, each exposing `update_raw(...)` for numeric feeding alongside `handle_bar`/`.value` or multi-attribute output).

As a strategy developer/builder,
I want every indicator in `nautilus_trader.indicators` computed once from the existing candle data using the real indicator classes, and selectable for the chart,
So that the chart offers the full built-in indicator toolkit with zero reimplementation and zero new dependency.

**Acceptance Criteria:**

**Given** a new `ml_signals/chart_indicators.py` module (dispatch/metadata only — no indicator math of its own, per DESIGN-02 module boundaries)
**When** this story is implemented
**Then** it defines `INDICATOR_CATALOG: dict[str, IndicatorSpec]` covering every concrete class in `nautilus_trader.indicators` (excluding `Indicator`/`MovingAverage`/`MovingAverageFactory`/`MovingAverageType`/module-name entries, which are base/factory/enum, not indicators), each entry recording: the class, its constructor parameter names/types/defaults, which OHLCV field(s) and in what order feed its `update_raw` (e.g. SMA/EMA/RSI/MACD/OBV: close only; ATR/Stochastics/CCI/AroonOscillator: high, low, close; VWAP: high, low, close, volume — determined per-indicator by inspecting its `update_raw` signature/docstring, not assumed uniform), which attribute(s) hold its output (`value` for single-line indicators; `upper`/`middle`/`lower` for band indicators; `value_k`/`value_d` for Stochastics; etc.), and a panel classification (`"overlay"` for price-scale indicators — moving averages, bands, VWAP — vs `"oscillator"` for bounded/differently-scaled indicators — RSI, MACD, Stochastics, CCI, AROON, etc.) (FR30)

**Given** a new `replay_indicator(candles: list[dict], name: str, params: dict) -> dict[str, list[float | None]]` function in `chart_indicators.py`
**When** it is called with a candle list (each with `o`/`h`/`l`/`c`/`v`/`t`) and a registered indicator name + params
**Then** it instantiates the real `nautilus_trader.indicators` class from the catalog with the given params, replays it by calling `update_raw` once per candle in chronological order with that indicator's registered OHLCV feed, and returns each configured output attribute as a list of values aligned 1:1 with the input candles — `None` for every candle before `indicator.initialized` becomes true (the indicator's own warm-up state, not a hand-computed guess at warm-up length)

**Given** a new `GET /data/coin/{id}/indicators?bar=&start=&end=&spec=SimpleMovingAverage:period=20,RelativeStrengthIndex:period=14` endpoint in `dashboard.py` (mirrors `coin_candles_handler`'s pattern, dashboard.py:1014)
**When** it is called
**Then** it fetches candles for the given window/bar via the existing candle-building path (reused, not duplicated), calls `replay_indicator` once per requested spec entry, and returns a JSON object keyed by a stable id (e.g. `"SimpleMovingAverage_period=20"`) each mapping to `{t, value}`-shaped points per output attribute; a name not present in `INDICATOR_CATALOG` returns a 400 with a clear error message rather than silently ignoring it

**Given** a new `GET /data/indicators/catalog` endpoint
**When** it is called
**Then** it returns `INDICATOR_CATALOG` serialized to JSON (name, params with defaults, panel classification) for every registered indicator — the single source Story 8.4's picker UI builds its list from, so the picker never hardcodes a name list that could drift from what the backend actually supports

**Given** the new dispatch mechanism (TEST-01: financial calculations require tests)
**When** it is tested
**Then** `ml_signals/tests/test_chart_indicators.py` (new file, one file per module under test) asserts `replay_indicator` against a known small candle series for at least one indicator from each output-shape category confirmed in this story — single-value (e.g. `SimpleMovingAverage`, hand-computable expected values), banded (e.g. `BollingerBands`, asserting all three of `upper`/`middle`/`lower`), and dual-line (e.g. `Stochastics`, asserting both `value_k`/`value_d`) — plus a warm-up test confirming `None`-padding matches the indicator's own `initialized` transition; exhaustive per-indicator tests for all ~45 catalog entries are not required (the mechanism is generic and class-driven, not per-indicator bespoke code), but every catalog entry must be confirmed importable and instantiable with its default params in a single smoke-test loop over `INDICATOR_CATALOG`

### Story 8.4: Indicator picker on the chart page's settings toolbar

Depends on Story 8.1 (the consolidated widget on `/chart/{id}`, whose existing toolbar this adds controls to) and Story 8.2 (indicator endpoint to call).

As a user of the chart page,
I want to toggle technical indicators on/off from `/chart/{id}`'s own settings toolbar and see them appear on the chart immediately,
So that I can inspect a coin with the same indicator toolkit a professional charting site offers, without editing config or reloading the page.

**Acceptance Criteria:**

**Given** `/chart/{id}`'s toolbar (consolidated onto this page by Story 8.1)
**When** this story is implemented
**Then** it gains an indicator picker (a multi-select control, e.g. a searchable dropdown-with-checkboxes — a plain flat list of ~45 toggle chips would be unusable) populated entirely from Story 8.2's `/data/indicators/catalog` endpoint, never a hardcoded name list in JS — every indicator the backend registers is selectable, and each selected indicator's own registered parameters (e.g. SMA's `period`) render as inline editable fields defaulting to the catalog's default values (FR30)

**Given** the user selects an indicator whose catalog entry is classified `"overlay"` (moving averages, Bollinger/Keltner/Donchian bands, VWAP, etc.)
**When** it renders
**Then** it is fetched via Story 8.2's `/data/coin/{id}/indicators` endpoint for the currently-loaded chart window and added as additional trace(s) directly on the `live-chart` candlestick chart (one trace per output attribute — e.g. Bollinger Bands adds three traces for `upper`/`middle`/`lower`), sharing the pan/zoom-triggered pagination refresh from Story 7.1/8.1 — an indicator trace is refetched/extended whenever `_loadOlderChunk` loads more candle history, never left stale/truncated relative to the candlestick trace

**Given** the user selects an indicator whose catalog entry is classified `"oscillator"` (RSI, Stochastics, MACD, CCI, AROON, etc.)
**When** it renders
**Then** it appears in a new dedicated indicator panel on `/chart/{id}` (a new Plotly chart div, own y-scale, positioned directly below `live-chart` and above the page's existing static 7-row microstructure subplot figure) rather than overlaid on the candlestick chart's y-axis; every active oscillator-type indicator shares this one panel (stacked traces, one per output attribute), not one panel per indicator — this is a new panel introduced by this story, not a reuse of `/coin/{id}`'s `sig-chart` (a different page, out of scope after Story 8.1)

**Given** the new indicator panel and `live-chart`, both on `/chart/{id}`
**When** the user drags/zooms either one
**Then** the resulting x-axis range is applied to the other via a small shared helper (e.g. `_syncChartXRange(sourceDivId, rng)`, guarded against re-triggering its own `plotly_relayout` listener) so both panels always show the same visible time window — the candlestick chart is the "mother" panel every spawned oscillator panel follows (FR29)

**Given** an active indicator selection
**When** the user changes chart mode (Lines/Candles/Ticks), changes the bar-size (`onBarChange`), or hits Live/Load
**Then** all active indicators are refetched for the new window/mode rather than left showing stale data from the previous mode (mirrors the existing `_chartState=null` + refetch pattern already used by `onBarChange`/`resetCoinLive`); an indicator that requires Candles-derived data (all of them, since they're computed from OHLC candles) is disabled (not silently blank) while in Ticks or Lines mode, with a one-line note why

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py -q`
**When** it runs after this story
**Then** it passes, with a new test asserting the indicator-fetch JS helper builds the correct `spec=` query string for a given active-indicator selection (JS verified via the same `node --check` + stubbed-harness method used in Story 7.1, since no browser is available in this environment — flag for manual in-browser confirmation before considering this story fully verified, same caveat 7.1 documented)

## Epic 10: Custom Chart Indicators & Persisted Configuration

Story 8.2's `chart_indicators.py` is explicitly scoped as "dispatch/metadata over `nautilus_trader.indicators` — no indicator math of its own" (`ml_signals/chart_indicators.py:16`) — every one of its ~30 catalog entries is fed purely from OHLCV candle fields (`o`/`h`/`l`/`c`/`v`). CVD, Cancel Pressure, and OFI cannot live there: none is a `nautilus_trader.indicators` class, and none is computable from OHLCV alone — all three need order-book-level or trade-level data (buy/sell volume split, book deltas) that a plain candle doesn't carry. All three already exist and already work, today, as fixed rows in `/chart/{id}`'s static 7-row `make_subplots` microstructure figure (`_render_chart_page`, `dashboard.py:1067-1076`: row 1 "OFI", row 5 "Cancel pressure", row 6 "5-min cumulative delta"), computed by `ml_signals/chart_data.py`'s per-event replay of raw catalog data (`OrderBookDelta`/`TradeTick`) driving `ml_signals/indicators.py`'s `OrderFlowImbalance` and `ml_signals/book_features.py`'s `CancellationTracker`. This epic does not reimplement any of the three — it re-exposes each one's existing computation through a new, second indicator catalog (alongside Story 8.2's native one), then retires the corresponding fixed row so the same signal is never shown in two places on the same page at once. Closes with persisting a coin's active indicator selection to a plain, source-control-committable file (mirroring `dydx_collector/config.py`'s `tomllib`/`tomli_w` load/save pattern, `tomli_w` already a pinned dependency per `troll-requirements.txt:9`), so a chart's configuration survives a page reload or a container redeploy instead of resetting to empty every time.

**One open question this epic's stories flag but do not silently resolve on their own:** CVD already has *two* independent existing implementations — `ranking_engine/engine.py:330`'s snapshot-based `cvd` (published to `rankings:live`, the ranking table/bot_tui's SSOT-02-owned value) and `chart_data.py:68-86`'s trade-tick-based `cum_delta` (chart-page-only, a 300-second rolling sum, not a true running-cumulative total). Story 10.2 does not silently pick one — it reuses `ml_signals/indicators.py`'s `trade_aggregates()`/`volume_delta()` SSOT-01 stateless helpers as the shared math primitive so a *third* independent implementation is not created, and calls out the ranking-table/chart-page distinction explicitly in its own AC. (Cancel Pressure's fixed row is also removed once its picker equivalent lands, same one-signal-one-place principle — confirmed by the user 2026-09-08, no longer an open question.)

### Story 10.1: Custom-indicator catalog, category-tagged picker, and a histogram panel type

Foundational — Stories 10.2/10.3/10.4 each register one indicator into the catalog this story creates; none of them can start before this one lands. Depends on Story 8.2 (the native catalog/endpoint contract this story runs alongside) and Story 8.4 (the picker UI this story extends).

As a user of the chart page's indicator picker,
I want native `nautilus_trader` indicators and dYdX-specific custom indicators presented as two clearly labeled groups, with a histogram rendering option for indicators that aren't a line,
So that I can tell at a glance which indicators come from the standard library versus this project's own signal work, and so a signal like Cancel Pressure (which isn't naturally a line) renders in a way that actually reads as its own metric.

**Acceptance Criteria:**

**Given** a new `ml_signals/custom_indicators.py` module (mirrors `chart_indicators.py`'s shape — `IndicatorSpec`-equivalent dataclass, a catalog dict, `catalog_json()`, a replay function — but is explicitly for indicators that are not `nautilus_trader.indicators` classes and are not fed from OHLCV candle fields alone)
**When** this story is implemented
**Then** it defines its own spec dataclass (e.g. `CustomIndicatorSpec`) carrying: a name, JSON-safe default params, a panel classification, and a replay callable whose signature accepts whatever raw window data it needs (candles plus one or more of: the window's `DydxSecondSnapshot` rows, `TradeTick` rows, or `OrderBookDelta` rows — not just the candle list `chart_indicators.replay_indicator` receives) and returns `dict[str, list[float | None]]` aligned 1:1 with the candle list, exactly matching Story 8.2's existing output contract so nothing downstream of the response needs to know which catalog an indicator came from

**Given** the `Panel` type currently defined as `Literal["overlay", "oscillator"]` (`chart_indicators.py:37`)
**When** this story is implemented
**Then** a third value, `"histogram"`, is added (in whichever module now owns the shared `Panel` type — extracting it to a small shared location both catalogs import, rather than each catalog defining its own copy) — an indicator classified `"histogram"` renders as Plotly `type:'bar'` traces instead of `type:'scattergl'` lines, sharing the oscillator panel's existing per-instance-axis-scaling machinery from Story 9.1 (`_oscillatorOverlayAxis`, `dashboard.py:583-585`) rather than a fourth new panel — a histogram trace still needs its own scale when it coexists with line-based oscillators, for the exact reason Story 9.1 gave every oscillator instance its own axis

**Given** `GET /data/indicators/catalog` (`indicators_catalog_handler`, `dashboard.py:1681-1682`) currently returns only `chart_indicators.catalog_json()`'s output
**When** this story is implemented
**Then** the endpoint merges both catalogs into one JSON object, and every entry (native and custom alike) gains a `category` field (`"native"` or `"custom"`) alongside its existing `params`/`panel` fields — this is the single source Story 10.1's picker UI reads to group entries, so the JS never hardcodes which names are custom vs native

**Given** `_indicators_json`/`coin_indicators_handler` (`dashboard.py:1630-1656`, `1659-1678`), which today only ever calls `chart_indicators.replay_indicator`
**When** a requested spec entry's name is registered in the custom catalog instead of the native one
**Then** the handler dispatches it to the custom module's replay function instead, fetching whatever extra raw window data that indicator's spec declares it needs (second-snapshots/trade-ticks/book-deltas) via the same catalog-query patterns already used elsewhere in this file (`dashboard.py:1338-1348` for `DydxSecondSnapshot`, `dashboard.py:1218`/`chart_data.py:64` for `TradeTick`, `chart_data.py:62` for `OrderBookDelta`) — a name present in neither catalog still returns the existing 400 (`dashboard.py`'s "Unknown indicator" path, unchanged)

**Given** `_renderIndicatorPicker`'s add-list (`dashboard.py:405-414`), currently one flat alphabetical list
**When** this story is implemented
**Then** the list renders as two labeled groups, "Nautilus Indicators" and "Custom Indicators", populated by filtering the merged catalog response on its new `category` field — the existing search box (`#ind-picker-search`) filters across both groups, not just one

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py ml_signals/tests/test_chart_indicators.py -q`
**When** it runs after this story
**Then** it passes, plus a new `ml_signals/tests/test_custom_indicators.py` (TEST-01: this module does financial-calculation dispatch) asserting the merged catalog response carries the correct `category` tag for at least one native and one placeholder custom entry, and a JS-harness test (same Node-stub pattern as Story 8.4/9.1's tests in `test_dashboard_chart_pan_js.py`) confirming a `"histogram"`-classified indicator's trace has `type:'bar'`, not `'scattergl'`

### Story 10.2: CVD as a custom indicator, retiring the fixed "5-min cumulative delta" row

Depends on Story 10.1 (the custom catalog this registers into).

As a user of the chart page,
I want Cumulative Volume Delta available from the indicator picker instead of a permanently-shown row,
So that I only see CVD when I actually want it, alongside whatever else I've picked, at whatever timeframe I'm viewing.

**Acceptance Criteria:**

**Given** `ml_signals/chart_data.py:40,68-86`'s existing `cum_delta` computation (a 300-second rolling sum of signed trade size from replayed `TradeTick`s) and `ml_signals/indicators.py`'s SSOT-01 stateless helpers `trade_aggregates()`/`volume_delta()` (`indicators.py:454-467`)
**When** Story 10.1's custom catalog registers a `"CumulativeVolumeDelta"` entry
**Then** its replay function computes a true per-candle running-cumulative series — bucket the window's buy/sell volume split into the candle time grid (same bucketing pattern `ml_signals/candles.py:47-50` already uses to build OHLCV candles from raw ticks, applied here to the signed-volume split instead), then accumulate that bucketed per-candle delta into a running total starting from 0 at the window's first candle — reusing `trade_aggregates()`/`volume_delta()` for the per-bucket buy/sell split rather than hand-rolling a third independent aggressor-side-sign implementation alongside `chart_data.py`'s and `ranking_engine/engine.py:330`'s existing two

**Given** the source data for the bucketed split
**When** implementing the replay function
**Then** it reuses `DydxSecondSnapshot.buy_volume`/`sell_volume` (already read for the window via the catalog-query pattern at `dashboard.py:1338-1348`, or the in-process `_second_rolling` buffer for the live window, `dashboard.py:105`) rather than re-replaying raw `TradeTick`s from scratch — this is a deliberate divergence from `chart_data.py`'s existing tick-based `cum_delta`, and the story's dev notes must say so explicitly rather than silently changing CVD's data source without comment

**Given** `_render_chart_page`'s 7-row figure and `chart_data.py`'s row-6 "5-min cumulative delta" computation
**When** this story is implemented
**Then** the fixed row and its `cum_delta` computation are removed entirely (not left dead/unreferenced) and the remaining rows renumber to fill 1-6, so CVD is shown exactly once on the page — via the picker, never both

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py ml_signals/tests/test_custom_indicators.py -q`
**When** it runs after this story
**Then** it passes, with a new test asserting the CVD replay function's per-candle running total against a hand-constructed small snapshot window (TEST-01: financial calculation), and `test_dashboard_chart.py`'s existing row-count-dependent assertions (if any exist for the 7-row figure) are updated for 6 rows

### Story 10.3: Cancel Pressure as a custom (histogram) indicator, retiring the fixed row

Depends on Story 10.1 (the custom catalog and the new `"histogram"` panel type this needs).

As a user of the chart page,
I want Cancel Pressure available from the indicator picker as a histogram instead of a permanently-shown line row,
So that I can add it only when relevant and read it the way a pressure/imbalance metric actually reads — as bars around zero, not a line.

**Acceptance Criteria:**

**Given** `ml_signals/book_features.py:195-263`'s existing `CancelRate`/`CancellationTracker` (a stateful rolling-window tracker of ADD/DELETE events at the best price level, `bid_pressure`/`ask_pressure` each in [-1, +1]) and `chart_data.py:34,100,113-117,150-152`'s existing per-event replay driving it for the fixed row-5 chart
**When** Story 10.1's custom catalog registers a `"CancelPressure"` entry classified `"histogram"`
**Then** its replay function reuses `CancellationTracker` unchanged (no new cancellation math, per DESIGN-02 — this story only relocates where the tracker's output is sampled and rendered), replaying the window's `OrderBookDelta`s the same way `chart_data.py` already does, and samples `bid_pressure`/`ask_pressure` once per candle-time bucket (last-value-in-bucket, matching how a live indicator reads "current state as of this bar close" rather than an average-over-bucket, unless dev investigation finds average-in-bucket reads better for this specific signal — flag whichever choice is made in Completion Notes since either is defensible and the story does not mandate one over the other)

**Given** Story 10.1's new `"histogram"` panel type
**When** `CancelPressure` is active
**Then** its `bid_pressure`/`ask_pressure` output attributes render as Plotly bar traces in the oscillator panel (positive/negative bars around a zero baseline), each on its own overlaid axis per Story 10.1's shared axis-scaling reuse — not as a fixed always-visible row

**Given** `_render_chart_page`'s figure (now 6 rows after Story 10.2) and `chart_data.py`'s row-5 "Cancel pressure" computation
**When** this story is implemented
**Then** the fixed row and its dedicated `CancellationTracker` replay call in `chart_data.py` are removed and the remaining rows renumber to fill 1-5 — confirmed by the user 2026-09-08: Cancel Pressure is picker-only, not shown in both places

**Given** `ml_signals/ofi_strategy.py:48,69-70,100,131,223,241,274-277`'s existing use of `CancellationTracker`/`max_cancel_pressure` as a backtest entry filter
**When** this story is implemented
**Then** that usage is confirmed unaffected — `ofi_strategy.py` instantiates its own `CancellationTracker` independently of the chart page's replay, and this story does not touch it

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py ml_signals/tests/test_custom_indicators.py ml_signals/tests/test_book_features.py ml_signals/tests/test_ofi_strategy.py -q`
**When** it runs after this story
**Then** it passes unchanged for the `book_features`/`ofi_strategy` suites (confirming `CancellationTracker` itself is untouched) plus a new bucketed-sampling test in `test_custom_indicators.py`, and a JS-harness test confirming the histogram panel renders bar traces for this specific indicator

### Story 10.4: OFI as a custom indicator, retiring the fixed row

Depends on Story 10.1 (the custom catalog this registers into).

As a user of the chart page,
I want Order Flow Imbalance available from the indicator picker instead of a permanently-shown row,
So that I only see it when I actually want it, consistent with how CVD and Cancel Pressure now work.

**Acceptance Criteria:**

**Given** `ml_signals/chart_data.py:98,130`'s existing top-of-book `OrderFlowImbalance` (`ml_signals/indicators.py:157-231`, fed via `update_raw(bid_price, bid_size, ask_price, ask_size)` on every replayed `OrderBookDelta`) driving the fixed row-1 chart — and confirmed distinct from `ranking_engine`'s `MultiLevelOFI`-based, full-depth, continuously-published `ofi_10`/`ofi_10_z` (`ranking_engine/engine.py:293,300,318-321`), a legitimately separate metric for a separate purpose (live ranking table vs. this per-window historical chart view)
**When** Story 10.1's custom catalog registers an `"OrderFlowImbalance"` entry
**Then** its replay function reuses the exact same `OrderFlowImbalance` class `chart_data.py` already drives (no new/third OFI variant), bucketing its per-event output into the candle time grid — dev must first re-confirm against `chart_data.py:98,130`'s exact accumulation semantics (whether `.value` is a running total across the whole replay or resets per sample) before choosing sum-per-bucket vs. last-value-per-bucket, rather than assuming one

**Given** `_render_chart_page`'s figure (now 5 rows after Stories 10.2/10.3) and `chart_data.py`'s row-1 "OFI" computation
**When** this story is implemented
**Then** the fixed row and its dedicated `OrderFlowImbalance` replay call in `chart_data.py` are removed and the remaining rows renumber to fill 1-4 (Book imbalance L1 agg, Mid-layer imbalance, Depth, Spread) — this epic does not touch these four remaining rows; they stay fixed, out of scope

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py ml_signals/tests/test_custom_indicators.py -q`
**When** it runs after this story
**Then** it passes, with a new bucketed-OFI test in `test_custom_indicators.py` asserting the chosen accumulation semantics against a hand-constructed small book-delta sequence

### Story 10.5: Persisted per-instrument chart indicator configuration

Depends on Stories 10.1-10.4 (needs the final `_activeIndicators` shape, spanning both catalogs, to persist). Format is TOML by default, matching `dydx_collector/config.py`'s established `tomllib`/`tomli_w` pattern (`tomli_w` already pinned, `troll-requirements.txt:9`) — but the concrete requirement is only that a coin's active indicator selection survives a reload/redeploy via a plain, source-control-committable file; if implementation finds a different plain-file format fits better, that substitution is acceptable as long as it's still a committed file, not browser-only state.

As a user of the chart page,
I want the indicators I've added to a coin's chart to still be there next time I open it — and to be able to check that configuration into git,
So that a chart's setup isn't lost on every page reload or container redeploy, and a useful indicator combination can be shared/reviewed like any other config change.

**Acceptance Criteria:**

**Given** a new `ml_signals/chart_indicator_config.py` module (mirrors `dydx_collector/config.py`'s `load_config()`/`save_config()` shape, `config.py:64-99,111-134`: `tomllib.load()` to read, `tomli_w.dump()` to write, full-rewrite-not-patch, same accepted tradeoff `config.py:114-118` already documents for hand-added comments)
**When** this story is implemented
**Then** it defines a schema keyed by `instrument_id`, each entry a list of `{name, params, category}` objects (one per active indicator instance — `id` is not persisted, since it is only ever a client-side sequence counter for the multi-instance UI, regenerated fresh on load), and reads/writes a new file, `troll/ml_signals/chart_indicators.toml`, confirmed not excluded by any `.gitignore` (root `.gitignore`'s `troll/` entries only exclude generated data directories — `catalog/`, `metrics/`, `bot_tui_logs/`, `live_paper/data/` — never config files, matching `dydx_collector/config.toml`'s own already-committed precedent)

**Given** `_render_chart_page`'s `init_script` (`dashboard.py`, declares `_activeIndicators=[]` as an empty array on every page load today)
**When** this story is implemented
**Then** a new `GET /data/coin/{id}/indicator-config` endpoint returns that instrument's persisted entries (or an empty list if none saved yet), and the page's init script fetches it and populates `_activeIndicators` from the response before the first render — a coin with no saved config still opens exactly as it does today (empty picker, no regression)

**Given** the indicator picker's toolbar
**When** this story is implemented
**Then** it gains a "Save" control that POSTs the current `_activeIndicators` array (both native and custom entries) to a new `POST /data/coin/{id}/indicator-config` endpoint, which calls `save_config()` for that instrument — saving is explicit/user-triggered, not automatic on every add/remove, so an in-progress exploratory selection is never persisted by accident

**Given** `docker-compose.yml`'s `dashboard` service currently mounts `./dydx_collector/catalog:/app/catalog:ro` and `./dydx_collector/metrics:/app/metrics_dir:ro` — both read-only
**When** this story adds a file the running dashboard container must be able to write
**Then** `docker-compose.yml` gains a new bind mount for `ml_signals/chart_indicators.toml` (or its containing directory) into the `dashboard` service, read-write — mirroring the collector's own single `rw` config mount (`docker-compose.yml`'s `collector` service, `./config.toml:/app/dydx_collector/config.toml:rw`, the only other `rw` config mount in the file) — without this, "Save" would succeed inside the container's writable layer and vanish on the next `make redeploy`

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py -q` plus a new `ml_signals/tests/test_chart_indicator_config.py`
**When** it runs after this story
**Then** it passes, asserting `load_config()`/`save_config()` round-trip a multi-instrument, mixed-category selection correctly (TEST-01: integration path touching a persisted config file) and that `GET`/`POST /data/coin/{id}/indicator-config` behave correctly against a temp config file (same temp-file test pattern `dydx_collector/tests` already uses for `config.py`)

## Epic 12: Local Dashboard + bot_tui, VPS as Data API

Builder runs `dashboard.py` and `bot_tui` on their own machine instead of on nifelheim, reaching nifelheim's live data over one SSH tunnel (Redis, unchanged wire protocol) plus a new small read-only HTTP API for the handful of local-disk reads `dashboard.py` currently does directly. The VPS-hosted `collector`/`ranking_engine`/`dashboard`/`bot_tui` services keep working exactly as today — this epic adds a new opt-in path, it does not remove or change the existing one. Investigated this session: `bot_tui/*.py` touches nothing but Redis pub/sub (`bots:status`/`bots:control`, `snapshots:raw`, `rankings:live`, `ranking:control`, `collector:status`/`collector:control`, plus plain-key polling in `bot_history_state.py`/`bot_incidents_state.py`) — no direct file access anywhere, so it moves with zero code changes once Redis is tunneled. `dashboard.py` additionally does 4 direct local-disk reads: `ranking_engine/metrics_store.py`'s `history()`/`nearest()` (SQLite), `ml_signals/catalog_stats.py::query_second_snapshots()`, `ml_signals/chart_data.py::compute_chart_series()`, and one inline `ParquetDataCatalog(...).query(DydxSecondSnapshot, ...)` in `_historical_candles_json` (Parquet). Chosen shape (confirmed with user, do not revisit): exactly one new service, Python, FastAPI — ruled out a Go/Python split and a Redis→WebSocket bridge as unneeded (Redis is already a network service; the catalog reads must stay Python regardless, since `DydxSecondSnapshot`'s Arrow schema is only registered via `nautilus_trader.serialization.arrow` in Python, per NAUT-02).
**FRs covered:** FR34, FR35

### Story 12.1: Read-only `data_api` FastAPI service on the VPS

New service, no changes to any existing service's behavior. Reuses `ranking_engine/metrics_store.py` and `ml_signals/catalog_stats.py`/`chart_data.py` verbatim — no reimplemented logic.

As a user who wants to run the dashboard/bot_tui off nifelheim,
I want a small, read-only HTTP API that serves the same catalog/metrics data `dashboard.py` reads from local disk today,
So that a remote `dashboard.py` process can get identical data over the network instead of needing local disk access to nifelheim's catalog.

**Acceptance Criteria:**

**Given** a new `troll/data_api/app.py` (FastAPI), env vars `CATALOG_PATH`/`METRICS_DB_PATH` with the same defaults `ml_signals/dashboard.py:69,85-86` already uses
**When** the service is running
**Then** it exposes exactly 4 routes, each a thin wrapper (no reimplemented logic) around an existing function:
- `GET /metrics/history/{symbol}?days=31` → `ranking_engine.metrics_store.history(symbol, METRICS_DB_PATH, days)`, JSON list of dicts
- `GET /metrics/nearest/{symbol}?ts_ns=<int>` → `ranking_engine.metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)`, JSON dict or `null`
- `GET /catalog/chart-series/{symbol}?start_ns=&end_ns=` → `ml_signals.chart_data.compute_chart_series(CATALOG_PATH, symbol, start_ns, end_ns)`, returned verbatim
- `GET /catalog/snapshots/{iid}?start_ns=&end_ns=` → `ml_signals.catalog_stats.query_second_snapshots(CATALOG_PATH, iid, start_ns, end_ns)`, serialized to a list of dicts covering the fields `_historical_lines_json` already extracts (`dashboard.py:1379-1387`: `bid_prices`, `bid_sizes`, `ask_prices`, `ask_sizes`, `buy_volume`, `sell_volume`, `ts_event`) plus `open_price`/`high_price`/`low_price`/`close_price` (needed by `_historical_candles_json`'s OHLC aggregation) — one route replacing both of today's separate catalog reads, since they query the same underlying `DydxSecondSnapshot` rows

**Given** `troll/troll-requirements.txt`
**When** this story lands
**Then** `fastapi`, `uvicorn[standard]`, and `httpx` (required by FastAPI's `TestClient`) are added; `aiohttp` (already present) is left as-is, it will cover Story 12.2's client side

**Given** `troll/collector.dockerfile` (the shared image `dashboard`/`ranking_engine` already build from) and `troll/docker-compose.yml`
**When** this story lands
**Then** the dockerfile gains `COPY troll/data_api ./data_api` alongside its existing module copies, and compose gains a new `data_api` service: same `build:` block (dockerfile `collector.dockerfile`), `command: uvicorn data_api.app:app --host 127.0.0.1 --port 9100`, `network_mode: host` (matching every other service), env `CATALOG_PATH`/`METRICS_DB_PATH` matching `dashboard`'s own, and read-only volume mounts `./dydx_collector/catalog:/app/catalog:ro` + `./dydx_collector/metrics:/app/metrics_dir:ro` (same AD-3 discipline as `dashboard`'s existing mounts) — **no `ports:` entry, `network_mode: host` binds the app itself to `127.0.0.1:9100` per SEC-01, nothing public, ever**

**Given** `troll/data_api/tests/test_data_api.py` (new)
**When** `pytest data_api/tests -q` runs
**Then** it passes: FastAPI `TestClient` hitting all 4 routes against a temp SQLite file (written via `ranking_engine.metrics_store.write()`) and a temp `ParquetDataCatalog` seeded with real `DydxSecondSnapshot` rows, following the `_write_snapshot` pattern already established in `ml_signals/tests/test_catalog_stats.py:138-139` — never mock Nautilus internals (TEST-03), this is exactly the kind of catalog-touching integration path TEST-01 requires a test for

**Given** `troll/Makefile`'s `test:` target
**When** this story lands
**Then** `data_api/tests` is added to the pytest module list it already runs, so `make test` covers the new service without a separate invocation

### Story 12.2: `dashboard.py` remote-data mode + local-machine run docs

Depends on Story 12.1 (needs `data_api`'s routes to exist). No behavior change to the VPS-hosted `dashboard` compose service — `DATA_API_URL` unset must be provably identical to today.

As a user running `dashboard.py` on my own machine,
I want it to fetch its catalog/metrics data over the tunnel instead of local disk when configured to,
So that I get the exact same dashboard, running locally, without needing nifelheim's filesystem mounted.

**Acceptance Criteria:**

**Given** a new `DATA_API_URL` env var read in `ml_signals/dashboard.py` near its other env-var reads (`dashboard.py:69,85-86`)
**When** it is unset
**Then** every one of the 4 call sites below behaves byte-for-byte as it does today (local `ParquetDataCatalog`/`metrics_store` access) — this is the default for the VPS-hosted `dashboard` compose service, which gets no new env var and therefore no behavior change

**Given** `DATA_API_URL` is set (e.g. to `http://127.0.0.1:9100`, reached via an SSH-tunneled port)
**When** any of these run: `_render_history_page` (`dashboard.py:1073`), `rank_history_json_handler` (`:1534`), `_render_chart_page`'s `compute_chart_series` call (`:1274-1278`), `_historical_candles_json` (`:1274-1278`), `_historical_lines_json` (`:1371-1372`)
**Then** each fetches from `data_api`'s matching route over `aiohttp.ClientSession` (already a dependency) instead of touching local disk, and returns data producing an identical rendered page/JSON response to today's local-disk path for the same underlying catalog/metrics state — share one small `_fetch_json(session, url)` helper across all 5 call sites rather than 5 separate HTTP-call implementations

**Given** `troll/.env-example`
**When** this story lands
**Then** it documents `DATA_API_URL` (default empty = unchanged local/VPS behavior), mirroring the existing `WEB_PORT` doc comment's style

**Given** a user wants to run both processes locally
**When** they follow the new docs added to `troll/ARCHITECTURE.md`'s deployment-topology section
**Then** the docs cover: the one SSH tunnel command covering both ports (`ssh -fN -L 6379:localhost:6379 -L 9100:localhost:9100 nifelheim`), and the two local run commands (`REDIS_URL=redis://127.0.0.1:6379 DATA_API_URL=http://127.0.0.1:9100 python -m ml_signals.dashboard --open` and `REDIS_URL=redis://127.0.0.1:6379 python -m bot_tui.app`) — `bot_tui` needs no code change (already Redis-only) and its reverse-tunnel URL hand-off (`_open_via_local_listener`/`BOT_TUI_OPEN_URL_PORT`, `app.py:1424-1426`) needs no change either, since it already no-ops when the env var is unset and falls through to `webbrowser.open()`, which works correctly once both processes are local — do not modify that code path

**Given** `cd troll && python -m pytest ml_signals/tests/test_dashboard_chart.py ml_signals/tests/test_rank_history.py -q` plus any new/updated tests for the remote-mode branches
**When** it runs after this story
**Then** it passes — cover both the `DATA_API_URL` unset (unchanged) and set (HTTP fetch) branches of at least one of the 5 call sites (TEST-01: this is new branching logic, not trivial glue)

## Epic 13: ranking_engine Memory/CPU Stabilization

Builder stops seeing `ranking_engine` OOM-restart every ~2 minutes on nifelheim. Root cause investigated this session: `ranking_engine/engine.py:485`'s `_slow_loop_task` calls `metrics_computer.compute_all()` every `DB_WRITE_INTERVAL_SECONDS` (60s), which spins up `ThreadPoolExecutor(max_workers=32)` (`metrics_computer.py:71`) — per instrument, opens a fresh `ParquetDataCatalog` and re-reads a full 25h (`PRICE_LOOKBACK_HOURS`) window via `catalog_stats.price_series()`, which queries `DydxSecondSnapshot` (`catalog_stats.py:200`) — the same type already streaming live through Redis `snapshots:raw` and already ingested into `ranking_engine`'s own `_SECOND_ROLLING` (`engine.py:129,287`). The only reason the Parquet re-read is needed at all: `_SECOND_ROLLING` is `deque(maxlen=300)` — 5 minutes, nowhere near enough for `pct_1h`/`pct_24h`/volatility. A 2026-09-11 fix already scoped the instrument count down (~300→~29) but, per the incident writeup (`troll/.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md`), "reduced per-cycle memory but didn't add real headroom." This epic ships an immediate concurrency cap (Story 13.1) and then removes the recurring re-scan entirely (Story 13.2).
**FRs covered:** FR36, FR37

### Story 13.1: Bound `compute_all()`'s catalog-read concurrency

Independent of Story 13.2, ships first — a one-line-call-site change with immediate effect.

As an operator of nifelheim,
I want ranking_engine's periodic catalog scan to run at a small, fixed concurrency instead of one thread per instrument,
So that its 60s cycle no longer spikes peak memory with ~29 simultaneous Parquet/pandas reads.

**Acceptance Criteria:**

**Given** `ranking_engine/engine.py:485`'s call to `metrics_computer.compute_all(catalog_path, book_metrics_fn=..., instrument_ids=list(book_metrics_by_iid))`
**When** this story lands
**Then** the call passes an explicit `max_workers` (a small fixed value, e.g. 4 — not derived from instrument count) instead of relying on `metrics_computer.compute_all`'s current `max_workers: int = 32` default; the default itself may stay 32 (call-site override is sufficient, no need to change the function signature's default) or be lowered too, dev's judgment, as long as `ranking_engine`'s own call is bounded

**Given** the existing behavior of `compute_all()` otherwise
**When** `max_workers` is lowered
**Then** results are unchanged (same instruments, same 25h lookback, same returned dicts) — only the number of concurrent in-flight `ParquetDataCatalog` reads changes; total per-cycle wall-clock time may increase (more sequential batches) but must stay well under `DB_WRITE_INTERVAL_SECONDS` (60s) for the current ~29-instrument count

**Given** `docker stats`/`free -h` evidence on nifelheim before/after (per troll/CLAUDE.md DATA-02 — real evidence, not "looks fine")
**When** this story is verified
**Then** `ranking_engine`'s peak RSS during a `_slow_loop_task` cycle is observably lower than before the change — this is a mitigation, not a full fix (the box may still be oversubscribed at rest per the incident writeup), so verification should report the actual before/after numbers rather than claim the OOM loop is fully resolved unless it demonstrably is

**Given** no existing test exercises `max_workers` as a parameter
**When** this story lands
**Then** no new test is required (TEST-02: trivial call-site argument change, no new branching logic) — existing `ranking_engine/tests/test_engine.py` coverage of `_slow_loop_task`'s behavior must still pass unchanged

### Story 13.2: In-memory long-window price series, replacing the recurring Parquet re-scan

Depends on Story 13.1 landing first (keeps the mitigation in place while this larger change is built and reviewed). This is the real fix — Story 13.1 only shrinks the existing spike, this removes its cause.

As an operator of nifelheim,
I want ranking_engine's `price`/`pct_1h`/`pct_24h`/`volatility` fields computed from data it's already holding in memory,
So that its 60s cycle stops re-reading ~25 hours of mostly-unchanged data from Parquet every single time.

**Acceptance Criteria:**

**Given** a new long-window, per-instrument price series held in `ranking_engine/engine.py` (numpy ring buffers of `(ts_event_ns, close_price)`, sized for `PRICE_LOOKBACK_HOURS` = 25h — explicitly NOT Python-boxed tuples/deques, to keep this cheap: ~90,000 points/instrument × ~29 instruments × 16 bytes/point ≈ 40MB steady-state, vs. today's spiky ~29-concurrent-pandas-DataFrame peak)
**When** `ranking_engine` starts up (or restarts)
**Then** it seeds this series with exactly ONE Parquet backfill read per instrument (reusing `catalog_stats.price_series()`/`query_second_snapshots()` as today, not reimplemented), preserving the existing restart-survives-with-full-history property (the collector's writes are independent of `ranking_engine`'s own uptime, so this backfill is always available even after an OOM restart)

**Given** the live `snapshots:raw` feed `ranking_engine` already consumes into `_SECOND_ROLLING` (`engine.py:287`, `_ingest_snapshot_batch`)
**When** each new `DydxSecondSnapshot` arrives
**Then** its `(ts_event, close_price)` is also appended to the new long-window series (when `close_price is not None`, matching `price_series()`'s existing "seconds with no trade contribute nothing" semantics, `catalog_stats.py:196-197`) with O(1) eviction of entries older than 25h — this must not duplicate or diverge from `_SECOND_ROLLING`'s own short-window bookkeeping, it is an additional, independent structure fed by the same ingest path

**Given** `_slow_loop_task`'s existing 60s cycle (`engine.py:459-503`)
**When** this story lands
**Then** `price`/`pct_1h`/`pct_24h`/`volatility` are computed as slices/reductions over the new in-memory series instead of via `metrics_computer.compute_all()`'s Parquet-backed `ThreadPoolExecutor` path — `compute_all()`/`price_stats()`/`price_series()` are no longer called from the periodic cycle for these fields (the backfill-at-startup call is the only remaining Parquet read in the hot path); `_legacy_book_metrics_for`'s OFI/OBI/etc. fields, already sourced from live trackers, are unaffected

**Given** the exact `pct_1h`/`pct_24h`/`volatility` formulas today (`catalog_stats.py:241-249`'s `_pct_change`/returns-stdev) and their "not enough history yet → `None`" guard (`catalog_stats.py:230-231,243-244`)
**When** the new in-memory computation replaces the Parquet-backed one
**Then** values match — verify side by side (both paths computing from the same underlying data during development, per troll/CLAUDE.md DATA-02's standard of real evidence, not code-reading alone) before removing the old path; the same "not enough history yet" `None` behavior must hold when the in-memory series hasn't yet accumulated a full 1h/24h since last backfill

**Given** `ranking_engine/tests/test_engine.py` and any new test file for the price-series structure
**When** `pytest ranking_engine/tests -q` runs
**Then** it passes, with new coverage for: the ring buffer's append/evict behavior, the startup-backfill-then-incremental-update sequence, and `pct_1h`/`pct_24h`/`volatility` computed from the in-memory series matching `catalog_stats.price_stats()`'s output for equivalent input data (TEST-01: this is a financial calculation and an integration path touching the catalog — real `DydxSecondSnapshot`/`ParquetDataCatalog` objects, never mocked, per TEST-03)

**Given** `docker stats`/`free -h` on nifelheim, and `rankings:live`/`metrics.db` output, before/after this story (DATA-02 real evidence)
**When** this story is verified
**Then** `ranking_engine`'s peak RSS during a `_slow_loop_task` cycle drops further than Story 13.1 alone achieved, and the OOM-restart loop (`docker events`, `RestartCount`) is observably reduced or stopped — report actual numbers; if the box is still oversubscribed at rest even after this fix (per the incident writeup's own conclusion that this may be a capacity problem, not purely a logic bug), say so explicitly rather than claiming full resolution

## Epic 15: Dashboard React/TypeScript Rewrite

Builder gets the same dashboard capabilities they use every day — live coin rankings, a coin's candlestick chart with synced indicator panes and TradingView-style scroll-back history, 31-day metrics history, docs — rebuilt as a fast React SPA with a terminal/ANSI visual identity, served by a single Read-Only Facade backend (`data_api`) that replaces `troll/ml_signals/dashboard.py` entirely. Backed by a finalized PRD (`prds/prd-chart-frontend-rewrite-2026-09-13/prd.md`) and architecture spine (`architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md`), both `status: final`. Stories are sequenced walking-skeleton-first: 15.1 proves the facade/SPA/codegen pipeline end-to-end via the simplest page (Docs), then each subsequent story builds one more vertical slice on top, ending with the cutover that retires `dashboard.py`. Supersedes `epic-14` (bypass-epic, never entered into this document; 14.1/14.2 done, 14.3 becomes moot and is marked superseded in Story 15.10, not shipped).
**FRs covered:** FR38, FR39, FR40, FR41, FR42, FR43, FR44, FR45, FR46
**NFRs covered:** NFR6, NFR7, NFR8, NFR9

### Story 15.1: Facade scaffold, SPA static serving, and Docs page

Foundational — every later story in this epic depends on this pipeline existing and working. Delivers a real page (Docs, FR45) as the walking-skeleton proof, not just infrastructure.

As the dashboard operator,
I want a working end-to-end pipeline (React SPA built and served by `data_api`, with generated API types) proven by the simplest existing page,
So that every later story lands on a working foundation instead of untested plumbing.

**Acceptance Criteria:**

**Given** `troll/frontend/` does not yet exist
**When** this story lands
**Then** it is scaffolded via Vite + React + TypeScript (strict mode) at the architecture spine's pinned versions (React 19.3.0, Vite 8.3.0 + `@vitejs/plugin-react` 6.1.1), with route-based code-splitting configured (each page a separate lazy-loaded chunk) per the spine's Consistency Conventions

**Given** `data_api/app.py`
**When** this story lands
**Then** it declares Pydantic response models for its first route and serves `troll/frontend/dist/` via FastAPI's native `app.frontend()` helper (AD-F1a) — never a hand-rolled `StaticFiles` mount + catch-all route — and an unmatched `/api/*` path returns a JSON 404, never SPA HTML (Consistency Conventions)

**Given** `data_api`'s OpenAPI schema
**When** the frontend build runs
**Then** TypeScript request/response types are generated from that schema (AD-F5) into `frontend/src/api/`, never hand-written to match — codegen tool choice is an implementation detail (Deferred)

**Given** `dashboard.py`'s existing `docs_handler` content
**When** this story lands
**Then** `/docs` (Docs page, FR45) is rebuilt as a React page reading that same content (a diffable content checklist, not a rewrite of the text) and served through the new pipeline end-to-end

**Given** `troll/collector.dockerfile` currently builds `collector`/`dashboard`/`ranking_engine`/`data_api`/`bot_tui` from one shared file
**When** this story lands
**Then** `troll/data_api.dockerfile` exists as its own file (layered on `nautilus-trader-base`, an added Node build stage running `vite build` against `frontend/`, `dist/` copied into the final image, `node_modules` absent from the runtime layer) and `troll/collector.dockerfile` is unchanged

**Given** `docker-compose.yml`
**When** this story lands
**Then** a `data_api` service exists (`network_mode: host`, binding `127.0.0.1` only per SEC-01) — the `dashboard` service is NOT yet removed in this story (removal is Story 15.10's cutover, once every page has a working equivalent per NFR9)

**Given** no test currently exercises this pipeline
**When** this story lands
**Then** a smoke test (a Vitest render test for `DocsPage`, plus a pytest hitting the route backing `/docs` and asserting a 200) proves the scaffold works end-to-end (TEST-01: every later story depends on this integration path)

### Story 15.2: Live coin-rankings page

As the dashboard operator,
I want to see every subscribed coin's live rank, price, and key metrics updating in real time without a manual refresh,
So that I can decide what to look at next the moment a coin's ranking changes.

**Acceptance Criteria:**

**Given** `ranking_engine`'s existing `rankings:live` Redis publish (parent spine AD-9)
**When** `GET /api/rankings` is called
**Then** `data_api/routes/rankings.py` returns the current snapshot verbatim (AD-F2 passthrough, no recomputation) shaped as `{"items": [...], "updated_at": ...}`

**Given** `data_api/ws/live.py`
**When** a client subscribes to `/ws/live`
**Then** `rankings:live` messages are relayed using their existing wire format verbatim (AD-F2), with no reshaping beyond channel subscription

**Given** `frontend/src/pages/RankingsPage.tsx` and a `useLiveChannel` hook
**When** a `rankings:live` message arrives
**Then** the table's row order matches the message's order exactly — no independent client-side re-sort logic beyond what the active Ranking Mode already dictates (FR38)

**Given** a coin whose `rankings:live` `updated_at` has exceeded its configured heartbeat timeout
**When** the table renders
**Then** that row is visibly marked stale (dimmed/badge), never silently frozen in its last position (AD-F6 staleness half)

**Given** each row's React key
**When** the table re-renders on every live tick
**Then** it is keyed by `instrument_id` (stable), never by array index or a per-tick timestamp/UUID (Consistency Conventions: "Live-refreshing list identity")

**Given** a rankings row
**When** the operator clicks it
**Then** the app navigates to that coin's chart page (realizes UJ-1)

**Given** `bot_tui`'s existing coin-list pane (parent FR19)
**When** this story ships
**Then** confirm no new column/metric was introduced beyond what `dashboard.py`'s rankings view already showed — FR38 is parity, not new columns; if a future story adds one, SSOT-04/05 requires landing it in `bot_tui` too

**Given** the rankings query/relay path
**When** tests run
**Then** existing `ranking_engine` test coverage of `rankings:live`'s shape is unaffected, plus a new integration test exercises `GET /api/rankings` and the `/ws/live` relay against a real (not mocked) Redis fixture matching the real wire format (TEST-03)

### Story 15.3: Chart page foundation — candlestick + cursor-paginated history

As the dashboard operator,
I want a coin's candlestick chart to load its recent window immediately and fetch older bars progressively as I scroll back,
So that I never wait on a multi-megabyte full-history fetch just to see a chart.

**Acceptance Criteria:**

**Given** `ml_signals.candles`' existing aggregation function (used today by `dashboard.py`'s `chart_handler`)
**When** `GET /api/candles/{instrument_id}` is called with `before_ns` + `limit`
**Then** `data_api/routes/candles.py` returns at most `limit` rows strictly older than `before_ns` as `{"items": [...], "has_more": bool}` (AD-F3) — the underlying business logic is relocated unchanged; the route's `before_ns`/`limit` wire contract replaces today's `start_ns`/`end_ns` shape (AD-F1's upgrade-during-relocation clause)

**Given** `frontend/src/components/chart/`
**When** a coin's chart page first loads
**Then** it fetches only the most recent window (matching today's 120-bar default), not the full catalog range (FR40)

**Given** `lightweight-charts`' `subscribeVisibleLogicalRangeChange`
**When** the visible range approaches the earliest currently-loaded bar
**Then** the next page is fetched via the same `before_ns`/`limit` contract — the operator never observes a request whose response exceeds one page's worth of bars (FR40)

**Given** `has_more: false` in a response
**When** the true start of a coin's history is reached
**Then** no further requests are issued for that direction — never retrying or hanging (FR40)

**Given** a genuine gap in the backend's queried range (collector-skipped emission, or a scroll-back page with a partial-range gap)
**When** the API returns that range
**Then** it includes an explicit gap marker (a `null`/whitespace-data point at the gap boundary, matching `lightweight-charts`' native whitespace-data support) rather than omitting the row, and the frontend renders it as a visible break — never interpolated or flat (AD-F6)

**Given** `frontend/src/components/chart/`
**When** this story lands
**Then** exactly one `lightweight-charts` `createChart()` instance is created for the page — the single-instance invariant (AD-F4) is established here even though indicator panes arrive in Story 15.4, so no instance created here is later discarded/recreated

**Given** the ~12MB-per-4-hour-window `/catalog/snapshots` timeout already fixed once this session for candles
**When** this story's tests run
**Then** a boundary test confirms a large `before_ns`/`limit` request never returns more than `limit` rows (MEM-01 extended to the API surface, per AD-F3's stated prevention)

**Given** this is a financial/catalog-integration path
**When** `pytest troll/data_api/tests` runs
**Then** it covers `/api/candles` against real `Price`/candle objects and a real (not mocked) catalog fixture (TEST-01/03)

### Story 15.4: Synced indicator panes

As the dashboard operator,
I want OFI, order-book imbalance, volume, microprice, and spread panes stacked beneath the candlestick chart and moving in lockstep with it,
So that I can read a coin's technical structure without the panes drifting out of sync the way ad-hoc event wiring would risk.

**Acceptance Criteria:**

**Given** Story 15.3's one `createChart()` instance
**When** an indicator pane is added
**Then** it is added via `chart.addPane()` + `addSeries`/`addCustomSeries` on its own `IPaneApi` — never a second `createChart()` instance kept in sync by application code (AD-F4)

**Given** `dashboard.py`'s existing `_indicator_id(name, params)` scheme
**When** a pane is created
**Then** it is keyed by that same indicator id (reused, not reinvented) in a single `Map<indicatorId, IPaneApi>` owned by the one component that calls `createChart()` — no child component creates or destroys a pane directly (AD-F4)

**Given** any one pane
**When** the operator pans or zooms it (mouse or touch)
**Then** every other pane on the page moves in lockstep in the same interaction, via the charting library's native multi-pane time-scale sync — no custom event-relay code (FR39)

**Given** an indicator is added, removed, or reconfigured
**When** the chart re-renders
**Then** the current zoom/pan position is never reset — the exact regression Story 14.3 exists to fix, and this epic's own trigger (FR39)

**Given** Story 15.3's cursor-paginated scroll-back
**When** the candlestick pane's scroll-back triggers the next `before_ns`/`limit` page
**Then** each visible indicator pane co-pages its own underlying snapshot data for that same window in the same interaction — never blank, never lagging the candlestick pane's loaded range (FR40)

**Given** a touch-driven phone/tablet viewport
**When** the operator drags to pan or pinches to zoom any pane
**Then** every consequence above holds identically to mouse/trackpad (FR39, NFR7)

**Given** up to five indicator panes visible at once
**When** they render
**Then** each gets a distinct, consistently-assigned series color — exact palette values finalize in Story 15.9, but the assignment logic (indicator id → color slot) is built here so panes are never visually indistinguishable in the interim (FR39)

**Given** `ml_signals.indicators`' existing OFI/OBI/microprice/spread functions
**When** a pane's data is fetched
**Then** the route calls those existing functions — never a reimplementation (AD-F2)

**Given** this is a multi-file, non-trivial sync mechanism
**When** tests run
**Then** a Vitest test exercises the keyed pane registry (add/remove by indicator id) and an integration/E2E-style test confirms pan/zoom on one pane's time-scale actually propagates to a sibling pane (TEST-01)

### Story 15.5: Live candle edge

As the dashboard operator,
I want the candlestick chart's currently-forming bar to update live without ever visibly diverging from the historical bars beside it,
So that I can trust the right edge of the chart as much as its paginated history.

**Acceptance Criteria:**

**Given** `ml_signals.candles`' existing aggregation function (already used by Story 15.3's `/api/candles`)
**When** a `snapshots:raw` tick arrives for a subscribed instrument
**Then** `data_api/ws/live.py` computes the forming bar server-side by calling that same function — never a new/parallel aggregation implementation (AD-F7, AD-F2)

**Given** that computed bar
**When** it changes (on tick and on bar-close)
**Then** it is published on a derived `/ws/live` sub-channel `candles:{instrument_id}:{bar_seconds}`, one bar per message (AD-F7)

**Given** `frontend/src/hooks/useLiveChannel`
**When** the chart page is open
**Then** it subscribes to this channel for the live edge only — it never aggregates a candle itself from raw snapshot data (AD-F7)

**Given** a bar-size change (e.g. 1m → 1h)
**When** the operator switches it
**Then** no stale live bar from the old bar-size is left overlapping the freshly-loaded historical bars for the new size (FR41)

**Given** the operator navigates away from a coin's chart page and back
**When** the page remounts
**Then** the live edge resumes cleanly with no stale bar carried over from the previous mount (FR41)

**Given** this is a live-data-consistency path
**When** tests run
**Then** a test verifies the server-computed forming bar matches `ml_signals.candles`' own aggregation for equivalent input ticks (TEST-01/03: real `Bar`/`Price` objects, no mocks)

### Story 15.6: Per-coin indicator configuration

As the dashboard operator,
I want to add, remove, and reconfigure a coin's chart indicators and have that choice persist,
So that I don't have to rebuild my preferred view of a coin every time I revisit it.

**Acceptance Criteria:**

**Given** `dashboard.py`'s existing `save_coin_indicator_config_handler`
**When** `PUT /api/coin/{iid}/indicators` is called
**Then** `data_api/routes/indicators.py` persists the config using that same relocated logic (AD-F1 verbatim relocation of business logic) — this is the Facade's one sanctioned write path beyond `/ws/live` relaying (AD-F2 exception)

**Given** `GET /api/indicators/catalog`
**When** the frontend requests it
**Then** it returns the available indicator catalog (names/params/overlay-vs-oscillator classification) sourced from `ml_signals`' existing registry — never a second, hand-duplicated list in the frontend

**Given** a coin's chart page
**When** the operator adds, removes, or reconfigures an indicator via the UI
**Then** the change is sent through `PUT /api/coin/{iid}/indicators` and the resulting pane set updates per Story 15.4's keyed registry

**Given** a coin's persisted indicator config
**When** the operator reloads that coin's chart page
**Then** the same indicators and parameters last configured for that coin are restored exactly (FR42)

**Given** this is a config-integration path (not pure financial calculation)
**When** tests run
**Then** an integration test covers the GET (catalog) + PUT (persist) + reload-restores-config round trip, using real config objects (TEST-01: integration path touching a persisted resource)

### Story 15.7: Lines mode

As the dashboard operator,
I want to switch a coin's chart from candlesticks to a direct line comparison of bid/ask/mid/microprice/price,
So that I can read raw price-series behavior the way I do today, without losing my place in time when I switch.

**Acceptance Criteria:**

**Given** `dashboard.py`'s existing Candles/Lines toggle
**When** this story lands
**Then** the frontend carries that same toggle forward on the chart page

**Given** `GET /api/snapshots/{instrument_id}` with `before_ns` + `limit`
**When** Lines mode requests history
**Then** `data_api/routes/snapshots.py` returns it under the identical cursor-paginated contract as `/api/candles` (`{"items": [...], "has_more": bool}`) — spine AD-F3 explicitly binds this route to the same contract, not a separate unbounded fetch

**Given** the operator is viewing a specific time range in Candles mode
**When** they switch to Lines mode (or back)
**Then** the currently-viewed time range is preserved, never reset to a default window (FR43)

**Given** a gap in the underlying snapshot data
**When** Lines mode renders it
**Then** the same AD-F6 gap-marker discipline from Story 15.3 applies — never an interpolated flat line

**Given** this route repeats the exact shape of Story 15.3's `/api/candles` route
**When** tests run
**Then** the same boundary/pagination test pattern is applied to `/api/snapshots` (TEST-01, mirrors Story 15.3's coverage)

### Story 15.8: 31-day metrics history page

As the dashboard operator,
I want to see a coin's ranking-input metrics (volume, volatility) plotted over the trailing 31 days,
So that I can decide whether a coin merits opt-in raw-delta capture.

**Acceptance Criteria:**

**Given** `dashboard.py`'s existing `_render_history_page`/`_history_page_from_rows` and `metrics_store`
**When** `GET /api/metrics/history/{symbol}` is called
**Then** `data_api/routes/metrics.py` returns the same underlying `metrics_store` query (relocated, not reimplemented) shaped as JSON for the new `HistoryPage`

**Given** `frontend/src/pages/HistoryPage.tsx`
**When** a coin's history page loads
**Then** it renders each ranking-input metric (volume, volatility, etc.) as a small time-series chart covering the trailing 31 days

**Given** a metric with no data for part of the 31-day window
**When** the chart renders that range
**Then** it shows a visible gap, never an interpolated flat line (FR44, restates AD-F6 for this page)

**Given** `GET /api/metrics/nearest/{symbol}` (per the architecture's Structural Seed)
**When** this story lands
**Then** it is relocated alongside metrics/history for any nearest-value lookup the page needs

**Given** this is a read-only reporting path with no complex branching
**When** tests run
**Then** a single integration test covers the route returning real `metrics_store` rows, including a deliberate gap case (TEST-01: touches the catalog-adjacent metrics store)

### Story 15.9: Terminal/ANSI visual identity

As the dashboard operator,
I want every page to render in a consistent DOS-style terminal aesthetic using only the classic 16-color VGA/ANSI palette,
So that the tool looks and feels like a serious terminal instrument for reading raw market data, not a generic web app.

**Acceptance Criteria:**

**Given** the 16-color VGA/ANSI palette (black, blue, green, cyan, red, magenta, brown/yellow, light gray, dark gray, light blue, light green, light cyan, light red, light magenta, yellow, white)
**When** this story lands
**Then** it is declared once as design tokens (e.g. CSS custom properties) and every page (rankings, chart, history, docs) sources all color — background, text, borders, semantic states, chart series — exclusively from those tokens; no color outside the set appears anywhere in the frontend (FR46)

**Given** a DOS/BIOS-style bitmap terminal font (direction confirmed in the PRD; exact family an implementation choice per PRD §8 Open Question 1)
**When** this story lands
**Then** it is applied as the sole typeface for headings, body, tables, and chart labels across all four pages — no page falls back to a proportional/sans-serif face (FR46)

**Given** box-drawing characters and terminal-style loading/empty states (blinking cursor, ASCII progress indicator)
**When** this story lands
**Then** they replace conventional web-app borders/dividers/spinners/skeleton screens as the pages' decorative motif (per PRD Aesthetic and Tone; exact placement/extent per PRD §8 Open Question 2, an implementation choice)

**Given** semantic color use (stale-data indicator, up/down candle, active/inactive UI)
**When** this story lands
**Then** each is drawn from the same 16-color token set, not a separate arbitrary palette (FR46) — this finalizes the pane-color assignment stubbed in Story 15.4

**Given** CRT/scanline effects are explicitly out of scope (PRD Aesthetic and Tone, Non-Goals)
**When** this story lands
**Then** no such rendering is added

**Given** SM-3 (visual-identity conformance)
**When** this story is verified
**Then** a manual palette check confirms no color outside the 16-color set appears anywhere in the built frontend, and the DOS-style font renders with no visible proportional-font fallback

### Story 15.10: Cutover — retire dashboard.py, close epic-14

As the dashboard operator,
I want `dashboard.py` fully retired once its React replacement has verified parity,
So that I'm not maintaining two dashboards, and stale/superseded work doesn't linger in the backlog.

**Acceptance Criteria:**

**Given** every page/capability present in today's `dashboard.py` (rankings, chart w/ candles+lines+indicators+live edge, 31-day history, docs, terminal visual identity)
**When** this story starts
**Then** a feature-parity checklist (SM-1) is walked and confirmed against Stories 15.1–15.9's shipped React equivalents — any gap found blocks this story, it does not get silently skipped

**Given** the checklist passes
**When** this story lands
**Then** `dashboard.py`'s HTML-rendering functions (`_page`, `_build_chart_page_html`, `_render_live_page`, `_render_history_page`, `_history_page_from_rows`, `docs_handler`, etc.) are deleted, not retained as dead code (AD-F1)

**Given** `docker-compose.yml`'s `dashboard` service
**When** this story lands
**Then** it is removed; `data_api` fully absorbs its role (already added in Story 15.1), still `network_mode: host` / `127.0.0.1`-bound (SEC-01 unchanged)

**Given** `epic-14` (bypass-epic, stories 14.1/14.2 done, 14.3 ready-for-dev)
**When** this story lands
**Then** 14.3 is marked superseded (not shipped) in `sprint-status.yaml`, since `dashboard.py`'s chart page — the surface 14.3 would have modified — no longer exists

**Given** the SSH-tunnel remote-dev flow (`troll/CLAUDE.md` "Desktop ↔ VPS Connection")
**When** this story lands
**Then** that doc is updated to reflect one tunneled surface (`data_api`) instead of two (`dashboard` + `data_api`), per the architecture spine's stated simplification

**Given** SM-2 (perceived speed) and NFR6
**When** this story is verified
**Then** chart-page interactivity and scroll-back are checked on the real SSH-tunneled access path (not just localhost), with results reported — not merely asserted as "fast"

**Given** this is a deletion/cutover story with no new branching logic
**When** it lands
**Then** no new test is required beyond re-running the full existing `troll/` test suite to confirm nothing outside `dashboard.py`'s own tests depended on the deleted functions (TEST-02: trivial removal, but verify no accidental external import breaks)

## Epic 16: Minute-Rollup Candle Cache

Builder's chart page can show daily/weekly candles — with order-book-derived signal (OFI/OBI, top-of-book) baked in — without every request rescanning years of raw 1-second data. The collector incrementally builds a small `DydxMinuteRollup` cache as data streams in (O(1)/second, no periodic full rescan); wide-window candle requests read from it instead of raw 1s, with correct-by-construction OFI continuity across minute boundaries and a fallback to raw 1s when rollup coverage is missing. The raw 1-second archive stays fully intact and authoritative — the rollup is a regenerable performance cache, never a replacement. Backend/collector-pipeline scope, standalone: delivers complete value against the existing `dashboard.py`/`data_api` candle route today, and is a dependency for Epic 15's future `/api/candles` story once that lands.

### Story 16.1: Incremental 1-minute rollup cache

As the collector operator,
I want a `DydxMinuteRollup` type built incrementally from each second's `DydxSecondSnapshot` as it streams in,
So that a small, always-up-to-date cache of OHLCV + order-book-derived aggregates exists for every closed minute, without any periodic full-catalog rescan and without touching the raw 1-second archive.

**Acceptance Criteria:**

**Given** a new `DydxMinuteRollup` `Data` type defined in `troll/dydx_collector/minute_rollup.py` (same `schema()`/`to_dict`/`from_dict`/`register_arrow` pattern as `second_snapshot.py`)
**When** the collector runs
**Then** it carries `instrument_id`, `ts_event`/`ts_init`, `open/high/low/close` (`None` if no trades that minute), `buy_volume`/`sell_volume`/`buy_count`/`sell_count` (summed), `seconds_observed`, `close_bid_price`/`close_bid_size`/`close_ask_price`/`close_ask_size` (raw top-of-book at the minute's last observed second, not pre-computed microprice/spread), and `ofi_5`/`ofi_10`/`obi_5`/`obi_10`

**Given** a `MinuteRollupBuilder.update(iid, snapshot)` fed one `DydxSecondSnapshot` per call
**When** a snapshot from a new minute bucket arrives
**Then** it returns the just-closed previous minute's completed `DydxMinuteRollup`; on every other call it returns `None`, and an in-progress (not-yet-closed) minute is never returned, including at collector shutdown

**Given** `ofi_5`/`ofi_10` are computed via one long-lived `MultiLevelOFI(levels=n, window=1)` instance per `(iid, n)`, never reconstructed at a minute boundary
**When** the first snapshot of a new minute arrives
**Then** its OFI contribution is computed against the true previous second's book (the last second of the prior minute) — never lost, never compared against nothing — verified by a test that hand-computes the expected boundary-crossing contribution via a bare `MultiLevelOFI` and asserts the rollup includes it

**Given** `MinuteRollupBuilder.discard_book_state(iid)`, called from `collector.py::_clear_book_state` (both existing call sites: `_unsubscribe` and `_resync_book`)
**When** the book has just been rebuilt from scratch (resync/resubscribe)
**Then** each of that instrument's OFI instances has its `clear_prev_state()` called, so the next `update_raw()` is treated as a first observation, not compared against pre-resync prices — verified by a test with an artificial large price jump across the `discard_book_state` call, asserting no phantom large OFI contribution results

**Given** `obi_5`/`obi_10` via `MultiLevelOBI` (stateless per snapshot, no continuity concern)
**When** a minute closes
**Then** the emitted rollup's `obi_5`/`obi_10` are the mean of that minute's per-second `MultiLevelOBI` readings

**Given** the collector's existing per-second loop (`collector.py:1194`, immediately after `self._on_data(snapshot)`)
**When** `MinuteRollupBuilder.update()` returns a completed rollup
**Then** it is routed through `self._on_data(rollup)` — the existing `_buffer_key`/`_process_data`/`_flush_once` buffer-and-flush path, on the existing `flush_interval_seconds` cadence — with no second flush mechanism added

**Given** a fully-skipped minute (e.g. book down, `_discard_second_accumulators` firing every tick)
**When** the book recovers in a later minute
**Then** no rollup row is ever emitted for the skipped minute — matching `DydxSecondSnapshot`'s own no-row-for-skipped-second contract

**Given** a minute with real book activity but zero trades
**When** it closes
**Then** `open`/`high`/`low`/`close` are all `None` while `obi_5`/`obi_10` are real floats and `seconds_observed` reflects actual coverage

**Given** TEST-01 (financial/stateful calculation)
**When** this story is verified
**Then** `troll/dydx_collector/tests/test_minute_rollup.py` exists with real `DydxSecondSnapshot`/`MultiLevelOFI` objects (no mocking), pytest, `_`-prefixed helpers, covering: correct OHLCV/volume/counts for a single minute; the OFI-continuity-across-boundary case above; the resync/`discard_book_state` case above; fully-skipped-minute emits nothing; no-trade minute has `None` OHLC but real OBI; a partial in-progress minute is never emitted by `update()`

### Story 16.2: Threshold-based candle source dispatch

As a chart-page user,
I want daily/weekly candle requests to read from the minute rollup instead of rescanning raw 1-second data,
So that wide-window charts load fast and carry order-book-derived signal, while short-window charts keep behaving exactly as they do today.

**Acceptance Criteria:**

**Given** `ml_signals/candles.py`'s `TIMEFRAMES` extended with `"1d": 86400, "1w": 604800`, and `ROLLUP_THRESHOLD_SECONDS = TIMEFRAMES["1h"]`
**When** a candle request's `bar_seconds` is `> 3600`
**Then** `choose_candle_source(bar_seconds)` returns `"rollup_1m"`; at or below `3600` it returns `"raw_1s"` — a single shared decision point, never duplicated in `dashboard.py`/`data_api/app.py`

**Given** `rollup_dicts_from_rows(rows, period_seconds)`, the rollup analogue of `candle_dicts_from_snapshots`
**When** re-bucketing `DydxMinuteRollup` rows into a wider window
**Then** OHLCV/counts are summed (no-trade members excluded from O/H/L/C derivation, still counted for volume), `seconds_observed` is summed, `ofi_5`/`ofi_10` are summed, `obi_5`/`obi_10` are `seconds_observed`-weighted means, and `close_bid_price`/`close_ask_price`/microprice/spread (derived via `indicators.py`'s existing `microprice()`/`spread()`) take the **last** member's value, never summed or averaged

**Given** `candle_dicts_for_window(iid, start_ns, end_ns, bar_seconds, snapshot_rows_fn, rollup_rows_fn)`, the dependency-injected dispatch function
**When** `choose_candle_source` selects `"rollup_1m"` and `rollup_rows_fn` returns at least one row
**Then** the response is built via `rollup_dicts_from_rows` and every returned candle dict carries `"source": "rollup_1m"`

**Given** the same dispatch, but `rollup_rows_fn` returns no rows for the requested range (pre-feature historical data, or a recently-restarted collector)
**When** the request is served
**Then** it falls back to `snapshot_rows_fn` + `candle_dicts_from_snapshots` (raw 1s), logs a warning naming the instrument and range, and every returned candle dict carries `"source": "raw_1s"` — never a silently empty or wrong chart

**Given** `troll/ml_signals/catalog_stats.py`'s new `query_minute_rollups(catalog_path, iid, start_ns, end_ns)` (exact mirror of the existing `query_second_snapshots`)
**When** `dashboard.py::_historical_candles_json` and `data_api/app.py::catalog_candles` are updated
**Then** both call `candle_dicts_for_window` with closures around `query_second_snapshots`/`query_minute_rollups` instead of calling `candle_dicts_from_snapshots` directly — URL/params (`start_ns`/`end_ns`/`bar_seconds`) unchanged, no new route added

**Given** TEST-01 (financial calculation) extending `troll/ml_signals/tests/test_candles.py`
**When** this story is verified
**Then** tests cover: re-bucketing OHLCV + summed `ofi_5`; close-book/microprice/spread fields take the last member's value, not summed; `obi_5`/`obi_10` seconds-observed-weighted mean with unequal weights; the threshold boundary in `choose_candle_source`; fallback-to-raw-1s when rollup is empty (result matches `candle_dicts_from_snapshots`' own output, tagged `source: "raw_1s"`); rollup used when present, tagged `source: "rollup_1m"`

**Given** a manual verification pass
**When** the chart page (local mode) requests `bar_seconds=86400` for a multi-week range spanning both pre-feature and post-feature history
**Then** the `source` field flips correctly across the boundary and both segments render sane candles

### Story 16.3: Backfill historical 1-second data into the rollup

As the collector operator,
I want a standalone script to reprocess existing catalog history into `DydxMinuteRollup` rows,
So that daily/weekly charts get rollup-speed and rollup-enriched data for time ranges that predate this feature (or after a rollup schema change), without touching or risking the raw 1-second archive.

**Acceptance Criteria:**

**Given** a new `troll/dydx_collector/backfill_minute_rollup.py` CLI script (argparse), same operational category as the existing `prune_catalog.py` — manually run, not scheduled
**When** invoked for one or more instrument ids (default: every id with existing `DydxSecondSnapshot` data)
**Then** it streams raw 1-second data in day-sized, time-bounded chunks per instrument (never an unbounded load, per MEM-01)

**Given** one long-lived `MinuteRollupBuilder` instance per instrument, reused across the entire requested date range (not recreated per chunk)
**When** chunk boundaries are crossed
**Then** OFI continuity carries across them exactly as it does for the live collector's minute boundaries — no dropped first-second-of-chunk contribution — reusing `MinuteRollupBuilder` unmodified (zero duplicated aggregation logic)

**Given** emitted `DydxMinuteRollup` rows per chunk
**When** the script writes them
**Then** it uses the existing `catalog.write_data()` API (NAUT-02) — no hand-rolled Parquet schema

**Given** the catalog is append-only (no upsert)
**When** an operator re-runs the backfill over an already-backfilled range (e.g. after a schema change)
**Then** the script's own `--help`/docstring documents that `data/dydx_minute_rollup/` must be cleared first — same wipe-and-rebuild pattern `wipe_data.sh` already establishes — rather than the script silently producing overlapping/duplicate files

**Given** a cross-check requirement (this touches financial OHLCV data)
**When** this story is verified
**Then** the backfill is run against one day of existing historical 1-second data for one real instrument, and the resulting rollup's OHLCV is confirmed to match what `aggregate_ohlc` independently computes over the same raw 1-second window (open/high/low/close/volume equality, not just "didn't crash")

## Epic 17: Screener — Rankings Becomes a Tabbed Performance/Technicals Screener

Builder's `RankingsPage.tsx` (Story 15.2, one flat live table today) becomes the full screener spec'd in `spec-multi-exchange-screener-chart.md` Part B: a pinned Symbol/Name column plus Performance and Technicals tabs, sharing one historical-data path with the still-parked Story 15.8 rather than building two.

### Story 17.1: Tab shell — Performance + Technicals tabs on the Rankings page

As the dashboard operator,
I want the Rankings page to show a pinned Symbol/Name column plus switchable Performance/Technicals tabs,
So that I can see either view without losing track of which coin's row I'm looking at.

**Acceptance Criteria:**

**Given** `RankingsPage.tsx`'s existing live table (Story 15.2, `useLiveChannel.ts`-driven)
**When** the tab shell is added
**Then** the Symbol/Name column stays pinned and visible regardless of which tab is active, and switching tabs swaps only the metric columns to its right

**Given** the row set currently matched by the (not-yet-built, Story 17.6) filter panel
**When** the user switches tabs
**Then** the row set is unchanged — no refetch, no re-filter — only the displayed columns change

**Given** no grid framework is introduced (NFR11)
**When** the tab bar and column-set swap are implemented
**Then** they are built directly against the existing hand-rendered table and React state, with no new dependency added to `troll/frontend/package.json`

**Given** live updates via `useLiveChannel.ts`
**When** a live update arrives while either tab is active
**Then** the currently-displayed tab's columns update live exactly as today's single table does — no regression to live-update behavior

### Story 17.2: Unpark and complete Story 15.8 — 31-day metrics history

As the dashboard operator,
I want the parked 31-day metrics history page finished,
So that Performance's multi-window deltas (Story 17.3) have a real, shared historical-data source instead of a second implementation.

**Acceptance Criteria:**

**Given** Story 15.8's existing, fully-scoped story file (`_bmad-output/implementation-artifacts/15-8-31-day-metrics-history-page.md`), zero code written, parked `in-progress` at the user's request 2026-09-16
**When** this story resumes it
**Then** all of 15.8's original tasks are completed exactly as scoped: `GET /api/metrics/history/{symbol}` and `GET /api/metrics/nearest/{symbol}` in a new `troll/data_api/routes/metrics.py`, wrapping `ranking_engine/metrics_store.py`'s `history()`/`nearest()` unchanged

**Given** `troll/frontend/src/pages/HistoryPage.tsx` (currently the Story 15.1-era placeholder)
**When** this story replaces it
**Then** it renders one independent `lightweight-charts` tile per ranking-input metric (price, pct_1h, pct_24h, volatility, ofi, microprice, spread, volume24h) over the trailing 31 days, feeding `None` as a whitespace gap point (never interpolated, per DATA-01/AD-F6) — same gap-honesty rule as every other chart in Epic 15

**Given** `metrics_store`'s `PRIMARY KEY (ts, instrument_id)` keys every row by the full instrument_id already
**When** this story is verified against multi-exchange readiness
**Then** no `metrics_store` schema change is needed — confirmed already venue-safe

**Given** TEST-01 (this touches a real store, not a mock)
**When** this story is verified
**Then** `data_api/tests/test_metrics.py` writes real rows via `metrics_store.write()` to a temp SQLite path, including at least one row with a `None` metric column and one fully-populated row, and asserts the route's JSON reflects both faithfully

### Story 17.3: Performance tab — multi-window % change

As the dashboard operator,
I want the Performance tab to show a coin's % change across multiple lookback windows,
So that I can assess momentum without leaving the screener.

**Acceptance Criteria:**

**Given** `ranking_engine/metrics_store.py` already stores `pct_1h`/`pct_24h` per row
**When** the Performance tab's columns are built
**Then** they are read from `metrics_store` via the same query path Story 17.2 wires up — never a second, independently-maintained delta calculation

**Given** `metrics_store.write()`'s current `retain_days=31` default
**When** windows beyond 31 days (e.g. 1M/YTD/1Y from the original brief) are considered
**Then** this story explicitly decides and documents which windows are buildable today (bounded by 31-day retention) vs. which require a deliberate retention extension — not discovered as a surprise mid-implementation

**Given** each Performance cell
**When** it renders
**Then** it is colored/signed by direction (positive/negative), matching the original spec's §B3 requirement, with no other interaction

### Story 17.4: Wire the Rankings/Performance → History link

As the dashboard operator,
I want a way to reach a coin's 31-day history directly from its Rankings/Performance row,
So that I don't have to type the `/history/:iid` URL by hand.

**Acceptance Criteria:**

**Given** `RankingsPage.tsx`'s row click today only calls `navigate(/chart/${row.instrument_id})`, and `/history/:iid` is registered in `App.tsx` but nothing links to it
**When** this story adds the link
**Then** a row-level affordance (e.g. a small history icon/button) on the Performance tab navigates to `/history/:iid`, without changing the existing row-click-to-chart behavior

### Story 17.5: Technicals tab — user-managed indicator columns

As the dashboard operator,
I want to add, configure, remove, and reorder indicator columns on the Technicals tab,
So that I can screen coins on any of the chart's existing indicators without leaving the table.

**Acceptance Criteria:**

**Given** the existing 37-entry indicator catalog (`troll/ml_signals/chart_indicators.py`'s `INDICATOR_CATALOG`, 34 entries; `custom_indicators.py`'s `CUSTOM_INDICATOR_CATALOG`, 3 entries) and `IndicatorPicker.tsx` (built for the chart, Story 15.6)
**When** the Technicals tab's "Edit columns" control opens
**Then** it reuses the exact same catalog and component — no second, curated indicator catalog, no calculation reimplementation

**Given** a catalog entry is clicked
**When** it is added
**Then** it appears immediately as one or more new columns with default parameters — no confirm step — multi-value entries (MACD, Bollinger Bands, Keltner Channel, Donchian Channel, Ichimoku Cloud, Directional Movement, etc.) add as a group of adjacent columns under one shared header

**Given** an added column's gear icon
**When** its settings are changed
**Then** that column recalculates for every row immediately

**Given** an added column
**When** the user clicks its × or drags its header
**Then** it is removed or reordered respectively — a per-user view preference only, never touching underlying data

**Given** NFR11 (no grid framework)
**When** add/configure/remove/reorder is implemented
**Then** it is built directly against React state and the existing hand-rendered table, matching Story 17.1's approach

### Story 17.6: Filter panel

As the dashboard operator,
I want to filter the screener's row set by any base field or any added Technicals column,
So that I can narrow to coins matching a specific condition (e.g. "RSI < 30").

**Acceptance Criteria:**

**Given** a `+` control opening a condition builder (`<field> <operator> <value>`)
**When** a Technicals column has been added (Story 17.5)
**Then** that column becomes available as a filterable field, matching TradingView's own "filters and columns share one metric set" behavior

**Given** multiple filter conditions
**When** more than one is active
**Then** they combine with AND only — no OR/grouped logic for this story

**Given** the currently-active tab (Performance or Technicals)
**When** a filter is applied
**Then** it narrows the row set regardless of which tab is displayed — filtering and column display stay independent, per Story 17.1

## Epic 18: Chart — Drawing Tools, Bar Replay, Volume Profile, Placement Pass

Builder gets the chart features `spec-multi-exchange-screener-chart.md` Part A specifies but Epic 15 never scoped: drawing tools (§A3), Bar Replay (§A5), the full Volume Profile family (§A7), and a placement/operation-parity pass (§A8) — all built against the existing `LightweightChart.tsx`/`ChartPage.tsx` chart instance, not a new chart setup.

### Story 18.1: Horizontal line drawing tool

As a chart user,
I want to click once to place a draggable horizontal price line,
So that I can mark a price level of interest.

**Acceptance Criteria:**

**Given** the left toolbar's horizontal-line tool is selected
**When** the user clicks once on the chart
**Then** `series.createPriceLine({ price, color, lineWidth, axisLabelVisible: true, title })` places a line at that price — native lightweight-charts support, no custom Primitive needed

**Given** a placed horizontal line
**When** the user drags it
**Then** its `price` updates live to track the drag

**Given** any active drawing tool
**When** the user presses `Esc`
**Then** the in-progress tool action cancels and the cursor returns to select/cursor mode

### Story 18.2: Trendline drawing tool

As a chart user,
I want to click-drag a line between two points on the price/time plane,
So that I can mark a trend.

**Acceptance Criteria:**

**Given** the left toolbar's line (trendline) tool is selected
**When** the user click-drags between two points
**Then** a custom Primitive holding two `{time, price}` anchors is created and rendered

**Given** an existing trendline Primitive
**When** the chart is panned, zoomed, or the crosshair moves
**Then** the Primitive redraws correctly via `updateAllViews`, staying anchored to its original `{time, price}` points

### Story 18.3: Measurement tool

As a chart user,
I want to click-drag a rectangle across two points and see price delta, bar count, and volume sum,
So that I can quickly measure a move without manual calculation.

**Acceptance Criteria:**

**Given** the left toolbar's measurement tool is selected
**When** the user click-drags a rectangle across the main pane
**Then** a custom Primitive (no native lightweight-charts equivalent) overlays a label showing price delta (absolute + %) and the number of bars/time spanned

**Given** the same click-drag selection spans the volume pane
**When** the label renders
**Then** it additionally shows summed volume across the selected bars

### Story 18.4: Bar Replay

As a chart user,
I want to pick a start bar and replay the chart bar-by-bar,
So that I can review how price action unfolded without seeing future bars.

**Acceptance Criteria:**

**Given** the top toolbar's Replay button
**When** clicked
**Then** the chart enters "pick a start bar" mode (crosshair + vertical guide line following the cursor)

**Given** the user clicks a candle in picker mode
**When** the start point is set
**Then** a vertical marker line is drawn at that bar, and the dataset fed to `setData()` is sliced to that start index — reusing the existing historical/live-bar split from Story 15.5's live-candle-edge work, not a second split

**Given** the replay control bar (Play/Pause, Step-back, Step-forward, speed selector, "Go to…", Exit)
**When** Play is active
**Then** one additional bar is revealed at a fixed interval scaled by the speed setting (base interval ÷ speed); Step-forward/back move exactly one bar and pause autoplay if running

**Given** drawing tools (Stories 18.1–18.3) and indicators are active during replay
**When** bars are revealed
**Then** both keep working and recalculating live — not special-cased out

**Given** Exit is clicked
**When** replay ends
**Then** the full dataset is restored and the control bar/vertical marker are removed

### Story 18.5: Volume Profile — shared engine and rendering Primitive

As a chart user,
I want one consistent Volume Profile calculation and rendering behind every variant,
So that Fixed Range, Visible Range, Session, Session HD, and Periodic profiles behave predictably and share bug fixes.

**Acceptance Criteria:**

**Given** `buildVolumeProfile(candles, rowCount, valueAreaPct)` (§A7.0: min/max price bucketing, up/down volume classification, POC = highest-volume bucket, Value Area accumulation from POC outward)
**When** implemented
**Then** it is one pure function consumed by all five variants, never duplicated per variant

**Given** a single `VolumeProfilePrimitive` (§A7.1)
**When** it renders a `VolumeProfile` object
**Then** it draws a horizontal histogram (up/down-colored segments per row), highlights the POC row distinctly, and shades the Value Area band — parameterized by x-anchor/width per variant, never a separate rendering implementation per variant

**Given** §A7.5's backend confirmation
**When** this story is implemented
**Then** it uses only `GET /api/candles/{instrument_id}` (`troll/data_api/routes/candles.py`) — no new backend route, no raw-snapshot/tick data — and verifies in practice that `_MAX_CANDLES_LIMIT`/`_MAX_QUERY_SPAN_SECONDS` don't cut off the range a typical Fixed Range selection needs (raising them if so)

### Story 18.6: Fixed Range Volume Profile (FRVP)

As a chart user,
I want to click-drag between two timestamps and see a persistent volume profile for that exact range,
So that I can analyze a specific historical move.

**Acceptance Criteria:**

**Given** the left toolbar (drawing-tool placement, per §A7.2 — not the Indicators dialog)
**When** the user click-drags between two points
**Then** `buildVolumeProfile` runs once over the candles between those two timestamps, on drag-release

**Given** a placed FRVP
**When** the user drags an edge to resize it
**Then** it recomputes — otherwise it stays static (confirm-once model, distinct from VRVP's always-recompute model per §A7.4)

### Story 18.7: Visible Range Volume Profile (VRVP)

As a chart user,
I want a volume profile that always reflects whatever's currently visible,
So that I get an at-a-glance profile without manually selecting a range.

**Acceptance Criteria:**

**Given** the Indicators dialog (§A4.1), `overlay: true`
**When** VRVP is added
**Then** it renders on the main price pane using `buildVolumeProfile` over the currently-visible candle range

**Given** `chart.timeScale().subscribeVisibleTimeRangeChange()`
**When** the user pans or zooms
**Then** the profile rebuilds against the new visible range — never sharing a "live" component with FRVP's confirm-once model (§A7.4)

### Story 18.8: Session Volume Profile and Session Volume Profile HD

As a chart user,
I want a volume profile computed per calendar session (day), with a higher-resolution variant available,
So that I can compare volume distribution session-over-session.

**Acceptance Criteria:**

**Given** candles grouped by calendar day using the base/finest timeframe data regardless of the chart's current timeframe
**When** SVP is added (Indicators dialog, `overlay: true`)
**Then** one profile per day is computed, recomputed once per session boundary, with only the current in-progress session's profile updating as new bars arrive

**Given** SVP HD is a config preset of the *same* component as SVP (not a separate code path)
**When** HD is selected
**Then** it uses a higher default `rowCount` (100+ vs SVP's ~24) and a `respondsToZoom: true` flag that redraws (not recomputes) the Primitive on zoom level changes

**Given** the settings panel (§A7.3: row size, value area %, up/down colors, POC/Value-Area visibility toggles, number of past sessions to render)
**When** "show last N sessions" is set
**Then** each rendered session keeps its own independent POC/VAH/VAL — never merged into one

### Story 18.9: Periodic Volume Profile (PVP)

As a chart user,
I want a volume profile grouped by a recurring period I choose (weekly, 4-hourly, monthly),
So that I can see volume distribution over a period longer or shorter than one session.

**Acceptance Criteria:**

**Given** the Indicators dialog, `overlay: true`, with a `period: 'daily' | 'weekly' | '4h' | 'monthly'` settings field
**When** PVP is added
**Then** candles are grouped by the chosen recurring period and a profile is computed per period, recomputed on each period boundary — same trigger pattern as Story 18.8's SVP, not a separate scheduling mechanism

### Story 18.10: Placement and operation-parity pass

As a chart user,
I want every tool in the same relative slot/group TradingView uses, and every interaction to behave the same way,
So that the chart feels familiar even with a fully custom visual style.

**Acceptance Criteria:**

**Given** §A8.1's top-toolbar clusters ([symbol+timeframe] [chart type] [indicators+fit+jump] [theme]) and the real app's table-first navigation (Rankings row → `/chart/:iid`, fixed `BAR_SECONDS`)
**When** the top toolbar is built
**Then** the symbol slot is a read-only label + back-to-Rankings link (not a free picker), a real timeframe selector is added only if multi-timeframe viewing is explicitly wanted, and no theme toggle is added (Story 15.9 already fixed the visual identity deliberately)

**Given** §A8.1's left-toolbar clusters (cursor/crosshair, then the three drawing tools in order)
**When** the left toolbar is built
**Then** it matches that relative order and grouping

**Given** §A8.2's full operation checklist (pan/zoom/fit/jump/crosshair-readout/pane-resize/indicator add-configure-toggle-remove/drawing-tool click-drag/Esc-cancel/replay entry-step-goto/alert creation)
**When** this story is verified
**Then** every listed interaction is checked against the real, live app — not a screenshot/visual comparison — before this epic is called done

## Epic 19: Multi-Exchange Support — Bybit and Hyperliquid

Builder's catalog, `data_api`, and screener stop being dYdX-only, per `spec-multi-exchange-screener-chart.md` Part D. New collectors mirror `dydx_collector`'s own direct-asyncio-PyO3 architecture — never `TradingNode`/`DataEngine` (FORK-02) — not the `TradingNode`-based `scripts/bybit_recorder/` on the `gg` branch.

### Story 19.1: Explicit `venue` field in the data model

As a frontend developer,
I want `venue` surfaced as its own field rather than an implicit `instrument_id` suffix,
So that the screener and chart can filter/group/label by exchange without string-parsing IDs.

**Acceptance Criteria:**

**Given** Nautilus's `InstrumentId` = `"{SYMBOL}.{VENUE}"` convention (`crates/model/src/identifiers/instrument_id.rs`, `rsplit_once('.')`)
**When** this story adds an explicit `venue` field
**Then** it appears in `troll/frontend/src/api/schema.ts` and every `data_api` response shape that already includes an `instrument_id` (`routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`, `routes/indicator_series.py`, `routes/rankings.py`)

**Given** the existing dYdX-only data
**When** this story ships
**Then** every existing `.DYDX` instrument's `venue` field reads `"DYDX"` — no behavior change for existing data, purely additive

### Story 19.2: Consolidate `data_api`'s `CATALOG_PATH` into one shared setting

As a backend developer,
I want one `CATALOG_PATH` source instead of six independently-duplicated defaults,
So that adding a second collector's catalog doesn't require editing six files in lockstep.

**Acceptance Criteria:**

**Given** `CATALOG_PATH = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")` independently redeclared in `app.py`, `routes/candles.py`, `routes/snapshots.py`, `routes/indicators.py`, `routes/indicator_series.py` (each to avoid a circular import from `app.py`, per each file's own comment)
**When** this story consolidates them
**Then** all five (six including any other route module) resolve from one shared setting, without reintroducing the circular-import problem each file's comment originally avoided

**Given** `catalog/data/<data_type>/<instrument_id>/` already partitions by the full `SYMBOL.VENUE` id
**When** a second collector's output (Story 19.3) writes into the same catalog root
**Then** its data is served by the existing routes with zero additional code — confirmed via a real Bybit or Hyperliquid instrument once Story 19.3/19.4 lands

### Story 19.3: `troll/bybit_collector/`

As the platform operator,
I want a Bybit market-data collector with the same reliability properties as the dYdX collector,
So that Bybit data lands in the same catalog without inheriting Nautilus's live-runtime OOM/wedge bug.

**Acceptance Criteria:**

**Given** `BybitHttpClient`/`BybitWebSocketClient` (`nautilus_trader/core/nautilus_pyo3.pyi`, backed by `crates/adapters/bybit/`)
**When** `troll/bybit_collector/` is built
**Then** it owns its own asyncio loop, buffer, and flush timer calling these PyO3 clients directly — never instantiating `TradingNode`/`DataEngine` (FORK-02), structured as a sibling of `troll/dydx_collector/`, not a shared base class with it

**Given** dYdX's `_at_fixed_precision()` workaround exists because of a dYdX-specific wire-format quirk (mark/index price precision derived from trailing-zero count)
**When** Bybit's own wire format is implemented
**Then** its precision handling is derived from Bybit's actual wire format, not assumed to need the same workaround

**Given** `ParquetDataCatalog.write_data()` (NAUT-02)
**When** the collector writes data
**Then** all writes go through this API — no hand-rolled Parquet schema — writing `SYMBOL.BYBIT`-suffixed instrument ids into the shared catalog root from Story 19.2

**Given** TEST-01 (financial calculations)
**When** this story is verified
**Then** any precision re-stamping logic has tests using real `Price`/`Quantity` objects, never mocked

### Story 19.4: `troll/hyperliquid_collector/`

As the platform operator,
I want a Hyperliquid market-data collector with the same architecture as the Bybit and dYdX collectors,
So that Hyperliquid data lands in the same catalog consistently.

**Acceptance Criteria:**

**Given** `HyperliquidHttpClient`/`HyperliquidWebSocketClient` (`nautilus_trader/core/nautilus_pyo3.pyi`, backed by `crates/adapters/hyperliquid/`)
**When** `troll/hyperliquid_collector/` is built
**Then** it follows the exact same direct-asyncio-PyO3 pattern as Story 19.3's Bybit collector — a sibling, not a shared base class imposed before real duplication across all three collectors is visible

**Given** Hyperliquid's own wire format
**When** precision/re-stamping is implemented
**Then** it is derived from Hyperliquid's actual wire format, not assumed identical to dYdX's or Bybit's

**Given** common helpers across all three collectors become visible only once this story lands (e.g. open-interest-poll shape, second-snapshot schema)
**When** this story is complete
**Then** any genuine duplication found is noted for a future extraction — not extracted speculatively as part of this story (DESIGN-01)

### Story 19.5: Rankings/screener venue column and filter

As the dashboard operator,
I want to see and filter by exchange in the screener,
So that I can distinguish a coin's dYdX row from its Bybit or Hyperliquid row.

**Acceptance Criteria:**

**Given** Story 19.1's explicit `venue` field
**When** the Rankings/screener table renders
**Then** it shows a venue column, and the existing filter panel (Story 17.6) accepts venue as a filterable field

### Story 19.6: CEX/DEX registry

As the dashboard operator,
I want to know whether a venue is a CEX or a DEX,
So that I can filter or reason about counterparty/custody risk differences.

**Acceptance Criteria:**

**Given** Nautilus has no usable native flag for this (`Venue.is_dex()` only fires on a `Chain:DexType`-formatted string behind the `defi` feature; none of dYdX's/Bybit's/Hyperliquid's adapter constants use that format)
**When** this story is built
**Then** a small, self-maintained `troll/common/venues.py` registry (plain dict, not a class hierarchy) maps `{"DYDX": {"kind": "dex"}, "HYPERLIQUID": {"kind": "dex"}, "BYBIT": {"kind": "cex"}}`

**Given** the registry
**When** the screener is extended to use it
**Then** it powers a CEX/DEX label or filter option, confirmed against all three real venues (dYdX and Hyperliquid both DEX, Bybit CEX)

## Epic 20: Alerts — Webhook Delivery (deferred, built last)

Builder can define a price/indicator condition and get a webhook POST plus an in-app toast when it fires. Deliberately sequenced after Epics 17–19 are done — this epic has no dependency the earlier epics need, and is a backend-first addition per the user's explicit deferral.

### Story 20.1: Alert creation dialog

As a chart user,
I want to define an alert condition, frequency, expiration, message template, and webhook URL,
So that I can be notified when a price or drawing-line condition is met.

**Acceptance Criteria:**

**Given** a clock/bell icon opening a "Create Alert" dialog
**When** the user builds a condition
**Then** MVP supports price crossing a static value, and price crossing a horizontal-line drawing (Story 18.1)

**Given** the dialog's frequency, expiration, message template (`{{ticker}}`, `{{close}}`, `{{time}}`, `{{interval}}` placeholders), and Webhook URL fields
**When** the user saves
**Then** all fields are persisted with the alert, with no validation on the Webhook URL beyond "looks like a URL"

### Story 20.2: Local alert evaluation engine

As a chart user,
I want my saved alerts evaluated automatically against live data,
So that I don't have to watch the chart myself.

**Acceptance Criteria:**

**Given** `troll/data_api`'s existing live Redis-subscriber loop (`ws/live.py`, `redis_bus.py`)
**When** the alert engine is built
**Then** it evaluates active alert conditions as a consumer of that same live feed — not a second, independent polling loop

**Given** a condition fires
**When** it matches its configured frequency (once per bar close / once per bar / only once) and hasn't expired
**Then** a `fetch(POST)` is sent to its Webhook URL with the templated JSON body, and an in-app toast/notification is shown

### Story 20.3: Alerts list view

As a chart user,
I want to see all my alerts and their status,
So that I can review or delete them.

**Acceptance Criteria:**

**Given** saved alerts (Story 20.1) and firing history (Story 20.2)
**When** the Alerts list view is opened
**Then** each alert shows its condition, status (active/triggered/expired), and a delete button — a simple list, not a full manager UI

