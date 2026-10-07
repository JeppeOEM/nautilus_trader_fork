---
title: 'DW-93/DW-216: remove bot_tui Bot-detail dead `o` deep-link'
type: 'chore'
created: '2026-10-07'
status: 'done'
final_revision: 'a652276751'
baseline_revision: '0ce8ca099366a1c5df8d4af06ff62fcd680abc1d'
review_loop_iteration: 0
followup_review_recommended: false
context: ['{project-root}/platform/CLAUDE.md']
warnings: []
---

<intent-contract>

## Intent

**Problem:** Bot-detail's `o` key opens `<DASHBOARD_BASE_URL>/bot/{bot_id}`, a route the web app does not have (`frontend/src/App.tsx` routes: `/`, `/chart/:iid`, `/history/:iid`, `/alerts`, `/docs/*`), so the TUI's only deep-link lands on a blank page (DW-93, DW-216). Human decision 2026-10-05: remove the key until a web bot view exists.

**Approach:** Delete the `o` key and everything that exists only to serve it. `_open_dashboard_bot` is the sole caller of `_open_url`, which is the sole user of the local-listener hand-off (`_open_via_local_listener`, `BOT_TUI_OPEN_URL_PORT`, `scripts/open_listener.go`), the browser launcher (`_launch_browser`, `_BROWSER_CHILD_SOURCE`, `_browser_tasks`), `bots_pane.osc52_copy_sequence` and `DASHBOARD_BASE_URL`; no other key uses any of it, so all of it is removed rather than left dead.

## Boundaries & Constraints

**Always:** Help text, footer hint and module/compose/architecture/CLAUDE.md docs stop advertising `o` and the hand-off. The DW-58 shutdown drain keeps working for listeners, the redraw loop and command publishes. Tests for removed code are removed; a regression test proves `o` in Bot-detail is now inert.

**Block If:** another key or module turns out to call `_open_url`, `_launch_browser`, `osc52_copy_sequence` or `open_listener.go`.

