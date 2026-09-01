---
name: Bot Monitoring TUI
description: Keyboard-only urwid terminal UI for a solo builder monitoring dYdX bots and coin rankings over SSH — a personal instrument, not a consumer product.
status: final
updated: '2026-07-24'
colors:
  # Rationale: see Colors section below. Tokens below are semantic
  # ANSI-family accents layered on the inherited terminal base.
  base-fg: 'terminal-default'
  base-bg: 'terminal-default'
  attention-stale: 'ansi-yellow'
  attention-critical: 'ansi-red'
  attention-positive: 'ansi-green'
  attention-negative: 'ansi-red'
  attention-neutral: 'terminal-default'
typography:
  # Rationale: see Typography section below. Monospace only, size never
  # carries emphasis.
  body:
    note: 'Inherits the SSH terminal emulator's own monospace font and size. No in-app font/size control.'
  emphasis:
    note: 'Bold or reverse-video (urwid "standout") attribute, paired with an attention color — never bold alone, never a larger glyph.'
rounded:
  # Not applicable in the CSS-radius sense — a character grid has no
  # curves. Recast as urwid's box-drawing/line-drawing convention.
  DEFAULT: 'single-line box-drawing (urwid LineBox, ─│┌┐└┘)'
  none: 'no border (bare list rows, bare text regions)'
spacing:
  # Not a px scale — a character-cell rhythm. One unit = one row or one
  # column of the terminal grid.
  row: '1 line per list row, no blank line between rows'
  section-gap: '1 blank line between stacked regions within a full-screen view'
  column-gap: '2 spaces between columns in a row (bot list, order-book ladder)'
  margin: '1 column inset from the terminal edge for all panes'
components:
  breadcrumb-header:
    foreground: '{colors.base-fg}'
    background: '{colors.base-bg}'
    position: 'row 0, full width'
  command-bar:
    foreground: '{colors.base-fg}'
    background: '{colors.base-bg}'
    prompt-glyph: ':'
    position: 'bottom row, overlays footer on activation'
  footer-hint-bar:
    foreground: '{colors.attention-neutral}'
    position: 'bottom row, replaced by command-bar on activation'
  list-row-coin:
    foreground-default: '{colors.base-fg}'
    foreground-stale: '{colors.attention-stale}'
    stale-glyph: '~'
  list-row-bot:
    foreground-default: '{colors.base-fg}'
    foreground-pnl-positive: '{colors.attention-positive}'
    foreground-pnl-negative: '{colors.attention-negative}'
    foreground-stale: '{colors.attention-stale}'
    stale-glyph: '~'
  stale-badge:
    foreground: '{colors.attention-stale}'
    glyph: '~'
    text-suffix: 'STALE'
  depth-ladder-row:
    bid-foreground: '{colors.attention-positive}'
    ask-foreground: '{colors.attention-negative}'
    empty-row: 'omitted entirely, not padded or grayed'
    default-state: 'collapsed to a single top-of-book row; `d` expands to full depth (see EXPERIENCE.md State Patterns: Book collapsed/expanded)'
  pnl-sparkline:
    positive-glyph-foreground: '{colors.attention-positive}'
    negative-glyph-foreground: '{colors.attention-negative}'
    glyph-set: '▁▂▃▄▅▆▇█ (block elements, no color-only bars)'
  border-linebox:
    style: '{rounded.DEFAULT}'
    used-for: 'full-screen detail views (Coin-detail, Bot-detail) framing each of their regions'
---

## Brand & Style

The Bot Monitoring TUI is a personal instrument, not a product. Its posture is closer to `htop` or `k9s` than to any web dashboard: dense, quiet, keyboard-operated, and built for one person who already knows what every number means. There is no onboarding, no empty-state marketing copy, no visual flourish competing for attention. The screen's job is to sit quietly in its default state and speak only when something needs the builder's eye — a stale feed, a bot swinging hard on PnL.

