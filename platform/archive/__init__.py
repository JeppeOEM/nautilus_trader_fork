# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
The archive context (DDD spine AD-D9, AD-D18; Story 25.1): the nightly maintenance of the one
shared `ParquetDataCatalog` -- rebuild, consolidate, reconcile, prune -- and the offline repair,
backfill and diagnostic tools over it.

Four invariants, each owned by one module. *One day status*: an instrument-day is provisional ->
rebuilt -> verified or mismatched -> released (`domain.archive_day.ArchiveDay`); the only persisted
status is the candle store's `verified_days` row, reached through candles' `VerifiedDays` port,
and `rebuilt` lives only inside one saga run (`application.nightly`), so `reconcile_day` refuses
(`reconcile.not_rebuilt`) any instrument that run's rebuild did not cover. *One deleter*: rows
leave the catalog only by `domain.retention.RetentionPolicy`'s decision, executed by
`application.prune` through `CatalogFiles.delete` -- verified-and-aged trades, dropped dYdX
instruments and per-instrument delta retention, read from the collection plan file (the dYdX
collector holds no prune loop any more); the one exception is `application.repair`, which
replaces a wrong row through Nautilus's own `delete_data_range` + `write_data` (a correction,
not a retention; its Known limit). *One rewriter*: every in-place Parquet rewrite is
`infrastructure.catalog_files.CatalogFiles.rewrite` (verified temp-then-rename, Arrow metadata
kept, zstd). *One writer per leaf*: capture writes the current UTC day and archive never changes
a row of it -- `CatalogFiles` refuses a whole-file write, merge or delete of a file whose `ts_init`
span reaches it, and lets the rebuild's row-preserving rewrite (`RewriteMode.KEEP_OPEN_DAY_ROWS`)
touch such a file only when every open-day row comes out identical (`OpenDayWriteError`
otherwise) -- and `repair_catalog` refuses a venue whose collector holds
`<catalog>/.capture-<VENUE>.lock`. The rebuild proof is an allowlist (`RebuildProof.covers`):
only instruments the rebuild names as rebuilt are ever judged.

`domain/` (the day state machine, the retention rules, the exact kline comparison, gap coverage) is
pure: stdlib, `kernel` and Nautilus value types. `application/` declares the ports
(`application.ports`: `CatalogWriter`, `GapMarkers`, `VenueKlines`, `RetentionPlanSource`) and
holds the services and the saga; it never imports `archive.infrastructure` or
`candles.infrastructure`. `infrastructure/` implements the ports: `CatalogFiles` and the two locks,
the gap-marker files, one `klines_<venue>` adapter per venue over `kernel.venue_http`, and the
catalog-bar adapter.

Composition roots -- the only importers of `infrastructure` (and of `candles.infrastructure`) --
are the operator CLIs `python -m archive.<tool>` (`rebuild_seconds`, `consolidate_catalog`,
`prune_catalog`, `repair_catalog`, `compare_klines`, `nightly`, `backfill_bars`,
`crosscheck_errors`, `backup_catalog`), the `archive` service `python -m archive.scheduler`
(Story 25.1b: the one scheduler of the nightly saga, consolidate, backup and the intraday
closed-hour merge -- `application.scheduler` over the `domain.schedule`/`domain.intraday` rules)
and `archive/tools/*` (`measure_lag`, `migrate_open_interest`, `normalize_snapshot_schema`). Their old `collector_core.*` / `dydx_collector.*` re-export shims
were removed in Story 25.3.

Dependency direction: archive imports `kernel`, `observability` and candles' application layer
(`VerifiedDays`, `queries`, `rebuild`); `prune_catalog` alone also reads the collection plan
through `collection_control.infrastructure.plan_store.TomlPlanStore` (Story 25.4). Nothing imports archive but its own
composition roots and tests (capture writes its own
`write_failed`/`quarantined` markers, `collector_core.gap_markers`), and archive imports neither
capture nor views (`platform/tests/test_boundaries.py`).

Known limits: `repair_catalog` rewrites through Nautilus's `delete_data_range` + `write_data`
(AD-6's official path), not `CatalogFiles`; `backfill_bars` keeps its PyO3 f64 fetch path rather
than the `VenueKlines` ACL (moving it would change the written bars, D-52); standalone
`compare_klines --rebuilt-by` is an operator attestation that cannot be checked; retention acts on
closed UTC days only, so its floor is about a day. Each names its upgrade path where it lives.
"""
