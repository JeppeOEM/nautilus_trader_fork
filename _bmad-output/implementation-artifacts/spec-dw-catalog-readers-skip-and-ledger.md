---
title: 'DW-181/183/288/289: catalog readers skip+ledger foreign names, relist vanished files, name unreadable files'
type: 'bugfix'
created: '2026-10-06'
status: 'done'
final_revision: 'a3d6fe9450'
baseline_revision: '06230f14687c53a621086351d783f7f13e62aca0'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `kernel.catalog_files`'s readers and `archive.application.rebuild_day` let three file faults escape as bare exceptions: a `*.parquet` name the catalog did not write (`CatalogFileSpan.from_path` `ValueError`) aborts the whole instrument (DW-181); a file removed between listing and open (nightly consolidation replacing minute files by their day file) raises a bare `FileNotFoundError` (DW-183/DW-288); a truncated/corrupt file's `ArrowInvalid`/`OSError` escapes unmapped, so `GET /api/candles` 500s with no `error_ledger` count (DW-289, DATA-07).

**Approach:** One kernel-side policy per shape. Foreign name: reported through a caller-supplied hook `on_foreign(site, detail)` and skipped (no hook = refused, as today), so `kernel` stays observability-free and every production caller passes `observability.error_ledger.record`. Vanished file: the self-listing readers list again up to a bounded count (the `query_trade_columns` pattern), then raise the named `CatalogReadError`. Unreadable file: `CatalogReadError` naming the path; `data_api/routes/candles.py` ledgers it and returns 500.

## Boundaries & Constraints

**Always:** `kernel/` imports no context (test_boundaries); the hook's site is the kernel constant `FOREIGN_FILE_SITE = "catalog.foreign_file"`; every non-test caller of a name-parsing reader passes `on_foreign=error_ledger.record`; `CatalogReadError` subclasses `ValueError` and its message names the file; `FileNotFoundError` is caught before `OSError` (it is one); rows are never silently dropped — a vanished file is re-read via a fresh listing, never skipped with its rows lost.

**Block If:** a production caller cannot import `observability` under test_boundaries (then HALT: the hook would have to be bound elsewhere).

**Never:** modify `nautilus_trader/` or `crates/`; make `kernel` import `observability`; skip a vanished or unreadable file in a *writer* path (`rebuild_day`, `second_ohlc_arrays` feeding the candle rebuild) — those keep refusing loudly; change `query_trade_columns`' existing relist/`TradeDecodeError` semantics beyond adding `on_foreign`; edit the deferred-work ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Foreign name, hook given | leaf holds `notes.parquet` + catalog files | catalog files' rows returned; hook called once with `("catalog.foreign_file", "<path>: not a catalog file name (...); skipped")` | none raised |
| Foreign name, no hook | same, `on_foreign` omitted | `ValueError` as before | caller decides |
| Vanished once | first open of a listed file raises `FileNotFoundError`, relist no longer holds it | rows of the second listing returned | none raised, nothing ledgered (no data lost) |
| Keeps vanishing | every listing loses a file | `CatalogReadError` naming the instrument and attempts | candles route ledgers + 500 |
| Corrupt file | truncated Parquet in the window | `CatalogReadError("<path>: unreadable, refused: ...")` | candles route: `error_ledger.record("data_api.candles_catalog_read", ...)` + HTTP 500 |
| `second_ohlc_arrays` vanished/corrupt | path list given by caller | `CatalogReadError` naming the path | candle rebuild refuses that instrument (propagates) |
| rebuild_day foreign name | foreign file in snapshot or trade leaf | skipped + ledgered `catalog.foreign_file`, day rebuilt from catalog files | vanished/corrupt keep `rebuild.error` refusal |

</intent-contract>

## Code Map

