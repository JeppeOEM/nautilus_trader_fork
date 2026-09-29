---
title: '30.2 The second snapshot stores its book and trade fields as exact integers'
type: 'feature'
created: '2026-09-29'
status: 'done'
final_revision: '4d394fd7354d83062dd4d04a2d42366c0c88d7e0'
baseline_revision: 'b27aa6e021b3cc6fe83a3b6ebe16d97f2c549168'
review_loop_iteration: 0
followup_review_recommended: false
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-30-context.md'
  - '{project-root}/platform/CLAUDE.md'
warnings: ['oversized']
---

<intent-contract>

## Intent

**Problem:** `DydxSecondSnapshot` stores book levels, OHLC and volumes as `float64` taken through `Price.as_double()`/`BookLevel.size()`. About 20.7 % of stored book prices carry float noise (`85891.90000000001`), the archive rebuild compares floats, and the `snapshots:raw` Redis payload ships the same floats to every consumer.

**Approach:** `kernel/second_snapshot.py` becomes the one encoder/decoder of an exact integer layout. It is used for Parquet (`to_dict`/`from_dict` via `register_arrow`) and for the `snapshots:raw` JSON alike. The layout:
- per-row `price_precision`/`size_precision` (`uint8`);
- book prices as `list<int64>`, gap-encoded (element 0 is the best price, then positive gaps to the level above);
- sizes, OHLC and volumes as `int64` units.

Units come from `Price.raw`/`Quantity.raw` by exact integer division, at the precision in the collector's instrument definition. Decoded objects expose floats, computed once, under today's attribute names, plus exact `Price`/`Quantity` values. Every reader decodes through the kernel. A report-first `archive.tools.migrate_snapshot_ints` rewrites the closed-day float files. The web frontend formats integers for display in one TypeScript helper.

## Boundaries & Constraints

**Always:**
- Never round-trip a market value through `float` on the way to units. Precision comes from the instrument definition, never from a value's own digit count.
- A value that cannot be encoded exactly is refused loudly and never rounded:
  - raw not divisible at the precision;
  - units outside int64;
  - a non-positive book gap;
  - precision outside `0..FIXED_PRECISION`.
