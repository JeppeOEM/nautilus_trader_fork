# PRD Quality Review — dYdX Signal Research & Trading Platform

## Overall verdict

This is a coherent, appropriately-scaled solo-hobby PRD with a real thesis (rank → research once in Jupyter → same code in backtest and live) and unusually clean traceability (contiguous FR/UJ/SM IDs, a glossary that's actually used consistently, an honest Non-Goals list). The weak spot is done-ness: the FR that carries the whole "coin ranking" thesis (FR-6) never defines how HFT and structural indicators combine into a single rank, and four FRs (5, 8, 9, 13) skip the "Consequences (testable)" pattern the other eleven FRs establish, leaving those as unbounded intentions rather than a build contract. Nothing here is broken, but an engineer picking up FR-6 or FR-9 first would have to invent the missing part themselves.

## Decision-readiness — adequate

The one substantive tension in the document — narrowing AD-8's blanket `TradingNode` ban to permit a paper-trading module (§4.5) — is surfaced with real justification (what the prior incident was, why the distinction is load-bearing) rather than smoothed over. That's the PRD doing its job.

Everywhere else, though, the document reports zero unresolved tension: §8 Open Questions is "None outstanding," §9 Assumptions Index is "None outstanding," and there isn't a single `[NOTE FOR PM]` callout anywhere in the text. For a PRD whose own title still carries "*Working title — confirm.*" (line 9), a literal open item exists and isn't tracked in §8 — the zero count is not quite true. That's a small thing on its own, but it's a symptom: real ambiguity likely exists (e.g., how ranking combines heterogeneous indicators — see Done-ness below) but isn't flagged as a decision point; it's just left unstated inside an FR.

### Findings
- **medium** Untracked open item in title (title line 9, § 8) — The title bears "*Working title — confirm.*" but §8 Open Questions says "None outstanding." An unresolved item exists outside the tracking mechanism meant to catch it. *Fix:* Either resolve the title now, or add it to §8 as a real open question.
- **low** No `[NOTE FOR PM]` callouts anywhere — Given the rubric's guidance that these belong at real tensions, and one clearly exists (FR-6's ranking-combination method, see Done-ness), the absence suggests tensions are being left implicit in FR prose rather than flagged. *Fix:* Add a `[NOTE FOR PM]` at FR-6 naming the undecided combination/weighting approach as a deferred decision, if it truly is deferred.

## Substance over theater — strong

No persona zoo — the PRD explicitly dials down UJs to one-liners "since solo hobby project, single operator role, no multi-stakeholder UX" (§2.3 preamble), which is honest calibration, not theater. The Vision (§1) contains specific, falsifiable commitments (Jupyter-only indicator authorship, watchlist-driven multi-coin backtests, a non-profitable "integration proof" Dummy Strategy) rather than a swappable mission statement. There is no dedicated NFR section with boilerplate ("must be scalable/secure") to flag — its absence is appropriate at this stakes level rather than a gap. No differentiation/competitive-positioning theater. Nothing here reads like furniture.

## Strategic coherence — strong

