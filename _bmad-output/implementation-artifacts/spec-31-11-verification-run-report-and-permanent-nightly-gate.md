---
title: 'Story 31.11: The verification run, the report and the permanent nightly gate'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_revision: '557e50a285'
final_revision: 'd40fc06af9'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
  - '{project-root}/_bmad-output/implementation-artifacts/epic-31-context.md'
  - '{project-root}/platform/docs/DATA_DICTIONARY.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** The six verifiers (31.2 conservation, 31.4 trades, 31.5 book, 31.6 derivs, 31.7 catalog, 31.8 candles) exist, but:
- none of them runs on its own;
- no verdict is recorded per data type × instrument over the soak;
- the nightly saga has no verification step.

So trust in the data is a one-off smoke, not a measured, continuously re-earned fact. On top of that, the story's soak precondition (one full clean closed UTC day) is not met as of 2026-09-30:
- 2026-09-29 ran only 12:59:19Z-20:42:04Z (then the host suspended, D-128);
- 2026-09-30 carries the stack stop 11:23-16:00Z and the 31.10 chaos windows, and has not closed.

**Approach:**
1. Add `verify_day` as the nightly saga's last step: an `archive` composition root that runs each verifier as a child process over the closed day, reduces each JSON report to a per-type verdict (a pure `verification.domain.verdict`), ledgers each failure and hands the verdicts to the scheduler, which keeps them as `verification_days` in `state.json` and `archive:status`.
2. Wire the step into the verify stack and run it over the best closed data available.
3. Write the verdict table from those runs, with the unmet precondition registered OPEN.
4. Close the epic's docs.

## Boundaries & Constraints

**Always:**
- **`archive/verify_day.py` (new composition root):**
  - Usage: `python -m archive.verify_day --catalog C --candles-dir X --venue V --day D --result-file F`.
  - It reads `VERIFY_DATA_DIR` (the raw root) and `VERIFY_DATA_API_URL` (optional) from its environment.
  - **Reference-data gate:** if the venue is not BYBIT or HYPERLIQUID, or `VERIFY_DATA_DIR` is unset, or `<VERIFY_DATA_DIR>/raw/<venue lower>/*/<D>T*.jsonl.zst` matches nothing, it writes `{"venue", "day", "verification": "no reference data", "reason"}`, logs one INFO line and exits 0. Nothing is ledgered: this is not an error.
  - **Otherwise, per tool** in `TOOLS = (conservation, trades, book, derivs, catalog, candles)`:
    - Run the child `[sys.executable, -m, verification.<tool>, --venue, V, --day, D, --json, --catalog, C, --raw-dir, R, ...]`.
    - Extra arguments: trades `--stage rebuilt`; catalog `--candles X --scratch-dir <VERIFY_DATA_DIR>/scratch/verify_day`; candles `--candles X` plus `--data-api URL` when `VERIFY_DATA_API_URL` is set, else `--no-served`.
    - Parse stdout as JSON, then `summarise`.
    - The runner and clock are injected. Each child has `TOOL_TIMEOUT_S` = 50 min, so 6 × 50 min stays under the scheduler's 360 min step timeout.
  - **Exit:** 0 when every type passed, 2 (`FINDINGS`) otherwise. It never exits 1, so a crash or an unreadable result can never fail the saga or hold the watermark: an unexpected exception is ledgered and the step still exits 2, with `"verification": "error"`.
  - **Ledger:** each non-passed type is one entry at the site `archive.verify_day`, naming the tool, the verdict, the failing count, the failing instruments and the missing raw files. The step's own job ledger is `error_ledger.job_service("verify_day", "archive", venue)`.
- **`verification/domain/verdict.py` (pure, stdlib only):**
  - `TypeVerdict(tool, verdict: pass|fail|refused, failing, inputs_missing, instruments: {iid: {"passed", "failing"}})`.
  - `summarise(tool, exit_code, body | None)`:
    - `pass` iff exit 0 and `body["passed"]` (candles: `failing == 0` and served checked, else `fail`);
    - `refused` when there is no parseable body.
  - `day_verdict(types, checked_at) -> {"verification": "verified" | "findings", "checked_at", "types": {...}}`.
  - Every tool's `report_json` gains a per-instrument `failing` taken from its own domain count (a new `failing` property where one is missing: conservation's `InstrumentReport`, trades' `InstrumentTrades`, derivs' `InstrumentDerivs`), so the reduction never re-derives a predicate (SSOT-01).
  - Catalog's instruments are read from its `parity` and `candles` entries; `failing` is the top-level count.
