---
stepsCompleted: [step-01-document-discovery, step-02-prd-analysis, step-03-epic-coverage-validation, step-04-ux-alignment, step-05-epic-quality-review, step-06-final-assessment]
documentsIncluded:
  - _bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md
  - _bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/DESIGN.md
  - _bmad-output/planning-artifacts/ux-designs/ux-nautilus_trader_fork-2026-07-24/EXPERIENCE.md
  - _bmad-output/planning-artifacts/epics.md
---

# Implementation Readiness Assessment Report

**Date:** 2026-07-24
**Project:** nautilus_trader_fork

## Document Inventory

**PRD:**
- Whole: `prds/prd-nautilus_trader_fork-2026-07-01/prd.md` (status: final, updated 2026-07-24)

**Architecture:**
- Whole: `architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md` (status: final, updated 2026-07-24)

**UX Design:**
- Pair: `ux-designs/ux-nautilus_trader_fork-2026-07-24/DESIGN.md` + `EXPERIENCE.md` (both status: final, updated 2026-07-24)

**Epics & Stories:**
- Whole: `epics.md` (updated 2026-07-24, Epic 1 extended + new Epic 4 added)

**Duplicates found:** None.
**Missing documents:** None.

## PRD Analysis

### Functional Requirements

FR1: Configurable-interval snapshot capture — the system captures a Snapshot for every subscribed coin at a configurable interval, defaulting to 0.5s. Interval is a config value, not a hardcoded constant; changing it requires no code change.
FR2: Opt-in raw delta capture — the user can flag specific coins for Raw Delta Capture in addition to standard Snapshots. Flagging a coin does not affect Snapshot capture for other coins; raw-delta-captured coins support resampling to any interval at read time; retention/pruning is a per-coin config value including an unlimited (never-pruned) setting.
FR3: Fail-closed data integrity gate — the system rejects (never fabricates, clamps, or averages) invalid data at the point of capture: crossed books, stale books, precision-invalid values. Every rejection is logged with the offending payload and specific reason; no schema carries a validity/flag field.
FR4: Reconnect & gap resilience — the system recovers from WS/HTTP disconnects without corrupting stored data, and flags resulting gaps rather than interpolating across them. A gap appears as a visible break (e.g. `None`/null), never a flat/interpolated line.
FR5: USD-denominated liquidity capture — open interest is polled separately and liquidity is classified using USD-denominated values (`volume24H` or `openInterest × oraclePrice`), never raw token-unit open interest.
FR6: Coin ranking, sorted by volume — the system ranks all subscribed coins continuously; in volume Ranking Mode (default), sort order is strictly by descending `volume24H` (USD); HFT/TA indicators are computed/displayed per coin but do not affect sort order in this mode; ranking is inspectable historically.
FR16: Volatility-based ranking mode — the system computes a volatility indicator (stddev of price/returns) per coin and ranks by relative cross-sectional volatility, as a user-selectable alternative Ranking Mode to FR-6; switchable without code change; volatility implemented once, consumed identically everywhere (Jupyter, backtest, live, dashboard, TUI); lookback window configurable, default 1h.
FR7: Live watchlist — the ranked list is queryable live and usable directly to select a coin set for a multi-coin backtest; not limited to a fixed, manually-curated coin set.
FR8: Research ranking view — the user can inspect how a coin's ranking evolved over time (queryable for any past timestamp within retention), to decide on opt-in raw-delta capture.
FR9: Jupyter research environment — the user can develop/test indicators and ML signals in Jupyter against catalog data, following Nautilus's own example research-notebook conventions, not a custom framework.
FR10: Single indicator implementation, three consumption contexts — every indicator/signal is implemented exactly once and consumed via identical code in Jupyter, backtest, and live contexts; no parallel reimplementations.
FR11: Nautilus-native backtesting — the system uses `BacktestNode` + `BacktestDataConfig` exclusively; no custom simulation/matching loop; strategies referenced via `ImportableStrategyConfig` by string path.
FR12: Dual-timeframe strategies — backtest at HFT granularity (raw 0.5s/1s Snapshot data) and at slower timeframes (candlesticks aggregated from the same underlying data, configurable aggregation window).
FR13: Multi-coin backtest runs — run a single backtest across the full ranking Watchlist (many coins at once), using the Watchlist's current dynamic output rather than a fixed static coin universe.
FR14: Dummy paper-trading strategy — a `TradingNode`-based, paper-mode strategy consuming all signals/indicators produced by the research feature, running against live dYdX market data as a live integration proof.
FR15: Trading-mode isolation — live paper-trading strategy execution lives in a module separate from `dydx_collector`/`ml_signals`'s data path (per amended AD-8); enabling real-money execution requires an explicit, separate config step not reachable by default/accidental state in v1.
FR17: Keyboard-only TUI over the shared live feed — a urwid-based terminal UI subscribes to the same live Redis pub/sub feed the web dashboard reads from, navigable entirely by keyboard; no separate data pipeline; every navigation action has a keybinding.
FR18: Bots pane — the TUI shows a list of running bots with PnL and other per-bot metrics, updated live, no manual refresh needed.
FR19: Coin list pane mirroring the dashboard — the TUI shows the Coin Ranking/Watchlist (incl. FR-16's Ranking Mode) as a live mirror of the web dashboard; both read the same ranking engine, never two independently-computed lists.
FR20: Coin-detail view — selecting a coin shows live-calculated indicators (OFI/OBI/microprice/spread) and full order-book depth (20 levels/side, not just top-of-book); depth never truncated as a permanent limitation; indicators computed via the same shared code path.
FR21: k9s-style navigation model — `:` command bar to jump views, `esc` to pop back a level (never exits), `/` to fuzzy-filter the coin list, breadcrumb header showing current location.
FR22: Attention-only color coding — color draws the eye only to what needs attention (stale/dead feed, large PnL swing); healthy state renders quiet/neutral; uniform monospace size throughout, never scaled for emphasis; ranking order itself stays uninflected by color.
FR23: Bot start/stop controls — start/stop a bot directly from the TUI, for both paper-mode and live-mode execution, gated by the same isolation/config-gate as FR-15; does not bypass the paper/real-money separation.
FR24: Live-only, no in-TUI history — the TUI shows only current/latest state; no historical replay or scrollback; historical/graph analysis remains the web dashboard's responsibility.
FR25: Deep-linked browser handoff (Should, not MVP-blocking) — a keybinding on a selected coin opens that coin's graph view in the web dashboard, deep-linked to the exact coin and time-window/zoom context; implement once FR-17–FR-24 are stable.

**Total FRs: 25** (FR1–FR15, FR16, FR17–FR25 — numbering is non-sequential by design; FR16 was inserted into §4.2 alongside FR-6 rather than appended at the end)

### Non-Functional Requirements

The PRD has no explicit NFR section (confirmed on full read — §0–§9 contain no NFR-labeled subsection). The following are derived from Vision (§1), Success Metrics (§7), and the architecture spine's invariants, matching what `epics.md` already derived and used for story coverage:

NFR1: Data integrity — zero tolerance for corrupted/fabricated market data; genuinely unavailable data must be visually flagged, never papered over (Vision; FR-3/FR-4; SM-C1).
NFR2: Operational reliability — the Dummy Strategy must run continuously in paper mode against live data for at least one week without manual intervention (SM-3).
NFR3: Memory-bounded access — no unbounded catalog reads; all data access time-bounded or streamed (architecture AD-6).
NFR4: Fork safety — `nautilus_trader/` and `crates/` never modified (architecture, all ADs).
NFR5: Precision correctness — price/quantity precision changes only via `Decimal.scaleb()` + `*.from_raw()` (architecture AD-5).

**Total NFRs: 5 (derived, not PRD-explicit)**

### Additional Requirements

- **Non-Goals (§5):** no auth/multi-user, dYdX-only in v1, no mobile app, no real-money trading in v1, not a polished consumer UI (applies to both dashboard and TUI), TUI doesn't manage tmux/pane layout, no k9s `:xray`/resource-count badges, no historical replay/scrollback in the TUI.
- **MVP Scope (§6):** FR-25 (deep-linked browser handoff) explicitly out of MVP scope, Should-tier, next increment after Must-tier TUI scope ships.
- **Success Metrics (§7):** SM-1–SM-4 plus counter-metric SM-C1 — SM-4 specifically validates FR-16/FR-19's shared-ranking-engine constraint (TUI and dashboard coin lists must never diverge).
- **Open Questions (§8):** OQ-1 (composite ranking) marked `[RESOLVED 2026-07-24]` via FR-16.
- **Assumptions Index (§9):** none outstanding.

### PRD Completeness Assessment

PRD is internally consistent and `status: final`. One traceability gap found during this analysis, carried forward to epic coverage validation: **`epics.md`'s Epic 4 references FR27 (bot-detail trade/PnL history) and it has full story coverage (Stories 4.6–4.7), but FR27 does not exist anywhere in this PRD** — it was surfaced during the 2026-07-24 UX design pass and explicitly flagged in that UX contract's Data Sources & Staleness section as "worth a PRD amendment," but the amendment itself was never made. This is not a coverage gap in epics/stories (FR27 is covered) — it's a **PRD-lags-epics gap**: a requirement with full downstream planning and no upstream PRD entry.

## Epic Coverage Validation

### Coverage Matrix

| FR Number | PRD Requirement (summary) | Epic Coverage | Status |
|---|---|---|---|
| FR1 | Configurable-interval snapshot capture | Epic 1, Story 1.1 | ✓ Covered |
| FR2 | Opt-in raw delta capture | Epic 1, Story 1.1 | ✓ Covered |
| FR3 | Fail-closed data integrity gate | Epic 1, Stories 1.1, 1.5, 1.6, 1.7 (extended) | ✓ Covered |
| FR4 | Reconnect & gap resilience | Epic 1, Stories 1.1, 1.5, 1.6, 1.7 (extended) | ✓ Covered |
| FR5 | USD-denominated liquidity capture | Epic 1, Story 1.1 | ✓ Covered |
| FR6 | Coin ranking, sorted by volume | Epic 1, Story 1.2 | ✓ Covered |
| FR16 | Volatility-based ranking mode | Epic 1, Story 1.8 | ✓ Covered |
| FR7 | Live watchlist | Epic 1, Story 1.3 | ✓ Covered |
| FR8 | Research ranking view | Epic 1, Story 1.4 | ✓ Covered |
| FR9 | Jupyter research environment | Epic 2, Story 2.1 | ✓ Covered |
| FR10 | Single indicator implementation, 3 contexts | Epic 2, Story 2.2 | ✓ Covered |
| FR11 | Nautilus-native backtesting | Epic 2, Stories 2.3, 2.4 | ✓ Covered |
| FR12 | Dual-timeframe strategies | Epic 2, Story 2.3 | ✓ Covered |
| FR13 | Multi-coin backtest runs | Epic 2, Story 2.4 | ✓ Covered |
| FR14 | Dummy paper-trading strategy | Epic 3, Story 3.2 | ✓ Covered |
| FR15 | Trading-mode isolation | Epic 3, Story 3.1 | ✓ Covered |
| FR17 | Keyboard-only TUI over shared live feed | Epic 4, Story 4.1 | ✓ Covered |
| FR18 | Bots pane | Epic 4, Stories 4.4, 4.5 | ✓ Covered |
| FR19 | Coin list pane mirroring the dashboard | Epic 4, Stories 4.1, 4.2 | ✓ Covered |
| FR20 | Coin-detail view | Epic 4, Story 4.3 | ✓ Covered |
| FR21 | k9s-style navigation model | Epic 4, Story 4.1 | ✓ Covered |
| FR22 | Attention-only color coding | Epic 4, Stories 4.2, 4.8 | ✓ Covered |
| FR23 | Bot start/stop controls | Epic 4, Stories 4.4, 4.5 | ✓ Covered |
| FR24 | Live-only, no in-TUI history (market data) | Epic 4, Story 4.3 | ✓ Covered |
| FR25 | Deep-linked browser handoff (coins) | Epic 4, Story 4.3 | ✓ Covered |

### Missing Requirements

None. All 25 PRD FRs have traceable epic/story coverage.

### FRs in Epics But Not in PRD

**FR27** (bot-detail trade/PnL history, extends FR-25's deep-link pattern to bots) — covered by Epic 4, Stories 4.6–4.7, but does not exist in the PRD (see PRD Completeness Assessment above). Flagged as a required PRD amendment before/alongside implementation, not a story-coverage defect.

### Coverage Statistics

- Total PRD FRs: 25
- FRs covered in epics: 25
- Coverage percentage: 100%
- Additional epic-covered items with no PRD entry: 1 (FR27)

## UX Alignment Assessment

### UX Document Status

Found — `ux-designs/ux-nautilus_trader_fork-2026-07-24/DESIGN.md` + `EXPERIENCE.md`, both `status: final`, produced specifically for the Bot Monitoring TUI (FR-17–FR-25/UJ-4).

### Alignment Issues

**1. FR24 vs. UX Bot-detail scope (Medium — needs a PRD text fix, not a rebuild).** PRD's FR24 states plainly: "The TUI shows only current/latest state; it provides no historical replay or scrollback." The finalized EXPERIENCE.md narrows this during Discovery to "no historical replay for *market data*" specifically, and treats bot/trade performance history as a distinct, in-scope domain (Bot-detail regions 2–3: trades blotter + PnL-over-time chart, FR27). This is a deliberate, memlog-recorded decision from the UX session — not an oversight — but as written, FR24's PRD text still reads as an unqualified "no history anywhere in the TUI" rule that a fresh reader (or a future PM) would reasonably take at face value and flag as contradicted by Epic 4's own stories. **Recommendation:** amend FR24's text to explicitly scope it to market data (order book/price/ranking history), alongside adding FR27, so the PRD reads consistently with what's already been designed and story-planned.

**2. FR20 "never truncated to top-of-book only" vs. UX's collapsed-by-default order book (Low — already reconciled in the spines, flagging for PRD text parity only).** The UX design pass deliberately reinterpreted this consequence line as a *capability* guarantee (full depth always reachable via `d`) rather than a default-render mandate, and documented the reasoning in both EXPERIENCE.md and the memlog. Epic 4's Story 4.3 ACs already reflect this reconciled interpretation correctly. No epic/story fix needed — flagging only because the PRD's FR-20 consequence text ("Depth is never truncated to top-of-book only in this view") reads, in isolation, as slightly in tension with a collapsed-by-default UI, even though the UX rationale for why these aren't actually in conflict is sound and already recorded.

**3. User journeys.** UJ-4 (§2.2) — SSH in, see bots/coins at a glance, drill into a coin, hand off to dashboard, start/stop a bot — is fully realized across Epic 4's stories and EXPERIENCE.md's Key Flow 1. EXPERIENCE.md's Key Flow 2 (bot post-mortem) is a UX-originated journey with no corresponding UJ number in the PRD — consistent with issue #1 above (it's the journey that exercises FR27).

### UX ↔ Architecture Alignment

**Gap (Medium-High — blocks Story 4.6/4.7 implementation until resolved).** EXPERIENCE.md's "Data Sources & Staleness" section specifies that `live_paper` must expose a thin read surface over its Nautilus `Cache` (built on `cache.orders_closed()`/`positions_closed()`) for trade/PnL history, with the exact channel/request-response shape explicitly left `[ASSUMPTION] ... undecided, left to architecture/implementation`. Checked the architecture spine directly (`grep` for `Cache`/`orders_closed`/`CacheConfig` returns zero matches) — **this mechanism has no corresponding architectural decision anywhere in `ARCHITECTURE-SPINE.md`.** AD-10 covers `bots:status`/`bots:control` only; there is no AD covering how durable trade/position history is persisted, addressed, or read. Epic 4's Story 4.6 correctly identifies the underlying capability (`CacheConfig(database=DatabaseConfig(type="redis", ...))`) but the *interface* `bot_tui` and the dashboard use to query it is still an open question at the architecture level, not just an implementation detail. **Recommendation:** amend the architecture spine with a new AD (or an AD-10 extension) formalizing this read surface (e.g., a new Redis channel, a request/response pattern, or direct read-only Cache access with a defined key/encoding contract) before Story 4.6 is picked up for implementation.

**No other architecture gaps found.** AD-9 (ranking_engine) and AD-10 (live_paper control-plane, `bots:status`/`bots:control`) fully support FR16–FR19, FR22, FR23. The `bot_tui` module boundary (AD-4 extension) supports FR17/FR20's reuse of `ml_signals.indicators`. Deployment shape (interactive/exec'd process, not a compose daemon) matches EXPERIENCE.md's Foundation section exactly.

