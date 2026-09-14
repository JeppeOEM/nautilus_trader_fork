---
title: 'Chart Frontend Rewrite'
status: 'final'
created: '2026-09-13'
updated: '2026-09-13'
---

# PRD: Chart Frontend Rewrite

## 0. Document Purpose

This PRD scopes the epic that replaces `troll/ml_signals/dashboard.py` — a single aiohttp process that currently renders HTML, serves ~25 JSON routes, and runs a live Redis-subscriber loop — with a React/TypeScript SPA served by an expanded `troll/data_api`. It builds directly on two already-finalized inputs rather than re-deriving them: the user's original request (quoted verbatim where load-bearing) and the **architecture spine** at `_bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md` (status: final), which already fixes the technical shape — Read-Only Facade paradigm, cursor-paginated history, native multi-pane chart sync, a WS live relay, and seven Architecture Decisions (AD-F1–AD-F7). This PRD does not re-litigate that shape; it states the capability-level requirements it must satisfy, plus one significant requirement the architecture pass never touched: the page's visual identity. It feeds directly into a `bmad-create-epics-and-stories` pass, which will append this epic (Epic 15, continuing the existing `epics.md`'s global sequence past epic-14) rather than starting a fresh backlog document.

## 1. Vision

The `troll/` dashboard is the one surface its owner actually looks at every day to read live dYdX market data, rankings, and per-coin technical structure. Today it's server-rendered HTML with hand-rolled JavaScript, growing a new inline `<script>` block or Plotly subplot every time a feature is added — the exact shape that produced this epic's own trigger: a chart page whose "last 120 bars" default zoom silently resets itself the moment an indicator overlay repaints (story 14.3), because nothing in that codebase owns "what range is the user currently looking at" as a single source of truth.

This rewrite replaces that surface with a React SPA that behaves the way a serious charting tool should: history loads incrementally as you scroll back — exactly like TradingView — never as one enormous up-front fetch; every chart pane on a coin's page (candlesticks, order-book imbalance, OFI, volume) moves in lockstep because one library-native mechanism owns the sync, not ad-hoc event wiring; and the whole thing is fast enough that navigating between coins and scrolling through months of history feels immediate, even over the SSH tunnel this tool is normally viewed through. It is not a redesign in the branding sense — it keeps doing exactly what the dashboard does today (rankings, per-coin charts, history, docs) — but it does it on a foundation built to stay fast and correct as more indicators and coins get added, and it looks the part: a monospace, ANSI-art terminal, the way a tool for reading raw market data should.

## 2. Target User

### 2.1 Jobs To Be Done

- As the sole operator of this dYdX research/trading stack, I open this dashboard to check live coin rankings and decide what to look at next.
- I drill into a specific coin's chart to read its recent price action and technical structure (indicators, order-book imbalance) before making a research or trading decision.
- I scroll a chart back through history — sometimes hours, sometimes months — to understand how a coin has behaved, without waiting on a multi-megabyte load each time.
- I check a coin's 31-day metrics history (volume, volatility) when deciding whether it's worth opt-in raw-delta capture.
- I do all of the above from a phone or tablet occasionally, not only from a desktop browser at my desk.

### 2.2 Non-Users (v1)

Explicitly nobody but the operator — this is a single-user personal tool with no auth system (see Non-Goals). No team/multi-viewer use case exists to design against.

### 2.3 Key User Journeys

*Internal, single-operator tool — journeys are restated JTBDs rather than named-persona narratives (per the scope dial's "Lighter" treatment).*

- **UJ-1.** The operator opens the rankings page, sees the live-sorted coin list, and clicks into a coin whose ranking just jumped.
- **UJ-2.** The operator, already on a coin's chart page, drags the candlestick chart back three weeks to see how it traded around a prior volatility spike — the OFI and order-book-imbalance panes beneath it scroll back in the same motion, and older bars keep loading in as the drag continues.
- **UJ-3.** The operator checks the dashboard from their phone during a break, glances at the rankings table, and it's legible without pinch-zooming.

## 3. Glossary

*Terms already established by the architecture spine and the wider `troll/` codebase — used here verbatim, not redefined.*

- **Facade** — the single `data_api` backend service; queries and relays already-validated data, computes no new signal (spine AD-F1/AD-F2).
- **Pane** — one sub-plot within a coin's single `lightweight-charts` instance (candlestick main pane, or one indicator's sub-pane), synced on the shared time axis by the charting library itself (spine AD-F4).
- **Cursor pagination** — the `before_ns` + `limit` → `{items, has_more}` contract every chart-history endpoint uses to load bars/snapshots incrementally as the user scrolls back (spine AD-F3).
- **Live edge** — the currently-forming candle bar at the right edge of the candlestick chart, server-aggregated and pushed over `/ws/live`'s `candles:{iid}:{bar_seconds}` channel (spine AD-F7).
- **Ranking** — the live-sorted coin list computed and published exclusively by `ranking_engine` (parent architecture spine AD-9); this rewrite is a pure reader of it.
- **Ranking Mode** — the volume-vs-volatility sort applied to the Ranking (parent spine AD-9), switched via existing `ranking_engine`/Redis `ranking:control` machinery. This epic displays it correctly and may expose the existing switch; it adds no new ranking computation or switching mechanism.
- **Staleness** — a live value whose `updated_at` has exceeded its heartbeat timeout; must render as visibly stale/unknown, never as a frozen "still current" value (spine AD-F6).
- **Lines mode** — the coin chart page's alternate view (vs. the candlestick main view) for comparing raw bid/ask/mid/microprice/price series directly, carried forward from today's dashboard (see FR-43).

## 4. Features

### 4.1 Live Coin Rankings Page

**Description:** The landing page — a live-updating table of every subscribed coin, sorted by the active Ranking Mode (volume or volatility), refreshed via the WS live relay. Replaces `dashboard.py`'s `_render_live_page`/`_rankings_json`. Realizes UJ-1, UJ-3.

**Functional Requirements:**

#### FR-38: Live coin-rankings table

The operator can view every subscribed coin's live rank, price, and key metrics in one sortable table, updating in real time without a manual refresh.

**Consequences (testable):**
- The table's row order matches `ranking_engine`'s published `rankings:live` order at all times, with no independent client-side re-sort logic beyond what the active Ranking Mode already dictates.
- A coin whose `rankings:live` heartbeat has gone stale is visibly marked (not silently frozen in its last position).
- Clicking a row navigates to that coin's chart page.

### 4.2 Single-Coin Chart Page

**Description:** The core surface this epic exists for — a coin's candlestick chart with any number of synced indicator panes beneath it, replacing `dashboard.py`'s `chart_handler`/`_build_chart_page_html` and its Plotly-based sync logic. Realizes UJ-2.

**Functional Requirements:**

#### FR-39: Candlestick chart with synced indicator panes

The operator can view a coin's candlestick chart with zero or more indicator panes (OFI, order-book imbalance, volume, microprice, spread) stacked beneath it, all sharing one time axis.

**Consequences (testable):**
- Panning or zooming any one pane moves every other pane on the same page in lockstep, in the same interaction, via the charting library's native multi-pane sync — never custom event-relay code (spine AD-F4).
- Adding, removing, or reconfiguring an indicator never resets the chart's current zoom/pan position (the exact regression this epic's trigger story, 14.3, exists to fix).
- Every consequence above holds identically on a touch-driven phone/tablet viewport (drag-to-pan, pinch-to-zoom), not only mouse/trackpad — the chart page is not exempt from NFR-B's responsive requirement.
- With up to five indicator panes visible at once, each gets a distinct, consistently-assigned series color drawn from the 16-color palette (FR-46) — the palette constraint does not mean panes become visually indistinguishable; exact color assignment is an implementation detail.

