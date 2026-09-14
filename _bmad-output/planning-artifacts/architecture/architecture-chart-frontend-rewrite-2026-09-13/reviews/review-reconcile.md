# Reconciliation Review — Chart Frontend Rewrite spine vs. load-bearing inputs

**Reviewed:** `architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md`
**Against:** parent spine (`architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`), `troll/CLAUDE.md`, and the verbatim original user request.
**Date:** 2026-09-13

---

## 1. Parent spine — Inherited Invariants table fidelity

Checked each row of the new spine's "Inherited Invariants" table against the actual AD text in the parent spine.

| Row | Verdict |
| --- | --- |
| AD-3 (readers trust the gate) | Faithful — matches parent's "no reader re-implements crossed-book/staleness/precision checks" verbatim in spirit. |
| AD-4 (module boundary) | Faithful — correctly scopes `data_api` to shared types + pure functions, no stateful ingestion internals. `data_api`/`frontend` aren't in AD-4's original `Binds` list (`dydx_collector, ml_signals, live_paper, ranking_engine, bot_tui`), but the new spine treats this as an extension, which is legitimate — not a silent narrowing. |
| AD-6 (catalog access only through official API) | Faithful for the read-only subset that applies (`data_api` never writes). |
| AD-9 (ranking_engine sole computer/publisher) | **Partial — see Finding 1.** The row captures only the "never recompute rank/volatility — pure passthrough" half of AD-9. AD-9 itself is a compound rule: computation-ownership *and* a staleness/heartbeat discipline ("dashboard and bot_tui treat absence of a heartbeat within a configurable timeout as unknown/stale, never as last known ranking is still current"). Only the first half survived into the new spine's row and into AD-F2's "forwards verbatim" language. The second half — the *reader's* obligation to visually flag staleness — isn't restated anywhere for the new frontend. |
| AD-10 (live_paper control-plane isolation) | **Same partial pattern as AD-9.** AD-10 has an even more detailed staleness discipline (separate bullets for `bots:status` staleness and `bots:history:*` staleness — "a live_paper crash or restart must show up as visibly stale in bot_tui, mirroring the project's existing never-show-stale-as-live discipline for market data"). The new spine's row only inherits the "pure client, never import live_paper internals" half. |
| Fork boundary convention | Faithful. |
| Memory convention (MEM-01) | Faithful, correctly extended via AD-F3 to the network boundary (cursor pagination, bounded `limit`). MEM-02 ("non-configured coins are rolling-window-in-memory only") isn't mentioned, but this is reasonable — `data_api` is stateless per-request, so MEM-02 doesn't structurally apply. Not a gap. |
| SEC-01 (localhost-only ports) | Faithful — restated correctly and also reconfirmed in the Deployment & Environments section ("still binding 127.0.0.1 only — SEC-01 unchanged"). |
| SSOT-01..05 | Faithful, and SSOT-04/05's cross-check-with-bot_tui obligation is explicitly carried into the Deferred section rather than silently dropped — this is the correct pattern (contrast with AD-9/AD-10 above, where the analogous obligation isn't even mentioned). |

### Finding 1 (HIGH) — Staleness/heartbeat discipline dropped for the new frontend

**What's missing:** Nowhere in the new spine — not in an AD, not in Consistency Conventions, not in the Structural Seed, not in Deferred — does the word "stale" or "staleness" or "heartbeat" appear. This is the exact discipline that:

- Parent spine's AD-9 and AD-10 both state explicitly as a *reader* obligation (not a backend-computation obligation, so AD-F2's "computes nothing" framing doesn't cover it — a reader can forward data "verbatim" and still get staleness handling wrong, because staleness handling is about what the reader does with an *absence* of updates, not about reshaping present ones).
- `troll/CLAUDE.md` DATA-01 states as the top data-integrity rule: "Correct data is the #1 priority. Never display stale or fabricated values as live market data... the gap must be flagged visually rather than papered over with a flatline," and explicitly calls out `dashboard._coin_chart_json`'s `_CHART_GAP_THRESHOLD_MS` gap-rendering (Plotly `None` insertion so a gap draws as a break, not an interpolated flatline) as the reference implementation.
- `troll/CLAUDE.md` OBS-01/02 exist specifically because a frozen-but-not-visibly-stale UI has already caused production incidents (zero book updates read as "quiet market" instead of a dead feed; a changing OFI z-score papering over a frozen underlying price).
- Parent spine's own Deferred section flags dashboard's staleness-gap rendering as "NOT deferred cleanup — it is a permanent reader responsibility... doing so would silently reintroduce the flatline-interpolation failure this logic exists to prevent."

