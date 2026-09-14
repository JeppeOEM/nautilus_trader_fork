# PRD Quality Review — Chart Frontend Rewrite

## Overall verdict

This is a well-constructed internal-tool PRD with a specific, non-generic Vision, honest scope boundaries, and mostly-testable FRs — it does not read as template filler. It has one broken FR (no testable consequences), one unexplained ID gap in the exact FR-38–FR-46 range this review was asked to check, a glossary term used but never defined, and a real risk (16-color-only palette on a data-dense chart UI) that the PRD asserts as a hard constraint without acknowledging the trade-off. None of these require restructuring the document — they're fixable in a single editing pass — but they should be closed before this feeds the epics/stories pass.

## Decision-readiness — adequate

Most decisions in this PRD are stated as decisions with visible cost, not smoothed to neutral. NFR-A explicitly declines to set a numeric performance target and says so ("qualitative by deliberate choice, not an oversight" — line 150), which is the kind of honest trade-off statement the checklist wants. FR-40's Out of Scope note (line 89) is a real boundary with a stated reason (indicator panes stay aligned to the candlestick pane's window rather than independently paginating). The Non-Goals section (§5) reads as genuine exclusions, not safe ones (e.g., "New capabilities beyond parity" directly forecloses the most tempting scope-creep vector for a rewrite).

Where it falls short: FR-46 / Aesthetic and Tone locks the *entire* color system — background, text, borders, semantic states, and "any chart series coloring" — to 16 ANSI/VGA colors (lines 141, 160), with zero acknowledgment that a multi-pane chart (candlesticks + OFI + order-book imbalance + volume + microprice + spread, per FR-39) may need to distinguish more simultaneous series/states than 16 colors comfortably support, several of which are known-low-contrast against a dark ground (e.g., dark gray, brown/yellow, light gray). This is exactly the kind of constraint a decision-maker pushing back would object to ("what happens when the chart needs a 6th indicator color and they're all used for UI chrome already?"), and the PRD doesn't surface it as a trade-off anywhere — not in Open Questions, not in the Aesthetic section's "Explicitly not required" carve-out. It should at minimum be flagged as an accepted risk.

### Findings
- **medium** 16-color palette constraint vs. chart-density trade-off unacknowledged (§FR-46, Aesthetic and Tone) — FR-39 stacks up to five simultaneous indicator panes, but FR-46 restricts *all* series coloring to the same 16-color set used for UI chrome, text, and semantic states, with no discussion of collision risk or fallback (e.g., pattern/dash differentiation). *Fix:* add one sentence acknowledging the constraint and either accept the risk explicitly or note a fallback (line style, pane separation) for series-color collisions.

## Substance over theater — strong

No persona theater (UJs are explicitly, self-awarely restated JTBDs per the "Lighter" scope dial, §2.3 line 32 — this is the correct move for a single-operator tool and the PRD says so rather than padding in named personas). No innovation theater — the rewrite doesn't claim novelty, it names a concrete production bug (story 14.3) as its trigger. No NFR boilerplate — NFR-A, NFR-B, NFR-C, NFR-D are each specific to this system, not "must be scalable/secure." Vision (§1) quotes a real incident and a real constraint (SSH tunnel) rather than reading as swappable boilerplate — this would not fit any other PRD unchanged.

## Strategic coherence — strong (with one traceability gap)

The thesis is clear and consistently followed: replace ad-hoc per-feature JS/Plotly wiring with one library-native sync mechanism, because the ad-hoc approach is what produced the bug that triggered this epic. Feature prioritization (§4) and MVP scope (§6) both track that thesis — parity-first, foundation-first, no superset. Counter-metric SM-C1 is a real counterbalance (correctness/honesty vs. speed), not decorative.

The gap: Success Metrics (§7) trace back to only a subset of FRs. SM-1 validates FR-38, FR-39, FR-44, FR-45, NFR-D; SM-2 validates FR-40, NFR-A; SM-C1 validates NFR-C. **FR-41 (live edge consistency), FR-42 (per-coin indicator config), and FR-46 (terminal visual identity — explicitly called "a first-class product requirement," line 157) are never referenced by any Success Metric.** For FR-46 in particular, a requirement the PRD elevates to first-class status having zero validation path is a real thesis-coherence gap, not just a bookkeeping slip.

