---
title: 'Story 22.11: Nightly catalog consolidation for every venue'
type: 'feature'
created: '2026-09-21'
status: done
baseline_revision: '4645f29059f9c28f5e9d001f95e1ffdd7cac3149'
final_revision: 'a91584685ba9e7a8404aeb05d41931f51936d0c6'
review_loop_iteration: 0
followup_review_recommended: true
context:
  - '{project-root}/_bmad-output/implementation-artifacts/22-11-nightly-catalog-consolidation-for-every-venue.md'
  - '{project-root}/troll/CLAUDE.md'
warnings: [oversized]
operator_actions:
  - "On nifelheim, deploy this branch (make redeploy-all) and run the first full consolidation by hand from troll/: `make consolidate`. Copy its final `consolidate: ...` summary line (days, files and MB before -> after, wall seconds, peak RSS) into troll/docs/DATA_INTEGRITY_AUDIT.md D-36 as **measured** (first full run), and confirm peak RSS stays well inside the box's free memory (MEM-01)."
  - "Install the nightly cron line from troll/README.md 'Nightly maintenance' in the VPS host crontab (add CRON_TZ=UTC if the box is not on UTC), then after the first nightly run copy that night's summary line from consolidate.log into D-36 as the measured nightly run."
  - "Choose an object-storage provider (Cloudflare R2 or Backblaze B2), create a bucket, install rclone on the VPS host, run `rclone config` to create the remote (credentials stay in ~/.config/rclone), and set RCLONE_REMOTE and RCLONE_BUCKET (bare values) in troll/.env."
  - "Run `make backup-catalog` once by hand after a consolidation, confirm with `rclone lsf $RCLONE_REMOTE:$RCLONE_BUCKET/catalog/data --max-depth 2` that the data type directories arrived, then update D-33 to say the backup is scheduled."
  - "Answer the still-open D-33 question: was the 2026-09-19 17:53 VPS catalog reset deliberate?"
---

<intent-contract>

## Intent

**Problem:** The shared catalog grows ~1,440 minute-sized Parquet files per (data type, instrument) per day for dYdX, Bybit and Hyperliquid alike (audit D-36). `collector_core/consolidate_catalog.py` exists but is only tested on dYdX ids, its schema guard compares column names only (the Arrow metadata carries `price_precision`, so a mid-day precision change would be silently relabelled by `pa.concat_tables`), its run emits no measurements, and there is no catalog backup at all (D-33).

**Approach:** Prove the job venue-agnostic and reader-equivalent with tests, tighten the schema guard to the full schema including metadata, make each run self-report wall time, peak RSS and files/MB before and after, add a `make backup-catalog` rclone target that never uploads today's files, and document cron + backup. VPS measurement and backup configuration are operator actions → story ends `awaiting-operator`.

## Boundaries & Constraints

**Always:** plain pyarrow merge (never `ParquetDataCatalog.consolidate_data_by_period` — see story Dev Notes); today's (UTC) files never read, rewritten or uploaded; row count verified before any source is deleted; one (type, instrument, day) in memory at a time (MEM-01); every refusal recorded in `error_ledger` and reflected in a non-zero exit code (DATA-07); credentials/remote names only from `troll/.env` or env vars; real Nautilus objects and a real tmp `ParquetDataCatalog` in tests (TEST-03); migrated test moves with import-path changes only.

**Block If:** the merged file cannot be made readable by `ParquetDataCatalog.query` without changing `nautilus_trader/` or `crates/`.

**Never:** modify `nautilus_trader/` or `crates/`; touch collectors or `ml_signals` readers; commit any rclone config, bucket name or key; `rclone sync` without a `--backup-dir` (a wiped local catalog must not erase the only backup — D-33).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Every venue | `.DYDX`, `.BYBIT`, `.HYPERLIQUID` snapshot leaves + a `mark_price_update` leaf, 2 closed days + today | each closed day → 1 file per leaf; today's files untouched; rerun → `0 day(s)` | none |
| Midnight crossing | one batch 23:59:30–00:00:30 between two closed days | crossing file left as-is; both days' inside files merged | none |
| Column mismatch | old-schema (no OHLC) file in a closed day | day refused, files unchanged | `consolidate.mixed_schema` ledger entry, exit 1 |
| Metadata mismatch | same columns, different `price_precision` metadata in one day | day refused, files unchanged | `consolidate.mixed_schema`, exit 1 |
| Interrupted run | merged file renamed in, sources still present | sources deleted, no duplicate rows | row mismatch → `consolidate.row_count`, nothing deleted |
| Backup unconfigured | `RCLONE_REMOTE` or `RCLONE_BUCKET` unset, or `catalog/data` missing/empty | nothing uploaded | message on stderr, exit 1 |

