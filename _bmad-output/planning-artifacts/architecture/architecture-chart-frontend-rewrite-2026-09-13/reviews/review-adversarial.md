# Adversarial Review — Chart Frontend Rewrite Architecture Spine

**Target:** `_bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md`
**Method:** For each AD (AD-F1..AD-F5) and each Inherited Invariant, construct two units one level down — two engineers, or two stories/epics — that each obey the letter of every AD/convention, and check whether the outputs are still compatible. Grounded against the current, real `troll/data_api/app.py` and `troll/ml_signals/dashboard.py` (this is a rewrite of live code, not a greenfield spec, so today's actual inconsistencies are the best predictor of tomorrow's).

**Verdict:** The spine is directionally sound (paradigm, module boundaries, and the five ADs are all individually well-formed) but it pins *behavior* (compute-nothing, cursor-bounded, one-chart-instance) far more tightly than it pins *shape* (JSON envelopes, field names, error format, the REST/WS live-edge handoff, pane registry) — and the gaps it leaves are exactly the seams where two independently-built stories touch the same wire or the same chart instance. Nine concrete incompatibility scenarios found, three of them load-bearing enough to block implementation until closed (F-1, F-2, F-6).

---

## Grounding: today's code already shows the failure mode, not just a hypothetical

Before the hypotheticals — `troll/data_api/app.py` (the file AD-F1 says to *extend*, not replace) already contains three of the exact inconsistencies this review predicts would fork further under multiple engineers, all written by the same author in one sitting:

| Route | Path param name | Query params | Response envelope |
| --- | --- | --- | --- |
| `/metrics/history/{symbol}` | `symbol` | `days` | bare `list[dict]` |
| `/catalog/chart-series/{symbol}` | `symbol` | `start_ns`, `end_ns` | `dict[str, list[dict]]` |
| `/catalog/snapshots/{iid}` | `iid` | `start_ns`, `end_ns` | bare `list[dict]` |
| `/catalog/candles/{iid}` | `iid` | `start_ns`, `end_ns`, `bar_seconds` | `{"candles": [...]}` (wrapped) |

Same underlying identifier, two names (`symbol` vs `iid`); same shape of data (a time-series list), two envelopes (bare array vs wrapped object) in adjacent route handlers. This is precedent, not speculation — see Finding F-1.

The spine itself repeats this exact ambiguity in its own text: AD-F3's rule states `Binds: /api/candles/{instrument_id}`, while the Structural Seed two sections later writes the same route as `/api/candles/{iid}`. The spine hasn't settled the question it needs stories to settle consistently.

---

## Findings

### F-1 — AD-F1 ("moves ... verbatim") directly contradicts AD-F3 ("cursor-paginated, never full-range") for the one route both ADs name

**Scenario:**
- Engineer A reads AD-F1 literally: *"Its route handlers' logic ... moves into `data_api` route modules verbatim; it is a relocation, not a reimplementation."* `catalog_candles(iid, start_ns, end_ns, bar_seconds)` already exists in `data_api/app.py` today. They relocate it unchanged into `routes/candles.py`, keeping `start_ns`/`end_ns` range params.
- Engineer B reads AD-F3 literally: *"every chart-history endpoint accepts `before_ns` (cursor) + `limit` ... this is the only sanctioned loading pattern for chart history."* They rewrite the same route's signature to `before_ns`/`limit`.

Both are compliant with the AD they read closest. But `/api/candles/{iid}` is explicitly named in AD-F3's own "Binds" line — it cannot simultaneously be a verbatim relocation of a `start_ns`/`end_ns` route (AD-F1) and a ground-up rewrite to cursor semantics (AD-F3). One of these ADs is wrong for this route, and the spine gives no priority order between "verbatim" and "cursor-paginated" when they collide on the same handler.