**Why it matters here specifically:** AD-F1 explicitly deletes `dashboard.py` including its rendering functions, and the new spine's Structural Seed replaces every one of its pages with a React equivalent — meaning the `_STALE_BOOK_NS`/`_CHART_GAP_THRESHOLD_MS`-equivalent logic has no landing spot unless a story happens to reinvent it from first principles during implementation. AD-F3's cursor-paginated candle fetching and lightweight-charts' own gap rendering (`whitespace` data points) would need this logic ported deliberately — it is not something FastAPI's "forward verbatim" plus React Query's caching gives you for free. This is precisely the class of "quiet requirement dropped by an AD reorganization" the review was commissioned to catch: the old spine encoded staleness handling as an explicit reader AD; the new spine's AD structure (facade computes/relays; frontend syncs charts) has no slot for a reader-side "treat absence of update as unknown, not last-known" rule, so it fell out silently when the ADs were rebuilt around a different paradigm (facade vs. gate).

**Recommendation:** Add either a new AD (e.g. AD-F6 — "Frontend never renders absence-of-update as still-current") or fold it into AD-F4/AD-F2, covering: (a) `/ws/live` heartbeat cadence is preserved verbatim (already implied by AD-F2 but should be said outright), (b) React Query / the WS hook must treat a missed heartbeat beyond a configurable timeout as `stale`/`unknown`, not silently keep rendering the last value, (c) the candlestick/indicator charts must render a genuine data gap (collector-side skip, e.g. `_STALE_BOOK_NS`) as a visible break, not an interpolated line — the lightweight-charts equivalent of dashboard.py's `None`-insertion trick.

### Finding 2 (MEDIUM) — AD-8 (TradingNode/DataEngine ban) not restated as an inherited invariant

The task's own example flags this, and it holds up: AD-8 ("No live-runtime engine in the data-collection path... `TradingNode` is for trading, not collecting") is not in the new spine's Inherited Invariants table at all. `data_api` is a new module not enumerated in AD-8's original `Binds` list (`dydx_collector, ml_signals, ranking_engine, bot_tui`), so it isn't automatically covered by the parent AD's own binding — same situation as AD-4 above, except AD-4 *was* explicitly extended in the new spine's table and AD-8 was not.

This matters because `data_api` is exactly the kind of service that could plausibly be tempted toward `TradingNode`/`DataEngine` later (e.g. "let's just run a live `DataClient` in `data_api` for lower WS latency instead of relaying through Redis") — the same shape of mistake AD-8 exists to prevent, one layer removed. `troll/CLAUDE.md`'s module docstring convention (`FORK-02`) also states the ban applies to `dydx_collector`/`ml_signals`, and `data_api` sits adjacent to `ml_signals` importing from it (per AD-4's extension) — so the ban's rationale clearly reaches this new module even though the letter of AD-8's `Binds` list doesn't yet name it.

**Recommendation:** Add an Inherited Invariants row for AD-8 explicitly binding `data_api`/`frontend`, even though it's a short one-liner — "never instantiate `TradingNode`/`DataEngine`; `data_api` stays a pure Redis/catalog client" — mirroring how AD-4 was already extended.

---

## 2. `troll/CLAUDE.md` rule cross-check

Walked FORK-01/02, SEC-01, DATA-01..04, OBS-01..03, DESIGN-01..03, MEM-01..03, TEST-01..04, READ-01..03, SSOT-01..05, NAUT-01..03 against the new spine.

