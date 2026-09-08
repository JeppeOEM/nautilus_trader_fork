---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/DESIGN.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/EXPERIENCE.md
---

# nautilus_trader_fork - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for nautilus_trader_fork (the `troll/` dYdX Signal Research & Trading Platform), decomposing the requirements from the PRD and Architecture spine into implementable stories. Epics 1–3 (below) cover the original PRD/architecture scope (FR1–FR15) and were fully implemented as of 2026-07-17. This document was reopened on 2026-07-24 to extend coverage for the PRD's volatility Ranking Mode (FR-16) and new Bot Monitoring TUI feature (FR-17–FR-25), backed by a finalized UX design contract (`DESIGN.md`/`EXPERIENCE.md`) — the project's first UX-driven surface. Reopened again on 2026-09-06 to add Epic 8 (FR28–FR30): TradingView-style multi-chart navigation and selectable technical indicators on the web dashboard's coin chart. Numbered Epic 8 (not 5) because epics 5–7 were filed directly as standalone stories bypassing epics.md ceremony (see `sprint-status.yaml`) — Epic 8 continues that same global epic-number sequence to avoid collision with their story-file paths (`5-1-*`, `6-1-*`, `7-1-*`). No PRD/Architecture update precedes this addition (same precedent as FR27); PM should fold FR28–FR30 into the PRD proper once shipped. (FR31, a combined candlestick + bid/ask overlay, was drafted alongside these but the story implementing it — 8.5 — was dropped before dev started; FR31 removed with it.)

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

### NonFunctional Requirements

_The PRD has no explicit NFR section; the following are derived from the Vision, Success Metrics, and Architecture spine invariants that constrain how the FRs above must be implemented._

NFR1: Data integrity — zero tolerance for corrupted or fabricated market data; genuinely unavailable data must be visually flagged (gap/break), never papered over with a fabricated flatline or stale value displayed as live (Vision; FR-3/FR-4; SM-C1 counter-metric: a rejected Snapshot is a gate success, not a coverage failure to fix by relaxing validation).
NFR2: Operational reliability — the Dummy Strategy must run continuously in paper mode against live data for at least one week without manual intervention (SM-3).
NFR3: Memory-bounded access — no unbounded catalog reads (e.g. `catalog.trade_ticks()` with no time bounds); all data access is time-bounded or streamed via `BacktestDataConfig`; non-configured coins are rolling-window-in-memory only (architecture AD-6, Consistency Conventions).
NFR4: Fork safety — `nautilus_trader/` and `crates/` are never modified; all `troll/` work is additive so upstream merges stay possible (architecture, all ADs; fork boundary convention).
NFR5: Precision correctness — price/quantity precision changes only via `Decimal.scaleb()` + `Price.from_raw()`/`Quantity.from_raw()`; never `Price(decimal, precision)`/`Quantity(decimal, precision)`, never inferred from digit count, never round-tripped through `float` (architecture AD-5).

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

