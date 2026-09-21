---
status: done
followup_review_recommended: false
final_revision: dec416641caee676e2bc0af60e005fd034c84539
operator_actions:
  - "On the VPS stop the three collectors, run `python -m collector_core.migrate_open_interest --catalog /app/catalog` (report), then again with `--apply --backup-dir <dir>`, then start the collectors (audit item D-40)."
  - "Run ruff --fix (import ordering after the collector_core rename) and mypy; neither is installed on the dev host. [done locally 2026-09-21: ruff --fix + ruff format over platform/ committed as 154d9898ad; mypy (pre-commit args) 95 findings before, 97 after, 0 new: the +2 are one pre-existing int|None defect in ml_signals/custom_indicators.py now reported per line after the line split]"
baseline_revision: c52a0dab7fb005cba489ce491ad37f5ea4f0db70
---

# Story 22.3: Shared data types and a single `OpenInterest`

Status: awaiting-operator

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a backend developer,
I want the venue-neutral Data types to live in `collector_core/` and the three identical open-interest classes to become one,
so that a new venue imports shared types instead of reaching into `dydx_collector`.

## Acceptance Criteria

1. **Shared types move, names don't.** `DydxSecondSnapshot` (+ `BOOK_DEPTH`) and `integrity.ohlc_outside_book` move to `collector_core/{second_snapshot,integrity}.py`, and the venue-neutral operator scripts `build_candles`, `consolidate_catalog`, `repair_catalog` move to `collector_core/` with class names unchanged, so the catalog directories `custom_dydx_second_snapshot/` stays valid, and every importer (`data_api/routes/*`, `ml_signals`, `ranking_engine`, the three collectors, tests) is updated.
2. **One `OpenInterest`.** `DydxOpenInterest`, `BybitOpenInterest`, `HyperliquidOpenInterest` are replaced by `collector_core.open_interest.OpenInterest` (same four fields: `instrument_id`, `open_interest: Decimal`, `ts_event`, `ts_init`; a `from_pyo3` staticmethod for Hyperliquid), registered for Arrow once.
3. **Idempotent catalog migration.** A script renames existing `data/custom_{dydx,bybit,hyperliquid}_open_interest/` directories into `data/custom_open_interest/` and rewrites each file's Arrow `type` metadata to `OpenInterest`; readers of the old types are updated; re-running the script is a no-op.

## Tasks / Subtasks

