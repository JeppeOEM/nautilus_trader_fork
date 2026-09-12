---
title: 'Bound compute_all()''s catalog-read concurrency in ranking_engine'
type: 'chore'
created: '2026-09-12'
status: 'in-review'
review_loop_iteration: 0
followup_review_recommended: false
context: []
warnings: []
baseline_revision: '7f1496be31b163e9a47f8a570760ab544b56b38e'
---

<intent-contract>

## Intent

**Problem:** `ranking_engine/engine.py`'s `_slow_loop_task` calls `metrics_computer.compute_all()` every 60s with `book_metrics_fn`/`instrument_ids` but no `max_workers`, so it falls back to `compute_all`'s default of 32 — spinning up ~29 simultaneous `ParquetDataCatalog` opens + 25h reads on every cycle and spiking peak memory (part of the nifelheim OOM-restart incident, `troll/.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md`).

**Approach:** Pass an explicit small fixed `max_workers` (4) at the `_slow_loop_task` call site in `ranking_engine/engine.py`. No change to `compute_all`'s behavior, return values, or default signature is required.

## Boundaries & Constraints

**Always:** Keep the call scoped to the exact same instruments (`instrument_ids=list(book_metrics_by_iid)`), same `book_metrics_fn`, same 25h `PRICE_LOOKBACK_HOURS` lookback, and same returned snapshot shape — only the concurrency (`max_workers`) changes. Per-cycle wall-clock time may grow but must stay well under `DB_WRITE_INTERVAL_SECONDS` (60s) at the current ~29-instrument count.

**Block If:** N/A — this is a fully-specified one-line call-site change with no ambiguous decision points.

**Never:** Do not modify `nautilus_trader/` or `crates/` (fork-safety). Do not change `metrics_computer.compute_all`'s default `max_workers=32` signature (optional per AC, not required — leaving it untouched is simpler and sufficient). Do not add new tests for this change (TEST-02: trivial call-site argument change, no new branching/arithmetic) beyond confirming the existing `test_engine.py` suite still passes. Do not fabricate "OOM resolved" before/after `docker stats`/`free -h` evidence — this sandbox has no reachable access to the nifelheim VPS (verified: SSH to the `nifelheim` host returns `Permission denied (publickey,password)` from this environment), so real-machine verification per troll/CLAUDE.md DATA-02 must be explicitly recorded as deferred, not claimed.

</intent-contract>

## Code Map

- `troll/ranking_engine/engine.py` -- `_slow_loop_task` (~line 488-496) calls `metrics_computer.compute_all` via `asyncio.to_thread` without `max_workers`; add the explicit override here.
- `troll/ml_signals/metrics_computer.py` -- defines `compute_all(catalog_path, book_metrics_fn, max_workers: int = 32, instrument_ids=None)` (line 69); default left untouched per AC.
- `troll/ranking_engine/tests/test_engine.py` -- existing suite exercising `_slow_loop_task`-adjacent behavior; must still pass unchanged, no new test required.

## Tasks & Acceptance

**Execution:**
- [x] `troll/ranking_engine/engine.py` -- add `max_workers=4` as an explicit keyword argument to the `metrics_computer.compute_all` call inside `_slow_loop_task` -- bounds concurrent in-flight `ParquetDataCatalog` reads to a small fixed value instead of the function's 32-worker default, without changing which instruments are scanned or what is returned.

**Acceptance Criteria:**
- Given `_slow_loop_task`'s call to `metrics_computer.compute_all`, when this change lands, then the call passes `max_workers=4` explicitly (a small fixed value, not derived from instrument count).
- Given the same instrument set, `book_metrics_fn`, and 25h lookback as before, when `max_workers` is lowered from 32 to 4, then `compute_all`'s returned snapshots are unchanged in shape and content -- only concurrency changes.
- Given `troll/ranking_engine/tests/test_engine.py`, when `pytest troll/ranking_engine/tests -q` runs, then it passes unchanged (no new test added, per TEST-02).
- Given no reachable nifelheim access in this environment, when this story is reported complete, then the completion notes explicitly record real before/after `docker stats`/`free -h` verification as deferred (not claimed as done), per troll/CLAUDE.md DATA-02.

## Spec Change Log

## Review Triage Log

### 2026-09-12 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 2, low 1)
- defer: 1 (high 0, medium 1, low 0)
- reject: 3 (high 0, medium 0, low 3)
- addressed_findings:
  - `[medium]` `[patch]` Blind Hunter + Edge Case Hunter both flagged that narrowing `compute_all` from 32→4 workers lengthens its per-cycle wall-clock time with no observability if it approaches/exceeds `DB_WRITE_INTERVAL_SECONDS` (60s) -- added a `time.monotonic()`-based duration measurement in `_slow_loop_task` with a `logger.warning` if the cycle exceeds the interval (`logger.debug` otherwise).
  - `[medium]` `[patch]` Blind Hunter noted the nifelheim incident writeup's own "(Chosen for now) Document and defer -- no change made this session" line is now stale/contradicted by this story's change, with no traceability from the doc to the fix -- appended an "Addendum (Epic 13, Story 13.1)" section to `troll/.planning/debug/nifelheim-resource-exhaustion-2026-09-12.md` recording what landed, that it's a mitigation not the root-cause fix, and that real nifelheim verification was not collected in this environment.
  - `[low]` `[patch]` Blind Hunter noted the `max_workers=4` magic number had no in-code justification -- expanded `_slow_loop_task`'s docstring to explain why 4, cite Story 13.1/the incident writeup, and note it's a mitigation (Story 13.2 is the real fix).
  - `[medium]` `[defer]` Edge Case Hunter noted `_current_ranks()`'s `_SLOW_METRICS.get(iid, {})` checks presence but not age, so served `pct_1h`/`pct_24h`/`volatility`/`price` can go stale for longer once cycles lengthen (DATA-01 tension) -- pre-existing gap, not introduced by this story; logged to `deferred-work.md`.
  - `[low]` `[reject]` (x3, deduplicated across ~6 raw Blind Hunter findings) "No measured evidence this reduces nifelheim OOM," "doesn't address the incident's actual host-oversubscription root cause," and "no new test added to verify the concurrency bound" -- all three are explicit, deliberate scope decisions already made in `epics.md`'s Story 13.1 AC (mitigation-not-fix framing; root cause is Story 13.2's job; TEST-02 explicitly waives a new test for this trivial call-site change) and in this spec's own `<intent-contract>` (deferred real-evidence verification, unchanged `compute_all` default). Re-litigating an epic-level scoping decision at code-review time on an already-correctly-scoped story is not this story's problem.

## Verification

**Commands:**
- `cd troll && python -m pytest ranking_engine/tests -q` -- expected: all existing tests pass, no failures/errors introduced.
- `cd troll && python -m mypy ranking_engine/engine.py` (if mypy is configured/runnable standalone) -- expected: no new type errors from the added keyword argument.
- `grep -n "max_workers=4" troll/ranking_engine/engine.py` -- expected: one match at the `compute_all` call site.

**Manual checks (if no CLI):**
- Manually inspect the diff to confirm only the `compute_all` call site changed -- no changes to `metrics_computer.py`, `instrument_ids` scoping, or `book_metrics_fn` wiring.