**Never:** edit the deferred-work ledger; edit `~/.zshrc` (outside the repo — note the needed desktop change in `platform/CLAUDE.md` instead); add a web `/bot/` route; touch `nautilus_trader/` or `crates/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| `o` in Bot-detail | bot open, press `o` | nothing happens: footer unchanged, no URL opened, no task spawned | No error expected |
| `:help` | open help | BOT DETAIL section lists no `o` line | No error expected |

</intent-contract>

## Code Map

- `platform/bot_tui/app.py` -- `o` dispatch in `_handle_bot_detail_key`, `_open_dashboard_bot`, `_open_url`, `_open_via_local_listener`, `_launch_browser`, `_BROWSER_CHILD_SOURCE`, `_browser_tasks`, `_dashboard_base_url`, footer/help text, module docstring, `socket` import, `_SHUTDOWN_GRACE_SECONDS`/`_shutdown_tasks` comments mentioning browser launches, `run()` shutdown call.
- `platform/bot_tui/bots_pane.py` -- `dashboard_bot_url`, `osc52_copy_sequence`, `base64` import, module docstring.
- `platform/scripts/open_listener.go` -- bot-only hand-off listener.
- `platform/bot_tui/tests/test_app_bot_detail.py`, `platform/bot_tui/tests/test_bots_pane.py` -- tests of the removed code.
- `platform/docker-compose.yml`, `platform/docker-compose.verify.yml` -- `DASHBOARD_BASE_URL`, `BOT_TUI_OPEN_URL_PORT` env, TERM comment listing `o`.
- `platform/ARCHITECTURE.md` (lines ~44, ~554), `platform/CLAUDE.md` (Desktop ↔ VPS `troll-tui`/`troll-down`) -- docs.

## Tasks & Acceptance

**Execution:**
- [x] `platform/bot_tui/app.py` -- remove the `o` branch, `_open_dashboard_bot`, `_open_url`, `_open_via_local_listener`, `_launch_browser`, `_BROWSER_CHILD_SOURCE`, `_browser_tasks`, `_dashboard_base_url`, unused imports; drop `o dashboard` from the footer and the `o` line from help; update docstrings/comments; `run()` passes only `tasks` to `_shutdown_tasks` -- nothing else uses them.
- [x] `platform/bot_tui/bots_pane.py` -- remove `dashboard_bot_url`, `osc52_copy_sequence`, `base64` import; fix module docstring -- dead after the key goes.
- [x] `platform/scripts/open_listener.go` -- delete -- its only client was `o`.
- [x] `platform/bot_tui/tests/test_app_bot_detail.py` -- delete tests of removed code (o footer, DW-66/67 launcher tests and fakes, dashboard-link raise test, local-listener tests and helper); assert footer lacks `o dashboard`; add an `o`-is-inert test and a help-has-no-`o` assertion; fix module docstring and imports.
- [x] `platform/bot_tui/tests/test_bots_pane.py` -- delete `dashboard_bot_url`/`osc52` tests.
- [x] `platform/docker-compose.yml`, `platform/docker-compose.verify.yml` -- remove `DASHBOARD_BASE_URL`, `BOT_TUI_OPEN_URL_PORT` and their comments from `bot_tui`; drop `o` from the TERM comment's key list.
- [x] `platform/ARCHITECTURE.md`, `platform/CLAUDE.md` -- remove the deep-link/dashboard dependency and the `troll-tui` listener description; add a migration note that `~/.zshrc`'s `troll-tui`/`troll-down` must drop starting/killing `open_listener.go` and the `-R` tunnel/`BOT_TUI_OPEN_URL_PORT`.

**Acceptance Criteria:**
- Given the tree after the change, when grepping `platform/` (excluding `.planning/`, `_bmad-output`) for `dashboard_bot_url|_open_dashboard_bot|_open_url|open_listener|BOT_TUI_OPEN_URL_PORT|DASHBOARD_BASE_URL|osc52|_launch_browser`, then only the CLAUDE.md migration note matches.
- Given the bot_tui suite, when run, then it passes with no warnings.

## Verification

**Commands:**
- `cd platform && python3 -m pytest bot_tui -q` -- expected: all pass.
- `cd platform && ruff check bot_tui && ruff format --check bot_tui && mypy bot_tui` -- expected: clean (mypy per repo config).

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 0, low 3)
- defer: 0
- reject: 11
- addressed_findings:
  - `[low]` `[patch]` PRD FR-27 still required the bot deep-link with no record of the descope -- added an `[AMENDED 2026-10-07]` tag to FR-27, matching the epics/spine amendments.
  - `[low]` `[patch]` `~/.zshrc` migration steps could break the tunnel (`-R` shares one `ssh` with the `-L` forwards) and left an already-running listener alive -- DEPLOY_CHECKLIST and the CLAUDE.md migration note now say to keep the `-L` forwards and run `pkill -f open_listener` once, and the checklist verifies with `pgrep`.
  - `[low]` `[patch]` docker-compose TERM comment still listed `d`, a key bot_tui no longer has -- removed.

## Auto Run Result

**Summary:** Follow-up review of the committed removal of bot_tui Bot-detail's `o` deep-link (DW-93, DW-216). Edge Case Hunter found no unhandled edge cases. Blind Hunter's findings led to three small doc patches. No code changed.

**Files changed (this pass):**
- `_bmad-output/planning-artifacts/prds/prd-nautilus_trader_fork-2026-07-01/prd.md` -- FR-27 amendment tag recording the descoped deep-link.
- `platform/docs/DEPLOY_CHECKLIST.md`, `platform/CLAUDE.md` -- safer `~/.zshrc` migration steps: keep the `-L` forwards, kill a running listener once.
- `platform/docker-compose.yml` -- stale `d` dropped from the TERM comment.

**Review:** 3 patches applied, 0 deferred, 11 rejected. Rejected: removal instead of building a web bot page (human decision 2026-10-05); the `commit: this change's` heading (the checklist's own convention); the trivially-true test assertion and help-section split fragility (already rejected in the first review); 100-column lines (ruff passes); UX-DR6/UX-DR5 and EXPERIENCE.md historical text (pre-existing; 25.1a's Coin-detail removal set the precedent of leaving the UX doc as written); spine markdown style; no changelog (the repo has none for platform/); deleted hand-off code (dead code, not kept).

**Follow-up review recommended:** false -- three localized low-severity doc edits.

**Verification:** `python3 -m pytest bot_tui -q` -- 374 passed. `docker compose -f docker-compose.yml config -q` -- OK. AC grep -- matches only the CLAUDE.md migration note (plus the DEPLOY_CHECKLIST operator entry). No Python touched in this pass, so ruff/mypy are unchanged from the first run.

**Residual risks:** `~/.zshrc` (outside the repo) still starts `open_listener.go` until the operator applies the DEPLOY_CHECKLIST entry.
