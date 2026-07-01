---
title: 'dYdX Signal Research & Trading Platform'
status: 'draft'
created: '2026-07-01'
updated: '2026-07-01'
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

## 3. Glossary

- **Snapshot** — A `DydxSecondSnapshot`-style capture of top-of-book levels (bid/ask prices+sizes, default 20 per side) and per-side trade volume, taken at a configurable interval (default 0.5s).
- **Raw Delta Capture** — Opt-in, per-coin capture of every order-book delta event as it occurs, with no fixed sampling interval, enabling resampling to any interval at read time.
- **Coin Ranking** — A continuously-refreshed ordering of subscribed coins by combined HFT indicators (OFI, OBI, microprice, spread) and slower/structural indicators (liquidity, volume).
- **Watchlist** — The live, queryable output of Coin Ranking; usable to select which coins a multi-coin backtest runs against.
- **Indicator / Signal** — A computed quantity (OFI, OBI, microprice, spread, or a new ML-derived signal) implemented once and consumed identically in Jupyter, backtest, and live contexts.
- **Gatekeeper** — The architecture paradigm binding `dydx_collector` as the sole writer and sole validator of market data (see architecture spine AD-1 through AD-7).
- **Dummy Strategy** — The v1 live paper-trading reference strategy that consumes every signal produced by the research side; not intended as a profitable strategy, but as an integration proof that the full loop (research → backtest → live) actually closes.

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
The system ranks all subscribed coins, refreshed continuously. For v1, the sort order is strictly by volume (USD, per FR-5's `volume24H` convention) — highest volume first. HFT indicators (OFI, OBI, microprice, spread) and slower/structural indicators (standard technical-analysis indicators, e.g. RSI and others, sourced from ta-lib/pandas-based Python libraries) are computed and displayed per coin alongside the rank, but do not currently affect sort order. `[NOTE FOR PM]` A composite ranking rule that combines all indicators into a single weighted score is a deliberately deferred research question — see §8.

**Consequences (testable):**
- Coins are ordered strictly by descending `volume24H` (USD); this ordering is independent of any HFT/TA indicator value.
- Ranking updates as new Snapshot data arrives, not on a static/one-time schedule.
- Ranking is inspectable historically (which coin ranked where, when) — realizes the research JTBD.

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

## 5. Non-Goals (Explicit)

- Not a multi-user product — no auth, accounts, or access control.
- Not a multi-exchange product in v1 — dYdX only.
- Not a mobile app.
- Not real-money live trading in v1 — the Dummy Strategy is paper-mode only (see FR-15). Real-money trading is expected to enter scope later, but as a config/mode change to the same strategy rather than new capability — not a v1 concern.
- Not a polished consumer UI — the dashboard exists to serve the builder's own research, not a general audience.

## 6. MVP Scope

### 6.1 In Scope
- Data collection: configurable-interval Snapshots (default 0.5s) + opt-in per-coin Raw Delta Capture, fail-closed integrity gate, reconnect/gap resilience, USD-denominated liquidity.
- Coin Ranking & Watchlist, both as a research view and a live backtest-input feed.
- Jupyter research environment with indicators reusable, unmodified, across research/backtest/live.
- Nautilus-native (`BacktestNode`) backtesting, dual timeframe (HFT + candlestick), multi-coin across the Watchlist.
- Live paper-trading Dummy Strategy wired to every signal produced.

### 6.2 Out of Scope for MVP
- Real-money live trading — the natural next step after the Dummy Strategy proves out, achieved via a config/mode change rather than new capability; revisit once paper results are trusted.
- Alerts/notifications (e.g. push/Slack when a coin's ranking spikes).
- CLI polish / packaging beyond what's needed to run the pieces locally and in Docker.

## 7. Success Metrics

**Primary**
- **SM-1**: An indicator built in Jupyter runs unmodified in a multi-coin backtest and in the Dummy Strategy, with no reimplementation. Validates FR-10, FR-14.
- **SM-2**: Known-liquid coins (BTC/ETH/SOL-class) are never dropped from the Watchlist due to a misclassification (e.g. the past `openInterest`-vs-`volume24H` bug). Validates FR-5, FR-6.

**Secondary**
- **SM-3**: The Dummy Strategy runs continuously in paper mode against live data without manual intervention for at least one week. Validates FR-14, FR-15.

**Counter-metrics (do not optimize)**
- **SM-C1**: Coverage (number of coins captured) is not optimized at the expense of data integrity — a rejected/flagged Snapshot is a success of the gate, not a failure to fix by relaxing validation. Counterbalances SM-2.

## 8. Open Questions

1. Composite multi-indicator ranking rule — FR-6 sorts by volume alone for v1; whether/how to combine HFT and TA indicators into a single weighted rank is deferred to future research.

## 9. Assumptions Index

None outstanding — both discovery-time assumptions (FR-9's Jupyter convention, FR-13's dynamic-Watchlist scope) were confirmed and folded into the FR text directly.
