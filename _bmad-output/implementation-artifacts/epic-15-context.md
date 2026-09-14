# Epic 15 Context: Dashboard React/TypeScript Rewrite

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Replace `troll/ml_signals/dashboard.py` (one aiohttp process that renders HTML, serves ~25 JSON routes, and runs a live Redis-subscriber loop) with a React/TypeScript SPA served by a single expanded `data_api` backend acting as a Read-Only Facade. The rewrite delivers the same four pages the operator uses daily — live coin rankings, a per-coin candlestick chart with synced indicator panes and TradingView-style incremental scroll-back, 31-day metrics history, and docs — on a foundation where one library-native mechanism owns chart pane sync (fixing the recurring "indicator repaint resets zoom" bug class) and history loads incrementally instead of as one large fetch. It is feature parity plus a terminal/ANSI visual identity, not a superset of capability; `dashboard.py` is deleted only once every page has a verified React equivalent.

## Stories

- Story 15.1: Facade scaffold, SPA static serving, and Docs page
- Story 15.2: Live coin-rankings page
- Story 15.3: Chart page foundation — candlestick + cursor-paginated history
- Story 15.4: Synced indicator panes
- Story 15.5: Live candle edge
- Story 15.6: Per-coin indicator configuration
- Story 15.7: Lines mode
- Story 15.8: 31-day metrics history page
- Story 15.9: Terminal/ANSI visual identity
- Story 15.10: Cutover — retire dashboard.py, close epic-14

## Requirements & Constraints