**Close it:** Amend AD-F1 to carve out an explicit exception: *"routes already covered by AD-F3 (candle/snapshot history) are re-signatured to cursor semantics, not relocated verbatim; AD-F1's verbatim-relocation rule applies to every other route (rankings, indicators, bots, metrics)."*

---

### F-2 — No pinned pagination *envelope* — cursor exhaustion is undetectable

**Scenario:**
- Engineer A implements `/api/candles/{iid}` returning a bare array `[{...}, {...}]`, `next_before_ns` implied to be `min(ts_ns for row in result)`.
- Engineer B implements the same shape as `{"candles": [...], "next_before_ns": <int|null>}` (matching today's `{"candles": [...]}` precedent in `app.py`, extended with an explicit cursor field).

AD-F3 only pins the *request* contract (`before_ns` + `limit`, "strictly older than"); it says nothing about the *response* envelope. This isn't cosmetic: with a bare array, the frontend's `subscribeVisibleLogicalRangeChange` scroll-back handler (AD-F3's own stated trigger) has no way to distinguish "reached the start of history, stop paging" from "this page happens to be short because of an unusual bar-count in the window" — both look like `len(result) < limit`. An explicit `next_before_ns: null` / `has_more: false` signal is required for that distinction and is currently unpinned. Two `useCandles`-style hooks built against the two shapes above are simply incompatible.

**Close it:** Fix the response envelope in AD-F3 itself: `{"rows": [...], "next_before_ns": int | null}`, where `null` is the sole, unambiguous "exhausted" signal — never inferred from `len(rows) < limit`.

---

### F-3 — `before_ns=null` (first page) is unspecified

Given F-2's envelope is fixed, the *request*-side twin gap remains: what does the client send for the very first page? AD-F3 says "strictly older than `before_ns`" but never states what an absent/null `before_ns` means. Two plausible, mutually exclusive readings:
- Reading A: omit the param → server defaults to "now" (`before_ns = current wall-clock ns`), returns the most recent `limit` rows.
- Reading B: the param is required; a first-page client must compute and send `before_ns=<now>` itself.

If a future indicator-history route (F-9, below) is built by someone who assumed Reading B while `candles.py` was built under Reading A, one endpoint 422s on first load and the other doesn't — divergent behavior for what should be one shared `useCursorPagination` hook pattern.

**Close it:** State explicitly in AD-F3: *"`before_ns` is optional; omitted means 'most recent,' server-side defaulted to current time — never a required client-computed value."*

---

### F-4 — No pinned error-response shape

Nothing in AD-F5 (which only governs success-path Pydantic response models / OpenAPI codegen) or elsewhere pins what a 4xx/5xx body looks like. FastAPI's default `HTTPException` yields `{"detail": "..."}`. Two engineers:
- Engineer A (candles.py): unknown instrument → `raise HTTPException(404, "instrument not found")` → `{"detail": "instrument not found"}`.
- Engineer B (bots.py): live_paper unreachable → catches the error and returns `JSONResponse({"error": "...", "code": "BOT_UNAVAILABLE"}, status_code=502)`.

AD-F5's codegen covers success models only (a typical OpenAPI-to-TS generator does not synthesize a discriminated union of hand-varying error bodies across routes), so the frontend's shared error-toast/retry logic in React Query's `onError` has to branch on two incompatible shapes it was never told to expect. This is worse than it looks because AD-F2 already establishes that `/ws/live` forwards Redis payloads "verbatim" — meaning WS error/staleness signaling (if any) is a *third*, Redis-defined shape, not reconciled with either REST error convention.

**Close it:** Add a Consistency Convention row: all `data_api` error responses use one shape, e.g. `{"error": {"code": str, "message": str}}`, enforced via a single FastAPI exception handler (`@app.exception_handler(HTTPException)`), not per-route ad hoc bodies.

---

### F-5 — Numeric vs. string identifiers/values diverge across REST vs. WS at the exact seam a client must reconcile

AD-F2 mandates verbatim forwarding of Redis pub/sub payloads over `/ws/live`; AD-F5 mandates Pydantic-typed (hence native-JSON-numeric) response models over REST. If the Redis wire format for `snapshots:raw`/`rankings:live` stores any numeric field as a JSON string (common in pub/sub payloads serialized once upstream, e.g. price/rank as `"61090.59855"` for precision-safety — plausible given this codebase's own NAUT-01 precision paranoia), then the *same conceptual field* (e.g. price, rank) is a `number` via REST and a `string` via WS. A component that fetches REST history then splices in WS live ticks for the trailing edge (exactly the reconciliation the user's prompt flags) will get a type mismatch mid-array unless it explicitly coerces — and nothing tells either engineer that coercion is needed, because each independently only reads the AD governing their own transport.

**Close it:** Add a Consistency Convention row pinning that every field appearing on both transports (price, size, rank, ts) uses the *same* JSON type on both — which may require `/ws/live` to re-encode (not purely "verbatim forward") the specific fields a REST-fetched history array will later be spliced with. This narrows AD-F2's "verbatim" rule and should be called out as an explicit, deliberate exception in AD-F2 itself, not left for the frontend to discover.

---

### F-6 — The REST/WS boundary for the *in-progress candle* is entirely unspecified

This is the most consequential gap. AD-F3 governs closed/historical candle pages; AD-F2's `/ws/live` channels are `snapshots:raw`, `rankings:live`, `bots:status` — there is no `candles:live` channel, and no candle-specific channel is named anywhere in the spine. Yet AD-F4 requires a live-updating candlestick main pane. Two structurally different, individually-compliant implementations:
- **Engineer A:** the chart's current/forming bar is obtained by short-interval React Query refetch of `/api/candles/{iid}` with no `before_ns` (per F-3's "omitted = most recent"), polling every N seconds. Fully REST-only; AD-F2 (compute-nothing) is honored because the aggregation already happens server-side in `candle_dicts_from_snapshots` (existing `ml_signals.candles`, per AD-F2/NAUT-02).
- **Engineer B:** the chart subscribes to `snapshots:raw` over `/ws/live` and aggregates the trailing partial bar *client-side* from raw ticks, only using REST for closed history older than the current bar.

Both technically satisfy AD-F2, AD-F3, and AD-F4's text. But Engineer B's approach re-implements bar aggregation in the frontend — a second, independently-drifting candle-building implementation next to `ml_signals.candles.candle_dicts_from_snapshots`, which is precisely the SSOT-01/02 violation AD-F2 exists to prevent, just relocated to `frontend/` where AD-F2 (a `data_api`-only rule) doesn't reach. If one story ships Engineer A's pattern for the main pane and another (built independently, e.g. an OFI/OBI indicator pane per AD-F4) ships Engineer B's pattern for its own trailing data point, the two panes' time-scale-synced x-axis (mandated by AD-F4) can show a closed bar boundary in one pane and a still-forming one in the sync'd sibling pane at the same x-position — a visibly wrong chart, not just an internal inconsistency.

**Close it:** Add a new AD (or extend AD-F2) that explicitly states: *the forming/current candle bar is never client-aggregated from raw ticks in `frontend/`; it is either (a) obtained via short-poll `/api/candles` with no lower bound, or (b) delivered through a new, explicitly-named `candles:live` Redis channel relayed verbatim like the other three — pick one, name it, and state it as the only sanctioned path for the live edge of any time-series chart pane.*

---

### F-7 — No pane registry: AD-F4's "N panes" has no shared ownership contract across independently-built indicator stories

AD-F4 correctly bans multiple `createChart()` instances, but says nothing about how competing pane-adding components agree on:
- **Pane index stability:** `chart.addPane()` / pane removal renumbers subsequent panes. If an OFI-pane story and an OBI-pane story are built independently against the same `ChartPage`, and each holds a local `paneIndex` ref captured at mount time, toggling one indicator off (removing its pane) silently invalidates the other's cached index — writes land on the wrong pane.
- **Chart-instance ownership:** nothing states *which* component calls `createChart()`. Two components each written defensively (`if (!chartRef.current) chartRef.current = createChart(...)`) can still end up creating two instances if render/mount order differs from what either author assumed — individually AD-F4-compliant code, still two chart instances.
- **Pane identity for removal/toggle:** no `paneId` concept exists to look a pane up by *what it is* (`"ofi"`, `"obi"`, `"volume"`) rather than by *numeric position*.

**Close it:** Add a Consistency Convention (or tighten AD-F4) requiring: (1) exactly one hook/module (`frontend/src/components/chart/useChartInstance.ts` or similar) owns `createChart()` and is the sole caller; (2) a shared `Map<paneId, IPaneApi>` registry keyed by a stable string id, not numeric index, that every indicator-pane component reads/writes through — never `chart.panes()[n]` by raw index from component code.

---

### F-8 — Catch-all SPA fallback can mask `/api/*` route bugs, and its interaction with router registration order is unpinned

The Consistency Conventions table states "a route can never exist under both regimes" but doesn't state *how* that's enforced. FastAPI/Starlette resolve routes in registration order; a wildcard catch-all (`/{full_path:path}` → `index.html`, per AD-F1/Structural Seed's `STATIC` node) must be registered strictly after every `/api/*` router `include_router()` call. Two engineers:
- Engineer A wires `app.py` with all routers included first, catch-all mounted last — correct.
- Engineer B (adding `routes/bots.py` in a later story, per the Deferred "exact 1:1 mapping... implementation-owned" note) adds their `include_router()` call after a refactor that moved the static mount earlier in `app.py` for readability — now `/api/bots/*` 404s are swallowed by the catch-all, returning `200 text/html` (the SPA shell) instead of `404 json`. React Query's JSON parse fails opaquely client-side; the bug looks like a frontend parsing bug, not a backend routing-order regression, and nothing catches it at review time because both files individually look correct.

**Close it:** Add a Consistency Convention: `/api/*` and `/ws/live` must 404/reject with a JSON body (not fall through) even when unmatched — implemented via an explicit `/api/{path:path}` catch-all *ahead of* the SPA catch-all that always returns a JSON 404, never relying on router-registration order alone to keep the two regimes separated.

---

### F-9 — No named owner for indicator *time-series* data (OFI/OBI/microprice history for chart panes), so two indicator-pane stories can each invent a different data path

The Structural Seed's route list has `indicators.py` for *config* only (`/api/coin/{iid}/indicators` GET/PUT, matching today's `save_coin_indicator_config_handler`) and `metrics.py` for bot-performance history (`/api/metrics/history/{symbol}` — a different subsystem, `metrics_store`/SQLite, per the existing `app.py`). Neither is an obvious home for "give me 4 hours of OFI values to plot in the OFI sub-pane" — and per SIGNAL-01/the parent spine, OFI/OBI are *computed on read* from raw snapshot levels, not stored. Two AD-F4-mandated indicator-pane stories, built independently:
- **OFI-pane story:** adds `GET /api/snapshots/{iid}` (raw levels, already listed in the seed) and computes OFI client-side in `frontend/`, per SIGNAL-01's letter ("compute on read").
- **OBI-pane story:** adds a new server-side route (not in the seed, but not forbidden either) that calls `ml_signals.indicators` MultiLevelOBI server-side and returns precomputed values, because AD-F2 says routes should call existing pure functions and shape the result — which server-side computation also satisfies.

Both individually cite a real rule (SIGNAL-01 vs. AD-F2) in support of opposite architectures for sibling panes on the *same chart*. One ships a heavier `/api/snapshots` payload plus client CPU cost per pane; the other ships a lighter precomputed series. Neither is wrong per the text, but they're incompatible patterns for what should be one shared "indicator series" abstraction feeding AD-F4's per-pane components uniformly.

**Close it:** Either (a) move "which layer computes indicator values shown in a chart pane — client from raw snapshots, or server via `ml_signals.indicators`" from unstated to explicitly decided (pick one, state it in a Consistency Convention row), or (b) if intentionally left flexible, require every indicator-pane component to go through one shared `useIndicatorSeries(iid, kind)` hook whose *internal* strategy can vary by `kind` but whose external contract is uniform — so future panes don't each reinvent the fetch/compute split.

---

## Deferred-section risk re-check

Per the user's brief, checking whether any Deferred item, left deferred, lets two units diverge in a way that matters:

| Deferred item | Risk if left deferred |
| --- | --- |
| Exact 1:1 route mapping | Low — AD-F1's verbatim-relocation rule (once F-1's exception is added) bounds this; cosmetic file-organization choice only. |
| OpenAPI→TS codegen tool | Low — AD-F5 already pins the requirement (generated, never hand-duplicated); tool choice doesn't affect contract shape. |
| Auth/access control | Low for now — correctly scoped as personal/single-user behind SSH tunnel, matches SEC-01 posture. |
| epic-14 disposition | Low — process/planning concern, not a runtime contract. |
| `bot_tui` cross-check (SSOT-04/05) | **Medium, not low** — this is a real recurrence risk, not just paperwork: `troll/CLAUDE.md`'s SSOT-04 already documents this exact class of miss happening in production ("the page jumps back to top" bug shipped twice per TUI-01). Deferring "which story updates `bot_tui`" per-story, with no tracking mechanism named, repeats the pattern SSOT-04 was written specifically to stop. Recommend at minimum a checklist line added to the epics/stories template, not left purely to individual story author discipline. |
| **Not listed as Deferred at all** (the actual gap): error-response shape, pagination envelope, live-candle-edge ownership, pane registry | **These aren't in the Deferred section, which is worse than being deferred** — an explicitly deferred item at least gets tracked and revisited; these four are simply absent from the document, so nothing flags them for the epics/stories pass to resolve before two stories collide on them. This is the spine's most important gap: promote F-2, F-4, F-6, and F-7 out of "invisible" and into either a decided AD/convention (preferred, given their blocking severity) or, at minimum, an explicit Deferred entry with an owner and a "must resolve before two chart-pane stories ship in parallel" note. |

