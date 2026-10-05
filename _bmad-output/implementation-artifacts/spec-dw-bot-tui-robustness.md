---
title: 'bot_tui robustness bundle (DW-58, DW-60, DW-66, DW-67, DW-74, DW-85, DW-92, DW-258)'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
baseline_revision: 'e936ee9161c43fb3a496ad8cc1fda578f00c0ed2'
review_loop_iteration: 0
followup_review_recommended: false
final_revision: c96ec4b4b2
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals', 'oversized']
---

<intent-contract>

## Intent

**Problem:** `platform/bot_tui` has eight deferred robustness defects: quit never awaits the cancelled listener/redraw tasks (DW-58); every receive-time staleness check uses wall-clock `time.time()`, so an NTP step flips stale/fresh or hides staleness (DW-60); `webbrowser.open()` runs on the loop thread (DW-67) with inherited stdio, so a browser launcher can write into urwid's screen (DW-66); `s` decides start/stop from a stale bot's last-known `running` and sends a command no live supervisor consumes (DW-74); Bot-detail colors PnL by `str.index()` into rendered text (DW-85); invariants are `assert`s stripped under `python -O` (DW-92); the Bots and Collector panes advertise `j/k` but only up/down work (DW-258).

**Approach:** Fix each at its cause inside `bot_tui`: a testable shutdown drain, `time.monotonic()` for every locally-stamped receive/send time, the browser launched in a child `python -m webbrowser` process with all stdio on `/dev/null`, a staleness refusal for `s`, structured detail-line segments, explicit raising guards, and `_VimListBox` for both panes.

## Boundaries & Constraints

