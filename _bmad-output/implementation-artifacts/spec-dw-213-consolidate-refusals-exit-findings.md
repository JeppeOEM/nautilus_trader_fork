---
title: 'DW-213: consolidate_catalog exits 2 (findings) when it only refused mixed-schema periods'
type: 'bugfix'
created: '2026-10-07'
status: 'done'
final_revision: 'ebe4c3048aea9444f98403b0544dcf93a6aa007b'
baseline_revision: 'c1096b191fabe9b8f0e9bb37570634edcbd9835f'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** `archive.consolidate_catalog` exits 1 whenever any day was refused, and the nightly saga (`archive.application.nightly.run_steps`) stops at any exit other than 0/2. So one standing mixed-schema day (D-24, refused identically every night until an operator rewrites it) stops `build_candles`, `compare_klines`, `prune_catalog` and `verify_day` for that venue every night, not just the prune the existing Known limit names.

**Approach:** Human decision 2026-10-05 (DW-213): consolidate exits 2 -- the saga's existing "findings, keep going" code (`archive/application/nightly.py` `FINDINGS`) -- when the only problems of the run were mixed-schema refusals; every other refusal or failure keeps exit 1. Each refusal stays ledgered (`consolidate.mixed_schema`) exactly as today.

## Boundaries & Constraints

**Always:** Every mixed-schema refusal is still recorded in the error ledger and logged above the summary line (DATA-07). Exit precedence: 1 if anything other than a mixed-schema refusal went wrong (a non-schema refused period -- row count / covering-file mismatch, partial commit, open-day write, unreadable file --, an abandoned leaf, a held lock, a missing catalog), else 2 if any mixed-schema refusal, else 0. Same rule at both grains (closed day and `--closed-hours`) and in report-only runs. `days_refused` keeps counting every refused period (the verification context, `verification/subject/consolidation.py`, reads it as a failing count and must keep doing so).

**Block If:** Making a non-schema refusal (e.g. the sparse-leaf late-file `consolidate.row_count` known limit) exit 2 would be needed to satisfy a test or doc -- that widens the human decision; HALT instead.

**Never:** Do not change the saga's exit-code semantics (`run_steps`, `exit_code`), the scheduler, or the watermark logic. Do not mute or drop any ledger entry. Do not edit the deferred-work ledger. Do not touch `nautilus_trader/` or `crates/`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Clean | every period merges | exit 0 | none |
| Only mixed-schema | one day refused for differing schema/metadata, rest fine | exit 2; `consolidate.mixed_schema` ledgered; summary shows the refusal | finding, not failure |
| Mixed-schema + other refusal | one schema refusal and one unreadable-file / row-count refusal | exit 1 | failure wins |
| Mixed-schema + abandoned leaf | schema refusal and a leaf `leaf abandoned` | exit 1 | failure wins |
| Closed hours | a closed hour refused for mixed schema only | exit 2 | same rule as days |
| Lock held / catalog missing | -- | exit 1 (unchanged) | unchanged |
| Saga | consolidate step returns 2 | `build_candles`, `compare_klines`, `prune_catalog`, `verify_day` still run; `nightly.consolidate_catalog` findings ledgered; saga exits 2 | unchanged saga logic |

</intent-contract>

## Code Map

- `platform/archive/application/consolidate_day.py` -- `RunStats`, `_consolidate_day` (the mixed-schema refusal and its Known-limit comment "exits 1"), `_consolidate_period`, `_consolidate_groups` (counts `days_refused`).
- `platform/archive/consolidate_catalog.py` -- CLI `main()` exit code and module docstring ("exits 1 when any day was refused").
- `platform/archive/application/nightly.py` -- saga; `FINDINGS = 2`; docstring lists which steps' findings exist (rebuild, compare).
- `platform/archive/nightly.py` -- run docs; Known limit names "a standing consolidate refusal" as an example FAILED step.
- `platform/archive/tests/test_consolidate_day.py` -- `test_a_day_with_differing_precision_metadata_is_refused_and_run_exits_1` (asserts `main(...) == 1`), unreadable-file test (exit 1), lock test.
- `platform/archive/tests/test_nightly.py` -- `_FakeRunner`, saga tests.
- `platform/README.md` (~L355-372), `platform/Makefile` (`consolidate` target comment), `platform/docs/DEPLOY_CHECKLIST.md` (~L40-46) -- operator docs of the exit code / summary line.

