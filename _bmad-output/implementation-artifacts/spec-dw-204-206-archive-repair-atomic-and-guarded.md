---
title: 'DW bundle archive-repair-atomic-and-guarded: repair_catalog rewrites in-file and refuses archive-covered rows'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
final_revision: 'fb3cb67e9da879c18de37a1498d60b5cedfd4299'
baseline_revision: 'ecfad5bae3ea191abfe5c7985b57e84a9078a2de'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `archive.application.repair.repair_instrument` clears a flagged second by `ParquetDataCatalog.delete_data_range` then `write_data` per `ts_init` group, so a crash between the two loses that group's seconds (DW-204); and `repair_catalog`'s "never run on a rebuilt day" rule lives only in a docstring, so an operator can clear real, exchange-timed trades of an archive-covered day (DW-206).

**Approach:** (DW-204) Locate each flagged row in the snapshot files whose name span holds its `ts_init`, build each changed file's new table in memory (the row's eight trade columns set to the kernel's no-trade units), stage every changed file through `CatalogWriter.stage_rewrite` (verified temp), clear the touched days' verdicts, then `commit_rewrites` -- the same stage → clear → commit order as `rebuild_day`. No row is ever absent from the catalog at any instant. (DW-206) Before any repair, refuse every flagged row whose `ts_event` is at or after the instrument's trade-archive coverage start (`archive.application.rebuild_day.covered_from`, i.e. `Coverage.start`): ledgered `repair.covered` once per instrument with count and range, row skipped, exit 2. `repair_catalog` wires the maintenance `CatalogFiles` writer and the coverage lookup.

## Boundaries & Constraints

**Always:** stage all of an instrument's changed files and verify them before the first rename; verdicts cleared after staging and before the first rename (any failure discards the temps, files untouched); a row of the current UTC day or in a file reaching it is still refused (`repair.open_day`, `closed_rows`); every skip/refusal ledgered (DATA-07) and reflected in a non-zero exit; no row removed except an extra stored copy of a flagged `(ts_init, ts_event)` whose cleared form is identical in every column to the kept cleared copy (`repair.duplicate`); a key whose stored copies differ beyond the trade columns is refused (`repair.error`), not collapsed; an unreadable trade archive refuses the instrument (fail safe); fork safety; tests use real types, no class-based tests; ruff/mypy clean; Known-limit comments and docs updated, not left contradicting the code.

**Block If:** the fix needs a change to the `CatalogWriter` port's methods, or a schema/layout change to the snapshot files.

**Never:** edit `_bmad-output/implementation-artifacts/deferred-work.md`; call `delete_data_range`/`write_data` from repair; repair a row at or after coverage start (including rows inside archive-gap spans after the start -- decision: pre-archive rows only); refuse a row on a `None` coverage (no archive = everything pre-archive).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | flagged pre-archive row, closed day | row's trade columns cleared in its file, siblings and other rows byte-identical in value, file zstd | none |
| Crash mid-repair | rename fails / verify fails / interrupt before commit | every original file whole, every second still stored | `repair.error` (partial commit names committed files), returns False |
| Covered row | flagged row with `ts_event >= covered_from` | untouched | `repair.covered` (count, first/last ts), exit 2 |
| No archive | `covered_from` None | all flagged rows repairable | none |
| Unreadable archive | `covered_from` raises OSError/ArrowException | instrument not repaired | `repair.error`, exit 2 |
| Leftover copy | spike + identical-book cleared copy at same clocks | one cleared row remains | `repair.duplicate` |
| Not stored | flagged key found in no file | that key skipped | `repair.error`, returns False |
| Verdict store fails | `clear_verified` raises | temps discarded, files untouched | `repair.error`, returns False |

</intent-contract>

## Code Map