### Warnings

- FR24's PRD text should be amended before implementation reaches Story 4.7, to avoid a developer implementing against the literal (unscoped) PRD text and being surprised by Epic 4's designed-in history feature.
- Story 4.6 should not start implementation until the architecture gap above is resolved — the read-surface *shape* is exactly the kind of decision that, if made ad hoc during coding, risks the dashboard and `bot_tui` independently guessing at different wire formats (the same failure mode AD-9 was written to prevent for ranking data).

## Epic Quality Review

Scope: this pass focuses on the new/changed content (Epic 1's Story 1.8, and Epic 4 in full) — Epics 1–3's original stories already passed this same review at their own creation and are unchanged here.

### Epic Structure Validation

**User Value Focus:** Epic 4's goal statement is fully user-outcome-framed ("Builder SSHes into the box... sees per-bot PnL/health and which coins are hot right now... starts/stops a bot without leaving the terminal"). The title itself ("Bot Monitoring TUI") names the surface rather than the outcome — technically closer to a feature name than a pure action phrase, but this matches the existing precedent already accepted for Epic 3 ("Live Paper-Trading Integration Proof"), so not flagged as a new deviation. Story 1.8's title/goal are both clearly user-outcome-framed.

**Epic Independence:** Epic 4 depends only on Epics 1–3's *outputs* (ranking engine, shared indicators, `live_paper`), never their future work — confirmed no epic prior to Epic 4 requires anything from it. One file-overlap note: Story 4.6 modifies `live_paper/node.py` (an Epic 3 file), but this extends an already-complete, already-functional Epic 3 deliverable rather than making Epic 3 incomplete without Epic 4 — not a violation, and not repeated churn (a single, one-story touch with clear rationale).

### Story Quality Assessment

**Sizing & independence:** All 8 Epic 4 stories and Story 1.8 are single-capability, single-dev-session sized, consistent with Epics 1–3's existing granularity.

**Forward-dependency check (all 9 new stories):** None found. Sequencing verified: 4.2→4.1, 4.3→4.1, 4.5→4.4, 4.7→4.5+4.6 (both prior), 4.8→4.1–4.7 (all prior, closing-verification story mirroring Epic 1's Story 1.1 pattern). Story 4.5 explicitly notes Story 4.7 will add more to the same view later, but is worded as a scope boundary ("not required for this story's completion"), not a functional dependency — correct pattern, not a violation.

**Acceptance Criteria:** Given/When/Then format used consistently. Error/edge-case coverage present where relevant: Story 4.6 has an explicit "read surface unreachable" AC, Story 4.7 has the corresponding "history unavailable" rendering AC, Story 4.2 covers the stale-feed edge case, Story 4.3 covers the thin-book edge case. No vague or non-testable criteria found.

**Entity/config creation timing:** Story 4.6 (the one story enabling durable persistence) sits immediately before Story 4.7 (the first story that needs it) — correct "create only when needed" placement, not front-loaded.

### Special Implementation Checks

No starter template applicable (brownfield, already established). Brownfield indicator present and correct: Story 4.6 is an integration story against an existing module (`live_paper`), not a greenfield scaffold.

### Findings by Severity

🔴 **Critical Violations:** None.
🟠 **Major Issues:** None.
🟡 **Minor Concerns:** Epic 4's title names the surface ("Bot Monitoring TUI") rather than a pure user-action phrase — consistent with existing Epic 3 precedent in this same document, not a new deviation; no change recommended.

**Overall: Epic Quality Review passes with zero critical/major violations.**

## Summary and Recommendations

### Overall Readiness Status

**NEEDS WORK — scoped narrowly.** Epics 1–3 (all 14 pre-existing stories) and Epic 4's Stories 4.1–4.5 and 4.8 (6 of Epic 4's 8 stories) are ready for implementation as written — 100% FR coverage, zero critical/major epic-quality violations, architecture fully supports what they need. **Story 4.6 (and therefore 4.7, which depends on it) is blocked** until the architecture spine is amended with a formal decision for the trade/PnL-history read surface. This is not a "redo the planning" situation — it's two documentation fixes and one architecture decision, all narrowly scoped.

### Critical Issues Requiring Immediate Action

None rise to "critical" (nothing is broken or contradictory) — but one is a genuine implementation blocker:

1. **Architecture gap (blocks Story 4.6/4.7):** No AD in `ARCHITECTURE-SPINE.md` covers how `live_paper` exposes durable trade/PnL history to `bot_tui`/dashboard. Confirmed via direct grep — zero matches for `Cache`/`orders_closed`/`CacheConfig` in the spine. Resolve via a `bmad-architecture` update pass before Story 4.6 is picked up.

### Recommended Next Steps

1. **Run `bmad-architecture` (update mode)** to add the missing AD for the trade/PnL-history read surface (channel/request-response shape, addressing, staleness convention — mirroring how AD-9 specified `rankings:live`'s shape). Unblocks Stories 4.6–4.7.
2. **Amend the PRD's FR24 text** to explicitly scope "no historical replay" to market data (order book/price/ranking history), and add FR27 (bot trade/PnL history) formally — both already effectively decided and story-planned, just not yet reflected in `prd.md` itself. A quick `bmad-prd` update pass covers both.
3. **Optional, low-priority:** note the FR20-vs-collapsed-default-UI text nuance in the PRD if a future reader might otherwise flag it as a conflict (no functional change needed, already correctly resolved in the UX spines and Story 4.3).
4. **No action needed** on Epics 1–3 or Epic 4 Stories 4.1–4.5/4.8 — proceed to Sprint Planning for that scope now if you want to start implementation in parallel with the doc fixes above.

### Final Note

This assessment identified 4 issues across 3 categories (PRD completeness, PRD/UX/Architecture alignment, epic quality) — 1 blocking (architecture gap), 2 documentation-only (FR24/FR27 PRD text), 1 non-blocking cosmetic note (Epic 4 title). Zero issues in FR coverage, zero critical/major epic-quality violations. Address the architecture gap before Story 4.6; everything else can proceed or be fixed in parallel.