## Tasks & Acceptance

**Execution:**
- [x] `platform/archive/application/consolidate_day.py` -- distinguish a mixed-schema refusal from every other refused period (e.g. a small outcome enum from `_consolidate_day`/`_consolidate_period` instead of `bool`), add a `RunStats` counter of mixed-schema refusals (a subset of `days_refused`), show it in `summary()`, and rewrite the `_consolidate_day` Known-limit comment (a standing refused day now exits 2 = findings; the saga continues) -- the CLI needs the split to choose its exit code.
- [x] `platform/archive/consolidate_catalog.py` -- `main()` returns 1 for any non-schema refusal / failed leaf, else 2 (`archive.application.nightly.FINDINGS`) for mixed-schema refusals only, else 0; error log line names both counts; update module docstring and `main` docstring.
- [x] `platform/archive/application/nightly.py` + `platform/archive/nightly.py` -- docs: consolidate's mixed-schema refusals are findings; rewrite the `archive/nightly.py` Known limit so it no longer cites a standing consolidate refusal as a FAILED step (remaining FAILED causes still postpone retention).
- [x] `platform/README.md`, `platform/Makefile`, `platform/docs/DEPLOY_CHECKLIST.md` -- update the exit-code text and the summary-line format.
- [x] `platform/archive/tests/test_consolidate_day.py` -- update the metadata test to expect exit 2; add tests: mixed-schema + unreadable-file refusal -> 1; mixed-schema + abandoned leaf -> 1; closed-hours schema-only refusal -> 2; `RunStats` counter/summary.
- [x] `platform/archive/tests/test_nightly.py` -- add a saga test: consolidate returns 2 -> every later step runs, `nightly.consolidate_catalog` ledgered once, saga exit 2.

**Acceptance Criteria:**
- Given a catalog whose only problem is one mixed-schema day, when `python -m archive.consolidate_catalog --catalog C [--apply]` runs, then it exits 2 and the `consolidate.mixed_schema` ledger count rises by one.
- Given the nightly chain where consolidate exits 2, when the saga runs, then all later steps run and the summary outcome is `findings`.
- Given any non-schema refusal or abandoned leaf in the same run, when consolidate runs, then it exits 1.

## Spec Change Log

## Review Triage Log

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 1, low 2)
- defer: 1 (high 0, medium 1, low 0)
- reject: 14
- addressed_findings:
  - `[medium]` `[patch]` argparse exits 2 on a usage error, the same code the saga now reads as FINDINGS for consolidate -- `main()` parses through `_parse_args` and maps a non-zero `SystemExit` to 1; new test `test_a_usage_error_exits_1_never_2_which_the_saga_reads_as_findings`; `test_closed_hours_takes_neither_days_nor_data_types_and_takes_the_lock` now asserts exit 1; README/DEPLOY_CHECKLIST/module docstring name the usage error as exit 1.
  - `[low]` `[patch]` new docstring lines over 100 columns (`archive/application/nightly.py`, `test_consolidate_day.py`, README) reflowed.
  - `[low]` `[patch]` ambiguous exit-code wording in the `Makefile` `consolidate` comment and the DEPLOY_CHECKLIST findings bullet rewritten.