**Always:** Only wire timestamps (bots' `started_at`, `last_fill_at`, incidents, archive times) stay wall-clock; every local receive/send stamp and its age comparison is monotonic. Refusals echo the reason in the footer and send nothing, mirroring the Collector pane's `cannot <action> <id>: <reason>`. A violated invariant fails loudly (raise naming it), never builds a wrong value. TUI-01 persistence (in-place walker mutation) is preserved. Tests per fix (TEST-01/03); no new warnings (TEST-04).

**Block If:** a fix would require changing `bots/`, a Redis wire contract, or `nautilus_trader/`/`crates/`.

**Never:** No optimistic local running/stopped flip (Story 4.4 AC3). No process-wide fd redirection (`dup2` on fd 1/2) — it races urwid's own writes. No new dependency. Do not edit the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Quit, idle | `:q`, listeners running | every task cancelled and awaited, loop closed, no "Task was destroyed" warning | none |
| Quit, publish in flight | `:q` right after a `stop` send | publish gets a bounded grace (`_SHUTDOWN_GRACE_SECONDS`) to finish before cancel | still-pending ones cancelled and ledgered (`error_ledger.record`) |
| Wall clock steps back 1 h | bot heard 1 s ago (monotonic) | row not stale | none |
| `s` on stale bot | last status older than `_BOT_STALE_SECONDS`, `running` true or false | footer `cannot stop/start <id>: no bots:status for over 15s (bot down?)`, no prompt, nothing sent | none |
| Bot goes stale during stop prompt | `stop`+enter after staleness | same refusal, nothing sent | none |
| `o` pressed | no listener port | child process spawned off-loop with stdin/stdout/stderr = DEVNULL in a new session; footer + OSC 52 as before | non-zero exit / spawn `OSError` logged as warning (headless no-browser is an expected environment, not our fault) |
| `j`/`k` on Bots or Collector pane | populated pane | focus moves down/up like the arrows | none |

</intent-contract>

## Code Map

- `platform/bot_tui/app.py` -- `run()` (shutdown), `_open_url` (browser), `_toggle_bot`/`_submit_stop_confirm` (stale refusal), `_build_bot_detail_body` (PnL), every `assert`, `_refresh_bots_body`/`_refresh_collector_body` (ListBox class), `time.time()` call sites feeding staleness, HELP/footers.
- `platform/bot_tui/bots_state.py`, `collector_state.py`, `archive_state.py`, `bot_history_state.py`, `bot_incidents_state.py`, `markets_state.py` -- receive stamps + `is_stale`/`_age`; collector sent-add times.
- `platform/bot_tui/bots_pane.py` -- `bot_detail_lines` (text) gains a segments twin.
- `platform/bot_tui/tests/` -- conftest resets, per-module tests.

## Tasks & Acceptance

**Execution:**
- [x] `platform/bot_tui/bots_state.py`, `collector_state.py`, `archive_state.py`, `bot_history_state.py`, `bot_incidents_state.py`, `markets_state.py` -- stamp with `time.monotonic()`; default `now` is `time.monotonic()`; "never received" is an absent key / `None`, not a `0.0` sentinel; docstrings say the clock -- DW-60.
- [x] `platform/bot_tui/app.py` -- pass a monotonic `now` to every staleness/sent-add consumer (`_refresh_bots_body` keeps a separate wall-clock `now` for uptime; markets/add paths and `record_sent_add` use monotonic) -- DW-60.
- [x] `platform/bot_tui/app.py` -- `_shutdown_tasks(loop, tasks, background)` helper: grace-wait background tasks, cancel and await everything, ledger cancelled background work, `shutdown_asyncgens`, `close`; `run()` calls it in `finally` -- DW-58.
- [x] `platform/bot_tui/app.py` -- `_open_url` schedules `_launch_browser(url)` (via `asyncio.create_subprocess_exec(sys.executable, "-c", _BROWSER_CHILD_SOURCE, url, stdio DEVNULL, start_new_session=True)`, the child running `webbrowser.open_new_tab` and exiting 1 when no browser took the url) as a tracked background task; no `webbrowser` import left in the loop thread -- DW-66/67.
- [x] `platform/bot_tui/app.py` -- `_toggle_bot` and `_submit_stop_confirm` refuse when `bots_state.is_stale(bot_id)`; HELP/footer say so -- DW-74.
- [x] `platform/bot_tui/bots_pane.py` -- `bot_detail_segments(row, now) -> list[list[Segment]]` (PnL segment attr-tagged); `bot_detail_lines` = their joined text; `app.py` renders segments, no `.index()` -- DW-85.
- [x] `platform/bot_tui/app.py` -- replace every `assert` with an explicit guard (a typed `_expect(widget, cls)` raising `TypeError`, `RuntimeError` for a missing id) -- DW-92.
- [x] `platform/bot_tui/app.py` -- Bots and Collector ListBoxes are `_VimListBox` -- DW-258.
- [x] `platform/bot_tui/tests/` -- tests for each matrix row and AC, conftest/fixtures updated for monotonic stamps.

**Acceptance Criteria:**
- Given `python -O`, when `_open_dashboard_bot` runs with no open bot, then it raises instead of opening `/bot/None`.
- Given the Bot-detail view, when PnL renders, then its colored segment comes from `bot_detail_segments`, and `bot_detail_lines` output is unchanged.
- Given the whole `bot_tui` package, when grepped, then no `assert` statement remains in non-test code and no staleness path reads `time.time()`.

## Spec Change Log

- Resume after power loss (2026-10-05): prior attempt restored from `dw-bot-tui-robustness-prior-attempt`, baseline restamped (unchanged, `e936ee9161`). Every task and AC re-verified against the code; the one failing test still asserted the earlier `-m webbrowser -t` argv, so it now asserts the `-c` child and is joined by a hermetic real-subprocess test of the child's exit code (PATH = only a fake launcher, no display). Execution task text updated to the `-c` form; the intent contract's "`python -m webbrowser` process" wording stands for the same child-process approach. Three new unused `type: ignore` comments removed (mypy errors in bot_tui now below baseline). KEEP: everything else in the prior attempt as is.

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4: (high 0, medium 1, low 3)
- defer: 0
- reject: 13: (high 0, medium 1, low 12)
- addressed_findings:
  - `[medium]` `[patch]` `_shutdown_tasks`'s `loop.shutdown_default_executor()` was unbounded (a resolver thread stuck on dead DNS could hold `:q` for minutes): now `_shutdown_default_executor` bounds it by `_SHUTDOWN_GRACE_SECONDS` and ledgers a timed-out join (`bot_tui.shutdown_stuck`); test `test_a_busy_default_executor_does_not_hold_the_quit`.
  - `[low]` `[patch]` A cut-short publish that ignored its cancel was ledgered twice (stuck + cancelled), and one that completed despite the cancel was ledgered "may never have been sent": `shutdown_cancelled` now only for tasks that really ended cancelled; test `test_a_publish_ignoring_its_cancel_is_ledgered_once_as_stuck`.
  - `[low]` `[patch]` `test_o_key_sets_footer_to_bot_dashboard_url` gathered `_background_tasks` while the launch lives in `_browser_tasks` (passed by scheduling luck): now gathers `_browser_tasks`.
  - `[low]` `[patch]` The shutdown tests' fake `error_ledger.record` took two arguments though `_ledger_failed_tasks` passes three, and the failed-task path was untested: fake takes `exc`; test `test_a_task_that_failed_is_ledgered_with_its_exception`.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 2: (high 0, medium 1, low 1)
- defer: 0
- reject: 17: (high 0, medium 2, low 15)
- addressed_findings:
  - `[medium]` `[patch]` urwid 4.0.6's `AsyncioEventLoop.run()` leaves its exception handler installed, and that handler `loop.stop()`s on any reported exception, so a done-callback or async-generator finaliser failing mid-drain made a `run_until_complete` in `_shutdown_tasks` raise and skip the ledgering and `loop.close()`. The drain now restores asyncio's default handler first; test `test_a_stopping_exception_handler_left_installed_does_not_abort_the_drain` (fails without the fix).
  - `[low]` `[patch]` `_launch_browser`'s cancel branch (kill + reap the launcher at quit) had no test: `test_a_quit_during_a_browser_launch_kills_and_reaps_the_launcher`.

## Design Notes

Prior attempt: dev attempt 2 of sweep run 20261005-113530-e9c6 was cut off by a machine power loss at 12:22 with most of the bundle implemented (19 files, +751/-159, plus `platform/bot_tui/tests/test_app_shutdown.py`). That work, this spec included, is pinned on branch `dw-bot-tui-robustness-prior-attempt`: restore it with `git read-tree -m -u HEAD dw-bot-tui-robustness-prior-attempt` + `git reset -q`, then re-verify every task and AC rather than trusting checkboxes.

Refusing `s` in both directions (not just stop): `bots/application/supervise.py` heartbeats every 5 s whether or not the bot runs, on the same connection that consumes `bots:control`, so a stale row means no live supervisor -- a `start` goes into the void exactly like a `stop`, and the row's direction is unknowable. This is the "disable `s` once the stale badge is active" fix DW-74 names.

Browser child process: a child Python running `webbrowser.open_new_tab(url)` (`-c`, not `python -m webbrowser -t`, whose CLI exits 0 even when no browser opened, which would hide the failure warning) keeps the stdlib's backend selection while every grandchild (`xdg-open`, a text browser like `w3m`) inherits `/dev/null` and no controlling terminal, so nothing can paint over urwid; the spawn is async and reaped by asyncio.

## Verification

**Commands:**
- `cd platform && python3 -m pytest bot_tui/tests -q -W error::RuntimeWarning` -- expected: all pass, no warnings
- `cd platform && ruff check bot_tui && ruff format --check bot_tui && mypy bot_tui` -- expected: clean (mypy as configured by the repo)
- `grep -n "assert \|time\.time()" platform/bot_tui/*.py` -- expected: no assert; `time.time()` only at wall-clock (wire-timestamp) sites


## Auto Run Result

Status: done
Blocking condition: none

**Summary:** Follow-up review pass on the done bundle (DW-58, DW-60, DW-66/67, DW-74, DW-85, DW-92, DW-258, implemented in ab5f5e33fd). A fresh Blind Hunter + Edge Case Hunter pass over the full diff since `e936ee9161` found one real drain defect: the exception handler urwid leaves installed could abort the quit drain. Fixed, plus a test for the untested browser-cancel branch.

**Files changed:**
- `platform/bot_tui/app.py` -- `_shutdown_tasks` restores the default exception handler before draining.
- `platform/bot_tui/tests/test_app_shutdown.py` -- drain survives a loop-stopping handler.
- `platform/bot_tui/tests/test_app_bot_detail.py` -- quit mid browser launch kills and reaps the launcher.

**Review findings:** 2 patches applied, 0 deferred, 17 rejected. Among the rejected: a slow quit (four separate 2 s bounds, each one documented); `o` launchers piling up (residual risk already documented); `publish_control` swallowing its own errors (existed before this change); raising from input handlers (fail-loud behaviour the spec chose); `bot_detail_lines` kept (the spec requires it); and suspend-blind `CLOCK_MONOTONIC` (the spec mandates `time.monotonic()`, and the TUI runs on an always-on host).

**Verification:**
- `cd platform && python3 -m pytest bot_tui/tests -q -W error::RuntimeWarning -W error::ResourceWarning` -- 387 passed.
- `cd platform && python3 -m pytest tests/test_boundaries.py -q` -- 100 passed.
- `uv run ruff check platform/bot_tui` / `ruff format --check` -- clean.
- `uv run mypy platform/bot_tui` -- 26 errors, unchanged from the prior pass (all pre-existing classes).
- `grep -n "assert \|time\.time()" platform/bot_tui/*.py` -- no assert; `time.time()` only at the three wire-timestamp sites plus one docstring.

**Residual risks:** unchanged from the prior run: a blocking `BROWSER` launcher holds one task per `o` until quit; a task ignoring cancellation past the grace is abandoned ("Task was destroyed" at loop close); a resolver thread stuck past the grace delays process exit (`Known limit:` in `_shutdown_default_executor`).
