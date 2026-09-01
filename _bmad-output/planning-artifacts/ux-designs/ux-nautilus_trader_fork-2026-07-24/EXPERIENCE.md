---
name: Bot Monitoring TUI
status: final
sources:
  - {planning_artifacts}/prds/prd-nautilus_trader_fork-2026-07-01/prd.md
  - {planning_artifacts}/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
  - {planning_artifacts}/ux-designs/ux-nautilus_trader_fork-2026-07-24/.memlog.md
updated: '2026-07-24'
---

# Bot Monitoring TUI — Experience Spine

> Solo builder tool. Keyboard-only urwid terminal UI over SSH, mirroring a web dashboard's live Redis feeds and adding bot control. Paired with `DESIGN.md` (Bot Monitoring TUI visual identity). Realizes PRD UJ-4 and FR-17–FR-25.

## Foundation

Single-surface terminal UI, launched interactively over SSH (`docker compose exec` into a running container, or run on the host against the same Redis instance) — **not a daemon, not a `restart: always` service**. One named user (the builder), no auth, no multi-user concerns. Built on **urwid**, Python's asyncio-native TUI toolkit, running its own asyncio event loop alongside `redis.asyncio` pub/sub subscriptions. `DESIGN.md` is the visual identity reference; this spine is the experience.

The TUI is a **pure reader/client** in this product's architecture, never a second computation surface:

- It reads `rankings:live` and `bots:status` as a subscriber only — it never recomputes Coin Ranking, volatility, or bot PnL locally (architecture AD-9, AD-10).
- It imports `ml_signals.indicators` (the `Microprice`, `MultiLevelOFI`, `MultiLevelOBI` classes) directly for Coin-detail's live indicators, rather than reimplementing them — the same pattern `dashboard.py` already uses (architecture §"Module dependencies").
- Its only path to `live_paper`'s trading runtime is the `bots:control` Redis channel; it never imports `live_paper` internals (architecture AD-10).

The TUI's session lasts exactly as long as the SSH session: the builder opens the TUI, monitors/acts, and quits with `:q`. There is no persistent state to restore between sessions beyond whatever Redis and the underlying data stores already hold.

## Information Architecture

