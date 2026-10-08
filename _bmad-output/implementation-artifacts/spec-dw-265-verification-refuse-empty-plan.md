---
title: 'DW-265: verification day tools refuse an empty recording plan'
type: 'bugfix'
created: '2026-10-08'
status: 'done'
baseline_revision: '7ae368b4b50305f298bb2db8a5a3e01f01c20101'
final_revision: 'f91a4ee7476261a861a5421ac353644975457ae2'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** Every `verification.*` tool that reads the venue's plan through `plan_of` (`conservation`, `trades`, `book`, `derivs`, `catalog`, `candles`, `chaos`) iterates zero instruments and prints a passing verdict (exit 0) when the plan is an explicit `instruments = []` (or every instrument is `exclude`d). That is the "exit 0 over nothing" shape DATA-07/D-123 refuses elsewhere.

**Approach:** One shared refusal in `plan_of` (`platform/verification/conservation.py`): after `read_plan_file` succeeds, raise `Refused` naming the plan path when `plan.instruments` is empty. Every tool already turns `Refused` into its ledgered `SystemExit`, so no per-tool change is needed.

## Boundaries & Constraints

**Always:** The refusal lives only in `plan_of`; its message names the plan file path and says it lists no instruments. `read_plan_file`/`parse_plan` keep accepting `instruments = []` ("a plan that records nothing"), because the recorder (`verification/recorder.py`'s `startup_plan` and hot reload) relies on it.

**Block If:** None expected.

**Never:** Do not change `domain/plan_file.py`'s parsing, the recorder, or `tools/record_fixtures.py`. Do not add per-tool empty checks. Do not touch `liquidations` (it reads no plan). Do not edit the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Non-empty plan | `instruments = ["BTCUSDT-LINEAR.BYBIT"]` | `plan_of` returns the plan unchanged | No error |
| Explicit empty plan | `instruments = []` | `plan_of` raises `Refused` naming the path and "no instruments" | Tool exits via `SystemExit` with its `<tool> refused:` message, ledgered |
| All excluded | `instruments = ["X"]`, `exclude = ["X"]` | `plan_of` raises the same `Refused` | Same as above |
| Unreadable/malformed plan | missing file, no `instruments` key | unchanged: `Refused("plan <path>: <exc>")` | unchanged |
| Recorder reads empty plan | `read_plan_file` on `instruments = []` | still returns a `RecordingPlan` with `instruments == ()` | No error |

</intent-contract>

## Code Map

- `platform/verification/conservation.py:141` -- `plan_of`, the single plan reader of every day tool and chaos; holds `Refused`.
- `platform/verification/domain/plan_file.py:47,83` -- `RecordingPlan` (`instruments: tuple[str, ...]`), `parse_plan` that accepts `[]`; unchanged.
- `platform/verification/recorder.py:75,118,140` -- `config_path` (`<VENUE>_COLLECTOR_CONFIG` env), recorder's direct `read_plan_file` calls; unchanged.
- `platform/verification/{trades,book,derivs,catalog,candles,chaos}.py` -- callers of `plan_of`; each converts `Refused` into a ledgered `SystemExit`.
- `platform/verification/tests/test_conservation.py:181` -- `_bybit_day` helper writing a plan toml and env; CLI refusal test pattern at :408.
- `platform/verification/tests/test_plan_file.py:48` -- existing proof `parse_plan` accepts `[]`.
- `platform/docs/DATA_DICTIONARY.md` -- each tool's refusal list (§1.16-§1.23) and §1.24's reduction; updated to name the empty-plan refusal.
- `platform/verification/domain/verdict.py:192` -- `_passes` comment on reports judging no instrument.

## Tasks & Acceptance

**Execution:**
- [x] `platform/verification/conservation.py` -- in `plan_of`, after the read, raise `Refused(f"plan {path} lists no instruments: a verdict over nothing is refused")` (wording may vary but must name the path and "no instruments") when `plan.instruments` is empty; update the docstring -- the one shared refusal for all plan-reading tools.
- [x] `platform/verification/tests/test_conservation.py` -- unit tests for `plan_of`: non-empty returns the plan; explicit `[]` and all-excluded raise `Refused` naming the path; plus a CLI test that `conservation.main` over a `_bybit_day` scenario with an empty plan raises `SystemExit` matching "no instruments" (never prints a PASS) -- covers the matrix.
- [x] `platform/verification/tests/test_tool_ledgers.py` -- parametrized test over the plan-reading roots (book, candles, catalog, chaos, derivs, trades) asserting `module.plan_of is conservation.plan_of` -- guards against a tool later bypassing the shared refusal; the behaviour itself is proven once end-to-end in conservation.
- [x] `platform/verification/tests/test_plan_file.py` -- keep/extend the assertion that `read_plan_file` on a file with `instruments = []` still returns an empty plan (recorder contract).

**Acceptance Criteria:**
- Given a venue plan file with `instruments = []`, when any of conservation/trades/book/derivs/catalog/candles/chaos reaches its plan read, then it exits via its refusal (`SystemExit`, ledgered) and never prints a verdict.
- Given the same file, when the recorder's `read_plan_file` reads it, then it returns a plan recording nothing (no exception).
- Given the full verification test suite, when run, then all tests pass with no new warnings.

## Spec Change Log

## Review Triage Log

### 2026-10-08 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 0, low 5)
- defer: 0
- reject: 11: (high 0, medium 0, low 11)
- addressed_findings:
  - `[low]` `[patch]` DATA_DICTIONARY conservation refusal list named the empty plan twice; reworded, scoped to "every day tool and `chaos`".
  - `[low]` `[patch]` Edited DATA_DICTIONARY lines ran past the 100-column wrap; reflowed the five touched paragraphs.
  - `[low]` `[patch]` §1.24's "every tool refuses it" was too broad (`liquidations` reads no plan); now "every plan-reading tool".
  - `[low]` `[patch]` `verification/domain/verdict.py` comment still cited an empty plan as a report judging no instrument; updated.
  - `[low]` `[patch]` Identity test alone could miss a tool reading the plan around `plan_of`; added a source scan pinning the direct `read_plan_file` readers to `recorder.py`, `conservation.py` and `tools/record_fixtures.py` (the recorder runner); the refusal message now names both empty shapes.

### 2026-10-08 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 2: (high 0, medium 0, low 2)
- defer: 0
- reject: 14: (high 0, medium 0, low 14)
- addressed_findings:
  - `[low]` `[patch]` DATA_DICTIONARY §1.20 (`verification.catalog`) refusal list still said "an unreadable plan"; now "an unreadable or empty plan", like every other plan-reading tool.
  - `[low]` `[patch]` The source-scan guard matched only `read_plan_file`, so a tool calling `parse_plan` directly would bypass the shared refusal unnoticed; it now matches both reader names and allows `domain/plan_file.py`, which defines them.

## Verification

**Commands:**
- `cd platform && python3 -m pytest verification/tests -q -p no:cacheprovider` -- expected: all pass
- `cd platform && ruff check verification && ruff format --check verification && mypy verification/conservation.py` -- expected: clean


## Auto Run Result

**Summary:** Follow-up review of DW-265. `plan_of` (`platform/verification/conservation.py`) refuses a plan listing no instruments (`instruments = []`, or every one excluded), so `conservation`, `trades`, `book`, `derivs`, `catalog`, `candles` and `chaos` exit through their ledgered refusal instead of printing PASS over nothing. `read_plan_file`/`parse_plan` and the recorder still accept an empty plan. This pass fixed one stale doc line and tightened the bypass guard.

**Files changed (this pass):**
- `platform/docs/DATA_DICTIONARY.md` -- §1.20 catalog refusal list names the empty plan.
- `platform/verification/tests/test_tool_ledgers.py` -- source scan also catches direct `parse_plan` use; `domain/plan_file.py` allowed as the defining module.

**Review:** 2 low patches applied, 0 deferred, 14 rejected. The rejections were:
- past days judged against the current plan: pre-existing, already the `Known limit (plan)` of §1.21;
- `chaos` refusing before any fault: verified, `plan_of` runs before `run_scenario`;
- `--json` on refusal: `SystemExit` is raised before any report is printed;
- the rest were noise or style: placement of the refusal (the intent's chosen locus), recorder logging (out of scope), message wording, the docs reflow, comment wording, and per-tool end-to-end tests (the identity test plus the source scan suffice).

**Verification:**
- `python3 -m pytest verification/tests -q` -> 820 passed, 5 skipped (env-gated).
- `ruff check` and `ruff format --check` on `verification` are clean.
- `mypy` is clean on `conservation.py` and `test_tool_ledgers.py`.

**Residual risk:** none new. An operator who empties a venue plan gets `refused` from the nightly `verify_day`, which is the intended behaviour.