The thesis is explicit and load-bearing: build every indicator once, in Jupyter, and run the identical code in ranking, backtest, and live (FR-10, restated in FR-14's consequences). Feature ordering in §4 follows the thesis (collect → rank → research once → backtest at scale → prove it live), not "what's easy first." SM-1 directly measures the thesis (code reuse with zero reimplementation) rather than an activity metric; SM-2 protects against a named historical regression (`openInterest`-vs-`volume24H`); SM-3 (paper-strategy uptime) is an operational metric appropriate to a solo-operator MVP scope kind, not a vanity DAU-style stand-in. A counter-metric (SM-C1: don't trade coverage for integrity) is present and explicitly counterbalances SM-2. This reads as a thesis with prioritized features, not a backlog with headings.

## Done-ness clarity — thin

Most FRs (1–4, 6, 7, 10, 11, 12, 14, 15 — 11 of 15) follow a consistent "**Consequences (testable)**" pattern with genuinely verifiable conditions (e.g., FR-4: "gap... appears as a visible break... never a flat/interpolated line"; FR-15: real-money mode "is not reachable by any default or accidental config state"). That's the PRD's done-ness bar working as intended where it's applied.

It isn't applied everywhere, and the gaps land on FRs central to the thesis:

### Findings
- **high** FR-6 never defines how indicators combine into one rank (§4.2, FR-6) — "ranks all subscribed coins using a combination of HFT indicators... and slower/structural indicators... refreshed continuously" states the ranking's *inputs* but not the *rule* — no weighting, scoring function, or tie-breaking method, and no consequence tests it. An engineer cannot know when the ranking is "done" versus merely "does something with these seven-plus inputs." This is the FR the entire Coin Ranking feature (and UJ-1) depends on. *Fix:* Either specify the combination approach (even at the level of "weighted z-score sum, weights configurable") or explicitly flag it as an open research question deferred to implementation, with a `[NOTE FOR PM]`.
- **medium** FR-8 has no Consequences block (§4.2, FR-8) — "The user can inspect how a coin's ranking evolved over time" has no testable condition: what time range, what granularity, what UI/API surface counts as satisfying this. *Fix:* Add at least one verifiable consequence (e.g., "ranking history is queryable for any past timestamp within the collector's retention window").
- **medium** FR-9 has no Consequences block and "following the conventions... not a custom notebook framework" is unbound (§4.3, FR-9) — there's no way to check compliance; "conventions" isn't pointed at a specific example notebook, directory, or pattern (e.g. Nautilus's `docs/tutorials` notebooks). *Fix:* Name the specific reference notebook(s)/pattern being followed, and add one concrete consequence (e.g., "a new indicator notebook imports the same class used in backtest, no notebook-local reimplementation").
- **low** FR-5 and FR-13 state a testable claim in the FR line itself but skip the "Consequences (testable)" heading used elsewhere (§4.1 FR-5, §4.4 FR-13) — purely a formatting inconsistency since both FR bodies are already fairly verifiable, but it breaks the pattern the other 11 FRs establish, which matters for downstream story-extraction tooling expecting a consistent structure. *Fix:* Add the heading for consistency, even if the consequence list is short.

## Scope honesty — adequate

§5 Non-Goals is concrete and does real work (no auth, dYdX-only, no mobile, paper-only in v1, no polished UI) rather than a token section. The real-money deferral is handled honestly — §4.5's Notes state plainly that "No additional safeguards beyond FR-15's config-gated isolation are defined for v1," which is a de-scoping made explicit rather than silently assumed. §9's Assumptions Index correctly roundtrips: the two assumptions it names (FR-9's Jupyter convention, FR-13's dynamic-Watchlist scope) were folded into FR text and there are no dangling inline `[ASSUMPTION]` tags left unindexed.

The only flag is the same one already raised under Decision-readiness: the title's "confirm" marker isn't reflected as an open item in §8, so the "None outstanding" claim isn't fully accurate. At hobby stakes this is low-severity, but it's worth fixing since it's the one place the PRD's own bookkeeping is inconsistent with itself. (See Decision-readiness finding above — not repeated here as a separate item.)

## Downstream usability — strong

Glossary (§3) terms — Snapshot, Raw Delta Capture, Coin Ranking, Watchlist, Indicator/Signal, Gatekeeper, Dummy Strategy — are each used consistently in the FRs that reference them, capitalized at point of defined-term use. FR IDs (FR-1–FR-15), UJ IDs (UJ-1–UJ-3), and SM IDs (SM-1–SM-3, SM-C1) are contiguous with no gaps or duplicates. Cross-references resolve: "per FR-10" (FR-14), "see FR-15" (§5), "(§4.5)" (glossary's Dummy Strategy entry) all point at real sections. Brownfield references check out — cross-checked against the architecture spine: AD-1 through AD-8 all exist there with matching descriptions, and the PRD's characterization of AD-8's narrowing (§4.5) is accurate to the spine's own "Explicitly not banned" clause. Each UJ (§2.3) has a named protagonist ("Builder") carrying context inline, appropriate to the lightened UJ format the PRD deliberately chose.

## Shape fit — strong

This is correctly shaped as a single-operator capability spec: no multi-stakeholder UJs, personas dialed down explicitly and self-consciously ("Lighter scope dial — solo hobby project... One line per JTBD rather than full narrative UJs," §2.3), SMs mixing user-facing (SM-1, SM-2) and operational (SM-3) framing appropriate to a solo-operator tool. Brownfield handling is accurate (see Downstream usability). Nothing here is over-formalized for the stakes, and nothing under-formalized relative to what a chain-top PRD (this feeds architecture/stories, per the existing architecture-spine reference in §0) needs downstream.

## Mechanical notes

- **Section numbering gap**: §2 jumps from "2.1 Jobs To Be Done" directly to "2.3 Key User Journeys" (prd.md lines 21, 28) — no 2.2 exists. Likely a dropped Persona subsection whose number was never reclaimed. Low-impact but worth a renumber for cleanliness.
- **Casing drift on glossary terms**: "Watchlist" (capitalized, glossary term) appears alongside lowercase "watchlist" used as a common noun in prose (lines 17, 33, 98, 102) — this reads as intentional (capitalized at defined-term references, lowercase as backreference) rather than true drift, but is worth a normalization pass if the PRD is machine-parsed downstream. Same pattern for "Dummy Strategy" vs. lowercase "dummy strategy" (line 34, UJ-3).
- **Assumptions Index roundtrip**: clean — no unindexed inline `[ASSUMPTION]` tags exist, and the two historical assumptions named in §9 are consistent with the FR text they describe.
- **ID continuity**: clean across FR/UJ/SM numbering; no duplicates or unresolved cross-refs found.
