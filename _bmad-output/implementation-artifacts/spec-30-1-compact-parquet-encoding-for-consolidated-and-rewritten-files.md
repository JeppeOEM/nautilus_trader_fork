---
title: '30.1 Compact Parquet encoding for every consolidated and rewritten catalog file'
type: 'feature'
created: '2026-09-29'
status: 'done'
final_revision: 'c69ebb54e9a333121dab51d2b309ffd19d35758a'
baseline_revision: '4bee8e069b2631bc6acc10bc0ce5afe31e6d5117'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-30-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** Every file the archive writes (`CatalogFiles.rewrite`/`write_merged`: nightly + intraday consolidation merges, the nightly snapshot rebuild, migration tools) uses pyarrow's zstd default level and default encodings, wasting 29–56 % of disk per data type (measured, epic 30 preamble). Already-consolidated closed-day files carry the same waste.

**Approach:** One named write-options function in `archive/infrastructure/` (zstd level 19, `DELTA_BINARY_PACKED` + no dictionary on integer timestamp columns, dictionary on for every other leaf column, one row group per instrument-day up to `_MAX_ROW_GROUP_ROWS`, statistics on) used by `CatalogFiles`' single write site, whose read-back verification grows to full value equality; plus a one-off `python -m archive.tools.recompress` for the files already on disk.

## Boundaries & Constraints

**Always:** lossless -- rewritten file's schema, schema/field metadata, row count, row order and values identical to the source (`Table.equals` + `same_schema`), verified on the read-back temp before the rename; every file still loads through `ParquetDataCatalog.query` and `BacktestNode` with zero conversion; `pq.write_table` stays called only in `archive/infrastructure/catalog_files.py` (the options module only *chooses* options); recompress runs under the maintenance lock, one file in memory at a time (MEM-01), never touches a file reaching the current UTC day, and is report-only without `--apply`; every tolerated per-file failure is ledgered (`recompress.error`) and the run continues, exit 2; LGPL header, ruff/mypy clean, type hints, pytest function tests with real Nautilus objects (no mocks of Nautilus).

**Block If:** the measured level-19 write of one instrument-day exceeds 10× the default level AND no level within 2 % of level 19's size exists (the AC's fallback rule is otherwise applied unattended); a data type's round-trip cannot be made value-identical under the compact settings.

**Never:** modify `nautilus_trader/` or `crates/` (FORK-01) -- the live minute files keep Nautilus's own `write_data` encoding; `BYTE_STREAM_SPLIT` or float32 (rejected by measurement, recorded in a comment); change `kernel/parquet_compat.py`'s behaviour; new dependencies; write `sprint-status.yaml`; a Python float round trip of any value.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Merge/rewrite | any catalog table | zstd-19 file, ts cols DELTA, others dictionary, values identical | verify fails → `RewriteVerifyError`, original kept, temp gone |
| Timestamp detection | integer column `ts_*` or `*_ns` (e.g. `next_funding_ns`) | DELTA_BINARY_PACKED, dictionary off | nested/non-integer columns never matched |
| Nested column | `list<double>` book columns | dictionary on its leaf path (`bid_prices.list.element`) | unsupported nested type (map/union) → `ValueError`, loud |
| Big day | rows > `_MAX_ROW_GROUP_ROWS` | several row groups, each with ts min/max statistics | -- |
| recompress report | closed non-compact files | per type: files, bytes before, projected bytes after; nothing written, no lock | unreadable file → ledgered, skipped, exit 2 |
| recompress --apply | same | each file `CatalogFiles.rewrite`; per-type before/after printed | lock held → exit 1; per-file failure ledgered, exit 2 |
| open-day / already compact | file span reaches today / ts_event chunk already DELTA | skipped and counted (not an error) | -- |

</intent-contract>

## Code Map

- `platform/archive/infrastructure/catalog_files.py` -- the one rewriter; `_write_verified_tmp` is the single `pq.write_table` site; `same_schema` verifier.
- `platform/archive/application/consolidate_day.py` -- `leaf_dirs` (excludes `bar`), `RunStats` (wall/peak RSS), merges via `write_merged`.
- `platform/archive/tools/normalize_snapshot_schema.py` -- template for a maintenance-lock tool (CLI shape, ledger, exit codes).
- `platform/archive/infrastructure/maintenance_lock.py` -- `maintenance()` yields the writer.
- `platform/archive/tests/test_catalog_files.py`, `test_one_deleter_one_rewriter.py` -- existing rewriter tests / AST invariants.
- `platform/kernel/catalog_files.py` -- column-projected readers (`query_second_ohlc`, `query_top_of_book`, `query_index_prices`, `second_ohlc_arrays`).
- `platform/kernel/parquet_compat.py` -- live-write zstd patch; docstring pointer only.
- `platform/research/tests/conftest.py` -- the Nautilus log-guard session fixture pattern (a second BacktestNode per process aborts without it).
- `platform/docs/DATA_DICTIONARY.md` §6, `platform/CLAUDE.md` DATA-05, `platform/docs/DEPLOY_CHECKLIST.md` "Deferred operator actions", `platform/docs/DATA_INTEGRITY_AUDIT.md`.