This is the same "minimum surface area, maximum leverage" principle that shaped the product's interaction model, expressed visually: color is rationed, size never varies, and the terminal's own theme is treated as ground truth rather than something the app overrides. A builder who runs a purple-on-black Solarized theme and one who runs plain white-on-black should both get a TUI that looks native to their own terminal, with the same small set of accent colors doing the same job in both.

k9s is the explicit visual and interaction reference, adopted wholesale with two deliberate subtractions: no resource-count header badges, no `:xray` secondary views. Both were considered and rejected as surface area the product doesn't need.

## Colors

There is no fixed hex palette — the base is **whatever the terminal's own default foreground/background already is** (`{colors.base-fg}` / `{colors.base-bg}`). This is a portability requirement, not a stylistic choice: the TUI is launched over SSH into terminals the builder already has configured to their own taste, and re-theming on top of that would fight the environment rather than respect it.

On top of that base, four ANSI-family semantic accents exist, and they exist for exactly one purpose: drawing the eye to something that needs it.

- **`{colors.attention-stale}`** (yellow-family) — a feed or bot has missed its heartbeat window. Used on the Coins pane's own stale badge and each Bots-pane row's own stale badge, independently — never merged into a single indicator (see EXPERIENCE.md's Data Sources & Staleness section).
- **`{colors.attention-critical}`** (red-family) — reserved for genuinely critical states (e.g. a bot in a failed/errored state, as distinct from merely stale).
- **`{colors.attention-positive}`** / **`{colors.attention-negative}`** (green/red-family) — PnL sign, bid/ask side in the order-book depth ladder. This is the one place color carries semantic meaning beyond "look here" — sign, not magnitude ranking.
- **`{colors.attention-neutral}`** — functionally identical to the terminal default; used for footer hint text and anything that should visually recede.

**What color is never used for:** the Coins pane's ranking order itself (sort position alone conveys rank — a coin at the top of the list is not additionally colored to say "good"), decorative chrome, borders, or headers. Baseline/healthy rows render in the terminal's own default text color, full stop. If a design decision can't name which specific attention state a color communicates, it doesn't get a color.

## Typography

Monospace only — there is no font choice in this product, no size scale, and size is **never** used for emphasis anywhere in the UI. This is stated as a hard rule, not an oversight: FR-22 explicitly bans font-size-based importance signaling, and the product's whole visual discipline routes all emphasis through color (plus, secondarily, urwid's bold/standout text attribute as a reinforcing signal, never a substitute).

`{typography.body}` inherits the SSH client's own monospace font and point size — the app has no opinion on this and exposes no in-app control for it. `{typography.emphasis}` is bold or reverse-video, always paired with one of the four attention colors above, never applied on its own to convey meaning (a color-blind builder relying on bold-alone would lose the signal entirely — see EXPERIENCE.md's Accessibility Floor for how glyph markers cover this gap).

## Layout & Spacing

The unit of measure is the character cell, not a pixel scale. `{spacing.row}` is one line per list row with no blank-line padding between rows — density is a feature in a monitoring tool, not a bug. `{spacing.section-gap}` (one blank line) separates the three stacked regions of a Bot-detail view from each other. `{spacing.column-gap}` (two spaces) separates adjacent columns in tabular rows — the Bots pane's column layout, the order-book ladder's price/size columns. `{spacing.margin}` insets every pane one column from the terminal's own edge, so nothing collides with the emulator's own scrollbar or border chrome.

There is no responsive breakpoint system — the terminal is whatever size the builder's SSH session is. Panes reflow within urwid's own flow/box widget model; content that doesn't fit is scrolled (trades blotter, order-book depth), never truncated silently.

## Elevation & Depth

Elevation and shadow are not meaningful concepts on a character grid, and this product does not simulate them. There is no drop-shadow, no tonal-layering illusion of surfaces stacking on top of each other. The single depth cue available is urwid's `LineBox` border (`{components.border-linebox}`), used only to frame the full-screen detail views' regions — a bordered box reads as "a distinct region," nothing more. List panes and the command bar have no border at all; bordering everything would be visual noise in a tool whose entire ethos is restraint.