- Units become floats by one definition, `float(units) / 10.0**precision`, used by both the scalar and the numpy paths.
- Migration and rewrites go through `CatalogFiles.rewrite` under the maintenance lock with 30.1's compact settings. Closed-day files only (DATA-05). One file in memory at a time (MEM-01).
- Every tolerated per-file failure is ledgered and the run continues. Exit codes follow `archive/tools/recompress.py`.
- LGPL headers, ruff/mypy clean, type hints, pytest functions with real Nautilus objects.
- Every consumer of `snapshots:raw` changes in this story.
- The VPS cutover and the migration run go into `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" as one entry `30-2 … (commit <hash>)`. The story finalizes `done` (OPS-01).

**Block If:** a catalog data type other than the snapshot would have to change layout; the fork (`nautilus_trader/`, `crates/`) would need a change.

**Never:**
- A legacy float-layout read path anywhere. Readers refuse a float file loudly, naming `migrate_snapshot_ints`.
- A silent float fallback in `from_dict`: a missing precision key or a float in an integer field raises.
- `BYTE_STREAM_SPLIT`/float32.
- New Python or npm dependencies (no hypothesis: the property test uses a seeded generator).
- Writing `sprint-status.yaml`.
- Converting the candle store (sqlite bars) or derived signals (mid, microprice, spread, scores) to integers. These are computations, and they stay floats (Known limit, below).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|---|---|---|---|
| Encode live row | levels `Price`/`Quantity` at the instrument's precision | units = raw // 10^(16-p); bids stored `[best, b0-b1, …]`, asks `[best, a1-a0, …]` | not exact / >int64 / gap ≤ 0 → `SnapshotEncodingError` (a `ValueError`); the sampler rejects the row as `Unencodable`, which is logged and ledgered (`collector.unencodable`, rate-limited) |
| No instrument precision for an accepted book | iid missing from the service's instruments | row rejected `Unencodable("no instrument definition")` | ledgered, never guessed |
| Decode (Parquet or Redis) | wire dict with precisions | floats + `exact` Price/Quantity, identical for both sources | missing precision key, non-int unit, bad gap → `ValueError` |
| Projected read of a legacy float file | file without `price_precision` column | — | `LegacySnapshotLayoutError` naming the file and `archive.tools.migrate_snapshot_ints` |
| Empty book side | `[]` | `[]` both ways | — |
| Migrate report | closed float files | per venue: files, rows, snapped values, bytes before / projected after; nothing written, no lock | unreadable → ledgered `migrate_snapshot_ints.error`, exit 2 |
| Migrate `--apply` | same | each file rewritten with ints, precisions per row from the definition whose `ts_init` ≤ the row's `ts_event` (latest); pre-OHLC files get null OHLC (subsumes `normalize_snapshot_schema`); decode-back is checked against the originals before replace | residual ≥ 0.001 unit, non-finite or ≥ 2^53 units → the file is refused and ledgered once as `migrate_snapshot_ints.off_grid` with file + value; no definition for a row → `migrate_snapshot_ints.error`; lock held → 1; any refusal → 2 |
| Rerun / open day | already integer / span reaches today | skipped and counted | — |
| Rebuild on legacy day | float-layout day files | — | refused `rebuild.legacy_layout` |
| Rebuild trade finer than row precision | trade raw not exact at the row's precision | — | refused `rebuild.off_grid` |

</intent-contract>

## Code Map

- `platform/kernel/second_snapshot.py`: the class, schema, `to_dict`/`from_dict` (Arrow and Redis), `SecondRow`/`SecondOHLC`, `ohlc_outside_book`.
- `platform/kernel/fold.py`: `SecondTradeFields.snapshot_values()` (float conversion). Replaced by units.
- `platform/kernel/catalog_files.py`: projected readers `query_second_ohlc`, `query_top_of_book` (`TopOfBook` floats), `second_ohlc_arrays`.
- `platform/kernel/indicators.py`: stateless functions take the "to_dict()-shaped" float dict (docstrings only).
- `platform/capture/domain/sampler.py` `_row`, `capture/domain/verdicts.py`, `capture/application/capture_service.py` (`_sample_tick`, `_report_rejection`, `self._instruments`), `capture/infrastructure/redis_stream.py`.
- `platform/ranking/domain/board.py:305` (to_dict → float dict), `ranking/application/engine.py`, `ranking/domain/price_series.py`, `ranking/infrastructure/redis.py` (docstrings/decode).
- `platform/views/chart_series.py:616-650, 915` (`price_series_rows`, OFI replay), `views/coin_detail.py` (`catalog_snapshot_rows`), `views/live_candles.py`.
- `platform/data_api/routes/snapshots.py` (`SnapshotSeriesPoint`), `data_api/app.py` `/catalog/snapshots`, `data_api/export_openapi.py`.
- `platform/frontend/src/{api/schema.ts,hooks/useSnapshotSeries.ts,components/chart/LightweightChart.tsx}`, `frontend/openapi.json`, `frontend/scripts/gen-api-types.mjs`, `frontend/src/lib/` (helpers + vitest tests).
- `platform/archive/application/rebuild_day.py` (`_TRADE_COLUMNS`, `fold_day`, `_updated_columns`, `_check_schema`), `archive/application/repair.py` `_cleared_copy`, `archive/domain/reconciliation.py` (`float_units`, the `_FLOAT_RESIDUAL` precedent), `archive/application/reconcile_day.py` `_load_instrument`.
- `platform/archive/tools/recompress.py` (the structural template for the new tool), `archive/tools/normalize_snapshot_schema.py` (+ test; deleted), `archive/tests/catalog_fixture.py`, `archive/tests/test_catalog_files_backtest.py`.
- `platform/research/application/{frames.py,inspection.py,quotes.py}`.
- `platform/tests/test_boundaries.py:1414-1475`: the existing snapshot-key AST rule.
- Docs: `platform/docs/DATA_DICTIONARY.md` §1/§6/ledger sites, `docs/DEPLOY_CHECKLIST.md`, `docs/DATA_INTEGRITY_AUDIT.md` (D-24 row, new entry), root `CLAUDE.md` price integrity, `platform/research/README.md`, `platform/archive/__init__.py`, `platform/README.md`, `platform/CLAUDE.md`/`ARCHITECTURE.md` citations.

## Tasks & Acceptance

**Execution:**
- [x] `platform/kernel/second_snapshot.py`:
  - Add `SnapshotEncodingError(ValueError)`, `LegacySnapshotLayoutError(ValueError)`, `units_of(raw, precision)`, `unit_float`, `unit_floats` (numpy, per-row precision), gap `encode_book_prices`/`decode_book_prices(side)`, and `SnapshotTradeUnits` (moved from fold, now int).
  - The class holds precisions and unit lists (absolute prices) and computes the float attributes once in `__init__` under today's names. `exact` is a cached frozen dataclass of `Price`/`Quantity`. `as_floats()` returns the indicator dict. `from_levels(...)` is the encoder from `Price`/`Quantity`. `to_dict`/`from_dict` handle the gap-encoded wire layout, and `from_dict` is strict.
  - Schema per the layout. `top_of_book_columns`/OHLC column decoders go here for `catalog_files`. `require_integer_layout(schema, path)`.
  - Docstring invariant updated.
- [x] `platform/kernel/fold.py`: replace `snapshot_values()` with `snapshot_units(price_precision, size_precision)` using `units_of`.
- [x] `platform/kernel/catalog_files.py`: projected readers read precision + unit columns and decode through the kernel. `TopOfBook` carries exact `Price`/`Quantity`. Legacy file → `LegacySnapshotLayoutError`.
- [x] `platform/kernel/indicators.py`: docstrings say `as_floats()`-shaped.
- [x] `platform/capture/domain/{sampler.py,verdicts.py}` + `capture/application/capture_service.py`:
  - The sampler takes `precisions: Mapping[str, tuple[int, int]]` and builds rows with `from_levels`. Exact level size is the sum of `order.size.raw`.
  - New verdict `Unencodable(reason)`, which the service logs and ledgers rate-limited.
  - The service passes the precisions from `self._instruments`.
- [x] `platform/ranking/domain/board.py`, `views/chart_series.py`, `research/application/frames.py`: `to_dict(s)` for indicators becomes `s.as_floats()`. Ranking/views/data_api decode docstrings are updated.
- [x] `platform/views/chart_series.py` + `data_api/routes/snapshots.py`: points carry `bid_units`/`ask_units`/`price_precision` (null in gap rows) instead of float `bid`/`ask`. `mid`/`micro`/`price` stay derived floats.
- [x] `platform/views/coin_detail.py`: the `/catalog/snapshots` rows are the kernel wire dict (integers, precisions, gaps).
- [x] `platform/frontend/`:
  - `src/lib/units.ts`: `formatUnits` (exact, string/BigInt, precisions 0–9+, negative), `unitsToNumber` (via the string), `decodeBookPrices`. Unsafe integers throw.
  - `src/lib/units.test.ts`.
  - `useSnapshotSeries` converts through the helper.
  - Regenerate `openapi.json` + `schema.ts`, and update the affected tests.
- [x] `platform/archive/application/rebuild_day.py`:
  - Fold to `SecondTradeFields` per second, then units at each row's precision.
  - Compare and write ints.
  - Refuse `rebuild.legacy_layout` and `rebuild.off_grid`.
- [x] `platform/archive/application/repair.py`: `_cleared_copy` zeroes the units.
- [x] `platform/archive/tools/migrate_snapshot_ints.py` (new):
  - CLI `--catalog --apply --venue`.
  - Vectorized snap with residual < 0.001 unit (the `float_units` bar).
  - Per-row precision from the catalog's instrument definitions.
  - Gap-encodes through the kernel, verifies decode-back, then `CatalogFiles.rewrite`.
  - Per-venue report.
- [x] Delete `platform/archive/tools/normalize_snapshot_schema.py` + `archive/tests/test_normalize_snapshot_schema.py`. Repoint every reference to the migration.
- [x] `platform/research/application/{frames.py,inspection.py,quotes.py}`:
  - frames add `price_precision`/`size_precision` columns;
  - inspection compares the decoded floats of the expected units at the row's precision (exact equality);
  - `derived_quotes` uses the exact `TopOfBook` values.
- [x] Tests:
  - `kernel/tests/snapshot_factory.py` (Decimal-exact builder from literals with explicit precisions), used by every test that builds a snapshot or payload.
  - `kernel/tests/test_second_snapshot.py`: seeded property test (1–50 levels, precisions 0–9, sizes up to `QUANTITY_MAX` raw; beyond int64 raises), Parquet round trip, strict `from_dict`.
  - fold/catalog_files tests.
  - `archive/tests/test_migrate_snapshot_ints.py`: report, apply, off-grid, no definition, open day, rerun, pre-OHLC, decoded == `round(old, p)`, smaller than the 30.1 float file, `ParquetDataCatalog.query` + `BacktestNode` over the migrated catalog.
  - rebuild tests.
  - `tests/test_boundaries.py` gap-layout rule (a module importing pyarrow/numpy that names `bid_prices`/`ask_prices` is only the kernel's `second_snapshot.py` or the migration tool).
  - Every existing test updated.
- [x] Docs (MR4):
  - `DATA_DICTIONARY.md` §1: layout, units, gaps, precision columns, decode by hand, Redis payload; plus ledger sites.
  - Root `CLAUDE.md` price integrity: the snapshot is integer-exact.
  - `research/README.md`: points at the kernel decoder.
  - `DEPLOY_CHECKLIST` entry `30-2`: a coordinated cutover across 00:00 UTC (collectors, then the migration of closed days, then ranking/data_api/alerting/bots; `snapshots:raw` is pub/sub, so there is nothing to drain; rerun `rebuild_seconds` for any `rebuild.legacy_layout` day; record totals in `DATA_INTEGRITY_AUDIT.md`).
  - Audit entry + D-24 repointed.
  - `archive/__init__.py`, `platform/README.md` tool lists; `platform/CLAUDE.md`/`ARCHITECTURE.md` citations where they describe the float layout.

**Acceptance Criteria:**
- Given a live second, when the collector samples it, then the Parquet row and the Redis message carry identical integer layouts, and decoding either gives `exact` values equal to the book's and fold's `Price`/`Quantity`.
- Given every snapshot reader in `platform/`, when it reads, then it decodes through `kernel/second_snapshot.py`, and both boundary tests pass.
- Given the migration on a float fixture catalog, when run with `--apply`, then every closed file is integer, the report is printed, a second run does nothing, and off-grid files are refused and ledgered.
- Given the full Makefile test line and the frontend vitest run, when they run, then there are no new failures versus the baseline.

## Spec Change Log

## Review Triage Log

### 2026-09-29 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 11: (high 0, medium 1, low 10)
- defer: 0
- reject: 9: (high 0, medium 2, low 7)
- addressed_findings:
  - `[medium]` `[patch]` The migration's flat 0.001-unit snap bar silently rounded genuine digits three or more places finer than the precision (`100.00001` at p=1). It also falsely refused genuine large values whose float noise exceeds 0.001 unit. Now `off_grid` accepts within 1024 ULP of the unit's exact float, capped at 0.001 unit, and refuses values too large to resolve that bar. Measured in value space. Tests for both directions; DATA_DICTIONARY §6, audit D-69, tool docstring and spec Design Notes updated.
  - `[low]` `[patch]` `_check_close` no longer risks a numpy broadcast error before its length check; this falls out of the `off_grid` rewrite.
  - `[low]` `[patch]` DEPLOY_CHECKLIST 30-2 contradicted itself about a midnight-crossing float file. It now says the open-day count is 0 when the collectors stop before 00:00 UTC, and what a non-zero count means. It also notes the report costs as much CPU as `--apply` (run per venue on the VPS).
  - `[low]` `[patch]` Capture's start-time instrument definitions are now a documented `Known limit:` in `SecondSampler._encoded_row`: a mid-run tick or lot refinement makes that instrument's seconds `Unencodable` until restart. Upgrade path: refetch the definition on the first `Unencodable`.
  - `[low]` `[patch]` The sampler maps any `ValueError`/`OverflowError` from encoding one instrument to `Unencodable`, so it can no longer escape `sample` and cost every other instrument its second.
  - `[low]` `[patch]` `tuple` is removed from `test_boundaries`' sanctioned module-level calls (it widened the import-time-effects rule for every module). `second_snapshot`'s precomputed tables became `_raw_step`/`_scale` functions.
  - `[low]` `[patch]` Decode cost: `from_dict` no longer type-checks each unit twice, and `_check_units`/`_check_levels` use builtins. Measured 37 → 30 µs per 20-level row.
  - `[low]` `[patch]` Trade counts are refused above uint32 (their column type) at encode, not at the flush; tested.
  - `[low]` `[patch]` Projected reads (`unit_floats`, `_precisions`, `top_of_book_units`) refuse a null or out-of-range stored precision with a named `ValueError` instead of an `IndexError`/`TypeError`; tested.
  - `[low]` `[patch]` `rebuild_day._check_schema` reads each footer once and treats a file lacking either precision column as legacy.

### 2026-09-29 — Review pass (follow-up)
- intent_gap: 0
- bad_spec: 0
- patch: 1: (high 0, medium 1, low 0)
- defer: 0
- reject: 16: (high 0, medium 3, low 13)
- addressed_findings:
  - `[medium]` `[patch]` `migrate_snapshot_ints` let an instrument-definition read failure outside `_FILE_ERRORS` escape `_migrate_one`. `ParquetDataCatalog._query_subclasses` re-raises anything but a "no rows" `AssertionError`, e.g. a DataFusion `Exception` from one bad instrument file. That would abort a whole `--apply` part-way. `_Run.history` now wraps any such failure as `DefinitionsUnreadableError` (a `ValueError`), so it becomes that file's `migrate_snapshot_ints.error` refusal and the run goes on. Tested.
- Rejected after verification (noted for the record):
  - The 1024-ULP bar for float-summed pre-22.13 volumes was measured on 200 seeded sums of 500 to 20,000 trades: worst drift 0.026 ULP per trade, zero refusals. A refusal needs roughly 40k trades per side in one second, and it would still be loud.
  - A per-row level-count check in `verify` is already covered. The kernel refuses unequal price/size counts per row, and gap-encoded prices decode to different absolute values when levels shift rows. A trial patch showed this and was reverted.
  - The following are by design per the intent contract or already documented as a `Known limit:`: a mid-run precision refinement; the sampler's broad catch (the reason is ledgered with the message); legacy files refusing readers; whole-day `rebuild.off_grid`; the `/catalog/snapshots` wire shape; strict int-only decoding; the midnight cutover.

## Design Notes

- Why the snap tolerance is float noise, capped at 0.001 unit: under a literal reading, the AC's "within half a unit is snapped, further off refuses" can refuse nothing, because every real number lies within half a unit of some unit. The refusal must be able to fire. A value is snapped only within 1024 ULP of its unit's exact float and never beyond 0.001 unit (the `archive.domain.reconciliation.float_units` bar). A value too large for a double to resolve 0.001 unit (about 2.2e12 units) is refused. The review pass tightened this from a flat 0.001 unit, which silently snapped genuine digits three or more places finer than the precision. Snapped count = values whose stored float differs from `unit_float(units)`.
- Known limit (in-code): int64 units cap sizes at 9.22e18 units (≈ 9.2e9 at precision 9). Larger values refuse loudly. Upgrade path: a per-row size exponent or decimal128.
- Known limit (in-code): the candle store and forming bars remain floats. They are aggregations and derived signals, so "integers everywhere a machine moves them" covers raw snapshot fields only. Upgrade path: an integer candle store.
- Cutover without a legacy read path: collectors restart right after 00:00 UTC, so no UTC day mixes layouts. Readers (ranking, data_api, alerting, bots) start after the migration finishes, because they refuse legacy files by design.
- Example: bids 100.5/100.3/99.9 and asks 100.7/101.0 at p=1 give `bid_prices=[1005,2,4]` and `ask_prices=[1007,3]`.

## Verification

**Commands:**
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. kernel/tests archive/tests -q`: all pass.
- `cd platform && python3 -m pytest -o addopts="" --rootdir=. <Makefile test dirs> -q`: failures only in the baseline set (the 10 known).
- `cd platform/frontend && npm ci && npx vitest run && npx tsc -b`: pass, if the registry is reachable. Otherwise this is recorded as not run.
- ruff 0.15.16 + mypy 1.20.2 (scratch venv) on the changed files: no new findings versus HEAD.

