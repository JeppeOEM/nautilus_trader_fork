---
title: 'Cutover — retire dashboard.py, close epic-14'
type: 'chore'
created: '2026-09-16'
status: 'ready-for-dev'
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/troll/CLAUDE.md']
warnings: []
---

<intent-contract>

## Intent

**Problem:** `troll/ml_signals/dashboard.py` (2369-line aiohttp HTML+JS app) still runs in parallel with its React/`data_api` replacement (epic-15, Stories 15.1-15.9), and `docker-compose.yml`/`Makefile`/`troll/CLAUDE.md` still describe it as the primary surface; epic-14's Story 14.3 sits `in-progress` even though its target surface (dashboard.py's chart page) is slated for deletion here.

**Approach:** Walk a feature-parity checklist for every dashboard.py page/capability against its shipped Story 15.x equivalent; only if every row passes, delete dashboard.py's rendering surface, remove its compose service, retarget the Makefile, update CLAUDE.md's SEC-01/VPS-tunnel docs, and close epic-14/epic-15 bookkeeping.

## Boundaries & Constraints

**Always:**
- Never modify `nautilus_trader/` or `crates/`.
- Delete dashboard.py's HTML-rendering surface outright (AD-F1) — never comment out or leave as dead code.
- `data_api` keeps `network_mode: host` + `uvicorn --host 127.0.0.1` (SEC-01) — already compliant, do not touch.
- `sprint-status.yaml` is orchestrator-owned for this run: never write it, never revert a change to it (explicit run instruction).
- Any irreversible git operation (e.g. dropping the parked 14.3 stash) is flagged in Completion Notes for the user to action, never performed by the agent.

**Block If:**
- **[RESOLVED 2026-09-17 — see Resolution below]** A dashboard.py page/capability in AC#1's checklist has no verified working React equivalent. This was confirmed true for **31-day metrics history (Story 15.8)** at spec-freeze time, but the user has since authored **Epic 17, Story 17.2** ("Unpark and complete Story 15.8 — 31-day metrics history," `ready-for-dev`) to close exactly this gap by implementing 15.8's scope verbatim. This spec now blocks on **Story 17.2 reaching `done` in `sprint-status.yaml`**, not on the 31-day-history gap being permanently unresolvable. Once 17.2 is `done`, re-verify the 31-day history row against the running app; if it passes, Task 1 proceeds normally.
- Any other Task-1 row (rankings, chart candles/lines/indicators/live-edge, docs, terminal identity) found to have no verified working equivalent when manually walked against the running app.

**Never:**
- Do not delete dashboard.py, its compose service, or its Makefile target while any Task-1 gap is open.
- Do not silently narrow AC#1's checklist to exclude 31-day history on the agent's own initiative.
- Do not implement Story 15.8's scope from within this story — that work belongs to Story 17.2 exclusively; this story only re-verifies its outcome once 17.2 is `done`.

</intent-contract>

## Code Map