- **Saga:**
  - `archive.nightly.steps` appends `Step("verify_day", ..., verdict_file=<result_file's sibling verify_result.json>)` after `prune_catalog`. It is last so that it can never gate pruning or a later step; it still runs after `compare_klines`, as the AC requires.
  - `run_steps` reads the verdict file into `StepResult.verification` via `read_verification_result(path, venue, day)`. A missing or invalid file gives `{"verification": "result unreadable"}` and a `nightly.verify_day` ledger entry; the step's code is unchanged.
  - `ArchiveScheduler._step_done` stores the verdict in `SchedulerState.verification_days[venue][day]`, keeping the newest `VERIFICATION_DAYS_KEPT` = 14 per venue.
  - `state_to_json` and `state_from_json` carry it; a state without the key loads as empty.
  - `status()` publishes `"verification_days"`.
  - The docstrings say this is an informational verdict record: reconcile and prune never read it, and `verified_days` remains the only gating day status (AD-D9 amended).
- **Boundaries:** `tests/test_boundaries.py` gets `VERIFICATION_IMPORTERS = {"archive.verify_day"}` and `COMPOSITION_ROOTS["archive.verify_day"] = {VERIFICATION}`. `archive.verify_day` imports only `verification.domain.verdict`; the tools are reached through argv only.
- **Verify stack wiring (`docker-compose.verify.yml`, the `archive` service only; the base file is unchanged):**
  - Environment: `VERIFY_DATA_DIR=/app/verify_data`, `VERIFY_DATA_API_URL=http://127.0.0.1:${DATA_API_PORT:-29100}`, and `BYBIT_COLLECTOR_CONFIG`/`HYPERLIQUID_COLLECTOR_CONFIG` pointing at the mounted venue directories.
  - Volumes: `./data/verification/raw:/app/verify_data/raw:ro`, `./data/verification/scratch:/app/verify_data/scratch`, `./data/coverage:/app/coverage:ro`, and the two venue config directories read-only.
  - The Makefile's `VERIFY_DATA_DIRS` gains `verification/scratch`; `tests/test_compose_verify.py` pins the wiring.
- **Code rules:**
  - LGPL header and full typing.
  - Functions of about 30 lines or fewer, complexity ≤ 10.
  - No module-level mutable state, no new dependency.
  - The verdict reduction ships a planted-defect test: a report with a failing instrument must not summarise to `pass`.
- **Runs (agent, verify project only; never `platform`):**
  1. Rebuild the verify `data_api`, `ranking_engine` and `archive` images and recreate them with `--no-deps` (the owed 31-9 verify-stack item). Log each image redeploy as a `deploy` line in the chaos scenario log, per the 31.10 rule.
  2. Run `docker exec verify-archive python3 -m archive.verify_day` for both venues over 2026-09-29 (the only closed day with soak data).
  3. Run `verification.conservation --start/--end` over 2026-09-30's clean windows, with the chaos windows and the 11:23-16:00Z stop excluded (DEPLOY_CHECKLIST 31-10).
  4. Classify every failing count as either a partial-day artefact (before the soak, after the suspend: D-76/D-128) or a finding.
- **Report (`docs/VERIFICATION_REPORT.md`):**
  - One verdict per data type × instrument: VERIFIED (0 unexplained inside the soak window), DEVIATION (with its audit row) or OPEN.
  - Each row carries the numbers, the window, the code revision and a repro command.
  - A header states that the precondition is unmet: the verdict window is 2026-09-29 12:59:19Z-20:42:04Z plus 2026-09-30's conservation windows, and the full-day verdict is left to the nightly `verify_day`.
  - D-113, D-129, D-133/D-134 and D-135 are shown OPEN, each with its recommended option (DEPLOY_CHECKLIST 31-9). Nothing waits on them.