## Auto Run Result

Follow-up review pass on the done story (2026-09-29).

- **Change:** unchanged from the 30.2 implementation (integer-exact second snapshot, kernel encoder/decoder, `migrate_snapshot_ints`, readers and frontend on units). This pass added one robustness patch to the migration tool.
- **Files changed in this pass:**
  - `platform/archive/tools/migrate_snapshot_ints.py`: `DefinitionsUnreadableError`; `_Run.history` turns any definition-read failure into a per-file refusal.
  - `platform/archive/tests/test_migrate_snapshot_ints.py`: test that an unreadable definition refuses only its file, ledgers `migrate_snapshot_ints.error`, and the run migrates the others (exit 2).
- **Review findings:** 1 patch applied (medium), 0 deferred, 16 rejected (see the triage log).
- **Verification:**
  - `python3 -m pytest -o addopts="" --rootdir=. archive/tests kernel/tests`: 850 passed.
  - `archive/tests tests/test_boundaries.py`: 583 passed.
  - ruff 0.15.16 check + format and mypy 1.20.2 (scratch venv) on the changed files: clean.
- **Residual risks:** the unchanged cutover risks in the DEPLOY_CHECKLIST 30-2 entry (a coordinated midnight-UTC cutover, no rollback once `--apply` runs), and the documented Known limits.
