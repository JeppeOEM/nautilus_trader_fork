# Story 22.8: Spine, rules and docs updated for the multi-venue core

Status: ready-for-dev

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a future contributor,
I want the architecture spine and `troll/CLAUDE.md` to describe the core, not three siblings,
so that the next venue is added the documented way.

## Acceptance Criteria

1. **Spine ADs amended.** AD-1 ("structural enforcement once a second writer exists"), AD-4 (shared types list, stale `DydxMinuteBar` name) and AD-11 each name `collector_core` as the single write gate, the moved shared types, and per-venue clients; the Consistency Conventions row reads "one producer per (channel, venue)".
2. **Rules re-scoped, recipe written once.** `troll/CLAUDE.md`'s header scopes the rules to `collector_core/` and every venue collector (not only `dydx_collector/` and `ml_signals/`), and the "how to add a venue" recipe (client + config + entrypoint + one `common/venues.py` line) is written down once.

## Tasks / Subtasks

- [ ] Task 0 — precondition: stories 22.1–22.6 merged (22.7/22.9 may land later; this story documents what exists, never what is planned).
- [ ] Task 1 — `ARCHITECTURE-SPINE.md` (AC: #1)
  - [ ] AD-1: **Binds** → `collector_core.collector.Collector` (`_second_loop` gate) and every `Collector` subclass (`dydx_collector.DydxCollector`, `bybit_collector.BybitCollector`, `hyperliquid_collector.HyperliquidCollector`) plus any writer script; **Rule** `[ADOPTED]` citation re-grounded to the core's file/lines (the old `collector.py:643/735/549` cites are dead); the sentence "there is currently no compiler/lint-level mechanism forcing this … holds by there being exactly one writer today" becomes "held structurally: every venue writes through the one core class; a venue may override `_apply_deltas`/`_handle_crossed_book` only, never the gate". Update the **Deferred: Shared validator extraction** item at the bottom to "resolved by Epic 22 story 22.1".
  - [ ] AD-2: Binds → the core gate; note the per-venue crossed-book semantics (dYdX uncross override; Bybit/Hyperliquid skip + ledger + fallback resync) with a pointer to research §A.
  - [ ] AD-4: shared types list → `DydxSecondSnapshot`, `OpenInterest`, `ohlc_outside_book` in `collector_core/` (drop the stale `DydxMinuteBar`); Binds add `collector_core`, `bybit_collector`, `hyperliquid_collector`, `common`; the `[ADOPTED]` citation re-grounded to the post-22.3 import paths (`grep -rn "from collector_core" troll/ml_signals troll/data_api troll/ranking_engine` at the time of writing).
  - [ ] AD-11: confirm 22.6's amendment landed; if the wording still says `DydxDataClientConfig`, fix it here.
  - [ ] Consistency Conventions "Redis channel conventions" row: "one producer per channel/key" → "one producer per (channel, venue) for `snapshots:raw` (payload entries disjoint by `instrument_id`); one producer per channel/key otherwise". `collector:status`/`collector:control` explicitly noted as dYdX-only.
  - [ ] Deployment paragraph: the thin `collector.dockerfile` now bakes `collector_core/` + every venue collector; three collector services share one catalog root.
- [ ] Task 2 — `troll/CLAUDE.md` (AC: #2)
  - [ ] Header: "These rules govern all code under `troll/collector_core/`, every `troll/*_collector/`, and `troll/ml_signals/`"; FORK-02 wording likewise (the `live_paper/` exception unchanged).
  - [ ] DATA-01/DATA-04/DATA-06/DATA-07/OBS-01 mention `collector._second_loop`, `_STALE_BOOK_NS`, `_STALE_TRADE_NS`, `collector._resync_book` — repoint to `collector_core.collector.Collector._second_loop`, `CoreConfig.stale_book_seconds`/`stale_trade_seconds`/`crossed_resync_seconds`, `_handle_crossed_book` (core default vs dYdX override). Keep the rules' substance and their incident history untouched.
  - [ ] New section **"Adding a venue"** (after Design Principles, ~12 lines): `venue_collector/client.py` implementing the duck-typed contract (list the seven methods, which are optional); `config.toml` (+ `config.py` only if the venue needs keys beyond `CoreConfig`); a ~15-line `XCollector(Collector)` entrypoint with `run_forever`; one line in `common/venues.py` (`VENUE_KINDS`, and `market_kind` if the id suffix is new); compose service + `collector.dockerfile` `COPY` + `Makefile` test path; a Task-1-style wire-behaviour investigation (crossed-book semantics, sequence semantics, precision, OI availability, subscribe replay, rate limits) **before** deciding which core hooks to override — cite 19.3/19.4/22.5 as the precedent; `DATA_INTEGRITY_AUDIT.md` rows for that venue.
  - [ ] SSOT/Desktop↔VPS sections: unchanged unless a path moved.
- [ ] Task 3 — other docs that name the old layout
  - [ ] Repo-root `CLAUDE.md` "Project"/"Constraints" paragraphs (they describe `troll/dydx_collector/` as *the* collector; the thin-image description names only `dydx_collector/`): one paragraph on the core + venue collectors; keep the Bybit/dYdX incident notes.
  - [ ] `_bmad-output/project-context.md` lines 21 and 37 (thin image bakes `troll/dydx_collector/`; "the collector (`dydx_collector/collector.py`) owns its own asyncio loop") → core wording.
  - [ ] `troll/README.md` collector section; `troll/docs/DATA_DICTIONARY.md` module paths for §1.x types (post-22.3) and one line that Bybit spot has no mark/funding/OI (22.4); `troll/docs/DATA_INTEGRITY_AUDIT.md` Mechanism/Treatment cells that cite `_STALE_TRADE_NS`/`_STALE_BOOK_NS`/`collector.py:NNN` → core names (don't rewrite the history in §1).
  - [ ] Story files `19-3-troll-bybit-collector.md` and `19-4-troll-hyperliquid-collector.md`: append one Completion Notes line — "Sibling-not-base deferral closed by Epic 22 (22.1: `collector_core`)". `epics.md` Story 19.3 AC text is historical; leave it.
- [ ] Task 4 — verification (no tests: TEST-02, docs only)
  - [ ] `grep -rn "dydx_collector.second_snapshot\|dydx_collector.integrity\|DydxMinuteBar\|DydxOpenInterest" troll _bmad-output/planning-artifacts/architecture troll/CLAUDE.md CLAUDE.md` returns only historical/archive mentions (audit §1, story files).
  - [ ] Every file path cited in the amended ADs exists (`ls` each); every `troll/CLAUDE.md` rule still names a real symbol.
  - [ ] Pre-commit passes (`check-copyright-year` is code-only; markdown just needs the hooks not to choke).

## Dev Notes

### Document what shipped, verbatim from the code

This story runs last on purpose. Read the merged `collector_core/collector.py`, the three venue `collector.py` files and `common/venues.py` before writing a sentence, and quote real symbol names and paths. The spine's `[ADOPTED]`/`[VERIFIED]` markers are only honest if re-grounded against the current tree (the existing ADs show the pattern: `re-verified 2026-07-24, citations corrected`). Use `[amended 2026-09-xx: Epic 22]` markers so the next audit can see what changed and why.

### Keep the incident history

`troll/CLAUDE.md`'s DATA rules and the audit register carry the evidence trail for real production incidents (fake candles, crossed-book categories, OOM). Re-pointing a symbol name must not shorten or soften the "Why" text. When the mechanism moved (e.g. `_STALE_TRADE_NS` is now a `CoreConfig` field), say both: what it was, where it lives now.

### Scope discipline (DESIGN-01/03)

No new rules unless Epic 22 produced a new lesson worth a rule (candidate: "a venue's sequence field means three different things across venues — never gap-check without the venue's own evidence", from 22.2/22.5). At most one such addition, with the evidence cited.

### Project Structure Notes

- Modified: `_bmad-output/planning-artifacts/architecture/architecture-nautilus_trader_fork-2026-07-01/ARCHITECTURE-SPINE.md`, `troll/CLAUDE.md`, `CLAUDE.md` (repo root), `_bmad-output/project-context.md`, `troll/README.md`, `troll/docs/{DATA_DICTIONARY,DATA_INTEGRITY_AUDIT}.md`, `_bmad-output/implementation-artifacts/{19-3-*,19-4-*}.md`.
- No code changes.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.8] — ACs.
- [Source: research 2026-09-20 §B1 ("Adding a venue = …"), §B4] — recipe and channel convention.
- [Source: ARCHITECTURE-SPINE.md#AD-1 (:78-82), #AD-2, #AD-4 (:96-100), #AD-11 (:159-175), Consistency Conventions (:181), Deferred "Shared validator extraction" (:253)] — text to amend.
- [Source: troll/CLAUDE.md header (:1-8), FORK-02, DATA-01..07, OBS-01] — rules to re-scope/re-point.
- [Source: CLAUDE.md (repo root) "Project"/"Constraints"; _bmad-output/project-context.md:21,37] — stale layout statements.
- [Source: _bmad-output/implementation-artifacts/19-3-*.md, 19-4-*.md AC #6/#3] — the deferral being closed.
- [Source: troll/CLAUDE.md DESIGN-01/03, TEST-02] — rules applied.

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

### File List