## Tasks & Acceptance

**Execution:**
- [x] `platform/archive/infrastructure/compact_parquet.py` (new) -- `COMPACT_ZSTD_LEVEL = 19` (epic-30 numbers cited), `_MAX_ROW_GROUP_ROWS = 1_048_576`, `timestamp_columns(schema)`, `compact_write_options(schema) -> dict[str, Any]` (compression, level, `use_dictionary` = every non-timestamp leaf path, `column_encoding`, `row_group_size`, `write_statistics=True`), `is_compact(path)` (ts_event chunk already DELTA); comment recording BYTE_STREAM_SPLIT +43 % / float32 no-gain rejections -- the one place options are chosen.
- [x] `platform/archive/infrastructure/catalog_files.py` -- write with `compact_write_options`; drop `apply_zstd_default()` here (explicit options win); read-back verification adds full `Table.equals` (message "values changed"); add `encoded_size(table) -> int` (in-memory write, same options) for the report; docstring updated.
- [x] `platform/archive/tools/recompress.py` (new) -- `--catalog` (required), `--apply`, `--venue`, `--type` (repeatable); leaves from `leaf_dirs`; skips open-day + already-compact files; `remove_stale_tmp` per leaf under `--apply`; per-type table of files / bytes before / after; exit 0/1/2 as normalize tool.
- [x] `platform/archive/tests/test_catalog_files.py` -- round-trip every type (second snapshot, trade tick, mark, index, funding, open interest, instrument status, instrument definitions perp + spot, order book deltas): `Table.equals`, `same_schema`, encodings (ts DELTA, others dictionary incl. nested leaves), smaller than the pre-story default write, `ParquetDataCatalog.query`/`instruments` identical objects, kernel projected readers unchanged; row-group cap split + statistics-based pruning (`split_by_row_group` with a ts filter); values-changed verify failure.
- [x] `platform/archive/tests/test_catalog_files_backtest.py` (new) + `platform/archive/tests/conftest.py` (log-guard session fixture, requested explicitly) -- one `BacktestNode` run over a rewritten fixture catalog loads trade ticks and snapshots with no error.
- [x] `platform/archive/tests/test_recompress.py` (new) -- report writes nothing; apply rewrites closed non-compact files value-identically, skips open-day and compact ones (rerun no-op), `--venue`/`--type` filters, lock held → 1, per-file failure ledgered → 2.
- [x] Measurement (scratch script, not committed) -- synthetic realistic instrument-day in minute files; consolidate wall time + peak RSS (separate processes) and sizes, default level vs 19; apply the >10× fallback rule if triggered.
- [x] Docs -- `docs/DATA_DICTIONARY.md` §6 write settings + why + measurements; `platform/CLAUDE.md` DATA-05 names `compact_write_options` (amended tag); `kernel/parquet_compat.py` docstring pointer; `docs/DEPLOY_CHECKLIST.md` deferred entry `30-1 ... (commit <hash>)` for the VPS recompress run and recording totals in `docs/DATA_INTEGRITY_AUDIT.md`; Makefile unchanged unless a tool target convention exists.

**Acceptance Criteria:**
- Given any archive merge/rewrite, when it lands, then the file is zstd-19, ts columns DELTA without dictionary, other leaves dictionary-encoded, row groups ≤ `_MAX_ROW_GROUP_ROWS` with statistics, values identical to the input.
- Given the recompress tool, when run with `--apply` on a catalog, then every closed non-compact file in scope is rewritten through `CatalogFiles.rewrite` under the maintenance lock and a second run finds nothing to do.
- Given the full platform test target, when it runs, then no new failures versus baseline and the archive AST invariants still pass.
- Given the story is merged, then DATA_DICTIONARY §6, DATA-05, parquet_compat docstring and the DEPLOY_CHECKLIST deferred entry exist; the story finalizes `done` (no awaiting-operator).

## Design Notes