- Rankings table order must match `ranking_engine`'s published order exactly — no client-side re-sort beyond the active Ranking Mode; stale rows (heartbeat timeout exceeded) are visibly marked, never silently frozen in place; rows keyed by `instrument_id`, never index/timestamp/UUID.
- Chart history loads only the recent window up front (today's 120-bar default), then pages older bars on scroll-back via cursor pagination; a response never exceeds one page; reaching true history start stops further requests rather than retrying/hanging.
- Any genuine data gap renders as an explicit visible break (gap marker), never an interpolated or flat line — applies to candles, Lines mode, and the 31-day metrics history page alike.
- Indicator panes must co-page with the candlestick pane's scroll-back in the same interaction — never blank or lagging.
- Adding/removing/reconfiguring an indicator must never reset the chart's current zoom/pan position.
- The live (forming) candle bar must never diverge visibly from historical bars; a bar-size change or page remount must never leave a stale live bar overlapping fresh history.
- Per-coin indicator configuration persists and is restored exactly on reload.
- Lines mode preserves the currently-viewed time range when toggled from/to Candles mode.
- Every page (rankings, chart, history, docs) must be usable on both desktop and phone/tablet viewports, with touch drag-to-pan/pinch-to-zoom behaving identically to mouse/trackpad.
- Every page must use only the classic 16-color VGA/ANSI palette (no other color anywhere), a DOS/BIOS-style bitmap terminal font (no proportional/sans-serif fallback), and box-drawing/ASCII-art motifs in place of conventional web spinners/skeletons — no CRT/scanline effects.
- Success is feature parity (SM-1) verified before `dashboard.py` deletion, perceived speed on the real SSH-tunneled path — not just localhost (SM-2), and a manual 16-color/font-conformance check (SM-3). Never trade data correctness/staleness-honesty for speed (SM-C1).
- Out of scope: bots/`live_paper` UI (stays `bot_tui`-only, this frontend never reads `bots:*`), any analytical capability beyond today's parity, auth/multi-user access, native mobile app.
- Any new rankings-page column or per-coin metric must also land in `bot_tui` from the same source (parity is cross-checked both ways, not just this frontend's problem).

## Technical Decisions

- **Facade pattern:** `data_api` is the sole backend the SPA talks to — REST under `/api/*`, live relay under `/ws/live`, SPA static files served via FastAPI's native `app.frontend()` helper (never hand-rolled `StaticFiles` + catch-all). An unmatched `/api/*` path returns JSON 404, never SPA HTML.
- **No new computation:** every route/relay calls an existing `ml_signals`/`ranking_engine`/`dydx_collector` pure function or query — never a second, independently-drifting implementation of OFI/OBI/rankings/candle-aggregation. The one sanctioned write path is indicator-config persistence (`PUT /api/coin/{iid}/indicators`); `/ws/live` forwards existing Redis wire formats verbatim plus the one derived `candles:{iid}:{bar_seconds}` channel.
- **Cursor pagination is universal and pinned:** every chart-history route (`/api/candles`, `/api/snapshots`, and any future scroll-back route) takes `before_ns` + `limit`, returns `{"items": [...], "has_more": bool}`. A short page with `has_more: true` is legal and must not be misread as exhaustion. Relocating a route's business logic does not mean preserving its old wire shape — where old and new contract conflict (e.g. old `start_ns`/`end_ns` vs new `before_ns`/`limit`), the new pagination contract wins.
- **One chart instance:** exactly one `lightweight-charts` `createChart()` per coin page, with N panes (`chart.addPane()`), each indicator its own `IPaneApi` via `addSeries`/`addCustomSeries`. Sync is the library's native multi-pane time-scale sync only — never custom event-relay code. Panes are keyed by indicator id (reusing `dashboard.py`'s existing `_indicator_id(name, params)` scheme) in one `Map<indicatorId, IPaneApi>` owned by the single chart-owning component; no child component creates/destroys a pane directly.
- **Live candle edge:** `data_api` computes the forming bar server-side by calling the same `ml_signals.candles` aggregation function used for history, against incoming `snapshots:raw` ticks, publishing on `/ws/live`'s `candles:{iid}:{bar_seconds}`. Frontend never aggregates a candle client-side from raw ticks.
- **Generated contract types:** TypeScript REST types are generated from `data_api`'s OpenAPI/Pydantic schema at build time (codegen tool choice is an implementation detail) — never hand-written to match. `/ws/live` frame types are the one hand-written exception, but must cite their exact source (Redis wire format or the `ml_signals.candles` function) in a comment.
- **Staleness & gaps:** every live-pushed channel carries `updated_at`; exceeding a configured timeout renders as a visible `stale`/`unknown` UI state, never silently kept as current. Genuine data gaps get an explicit gap marker (null/whitespace-data point) at the boundary, never omitted or interpolated.
- **State/library choices:** React Query owns all server state (no Redux/Zustand). Route-based code-splitting per page (chart page bundle never blocks rankings/history/docs and vice versa). Frontend imports `data_api` only via HTTP/WS — never a direct Python import.
- **Stack (pinned versions):** React 19.3.0, Vite 8.3.0 + `@vitejs/plugin-react` 6.1.1, `@tanstack/react-query` 5.102.8, `lightweight-charts` 5.2.1, FastAPI 0.141.1 (`app.frontend()` available since 0.138.0), Vitest + React Testing Library (Vite scaffold default), TypeScript strict mode, Node.js LTS build-stage only (never a runtime dependency).
- **Structural seed:** `data_api/routes/{candles,snapshots,rankings,indicators,metrics}.py`, `data_api/ws/live.py`, own `data_api.dockerfile`; `frontend/src/{pages,components/chart,components/rankings,api,hooks}`. `ml_signals` compute modules stay unchanged and are called from `data_api`, not duplicated into it.
- **Deployment:** `data_api` gets its own dockerfile (`troll/data_api.dockerfile`, layered on `nautilus-trader-base` with an added Node build stage running `vite build`; `dist/` copied in, `node_modules` absent from runtime) — `troll/collector.dockerfile` is untouched. `docker-compose.yml` gets a `data_api` service (`network_mode: host`, `127.0.0.1`-bound) alongside (not yet replacing) `dashboard` until the Story 15.10 cutover.
- **Superseding epic-14:** epic-14's 14.1/14.2 are done; 14.3 becomes moot once `dashboard.py`'s chart page is deleted and is marked superseded (not shipped) in `sprint-status.yaml` — a Story 15.10 task, not resolved earlier.
- **Open/deferred implementation choices:** exact DOS-style font family, exact extent/placement of ASCII-art decorative elements, and OpenAPI→TS codegen tool are all left to implementation time.

## UX & Interaction Patterns

- Single visual identity across all four pages: monospace DOS/BIOS bitmap terminal font throughout (headings, body, tables, chart labels — no proportional-font fallback anywhere), 16-color VGA/ANSI palette as the *entire* color system (background, text, borders, semantic states, chart series), box-drawing characters for borders/dividers, terminal-style loading/empty states (blinking cursor, ASCII progress indicator) instead of web spinners/skeletons. No CRT/scanline effects.
- Key journeys: operator opens rankings, clicks a coin whose rank just jumped, lands on its chart page (UJ-1); drags a coin's candlestick chart back weeks while OFI/OBI panes scroll back in the same motion and older bars keep loading (UJ-2); checks the dashboard from a phone and it's legible without pinch-zooming (UJ-3).
- Primary access path is an SSH-tunneled browser session — interaction speed (pan/zoom/scroll-back page load) must be judged there, not just localhost.

## Cross-Story Dependencies

- Story 15.1 is foundational: every later story depends on the facade/SPA/codegen pipeline it proves out (Docs page as walking skeleton).
- Story 15.3 (candlestick + pagination, single `createChart()` instance) is the base Story 15.4 (indicator panes via `addPane()`) builds on; 15.4's keyed pane registry and color-slot assignment are what 15.6 (per-coin indicator config) and 15.9 (final palette) build on top of respectively.
- Story 15.5 (live candle edge) depends on 15.3's candle aggregation path and 15.4's established chart instance.
- Story 15.7 (Lines mode) reuses 15.3's exact cursor-pagination contract on a new route (`/api/snapshots`).
- Story 15.9 finalizes the pane-color assignment logic stubbed out in 15.4; it is not new logic.
- Story 15.10 (cutover) depends on every other story in this epic being shipped — it walks a feature-parity checklist against 15.1–15.9 before deleting `dashboard.py`, and formally closes out epic-14 (marks 14.3 superseded).
- Epic 16 (Minute-Rollup Candle Cache) is a separate, standalone epic that becomes a future dependency for this epic's `/api/candles` route once it lands — not itself part of Epic 15's scope.
