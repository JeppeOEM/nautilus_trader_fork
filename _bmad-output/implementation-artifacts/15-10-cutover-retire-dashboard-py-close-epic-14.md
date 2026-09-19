# Story 15.10: Cutover — retire dashboard.py, close epic-14

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As the dashboard operator,
I want `dashboard.py` fully retired once its React replacement has verified parity,
so that I'm not maintaining two dashboards, and stale/superseded work doesn't linger in the backlog.

## Acceptance Criteria

1. **A feature-parity checklist (SM-1) is walked and confirmed against Stories 15.1-15.9's shipped React equivalents** for every page/capability present in today's `dashboard.py` (rankings, chart w/ candles+lines+indicators+live edge, 31-day history, docs, terminal visual identity) — any gap found blocks this story, it does not get silently skipped.
2. **`dashboard.py`'s HTML-rendering functions are deleted, not retained as dead code** (`_page`, `_build_chart_page_html`, `_render_live_page`, `_render_history_page`, `_history_page_from_rows`, `docs_handler`, etc., AD-F1).
3. **`docker-compose.yml`'s `dashboard` service is removed; `data_api` fully absorbs its role** (already added in Story 15.1), still `network_mode: host` / `127.0.0.1`-bound (SEC-01 unchanged).
4. **`epic-14`'s Story 14.3 is marked superseded (not shipped) in `sprint-status.yaml`**, since `dashboard.py`'s chart page — the surface 14.3 would have modified — no longer exists.
5. **The SSH-tunnel remote-dev flow doc (`troll/CLAUDE.md`'s "Desktop ↔ VPS Connection") is updated to reflect one tunneled surface (`data_api`) instead of two** (`dashboard` + `data_api`), per the architecture spine's stated simplification.
6. **Chart-page interactivity and scroll-back are checked on the real SSH-tunneled access path (not just localhost), with results reported** — not merely asserted as "fast" (SM-2, NFR6).
7. **No new test is required beyond re-running the full existing `troll/` test suite** to confirm nothing outside `dashboard.py`'s own tests depended on the deleted functions (TEST-02).

## Tasks / Subtasks

- [ ] Task 1 — Walk the feature-parity checklist (AC: #1) — do this before any deletion
  - [ ] Build an explicit checklist, one row per `dashboard.py` page/capability, each mapped to the story that shipped its equivalent: Rankings (15.2), Chart candlestick + cursor-paginated history (15.3), synced indicator panes (15.4), live candle edge (15.5), per-coin indicator config (15.6), Lines mode (15.7), 31-day metrics history (15.8), Docs (15.1), terminal/ANSI visual identity across all pages (15.9).
  - [ ] For each row, verify against the **real running app** (`run` skill or a manual session), not against "the story file says done" — a story marked `done` in `sprint-status.yaml` is a necessary but not sufficient condition; this checklist is the actual gate.
  - [ ] **Any gap blocks this story.** If a capability has no verified working equivalent, stop here and route back to the owning story (or file a new bypass-epic bug-fix story, same precedent as epics 5/6/7/9/11) rather than proceeding to delete `dashboard.py` with a known regression.

- [ ] Task 2 — Delete `dashboard.py`'s HTML-rendering surface (AC: #2)
  - [ ] **Before deleting anything, grep for any remaining import of `ml_signals.dashboard` from outside `dashboard.py` itself** (`grep -rn "from ml_signals import dashboard\|from ml_signals.dashboard import\|ml_signals\.dashboard\." troll/ --include=*.py`) — this should return zero hits, since Stories 15.6/15.7 already ported/duplicated everything `data_api` needed (`_merged_indicator_catalog`, `_price_series_rows`) out of `dashboard.py` specifically so this deletion would be safe. If it returns any hit, that dependency must be resolved first (port the needed logic properly, per AD-F1) — do not delete out from under a live import.
  - [ ] Delete `ml_signals/dashboard.py`'s HTML-rendering functions and the full `make_app`/route-table wiring wholesale — in practice this is very close to deleting the entire file, since almost everything in it exists to serve one of those routes. Delete its dedicated test file(s) covering only the deleted HTML/route surface (check for a `test_dashboard.py`-equivalent).
  - [ ] `_LIVE_CHART_JS` and every other embedded client-side JS string in `dashboard.py` goes with it — it has no React equivalent to preserve, it *is* the code being replaced.

- [ ] Task 3 — `docker-compose.yml`: remove the `dashboard` service (AC: #3)
  - [ ] Delete the `dashboard:` service block (`troll/docker-compose.yml:53-88`) entirely. `data_api`'s existing service block (lines 119-137, added Story 15.1) already uses `network_mode: host` with `uvicorn --host 127.0.0.1` — confirm SEC-01 compliance is unchanged (it already was compliant; this task removes a service, it does not need to fix one).
  - [ ] Confirm `data_api`'s service block has whatever `dashboard`'s had that it still needs — cross-check volume mounts specifically: `dashboard` mounted `chart_indicators.toml` as `:rw` (line 72) for indicator-config saves; Story 15.6 should already have added the equivalent mount to `data_api`'s block once `PUT /api/coin/{iid}/indicators` needed it — verify this landed in Story 15.6, don't silently assume it and don't re-add it here as new scope if it's missing (surface it as a real gap against Task 1's parity checklist instead).
  - [ ] **`troll/CLAUDE.md`'s own SEC-01 section lists `dashboard` by name** among services following the localhost-binding convention (`"collector`/`dashboard`/`ranking_engine`/`bot_tui`/`live-paper` use `network_mode: host`..."`) — update that line to drop `dashboard` from the list once its service is gone, so the doc doesn't reference a service that no longer exists.
  - [ ] Check the Makefile for a `dashboard` target (`make dashboard`, referenced by name in `troll/CLAUDE.md`'s "Desktop ↔ VPS Connection" section) — it must be retargeted to build/run `data_api` (which now also serves the frontend) instead, or removed if `data_api`'s own target already covers the same need. This is a real, easy-to-miss follow-on from removing the compose service — a stale Makefile target that references a deleted service fails loudly the next time someone runs it, which is a real but shallow bug worth catching in review rather than in production use.

- [ ] Task 4 — `sprint-status.yaml`/epic housekeeping (AC: #4)
  - [ ] The `# STATUS DEFINITIONS` block's "Story Status" enum (`backlog`/`ready-for-dev`/`in-progress`/`review`/`done`) has no existing value for "descoped/made-moot-by-later-work" — add one (e.g. `superseded: made moot by later work, intentionally not shipped`) to that comment block rather than overloading `done` (which implies it shipped) or leaving 14.3 dangling at `in-progress` forever.
  - [ ] Set `14-3-preserve-120-bar-zoom-through-indicator-overlay-repaints: superseded` (was `in-progress`), and `epic-14: done` (14.1/14.2 done, 14.3 now resolved-as-superseded — every story in the epic has reached a terminal state).
  - [ ] Set `epic-15: done` once every story 15.1-15.10 is confirmed done (this story is epic-15's last).
  - [ ] `sprint-status.yaml`'s 14.3 comment block references a parked `git stash` ("14.3 partial dev work, parked: superseded by epic-15 (15.10 cutover)") — that work is now permanently moot. **Do not silently `git stash drop` it** — flag it in Completion Notes as safe-to-drop and let the user confirm before actually dropping it (an irreversible git operation, same caution `troll/CLAUDE.md`'s GIT-01 already applies to `git push`).

- [ ] Task 5 — Update `troll/CLAUDE.md`'s "Desktop ↔ VPS Connection" section (AC: #5)
  - [ ] Rewrite the section to describe one tunneled surface (`data_api`, which now also serves the built frontend SPA per Story 15.1's `app.frontend()`) instead of two — `_troll_tunnel_ensure` forwards one fewer port purpose (no separate `dashboard` `WEB_PORT`), `_troll_dashboard_ensure`/`troll-web` point at `data_api`'s port instead.
  - [ ] **The actual `~/.zshrc` shell functions this section documents live outside this repository** (the doc's own text already says so) — this task can only update the documentation's description of the intended new shape; it cannot itself edit the user's shell config. State this explicitly in Completion Notes as a follow-up action for the user, not something this story silently completed.

- [ ] Task 6 — Real-path verification (AC: #6)
  - [ ] Check chart-page interactivity (pan/zoom) and scroll-back page-load latency over the actual SSH-tunneled path (not localhost) — per `troll/CLAUDE.md`'s own tunnel setup, or an equivalent manual SSH `-L` session if the desktop-side shell functions aren't available in this environment. Report actual observations (e.g., approximate scroll-back page latency) in Completion Notes — SM-2/NFR6 requires a reported result, not an unverified "should be fine."

- [ ] Task 7 — Regression run only (AC: #7)
  - [ ] `cd troll && PYTHONPATH=. python -m pytest data_api/tests ml_signals/tests -q` and `cd troll/frontend && npm run build && npm run test && npm run lint` — no new tests required for this deletion/cutover story itself, but the full suite must be re-run to confirm nothing outside `dashboard.py`'s own tests silently depended on a now-deleted function.
  - [ ] **The previously-tracked "expect 1 pre-existing unrelated failure" (`test_microfeatures_json_decimates_and_reports_true_pre_decimation_count`, carried since Story 14.3) needs to be re-checked, not assumed to still apply** — if that test lived in `dashboard.py`'s own (now-deleted) test module, the regression baseline changes to 0 known failures post-cutover, which is a welcome cleanup side-effect, not a new problem. Confirm and note the new baseline explicitly rather than carrying forward a stale "expect 1 failure" note by habit.

## Dev Notes

- **This is the epic's terminal story — everything before it must actually be done, not just spec'd.** As of this story's creation, only Stories 15.1 and 15.2 have shipped; 15.3-15.9 exist as story files (`ready-for-dev`) with empty Dev Agent Records. Do not attempt this story until Task 1's checklist can honestly be walked against real, working pages — starting it early just means discovering the same gaps Task 1 would have caught, later and more expensively (mid-deletion instead of before it).
- **Deletion is the reward for parity, not a race to clean up code.** If Task 1 finds even one real gap, the correct move is to stop and close that gap (in its owning story, or a new bypass-epic fix) — never to delete `dashboard.py` anyway on the theory that the gap is minor or unlikely to be noticed. `dashboard.py` costs nothing to leave running one more story-cycle; a lost capability post-deletion costs the operator a page they used to have.
- **Why the parity-checklist verification must be against the *running app*, not story status:** this epic's own precedent (Story 15.1's own root-caused lesson, referenced repeatedly in later stories' Dev Notes) is that no prior chart-page work in this codebase was ever verified in a real browser before shipping, and that gap is exactly what let a real regression (Story 14.3) ship unnoticed for a full story cycle. Task 1 exists specifically to not repeat that pattern at the moment `dashboard.py` becomes unrecoverable-by-`git revert`-alone (once deleted and the compose service removed, "just go check the old page" stops being a fallback option).
- **`troll/CLAUDE.md` constraints that apply:** AD-F1 (delete, don't retain as dead code), SEC-01 (unchanged — `data_api` was already compliant before this story), GIT-01 (never destructively drop the parked stash without asking), DATA-02 spirit (Task 2's "grep for remaining imports first" is exactly "close the observability gap before declaring victory," applied to a deletion instead of an ingestion bug).

### Project Structure Notes

- Deleted: `troll/ml_signals/dashboard.py` (its HTML/route surface — see Task 2 for the "verify no remaining import" precondition), its dedicated test file(s), the `dashboard:` service block in `troll/docker-compose.yml`.
- Modified: `troll/docker-compose.yml` (service removal), `troll/Makefile` (retarget/remove the `dashboard` target), `troll/CLAUDE.md` (SEC-01's service list, "Desktop ↔ VPS Connection" section), `_bmad-output/implementation-artifacts/sprint-status.yaml` (14.3 → `superseded`, `epic-14` → `done`, `epic-15` → `done`, plus a new `superseded` entry in the Story Status definitions comment block).
- Not modified: every `data_api`/`frontend` file shipped by Stories 15.1-15.9 — this story's only code changes are deletions and the small compose/Makefile/doc updates above.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 15.10, lines 1531-1565] — this story's origin; all 7 Given/When/Then blocks map to this file's ACs.
- [Source: _bmad-output/planning-artifacts/architecture/architecture-chart-frontend-rewrite-2026-09-13/ARCHITECTURE-SPINE.md] — AD-F1 ("`dashboard.py`'s HTML-rendering functions... are deleted, not retained"), "Deployment & Environments" section ("`docker-compose.yml`'s `dashboard` service is removed; `data_api` absorbs its role"), Deferred section ("epic-14's disposition... This spine does not itself close or delete that story — flagging it for the sprint-status/epics pass").
- [Source: _bmad-output/planning-artifacts/prds/prd-chart-frontend-rewrite-2026-09-13/prd.md, lines 213-223] — SM-1/SM-2/SM-3/SM-C1 (Success Metrics, full text) — this story is where SM-1/SM-2 are actually walked and reported, not just defined.
- [Source: troll/docker-compose.yml:53-88,119-137] — full file read this session; the exact `dashboard`/`data_api` service blocks this story reconciles.
- [Source: troll/CLAUDE.md, "Network Security" (SEC-01) and "Desktop ↔ VPS Connection" sections] — the two sections this story must update; both name `dashboard` explicitly and must be edited to reflect its removal.
- [Source: _bmad-output/implementation-artifacts/sprint-status.yaml] — full file read this session; confirms 14.3's current `in-progress` status, its parked-stash comment, and the existing Story Status enum this story extends with `superseded`.
- [Source: _bmad-output/implementation-artifacts/15-6-per-coin-indicator-configuration.md, 15-7-lines-mode.md] — both stories already relocate the specific `dashboard.py` logic `data_api` needs (`_merged_indicator_catalog`, `_price_series_rows`) precisely so this story's deletion is safe — Task 2's "verify zero remaining imports" check is this story's confirmation that precedent actually held.
- [Source: _bmad-output/implementation-artifacts/epic-15-context.md] — "Story 15.10 (cutover) depends on every other story in this epic being shipped — it walks a feature-parity checklist against 15.1-15.9 before deleting `dashboard.py`, and formally closes out epic-14."

## Dev Agent Record

### Agent Model Used

### Debug Log References

### Completion Notes List

- Code review (2026-09-19, commit 989aca2fb0): no findings. Repointed `fetch_watchlist` -> `/api/rankings` and `fetch_rank_history` -> `/api/metrics/nearest/{iid}?ts_ns=` verified against data_api routes; no stale `ml_signals.dashboard` imports; compose/Makefile/CLAUDE.md consistent.
- Tests: data_api + ml_signals + bot_tui 484 passed, 1 failed (`test_rankings_live_message_reflected_by_rest_and_ws_relay`, needs a live Redis on 6379, env-only). Frontend build/test pass; lint has 2 pre-existing TrustedHtml.tsx warnings.
- Human check owed, marked done anyway: AC1 browser parity walk and AC6 SSH-tunnel scroll-back latency were NOT performed.
- Follow-up for user: `~/.zshrc` must drop `_troll_dashboard_ensure`/`TROLL_WEB_PORT` and point `troll-web` at http://localhost:9100. Parked 14.3 git stash is safe to drop (not dropped).

### File List