</intent-contract>

## Code Map

- `troll/collector_core/consolidate_catalog.py` -- the job: `leaf_dirs`, `closed_days_needing_work`, `_schemas_agree` (names only today), `_merge`, `consolidate_directory`, `main`.
- `troll/dydx_collector/tests/test_consolidate_catalog.py` -- 3 existing tests; belongs in `collector_core/tests/` (22.3 moved the module, not the test).
- `troll/collector_core/build_candles.py:85` -- `rebuild_instrument(db_path, catalog_path, iid, start_ns, end_ns, allow_open_day=False)`; reader to prove unchanged.
- `troll/ml_signals/catalog_stats.py:92,166` -- `query_second_ohlc`, `data_file_ranges`; parse filename stems.
- `troll/ml_signals/candle_store.py:63,289` -- `connect_rw`, `window(db, iid, bar_seconds, before_ms, limit)`; `BAR_SECONDS`.
- `troll/dydx_collector/tests/test_build_candles.py:37-80` -- golden pattern for rebuild + window comparison.
- `troll/Makefile:147-153` -- `consolidate` target + cron comment; `troll/docker-compose.yml:50` -- host catalog is `./dydx_collector/catalog`.
- `troll/README.md`, `troll/.env-example`, `troll/docs/DATA_INTEGRITY_AUDIT.md` (D-33 row 107, D-36 row 116, runbook step 6 line 197), `troll/ARCHITECTURE.md:115` -- docs.

## Tasks & Acceptance

