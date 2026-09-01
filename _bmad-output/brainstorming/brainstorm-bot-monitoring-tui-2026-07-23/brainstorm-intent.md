# Brainstorm Intent: Bot Monitoring TUI

A keyboard-only terminal UI for monitoring running trading bots — performance, PnL, dashboard metrics, and coin rankings — enabling fast SSH/tmux-based monitoring without opening a browser. The existing web dashboard is retained for graph-heavy, historical analysis on select coins; the TUI is a live-only, low-friction companion for at-a-glance status and light control.

## Decisions

- Framework: urwid (asyncio-native curses wrapper), used broadly wherever it fits
- Navigation: keyboard-only, no mouse
- Data source: subscribes to the same Redis pub/sub channel the web dashboard already reads from — no separate feed
- tmux is the user's own pane-layout tool (multiple TUI instances side by side) — not a feature the TUI itself manages
- Panes: bots-list pane (PnL + per-bot metrics); coin-list pane mirroring the web dashboard's coin rankings
- Coin-detail view: live-calculated metrics (OFI/OBI/microprice/spread etc.) shown inline for the selected coin
- Coin-detail view: full 20 order-book levels rendered, not just top-of-book
- UX model adapted wholesale from k9s: `:` command bar to jump views, `esc` to pop back, `/` fuzzy-filter on coin list, breadcrumb header, color-coded PnL/stale rows
- Visual style: uniform monospace text everywhere; no ASCII-art scaling — all emphasis/de-emphasis via color, never size
- Color discipline: highlight only what needs attention (stale feed, big PnL swing); healthy/normal state stays quiet/gray so the eye jumps to problems, not decorative color elsewhere
- Scope: includes start/stop bot controls, built ready for both paper and live trading (paper is close to going live) — this supersedes an earlier read-only-only v1 call
- No historical replay/scrollback in the TUI — strictly live/latest-state; historical viewing stays on the website
- TUI auto-surfaces coins that are currently good-to-trade rather than requiring the user to scan manually
- Coin-ranking / good-to-trade logic is a single shared engine, used identically by both the web dashboard and the TUI

## Scope (MoSCoW)

**Must**
- urwid keyboard-only TUI over the shared Redis feed
- Bots-list pane (PnL + per-bot metrics) and coin-list pane (mirrors web dashboard rankings)
- Coin-detail view with inline live indicators (OFI/OBI/microprice/spread) and full 20 book levels
- k9s-style navigation: `:` command bar, `esc` back, `/` fuzzy filter, breadcrumb header, color-coded rows
- Color discipline: attention-only highlighting, quiet/gray baseline
- Start/stop bot controls, paper-ready-for-live
- Shared ranking/good-to-trade engine as the single source of truth for both web dashboard and TUI
- Auto-surfacing of good-to-trade coins

**Should**
- Deep-linked browser-handoff keybinding: opens the selected coin's graph view in the web dashboard at the exact coin + time-window/zoom

**Could**
- (none identified in the session)

**Won't**
- Historical replay/scrollback in the TUI (stays web-only)
- k9s-style resource-count badges in the header
- `:xray`-style secondary views

## Key constraint

The coin-ranking / "good-to-trade" engine must be one shared implementation consumed identically by both the web dashboard and the TUI — never two independently-implemented ranking lists that can drift apart. This is architecturally load-bearing: it's what makes the TUI's coin-list a true "mirror" of the dashboard over time, and what makes the deep-linked browser handoff meaningful (same ranking context on both ends).
