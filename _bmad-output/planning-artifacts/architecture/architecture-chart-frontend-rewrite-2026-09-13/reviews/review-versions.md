# Version Verification Review — Chart Frontend Rewrite Architecture Spine

**Reviewed:** `ARCHITECTURE-SPINE.md` (Stack table + AD-F4's `[VERIFIED lightweight-charts 5.2.1, 2026-09-13]` tag)
**Method:** Direct `registry.npmjs.org`/`pypi.org` JSON API queries (ground truth, not search-engine summaries) for every pinned package, cross-checked against WebSearch/WebFetch for API-shape and pattern claims. Today: 2026-09-13.

## Verdict

All six pinned versions in the Stack table are correct and are genuinely the current `dist-tags.latest` as of today. One process/pattern gap found: the spine's `data_api` design (mermaid diagram + `app.py` comment) describes the StaticFiles-mount + manual catch-all-route pattern for SPA serving, but the project's own already-pinned `fastapi==0.141.1` ships a native `app.frontend()` SPA-serving API that supersedes that pattern and should be used instead.

## Per-item findings

### lightweight-charts 5.2.1 — CONFIRMED
`registry.npmjs.org/lightweight-charts` `dist-tags.latest` = `5.2.1`, published 2026-08-12. No newer major/minor exists. Multi-pane API (`chart.addPane()`, `IPaneApi.addSeries()`/`addCustomSeries()`, `pane.setHeight()`, `chart.panes()`, `chart.removePane()`/`swapPanes()`) was introduced as a major feature in v5.0.0 and expanded through v5.0.8; per TradingView's own release notes, panes have been stable since with only bugfixes/enhancements (v5.0.3 fixed a per-pane price-scale bleed bug, v5.2.0 added `hoveredSeriesOnTop`). AD-F4's claim that native multi-pane + shared time-scale sync is the correct mechanism (vs. app-level chart-instance sync) is accurate and current — nothing has superseded it.

### React 19.3.0 — CONFIRMED
`registry.npmjs.org/react` `dist-tags.latest` = `19.3.0`, published 2026-09-09 (4 days before this review). Current stable.

### Vite 8.3.0 + @vitejs/plugin-react v6 — CONFIRMED
`registry.npmjs.org/vite` `dist-tags.latest` = `8.3.0`, published 2026-09-10 (3 days before this review). `@vitejs/plugin-react` `dist-tags.latest` = `6.1.1`, published 2026-08-28 — spine correctly cites it at major-version granularity ("v6"). Note for implementation: plugin-react v6 dropped Babel for an Oxc-based React Refresh transform (smaller install, faster); if React Compiler is wanted later, v6 needs the separate `reactCompilerPreset` + `@rolldown/plugin-babel` opt-in rather than the old built-in path — not a spine correction, just a heads-up for whoever scaffolds it.

### @tanstack/react-query 5.102.8 — CONFIRMED
`registry.npmjs.org/@tanstack/react-query` `dist-tags.latest` = `5.102.8`, published 2026-08-27. Current stable, nothing newer.

### FastAPI (no version pinned by this spine) — CONFIRMED pin is current, but pattern described is outdated for that pin
`pypi.org/pypi/fastapi/json` `info.version` = `0.141.1`, matching what's already pinned in `troll/troll-requirements.txt` (`fastapi==0.141.1`) — so the spine's "already pinned, no change forced" framing is factually correct and the pin itself is current-latest.

**However:** FastAPI shipped a native SPA-serving API, `app.frontend()`, in v0.138.0 (2026-06-20), with refinements through v0.141.x (`check_dir="auto"` for `fastapi dev` convenience in July 2026; a background-tasks/dependency-headers fix in 0.141.1 itself). This is a first-class replacement for the community-standard "mount `StaticFiles(html=True)` + hand-rolled catch-all route registered last" pattern that the spine's mermaid diagram (`STATIC["/* — SPA static files (catch-all)"]`) and Structural Seed (`app.py # FastAPI app assembly, StaticFiles mount, catch-all fallback`) both describe. The hand-rolled pattern's known footgun — route registration order matters, and mounting the catch-all before API routers causes it to swallow `/api/*` requests — is exactly what `app.frontend()` was built to eliminate: it distinguishes a missing static asset (404) from a client-side-route navigation (falls back to `index.html`) correctly, and there's no "must register last" ordering hazard to get wrong. Since the project is already on the exact FastAPI version that has this, the spine should specify `app.frontend()` rather than the older StaticFiles+catch-all idiom — this is implementation-detail-level, not a structural change to any AD, but worth updating in the Structural Seed's `app.py` comment before the epics/stories pass.

### Vitest + React Testing Library — CONFIRMED still the standard pairing
`registry.npmjs.org/vitest` `dist-tags.latest` = `5.0.0` (published 2026-09-03); `@testing-library/react` `dist-tags.latest` = `16.3.3`. Multiple independent 2026 sources concur Vitest has displaced Jest as the default test runner for Vite-scaffolded React projects (State of JS 2025: 52% adoption, up from 20% in 2023), paired with React Testing Library for component-level queries. No tooling-consensus shift away from this pairing. Spine's "ships with Vite scaffold, latest stable" phrasing (no version pin) is appropriately loose and still accurate — no correction needed.

## Sources
- npm registry JSON API (`registry.npmjs.org/<pkg>`) — direct, queried live for lightweight-charts, react, react-dom, vite, @vitejs/plugin-react, @tanstack/react-query, vitest, @testing-library/react
- PyPI JSON API (`pypi.org/pypi/fastapi/json`) — direct
- https://tradingview.github.io/lightweight-charts/docs/release-notes
- https://tradingview.github.io/lightweight-charts/docs/next/api/interfaces/IPaneApi
- https://github.com/tradingview/lightweight-charts/blob/master/src/api/chart-api.ts
- https://vite.dev/blog/announcing-vite8
- https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react/CHANGELOG.md
- https://umesh-malik.com/blog/fastapi-spa-app-frontend-explained (corroborated against PyPI's own version history and independent WebSearch results — not taken on the blog's authority alone)
- https://dualite.dev/blogs/component-tests-guide
- `/home/mrqdt/code/nautilus_trader_fork/troll/troll-requirements.txt` (project's own fastapi pin, cross-checked)
