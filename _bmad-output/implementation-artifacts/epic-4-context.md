# Epic 4 Context: Bot Monitoring TUI

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Builder SSHes into the box, opens a keyboard-only urwid terminal UI, and at a glance sees per-bot PnL/health and which coins are hot right now — drills into a coin's live indicators/book or a bot's trade history with a keypress, hands off to the web dashboard for deeper graphs, and starts/stops a bot without leaving the terminal. It is standalone: it reads Epic 1's ranking engine and Epic 2's shared indicators as pure inputs, and talks to Epic 3's `live_paper` only through a control/status channel, but delivers complete monitoring/control value on its own. This is the project's first UX-driven surface, backed by a finalized design contract.

## Stories

- Story 4.1: Scaffold the TUI shell and live Coins pane
- Story 4.2: Coins pane interaction and attention-only visual polish
- Story 4.3: Coin-detail drill-down with collapsible order-book depth
- Story 4.4: Bots pane with start/stop control
- Story 4.5: Bot-detail live snapshot view
- Story 4.6: Durable bot trade/position history via Nautilus Cache
- Story 4.7: Bot-detail trades blotter and PnL-over-time chart

## Requirements & Constraints

- The TUI is keyboard-only (no mouse-dependent affordance anywhere) and reuses the live Redis pub/sub feed the web dashboard already reads — no separate data pipeline, no local recomputation of rank, volatility, indicators, or bot PnL.
- Bots pane shows one row per bot (bot_id, sign-colored PnL, strategy/symbol, mode, position/exposure, uptime/last-heartbeat, win-rate-to-date), updating live with no manual refresh.
- Coins pane mirrors the web dashboard's ranking exactly (including the volume/volatility mode toggle) so the two surfaces never diverge in order.
- Coin-detail shows live indicators (OFI/OBI/microprice/spread) via the shared indicator code path, plus full order-book depth (up to 20 levels/side) — never a permanent truncation, though it may open collapsed by default as a progressive-disclosure choice.
- Navigation follows a k9s-style model: a `:` command bar with a defined vocabulary and an explicit "unknown command" echo (never a silent no-op), `esc` pops back exactly one level and never exits, `/` fuzzy-filters the coin list only, and a breadcrumb header always shows current location.
- Color is attention-only: it draws the eye to stale/dead feeds or large PnL swings; healthy/baseline state and the coin list's ranking order itself stay uncolored; text is uniform monospace, size is never used for emphasis.
- Bot start/stop is controllable from the TUI for both paper and live-mode bots, gated by the same isolation/config-gate as the live-trading module; the control channel never carries a mode parameter, so it can never be used to bypass that gate.
- The TUI shows only current/latest market-data state — no time-scrubbing or historical replay of market data (that stays the web dashboard's job). This restriction is scoped to market data only: bot/trade performance history is a distinct data domain and is in-scope for Bot-detail.
- A keybinding on a selected coin, and a symmetric one on a selected bot, deep-links to the web dashboard's fuller view for that same coin/bot, at matching time context where applicable. The coin deep-link is Should-tier and does not block MVP sign-off.
- Bot-detail's trades blotter and PnL-over-time chart (day/week/month/all preset toggle, never free-form scrubbing) are read-only and sourced from the same durable trade/position history the web dashboard reads — zero duplicate computation between the two surfaces.
- Every color-carried state must also have a non-color marker (glyph, text suffix, or fixed column position) so a color-blind reading still resolves correctly.
- Voice/tone is terse, data/state/keybinding-only — no marketing copy, emoji, or exclamation marks.

## Technical Decisions

- New `bot_tui` module: a pure reader/client, not a daemon — launched interactively (`docker compose exec` or on-host), never `restart: always`. It uses `nautilus_trader` as a library only, never instantiating `TradingNode`/`Strategy`/`DataEngine`.
- Module boundary: `bot_tui` may import `ml_signals.indicators` (pure Indicator classes) and shared data types directly, but never any module's stateful internals; it never imports `live_paper` internals — its only path to the trading runtime is `live_paper`'s Redis control/status channels.
- Two independent staleness signals, never merged: a Coins-pane-level badge tied to the ranking engine's own heartbeat, and a separate per-bot-row badge tied to that specific bot's `live_paper` heartbeat. A missed heartbeat within a configurable timeout means "stale," never "still current" — applies identically to ranking, bot status, and trade-history data.
- Redis wire contracts `bot_tui` consumes: `rankings:live` (ranking engine → readers; JSON `{mode, updated_at, ranks: [{instrument_id, rank, volume24h, volatility_score}]}`, published on change + heartbeat) and `ranking:control` (readers → ranking engine; mode-switch requests, last-write-wins) for coin ranking; `bots:status` (live_paper → readers; per-bot PnL/status JSON, published on change + heartbeat) and `bots:control` (readers → live_paper; `{bot_id, action: "start"|"stop"}` only, no mode field) for bot state/control.
- Trade/PnL history read surface: `live_paper` enables Nautilus's own `CacheConfig(database=DatabaseConfig(type="redis", ...))` (not a bespoke store) and computes four Redis keys per bot — `bots:history:{bot_id}:day|week|month|all` — each `{bot_id, range, updated_at, trades: [...], pnl_series: [...]}`, refreshed on a timer (~30-60s) and on each fill. `bot_tui`/dashboard are pure GET clients of these keys, never touching the Cache's internal Redis encoding directly. Timestamps are UNIX nanoseconds; `trades[].realized_pnl` and `pnl_series[].pnl` are each per-item/per-bucket values, not cumulative; windows are rolling from now (not calendar-aligned); `trades` capped at 500 most-recent fills, `all`'s `pnl_series` is daily-bucketed; the four sibling keys refresh independently (no cross-key atomicity guarantee, acceptable skew).
- `bot_id` must be distinct between a bot's paper and live-mode configs — a cloned config that keeps the same `bot_id` would silently merge that bot's status/control/history across paper and real-money trading. No automated check exists; this is an operator discipline requirement worth surfacing in any bot-config-touching story.
- Deployment: `bot_tui` has no fixed `docker-compose.yml` service definition yet (conceptual shape only — interactive/exec'd process, not a compose service) — implementation is free to define the concrete invocation.
- Built on `urwid` (new pinned dependency, asyncio-native), running its own asyncio loop alongside `redis.asyncio` pub/sub subscriptions.

## UX & Interaction Patterns

- Color has no fixed hex palette — the terminal's own inherited default fg/bg is the base; four semantic accents exist solely for attention states: stale (yellow), critical (red), PnL/bid-ask sign (green/negative-red), neutral (recedes visually). Bold/reverse-video is a secondary reinforcing signal only, never a substitute for color.
- Keybindings: `:` opens the command bar (`:coins`, `:bots`, `:q`); `esc` pops back one level; `/` fuzzy-filters the Coins pane only; `j`/`k`/arrows move focus; `Enter` drills into a highlighted row's detail view; `d` toggles the Coin-detail order-book ladder collapsed/expanded (always opens collapsed to top-of-book on entry, never remembers prior state); `o` deep-links to the dashboard (coin graph from Coin-detail, fuller trades/PnL view from Bot-detail); `s` toggles start/stop on the highlighted/open bot with a footer-echo confirmation and no optimistic local state change; `m` toggles Coins-pane Ranking Mode; `t` cycles the Bot-detail PnL time-range preset (day→week→month→all→day…).
- Full-screen detail views (Coin-detail, Bot-detail) are replaces of the pane that launched them, not split-panes or modals; Bot-detail stacks three bordered regions (live snapshot header; trades blotter; PnL sparkline).
- Empty/edge states: cold open before first `rankings:live` message shows `waiting for rankings:live…` (no skeleton rows); a thin order book simply ends short with `no bids`/`no asks` text, never padding or error styling; fuzzy-filter with no matches renders `no matches` in place of rows; an unrecognized command bar entry echoes `unknown command: {input}` and stays open; if the trade/PnL history read surface is unreachable, Bot-detail's blotter and chart regions each independently render `history unavailable` while the live snapshot header (sourced from `bots:status` directly) is unaffected.

## Cross-Story Dependencies

- Story 4.1 (shell + Coins pane) is the foundation every other story in this epic builds on.
- Story 4.3 (Coin-detail) and Story 4.4/4.5 (Bots pane / Bot-detail) both extend the navigation shell from 4.1.
- Story 4.7 (trades blotter + PnL chart) depends on Story 4.6 (Cache-backed history read surface) and Story 4.5 (Bot-detail view it renders inside).
- This epic depends on Epic 1's `ranking_engine` (`rankings:live`/`ranking:control`) for both Coins-pane stories, Epic 2's shared `ml_signals.indicators` for Coin-detail, and Epic 3's `live_paper` for the Bots pane, Bot-detail, and start/stop control — all consumed only through their published Redis contracts, never their internals.
