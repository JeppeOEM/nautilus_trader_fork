---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories, step-04-final-validation]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
---

# nautilus_trader_fork - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for nautilus_trader_fork (the `troll/` dYdX Signal Research & Trading Platform), decomposing the requirements from the PRD and Architecture spine into implementable stories. No UX design contract exists for this project (backend/research pipeline, not a UX-driven product).

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

### UX Design Requirements

None — no UX design contract exists for this project. The dashboard exists to serve the builder's own research, not a general audience (PRD §5 Non-Goals), so no UX-DRs apply.

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

## Epic List

### Epic 1: Trustworthy Coin Ranking & Watchlist
Builder opens a ranking/watchlist view showing which coins are worth watching right now, backed by continuously-captured, integrity-gated market data, with opt-in deep (raw-delta) capture for coins worth studying further. FR1–FR5 largely restate already-adopted architecture (Gatekeeper gate is built) — stories here verify/close gaps rather than rebuild. FR6–FR8 are the newer surface to confirm/build against the existing dashboard.
**FRs covered:** FR1, FR2, FR3, FR4, FR5, FR6, FR7, FR8

### Epic 2: Reusable Signal Research & Multi-Coin Backtesting
Builder writes an indicator once in Jupyter (Nautilus notebook conventions) and runs it unmodified in a multi-coin, dual-timeframe `BacktestNode` run across the current Watchlist. Standalone: uses Epic 1's Watchlist as an input, but delivers complete research→backtest value on its own.
**FRs covered:** FR9, FR10, FR11, FR12, FR13

### Epic 3: Live Paper-Trading Integration Proof
Builder starts the Dummy Strategy and watches it place paper orders driven by every signal validated in backtest — closing the full research→backtest→live loop, in a new module structurally isolated from the data-collection path per the amended AD-8. Standalone: consumes Epic 2's signals but is the first and only sanctioned `TradingNode`/`Strategy` usage, delivered end to end including the trading-mode isolation safeguard.
**FRs covered:** FR14, FR15

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