- [x] Task 1 — move the three modules (AC: #1)
  - [x] `git mv troll/dydx_collector/second_snapshot.py troll/collector_core/second_snapshot.py` (same for `integrity.py`, `build_candles.py`, `consolidate_catalog.py`, `repair_catalog.py`). Contents unchanged. The Arrow `register_arrow(...)` calls at module bottom stay — the class name in `metadata={"type": ...}` is what the catalog keys on, not the module path.
  - [x] Update every importer. Current list (`grep -rl 'dydx_collector\.\(second_snapshot\|integrity\)' troll --include='*.py'`, 45 files at story creation): `bybit_collector/{collector,open_interest}.py` + tests, `hyperliquid_collector/collector.py` + tests, `dydx_collector/{collector,normalize_snapshot_schema,repair_catalog}.py` + 8 test modules, `data_api/{app,live_candles}.py`, `data_api/routes/snapshots.py`, 9 `data_api/tests/*`, `ml_signals/{catalog_stats,chart_data}.py`, `ml_signals/strategies/{backtest_snapshot,ofi_strategy,snapshot_backtest,snapshot_strategy}.py`, 6 `ml_signals/tests/*`, `ranking_engine/engine.py` + 2 tests. No compatibility shims in `dydx_collector/` (DESIGN-03) — one sweep, `grep` returns nothing afterwards.
  - [x] `dydx_collector/normalize_snapshot_schema.py` stays where it is (operator scripts, `python -m dydx_collector.<name>` is documented in `DATA_INTEGRITY_AUDIT.md`'s runbook) — only their imports change.
- [x] Task 2 — `collector_core/open_interest.py` (AC: #2)
  - [x] `class OpenInterest(Data)`: copy `dydx_collector/open_interest.py:45-107` (`DydxOpenInterest`) verbatim, rename, schema `metadata={"type": "OpenInterest"}`, `__repr__` updated, one `register_arrow(...)`. Add `@staticmethod from_pyo3(obj) -> OpenInterest` from `hyperliquid_collector/open_interest.py` (`HyperliquidOpenInterest.from_pyo3` — read its exact field mapping; the pyo3 object is `nautilus_pyo3.HyperliquidOpenInterest` delivered inside `CustomData`, `hyperliquid_collector/client.py:87-91`).
  - [x] `bybit_collector/open_interest.py`: keep `_fetch_tickers_json`, `fetch_open_interest`, `parse_open_interest`; they return `OpenInterest`. `dydx_collector/open_interest.py`: keep `classify_liquidity`, `_fetch_markets_json`, `fetch_open_interest`, `parse_open_interest`; delete the class. `hyperliquid_collector/open_interest.py`: delete the file; `client.py` imports `OpenInterest` from the core.
  - [x] Docs: `troll/docs/DATA_DICTIONARY.md` §1.8 becomes `OpenInterest` (`collector_core/open_interest.py`), catalog dir `custom_open_interest/`, note it is written by all three collectors (dYdX/Bybit via REST poll, Hyperliquid via WS).
- [x] Task 3 — migration script `collector_core/migrate_open_interest.py` (AC: #3)
  - [x] Shape and safety rules copied from `dydx_collector/normalize_snapshot_schema.py` (audit D-24 precedent): `--catalog` required; report-only by default; `--apply` refuses without `--backup-dir`; each source file copied to the backup dir at its relative path before being touched; write to a temp file then `os.replace` (never a torn file); one file in memory at a time (MEM-01).
  - [x] Behaviour: for each `data/custom_{dydx,bybit,hyperliquid}_open_interest/<instrument_id>/*.parquet`: read table, `table.replace_schema_metadata({**meta, b"type": b"OpenInterest"})`, write (zstd, same file name — the name encodes the time range the catalog reads) under `data/custom_open_interest/<instrument_id>/`, then remove the source file; remove the source directory when empty. Instrument ids are venue-disjoint (`.DYDX`/`.BYBIT`/`.HYPERLIQUID`), so no target collisions; still fail loudly if a target file already exists rather than overwrite. Idempotent: no source dirs → "nothing to do", exit 0; a target file whose metadata already says `OpenInterest` is skipped.
  - [x] Verify the catalog's directory naming assumption with a test, not by reading: write one `OpenInterest` row through `ParquetDataCatalog.write_data` into a tmp catalog and assert `data/custom_open_interest/<iid>/` exists (nautilus derives the dir from the class name; if it differs, the script's target dir follows the catalog, not this story text).
  - [x] Runbook line in `troll/docs/DATA_INTEGRITY_AUDIT.md` §4: stop the three collectors → `python -m collector_core.migrate_open_interest --catalog /app/catalog` (report) → `--apply --backup-dir ...` → start collectors. Until run, new rows land in `custom_open_interest/` while history sits orphaned in the old dirs (readable by nothing — the old classes no longer exist). That is the only reason the script must run on the VPS; register it OPEN in §2 until done, same as D-03.
- [x] Task 4 — tests (TEST-01: catalog integration paths)
  - [x] `collector_core/tests/test_open_interest.py`: round-trip one `OpenInterest` per venue id through `ParquetDataCatalog.write_data` + `catalog.query(OpenInterest, identifiers=[...])`; `from_pyo3` mapping against a real `nautilus_pyo3.HyperliquidOpenInterest` if constructible from Python, else skip with the reason in the test (TEST-03: never mock it).
  - [x] `collector_core/tests/test_migrate_open_interest.py`: build a legacy layout in a tmp catalog (write via `OpenInterest`, then rename the dir to `custom_dydx_open_interest` and patch the file metadata `type` to `DydxOpenInterest` with pyarrow — that *is* what production files look like), run report (no changes), run `--apply` with backup, assert rows readable via `catalog.query(OpenInterest, ...)`, backup file present, second `--apply` is a no-op, a pre-existing target file makes it fail without touching anything.
  - [x] Existing OI tests (`bybit_collector/tests` `parse_open_interest`, `dydx_collector/tests/test_open_interest.py`, HL's) updated to the new class; `ml_signals`/`data_api`/`ranking_engine` tests only change imports.
- [x] Task 5 — `make test`, ruff, mypy green; `grep -rn "DydxOpenInterest\|BybitOpenInterest\|HyperliquidOpenInterest" troll` returns only the migration script and the audit/dictionary history notes.

## Dev Notes

### Why the names stay (and why the dirs would otherwise orphan data)

`ParquetDataCatalog` maps a custom `Data` class to `data/custom_<snake_case_class_name>/`. Renaming `DydxSecondSnapshot` → `SecondSnapshot` would silently start a second directory and every reader would lose history until a migration — exactly what this story does deliberately, once, for open interest only, where the three-way duplication is the actual problem. The snapshot class keeps its `Dydx` prefix for that reason (research §A "Key conclusions"; DATA-05: no silent gaps).

### Module-boundary rule (AD-4)

The moved modules are "shared data types and pure utilities": `DydxSecondSnapshot`, `ohlc_outside_book`. Nothing else from `collector_core.collector` (buffers, gate, client state) may be imported across namespaces — `ml_signals`/`data_api`/`ranking_engine` import from `collector_core.{second_snapshot,integrity,open_interest}` only. Keep `collector_core/__init__.py` empty; import from the module paths.

### Open interest has no downstream reader today

`docs/DATA_DICTIONARY.md` §1.8 records "downstream use of the stored `open_interest` field: none found", and `grep OpenInterest troll/{ml_signals,data_api,ranking_engine,bot_tui,live_paper}` is empty. So the only readers to update are the collectors' own tests. That also means the migration is about preserving history for a future reader, not fixing a live consumer — still required (DATA-05), just not urgent to run mid-day.

### Ordering with 22.1/22.2

Land after 22.2 so there is exactly one importer of each moved module inside the collectors (the core). If 22.2 slips, this story can still run: the moves are mechanical and 22.2's import list just shrinks.

### Project Structure Notes

- New: `troll/collector_core/{second_snapshot,integrity,open_interest,migrate_open_interest,build_candles,consolidate_catalog,repair_catalog}.py`, `troll/collector_core/tests/{test_open_interest,test_migrate_open_interest}.py`.
- Deleted: `troll/dydx_collector/{second_snapshot,integrity,build_candles,consolidate_catalog,repair_catalog}.py` (moved), `troll/hyperliquid_collector/open_interest.py`, the three `*OpenInterest` classes.
- Modified: ~45 importers (Task 1 list), `troll/docs/DATA_DICTIONARY.md`, `troll/docs/DATA_INTEGRITY_AUDIT.md`.
- Unchanged: `troll/collector.dockerfile` (core dir already copied), compose, catalog dir for snapshots.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 22.3] — ACs.
- [Source: research 2026-09-20 §B2] — move list, single `OpenInterest`, migration + readers in one story.
- [Source: troll/dydx_collector/open_interest.py:40-107] — the class to generalise and its Arrow registration.
- [Source: troll/hyperliquid_collector/{open_interest,client}.py] — `from_pyo3` source.
- [Source: troll/dydx_collector/normalize_snapshot_schema.py] — migration-script safety pattern (backup dir, temp-then-rename, report-only default).
- [Source: troll/docs/DATA_DICTIONARY.md §1.8; troll/docs/DATA_INTEGRITY_AUDIT.md §2 D-24, §4 runbook] — docs to update.
- [Source: ARCHITECTURE-SPINE.md#AD-4] — what may cross namespaces.
- [Source: troll/CLAUDE.md DATA-05, DESIGN-03, MEM-01, NAUT-02, TEST-01/03] — rules applied.

## Dev Agent Record

### Agent Model Used

claude-sonnet-5

### Debug Log References

### Completion Notes List

- Moved (git mv) `second_snapshot, integrity, build_candles, consolidate_catalog, repair_catalog` to `collector_core/`; swept all 45 importers plus non-.py refs (Makefile, docker-compose comment, CLAUDE.md, audit runbook) with one sed. No shims.
- `collector_core/open_interest.py`: single `OpenInterest` + `from_pyo3`; dYdX/Bybit modules keep only fetch/parse (+ `classify_liquidity`); `hyperliquid_collector/open_interest.py` deleted.
- `collector_core/migrate_open_interest.py` (report default, backup-dir required, temp+replace, aborts on existing target, idempotent). Catalog dir naming verified by test (`custom_open_interest/`).
- Tests: new `collector_core/tests/test_{open_interest,migrate_open_interest}.py` (from_pyo3 uses a real `nautilus_pyo3.HyperliquidOpenInterest`); HL/Bybit tests updated (HL OI tests moved to core).
- Docs: DATA_DICTIONARY 1.8 and refs, ARCHITECTURE, README, frontend kbData.ts, DATA_INTEGRITY_AUDIT (new OPEN item D-40 + runbook step 7; D-38/D-39 were already taken).
- Tests: 498 passed, 5 failed + 3 collection errors, all pre-existing (ml_signals OFI x4, data_api rankings needs redis, 3 backtest modules import ml_signals.backtest_*).
- ruff and mypy are not installed in this environment: NOT run. Import ordering after the module-path rename (collector_core sorts before dydx_collector/ml_signals) may need `ruff --fix` (isort rule I).
- Final grep for old class names remains only in the migration script/test, audit/dictionary history notes, and the unrelated `nautilus_pyo3.HyperliquidOpenInterest` / docs/integrations.


### File List

- Moved: troll/dydx_collector/{second_snapshot,integrity,build_candles,consolidate_catalog,repair_catalog}.py -> troll/collector_core/
- New: troll/collector_core/{open_interest,migrate_open_interest}.py, troll/collector_core/tests/{test_open_interest,test_migrate_open_interest}.py
- Deleted: troll/hyperliquid_collector/open_interest.py
- Modified: troll/{dydx,bybit,hyperliquid}_collector open_interest/client/collector + tests, ~45 importers in data_api/ml_signals/ranking_engine/collectors, troll/{Makefile,docker-compose.yml,CLAUDE.md,README.md,ARCHITECTURE.md}, troll/docs/{DATA_DICTIONARY,DATA_INTEGRITY_AUDIT}.md, troll/frontend/src/pages/docs/kbData.ts

## Review Triage Log

### 2026-09-20 — Review pass
- intent_gap: 0
- bad_spec: 0
- patch: 1: (medium 1)
- defer: 0
- reject: 20
- addressed_findings:
  - `[medium]` `[patch]` Migration could not resume after a crash between os.replace and source.unlink (target existed -> abort forever). Now a target already typed `OpenInterest` with equal row count is treated as a finished copy: source is backed up and unlinked. Test added.

## Auto Run Result

Status: awaiting-operator

- Implemented: shared modules moved to `collector_core/`, all importers swept, single `OpenInterest` (+`from_pyo3`), idempotent `migrate_open_interest` script, tests, docs (D-40 OPEN).
- Review: 1 patch applied, rest rejected (hardening beyond spec scope).
- Verification: core+dYdX+Bybit+HL 164 passed (DeprecationWarning as error); wider run only the known pre-existing failures (ml_signals OFI x4, data_api rankings needs redis, 3 ml_signals backtest import errors). ruff/mypy unavailable on host.
- Residual risk: import ordering may need `ruff --fix`; VPS migration owed.
- followup_review_recommended: false

## Operator Confirmation

Confirmed 2026-09-21: the external actions this story owed were carried out.

- On the VPS stop the three collectors, run `python -m collector_core.migrate_open_interest --catalog /app/catalog` (report), then again with `--apply --backup-dir <dir>`, then start the collectors (audit item D-40).
- Run ruff --fix (import ordering after the collector_core rename) and mypy; neither is installed on the dev host. [done locally 2026-09-21: ruff --fix + ruff format over platform/ committed as 154d9898ad; mypy (pre-commit args) 95 findings before, 97 after, 0 new: the +2 are one pre-existing int|None defect in ml_signals/custom_indicators.py now reported per line after the line split]

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