- **Audit and docs:**
  - `DATA_INTEGRITY_AUDIT.md`: a row for every new finding (D-138 onward), including the unmet full-day precondition (OPEN, upgrade path: the first nightly `verification_days` of a clean day).
  - `DATA_DICTIONARY.md`: a new §1.24 on the verification context as a whole (tools, verdict classes, `verify_day`, `verification_days`, where each is documented, and the coverage record at §1.16), and §6 updated for the step.
  - `platform/CLAUDE.md` DATA-02 names the reference recorder, the `verification.*` tools and `archive.verify_day` as the standing independent source.
  - `DEPLOY_CHECKLIST.md` gains entry 31-11, covering:
    - the recorder budget on nifelheim (2 vCPU / 3.7 GB: recorder-on-VPS vs recorder-on-desktop, with the measured footprint and a recommendation);
    - the VPS rollout of the 31.2/31.3/31.5/31.8 fixes (and the archive image, for `verify_day`);
    - reading `verification_days` for the first full clean day (2026-10-01 or later), then filling the report's full-day column and closing the precondition row.

**Block If:** none. OPS-01 binds: every human step is deferred to DEPLOY_CHECKLIST 31-11, and the story finalizes `done`. It never writes `operator_actions` and never sets `awaiting-operator`.

**Never:**
- Touch the production (`platform`) compose project, `nautilus_trader/`, `crates/` or `sprint-status.yaml`.
- Let `verification_days` gate pruning or advance or hold a watermark.
- Loosen a verifier's pass rule.
- Call a partial day VERIFIED without naming its window.
- Wait for 2026-10-01 to close.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected |
|---|---|---|
| No recorders | `VERIFY_DATA_DIR` unset, or no raw file for D | `verification: no reference data`, exit 0, no ledger |
| DYDX | `--venue DYDX` | `no reference data` |
| All pass | every child exits 0 with a passing body | `verified`, exit 0 |
| One fails | book exits 1 with a body in which 1 instrument fails 3 | `findings`, book `fail`/3, one `archive.verify_day` entry, exit 2 |
| Refused | a child exits 1 with no JSON | `refused`, ledgered, exit 2 |
| Child hangs | past `TOOL_TIMEOUT_S` | killed, `refused` (timeout), exit 2 |
| Crash in the root | unexpected exception | ledgered, `verification: error`, exit 2 |
| Planted pass | body `passed: false` but exit 0 | `fail`, never `pass` |
| Verdict file bad | unreadable JSON | `result unreadable`, ledgered `nightly.verify_day`, exit code unchanged |
| State load | an old `state.json` without the key | empty `verification_days` |
| Retention | a 15th day for one venue | the oldest day dropped |

</intent-contract>

## Code Map

- `platform/archive/nightly.py:80-178` -- `steps()`: append `verify_day` after `prune_catalog`.
- `platform/archive/application/nightly.py` -- `Step`, `StepResult`, `run_steps`: add `verdict_file` and `verification`.
- `platform/archive/application/scheduler.py:303-325,708-710,777-788` -- `SchedulerState`, JSON round-trip, `_step_done`, `status()`.
- `platform/archive/scheduler.py:106-142` -- `build_chains` (unchanged signature).
- `platform/verification/{conservation,trades,book,derivs,catalog,candles}.py` -- the tool CLIs (`--json`, exits 0/1/2, `Refused` → exit 1).
- `platform/verification/application/*.py` `report_json`, and `verification/domain/{conservation,trade_check,reference_book,derivs_check,catalog_check,candle_check}.py` -- the per-instrument `failing`/`passed`.
- `platform/tests/test_boundaries.py:158,2029-2031,2229` -- `COMPOSITION_ROOTS`, `VERIFICATION_IMPORTERS`.
- `platform/docker-compose.verify.yml:45-52`, `Makefile:217`, `tests/test_compose_verify.py` -- the verify wiring.
- `platform/archive/tests/test_nightly.py`, `test_scheduler.py`, `test_step_ledgers.py` -- the test patterns (fake runner, `_Rig`).
- Docs: `docs/VERIFICATION_REPORT.md:67-106`; `docs/DATA_INTEGRITY_AUDIT.md` (D-137 last, :298); `docs/DATA_DICTIONARY.md` §1.23 :1982, §6 :2879; `docs/DEPLOY_CHECKLIST.md` 31-10 last (:1126); `platform/CLAUDE.md:56`.

