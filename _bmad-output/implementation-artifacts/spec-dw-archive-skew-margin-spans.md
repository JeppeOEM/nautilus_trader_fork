---
title: 'DW bundle archive-skew-margin-spans: ts_event/ts_init skew handled on both sides in reads, prune markers, retention and repair'
type: 'bugfix'
created: '2026-10-05'
status: 'done'
final_revision: 'ef037e75a8ced7fe5c5c8b4c31871bd0af68008b'
baseline_revision: '988fd26230ba9fae8def0cb7fb09af54cd9831d3'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['multiple-goals']
---

<intent-contract>

## Intent

**Problem:** Five deferred-work entries (DW-184, DW-201, DW-205, DW-210, DW-214) share one root cause. Catalog files and `delete_data_range` work on `ts_init`, while readers, gap markers and retention days work on `ts_event`, and the two clocks can differ in either direction (`kernel/clocks.py`). Four sites handle only one direction or the wrong clock:
- DW-184: `views.catalog_reads.query_second_snapshots` (and its copy `archive.application.repair.second_snapshots`) widens only the query end, so a venue-ahead row near `start_ns` is dropped.
- DW-201/214: the prune's `pruned` marker spans the deleted file's raw `ts_init` name range, so rebuild rows just outside it are treated as covered after their trades were deleted.
- DW-205: `retention.file_days` widens only backwards, so the next day's venue-ahead trades never have to be verified.
- DW-210: `repair_instrument` deletes by `ts_event`, which can miss the row.

**Approach:** Widen each site by the one skew bound in both directions, and make the repair delete by `ts_init`. Because caught-up snapshot rows can share one `ts_init` (`capture_service.py` `_MAX_CATCH_UP_SECONDS`), the repair rewrites every sibling row at that `ts_init`, with only the flagged ones cleared. Each fix gets a regression test.

## Boundaries & Constraints

**Always:**
- Snapshot readers widen the `ts_init` query start by `READ_SPAN_MARGIN_NS`, clamped at 0, as they already widen the end. The exact `ts_event` filter is unchanged.
- The `pruned` marker span is `[max(0, start - MAX_TS_INIT_SKEW_NS), end + MAX_TS_INIT_SKEW_NS]`. The `assert_span_closed` gate still checks the raw file span.
- `file_days` adds the next day when `end + MAX_TS_INIT_SKEW_NS` crosses midnight, mirroring the existing previous-day rule.
- The repair never loses a non-flagged row: every row whose `ts_init` equals a flagged row's `ts_init` is written back unchanged. All replacements are built before anything is deleted.
- Remove `Known limit:` comments these fixes resolve, and correct docstrings that describe the old behaviour.

**Block If:** A fix would require editing `nautilus_trader/` or `crates/`.

**Never:**
- Edit `_bmad-output/implementation-artifacts/deferred-work.md`.
- Change `MAX_TS_INIT_SKEW_NS`, `READ_SPAN_MARGIN_NS`, or the rebuild's `ts_init` window.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Venue-ahead row at read start | snapshot `ts_event = start_ns + 10 s`, `ts_init = start_ns - 5 s` | returned by `query_second_snapshots` and by repair's `second_snapshots` | -- |
| Pruned trade file | trade file span `[S, E]` deleted | marker `[S - 300 s, E + 300 s]`, reason `pruned`; rebuild `Coverage` does not cover `S - 100 s` | -- |
| File ending before midnight | span ends 23:58 on day D | `file_days` includes D+1 | -- |
| Repair with ts_init != ts_event | flagged row `ts_init = ts_event + 3 s` | the row is replaced by its cleared copy, with no duplicate second | -- |
| Caught-up siblings | flagged row shares its `ts_init` with an unflagged row | unflagged sibling survives unchanged | -- |

</intent-contract>

## Code Map

