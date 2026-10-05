---
title: 'DW-177/DW-186: incident debounce key and error-ledger timestamp order'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
baseline_revision: '7b7f8a80c03e45bc2988474b0e6147089c61799f'
final_revision: '298867adcc8906382bbda437600972f7f7ca247e'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: []
---

<intent-contract>

## Intent

**Problem:** (DW-177) `IncidentHandler` debounces on `(incident_type, iid)`, so every unclassified WARNING+ without an instrument shares `("unclassified", None)` and unrelated warnings inside the 10 s window get no report. Every `error_ledger.record()` ERROR line also shares the one template `"[%s] %s"`, so two different ledger sites collapse the same way. (DW-186) `record()` captures `ts_ns` before `_FileSink.write` takes its lock, so two racing threads at a minute boundary can be admitted, bucketed and appended in an order that disagrees with their `ts_ns`.

**Approach:** Unclassified reports debounce on `(type, iid, logger name, message template, incident_key)`, where `incident_key` is an optional record attribute that `error_ledger.record()` sets to its site. Classified reports keep `(type, iid)`. Expired debounce entries are swept so the now open-ended key space stays bounded. `_FileSink.write`/`write_extra` read the clock inside the sink lock when no explicit `ts_ns` is given, and `record()`/`start()` stop passing one.

## Boundaries & Constraints

**Always:** Classified incidents (rule match or structured `reason`) still debounce as one incident per `(type, iid)`, whatever the logger or template. The failed-report re-entry still retries at most once per debounce window. The sweep only drops entries older than `debounce_ns`, which behave exactly like absent ones, so debounce semantics stay exact. `observability/` stays standard-library only. Ledger file lines are appended in non-decreasing `ts_ns` order within a process (wall clock permitting).

**Block If:** A change would alter the ledger line's field set. AC1 of story 23.3 freezes it.

**Never:** Exempting unclassified reports from debounce. Changing any logger call site other than `error_ledger.record()`. Editing the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Unrelated unclassified, no iid | two different loggers, or two templates on one logger, within 10 s | two reports | none |
| Same unclassified template | `logger.warning("x %s", 1)` then `("x %s", 2)`, same logger | one report | none |
| Two ledger sites | `record("a")`, `record("b")` | two reports | none |
| Same ledger site | `record("a")` twice | one report | none |
| Classified across loggers | "Crossed book for IID" from two loggers | one report | none |
| Sweep | key reported, clock advances > `debounce_ns` past the last sweep | expired keys removed from `_last_report_ns` | none |
| Lock-ordered ts | thread blocked on the sink lock while the clock moves past a minute boundary | line carries the post-acquire clock value | none |

</intent-contract>

## Code Map

- `platform/observability/incidents.py` -- `IncidentHandler.emit`: the debounce key and `_last_report_ns`, plus comments that describe the key
- `platform/observability/error_ledger.py` -- `record()` (passes `extra={"incident_key": site}`, no `ts_ns`), `_FileSink.write`/`write_extra` (clock read under the lock), `start()`
- `platform/observability/tests/test_incidents.py` -- handler tests built with `_config` and `_attach`. `test_a_failed_reports_ledger_line_re_enters_once_per_debounce_window` must keep passing.
- `platform/observability/tests/test_error_ledger.py` -- sink tests that pass explicit `ts_ns` (must keep working)
- `platform/docs/DATA_DICTIONARY.md` §1.11 -- describes `ts_ns` as "arrival time"

## Tasks & Acceptance

**Execution:**
- [x] `platform/observability/incidents.py` -- add a `_debounce_key(incident_type, iid, record)` helper that extends the unclassified key with `record.name`, `str(record.msg)` and `getattr(record, INCIDENT_KEY_ATTR, None)`. Add a sweep of expired entries at most once per `debounce_ns`. Update the comments that describe the key. -- DW-177
- [x] `platform/observability/error_ledger.py` -- define `INCIDENT_KEY_ATTR = "incident_key"` here, because incidents already imports error_ledger and this direction keeps the dependency acyclic. Pass `extra={INCIDENT_KEY_ATTR: site}` on `record()`'s ERROR line. Make `_FileSink.write(..., ts_ns: int | None = None)` and `write_extra` read `time.time_ns()` under `self._lock` when `ts_ns` is None, and stop passing it from `record()`/`start()`. Note the ordering invariant in the docstring. -- DW-186 (and DW-177's ledger case)
- [x] `platform/observability/tests/test_incidents.py` -- add tests for every incident row of the matrix. The sweep test monkeypatches `incidents.time.time_ns`.
- [x] `platform/observability/tests/test_error_ledger.py` -- add a deterministic test. Hold `sink._lock` and call `record()` from a thread while the fake clock is at T1. Confirm the thread is blocked and the clock has not been read, then move the clock to T2 (the next minute) and release. The line must carry T2.
- [x] `platform/docs/DATA_DICTIONARY.md` -- change §1.11 `ts_ns` to "the time the line was admitted, read under the sink lock". -- the doc must match the code

**Acceptance Criteria:**
- Given the existing observability and dYdX incident-config tests, when the suite runs, then they all pass unchanged.
- Given ruff and mypy settings, when both are run on the changed files, then they report no new findings.