- `platform/kernel/catalog_files.py` -- all readers; add `CatalogReadError`, `ForeignFileReporter`, `FOREIGN_FILE_SITE`, public `named_spans(paths, on_foreign)`, private `_reading(path)` error mapper and relisting helper; reuse one `_LISTING_ATTEMPTS = 2`.
- `platform/archive/application/rebuild_day.py` -- `covered_from`, `trade_files`, `day_files` parse names: use `named_spans(..., error_ledger.record)`.
- `platform/data_api/routes/candles.py` -- map `CatalogReadError` to ledgered 500.
- `platform/data_api/routes/indicators.py` -- `indicator_values_page` wraps a `CatalogReadError` in an unledgered `CandleReadError`: the route ledgers it at `data_api.indicator_values_catalog_read` (review-2 patch).
- Callers passing `on_foreign=error_ledger.record`: `views/chart_series.py`, `views/live_candles.py`, `views/ranking_columns.py`, `views/catalog_reads.py` (if it calls a reader), `candles/application/rebuild.py`, `capture/application/capture_service.py`, `ranking/infrastructure/catalog_prices.py`, `archive/application/crosscheck.py`, `research/application/{frames,inspection,backtest_runner}.py`, `research/strategies/snapshot_backtest.py`.
- `platform/kernel/tests/test_catalog_files.py`, `platform/archive/tests/test_rebuild_day.py`, `platform/data_api/tests/` -- tests.

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/catalog_files.py` -- add keyword-only `on_foreign: ForeignFileReporter | None = None` to `data_file_ranges`, `files_by_day`, `query_second_ohlc`, `query_top_of_book`, `query_index_prices`, `price_precision_labels`, `query_trade_columns`; route their name parsing through `named_spans`; wrap every Parquet open (`_read_snapshot_columns`, `_index_rows`, `price_precision_labels`, `second_ohlc_arrays`) in `_reading`; relist the four self-listing non-trade readers on `FileNotFoundError` up to `_LISTING_ATTEMPTS`, then `CatalogReadError`; `second_ohlc_arrays` maps a vanished file to `CatalogReadError`; update module/function docstrings.
- [x] `platform/archive/application/rebuild_day.py` -- foreign names skipped + ledgered via `named_spans`; docstrings updated.
- [x] `platform/data_api/routes/candles.py` -- `except catalog_files.CatalogReadError`: `error_ledger.record("data_api.candles_catalog_read", str(exc), exc)`, raise 500; module docstring.
- [x] production callers listed in Code Map -- pass `on_foreign=error_ledger.record`.
- [x] tests -- one per matrix row: kernel readers (foreign hook/no hook, vanish-once relist via monkeypatched open, keeps-vanishing, corrupt file), `second_ohlc_arrays` corrupt/vanished, rebuild_day foreign file skipped + ledgered, candles route corrupt → 500 + ledger count.

**Acceptance Criteria:**
- Given a foreign `*.parquet` in any instrument leaf, when `GET /api/candles` or the nightly rebuild runs, then it completes over the catalog's own files and `catalog.foreign_file` is counted once per encounter.
- Given the full platform suite, when run, then it passes with zero new warnings and test_boundaries still holds.

## Spec Change Log

## Review Triage Log

### 2026-10-06 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 3 (high 0, medium 2, low 1)
- defer: 3 (high 0, medium 2, low 1)
- reject: 12 (high 0, medium 3, low 9)
- addressed_findings:
  - `[medium]` `[patch]` `GET /api/coin/{iid}/indicator-values` turned a `CatalogReadError` (via `views.chart_series.CandleReadError`) into an unledgered 500 -- the DW-289 shape on a third route; now ledgered at `data_api.indicator_values_catalog_read`, with a truncated-file test in `data_api/tests/test_indicators_config.py`.
  - `[medium]` `[patch]` the immediate, bounded relist can still lose a race with the consolidation's file-by-file source removal, and a listing taken between the day file's rename and the sources' removal returns those seconds twice from the snapshot readers (pre-existing duplication) -- documented as a `Known limit:` with its upgrade path at `_LISTING_ATTEMPTS` (`query_trade_columns`' attempt count is left unchanged, per Never).
  - `[low]` `[patch]` "a foreign file holds no catalog rows" was false: `ParquetDataCatalog.query` reads a file whose name it cannot parse (`nautilus_trader/persistence/catalog/parquet.py` `_query_intersects_filename`), so backtests read what these readers skip; reworded in `named_spans` and `rebuild_day`, with a `Known limit:` + upgrade path (quarantine) in `named_spans`.

### 2026-10-06 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 2 (high 0, medium 1, low 1)
- defer: 3 (high 0, medium 1, low 2)
- reject: 13 (high 0, medium 2, low 11)
- addressed_findings:
  - `[medium]` `[patch]` the relist a vanished file triggers is taken right after the consolidation started removing sources, so it most likely holds the day file beside sources not yet removed, and `query_second_ohlc`/`query_top_of_book` then returned those seconds twice (doubled volume in a candle fold) where the baseline gave a loud 500. Both now keep one copy per second (`_one_copy_per_second`, like the trade reader's `_unique_sorted`) and refuse disagreeing copies with `CatalogReadError`; `_LISTING_ATTEMPTS`' `Known limit:` rewritten (`query_index_prices` keeps every row, documented with its upgrade path); two parametrized tests over both readers.
  - `[low]` `[patch]` `test_a_foreign_file_name_is_skipped_and_ledgered_and_the_day_rebuilt` checked only the last ledger detail, so it passed with one of the two foreign files never reported; it now records every `error_ledger.record` call and asserts each foreign path's reports.

### 2026-10-06 — Review pass (second follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 1 (high 0, medium 0, low 1)
- defer: 1 (high 0, medium 1, low 0)
- reject: 22 (high 0, medium 5, low 17)
- addressed_findings:
  - `[low]` `[patch]` `_one_copy_per_second`'s docstring said "one copy of each second" while it keys on the exact `ts_event`; two rows of one floor second at different `ts_event`s (sample-mode `ts_event = now_ns`) both pass, as at baseline. Docstring now names the key and points at `rebuild_day`'s `rebuild.duplicate_second` refusal for that anomaly.

## Design Notes

Relist instead of the DW-183 "skip" decision for the self-listing readers: consolidation writes the day file and *then* removes the minute files, so a listing taken before the day file existed sees only minute files — skipping one that vanished would serve a window missing its rows. A fresh listing always picks up the day file, so the result is exact; only exhaustion is a fault (named, ledgered by the caller). The intent explicitly allows this ("or re-listed up to a bounded count"). Writers (`rebuild_day`, the candle rebuild's `second_ohlc_arrays`) cannot relist a caller's path list and must never write from a partial read, so they refuse loudly (existing `rebuild.error`; `CatalogReadError` propagating out of `rebuild_instrument`).


- **Prior attempt (2026-10-06):** dev-1 finished this bundle (commits 2e9e1efab0 + 8e0fd783ac on troll) and review-1 had started applying patches when the 06:58 UTC host shutdown (power cut) cut it off; the engine then required troll reset to the baseline. The finished tree, the partial review patch (`_reporting_once()` shared by `_read_overlapping` and `query_trade_columns`, `_RELISTING_READERS` in the tests) and this spec are pinned on branch `dw-catalog-readers-skip-and-ledger-review-prior-attempt`. Start from it: `git read-tree -m -u HEAD dw-catalog-readers-skip-and-ledger-review-prior-attempt && git reset -q && git checkout HEAD -- _bmad-output/implementation-artifacts/deferred-work.md` (the ledger closes are the orchestrator's to write), keep `baseline_revision` 06230f14687c (HEAD is the baseline again), then re-verify every task, AC and verification command rather than trusting the checkboxes, and set `status`/`final_revision` from your own commit.

## Verification

**Commands:**
- `cd platform && python3 -m pytest kernel/tests archive/tests views/tests data_api/tests candles/tests ranking/tests research/tests tests/test_boundaries.py -q -p no:cacheprovider` -- expected: all pass
- `cd platform && ruff check . && ruff format --check . && mypy kernel/catalog_files.py archive/application/rebuild_day.py data_api/routes/candles.py` -- expected: clean



## Auto Run Result

Status: done (second follow-up review pass, 2026-10-06, on 66ca74e140/042ad486e9/1d45d64242 against baseline 06230f14687c).

**Summary:** the change under review is unchanged in scope. Every `kernel.catalog_files` reader takes `on_foreign`: a foreign `*.parquet` name is ledgered at `catalog.foreign_file` and skipped (DW-181), in `rebuild_day` too. A file that vanished between listing and open makes the self-listing readers list again, then raise `CatalogReadError` (DW-183/DW-288); the snapshot readers keep one copy per `ts_event` across that relist. An unreadable file is a named `CatalogReadError`, which the data API ledgers and answers with a 500 (DW-289). This pass found no correctness defect in the change; it corrected one docstring.

**Files changed in this pass:**
- `platform/kernel/catalog_files.py` -- `_one_copy_per_second` docstring: the key is the exact `ts_event`, not the floor second; the floor-second anomaly passes as before and `rebuild_day` refuses it.

**Review findings:** 1 patch applied, 1 deferred, 22 rejected (Blind Hunter 16 + Edge Case Hunter 8, deduplicated).
- Rejected, with the reason:
  - A disagreeing duplicate second (e.g. a pre-fix `repair_catalog` leftover, `repair.duplicate`) is now a ledgered 500 instead of doubled rows. This is by design: DATA-07, and the spec's Always says rows are never silently dropped. `rebuild_day` already refuses the same state (`rebuild.duplicate_second`). The fix is to rerun `archive.repair_catalog`. It stays listed under residual risks.
  - `ts_init` differing between two copies of one `ts_event` is the same anomaly as the item above.
  - An inverted-span name is classed "foreign". That follows the DW-181 decision, which covers every `CatalogFileSpan` refusal, and the ledger detail carries the parser's message.
  - `second_ohlc_arrays` doubling a second when the candle rebuild lists mid-consolidation is pre-existing. The nightly saga runs consolidate and then build_candles in sequence under the maintenance lock, so only a manual run outside the lock reaches it. The candle rebuild failing instead of relisting is the spec's Never (writers refuse loudly).
  - Ledger volume per request for a persistent stray file was already rejected: it is rate-capped by design and is per encounter (the AC).
  - The indicator route ledgering every `CandleReadError` at a catalog site, and the three per-route site names: low impact, and now ledgered where the baseline was not.
  - The trade reader's `TradeDecodeError` (spec Never), and `_reading` mapping `PermissionError`/`ArrowMemoryError` as "unreadable" (the message carries the cause; already rejected).
  - The immediate relist (a documented `Known limit:`).
  - The ranking backfill marking a failed read "NOT retried" is pre-existing for every exception.
  - `query_index_prices` duplicates (a documented `Known limit:`, research only).
  - A dangling symlink reported as vanished.
  - Test-shape nits (the footprint test's monkeypatch, the unasserted count, the rebuild test's count of 3).
- Deferred (recorded here because the intent contract's Never forbids editing the ledger):
  - `archive.application.consolidate_day.file_span` (and `closed_days_needing_work`/`_consolidate_hours`), `archive.infrastructure.catalog_files`, `archive.application.prune`/`repair` and `capture.infrastructure.parquet_writer` still call `CatalogFileSpan.from_path` with no foreign-name policy. One stray `*.parquet` in a leaf therefore raises `ValueError` in the nightly or intraday consolidation, which the readers now skip, and that leaf never consolidates (minute files pile up and widen the readers' relist race). This predates the change. DW-181's decision scoped the fix to `kernel.catalog_files` and `rebuild_day`. Upgrade path: route these through `named_spans(..., error_ledger.record)`, or quarantine foreign names out of the leaf (the `named_spans` `Known limit:`).

**Verification (this session):**
- `cd platform && python3 -m pytest kernel/tests archive/tests views/tests candles/tests ranking/tests research/tests tests/test_boundaries.py -q`: 2481 passed, 3 skipped, no warnings.
- `data_api/tests` was not rerun: this pass changed only a docstring, and those tests need a throwaway Redis on 6379. The previous pass gave 296 passed.
- `ruff check` and `ruff format --check` (repo `.venv` ruff) on `kernel/catalog_files.py`: clean.

**Residual risks:**
- A snapshot second stored twice with different values, such as a pre-fix repair leftover or a rebuild rewrite seen mid-swap, is a ledgered 500 on every chart, indicator, ranking-backfill and live-seed read covering it until `archive.repair_catalog` collapses it. This is a new refusal mode on a served path. It is loud, never silent.
- `query_index_prices` can return an update twice under the relist race. Its only caller is `research` (`Known limit:`).
- A foreign file name still stops consolidation of its leaf (deferred above).

**Follow-up review recommended:** false. This pass changed one docstring. The previous pass's behaviour change was independently reviewed here and held up.