| Surface | Reached from | Purpose | Mock |
|---|---|---|---|
| Coins pane | App open (default) / `:coins` | Live-ranked coin list, mirrors web dashboard exactly (FR-19) | [mockups/key-coins-pane.html](mockups/key-coins-pane.html) (also shows the command-bar overlay state) |
| Bots pane | `:bots` | Live list of running bots with PnL/status (FR-18) | [mockups/key-bots-pane.html](mockups/key-bots-pane.html) |
| Coin-detail | Enter on a Coins-pane row | Full-screen: live indicators (OFI/OBI/microprice/spread) + order-book depth ladder, collapsed to top-of-book by default, `d` expands to 20 levels/side (FR-20) | [mockups/key-coin-detail.html](mockups/key-coin-detail.html) (collapsed default, expanded states) |
| Bot-detail | Enter on a Bots-pane row | Full-screen, 3 regions: live snapshot header, trades blotter, PnL-over-time chart | [mockups/key-bot-detail.html](mockups/key-bot-detail.html) (Flow 2's hero screen) |
| Command-bar overlay | `:` from anywhere | Modal-style single-line input for navigation/quit commands; not a separate screen | see Coins-pane mock, second state |

Every pane and detail view is reachable through the `:` command bar (FR-21's testable consequence: "every view is reachable via the `:` command bar"). The two detail views are **full-screen replaces** of whichever pane launched them (k9s's own drill-down model) — not split-panes, not modals. `esc` pops back exactly one level and never exits the program; only `:q` quits. This closes the IA loop stated in FR-17–FR-21: Coins pane and Bots pane are the two top-level panes, each with one drill-down, and the command bar is the sole means of jumping between any of the above without stepping back through intermediate views first.

→ Four key-screen mocks exist under `mockups/` (see the table above), covering all five IA surfaces (Command-bar overlay is shown as a second state within the Coins-pane mock rather than its own file, since it's never a standalone screen). Spine tables remain the contract; mocks illustrate.

## Voice and Tone

Terse builder-tool copy, not consumer microcopy. This product has no onboarding and no marketing voice — every string is either a data value, a state label, or a keybinding hint.

| Do | Don't |
|---|---|
| `~ STALE` | `⚠️ We haven't heard from this bot in a while!` |
| `no bids` / `no asks` (thin book, one side empty) | `Order book depth unavailable` |
| `:q to quit` | `Press Ctrl+C or type :quit to exit the application` |
| `PnL +142.30` | `Great job! You're up $142.30 today!` |
| Numbers and labels, no punctuation flourish | Exclamation marks, emoji, encouragement copy |

## Component Patterns

Behavioral. Visual specs live in `DESIGN.md.Components`.

| Component | Use | Behavioral rules |
|---|---|---|
| Coins-pane list row | Coins pane | One row per ranked coin, sorted by the active Ranking Mode (volume or volatility) as published on `rankings:live`. Enter drills into Coin-detail for the highlighted row. `/` opens fuzzy-filter on this list (see below). Row order never re-sorts locally — it renders exactly the order `rankings:live` provides. |
| Bots-pane list row | Bots pane | One row per bot from `bots:status`: bot_id, PnL, strategy/symbol, mode (paper/live), position/exposure, uptime/last-heartbeat, win-rate-to-date. Enter drills into Bot-detail. Start/stop keybinding acts on the highlighted row directly, no drill-in required. |
| Fuzzy-filter (`/`) | Coins pane only | Opens an inline filter input on the currently-focused list pane (per FR-21, scoped to the coin list). Typing narrows the visible rows by fuzzy substring match against instrument ID. `esc` clears the filter and restores the full list without leaving the pane. Empty-match state renders `no matches` in place of rows — the list is not hidden, just empty. |
| Command bar | Global (`:`) | Single-line input at the bottom row. Accepts the command vocabulary (see Interaction Primitives). Unrecognized command: echoes `unknown command: {input}` and stays open for correction; does not silently no-op. `Enter` executes and closes the bar; `esc` cancels and closes it without acting. |
| Coin-detail view | From Coins pane | Full-screen replace. Shows live indicators (Microprice, spread, OFI, OBI — computed via `ml_signals.indicators`, never re-derived locally) plus the order-book depth ladder. The ladder opens **collapsed to top-of-book only** (best bid/ask, one row) every time the view is entered — it never remembers an expanded state from a prior visit. `d` toggles it open to the full depth ladder, up to 20 levels/side, and toggles it back closed; this is a pure display toggle scoped to the ladder region only — it does not affect the breadcrumb, the pane underneath, or what `esc` does. `o` deep-links to the web dashboard's graph view for this exact coin (FR-25, Should-tier — see State Patterns and Key Flows). `esc` returns to Coins pane, preserving the pane's scroll position and any active filter (regardless of whether the ladder was collapsed or expanded at the time). |
| Bot-detail view | From Bots pane | Full-screen replace, three bordered regions stacked top-to-bottom: (1) live snapshot header — PnL, position/exposure, mode, uptime/last-heartbeat, win-rate-to-date, strategy/symbol, all from `bots:status`; (2) trades blotter — scrollable list of individual fills (timestamp, side, price, qty, realized PnL), sourced from `live_paper`'s Nautilus `Cache` history via its own read surface (see Data Sources & Staleness); (3) PnL-over-time chart — sparkline/bar rendering of the same Cache history, time range cycled via a preset toggle (day/week/month/all), never free-form scrubbing. `o` deep-links to the web dashboard for the fuller trades/PnL view (mirrors the coin deep-link pattern). Start/stop keybinding acts on this bot without leaving the view. |
| Start/stop control | Bots pane row + Bot-detail | Publishes `{bot_id, action: "start"|"stop"}` on `bots:control` — never a mode parameter (AD-10). Confirmation is a one-line footer echo (`sent: start bot-07`), not a modal dialog — this is a personal tool, not a destructive-action-guarded consumer surface. |

## State Patterns

| State | Surface | Treatment |
|---|---|---|
| Cold open | Coins pane | Renders as soon as the first `rankings:live` message arrives. Before that: `waiting for rankings:live…` in `{colors.attention-neutral}`, no skeleton rows (there's no layout to preview — row count is data-dependent). |
| Coin feed stale | Coins pane row | If no `rankings:live` heartbeat arrives within the configured timeout, the pane-level stale badge (`{components.stale-badge}`) appears — tied to the `ranking_engine` heartbeat specifically, independent of any single bot's state. Last-known ranking keeps rendering but is visibly marked stale, never silently frozen (architecture AD-9). |
| Bot feed stale | Bots-pane row | Same heartbeat-timeout discipline, but scoped **per bot** to that bot's own `bots:status` heartbeat (architecture AD-10) — a healthy bot next to a crashed one shows one stale row, not a pane-wide flag. See Data Sources & Staleness for why these two signals are never merged. |
| Book collapsed (default) | Coin-detail depth ladder | Every entry into Coin-detail opens the ladder collapsed to a single top-of-book row (best bid/ask). This is a UX-scoped clarification of FR-20 agreed during this design pass: FR-20's "never truncated to top-of-book only" guarantees that the *capability* to see full depth is always present and never removed — it does not mandate the ladder render fully expanded by default. `d` expands it; nothing about the coin, its indicators, or the rest of the screen changes when toggled. |
| Book expanded | Coin-detail depth ladder | `d` pressed once from collapsed. Shows up to 20 levels/side. `d` again re-collapses to top-of-book. State does not persist across a return to Coins pane and back — re-entering Coin-detail always starts collapsed. |
| Thin order book | Coin-detail depth ladder (expanded) | Fewer than 20 levels on one or both sides is a **normal state, not an error**: the ladder simply ends short on the thin side, with no padding rows, no placeholder glyphs, no error styling. A side with zero levels renders `no bids`/`no asks` (Voice and Tone table), never an error message. |
| Fuzzy-filter no matches | Coins pane, `/` active | Renders `no matches` where rows would be; the filter input stays open and editable. `esc` clears it. |
| Bot start/stop in flight | Bots pane / Bot-detail | No optimistic local state change — the row keeps showing its last `bots:status` value until `live_paper` itself publishes the new state. A footer echo confirms the command was sent, not that it succeeded. |
| Command bar unknown command | Command bar | `unknown command: {input}`, bar stays open. |
| Trade/PnL history unreachable | Bot-detail regions 2–3 | If `live_paper`'s Cache-history read surface (see Data Sources & Staleness) is unreachable — e.g. persistence not yet enabled, or the query times out — regions 2 and 3 each render `history unavailable` independently in `{colors.attention-neutral}` — region 1 (live snapshot) is unaffected, since it sources from `bots:status` directly and has no dependency on the history read path. |

## Interaction Primitives

**Keyboard-only, k9s-derived, adopted wholesale except header resource-count badges and `:xray` secondary views (both explicitly rejected — brainstorm MoSCoW Won't-tier).**

- `:` — open the command bar from anywhere.
- `:coins` — jump to Coins pane.
- `:bots` — jump to Bots pane.
- `:q` — quit the program. This is the **only** way to quit; `esc` never quits.
- `esc` — pop back exactly one level (detail view → its origin pane; open filter → cleared filter; open command bar → cancel). Never exits the program.
- `/` — open fuzzy-filter on the Coins pane (the only pane FR-21 specifies filtering for).
- Arrow keys / `j` `k` — move row focus up/down within the focused list (vim-style, consistent with k9s).
- `Enter` — drill into the highlighted row's detail view (Coins pane → Coin-detail; Bots pane → Bot-detail).
- `d` — toggle the Coin-detail order-book ladder between collapsed (top-of-book only, the default on every entry) and expanded (full depth, up to 20 levels/side). Scoped entirely to the ladder region — does not affect the breadcrumb, indicators, or `esc`'s meaning.
- `o` — deep-link to the web dashboard: from Coin-detail, opens that coin's graph view at the same coin + time-window/zoom context (FR-25); from Bot-detail, opens the dashboard's fuller trades/PnL view for the same bot (extends FR-25's pattern symmetrically — flagged as a PRD amendment candidate, see Data Sources & Staleness).
- `s` — toggle start/stop on the highlighted bot (Bots pane) or the open bot (Bot-detail). Publishes `bots:control`, never a mode parameter.
- `m` — toggle Ranking Mode (volume ↔ volatility) from the Coins pane, publishing `ranking:control`. [ASSUMPTION] the memlog confirms Coins-pane Ranking Mode switching is in scope but does not name a specific keybinding; `m` is chosen for mnemonic fit ("mode") and left available for revision without IA impact.
- `t` — cycle the Bot-detail PnL-chart time-range preset (day → week → month → all → day…). [ASSUMPTION] memlog specifies a preset toggle but not its keybinding; `t` ("time range") chosen for mnemonic fit.
- **Banned:** any mouse-dependent affordance (FR-17: every feature must be keyboard-reachable), free-form date/time scrubbing anywhere. See Inspiration & Anti-patterns below for the full rejected-feature list (`:xray` views, resource-count badges, granular bot controls) and why each was cut.

## Accessibility Floor

Behavioral. Visual contrast lives in `DESIGN.md`.

- **Color is never the sole carrier of a state signal.** Every attention-colored state also has a text or glyph marker: the stale badge is color **plus** a `~` glyph **plus** optional `STALE` text, never color alone; PnL sign is color **plus** the `+`/`-` sign already present in the numeric value itself; bid/ask sides in the depth ladder are color **plus** their fixed column position (bids always left, asks always right), so a color-blind reading of the ladder still resolves correctly from position alone. This directly mitigates the fact that terminal color rendering varies by emulator/theme and by the viewer's color vision. The product's own "respect terminal default, accent only for attention" posture (DESIGN.md's Colors section) follows from this: since the base palette is unknown at design time, no interaction can depend on a specific hue being distinguishable.
- **Keyboard-complete by construction.** FR-17 makes this a hard requirement, not an enhancement: every state-changing action (navigate, filter, drill in, start/stop, deep-link, quit) has a keybinding and none requires a mouse.
- **No reliance on terminal-specific color rendering.** The base-fg/base-bg inheritance (DESIGN.md) means the product makes no contrast guarantee beyond "whatever contrast the builder's own terminal theme already provides between its default fg/bg" — this is accepted as a scope boundary for a single-named-user personal tool, not silently ignored: if the builder's terminal has poor contrast, that is a terminal-configuration concern, not this product's to solve.
- **Screen-reader support is out of scope.** [ASSUMPTION] Nothing in the PRD, architecture, or memlog raises screen-reader/TTY-accessibility support for this product, and its target user is a single sighted builder monitoring visually. Noted explicitly rather than silently omitted, since every other reviewed EXPERIENCE.md example includes an Accessibility Floor section with screen-reader rules — this one intentionally narrows that scope to color-blind-safe signaling and full keyboard operability, which are the two accessibility concerns actually load-bearing for this product's real constraints.

## Data Sources & Staleness

This section exists because heartbeat/staleness discipline and one net-new architecture dependency are load-bearing enough to warrant a home outside the standard headers.

**Two independent staleness signals, never merged (memlog decision, architecture AD-9/AD-10):**

1. **Coins pane** — tied to the `ranking_engine`'s own heartbeat on `rankings:live`. One flag, pane-level, reflects whether the shared ranking computation itself is alive.
2. **Bots pane** — tied to each individual bot's own `live_paper` heartbeat on `bots:status`. One flag **per bot row** — a healthy bot sitting next to a crashed one must show exactly one stale row, not a pane-wide state.

These are deliberately never unified into a single "something is stale" indicator, because they diagnose different failures: a stale Coins pane means the ranking engine is down (affects every coin); a stale Bots-pane row means one specific bot's process is down (affects nothing else). Collapsing them would hide which system actually failed.

**Architecture dependency — trades/PnL history, via Nautilus's own Cache, not a bespoke new store.** Bot-detail's regions 2 (trades blotter) and 3 (PnL-over-time chart) need durable, queryable trade/position history. Re-checked against the `nautilus_trader` library during this UX pass: this is **not net-new infrastructure** — `Cache` already exposes the query surface (`cache.orders_closed()`, `cache.positions_closed()`, `cache.position_snapshots()`, per-instrument trade counts) and already supports a durable backing `database` (`CacheConfig(database=DatabaseConfig(type="redis", ...))`) — Redis, which this stack already runs. The real gap is narrower than "build a store":

- `troll/live_paper/node.py`'s `TradingNodeConfig` currently constructs no `cache=CacheConfig(...)` at all, so it defaults to in-memory-only — trade/position history is lost on restart today and reachable by nothing outside the process. Turning on `CacheConfig(database=DatabaseConfig(type="redis", ...))` is the missing piece, not a new persistence layer.
- `live_paper` stays the **sole writer** — its own `TradingNode`/`Cache` is already, structurally, the only thing that ever writes orders/positions/fills; nothing else needs to be prevented from writing them.
- The web dashboard and `bot_tui` still must **not** reach into that Redis-backed Cache's internal keys/msgpack encoding directly — doing so would cross AD-10's "never import/touch `live_paper` internals" boundary, since the Cache's on-wire encoding is Nautilus-internal, not a stable public contract. They need `live_paper` to expose trades/PnL history through its **own** read surface (built on top of `cache.orders_closed()`/`positions_closed()`), the same boundary discipline as the existing `bots:status`/`bots:control` channels — not a second, competing implementation of trade history.
- [ASSUMPTION] The exact shape of that read surface (a new Redis channel/request-response pattern, or something else) is still undecided — genuinely left to architecture/implementation — but the underlying persistence is Nautilus's own Cache database, confirmed to already exist as library capability, not something to build from scratch.

**PRD amendment flag:** the `o` deep-link from Bot-detail to the dashboard's fuller trades/PnL view extends FR-25's coin-deep-link pattern to bots, but FR-25 as written only covers coins. The memlog explicitly calls this out as worth a PRD amendment once this UX ships — noted here so it isn't lost between artifacts.

## Inspiration & Anti-patterns

- **Lifted from k9s, wholesale:** the `:` command bar, `esc` pop-back-never-quit, `/` fuzzy-filter, breadcrumb header, and the general "keyboard-only resource monitor" posture.
- **Explicitly rejected from k9s:** resource-count badges in the header (this product's scope doesn't need aggregate counts as a persistent chrome element) and `:xray`-style secondary drill-down views (would add a second navigation dimension this tool's two-pane, two-detail-view IA doesn't need).
- **Rejected — separate visual treatment for "good to trade" coins:** considered during brainstorming, resolved as unnecessary — sort order from the shared ranking engine already conveys this; no pinning, highlighting, or summary line beyond normal rank position.
- **Rejected — granular bot controls (kill / restart / close-position / pause-coin):** only start/stop exists, deliberately. This is the interaction-surface expression of the same "minimum surface area, maximum leverage" principle that governs color use — rare, deliberate actions over a menu of fine-grained ones.
- **Rejected — tmux/pane-layout management:** the user's own terminal setup is out of scope; this product does not try to manage screen real estate beyond its own single urwid surface.
- **Rejected — historical replay/scrollback for market data:** FR-24 keeps order-book/price history strictly dashboard-only. (Bot/trade performance history is a *different* data domain and is now in-scope per the reversed decision on Bot-detail's regions 2–3 — the memlog notes this narrows FR-24 rather than contradicts it, and flags a PRD split/amendment.)

## Key Flows

### Flow 1 — Nightly check-in (the builder, SSHed into the Hetzner box, after dinner) — realizes UJ-4 end to end

1. Builder SSHes into the box and launches the TUI (`docker compose exec` into the running container). Coins pane renders as soon as the first `rankings:live` message lands — no separate loading screen, since the pane itself doubles as its own loading state.
2. Breadcrumb reads `Coins`. The list is sorted by whichever Ranking Mode is currently active; builder hits `m` to flip from volume to volatility mode, confirming both dashboard and TUI would now show the same reordered list (shared ranking engine, FR-19).
3. A coin near the top catches the eye. Builder moves focus with `j`/`k`, hits `Enter`.
4. Coin-detail opens full-screen. Breadcrumb reads `Coins > BTC-USD`. Live indicators (microprice, spread, OFI, OBI) update continuously via the shared `ml_signals.indicators` code path; the order-book ladder opens collapsed to top-of-book, as it always does. Builder hits `d` to expand it — full 20 levels per side; this coin's book happens to be deep tonight.
5. Builder hits `o`. The web dashboard opens in the browser, already showing BTC-USD's graph at the same time-window/zoom the TUI was implicitly at — no manual re-navigation on the dashboard side (FR-25).
6. Builder returns to the terminal, hits `esc` — back to Coins pane, scroll position and Ranking Mode preserved.
7. **Climax:** Builder hits `:bots`. Breadcrumb reads `Bots`. One row shows a stale badge (`~`) — that bot's `live_paper` process hasn't sent a heartbeat in a while, while every other bot row (and the Coins pane, checked a moment earlier) is unaffected. The builder immediately knows *which specific thing* broke, not just that "something" is wrong — the whole point of keeping the two staleness signals independent.

Failure variant: if `rankings:live` itself goes stale (step 2), the Coins pane shows its own pane-level stale badge instead of a reordered list — last-known ranks stay visible but visibly marked, never silently frozen.

### Flow 2 — Bot post-mortem after a rough day (the builder, mid-morning, checking why a bot's PnL dropped overnight)

1. Builder opens the TUI, hits `:bots` directly from the command bar (skips Coins pane entirely — every view is one command away).
2. Bots pane lists running bots. One row shows a large negative PnL swing in `{colors.attention-negative}` — the builder's eye is drawn there specifically because everything else on the pane renders in the terminal's quiet default color.
3. Builder moves focus to that row, hits `Enter`.
4. Bot-detail opens full-screen, three bordered regions. Region 1 (live snapshot header) confirms current PnL, position/exposure, mode (paper), uptime, win-rate-to-date, strategy/symbol — all live from `bots:status`.
5. Builder cycles the PnL-chart time-range preset with `t`: day → week, looking for when the drop happened. The sparkline (region 3) redraws for the new window.
6. Builder scrolls the trades blotter (region 2) to the timestamp matching the sparkline's drop, reading the individual fills (timestamp, side, price, qty, realized PnL) that caused it.
7. **Climax:** Builder hits `o`. The web dashboard opens to this bot's fuller trades/PnL view for deeper analysis than the TUI's compact regions support — both surfaces read the same underlying `live_paper` Cache history through its own read surface, so the numbers match exactly, with zero risk of the TUI and dashboard telling two different stories about the same bad night.
8. Satisfied that it's understood (not a live bug, just a bad market move), builder hits `esc` back to Bots pane, then `s` to stop the bot for the day — publishing `{bot_id, action: "stop"}` only, with no mode parameter ever exposed on this path.

Failure variant: if the Cache-history read path is unreachable, regions 2–3 each independently render `history unavailable` while region 1's live snapshot keeps working — the builder still gets the current PnL number even if the deeper investigation has to wait.
