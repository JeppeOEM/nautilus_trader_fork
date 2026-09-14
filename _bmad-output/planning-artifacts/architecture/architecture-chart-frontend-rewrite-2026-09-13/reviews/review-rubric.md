# Review — Chart Frontend Rewrite Architecture Spine

**Reviewer:** independent, fresh-context (rubric-driven)
**Date:** 2026-09-13
**Verified against:** live repo state (`troll/data_api/app.py`, `troll/ml_signals/dashboard.py`, `troll/docker-compose.yml`, `troll/collector.dockerfile`, `troll/troll-requirements.txt`, `troll/CLAUDE.md`, parent spine `architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`)

## Overall verdict

Structurally sound and mostly well-grounded (function names, route shapes, and version pins it cites all check out against the live repo), but it has one critical omission (chart gap/staleness rendering — the one dashboard behavior troll/CLAUDE.md treats as non-negotiable, DATA-01 — is never carried forward) and one internal self-contradiction on its own headline fix (AD-F3 cursor pagination vs. the Structural Seed's own "history-range" `snapshots.py` route), plus an unaddressed build-pipeline detail (the "multi-stage data_api Dockerfile" doesn't exist as a separate file today — five services share one `collector.dockerfile`).

## Findings

### 1. [Critical] DATA-01 (staleness/gap rendering) is dropped, not inherited

troll/CLAUDE.md's DATA-01 is explicit and non-negotiable: "the gap must be flagged visually rather than papered over with a flatline," with `dashboard._coin_chart_json` inserting `None` at timestamp gaps > `_CHART_GAP_THRESHOLD_MS` cited as the reference implementation, and a standing instruction that "when adding new data sources or display paths, apply the same principle." This spine is a from-scratch rewrite of exactly that display path (the chart page), yet:

- The Inherited Invariants table lists AD-3/4/6/9/10, Fork boundary, MEM-01, SEC-01, SSOT-01..05 — **DATA-01 is absent**.
- AD-F3/AD-F4 (the two ADs governing chart data loading and rendering) say nothing about gap/whitespace handling.
- `lightweight-charts` requires an explicit convention here (its API distinguishes a missing point from a zero/flat one via whitespace data) — silence means two independently-built series (candlestick vs. an indicator sub-pane) can trivially diverge: one interpolates across a stale-book gap, the other doesn't.

This is precisely the class of "genuinely load-bearing divergence point" the checklist asks to hunt for, and it's the one piece of prior dashboard behavior the parent codebase's own rules single out as most important to preserve.

**Fix:** add an AD (or extend AD-F3/F4) binding every chart-rendering component to insert explicit gap/whitespace data at the same `_CHART_GAP_THRESHOLD_MS`-equivalent boundary, and add DATA-01 to the Inherited Invariants table.

### 2. [Major] AD-F3 contradicts the Structural Seed's own route for the exact bug it cites

AD-F3's Prevents clause names a specific incident: "the ~12MB-per-4-hour-window `/catalog/snapshots` timeout." But the Structural Seed lists the successor route as:

```
snapshots.py    # /api/snapshots/{iid} history-range routes
```

"History-range" is start/end semantics — the same shape that produced the cited 12MB/timeout bug in the current `data_api/app.py` (`/catalog/snapshots/{iid}` takes `start_ns, end_ns` with no bound, per its own docstring: *"For a 4-hour window /catalog/snapshots serializes to ~12MB, which took 30-60s over an SSH tunnel"*). AD-F3's Rule text binds only `/api/candles/{instrument_id}` by name, plus "any future `/api/*` route the frontend adds for chart scroll-back" — leaving it genuinely ambiguous whether `/api/snapshots` (an existing-shape route, not a "future" one, and arguably not "for chart scroll-back" since it's full order-book depth for Lines mode) is bound by AD-F3 at all. No `useSnapshots`-style hook appears in `frontend/src/hooks/` to force cursor consumption on the client side either — only `useCandles` and `useLiveChannel` are named.

**Concrete divergence scenario (per the rubric's request):** Dev A builds `/api/candles` cursor-paginated, obeying AD-F3's explicit name. Dev B builds `/api/snapshots` as literally described in the Seed — a start/end "history-range" route, unbounded — obeying the Seed's own text and reasonably concluding AD-F3 doesn't reach a route it doesn't name. Both are individually spine-compliant; the result reintroduces the exact payload/timeout failure this rewrite exists to fix, on the one route (full order-book depth) most likely to reproduce it.

**Fix:** either fold `/api/snapshots` explicitly into AD-F3's binding list, or state explicitly that Lines-mode depth history is out of scope / uses a different bounded strategy (e.g. depth-reduced payload), and add the missing frontend hook to the Structural Seed.

### 3. [Major] "data_api's Dockerfile becomes multi-stage" — no such per-service Dockerfile exists

Live `docker-compose.yml` shows `collector`, `dashboard`, `ranking_engine`, `data_api`, and `bot_tui` **all** build from the same `troll/collector.dockerfile` (one `COPY` of all five Python packages, `CMD`/`command:` overridden per service). There is no `data_api.dockerfile` today. The Deployment & Environments section says:

> `data_api`'s Dockerfile becomes multi-stage: a `node:`-based stage runs `vite build`... its `dist/` output is `COPY`'d into the existing thin troll layer

If this means adding a Node/vite build stage directly to the *shared* `collector.dockerfile`, every other service (collector, ranking_engine, bot_tui — none of which touch the frontend) picks up an unrelated Node build stage and larger image, contradicting the parent spine's explicit "thin layer, rebuilds in seconds" design goal for that shared file. If instead `data_api` is meant to split off into its own Dockerfile (a real structural change to the shared-image convention the parent spine established), that decision and its consequences (now two service-Dockerfiles instead of one shared one; does `dashboard`'s removal free up the slot, or does the shared file still serve 4 remaining Python-only services?) are never stated. This is exactly the "build/deploy pipeline" sub-dimension the checklist calls out as easy to silently skip.