- `platform/archive/application/repair.py` -- `repair_instrument`, `closed_rows`, `_replacement_groups`, `_clear_verdicts`; drops the `ParquetDataCatalog` write path and `apply_zstd_default`.
- `platform/archive/application/rebuild_day.py` -- `covered_from` (coverage start), the stage/clear/commit pattern to mirror, `_TRADE_COLUMNS`.
- `platform/archive/application/ports.py` -- `CatalogWriter` (`stage_rewrite`, `commit_rewrites`, `discard_rewrites`), `PartialCommitError`, `RewriteVerifyError`, `OpenDayWriteError`, `RewriteMode.WHOLE_FILE`; invariant (3) text.
- `platform/archive/infrastructure/catalog_files.py` -- `CatalogFiles`, the verified temp-then-rename writer.
- `platform/archive/repair_catalog.py` -- CLI composition root: wire writer + `covered_from`, docstring rule now enforced.
- `platform/kernel/fold.py` / `kernel/second_snapshot.py` -- `SecondTradeFields().snapshot_units(pp, sp)` = no-trade units; `PRECISION_COLUMNS`.
- `platform/kernel/catalog_files.py` -- `snapshot_files`, `named_spans`.
- `platform/archive/tests/test_repair.py`, `platform/archive/tests/test_one_deleter_one_rewriter.py` -- tests.
- Docs: `platform/CLAUDE.md` DATA-05, `platform/archive/__init__.py`, `archive/backfill_bars.py`, `archive/infrastructure/compact_parquet.py`, `docs/DATA_DICTIONARY.md` §6, `docs/DEPLOY_CHECKLIST.md` §4, `docs/DATA_INTEGRITY_AUDIT.md` step 9, `ARCHITECTURE.md`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/archive/application/repair.py` -- replace delete+write with locate → in-memory table per changed file → `stage_rewrite(WHOLE_FILE)` → `_clear_verdicts` → `commit_rewrites`; `repair_instrument(writer, catalog_path, iid, flagged, verified, candles_db_path=None)`; add `uncovered_rows(iid, flagged, archive_start)` (ledgers `repair.covered`); refuse legacy-layout files; rewrite module docstring (Known limit: renames are per file, a partial commit leaves each file whole and is ledgered, rerun completes); drop `apply_zstd_default` -- DW-204/DW-206.
- [x] `platform/archive/repair_catalog.py` -- per instrument under `--apply`: `covered_from` (failure → `repair.error`, not repaired), `uncovered_rows`, then `closed_rows`, then `repair_instrument(writer, ...)`; report mode logs which flagged rows are archive-covered; docstring states the rule is enforced -- DW-206 wiring.
- [x] `platform/archive/application/ports.py` -- invariant (3): name repair's collapse of an identical extra copy as the one rewrite that drops a row.
- [x] `platform/archive/tests/test_one_deleter_one_rewriter.py` -- only `backfill_bars` writes through the Nautilus catalog.
- [x] `platform/archive/tests/test_repair.py` -- adapt existing tests to the new signature; add: crash safety (a failing commit/verify/interrupt leaves every file and second intact), covered-row refusal (lib + CLI exit 2 + ledger), unreadable archive refusal, non-identical duplicate refused, pre-archive rows of a partially covered instrument still repaired.
- [x] Docs listed in the Code Map -- update the repair's write path and the now-enforced rule.

**Acceptance Criteria:**
- Given a flagged pre-archive row, when `repair_catalog --apply` runs, then no `delete_data_range`/`write_data` is called and the row is cleared in place in its original file.
- Given the commit's rename fails, when the repair runs, then every snapshot second stored before is still stored after (cleared or not) and the failure is ledgered.
- Given an instrument whose trade archive starts at T, when flagged rows lie both before and at/after T, then only the earlier ones are repaired and `repair.covered` is ledgered once with the refused count.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 8: (high 1, medium 2, low 5)
- defer: 0
- reject: 8: (medium 2, low 6)
- addressed_findings:
  - `[high]` `[patch]` The coverage start came from the earliest *stored* trade, so pruning a verified (rebuilt) day's trade files moved it later and handed that day's rows back to the repair. Added `repair.coverage_start` = min(earliest stored trade, earliest `_archive_gaps` marker); prune writes a `pruned` marker before deleting a trade file. `repair_catalog` loads the markers through `GapMarkerFiles`, and a malformed marker refuses the instrument. Tests: unit test plus a CLI pruned-day test.
  - `[medium]` `[patch]` After a partial commit or a failed emptied-file removal, the candle rebuild was skipped, and a rerun only rebuilds the days of rows still flagged. The rebuild now runs whenever a commit was attempted (it is idempotent). Test added.
  - `[medium]` `[patch]` An `OSError` from the directory fsync after every rename landed escaped uncaught. It is now ledgered as `repair.error` and returns False.
  - `[low]` `[patch]` The ledger said "PARTIALLY repaired" when the first rename failed and nothing was committed. It now gets its own wording, and the test checks it.
  - `[low]` `[patch]` The `repair.duplicate` wording claimed the dropped copy was "identical cleared". It is reworded.
  - `[low]` `[patch]` A null precision (`TypeError`) escaped `repair_instrument`. It is added to the refusal tuple.
  - `[low]` `[patch]` The sentence in `platform/CLAUDE.md` DATA-05 was garbled. It is split into a separate amended sentence, and DATA-06's repair guidance now says covered rows are refused.
  - `[low]` `[patch]` The docstring said "no file is renamed or split" (temps are renamed over files). It is reworded, and the `archive/__init__.py` Known limits now name the pre-marker prune case.

### 2026-10-06 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 5: (medium 3, low 2)
- defer: 0
- reject: 8: (medium 1, low 7)
- addressed_findings:
  - `[medium]` `[patch]` A dropped duplicate was lost to every rerun once both copies were cleared: after a partial commit with the kept file renamed and the drop file kept, after a failed emptied-file removal, or after a directory-fsync failure that skipped the removal, neither copy was flagged any more, so the duplicate stayed stored for good, despite the "rerun completes it" Known limit. Fix: temps are staged and renamed in reverse path order (a dropped copy always sits in a later path than its kept copy), and emptied files are removed before the first rename. A failed removal discards every temp. Until the kept file is renamed, the key's flagged original is still stored, so a rerun finds it and completes it. Tests: a partial commit whose drop landed (fails under the old order), and an emptied file that cannot be removed. The existing partial-commit test was adapted to the new order.
  - `[medium]` `[patch]` An interrupt or exception during the commit or the candle rebuild skipped the rebuild silently. The committed rows are no longer flagged, so their candle days kept the spike for good. Fix: `_commit_and_rebuild` ledgers `repair.error` naming the days to rebuild (`python -m candles.rebuild --day D`), then re-raises. Test added.
  - `[medium]` `[patch]` A copy of a flagged row in a foreign-named snapshot file (which the detector reads and the repair skips, DW-181) could stay unrepaired while `repair_instrument` returned True and the run exited 0. Fix: the plan records skipped foreign files, and the repair then reports incomplete (False, exit 2); this is documented in the module and CLI docstrings. Test added.
  - `[low]` `[patch]` The `repair_catalog` docstring said a report "ledgers nothing", but `covered_from` ledgers a foreign-named trade file. Reworded.
  - `[low]` `[patch]` `platform/CLAUDE.md` DATA-05 still said every in-place rewrite is `CatalogFiles.rewrite`, and it did not name the repair's one row drop. Amended.

### 2026-10-06 — Review pass (second follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 6: (medium 1, low 5)
- defer: 0
- reject: 10: (medium 2, low 8)
- addressed_findings:
  - `[medium]` `[patch]` When an emptied duplicate file had been removed and the first rename then failed, the ledger said "every file kept as it was" although a file was gone. The commit-failure details (no rename, partial, and the `OSError` case) now name the removed files and say that each held only a copy its kept file still stores. Test: the removal happens, then the first rename fails; the message is checked, and a rerun completes with one copy left.
  - `[low]` `[patch]` An `OSError` from `commit_rewrites` was always reported as "renamed, but the directory fsync failed". It can also be a temp-discard failure after a failed rename. The wording no longer claims which renames landed; it says the files are whole, durability is unproven, and the repair should be rerun. Test added for the fsync case, which no test covered before.
  - `[low]` `[patch]` An ordinary exception in the commit or the candle rebuild propagated and aborted the whole run, so later instruments and venues were never processed, contrary to `_all_instruments`' "none skipped". It is now ledgered with the days to rebuild and returns False (exit 2). Only an interrupt still propagates. Test added.
  - `[low]` `[patch]` A flagged row stored only in a foreign-named file was ledgered as "not stored". It now says no catalog-named file holds the row and that the skipped foreign files may. Test added.
  - `[low]` `[patch]` `coverage_start` said the `pruned` marker's skew widening "only refuses more". It can refuse up to 300 s of real pre-archive rows that neither tool repairs. This is now a module `Known limit:` with an upgrade path.
  - `[low]` `[patch]` The `repair_catalog` comments said "a report writes nothing", but a report can ledger `catalog.foreign_file`. They now say "writes no catalog file".

## Design Notes

Clearing in Arrow: for each matched row index `i`, set every `_TRADE_COLUMNS` value to `SecondTradeFields().snapshot_units(price_precision[i], size_precision[i])._asdict()[col]` and rebuild the table with the original field/schema (refuse if schema would change) -- exactly the no-trade encoding `rebuild_day` writes. Duplicates: across the candidate files in sorted path order, the first stored copy of a key is cleared and kept; a later copy is dropped only when its cleared row (`to_pylist`) equals the kept one, else the key is refused. Days whose verdict is cleared are the UTC days of the cleared rows' `ts_event`.

## Verification

**Commands:**
- `cd platform && python3 -m pytest archive/tests/test_repair.py archive/tests/test_one_deleter_one_rewriter.py archive/tests/test_rebuild_day.py -q` -- expected: all pass
- `cd platform && ruff check archive && ruff format --check archive && mypy archive/application/repair.py archive/repair_catalog.py` -- expected: clean


## Auto Run Result

Status: done

**Summary:** Second follow-up review of the DW-204/DW-206 repair. `archive.repair_catalog --apply` clears each flagged pre-archive row in place through the maintenance `CatalogFiles` writer: it stages verified temps, clears the verdicts, then renames, and it refuses rows the trade archive covers. This pass made the failure ledger accurate in three places: a first-rename failure after an emptied file was removed, an `OSError` from the commit, and a flagged row stored only in a foreign-named file. An ordinary exception in the commit or the candle rebuild no longer aborts the whole run. A `Known limit:` now names the pre-archive window that the `pruned` marker's skew widening refuses.

**Files changed (this pass):**
- `platform/archive/application/repair.py`: `_removed_detail` and `_partial_commit_detail(..., emptied)`; honest `OSError` commit wording; `_commit_and_rebuild` returns False on an `Exception` and re-raises only an interrupt; `_resolve_key` names skipped foreign files; new Known limit; docstrings updated.
- `platform/archive/repair_catalog.py`: two comments ("writes no catalog file").
- `platform/archive/tests/test_repair.py`: four new tests (emptied file removed then first rename fails, directory fsync failure, failing candle rebuild, flagged row only in a foreign file).

**Review:** 6 patches applied (1 medium, 5 low), 0 deferred, 10 rejected. The rejected findings:
- A foreign file irrelevant to the flagged keys keeps exit 2: this was decided in the earlier pass and is a documented residual.
- `repair.covered` at ERROR with exit 2 on every run: the spec's decision.
- Verdicts stay cleared after a commit that renamed nothing: fail-safe, since the trades are held from prune, and a successful rerun clears them again anyway.
- Whole-file reads per duplicate copy: rejected twice before; such copies are rare.
- The candle rebuild runs after a no-op failure: idempotent.
- An open-day duplicate copy: impossible, because a copy's file span always holds its `ts_init`, which `closed_rows` already checks.
- The report not marking covered rows when the archive is unreadable: it already warns that `--apply` would refuse the instrument.
- Temps left after an interrupt mid-commit: removed at the next run's staging (`remove_stale_tmp`), and they are not catalog data.
- Over-clearing verdicts for an unchanged key: not reachable, since a flagged key always changes a file.
- A discard failing inside the staging handler: rare, and it surfaces as a loud exception.

Per the invocation, nothing was written to `deferred-work.md`.

**Follow-up review recommended:** false. This pass changed ledger wording and docstrings, plus one small, tested control-flow change (an exception in commit or rebuild now returns False).

**Verification:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests candles/tests tests/test_boundaries.py tests/test_capture_archive_handoff.py -q`: 829 passed.
- `uv run --no-sync ruff check archive` and `ruff format --check archive` are clean. `mypy archive/application/repair.py archive/repair_catalog.py`: no issues.

**Residual risks:**
- Renames are atomic per file, not as a set (Known limit; a rerun completes the repair).
- Rows inside a gap span after the coverage start, and up to 300 s before a pruned first trade file, are repaired by neither tool. Both are Known limits, and both stay ledgered.
- A stray foreign-named snapshot file keeps its instrument at exit 2 until an operator moves it out of the leaf.