- Leaf paths for `use_dictionary`: pyarrow ignores top-level names for nested columns (measured: `bid_prices` left PLAIN), so paths are derived per type: list-like → `<name>.list.element`, struct → `<name>.<child>`; anything else nested raises. The encodings test over every catalog type guards the naming.
- Known limit (document in-code): a closed day holding exactly one file, or a midnight-crossing file, is never merged, so it keeps Nautilus's encoding until the next `recompress` run; upgrade path: have the archive service run recompress over closed days after consolidation.
- Prototype (synthetic 86,400-row snapshot day): default write 0.20 s / 11.05 MB, level 19 compact 1.22 s / 8.78 MB (~6×, under the 10× rule).

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. archive/tests -q` -- expected: all pass.
- `cd platform && make test` equivalent pytest line from the Makefile -- expected: failures only the pre-existing baseline set.
- ruff/mypy via a scratch venv (`ruff==0.15.16`, `mypy==1.20.2`) on changed files -- expected: no new findings versus HEAD.


## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 5: (high 0, medium 1, low 4)
- defer: 1: (high 0, medium 0, low 1)
- reject: 21: (high 0, medium 0, low 21)
- addressed_findings:
  - `[medium]` `[patch]` `recompress --apply` counted a file that disappeared mid-run as "vanished" (unledgered) even though the maintenance lock excludes every other remover; under `--apply` it is now ledgered `recompress.error` (exit 2), and only a lock-free report counts it vanished. New test `test_a_file_gone_under_the_lock_is_ledgered_as_a_failure`.
  - `[low]` `[patch]` A value-verification failure said only "values changed"; `_read_back_equals` became `_read_back_mismatch`, returning the first differing column + row group (or the row-count gap), carried in the `RewriteVerifyError` message; tests pin the text.
  - `[low]` `[patch]` The full value read-back still ran when the row count had already failed; it now runs only when schema and row count pass.
  - `[low]` `[patch]` DATA-05 (`platform/CLAUDE.md`), DATA_DICTIONARY §6 "Write settings" and the `compact_parquet` docstring said "every file archive writes" gets the compact options; scoped to files archive writes itself, naming `backfill_bars`/`repair_catalog` as `write_data` writers that keep Nautilus's encoding.
  - `[low]` `[patch]` Docstring reflow: orphaned "§6). Files" line and >100-column `Usage:`/exit-code lines in `recompress.py`, >100-column line in `migrate_open_interest.py`.

## Auto Run Result

Status: done

**Summary.** Follow-up review pass over the done story (baseline `4bee8e069b` -> `e9c03fad7f`). Story as shipped: every archive merge/rewrite takes its options from `archive.infrastructure.compact_parquet.compact_write_options` (zstd `COMPACT_ZSTD_LEVEL` = 16 under AC 4's >10x fallback rule, `DELTA_BINARY_PACKED` without dictionary on integer timestamp columns, dictionary on every other leaf by leaf path, row groups capped at `_MAX_ROW_GROUP_ROWS`, statistics); `CatalogFiles`' read-back verifies every value in order before the rename; `python -m archive.tools.recompress` rewrites already-consolidated closed-day files. This pass applied 5 review patches (below).

**Files changed in this pass.**
- `platform/archive/tools/recompress.py` -- a file gone under `--apply` is ledgered as a failure; docstring reflowed.
- `platform/archive/infrastructure/catalog_files.py` -- `_read_back_mismatch` names where a read-back differs; value check skipped once schema/count already failed.
- `platform/archive/infrastructure/compact_parquet.py`, `platform/CLAUDE.md` DATA-05, `platform/docs/DATA_DICTIONARY.md` §6 -- scope "every file archive writes" to files it writes itself.
- `platform/docs/DATA_INTEGRITY_AUDIT.md` D-68 -- renamed function reference.
- `platform/archive/tools/migrate_open_interest.py` -- docstring reflow.
- `platform/archive/tests/test_catalog_files.py`, `test_recompress.py` -- mismatch message assertions; new lock-held vanished-file test.
- `_bmad-output/implementation-artifacts/deferred-work.md` -- one new entry (repair_catalog's `write_data` files are snappy, pre-existing).

**Review.** 5 patches applied (1 medium, 4 low), 1 deferred, 21 rejected (spec-mandated choices such as name-based timestamp detection and the row-group cap, documented Known limits for NaN and single-file days, and test-scope/process suggestions outside the story's intent).

**Verification.** `archive/tests`: 491 passed. Full Makefile test line (`-o addopts="" --rootdir=.`): 10 failed / 2876 passed / 3 skipped, the 10 failures being the baseline set (9 `data_api` Redis-dependent, 1 `ranking` `test_metrics_store`). ruff 0.15.16 check + format clean on the 6 changed Python files; mypy 1.20.2 clean on the 3 changed source modules.

**Residual risks.** Unchanged from the story: level-16 trade-tick write time ~10.8x the default level; a stored NaN makes a file unrewritable (loud, Known limit); single-file closed days and midnight-crossing files keep Nautilus's encoding until a recompress run. The VPS recompress run and its totals remain the deferred operator action (DEPLOY_CHECKLIST entry 30-1).