- `platform/views/catalog_reads.py` -- `query_second_snapshots`. Its start is not widened, and a `Known limit:` comment describes that.
- `platform/archive/application/repair.py` -- `second_snapshots` (the same query), and `repair_instrument`, which deletes by `ts_event`.
- `platform/archive/application/prune.py` -- `_delete` records `ArchiveGap(f.iid, *span, "pruned", 0)`.
- `platform/archive/domain/retention.py` -- `file_days` and the module docstring's trade rule.
- `platform/kernel/clocks.py` -- `MAX_TS_INIT_SKEW_NS` and `READ_SPAN_MARGIN_NS`, with the docs on skew direction.
- `platform/tests/test_skew_constants.py` -- static guards tying margins to the bound.
- Tests: `views/tests/test_catalog_reads.py`, `archive/tests/test_repair.py`, `archive/tests/test_prune.py`, `archive/tests/test_retention.py`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/views/catalog_reads.py` -- set the query start to `max(0, start_ns - READ_SPAN_MARGIN_NS)`, replace the `Known limit:` comment with a two-direction rationale, and update the docstring -- DW-184.
- [x] `platform/archive/application/repair.py` -- apply the same widening in `second_snapshots`. Rewrite `repair_instrument` to group flagged rows by `ts_init`. For each group: query the rows at exactly that `ts_init`, substitute the cleared copies (matched by `ts_event`), then `delete_data_range(..., ts_init, ts_init)` and `write_data` the full group -- DW-210.
- [x] `platform/archive/application/prune.py` -- record the `pruned` marker over the skew-widened span, through a small helper -- DW-201, DW-214.
- [x] `platform/archive/domain/retention.py` -- add the next-day rule to `file_days` and update its docstring and the module docstring -- DW-205.
- [x] `platform/tests/test_skew_constants.py` -- assert that the prune marker helper references `MAX_TS_INIT_SKEW_NS`.
- [x] Regression tests, one per matrix row, in the four test files listed in the Code Map.

**Acceptance Criteria:**
- Given each I/O matrix scenario, when its test runs against the pre-fix code, then it fails; after the fix, it passes.
- Given the full archive, views, kernel and cross-cutting test suites, when they run, then all pass, and ruff and mypy are clean on touched files.

## Design Notes

Why `ts_init` grouping in the repair: `delete_data_range` splits files on `ts_init` with inclusive bounds (`nautilus_trader/persistence/catalog/parquet.py` `_prepare_delete_operations`), so it removes every row at that instant. Rewriting the whole group keeps caught-up siblings. The new file's `[ts_init, ts_init]` span never overlaps the split remainders.

The upper widening of the marker is conservative. A wider `pruned` span only keeps more rows' live values (`Coverage` invariant). `verification/domain/conservation.py` caps a marker's explanation at its `count` (0 for `pruned`), so it cannot hide a missing trade.

## Verification

**Commands:**
- `cd platform && python3 -m pytest archive/tests views/tests kernel/tests tests/test_skew_constants.py -q` -- expected: all pass
- `ruff check` and `ruff format --check` on touched files -- expected: clean

## Review Triage Log

### 2026-10-05 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 3, low 2)
- defer: 0
- reject: 21
- addressed_findings:
  - `[medium]` `[patch]` A group skipped because its flagged row is no longer stored was ledgered, but `repair_catalog` still logged "repaired" and exited 0. `repair_instrument` now returns False in that case, and `_instrument` passes it through, so the run exits 2. Docstring and test updated.
  - `[medium]` `[patch]` The repair collapsed leftover duplicate rows of a flagged second without any record (DATA-07). Each collapse is now ledgered at `repair.duplicate` with its count.
  - `[medium]` `[patch]` A `write_data` failure after a group's `delete_data_range` lost the whole group with no durable record. It is now ledgered at `repair.error`, naming the lost `ts_event`s, before it is re-raised. Known limit text and a test updated.
  - `[low]` `[patch]` `prune._delete` built the `pruned` marker for every file type. It is now built only for trade files.
  - `[low]` `[patch]` The `data_api/app.py` Known limit still said the snapshot read widens only its end (cap + 60 rows). It now says cap + 120 and notes that the clamped start cannot overflow.

## Auto Run Result

**Summary:** This was a follow-up review of the done bundle (DW-184, DW-201, DW-205, DW-210, DW-214). The two-sided skew handling holds:
- Reads widen both ends.
- `pruned` markers are widened by `MAX_TS_INIT_SKEW_NS`.
- `file_days` requires the next day for a file ending near midnight.
- The repair deletes by `ts_init` and rewrites whole `ts_init` groups.

This pass hardened the repair's failure reporting and fixed one stale comment.

**Files changed (this pass):**
- `platform/archive/application/repair.py` -- `repair.duplicate` ledger entry for collapsed leftovers; a failed rewrite is ledgered with its lost rows, then re-raised; `repair_instrument` returns whether every flagged row was repaired.
- `platform/archive/repair_catalog.py` -- exits 2 when a flagged row was not repaired because it was no longer stored; docstring updated.
- `platform/archive/application/prune.py` -- the `pruned` marker is built inside the trade-file branch only.
- `platform/data_api/app.py` -- the read-bound Known limit reflects the two-sided widening.
- `platform/archive/tests/test_repair.py` -- duplicate-ledger, False-return, failed-rewrite and exit-2 tests.

**Review:** 5 patches applied, 0 deferred, 21 rejected. Rejected findings were by-design behaviour (Design Notes), already-documented `Known limit:`s, or speculation. Real but pre-existing items the first pass handed to the orchestrator are listed again below, because the ledger is not edited here:
- `pruned` markers written before this change keep the narrow raw span and need a one-off widening (and a DEPLOY_CHECKLIST deferred operator action).
- `archive/repair_catalog.py` passes a file-name `ts_init` span (`data_range_ns`) as a `ts_event` scan window. A venue-ahead row past the newest file's end is never scanned.
- Edge-day verification can deadlock. A trade file ending within the bound before midnight needs D+1 verified. If that day never gets a `pass` (for example, an instrument that stopped being collected), the file is kept and reported `unverified` indefinitely. This mirrors the existing D-1 rule.
- Rows that pre-fix repairs deleted by `ts_event` on the `ts_init` axis are not recovered.

**Verification:**
- `cd platform && python3 -m pytest archive/tests views/tests kernel/tests tests/test_skew_constants.py tests/test_boundaries.py -q`: 1539 passed.
- `data_api/tests`: 291 passed, run against a throwaway Redis on 6379. Without Redis, 9 Redis-connection tests fail; that is unrelated to this change.
- `ruff check`, `ruff format --check` and `mypy` on the 5 touched files are clean.

**Residual risks:** The repair's per-group delete and write are still not atomic. A failure between them is now ledgered with the lost rows rather than prevented (documented `Known limit:`). The ahead-direction skew is bounded only by detection (documented `Known limit:`).