### Findings
- **medium** FR-41, FR-42, FR-46 untraced to any Success Metric (§7) — SM-1's FR list is a partial sample of the parity checklist and omits three FRs, including the aesthetic requirement the PRD calls first-class. *Fix:* either broaden SM-1's "Validates" list to cover all FRs (it's a parity checklist, so arguably should), or add explicit sub-checks for FR-41/42/46.

## Done-ness clarity — thin (one broken FR)

Most FRs are held to a real testable bar: FR-38, FR-39, FR-40, FR-41, FR-42, and FR-46 all carry "Consequences (testable)" bullets that are genuinely falsifiable (e.g., FR-40's "operator never observes a request whose response exceeds one page's worth of bars"; FR-46's "no page introduces a color outside that set"). This is the dimension the checklist says to be most unforgiving on, and on that basis:

**FR-45 (Docs page, line 129-131) has no "Consequences (testable)" subsection at all** — it is a bare statement ("The operator can view the existing documentation content at its current URL shape, rebuilt on the new stack") with nothing an engineer or QA pass could verify against beyond "does a page exist." Every sibling FR in this document has at least one testable consequence; FR-45 is the sole exception, and it's not because the requirement is trivial — "current URL shape" and "existing... content" both imply verifiable claims (which URL, which content, parity with what) that simply weren't written down.

FR-44 (31-day metrics history) has exactly one consequence, and it's about gap-rendering, not about the core claim (that all ranking-input metrics render correctly over 31 days) — thin but not broken, since NFR-D (feature parity) implicitly backstops it.

### Findings
- **high** FR-45 has zero testable consequences (line 129-131) — the only FR in the document missing this subsection entirely. *Fix:* add at least one consequence, e.g. "the docs page is reachable at the same path `dashboard.py` served it at" and/or "content matches the current docs page section-for-section, verified during the parity pass (SM-1)."

## Scope honesty — strong

Non-Goals (§5) do real exclusionary work rather than listing the obvious (e.g., "Bots / `live_paper` UI" is called out with the specific reason it was almost included by mistake — line 171 — which is exactly the kind of honest correction-in-the-open the checklist wants, not a smoothed-over retcon). The Assumptions Index (§9) correctly reports zero open `[ASSUMPTION]` tags and the inline text supports that — no dangling tags found. The one `[NOTE FOR PM]` (§6.2, line 192) sits at a real tension (epic-14's stories becoming moot) rather than at a safe checkpoint. Open-items density (3 Open Questions, 0 open assumptions, 1 NOTE FOR PM) is appropriately low for an internal-tool PRD that isn't a green-light-to-build gate on every micro-decision — though see Mechanical notes on Open Question #3's genuineness.

## Downstream usability — thin

This PRD explicitly feeds a `bmad-create-epics-and-stories` pass (§0), so ID continuity and glossary completeness matter more than they would for a standalone document — and both have gaps (see Mechanical notes for the FR-43 gap specifically, since the task scope called that range out directly). Section cross-referencing otherwise works well: FR ranges are cited correctly elsewhere (e.g., §6.1's "FR-39–FR-42"), and most Glossary terms (Facade, Pane, Cursor pagination, Live edge, Staleness) are used consistently and match their definitions everywhere they appear in §4.

One term is not extractable cleanly, however: **"Ranking Mode"** appears twice as a capitalized, apparently load-bearing concept (§4.1 description, line 53: "sorted by the active Ranking Mode (volume or volatility)"; FR-38 consequence, line 62: "beyond what the active Ranking Mode already dictates") but is never defined in the Glossary (§3) or anywhere else in the document. A reader sourcing only this PRD cannot tell whether Ranking Mode is operator-configurable from this UI, inherited read-only from `ranking_engine`, or something else — which matters directly for FR-38's scope (does this epic build a mode switcher, or just render whatever mode is active?).

### Findings
- **medium** "Ranking Mode" used but undefined (lines 53, 62) — capitalized like a Glossary term, treated as established, but absent from §3 Glossary. *Fix:* add a Glossary entry, or if it's purely a `ranking_engine`-owned concept this epic only reads, say so explicitly in FR-38's consequences (is there a mode-switch control in scope or not?).

## Shape fit — strong

Correctly calibrated to a single-operator internal tool: UJs are self-consciously "restated JTBDs rather than named-persona narratives" (§2.3) rather than forced into consumer-product UJ theater; Success Metrics are operational (feature parity, perceived speed) rather than engagement metrics that wouldn't make sense for a one-user tool; the document doesn't over-invest in stakeholder-alignment scaffolding a solo-operator project doesn't need. This is the right amount of formality for the stated stakes tier.

## Mechanical notes

- **FR numbering gap: FR-43 is missing.** The FR sequence runs FR-38, FR-39, FR-40, FR-41, FR-42 (§4.2), then jumps directly to FR-44 (§4.3) — FR-43 does not exist anywhere in the document, including in the §6.1 MVP scope range citations ("FR-39–FR-42" then separately "FR-44"). Nothing in the document explains the gap (no "FR-43 removed/merged" note), which is inconsistent with how the PRD documents its other edits (e.g., the Bots/live_paper Non-Goal explicitly notes it was "corrected mid-session," line 171). Since this PRD feeds a downstream epics/stories pass that will likely use FR IDs as story keys, an unexplained gap invites a future reader to wonder if a requirement was silently dropped. *Fix:* either renumber contiguously, or add a one-line note (e.g., in §9 Assumptions Index or a footnote) stating why FR-43 doesn't exist (merged into FR-42? cut during review?).
- **Section numbering gap: §2.2 is missing.** §2 Target User goes from "### 2.1 Jobs To Be Done" directly to "### 2.3 Key User Journeys" — no 2.2 section exists. Likely a removed Persona subsection (consistent with the "Lighter" UJ treatment noted in §2.3), but, as with FR-43, this isn't stated anywhere.
- **Glossary drift:** otherwise clean — Facade, Pane, Cursor pagination, Live edge, Ranking, and Staleness are all used consistently with their §3 definitions everywhere they recur in §4 and the NFRs. "Ranking Mode" is the one gap (see Downstream usability finding above).
- **Assumptions Index roundtrip:** clean. §9 claims zero open `[ASSUMPTION]` tags remain, and no inline `[ASSUMPTION]` tag exists anywhere in the document — statement and body agree.
- **Open Question #3 genuineness:** §8's item 3 ("epic-14's formal closure... a sprint-status housekeeping item, not resolved here") is already effectively answered in the same sentence it's posed in, and duplicates the §6.2 `[NOTE FOR PM]` almost verbatim. It reads more like a cross-reference than a genuinely open question. Low-severity — doesn't block anything, but could be cut or merged into the §6.2 note rather than double-counted as an Open Question.
- **UJ protagonist naming:** all three UJs use "The operator" consistently (not a named persona) — correct per the Shape fit calibration for a single-operator tool, and internally consistent across UJ-1/2/3.
