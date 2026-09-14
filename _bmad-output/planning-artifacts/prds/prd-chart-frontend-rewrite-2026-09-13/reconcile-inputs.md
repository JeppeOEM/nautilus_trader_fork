# Reconciliation: PRD vs. Architecture Spine vs. Original/Corrected User Request

Scope: `prd.md` (2026-09-13) checked against `ARCHITECTURE-SPINE.md` (status: final) and the
user's original verbatim request plus two later verbatim corrections from this session.

---

## 1. Architecture Spine (AD-F1–AD-F7) vs. PRD

| AD | User-facing implication | PRD coverage | Verdict |
|---|---|---|---|
| AD-F1 (single facade, dashboard.py deleted) | Feature parity before old surface is removed | Vision, NFR-D, MVP Scope | Covered |
| AD-F1a (SPA static serving mechanics) | None (backend-internal) | N/A | Correctly omitted |
| AD-F2 (facade computes nothing, one write path) | Per-coin indicator config persists | FR-42 | Covered |
| AD-F3 (cursor pagination, binds **both** `/api/candles` AND `/api/snapshots`) | Incremental scroll-back for candles *and* for Lines-mode order-book history | FR-40 covers candles only; **Lines mode itself is never mentioned** (see Finding 1) | **Partial — see Finding 1** |
| AD-F4 (one chart instance, native pane sync) | Moving one pane moves all panes | FR-39 | Covered, matches original request's OBI-sync clause verbatim |
| AD-F5 (generated contract types, never hand-duplicated) | Engineering-only anti-drift discipline, not a user-facing capability | Not reflected in any FR/NFR | Reasonable to omit — flagged only for completeness, not a real gap |
| AD-F6 (never flatline a gap, never show stale as live) | Honest rendering of missing/stale data | NFR-C, FR-38 consequence, FR-44 consequence | Covered |
| AD-F7 (one sanctioned live-candle path) | Live edge never desyncs from history | FR-41 | Covered |

**No direct contradictions** found between the PRD's stated requirements and any AD's rule. The
one real structural gap is Finding 1 below: an AD the spine treats as first-class (its own route
file, its own named endpoint, "SAME cursor contract as candles.py") never surfaces as a PRD
feature or FR, only as an implicit case under the blanket NFR-D parity clause.

---

## Finding 1 (major) — "Lines mode" is a real, distinct existing capability with zero FR coverage

`ml_signals/dashboard.py`'s current `/chart/{id}` page has a **Candles/Lines toggle**
(`btn-candles`/`btn-lines`), not just a candlestick view:

- Lines mode renders raw bid/ask/mid/microprice/price as line series (`scattergl`, `mode:"lines"`),
  backed by its own endpoint (`/data/coin/{id}/lines`, `_live_lines_json`/`_historical_lines_json`,
  Story 8.1) — a *different* data source and rendering path from Candles mode, not a
  reskin of it.
- Lines mode has its own feature on top: **click-to-diff** (bid/ask/mid/micro/price A/B point
  comparison), explicitly scoped as "Lines-mode-only" in the dashboard source.

The architecture spine treats this as first-class: AD-F3 explicitly names
`/api/snapshots/{instrument_id}` as "Lines-mode order book history" and binds it to the *same*
cursor-pagination contract as candles "without exception" ("Naming only `/api/candles` in an
earlier draft of this AD was itself a bug"); the Structural Seed gives it its own route file
(`routes/snapshots.py — /api/snapshots/{iid} — Lines-mode history, SAME cursor contract as
candles.py`).

The PRD never mentions Lines mode, the Candles/Lines toggle, or click-to-diff anywhere — not in
§4.2's FR-39–FR-42 (which enumerate candlestick-page capabilities in detail: sync, incremental
load, live edge, per-coin config), not in the Glossary, not in Non-Goals. It is covered only
implicitly by NFR-D's blanket "every capability in today's dashboard.py has a working equivalent."
That's a real risk: FR-39 through FR-42 will directly drive the epics/stories breakdown for the
chart page, and nothing in that FR list tells a story-writer Lines mode and click-to-diff exist —
they're one blanket-NFR sentence away from being silently dropped, which is exactly the class of
loss NFR-D itself exists to prevent. Recommend an explicit FR (or an FR-39 sub-bullet) naming
Lines-mode + click-to-diff before the epics pass.

