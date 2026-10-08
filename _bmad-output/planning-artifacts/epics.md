---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/DESIGN.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/EXPERIENCE.md
  - _bmad-output/planning-artifacts/prds/prd-chart-frontend-rewrite-2026-09-13/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/ddd-redesign-seed-2026-09-21.md
---

> **Renamed 2026-09-21:** `troll/` is now `platform/` and every durable store moved under `platform/data/` (DDD spine AD-D13, `_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/`). Paths below are as written at the time; read `troll/` as `platform/`.

# nautilus_trader_fork - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for nautilus_trader_fork (the `troll/` dYdX Signal Research & Trading Platform), decomposing the requirements from the PRD and Architecture spine into implementable stories. Epics 1–3 (below) cover the original PRD/architecture scope (FR1–FR15) and were fully implemented as of 2026-07-17. This document was reopened on 2026-07-24 to extend coverage for the PRD's volatility Ranking Mode (FR-16) and new Bot Monitoring TUI feature (FR-17–FR-25), backed by a finalized UX design contract (`DESIGN.md`/`EXPERIENCE.md`) — the project's first UX-driven surface. Reopened again on 2026-09-06 to add Epic 8 (FR28–FR30): TradingView-style multi-chart navigation and selectable technical indicators on the web dashboard's coin chart. Numbered Epic 8 (not 5) because epics 5–7 were filed directly as standalone stories bypassing epics.md ceremony (see `sprint-status.yaml`) — Epic 8 continues that same global epic-number sequence to avoid collision with their story-file paths (`5-1-*`, `6-1-*`, `7-1-*`). No PRD/Architecture update precedes this addition (same precedent as FR27); PM should fold FR28–FR30 into the PRD proper once shipped. (FR31, a combined candlestick + bid/ask overlay, was drafted alongside these but the story implementing it — 8.5 — was dropped before dev started; FR31 removed with it — its number is reused below.) Reopened again on 2026-09-08 to add Epic 10 (FR31–FR33): a second, non-Nautilus indicator category on the chart page's picker (Story 8.4), migrating three of the chart page's fixed microstructure-panel rows (OFI, Cancel Pressure, CVD) into it, plus persisted per-instrument indicator configuration. Numbered 10 (not 9) for the same reason Epic 8 skipped 5–7: Epic 9 is itself a standalone bypass-epic bug-fix story (`9-1-fix-oscillator-panel-shared-y-axis-scaling`, see `sprint-status.yaml`), not a real epics.md entry — Epic 10 continues the sequence past its file-path prefix (`9-1-*`). Reopened again on 2026-09-12 to add Epic 12 (FR34–FR35: run dashboard/bot_tui on the user's own machine via a new read-only data API, offloading load from the oversubscribed nifelheim VPS) and Epic 13 (FR36–FR37: stop ranking_engine's recurring Parquet-read memory spike, the mechanism behind its OOM-restart loop). Numbered 12 (not 9) for the same reason Epic 10 was: Epic 11 is itself a standalone bypass-epic bug-fix story (`11-1-fix-empty-imbalance-depth-spread-chart-panes`, see `sprint-status.yaml`), not a real epics.md entry. No PRD/Architecture update precedes this addition (same precedent as FR27–FR33); PM should fold FR34–FR37 into the PRD proper once shipped. Created via direct technical investigation this session (root-caused against real code, real measurements, and an already-logged production incident) rather than the standard PRD-first elicitation flow, at the user's explicit request — same precedent as Epic 11. Reopened again on 2026-09-14 to add Epic 15 (FR38–FR46, NFR6–NFR9): a full rewrite of `troll/ml_signals/dashboard.py` into a React/TypeScript SPA served by an expanded `troll/data_api`, backed this time by a proper PRD (`prds/prd-chart-frontend-rewrite-2026-09-13/prd.md`) and architecture spine (`architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md`), both status `final`. This epic supersedes `epic-14` (a bypass-epic never entered into this document — 14.1/14.2 done, 14.3 becomes moot once the chart page is deleted under this epic's AD-F1 and should be marked superseded in `sprint-status.yaml`, not shipped). Reopened again on 2026-09-14 to add Epic 16 (FR47–FR50, NFR10): an incremental 1-minute rollup cache (`DydxMinuteRollup`), maintained by the collector as new `DydxSecondSnapshot` rows stream in, so wide-window (daily/weekly) candle requests stop rescanning the full raw 1-second archive. Backend/collector-pipeline scope, independent of Epic 15's dashboard rewrite — Epic 15's future `/api/candles` story will depend on this epic's output, but this isn't "replace dashboard.py with React." No PRD/Architecture update precedes this addition; created via direct technical investigation this session (real code read, real read-cost scaling estimated), same precedent as Epic 12/13.

Reopened again on 2026-09-17 to add Epic 17 (FR51–FR56), Epic 18 (FR57–FR60), Epic 19 (FR61–FR66), and Epic 20 (FR67–FR69, deferred): the next phase of Epic 15's chart+screener rewrite. Epic 17 evolves `RankingsPage.tsx` (Story 15.2) into the full tabbed screener (Performance + Technicals tabs), unparking Story 15.8 (31-day metrics history, parked in-progress since 2026-09-16) as part of wiring Performance's multi-window % change and the Rankings→History link. Epic 18 builds the chart features Epic 15 never scoped — drawing tools, Bar Replay, the full Volume Profile family, and a placement/operation-parity pass. Epic 19 adds Bybit and Hyperliquid alongside dYdX, mirroring `dydx_collector`'s direct-asyncio-PyO3 architecture (never `TradingNode`/`DataEngine`, per FORK-02) rather than the `TradingNode`-based `scripts/bybit_recorder/` on the `gg` branch. Epic 20 (Alerts/webhook delivery) is deliberately sequenced last, after Epics 17–19 ship. No PRD/Architecture update precedes this addition — created from a jointly-revised build spec (`_bmad-output/planning-artifacts/spec-multi-exchange-screener-chart.md`, itself a revision of an external TradingView-clone brief against this codebase, confirmed file-by-file this session), same "direct technical investigation" precedent as Epics 12/13/16.

Reopened again on 2026-09-22 to add Epic 27 (FR70–FR79, NFR12): the research notebooks rebuilt on a shared analysis layer inside the `research/` context that Story 24.4 creates — typed analysis value objects and ports (`research/domain`, `research/application`), six executable jupytext-paired notebooks (catalog inspection, microstructure, correlation and cross-venue, backtest evaluation with sweeps and walk-forward, Monte Carlo and robustness, candlestick scanner), a candlestick pattern detector as a streaming `Indicator` in `kernel/` shared by the chart picker, the screener's Technicals tab, backtests and `live_paper`, and a `CandlePatternStrategy` that makes those patterns tradeable. Numbered 27 after the DDD migration epics; depends on Story 24.4 only. No PRD/Architecture update precedes this addition — created via direct investigation of the three existing notebooks (all three stale: hard-coded `ml_signals` paths, a `../catalog` relative path that predates `platform/data/`, a runtime `pip install` of TA-Lib/`pandas_ta` against the retired `custom_dydx_minute_bar` directory), same precedent as Epics 12/13/16; Story 27.1 amends the DDD spine's AD-D1 research row and 27.7 its AD-D3 kernel list.

Reopened again on 2026-10-05 to add Epic 33 (thirteen stories): the operator's feature review of the frontend and backend against TradingView and against the metrics a perpetuals trader uses. Verified this session: mark/index/funding/open interest are captured and nightly-proven yet reach no route, pane, column or alert; the candle store drops the per-second buy/sell split; liquidations exist nowhere; several read models and hooks have no caller. Bybit liquidations arrive on the public `allLiquidation` topic the Rust handler drops as unknown, so they are read over Nautilus's generic `nautilus_pyo3.WebSocketClient` from Python; Hyperliquid has no market-wide liquidation feed (a logged-in user sees only its own), so Story 33.2 opens with a wire investigation whose result decides the design. No PRD/Architecture update precedes this addition -- same "direct technical investigation" precedent as Epics 12/13/16/32; stories 33.5 onward run after `epic-32` is merged.

Reopened again on 2026-09-28 to add Epic 31: trade-grade data verification for Bybit and Hyperliquid (dYdX out of scope) — an independent reference recorder sharing no code with capture, id-by-id trade, book, mark/index/funding/OI and instrument comparisons, catalog and backtest-read parity, candle/kline pass rates, independent reference implementations of every derived signal, live-vs-backtest bot signal parity, fault injection with a conservation report, and a permanent nightly `verify_day` gate. Created from a direct code survey (same precedent as Epics 27/28/30); runs on branch `verify/data-correctness`.

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