---

## Summary of recommended spine edits

1. **AD-F1** — add explicit carve-out: routes covered by AD-F3 are re-signatured, not relocated verbatim.
2. **AD-F3** — pin response envelope (`{"rows": [...], "next_before_ns": int | null}`) and first-page semantics (`before_ns` omitted = most recent).
3. **New Consistency Convention row** — uniform error-response shape (`{"error": {"code", "message"}}`) via one FastAPI exception handler.
4. **AD-F2** — carve out explicit exception for fields shared between REST and WS (same JSON type both places), narrowing "verbatim forward."
5. **New AD or AD-F2 extension** — name the single sanctioned mechanism for the live/forming candle bar (short-poll REST vs. a named `candles:live` WS channel); explicitly ban client-side bar aggregation from raw ticks in `frontend/`.
6. **AD-F4** — add shared pane-registry requirement: one owner for `createChart()`, one `Map<paneId, IPaneApi>` keyed by stable string id, never raw numeric pane index from component code.
7. **New Consistency Convention row** — `/api/*` and `/ws/live` always reject unmatched paths with JSON, via an explicit `/api/{path:path}` 404 handler registered ahead of the SPA catch-all — never relying on include-order alone.
8. **New Consistency Convention row (or explicit Deferred entry with owner)** — decide, or explicitly punt with a tracking owner, whether indicator-pane series data is computed client-side from raw snapshots or served precomputed — and require a shared `useIndicatorSeries` hook either way.
9. **Deferred section** — tighten the `bot_tui` cross-check entry from a passive note into an epics/stories-template checklist item, given SSOT-04's documented history of this exact miss shipping twice already.