- `troll/ml_signals/dashboard.py` -- 2369-line aiohttp app; `_page`, `_build_chart_page_html`, `_render_live_page`, `_render_history_page`, `_history_page_from_rows`, `docs_handler`, `make_app`, `_LIVE_CHART_JS` all slated for deletion. Confirmed via repo-wide grep: zero remaining imports of `ml_signals.dashboard` outside the file itself (Stories 15.6/15.7 already relocated `_merged_indicator_catalog`/`_price_series_rows` into `data_api`) — Task 2's precondition already holds.
- `troll/docker-compose.yml:61-88` -- `dashboard:` service block to remove. `data_api:` block (`:127-150`) already has `network_mode: host`, `uvicorn --host 127.0.0.1`, and the `rw` `chart_indicators.toml` mount (shared anchor `*chart-indicator-config-mount`) that dashboard's own mount comment describes — Task 3's cross-check on that mount already passes.
- `troll/Makefile:89,100,125,172,232-234` -- `dashboard` referenced in the `restart`/`up`/`build-insecure`/`logs`/own `dashboard:` targets; the last (`232-234`) runs `python -m ml_signals.dashboard --open` directly and needs retargeting to `data_api` (which now also serves the built SPA) or removal.
- `troll/CLAUDE.md:27` -- SEC-01 service list names `dashboard` explicitly, needs to drop it.
- `troll/CLAUDE.md:150-190` ("Desktop ↔ VPS Connection") -- describes `_troll_dashboard_ensure`/`troll-web`/`make dashboard` against a `dashboard` port; needs rewriting to describe one tunneled surface (`data_api`). Note: the actual `~/.zshrc` functions live outside this repo — only the doc's description can change here.
- `_bmad-output/implementation-artifacts/sprint-status.yaml` -- **orchestrator-owned, do not write** (per this run's explicit instruction); 14.3→`superseded` and epic-14/epic-15→`done` bookkeeping described in the original story's Task 4 is therefore **out of scope for this run**, not silently dropped.
- `_bmad-output/implementation-artifacts/15-9-terminal-ansi-visual-identity.md` -- source of the confirmed Story 15.8 gap evidence (Completion Notes, "Deviations / notes" bullet).
- `_bmad-output/implementation-artifacts/15-10-cutover-retire-dashboard-py-close-epic-14.md` -- the original story spec (Given/When/Then ACs, full task breakdown) this spec compresses.

## Tasks & Acceptance

**Execution:** blocked before Task 1 completes — no file changes are safe to make until Story 17.2 reaches `done` in `sprint-status.yaml` (see Resolution below).

- [ ] Task 1 (AC#1) — Walk the feature-parity checklist against the running app. **Blocked on Story 17.2 shipping the 31-day metrics history equivalent** (Story 15.8's scope, executed by 17.2). Once 17.2 is `done`, re-verify this row against the running `HistoryPage.tsx` before proceeding — do not assume pass from 17.2's own Completion Notes alone. All other rows (rankings 15.2, chart+candles 15.3, indicator panes 15.4, live edge 15.5, per-coin indicator config 15.6, lines mode 15.7, docs 15.1, terminal identity 15.9) are `done` in `sprint-status.yaml` and plausible from their own Completion Notes, but were not independently re-verified against the running app in this session — that verification is also still owed once the blocker clears.
- [ ] Task 2 (AC#2) -- delete `dashboard.py`'s HTML-rendering surface -- blocked on Task 1.
- [ ] Task 3 (AC#3) -- remove `docker-compose.yml`'s `dashboard` service, retarget the `Makefile` `dashboard` target -- blocked on Task 1.
- [ ] Task 4 (AC#4) -- `sprint-status.yaml` housekeeping (14.3 → superseded, epic-14/epic-15 → done) -- **out of scope for this run regardless of the blocker**: `sprint-status.yaml` is orchestrator-owned, this agent must not write it.
- [ ] Task 5 (AC#5) -- update `troll/CLAUDE.md`'s SEC-01 list and Desktop ↔ VPS Connection section -- blocked on Task 1.
- [ ] Task 6 (AC#6) -- verify chart interactivity/scroll-back over the real SSH-tunneled path, report actual numbers -- blocked on Task 1.
- [ ] Task 7 (AC#7) -- re-run `troll/` test suites (`data_api`, `ml_signals`, frontend build/test/lint) as a regression check, and confirm whether the previously-tracked pre-existing failure (`test_microfeatures_json_decimates_and_reports_true_pre_decimation_count`) lived in a now-deleted test module -- blocked on Task 1.

**Acceptance Criteria:**
- Given the 31-day metrics history capability has no shipped React equivalent, when Task 1's checklist is walked, then the story halts before any deletion, per this story's own Dev Notes ("deletion is the reward for parity, not a race to clean up code").

## Verification

**Commands (deferred until unblocked):**
- `cd troll && PYTHONPATH=. python -m pytest data_api/tests ml_signals/tests -q` -- expected: 0 known pre-existing failures once `dashboard.py`'s own test module is deleted (needs re-confirming — previously 1 was tracked).
- `cd troll/frontend && npm run build && npm run test && npm run lint` -- expected: clean.

## Auto Run Result

Status: blocked

Blocking condition: intent gaps

**Details:** Story 15.10's AC#1 requires every dashboard.py page/capability to be confirmed against a shipped React equivalent before any deletion proceeds. Walking that checklist during planning found one row that provably fails: **31-day metrics history (Story 15.8)** has no working equivalent — `HistoryPage.tsx` is still a placeholder. This is not a discoverable-later risk; it's already confirmed true from two independent sources gathered this session:
- `_bmad-output/implementation-artifacts/sprint-status.yaml` (commit `0157c2b2e964052c7ebc6b60abdf85975989434e`, 2026-09-16): 15.8 deliberately parked at `in-progress` — "deferred ... not needed now, per user request ... No dev work has been done on it."
- `_bmad-output/implementation-artifacts/15-9-terminal-ansi-visual-identity.md` Completion Notes: "`HistoryPage.tsx` has no metric tiles to apply a box-drawing treatment to — Story 15.8 (the 31-day metrics history page) was deferred per sprint-status commit `0157c2b2e9`."

This is a genuine contradiction between two of the user's own decisions made at different times, not a task an agent can safely resolve alone: Story 15.10's AC#1 (authored 2026-09-15) mandates full parity as a hard gate, while the 15.8 deferral (2026-09-16, one day later) explicitly deprioritized the one capability that gate can't yet clear. Everything an agent could verify without touching that question was checked and is clean (zero remaining external imports of `ml_signals.dashboard`; `data_api`'s compose block already has the `rw` `chart_indicators.toml` mount and SEC-01-compliant binding dashboard's own mount comment calls for).

**Unanswered questions for the user (as of spec-freeze 2026-09-16):**
1. Should Story 15.8 (31-day metrics history) be un-deferred and implemented now, so Story 15.10 can proceed with true full parity?
2. Or should AC#1's parity gate be explicitly narrowed to exclude 31-day history (i.e. dashboard.py's history page is accepted as a known, permanent regression against the old tool), and if so, should dashboard.py's history-page code specifically be kept alive somewhere, or deleted anyway despite the gap?
3. Or should Story 15.10 itself stay parked (like 14.3 and 15.8 already are) until 15.8 is picked back up on its own schedule?

No files outside this new spec were modified. `sprint-status.yaml` was read but not written, per this run's explicit instruction that it is orchestrator-owned.

## Resolution (2026-09-17)

**Decision:** Question 1, answered yes — but as a separate, already-authored story rather than folded into this one. Story 15.8 is un-deferred via **Epic 17, Story 17.2** (`_bmad-output/implementation-artifacts/17-2-unpark-and-complete-story-15-8-31-day-metrics-history.md`, `ready-for-dev`), which executes 15.8's scope verbatim and moves both its own and 15.8's `sprint-status.yaml` entries to `done` on completion. This story (15.10) does not implement 15.8 itself; it waits on 17.2, then re-verifies the resulting `HistoryPage.tsx` against the running app as an ordinary Task 1 row before deletion proceeds.

**Sequencing:** Run Story 17.2 to `done` first (separate bmad-loop dispatch — this run is filtered to `story_filter: 15-10` and will not pick up 17.2 on its own). Once `sprint-status.yaml` shows `17-2-unpark-and-complete-story-15-8-31-day-metrics-history: done`, re-arm and resume this story.