## Shapes

There is no border-radius concept in a terminal — "shape" here means line-drawing style. The product uses a single convention throughout: `{rounded.DEFAULT}`, single-line box-drawing characters (`─│┌┐└┘`) for the `LineBox` borders framing Coin-detail and Bot-detail's three regions. `{rounded.none}` (no border) is the default for list rows, the breadcrumb header, the footer hint bar, and the command bar — bordering is reserved for the moment a full-screen view needs to visually separate its internal regions from each other, not applied reflexively everywhere.

## Components

- **Breadcrumb header** (`{components.breadcrumb-header}`) — single row at the top of every screen, showing current location (e.g. `Coins` / `Coins > BTC-USD` / `Bots > bot-07`). Plain text, terminal-default color, no border. Never colored — it is orientation chrome, not a signal.
- **Command bar** (`{components.command-bar}`) — bottom-row overlay, activated by `:`, showing a `:` prompt and the in-progress command text. Replaces the footer hint bar for its duration. Terminal-default color; it is a mode indicator, not an attention signal.
- **Footer hint bar** (`{components.footer-hint-bar}`) — bottom row, default state, showing available keybindings for the current view in `{colors.attention-neutral}`. Recedes visually; present but quiet.
- **Coins-pane list row** (`{components.list-row-coin}`) — one row per ranked coin: rank position, instrument ID, the active Ranking Mode's score column (volume or volatility), no per-row color unless that specific coin's feed is stale, in which case the row (or its trailing stale badge) switches to `{colors.attention-stale}` plus the `~` glyph. Rank order itself carries zero color.
- **Bots-pane list row** (`{components.list-row-bot}`) — one row per running bot: bot ID, strategy/symbol, mode (paper/live), PnL (colored `{colors.attention-positive}`/`{colors.attention-negative}` by sign), position/exposure, uptime/last-heartbeat, win-rate-to-date. Independently carries its own stale badge tied to that bot's own heartbeat.
- **Stale-indicator badge** (`{components.stale-badge}`) — a small `~` glyph plus optional `STALE` text suffix, `{colors.attention-stale}`. Appears once per stale domain (Coins pane has one; each stale Bots-pane row has its own) — never a single global "something is stale" indicator.
- **PnL sparkline / bar-chart glyphs** (`{components.pnl-sparkline}`) — Unicode block-element glyphs (`▁▂▃▄▅▆▇█`) rendering the Bot-detail PnL-over-time region, colored by sign per bar/column. No axis chrome beyond the preset time-range label (day/week/month/all).
- **Order-book depth ladder row** (`{components.depth-ladder-row}`) — one row per price level, bid side and ask side each in their own attention color. Opens **collapsed to one top-of-book row by default** on every entry to Coin-detail; `d` toggles it open to up to 20 rows per side. When expanded on a thin book with fewer than 20 levels, the ladder simply ends short — no placeholder/padding rows, no error styling, because a thin book is a normal state, not a fault.
- **Full-screen border frame** (`{components.border-linebox}`) — `LineBox` used only in Coin-detail and Bot-detail, framing each of their internal regions (e.g. Bot-detail's snapshot header / trades list / PnL chart as three separately bordered regions within the one screen).

## Do's and Don'ts

| Do | Don't |
|---|---|
| Inherit the terminal's own default foreground/background as the base | Force a fixed dark (or any fixed) color theme |
| Use `{colors.attention-*}` only for genuine attention states (stale, critical, PnL sign) | Color the Coins pane's ranking order, or any healthy/baseline row |
| Let a thin order book (< 20 levels/side) end its ladder short | Pad a thin book with placeholder or zeroed rows, or flag it as an error |
| Show two independent stale badges (Coins pane, each Bots row) | Merge staleness into one unified "something's stale" indicator |
| Keep every emphasis signal color-first, glyph-reinforced | Use bold/size alone to signal importance |
| One monospace size throughout, inherited from the terminal | Any enlarged text, ASCII-art banners, or in-app font-size control |