## Finding 2 (moderate) — FR-40's indicator-pane Out-of-Scope note is ambiguous about scroll-back behavior

FR-40's Out-of-Scope clause: "Loading indicator-pane history further back than the candlestick
pane's currently-loaded range is not required — panes stay aligned to the candlestick pane's
loaded window."

Under SIGNAL-01/AD-F4, the indicator panes (OFI, OBI, microprice, spread) are all derived from the
same `/api/snapshots` data AD-F3 requires to be cursor-paginated "without exception." The PRD's
note is ambiguous on the actual runtime behavior when the user drags the candlestick chart back
past what the indicator panes have loaded: does each pane also fire its own paginated
`/api/snapshots` fetch to keep pace (consistent with FR-39's "every other pane moves in lockstep,
in the same interaction"), or do panes simply stop rendering data past their initial window while
the candlestick keeps scrolling? The former is required to satisfy FR-39 and the user's original
"when moving the candlestick chart you also move the order book imbalance charts" — the latter
would produce indicator panes going visibly blank mid-scroll, which is also arguably an AD-F6/
DATA-01 "gap" the UI must render honestly rather than silently. This needs to be resolved as an
explicit rule (most likely: indicator panes *do* page in lockstep via the same cursor mechanism)
before it reaches the epics pass, not left as an "out of scope" line that reads as permission to
skip it.

## Finding 3 (moderate) — Mobile chart interaction is not tested by any Journey

Correction 2 states mobile layout "matter[s], make it feature parity" — not just "legible," full
parity. NFR-B ("every page is usable on both desktop and phone/tablet viewports") and UJ-3 ("checks
the dashboard from their phone... glances at the rankings table, and it's legible without
pinch-zooming") are the only mobile-facing requirements in the PRD, and UJ-3 only exercises the
*rankings table* on a phone. No Journey exercises the single-coin chart page — by far the most
interaction-heavy surface (drag-to-pan, cursor-pagination scroll-back, multi-pane sync) — on a
touch device. FR-39/FR-40's "lockstep pan/zoom via the library's native sync" and "scroll back
triggers cursor-paginated fetches" are both written in mouse-drag/scroll terms; nothing confirms
these interactions are validated (or even expected to work well) via touch gestures on the
literal use case ("I do all of the above from a phone... occasionally") the PRD's own §2.1 JTBD
list states. Given "feature parity" was explicitly demanded for mobile, recommend adding a Journey
or FR consequence that the chart page's pan/zoom/scroll-back specifically works on touch, not just
that the page "is usable."

## Finding 4 (minor, traceability only) — 31-Day Metrics History page vs. "only consist of the same things as before"

Correction 1, verbatim: "the frontend still only consist of the same things as before, docs,
rankings, single coin chart" — three items named. The PRD ships four pages, adding "31-Day Metrics
History" (§4.3). Per the task brief, this was confirmed separately in the architecture-pass
discussion, and it does not actually contradict the user's framing substantively — `dashboard.py`
already has this page today (`_render_history_page`), so it genuinely *is* "the same as before,"
just not one of the three examples the user happened to name in that sentence. The gap is
purely one of documentation: the PRD states the page as fact (§4.3's description, MVP Scope) with
no cross-reference to the confirmation that resolved the apparent mismatch with Correction 1. A
future reader who only has the PRD plus the quoted correction (not this session's full history)
would see an unexplained fourth page. Recommend one clause in §4.3 or the Assumptions Index noting
this was confirmed with the user as carried-forward-parity, not a new addition.

## Finding 5 (minor) — AD-F5 (generated contract types) has no FR/NFR presence

Pure engineering-integrity constraint (prevents frontend/backend type drift), not a user-facing
capability or behavior, so its absence from the FR/NFR list is defensible. Noted for completeness
only, not a required fix — it's arguably closer to a DATA-02-flavored "no mysteries" concern
(contract drift is exactly the kind of silent-failure class troll/CLAUDE.md DATA-02 cares about)
and could warrant a one-line NFR, but is not load-bearing enough to block this PRD.

---

## 2. Original User Request — Clause-by-Clause

| Clause (verbatim) | Coverage |
|---|---|
| "complete refactor of the frontend" | Vision, full SPA rewrite — covered |
| "decoupled from the backend" | Structural (architecture's Deployment & Environments section explicitly addresses this); PRD doesn't restate it as its own FR/NFR, but PRD explicitly defers architectural shape to the spine (§0) — not a gap |
| "written in react with react query" | Deferred to spine's Stack table by design (§0) — not a gap |
| "needs to use lightweight charts" | Same — deferred to spine (AD-F4, Stack table) — not a gap |
| "candlestick chart with indicators needs to mimic tradingview... browse entire history... bit by bit" | FR-39, FR-40 — covered. Note: PRD correctly narrows "mimic tradingview" to the *loading-mechanics* meaning (FR-40's title: "TradingView-style history loading"), keeping it separate from the later ANSI/terminal visual-identity correction, which supersedes any visual "look like TradingView" reading. No contradiction. |
| "backend also needs a refactor so i can serve the frontend better" | MVP Scope: "The backend refactor (data_api as the sole Facade)" — covered |
| "speed... blazingly fast loading speeds and navigation" | NFR-A — downgraded to qualitative-only per Correction 2 ("No concrete target"); consistent, not a flattening — the user themselves walked this back |
| "charts to be synced so when moving the candlestick chart you also move the order book imbalance charts" | FR-39 (AD-F4) — covered, though see Finding 2 for an edge case (scroll-back-past-loaded-window behavior) this clause implies but the PRD leaves ambiguous |

No clause from the original request is unaddressed outright. The one real erosion is the edge
case in Finding 2, where the literal promise of "you also move the order book imbalance charts"
during a deep scroll-back is not obviously guaranteed by FR-40's wording.

---

## 3. Later Corrections — Verification

**Correction 1** ("bots... in the tui... frontend still only consist of the same things as
before, docs, rankings, single coin chart"):
- Bots excluded: PRD Non-Goals explicitly states this, with the note "(Corrected mid-session
  during the architecture pass, after an early draft mistakenly included it.)" — correctly
  captured, including the correction's own provenance.
- Page scope: three pages named, PRD ships four (see Finding 4) — traceability gap, not a
  substantive contradiction.

**Correction 2** ("No concrete target, but it is requirement in the architecture, phone mobile
layout matter, make it feature parity and fast. Style... terminal like with terminal font and
oldschool ascii art vibe using classic vga 16 colors palette of ansi art."):
- No concrete performance target: NFR-A, matches exactly, including "by deliberate choice, not an
  oversight" framing in §9 Assumptions Index.
- Phone/mobile layout + "feature parity": NFR-B covers layout usability; feature-parity-on-mobile
  specifically for the chart page's interactive surface is under-specified — see Finding 3.
- Terminal font + ANSI/VGA 16-color palette + ASCII-art vibe: §Aesthetic and Tone is thorough and
  accurate — names the correct 16-color VGA/ANSI palette, a DOS/BIOS bitmap font direction (not
  a modern coding monospace, correctly distinguished), box-drawing ASCII-art motif, and explicitly
  confirms CRT/scanline effects are *out* of scope, matching the "Explicitly not required" note.
  This is the strongest, most fully-realized section of the PRD relative to its source — no gap
  found here.

---

## Summary of Findings by Severity

1. **Major** — Lines-mode chart view + click-to-diff (an existing, architecturally-named
   capability) has no FR coverage, only an implicit blanket-NFR mention. Risk: dropped silently
   in the epics/stories pass.
2. **Moderate** — FR-40's "indicator panes stay aligned, don't load further" note doesn't specify
   whether panes co-page with candlestick scroll-back or go blank — ambiguous against FR-39's
   lockstep-sync promise and the user's original OBI-sync clause.
3. **Moderate** — No Journey/FR consequence validates the chart page's touch/mobile interaction,
   despite "feature parity" being explicitly demanded for mobile in Correction 2.
4. **Minor** — 31-Day Metrics History page's reconciliation with Correction 1's three-page list
   isn't cross-referenced in the PRD text itself.
5. **Minor** — AD-F5 (generated contract types) has no FR/NFR presence; defensible omission,
   noted for completeness.