#### FR-40: Incremental, TradingView-style history loading

The operator can scroll a chart's candlestick pane back through its full available history, with older bars loading in progressively as the visible range approaches the earliest currently-loaded bar — never a single full-history fetch.

**Consequences (testable):**
- The initial chart load fetches only the most recent window (matching today's 120-bar default), not the full catalog range.
- Scrolling back triggers cursor-paginated fetches per the spine's `before_ns`/`limit` contract; the operator never observes a request whose response exceeds one page's worth of bars.
- Reaching the true start of a coin's history stops issuing further requests (`has_more: false`) rather than retrying or hanging.
- Indicator panes are not a separate, unsynced history load: as the candlestick pane's scroll-back triggers the next `before_ns`/`limit` page, each visible indicator pane co-pages its own underlying snapshot data (same cursor contract, spine AD-F3) for that same window, in the same interaction — never blank, never lagging behind the candlestick pane's loaded range.

#### FR-41: Live edge stays consistent with loaded history

The chart's currently-forming candle bar updates live at the right edge of the candlestick pane, without ever visibly diverging from the shape of the paginated historical bars beside it.

**Consequences (testable):**
- The live bar is sourced from the one sanctioned server-aggregated channel (spine AD-F7) — never assembled client-side from raw tick data.
- A bar-size change (e.g. 1m → 1h) or navigating away and back never leaves a stale live bar overlapping a freshly-loaded historical one.

#### FR-42: Per-coin indicator configuration

The operator can add, remove, and reconfigure indicators on a coin's chart, with the configuration persisted per coin and restored on the next visit.

**Consequences (testable):**
- Reloading a coin's chart page restores the same indicators and parameters last configured for that coin.
- Indicator catalog and config persistence route through the Facade's one sanctioned write path (`PUT /api/coin/{iid}/indicators`, spine AD-F2).

#### FR-43: Lines mode

The operator can switch a coin's chart page from the candlestick view to Lines mode — a direct comparison of raw bid/ask/mid/microprice/price series — carried forward from today's dashboard's Candles/Lines toggle.

**Consequences (testable):**
- Lines mode's underlying data loads via the same cursor-paginated contract as candlestick history (spine AD-F3 explicitly binds `/api/snapshots` to it), not a separate unbounded fetch.
- Switching between Candles and Lines mode preserves the currently-viewed time range rather than resetting to a default window.

**Feature-specific NFRs:**
- Chart interaction (pan, zoom, scroll-back page load) must feel immediate on the primary access path (SSH-tunneled browser session), in both Candles and Lines mode — see §7 Success Metrics.

### 4.3 31-Day Metrics History Page

**Description:** A per-coin page of small time-series charts (volume, volatility, and other ranking-input metrics) covering the trailing 31 days, replacing `dashboard.py`'s `_render_history_page`. In scope on the same basis as every other page here: it exists in today's dashboard, and NFR-D's parity bar covers it. Used to decide whether a coin merits opt-in raw-delta capture (existing platform FR-2/FR-8, unaffected by this epic).

**Functional Requirements:**

#### FR-44: 31-day metrics history view

The operator can view a coin's ranking-input metrics (volume, volatility, etc.) plotted over the trailing 31 days.

**Consequences (testable):**
- A metric with no data for part of the window renders a visible gap, never an interpolated flat line (spine AD-F6, restated for this page's own time-series rendering).

### 4.4 Docs Page

**Description:** The existing reference/help page (`dashboard.py`'s `docs_handler`), carried forward as-is in content, rebuilt as a React page.

**Functional Requirements:**

#### FR-45: Docs page

The operator can view the existing documentation content at its current URL shape, rebuilt on the new stack.

**Consequences (testable):**
- Every section present on today's `/docs` page has a corresponding section on the new page — a diffable content checklist, not a rewrite of the text.
- The page renders in the terminal visual identity (FR-46) like every other page — it is not left as an unstyled exception.

### 4.5 Terminal Visual Identity

**Description:** The single visual language applied consistently across every page in this rewrite — not a per-page styling choice. See §Aesthetic and Tone for the full specification.

**Functional Requirements:**

#### FR-46: Terminal / ANSI visual identity

Every page in the rewritten frontend renders in a consistent old-school terminal aesthetic: a monospace terminal font, ASCII-art-style decorative elements (box-drawing borders/dividers), and the classic 16-color VGA/ANSI palette as the page's entire color system — not merely an accent color layered onto an otherwise conventional web-app look.

**Consequences (testable):**
- Every page (rankings, chart, history, docs) uses the same declared color tokens, all sourced from the 16-color VGA/ANSI set (§Aesthetic and Tone) — no page introduces a color outside that set.
- Body and UI text render in a monospace typeface throughout; no page mixes in a proportional/sans-serif face for body content.
- Semantic color (e.g. a stale-data indicator, an up/down candle) is drawn from within the same 16-color set, not from a separate arbitrary palette.

## Cross-Cutting NFRs

- **NFR-A (Performance):** Initial chart-page load and scroll-back history fetches must feel immediate, not merely "eventually consistent." No numeric target is set — qualitative by deliberate choice, not an oversight. Structurally supported by the architecture spine's cursor pagination (AD-F3), code-splitting, cache headers, and compression (spine Consistency Conventions).
- **NFR-B (Responsive layout):** Every page is usable on both desktop and phone/tablet viewports — not desktop-only. This was an explicit correction during PRD discovery; the architecture spine was neutral on device scope and needs no rework to accommodate it (layout is a frontend-component concern, not a backend contract one).
- **NFR-C (Live-data honesty):** Inherited unchanged from the architecture spine's AD-F6 / the parent spine's DATA-01: no page ever renders a data gap as a flat/interpolated line, and no live value is shown as current once its heartbeat has gone stale.
- **NFR-D (Feature parity):** Every page and capability present in today's `dashboard.py` has a working equivalent in the new frontend before `dashboard.py` is deleted — this epic is a rewrite of the existing surface, not a reduction of it.

## Aesthetic and Tone

*A first-class product requirement surfaced during PRD discovery, not covered by the architecture spine (which is silent on visual design) or by any existing UX document (none exists for this epic).*

- **Typeface:** a genuine DOS/BIOS-style bitmap terminal font throughout — headings, body, tables, chart labels — in the spirit of classic VGA text-mode fonts (e.g. the IBM PC/VGA text-mode font, or a webfont in that family such as "Perfect DOS VGA 437"), not a modern coding monospace like IBM Plex Mono/JetBrains Mono. The exact font file/family is an implementation detail for the epics/stories pass — direction is decided (DOS-style, not modern-coding-style), the specific face is not.
- **Palette:** the classic 16-color VGA/ANSI console palette (black, blue, green, cyan, red, magenta, brown/yellow, light gray, dark gray, light blue, light green, light cyan, light red, light magenta, yellow, white) is the *entire* color system — background, text, borders, semantic states (up/down candles, stale-data flags, active/inactive UI), and any chart series coloring all draw from this set. No colors outside it appear anywhere in the frontend.
- **Motif:** an old-school ASCII-art vibe — box-drawing characters for borders/dividers/frames, terminal-style loading/empty states (e.g. a blinking cursor, an ASCII progress indicator) in place of conventional web-app spinners and skeleton screens.
- **Scope:** applies uniformly across all four pages (rankings, chart, history, docs) — this is a single visual identity, not a per-page choice (ties to FR-46).
- **Explicitly not required:** CRT-style rendering (scanlines, phosphor glow, curvature) — confirmed out of scope. The requirement is the DOS-style font, the 16-color palette, and the ASCII-art motif; not a full retro-CRT simulation.

## Platform

- **Web, responsive** — desktop browser (the primary access path, typically over the existing SSH-tunnel remote-dev setup) and phone/tablet browsers, both in scope. No native mobile app.

## 5. Non-Goals (Explicit)

- **Bots / `live_paper` UI.** Explicitly out of scope — stays exclusively `bot_tui`'s domain. This frontend never reads `bots:status`/`bots:control` or shows bot PnL/position data. (Corrected mid-session during the architecture pass, after an early draft mistakenly included it.)
- **New capabilities beyond parity.** This epic is a platform/foundation rewrite, not a vehicle for new analytical features — success is feature parity with today's dashboard, delivered fast and on the new stack, not a superset.
- **Auth / multi-user access.** Unchanged from today — a personal, single-user tool reachable only via SSH tunnel (SEC-01); no login system is introduced.
- **Native mobile app.** Responsive web only.
- **CRT/scanline retro effects.** See Aesthetic and Tone's explicit note — not requested, not assumed in scope.

## 6. MVP Scope

### 6.1 In Scope

- Rankings page (FR-38)
- Single-coin chart page: candlestick + synced indicator panes, incremental scroll-back, live edge, per-coin indicator config, Lines mode (FR-39–FR-43)
- 31-day metrics history page (FR-44)
- Docs page (FR-45)
- Terminal/ANSI visual identity across all of the above (FR-46)
- The backend refactor (`data_api` as the sole Facade) that all of the above depends on — already fully specified by the architecture spine

### 6.2 Out of Scope for MVP

- Bots/`live_paper` — permanently out of scope for this frontend, not merely deferred (see Non-Goals).
- Any indicator/analytical capability not already present in today's dashboard.
- `epic-14`'s own stories (14.1/14.2 done, 14.3 ready-for-dev) — become moot once `dashboard.py`'s chart page is deleted; closing them out is a sprint-status housekeeping task for the epics/stories pass, not a requirement of this PRD. `[NOTE FOR PM]`

## 7. Success Metrics

**Primary**
- **SM-1**: Feature-parity checklist — every page/capability in today's `dashboard.py` has a working, verified-equivalent React page before `dashboard.py` is deleted. Validates FR-38, FR-39, FR-41, FR-42, FR-43, FR-44, FR-45, NFR-D.

**Secondary**
- **SM-2**: Perceived speed — chart page interactive and scroll-back history responsive, in both Candles and Lines mode, measured on the real SSH-tunneled access path (not just localhost). Validates FR-40, FR-43, NFR-A.
- **SM-3**: Visual-identity conformance — every page passes a manual palette check (no color used outside the 16-color VGA/ANSI set) and renders in the DOS-style monospace font with no proportional-font fallback visible. Validates FR-46.

**Counter-metrics (do not optimize)**
- **SM-C1**: Never trade data correctness or staleness-honesty for speed — a page that loads fast but silently shows a gap as a flat line, or a stale value as live, is a regression, not a win. Counterbalances SM-2; enforces NFR-C.

## 8. Open Questions

1. Exact DOS-style font file/family (direction confirmed — DOS/BIOS bitmap style, not a modern coding monospace; specific face not yet chosen) — left to the epics/stories or an implementation-time design pass.
2. Exact placement/extent of ASCII-art decorative elements (how much is "vibe" vs. functional UI, e.g. box-drawing table borders vs. decorative banners) — left to the epics/stories or an implementation-time design pass.
3. `epic-14`'s formal closure (marking 14.3 superseded rather than shipping it) — a sprint-status housekeeping item, not resolved here.

## 9. Assumptions Index

*No open `[ASSUMPTION]` tags remain — all three from the initial Fast-path draft (performance target, font style, CRT effects) were resolved directly with the user during review: performance stays qualitative with no numeric target, the font is confirmed DOS-style (specific face still open, see §8), and CRT effects are confirmed out of scope.*