**Execution:**
- [x] `git mv troll/dydx_collector/tests/test_consolidate_catalog.py troll/collector_core/tests/` -- test follows its module; content unchanged apart from any import path.
- [x] `troll/collector_core/consolidate_catalog.py` -- `_schemas_agree` compares the full `pq.read_schema` (names, types, nullability, metadata — `Schema.equals(check_metadata=True)`); extract a `run(catalog_path, data_types, max_days, apply, now_ns)` returning a small stats result (days done, days refused, files/bytes before and after, wall seconds, peak RSS from `resource.getrusage`) that `main` logs as one summary line and turns into exit code 1 when any day was refused; count refusals inside `consolidate_directory` without changing its return value; delete leftover `*.parquet.tmp` files (a crash inside `_merge`, never a catalog file) from a leaf before processing it. Keep `--data-type`/`--days`/`--apply`.
- [x] `troll/collector_core/tests/test_consolidate_catalog.py` -- add: every-venue test (3 snapshot venues + a `MarkPriceUpdate` leaf via `run`, rerun reports 0); reader equivalence (`ParquetDataCatalog.query(data_cls=DydxSecondSnapshot, identifiers=[iid])` same `ts_event` list, merged schema metadata == source metadata, `data_file_ranges` sane, `rebuild_instrument` → `candle_store.window` identical for every `BAR_SECONDS` before/after); midnight-crossing test; precision-metadata mismatch refusal; exit code/stats of `run` on a refused day.
- [x] `troll/Makefile` -- `consolidate` passes `$(CONSOLIDATE_ARGS)` through; new `backup-catalog` target (host `rclone sync ./dydx_collector/catalog/data "$(RCLONE_REMOTE):$(RCLONE_BUCKET)/catalog/data"`, `--exclude` today's `YYYY-MM-DDT*` stems and `*.tmp`, `--backup-dir` a dated `catalog-replaced/` prefix, `--transfers 8 --fast-list`; refuses when vars unset, `rclone` missing, or local data dir empty); add both to `.PHONY`; cron comment runs backup after consolidate.
- [x] `troll/.env-example` -- commented `RCLONE_REMOTE=`/`RCLONE_BUCKET=` with a one-line pointer to README.
- [x] `troll/README.md` -- new "Nightly maintenance" section: cron line, what the summary line reports, why consolidation precedes backup (millions of tiny objects otherwise), R2/B2 free tiers as cheap targets (prices change — check), `candles_*.db` needs no backup (`make build-candles` rebuilds it), rclone remote setup lives in the operator's `~/.config/rclone`, never the repo.
- [x] `troll/docs/DATA_INTEGRITY_AUDIT.md` + `troll/ARCHITECTURE.md` -- D-36: every venue covered, metadata guard, self-reported numbers, VPS still NOT measured; D-33: backup target exists, not yet scheduled; runbook step 6 mentions backup.

**Acceptance Criteria:**
- Given the collector image, when `pytest collector_core/tests dydx_collector/tests ml_signals/tests` runs, then all pass and no test remains under `dydx_collector/tests` for consolidation.
- Given `RCLONE_REMOTE`/`RCLONE_BUCKET` unset, when `make backup-catalog` runs, then it exits non-zero with a message naming the missing variable.
- Given a VPS run is owed, when the story finishes, then the spec is `awaiting-operator` with the measurement, cron install and rclone configuration listed under `operator_actions`.

## Design Notes

`pa.concat_tables` ignores schema metadata and keeps the first table's, so files differing only in `price_precision` (a venue tick-size change mid-day) would merge into one file whose label is wrong for part of its rows — exactly the precision incident class the catalog refuses on read. Probe on the collector image: a catalog-written `mark_price_update` file's metadata is `{instrument_id, price_precision}`, stable across files of one instrument, so full-schema equality refuses nothing today.

Peak RSS must be measured inside the container: `/usr/bin/time -v make consolidate` measures the `docker compose` client, not the job — hence the job reports its own `ru_maxrss`.

Known limit: a file arriving for an already-consolidated day wholly inside the merged file's span is indistinguishable from an interrupted run whose rows disagree; the job refuses it loudly (`consolidate.row_count`) rather than guessing. Cron at 03:07 UTC is hours after the last flush of the day, so this needs a writer other than the collector.

## Verification

**Commands:**
- `docker run --rm -v "$PWD":/app -w /app -e HOME=/tmp troll-collector:latest python3 -m pytest collector_core/tests dydx_collector/tests ml_signals/tests -q` (from `troll/`) -- expected: all pass
- `ruff check` + `ruff format --check` on changed Python files -- expected: clean
- `make -n consolidate backup-catalog` (from `troll/`) -- expected: renders; `RCLONE_REMOTE= make backup-catalog` exits 1 with message

## Auto Run Result

Status: awaiting-operator

**Summary:**
- `collector_core/consolidate_catalog.py` is proven venue-agnostic: tests cover `.DYDX`, `.BYBIT` and `.HYPERLIQUID` snapshot leaves plus a real `MarkPriceUpdate` leaf, and a rerun reports 0 days.
- Tests prove readers are unchanged on merged files: `ParquetDataCatalog.query`, `query_second_ohlc`, `data_file_ranges`, and `rebuild_instrument` producing identical `candle_store.window` output for every bar size.
- The schema guard now compares the full Arrow schema, metadata included. `pa.concat_tables` silently keeps the first file's metadata, and the catalog does write mixed `price_precision` files into one leaf (verified), so the old names-only check would have relabelled rows.
- Each run prints one summary line with days done/refused, leaves failed, files and MB before and after, wall seconds and its own peak RSS (measured inside the container), and exits 1 on any refusal.
- Hardening from review: `bar` leaves are excluded (they are the backfill's coverage record), failures are isolated per day/leaf, interrupted-run recovery is verified by exact `ts_init`, the job has its own temp suffix, and an flock enforces one run at a time.
- New `make backup-catalog` does a host `rclone sync` that excludes today's files and `*.tmp`, uses a dated `--backup-dir` so a wipe can never erase the backup, and refuses when unconfigured, when rclone is missing, or when the catalog is empty or only has today's files. `make consolidate` takes `CONSOLIDATE_ARGS`. The cron line runs consolidate, then backup.

**Files changed:**
- `troll/collector_core/consolidate_catalog.py`: full-schema guard, `RunStats`/`run()`/`main(argv) -> int`, per-day/leaf failure isolation, exact interrupted-run check, `bar` exclusion, `.consolidate.tmp`, flock.
- `troll/collector_core/tests/test_consolidate_catalog.py`: moved from `dydx_collector/tests/` (the three original tests kept; one line adapted to the new temp suffix) plus 11 new tests.
- `troll/Makefile`: `consolidate` passthrough, `backup-catalog`, cron comment.
- `troll/README.md`: quick-reference rows and a "Nightly maintenance" section.
- `troll/.env-example`: `RCLONE_REMOTE`/`RCLONE_BUCKET`.
- `troll/docs/DATA_INTEGRITY_AUDIT.md`: D-33 (backup target exists, not scheduled), D-36 (coverage and guards; VPS NOT measured) and runbook step 6.
- `troll/ARCHITECTURE.md`: catalog tools line.

**Review:** 11 patches applied (1 high, 5 medium, 5 low), 0 deferred, 9 rejected. The rejected items were an rclone per-object HEAD cost optimisation, cron alerting beyond the ledger, Makefile unit tests, a real-clock test, markdown line length, and summary-count drift from live flushes.

**Verification:**
- `pytest collector_core/tests dydx_collector/tests ml_signals/tests` in the troll-collector image: 358 passed. The 4 failures (`test_ofi_strategy*.py`) and 3 collection errors (`ml_signals/tests/test_{snapshot_backtest_node,timeframe_backtest,watchlist_multi_coin_backtest}.py`) are identical at the baseline HEAD and unrelated.
- All 14 consolidation tests pass.
- `ruff check` and `ruff format --check` (0.15.16) are clean on both changed Python files. `mypy --disallow-incomplete-defs` reports no errors in `consolidate_catalog.py`.
- `RCLONE_REMOTE= make backup-catalog` fails with a message naming the variable. `make -n backup-catalog` with `RCLONE_REMOTE=r2:` renders `r2:b/...`, the dated `--backup-dir` and the today exclude. `make -n consolidate CONSOLIDATE_ARGS="--days 3"` renders the flag.

**Residual risks:**
- The VPS runtime, RSS and file counts are unmeasured, and D-36 says so. The backup has never run against a real remote because rclone isn't installed here. Both are listed under `operator_actions`.
- The first sync uploads the whole history. Nightly `sync` then moves each consolidated day's minute objects to `catalog-replaced/`, which must be pruned by hand.
- rclone's default modtime comparison costs one HEAD request per object on S3-type remotes. `--fast-list` limits listing cost but not that.

## Operator Confirmation

Confirmed 2026-09-21: the external actions this story owed were carried out.

- On nifelheim, deploy this branch (make redeploy-all) and run the first full consolidation by hand from troll/: `make consolidate`. Copy its final `consolidate: ...` summary line (days, files and MB before -> after, wall seconds, peak RSS) into troll/docs/DATA_INTEGRITY_AUDIT.md D-36 as **measured** (first full run), and confirm peak RSS stays well inside the box's free memory (MEM-01).
- Install the nightly cron line from troll/README.md 'Nightly maintenance' in the VPS host crontab (add CRON_TZ=UTC if the box is not on UTC), then after the first nightly run copy that night's summary line from consolidate.log into D-36 as the measured nightly run.
- Choose an object-storage provider (Cloudflare R2 or Backblaze B2), create a bucket, install rclone on the VPS host, run `rclone config` to create the remote (credentials stay in ~/.config/rclone), and set RCLONE_REMOTE and RCLONE_BUCKET (bare values) in troll/.env.
- Run `make backup-catalog` once by hand after a consolidation, confirm with `rclone lsf $RCLONE_REMOTE:$RCLONE_BUCKET/catalog/data --max-depth 2` that the data type directories arrived, then update D-33 to say the backup is scheduled.
- Answer the still-open D-33 question: was the 2026-09-19 17:53 VPS catalog reset deliberate?

_Appended by the bmad-loop orchestrator (`bmad-loop confirm`, #335): a human confirmed these external actions out of band, and the story was advanced from `awaiting-operator` to `done`._