FR70 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Research analysis layer — `research/domain` holds typed analysis values with named invariants (`ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, `MonteCarloResult`) and `research/application` holds the ports (`MarketFrames`, `RankingHistory`, `BacktestRunner`) through which every notebook and backtest report reads data and runs `BacktestNode`; every portfolio statistic is `performance_metrics` (Nautilus `PortfolioStatistic`) and no notebook cell carries analysis logic.

FR71 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Executable, version-controlled notebooks — every research notebook is a jupytext-paired percent-format `.py` plus an output-stripped `.ipynb`, parameterised by environment variables, and `make test` executes each against a synthetic fixture catalog with warnings as errors.

FR72 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Catalog inspection notebook — per venue and instrument: inventory, coverage and gaps, outages, verified/provisional days, raw-trade-versus-folded-seconds agreement, snapshot sanity checks and ledger rejection counts over a bounded window.

FR73 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Microstructure notebook — spread, depth profile, OBI/OFI and z-scores, microprice predictiveness, trade flow and CVD, price impact, funding/basis/open-interest overlays, return autocorrelation by horizon, volatility signature and realised volatility, all through `kernel.indicators`.

FR74 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Correlation and cross-venue notebook — return correlation matrices at several horizons, rolling correlation, numpy-only clustering, funding and open-interest correlation, and for symbols collected on more than one venue the basis, the lead-lag cross-correlation and the volume share.

FR75 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Backtest evaluation notebook — any strategy by string path over a bounded window: equity and drawdown episodes, the full `MetricReport`, rolling Sharpe, trade distributions, a parameter-sweep heatmap on `BacktestNode` multi-config runs and a walk-forward in/out-of-sample split.

FR76 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Monte Carlo and robustness notebook — seeded trade-order bootstrap and block bootstrap of returns, drawdown and terminal-wealth distributions, risk of ruin, a Sharpe confidence interval, probabilistic and deflated Sharpe for sweep-selected parameters, hand-rolled on numpy.

FR77 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Candlestick pattern detection — the classic single-, two- and three-bar patterns as one streaming `Indicator` in `kernel/` (no TA-Lib), registered in the chart picker's native catalog and offered as a screener Technicals column (a universe-wide pattern scanner), plus a scanner notebook with multi-timeframe hits, EMA filter, hit chart and forward-return statistics.

FR78 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Candlestick patterns tradeable — `CandlePatternStrategy` (config + strategy, string-path importable) trades pattern signals with a trend filter and defined exits on the same detector, runs on `BacktestNode`, is evaluated by the FR75 notebook and is startable as a `live_paper` bot by config.

FR79 `[NEW — 2026-09-22, not yet in PRD, PM should fold in]`: Research documentation and rules — `research/README.md` with the notebook index and the write-a-notebook recipe, `platform/CLAUDE.md` NB-01..NB-04, every legacy notebook and runtime `pip install` cell gone.

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

NFR12 `[NEW — 2026-09-22]`: No new dependency for the research epic (Epic 27) — numpy, pandas, pyarrow, plotly and jupytext are already in `uv.lock` and are the whole toolkit; scipy, statsmodels, matplotlib, seaborn, ipywidgets, nbclient/papermill, TA-Lib and `pandas_ta` are explicitly rejected (Monte Carlo, bootstrap, correlation, clustering and candlestick recognition are hand-rolled on numpy and unit-tested against closed-form cases); Jupyter itself stays a personal tool launched locally, never a compose service; every notebook read is time-bounded (NFR3).

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

- **Research context shape (Epic 27, amends DDD spine AD-D1 research row):** `research/` stays a consumer (reads the catalog, `metrics.db`, `/api/rankings` over HTTP; never imports `data_api`, `capture`/`collector_core` or `views` internals beyond the read functions the spine allows) but gains `domain/` (numpy-only analysis value objects with named invariants) and `application/` (`typing.Protocol` ports + services over `kernel.catalog_files`, `candles.application`, the ranking query service and `BacktestNode`). Notebooks are interface adapters: parameters cell, calls, figures, prose. The candlestick pattern detector lives in `kernel/candle_patterns.py` (amends AD-D3/MR5) because views, research and bots all consume it.
- **Notebook execution in CI without a notebook runner (Epic 27):** the jupytext `.py` twin is the source of truth and is executed with `runpy` inside pytest against a fixture catalog built with `ParquetDataCatalog.write_data()`; `.ipynb` files are output-stripped artefacts for the user's local Jupyter. No `nbclient`/`papermill` and no Jupyter kernel in the collector image.
- **`live_paper` strategy selection by string path (Epic 27, Story 27.8):** a bot's strategy is chosen by `BotConfig.strategy` and built through Nautilus's `StrategyFactory.create(ImportableStrategyConfig)`, the same mechanism `BacktestNode` uses, so `live_paper` gains no import edge to `research`; `test_images.py` needs an explicit entry because its `ast` walk cannot see a string path.

### UX Design Requirements

_Extracted from the finalized UX design contract at `_bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/` (`DESIGN.md` + `EXPERIENCE.md`, both `status: final`) — the project's first UX-driven surface (the existing web dashboard predates any UX design contract; the Bot Monitoring TUI is what prompted creating one)._

UX-DR1: Terminal color/typography system — inherit the terminal's own default fg/bg as the base (no fixed hex palette); four semantic ANSI-family accent tokens (attention-stale=yellow, attention-critical=red, attention-positive/negative=green/red, attention-neutral) used exclusively for attention states, never decoratively; monospace-only throughout, no size-based emphasis (bold/standout is a secondary reinforcing channel only, never a substitute for color).
UX-DR2: k9s-style navigation shell — breadcrumb header (row 0, plain/uncolored), footer hint bar (replaced by command bar on activation), `:` command bar with a defined vocabulary (`:coins`, `:bots`, `:q`) and an explicit "unknown command" echo state (never a silent no-op), `esc` pop-back-exactly-one-level semantics that never exits the program.
UX-DR3: Coins pane — list row component (rank, instrument ID, active Ranking Mode's score column, per-row stale badge on feed loss), `/` fuzzy-filter scoped to this pane only (substring match against instrument ID, `no matches` empty state, `esc` clears without leaving the pane), `m` keybinding toggling Ranking Mode (volume ↔ volatility).
UX-DR4: Bots pane — list row component (bot_id, sign-colored PnL, strategy/symbol, mode, position/exposure, uptime/last-heartbeat, win-rate-to-date), independent per-row stale badge, `s` start/stop toggle with a one-line footer-echo confirmation and no optimistic local state change (row waits for `bots:status` to confirm).
UX-DR5: Coin-detail full-screen view — live indicators region (Microprice/OFI/OBI/spread via the shared `ml_signals.indicators` code path) plus an order-book depth ladder that opens collapsed to top-of-book by default and toggles via `d` to full 20-level depth; graceful thin-book rendering (no padding rows, `no bids`/`no asks` text); `o` deep-links to the web dashboard at the same coin + time context.
UX-DR6: Bot-detail full-screen view — three bordered regions (live snapshot header; trades blotter; PnL-over-time sparkline with a day/week/month/all preset toggle via `t`, never free-form scrubbing); `o` deep-links to the dashboard's fuller view `[amended 2026-10-07: DW-93/DW-216 — `o` removed, the web app has no bot view; re-adding it is a future story once it does]`; each of regions 2–3 independently renders `history unavailable` if the Cache-history read surface is unreachable, without affecting region 1.
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

FR70: Epic 27 - `research/domain` analysis value objects and `research/application` ports
FR71: Epic 27 - Executable jupytext-paired notebooks run by `make test` against a fixture catalog
FR72: Epic 27 - Catalog inspection notebook
FR73: Epic 27 - Microstructure notebook
FR74: Epic 27 - Correlation and cross-venue notebook
FR75: Epic 27 - Backtest evaluation notebook with sweeps and walk-forward
FR76: Epic 27 - Monte Carlo and robustness notebook
FR77: Epic 27 - Candlestick pattern detector in `kernel/`, chart picker, screener column and scanner notebook
FR78: Epic 27 - `CandlePatternStrategy` in backtests and `live_paper`
FR79: Epic 27 - Research README, notebook index, NB-01..NB-04 rules, legacy notebooks retired

### DDD Migration Requirements (2026-09-21, from the DDD spine AD-D1..AD-D18)

Extracted for Epics 23+ from `architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`. Step 0 (rename `troll/` → `platform/`, stores under `platform/data/`) is done (`18c12eedf4`).

- MR1: One bounded context moves per story, in AD-D12's order (observability → kernel → candles → views → alerting → research → archive → ranking → bots → collection_control → capture). Every story is deployable alone: Parquet schemas and catalog directory names, every Redis payload, the SQLite and TOML store schemas, compose service names, env vars and the `platform/data/` bind mounts are frozen for the whole migration.
- MR2: Every move leaves a pure re-export shim at the old import path (`from <new> import <names>` + `DeprecationWarning` + `REMOVE_AFTER = "<story key>"`); a shim is deleted no later than two stories after it appears; `test_namespace.py` asserts `old.X is new.X` and exactly one Arrow registration per kernel class.
- MR3: The first story ships the enforcement: `platform/tests/test_boundaries.py` (AD-D2 graph judged by target contexts via a legacy-module map, cross-row private-name ban), `platform/tests/test_images.py` (every compose/Makefile entrypoint's import closure ⊆ its dockerfile `COPY` set), the hot-path baseline (`tracemalloc` allocations + wall time per message, `platform/tests/fixtures/hotpath_baseline.json`), and closes the live `data_api`/`live_paper` image gaps.
- MR4: Each move updates, in the same commit: `platform/CLAUDE.md` citations, `ARCHITECTURE.md`, `docs/DATA_DICTIONARY.md`, the three dockerfiles' `COPY` sets, compose `command:` lines and both Makefile test lists.
- MR5: `kernel/` membership is exactly AD-D3: `DydxSecondSnapshot` + `SecondOHLC`, `OpenInterest`, `fold_trades`, `indicators`, `performance_metrics`, `venues` (the only `InstrumentId` parser, incl. `bybit_category`), `clocks` (`CatalogFileSpan`, single `MAX_TS_INIT_SKEW_NS`), `archive_markers` (`ArchiveGap`), `venue_http` (every venue REST request), `catalog_files` (read helpers), `parquet_compat` (one zstd patch), `candle_patterns` (`CandlePattern`, `CandlePatternSet`) `[amended 2026-09-28: Story 27.7 — one pattern definition, shared by views, research and bots]`. Kernel imports nothing from any context; a kernel change ships with every consumer's tests.
- MR6: Candles precede views: exactly two folds (`fold_trades`, `fold_arrays`); `forming_bar` is a candles query service; `SecondSink.apply` receives the flushed batch; the candle prune loop becomes a candles process manager; `CandleStore` is the only rw opener of `candles_<venue>.db`; archive gets a `VerifiedDays` port.
- MR7: `alerting` consumes the forming bar only as a `BarObserver` implementation wired in `data_api`'s composition root; `observability.notify` is the one outbound transport and `alerting.Deliverer` is implemented over it; `views` owns the two UI preference TOMLs and is the only place both UIs' values are computed.
- MR8: Archive: `ArchiveDay` machine with `verified_days` as the only day-status store, `reconcile_day` only after a same-run `rebuild_day`, `provisional → rebuilt` skipping `ArchiveGap` spans, `RetentionPolicy` the only file deleter (incl. dropped-instrument and delta retention), `CatalogFiles` the only in-place rewriter, one writer process per catalog leaf (capture lock for the open day, maintenance lock for closed days, `repair_catalog` refuses while capture holds the lock).
- MR9: Ranking: `RankingBoard` replaces the twelve mutable module globals; the pct/volatility math is ranking's alone; ports `VolumeSource` (per venue, over `kernel.venue_http`), `PriceHistory`, `RankingHistory`, `LivePublisher`; `views`/`research` never recompute.
- MR10: Bots: `PaperFleet` and `ExecBot` as distinct aggregate types, `Bot.id == order_id_tag`, bounded `Incident` list, `FillLedger`, `NautilusHost` and strategy-scoped `CacheReader` ACLs; every AD-10/AD-11 wire contract unchanged.
- MR11: Collection control: `CollectionPlan` (cap 30, `exclude ∩ collected = ∅`, USD-classified pins), plan-vs-applied set through `CaptureService.apply(plan_diff) → Applied(subscribed, unsubscribed, failed)`, one `config.toml` loader returning `(CoreConfig, CollectionPlan)`, no prune loop in control.
- MR12: Capture last, gated by the hot-path test: `LiveBook`, `TradeIntake`, `FeedGroup`, pure `SecondSampler`, venue policies as pure synchronous values (`CrossedBookPolicy.step`, `LevelTagger`, `SequenceCanary`, `BookTimeSource`, `BackfillCapability`), `TradeBackfill` with per-venue `trade_history.py` adapters, `FlushBatch`, `CaptureService` as the only ledger caller with sites in one file, `VenueFeed` as a `Protocol`.
- MR13: The reader-side crossed-book and empty-top skips in `data_api/routes/snapshots.py:129-133` are removed in the views move (gap markers kept); the silent empty-top-of-book skip in the sampler gains a rate-limited warning and ledger site in the capture move.
- MR14: Every parent-spine Deferred item the DDD spine resolves is struck in the parent (with an amendment) by the story that lands it; the parent's `open_interest`/`volume24h` and `capture_hl_ws.py` items included.

## Epic List

## Epic 1: Trustworthy Coin Ranking & Watchlist
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
Builder's chart page can show daily/weekly candles — with order-book-derived signal (OFI/OBI, top-of-book) baked in — without every request rescanning years of raw 1-second data. The collector incrementally builds a small `DydxMinuteRollup` cache as data streams in (O(1)/second, no periodic full rescan); wide-window candle requests read from it instead of raw 1s, with correct-by-construction OFI continuity across minute boundaries and a fallback to raw 1s when rollup coverage is missing. The raw 1-second archive stays fully intact and authoritative — the rollup is a regenerable performance cache, never a replacement. Backend/collector-pipeline scope, standalone: delivers complete value against `data_api/app.py`'s existing `/api/candles` route (`catalog_candles`) today — `dashboard.py` itself is retired by Story 15.10, so `data_api` is the sole integration point. No PRD/Architecture update precedes this addition (same precedent as Epic 12/13) — created via direct technical investigation this session.
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

### Epic 23: Migration guardrails, observability and the shared kernel
Every service image is proven to ship its own imports, every process reports failures through one ledger and one notifier, boundary and hot-path regressions fail CI, and the shared types live in `kernel/` with nothing else. Closes the live `data_api`/`live_paper` image gaps.
**MRs covered:** MR1, MR2, MR3, MR4, MR5, MR7 (notifier), MR14

### Epic 24: Derived data and read models on one fold
The chart's forming candle, the stored candle, the ranking sparkline and an alert's bar close come from one fold; both UIs read one set of view functions; the reader-side crossed-book re-validation is gone; research is a pure consumer.
**MRs covered:** MR1, MR2, MR4, MR6, MR7, MR13 (views), MR14

### Epic 25: Archive, ranking, bots and collection control as aggregates
The nightly saga cannot zero rows over an archive gap or reconcile an unrebuilt day; the ranking engine has no module globals; the bots' paper/real split is a type; a venue's collected set is the applied plan, not the intent.
**MRs covered:** MR1, MR2, MR4, MR8, MR9, MR10, MR11, MR14

### Epic 26: The gate as an aggregate
The write gate is one pure function over explicit aggregates, venue variance is a set of pure policy values, and no shim remains; a fourth venue is client + policies + config.
**MRs covered:** MR1, MR2, MR4, MR12, MR13 (sampler), MR14

### MR Coverage Map (Epics 23–26)

MR1, MR2, MR4, MR14: every epic (per-story rules) · MR3, MR5: Epic 23 · MR6, MR7: Epic 24 (MR7's notifier lands in 23.1) · MR8–MR11: Epic 25 · MR12: Epic 26 · MR13: 24.2 (readers) and 26.1 (sampler). Order 23 → 24 → 25 → 26 (AD-D12).

### Epic 27: Research notebooks on a shared analysis layer, with candlestick patterns detectable and tradeable
A researcher inspects the archive, the microstructure, cross-instrument and cross-venue correlation, a strategy's evaluation page and its Monte Carlo distribution from six executable notebooks that carry no analysis logic of their own; candlestick patterns are one kernel indicator shared by the chart, the screener, backtests and paper bots, and a pattern is one config file away from a backtest and a paper bot.
**FRs covered:** FR70, FR71, FR72, FR73, FR74, FR75, FR76, FR77, FR78, FR79 · **NFRs:** NFR3, NFR12 · depends on Story 24.4; order 27.1 → 27.9.

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

Builder's chart page can show daily/weekly candles — with order-book-derived signal (OFI/OBI, top-of-book) baked in — without every request rescanning years of raw 1-second data. The collector incrementally builds a small `DydxMinuteRollup` cache as data streams in (O(1)/second, no periodic full rescan); wide-window candle requests read from it instead of raw 1s, with correct-by-construction OFI continuity across minute boundaries and a fallback to raw 1s when rollup coverage is missing. The raw 1-second archive stays fully intact and authoritative — the rollup is a regenerable performance cache, never a replacement. Backend/collector-pipeline scope, standalone: delivers complete value against `data_api/app.py`'s existing `/api/candles` route (`catalog_candles`) today — `dashboard.py` itself is retired by Story 15.10, so `data_api` is the sole integration point.

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
**Then** `choose_candle_source(bar_seconds)` returns `"rollup_1m"`; at or below `3600` it returns `"raw_1s"` — a single shared decision point, never duplicated elsewhere in `data_api/app.py`

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
**When** `data_api/app.py::catalog_candles` is updated (`dashboard.py` no longer exists post-15.10, so this is the only candle route left to update)
**Then** it calls `candle_dicts_for_window` with closures around `query_second_snapshots`/`query_minute_rollups` instead of calling `candle_dicts_from_snapshots` directly — URL/params (`start_ns`/`end_ns`/`bar_seconds`) unchanged, no new route added

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



## Epic 21: Chart Data Correctness and Speed

Builder can trust every candle and volume bar on the chart end-to-end (collector → catalog → rollup → `data_api` → frontend) and the chart loads fast. Driven by the 2026-09-19 fake-spike/odd-candle incident and `troll/docs/DATA_INTEGRITY_AUDIT.md`.

### Stories 21.1–21.5

21.1 Ingest and rollup correctness · 21.2 Candle invariants and source equivalence · 21.3 Live candle seeding and volume · 21.4 Vite proxy connection resets · 21.5 Chart load speed. Each has a story file in `_bmad-output/implementation-artifacts/21-*.md`.

## Epic 22: Multi-Exchange Trading, Paper Trading and Collection on One Collector Core

Builder trades, paper-trades and collects backtest data on dYdX, Bybit (linear perps + spot) and Hyperliquid perps through **one shared collector core** (`troll/collector_core/`) and a venue-parameterised `live_paper`, so a fourth exchange is a thin `client.py` + config + entrypoint. Research and architecture: `_bmad-output/planning-artifacts/research/technical-multi-exchange-collector-core-and-orderbook-research-2026-09-20.md` (approved 2026-09-20). Decisions: all three collectors on the core (dYdX included); 1.0 s snapshot cadence everywhere; Bybit linear + spot with perp/spot explicit in API and UI; Sandbox paper trading primary, Bybit Demo / Hyperliquid Testnet via the explicit exec-config path only. Closes story 19.3/19.4's "extract common helpers once duplication across all three is visible".

**Candle-accuracy decision (2026-09-20, operator review of the candle-store change):** the goal is 1 s aggregates and candles that match the exchange's own numbers exactly, and live trading on the same fold. Findings that reshaped the stories below: (a) the 1 s snapshot is the *only* record of trades -- raw `TradeTick`s are folded live and discarded, deltas are an unused per-instrument opt-in -- so anything the fold gets wrong or misses is unrecoverable; (b) the fold sums `size.as_double()`, so volume is a float sum, not exact; (c) nothing backfills trades missed across a reconnect; (d) exchange time (`ts_event`) exists on trades for all three venues and on the book for Bybit/Hyperliquid only; (e) a live hold-back cannot make a bar correct, only later -- correctness comes from rebuilding closed days from raw trades on exchange time and reconciling them against the venues' klines. The professional shape is therefore: archive raw trades with both clocks, one exact fold shared by live, rebuild and strategy, nightly rebuild + reconciliation, gap closure at the source. **Execution order: 22.13 -> 22.14 -> 22.12** (22.12 now depends on 22.13). Raw trades are retained only until their day is verified ("keep until proven, then release"), not indefinitely.

### Story 22.1: `troll/collector_core/` extracted from the Bybit and Hyperliquid collectors

As the platform operator,
I want the ~200 identical lines of `bybit_collector/collector.py` and `hyperliquid_collector/collector.py` to live once in `troll/collector_core/collector.py`,
So that every venue gets the same ingest/flush/sample/write path and the same integrity guards.

**Acceptance Criteria:**

**Given** the two sibling collectors (story 19.3/19.4)
**When** `collector_core.Collector(config, client, extra_loops=())` and `run_forever(build)` exist
**Then** `bybit_collector/collector.py` and `hyperliquid_collector/collector.py` each shrink to a client + config + entrypoint of roughly 15–25 lines, with the Bybit open-interest REST poll passed as an `extra_loops` entry, and the duck-typed client contract (`fetch_instruments`, `connect`, `disconnect`, `subscribe`, `unsubscribe`, optional `subscribe_global`, optional `resync_orderbook`) is documented in the core docstring

**Given** dYdX already has trade stale-age filtering + bounded `trade_id` dedup (DATA-06), the SQLite candle-store feed (DATA-05: `ml_signals/candle_store.py`, fed from each Parquet flush, one `candles_<venue>.db` per venue), `snapshots:raw` publish, the `_second_loop` lag canary and the OBS-01 watchdog
**When** the core is built
**Then** all of these run for Bybit and Hyperliquid too, with thresholds in `CoreConfig` defaulting to dYdX's current values

**Given** the user's decision to unify cadence
**When** the core's `_second_loop` runs
**Then** every venue samples at `snapshot_interval_seconds = 1.0` (the Bybit/Hyperliquid configs lose their `0.5`)

**Given** the crossed-book differences documented in the research (Bybit = local corruption, Hyperliquid = impossible by construction)
**When** the core sees `best_bid >= best_ask`
**Then** it skips the sample, records `error_ledger.record("collector.crossed_book", ...)`, and resyncs only if the client exposes `resync_orderbook` and the book has stayed crossed longer than `crossed_resync_seconds` (DATA-03: a fallback, never the fix)

**Given** TEST-01/TEST-03
**When** verified
**Then** the sibling tests move to `collector_core/tests/` with real `OrderBook`/`TradeTick`/`ParquetDataCatalog` objects, and both collectors are live-verified on the VPS writing `DydxSecondSnapshot` rows at 1 s spacing

### Story 22.2: dYdX collector onto the core

As the platform operator,
I want `dydx_collector/collector.py` to be `class DydxCollector(collector_core.Collector)`,
So that the production dYdX feed shares the single write gate (AD-1) instead of a 1.7k-line copy of it.

**Acceptance Criteria:**

**Given** dYdX's venue-specific book logic (per-level message-id tagging in `_apply_deltas`, uncross + escalation in `_handle_crossed_book`, DATA-04)
**When** migrated
**Then** it lives in `dydx_collector/uncross.py` and is wired by overriding exactly those two core methods; the core's `_second_loop` gate is otherwise unchanged from today's `collector.py:1188-1269`

**Given** dYdX's control plane (config reload, status/control Redis loops, liquidity tiering, prune, watchdog notifications, incident reports, `[WS_RAW]` flush, `_MAX_WS_SUBSCRIPTIONS = 32`)
**When** migrated
**Then** these stay in `dydx_collector/` as `extra_loops` and `DydxConfig(CoreConfig)` fields, unchanged in behaviour

**Given** the 18 existing test modules in `dydx_collector/tests/`
**When** the migration lands
**Then** all pass with only import/attribute-path updates (no behavioural rewrites), `make test` is green, and the dYdX collector is live-verified on the VPS with the bot_tui collector pane still working

**Given** the candle store (`ml_signals/candle_store.py`), fed today from `dydx_collector/collector.py`'s flush path
**When** the core (22.1) owns that path
**Then** dYdX's `_flush_once` -> `_apply_to_candle_store`, the :02 wall-clock flush, the startup catch-up and the prune call are deleted from `dydx_collector/` with no behavioural change, and `candles_dydx.db` keeps being written

### Story 22.3: Shared data types and a single `OpenInterest`

As a backend developer,
I want the venue-neutral Data types to live in `collector_core/` and the three identical open-interest classes to become one,
So that a new venue imports shared types instead of reaching into `dydx_collector`.

**Acceptance Criteria:**

**Given** `DydxSecondSnapshot`, `integrity.ohlc_outside_book` and the venue-neutral operator scripts `build_candles`, `consolidate_catalog` and `repair_catalog` live in `dydx_collector/`
**When** moved to `collector_core/`
**Then** class names are unchanged (catalog directories `custom_dydx_second_snapshot/` etc. stay valid) and every importer (`data_api/routes/*`, `ml_signals`, `ranking_engine`, collectors, tests) is updated

**Given** `DydxOpenInterest`, `BybitOpenInterest`, `HyperliquidOpenInterest` share the same four fields
**When** replaced by `collector_core.open_interest.OpenInterest` (with a `from_pyo3` staticmethod for Hyperliquid)
**Then** an idempotent migration script renames existing `custom_{dydx,bybit,hyperliquid}_open_interest/` catalog directories to `custom_open_interest/` and rewrites the Arrow `type` metadata, readers of `custom_data(...)` are updated, and re-running the script is a no-op

### Story 22.4: Bybit spot collection and explicit perp/spot everywhere

As the dashboard operator,
I want Bybit spot pairs collected alongside linear perps and every API response and screen to say whether an instrument is perp or spot,
So that a `BTCUSDT-SPOT.BYBIT` row is never mistaken for `BTCUSDT-LINEAR.BYBIT`.

**Acceptance Criteria:**

**Given** Bybit runs one public WebSocket per product type and its spot ticker has no bid/ask, funding or open interest (docs `websocket/public/ticker`)
**When** `BybitClient` gains a SPOT connection
**Then** `subscribe(iid)` routes by suffix (`nautilus_pyo3.bybit_product_type_from_symbol`), spot subscribes `publicTrade` + `orderbook.50` only, `fetch_instruments` returns LINEAR + SPOT, and the OI poll / ticker subscribe remain linear-only by construction

**Given** Nautilus's id suffixes (`-PERP`, `-LINEAR`, `-INVERSE` ⇒ perp; `-SPOT` ⇒ spot)
**When** `common/venues.py` gains `market_kind(instrument_id) -> "perp" | "spot" | "unknown"`
**Then** it is a pure function unit-tested against every real id shape for all three venues

**Given** story 19.1's `venue` field
**When** `market` is added
**Then** it appears next to `venue` in every `data_api` response that carries `venue` (candles, snapshots, indicators, indicator_series, rankings), in `ranking_engine`'s rank entries, in `frontend/src/api/schema.ts`, as a screener column + filter (same pattern as story 19.5), and as a badge in the chart header

### Story 22.5: Order book validation per venue

As the platform operator,
I want each venue's book to be checked against an independent source of truth and its known failure modes to have loud canaries,
So that DATA-02's "no mysteries in ingestion" holds for Bybit and Hyperliquid, not just dYdX.

**Acceptance Criteria:**

**Given** Bybit's `u` update id (documented as a sequence; `u=1` = service-restart snapshot) is stamped as `BookOrder.order_id` by the Rust client, which performs no gap check
**When** a non-snapshot delta arrives with `u` ≤ the previous `u` for that instrument, or with a gap > 1
**Then** a `collector.book_sequence` ledger entry + counter is recorded (WARNING for a gap until a live capture proves `u` is contiguous in practice, then ERROR) and the book is resynced; dYdX and Hyperliquid opt out with the reason documented in their `client.py`

**Given** REST snapshots exist for both venues (pyo3 `BybitHttpClient.request_orderbook_snapshot`; Hyperliquid `POST /info l2Book` via stdlib `urllib`, weight 2)
**When** a periodic cross-check `extra_loop` runs
**Then** the top-20 levels of the live book are diffed against the REST book taken at the same instant, mismatches beyond a tolerance land in `error_ledger` and `troll/docs/DATA_INTEGRITY_AUDIT.md`, and one hour of Bybit and Hyperliquid runs reports zero mismatches

**Given** DATA-06 requires every new channel to be checked for subscribe-time replay
**When** the first messages after `publicTrade` (Bybit) and `trades` (Hyperliquid) subscription are captured with the `[WS_RAW]` debug feed
**Then** the replay finding per venue is registered in `DATA_INTEGRITY_AUDIT.md` with evidence, and the core's dedup/age filter is confirmed to cover it

**Given** Hyperliquid pushes `l2Book` "on each block that is at least 0.5 s since last push" but the collector observed ~5 s spacing
**When** a raw capture resolves the discrepancy
**Then** a feed-level liveness timestamp distinguishes "quiet feed, book unchanged" from "dead feed / post-reconnect stale", per-venue `stale_book_seconds` is set from the evidence, and the finding is registered

### Story 22.6: `live_paper` multi-venue paper trading (Sandbox)

As a strategy developer,
I want one `live_paper` process to run paper bots on dYdX, Bybit and Hyperliquid instruments at once,
So that the same `DummyStrategy` is validated on every venue against live mainnet data.

**Acceptance Criteria:**

**Given** `live_paper/node.py` is hard-wired to `DydxDataClientConfig`/`DydxLiveDataClientFactory`
**When** `live_paper/venues.py` maps `DYDX`/`BYBIT`/`HYPERLIQUID` to (data config class, data factory, exec config class, exec factory, allowed environments, paper quote currency) as a plain dict
**Then** `build_node` creates one data client and one `SandboxExecutionClientConfig` per venue present in `PaperConfig.bots` (venue from `venue_of(bot.instrument_id)`), still one `TradingNode` per process

**Given** AD-11 currently reads "one `DydxDataClientConfig`, one `SandboxExecutionClientConfig` no matter how many bots"
**When** this story ships
**Then** the spine is amended to "one data client and one exec client per venue in use" and `PaperConfig` carries per-venue `starting_balances` and `environment`

**Given** Bybit spot and linear share the `BYBIT` venue
**When** a spot bot and a linear bot run in the same node
**Then** a spot fill and a linear fill are both observed in Sandbox (the open item on Sandbox account type for mixed `CurrencyPair`/perp instruments is resolved with evidence), and `bots:status` shows every bot

### Story 22.7: Exchange demo/testnet and real money for Bybit and Hyperliquid

As the platform operator,
I want the explicit exec-config path to reach Bybit Demo, Hyperliquid Testnet and (separately) real mainnet execution on both venues,
So that real order signing and reports are validated on the exchange's own paper environment before any real funds are used.

**Acceptance Criteria:**

**Given** story 3.1's two-signal safety design (separate file, separate loader, explicit `mode`)
**When** the loader accepts `mode = "exchange_demo"`
**Then** it requires a non-mainnet `environment` (Bybit `demo`/`testnet`, Hyperliquid `testnet`, dYdX `testnet`), `mode = "real_money"` requires `environment = "mainnet"`, and a mismatch fails closed with a clear error

**Given** Bybit Demo uses `api-demo.bybit.com` + `wss://stream-demo.bybit.com` for private streams only, has no WS Trade API, and takes funds via `POST /v5/account/demo-apply-money` (docs `v5/demo`)
**When** a Bybit demo run starts
**Then** it uses `BybitEnvironment.DEMO`, credentials come only from the env vars Nautilus's factory reads, and one order is placed and cancelled with its reports visible in `bots:status`

**Given** Hyperliquid Testnet needs a mainnet deposit from the same address before `claimDrip` grants 1,000 mock USDC
**When** a Hyperliquid testnet run starts
**Then** it uses `HyperliquidEnvironment.TESTNET` with `HYPERLIQUID_TESTNET_PK`, and `DEPLOY_CHECKLIST.md` documents the faucet prerequisite and the thin-liquidity caveat

### Story 22.8: Spine, rules and docs updated for the multi-venue core

As a future contributor,
I want the architecture spine and `troll/CLAUDE.md` to describe the core, not three siblings,
So that the next venue is added the documented way.

**Acceptance Criteria:**

**Given** ARCHITECTURE-SPINE AD-1 ("structural enforcement once a second writer exists"), AD-4 (shared types list, stale `DydxMinuteBar` name) and AD-11
**When** this story ships
**Then** each is amended to name `collector_core` as the single write gate, the moved shared types, and per-venue clients; the "one producer per channel" convention reads "one producer per (channel, venue)"

**Given** `troll/CLAUDE.md`'s header scopes the rules to `dydx_collector/` and `ml_signals/`
**When** updated
**Then** the scope covers `collector_core/` and every venue collector, and the "how to add a venue" recipe (client + config + entrypoint + one `common/venues.py` line) is written down once

### Story 22.9 (optional): Historical bar backfill for Bybit and Hyperliquid

As a strategy developer,
I want 1-minute `Bar` history backfilled into the catalog from Bybit klines (pyo3 `request_bars`) and Hyperliquid `candleSnapshot` (last 5000 candles),
So that backtests on these venues can run over more history than the collector has been alive for, and 22.13's reconciliation has a stored reference to diff against.

**Acceptance Criteria:**

**Given** NAUT-02/NAUT-03
**When** the backfill runs
**Then** bars are written via `ParquetDataCatalog.write_data()` as `-EXTERNAL` bar types, re-runs are idempotent (skip existing ranges), and a `BacktestDataConfig` over the backfilled range loads them without conversion

**Given** the candle store is built only from our own 1 s snapshots (D-35)
**When** venue bars are backfilled
**Then** they land in Parquet only and never in `candles_<venue>.db` or on the chart; the data dictionary says so, and `compare_klines` (22.13) may read them instead of calling the venue

### Story 22.10: Rankings show every collected coin across venues, with an exchange filter

As the dashboard operator,
I want the rankings page to list every coin any collector is currently collecting — dYdX, Bybit (linear + spot) and Hyperliquid — by default, and to narrow it by exchange with one click,
So that a multi-venue watchlist is the normal view and a single venue is a filter, not the other way round.

**Acceptance Criteria:**

**Given** all three collectors publish to `snapshots:raw` (story 22.1)
**When** `ranking_engine` builds `rankings:live`
**Then** it contains one row per fresh instrument from every venue; a missing coin means "not collected / stale", never "hidden by venue"

**Given** `ranking_engine`'s 24h volume comes only from dYdX's indexer today, so Bybit/Hyperliquid rows would rank at 0
**When** the volume poll also reads Bybit `/v5/market/tickers` (`turnover24h`, linear + spot) and Hyperliquid `metaAndAssetCtxs` (`dayNtlVlm`)
**Then** every venue's rows carry USD `volume24h` (OBS-03), and a row whose venue volume is unavailable is excluded from volume mode loudly (`error_ledger`, DATA-01), never ranked at 0

**Given** story 19.5's typed `venue = X` filter condition
**When** the rankings page shows a venue chip row (all venues present, all selected by default, per-viewer selection in `localStorage`)
**Then** deselecting a chip hides that venue's rows, chips compose with `FilterPanel` conditions and sort, and a newly appearing venue is shown by default

~~**Given** SSOT-04 (web and bot_tui are two renderers of one ranking page)~~
~~**When** the bot_tui coins pane renders Hyperliquid's 24-char ids~~
~~**Then** the instrument column fits without misaligning later columns (TUI-02) and the existing substring filter (`.BYBIT`, `.DYDX`, `.HYPERLIQUID`) is documented as the venue filter~~
`[amended 2026-09-26: Story 25.1a -- struck: rankings are web-only and the bot_tui coins pane was deleted, so the web rankings page (venue chips above) is the only ranking renderer; see "Story 25.1a: Rankings web-only"]`

### Story 22.11: Nightly catalog consolidation for every venue

As the platform operator,
I want each closed UTC day's minute-sized Parquet files merged into one file per (data type, instrument) for every venue,
So that the archive's file count grows by hundreds a day, not hundreds of thousands (audit D-36: inodes, backup and read cost all scale with files).

**Acceptance Criteria:**

**Given** `collector_core/consolidate_catalog.py` (moved there in 22.3): plain pyarrow, one schema per day or refused (D-24), row count verified before the sources are deleted, interrupted runs self-healing, today never touched
**When** it runs nightly (`make consolidate`, cron) against the shared catalog
**Then** every closed day of every `data/<type>/<instrument>/` leaf -- `.DYDX`, `.BYBIT` and `.HYPERLIQUID` ids alike -- holds one file (plus at most one midnight-crossing file), the collectors keep writing today's files untouched, and `data_file_ranges`/`query_second_ohlc`/`build_candles` read the merged files unchanged (tested)

**Given** the VPS
**When** the first full run and one nightly run are timed
**Then** wall time, peak RSS and files before/after are recorded in `docs/DATA_INTEGRITY_AUDIT.md` D-36 as measured, and the run stays under the collector's memory headroom (MEM-01)

**Given** the Parquet backup that D-33 still lacks
**When** consolidation has run
**Then** a nightly `rclone`/`restic` copy of closed-day files to object storage is one command in the Makefile (its target and credentials are the operator's), documented next to `make consolidate`

### Story 22.12: Exchange-time bucketing for trades on every venue and the book on Bybit/Hyperliquid (depends on 22.13)

As a strategy developer,
I want every rebuilt second and bar to hold the trades the exchange executed in that second, and the Bybit/Hyperliquid book as the exchange stamped it,
So that bars match the venues' klines bar for bar and align across venues, while the live loop stays instant.

**Acceptance Criteria:**

**Given** trades carry venue `ts_event` on all three venues, and the book carries it on Bybit (`orderbook.ts`) and Hyperliquid (`book.time`) but not dYdX (`OrderBookDelta.ts_event = ts_init`)
**When** 22.13's nightly rebuild recomputes a closed day's second-level trade fields
**Then** it buckets trades by `ts_event` into half-open `[S, S+1)` on every venue (dYdX included, which closes D-31 and D-44 for trades), and the snapshot's `ts_event` semantics per venue are written in the data dictionary: trades exchange-timed everywhere; book exchange-timed on Bybit/Hyperliquid, arrival-timed on dYdX

**Given** the live loop is arrival-timed and provisional by design (22.13)
**When** this story ships
**Then** the live `_second_loop` is unchanged and needs no hold-back for correctness; `hold_back_seconds` exists only as an optional `CoreConfig` knob that delays the live close so fewer seconds differ between live and rebuild, default `0.0`, and is set per venue from a measured `ts_init - ts_event` distribution (`measure_lag.py`, p50/p99/p99.9/max over >= 3 h, recorded in the audit); ranking staleness and the watchdog compare on arrival so a hold-back never flags a healthy feed stale

**Given** 22.13's `compare_klines`
**When** one full UTC day per venue is rebuilt exchange-timed
**Then** 1 m volume equals the venue's kline volume exactly (integer units) and OHLC equals the kline's on every bar that our trade archive covers completely; every remaining mismatch is root-caused (missing trades -> 22.14, never tolerance), and the pass rate is recorded per venue in `DATA_INTEGRITY_AUDIT.md`

### Story 22.13: Raw trade archive, exact fold, nightly rebuild and kline reconciliation

As the platform operator,
I want every venue's raw trades archived with both clocks, one exact fold shared by the live loop and the rebuild, and every closed day rebuilt from trades and reconciled against the venue's klines,
So that a bar is either proven equal to the exchange's or loudly flagged, and nothing the live fold gets wrong is unrecoverable.

**Acceptance Criteria:**

**Given** the core folds each `TradeTick` into the second accumulators and discards it
**When** the core also appends the `TradeTick` to the flush buffer after the stale-age filter and `trade_id` dedup
**Then** `data/trade_tick/<instrument>/` is written every flush via `ParquetDataCatalog.write_data()` for all three venues, with the venue's `ts_event` and our `ts_init` intact, loadable through `BacktestDataConfig(data_cls=TradeTick)` unchanged; measured footprint recorded (expected ~1 MB/venue/day on dYdX from today's counts: ETH ~2.9k, BTC ~1.4k, median coin ~150 trades/day)

**Given** the live fold sums `size.as_double()` (float accumulation, D-46)
**When** `collector_core/fold.py` holds one pure `fold_trades(trades, second_ns) -> SecondTradeFields` used by the live loop and the rebuild
**Then** it accumulates `Quantity.raw` integers and compares `Price.raw` integers, converting to the snapshot's `float64` columns exactly once at the end; equivalence-tested against the previous fold on real `TradeTick`s; `DydxSecondSnapshot`'s Arrow schema is unchanged (D-24)

**Given** a closed UTC day `D` and its trade archive
**When** `collector_core/rebuild_seconds.py --catalog ... --day D [--instrument ...] [--apply]` runs (nightly, before `consolidate_catalog`)
**Then** every snapshot of `D` has its trade fields (`open/high/low/close_price`, `buy_volume`, `sell_volume`, counts) recomputed from the archived trades with `fold_trades`, the files are rewritten temp-then-rename like `repair_catalog`, the book columns and `ts_event`/`ts_init` are untouched, `build_candles` then refolds `D` into `candles_<venue>.db`, and the run is idempotent and reports the number of seconds whose values changed (that number is the live/rebuild disagreement metric, published to the audit)

**Given** the venues' own 1 m klines (Bybit `request_bars`, Hyperliquid `candleSnapshot`, dYdX indexer `/v4/candles/perpetualMarkets/{ticker}?resolution=1MIN`)
**When** `collector_core/compare_klines.py --venue ... --day D` runs after the rebuild
**Then** for every instrument it compares the store's 1 m bars bar for bar (volume in integer units, OHLC in raw price units), writes a `verified_days(instrument_id, day, status, checked_at, mismatches)` row into `candles_<venue>.db`, records every mismatch in `error_ledger` (`reconcile.kline_mismatch`) with instrument, minute and delta, and the per-venue pass rate goes into `DATA_INTEGRITY_AUDIT.md` as a running number; a mismatch is root-caused (DATA-02), never absorbed by a tolerance

**Given** the decision to keep raw trades only to correct aggregates
**When** `prune_catalog` gains a `trade_tick` policy
**Then** a day's trades are pruned only when the day is older than `trade_retention_days` (default 7) **and** its `verified_days` row is `pass`; failed or unverified days are kept and listed in the prune report; the policy and its upgrade path (extend the window if tick-level features are ever wanted) are a `Known limit:` in the data dictionary

**Given** the nightly job
**When** `make nightly` runs (cron on the VPS)
**Then** it runs rebuild -> consolidate -> build-candles -> compare -> prune for yesterday in that order, each step refusing to continue after a failure, with one summary line per venue in the collector log and every failure in `error_ledger`

### Story 22.14: Trade gap closure: REST backfill after reconnect and dual-feed arbitration

As the platform operator,
I want trades missed while a WebSocket was down to be recovered from the venue, and the remaining gap closed with a second independent feed,
So that the trade archive is complete on every venue that allows it, and the venue that does not is documented as such.

**Acceptance Criteria:**

**Given** the Rust clients reconnect and resubscribe silently, and the core only sees the resulting replay through `trade_id` dedup
**When** the core detects a reconnect (client-exposed hook or a feed-silence gap longer than `stale_book_seconds` followed by messages) for an instrument
**Then** it requests the venue's trade history covering `[last_trade_ts - margin, now]` -- dYdX indexer `GET /v4/trades/perpetualMarket/{ticker}` (paged by `createdBeforeOrAt`), Bybit `GET /v5/market/recent-trade?limit=1000` (most recent 1000 only; a longer gap on a busy pair is reported as unrecoverable) -- via stdlib `urllib` in an `extra_loop`, dedups by `trade_id`, appends the missing trades to the archive with their venue `ts_event` and `ts_init = now`, counts them in `collector:status` and `error_ledger` (`collector.trade_backfill`), and leaves the live second accumulators alone (the nightly rebuild absorbs them)

**Given** Hyperliquid's public API has no documented trade-history endpoint
**When** the story is implemented
**Then** the venue capability table in the data dictionary states per venue what a reconnect gap costs (dYdX: recoverable; Bybit: last 1000 trades; Hyperliquid: unrecoverable from one connection), verified against the live API on the day, with the `Known limit:` and the upgrade path being the dual feed below

**Given** professional A/B feed arbitration
**When** `CoreConfig.trade_feeds: int = 1` is set to `2` for a venue
**Then** the client opens a second independent WebSocket subscribed to trades only, both feeds flow through the same `_on_data`, the existing `trade_id` dedup takes the union (first copy wins, second counted as `duplicate` not `dropped`), a per-feed liveness timestamp feeds the OBS-01 watchdog, and a 24 h run on Hyperliquid and Bybit records in the audit how many trades only one feed delivered

**Given** 22.13's reconciliation
**When** 22.14 has run for a week
**Then** the kline pass rate per venue is re-recorded and any remaining mismatch has a named cause

## Epic 23: Migration guardrails, observability and the shared kernel

First epic of the DDD migration (`_bmad-output/planning-artifacts/architecture/architecture-ddd-platform-2026-09-21/ARCHITECTURE-SPINE.md`, AD-D1..AD-D18; requirements MR1–MR14 above). Step 0, the `troll/` → `platform/` rename with the stores under `platform/data/`, is done (`18c12eedf4`). Every story here and in Epics 24–26 obeys the per-story rules MR1 (deployable alone, published language frozen), MR2 (pure re-export shims with `REMOVE_AFTER`, gone within two stories), MR4 (docs, dockerfile `COPY` sets, compose `command:` lines and both Makefile test lists updated in the same commit) and MR14 (parent-spine Deferred items struck when resolved). Order inside the epic is fixed: 23.1, then 23.2, then 23.3 (added 2026-09-21 after the Epic 22 operator-action pass).

### Story 23.1: `observability/` context, one notifier, and the migration's enforcement tests

As the platform operator,
I want every process to report tolerated failures through one ledger and page me through one notifier, and the migration's boundary, image-closure and hot-path checks to run in `make test` from the first move,
So that the two live image gaps are closed now and every later context move is caught by a test instead of a review.

**Acceptance Criteria:**

**Given** `ml_signals/error_ledger.py`, the module functions `_notify` and `_watchdog_transition` in `collector_core/collector.py`, the dYdX incident-report handler (`dydx_collector/collector.py`, `[WS_RAW]` flush + `_IncidentHandler`) and `data_api/alerts.py`'s `post_webhook`/`post_telegram`
**When** the story ships
**Then** `platform/observability/{error_ledger,notify,watchdog,incidents}.py` exist and import only the standard library (asserted by `test_boundaries.py`); `observability.notify(channel, title, body)` is the one outbound transport with ntfy, Telegram and generic-webhook adapters chosen by env (`WATCHDOG_NTFY_URL`, `TELEGRAM_*`, webhook URL), and both the capture watchdog and `data_api/alerts.py` deliver through it (an `Alert` names a channel, never a transport); `observability.watchdog` holds only the generic `(down_since, reminder)` transition over a boolean, the feed-silence verdict staying in the collector; the incident handler takes its instrument-id pattern from the dYdX entrypoint and holds no venue token; the old paths `ml_signals.error_ledger` and the moved functions are pure re-export shims with `DeprecationWarning` and `REMOVE_AFTER = "24-1-..."`, and every in-repo caller is updated in this story (a `DeprecationWarning` in the test run is a failure, TEST-04)

**Given** the AD-D2 dependency graph and the AD-D1 context table
**When** `platform/tests/test_boundaries.py` runs in `make test`
**Then** it walks `ast` imports of every Python module under `platform/` (excluding `frontend/`, `node_modules/`, `data/`), maps every legacy module to its target context through a static `LEGACY_MODULE_TO_CONTEXT` table (an unmapped module is a failure), fails any import edge not in the AD-D2 graph between the two ends' target contexts, fails any import of a `_private` name across two contexts, treats `capture/venues/<v>/policies.py` as `domain/`, exempts only edges within one unmoved package, asserts `observability/` and `kernel/` import no context and `research/` imports no `data_api` symbol, and passes on the tree as of this story

**Given** every `command:` in `docker-compose.yml` and every `-m` module in the `Makefile` and the nightly cron line
**When** `platform/tests/test_images.py` runs in `make test`
**Then** it computes each entrypoint's top-level-package import closure and asserts every package is in that service's dockerfile `COPY` set (a shim counts as its target), and the story adds the `COPY` lines the test demands today (`data_api.dockerfile` lacks `collector_core` and `common`; `live_paper.dockerfile` whatever its closure requires) so that the test passes and `make build` succeeds for all three thin images; the parent spine's Deferred entry "`data_api`'s image does not ship the packages its code imports" is struck with an amendment

**Given** the recorded WS fixtures under `collector_core/tests/fixtures/` and the current `Collector._process_data`
**When** the hot-path replay test `platform/tests/test_hotpath.py` runs
**Then** it replays a fixed burst (all three venues, 30 instruments' worth of deltas and trades) through `_process_data`, measures `tracemalloc` allocations per message and `time.perf_counter_ns` wall time per message, writes the first run's numbers to `platform/tests/fixtures/hotpath_baseline.json` and records them in `docs/DATA_INTEGRITY_AUDIT.md`, and on every later run asserts allocations ≤ baseline and wall time ≤ 2× baseline; the test is deterministic across runs on the same host (three consecutive runs agree within the tolerance)

**Given** MR4 and AD-D16's per-process Known limit
**When** the story is merged
**Then** `platform/CLAUDE.md` DATA-07 names `observability.error_ledger` and states the Known limit (the ledger is per process; `/api/errors` shows `data_api`'s own sites; upgrade path `errors:ledger`), `ARCHITECTURE.md`'s module map gains the `observability/` row, `docs/DATA_DICTIONARY.md` is unchanged in content but re-cited, the collector image `COPY`s `observability`, and both Makefile test lists (`test`, `test-live-paper`) include `tests`

### Story 23.2: `kernel/` shared kernel

As a strategy developer and collector maintainer,
I want the one snapshot schema, the one fold, the one venue-id parser, the one skew constant, the one REST transport and the one Parquet compression patch to live in a package that imports nothing else,
So that no two contexts can ever hold two copies of a shared type or a shared number.

**Acceptance Criteria:**

**Given** `collector_core/{second_snapshot,open_interest,fold,venue_http,archive_gaps}.py`, `common/venues.py`, `ml_signals/{venue,indicators,performance_metrics}.py`, `catalog_stats.SecondOHLC`/`_stamp_to_ns`/`data_file_ranges`/`second_ohlc_arrays`/`query_second_ohlc` and the zstd `pq.write_table` patch duplicated in `collector.py` and `backfill_bars.py`
**When** the story ships
**Then** `platform/kernel/` holds exactly `second_snapshot.py` (`DydxSecondSnapshot` + `SecondOHLC`), `open_interest.py`, `fold.py`, `indicators.py` (pure `Indicator` classes and stateless snapshot functions only), `performance_metrics.py`, `venues.py`, `clocks.py`, `archive_markers.py`, `venue_http.py`, `catalog_files.py`, `parquet_compat.py` and nothing else; `test_boundaries.py` asserts kernel imports no context and holds no module-level mutable state, no store and no config loader; `common/` and the moved `ml_signals`/`collector_core` modules become pure re-export shims (`REMOVE_AFTER = "24-2-..."`) and every in-repo caller is updated

**Given** the catalog directory names derive from the class names (`nautilus_trader.persistence.funcs.class_to_filename`)
**When** `DydxSecondSnapshot` and `OpenInterest` move
**Then** their class names and Arrow schemas are byte-identical, `register_arrow` runs exactly once per class (`test_namespace.py` asserts one `_SCHEMAS` key per kernel class `__name__` and `old.X is new.X` for every shim name), a fixture test proves a `snapshots:raw` payload and a catalog row written before the move are read back unchanged after it, and `DydxSecondSnapshot.from_dict` is the only `snapshots:raw` parser left in `data_api/live_candles.py` (ranking and bot_tui parsers are chased in their own stories)

**Given** three `InstrumentId` parsers today (`common.venues.market_kind`, `ml_signals.venue.venue_of`, `venue_http.bybit_category`)
**When** `kernel/venues.py` lands
**Then** it is the only module that parses an `InstrumentId` string, exposing `venue_of`, `venue_kind`, `market_kind`, `bybit_category` (defined over `market_kind`, raising `MalformedInstrumentId` for a non-Bybit id) and `MalformedInstrumentId`, with the existing tests of all three sources passing against it and a table test over every id shape the three venues produce (`-USD-PERP.DYDX`, `-LINEAR.BYBIT`, `-SPOT.BYBIT`, `-USD-PERP.HYPERLIQUID`)

**Given** `ARRIVAL_MARGIN_NS` (300 s) and the five other skew-related constants (`_FILE_MARGIN_NS`, `_TS_INIT_MARGIN_NS`, `_MAX_CATCH_UP_SECONDS`, `hold_back_seconds + _VENUE_AHEAD_NS`, the backfill refusal)
**When** `kernel/clocks.py` lands
**Then** it holds `TwoClocks`, ns helpers, `CatalogFileSpan` (the former `_stamp_to_ns` stem parse plus `covers(ts_event)`) and the single `MAX_TS_INIT_SKEW_NS`; all six `collector_core` modules that imported `_stamp_to_ns` use `CatalogFileSpan`; every other constant is defined as an expression of, or asserted ≤, `MAX_TS_INIT_SKEW_NS` by a kernel test; `kernel/archive_markers.py` holds the `ArchiveGap` value object and the pure encode/decode of `_archive_gaps/<iid>.jsonl`, with `collector.py` and the archive tools reading and writing through it (writers unchanged: capture `write_failed`/`quarantined`, prune `pruned`)

**Given** `venue_http` is used by `trade_backfill.py` and `compare_klines.py`, and `catalog_stats` read helpers by capture, archive, views and ranking
**When** `kernel/venue_http.py` and `kernel/catalog_files.py` land
**Then** every stdlib REST request in `collector_core/` is built through `kernel.venue_http` (a literal venue URL in a moved context is a boundary-test failure; `ranking_engine`'s duplicate maps stay until Story 25.2 and are listed in that story), `catalog_files.py` exposes `data_file_ranges`, `second_ohlc_arrays`, `query_second_ohlc`, `files_by_day` with no write path and no `ParquetDataCatalog` construction, `rebuild_seconds.py` no longer imports `build_candles._files_by_day`, and `parquet_compat.py` is the one place the zstd `write_table` patch is applied, imported by `collector.py` and `backfill_bars.py`

**Given** MR4
**When** the story is merged
**Then** all three dockerfiles `COPY` `kernel`, `test_images.py` and `test_boundaries.py` pass, `platform/CLAUDE.md`'s "Adding a venue" step 5 points at `kernel/venues.py`, `ARCHITECTURE.md` and `docs/DATA_DICTIONARY.md` cite the kernel paths, and the parent spine's Deferred entry "Writer→reader imports contradict AD-4" is amended to record that `error_ledger` (23.1) and the shared types, clocks and read helpers (23.2) are resolved, with `candle_store` remaining for Story 24.1

### Story 23.3: Durable error ledger and the day-long data/error cross-check

As the platform operator,
I want every tolerated failure written to a file that survives restarts, rebuilds and Redis loss, and one command that cross-checks a day of archived data against those errors,
So that "leave it running for a day and look at the logs" is real evidence, not a count that silently reset when a container was recreated.

**Acceptance Criteria:**

**Given** `observability.error_ledger.record(site, detail="", exc=None)` from 23.1
**When** a process calls it
**Then** besides the ERROR log line and the in-memory count (both unchanged), it appends one JSON line `{"ts_ns", "service", "pid", "site", "detail", "exc_type", "suppressed"}` to `<ERROR_LEDGER_DIR>/<service>.jsonl`, using the standard library only (the `test_boundaries.py` stdlib assertion for `observability/` still passes); `service` comes from `ERROR_LEDGER_SERVICE`; the write is flushed per line and never raises into the caller (a failed write is counted under the site `observability.ledger_write` in memory and logged, never swallowed, DATA-07); with `ERROR_LEDGER_DIR` unset the ledger behaves exactly as today and tests need no directory

**Given** a process starts
**When** its ledger is initialised
**Then** it writes a `{"site": "process_start", ...}` line with the pid and image/code revision if available, so a reader can tell a zero-error window from a restarted one; the cross-check (AC5) reports every restart inside the window

**Given** an error storm (a flapping feed, a reconnect loop)
**When** one site records faster than the write cap
**Then** at most `ERROR_LEDGER_MAX_LINES_PER_SITE_PER_MIN` (default 60) lines per site per minute are written, the next written line for that site carries the exact number of suppressed records in `suppressed`, so persisted totals stay exact; files rotate by size (`<service>.jsonl`, `.1` .. `.N`, default 20 MB × 10, mirroring the compose `x-logging` policy) and the bound is a documented `Known limit:` comment naming the ceiling and the upgrade path (object-storage shipping with the catalog backup, Story 22.11's rclone remote)

**Given** every service that calls `record()` (`collector`, `bybit_collector`, `hyperliquid_collector`, `ranking_engine`, `data_api`, `live-paper`, `bot_tui`)
**When** the story ships
**Then** `docker-compose.yml` bind-mounts `./data/errors:/app/errors_dir` into each and sets `ERROR_LEDGER_DIR=/app/errors_dir` and a distinct `ERROR_LEDGER_SERVICE` equal to the compose service name (new env vars and one new mount are additive; every existing env var, mount and service name is unchanged, MR1); `.gitignore` covers `platform/data/errors/`; `platform/data/` stays the only place durable stores live (AD-D13)

**Given** a UTC window (`--since`/`--until`, default the last 24 h) and optional `--venue`
**When** `python3 -m collector_core.crosscheck_errors` runs (mapped to the `archive` context in `LEGACY_MODULE_TO_CONTEXT`; it moves with archive in 25.1)
**Then** it reads the ledger files (rotated ones included) and, through `kernel.catalog_files` only (no `ParquetDataCatalog` construction, no unbounded loads, one day and one instrument at a time, MEM-01), the `custom_dydx_second_snapshot` rows of every collected instrument in the window, and prints: per service, restarts and per-site counts (suppressed included); per instrument, missing 1 s snapshot seconds grouped into gap intervals; and for every gap interval, the ledger entries of the owning collector within `MAX_TS_INIT_SKEW_NS` of it. A gap with no matching ledger entry and no restart is reported as `UNEXPLAINED` (a DATA-07 finding, never tolerated); the exit code is non-zero when any `UNEXPLAINED` gap or any site in a `--fail-on` list (default `collector.book_crosscheck`, `collector.book_sequence`, `collector.pending_deltas`) is non-zero

**Given** `GET /api/errors`
**When** `ERROR_LEDGER_DIR` is set for `data_api`
**Then** the response keeps its current fields unchanged and adds a `services` object with, per service file, the per-site counts since that service's last `process_start` and since a `?since_ns=` bound; the frontend error bar keeps working unmodified (fixture test on the old response shape)

**Given** the Epic 22 operator checks that need a clean day
**When** the story is merged and deployed
**Then** `platform/docs/DEPLOY_CHECKLIST.md` gains a "Day-long clean-run check" section with the exact `crosscheck_errors` invocation that closes each of: 22.5 #1 (`collector.book_crosscheck` zero on Bybit and Hyperliquid for a day), 22.1 #2 (no `[collector.*]` ledger lines, snapshots 1 s apart), 22.10 #4 (`ranking_engine.volume24h` not growing), 22.12 #5 (`collector.late_trade`, `collector.pending_deltas`, `collector.book_sequence` for Bybit and Hyperliquid); each of those story files gets a one-line pointer to it under its operator actions

**Given** MR4
**When** the story is merged
**Then** `platform/CLAUDE.md` DATA-07 replaces the per-process Known limit with the durable-file behaviour and its new ceiling, `ARCHITECTURE.md` and `docs/DATA_DICTIONARY.md` document `platform/data/errors/*.jsonl` (fields, rotation, cap), the three dockerfiles and both Makefile test lists stay consistent (`test_images.py`, `test_boundaries.py` pass), and the spine's AD-D16 Known limit is amended with a `[amended 2026-09-21: Story 23.3]` note

## Epic 24: Derived data and read models on one fold

Second epic of the DDD migration (spine AD-D8, AD-D11, AD-D16; MR6, MR7, MR13). After Epic 23 the kernel and observability exist; this epic moves every *derived* reader: candles first (so the one seconds→bars fold exists before anything consumes it), then the read models both UIs share, then alerting as an observer of the forming bar, then research as a pure consumer. Order is fixed: 24.1 → 24.2 → 24.3 → 24.4. The per-story rules MR1, MR2, MR4 and MR14 apply to every story.

### Story 24.1: `candles/` context behind capture's `SecondSink` port

As a strategy developer and chart user,
I want the candle store to be its own context that capture feeds through a port with the flushed batch, and the only seconds→bars fold in the platform,
So that a bar is never ahead of the archive, is always rebuildable from seconds, and cannot disagree with the chart's forming candle.

**Acceptance Criteria:**

**Given** `ml_signals/candle_store.py`, `ml_signals/candles.py`, `collector_core/build_candles.py` and the collector's direct `candle_store` calls (`collector_core/collector.py` `_apply_to_candle_store`, `_catch_up_candle_store`, `_candle_prune_loop`)
**When** the story ships
**Then** `platform/candles/` holds `domain/` (`CandleSeries` with the per-instrument watermark and `seconds_observed`/`partial` semantics, `fold_arrays`), `application/` (`apply_seconds` implementing the `SecondSink` `Protocol`, `forming_bar(rows: Sequence[SecondOHLC], bar_seconds) -> Bar | None`, `rebuild_day`, `prune`, the queries `window`/`latest`/`oldest_t`/`watermarks`, and a `VerifiedDays` service exposing `mark_verified`/`verified_status`) and `infrastructure/` (`CandleStore`, the only code that opens `candles_<venue>.db` rw; schema and `verified_days` table byte-identical); `collector_core/ports.py` declares `SecondSink` (the legacy capture module) and the collector calls `self._second_sink.apply(iid, flushed_rows)` only with rows whose `write_data` succeeded (`collector.py:1063-1073` semantics kept, with a test); each venue entrypoint constructs the candles adapter and passes it in, and the candle prune loop runs as a candles process manager started through `extra_loops`; `test_boundaries.py` whitelists the venue entrypoints as composition roots for that wiring

**Given** three seconds→bars folds today (`candle_store.fold_arrays`, `candles.aggregate_ohlc`/`candle_dicts_from_snapshots`, and the forming-bar arithmetic in `data_api/live_candles.py`)
**When** the story ships
**Then** exactly two folds exist in `platform/` (`kernel.fold.fold_trades` and `candles.domain.fold_arrays`), `ml_signals/candles.py`'s `aggregate_ohlc`, `candle_dicts_from_snapshots`, `build_candles` and `PARTIAL_OBSERVED_FRACTION` are retired (their callers in `data_api/live_candles.py`, `routes/rankings.py`, `routes/candles.py` call `candles.application.forming_bar`/`window`), and an equivalence test proves that `forming_bar` over a day of recorded seconds equals the stored closed bars for 1 m, 5 m and 1 h (the "source equivalence" of Story 21.2 restated over the single fold)

**Given** `compare_klines.py` and `prune_catalog.py` open the candle store themselves (`connect_rw`, `connect_ro` + `verified_status`)
**When** the story ships
**Then** both receive a `VerifiedDays` adapter injected by `nightly.py` and their own CLIs, never a connection; `python -m collector_core.build_candles` becomes `python -m candles.rebuild` with the same arguments (the old module is a shim that forwards `main`), `make nightly` and the README are updated, and the idempotent-rebuild test still passes

**Given** MR2 and MR4
**When** the story is merged
**Then** `ml_signals.candle_store`, `ml_signals.candles` and `collector_core.build_candles` are pure re-export shims with `REMOVE_AFTER = "24-3-..."`, every in-repo caller is updated, all three dockerfiles `COPY` `candles`, the Makefile test lists include `candles/tests`, `docs/DATA_DICTIONARY.md` §2.5/§5 and `ARCHITECTURE.md` cite the new paths, and the parent spine's Deferred entry "Writer→reader imports contradict AD-4" is struck as fully resolved

### Story 24.2: `views/` read models for both UIs, and the reader-side re-validation removed

As a trader reading the web UI and the TUI,
I want every number both surfaces show to come from one function over one input, and the chart to render what the gate approved,
So that the two UIs can never disagree and the reader never second-guesses the gate.

**Acceptance Criteria:**

**Given** `ml_signals/{ranking_columns,screener_columns_config,chart_indicators,chart_indicator_config,custom_indicators,book_features,footprint,chart_data}.py`, `data_api/{live_candles,redis_bus}.py`, the `catalog_stats` series reads (`query_second_snapshots`, `query_second_ohlc`, `second_ohlc_arrays`, `overview_table`) and the inline computations in `data_api/routes/*.py` and `bot_tui/*_state.py`
**When** the story ships
**Then** `platform/views/` holds `ranking_columns.py`, `coin_detail.py`, `chart_series.py`, `indicator_picker.py`, `live_candles.py` (declaring the `BarObserver` `Protocol` and keeping `LiveCandleBus`), `rankings_bus.py`, `preferences.py` (the one loader/saver for `chart_indicators.toml` and `screener_columns.toml`, full-rewrite TOML, key sets frozen) and the catalog series reads over `kernel.catalog_files`; an audit list in the story's Dev Notes names every computation found in `data_api/routes` and `bot_tui` state modules and each is moved into `views/` or shown to be pure formatting; `views` parses `snapshots:raw` only through `DydxSecondSnapshot.from_dict`, and `bot_tui`'s hand-indexed parsing is replaced by the same call

**Given** `data_api/routes/snapshots.py:129-133` skips empty-top and `bp >= ap` rows (the AD-3 deviation) and `:137-138` inserts gap markers
**When** the story ships
**Then** the two skips are deleted, the gap-marker insertion (`_SNAPSHOT_GAP_THRESHOLD_MS`) moves into `views/chart_series.py` as a rendering rule with its existing test, a test proves a crossed row written by the gate is returned unchanged by `/api/snapshots`, and the parent spine's Deferred entry "Reader-side crossed-book skip survived the `dashboard` → `data_api` move" is struck with an amendment

**Given** AD-D2's graph
**When** `test_boundaries.py` runs after the move
**Then** `views` imports only `kernel`, `observability` and the `candles`/`ranking` query services (`window`, `latest`, `forming_bar`, `verified_status`, `history`, `nearest`), `data_api` and `bot_tui` import `views`, `kernel`, `observability` and `alerting`'s application service only, and no `data_api` route or `bot_tui` module imports `ml_signals`, `collector_core`, `ranking_engine` or `common` directly

**Given** MR2 and MR4
**When** the story is merged
**Then** the moved `ml_signals` and `data_api` modules are pure re-export shims with `REMOVE_AFTER = "24-4-..."`, the `data_api` and collector images `COPY` `views`, the Makefile test lists include `views/tests`, the frontend's docs page (`frontend/src/pages/docs/`) and `ARCHITECTURE.md` name `views/`, and SSOT-01..05 in `platform/CLAUDE.md` cite `views/` as the one place

### Story 24.3: `alerting/` context as an observer of the forming bar

As a trader who set a price alert,
I want alerts evaluated on the same forming bar the chart shows and delivered through the one notifier,
So that an alert never fires on a bar the chart never drew, and my Telegram or webhook settings work for every kind of page.

**Acceptance Criteria:**

**Given** `data_api/alerts.py` (`Alert`, `AlertStore`, `RunState`, `evaluate`, `render`, `post_webhook`, `post_telegram`, `AlertEngine`) and `data_api/routes/alerts.py`
**When** the story ships
**Then** `platform/alerting/` holds `domain/` (`Alert`, `FiringPolicy` for `once_per_bar_close | once_per_bar | only_once`, `RunState`, `evaluate`, `render`), `application/` (`AlertEngine` implementing `views.BarObserver`, subscribe/unsubscribe of SSE queues) and `infrastructure/` (`AlertStore` over `alerts.toml`, key set frozen; `Deliverer` implemented over `observability.notify`); `data_api/app.py`'s lifespan is the only place `AlertEngine` is attached to `LiveCandleBus` (composition-root wiring, no `alerting → views` import edge beyond the `BarObserver` type), `routes/alerts.py` is a thin adapter over `alerting.application`, and the frequency semantics tests from Story 20.2 pass unchanged

**Given** an alert names a channel, never a transport
**When** an alert fires
**Then** `Deliverer.deliver(alert, body)` calls `observability.notify(channel, title, body)` and the transport (Telegram, webhook URL) is chosen by the notifier's env configuration; the SSE stream, the `/api/alerts` contract and the frontend dialog are byte-for-byte unchanged (existing route tests pass)

**Given** MR2 and MR4
**When** the story is merged
**Then** `data_api.alerts` is a pure re-export shim with `REMOVE_AFTER = "25-1-..."`, the `data_api` image `COPY`s `alerting`, the Makefile test lists include `alerting/tests`, and `ARCHITECTURE.md` and the data dictionary cite `alerting/`

### Story 24.4: `research/` as a pure consumer, with its broken tests repaired

As a strategy researcher,
I want the backtest strategies, runners and notebooks in one context that only reads the catalog, the ranking history and the watchlist API,
So that research can never leak a computation back into the live path, and its test suite runs green again.

**Acceptance Criteria:**

**Given** `ml_signals/strategies/*`, `ml_signals/{run_backtest,watchlist}.py`, `ml_signals/BACKTESTING.md`, `ml_signals/backtest.ipynb`, `dydx_collector/notebooks/`
**When** the story ships
**Then** `platform/research/` holds `strategies/`, `run_backtest.py`, `watchlist.py` (HTTP to `/api/rankings` only; `test_boundaries.py` asserts no `data_api` import), `notebooks/` and `BACKTESTING.md`; backtests still reference strategies by `ImportableStrategyConfig` string path (the paths change to `research.strategies...` and the docs say so); every research catalog read goes through `kernel.catalog_files` or `BacktestDataConfig`; research computes no rolling metric of its own (pct/volatility come from `metrics.db` via the ranking query service)

**Given** `ml_signals/tests/{test_snapshot_backtest_node,test_timeframe_backtest,test_watchlist_multi_coin_backtest}.py` fail at collection (`from ml_signals import backtest_dydx` while the module lives in `strategies/`) and `test_ofi_strategy*.py` fail on a stale `ma_period` keyword
**When** the story ships
**Then** all five modules are repaired against the real strategy API (never by deleting a test or loosening an assertion), run in `make test` under `research/tests`, and their pass is recorded in the story's Completion Notes together with the root cause of each break (TEST-04)

**Given** MR2 and MR4
**When** the story is merged
**Then** the moved `ml_signals` modules are pure re-export shims with `REMOVE_AFTER = "25-2-..."` (the `ml_signals` package is retired for good in Story 25.2), the collector image `COPY`s `research`, the Makefile test lists include `research/tests`, and `platform/CLAUDE.md` NAUT-03 and the README's backtest section cite the new paths

## Epic 25: Archive, ranking, bots and collection control as aggregates

Third epic of the DDD migration (spine AD-D9, AD-D10, AD-D15, AD-D17, AD-D18; MR8–MR11). Four contexts with disjoint files, moved in the fixed order 25.1 → 25.2 → 25.3 → 25.4; each leaves the wire contracts of AD-9/AD-10/AD-11 and `collector:status`/`collector:control` unchanged. MR1, MR2, MR4 and MR14 apply to every story.

### Story 25.1: `archive/` context: `ArchiveDay`, one deleter, one rewriter, one writer per leaf

As the platform operator,
I want the nightly maintenance to be one saga over an explicit day state machine, with one code path that deletes files and one that rewrites them,
So that a rebuild can never zero rows over an archive gap, a reconcile can never pass on an unrebuilt day, and a repair can never collide with a running collector.

**Acceptance Criteria:**

**Given** `collector_core/{rebuild_seconds,consolidate_catalog,prune_catalog,repair_catalog,compare_klines,nightly,backfill_bars,migrate_open_interest,measure_lag}.py`, `dydx_collector/normalize_snapshot_schema.py`, the `catalog_stats` diagnostics (`data_file_ranges`, `find_gaps`, `likely_outages`, `coverage`) and `DydxCollector._prune_loop`
**When** the story ships
**Then** `platform/archive/` holds `domain/` (`ArchiveDay` with `DayStatus`, `RetentionPolicy`, `ReconciliationResult`, `ArchiveGap` handling over `kernel.archive_markers`), `application/` (`rebuild_day`, `consolidate_day`, `reconcile_day`, `prune`, `backfill_bars`, `diagnostics`, the `nightly` saga with `Step`/`StepResult`), `infrastructure/` (`catalog_files.py` — the `CatalogFiles` adapter, `klines_<venue>.py` over `kernel.venue_http`) and `tools/` (`measure_lag`, `migrate_open_interest`, `normalize_snapshot_schema`); every operator CLI keeps its arguments under `python -m archive.<tool>` with the old module paths forwarding; `make nightly`, `make consolidate`, `make prune`, `make backup-catalog` and the cron line in the README are updated

**Given** four in-place `pq.write_table` rewrite sites and two `write_data()` callers in the tools
**When** the story ships
**Then** `CatalogFiles.rewrite(path, table)` (temp-then-rename, Arrow metadata preserved, zstd via `kernel.parquet_compat`) is the only in-place rewriter and the four sites call it; `backfill_bars` and `repair_catalog` remain the only offline `write_data()` callers and are listed in the data dictionary; a test proves a rewritten file's schema metadata and row order are unchanged

**Given** AD-D9's state machine and AD-D18's leaf rule
**When** the story ships
**Then** `verified_days` (through the `VerifiedDays` port from 24.1) is the only persisted day status; `reconcile_day` refuses, ledgering `reconcile.not_rebuilt`, unless invoked by a saga run whose `rebuild_day` for the same (venue, day) succeeded (`StepResult` passed in-process; standalone `compare_klines` requires `--rebuilt-by <run id>`); `rebuild_day` leaves every row inside an `ArchiveGap` span untouched and reports the count; `RetentionPolicy` is the only code that deletes a catalog file and covers verified-and-aged `trade_tick/`, dropped-instrument retention (`non_config_retain_hours`, read from the venue plan file) and per-instrument `order_book_deltas` retention, so `DydxCollector._prune_loop` is deleted; the collector writes `<catalog>/.capture-<venue>.lock` for its whole run (the one capture edit in this story, with a test), `repair_catalog` refuses with `repair.capture_running` while that lock is held, and no archive tool writes a file whose `ts_init` span intersects the current UTC day (asserted in `CatalogFiles`)

**Given** MR2 and MR4
**When** the story is merged
**Then** the moved modules are pure re-export shims with `REMOVE_AFTER = "25-3-..."`, the collector image `COPY`s `archive`, the Makefile test lists include `archive/tests`, `docs/DATA_DICTIONARY.md` §6 and `DATA_INTEGRITY_AUDIT.md` D-36/D-45..D-51 cite the new paths, and `platform/CLAUDE.md` DATA-05/DATA-06 name `archive.` tools

### Story 25.1a: Rankings web-only: the ranking-mode toggle moves to the web and the TUI's Coins pane is deleted

Pulled forward from Epic 29 on 2026-09-26 (operator decision: "from now on all rankings are in the web only; the TUI is for controlling bots and collector settings"). It runs before Story 25.2 so that 25.2 (ranking context), 25.4 (collection control) and 26.3 (closeout) never refactor, shim or re-point modules that are about to be deleted: `bot_tui/coins_pane.py` imports `views.ranking_columns`, `bot_tui/coin_detail_state.py` imports `views.coin_detail` and `kernel.second_snapshot`, and `bot_tui/ranking_state.py` owns the only ranking-mode publisher. Rules: TUI-02, TEST-04, MR4; `rankings:live`, `collector:status`, `collector:control` and `bots:*` payloads unchanged.

As the bot and collector operator,
I want the TUI to show only what it controls (bots and the collector) and the rankings, including the ranking-mode switch, to live in the web only,
So that there is exactly one rankings UI and no later refactor carries TUI rankings code that is about to be deleted.

**Acceptance Criteria:**

**Given** the ranking mode is global and last-write-wins (`ranking_engine` today; Story 25.2's `RankingBoard` keeps the same rule) and today only the TUI's `m` key can change it
**When** the story ships
**Then** `data_api` gains `PUT /api/rankings/mode` (`{"mode": "volume" | "volatility"}`) that publishes the same Redis message `bot_tui/ranking_state.py`'s `publish_mode_toggle` sends today (recorded byte-for-byte in a replay test), the rankings page shows the current mode from the `rankings:live` payload with a two-state control next to the venue chips, an unknown mode is a 422, and `docs/DATA_DICTIONARY.md` §3 records the channel and payload; the web control ships in the same commit that removes the TUI's `m` key below, so the mode is never unreachable

**Given** `bot_tui/{coins_pane,coin_detail,coin_detail_state,ranking_state}.py` and the Coins-pane parts of `bot_tui/app.py` (the `/` inline filter, the `m` mode toggle, the `o` dashboard deep-link, the Enter Coin-detail view, the `j`/`k` row focus, the stale-feed banner, `COLD_OPEN_TEXT`/`NO_MATCHES_TEXT`)
**When** the story ships
**Then** those four modules and their tests are deleted, `app.py` starts on the Bots pane with the Collector pane as the second and last pane, no `rankings:live` or `snapshots:raw` subscription remains anywhere under `bot_tui/` (a grep test), the footers and the `:help` text list only the keys that still exist, `views/ranking_columns.py` keeps `RANKING_COLS` as the web's single column source (its urwid colour tuple members and `NEGATIVE_COLOR`/`POSITIVE_COLOR` are removed only if the web does not read them; record which), `bot_tui/tests` pass with real Redis where they did before, and `test_boundaries.py`'s legacy map loses the entries for the deleted modules

**Given** MR4
**When** the story is merged
**Then** `test_images.py`'s expectations for the `bot_tui` image drop the removed modules, `ARCHITECTURE.md`'s module map shows `bot_tui` reading `bots:*` and `collector:status` only, `docs/BOT_OPERATIONS.md` and `platform/README.md`'s TUI section describe the two-pane TUI, and the Story 22.10 AC that names "the bot_tui coins pane" is struck in `epics.md` with an amendment pointing here

### Story 25.1b: `archive` service: the nightly maintenance scheduled in our own code, no host cron

Added 2026-09-26 (operator decision: "no cron, it is a Linux thing, not part of my code; a small service"). Story 25.1 builds `archive/`'s operator tools as `python -m archive.<tool>` CLIs, still started by the host crontab line in `docs/DEPLOY_CHECKLIST.md` §1. This story moves that schedule into a small long-running service of our own. Python, not Go (decided the same day): every step it runs is Python on Nautilus's catalog, so a Go scheduler would need a second runtime in the image or the Docker socket. Not inside `data_api`: that service mounts the catalog, candles and metrics `:ro` by design, restarts on every frontend redeploy, and serves the dashboard from the memory the rebuild would compete for. Rules: MEM-01 (every step stays a separate subprocess so its RSS returns to the OS), DATA-07 (every failure is one ledger entry), OBS-01, no new dependency (plain asyncio, no APScheduler), DESIGN-01, MR4.

As the platform operator,
I want the nightly archive maintenance, the consolidation and the catalog backup to be run by a service in `docker-compose.yml`, with its status on the dashboard and a "run now" control,
So that nothing depends on a host crontab, a reboot or redeploy at night never loses a day, and I can see when maintenance last ran and whether it passed.

**Acceptance Criteria:**

**Given** the host cron line (`docs/DEPLOY_CHECKLIST.md` §1: `make nightly VENUE=<v>` per venue, then `make consolidate`, then `make backup-catalog`)
**When** the story ships
**Then** `platform/archive/scheduler.py` (composition root, `python -m archive.scheduler`) runs the same steps in the same order through `archive.application.nightly`'s `run_steps` (each step a subprocess, `;` semantics: one venue's failure never skips the next venue or the backup), at a daily UTC time from `platform/archive/config.toml` (`nightly_at = "03:07"`, `venues = ["DYDX", "BYBIT", "HYPERLIQUID"]`, the backup target), with a pure `next_run(now, schedule, last_success)` in `archive/domain/` tested across midnight, DST-free UTC, and a clock that jumps; `docker-compose.yml` gains an `archive` service (collector image, `command: python3 -m archive.scheduler`, catalog and candles mounted `rw`, `restart: always`, the shared logging anchor, `ERROR_LEDGER_SERVICE: "archive"`), started by `make up`

**Given** a night the service was down (reboot, redeploy, crash)
**When** it starts
**Then** it reads each venue's last successfully maintained day (from the nightly summary it persists under `platform/data/archive/state.json`, written atomically) and runs every missed closed day oldest first before sleeping, capped at `catch_up_max_days` (default 7) with one `archive.catch_up_capped` ledger entry when the gap is larger; a day is "successful" when `run_steps` returned without a FAILED step for that venue

**Given** the maintenance lock `.consolidate.lock` (Story 25.1) and the capture lock
**When** a scheduled run overlaps a manual `make nightly`/`make consolidate`
**Then** the scheduler waits for the lock (bounded, `lock_wait_minutes`, default 60) instead of failing the night, ledgers `archive.lock_wait` once per wait and `archive.lock_timeout` if the bound passes, and never runs two of its own jobs at once

**Given** the small, rarely-updating types (mark/index price, funding rate, open interest, instrument status) leave one ~1–7-row file per instrument per minute until the nightly consolidate (a 4 KB disk block each; ~68k files per type measured on the dev box, 2026-09-26)
**When** the story ships
**Then** the scheduler also runs `consolidate_catalog` for the current day's *closed hours* of those types every `intraday_consolidate_hours` (default 4) under the same lock, the snapshot, trade and delta leaves excluded (they stay nightly-only, after the rebuild), with the per-hour merge rule and the open-hour exclusion added to `archive/application/consolidate_day.py` and tested (a merged hour never includes a file reaching the current hour; the nightly merge of that day later absorbs the hourly files)

**Given** the operator needs to see and trigger maintenance without SSH
**When** the story ships
**Then** the scheduler publishes `archive:status` on Redis after every step (`{"next_run", "running", "last_run": {"run_id", "day", "started", "finished", "steps": [{"venue", "name", "exit", "duration_s"}]}}`) and listens on `archive:control` for `{"command": "run_now", "day": "YYYY-MM-DD" | null}` (null = yesterday); `data_api` gains read-only `GET /api/archive/status` and `POST /api/archive/run` (which only publishes the control message: `data_api` never writes the catalog), the web UI shows the status with a "Run now" button behind a confirm, `bot_tui`'s Collector pane shows the same status line, and `docs/DATA_DICTIONARY.md` §3 records both channels

**Given** MR4 and the deploy docs
**When** the story is merged
**Then** `docs/DEPLOY_CHECKLIST.md` §1 becomes "remove the old cron line" with the one command to check it is gone, `make nightly`/`make consolidate`/`make backup-catalog` stay as manual tools (documented as such), `ARCHITECTURE.md` and the DDD spine's `archive` row name the service and strike "the nightly cron" (spine line ~250) with an amendment, `test_images.py` covers the `archive.scheduler` entrypoint, `archive/tests` cover the scheduler loop with an injected clock and a fake step runner, and `platform/CLAUDE.md` DATA-05/06 name the service as the one place maintenance is scheduled

### Story 25.2: `ranking/` context: `RankingBoard` replaces the module globals

As a trader watching the rankings,
I want the ranking engine to be one aggregate whose every input arrives through a named port,
So that its behaviour is unit-testable, a second copy of a rolling metric can never appear, and a venue's volume source is one adapter.

**Acceptance Criteria:**

**Given** `ranking_engine/{engine,metrics_store,price_series,volatility}.py` with twelve mutable module globals (`engine.py:117-196`), `ml_signals/{metrics_computer,rank_history}.py` and the `catalog_stats` price math (`price_series`, `price_stats_from_series`, `price_stats`)
**When** the story ships
**Then** `platform/ranking/` holds `domain/` (`RankingBoard` owning `mode`, per-instrument `InstrumentMetrics`, `RankingsPublisher`; `RankingMode`, `VolumeReading`, `VolatilityScore`; `price_series.py`, `volatility.py`, `metrics.py` — the pct/volatility math, ranking's alone), `application/` (`ports.py` with `VolumeSource`, `PriceHistory`, `RankingHistory`, `LivePublisher`; `engine.py` with `ingest_snapshot_batch`, `switch_mode`, `volume_cycle`, `slow_loop`, `heartbeat`, constructed at `__main__`) and `infrastructure/` (`redis.py`, `metrics_store.py`, `catalog_prices.py` over `kernel.catalog_files`, `volume_<venue>.py` over `kernel.venue_http` — `engine.py`'s own URL maps, `_USER_AGENT` and timeouts retired); `test_boundaries.py` fails any module-level mutable runtime state in `ranking/`; the `ml_signals` package is deleted (its last shims expire here)

**Given** AD-9 and Story 22.10's invariants
**When** `RankingBoard` is tested
**Then** invariant tests cover: both scores present on every row; a row without a fresh USD volume absent from volume mode and present in volatility mode with one `ranking_engine.volume24h` ledger entry per poll; stale instruments aged out; mode global and last-write-wins; publish on change and on heartbeat; and a replay test proves the `rankings:live` payload for a recorded `snapshots:raw` burst is byte-identical before and after the move

**Given** SSOT-02
**When** the story ships
**Then** `views` and `research` read `pct_1h`/`pct_24h`/`volatility` only from `rankings:live`/`metrics.db` through the ranking query service (`history`, `nearest`), a grep-based test asserts no second implementation of `price_stats_from_series` exists, and `ranking` parses `snapshots:raw` only through `DydxSecondSnapshot.from_dict`

**Given** MR2 and MR4
**When** the story is merged
**Then** `ranking_engine` is a pure re-export shim with `REMOVE_AFTER = "25-4-..."`, compose's `ranking_engine` service runs `python -m ranking`, the collector image `COPY`s `ranking`, the Makefile test lists include `ranking/tests`, `docs/DATA_DICTIONARY.md` §3 and `platform/CLAUDE.md` SSOT-02/"Adding a venue" step 7 cite the new paths, and the parent spine's Deferred entry "`open_interest` vs `volume24h` polling live in different namespaces" is struck as resolved by ownership

### Story 25.3: `bots/` context: paper and non-paper as types, Nautilus behind an ACL

As the bot operator,
I want the trading runtime wrapped as one context whose aggregates make the paper/real split a type and whose only view of Nautilus is a strategy-scoped reader,
So that a config key or a list reorder can never promote a bot to real money, and the TUI's status and history contracts stay exactly as they are.

**Acceptance Criteria:**

**Given** `live_paper/{config,node,strategy,bot_status,trade_history,fills_store,venues}.py`
**When** the story ships
**Then** `platform/bots/` holds `domain/` (`Bot` with `BotId == order_id_tag`, the bounded `Incident` list and heartbeat state; `FillLedger` with per-fill realized PnL and the day/week/month/all buckets of AD-10; `PaperFleet` and `ExecBot` as distinct aggregate types), `application/` (`supervise` — status heartbeat and `bots:control` handling; `history` — the refresh cycle), `infrastructure/` (`nautilus_host.py` building the one `TradingNode` per process with one data + Sandbox exec client per venue from the `VENUES` table; `cache_reader.py` filtering `cache.positions_open/closed(strategy_id=...)`; `fills_store.py`; `redis.py`; `config.py` with the two loaders, `load_paper_config` still rejecting a `mode` key and `ExecConfig.mode` still validated against `environment`) and `strategies/` (`DummyStrategy`, framework code); no module outside `bots/infrastructure/nautilus_host.py` imports `TradingNode` (asserted by `test_boundaries.py`)

**Given** AD-10/AD-11's wire contracts
**When** the story ships
**Then** `bots:status`, `bots:control`, `bots:history:*` and `bots:incidents:*` payloads are byte-identical (replay tests against recorded messages), `bot_tui` needs no change, `docker-compose.yml`'s `live-paper` service runs `python -m bots` with the same env and mounts (`./data/live_paper:/app/live_paper/data` keeps the container path), and `live_paper.dockerfile` `COPY`s `bots`, `kernel`, `observability` (proven by `test_images.py`)

**Given** MR2 and MR4
**When** the story is merged
**Then** `live_paper` is a pure re-export shim package with `REMOVE_AFTER = "26-1-..."`, the `test-live-paper` Makefile target runs `bots/tests` (the two host-dependent `test_node.py` tests stay deselected there with the reason recorded), `docs/BOT_OPERATIONS.md`, `live_paper/README.md` (moved to `bots/README.md`) and `platform/CLAUDE.md`'s `live_paper` exception clause cite `bots/`

### Story 25.4: `collection_control/` context: the plan is the intent, the applied set is the fact

As the collector operator,
I want a venue's collected instruments to be one plan aggregate with a cap and USD-classified pins, applied by capture with an explicit report of what actually subscribed,
So that the TUI never shows an instrument as collected that the feed never applied, and control can never reach the gate or delete catalog files.

**Acceptance Criteria:**

**Given** `DydxCollector`'s control plane (`dydx_collector/collector.py:369-625`: `_subscribe`/`_unsubscribe`, `_apply_config`, `_status_loop`, `_publish_status`, `_reload_config_loop`, `_apply_and_persist`, `_handle_control_message`, `_publish_removed`, `_pin_top_liquid`, `_control_loop`), `dydx_collector/config.py` and `classify_liquidity`
**When** the story ships
**Then** `platform/collection_control/` holds `domain/` (`CollectionPlan(venue)` with `instruments`, `exclude`, pins and `cap` = 30 for dYdX; invariants `exclude ∩ collected = ∅`, `|collected| ≤ cap`, pins admitted only by `classify_liquidity` on USD volume; `LiquidityTier`; commands `add`/`remove`/`pin`/`unpin`/`exclude`/`reload` returning a plan diff), `application/` (`ControlService` for `collector:control`, `StatusPublisher` for `collector:status`, the reload loop) and `infrastructure/` (`CollectionPlanStore` over `platform/data/dydx_config.toml`, full rewrite, comment loss as a `Known limit:`; redis); the dYdX entrypoint starts these as `extra_loops` and `DydxCollector` keeps only its book hooks

**Given** AD-D17's applied-set rule
**When** a plan diff is applied
**Then** `Collector.apply(plan_diff)` (the legacy capture class, one new method) returns `Applied(subscribed, unsubscribed, failed)`; the sampler iterates `applied ∩ plan`; a `failed` instrument is `pending` on `collector:status`, ledgered `collector.subscribe_failed` once per attempt and retried by capture; a `LiveBook`/book state exists only for a subscribed instrument and is cleared on `unsubscribed`; an unsolicited message for a non-applied instrument is counted at `collector.unplanned_message`, not booked; tests cover a subscribe that fails on the wire and an unsubscribe that fails

**Given** one `config.toml` with two schema owners today
**When** the story ships
**Then** `collector_core/config.py` is the one loader, returning `(CoreConfig, CollectionPlan)` for every venue (Bybit/Hyperliquid plans are static tuples applied once through the same `apply`), control validates through it before `CollectionPlanStore.save`, the key set is frozen, and `bot_tui`'s collector pane reads an unchanged `collector:status` payload (replay test)

**Given** MR2 and MR4
**When** the story is merged
**Then** the moved `dydx_collector` modules are pure re-export shims with `REMOVE_AFTER = "26-2-..."`, the collector image `COPY`s `collection_control`, the Makefile test lists include `collection_control/tests`, and `platform/CLAUDE.md`'s "Adding a venue" steps 2–4 and `ARCHITECTURE.md` describe control as a separate context

## Epic 26: The gate as an aggregate

Last epic of the DDD migration (spine AD-D5, AD-D6, AD-D7, AD-D16; MR12, MR13). Capture moves last, in three stories: the domain shape first, in place, gated by the hot-path test (26.1); then the package and venue-package move with new entrypoints (26.2); then the closeout that removes the last shims and reconciles both spines with reality (26.3). MR1, MR2, MR4 and MR14 apply.

### Story 26.1: `LiveBook`, `TradeIntake`, `FeedGroup` and a pure `SecondSampler`, in place

As a collector maintainer,
I want the write gate to be one pure function over explicit per-instrument and per-venue aggregates, with venue variance supplied as pure policy values,
So that no venue can override the gate, every counter has a name, and the hot path is provably no slower than before.

**Acceptance Criteria:**

**Given** `collector_core/collector.py`'s per-instrument dicts (`_live_books`, `_last_book_update_ns`, `_crossed_since_ns`, `_resync_pending`, `_level_msg_id`, `_last_u`, pending deltas), trade dicts (`_second_trades`, `_seen_trade_ids`, the duplicate/stale/late/ahead counters, per-feed baselines), feed state (`_last_feed_message_ns`, feed states, backfill requests) and `_sample_tick`
**When** the story ships
**Then** `collector_core/domain/` holds `live_book.py` (`LiveBook` wrapping the Nautilus `OrderBook` by reference; `apply`, `clear`, `resync`, `snapshot_top(depth, now, policies) -> SampleVerdict`; pending `ts_event`-ordered deltas with the `hold_back + _VENUE_AHEAD_NS` overflow bound), `trade_intake.py` (`TradeIntake`: bounded id window, stale-history filter, per-feed first-copy arbitration with `duplicate` vs `duplicate_feed`, late/ahead counters, every counter reported at flush), `feed_group.py` (`FeedGroup`: `Feed` values, per-feed `FeedLiveness`, reconnect detection, one-sided outage comparison, `BackfillRequest` scheduling and abandonment), `sampler.py` (`SecondSampler.sample(...)` — pure, returns accepted snapshots, `SampleRejected`s and requested actions; closes second `S` at `S + 1 + hold_back_seconds` under `venue` time), `flush_batch.py` (`FlushBatch` with the `_TRADE_CARRY_NS` carry rule), `verdicts.py`, `events.py`; `Collector` becomes the application service that owns the loops, executes `ResyncRequested` through the client, and is the only ledger caller with every site listed in `collector_core/sites.py`; `LiveBook` is the only module that names the concrete order-book class (today the Cython `nautilus_trader.model.book.OrderBook`; Epic 28 may swap it for `nautilus_pyo3.OrderBook`) and `ArchiveWriter` is the only module that names the batch encoder (today `make_dict_serializer`; Epic 28 may swap it for a columnar one), so neither swap touches a caller; the empty-top-of-book rejection gains a rate-limited warning and the ledger site `collector.empty_top` (parent Deferred struck)

**Given** the venue hook overrides (`_apply_deltas`, `_handle_crossed_book`, `_clear_book_state`, `_instrument_ids`) in the three venue collectors and `dydx_collector/uncross.py`
**When** the story ships
**Then** venue variance is supplied as policy values passed to the aggregates: `CrossedBookPolicy.step(book, tags, now_ns) -> Uncrossed | StillCrossed(since) | ResyncRequested` (dYdX: the uncross ladder, synchronous; Bybit/HL: core default), `LevelTagger`, `SequenceCanary`, `BookTimeSource`, `BackfillCapability`; every policy is pure and synchronous (no logging, ledgering or `await`; `test_boundaries.py` treats policy modules as `domain/`), the three venue `Collector` subclasses override no core method other than `__init__`, and the DATA-04/DATA-08 tests (uncross ladder, Bybit `u` canary incl. the zero-level message case, Hyperliquid full-snapshot) pass against the policies

**Given** `test_hotpath.py`'s baseline from Story 23.1
**When** the refactored ingest path runs the same replay
**Then** allocations per message ≤ baseline and wall time per message ≤ 2× baseline; the numbers are recorded next to the baseline in `docs/DATA_INTEGRITY_AUDIT.md`; the story does not merge otherwise

**Given** the client contract docstring (`collector_core/collector.py:32-50`)
**When** the story ships
**Then** `collector_core/ports.py` declares `VenueFeed`, `VenueTradeHistory`, `ArchiveWriter`, `LiveStream`, `Notifier` as `Protocol`s alongside `SecondSink`; `trade_backfill.py`'s fetch/parse half moves into per-venue `trade_history.py` modules implementing `VenueTradeHistory` (fixture tests unchanged) and its scheduling half stays in the application layer; the Parquet write, quarantine, instrument-definition write and capture lock live in an `ArchiveWriter` adapter and the Redis publish in a `LiveStream` adapter; all 22.13/22.14/22.12 tests pass unchanged

### Story 26.1b: The off-site catalog backup is an explicit setting, off until a storage target exists

Added 2026-09-26 (operator: no cloud storage yet, the catalog lives on the VPS only). Placed in Epic 26's queue only so the running chain picks it up next; it touches `archive/` alone. Today `archive.backup_catalog` treats a missing `RCLONE_REMOTE`/`RCLONE_BUCKET` as an error (`archive/backup_catalog.py:125-132`: ledger entry, exit 1), so every nightly run reports a failed `backup_catalog` step and the error ledger fills with a condition that is a deliberate choice, which would also hide a real backup failure later (DATA-07: no muting, so the fix is to make the choice explicit, not to ignore the error).

As the platform operator,
I want to switch the off-site backup off on purpose while I have no cloud storage, and see that it is off,
So that a nightly run with no backup configured is green, and a backup that is switched on and fails is still loud.

**Acceptance Criteria:**

**Given** `platform/archive/config.toml` (every key required, unknown keys refuse start)
**When** the story ships
**Then** it gains `backup_enabled` (bool, required; the committed file sets `false` with a comment: "no off-site storage yet, the catalog exists only on this host; set true after configuring RCLONE_REMOTE/RCLONE_BUCKET, see README 'Nightly maintenance'"); with `false` the scheduler's chains contain no `backup_catalog` step, the service logs one WARNING at start ("off-site backup disabled: the catalog has no copy off this host") and `archive:status` carries `"backup": "disabled"` so the dashboard's archive panel shows it; with `true` behaviour is unchanged, and a missing `RCLONE_REMOTE`/`RCLONE_BUCKET` then refuses the service at start (config error, exit 1) instead of failing every night

**Given** `make backup-catalog` / `python -m archive.backup_catalog` run by hand
**When** no remote is configured
**Then** it still exits 1 with the existing message (a manual run asked for a backup), unchanged

**Given** MR4
**When** the story is merged
**Then** `archive/tests` cover both settings (no backup step and the status field when off; the start refusal when on without a remote), `docs/DEPLOY_CHECKLIST.md`'s 25-1b deferred entry drops the "run `make backup-catalog` once" step and gains an unchecked "when off-site storage exists: configure rclone, set `backup_enabled = true`" item, the README's nightly section says the backup is optional and off by default, and audit D-33 ("there is no other copy") stays open with a note that it is now an explicit, visible choice

### Story 26.2: `capture/` package and `capture/venues/<v>/` with new entrypoints

As a maintainer adding a fourth venue,
I want capture and its venue packages laid out as the spine's tree, with a venue being client + trade history + policies + config + entrypoint,
So that "Adding a venue" is a recipe over named files, and no `Collector` subclass exists anywhere.

**Acceptance Criteria:**

**Given** `collector_core/` (post-26.1), `dydx_collector/`, `bybit_collector/`, `hyperliquid_collector/`
**When** the story ships
**Then** `platform/capture/{domain,application,infrastructure}/` and `capture/venues/{dydx,bybit,hyperliquid}/` with `client.py`, `trade_history.py`, `policies.py`, optional `open_interest.py`/`book_snapshot.py`, `config.py` and `__main__.py` exist as the Structural Seed lists; each `__main__.py` is the composition root that builds the client, policies, adapters (`ArchiveWriter`, `LiveStream`, candles `SecondSink`, `Notifier`), the collection-control loops for dYdX, and calls `run_forever`; compose `command:` lines become `python -m capture.venues.<venue>`; `docker-compose.yml`'s `collector` service is renamed `dydx_collector` for symmetry only if the operator checklist records the container-name change, otherwise left as is (decide in the story, record the decision)

**Given** MR2 and MR4
**When** the story is merged
**Then** `collector_core`, `dydx_collector`, `bybit_collector`, `hyperliquid_collector` are pure re-export shim packages with `REMOVE_AFTER = "26-3-..."`, the collector image `COPY`s `capture`, the Makefile test lists include `capture/tests` and `capture/venues/*/tests`, `test_images.py` and `test_boundaries.py` pass with the full AD-D2 graph active (no unmoved package remains), `platform/CLAUDE.md`'s "Adding a venue" recipe is rewritten over the new files (steps 1–8, same evidence requirements, `capture/venues/<v>/trade_history.py` and `policies.py` added), `ARCHITECTURE.md`'s module map and diagram show the target tree, and `docs/DATA_DICTIONARY.md` §1 cites `capture/`

**Given** the deployed collectors, and the operator's decision (2026-09-26) that VPS steps are deferred rather than parked, so the loop never stops for them and the story still gets its independent review
**When** the story ships
**Then** the redeploy order (all three collectors in one `make redeploy-all`, the Dozzle check, `GET /api/errors` flat) is appended to `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section as one entry headed with this story's key and commit, and the story finalizes `done` through the normal review path: it does not park `awaiting-operator` and carries no `operator_actions`

### Story 26.3: Closeout: last shims gone, spines reconciled, guardrails permanent

As the platform owner,
I want the migration declared finished only when no shim remains, every `[TARGET]` in the DDD spine reads `[ADOPTED]` with a citation, and every parent Deferred item the migration resolved is struck,
So that the architecture documents describe the code that runs.

**Acceptance Criteria:**

**Given** the shims created by Stories 23.1–26.2
**When** the story ships
**Then** no module carrying `REMOVE_AFTER` exists under `platform/`, `test_namespace.py`'s shim assertions are retired with it, and `git grep -n "ml_signals\|collector_core\|dydx_collector\|bybit_collector\|hyperliquid_collector\|ranking_engine\|live_paper\|common\.venues"` over `platform/` (excluding `docs/` history notes and `.planning/`) returns nothing

**Given** the DDD spine's `[TARGET]` markers and `Today` columns and the parent spine's Deferred list
**When** the story ships
**Then** every `[TARGET]` is re-verified against the code and rewritten `[ADOPTED]` with `path:line` citations (or left `[TARGET]` with the reason and a Deferred entry), the `Today` columns are replaced by the target paths, the parent spine's resolved Deferred entries are struck with amendments, `troll/...` citations in both spines are re-pointed to `platform/...`, and the spine's Reviewer Gate (`lint_spine.py` + the version lens) is re-run clean

**Given** the guardrails
**When** the story ships
**Then** `test_boundaries.py`'s legacy map is deleted (every module is in a context), `test_images.py` and `test_hotpath.py` stay in `make test`, the hot-path baseline is re-recorded from the final tree, `docs/DATA_INTEGRITY_AUDIT.md` carries the final numbers, and `_bmad-output/implementation-artifacts/sprint-status.yaml` marks Epics 23–26 `done`

## Epic 27: Research notebooks on a shared analysis layer, with candlestick patterns detectable and tradeable

Research epic (FR70–FR79, NFR12), added 2026-09-22. It runs after Story 24.4 has landed `platform/research/` (the notebooks and strategies live there) and is otherwise independent of Epics 25–26: nothing here touches capture, archive, ranking's aggregates or the bots' aggregate types. The DDD spine (AD-D1) lists `research/` as a pure consumer with no aggregates; this epic keeps it a consumer (it still reads only the catalog, `metrics.db` and `/api/rankings`) but gives it a **domain layer of analysis value objects** so that a Sharpe ratio, a drawdown, a correlation matrix or a Monte Carlo distribution is computed exactly one way whether a notebook, a backtest report or (later) a bot report asks for it. Story 27.1 amends the spine's research row accordingly. Candlestick pattern detection (FR77/FR78) is the one piece that leaves `research/`: the detector is a pure streaming `Indicator` in `kernel/` so the chart picker, the screener's Technicals tab, backtests and `live_paper` all run the same code. Order inside the epic: 27.1 → 27.2 → 27.3 → 27.4 → 27.5 → 27.6 → 27.7 → 27.8 → 27.9; 27.3–27.6 depend only on 27.1 and 27.2. Rules that bind every story: NFR12 (no new dependency), NFR3/MEM-01 (every catalog read time-bounded), SSOT-02 (no metric recomputed outside its one home), TEST-04 (a `DeprecationWarning` or `FutureWarning` in a notebook run is a failure), DESIGN-01 (every value object and port docstring names its invariant), and MR4 (docs, dockerfile `COPY` sets and Makefile test lists updated in the same commit).

### Story 27.1: `research/domain` analysis value objects and the `research/application` ports

As a strategy researcher,
I want the numbers every notebook and backtest report shows to be typed values with named invariants, computed by one function each,
So that a Sharpe ratio in a notebook, in a backtest report and in a bot's history page can never disagree, and a notebook cell never carries analysis logic of its own.

**Acceptance Criteria:**

**Given** `platform/research/` as Story 24.4 left it (`strategies/`, `run_backtest.py`, `watchlist.py`, `notebooks/`, `BACKTESTING.md`)
**When** the story ships
**Then** `platform/research/domain/` holds `returns.py` (`ReturnSeries`: a float64 array of simple returns plus `period_seconds`; invariant: one period per series, so annualisation is `sqrt(periods_per_year)` from the stored period and never a caller-supplied constant; constructors `from_prices(prices, ts_ns)` and `from_equity(EquityCurve)`; `resample(period_seconds)` compounds, never averages), `equity.py` (`EquityCurve`: strictly increasing `ts_ns`, finite values, `starting_balance`; `drawdowns()` returns the underwater series and every drawdown episode as `(peak_ts, trough_ts, recovery_ts | None, depth)`; `from_pnl_by_day` matches `performance_metrics.equity_returns` bit-for-bit, proven by a test), `trades.py` (`TradeLedger`: closed trades as `(instrument_id, entry_ts, exit_ts, side, qty, realized_pnl, fees)`; invariant `exit_ts >= entry_ts`; `realized_pnls()` is the exact input `performance_metrics.trade_stats` expects), `report.py` (`MetricReport`: the `performance_metrics.all_metrics` dict frozen into a typed record with `as_table()`; it calls `all_metrics`, it never reimplements a statistic), and `correlation.py` (`correlation_matrix(aligned_returns) -> CorrelationMatrix` over numpy only, pairwise-complete on `None` gaps, `lead_lag(a, b, max_lag)` cross-correlation by lag, and `cluster(matrix) -> list[list[str]]` single-linkage hierarchical clustering on `1 - rho`, numpy only); every class docstring names the invariant it protects (DESIGN-01), and `research/domain/` imports only the standard library, numpy, `kernel/` and `nautilus_trader.model` (the spine AD-D2 layering rule with numpy admitted as pure arithmetic, recorded in `test_boundaries.py`)

**Given** the catalog, `metrics.db` and `BacktestNode`
**When** the story ships
**Then** `platform/research/application/` holds `ports.py` (`MarketFrames`, `RankingHistory`, `BacktestRunner` as `typing.Protocol`s) and `frames.py`, `ranking_history.py`, `backtest_runner.py` implementing them: `MarketFrames.seconds(instrument_id, start, end) -> pandas.DataFrame` (the `DydxSecondSnapshot` columns plus derived `mid`, `spread`, `microprice`, `obi_N` computed through `kernel.indicators`' stateless functions and never inline), `MarketFrames.trades(instrument_id, start, end)`, `MarketFrames.bars(instrument_id, bar_seconds, start, end)` (from the candle store via `candles.application.window`, never a third seconds→bars fold), `MarketFrames.funding(...)`, `MarketFrames.open_interest(...)`, `MarketFrames.mark_index(...)`; every read is time-bounded (`start`/`end` are required, no defaults) and goes through `kernel.catalog_files` or the catalog's typed `query` with `start`/`end` (MEM-01); `RankingHistory.history(instrument_id, days)` reads `metrics.db` through the ranking query service and research computes no pct/volatility of its own (AD-D10); `BacktestRunner.run(RunSpec) -> RunResult` wraps `BacktestNode` + `BacktestDataConfig` (NAUT-03), takes strategies by `ImportableStrategyConfig` string path, and returns `EquityCurve`, `TradeLedger` and `MetricReport` built from the engine's `PortfolioAnalyzer` and the fills report, plus the run's `config_id`; `BacktestRunner.sweep(RunSpec, grid) -> list[RunResult]` runs one `BacktestNode` with one `BacktestRunConfig` per grid point and re-attributes results by `config_id` (the Story 2.4 attribution rule, `ml_signals/strategies/backtest_dydx.py:42-56`)

**Given** `platform/CLAUDE.md` TEST-01 and the DDD spine's AD-D1 research row ("none (consumer)")
**When** the story ships
**Then** `research/tests/` covers every domain function with hand-computable cases (a 3-point equity curve with a known drawdown, a two-series correlation of exactly `±1`, a lead-lag of a shifted copy equal to the shift, `from_pnl_by_day` equality against `performance_metrics.equity_returns`), `BacktestRunner` against a two-day synthetic catalog built with `ParquetDataCatalog.write_data()` (real Nautilus objects, no mocks, TEST-03), the DDD spine's AD-D1 research row reads "value objects for analysis results (`ReturnSeries`, `EquityCurve`, `TradeLedger`, `MetricReport`, `CorrelationMatrix`, `MonteCarloResult`); ports `MarketFrames`, `RankingHistory`, `BacktestRunner`" with a `[amended 2026-..: Story 27.1]` note and the invariant list, `test_boundaries.py`'s research rows admit `research.domain` → numpy and nothing else new, `research/tests` is already in `make test` (24.4) and stays there, `ARCHITECTURE.md`'s module map names the two research layers, and `ml_signals/BACKTESTING.md`'s successor `research/BACKTESTING.md` documents `BacktestRunner` as the one way a notebook runs a backtest

### Story 27.2: Executable notebooks and the catalog inspection notebook

As a strategy researcher,
I want every notebook stored as reviewable source that the test suite executes against a fixture catalog, and a first notebook that shows me exactly what my archive contains,
So that a notebook can never rot silently after a refactor again, and I can see coverage, gaps, verified days and data quality per venue before I trust a research result.

**Acceptance Criteria:**

**Given** `research/notebooks/` and the `jupytext` dev dependency already pinned in `pyproject.toml`
**When** the story ships
**Then** every notebook under `research/notebooks/` is a jupytext-paired `<nn>_<name>.py` (percent format, the source of truth, `ruff`- and `mypy`-clean like any other module) plus a `<nn>_<name>.ipynb` with outputs stripped (a `research/tests/test_notebooks.py` check fails on any stored output cell, on any `.ipynb` without its `.py` twin, and on a pair whose cells differ); each notebook's first code cell is a **Parameters** cell reading `CATALOG_PATH`, `CANDLES_DIR`, `METRICS_DB_PATH`, `INSTRUMENTS`, `START`, `END` from environment variables with the `platform/data/` defaults, so the same file runs against the fixture catalog in tests and the real archive on the user's machine; a `make notebooks` target syncs every pair (`jupytext --sync`) and is documented in `research/README.md` together with the one-line local launch (`uv run jupyter lab research/notebooks`, Jupyter itself stays a personal tool and is never containerised)

**Given** the notebook smoke test
**When** `make test` runs
**Then** `research/tests/test_notebooks.py` builds one small fixture catalog per session with `ParquetDataCatalog.write_data()` (two instruments on each of dYdX, Bybit and Hyperliquid, ~10 minutes of `DydxSecondSnapshot`, `TradeTick`, `MarkPriceUpdate`, `IndexPriceUpdate`, `FundingRateUpdate`, `OpenInterest`, plus a candle store built by `candles.application.rebuild_day` and a `metrics.db` with two rows), executes every `.py` notebook with `runpy` under `warnings.simplefilter("error")` (TEST-04) with plotly's renderer forced off-screen, asserts each finishes in under 60 s, and lists the fixture's deliberate defects (one gap, one provisional day, one crossed second) so a notebook that claims to show them can be checked against them

**Given** the archive, the candle stores and the durable error ledger from Story 23.3
**When** `01_catalog_inspection` runs
**Then** it shows, per venue and instrument: the instrument inventory (`MarketFrames` over `catalog.instruments()`, market kind from `kernel.venues`), the coverage timeline and gaps (`kernel.catalog_files.data_file_ranges` and `catalog_stats.find_gaps` via `views`/`kernel`, never re-derived), likely outages versus quiet market, verified/provisional day status from the candle store's `verified_days`, raw-trade-archive-versus-folded-seconds volume agreement per day (the Story 22.13 fold, `kernel.fold.fold_trades`, re-run over the raw `trade_tick/` archive for the window), a snapshot sanity table (spread `>= 0`, best bid `<` best ask, `Price.precision` uniform per instrument, `ts_init - ts_event` distribution) and the ledger's rejection counts by site over the same window; every read is bounded by `START`/`END`; the parent spine's Deferred item "Rejection-rate observability for research use" is struck with an amendment naming this notebook and the ledger reader it uses (MR14); `dydx_collector/notebooks/dydx_catalog_pandas.ipynb` (moved by 24.4) is deleted, with its one still-useful cell (the `to_dict` catalog-to-pandas idiom) folded into this notebook's first section

### Story 27.3: Microstructure notebook

As a strategy researcher,
I want to inspect the microstructure of any collected instrument over any bounded window,
So that I can see spread, depth, imbalance, order flow, trade flow, seasonality and the return-autocorrelation and volatility structure before I design a signal.

**Acceptance Criteria:**

**Given** `MarketFrames.seconds` and `kernel.indicators`
**When** `02_microstructure` runs
**Then** it shows, for each instrument in `INSTRUMENTS` over `START`–`END`: the spread in ticks and basis points over time with its distribution by hour of day (UTC); the depth profile (cumulative size by level and by distance from mid) for both sides, from `views.book_features.depth_profile` over the snapshot's 20 levels; order-book imbalance at N = 1, 5, 10, 20 (`MultiLevelOBI`) and OFI (`MultiLevelOFI` replayed over consecutive seconds) with their z-scores at the window the OFI strategy uses; microprice-minus-mid as a predictor of the next-second mid change (a binned scatter with the hit rate per bin); trade flow (`buy_volume`, `sell_volume`, counts, CVD) and a Kyle-lambda style price-impact regression of mid change on signed volume by bucket size; the funding rate, mark-minus-index basis and open interest overlaid on price; return autocorrelation at 1 s, 10 s, 1 m, 5 m and 1 h lags (`ReturnSeries.resample`), the volatility signature plot (realised variance per sampling interval) and rolling realised volatility; every indicator is the `kernel.indicators` class, never a formula in a cell

**Given** the fixture catalog's deliberate defects
**When** the smoke test executes the notebook
**Then** the crossed second and the gap are visible as `None` gaps in the series (never interpolated, DATA-01), and no cell computes over an unbounded window

**Given** a reader who has not seen the platform's signal architecture
**When** they open the notebook
**Then** each section's markdown states what is read from the archive, what is derived on read and by which `kernel.indicators` class (SIGNAL-01), and the closing section lists the observations the notebook is designed to surface (spread regime changes, depth asymmetry, seasonality, autocorrelation sign at each horizon) with a pointer to the strategy that uses each (`research/strategies/ofi_strategy.py` for OFI)

### Story 27.4: Correlation and cross-venue notebook

As a strategy researcher,
I want return correlation, lead-lag and clustering across the collected universe and across venues for the same symbol,
So that I can pick uncorrelated instruments for a portfolio, find which venue leads on price, and see where the same asset trades at a basis across venues.

**Acceptance Criteria:**

**Given** `research.domain.correlation` and `MarketFrames`
**When** `03_correlation` runs
**Then** it builds, for `INSTRUMENTS` over `START`–`END`, aligned `ReturnSeries` at 1 m, 5 m, 1 h and 1 d (from `MarketFrames.bars`, so the seconds→bars fold is the candle store's), shows the correlation matrix at each horizon as a diverging heatmap (plotly, clustered order from `cluster()`), rolling 1-day correlation for every pair against a chosen anchor instrument, the clustering dendrogram as an ordered list with the merge distances, and the correlation of funding rates and of open-interest changes across the same instruments; missing bars align as gaps and the matrix is pairwise-complete (the domain function's contract), never forward-filled

**Given** the same symbol collected on more than one venue (BTC and ETH on dYdX, Bybit linear and Hyperliquid)
**When** the cross-venue section runs
**Then** it shows the mid-price basis between each venue pair in basis points over time, the lead-lag cross-correlation of 1 s returns for lags of ±1 s to ±30 s (`lead_lag`) with the peak lag and its sign stated in words ("Bybit leads dYdX by 2 s"), the funding-rate differential, and a trade-volume share per venue per hour; the symbol matching uses `kernel.venues` (`market_kind`, base/quote parsing), never string prefix guessing, and a venue absent from the fixture or the archive produces a stated "not collected in this window" line rather than an exception

**Given** the smoke test
**When** the notebook executes against the fixture catalog
**Then** the two instruments per venue produce a `2 × 2` matrix per venue and the BTC cross-venue section finds all three venues, and the notebook's markdown explains how to feed the clustered universe into a multi-instrument `BacktestRunner.run` (the Story 1.3 watchlist idea, restated on this epic's types)

### Story 27.5: Backtest evaluation notebook, parameter sweeps and walk-forward

As a strategy researcher,
I want to run any strategy by string path over any bounded window and read one standard evaluation page,
So that every strategy is judged by the same equity, drawdown, metric, sweep and out-of-sample views, with nothing recomputed by hand.

**Acceptance Criteria:**

**Given** `BacktestRunner` and `research/strategies/*`
**When** `04_backtest_evaluation` runs with `STRATEGY`, `STRATEGY_CONFIG`, `PARAMS` (the same three parameters `ml_signals/backtest.ipynb` had)
**Then** it shows the equity curve with the underwater (drawdown) series and each drawdown episode listed, the `MetricReport` table (Sharpe, Sortino, Calmar, max drawdown, profit factor, expectancy, win rate, avg/max win and loss, returns volatility, exactly `performance_metrics.all_metrics`' keys, no additions), rolling Sharpe over a configurable window from `ReturnSeries`, the per-trade PnL distribution and holding-time distribution from `TradeLedger`, PnL by hour of day and by day of week, and the trade list; the notebook never touches `BacktestNode` directly and never sums a PnL itself

**Given** `BacktestRunner.sweep`
**When** the sweep section runs with a two-parameter grid
**Then** it renders a heatmap of the chosen metric over the grid (plotly), lists the top runs with their full `MetricReport`, and states the grid size and total runtime; the walk-forward section splits `START`–`END` into `N` consecutive in-sample/out-of-sample folds, picks each fold's parameters on the in-sample metric and reports the concatenated out-of-sample equity and metrics next to the in-sample ones; both sections stream the catalog through `BacktestDataConfig` and never materialise the window twice

**Given** `ml_signals/backtest.ipynb` (moved to `research/notebooks/` by 24.4)
**When** the story ships
**Then** it is deleted, `research/BACKTESTING.md` points at this notebook as the one way to evaluate a strategy interactively, and the smoke test runs the notebook against the fixture catalog with `OFIStrategy` and a `2 × 2` grid in under 60 s

### Story 27.6: Monte Carlo and robustness notebook

As a strategy researcher,
I want the distribution of outcomes a strategy's trade record implies, not a single equity path,
So that I can see the drawdown I should expect, the probability of ruin, a confidence interval on Sharpe, and whether a sweep-selected parameter set is likely overfit.

**Acceptance Criteria:**

**Given** `research/domain/monte_carlo.py`
**When** the story ships
**Then** it holds, over numpy only and a caller-supplied `numpy.random.Generator` seed recorded in every result (`MonteCarloResult.seed`, so a figure is reproducible): `bootstrap_trades(TradeLedger, n_paths, seed)` (trade-order resampling with replacement, returning terminal-wealth, max-drawdown and Sharpe distributions), `block_bootstrap_returns(ReturnSeries, block_len, n_paths, seed)` (stationary block bootstrap preserving autocorrelation), `risk_of_ruin(paths, ruin_level)`, `sharpe_confidence_interval(ReturnSeries, n_paths, seed, level)`, `deflated_sharpe(observed_sharpe, n_trials, returns_skew, returns_kurtosis, n_obs)` (Bailey & López de Prado's deflated Sharpe ratio, the multiple-testing correction for a sweep), and `probabilistic_sharpe(observed, benchmark, n_obs, skew, kurtosis)`; each function's docstring names its invariant (paths never exceed `n_paths`, a resampled ledger has the same trade count, a zero-variance series returns a stated `None` rather than a division error) and cites the formula's source; tests check closed-form cases (a constant-return series yields an interval of zero width, a ledger of one trade yields identical paths, `deflated_sharpe` with `n_trials = 1` equals `probabilistic_sharpe`)

**Given** `BacktestRunner` and the domain functions
**When** `05_monte_carlo` runs
**Then** it takes a `RunResult` (or the notebook's own run with the same `STRATEGY` parameters as 27.5), shows the fan chart of bootstrapped equity paths with the observed path, the max-drawdown and terminal-wealth histograms with the observed values marked, risk of ruin at three ruin levels, the Sharpe confidence interval, and, when a sweep was run, the deflated Sharpe of the best grid point with a plain-language verdict line; every figure states its seed and path count; the smoke test runs it with `n_paths = 200`

### Story 27.7: Candlestick pattern detector in the kernel, on the chart, in the screener, and a scanner notebook

As a trader and researcher,
I want the classic candlestick patterns detected by one streaming indicator that the chart, the screener and a strategy all share, with no TA-Lib,
So that I can scan the whole collected universe for a pattern at any timeframe, see it on the chart, and later trade it with the same code.

**Acceptance Criteria:**

**Given** `kernel/indicators.py` (pure `Indicator` classes) and NFR12
**When** the story ships
**Then** `kernel/candle_patterns.py` holds `CandlePattern(Indicator)` with `update_raw(open, high, low, close)` and outputs `value` (`+100` bullish, `-100` bearish, `0` none, TA-Lib's convention so the scanner tables read the same) and a `pattern` parameter selecting one of: single-bar `DOJI`, `DRAGONFLY_DOJI`, `GRAVESTONE_DOJI`, `HAMMER`, `HANGING_MAN`, `INVERTED_HAMMER`, `SHOOTING_STAR`, `MARUBOZU`, `SPINNING_TOP`; two-bar `ENGULFING`, `HARAMI`, `HARAMI_CROSS`, `PIERCING`, `DARK_CLOUD_COVER`, `TWEEZER_TOP`, `TWEEZER_BOTTOM`; three-bar `MORNING_STAR`, `EVENING_STAR`, `THREE_WHITE_SOLDIERS`, `THREE_BLACK_CROWS`, `THREE_INSIDE_UP`, `THREE_INSIDE_DOWN`; the geometric thresholds (`body_ratio`, `shadow_ratio`, `doji_body_ratio`, `trend_bars` for the prior-trend requirement of hammer/hanging-man/star patterns) are explicit constructor parameters with documented defaults, the module docstring defines each pattern in words and by inequality, the detector keeps only the last three bars (`O(1)` per bar, no history list), and `CandlePatternSet` runs every pattern over one bar stream and returns the fired names for the scanner; `kernel/tests/test_candle_patterns.py` has one hand-drawn bar sequence per pattern for the bullish, bearish and negative case, plus a property test that a bar sequence never fires both an `X` and its mirror; the DDD spine's AD-D3 kernel list and MR5 are amended to include `candle_patterns` with the invariant "one pattern definition, shared by views, research and bots"

**Given** `views`' native indicator catalog (`chart_indicators.INDICATOR_CATALOG`, an `IndicatorSpec` with `feed`, `outputs`, `panel`) and the screener's Technicals columns (`/api/rankings/technicals-values`, `screener_columns.toml`)
**When** the story ships
**Then** `CandlePattern` is registered in that catalog with `feed = ("open", "high", "low", "close")`, `outputs = ("value",)`, `panel = "histogram"` and its parameters JSON-safe (`pattern` as a string enum, listed in the picker's dropdown like `ma_type`); the chart page's picker offers it under the native category and draws the `±100` spikes in a histogram pane; the Technicals tab offers it as a column with a `pattern` parameter and a timeframe, so the screener becomes a pattern scanner across every collected instrument (the latest closed bar's `value` per instrument; sorting by the column groups the hits); `chart_indicators.toml` and `screener_columns.toml` key sets are unchanged (a new entry uses the existing `name`/`category`/`bar_seconds`/`params` keys); a `Known limit:` comment in `ChartPage.tsx`'s pane code records that pattern hits are histogram spikes, not on-candle markers, with the upgrade path (lightweight-charts series markers when the chart adopts them); frontend tests cover the catalog entry rendering and the column values; `views/tests` and `data_api/tests` cover the replay through the existing `replay_indicator` path with no special case

**Given** `dydx_collector/notebooks/candlestick_pattern_scanner.ipynb` (moved by 24.4; it pip-installs TA-Lib and `pandas_ta` at runtime and reads the retired `custom_dydx_minute_bar` directory)
**When** `06_candlestick_scanner` ships
**Then** it scans `INSTRUMENTS` over `START`–`END` at every timeframe in `TIMEFRAMES` from `MarketFrames.bars` (the candle store's fold, never a pandas resample of its own), filters hits by the EMA condition (`above`/`below`/`any` against `nautilus_trader.indicators.ExponentialMovingAverage`) and an optional pattern filter, lists hits in one table tagged by timeframe and direction, renders a candlestick chart centred on a chosen hit with the EMA overlaid and the hit marked (plotly, a parameter cell selecting the hit; no `ipywidgets`), and computes the forward return after each hit at 1, 5 and 20 bars with the hit rate per pattern as the notebook's research output; the old notebook is deleted, the smoke test runs the new one against the fixture catalog, and a one-off parity check against TA-Lib (run locally by the developer where TA-Lib is installed, not in CI, not a dependency) is recorded in the story's Completion Notes with the per-pattern agreement rate and every documented deviation

### Story 27.8: Candlestick patterns tradeable: `CandlePatternStrategy` in backtests and `live_paper`

As a trader,
I want a strategy that trades candlestick pattern signals with a trend filter and a defined exit, runnable by string path in a backtest, evaluated by the 27.5 notebook, and startable as a paper bot,
So that a pattern I found in the scanner is one config file away from a backtest and one more from a paper bot, on the same detector the scanner used.

**Acceptance Criteria:**

**Given** `kernel.candle_patterns.CandlePattern` and the `StrategyConfig` + `Strategy` conventions in `research/BACKTESTING.md`
**When** the story ships
**Then** `research/strategies/candle_pattern_strategy.py` holds `CandlePatternStrategyConfig(StrategyConfig, frozen=True)` (`instrument_id`, `bar_type` as a Nautilus bar-spec string, `long_patterns` and `short_patterns` as tuples of pattern names, `trend_ema_period` and `trend_condition` `above | below | any` matching the scanner's filter, `trade_size`, `exit_bars`, `stop_atr_multiple` with `atr_period`, `allow_short`) and `CandlePatternStrategy(Strategy)`, which subscribes the bar type, feeds one `CandlePattern` per configured pattern plus the EMA and ATR through `handle_bar`/`update_raw`, enters at the next bar's open on a fired pattern that passes the trend filter, exits after `exit_bars` bars or on the ATR stop or on an opposite-direction pattern, holds at most one position, and never imports collector, views or `data_api` code (DESIGN-02, `test_boundaries.py`); internal bar aggregation from `TradeTick` is the default (so every venue with a raw trade archive works), with `EXTERNAL` catalog bars used when the config's bar type says so

**Given** `BacktestRunner` and the evaluation notebook from 27.5
**When** `research/strategies/backtest_candle_pattern.py` runs (`run()` returning a `RunResult`, `__main__` printing the `MetricReport`)
**Then** it runs `CandlePatternStrategy` by `ImportableStrategyConfig` string path over a bounded window on `BacktestNode` + `BacktestDataConfig(data_cls=TradeTick)` (NAUT-03), `04_backtest_evaluation`'s parameters cell lists it as its second worked example (with a `long_patterns = ("HAMMER", "ENGULFING")`, `trend_condition = "above"` default), and `research/tests/test_candle_pattern_strategy.py` proves on a synthetic trade-tick catalog with one planted hammer that exactly one long entry occurs at the bar after the hammer, exits after `exit_bars`, and that no entry occurs when the trend filter fails; real Nautilus objects only (TEST-03)

**Given** `live_paper`'s one hard-wired `DummyStrategy` per bot (`live_paper/node.py`, `BotConfig`)
**When** the story ships
**Then** `BotConfig` gains `strategy: str = "dummy"` and `params: dict[str, Any] = {}` (optional keys with defaults, so every existing `config.toml` parses unchanged; the frozen key set is extended, not altered, and `live_paper/README.md` documents the two keys), `node.py` resolves `strategy = "candle_pattern"` to `CandlePatternStrategy` through Nautilus's `StrategyFactory.create(ImportableStrategyConfig(...))` from a string path (the mechanism `BacktestNode` uses; no `live_paper → research` import edge, and `test_boundaries.py` states why), `bot_id` still maps to `order_id_tag`, `bot_status`/`trade_history` need no change because they are strategy-scoped `Cache` reads (AD-11), `live_paper.dockerfile` `COPY`s `research` and `kernel` and `test_images.py` gains an explicit entry for the string-path import (its `ast` walk cannot see a string), `make test-live-paper` covers a `candle_pattern` bot constructing on the sandbox venue, and `docs/BOT_OPERATIONS.md` gains the config example; the VPS step "start one `candle_pattern` paper bot and confirm a fill in `bot_tui`" is appended to `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section as one entry headed with this story's key and commit; the story finalizes `done` through the normal review path and never parks `awaiting-operator`

### Story 27.9: Closeout: research README, notebook index, rules, and the last legacy notebook gone

As the platform owner,
I want the research context documented as one page with a notebook index and a recipe, the rules for notebooks in `platform/CLAUDE.md`, and no legacy notebook or `pip install` cell left anywhere,
So that the next notebook is written the platform's way by default and the docs describe the code that runs.

**Acceptance Criteria:**

**Given** the six notebooks and the analysis layer from 27.1–27.8
**When** the story ships
**Then** `research/README.md` replaces `research/BACKTESTING.md` (the backtesting content moves in unchanged, with a redirect stub for one release) and holds: the notebook index (number, purpose, inputs, the domain functions it calls, run time on the fixture), the "write a notebook" recipe (pair with jupytext, parameters cell, one section per question, analysis logic goes in `research/domain`, reads go through `MarketFrames`, run `make notebooks` and `make test`), the "add a metric" recipe (add to `performance_metrics` or `research/domain`, then the notebook, never the reverse), and the local launch instructions; `platform/README.md` and the frontend docs page link to it

**Given** `platform/CLAUDE.md`
**When** the story ships
**Then** it gains a "Research notebooks" section with `NB-01` (a notebook holds no analysis logic: every computation is a `research/domain`, `kernel` or `performance_metrics` call; a formula in a cell is a review failure), `NB-02` (every notebook is jupytext-paired, output-stripped, parameterised by the environment variables of 27.2, and executed by `make test` against the fixture catalog), `NB-03` (no `%pip`/`!pip` cell, no dependency outside `uv.lock`; a notebook that needs a library files a dependency decision first) and `NB-04` (every catalog read in a notebook is bounded by `START`/`END`; MEM-01 restated for notebooks), and its "Development philosophy" indicator note points at `kernel/candle_patterns.py` as the second custom-`Indicator` precedent

**Given** the repository
**When** the story ships
**Then** `git grep -n "pandas_ta\|talib\|%pip\|custom_dydx_minute_bar" platform/` returns nothing outside `docs/` history notes and `.planning/`, no `.ipynb` exists under `platform/` outside `research/notebooks/`, `ARCHITECTURE.md`'s module map and diagram show `research/{domain,application,notebooks,strategies}` and `kernel/candle_patterns.py`, `docs/DATA_DICTIONARY.md` gains a §"Research reads" listing which stored fields each notebook reads and which derived values it computes on read (SIGNAL-01), `sprint-status.yaml` marks Epic 27 `done` (27.8's VPS step stays in the deferred list and does not hold the epic open), and this epic's `Known limit:` comments (histogram-not-marker pattern display, no `ipywidgets` interactivity, Monte Carlo on closed trades only) are listed in the DDD spine's Deferred section with their upgrade paths

## Epic 29: Rankings web-only across exchanges, the TUI as a control surface, and the venue cutover from dYdX to Bybit + Hyperliquid

Product epic, added 2026-09-26 from the operator's decision to stop collecting from dYdX and collect Bybit `BTCUSDT`/`ETHUSDT` and Hyperliquid `SOL` instead, with the switch made only once the new venues are proven end to end. Two facts shape it. First, the rankings are already multi-venue: Story 22.10 lists one row per collected instrument from every venue, each rank entry carries `venue`/`venue_kind`/`market` (`ranking_engine/engine.py:751-754`; `RankingBoard` in `ranking/` after Story 25.2), and the web page has per-viewer venue chips plus `venue = X` filter conditions (`frontend/src/pages/RankingsPage.tsx:28-46,200-201,279-283`). What is missing is a way to see and sort the same coin across exchanges: the exchange lives only in the id's suffix and there is no base-symbol field, so `BTC` on Bybit and `BTC` on Hyperliquid cannot be lined up. Second, the operator wants rankings in the web only: `bot_tui` keeps its Bots pane and its Collector pane (Story 6.1: the dYdX plan's pins, excludes, `:start`, `:pintop`, every action through `collector:control`) and loses the Coins pane, the Coin-detail view and the `rankings:live` listener. The one thing that pane owns which nothing else provides is the `m` ranking-mode toggle (`bot_tui/ranking_state.py:100-113` publishes it); the web gains it before the TUI loses it. Order: 29.1 → 29.2 → 29.3 → 29.4 → 29.5 → 29.6 (the TUI rankings removal and the web ranking-mode toggle were pulled forward into Story 25.1a on 2026-09-26) (29.4 and 29.5, added the same day, give the TUI a name-only market browser to add coins from and a per-exchange count of what is collected; how many coins to collect is the operator's decision, made from outside research, so no cap or capacity estimate is built for Bybit or Hyperliquid; 29.6, added the same day, puts each bot's take-profit, stop-loss and position details on the Bots pane and gives `DummyStrategy` optional bracket exits so they can be seen working). The epic runs after Epic 25 (29.1 reads the rank entry `RankingBoard` publishes and 29.3's static Bybit/Hyperliquid lists are the `CollectionPlan`s Story 25.4 defines) and is independent of Epics 26–28. Rules that bind it: 25.2's wire rule (rank entry fields are added, never renamed or removed), SIGNAL-01 (the venue and symbol are derived from the id on read, in one kernel helper, never stored twice), TUI-02, DATA-01 (a stopped venue's rows age out as stale, never hidden), TEST-04, MR4 (docs, images and Makefile updated in the same commit), and the archive rule that a venue's catalog is never deleted by a cutover: dYdX's days keep being verified and pruned by the nightly until they age out under the normal retention.

### Story 29.1: Exchange and Symbol on the web rankings, sortable and filterable

As a trader comparing the same coin on several exchanges,
I want every rankings row to show its exchange and its base symbol as real columns I can sort and filter on,
So that `BTC` on Bybit and `BTC` on Hyperliquid line up next to each other.

**Acceptance Criteria:**

**Given** `kernel/venues.py` (`venue_of`, `venue_kind`, `market_kind`, `market_suffix`, `bybit_category`)
**When** the story ships
**Then** it gains `base_symbol(instrument_id) -> str`, pure and tested for every id shape the three venues emit (`BTC-USD-PERP.DYDX` → `BTC`, `BTCUSDT-LINEAR.BYBIT` and `BTCUSDT-SPOT.BYBIT` → `BTC`, `SOL-USD-PERP.HYPERLIQUID` → `SOL`, plus a Bybit id whose quote is not `USDT` (`USDC`, `USD`) and an id with a numeric prefix such as `1000PEPEUSDT-LINEAR.BYBIT` → `1000PEPE`), with the Bybit quote list a named constant and a `Known limit:` naming the ceiling (a symbol whose base itself ends in a quote name) and the upgrade path (the venue's instrument definition, which the catalog stores); the rank entry gains `symbol` computed by it next to `venue` (added field, 25.2's wire rule), `docs/DATA_DICTIONARY.md` §3 lists it, and the `rankings:live` replay test from 25.2 is extended with the new field

**Given** `views/ranking_columns.py`'s `RANKING_COLS` (the single column-metadata list the web mirrors) and `RankingsPage.tsx`
**When** the story ships
**Then** the table shows `Symbol` and `Exchange` as pinned columns between Rank and Instrument (rendered from `symbol` and `venue`, with `market` shown as a small tag on the exchange cell: `BYBIT · linear`, `BYBIT · spot`), both sortable (sorting by Symbol groups the same coin across exchanges, ties broken by the current rank), both available as `=` conditions in `FilterPanel` alongside the existing `venue`/`venue_kind` text fields, the venue chips unchanged, the instrument column narrowed accordingly without misaligning later columns (TUI-02), and `RankingsPage.test.tsx` covers: two rows with the same symbol on different exchanges sort adjacent, an exchange filter hides the other venue's rows, and a `symbol = BTC` condition shows both `BTC` rows

### Story 29.2: Collector pane shows every venue's plan

The TUI's rankings removal that used to open this story moved to Story 25.1a (2026-09-26), so it lands before Epic 25's refactors; this story keeps only the Collector pane change, which needs Story 25.4's per-venue `CollectionPlan`.

As the collector operator,
I want the Collector pane to show every venue's collected set, not only dYdX's,
So that the TUI is the one place I see and control what each exchange collects.

**Acceptance Criteria:**

**Given** the Collector pane (Story 6.1) and Story 25.4's `CollectionPlan` per venue
**When** the story ships
**Then** the pane shows every venue's plan, not only dYdX's: one section per venue with its collected set, pins, excludes and cap, the applied-set report (`Applied(subscribed, unsubscribed, failed)`, `pending` instruments marked) from `collector:status`, and the existing `p`/`x`/`:start`/`:pintop` actions enabled for dYdX and shown read-only with the reason ("static plan: edit `<venue>_collector/config.toml`") for Bybit and Hyperliquid until their plans accept commands; `collector:status` and `collector:control` payloads are byte-identical (replay tests), and `docs/BOT_OPERATIONS.md` and `platform/README.md`'s TUI section describe the two-pane TUI

### Story 29.3: Venue cutover: Bybit `BTCUSDT`/`ETHUSDT` and Hyperliquid `SOL` proven, then dYdX stopped

As the platform operator,
I want the two new venue sets collected and proven end to end (rankings, chart, candles, nightly verification) before the dYdX collector is stopped, with dYdX's archive left to the normal retention,
So that the switch never leaves a gap in what the platform shows and never deletes data.

**Acceptance Criteria:**

**Given** `bybit_collector/config.toml` (`BTCUSDT`/`ETHUSDT` linear + spot today) and `hyperliquid_collector/config.toml` (`BTC-USD-PERP`/`ETH-USD-PERP` today)
**When** the story ships
**Then** Hyperliquid's list becomes `["SOL-USD-PERP.HYPERLIQUID"]` (decision 2026-09-26: Solana on Hyperliquid, BTC/ETH on Bybit; the two Hyperliquid majors are dropped, reversible by config, recorded in the file's comment), Bybit's list keeps the four ids (linear for mark/funding/OI, spot for the spot book; the comment says why both stay), `ranking/`'s Hyperliquid volume poll and `views` need no change (proven by the existing tests), and `docs/DATA_DICTIONARY.md` §1's collected-set table is updated

**Given** `docker-compose.yml`'s `collector` service (dYdX) started by every `make up`
**When** the story ships
**Then** the service is gated by `profiles: ["dydx"]` the same way `live-paper` and `bot_tui` are, `make up` no longer starts it, `make up-dydx` / `make down-dydx` start and stop it explicitly, `make redeploy-all` and `redeploy-no-paper` follow, the nightly cron line keeps its `VENUE=DYDX` step (the archive keeps verifying and pruning dYdX's days until they age out; the deploy checklist says when the line may be dropped: once `verified_days` holds no dYdX day younger than the trade retention), `ranking`'s dYdX volume poll stays (a venue with no fresh rows publishes none, DATA-01), and every doc, knowledge-base entry and example string that uses a `.DYDX` id as the default (`frontend/src/pages/docs/kbData.ts`, `AlertsPage.tsx`'s example, `README.md`, `docs/*.md`) uses `BTCUSDT-LINEAR.BYBIT` instead, with the dYdX form kept where the text is about dYdX

**Given** the acceptance gate the operator asked for ("once it works")
**When** the story ships
**Then** `docs/DEPLOY_CHECKLIST.md` gains a §8 "Venue cutover" runbook with these checks in order, each with the command or URL and the expected result: (1) `make redeploy-all` with the new configs, dYdX still running; (2) within 10 minutes the rankings page shows `BTCUSDT-LINEAR.BYBIT`, `ETHUSDT-LINEAR.BYBIT`, `BTCUSDT-SPOT.BYBIT`, `ETHUSDT-SPOT.BYBIT` and `SOL-USD-PERP.HYPERLIQUID` fresh, with `volume24h` present for the linear and Hyperliquid rows, and an `Exchange` filter for each venue shows only its rows; (3) the chart loads 1 m candles and a forming bar for each of the five; (4) `GET /api/errors` shows no new collector site for either venue over one hour; (5) the next nightly (`VENUE=BYBIT` and `VENUE=HYPERLIQUID`) rebuilds, reconciles and marks that day `verified` for every one of the five (`verified_days`, `reconcile.*` ledger flat); (6) only then `make down-dydx`, and the rankings show dYdX's rows ageing out as stale and then gone, never hidden; (7) `make up` on a fresh boot brings up Bybit and Hyperliquid only. The runbook itself is the story's deliverable and is appended to `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section as one entry headed with this story's key and commit; the story finalizes `done` through the normal review path and never parks `awaiting-operator`; the operator runs it later and records the date and the last dYdX day collected in the checklist

### Story 29.4: Runtime collection control for Bybit and Hyperliquid

As the collector operator,
I want Bybit's and Hyperliquid's collected sets to be plans I can change while the collector runs, exactly as dYdX's is,
So that adding a coin on any venue is one control command with an applied-set report, never a config edit and a container restart.

**Acceptance Criteria:**

**Given** Story 25.4's `CollectionPlan` (Bybit/Hyperliquid as static tuples applied once) and `ControlService` (dYdX only)
**When** the story ships
**Then** every venue's plan accepts `add`/`remove`/`pin`/`unpin`/`exclude`/`reload` through the one `ControlService` and the one `collector:control` channel (payload gains a `venue` field, added not renamed; a message without it means dYdX for compatibility, replay-tested), the Bybit and Hyperliquid collectors run the same control loop, status loop and hot-reload as dYdX (`collector:status` per venue, byte-identical shape), the plan file for each venue is written back through `CollectionPlanStore` with the same frozen key set (`instruments` stays a flat list for these two venues), and `Collector.apply(plan_diff)` returns `Applied(subscribed, unsubscribed, failed)` for them with `pending` on failure and one `collector.subscribe_failed` ledger entry per attempt

**Given** `platform/CLAUDE.md`'s "Adding a venue" rule that a venue's limits are investigated, never inherited
**When** the story ships
**Then** each venue's WebSocket subscribe/unsubscribe limits are measured on the live endpoint (Bybit: args per request and requests per second; Hyperliquid: subscriptions per connection and per second) and recorded in `docs/DATA_DICTIONARY.md` §1 with the date and method, the control loop paces subscribes under the measured limit (a named constant per venue with the citation). Bybit's and Hyperliquid's plans have no `cap` (decision 2026-09-26: the operator decides how many to collect from outside research); `CollectionPlan`'s `cap` stays optional and dYdX keeps its 30

**Given** DATA-01 and the archive
**When** an instrument is removed from a plan
**Then** its book state is cleared, its rows stop, its catalog files are untouched (retention alone deletes, Story 25.1), and the rankings show it ageing out as stale, never hidden

### Story 29.5: Market browser in the Collector pane: search a venue's coins by name and add them

As the collector operator,
I want to type a coin name, see the matching markets each venue lists, and add one to that venue's collection with one key, with the pane showing how many coins I collect on each exchange,
So that I never look up an instrument id by hand or edit a config file to start collecting a coin.

**Acceptance Criteria:**

**Given** the ranking engine's per-venue market polls (`ranking/` after 25.2: every venue's full market list with 24 h USD volume, refreshed each cycle)
**When** the story ships
**Then** `ranking` publishes the per-venue market list on a new Redis channel `markets:live` (one message per poll: `{venue, ts, markets: [{instrument_id, symbol}]}`, symbol from `kernel.venues.base_symbol`; the list is names only by decision 2026-09-26, no volume or metrics), the TUI's Collector pane gains a `/` search box that filters that list by symbol or instrument id across all venues (case-insensitive substring, matching the web's filter semantics), each result row shows the name only (the instrument id, e.g. `SOL-USD-PERP.HYPERLIQUID`, which already names the coin, market and exchange) plus a `collected` marker when the id is in that venue's applied set; no volume, price or other metric is shown (decision 2026-09-26), and a cold open before the first message shows "waiting for markets:live" rather than an empty list

**Given** a focused result row and Story 29.4's control plane
**When** the operator presses `a`
**Then** the pane sends `add` for that instrument to its venue through `collector:control` behind the same type-to-confirm guard as `s`/`x`, the row turns `pending` until `collector:status` reports it applied (or `failed`, with the reason from the status payload), and an id already collected or excluded is refused in the pane with the reason before any message is sent

**Given** the operator decides how many coins to collect per exchange from outside research
**When** the pane renders
**Then** each venue section's header reads `<VENUE>: N collected` (N = that venue's applied set, `pending` ones counted separately as `+P pending`), the count updates from `collector:status` without a reload, and the pane enforces no Bybit or Hyperliquid limit and shows no capacity estimate; dYdX's existing cap of 30 still refuses `a` with "cap reached"

**Given** MR4
**When** the story is merged
**Then** `docs/DATA_DICTIONARY.md` §3 records `markets:live`, `docs/BOT_OPERATIONS.md` documents the browser keys, `bot_tui/tests` cover the search, the add flow with a fake control channel, the per-exchange count, dYdX's cap refusal and the cold open, and `test_boundaries.py` records `bot_tui` reading `markets:live` alongside `bots:*` and `collector:status`


### Story 29.6: Bots pane shows each bot's live take-profit, stop-loss and position details

Added 2026-09-26 at the operator's request. No bot places a take-profit or stop-loss today (`DummyStrategy` in `bots/strategies/dummy.py` submits market orders and exits by reversing on a signal), so the story gives `DummyStrategy` optional bracket exits as well as the display: without a strategy that rests protective orders the new columns could never be seen working. Story 27.8's `CandlePatternStrategy` ATR stop appears in the same columns without any change here if it rests as a stop order; nothing in this story alters 27.8.

As the bot operator,
I want every row in the Bots pane to show the bot's latest stop-loss and take-profit next to its entry price, with the rest of the position's details in Bot-detail,
So that I can see at a glance which bots are protected, how far each is from its exits, and which one holds an open position with no stop.

**Acceptance Criteria:**

**Given** `bots/infrastructure/cache_reader.py`'s `StrategyCacheReader` (the context's only view of Nautilus, strategy-scoped per AD-11) and the `bots:status` heartbeat built in `bots/application/supervise.py`
**When** the story ships
**Then** the reader gains a strategy-scoped read of the bot's protective orders from `cache.orders_open(instrument_id=..., strategy_id=...)` plus `cache.orders_emulated(...)` (an order held by Nautilus's `OrderEmulator` is not in `orders_open` and must still count), classified by the order itself and never by the strategy's class: a reduce-only `STOP_MARKET`, `STOP_LIMIT`, `TRAILING_STOP_MARKET` or `TRAILING_STOP_LIMIT` on the side that closes the open position is a stop-loss at its `trigger_price` (a trailing stop reports its current trigger, so the value follows the trail); a reduce-only `LIMIT` on the closing side is a take-profit at its `price`, and a reduce-only `LIMIT_IF_TOUCHED` or `MARKET_IF_TOUCHED` on the closing side is a take-profit at its `trigger_price`; a non-reduce-only stop or limit is an entry order and is never reported as protection; when several orders of one kind rest (scaled exits) the one nearest the current mid is reported with the count of the rest; a flat bot reports none of them

**Given** the same heartbeat and 25.2's wire rule (fields are added, never renamed or removed)
**When** the story ships
**Then** `bots:status` gains `stop_loss`, `take_profit`, `entry_price` (the position's `avg_px_open`), `mark_price` (the `PriceType.MID` price the payload already uses for unrealized PnL), `position_qty` (the position's unsigned `quantity`), `stop_loss_orders` and `take_profit_orders` (counts), `open_orders` (every open or emulated order of the bot, protective or not) and `last_fill_at` (UNIX nanoseconds of the bot's latest fill, from `fills_store`, or `null` before its first); the five price and quantity fields are JSON strings from `str(Price)`/`str(Quantity)` so the TUI shows the instrument's own precision and no value passes through `float` (the project's price-integrity rule), each is `null` when it does not apply (flat bot, no protective order, no mid yet), every existing field is byte-identical (replay test against a recorded pre-story message), and `docs/DATABASE_SETUP.md`'s `bots:status` row and `docs/BOT_OPERATIONS.md` list the new fields

**Given** `DummyStrategyConfig` and `BotConfig`'s frozen key set
**When** the story ships
**Then** both gain optional `take_profit_bps` and `stop_loss_bps` (`int | None = None`, each validated positive when set, extended not altered so every existing `config.toml` parses unchanged, and `bots/README.md` documents them with an example); when either is set, `DummyStrategy` enters through `order_factory.bracket(...)` (a market entry with a reduce-only `STOP_MARKET` stop-loss and a reduce-only `LIMIT` take-profit, only the configured legs present) with each exit price computed from the entry-time mid in `Decimal`, rounded to the instrument's price increment away from the entry so the distance is never smaller than configured, and built at the instrument's precision without a `float` round trip; before a signal reversal or a stop the strategy cancels its resting protective orders, so no orphaned reduce-only order survives a position change; with both unset the strategy behaves exactly as today (its existing tests unchanged)

**Given** `bot_tui/bots_pane.py`'s `format_bot_line`/`bot_detail_lines` and the TUI rules TUI-01 and TUI-02
**When** the story ships
**Then** each Bots-pane row gains `entry`, `sl` and `tp` columns, the `sl`/`tp` cells showing the price plus its signed distance from `mark_price` in percent (`58,900.0 -1.8%`), each price cell a `fit()`-bounded fixed width because price strings have no fixed length (TUI-02); a bot in a position with no stop-loss shows `none` in the `sl` cell in the warning colour (the text carries the meaning, the colour is an extra cue), a flat bot shows blank exit cells, and a message from a producer that predates the story (fields absent) shows `n/a`, never a fabricated value; the pane gains a one-line column header aligned to `format_bot_line`'s field widths; Bot-detail's snapshot header gains two lines for quantity, entry, mark, stop-loss and take-profit with their order counts, open orders, and time since last fill (`format_uptime`'s style, `no fills yet` before the first); the refresh keeps the persistent `ListBox` and in-place walker update (TUI-01), and the widened row is checked at the Bots pane's documented minimum terminal width, with `docs/BOT_OPERATIONS.md` updated if that minimum grows

**Given** TEST-03 and TEST-04
**When** the story is merged
**Then** `bots/tests` prove the classification on real Nautilus orders in a real `Cache` (long and short positions, each stop and if-touched type, a trailing stop after it moves, an emulated stop, a non-reduce-only entry stop excluded, two scaled take-profits reporting the nearer one and a count of two, a flat bot reporting nothing); a `DummyStrategy` bot with both bps keys set runs on the sandbox venue, its first entry leaves exactly one stop-loss and one take-profit resting at the expected rounded prices, and a reversal leaves no orphaned order; `bot_tui/tests` cover the row with both exits, with an unprotected position, flat, and from a pre-story message, plus the detail lines and the header's alignment with a long `bot_id` and a long price

**Given** that no committed bot trades often enough to show exits within minutes (the stress-test bots in `bots/config.toml` enter only when their trend and OFI thresholds cross)
**When** the story is implemented
**Then** it commits `bots/tests/fixtures/config.churn.toml`: one paper bot on `BTC-USD-PERP.DYDX` mainnet market data with Sandbox execution, `trend_buy_threshold = 0.0`, `trend_sell_threshold = -1.0` and `ofi_confirm_threshold = -1e9` so it goes long on the first `_maybe_trade` after its indicators initialize (about six minutes of 1-minute bars) and re-enters after every exit, `take_profit_bps = 5` and `stop_loss_bps = 5` so each position exits within minutes, and a header comment saying it is a mechanics fixture that is never deployed; plus a `make bots-churn-check` target that starts a local Redis if none is running, runs the bots image with the fixture mounted read-only over `/app/bots/config.toml` (the standalone-run form in `bots/DEPLOY_CHECKLIST.md`), `FILLS_DB_PATH` on a scratch path and host networking (the dev box's default Docker bridge stalls venue TLS handshakes), subscribes to `bots:status`, and exits 0 only when within 15 minutes it has seen, in order: (1) a long position with non-null `stop_loss`, `take_profit`, `entry_price`, `mark_price` and `position_qty`, `stop_loss_orders == 1`, `take_profit_orders == 1`, the stop below the entry and the take-profit above it; (2) a flat message with `stop_loss` and `take_profit` null, `closed_trades` higher than in (1) and no reduce-only order left open (no orphan); (3) a second long position with fresh exits; otherwise it exits non-zero naming the check that failed, and it removes the bot container either way. The dev session runs it, and the story's `Auto Run Result` records the command, its exit status, and the captured protected and flat `bots:status` payloads rendered through `format_bot_line`, so the TUI row is shown with real data. The story ends `done` and never parks `awaiting-operator`; a failing run is a defect to fix inside the story, not an operator action

## Epic 30: Catalog storage footprint

Operations epic, added 2026-09-26 from measurements on the dev-box catalog (2.9 GB, never consolidated) and one day of `BTCUSDT-LINEAR.BYBIT` (98,372 snapshot rows). Nothing on the platform is frozen before prod launch (operator, 2026-09-26), so schema changes are on the table and are judged on their merits and migration cost. The Nautilus-catalog-compatibility requirement stays: every file must load through `ParquetDataCatalog` and `BacktestNode` with zero conversion. Measured, per row, one consolidated day file with the current writer (zstd default) versus zstd level 19 plus `DELTA_BINARY_PACKED` timestamps: second snapshots 96.8 → 59.2 B (−39 %), mark price 22.3 → 10.7 (−52 %), index price 21.4 → 9.4 (−56 %), funding rate 22.4 → 12.3 (−45 %), trade ticks 19.9 → 14.1 (−29 %). Rejected by measurement: `BYTE_STREAM_SPLIT` on the float book columns (+43 %), float32 book columns (no gain, and lossy), dropping `ts_event` (it is the snapshot's exchange second on Bybit/Hyperliquid and cannot be recovered from `ts_init`: the gap is 1.0–5.2 s and several overdue seconds close at one wake-up). Order: 30.1 first; further stories (file-count reduction for the small types, instrument definitions written only for collected coins and only on change, legacy-type cleanup, incident-report retention, an integer book layout) are added as the operator decides them. The epic depends only on Story 25.1 (`CatalogFiles` is the one rewriter) and Story 25.1b (the `archive` service runs consolidation). Rules: DATA-02, MEM-01, TEST-04, DESIGN-01, no new dependency, MR4.

### Story 30.1: Compact Parquet encoding for every consolidated and rewritten catalog file

As the platform operator,
I want every file the archive writes (consolidated days, rebuilt snapshot files, migrations) to use the most compact lossless Parquet settings the measurements support,
So that the catalog takes roughly 30–55 % less disk per data type with byte-identical values and no change to how anything reads it.

**Acceptance Criteria:**

**Given** `archive/infrastructure/catalog_files.py`'s `CatalogFiles` (the one rewriter: `rewrite`, `write_merged`) and `kernel/parquet_compat.py`'s `apply_zstd_default()`
**When** the story ships
**Then** one named write-options function in `archive/infrastructure/` (the only place `pq.write_table` options are chosen) writes: `compression="zstd"` at `compression_level=19` (a named constant with this epic's measurement cited); `DELTA_BINARY_PACKED` for `ts_event`, `ts_init` and every other monotonic or near-monotonic integer timestamp column a type carries (e.g. funding's `next_funding_ns`), with dictionary encoding off for exactly those columns and left on for the rest; row groups sized so one day of one instrument is one row group up to a named cap (`_MAX_ROW_GROUP_ROWS`, default 1,048,576), with column statistics kept so `ts_event`/`ts_init` filter pushdown still prunes; `BYTE_STREAM_SPLIT` and float32 not used (the rejections recorded in a comment with the numbers)

**Given** the settings above
**When** `archive/tests/test_catalog_files.py` round-trips a fixture day for every data type the catalog holds (second snapshot, trade tick, mark/index price, funding rate, open interest, instrument status, instrument definitions, order book deltas)
**Then** the rewritten file's full schema, schema metadata, row count, row order and every value are identical to the source (`Table.equals`), `ParquetDataCatalog.query` returns identical objects for each type, a `BacktestNode` run over a fixture catalog of rewritten files loads trade ticks and snapshots with no error, the kernel's column-projected readers (`kernel/catalog_files.py`) read them unchanged, and the file is smaller than the default-settings write of the same table

**Given** the live minute files are written by Nautilus's own `write_data` (their encoding is not ours to choose, FORK-01)
**When** the nightly and the intraday consolidation (Story 25.1b) merge them
**Then** every merged file gets the compact settings, the nightly snapshot rebuild's rewrite gets them too, and a one-off `python -m archive.tools.recompress --apply [--venue V] [--type T]` rewrites every already-consolidated closed-day file through `CatalogFiles.rewrite` under the maintenance lock (report-only without `--apply`, printing bytes before/after per type; open-day files skipped as Story 25.1 defines; each file verified before replace), whose one VPS run (with the before/after totals to record in `docs/DATA_INTEGRITY_AUDIT.md`) is appended to `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section as one entry headed with this story's key and commit; the story finalizes `done` through the normal review path and never parks `awaiting-operator`

**Given** zstd level 19 costs CPU at write time
**When** the story ships
**Then** the nightly's consolidate step wall time and peak RSS before/after are measured on the fixture day and recorded next to the size numbers; if a level-19 write of one instrument-day takes more than 10× the default level, the level is lowered to the smallest one within 2 % of level 19's size and the choice is recorded with both numbers

**Given** MR4
**When** the story is merged
**Then** `docs/DATA_DICTIONARY.md` §6 documents the write settings and why, `platform/CLAUDE.md` DATA-05 names the one write-options function, and `kernel/parquet_compat.py`'s docstring points at it for anything written outside Nautilus's `write_data`

### Story 30.2: The second snapshot stores its book and trade fields as exact integers

As a strategy researcher and the platform operator,
I want every price and size in the 1-second snapshot stored as an exact integer in the instrument's own precision, with book prices as gaps from the level above,
So that no stored value carries float noise (20.7 % of stored book prices do today, e.g. `85891.9` stored as `85891.90000000001`), the rebuild and reconcile compare exact values, and each row is about 22 % smaller than Story 30.1's float layout (59.2 → 46.1 B/row measured on one Bybit BTC day).

**Acceptance Criteria:**

**Given** `kernel/second_snapshot.py`'s `DydxSecondSnapshot` (book lists as `float64` from `lv.price.as_double()`/`lv.size()`, OHLC and volumes as floats) and the project rule "never round-trip a market data value through `float` before it is safely inside a `Price`/`Quantity`"
**When** the story ships
**Then** the stored layout is: `price_precision: uint8` and `size_precision: uint8` per row (per row, not per file, so a precision change mid-day, as dYdX's mark feed once produced, can never make two files disagree); `bid_prices`/`ask_prices` as `list<int64>` where element 0 is the best price in units of `10^-price_precision` and every later element is the positive gap to the previous level (bids: previous − this; asks: this − previous); `bid_sizes`/`ask_sizes` as `list<int64>` in units of `10^-size_precision`; `open/high/low/close_price` as nullable `int64` price units; `buy_volume`/`sell_volume` as `int64` size units; counts and timestamps unchanged. Values are taken from Nautilus's exact `Price.raw`/`Quantity.raw` scaled down by `10^(FIXED_PRECISION - precision)` (integer division, asserted exact), never through `float`; the precision comes from the instrument definition the collector already holds (`self._instruments`), and `kernel.fold.fold_trades` produces the OHLC/volume units the same way from its `Price`/`Quantity` sums

**Given** every reader of snapshots (the collector's Redis publish, `ranking`'s `snapshots:raw` parse, `candles`' fold and `kernel/catalog_files.py`'s column-projected reads, `views`, `archive`'s rebuild/reconcile/repair, `research`'s `MarketFrames`, the tests)
**When** the story ships
**Then** `kernel/second_snapshot.py` owns the one encoder/decoder pair: the Arrow encoder writes the integer layout; decoded objects expose the book and trade fields both as exact values (`Price`/`Quantity` via `from_raw`) and as floats for indicators, computed once in the decoder; every reader goes through it (a grep test asserts no other module decodes the gap layout); `kernel/catalog_files.py`'s projected readers decode the columns they project with the same functions; the `snapshots:raw` Redis payload is produced by the same module in the same integer layout (see the live-payload criterion below); and `archive`'s rebuild compares and rewrites trade fields as integers (no float tolerance anywhere in the fold path)

**Given** the operator's rule (decided 2026-09-28): values stay exact integers everywhere a machine moves or stores them, and become decimals only where a human looks at them
**When** the story ships
**Then** the `snapshots:raw` Redis payload carries the same integer layout as the Parquet file (price/size units, per-message `price_precision`/`size_precision`, gap-encoded book prices), produced by `kernel/second_snapshot.py`'s encoder and never as floats; every consumer changes in this story: `ranking` (`infrastructure/redis.py`, `domain/price_series.py`, `application/engine.py`), `views` (`coin_detail`, `live_candles`), `alerting`, `data_api/buses.py` and anything else that reads `snapshots:raw` decode through the kernel decoder and may convert to float only inside their own computation (scores, indicator math, alert thresholds), never re-publishing floats onward; `data_api`'s SSE/HTTP payloads to the web frontend pass the integers and precisions through unchanged, and the frontend (`platform/frontend/`) converts to display decimals in one shared TypeScript helper (exact, string-based formatting at the carried precision, no float arithmetic on the integer before display), covered by frontend tests including gap decoding and precisions 0–9; `bot_tui` likewise decodes only for display; a grep test asserts no Python module reads float book/trade fields from `snapshots:raw`; because a collector publishing the new payload breaks any consumer not yet updated, the VPS cutover (collectors, ranking, data_api/frontend, alerting, bots redeployed together, old stream entries drained or trimmed) is part of this story's entry in `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions", and `docs/DATA_DICTIONARY.md` documents the Redis payload next to the Parquet layout

**Given** the snapshot files already in the catalog (float layout)
**When** the operator runs `python -m archive.tools.migrate_snapshot_ints --apply [--venue V]` (report-only without `--apply`)
**Then** each closed-day snapshot file is rewritten through `CatalogFiles.rewrite` under the maintenance lock with 30.1's write settings, precision taken from the catalog's stored instrument definition valid for that day; a float value within half a unit of an exact price/size unit is snapped to it and counted, and a value further off refuses that file with one `migrate_snapshot_ints.off_grid` ledger entry naming the file and value (never silently rounded); open-day files are skipped and taken by the next run; the report prints files, rows, snapped values and bytes before/after per venue; the VPS run (with the totals to record in `docs/DATA_INTEGRITY_AUDIT.md`) is appended to `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section as one entry headed with this story's key and commit; the story finalizes `done` through the normal review path and never parks `awaiting-operator`

**Given** the round-trip and compatibility requirement
**When** `kernel/tests` and `archive/tests` run
**Then** a property test over generated books (1–50 levels, precisions 0–9, sizes up to `QUANTITY_RAW_MAX`) proves encode → Parquet → decode returns identical `Price`/`Quantity` values; a fixture day decodes to the same floats as the old layout's floats rounded to the instrument precision; `ParquetDataCatalog.query(DydxSecondSnapshot, …)` and a `BacktestNode` run over a migrated fixture catalog load with no error; and the migrated fixture file is smaller than its 30.1-settings float version

**Given** MR4
**When** the story is merged
**Then** `docs/DATA_DICTIONARY.md` §1 documents the new layout (units, gap encoding, precision columns, how to decode by hand), the root `CLAUDE.md`'s price-integrity section cites the snapshot as integer-exact, and `research/README.md`'s snapshot-reading note points at the kernel decoder

## Epic 31: Trade-grade data: every captured value and every derived signal proven against an independent oracle (Bybit + Hyperliquid)

Verification epic, added 2026-09-28 at the operator's request ("I want to ensure that every datapoint is correct and no data is lost silently or misrepresented or interpreted, so that I can trust my bots to trade on the signals derived from it"). Scope: Bybit (`BTCUSDT`/`ETHUSDT` linear and spot) and Hyperliquid (`BTC`/`ETH` perp); **dYdX is out of scope** (operator, 2026-09-28: Epic 29 cuts it over). Deliverable is both a one-off verdict over a fresh local capture (the dev-box catalog was wiped 2026-09-28 for a clean slate) and permanent gates (tests in `make test`, a nightly verification step in the `archive` saga). Runs on branch `verify/data-correctness` in its own worktree, alongside the in-place epic 27 run; merged into `troll` when closed.

What exists already and is reused, not rebuilt: `archive.compare_klines` (trade OHLCV vs venue klines, exact units, D-51), `archive.rebuild_seconds` (`kernel/fold.py` refold), the live REST book cross-check (`capture/application/book_check.py`, D-64), `archive.crosscheck_errors`, `research/application/inspection.py` (`fold_agreement`, `second_grid`, `gap_report`, `snapshot_sanity`), `archive/tools/measure_lag.py`, the durable error ledger. What is missing, found by a three-part code survey on 2026-09-28:
- **No independent oracle** for the book columns, individual trade ids, mark/index/funding/open interest, instrument definitions, or any derived signal. Every existing derived-value test except `compare_klines` is either hand-computed on toy inputs or a consistency check that runs the same code on both sides.
- **Silent or log-only drop sites (DATA-07):** the stale-trade age filter is judged at *processing* time (`capture/domain/trade_intake.py` `accept`, `now_ns` from `_process_data`), so an ingest backlog >10 s drops genuine live trades unarchived with an INFO line only; the OHLC-outside-book canary is `logger.error` only (no ledger); stale/no-book second rejections are warnings only; a `snapshots:raw` publish failure is a swallowed warning (`capture/infrastructure/redis_stream.py`); a `run()` crash is `logger.exception` only; open-interest parsers skip malformed rows silently; unknown message types are dropped at DEBUG; the 2000-id dedup window can re-archive an evicted id; `DydxSecondSnapshot.from_dict` turns a missing volume/count into 0.
- **Silent substitutions in derived values:** micro→mid (`views/chart_series.py` `price_series_rows`), price→slow close (`ranking/domain/board.py`), trades→mark prices in the ranking price backfill (`ranking/infrastructure/catalog_prices.py`), `pct_1h`/`pct_24h` horizon shortened at a gap (`ranking/domain/metrics.py`, first trade at or after the cutoff), `metrics_nearest` with no tolerance, OFI previous-state never reset on a time gap in `views._replay_bucket_samples`, `research/strategies/snapshot_strategy.py` and `bots/strategies/dummy.py`, 1W indicator panes computed on 1D bars (`data_api/routes/indicators.py` clamp) and 1W buckets starting Thursday (epoch-aligned) while the frontend week starts Monday, three differently defined volatilities, empty rolling buffer → `cvd = 0.0`.
- **The live bot does not read the collector:** `DummyStrategy` computes its signals from its own ungated `TradingNode` book; backtests compute theirs from the catalog. Nothing proves the two agree.

Principles binding every story: **oracle independence** (DATA-02: the reference side shares zero code with capture — aiohttp WebSocket/REST, `json`, `Decimal`, its own book builder and fold; never `nautilus_pyo3`, `capture.*`, `candles.*`, `kernel.fold`; enforced by `tests/test_boundaries.py`); **exact comparison** (Decimal at the instrument's precision; float64 storage noise, Epic 30.2, is measured and reported as its own class, never absorbed into a tolerance, and every comparator works unchanged once 30.2 lands); **verify the verifiers** (every comparator ships a planted-defect test that must fail it); **two end states per finding** (fixed with a test and an audit row, or registered OPEN in `docs/DATA_INTEGRITY_AUDIT.md` with a follow-up story — never "probably fine"). New code lives in a new DDD context `platform/verification/` (domain / application / infrastructure / tests, registered in `tests/test_boundaries.py`'s `CONTEXTS`, its composition roots in `COMPOSITION_ROOTS`), with REST URLs from `kernel/venue_http.py` (WS URLs added there too). Order: 31.1 → 31.2 (then the soak restarts from empty on the fixed code) → 31.3 (needs no soak data) → 31.4 → 31.5 → 31.6 → 31.7 → 31.8 → 31.9 → 31.10 → 31.11. Rules: DATA-01..08, OPS-01, FORK-01, NAUT-01/02/03, MEM-01, TEST-01..04, DESIGN-01, SSOT-01/02, MR4, no new dependency (aiohttp, numpy, pandas, pyarrow are already pinned; property-style tests use seeded stdlib `random`, not `hypothesis`).

### Story 31.1: Verification context, independent reference recorder and a clean-slate side-by-side stack

As the platform operator,
I want a recorder that captures each venue's raw market-data frames with a client sharing no code with the collectors, running next to them on a fresh catalog,
So that every later story compares our stored data against an independent source of truth instead of against itself.

**Acceptance Criteria:**

**Given** DATA-02's standard of proof ("a second independent client with zero shared code path")
**When** the story ships
**Then** `platform/verification/` exists as a context (domain / application / infrastructure / tests, module docstrings naming its invariant: "the reference side never imports the code it checks"); `tests/test_boundaries.py` registers it and fails any import from `verification/` of `nautilus_pyo3`, `capture`, `candles`, `ranking`, `views`, `kernel.fold` or `kernel.second_snapshot` (the reference parses wire JSON itself; it may import `kernel.venue_http`, `kernel.venues` and `observability`), and fails any import of `verification` from another context except `archive`'s nightly composition root (Story 31.11)

**Given** Bybit's public WS (`orderbook.50.<symbol>`, `publicTrade.<symbol>` for linear and spot; `tickers.<symbol>` for linear) and Hyperliquid's (`l2Book`, `trades`, `activeAssetCtx` per coin), with URLs added to `kernel/venue_http.py`'s map
**When** `python3 -m verification.recorder --venue {BYBIT,HYPERLIQUID}` runs
**Then** it subscribes the same instruments as that venue's `config.toml` (read from the file, not copied), writes every received frame verbatim with its local receive time (`time.time_ns()`, taken before parsing) as one JSON line to `<VERIFY_DATA_DIR>/raw/<venue>/<channel>/<UTC hour>.jsonl.zst`, rotates hourly, reconnects with backoff and writes a `{"kind": "connection", "event": "open"|"close"|"error", ...}` line for every transition (so a recorder gap is itself visible and never mistaken for a collector gap), sends each venue's documented ping/keepalive, and ledgers every failure through `observability.error_ledger.record` at a new `verification.recorder.*` site; REST pollers write the same way (Bybit `/v5/market/instruments-info`, `/v5/market/open-interest`, `/v5/market/recent-trade` every 30 s, `/v5/market/orderbook` every 60 s; Hyperliquid `metaAndAssetCtxs` every 30 s and `l2Book` every 60 s), all built with `kernel/venue_http.py`; a retention prune keeps `VERIFY_RETAIN_DAYS` (default 7) and logs the bytes/day per venue at each hourly rotation

**Given** the live epic 27 run's `make test` (main checkout, compose project `platform`, hard-coded `container_name: dydx-redis` etc., `REDIS_PORT` 16379)
**When** the verify stack runs from this worktree
**Then** `platform/docker-compose.verify.yml` (an override, the base file unchanged) gives every service a `verify-` container name, sets `REDIS_PORT`/`DATA_API_PORT`/`DOZZLE_PORT` to 26379/29100/28080 by default, adds the `reference_recorder_bybit` and `reference_recorder_hyperliquid` services (collector image, `network_mode: host`, **a uid other than the collectors' 1000:1000** so Story 31.10 can cut the collectors' venue connections by uid without cutting the recorder's, `logging: *default-logging`, `ERROR_LEDGER_DIR`/`ERROR_LEDGER_SERVICE`), and excludes the dYdX `collector` service; `make verify-up` / `verify-down` / `verify-wipe` (the last refuses while any verify container runs, lists what it deletes, and keeps `dydx_config.toml`, `chart_indicators.toml`, `screener_columns.toml`) drive it with `-p verify -f docker-compose.yml -f docker-compose.verify.yml`; all ports stay `127.0.0.1` (SEC-01)

**Given** the tests need wire shapes without the network
**When** the story ships
**Then** small recorded fixtures (a few minutes per channel per venue, a Bybit snapshot + ≥200 deltas, a reconnect) live under `verification/tests/fixtures/`, and the parser tests use them; `make test` includes `verification/tests`

**Given** OPS-01
**When** the story finalizes
**Then** the verify stack (`bybit_collector`, `hyperliquid_collector`, `archive`, `ranking_engine`, `data_api`, `redis`, both recorders) is left running detached from an empty `platform/data/`, the start time is recorded in `docs/VERIFICATION_REPORT.md` (created with a skeleton verdict table), and the measured recorder footprint (bytes/day/venue, RSS, CPU) is written there too

### Story 31.2: Every drop is counted, ledgered and explainable (DATA-07 closure)

As the platform operator,
I want every site where capture discards, defers or fails to store a message to leave a durable, countable trace, and the stale-trade filter to judge age on arrival,
So that a missing trade or a missing second is always explainable from the record, never silent.

**Acceptance Criteria:**

**Given** `TradeIntake.accept`'s stale filter compares `now_ns` at processing time with `ts_event`
**When** the story ships
**Then** first the recorder's frames prove (or refute) that an ingest backlog drops genuine live trades (replay a recorded burst through `CaptureService` with an artificially slowed `_ingest_loop` and count trades the reference saw that are neither archived nor ledgered); if proven, the age is judged on the trade's arrival `ts_init` (the Rust client's receipt stamp), a replay (DATA-06) is still classified from its evidenced shape, and a test pins both; every trade the filter drops is counted per instrument per flush **and** ledgered at `collector.stale_trade` with the count and the oldest/youngest age (one line per flush, within the write cap); the audit gains a row with the evidence

**Given** the log-only sites listed in this epic's preamble (OHLC-outside-book canary, stale/no-book/empty-top/crossed second rejections, `snapshots:raw` publish failure, `run()` crash, open-interest parser skips, unknown message types, `from_dict` defaults)
**When** the story ships
**Then** each one records through `CaptureService._ledger` (or, for the kernel/parsers, raises to a caller that does) at a named constant in `capture/application/sites.py`; `DydxSecondSnapshot.from_dict` raises on a missing field instead of defaulting it to 0 (the only legitimate default, pre-OHLC files, is handled by the catalog reader with a named `Known limit:`); a grep test fails a new bare `logger.warning(...)`/`logger.debug(...)` followed by `continue`/`return` in `capture/`

**Given** the per-instrument dedup window (`seen_trade_ids`, 2000) evicts old ids
**When** a REST backfill or a late duplicate carries an evicted id
**Then** the archive still holds each `trade_id` at most once per instrument (proven by a test that evicts then replays), by a bounded, time-based check against the archive's own recent ids or an equivalent documented mechanism; the rebuild's per-hour dedup is no longer the only guard

**Given** a second that is sampled but written no row (NoBook, EmptyTop, Crossed, Stale, venue-mode catch-up cap exceeded)
**When** the story ships
**Then** durable per-instrument, per-reason counts of rejected seconds (with the first and last second of each run) are written each flush to `<catalog>/../coverage/<venue>.jsonl` (append-only, fsync'd, the gap-marker pattern), documented in `docs/DATA_DICTIONARY.md`; `python -m verification.conservation --venue V --day D` reports, per instrument: reference trade ids seen, archived, backfilled, ledgered-unrecoverable, **unexplained** (must be 0), and expected seconds, rows written, rows explained by a coverage reason, **unexplained** (must be 0); a planted-defect test (an archived trade deleted, a coverage line deleted) makes it report a non-zero unexplained count

**Given** OPS-01
**When** the story finalizes
**Then** the verify stack is stopped, wiped (`make verify-wipe`) and restarted on the fixed code, and the new soak start time is recorded in `docs/VERIFICATION_REPORT.md`

### Story 31.3: Derived signals against independent reference implementations

As a strategy researcher,
I want every derived value the bots, backtests, rankings and charts use recomputed by an independent, obviously correct implementation and compared on adversarial and real inputs,
So that a signal means exactly what `docs/DATA_DICTIONARY.md` §2 says it means.

**Acceptance Criteria:**

**Given** the formulas in `docs/DATA_DICTIONARY.md` §2–§3
**When** the story ships
**Then** `verification/domain/reference_signals.py` implements, in plain Python over `Decimal` (floats only where the production definition is itself statistical, e.g. z-score, stdev, Pearson), written from the dictionary text and not from the production code: microprice, spread, mid, OBI_N, multi-level OFI_N (count and USD-notional; level-aligned by position as production does, with the definition's source cited), rolling z-score (ddof=0), CVD, volume_delta, avg_trade_size, depth_within_bps, the candle fold at every stored and read-time width, `pct_1h`/`pct_24h`/`pct_1w`, each of the three volatility definitions (`VolatilityTracker.score`, `price_stats_from_series`, `volatility_fast`), returns and resampling, Pearson/rolling correlation, lead-lag, basis bps, `funding_per_hour`

**Given** `kernel/indicators.py`, `candles/domain/fold.py`, `ranking/domain/*`, `views/chart_series.py`, `views/indicator_picker.py`, `research/domain/*` and `research/application/{frames,aligned,microstructure}.py`
**When** `verification/tests/test_reference_signals.py` runs in `make test`
**Then** each production function is compared with its reference on (a) seeded stdlib-`random` generators including empty sides, zero totals, one-sided and crossed books, time gaps, NaN, duplicate seconds, mixed precisions, 1–50 levels; (b) real snapshot fixtures cut from the 31.2 soak (Bybit linear + spot, Hyperliquid; committed, small); (c) hand-computed golden cases; every tolerance is written next to its justification (exact for Decimal-exact functions); and a planted-defect test (e.g. OBI with bid/ask swapped) must fail the comparison

**Given** the silent substitutions listed in this epic's preamble
**When** the story ships
**Then** each one is decided and recorded: either fixed to be loud (None/NaN plus a canary or a visible marker — e.g. micro→mid removed and the chart shows no microprice for that second; OFI previous-state reset on a gap in `views._replay_bucket_samples` and `SnapshotStrategy` with the same threshold semantics as `ranking` and `OFIStrategy`; `metrics_nearest` given a tolerance; the `pct_*` horizon reported, or NaN when shortened beyond a named bound; the ranking backfill no longer mixing mark prices into a trade-close series, or labelling it) or kept as a documented `Known limit:` in the code and `docs/DATA_DICTIONARY.md` with a test pinning the behaviour; the three volatility definitions are documented as three named, distinct metrics wherever they are displayed; every displayed unit matches its label (the rankings page shows raw token and price units while `views/ranking_columns.py`'s comments and `frontend/src/pages/docs/data.ts` claim a client-side `usdFromTokens`/`bpsFromPriceUnits` normalisation that does not exist — fix the display or the docs, with a test); `DummyStrategy`'s gap handling is decided in Story 31.9

### Story 31.4: Trades proven id by id against the venue

As the platform operator,
I want every archived trade matched against the reference stream and every second's trade columns re-folded independently,
So that trade volume, OHLC and flow are provably complete and exact.

**Acceptance Criteria:**

**Given** the recorder's `publicTrade`/`trades` frames and the catalog's `trade_tick` archive for one closed UTC day
**When** `python -m verification.trades --venue V --day D` runs
**Then** per instrument it reports: ids in reference only (missing), in archive only (extra — each must be a REST-backfilled row inside a recorder `connection` gap, or it is a finding), duplicated ids, and for every matched id exact equality of price, size (Decimal at the instrument precision), aggressor side, and `ts_event` (Hyperliquid compared at its millisecond truth, D-62), plus `ts_init − receive_ts` distribution (a plausibility check, not an equality); missing/extra ids are cross-referenced with the ledger and coverage (31.2) and anything unexplained is non-zero in the report

**Given** the snapshot rows' eight trade columns (OHLC, buy/sell volume, buy/sell count)
**When** the same tool runs
**Then** it folds each exchange second independently in `Decimal` from the **reference** trades (its own fold, not `kernel.fold`) and compares with both the live row and, after the nightly `rebuild_seconds`, the rebuilt row; differences are classified (live-provisional boundary effect the rebuild fixes / rebuild mismatch / missing row) and the rebuilt row must match exactly; the NO_AGGRESSOR→sell convention is confirmed against each venue's wire (does either venue send one?) and documented

**Given** "verify the verifiers"
**When** the tests run
**Then** planted defects (one trade removed, one size changed by one unit, one `ts_event` moved across a second boundary) each make the report non-zero

### Story 31.5: The stored book proven against an independently rebuilt book

As a strategy researcher,
I want each stored top-20 book compared with a book rebuilt from the raw frames by independent code,
So that every OFI/OBI/microprice input is known to be the venue's actual book at that second.

**Acceptance Criteria:**

**Given** Bybit `orderbook.50` (snapshot + deltas, `u` per topic) and Hyperliquid `l2Book` (full snapshot per message)
**When** `verification/domain/reference_book.py` replays a day
**Then** it maintains its own book (Decimal levels; Bybit `u` contiguity checked independently, re-baselined only on a snapshot; Hyperliquid replaced per message), and for every exchange second S produces the top 20 per side using the collector's documented close rule (DATA-01: deltas with `ts_event < S+1`, venue time); the reference book is itself checked against the recorder's REST order-book polls (at the matching `seq`/`time`), and that agreement rate is reported first — a reference that disagrees with REST invalidates the comparison

**Given** the `second_snapshot` rows for the same day
**When** `python -m verification.book --venue V --day D` runs
**Then** per instrument it reports rows compared, rows exactly equal (after converting the stored floats to Decimal at the instrument precision), rows equal only after that rounding (the float-noise class, counted separately — the Epic 30.2 measurement), rows differing in content (level missing, extra, wrong size, wrong price) with the level index, and seconds where the reference has a valid book but no row exists, each of which must be explained by a coverage reason (31.2) or is unexplained; boundary-timing differences are separated from content differences by comparing against the reference at S±1 message

**Given** DATA-08's open question (Bybit empty-level message leaves `last_u` one behind → false `collector.book_sequence`) and `book_check.py`'s blind spot for levels missing below the best
**When** the story ships
**Then** both are settled with recorded evidence: the recorder's raw frames show whether zero-level messages occur and whether a `collector.book_sequence` entry coincides with one; the fix (advance the baseline on an empty message, if proven) ships with a test; the cross-check blind spot is either closed or recorded as a `Known limit:` now covered by this nightly comparison; Bybit spot's `u` behaviour is measured (D-41's scope note) and recorded

**Given** "verify the verifiers"
**When** the tests run
**Then** planted defects (a level's size perturbed by one unit, a level deleted, a whole row shifted by one second) each make the report non-zero

### Story 31.6: Mark, index, funding, open interest and instrument definitions proven

As a strategy researcher,
I want the non-trade, non-book streams checked value by value against the venue,
So that funding carry, basis and open-interest signals rest on correct inputs.

**Acceptance Criteria:**

**Given** the recorder's Bybit linear `tickers` (markPrice, indexPrice, fundingRate, nextFundingTime, openInterest) and Hyperliquid `activeAssetCtx` (markPx, oraclePx, funding, openInterest), plus the REST open-interest polls
**When** `python -m verification.derivs --venue V --day D` runs
**Then** per instrument and type it reports coverage (reference updates vs stored updates, with the collector's own sampling/throttle documented as the expected ratio) and exact value agreement for every stored update matched to its reference frame (by venue timestamp where carried, else nearest receive time within a stated bound); `OpenInterest`'s `ts_event` semantics (poll wall-clock on Bybit) are checked against the dictionary; each instrument carries exactly one precision label per type across the whole day (a disagreement is a finding: it breaks catalog reads, cf. the dYdX incident)

**Given** Bybit spot is documented to produce trades and book only
**When** the tool runs
**Then** it asserts no mark, index, funding or open-interest row exists for any `-SPOT.BYBIT` id (none fabricated) and reports it

**Given** the instrument definitions written at `CaptureService.run`
**When** compared with the recorder's instruments-info / `meta` snapshots
**Then** tick size, lot size, price and size precision, min quantity and multiplier match exactly; a venue-side change during the soak is detected and reported with how the collector handled it

### Story 31.7: Catalog integrity and backtest-read parity

As a strategy researcher,
I want proof that the catalog is internally consistent and that a backtest sees exactly the rows that were stored,
So that research results and live signals are computed on the same data.

**Acceptance Criteria:**

**Given** the verify catalog
**When** `python -m verification.catalog --day D` runs
**Then** every Parquet file opens through `ParquetDataCatalog`; each data type has one schema (D-24's class); within each instrument and type, files' `[start, end]` intervals do not overlap, rows are sorted by `ts_init`, and no `(instrument, ts_event)` appears twice in `second_snapshot` (no reader dedupes, so a duplicate reaches every consumer); a planted duplicate or overlap fails it

**Given** `consolidate_catalog` and the intraday consolidation rewrite files
**When** a day is consolidated in the verify stack
**Then** a per-type row hash (order-independent, over all columns) is identical before and after, recorded in the report

**Given** NAUT-03 (`BacktestNode` + `BacktestDataConfig` streaming)
**When** a backtest streams one full day of `TradeTick` and `DydxSecondSnapshot` for each instrument
**Then** the rows the strategy receives equal, by count and hash, a direct query bounded by both `start=` and `end=` (MEM-01), and the candle store's bars for that day equal a fold of the same rows; any difference is a finding

### Story 31.8: Candles and klines on every timeframe, with pass rates recorded

As the platform operator,
I want every stored and read-time candle width proven against an independent fold and against the venue's klines,
So that the chart, the technicals and the bar-based strategies show the market as it traded.

**Acceptance Criteria:**

**Given** the candle store (1m, 5m, 15m, 1h, 4h, 1d) and read-time widths (10m, 30m, 45m, 1W)
**When** `python -m verification.candles --venue V --day D` runs
**Then** every bar equals the reference fold (31.3) of the reference trades bucketed on `ts_event`, and equals a fold of the catalog's rebuilt seconds; `seconds_observed` and `partial` are checked against the coverage record (31.2); untraded buckets are reported distinctly from missing data

**Given** the `archive` service's nightly saga on the verify stack
**When** each closed UTC day of the soak has been processed
**Then** `compare_klines` pass rates per venue and instrument are recorded in `docs/VERIFICATION_REPORT.md` and audit D-51 is updated with the numbers (it closes locally when every compared minute passes or each failing minute is explained with evidence)

**Given** 1W buckets are epoch-aligned (Thursday) while the frontend week starts Monday, and 1W indicator panes are clamped to 1D bars (`data_api/routes/indicators.py`, `indicator_series.py`)
**When** the story ships
**Then** both are fixed (one documented week start used by every layer; 1W indicators computed on 1W bars) or each is a documented `Known limit:` shown in the UI, with a test pinning whichever is chosen

### Story 31.9: Live, backtest and display parity, including the bot's own signals

As a trader,
I want proof that the signal a bot acts on live equals the signal the same strategy computes in a backtest, and that every screen shows the same value,
So that a backtest's edge is an edge the live bot can actually see.

**Acceptance Criteria:**

**Given** one second's snapshot row on the verify stack
**When** `verification/tests/test_ssot_trace.py` (live, marked for the verify stack) and a recorded-fixture unit variant run
**Then** the value of each field and each derived metric is equal (or the difference accounted for) along: catalog row → `snapshots:raw` payload → `RankingBoard` metrics → `rankings:live` → `metrics.db` → the `data_api` response the frontend receives

**Given** `bots/strategies/dummy.py` reads its own live `TradingNode` book (ungated) and `bots/config.toml` configures dYdX data
**When** the live-paper bot runs on Bybit and Hyperliquid data for at least one hour (config added for the verify stack, paper execution only), logging per-second signal inputs (top-10 levels used, trend input bars) and outputs (microprice, OBI, MLOFI, trend, decision)
**Then** a backtest of the same strategy over the same window from the catalog recomputes the same series, and the report quantifies divergence per signal (exact-equal share, max abs difference, decision disagreements) with each divergence class explained (book source: full live book vs gated top-20 snapshot; timing; gaps); the decision whether the bot must gate its book (stale/crossed/gap) and reset OFI on a gap like the collector does is made with the operator and implemented or recorded as a `Known limit:`

**Given** `research.strategies.ofi_strategy:OFIStrategy`
**When** its backtest runs over a soak day
**Then** its OFI z-score series equals `research/application/microstructure.py`'s `ofi_replay` over the same rows and the reference implementation (31.3) within the stated tolerance

### Story 31.10: Fault injection proves every loss is accounted for

As the platform operator,
I want the failures that happen in production injected deliberately while the reference keeps recording,
So that every recovery path is proven to leave no silent loss.

**Acceptance Criteria:**

**Given** the verify stack with both recorders running under their own uid
**When** `python -m verification.chaos --scenario S` runs each scenario on a running collector
**Then** the scenarios are: SIGKILL during the `:02` flush; graceful restart; a 15 s `docker pause` (exercises the stale-trade filter); a 45 s pause (past the 30 s venue catch-up cap); a 60 s cut of the collectors' venue connections by uid (iptables owner match, recorder unaffected) — Bybit recovered by REST backfill, Hyperliquid unrecoverable per D-48; a catalog write failure (the catalog mount made read-only for one flush); Redis stopped for 60 s; each records its start/end in a scenario log

**Given** an expected-outcome table per scenario (what is backfilled, what is gap-marked, what is ledgered unrecoverable, which seconds have no row and which coverage reason explains them)
**When** `verification.conservation` (31.2) runs over each scenario window
**Then** unexplained trades and unexplained seconds are 0 for every scenario, the observed outcome matches the table, and a mismatch is either fixed or registered OPEN in the audit with the scenario that reproduces it

### Story 31.11: The verification run, the report and the permanent nightly gate

As the platform operator,
I want one report that states, per data type and instrument, whether the data is verified, and a nightly job that keeps checking it,
So that trust in the data is a measured, continuously re-earned fact.

**Acceptance Criteria:**

**Given** at least one full closed UTC day (target 48 h) of clean soak after Story 31.2's restart, and the chaos windows kept separate
**When** every verifier (31.2 conservation, 31.4 trades, 31.5 book, 31.6 derivs, 31.7 catalog, 31.8 candles) runs over it
**Then** `docs/VERIFICATION_REPORT.md` holds a verdict table per data type × instrument — VERIFIED (zero unexplained), DEVIATION (each with its audit row) or OPEN — with the numbers, the soak window, the code revision, and the reproduction command for each row

**Given** the `archive` service's nightly saga (`archive/application/nightly.py`)
**When** the story ships
**Then** a `verify_day` step runs after `compare_klines` for each venue whose recorder data covers the day, writes its per-type verdicts into `archive:status` and `data/archive/state.json` as `verification_days` (never gating trade pruning on its own, which stays `verified_days`), ledgers every non-zero unexplained count at `archive.verify_day`, and is skipped with one visible `"verification": "no reference data"` status (not an error) on a stack without recorders

**Given** MR4 and OPS-01
**When** the epic closes
**Then** `docs/DATA_INTEGRITY_AUDIT.md` has a row for every finding, `docs/DATA_DICTIONARY.md` documents the verification context, the coverage record and every signal definition change, `platform/CLAUDE.md` DATA-02 names the reference recorder and `verification.*` tools as the standing independent source, and `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" gains one entry for this epic: the recorder's resource budget on nifelheim (2 vCPU / 3.7 GB, already oversubscribed — decide recorder-on-VPS vs recorder-on-desktop), and the VPS rollout of the 31.2/31.3/31.5/31.8 fixes

## Epic 28: Capture on a measured CPU budget

Operations epic, added 2026-09-26 from the nifelheim evidence (`docs/DATA_INTEGRITY_AUDIT.md` D-06, D-07, D-10; `.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md`): the collectors run on a 2 vCPU / 3.7 GB box where `dydx-collector` alone sat at ~105 % CPU, the load average reached 10.85 on 2 cores, and `_second_loop` woke 2–21.5 s late about every 3 minutes. A late wake-up is not an archive-correctness problem any more (Story 25.1's midnight rule rebuilds a late row wherever its file starts) but it still delays every live consumer (chart, rankings, alerts), suspends the crossed-book check for the stall's duration and, on dYdX, leaves the missed second without a row. Dev-box micro-benchmarks (2026-09-26, i7-11370H, `python3` with the system `nautilus_trader`; scripts are reproducible from the figures in Story 28.1's third AC) already rank the candidates: the per-minute Arrow flush encode (291 ms under the GIL, at the same :02 instant venue-time capture closes the next seconds) dwarfs the per-message book path (~6.5 µs/message plus 6–370 µs per instrument-second depending on book depth, i.e. a few per cent of one core), and the dYdX always-on `[WS_RAW]` DEBUG file sink is the unmeasured suspect for the rest. This epic makes the collector cheaper on every measured Python cost and gives it CPU priority over the box's batch work. Rescoped 2026-09-30 (operator decision): production moved off dYdX in Story 29.3, and the same fleet it now runs (Bybit BTC/ETH linear + spot, Hyperliquid SOL) measured 8.9 % (`bybit_collector`) and 1.5 % (`hyperliquid_collector`) of one dev-box core over Story 31.1's 1,029 s soak (`docs/VERIFICATION_REPORT.md`, "Reference recorder footprint"), so the nifelheim figure was the dYdX regime at ~25 markets. The target is the regime the operator is heading for, 30+ instruments over several venues (Bybit alone pushes its 50-level book every 20 ms, ~50 messages/s per instrument, so 30 instruments is ~1,500 book messages/s before trades and tickers), and Story 28.2 therefore does not wait for a VPS profile: it measures every fix on a scaled hot-path burst and takes it when the number says so. It is Python-only by decision: a Rust capture binary over the `nautilus-adapters`/`nautilus-persistence` crates is the ceiling lift above this epic and is recorded in the DDD spine's Deferred section as the upgrade path, not scheduled. Order: 28.1 → 28.2. The VPS profile is a deferred operator action (2026-09-26 rule: stories never park) and, since the 2026-09-30 rescope, a post-hoc check rather than a gate: when it lands, its README says whether the dev-box ranking held on the VPS. The epic runs after Story 26.2 (decided 2026-09-26: Story 26.1 already builds the seams the fixes belong in, `LiveBook` for the book and `ArchiveWriter` for the batch encoder, and its AC now keeps both concrete types private to those modules so Epic 28's swaps touch no caller) and is independent of Epic 27. Rules that bind it: DATA-02 (root cause before mitigation: no fix without a measurement that names its cost -- the scaled `test_hotpath.py` burst for 28.2, the VPS profile when it exists), NFR12 (no new dependency: `uvloop` is already pinned in `pyproject.toml`; `py-spy` is a host tool, never a project dependency), FORK-01 (`nautilus_trader/` and `crates/` untouched), TEST-04, DESIGN-01, MR4, and the hot-path budget (`tests/test_hotpath.py`, audit D-65): every change is measured against the recorded baseline, and the baseline is re-recorded lower at the end so Epic 26's and later refactors cannot quietly give the gain back.

### Story 28.1: Measure the capture hot path and give capture CPU priority

As the platform operator,
I want each collector to report its queue depth, loop lag and flush time, to have CPU priority over the box's batch services, and to be profiled on the VPS under real load,
So that the next story fixes only what is measured, and the stalls stop being amplified by contention while it does.

**Acceptance Criteria:**

**Given** the flush-time report lines (`_report_stale_trades`, `_report_trade_sources`, `_report_venue_counts`) and the `_second_loop` lag canary (D-10)
**When** the story ships
**Then** each flush also reports, per collector, the ingest queue's max depth since the last flush (`_ingest_queue.qsize()` sampled on every `_process_data`), the number of messages processed, the max and p99 `_second_loop`/`_venue_second_loop` wake-up lag, and the wall time of the last `write_data` call; the four figures are also published on the existing per-flush Redis channel the dashboard reads so `/api/errors`-style visibility needs no new endpoint (record the exact channel and key names in `docs/DATA_DICTIONARY.md`); audit D-07's "needs a queue-depth metric first" is struck with a citation; no drop policy is added to the queue (DATA-05)

**Given** `platform/docker-compose.yml`, which sets no CPU or memory limits on any service
**When** the story ships
**Then** the three collector services carry `cpu_shares: 1024` and the batch services (`ranking_engine`, `data_api`, `bot_tui`, `live-paper`, `dozzle`) carry `cpu_shares: 256`, so under contention capture gets ~4× the CPU of any batch service and nothing is limited when the box is idle (no `cpus:` hard cap: a cap would starve capture during a burst, which is the failure being fixed); each collector gets a `mem_limit` chosen from the story's `docker stats` evidence plus 50 % headroom, with the value and the evidence line in a compose comment; `docs/DEPLOY_CHECKLIST.md` gains a §7 "Capture CPU budget" with the redeploy order and the one-line acceptance check (`uptime` load average ≤ number of cores over an hour, zero `_second_loop tick arrived … late` lines in Dozzle over the same hour), and those checks, with the profile below, are appended to `docs/DEPLOY_CHECKLIST.md`'s "Deferred operator actions" section as one entry headed with this story's key and commit; the story finalizes `done` through the normal review path and never parks `awaiting-operator`

**Given** the three collectors running on nifelheim under their normal instrument load
**When** the operator later runs, on the VPS (a deferred operator action, not a park): one `py-spy record --pid <collector> --duration 300 --format speedscope` per collector plus one `py-spy dump` during a logged `_second_loop tick arrived … late` if one occurs, alongside `uptime`, `free -h` and `docker stats --no-stream`
**Then** the operator commits the three profiles under `platform/.planning/debug/capture-profile-2026-<date>/` with a one-page `README.md` that ranks, per venue, the top ten frames by self-time and states for each whether it is (a) our Python (`capture/`, `kernel/`), (b) Nautilus Cython/Rust behind a Python call, or (c) the interpreter/asyncio itself, and whether the box was CPU-contended during the capture (load average vs cores); the audit gains `D-66` citing this README; this profiling step is one of the deferred entries above, and Story 28.2 does not wait for it

### Story 28.2: Remove the measured Python overhead and lower the hot-path baseline

As the platform operator,
I want the per-message and per-second Python overhead removed from the collectors, each fix measured on a burst the size of the fleet I am scaling to, with the hot-path baseline re-recorded lower afterwards,
So that one collector process carries 30+ instruments on a small box without a `_second_loop` stall, live data stays fresh as venues are added, and no later refactor can quietly give the gain back.

**Acceptance Criteria:**

**Given** the 2026-09-30 evidence in this epic's preamble (the production fleet at 8.9 % / 1.5 % of one dev-box core; the nifelheim 105 % was dYdX at ~25 markets; the target regime is 30+ instruments over several venues, ~1,500 book messages/s on Bybit alone), the 2026-09-26 dev-box micro-benchmarks (Arrow flush encode 291 ms for 30 instruments × 60 rows; `capsule_to_data` 5.3 µs per message; Cython `bids()[:20]` 378 µs on a 500-level book vs 11.6 µs for pyo3 `bids(20)`; Redis publish encode 2.3 ms per second), and `tests/test_hotpath.py`
**When** the story starts
**Then** `test_hotpath.py` gains a second, `scale` burst next to the existing one -- 30 instruments per venue, 200-level books, the same per-instrument deltas and recorded trades -- recorded as its own baseline (`tests/fixtures/hotpath_baseline_scale.json`, `make hotpath-baseline` records both) and asserted the same way (allocations ≤ baseline; wall time ≤ baseline on the recording CPU); the story's first commit is the two baselines on the unchanged code, so every later commit's before/after is against a recorded number, never a remembered one. The VPS profile from Story 28.1 is not waited for: when it exists it is cited, when it does not the story says so in its `Auto Run Result` and the deferred entry stays.

**Given** the two bursts and their baselines
**When** the fixes are applied, each as a separate commit whose message carries the before/after `test_hotpath.py` figures (both bursts: ns per message, retained and peak bytes per message, and for the flush its wall time)
**Then** they land in this order:
1. **The flush encoder.** The `:02` flush serialises `DydxSecondSnapshot` rows through one Python dict per row (`kernel/second_snapshot.py` `to_dict` via `make_dict_serializer`) inside the GIL, ~0.3 s on the dev box for 30 instruments and longer on a VPS core, at the very moment `_venue_second_loop` closes the next seconds (the mechanism behind the 00:00:02 stall Story 25.1's midnight rule tolerates) → a columnar encoder (one `pa.array` per column over the batch) registered through the same `register_arrow` call, byte-identical Parquet output proven by a round-trip test against the existing encoder on a fixture batch, target ≤ 20 ms for the 30 × 60 batch, its wall time reported through Story 28.1's flush metrics;
2. **The ingest hand-off.** `CaptureService._ingest_loop` wraps every `queue.get()` in `asyncio.wait_for(..., timeout=1.0)`, a timer handle and a cancellation scope per message, so the per-message cost of the queue is paid twice → a plain `await self._ingest_queue.get()` with a stop sentinel enqueued by `stop()` (the 1 s poll existed only to notice `_stop`), the yield-every-64 rule kept, measured on both bursts with the queue on the path (the replay gains a variant that drives `_on_data` + `_ingest_loop` rather than bypassing the queue, so this item has a number);
3. **The event loop.** `asyncio.run(...)` in each venue entrypoint → `uvloop` (already a pinned `nautilus_trader` dependency) selected through one helper in `capture/application`, with a fallback to the default loop and a startup log line naming which loop runs; the Rust clients hand every message over with `call_soon_threadsafe`, so the loop's wake-up cost is per message and uvloop roughly halves it (its published `call_soon` figures: 2.35 M/s asyncio vs 5.05 M/s uvloop); measured as item 2, on the queued variant;
4. **The book.** `_apply_deltas` applies one level at a time through `LiveBook.apply` on the Cython `OrderBook`, every message is first converted from its pyo3 form by `capsule_to_data` in each venue `client.py`, and the sampler reads `book.bids()[:BOOK_DEPTH]` / `book.asks()[:BOOK_DEPTH]`, whose `bids()`/`asks()` (`nautilus_trader/model/book.pyx:425`) materialise a `BookLevel` for every level before the slice → decided by one number from the `scale` burst: the Python book path's share (conversion + per-level apply + the per-second reads) of one dev-box core at 1,500 messages/s. At or above 1 % (about 3 % of a VPS vCPU) the collector keeps the book as `nautilus_pyo3.OrderBook` inside `LiveBook` (the one module allowed to name the concrete class since Story 26.1: no per-message conversion, one `apply_deltas(deltas)` call per message, `bids(BOOK_DEPTH)`/`asks(BOOK_DEPTH)` depth-bounded reads), with the venue hooks (dYdX per-level tagging, Bybit `u` canary, the 22.5 cross-check captures) kept correct on the pyo3 types and their DATA-04/DATA-08 tests unchanged, a test that a 500-level book and a 20-level book give identical snapshots, and the `capsule_to_data` path retained only for the non-book message types that still need Cython objects for `write_data` (record which, per venue, in the story); below 1 % it is recorded "not taken: <figure> of a core at the scale burst" in the `Auto Run Result` and in audit D-65, with the pyo3 swap named as the next step when the fleet grows past that number.
The dYdX `[WS_RAW]` file sink is out of this story: dYdX is not deployed since Story 29.3 (`make up` never starts it) and since 2026-09-30 the sink is off unless the plan file says `ws_raw_sink = true` (`DydxConfig.ws_raw_sink`, `docs/DEPLOY_CHECKLIST.md` §8), so its always-on per-message cost is already gone; an incident-scoped ring flushed by `IncidentHandler` (preserving the Story 5.1 reports byte-for-byte) remains the design if the raw window is ever wanted at zero steady-state cost, recorded in `docs/DATA_INTEGRITY_AUDIT.md` as OPEN, not silently forgotten.
**And** every applied fix keeps `test_hotpath.py` allocations ≤ baseline and wall time ≤ baseline on both bursts (not 2×: this story only removes cost); no behaviour change is visible in the archive (`kernel` schemas, row values and file names byte-identical; the D-24 schema tests pass unchanged); no new dependency (NFR12); a fix that does not lower its number is not merged and is recorded with the figure.

**Given** the two baselines and the DDD spine
**When** the code fixes are merged
**Then** `make hotpath-baseline` is re-run on the same CPU the current baselines name and the lower figures are committed as the new baselines (so Epic 26's and later refactors are held to the improved cost, not the old one); `docs/DATA_INTEGRITY_AUDIT.md` D-65 records the before/after per-message allocation and wall-time figures per venue for both bursts plus the projected cost of one collector at 30 instruments on one VPS vCPU (dev-box figure × the ratio Story 28.1's profile README states when it exists, else "× 3, assumed" and marked as such); the DDD spine's Deferred section gains one entry: "Capture in Rust: a `platform/capture_rs` binary over `nautilus-adapters`/`nautilus-model`/`nautilus-persistence`, no Python object per message; upgrade path once the Python budget from Epic 28 is exhausted; Go is not an option (no Nautilus bindings)"; and `platform/CLAUDE.md`'s "Adding a venue" gains one line: a new venue's expected messages/s per instrument is part of its wire-behaviour investigation and is checked against the `scale` burst's per-message figure before the venue is deployed.

## Epic 32: Chart honesty and cleanup: gaps drawn to length, panes that grow the page, indicator settings on the legend, and the classic light chart

Frontend epic, added 2026-09-30 from the operator's review of the chart page. Today a multi-hour collection gap looks like one missing candle: `views/chart_series.py`'s `with_gap_markers` (`:548`) emits exactly one gap row per hole, the five frontend page-seam sites (`useCandles.ts` `loadPage` and `mergeByTime`, `useIndicatorSeries.ts`, `useSnapshotSeries.ts`, `usePickerIndicatorValues.ts`) add one whitespace point each, and lightweight-charts gives every point one slot, so a 6-hour hole on a 1m chart is as wide as a 1-minute one. Surveyed facts the stories build on: the chart is created at a fixed `height: 500` with no `autoSize`, and every added pane shares that height, so the price pane shrinks with each indicator; volume is not an overlay but a pane hard-wired first in `ChartPage.tsx`'s `panes` (`DEFAULT_PANE_IDS = ["volume"]`), with no toggle; indicator parameters are edited only in the `IndicatorEntryRow` list rendered below the chart, persisted per coin through `PUT /api/coin/{iid}/indicators`; the legend is a 12 px `pointer-events: none` block; the app has one fixed dark VGA identity (Story 15.9) and the chart reads `--color-*` tokens from `document.documentElement` once at mount; the left-rail Cursor button is not a no-op (it disarms the armed tool and is the only mode in which drawings and profile edges are editable), so it stays. Operator decisions (2026-09-30): a gap is drawn as one placeholder bar per missing bar, in a colour nothing else uses; volume is toggled from the Indicators menu; a new pane grows the page instead of shrinking the others; the chart gets TradingView's classic white palette; legend names get larger, every indicator (overlays included) gets an eye to hide it, and a gear opens a settings modal with its parameters, a TradingView-style price source and per-line style (colour, width, dash); style, source and hidden state persist in the same per-coin config as the params. Rules: AD-F6 (a gap is never interpolated or filled with a fabricated value: whitespace stays whitespace and every consumer that skips it keeps skipping it), DATA-07 (a gap is shown loudly, never hidden), SSOT-02 (one edit path for indicator parameters), DESIGN-03 (delete the redundant list below the chart), TEST-04, MR4, no new dependency (icons are inline SVG). Verification for every story: `cd platform/frontend && npm test && npm run lint && npm run build`; Story 32.1 also `python3 -m pytest views/tests data_api/tests -q` from `platform/` (no Rust build needed). The epic runs on branch `epic-32` in its own worktree in parallel with Epics 31 and 28 and is merged by hand; it touches only `platform/frontend/`, `platform/views/chart_series.py`, `views/indicator_picker.py`, `views/preferences.py` and their tests, `data_api/routes/indicators.py` and `data_api/tests`, `docs/`, `platform/CLAUDE.md` and the planning artefacts. Order: 32.1 → 32.2 → 32.3 → 32.4 → 32.5 → 32.6 → 32.7 → 32.8 (32.7 and 32.8 added 2026-09-30: the remaining TradingView profile family on the shared engine, and volume footprint bars from the raw trade archive, toggled from the Indicators menu; 32.8 is the one story that touches `kernel/catalog_files.py`, for a column-projected trade-tick reader. 32.5 added 2026-09-30: Fibonacci retracement and Long/Short position tools, with every drawing persisted server-side and the preference files moved to one mounted directory; 32.6 added 2026-09-30: the whole chart setup of a coin saved server-side and restored on return, with a default template for a coin opened for the first time).

### Story 32.1: Every gap drawn to its real length, in a colour nothing else uses, on every chart

As the platform operator,
I want a hole in the data to take as many bar slots on the chart as bars are missing, each slot painted in one dedicated gap colour with the hole's duration written next to it,
So that a five-minute outage reads as five missing bars and a six-hour one as six hours, on the candles, the volume pane, every indicator pane and Lines mode alike, and nothing ever looks like a normal chart with one bar missing.

**Acceptance Criteria:**

**Given** `views/chart_series.py`'s `with_gap_markers` (bars: candles, indicator series and indicator values share it) and Lines mode's `_gap_row` path (`SNAPSHOT_GAP_THRESHOLD_MS = 2500`)
**When** two consecutive kept rows are more than one interval apart
**Then** one gap row (all values `None`, schema unchanged) is emitted for every missing interval, at `earlier + k * interval_ms` for `k = 1 .. missing`, so a hole of `n` missing bars yields exactly `n` rows and a Lines-mode hole one row per missing second; the run is capped by one named constant `MAX_GAP_ROWS_PER_GAP` (default 720, i.e. 12 hours of 1m bars or 12 minutes of seconds) whose `Known limit:` comment names the ceiling and the upgrade path (a gap row carrying `span_ms`, drawn as one wide band); a hole longer than the cap emits exactly the cap's rows contiguous from the hole's start and the next real row follows, so the frontend can tell the hole is compressed from the distance between the last gap row and the next real row; the three bar endpoints return identical gap times for one window (test), so every pane's slots stay aligned; `views/tests/test_chart_series.py` (`:159,194,205`) and `data_api/tests/test_indicator_series.py` (`:236`) are updated from one-row-per-hole to one-row-per-interval, with new cases for the cap, a two-bar hole, Lines mode and the module docstring rewritten

**Given** the five frontend page-seam sites named in the epic preamble
**When** a hole straddles a page cursor or a live-refetch seam
**Then** each site builds its seam through one shared helper `frontend/src/lib/gaps.ts` (`gapRun(afterExclusive, beforeExclusive, stepSeconds, cap)` returning the whitespace times) with the same cap value mirrored as a named constant and asserted by a test on each side; the affected tests (`useCandles.test.ts:125,179`, `useSnapshotSeries.test.ts:137,171`, `usePickerIndicatorValues.test.ts`, a new `useIndicatorSeries.test.ts`) assert the full run, and the "2 s snapshot spacing is not a gap" case still passes; a scroll-back refill still fires when the loaded left edge is a run of gap slots (`REFILL_MARGIN_BARS` counts logical slots; test)

**Given** AD-F6 and the consumers that skip whitespace (`lib/volumeProfile.ts`, `lib/sessionProfile.ts`, `MeasurementPrimitive.ts`, `legend.ts`, `useReplay.ts`, `HistoryPage.tsx`, alert evaluation)
**When** the story ships
**Then** a gap slot is still native whitespace data (`{ time }`): no fabricated OHLC, value or volume, so every profile, measurement, replay step, legend value and alert keeps ignoring it (their existing tests pass unchanged and one new test per consumer feeds a long gap run); the visual comes from a new `components/chart/primitives/GapPrimitive.ts` (same family as `VerticalMarkerPrimitive`) attached to the candlestick series, the volume series, every indicator pane series and the Lines-mode series, which paints each whitespace slot in its pane as one placeholder bar of a dedicated colour token `--chart-gap` (defined in `theme.css`, used by nothing else; hatched or translucent so the grid stays visible), one candle-width wide at the current bar spacing and never narrower than 1 px, full pane height; the first slot of every run carries a label in the price pane, "no data · 5m" (seconds/minutes/hours/days formatted by one helper), and a compressed run reads "no data · 3d 4h (compressed)"; with the crosshair over a gap slot, the status bar and the legend show "no data · <duration>" in place of the "—" values

**Given** MR4
**When** the story is merged
**Then** the DocsPage chart section (`pages/docs/kbData.ts`) documents how a gap is drawn and what the cap means, `platform/CLAUDE.md`'s text that describes one marker per gap (if any) is amended, and `spec-21-x-candlestick-chart-correctness.md`'s backlog entry for gap visibility (if present) is closed with this story's key

### Story 32.2: Panes grow the page instead of shrinking each other, and volume is an Indicators-menu entry with a toggle

As the platform operator,
I want every pane I add to make the chart taller so I scroll the page, never smaller candles, and volume to be one more entry in the Indicators menu that I can switch off,
So that the price pane keeps its size no matter how many histograms I stack under it.

**Acceptance Criteria:**

**Given** `LightweightChart.tsx`'s `createChart(container, { height: 500 })`, the width-only `ResizeObserver` and the pane registry effect that calls `chart.addPane()` per non-overlay pane
**When** a non-overlay pane is added or removed
**Then** the chart's total height becomes the price pane's height (one named constant, 500 px kept) plus one default height per extra pane (named constants: 120 px for volume, 160 px for any other histogram or line pane), applied through `chart.applyOptions({ height })` and per-pane `setStretchFactor` (or `setHeight` where lightweight-charts 5.2.1 offers it) so the price pane and every existing pane keep their current pixel size when another is added or removed; a divider the operator dragged keeps its size across a later add/remove (§A8.2 "resize a pane" still works); the page scrolls (no `overflow: hidden` or fixed-height ancestor between the chart and `body`; `frontend/scripts/chart-layout.test.mjs` pins this); Fit, Latest, legend placement, crosshair sync, the replay marker and every primitive behave as before (existing `LightweightChart.test.tsx` pane tests pass, new ones assert the height arithmetic for add, remove and re-add)

**Given** `ChartPage.tsx`'s hard-wired volume pane (`DEFAULT_PANE_IDS = ["volume"]`, always first in `panes`) and the Indicators dialog (`IndicatorPicker.tsx`)
**When** the story ships
**Then** "Volume" is the first entry of the Indicators dialog (pinned above the catalog categories, no params), added and removed like any indicator, on by default, its on/off state persisted per instrument in `localStorage` under `chart-volume:{iid}` next to `chart-timeframe:{iid}` (provisional: Story 32.6 moves both keys into the server-side chart layout and imports them once); when off, the volume pane is absent from `panes` and the registry diff removes it, while `fullVolume` is still fetched and still feeds FRVP/VRVP/SVP and the measurement tool (tests: profiles unchanged with volume off), and the live `series.update("volume")` path is a no-op instead of an error; when on again, the pane comes back first, under the price pane; the stale "overlay" wording in the comments at `ChartPage.tsx:47` and `:260` is corrected (volume is a pane, an overlay is `placement: "overlay"`)

**Given** `ChartPage.test.tsx:279` ("shows only candles + a volume pane by default")
**When** the tests run
**Then** it is updated for the toggle, with new cases: switching volume off removes the pane and persists, a reload restores the persisted state, and the dialog lists Volume first

### Story 32.3: The legend is the indicator's control surface: larger type, an eye to hide it, and a settings modal with inputs, source and style

As the platform operator,
I want each indicator's name on the chart to be easy to read and to carry an eye that hides or shows it, a gear that opens a settings modal where I change its parameters, its price source and how its lines look, and an × that removes it, as on TradingView,
So that I never scroll below the chart to find the row that belongs to a pane, and I can compare an overlay against the candles by switching it off and on.

**Acceptance Criteria:**

**Given** `components/chart/legend.ts` (`renderLegends`, one `div.chart-legend` per pane, `pointer-events: none`, 12 px) and `index.css:341-362`
**When** the story ships
**Then** the legend font size is one token `--legend-font-size` set to 14 px (title in `--chart-text`, values in their line colour), and each legend row has, after the values, an eye button, a gear button and a × button drawn as inline SVG (no icon library), visible on hover or focus and keyboard reachable (`aria-label` "Hide <name>"/"Show <name>", "Settings for <name>", "Remove <name>"); pointer events are enabled on the row only, so a drag or wheel anywhere else on the pane still pans and zooms (test on the CSS rule); the Volume row (Story 32.2) gets eye and × only

**Given** the eye
**When** it is clicked on an overlay indicator
**Then** every series of that indicator is hidden (`series.applyOptions({ visible: false })`), its legend row stays with dimmed values and the crossed icon, the price pane keeps its size, the crosshair readout skips it, and a second click shows it again
**When** it is clicked on a pane indicator (oscillator or histogram, operator decision 2026-09-30)
**Then** its pane collapses: the pane is removed from the chart and the total height shrinks by that pane's height through Story 32.2's arithmetic, so the panes below move up; its legend row is kept on the price pane's legend in the crossed state so the operator can find it, and a second click re-adds the pane at its former position and height and refetches nothing (the series data is kept in state); the hidden state is persisted per instrument in the same config entry as its params (`hidden: true`, see below) so a reload restores it; tests cover an overlay (SMA on the price pane, pane count unchanged) and a pane indicator (RSI: pane count and height drop by one pane and come back)

**Given** the gear
**When** it is clicked
**Then** a modal (`<dialog>`, like the Indicators dialog) opens titled with the indicator's legend title, with three sections. **Inputs:** that indicator's parameters as inputs (text inputs, or a `<select>` for catalog `choices`), pre-filled with the current values, validated and coerced by `paramCoercion.ts`'s `isValidParamText`/`coerceParamValue`, the row UI being `IndicatorEntryRow`'s logic moved into one shared component, not a second copy (SSOT-02); plus, for an indicator whose catalog entry says `source_selectable`, a Source `<select>` with `close` (default), `open`, `high`, `low`, `hl2`, `hlc3`, `ohlc4`. **Style:** per output line (an indicator with several outputs, e.g. MACD's line and signal, lists each) a colour input seeded from the pane palette, a line width choice of 1–4 px and a line style of solid, dashed or dotted; a histogram output gets its up and down colours. **Footer:** Apply, Cancel and Remove. Apply persists through the existing `PUT /api/coin/{iid}/indicators` and refetches values exactly as the old list did, a duplicate instance (same name, params and source) is refused as today, Esc, Cancel or a click on the backdrop closes without changes, and Remove (like the row's ×) removes the indicator through the same persist path; style changes apply immediately on Apply through `series.applyOptions` without a refetch

**Given** the price source
**When** the story ships
**Then** `views/indicator_picker.py` marks an `IndicatorSpec` whose `feed` is exactly `("close",)` as `source_selectable` in `native_catalog_json()` (the SMA/EMA/WMA/Hull/RSI/ROC/CMO family; an indicator fed `high`/`low`/`volume` or a custom indicator is not), `_feed_values` resolves the requested source for such a spec from the candle's `o`/`h`/`l`/`c` (`hl2 = (h+l)/2`, `hlc3 = (h+l+c)/3`, `ohlc4 = (o+h+l+c)/4`, computed once per candle in one named helper, never stored), an unknown source or a source on a non-selectable indicator is a 422 on the request, `IndicatorRequest`/`IndicatorValueRequestEntry`/`IndicatorConfigEntry` gain `source: str = "close"`, and `indicator_id(name, params, source)` includes the source whenever it is not `close` so SMA(20) on close and SMA(20) on hl2 are two series with two legend rows; the screener's `technicals_values` and every other `indicator_id` caller keep their ids unchanged for the default source (a test asserts the id of every existing entry is byte-identical); `views/tests/test_indicator_picker.py` proves each source expression against a hand-computed candle and that a non-selectable spec refuses one

**Given** `views/preferences.py`'s `IndicatorEntry {name, params, category}` in `chart_indicators.toml`
**When** the story ships
**Then** the entry gains `source: str = "close"`, `hidden: bool = False` and `style: dict` (per output: `color`, `line_width`, `line_style`; empty means the pane palette default), all round-tripped through `load_chart_indicators`/`save_chart_indicators` and the GET/PUT route, with an entry written before this story loading unchanged (defaults applied, test with a fixture file); the frontend `IndicatorEntry` type mirrors the three fields and `LightweightChart.tsx`'s pane specs carry `style` and `hidden` into `addSeries`/`applyOptions`; the screener `ColumnEntry` is untouched

**Given** DESIGN-03 and the `<div id="indicators">` entry list below the chart (`IndicatorPicker.tsx`'s `IndicatorEntryRow` list, inline `<select>` and Add button)
**When** the modal covers edit, style and remove, the eye covers hide, and the dialog covers add
**Then** the list below the chart is deleted, the Indicators dialog stays the one add path, and `ChartPage.test.tsx`'s indicator tests (`:417-456`) move to the dialog, the eye and the modal: opens with current params, source and style, Apply persists and refetches, a style-only Apply does not refetch, invalid input is refused, source change yields a new series id, × removes, nothing renders under the chart

**Given** the left-rail Cursor button, which the survey found to be the disarm and the only mode with editable drawings (`ChartPage.tsx` `selectTool`, `drawEditable`)
**When** the story ships
**Then** it is kept and its role made visible: it shows the active state whenever no drawing tool is armed (including after Esc and after a tool completes) and its tooltip reads "Select / edit drawings (Esc)"; `ChartPage.test.tsx:174-215` gains the two active-state cases

### Story 32.4: The classic light chart: TradingView's palette inside the dark terminal app

As the platform operator,
I want the chart area to have TradingView's classic white background and colours,
So that candles, gaps, indicators and drawings read the way I am used to, while the rest of the app keeps its terminal identity.

**Acceptance Criteria:**

**Given** `theme.css`'s single dark VGA palette and `paneColors.ts`'s `cssVar` reading `--color-*` from `document.documentElement` once at mount
**When** the story ships
**Then** a chart token set lives in `theme.css` scoped to `.chart-workspace`: `--chart-bg #ffffff`, `--chart-grid #f0f3fa`, `--chart-text #131722`, `--chart-text-dim #787b86`, `--chart-border #e0e3eb`, `--chart-up #26a69a`, `--chart-down #ef5350`, `--chart-crosshair #9598a1`, `--chart-volume-up`/`--chart-volume-down` (the up/down colours at ~50 % alpha), `--chart-gap` (Story 32.1's token, moved here and re-chosen for white), `--chart-marker`, `--chart-drawing` and an eight-colour pane palette legible on white (`#2962ff`, `#f23645`, `#089981`, `#ff9800`, `#9c27b0`, `#00bcd4`, `#795548`, `#131722`); `cssVar` reads them from the chart container element, and everything the chart draws (background, text, font, grid, scale borders, crosshair, candles, volume, gap bars and labels, legend, replay marker, measurement, trendline and horizontal-line colours, volume-profile fills, candle-pattern markers) takes its colour from these tokens and nothing else (a grep test over `components/chart/` and `pages/ChartPage.tsx` fails a `--color-*` or `--vga-*` read and any hard-coded colour literal); the toolbar, rankings, history, alerts and docs pages keep the VGA dark identity untouched; the font stays `--font-terminal`

**Given** the "no theme toggle" decision (`ChartPage.test.tsx:380`, `index.css:2-6`, `theme.css:9-16`, spec `spec-multi-exchange-screener-chart.md` §A8.1 slot 10)
**When** the story ships
**Then** there is still no toggle; those comments and the spec note are updated to state that the chart area alone is light by operator decision (2026-09-30), and the DocsPage chart section says the same

**Given** legibility on white
**When** the tests run
**Then** a vitest test parses the chart tokens out of `theme.css` and asserts a contrast ratio of at least 3:1 against `--chart-bg` for text, each pane palette colour, up, down, gap, marker and drawing colours (WCAG 2.1 graphics threshold), and the legend's dark-background `text-shadow` is replaced by a light halo so names stay readable over candles

### Story 32.5: Fibonacci retracement and Long/Short position tools, and every drawing stays on the chart

As the platform operator,
I want to draw a Fibonacci retracement between two points and place Long and Short position boxes with entry, stop and target, drag them, open their settings, and find every drawing (these, trendlines and horizontal lines) exactly where I left it on any browser,
So that I plan and review trades on the chart the way I do on TradingView, and a reload, a timeframe change or another machine never loses my work.

**Acceptance Criteria:**

**Given** today's drawings: horizontal lines persisted in `localStorage` (`chart-hlines:{iid}`), trendlines in `ChartPage.tsx`'s in-memory `drawings` state only, a colour/delete context menu, and drag editing for horizontal lines alone
**When** the story ships
**Then** every drawing of an instrument (horizontal lines, trendlines, Fibonacci retracements, positions) persists server-side in one resource, `GET`/`PUT /api/coin/{instrument_id}/drawings`, backed by `views/preferences.py` (`chart_drawings.toml` next to `chart_indicators.toml`, `load_chart_drawings`/`save_chart_drawings`, one versioned `{v: 1, items: [...]}` shape with a tagged `kind` per item, a malformed item refused by the route with a 422 naming the field, never silently dropped) so the chart is the same in every browser; the frontend persists on every change (debounced, one PUT per burst, the same explicit-save discipline as indicators) and restores on mount; the first load after this story imports the browser's `chart-hlines:{iid}` entries into the resource once and removes the key (test with a seeded `localStorage`); the three preference files stop being three per-file bind mounts: `docker-compose.yml` mounts one directory `./data/preferences/:/app/preferences/:rw`, the two tracked files move there with `git mv` (`platform/data/preferences/chart_indicators.toml`, `screener_columns.toml`) and the new tracked empty `chart_drawings.toml` joins them, `data_api/settings.py` derives the three paths from one `CHART_PREFERENCES_DIR` (default `/app/preferences`; the two old path env vars are removed, not kept as aliases, so a stale compose file fails loudly at startup with a message naming the new variable), the `Makefile`'s `VERIFY_KEEP` list and `docs/DEPLOY_CHECKLIST.md`'s Story 25.2 notes are updated, and one entry in the checklist's "Deferred operator actions" names the VPS steps (stop `data_api`, copy the two live files into `data/preferences/`, `git pull`, `make up`; OPS-01, the story finalizes `done`); a later preference file (Story 32.6's layout) then needs no compose change; the DocsPage and `views/preferences.py`'s module docstring list every file in the directory

**Given** the left rail's drawing cluster (`DRAWING_TOOLS`: Trend, HLine, Measure, FRVP) and `rangeDrag.ts`'s `attachRangeDrag`
**When** the story ships
**Then** three tools join the cluster: **Fib** (click-drag between two anchors through `attachRangeDrag`), **Long** and **Short** (one click places the box at the clicked bar and price); the cluster order and §A8.1 of `spec-multi-exchange-screener-chart.md` are updated; Esc cancels an in-progress placement and the Cursor mode is selected after a placement, as for the other tools

**Given** a Fibonacci retracement (`components/chart/primitives/FibRetracementPrimitive.ts`, same family as `TrendlinePrimitive`)
**When** it is drawn from anchor A to anchor B
**Then** it draws one horizontal level per enabled ratio at `price = B + (A − B) × ratio` (so 0 sits on B and 1 on A, following the drag direction like TradingView), default ratios on: 0, 0.236, 0.382, 0.5, 0.618, 0.786, 1; default off: 1.272, 1.618, 2.618, 4.236; each level labelled "0.618 (price)" with the price formatted at the instrument precision through `lib/units.ts`, levels extended to the right edge by default ("extend right" setting), a translucent band between consecutive levels in each level's colour, and the A–B trend segment drawn faintly; both anchors are draggable in Cursor mode and the levels follow; the settings modal (the dialog component from Story 32.3, opened by the context menu or a double click) offers each ratio's on/off and colour, extend right, label side and line width; geometry is unit-tested for both drag directions and for a level list edited in the modal

**Given** a Long or Short position (`components/chart/primitives/PositionPrimitive.ts`)
**When** it is placed
**Then** it shows an entry line at the clicked price, a target and a stop at default distances (target 2 × the stop distance, stop distance 1 % of entry, named constants; Long has the target above and the stop below, Short the mirror), a profit zone (entry to target) filled in the up colour and a loss zone (entry to stop) in the down colour at ~20 % alpha, both spanning from the placed bar to a right edge a default 40 bars later; labels read "Target: price (+x.xx %)", "Entry: price", "Stop: price (−x.xx %)" and "Risk/Reward: r.rr" (|target − entry| ÷ |entry − stop|, two decimals, recomputed on every drag); the target, stop and entry handles and the right edge are draggable in Cursor mode (entry drags the whole box, a target dragged past the entry or a stop dragged past it is refused, never a negative zone); the settings modal offers entry, stop and target prices as inputs, the box width in bars, and optional account size and risk % whose product ÷ |entry − stop| is shown as "Size: qty" at the instrument's size precision when both are set (empty by default, persisted with the drawing); prices are formatted through `lib/units.ts` at the instrument precision and never displayed with float noise (test with a precision-2 and a precision-6 instrument); geometry, R/R and size are unit-tested for Long and Short

**Given** Cursor mode editing (`LightweightChart.tsx`'s capture-phase mousedown grab, `findClickedDrawingId`, the drawing context menu)
**When** the story ships
**Then** hit-testing covers the new primitives' handles and lines with the same grab-then-suppress-click discipline as horizontal lines, the trendline's two anchors become draggable the same way (one mechanism for every drawing's handles, not one per kind), the context menu keeps colour and delete and gains "Settings…" for kinds that have a modal, every drawing survives the Candles/Lines switch and the timeframe remount (anchors are time + price, so a drawing re-places on any bar size; a drawing whose time falls between two bars snaps to the earlier bar for drawing only, the stored anchor is unchanged), and replay, measurement and the volume profiles ignore drawings as before

**Given** MR4 and the tests
**When** the story is merged
**Then** `ChartPage.test.tsx` covers placing a Fib by drag, placing a Long and a Short by click, dragging a target and reading the new R/R label, deleting through the menu, and the persist round-trip against the mocked drawings resource; `views/tests/test_preferences.py` and `data_api/tests` cover the resource's round-trip, the version tag and a malformed item; the DocsPage chart section lists the three tools and that drawings are saved per instrument on the server

### Story 32.6: A coin's chart comes back exactly as it was left, and a new coin opens with your default setup

As the platform operator,
I want everything about how I set up a coin's chart (timeframe, candles or lines, volume, crosshair, pane heights, zoom, profiles, and the indicators and drawings the earlier stories already save) stored on the server and restored when I open that coin again, from any browser, and a coin I open for the first time to start from a default setup I chose,
So that I never rebuild a chart, and the platform behaves like a saved TradingView layout rather than a page that forgets.

**Acceptance Criteria:**

**Given** the chart state scattered today across `localStorage` (`chart-timeframe:{iid}`, Story 32.2's `chart-volume:{iid}`), React state that is lost on navigation (Candles/Lines mode, crosshair on/off, dragged pane heights, the visible zoom, FRVP/VRVP/SVP settings from Stories 18.6–18.9) and the two server-side files (`chart_indicators.toml`, `chart_drawings.toml`)
**When** the story ships
**Then** one more file in the preferences directory of Story 32.5, `chart_layouts.toml` (`views/preferences.py` `load_chart_layouts`/`save_chart_layouts`, one table per instrument id, one `[default]` table, a `v = 1` version key), holds per instrument: `bar_seconds`, `mode` (`candles`/`lines`), `volume` (on/off), `crosshair` (on/off), `pane_heights` (pane id → px, only for panes the operator dragged), `visible_bars` (the number of bars on screen, restored as the zoom while scrolling to the latest bar, never the absolute scroll position, so a return always shows live data at the remembered zoom), and the volume-profile settings (kind, row count, value-area %, session, HD flag, the fixed range's anchors; Story 32.7's auto-anchored and TPO settings and Story 32.8's footprint on/off and settings join the same table when they land) with each key's allowed values validated by the route (`GET`/`PUT /api/coin/{instrument_id}/layout`, 422 naming the bad key); indicators stay in `chart_indicators.toml` and drawings in `chart_drawings.toml` (one file per concern, no duplication); the frontend saves through one `useChartLayout(iid)` hook, debounced, one PUT per burst, on every change of any listed field, and restores every field on mount before the first fetch so the first candle request already uses the saved timeframe; the two `localStorage` keys are imported once on the first load and removed (test with seeded storage), and no chart-page state remains in `localStorage` afterwards (a grep test over `pages/ChartPage.tsx` and `hooks/` fails a `localStorage` read of a `chart-` key)

**Given** a coin opened for the first time (no table for its instrument id)
**When** the chart mounts
**Then** it is seeded from the `[default]` layout, and from `[default]`'s indicator list (a `default_indicators` array in the same file, copied into `chart_indicators.toml` for that coin on first open; drawings are never copied, they are price-specific), then saved under the coin so later edits are its own; the top toolbar's Indicators cluster gains a small "Layout" menu with "Save as default" (copies this coin's layout and indicator list into `[default]`, after a confirm naming what it overwrites) and "Reset to default" (replaces this coin's layout and indicators with `[default]`, after a confirm; drawings untouched); when `[default]` is empty the built-in defaults apply (1m, candles, volume on, crosshair on, no indicators), and `ChartPage.test.tsx` covers first-open seeding, save-as-default, reset, and that a second coin is not affected by the first coin's edits

**Given** navigation and the live edge
**When** the operator leaves the chart page and comes back, or opens the same coin in another browser
**Then** the chart is identical in every listed field, the indicators show their saved params, source, style and hidden state (Story 32.3), the drawings are in place (Story 32.5), and the live candle and websocket paths behave as before (existing tests pass); a layout saved for a timeframe that no longer exists in `TIMEFRAMES` or a mode that no longer exists falls back to the built-in default for that field with one `console.error` (visible in the ErrorBar), never a blank chart

**Given** MR4
**When** the story is merged
**Then** `views/preferences.py`'s module docstring, the DocsPage chart section and `platform/CLAUDE.md`'s SSOT notes for the web UI describe the three preference files and the layout's field list, and `views/tests/test_preferences.py` covers the round-trip, the default table, the version key and a malformed field

### Story 32.7: The rest of the TradingView profile family on the one shared engine: Auto Anchored, Anchored, Anchored VWAP and TPO

As the platform operator,
I want the profile tools TradingView offers that the chart still lacks: an Auto Anchored Volume Profile, an Anchored Volume Profile drawing, an Anchored VWAP drawing, and a Time Price Opportunity (TPO) profile,
So that the chart's profile family matches the one I read charts with, on the same engine and data as the five profiles already built.

**Acceptance Criteria:**

**Given** `lib/volumeProfile.ts`'s one engine (`buildVolumeProfile`: candle volume spread evenly over each candle's low..high, up/down split by candle direction, POC and value area) behind FRVP (18.6), VRVP (18.7), SVP and SVP HD (18.8) and PVP (18.9), and the `VolumeProfilePrimitive`
**When** the story ships
**Then** every new profile is computed by that engine and drawn by that primitive (spec §A7.0: one calculation, no second implementation); the engine gains one option, `weight: "volume" | "time"`, where `"time"` counts each candle once per row it touches (TPO) instead of its volume, unit-tested against a hand-built slice; the candle-level spread stays the documented `Known limit:` with Story 32.8's per-trade footprint as its named upgrade path

**Given** the Auto Anchored Volume Profile (TradingView's "Auto Anchored VP")
**When** it is added from the session-profile control (`SessionProfileControl.tsx`, one session-type profile per chart as today) with a preset of `session`, `week`, `month`, `highest high`, `lowest low` or `auto`
**Then** its anchor is chosen by the preset from the loaded candles (`auto` picks by bar size: session for ≤ 15m, week for ≤ 4H, month above, the mapping a named table), the profile spans anchor → latest bar and re-anchors as new bars arrive and on every timeframe change, the anchor is marked by a vertical line (`VerticalMarkerPrimitive`), its settings (preset, rows, value-area %, colours) live in the chart layout (Story 32.6), and tests cover each preset's anchor on a fixture slice and the re-anchor on a new session

**Given** the Anchored Volume Profile and the Anchored VWAP as drawing tools (left rail, Story 32.5's cluster; both single-click placement at a bar, both persisted in `chart_drawings.toml` with `kind: "anchored_vp"` / `"anchored_vwap"`)
**When** the operator clicks a bar
**Then** the Anchored VP draws the engine's profile from that bar to the latest bar, growing rightward from the anchor with the FRVP's `{time}` anchoring, following new bars; the Anchored VWAP draws `Σ(typical price × volume) / Σ volume` from the anchor bar onward as a line with optional ±1σ and ±2σ bands (volume-weighted standard deviation, a named helper, unit-tested against a hand computation), its current value in the legend; both anchors are draggable in Cursor mode through the one handle mechanism of Story 32.5, both have the context menu and a settings modal (rows and value area for the VP; bands, source `hlc3`/`close`/`ohlc4` and colours for the VWAP), and both use `lib/units.ts` for every price they print

**Given** the TPO profile (TradingView's "Time Price Opportunity")
**When** it is added from the session-profile control (`tpo` preset, per session or per day like SVP)
**Then** the engine runs with `weight: "time"` over the session's candles, the primitive draws each row's count as blocks (one block per candle-touch, capped per row at a named constant with the overflow drawn as one longer bar), the POC row and the value area (70 % by default) are marked as for the volume profiles, an "initial balance" band (the first N bars of the session, default 2 × 30m equivalent, a setting) is outlined, and a `letters` display option is off by default (blocks) with letters A.. per candle when on; tests cover the count per row, the POC on a tie, and the initial-balance span across bar sizes

**Given** MR4
**When** the story is merged
**Then** the DocsPage chart section lists all nine profile tools with one line each on what they anchor to and what they count, spec `spec-multi-exchange-screener-chart.md` §A7 is amended for the four new ones, and `platform/CLAUDE.md`'s SSOT note names the engine as the one profile calculation

### Story 32.8: Volume footprint bars from the raw trade archive, toggled on from the Indicators menu

As the platform operator,
I want a footprint view of each candle: per price row the sell and buy volume traded there, the bar's delta and total, imbalances highlighted,
So that I see where inside a bar the aggression was, from the exact trades the archive holds, not a spread of the bar's volume.

**Acceptance Criteria:**

**Given** the raw trade archive (Story 22.13: `TradeTick` per venue with price, size, `aggressor_side` and `ts_event`, readable through `ParquetDataCatalog` and the live minute files) and `kernel/catalog_files.py`'s column-projected readers (`query_second_ohlc`, `query_top_of_book`)
**When** the story ships
**Then** `kernel/catalog_files.py` gains `query_trade_columns(catalog_path, instrument_id, start_ns, end_ns)` reading only `price`, `size`, `aggressor_side` and `ts_event` as their stored integer raws (never through `float`, DATA rule on price integrity; a projected read of the trade files in the same file-range discipline as the snapshot readers, MEM-01 bounded by the caller's window), and `views/chart_series.py` gains `footprint_page(instrument_id, before_ns, limit, bar_seconds, row_ticks, ...)` over the same `candle_page` window the chart shows (so footprint bars and candles agree on bar boundaries): per bar, rows keyed by an integer price bucket of `row_ticks` price units (auto: the smallest `row_ticks` giving ≤ 24 rows for the bar's high..low, else the operator's fixed value), each row `{p: bucket_low_units, b: buy_units, s: sell_units}` as integers plus per-bar `delta`, `total` (integers), `poc_row` and the instrument's `price_precision`/`size_precision` carried on the response; a bar the archive holds no trades for carries `rows: []` and a `no_trades: true` flag (a gap, never zeros presented as data, AD-F6); `limit` is clamped to a named `MAX_FOOTPRINT_BARS` (default 200) and the span to `MAX_QUERY_SPAN_SECONDS`; served as `GET /api/coin/{instrument_id}/footprint` with the same `before_ns`/`limit`/`bar_seconds` contract as candles plus `row_ticks`; a planted test proves that the sum of a bar's rows equals the candle's volume from the same window (Story 31.8's fold) for a fixture day, and that a buy and a sell at the same price land in the same row on the right sides

**Given** the forming (live) bar
**When** the story ships
**Then** the footprint is historical only, like CVD and Cancel Pressure (closed bars from the archive; the forming bar shows no footprint), recorded as a `Known limit:` whose upgrade path is a fold of the `trades` Redis stream by the candles context; the chart never fabricates a live footprint from the 1 s snapshot's buy/sell totals

**Given** the chart (Candles mode only, like FRVP)
**When** "Footprint" is switched on from the Indicators dialog (pinned next to Volume, Story 32.2; on/off and settings persisted in the layout, Story 32.6)
**Then** a new `components/chart/primitives/FootprintPrimitive.ts` on the price pane draws, for every closed bar in view, a column of cells aligned to the price rows: the display mode is `bid×ask` (default: "sell × buy" text per cell), `delta` (buy − sell) or `volume` (total), each cell shaded on a heat scale of the bar's largest row, the bar's POC row outlined, diagonal imbalances (buy at row n against sell at row n−1, ratio ≥ a setting, default 3) highlighted in the up colour and the mirror case in the down colour, and a footer row under each bar with delta and total; below a named bar-spacing threshold the text hides and the cells stay as heat; candles keep drawing under the cells at reduced opacity so the OHLC is still readable; every number is formatted from the integer units through `lib/units.ts` (test with a precision-2 and a precision-6 instrument); the page is fetched through one `useFootprint(iid, chart, barSeconds, enabled)` hook with the same cursor pagination and seam rule as candles (it refills on scroll-back and refetches on timeframe change), and a `no_trades` bar draws the gap colour of Story 32.1

**Given** the settings modal (Story 32.3's dialog component, opened from the Footprint legend row's gear)
**When** it is used
**Then** it offers row size (auto or a fixed number of ticks), display mode, imbalance ratio, text on/off and colours; Apply refetches only when the row size changed

**Given** MR4 and the tests
**When** the story is merged
**Then** `kernel/tests` cover the projected trade reader on a fixture day (integers in, integers out, bounded window), `views/tests/test_chart_series.py` covers `footprint_page` (bucketing, auto row size, the no-trades flag, the clamp), `data_api/tests` the route contract, `FootprintPrimitive.test.ts` the cell layout, imbalance detection and the heat scale, `ChartPage.test.tsx` the toggle and persistence, `docs/DATA_DICTIONARY.md` gains the footprint read model's definition, and the DocsPage chart section documents the display modes and the historical-only limit

## Epic 33: Everything the archive holds reaches the trader: liquidations, derivatives, per-bar order flow, alerts that watch more than a price, and the TradingView chart features still missing

Added 2026-10-05 from the operator's feature review of the frontend and backend against TradingView and against the metrics a perpetuals trader works with. The review's finding, verified file by file this session: the backend captures far more than the frontend shows, and the biggest misses are trading metrics already in the catalog, not chart polish. Surveyed facts the stories build on: mark, index, funding (`FundingRateUpdate`: rate, interval, `next_funding_ns`) and open interest (`kernel/open_interest.py`) are captured and proven nightly (`verification/derivs.py`) yet **no module under `data_api/`, `views/`, `ranking/` or `alerting/` references any of them**: no route, no pane, no column, no alert condition; the 1 s snapshot stores `buy_volume`/`sell_volume`/`buy_count`/`sell_count` per second but the candle store keeps only `v = buy + sell` (`candles/infrastructure/sqlite_store.py` `_SCHEMA`: `o,h,l,c,v,seconds_observed`), so no bar carries delta, counts or a VWAP, and the picker's `CumulativeVolumeDelta` (`views/indicator_picker.py` `_cvd_replay`) resets at the left edge of the loaded window; `GET /api/candles` serves OHLCV only (`data_api/routes/candles.py` `CandleItem`); liquidations exist nowhere in `platform/` (no type, no capture, no API); `hooks/useIndicatorSeries.ts` and `GET /api/indicator-series/{iid}` have no caller (only the `IndicatorDatum` type is imported), `views/chart_series.py`'s `build_footprint` and `compute_features` have no consumer, and the Docs page's Indicator Reference still advertises a "Web dashboard footprint chart" that does not exist in the React app; the Rankings table sorts only by Symbol and Exchange (`RankingsPage.tsx` `:142-215`); `alerting/domain/alert.py` holds one condition, a static `level` crossed by the forming bar's close, and `AlertsPage.tsx` has no create or edit form; the chart has one series type (`CandlestickSeries`) and one scale (no `PriceScaleMode` anywhere), no compare series, no symbol search, Escape is the only keyboard shortcut; drawings are `hline`, `trendline`, `fib`, `position` (`lib/drawings.ts` `KINDS`); candle patterns draw as ±100 spikes in a pane, not as markers on the candles.

**Liquidations, the wire facts (2026-10-05).** *Bybit* publishes `allLiquidation.{symbol}` on the public linear and inverse streams (spot: none), pushed every 500 ms with every liquidation of the window (the older sampled `liquidation.{symbol}` topic is gone from the v5 docs); each entry: `T` (venue ms), `s`, `S` (**the liquidated position's side**: `Buy` = a long was force-closed, so the forced order hitting the book is a sell), `v` (executed size, base units), `p` (**the bankruptcy price, not the fill**). The Rust Bybit handler decodes only orderbook, trade, kline and ticker topics and drops anything else as `BybitWsFrame::Unknown` with a debug log (`crates/adapters/bybit/src/websocket/handler.rs:270`), so the PyO3 client's generic `subscribe(topics)` sends the subscription and the payload never reaches Python; `crates/` is untouchable (FORK-01), so the feed runs over Nautilus's generic `nautilus_pyo3.WebSocketClient` (`WebSocketConfig` + `connect(loop_, config, handler, post_reconnection)`, the pattern of `nautilus_trader/adapters/binance/websocket/client.py:270`: automatic reconnection with backoff, a text handler), decoded in Python. Bybit has no public liquidation history endpoint, so an outage's liquidations are unrecoverable and the coverage record must say so. *Hyperliquid* has **no market-wide liquidation feed**: the public `trades` payload (`coin, side, px, sz, hash, time, tid, users[buyer, seller]`) carries no liquidation flag; the `liquidation` marker (`liquidatedUser`, `markPx`, `method: market | backstop`) sits on *fills*, which are per address (`userFills`, `userFillsByTime`, `userEvents`), so **a logged-in Hyperliquid user does not help** (operator offered one 2026-10-05: a user channel shows only that user's own liquidations). What is public without any login: `userFillsByTime` for *any* address (no auth; the `/info` endpoint), the two counterparty addresses on every public trade, and the liquidator vault's address (backstop liquidations are its fills, `method: backstop`). Two hypotheses Story 33.2 must settle on captured frames before building: (a) a market-liquidation fill is system-generated, so its public trade carries a null/zero `hash` (TWAP fills are documented to carry none; liquidations are not documented either way) and the liquidated address is one of its `users`, confirmable by one bounded `userFillsByTime` call per such address per minute; (b) the liquidator vault's fills, polled, give the backstop subset exactly. The complete alternative is a non-validator Hyperliquid node's fill stream (heavy ops), named as the upgrade path; third-party liquidation APIs are not an option (no external data dependency, NFR12).

**Operator decisions (2026-10-05):** both Bybit and Hyperliquid liquidations are wanted; everything in the review becomes a story in this epic; order-flow aggregates are stored per bar once, in the candle store, so the fold is touched once and Story 32.8's footprint, the screener's columns and the chart's delta indicators all read the same numbers. **Rules:** SIGNAL-01 (store raw inputs, derive on read; a per-bar aggregate is a stored *input* like `v`, never a signal), DATA-01/DATA-07 (a gap is flagged, never filled; an undecodable frame is ledgered, never dropped quietly), DATA-04 (a new wire value's precision comes from the instrument definition, never its digits; liquidation size and bankruptcy price are stored as integer units like the snapshot), AD-D12 (every published message and stored schema is extended with **added fields only**: `rankings:live`'s recorded bytes in `ranking/tests/test_replay.py` stay valid, `/api/candles` items keep every existing key), SSOT-02 (one computer per metric: the candles context folds, `views` reads, `ranking` publishes, the frontend formats through `lib/units.ts`), NFR12 (no new dependency: `nautilus_pyo3.WebSocketClient`, stdlib `json`, inline SVG), FORK-01 (`nautilus_trader/` and `crates/` untouched), MR4, TEST-01/TEST-03/TEST-04, MEM-01, OPS-01 (every VPS step goes to `docs/DEPLOY_CHECKLIST.md`'s deferred operator actions). **Verification per story:** backend `cd platform && python3 -m pytest <the touched packages>/tests -q` (no Rust build needed, the system `nautilus_trader`), frontend `cd platform/frontend && npm test && npm run lint && npm run build`; a story that changes a `data_api` response regenerates `frontend/src/api/schema.ts` (`data_api/export_openapi.py` then `npm run codegen`) in the same commit. **Sequencing:** 33.1 → 33.2 → 33.3 → 33.4 → 33.14 → 33.5 → 33.6 → 33.7 → 33.8 → 33.9 → 33.10 → 33.11 → 33.12 → 33.13 (33.14, the liquidation cascade bot, added 2026-10-05 at the operator's request: "a liquidation bot that jumps in on cascading liquidations and starts shorting"; it runs right after 33.4 because it needs only the liquidation data, the kernel and the bots context). Stories 33.1–33.4 and 33.14 touch `capture/`, `kernel/`, `candles/`, `views/`, `data_api/`, `ranking/`, `verification/`, `archive/`, `bots/`, `research/` and may start before Epic 32 is merged; 33.5 onward touch `platform/frontend/` and run **after `epic-32` is merged** (32.7 and 32.8 edit `ChartPage.tsx`, `LightweightChart.tsx` and the primitives this epic extends). Each story registers its wrong-data risks in `docs/DATA_INTEGRITY_AUDIT.md` (next row D-147) and its new types, fields and read models in `docs/DATA_DICTIONARY.md` (new §1.26 for liquidations, §2.15 for the per-bar aggregates (§2.14 is Story 32.8's volume footprint) and derivatives read models, §3.2/§3.3 amended for the ranking fields).

### Story 33.1: Bybit liquidations captured over a second socket into one shared `Liquidation` type

As the platform operator,
I want every Bybit linear liquidation archived as it happens, with the liquidated side, the size and the bankruptcy price exact, and published live like the snapshots,
So that forced flow is a first-class input the chart, the screener, the alerts and the backtests can read, not something I look up on a third-party site.

**Acceptance Criteria:**

**Given** the wire facts in this epic's preamble and `platform/CLAUDE.md`'s "Adding a venue" step 1
**When** the story starts
**Then** `scripts/capture_hl_ws.py` gains a `--topic` option (default the venue's trade topic) and the Bybit branch sends `allLiquidation.{coin}`; a ≥ 30-minute capture on a volatile hour is summarised (frames/min, entries per frame, distinct `T` per 500 ms window, any duplicate `(T, s, S, v, p)` across frames, the share of entries whose `p` is finer than the instrument's `price_precision` or whose `v` is finer than `size_precision`) and recorded in `docs/DATA_DICTIONARY.md` §1.26 with the capture date and method; a precision finer than the definition is a wire fact that decides the type's units (see below), never something rounded away

**Given** `kernel/open_interest.py` (the custom `Data` precedent: `schema()`, `to_dict`/`from_dict`, `register_arrow` with `make_dict_serializer`) and `kernel/second_snapshot.py`'s integer units
**When** `kernel/liquidation.py` is added
**Then** it defines `Liquidation(Data)` with `instrument_id`, `side` (an enum `LiquidatedSide.LONG | SHORT`, stored as `dictionary<int8,string>` `"long"`/`"short"`; the wire's `Buy` maps to `LONG` with that sentence in the docstring and a test, so the sign can never be misread), `size_units` (`int64`, units of `10^-size_precision`), `price_units` (`int64`, the bankruptcy price in units of `10^-price_precision`), `price_precision`/`size_precision` (`uint8`, per row like the snapshot), `venue_event_id` (`string`, the venue's own key when it has one, else the dedup key `"{T}:{S}:{v}:{p}"`), `ts_event` (the venue `T` in ns), `ts_init`; `units_of` from `kernel/second_snapshot.py` is reused for the integer conversion (an inexact value raises `SnapshotEncodingError`, the row is ledgered `collector.unencodable` and never rounded, DATA-04); `exact` properties return `Price`/`Quantity`, `notional_units()` is documented as size × bankruptcy price (an approximation of the fill notional, by one sentence in the docstring); the catalog directory is `custom_liquidation`; `kernel/tests/test_liquidation.py` round-trips a batch through `ParquetDataCatalog.write_data` and back byte-identical and proves the side mapping on a recorded frame

**Given** the Bybit capture client (`capture/venues/bybit/client.py`, its `Feed`s and `WireChannels`, the optional trades-only twin's `optional_feed_step` discipline) and `CaptureService.poll_loop`'s buffer-only path (rows never through `_on_data`, so REST data never counts as WS liveness)
**When** `capture/venues/bybit/liquidations.py` is added and wired by `capture/venues/bybit/__main__.py`
**Then** it owns one `nautilus_pyo3.WebSocketClient` against `kernel.venue_http.bybit_ws_url(environment, "linear")` (the URL map stays in the kernel, `test_boundaries.py`), connected with a text handler and a `post_reconnection` that resubscribes every held topic; its `subscribe(iid)`/`unsubscribe(iid)` route through its own `WireChannels` at `BYBIT_WS_FRAMES_PER_SECOND`, are called by the client's `subscribe`/`unsubscribe` for LINEAR ids only (spot and inverse are refused with a logged reason, inverse until an inverse instrument is ever planned), and a failed liquidation subscribe is ledgered (`collector.liquidation_feed`, a new `sites` constant) and retried by capture's existing retry loop, never fatal; decoded rows go **straight into the service's flush buffer** by the same method `poll_loop` uses (a new `CaptureService.ingest_rows(rows, site)` extracted from `poll_loop` so both share it, with the planned-ids filter), so a quiet hour without liquidations is never read as a dead feed (DATA-07's "pipeline failure, not quiet market" rule) -- the feed's liveness is the socket's `is_active()`, reported in `collector:status` as `liquidations: connected | reconnecting | down` and ledgered on each transition to `down`; every frame is decoded by a pure, fixture-tested `parse_liquidation_frame(frame_json, definitions, ts_init) -> list[Liquidation] | Malformed` (`Decimal` from the wire text, never `float`), an undecodable frame or an unknown topic is ledgered through `report_unknown_message`, and duplicates across the 500 ms snapshots are dropped by `venue_event_id` within a 5 s window (the window a `Known limit:` with the measured duplicate rate from the capture); rows are also published on a new Redis channel `liquidations:raw` (the `to_dict` rows, integers and precisions, like `snapshots:raw`) through `capture/infrastructure/redis_stream.py`, a publish failure ledgered at `collector.liquidation_publish`; `capture/venues/bybit/tests/test_liquidations.py` covers the parser on the recorded frames, the side mapping, the dedup window, the LINEAR-only routing and (TEST-03, real `Price`/`Quantity`) the integer units at precision 2 and 6; `tests/test_hotpath.py`'s burst is unchanged (liquidation rows bypass `_process_data`), and the story records the socket's measured frames/s against the `scale` burst in audit D-65's manner

**Given** the coverage record (`capture/domain/coverage.py`, `capture/infrastructure/coverage_file.py`, `docs/DATA_DICTIONARY.md` §1.16) and the nightly saga (`archive/application/nightly.py`)
**When** the feed is down or the collector was not running
**Then** the window is written to the venue's coverage record as `liquidations: unrecoverable` (there is no history endpoint to backfill from: a `Known limit:` naming a Bybit history endpoint, should one appear, as the upgrade path), the nightly `verify_day` reports the day's liquidation coverage next to the trade coverage, and `verification/liquidations.py` (a new verifier in the Epic 31 shape) proves each archived liquidation against the raw trade archive: a trade of the same size on the forced side within ±2 s of `ts_event` (the mechanism: Bybit's liquidation order executes on the book, so it is also a `publicTrade`), reporting the matched share per day with the unmatched ids listed, never asserting 100 % (the match is a self-check, not an oracle; the `Known limit:` says so)

**Given** MR4
**When** the story is merged
**Then** `docs/DATA_DICTIONARY.md` §1.26 defines the type, its units, the side semantics, the dedup rule, the channel and the coverage rule; `docs/DATA_INTEGRITY_AUDIT.md` gains rows for the side-sign risk, the bankruptcy-vs-fill-price risk and the no-backfill gap; `platform/CLAUDE.md`'s "What is collected" and "Adding a venue" step 1 name liquidations as part of a venue's wire investigation; `docs/DEPLOY_CHECKLIST.md` gains the deferred operator action (restart `bybit_collector`, confirm `liquidations: connected` in `collector:status`)

### Story 33.2: Hyperliquid liquidations: the wire investigation first, then the feed that holds

As the platform operator,
I want Hyperliquid liquidations in the same `Liquidation` type as Bybit's, with the coverage I actually get stated plainly,
So that the two venues I trade compare on forced flow, and I know exactly which liquidations the archive can and cannot see.

**Acceptance Criteria:**

**Given** the Hyperliquid wire facts in the preamble (no market-wide feed; per-address fills carry the `liquidation` marker; `userFillsByTime` is public for any address; public trades carry `users[buyer, seller]` and `hash`; the liquidator vault's fills are the backstop subset)
**When** the story starts (its Task 1, the investigation, a commit of its own before any feed code)
**Then** `scripts/capture_hl_ws.py` captures ≥ 60 minutes of `trades` for BTC and ETH on a volatile hour, and a one-off script under `scripts/` (not shipped in the image) cross-queries `userFillsByTime` for every address of every trade whose `hash` is null, zero or otherwise non-transaction (hypothesis (a)), and for a random sample of 200 ordinary trades' addresses (the control), and polls the liquidator vault's `userFillsByTime` for the same hour (hypothesis (b)); the findings are recorded in `docs/DATA_DICTIONARY.md` §1.26 as a table: how many public trades carried a non-transaction hash, how many of those were confirmed liquidations by the fill marker, how many confirmed liquidations the control sample found among ordinary-hash trades (the false-negative rate of (a)), how many backstop fills (b) found and whether each also appeared as a public trade, the `/info` weight consumed per hour and the documented rate limit, and the date and method; **a hypothesis is adopted only with ≥ 99 % confirmed on the capture and a measured false-negative rate**, otherwise it is recorded as refuted with the numbers

**Given** the investigation's outcome
**When** the feed is built in `capture/venues/hyperliquid/liquidations.py`, wired by the venue's `__main__.py`
**Then** exactly one of three designs ships, each with its coverage stated in the type's rows and in §1.26: **(a) held** -- the Hyperliquid client's existing trade handler cannot see `hash`/`users` (the Rust parser keeps only `tid`), so a second `nautilus_pyo3.WebSocketClient` subscribes `trades` for the planned coins (the twin-trades precedent, counted against `HYPERLIQUID_MAX_WS_CHANNELS` in the client's `subscribe` before sending), a candidate trade (non-transaction hash) is confirmed by one `userFillsByTime` call per candidate address per minute through `kernel.venue_http.post_json_request` (bounded: a `MAX_CONFIRMATIONS_PER_MINUTE` from the measured weight, overflow ledgered `collector.liquidation_unconfirmed` and the candidate archived with `confirmed=false` in a new nullable `bool` column added to the type in this story, never dropped), the confirmed fill's `liquidatedUser`, `markPx` and `method` decide `side` (the liquidated user's side from `dir`/`side` of the fill) and fill `venue_event_id` (`tid`), and `price_units` holds **`markPx`** (the price the venue liquidated at; a `Known limit:` that Bybit's row holds the bankruptcy price and Hyperliquid's the mark, both named `price_units` with a `price_kind` `dictionary<int8,string>` column `"bankruptcy" | "mark"` added to the type so a reader never conflates them); **(b) only** -- a `poll_loop` of the liquidator vault's `userFillsByTime` every `every_seconds` from the measured weight, rows `method="backstop"` only, the coverage recorded as `partial: backstop only` in every day's coverage record and in the data dictionary, so no reader can mistake it for the full set; **neither** -- no feed ships, §1.26 records the refutation, and the DDD spine's Deferred section gains "Hyperliquid liquidations from a non-validator node's fill stream"; in every case `platform/CLAUDE.md`'s "Open interest" per-venue bullet list gains a sibling "Liquidations" list with the three venues' answers (dYdX: trades carry `type: LIQUIDATED` on REST only, the WebSocket parser discards it, not deployed since Story 29.3, out of scope)

**Given** the shipped design
**When** the tests run
**Then** `capture/venues/hyperliquid/tests/test_liquidations.py` covers the parser on the recorded frames and fills, the candidate rule, the confirmation budget and its overflow path, the `price_kind`, and a planted replay proving no candidate is lost between the socket and the buffer; `verification/liquidations.py` from 33.1 runs for Hyperliquid with the venue's matching rule (a confirmed fill's `tid` must exist in the trade archive, exact); the audit gains rows for the confirmation budget and for the mark-vs-bankruptcy distinction; `docs/DEPLOY_CHECKLIST.md` gains the deferred operator action

### Story 33.3: Per-bar order flow and liquidation aggregates in the candle store, folded once

As the platform operator,
I want every bar to carry its buy and sell volume, buy and sell trade counts, a VWAP numerator, and its long and short liquidation volume and count, exactly, from the same fold that makes its OHLCV,
So that delta, CVD, trade count, average trade size, VWAP, forced share and footprint totals all come from one stored set of numbers, on every timeframe, live and historical, and never disagree with the candle they sit under.

**Acceptance Criteria:**

**Given** `candles/domain/fold.py` (`fold_rows`, `fold_arrays`, `bucket_start_ms`), `candles/infrastructure/sqlite_store.py` `_SCHEMA`, `candles/application/sink.py` (`CandleSink.apply` over `SecondRow`s), `candles/application/rebuild.py`, `candles/application/forming.py` and `kernel/fold.py`'s `SecondTradeFields` (the exact integer totals the snapshot holds)
**When** the story ships
**Then** the `candles` table gains nullable integer columns `buy_v`, `sell_v`, `buy_n`, `sell_n`, `pv` (Σ close-of-second price units × size units over the bucket's traded seconds, the VWAP numerator in `price_units × size_units`, a `Known limit:` that it is the second-close VWAP, not the per-trade one; upgrade path: Story 32.8's trade reader), `liq_long_v`, `liq_short_v`, `liq_n` (from the `Liquidation` rows of the bucket, units of `10^-size_precision`, null for a venue with no liquidation feed, **0** for a venue with the feed and no liquidation in the bucket -- the null/0 distinction is a test), and `price_precision`/`size_precision` carried per row (a mid-day precision change can never make two rows disagree, as the snapshot does); `fold_arrays` folds them from the 1 s rows' integer columns (sums, never through `float`), `fold_rows` the same for the forming bar, `CandleSink.apply` and the live `LiveCandleBus` write them, `rebuild` recomputes them for a whole day from Parquet (snapshots and `custom_liquidation`), `v` keeps its meaning and value byte for byte (`buy_v + sell_v` equals `v` in units, a test), and the schema migration adds the columns to an existing `candles_<venue>.db` in place with every pre-existing row's new columns **null** (unknown, never 0), the rebuild filling them day by day through the existing `python -m candles.rebuild`, recorded as a deferred operator action per venue

**Given** `candles/application/queries.py` (`window`, `latest`, `candle_dicts_for_window`), `views/chart_series.py` `candle_page` and `data_api/routes/candles.py` (`CandleItem`, `CandlesResponse`)
**When** a candle is served
**Then** every dict and item gains the new fields as **added, nullable** keys (`buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n` as integers in units; `price_precision`/`size_precision` already on the response), the archive-side `raw_1s` fold carries the same, the `/ws/live` `bar` payload gains them for the forming bar (AD-D12: added keys; `useLiveCandle.ts`'s parser accepts their absence), `is_valid_candle` additionally refuses `buy_v + sell_v != v`, `buy_n + sell_n < 0` or a negative liquidation count (a 500 and a counter, DATA-07), and the OpenAPI schema and `frontend/src/api/schema.ts` are regenerated

**Given** the picker's `CumulativeVolumeDelta` (`views/indicator_picker.py` `_cvd_replay`, replaying raw seconds over a ≤ 7-day window)
**When** the story ships
**Then** it is recomputed from the bars' `buy_v - sell_v` (no raw-seconds replay, so the 7-day `Known limit:` and the "value only on the last 7 1D bars" limit of §2.7 are removed for CVD), with an `anchor` parameter `session | visible | all` (the chart's bucket rule for session; `visible` resets at the first loaded bar as today; `all` accumulates from the store's oldest bar through a stored running total maintained by the sink so it never rescans, MEM-01), unit-tested against a hand fold; `verification/candles.py` proves the new columns of a day against the catalog fold and the day's `Liquidation` rows

**Given** MR4
**When** the story is merged
**Then** `docs/DATA_DICTIONARY.md` §2.5 and new §2.15 (§2.14 is Story 32.8's volume footprint) define every column, its units and the null/0 rule, §4's lineage table gains the rows, `docs/DATA_INTEGRITY_AUDIT.md` gains the migration (null, never 0) and the `v` identity rows, and `docs/DEPLOY_CHECKLIST.md` the rebuild actions

### Story 33.4: Derivatives and liquidations as read models: funding, open interest, mark, index, basis and liquidations served by the API and pushed live

As the platform operator,
I want funding, open interest, mark and index, the basis between them and the liquidations to be readable per instrument and per window from the API and to arrive live over the one WebSocket,
So that the chart, the screener and the alert engine have the derivatives data the collectors have been archiving since Epic 22.

**Acceptance Criteria:**

**Given** the catalog types `MarkPriceUpdate`, `IndexPriceUpdate`, `FundingRateUpdate`, `OpenInterest`, `Liquidation`, `views/catalog_reads.py`'s bounded page reads and `kernel/catalog_files.py`'s column-projected readers
**When** the story ships
**Then** `views/derivatives.py` is the one read model: `funding_page(iid, before_ns, limit)` (rate as a `Decimal` string, interval, `next_funding_ns`, `t`), `open_interest_page(iid, before_ns, limit, bar_seconds)` (the last OI per bucket, the bucket rule of §2.5, and `oi_change` to the previous bucket), `mark_index_page(iid, before_ns, limit, bar_seconds)` (last mark and index per bucket, `basis_mi_bps = (mark - index) / index × 10⁴` and, where the bucket's candle exists, `basis_ml_bps = (mark - close) / close × 10⁴`, both computed from `Decimal`/integer units and emitted as floats only at the edge), `liquidations_page(iid, before_ns, limit)` (the rows, units and precisions, `price_kind`) and `liquidation_bars(iid, before_ns, limit, bar_seconds)` (per bucket `long_v, short_v, n, notional_units` from the stored candle columns of 33.3, so chart and screener agree); every read is bounded by `MAX_QUERY_SPAN_SECONDS` and gap rows follow `with_gap_markers`; `kernel/indicators.py` gains the pure functions `basis_bps(mark, ref)` and `funding_annualised(rate, interval_s)` with tests; a spot id returns an empty page with `market: "spot"` and no 404 (the frontend hides the panes)

**Given** `data_api/routes/`
**When** the routes are added
**Then** `GET /api/coin/{iid}/funding`, `/open-interest`, `/mark-index`, `/liquidations`, `/liquidation-bars` with the `before_ns`/`limit`/`bar_seconds` contract of `/api/candles`, response models carrying `price_precision`/`size_precision` where units are returned, the OpenAPI schema and `schema.ts` regenerated, `data_api/tests` covering each route's contract and the spot case; the legacy `GET /catalog/chart-series/{symbol}` and `GET /api/indicator-series/{iid}` are **deleted** together with `hooks/useIndicatorSeries.ts`, `views/chart_series.py`'s `compute_chart_series`, `build_footprint` and `compute_features` (no caller; the depth-at-bps and liquidity-distance functions stay in `kernel/indicators.py` for 33.6), with the Docs page's Indicator Reference entries `footprint` and `book_features` rewritten to what exists (DESIGN-03)

**Given** `/ws/live` (`data_api/ws/live.py`: `rankings:live` relayed, `candles:{iid}:{bar_seconds}` subscriptions over `LiveCandleBus`) and the `liquidations:raw` channel of 33.1
**When** a client subscribes `derivs:{iid}` or `liquidations:{iid}`
**Then** `views/live_derivs.py` (a bus in the `LiveCandleBus` shape) forwards each new funding, OI, mark and index value as `{"channel": "derivs:{iid}", "kind": "funding|oi|mark|index", "t", "value", ...}` from a new capture publish of those rows on `derivs:raw` (added to `capture/infrastructure/redis_stream.py`, every venue, a failed publish ledgered), and each liquidation as `{"channel": "liquidations:{iid}", "liq": {to_dict row}}`; the `_MAX_SUBSCRIPTIONS` cap counts them; the frontend hooks `useLiveDerivs`/`useLiveLiquidations` are added for 33.5; tests cover the parse of the two channel names, the cap and a malformed subscribe

**Given** the ranking engine (`ranking/domain/board.py` `InstrumentMetrics`, `ranking/__main__.py`'s subscriptions, `ranking/infrastructure/metrics_store.py` `COLS`)
**When** the story ships
**Then** `ranking` subscribes `derivs:raw` and `liquidations:raw` and each row gains **added fields only** (AD-D12; `test_replay.py`'s recorded bytes still pass with the new keys stripped): `funding_rate`, `funding_annualised`, `next_funding_ns`, `open_interest`, `oi_change_1h`, `oi_change_24h` (from the stored series through `catalog_prices.py`'s pattern, None when the series does not reach), `basis_mi_bps`, `basis_ml_bps`, `liq_long_1h`, `liq_short_1h`, `liq_notional_1h`, `liq_ratio_1h` (long/(long+short), None at 0), `forced_share_1h` (liquidation volume / traded volume, None at 0 volume), `relative_volume` (the last 1 h traded volume over the trailing 24 h hourly mean, None under 2 h of data), `range_position_24h` ((price − low24h)/(high24h − low24h), None on a flat range), `high_24h`, `low_24h`, `avg_trade_size` already present; `metrics_store.COLS` is extended with the new columns (AD-D12: appended, the writer's rows extended, a migration adding nullable columns in place), so the History page can tile them in 33.7; `docs/DATA_DICTIONARY.md` §3.2/§3.3 are amended field by field

### Story 33.5: Chart panes for open interest, funding, basis and liquidations, and the mark/index overlay

As the platform operator,
I want the derivatives data drawn where I read price: OI as a pane, funding as a pane with the countdown to the next payment, basis as a pane, mark and index as overlays on the candles, and liquidations as a mirrored histogram pane with markers on the candles at the price they died,
So that a flush, a squeeze or a funding extreme is visible on the same chart as the move it drove.

**Acceptance Criteria:**

**Given** the chart's pane mechanism (`LightweightChart.tsx` `IndicatorPaneSpec`, `PaneSeriesKind` `Line | Histogram`, the gap painter per pane from Story 32.1, pane heights persisted by 32.6) and the Indicators dialog (`IndicatorPicker.tsx`, Volume pinned first since 32.2)
**When** the story ships
**Then** the dialog gains a pinned **Derivatives** group with `Open Interest` (line pane, `oi_change` per bar in the legend), `Funding` (histogram pane coloured by sign, the current rate, its annualised figure and a live countdown to `next_funding_ns` in the legend, ticking each second from the client clock against the server value), `Basis` (line pane, `basis_mi_bps` and `basis_ml_bps` as two lines, zero line drawn), `Mark / Index` (two overlay lines on the price pane, style editable in the 32.3 settings modal) and `Liquidations` (a mirrored histogram pane: long liquidations below zero in the down colour, short above in the up colour, per-bar notional and count in the legend; a toggle for `size | notional`); each is fetched by a hook over the 33.4 routes with the candles' cursor pagination and seam rule, follows the live channel of 33.4 for the forming bar, is cut at the replay time in Bar Replay, draws a gap row in the gap colour, and persists on/off, style and pane height in the per-coin layout (32.6); on a spot instrument the group is shown disabled with "spot: no derivatives" and nothing is fetched

**Given** the liquidation rows (`/api/coin/{iid}/liquidations`) and lightweight-charts 5's series-markers plugin
**When** `Liquidations` is on and the bar spacing is above a named threshold
**Then** each liquidation draws a marker on the price pane at its `price_units` (a circle below the bar for a long liquidation, above for a short, radius by notional on a sqrt scale between named min and max px, the `price_kind` in its tooltip: "bankruptcy" or "mark"), markers merge per bar when more than a named count fall in one bar (one marker with the sum and count), and a **Liquidation tape** panel (toggle in the chart header) lists the last 50 liquidations of the instrument live (time, side, size, price, notional), formatted through `lib/units.ts`; `MetricTile.tsx` on the History page gains OI, funding and liquidation tiles from the metrics store columns of 33.4

**Given** the tests and MR4
**When** the story is merged
**Then** `LightweightChart.test.tsx`/`ChartPage.test.tsx` cover each pane's mount, legend values, spot disabling and replay cut; a `LiquidationMarkers.test.ts` covers the merge rule and the radius scale; the DocsPage chart section documents the five entries, the two price kinds and the countdown's clock rule; `platform/CLAUDE.md`'s "What is collected" points at where each derivative is shown

### Story 33.6: Order-flow indicators from the stored per-bar aggregates: delta, anchored CVD, organic delta, forced share, trade count, average trade size, VWAP and depth

As the platform operator,
I want the per-bar flow numbers the store now holds as chart indicators and a volume pane that can colour by delta,
So that I read aggression, forced flow and participation per bar from exact stored totals, on every timeframe, without a raw replay.

**Acceptance Criteria:**

**Given** the 33.3 columns on every candle and the custom indicator catalog (`views/indicator_picker.py` `CUSTOM_INDICATOR_CATALOG`, `replay_custom`, the per-coin indicator config of 32.3)
**When** the story ships
**Then** the catalog gains, each computed from the bar dicts only (no raw window, so no 7-day limit), unit-tested against a hand fold, and each a `Known limit:`-free entry: `VolumeDelta` (`buy_v − sell_v`, histogram), `CumulativeVolumeDelta` with its 33.3 anchor, `OrganicDelta` (`(buy_v − liq_short_v) − (sell_v − liq_long_v)`: a short liquidation is a forced buy, a long liquidation a forced sell; the docstring and a test state the mapping; null where liquidations are null), `ForcedShare` (`(liq_long_v + liq_short_v) / v`, histogram 0..1, null at `v = 0`), `TradeCount` (`buy_n + sell_n`, histogram with a `split` option drawing buys up and sells down), `AverageTradeSize` (`v / (buy_n + sell_n)`, null at 0 trades), `StoredVWAP` (`pv / v` per bar, and `session` / `anchored` cumulative modes from the stored numerators, the anchored mode as a drawing in 32.7's manner only if 32.7 has landed, else a parameter `anchor_t`), and `DepthWithinBps` (from `kernel.indicators.depth_within_bps` over the 1 s snapshots' last row per bar, the one indicator here that reads raw seconds, kept under the ≤ 7-day window with its `Known limit:`); the Volume pane gains a `colour by` setting `direction | delta` (delta: the bar's `buy_v − sell_v` sign, intensity by `|delta| / v`); the Technicals tab of the screener can pick every one of them like any catalog entry (its `technicals-values` route evaluates them on the latest closed bar from the store)

**Given** the tests and MR4
**When** the story is merged
**Then** `views/tests/test_indicator_picker.py` covers each indicator on a fixture with nulls, the `forced` sign mapping and the anchor modes; the frontend's legend shows each value through `lib/units.ts`; `docs/DATA_DICTIONARY.md` §2.7 lists them and the Docs page's Indicator Reference gains one entry each with the formula

### Story 33.7: The Rankings page sorts by every column and gains derivatives, flow and range columns, with saved filter presets

As the platform operator,
I want to sort the screener by any column, see open interest, OI change, funding, basis, liquidations, forced share, relative volume and the 24 h range position next to the existing metrics, and save the filters I keep rebuilding,
So that the table answers "where is the leverage, where is it being flushed, what is trading unusually" in one view, like the TradingView screener does.

**Acceptance Criteria:**

**Given** `RankingsPage.tsx` (sort only by Symbol and Exchange, `:142-215`), `views/ranking_columns.py` (the shared column definitions) and the 33.4 fields on `rankings:live`
**When** the story ships
**Then** every Performance column header sorts ascending/descending on click with a third click returning to rank order, a None value always sorts last in either direction, the sort key is a per-viewer preference in `localStorage` like the venue chips, and `ranking_columns.py` gains the columns `OI`, `OI Δ1h %`, `OI Δ24h %`, `Funding` (with the annualised figure in the tooltip and the next payment's countdown), `Basis (bps)` (mark − index), `Liq 1h` (notional, the long/short split in the tooltip), `Liq L/S` (`liq_ratio_1h`), `Forced %` (`forced_share_1h`), `Rel vol` (`relative_volume`), `24h range` (`range_position_24h` as a small bar with `high_24h`/`low_24h` in the tooltip); each column is a filter field in `FilterPanel.tsx` (`pages/filters.ts` gains them with their units), a spot row shows `—` for every derivatives column (never 0), and the `⏱`/`⚠` staleness markers are unchanged

**Given** the filter builder (`pages/FilterPanel.tsx`, `pages/filters.ts`)
**When** presets are added
**Then** a filter set can be saved under a name and recalled or deleted from a dropdown, stored server-side through `GET/PUT /api/rankings/filter-presets` in `views/preferences.py`'s one preferences directory (Story 32.5), with the active preset name shown as a chip; the History page (`HistoryPage.tsx`, `MetricTile.tsx`) tiles the new persisted columns of 33.4 (OI, funding, liquidation notional, forced share, relative volume), skipping a metric with no data as today

**Given** the tests and MR4
**When** the story is merged
**Then** `RankingsPage.test.tsx` covers sorting with Nones, the spot dashes and the preset round trip; `views/tests` the column definitions and the preset store; `docs/DATA_DICTIONARY.md` §2.10 and §3.5 are amended; `spec-multi-exchange-screener-chart.md` §B3 and §B4 are amended for the sort rule and the presets

### Story 33.8: Alert conditions beyond a price cross, indicator and derivatives conditions, and an Alerts page that creates and edits

As the platform operator,
I want alerts that fire on crossing up or down, on greater or less than, on a percent move within N bars, on an indicator's output (RSI above 70 on the 1H bar), on a trendline being crossed, on funding, open interest change or liquidation notional passing a threshold, and a page where I can create and edit them without opening a chart,
So that the platform watches what I would watch, in the terms I think in.

**Acceptance Criteria:**

**Given** `alerting/domain/alert.py` (one static `level`), `alerting/domain/policy.py` (`_crossed`, the three firing policies, `render`), `AlertEngine.on_bar` as a `BarObserver` on the forming bar, the TOML store (`alerting/infrastructure/toml_store.py`, full rewrite, a corrupt file raises) and the frozen key set (AD-D12)
**When** the story ships
**Then** an alert carries a `condition` table (added key; a stored alert without one is read as `{"kind": "price_cross", "level": <level>}`, so every existing alert keeps firing unchanged, with a test over the committed `alerts.toml` fixture) with kinds `price_cross` (as today), `price_cross_up`, `price_cross_down`, `price_above`, `price_below`, `pct_move` (`pct`, `bars`: the close moves by ≥ pct over the last N bars), `channel_exit` (`upper`, `lower`), `indicator` (`name`, `params`, `source`, `output`, `op` in `> < crosses_up crosses_down`, `value`: evaluated on the closed bars through `views.indicator_picker.replay_entry` for the alert's `(iid, bar_seconds)`, cached per bar so N alerts on one indicator compute it once), `trendline_cross` (`drawing_id` of a `trendline` in the coin's drawings, the line's price at the bar's time from `lib/drawings.ts`'s formula ported to `alerting/domain/geometry.py` with a cross-language fixture test), `funding_above`/`funding_below` (`rate`), `oi_change` (`pct`, `window_s`), `liquidation_notional` (`notional`, `window_s`, optional `side`) and `forced_share` (`share`, `window_s`); the derivatives conditions are evaluated by a second observer on the 33.4 live bus (`AlertEngine.on_deriv`, `on_liquidation`), every condition is a pure `evaluate(state, inputs) -> bool` in `alerting/domain/conditions.py` with a table-driven test per kind, the firing policies and the template apply to all (the template gains `{{value}}` and `{{condition}}`), run state stays in memory with the documented first-bar-after-restart limit, and an alert whose indicator or drawing no longer exists is marked `status: invalid` and ledgered, never silently skipped (DATA-07)

**Given** `data_api/routes/alerts.py` and `AlertsPage.tsx`
**When** the story ships
**Then** `PUT /api/alerts/{id}` edits an alert (condition, frequency, expiry, template, webhook; a fired `only_once` alert can be re-armed), `POST` accepts the condition table, the Alerts page gains a create form (instrument from the markets list, timeframe, condition kind with its fields, the indicator picked from the catalog) and an edit dialog per row, the chart's `AlertDialog.tsx` offers the same kinds with the price kinds prefilled from the clicked level and `trendline_cross` from a right-clicked trendline, the toast carries the condition text, and the schema is regenerated; `alerting/tests` cover every kind's `evaluate`, the back-compat read, the invalidation path and the derivatives observers; `docs/DATA_DICTIONARY.md` §2.11 lists the kinds and the Docs page documents them

### Story 33.9: Price-scale modes, chart types, and a compare symbol on the percent scale

As the platform operator,
I want log, percent and indexed price scales with an auto-scale lock, bars, area, baseline, Heikin Ashi and hollow candles, and a second symbol overlaid on the percent scale,
So that a 30 % move and a 3 % move read correctly, a long history is readable on log, and the same coin on two venues or two coins compare on one chart.

**Acceptance Criteria:**

**Given** `LightweightChart.tsx` (one `CandlestickSeries`, `LineSeries`, `HistogramSeries`, no `PriceScaleMode`), the chart header's Candles/Lines toggle and the per-coin layout of 32.6
**When** the story ships
**Then** the price pane's right scale offers `Normal | Log | Percent | Indexed to 100` (the library's `PriceScaleMode`, a header control and a right-click menu on the scale), an **auto-scale lock** (the library's `autoScale` off keeps the operator's vertical range through scrolls; double-click on the scale resets), `invert scale`, and the chart type control offers `Candles | Hollow candles | Bars | Line | Area | Baseline | Heikin Ashi`: Bars/Area/Baseline are the library's series, Hollow is the candlestick series with per-bar fill rules, Heikin Ashi is a pure transform `lib/heikinAshi.ts` over the loaded candles (unit-tested on a fixture; the legend states "Heikin Ashi (derived)" and every indicator and drawing stays on the **real** OHLC, with a test, AD-F6: a derived series is never fed to anything that reads price); every choice persists in the layout and the default template; the volume and indicator panes are unaffected

**Given** a second instrument
**When** `Compare` is used (a header button opening the symbol search of 33.12 if it has landed, else a text field accepting an instrument id; the same-asset other venues listed first through `kernel.venues.same_asset`, served by `GET /api/markets` from the `markets:live` message)
**Then** up to three compare series draw as lines on the price pane, forcing the scale to `Percent` from the first visible bar (or `Indexed`), each fetched through `useCandles` with the same timeframe and cursor rule, aligned on bar time with gaps where the other venue has none (never interpolated, AD-F6), coloured from a named palette with the symbol in the legend, removable from the legend row; a `Spread` toggle draws `(a − b) / b` in bps as a pane for exactly two series (the cross-venue basis of `research/application/aligned.py` `basis_bps`, the formula shared by naming it in both docstrings); compare series persist in the layout; `LightweightChart.test.tsx` covers the scale modes, the type switch, the Heikin Ashi isolation and the compare alignment; the Docs page documents the scale and type rules

### Story 33.10: Drawing tools II: ray, extended line, vertical line, rectangle, parallel channel, text, arrow, Fibonacci extension and saved price/date ranges, with magnet snapping, undo/redo, lock and hide-all

As the platform operator,
I want the TradingView drawing set I actually use beyond the four tools built, snapping to OHLC, undoable, lockable and hideable in one click,
So that marking a chart is as fast as it is in TradingView and nothing I draw is lost or nudged by accident.

**Operator decision (2026-10-05, quick-dev `spec-quick-volume-overlays-modal-and-grouped-tool-rail.md`):** the left rail is grouped like TradingView, declared as data in `lib/chartTools.ts` (Cursor, Lines, Fibonacci, Projection, Measure, Volume-based; each group button arms its last-used tool, an arrow opens its menu). Every tool this story adds is appended to a group there, never as a new flat rail button: ray, extended line, vertical line and parallel channel under Lines; Fibonacci extension under Fibonacci; rectangle, text and arrow in a new Shapes/Annotation group; price and date ranges under Measure. Magnet, undo/redo, lock and hide-all are rail toggles/actions like the crosshair toggle, not group members.

**Acceptance Criteria:**

**Given** `lib/drawings.ts` (`Drawing` union, `KINDS`, `nextDrawingId`, the server-side persistence through `useChartDrawings.ts` and `PUT /api/coin/{iid}/drawings`), the primitives (`TrendlinePrimitive`, `FibPrimitive`, `PositionPrimitive`, `MeasurementPrimitive`, `VerticalMarkerPrimitive`, `drawingPrimitive.ts`) and the left rail of 32.5
**When** the story ships
**Then** the union gains `ray` and `extended` (a trendline with `extend: "right" | "both"`, drawn by `TrendlinePrimitive` with the extension flag), `vline` (time only), `rect` (two corners, fill at a settable opacity, the price range and % in a corner label), `channel` (a trendline plus a parallel offset, three handles), `text` (anchored at `(time, price)`, editable in a dialog, font size setting), `arrow` (two points, head at the end), `fib_extension` (three points, the levels of `fib` projected from the third), `price_range` and `date_range` (the measurement, persisted: price change, %, bars and summed volume like `MeasurementPrimitive`, which stays the temporary tool); every kind has a primitive, a settings dialog (colour, width, style, kind-specific fields), the context menu of 32.5, handles in Cursor mode, and a `locked: boolean` and `hidden: boolean` flag; `drawings.ts`'s parser rejects an unknown kind loudly (a ledgered `drawings.unknown_kind`, the file never rewritten without it, DATA-07) and round-trips every kind byte for byte (a test per kind)

**Given** the editing mechanics
**When** the operator draws
**Then** a **magnet** toggle in the rail snaps a point to the nearest of the bar's O/H/L/C within a named px radius (weak magnet) or always (strong), **undo/redo** (Ctrl+Z / Ctrl+Shift+Z, a bounded history of 100 drawing-set states kept in the page, never persisted), **lock** (a locked drawing has no handles and ignores drags), **hide all drawings** (an eye in the rail, a layout flag of 32.6), **delete all** behind a confirm, Shift constrains a trendline to horizontal/vertical/45°, and the Escape rule of 32.5 holds; `drawings.test.ts`, `drawingKit.ts` and a test per primitive cover the geometry, the snap, the undo history and the lock; the Docs page's Drawings article lists every tool and the shortcuts

### Story 33.11: The indicators the catalog lacks, candle patterns as markers on the candles, and the dead code removed

As the platform operator,
I want Supertrend, Parabolic SAR, an ADX line, Williams %R, Pivot Points, Money Flow Index, Chaikin Money Flow, the Awesome Oscillator and ZigZag in the picker, candle patterns drawn as markers on the candles that found them, and nothing in the repo that pretends to be a feature,
So that the picker covers what TradingView users reach for first and the Docs describe what the app actually does.

**Acceptance Criteria:**

**Given** `nautilus_trader.indicators` (checked first, per the Nautilus-first rule: none of the nine exists there; `DirectionalMovement` gives `pos`/`neg` only, no ADX) and the two precedents for a custom `Indicator` (`kernel/indicators.py`, `kernel/candle_patterns.py`)
**When** `kernel/ta.py` is added
**Then** it holds streaming `Indicator` subclasses, `O(1)` per bar, each with `update_raw`, `initialized` semantics and a hand-computed fixture test from a published reference series (TEST-01): `Supertrend(period, multiplier)` (value, direction), `ParabolicSAR(step, max_step)`, `AverageDirectionalIndex(period)` (ADX, +DI, −DI; the DIs from Nautilus's `DirectionalMovement`, only the ADX smoothing added), `WilliamsPercentR(period)`, `PivotPoints(kind: standard | fibonacci | camarilla, session)` (levels from the previous session's H/L/C by the bucket rule of §2.5), `MoneyFlowIndex(period)`, `ChaikinMoneyFlow(period)`, `AwesomeOscillator(fast, slow)`, `ZigZag(deviation_pct)` (swing points, drawn as a line through them, marked `repaints last leg` in the legend as TradingView does); each is registered in `views/indicator_picker.py`'s catalog with panel, params and source, and in the `IndicatorSignalStrategy` signal table where a direction is defined (Supertrend, PSAR, ADX, MFI, CMF, AO); `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` lists them under "custom, no built-in"

**Given** `CandlePattern` drawing ±100 spikes in a pane (`ChartPage.tsx:459-461`) and lightweight-charts 5's series-markers plugin
**When** the story ships
**Then** a `CandlePattern` entry draws a marker on the pattern's bar (up arrow below for bullish, down arrow above for bearish, the pattern name on hover; the pane display stays as an option `display: markers | pane`), the Technicals tab's pattern columns are unchanged, and `ChartPage.test.tsx` covers the marker placement

**Given** DESIGN-03 and the review's dead-code list
**When** the story is merged
**Then** anything 33.4 left is gone: no file under `platform/` is importable without a caller (`platform/tests/test_boundaries.py` gains a dead-module check over `views/`, `kernel/` and `frontend/src/hooks`: every exported function or hook has at least one non-test importer), the Docs page's Indicator Reference and Knowledge Base describe only shipped features (the `footprint` entry becomes Story 32.8's trade footprint when it exists, else is removed), and `docs/DATA_DICTIONARY.md` §2.4 and §2.6 are rewritten or removed accordingly

### Story 33.12: Symbol search and watchlist, fullscreen chart with its panes, keyboard shortcuts, time zone, session breaks, countdown and the last-price label

As the platform operator,
I want to switch instrument from the chart page, put the chart and every pane connected to it in fullscreen, drive the timeframe and tools from the keyboard, and the small TradingView conveniences the chart still lacks,
So that moving between coins and timeframes costs nothing and the chart behaves like the one my hands know.

**Operator decision (2026-10-05):** no multi-chart layouts (no 2-up / 2x2 grid, no synced crosshair between charts) and no chart data export (no CSV download); instead the chart can go fullscreen together with all its connected panes.

**Acceptance Criteria:**

**Given** `ChartPage.tsx` (one chart, a symbol label and a back link; coins picked on Rankings), the `markets:live` message (§3.6) and the per-coin layouts of 32.6
**When** the story ships
**Then** a **symbol search** (Ctrl+K or clicking the symbol) lists every collected instrument from `GET /api/markets` with venue, market and 24 h volume, filters as you type on symbol and venue, and navigates on Enter; a **watchlist** rail (toggle, persisted server-side in the preferences directory, `GET/PUT /api/watchlist`) holds pinned instruments with their live price and 24 h % from `rankings:live`, each click swapping the chart; a **fullscreen** button in the chart top bar (and `Shift+F`) puts the chart and every pane connected to it -- the price pane, Volume, the indicator panes, the derivatives and liquidation panes of 33.5 and the footprint, with their legends and the left tool rail -- into fullscreen through the browser Fullscreen API on the one element that holds them, so they keep their relative pane heights and stay interactive (drawing, replay, legend gear/x); `Esc` or the button leaves it; the chart resizes to the new viewport on enter and on exit (the existing resize observer, no fixed sizes), a browser that refuses fullscreen says so inline instead of failing silently, and fullscreen is a view state, never persisted in the layout; keyboard: digits + Enter for a timeframe (`1`, `5`, `15`, `1h`, `4h`, `d`, `w`), `Alt+T` trendline, `Alt+H` horizontal line, `Alt+F` fib, `Alt+V` vertical line, `Alt+R` replay, `Shift+L` log scale, `Shift+F` fullscreen, `Alt+C` compare, `/` opens the search, `?` shows the shortcut sheet (an inline dialog, no library), all listed on the Docs page (`kbData.ts`)

**Given** the loaded series and the chart's settings
**When** the story ships
**Then** a **time zone** setting (`UTC | local | exchange (UTC)`) applies to the time scale and every printed time through one `lib/time.ts` (the data stays UTC; a test that a bar's `t` is never changed), **session breaks** draw a dashed vertical line at each UTC day boundary (toggle), a **bar countdown** shows the time to the forming bar's close under the last price label, the **last price line and label** are togglable with the line's colour following the last bar's direction, ~~and Bar Replay works in **Lines mode** too (the five snapshot series cut at the replay time like the candles)~~ **[amended 2026-10-07: operator]** Bar Replay is supported only on candle charts (candles mode, every chart type incl. Line/Area); Lines mode never gets Replay; the control is disabled there with the visible reason "Replay is available on candle charts"; every setting persists in the layout; `ChartPage.test.tsx`, `chartBrowserState.test.ts` and a `fullscreen.test.tsx` cover the search, the fullscreen enter/exit (every connected pane inside the fullscreen element, the resize on both transitions, the refused-request message, never persisted), the shortcut map and the time-zone invariance; `spec-multi-exchange-screener-chart.md` §A1 and §A9 are amended for what this story adds that the original spec excluded, each with the operator's 2026-10-05 decisions cited (including that multi-chart and export were dropped)

### Story 33.13: Liquidation and forced-flow research: the cascade detector, the implied-leverage profile, the forced/organic split proven on the trade archive, and the strategy filter

As the platform operator,
I want the research layer to show me what the liquidation archive is worth: when cascades happen and what follows them, how levered the crowd was, how much of the volume was forced, and whether filtering forced flow out of OFI improves the strategy,
So that liquidations become a tested input to the bots, not a chart decoration.

**Acceptance Criteria:**

**Given** `research/application/microstructure.py` (`trade_flow`, `basis_frame`, `price_impact`, `rolling_volatility`), `research/application/aligned.py` (`oi_changes`, `funding_*`, `cross_venue`), `research/application/ports.py` `MarketFrames` and the `Liquidation` rows of 33.1/33.2
**When** the story ships
**Then** `MarketFrames` gains `liquidations(iid, start, end)` (typed rows, units decoded once, the `price_kind` kept), and `research/application/liquidations.py` adds pure, tested functions: `cascade_episodes(liqs, window_s, baseline_s, intensity_threshold, decay_ratio)` (episodes as `kernel.indicators.LiquidationCascade` of Story 33.14 reports them -- the function replays the indicator over the rows, never a second definition of a cascade -- with side, duration, peak rate and the forward returns at 1, 5, 15 and 60 minutes after the episode's end), `implied_leverage(liqs, mark_index_frame)` (per liquidation `mark / |mark − bankruptcy|` where `price_kind == "bankruptcy"`, a distribution per day; null for mark-priced rows, a `Known limit:` for Hyperliquid), `forced_share(liqs, seconds)` (per minute and per hour, forced volume over traded volume) and `match_to_trades(liqs, trades, tol_s)` (the self-check of 33.1 as a research frame: matched share, size and time offsets), `organic_delta(seconds, liqs)` (the 33.6 formula at second resolution), and `aligned.py` gains `liquidations_vs_oi(liqs, oi)` (deleveraging episodes: liquidation notional against OI change over the same window) and `cross_venue_liquidations(a, b)` (lead-lag of cascades between Bybit and Hyperliquid on the same asset); `research/notebooks/09_liquidations.py` (jupytext-paired, run in `make test` like the others, `_params.py` driven) presents them on a recorded week with the research README's reading guide

**Given** `research/strategies/ofi_strategy.py` (`OFIStrategy`: multi-level OFI z-score, OBI and cumulative-delta confirmation, EMA filter) and `RunSpec`
**When** the story ships
**Then** the strategy gains `forced_flow_filter: bool` and `liquidation_cascade_mode: "off" | "fade" | "follow"` parameters: the filter feeds the strategy's cumulative delta from the organic delta (liquidation rows subscribed as custom data in the backtest, `BacktestDataConfig` for `custom_liquidation`), the cascade mode suppresses entries during a detected episode (`off` ignores it, `fade` enters against the cascade's side after its rate decays below the baseline, `follow` enters with it while the rate rises), each a small pure decision function with a test; `research/notebooks/08_strategy_gallery.py` gains the three variants in its sweep with the fill/fee/latency models unchanged; the gallery's result table shows them beside the baseline with no claim beyond the recorded week (the notebook's text names the sample size)

**Given** MR4
**When** the story is merged
**Then** `docs/DATA_DICTIONARY.md` §2.12 lists notebook 09 and the new frames; `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` documents the two parameters; the DDD spine's research row (AD-D1) names liquidations among the research inputs

### Story 33.14: The liquidation cascade bot: a strategy that shorts into a long-liquidation cascade, backtested on the archive and run as a paper bot on the live liquidation feed

As the platform operator,
I want a bot that recognises a cascade of long liquidations as it builds, enters short with the forced selling, and gets out when the cascade is spent or the stop is hit, with the same code backtested on the liquidation archive and run live as a paper bot,
So that forced flow is traded, not only charted, and I can see in the gallery and in `live-paper` whether following cascades pays before any real money touches it.

**Acceptance Criteria:**

**Given** `kernel/indicators.py` (streaming `Indicator` subclasses, `O(1)` per event) and the `Liquidation` rows of 33.1/33.2
**When** the story ships
**Then** `kernel/indicators.py` gains `LiquidationCascade(window_s, baseline_s, intensity_threshold, decay_ratio)`: fed one `Liquidation` at a time (`update_liquidation(side, notional_units, ts_ns)`), it keeps a time-bounded deque of the window's liquidations per side, `rate_long`/`rate_short` (notional per second over `window_s`), a baseline (exponential mean of the window rate over `baseline_s`, floored at a named minimum so a quiet market never divides by zero), `intensity = (rate_long + rate_short) / baseline`, `direction` (`-1` when `rate_long > rate_short`: longs are being force-sold, the price is falling; `+1` when shorts dominate; `0` under the threshold), `active` (`intensity >= intensity_threshold`), `peak_rate` of the current episode and `spent` (`active` was true and the rate has fallen below `peak_rate × decay_ratio`), `initialized` once `baseline_s` of data has been seen; `kernel/tests/test_liquidation_cascade.py` drives a synthetic cascade (a quiet hour, a 90 s burst of long liquidations, a decay) and asserts every output's transitions at hand-computed points, and `research/application/liquidations.py`'s `cascade_episodes` (Story 33.13) is specified to be built on this indicator, so research, the backtest and the bot never disagree on what a cascade is (SSOT-02)

**Given** `research/strategies/` (`StrategyConfig` + `Strategy` pairs importing only `kernel` and `nautilus_trader`; `OFIStrategy` subscribes `DataType(DydxSecondSnapshot)`; `CandlePatternStrategy` is the live-runnable reference with `last_data_ns`, quote ticks for the Sandbox fill engine and `forbid_unknown_fields=True`)
**When** `research/strategies/liquidation_cascade_strategy.py` is added
**Then** `LiquidationCascadeStrategyConfig` holds the indicator's four parameters plus `sides` (`["short"]` by default: enter short on a long-liquidation cascade; `["short", "long"]` also enters long on a short squeeze), `mode` (`follow`: enter while `active` and the rate is still rising toward or at its peak; `fade`: enter against the cascade once `spent`), `min_episode_notional` (quote units, below which an episode is ignored), `entry_timeout_s` (no entry past this long after `active` turned true: a cascade that is already old is not chased), `max_entries_per_episode` (default 1), `cooldown_s` between episodes, `trade_size`, `stop_pct` or `stop_atr_multiple` (one of them, ATR from the strategy's own 1-minute bars as `CandlePatternStrategy` does), `take_profit_r` (a multiple of the stop distance, optional), `exit_on_spent` (default true: close when the indicator reports `spent`), `max_hold_s`, `ofi_confirm` (optional: the OFI sign must agree with the entry side, from the OFI indicator over quote ticks as `bots/strategies/dummy.py` samples it), and `max_daily_loss` (quote units; once breached the strategy stops entering for the UTC day and logs it); the strategy subscribes `DataType(Liquidation)` and quote ticks, keeps `last_data_ns`, feeds the indicator in `on_data`, takes every decision through pure functions in `research/strategies/cascade_rules.py` (`should_enter(state, config) -> OrderSide | None`, `should_exit(state, position, config) -> str | None` returning the exit reason), never adds to an open position, submits market orders with the `bot_id` as `order_id_tag` (AD-11), and writes one `bots.strategies.signal_log` record per indicator update (`ts_ns`, intensity, direction, active, spent, decision, reason) in the same shape `DummyStrategy` writes, so the parity tool can pair cycles; `research/strategies/tests/test_cascade_rules.py` covers every branch of both rule functions (sides, mode, timeout, cooldown, max entries, daily loss, each exit reason), TEST-01

**Given** the backtest side (`research/strategies/snapshot_backtest.py`, `_bars.py`, `research/run_backtest.py`, `RunSpec`, `BacktestDataConfig` for custom data as the OFI backtest reads `custom_dydx_second_snapshot`)
**When** the story ships
**Then** `research/strategies/backtest_liquidation_cascade.py` runs the strategy on a recorded window with `BacktestDataConfig` rows for `custom_liquidation` and the quotes from the snapshots (`kernel/snapshot_book.py`'s `snapshot_quote`), a planted test proves on a fixture day with a synthetic cascade that exactly one short is entered inside the burst and closed on `spent` with the signal log stating both reasons, `research/notebooks/08_strategy_gallery.py` gains the strategy (follow and fade, short-only and both sides) in its sweep under the unchanged fill/fee/latency models with `_params.py` entries, and the gallery text states the sample (days, number of episodes found) next to every figure

**Given** the bots context (`bots/infrastructure/nautilus_host.py`: `STRATEGIES`, `build_node`, `_paper_venue_clients`; the live node receives market data from Nautilus's venue data clients, which carry no `Liquidation`) and the collector's `liquidations:raw` channel (33.1)
**When** the strategy is made runnable as a paper bot
**Then** `bots/infrastructure/liquidation_data_client.py` adds a Nautilus `LiveMarketDataClient` subclass (the documented custom-data-client extension point, NAUT-01: no `TradingNode` internals touched) that subscribes `liquidations:raw` on the fleet's Redis, decodes each entry with `Liquidation.from_dict` (an undecodable entry is an incident record, never dropped silently, DATA-07), publishes it through the client's `_handle_data` for the instruments the fleet's bots subscribed, reports its own `last_data_ns` and connection state to `bots.infrastructure.cache_reader`'s staleness log, and is added to the node by `build_node` only when a bot's strategy subscribes `DataType(Liquidation)`; `STRATEGIES` gains `"liquidation_cascade"`, `platform/tests/test_images.py`'s `_STRING_PATH_IMPORTS` the two paths, `docker-compose.yml` the `bot_tui` source mount, `bots/config.toml` a commented example `[[bots]]` with `[bots.params]` for `BTCUSDT-LINEAR.BYBIT`, `bots/signal_replay.py` replays this strategy too (`--strategy` or by the bot's configured strategy) so `verification.bot_parity` pairs its live cycles with the catalog replay on equal `ts_ns`, and `bots/tests` cover the client bridge against the throwaway Redis the bots tests already need, the `STRATEGIES` row and the config example parsing; the bot never runs under `ExecConfig` (real money stays `DummyStrategy`-only, the existing `Known limit:`), and `bots/README.md` plus `docs/BOT_OPERATIONS.md` §3 document the strategy, its parameters, that the live feed depends on the Bybit collector publishing liquidations, and the paper-first rule

**Given** MR4
**When** the story is merged
**Then** `docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` documents the strategy and the indicator, `docs/DATA_DICTIONARY.md` §2.1 the indicator, §2.12 the gallery entry, `docs/DATA_INTEGRITY_AUDIT.md` gains rows for the side-direction mapping (a long liquidation is a forced sell) and for the live-feed dependency, and `docs/DEPLOY_CHECKLIST.md` the deferred operator action (add the bot to the paper config on the VPS, rebuild `live-paper`)