**Fix:** state explicitly whether `data_api` gets its own Dockerfile (breaking from the shared-image convention) or the Node stage is added to the shared file with a rationale for why that's acceptable now that `dashboard` is deleted.

### 4. [Moderate] AD-F2's "no filtering, ever" forecloses an optimization this rewrite likely needs

Confirmed in the live collector (`collector.py:366`): `snapshots:raw` is one global Redis channel carrying every subscribed instrument's batch per flush — not per-instrument. AD-F2's Rule states `/ws/live` "does not reshape, recompute, or filter fields beyond per-connection channel subscription" — i.e. a client can choose *which channels* (`snapshots:raw`/`rankings:live`/`bots:status`) but never a subset of one channel's content. That means every browser tab open on a single coin's chart page receives every other subscribed coin's book data too, unconditionally, for the life of this spine. Given the whole point of AD-F3 is fixing a payload-size problem, locking out per-instrument WS filtering by rule (rather than deferring it) is worth a second look — at minimum it should be flagged as a known, accepted cost rather than left implicit.

### 5. [Minor] No React-side equivalent of the TUI-01 lesson

troll/CLAUDE.md's TUI-01 documents a bug class that has shipped **twice** in this codebase: a scrollable pane rebuilt fresh on every refresh tick silently resets the user's scroll/focus position. The React rewrite's structurally identical risk — a live WS-driven re-render of the coin list / rankings table / history table resetting scroll position because a new array/key identity is created each tick — is not mentioned anywhere (AD-F4 only covers chart-pane sync, not list panes). Given this exact failure mode has cost real engineering time twice already in the urwid codebase, its total absence here is a notable miss, even though React's virtual-DOM diffing reduces (but doesn't eliminate) the risk class.

### 6. [Minor] Testing dimension is undecided, not flagged

Stack lists "Vitest + React Testing Library — ships with Vite scaffold, latest stable" but no AD or Consistency Convention states what must be tested (an equivalent to troll/CLAUDE.md's TEST-01/02), nor is there any mention of a CI/pre-commit gate for the new TS code (lint/typecheck/test) alongside the repo's extensive existing pre-commit-hook regime for Python/Rust. This should be Decided or explicitly listed in Deferred; right now it's simply absent.

### 7. [Minor] AD-F3 cursor semantics underspecified for the first page

"Accepts `before_ns` (cursor) + `limit`... returns rows strictly older than `before_ns`" doesn't say what the frontend sends on the very first request (no prior cursor exists yet — `Date.now()`? omit and default server-side to "most recent"?). Low risk since it's inferable, but AD-F3 calls itself "the only sanctioned loading pattern," so the boundary condition is worth pinning explicitly.

## Checklist-by-checklist notes

- **Real divergence points fixed / missed:** Fixes the dashboard's genuine split-service and unbounded-payload problems well (AD-F1/F2/F3). Misses DATA-01/gap-rendering (Finding 1) and the list-scroll-reset class (Finding 5).
- **AD enforceability / Rule↔Prevents match:** AD-F1, AD-F2, AD-F4, AD-F5 are all concretely enforceable and their Rule text matches their Prevents clause. AD-F3's Rule↔Prevents match breaks down at the boundary with `/api/snapshots` (Finding 2).
- **Technology plausibility/internal consistency:** No internal version contradictions found — each package version is cited exactly once, and the ones cross-checked against the live repo (FastAPI 0.141.1 in `troll-requirements.txt`) match the spine's "already pinned, no change forced" claim.
- **Ratifies brownfield structure:** Mostly yes — dashboard.py function names cited in AD-F1 (`_page`, `_build_chart_page_html`, `_render_live_page`, `_render_history_page`, `_history_page_from_rows`) all verified present in the live file. Falls short on DATA-01 (Finding 1) and the shared-Dockerfile convention (Finding 3).
- **Weakening inherited invariants:** None of AD-F1–F5 contradict a parent AD directly. AD-F2's "no filtering" (Finding 4) is a new constraint, not a weakening, but worth flagging as it forecloses a future fix without discussion.
- **Dimension coverage (decided/deferred/flagged):** Auth, codegen tool choice, epic-14 disposition, bot_tui cross-check, exact route mapping are all properly Deferred. Testing strategy (Finding 6) and the Dockerfile-split question (Finding 3) are the two dimensions that are silently absent rather than Decided or Deferred.
- **Two-units-diverge scenario:** Constructed in Finding 2 (candles vs. snapshots route pagination pattern) — concrete and grounded in the doc's own text.