## Spec Change Log

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 6: (high 0, medium 3, low 3)
- defer: 1: (high 0, medium 0, low 1)
- reject: 12
- addressed_findings:
  - `[medium]` `[patch]` Documented upgrade path (`incident_key` extra) did not work because the key still held the f-string text. When the attribute is set it now replaces the template in the key. Tested.
  - `[medium]` `[patch]` A burst of distinct f-string texts scheduled one report each and grew `_last_report_ns` without bound inside one window. Added `IncidentConfig.max_unclassified_reports_per_window` (default 20, validated >= 1). The overflow is counted and logged at INFO at the next sweep. Tested.
  - `[medium]` `[patch]` Debounce and sweep ran on the wall clock, so an NTP step back stalled the sweep and suppressed reports. Both now use `time.monotonic_ns()` with None-aware first-seen checks, and `trigger_ns` stays wall time.
  - `[low]` `[patch]` Lock-order test could pass without the worker reaching the lock. A `_SpyLock` now signals the wait deterministically.
  - `[low]` `[patch]` The ordering claim in DATA_DICTIONARY §1.11 and `_iter_records_newest_first` now carries the caveat "one writer process, barring a backward wall-clock step".
  - `[low]` `[patch]` Removed the unused `reads` counter from `test_incidents._Clock` and fixed its docstring.

### 2026-10-05 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 2, low 3)
- defer: 0
- reject: 16
- addressed_findings:
  - `[medium]` `[patch]` `error_ledger.record()` lines drew from the same unclassified cap as f-string noise, so a burst of noise could leave a ledgered failure with no report. Records that name a site (`INCIDENT_KEY_ATTR`) are now exempt. Named sites are a bounded set fixed by the code, so the key space stays bounded. Tested.
  - `[medium]` `[patch]` The held-back count was logged only when a later record triggered a sweep. It could come hours late, or never (a quiet process, or shutdown). The first record held back in a window is now logged at once at INFO, and `IncidentHandler.close()` logs the open window's count. Both tested.
  - `[low]` `[patch]` The held-back count counts warnings, not distinct reports (a held-back key is not remembered, so its repeats count too). The message now reads "N unclassified warnings got no incident report", and the docstring says what is counted.
  - `[low]` `[patch]` The monotonic-debounce claim had no test, because the test clock returned one value for both clocks. `_Clock` now models a wall-clock step, and a test proves that a step back does not suppress a report.
  - `[low]` `[patch]` Missing tests added: a held-back text reports in the next window, and an `incident_key` extra on a classified record is ignored.

## Design Notes

The template is `record.msg` before `%`-formatting, so a `%s`-style per-tick warning with changing values still debounces. An f-string warning has no template, so each distinct text gets its own key. That bounds report volume per text rather than per call site. The cost is accepted (`Known limit:` in code), and the sweep keeps memory bounded either way. The `logging.Handler` lock serialises `emit`, so the dict and the sweep need no extra lock.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. observability/tests capture/venues/dydx/tests/test_incident_config.py -q` -- expected: all pass
- `cd platform && ruff check observability && ruff format --check observability && mypy observability/incidents.py observability/error_ledger.py` -- expected: clean (mypy at the repo's configured strictness)


## Auto Run Result

**Summary:**
- **DW-177:** unclassified incident reports debounce on logger + message template, or on the record's `incident_key` attribute when one is set. `error_ledger.record()` sets that attribute to its site. Classified incidents still debounce on `(type, iid)`. Expired keys are swept once per window. A per-window cap applies to unclassified records that name no site, and keeps the key space and report volume bounded. Named sites (every ledger line) are exempt from the cap. The cap's onset is logged at once, its count at the next sweep or at `close()`. The debounce runs on a monotonic clock.
- **DW-186:** `_FileSink.write`/`write_extra` read the clock inside the sink lock, so a line's stamp, its cap bucket and its file order agree across threads.

**Files changed (follow-up pass):**
- `platform/observability/incidents.py` -- named-site cap exemption (`_names_a_site`), cap onset log, `_log_suppressed`, the `close()` flush, the count's wording, docstrings
- `platform/observability/tests/test_incidents.py` -- `_recording_handler` takes config overrides, `_Clock` models a wall step, 4 new tests, the cap test extended

**Review (follow-up pass):** 5 patches applied, 0 deferred, 16 rejected. Rejected as noise or by design: INFO visibility (the one consumer configures INFO), logging inside `emit`, `ts_ns` semantics (documented), the backward wall-clock step (already deferred by the first pass), the test-only `ts_ns` parameter, a line-length claim (ruff clean), constant placement, the cap default's rationale, a tuple-position test assertion, test sync helpers, sweep docstring wording, `debounce_ns <= 0`, a sliding cap window, a scheduling failure after admit (pre-existing order), and a raising or changing `str(record.msg)`.

**Verification:**
- `python3 -m pytest -o addopts="" --rootdir=. observability/tests capture/venues/dydx/tests/test_incident_config.py tests/test_boundaries.py -q` passed: 197 tests. The observability suite passed 3 more consecutive reruns (88 tests each).
- `ruff format --check`, `ruff check observability` and `mypy observability/incidents.py observability/error_ledger.py observability/tests/test_incidents.py` are clean.

**Residual risks:** the cap is still process-wide for unnamed unclassified warnings, so a storm can crowd out a later unnamed report for the rest of that window. The onset and the count are both logged. `close()` logs only if the console handler is still open at shutdown, which holds for the venue entrypoints because that handler is attached first.