## Tasks & Acceptance

**Execution:**
- [x] `verification/domain/verdict.py` + `verification/tests/test_verdict.py` -- the reduction and day verdict; a planted-defect test for every tool's shape.
- [x] Domain `failing` properties + `report_json` `failing` per instrument (conservation, trades, derivs; book exposes it) -- tests assert the key and that `passed` ⇔ the unchanged predicate.
- [x] `archive/verify_day.py` + `archive/tests/test_verify_day.py` -- every I/O row (argv per tool, gate, timeouts, crash, ledger, result file).
- [x] `archive/application/nightly.py`, `archive/nightly.py`, `archive/application/scheduler.py` + tests -- the step, the verdict transport, the state/status round-trip and retention.
- [x] `tests/test_boundaries.py` -- the importer/root entries; `archive/tests/test_step_ledgers.py` covers `verify_day`'s job ledger.
- [x] `docker-compose.verify.yml`, `Makefile`, `tests/test_compose_verify.py` -- the wiring.
- [x] Runs 1-4, then docs: VERIFICATION_REPORT, DATA_INTEGRITY_AUDIT, DATA_DICTIONARY §1.24/§6, CLAUDE.md DATA-02, DEPLOY_CHECKLIST 31-11.

**Acceptance Criteria:**
- Given the verify stack with this story's archive image, when `archive.verify_day` runs for a venue-day with recorder data, then `verification_days` for that day holds one verdict per type, and every non-passed type is ledgered at `archive.verify_day`.
- Given a stack without recorders, when the nightly runs, then the step exits 0 with `"verification": "no reference data"`, and pruning and watermarks are unaffected.
- Given the runs, when the report is written, then every data type × instrument row has a verdict, numbers, window, revision and repro, and the unmet full-day precondition is an OPEN audit row that DEPLOY_CHECKLIST 31-11 closes.
- Given the epic closes, then the audit has a row for every finding, DATA_DICTIONARY §1.24 exists, DATA-02 names the standing source, and DEPLOY_CHECKLIST 31-11 exists; the status is `done` with no `operator_actions`.

## Spec Change Log

## Review Triage Log

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 11: (high 0, medium 4, low 7)
- defer: 0
- reject: 10: (high 0, medium 2, low 8)
- addressed_findings:
  - `[medium]` `[patch]` `archive/verify_day.py` `main`: `error_ledger.start` ran outside the never-exit-1 `try`, so an unwritable `ERROR_LEDGER_DIR` exited 1 and failed the saga; now caught and logged, and the ledger stays in-memory.
  - `[medium]` `[patch]` `archive/application/scheduler.py`: a malformed `verification_days` record made `state_from_json` reject the whole state and lose the gating watermarks; the record is now dropped and ledgered at `archive.state_unreadable`, and the watermarks are kept (test parametrised over four malformed shapes).
  - `[medium]` `[patch]` `verification/domain/verdict.py`: a catalog report claiming `passed: true` with a non-zero top-level `failing` reduced to `pass`; every tool now needs its top-level `failing` at 0 (planted test for catalog and candles).
  - `[medium]` `[patch]` `verification/domain/verdict.py`: a report judging no instrument (an empty plan, a wrong mount) reduced to `pass`; it is now `fail` (planted test for all six tools); DATA_DICTIONARY §1.24 updated.
  - `[low]` `[patch]` `Makefile` `verify-up`: `verification/scratch` gets `chmod g+w` like `verification`.
  - `[low]` `[patch]` `kernel/catalog_files.py` `files_by_day`: the docstring now warns that a day outside the range is listed partially (D-145), so a later caller cannot repeat the bug.
  - `[low]` `[patch]` `candles/tests/test_build_candles.py`: the D-145 test's first rebuild now uses an inclusive end, as production does, and a second test covers a file crossing the range's last midnight (both fail without the fix).
  - `[low]` `[patch]` `archive/tests/test_verify_day.py`: the timeout budget is checked against the shipped `archive/config.toml` `step_timeout_minutes` instead of a restated 360.
  - `[low]` `[patch]` `archive/tests/test_verify_day.py`: `test_the_day_is_canonical` now passes `20260929`, so the ISO normalisation is actually exercised.
  - `[low]` `[patch]` `archive/verify_day.py`: `Known limit:` notes for a `run_now` past trade retention (findings that are retention, not bad data) and catch-up cost, each with its upgrade path.
  - `[low]` `[patch]` `verification/tests/test_ssot_trace.py`: an offline planted-defect test that the `board_changed_before_its_publish` accounted row admits only the first message after `ts`, never a later one.

