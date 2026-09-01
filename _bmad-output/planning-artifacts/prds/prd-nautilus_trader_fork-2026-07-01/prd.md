---
title: 'dYdX Signal Research & Trading Platform'
status: 'final'
created: '2026-07-01'
updated: '2026-07-24'
---

# PRD: dYdX Signal Research & Trading Platform

## 0. Document Purpose

This PRD scopes the whole `troll/` product: continuous dYdX market-data collection, coin ranking, signal/indicator research, backtesting, and live paper trading. It builds on two existing artifacts rather than duplicating them: `_bmad-output/project-context.md` (35 agent implementation rules) and the architecture spine at `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md` (the Gatekeeper paradigm governing the data-collection path, amended during this PRD's discovery — see §4.5). Features are grouped with functional requirements (FRs) nested and globally numbered; assumptions are tagged inline and indexed in §9.

## 1. Vision

A solo research-to-live pipeline for trading dYdX perpetuals. Raw market data is captured with zero tolerance for corruption or fabrication, then used to rank coins by opportunity (fast HFT-style signals and slower structural ones alike) so the builder can decide, day to day, which coins deserve attention. Signals and indicators are built once, in Jupyter, using Nautilus's own research conventions — and that exact same code runs unmodified in backtests and in a live paper-trading strategy. Backtests aren't limited to one instrument at a time: the whole ranked watchlist can be run at once, at both HFT (sub-second) and slower candlestick timeframes derived from the same underlying capture. The end state is a live paper-trading "dummy" strategy that wires together every signal produced by the research side, proving the whole loop actually closes.

## 2. Target User

### 2.1 Jobs To Be Done

- As the sole builder and operator, I need to know which dYdX coins are worth watching *right now*, using both fast and slow signals — not just the biggest-cap coins by default.
- As a researcher, I need to build and iterate on indicators in Jupyter without having to reimplement them later for backtesting or live use.
- As a strategy developer, I need to backtest across many coins at once, at more than one timeframe, using data I trust completely.
- As an operator, I need to see a real (paper) strategy actually trade on my signals, end to end, without risking real capital.

### 2.2 Key User Journeys

*Lighter scope dial — solo hobby project, single operator role, no multi-stakeholder UX. One line per JTBD rather than full narrative UJs:*

- **UJ-1.** Builder opens the ranking view, sees which coins are currently hot by HFT + structural signals, and flags one for opt-in raw-delta capture to study further.
- **UJ-2.** Builder writes a new indicator in Jupyter against captured data, then runs it — same code, no rewrite — inside a multi-coin `BacktestNode` run across the current watchlist.
- **UJ-3.** Builder starts the paper-trading dummy strategy and watches it place (paper) orders driven by the same signals just validated in backtest.
- **UJ-4.** Builder SSHes into the Hetzner box, opens the terminal UI, and at a glance sees per-bot PnL/health and which coins are hot right now (by volume or volatility mode); drills into a coin's live indicators and order book with a keypress, hands off to the web dashboard for graphs with another, and starts/stops a bot without leaving the terminal.

## 3. Glossary

- **Snapshot** — A `DydxSecondSnapshot`-style capture of top-of-book levels (bid/ask prices+sizes, default 20 per side) and per-side trade volume, taken at a configurable interval (default 0.5s).
- **Raw Delta Capture** — Opt-in, per-coin capture of every order-book delta event as it occurs, with no fixed sampling interval, enabling resampling to any interval at read time.
- **Coin Ranking** — A continuously-refreshed ordering of subscribed coins by combined HFT indicators (OFI, OBI, microprice, spread) and slower/structural indicators (liquidity, volume).
- **Watchlist** — The live, queryable output of Coin Ranking; usable to select which coins a multi-coin backtest runs against.
- **Indicator / Signal** — A computed quantity (OFI, OBI, microprice, spread, or a new ML-derived signal) implemented once and consumed identically in Jupyter, backtest, and live contexts.
- **Gatekeeper** — The architecture paradigm binding `dydx_collector` as the sole writer and sole validator of market data (see architecture spine AD-1 through AD-7).
- **Dummy Strategy** — The v1 live paper-trading reference strategy that consumes every signal produced by the research side; not intended as a profitable strategy, but as an integration proof that the full loop (research → backtest → live) actually closes.
- **Ranking Mode** — The active sort basis for Coin Ranking: either pure volume (FR-6) or cross-sectional volatility (FR-16), user-selectable — not a single blended weighted score.
- **Bot Monitoring TUI** — A keyboard-only terminal UI (urwid) that mirrors the web dashboard's live coin ranking and adds a bots-status view, for fast SSH-based monitoring and light control without a browser.

## 4. Features

### 4.1 Data Collection & Integrity
**Description:** Continuous, trustworthy capture of dYdX market data, per the existing Gatekeeper architecture (already largely implemented — this section confirms the shape rather than proposing new build). Realizes UJ-1.

**Functional Requirements:**

#### FR-1: Configurable-interval snapshot capture
The system captures a Snapshot for every subscribed coin at a configurable interval, defaulting to 0.5s.

**Consequences (testable):**
- Interval is a config value, not a hardcoded constant; changing it requires no code change.
- Default interval is 0.5s.

#### FR-2: Opt-in raw delta capture
The user can flag specific coins for Raw Delta Capture in addition to standard Snapshots.

**Consequences (testable):**
- Flagging a coin does not affect Snapshot capture for other coins.
- Raw-delta-captured coins support resampling to any interval at read time (backtest, Jupyter) without re-collection.
- Retention/pruning for raw-delta-captured data is a per-coin config value, including an unlimited (never-pruned) setting — no automatic expiry is imposed.

#### FR-3: Fail-closed data integrity gate
The system rejects — never fabricates, clamps, or averages — invalid data at the point of capture: crossed books, stale books, and precision-invalid values.

**Consequences (testable):**
- Every rejection is logged with the offending payload and specific reason (existing behavior — architecture spine AD-2).
- No schema carries a validity/flag field; rejected data is simply absent from the catalog and live stream.

#### FR-4: Reconnect & gap resilience
The system recovers from WebSocket/HTTP disconnects without corrupting stored data, and flags resulting gaps rather than interpolating across them.

**Consequences (testable):**
- A gap in capture appears as a visible break (e.g. `None`/null) in any downstream chart or read, never a flat/interpolated line.

#### FR-5: USD-denominated liquidity capture
Open interest is polled separately and liquidity is classified using USD-denominated values (`volume24H` or `openInterest × oraclePrice`), never raw token-unit open interest.

**Consequences (testable):**
- No liquidity/volume classification anywhere in the system compares a raw token-unit `openInterest` value against a USD threshold.

**Notes:** FR-1 through FR-5 largely restate already-adopted architecture (AD-1–AD-7); included here so the PRD is a complete capability picture, not because new work is implied.

### 4.2 Coin Ranking & Watchlist
**Description:** Ranks all subscribed coins to answer "what's worth watching right now" — dual purpose as a research tool and a live filter for backtests. Realizes UJ-1.

**Functional Requirements:**

#### FR-6: Coin ranking, sorted by volume
The system ranks all subscribed coins, refreshed continuously. In volume Ranking Mode (the default), the sort order is strictly by volume (USD, per FR-5's `volume24H` convention) — highest volume first. HFT indicators (OFI, OBI, microprice, spread) and slower/structural indicators (standard technical-analysis indicators, e.g. RSI and others, sourced from ta-lib/pandas-based Python libraries) are computed and displayed per coin alongside the rank, but do not affect sort order in this mode. See FR-16 for the volatility-based alternative Ranking Mode.

**Consequences (testable):**
- Coins are ordered strictly by descending `volume24H` (USD) in volume mode; this ordering is independent of any HFT/TA indicator value.
- Ranking updates as new Snapshot data arrives, not on a static/one-time schedule.
- Ranking is inspectable historically (which coin ranked where, when) — realizes the research JTBD.

#### FR-16: Volatility-based ranking mode
The system computes a volatility indicator (standard deviation of price/returns) per coin and ranks coins by relative volatility — cross-sectional comparison against all other subscribed coins, highest relative volatility first — as a user-selectable alternative Ranking Mode to FR-6's volume sort.

**Consequences (testable):**
- The user can switch the Watchlist's active Ranking Mode between volume (FR-6) and volatility (FR-16) without a code change.
- Volatility is implemented once and consumed identically per §4.3's single-implementation convention — Jupyter, backtest, live, web dashboard, and TUI all read the same computed value, never a per-surface reimplementation.
- A coin with higher relative volatility than its peers ranks higher in volatility mode; ranking is independent of that coin's volume rank.
- The volatility lookback window is a configurable value (mirroring FR-1's configurable-interval pattern), defaulting to 1 hour; changing it requires no code change.

**Notes:** Resolves OQ-1 (see §8) — composite ranking is realized as user-selectable Ranking Modes, not a single blended weighted score across all indicators. Volume Ranking Mode (FR-6) intentionally stays pinned to dYdX's fixed `volume24H` indexer figure for v1 — an in-house, similarly configurable-window volume calculation would require computing volume from raw captured trades rather than reading the exchange's fixed field, and is a future direction, not v1 scope.

#### FR-7: Live watchlist
The ranked list is queryable live and can be used directly to select a coin set for a multi-coin backtest.

**Consequences (testable):**
- A coin whose ranked opportunity is short-lived still appears/disappears from the watchlist accordingly — the watchlist is not limited to a fixed, manually-curated coin set.

#### FR-8: Research ranking view
The user can inspect how a coin's ranking evolved over time, to decide whether it warrants FR-2's opt-in raw-delta capture.

**Consequences (testable):**
- Ranking history is queryable for any past timestamp within the collector's retention window, not just the current live rank.

### 4.3 Signal & Indicator Research
**Description:** Jupyter-based research on indicators/ML signals, using Nautilus's own research-notebook conventions rather than custom tooling. The defining requirement of this feature is code reuse across contexts. Realizes UJ-2.

**Functional Requirements:**

#### FR-9: Jupyter research environment
The user can develop and test indicators and ML signals in Jupyter against catalog data, following the conventions of Nautilus's own example research notebooks (the tutorial/research notebooks shipped with `nautilus_trader`) — not a custom notebook framework.

**Consequences (testable):**
- A new indicator authored in a research notebook imports the same class/function later used in backtest and live contexts (per FR-10) — no notebook-local reimplementation.

#### FR-10: Single indicator implementation, three consumption contexts
Every indicator/signal is implemented exactly once and consumed via the identical code path in Jupyter research, backtest, and live strategy contexts.

**Consequences (testable):**
- No indicator has a second, parallel implementation for backtest vs. live vs. research.
- A new indicator built in Jupyter is usable in a backtest and in the Dummy Strategy (§4.5) without modification.

### 4.4 Backtesting
**Description:** Nautilus-native backtesting (no custom engine), supporting both HFT and slower timeframes, and multi-coin runs across the Watchlist. Realizes UJ-2.

**Functional Requirements:**

#### FR-11: Nautilus-native backtesting
The system uses `BacktestNode` + `BacktestDataConfig` exclusively; strategies are referenced via `ImportableStrategyConfig` by string path.

**Consequences (testable):**
- No custom simulation/matching loop exists in `troll/`.
- Strategy parameter sweeps and time-range filtering require no code changes (existing architecture convention, AD-6).

#### FR-12: Dual-timeframe strategies
The user can backtest strategies at HFT granularity (raw 0.5s/1s Snapshot data) and at slower timeframes (candlesticks aggregated from that same underlying data, with an adjustable aggregation window).

**Consequences (testable):**
- Candlestick aggregation window is configurable (e.g. 1s, 1m, 5m) without re-collecting data.

#### FR-13: Multi-coin backtest runs
The user can run a single backtest across the full ranking Watchlist — many coins at once, not just one instrument at a time — using the Watchlist's current, dynamic output rather than a fixed static coin universe.

**Consequences (testable):**
- A single `BacktestNode` run accepts the Watchlist's coin set as-is (no manual per-coin config editing needed to add/remove a coin as it enters/leaves the Watchlist).

### 4.5 Live Paper Trading
**Description:** A live, paper-mode reference strategy ("Dummy Strategy") that consumes every signal the research side has produced, proving the research → backtest → live loop closes end to end. Realizes UJ-3. This feature required an architecture amendment: AD-8 previously banned any `TradingNode`/`Strategy` instantiation anywhere in `troll/`, written in response to an earlier incident where `TradingNode` was misused as a *data recorder* (OOM/shutdown-wedge failure). That ban has been narrowed to the data-collection/reading path only — `TradingNode` used for its intended purpose, running a trading strategy, is explicitly permitted in a new, separate module.

**Functional Requirements:**

#### FR-14: Dummy paper-trading strategy
The system provides a `TradingNode`-based, paper-mode strategy that consumes all signals/indicators produced by §4.3, serving as a live integration proof of the full signal set.

**Consequences (testable):**
- The strategy runs against live dYdX market data via `TradingNode` in paper mode.
- Adding a new indicator to §4.3 makes it available to this strategy without a rewrite of the strategy's data plumbing (per FR-10).

#### FR-15: Trading-mode isolation
Live paper-trading strategy execution lives in a module separate from `dydx_collector`/`ml_signals`'s data path — the one sanctioned use of `TradingNode`/`Strategy` in `troll/`, per amended AD-8.

**Consequences (testable):**
- Enabling real-money (non-paper) execution requires an explicit, separate configuration step — it is not reachable by any default or accidental config state in v1.

**Notes:** Real-money execution is expected eventually, as a mode/config change to this same strategy — not new capability. No additional safeguards beyond FR-15's config-gated isolation are defined for v1.

### 4.6 Bot Monitoring TUI
**Description:** A keyboard-only terminal UI (urwid) for fast SSH-based monitoring of running bots and coin rankings, without needing a browser; the web dashboard remains the tool for graphs and historical analysis. Realizes UJ-4.

**Functional Requirements:**

#### FR-17: Keyboard-only TUI over the shared live feed
The system provides a terminal UI (urwid-based) that subscribes to the same live Redis pub/sub feed the web dashboard reads from, navigable entirely by keyboard.

**Consequences (testable):**
- No separate data pipeline/feed is created for the TUI; it reads the identical live stream as the dashboard.
- Every navigation action has a keybinding; no feature requires mouse input.

#### FR-18: Bots pane
The TUI shows a list of running bots with PnL and other per-bot metrics, updated live.

**Consequences (testable):**
- The bot list refreshes as PnL/metrics change; no manual refresh action is needed.

#### FR-19: Coin list pane mirroring the dashboard
The TUI shows the Coin Ranking/Watchlist (§4.2, including FR-16's Ranking Mode) as a live mirror of what the web dashboard shows.

**Consequences (testable):**
- The TUI's coin order matches the web dashboard's coin order at all times, because both read the same ranking engine (§4.2) — never two independently-computed lists.

#### FR-20: Coin-detail view
Selecting a coin shows its live-calculated indicators (OFI/OBI/microprice/spread, per §4.3) and the full order-book depth (20 levels per side, not just top-of-book).

**Consequences (testable):**
- Depth is never truncated to top-of-book only in this view.
- Indicator values shown are computed via the same §4.3 code path used everywhere else — no TUI-local reimplementation.

#### FR-21: k9s-style navigation model
Navigation adopts k9s's interaction pattern: a `:`-command bar to jump between views, `esc` to pop back a level, `/` to fuzzy-filter the coin list, and a breadcrumb header showing current location.

**Consequences (testable):**
- Every view is reachable via the `:` command bar.
- `esc` always returns to the immediately previous view; it never exits the program.

#### FR-22: Attention-only color coding
Color is used exclusively to draw the eye to what needs attention (e.g. a stale/dead feed, a large PnL swing); baseline/healthy state renders in a quiet/neutral color, never decoratively elsewhere. Text is uniform monospace size throughout — no enlarged/ASCII-art scaling.

**Consequences (testable):**
- A row in normal/healthy state and one in a genuinely flagged state are visually distinguishable by color alone.
- No UI element changes font size to indicate importance.
- The coin-list pane's ranking order (FR-19) is plain and uninflected by color — sort position alone conveys rank; color coding applies to bot/feed health signals, not to the ranking list.

#### FR-23: Bot start/stop controls
The user can start and stop a bot directly from the TUI, for both paper-mode (FR-14) and live-mode execution, gated by the same isolation/config-gate as FR-15.

**Consequences (testable):**
- Starting/stopping a bot from the TUI does not bypass FR-15's config-gated separation between paper and real-money execution.
- This control surface exists even though v1 trading itself remains paper-only per §5 — the control is built live-ready, not itself an enabling of real-money trading.

#### FR-24: Live-only, no in-TUI history for market data
The TUI shows only current/latest state for market data (coin ranking, order book, live indicators); it provides no historical replay or scrollback for this data. Historical/graph analysis of market data remains the web dashboard's responsibility.

**Consequences (testable):**
- No time-scrubbing or history view for market data exists in the TUI.

**Notes:** `[UPDATED 2026-07-24]` This restriction is scoped to market data specifically, not "any history anywhere in the TUI" — see FR-27, a distinct data domain (bot/trade performance) that the TUI does show history for.

#### FR-25: Deep-linked browser handoff (Should — next phase, not MVP-blocking)
A keybinding on a selected coin opens that coin's graph view in the web dashboard, deep-linked to the exact coin and the TUI's current time-window/zoom context.

**Consequences (testable):**
- The opened browser page reflects the same coin and time context the TUI was showing, not a generic dashboard landing page.

**Notes:** Should-tier per brainstorm convergence (2026-07-23) — implement once the Must-tier panes/navigation (FR-17–FR-24) are stable; does not block MVP sign-off (see §6.2).

#### FR-27 `[ADDED 2026-07-24]`: Bot-detail trade/PnL history
The Bot-detail view shows a trades blotter (individual fills) and a PnL-over-time chart (day/week/month/all preset toggle, no free-form scrubbing), sourced from a durable trade/position history that both the web dashboard and the TUI read — read-only, zero duplicate computation between the two surfaces. A keybinding on the open bot opens the dashboard's fuller trades/PnL view for the same bot, mirroring FR-25's coin deep-link.

**Consequences (testable):**
- The trades blotter and PnL chart are never computed independently by the TUI — both the TUI and the dashboard read the same underlying history, so the numbers always match.
- The PnL-over-time chart's time range is restricted to the day/week/month/all presets — no arbitrary date-range scrubbing.
- The dashboard deep-link opens the same bot's fuller trades/PnL view, not a generic landing page.

**Notes:** Surfaced during the 2026-07-24 UX design pass (see `_bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/EXPERIENCE.md`'s Data Sources & Staleness section) and backed by an architecture decision the same day (`ARCHITECTURE-SPINE.md`'s AD-10): sourced from Nautilus's own `Cache` (via `live_paper`'s `TradingNodeConfig` enabling `CacheConfig(database=DatabaseConfig(type="redis", ...))`), not a bespoke new store. Already story-planned (`epics.md` Epic 4, Stories 4.6–4.7). This FR was originally going to reuse number "26," but FR-26 had already been assigned and retired earlier the same day for an unrelated, explicitly-rejected feature (auto-surfacing coins via color) — FR-27 avoids that collision.

## 5. Non-Goals (Explicit)

- Not a multi-user product — no auth, accounts, or access control.
- Not a multi-exchange product in v1 — dYdX only.
- Not a mobile app.
- Not real-money live trading in v1 — the Dummy Strategy is paper-mode only (see FR-15). Real-money trading is expected to enter scope later, but as a config/mode change to the same strategy rather than new capability — not a v1 concern.
- Not a polished consumer UI — the dashboard exists to serve the builder's own research, not a general audience. Applies equally to the Bot Monitoring TUI.
- The TUI does not manage tmux/pane layout — that remains the user's own terminal setup, outside the product's concern.
- The TUI does not add secondary resource-management views beyond what §4.6 specifies (e.g. no k9s-style `:xray` view or resource-count badges) — explicit non-goal per brainstorm convergence.
- The TUI does not provide historical replay/scrollback for market data (FR-24) — that stays the web dashboard's job. (Bot/trade performance history is a distinct data domain and is in-scope per FR-27.)

## 6. MVP Scope

### 6.1 In Scope
- Data collection: configurable-interval Snapshots (default 0.5s) + opt-in per-coin Raw Delta Capture, fail-closed integrity gate, reconnect/gap resilience, USD-denominated liquidity.
- Coin Ranking & Watchlist, both as a research view and a live backtest-input feed, with user-selectable Ranking Mode (volume per FR-6, volatility per FR-16).
- Jupyter research environment with indicators reusable, unmodified, across research/backtest/live.
- Nautilus-native (`BacktestNode`) backtesting, dual timeframe (HFT + candlestick), multi-coin across the Watchlist.
- Live paper-trading Dummy Strategy wired to every signal produced.
- Bot Monitoring TUI (FR-17–FR-24, FR-27): urwid keyboard-only interface, bots pane, coin-list pane mirroring the dashboard's plain sorted ranking, coin-detail view with live indicators and full book depth, k9s-style navigation, attention-only color coding (bot/feed health only, not the ranking list), start/stop bot controls (paper-ready-for-live), bot-detail trade/PnL history (FR-27) sourced from a shared durable history read by both dashboard and TUI.

### 6.2 Out of Scope for MVP
- Real-money live trading — the natural next step after the Dummy Strategy proves out, achieved via a config/mode change rather than new capability; revisit once paper results are trusted.
- Alerts/notifications (e.g. push/Slack when a coin's ranking spikes).
- CLI polish / packaging beyond what's needed to run the pieces locally and in Docker.
- Deep-linked browser handoff from the TUI to the web dashboard (FR-25) — Should-tier, planned as the next increment after the TUI's Must-tier scope ships.

## 7. Success Metrics

**Primary**
- **SM-1**: An indicator built in Jupyter runs unmodified in a multi-coin backtest and in the Dummy Strategy, with no reimplementation. Validates FR-10, FR-14.
- **SM-2**: Known-liquid coins (BTC/ETH/SOL-class) are never dropped from the Watchlist due to a misclassification (e.g. the past `openInterest`-vs-`volume24H` bug). Validates FR-5, FR-6.

**Secondary**
- **SM-3**: The Dummy Strategy runs continuously in paper mode against live data without manual intervention for at least one week. Validates FR-14, FR-15.
- **SM-4**: The Bot Monitoring TUI's coin list and the web dashboard's coin list never diverge in ranking order at the same point in time, under either Ranking Mode. Validates FR-16, FR-19 (shared-ranking-engine constraint).
- **SM-5** `[ADDED 2026-07-24]`: The Bot Monitoring TUI's trades/PnL figures for a given bot and the web dashboard's figures for the same bot never disagree at the same point in time. Validates FR-27 (shared-history-source constraint).

**Counter-metrics (do not optimize)**
- **SM-C1**: Coverage (number of coins captured) is not optimized at the expense of data integrity — a rejected/flagged Snapshot is a success of the gate, not a failure to fix by relaxing validation. Counterbalances SM-2.

## 8. Open Questions

1. `[RESOLVED 2026-07-24]` Composite multi-indicator ranking rule — resolved via FR-16: ranking is user-selectable between volume (FR-6) and cross-sectional volatility (FR-16) Modes, not a single blended weighted score across all indicators.

## 9. Assumptions Index

None outstanding — FR-16's volatility lookback window default (1h, configurable) was confirmed and folded into the FR text directly.