- **FORK-01/02** — no contradiction; new spine is additive-only under `troll/data_api`, `troll/frontend`.
- **SEC-01** — no contradiction; explicitly restated and reconfirmed in Deployment & Environments.
- **DATA-01** — **not reflected** (see Finding 1 above; this is DATA-01's exact language: "never display stale... as live market data," "flagged visually rather than papered over with a flatline").
- **DATA-02/03/04** — ingestion-side rules (crossed-book resolution, forced-resync semantics); correctly out of scope here since AD-3 (inherited) already forbids the new readers from re-implementing them. No contradiction.
- **OBS-01/02** — not contradicted, but their detection *mechanism* (a human noticing a frozen chart) depends on the UI actually surfacing staleness — same underlying gap as Finding 1, restated from the observability angle: if the React rewrite doesn't carry forward visual staleness flagging, OBS-01/02-class incidents become harder to catch by eyeballing the new UI, even though the rule itself isn't "violated" by any AD text.
- **OBS-03** — backend/collector concern (`open_interest.py`), untouched by this epic; no contradiction.
- **DESIGN-01/02/03** — consistent; the spine is notably YAGNI-disciplined (single facade, deletion of `dashboard.py` rather than parallel legacy surface, no premature abstraction).
- **MEM-01/02/03** — MEM-01 explicitly extended (AD-F3); MEM-02/03 not applicable to a stateless facade, not a gap.
- **TEST-01..04** — no explicit test-strategy AD for `data_api`/`frontend`, but nothing in the new spine performs financial calculation itself (AD-F2 forbids it), so TEST-01's trigger condition doesn't newly apply. Not a gap.
- **READ-01..03** — Python-specific (function length, type hints); the new spine's TypeScript-strict-mode convention is a reasonable analogue, not a contradiction.
- **SSOT-01..05** — consistent; SSOT-04/05's bot_tui parity obligation is explicitly carried into Deferred (correct handling, unlike the AD-9/AD-10 staleness omission above).
- **NAUT-01/02/03** — untouched by this read-only facade; consistent via inherited AD-3/AD-4/AD-6.

No direct rule contradictions found. The one substantive gap against `troll/CLAUDE.md` is DATA-01, which is the same root issue as Finding 1.

---

## 3. Original user request — clause-by-clause coverage

> "i want to create a complete refactor of the frontend, it needs to be decoubled from the backend, written in react with react query, it needs to use lighweight charts. The candlestick chart with the indicators needs to mimic tradingview, so you can browse entire history of a coin without loading entire history at once, but bit by bit as you go back on the chart. THe backend also needs a refactor so i can serve the frontend better. I want speed of the page to be though in to the process i want blazingly fast loading speeds and navigation of charts. I also want the charts to be synced so when moving the candlestick chart you also move the order book imbalance charts forexample."

| Clause | Covered by | Verdict |
| --- | --- | --- |
| "complete refactor of the frontend" | Entire spine; `dashboard.py` deleted, `frontend/` SPA built (AD-F1) | Covered |
| "react with react query" | Stack table (React 19.3.0, @tanstack/react-query 5.102.8); Consistency Conventions "State" row | Covered |
| "lightweight charts" | Stack table (lightweight-charts 5.2.1); AD-F4 | Covered |
| "mimic tradingview... browse entire history bit by bit, not all at once" | AD-F3 (cursor-paginated `before_ns`+`limit`, driven by `subscribeVisibleLogicalRangeChange`) | Covered, and specifically cites the TradingView-mimicking mechanism |
| "backend also needs a refactor so I can serve the frontend better" | AD-F1/AD-F2 (collapse `data_api`+`dashboard.py` into one facade) | Covered |
| "charts synced... moving candlestick also moves order book imbalance chart" | AD-F4 (one `lightweight-charts` instance, N panes, native time-scale sync — explicitly bans the old per-instance Plotly relayout-sync hack) | Covered, and directly named as the pattern being replaced |
| "decoupled from the backend" | AD-F1 + Deployment & Environments | **Ambiguous / partially undermined — see Finding 3** |
| "blazingly fast loading speeds ... navigation of charts" | AD-F3 (scroll-back speed), AD-F4 (pane-sync, avoids Plotly relayout cost) address *navigation*; nothing addresses *loading* | **Gap — see Finding 4** |

### Finding 3 (MEDIUM) — "Decoupled from the backend" is only half-satisfied, and the spine doesn't acknowledge the tradeoff

AD-F1 mandates that `data_api` serve the built SPA's static files itself as the catch-all route (`STATIC["/* — SPA static files (catch-all)"]` in the diagram; `static/ # vite build output (dist/), COPY'd in at image build time` in the Structural Seed), and the Deployment section confirms the frontend's build output is baked into the same Docker image/container as the backend via a multi-stage Dockerfile.

This satisfies one reading of "decoupled" — the frontend is no longer Python-string-templated HTML coupled to backend rendering code, it talks to the backend only over a typed REST/WS contract (AD-F5), and it *could* be pointed at any backend implementing that contract. That's a real, legitimate win and probably what "decoupled" chiefly meant.

But it does not satisfy a second, equally plausible reading: independent build/deploy/release lifecycle. Under AD-F1, the frontend cannot be deployed, cached at a CDN edge, or rolled back independently of the backend — a frontend-only change still requires rebuilding and redeploying the `data_api` container (the Node build stage just becomes a layer in that same image), and the two share one port/one process/one container lifecycle. This is a deliberate, defensible architectural choice (fewer moving parts, matches SEC-01's single-port-per-service posture, avoids a second container per DESIGN-01 YAGNI) — but the spine never states it as a tradeoff against the user's explicit "decoupled" ask, and never considers or rejects the alternative (data_api as pure API+WS only, frontend static files served separately, e.g. by nginx or a CDN, with only a reverse-proxy or CORS relationship to the backend). A reader of this spine could reasonably conclude the "decoupled" requirement was fully met when only the rendering-coupling half was addressed and the deploy-coupling half was implicitly re-introduced without comment.

**Recommendation:** Either (a) add a short "Chosen over" note under AD-F1 explaining this tradeoff was deliberate and why (mirrors the parent spine's own AD-11 style, which is good at naming rejected alternatives), or (b) if genuinely undecided, flag it in Deferred rather than let AD-F1 silently settle it.

### Finding 4 (MEDIUM) — Initial page-load speed has no AD; only in-chart navigation speed does

AD-F3 and AD-F4 are both about the speed and correctness of *navigating within an already-loaded chart* (scroll-back pagination, native pane sync). Nothing in the spine addresses the other half of "blazingly fast loading speeds": first paint / time-to-interactive of the SPA itself. Concretely absent from both the ADs and the Stack table:

- Code-splitting / route-based lazy-loading (React Router lazy routes, `React.lazy`) so the chart page's JS isn't shipped on the coin-list page.
- Bundle-size budget or tree-shaking discipline for `lightweight-charts` + React + React Query.
- HTTP caching headers / ETags / immutable-asset hashing for the static `dist/` output `data_api` now serves (this is squarely `data_api`'s job once it owns static serving per AD-F1 — the spine assigns it the responsibility without assigning it the requirement).
- Compression (gzip/brotli) for API responses and static assets.
- Any initial-payload budget for the coin-list/rankings page (which the request's "navigation of charts" language suggests is the first thing a user sees).

Vite is picked as the build tool (Stack table), which does default to route-level code-splitting when configured with lazy imports — but the spine never states this as a requirement, so it's left entirely to implementation discretion whether the resulting bundle is actually fast to load, only that it exists and is served as static files.

**Recommendation:** Add a lightweight AD or Consistency Conventions row covering initial load — even a minimal one (e.g. "SPA routes are code-split; `data_api`'s static file serving sets immutable cache headers on hashed Vite build assets; bundle-size regression is checked at build time") would close the gap. This doesn't need the rigor of AD-F3/AD-F4, but currently it's not mentioned at all, which is asymmetric given how explicit and mechanism-specific the chart-navigation-speed ADs are.

### Finding 5 (LOW) — Minor internal-naming tension: "Read-Only Facade" vs. the indicator-config `PUT` route

The paradigm is named "Read-Only Facade" and AD-F2 states the facade "computes nothing; it queries and relays only." The Consistency Conventions table then carves out `/api/coin/{iid}/indicators` as a `GET`/`PUT` resource for indicator-config persistence, explicitly a write. This is a reasonable, narrow exception (persisting a UI preference isn't "computing a signal," and the old `dashboard.py` had the same route) — but it sits oddly under a paradigm literally named "Read-Only" and an AD literally titled "computes nothing." Not a functional problem, just a naming/self-consistency wrinkle worth a one-line acknowledgment (e.g. "Read-Only" refers to market/derived data, not all application state) so a future reader doesn't treat the `PUT` route as an AD-F2 violation.

---

## Summary of findings by severity

1. **HIGH** — Staleness/heartbeat/gap-visualization discipline (parent AD-9 & AD-10's reader-staleness bullets; `troll/CLAUDE.md` DATA-01, OBS-01/02) has no landing spot anywhere in the new spine — not an AD, not a Consistency Convention, not even a Deferred entry. This is the clearest candidate for "quiet requirement dropped by the AD restructuring."
2. **MEDIUM** — AD-8 (`TradingNode`/`DataEngine` ban) isn't restated in the Inherited Invariants table for the new `data_api`/`frontend` modules, unlike AD-4 which *was* explicitly extended.
3. **MEDIUM** — "Decoupled from the backend" is satisfied for rendering-coupling (React+REST/WS vs. server-rendered HTML) but AD-F1 silently reintroduces deploy/lifecycle coupling (one container, one image, frontend build baked into backend's Dockerfile) without acknowledging this as a tradeoff against the user's explicit wording.
4. **MEDIUM** — "Blazingly fast loading speeds" is addressed only for in-chart navigation (AD-F3/AD-F4); initial page-load performance (code-splitting, caching headers, bundle size, compression) has no AD or convention at all.
5. **LOW** — Minor naming tension between "Read-Only Facade"/AD-F2's "computes nothing" and the carved-out indicator-config `PUT` route.