### 2026-10-07 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 4 (high 0, medium 1, low 3)
- defer: 0
- reject: 13
- addressed_findings:
  - `[medium]` `[patch]` `_schemas_agree` stopped at the first mismatch, so an unreadable file sorting after it went unread and the period came back `MIXED_SCHEMA` (exit 2) instead of a `consolidate.error` failure (exit 1) -- every footer is now read before comparing; new test `test_an_unreadable_file_after_the_schema_mismatch_is_a_failure_not_a_finding` (fails on the old code).
  - `[low]` `[patch]` `RunStats.count_refusal(PeriodOutcome.DONE)` silently counted a finished period as refused -- now raises `ValueError`; new test `test_run_stats_refuse_to_count_a_done_period_as_a_refusal`.
  - `[low]` `[patch]` orphaned "night. Any" line in the `archive/application/nightly.py` docstring re-wrapped.
  - `[low]` `[patch]` `Makefile` `consolidate` comment re-wrapped under 100 columns and notes that make prints exit 2 as "Error 2".

## Design Notes

Why only mixed-schema: the human decision names mixed-schema days. The other refusals are write/verification failures (`RewriteVerifyError`, `PartialCommitError`, covering-file mismatch, unreadable files) and stay failures. The scheduler watermark (`advance_watermark`) advances over findings, which is the intended effect: the refused day is re-reported by every full-run `consolidate_catalog` (no `--days`), so it stays loud.

## Verification

**Commands:**
- `cd platform && python3 -m pytest archive/tests/test_consolidate_day.py archive/tests/test_nightly.py archive/tests/test_step_ledgers.py archive/tests/test_scheduler.py verification -q` -- expected: all pass
- `cd platform && ruff check archive && ruff format --check archive && mypy archive/consolidate_catalog.py archive/application/consolidate_day.py` -- expected: clean

## Auto Run Result

Status: done

**Summary:** Follow-up review pass on the DW-213 change (`archive.consolidate_catalog` exits 2 = findings when the only problems of a run were mixed-schema refusals; every other refusal, an abandoned leaf, a held lock, a missing catalog or a usage error stays exit 1). This pass closed one way a real failure could pass as a finding: the schema check now reads every file's footer before comparing, so an unreadable file anywhere in a mixed-schema period is a `consolidate.error` failure (exit 1).

**Files changed (this pass):**
- `platform/archive/application/consolidate_day.py` -- `_schemas_agree` reads all schemas first; `RunStats.count_refusal` refuses `DONE`.
- `platform/archive/application/nightly.py` -- docstring re-wrap.
- `platform/Makefile` -- `consolidate` comment re-wrap, "Error 2" note.
- `platform/archive/tests/test_consolidate_day.py` -- two new tests (unreadable file after the mismatch -> exit 1; `count_refusal(DONE)` raises).

**Review:** 4 patches applied, 0 deferred, 13 rejected. The rejected ones were either intended by the human decision or noise: a standing finding no longer fails the saga, the watermark advances over findings, and the error-level log wording is kept. Defensive guards on the public counters, moving `FINDINGS` to a neutral module, argparse string codes, the `days_*` naming and the summary-line readers were rejected too (grep found no parser of the line).

**Verification:**
- `cd platform && python3 -m pytest archive/tests/test_consolidate_day.py archive/tests/test_nightly.py archive/tests/test_step_ledgers.py archive/tests/test_scheduler.py archive/tests/test_intraday.py verification/tests/ -q -k "not test_candles"` -- 813 passed, 5 skipped (`test_candles` deselected: its 17 data_api 404 failures are pre-existing, as in the previous pass).
- New unreadable-file test fails with the old `_schemas_agree` and passes with the fix.
- `ruff check archive` clean; `ruff format archive` applied.
- `mypy --config pyproject.toml` on the changed modules: 2 errors, both the pre-existing pyarrow stub errors (`pc.sort_indices`).

**Residual risk:** unchanged from the previous pass. The watermark advances over a standing mixed-schema day, which stays visible through every full-run consolidate (exit 2 + `consolidate.mixed_schema`) and the verification gate's `days_refused`.