### 2026-10-01 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 2: (high 1, medium 0, low 1)
- defer: 0
- reject: 16: (high 0, medium 4, low 12)
- addressed_findings:
  - `[high]` `[patch]` `candles/application/rebuild.py` `rebuild_instrument`: the D-145 fix still listed files over the range itself, so a range not on midnights (`archive.application.repair.repair_instrument`'s flagged-row span) rebuilt its whole day from only the files overlapping the range, wiping the day's other bars (360 seconds became 120). Files are now listed over whole days, and out-of-range days are still skipped. `files_by_day`'s docstring and audit D-145 are updated, and a regression test that fails without the fix is added.
  - `[low]` `[patch]` `archive/verify_day.py` `run_tool`: an `OSError` raised while starting a tool (fork ENOMEM/EAGAIN) escaped to the catch-all, which turned the whole day into `error` and dropped the verdicts of tools that had already run. That tool is now `refused` ("not started: ...") and the rest still run. A test is added.

## Design Notes

**Why children, not in-process calls:** MEM-01. Each tool has a peak of up to about 1.6 GB on a partial Bybit day. Running each as its own child returns that memory to the OS before the next tool starts. It also keeps the tools' own refusal and ledger paths, and `verification.infrastructure` stays wired by `VERIFICATION_ROOTS` alone.

**Why the step is last:** it cannot fail the saga (its exit is always 0 or 2), and placing it after `prune_catalog` makes "never gates pruning" structural. Trades run at `--stage rebuilt`, which is valid only after `rebuild_seconds`.

**Known limit:** because the step runs after consolidation, the catalog tool's rehearsal for day D is `not_exercised` (D-116), which is not failing. The rehearsal is exercised only by a manual run before the nightly. Upgrade path: a pre-consolidation catalog step.

**Prior attempt (2026-09-30, run 20260929-113859-0838 dev-1, 18:33-19:31 UTC, stopped by the operator for a break, resumed 21:00 UTC):** branch `31-11-prior-attempt` carries the whole WIP as one commit on top of the story baseline 557e50a285: 25 modified files (archive nightly step/scheduler/Makefile/compose wiring, the verification domain and application `failing` properties, their tests) and 4 new ones (`verification/domain/verdict.py` + `test_verdict.py`, `archive/verify_day.py` + `test_verify_day.py`), plus this spec. No task box was ticked yet; a sub-agent was classifying the HL 2026-09-29 verifier failures for the report when it stopped. Reuse it: `git read-tree -m -u HEAD 31-11-prior-attempt && git reset -q`, then re-verify every task and AC rather than trusting checkboxes.

**Prior attempt 2 (2026-09-30 21:00 - 2026-10-01 06:08 UTC, same run, dev-2, stopped by the operator for a machine shutdown):** branch `31-11-prior-attempt-0608` SUPERSEDES `31-11-prior-attempt`. It is one commit on top of the same baseline 557e50a285 with everything dev-1 had plus dev-2's progress (36 files, +2,064/-67): small fixes in `archive/verify_day.py` + its test, `platform/CLAUDE.md` DATA-02 line, `DATA_DICTIONARY.md` §1.24/§6, `DATA_INTEGRITY_AUDIT.md` rows, `DEPLOY_CHECKLIST.md` entry 31-11 (including the D-138 reference-recorder placement decision with measured recorder/verify_day RSS and disk), `VERIFICATION_REPORT.md` "Verdicts (Story 31.11)" with the windowed per-instrument verdict table (W29/W30), and `verification/tests/test_ssot_trace.py` with the D-130 `slow_loop_reads_its_clock_first` handling (the dev was editing that test's `live_only` set when it stopped). Still no task box ticked. Reuse it: `git read-tree -m -u HEAD 31-11-prior-attempt-0608 && git reset -q`, then re-verify every task and AC (tests, ruff, mypy, the docker runs) rather than trusting checkboxes. The verify compose project is up after the reboot (all 10 `verify-*` containers restarted at boot 2026-10-01 ~15:35 UTC, so the recorders have a gap from 06:08Z to then: D-128-class, not a full clean day).

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. verification/tests archive/tests tests/test_boundaries.py tests/test_compose_verify.py -q` -- expected: pass, with only the known pre-existing failures, unchanged.
- `cd platform && ruff format --check <touched> && ruff check <touched> && mypy archive verification` -- expected: clean.
- `docker exec verify-archive python3 -m archive.verify_day --venue HYPERLIQUID --day 2026-09-29 ...` -- expected: exit 2 (partial day), one verdict per type.

## Auto Run Result

Status: done

**Summary.** This was a follow-up review of the done story (baseline 557e50a285 to 32741da390). Blind Hunter and Edge Case Hunter both reviewed the whole diff, `platform/` only, 3056 lines. After deduplication there were 18 findings. Two were patched and 16 were rejected. The previous pass's work (`archive.verify_day`, the `verification.domain.verdict` reduction, `verification_days`, the verify-stack wiring, the report and docs, the D-145 fix) stands unchanged otherwise.

**Patches in this pass:**
- **D-145, completed (high):** `rebuild_instrument` lists every day it rebuilds over the whole day. Before, a non-midnight range rebuilt its day from that range's files only, which is reachable in production through `repair_instrument`. The days outside the range are still skipped.
- **`verify_day` tool start (low):** a tool that cannot be started (`OSError`) is `refused` and the others still run, instead of the whole day becoming `error`.

**Files changed:**
- `platform/candles/application/rebuild.py`: the listing window is widened to whole days.
- `platform/candles/tests/test_build_candles.py`: a regression test for a range inside one day (fails before: 360 seconds become 120).
- `platform/kernel/catalog_files.py`: the `files_by_day` docstring now names both kinds of partial list.
- `platform/archive/verify_day.py`: `run_tool` catches `OSError` and returns `refused`; the docstring is updated.
- `platform/archive/tests/test_verify_day.py`: a test that a tool which cannot be started is refused while the others still run.
- `platform/docs/DATA_INTEGRITY_AUDIT.md`: the D-145 fix column records the follow-up.

**Rejected (16).** Most repeat rejections from the first pass that still hold:
- Grandchild or orphaned children, and a tool exiting 124 by itself: no verifier spawns a subprocess.
- OOM 137 or an operator-lowered step timeout: the child, not `verify_day`, holds the memory, and 6 × 50 min < 360 min by design.
- A `run_now` verdict evicted by the 14-day retention, and a missing entry after an earlier step failed: spec-defined.
- derivs `failing 0` under `fail`: the per-instrument `passed` names it.
- The `coverage_present` default: the tools without a coverage record don't emit the key.
- The ssot-trace accounted row: tighter than the baseline's 2 s slack, and kept as a residual risk.
- The pruned-raw `no reference data`: its `reason` names the missing file, distinct from an unset root.
- The shared catalog scratch dir: the path is fixed by the spec, and an overlapping by-hand run is operator error.
- `scratch` chmod without an ownership guard: an EPERM abort is the louder, correct failure for a directory the archive could not write anyway.
- `archive.state_unreadable` site reuse: its detail prefix says `verification_days dropped`.
- Hand-edited oversized or invalid state, the import-time `ImportError`, and the reports-dir `OSError` on a by-hand run: hypothetical or by-hand only.

**Verification:**
- `python3 -m pytest -o addopts="" --rootdir=. verification/tests archive/tests candles/tests kernel/tests tests/test_boundaries.py tests/test_compose_verify.py`: 1906 passed, 5 skipped, 0 failed.
- The new rebuild test fails with `rebuild.py` reverted (checked by stash).
- `ruff format --check` and `ruff check` on the 5 touched Python files: clean. `mypy archive/verify_day.py candles/application/rebuild.py`: no issues.

**Residual risks:**
- All the residual risks of the first pass stand: the VPS still runs the old images; the D-145 repair, the recorder placement and the full-day precondition (D-138) are owed in DEPLOY_CHECKLIST 31-11.
- The completed D-145 fix also needs that VPS archive deploy. A `repair_catalog --candles-db` run on the VPS before that deploy can still empty the days its flagged rows touch.
