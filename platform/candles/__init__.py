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
The candles context (DDD spine AD-D8): every bar the platform shows, closed or forming, from the
one seconds -> bars fold over the archived 1 s rows.

Four invariants, each owned by a module here. *Exactly once*: a second is folded into a bucket at
most once, enforced by the per-instrument watermark `domain.candle_series.CandleSeries` applies and
`infrastructure.sqlite_store` persists -- the `_UPSERT` accumulates `v` and `seconds_observed`, so a
replayed batch would otherwise double-count. *Rebuildable from seconds*: the Parquet 1 s snapshots
are the archive and the source of truth, this store is derived, and `application.rebuild` recomputes
any whole UTC day from them idempotently (`python -m candles.rebuild`). *Never ahead of the
archive*: `application.sink.CandleSink` is fed only rows whose `ParquetDataCatalog.write_data`
already succeeded (capture's `collector_core.ports.SecondSink` port). *One fold*: `domain.fold`'s
`fold_arrays` is the only seconds -> bars aggregation in `platform/` -- the stored closed bar
(`application.queries.window`), the chart's forming bar (`application.forming.forming_bar`) and the
archive-side read (`application.queries.candle_dicts_for_window`) all call it, so they cannot
disagree. The only other fold in the platform is trades -> second, `kernel.fold.fold_trades`.

`domain/` (the fold, the `Candle` value type, the `CandleSeries` aggregate) is pure: stdlib, numpy
and `kernel` only. `application/` holds the ports (`VerifiedDays`), the query and forming services,
the rebuild logic and the retention process manager. `infrastructure/` holds `CandleStore`, the only
read-write opener of a `candles_<venue>.db`, and the `VerifiedDays` adapters.

Composition roots -- each venue entrypoint, the rebuild CLI, `collector_core.nightly`, the two
archive tools and, until Story 24.2 moves the reads behind views, `data_api`'s two candle routes --
are what construct `infrastructure`; no context outside candles imports it for any other reason.
Known limit: `application/` is not storage-agnostic. `application.sink` and `application.rebuild`
name `CandleStore` concretely rather than a port, and `application.queries` goes further -- it takes
a `sqlite3.Connection` in every signature and holds the `SELECT` text itself, so every reader must
open the file through `infrastructure.connect_ro` to call the application layer at all. The layering
rule therefore holds by convention rather than by type across those three modules
(`application.prune` shows the shape, declaring the `RetentionStore` Protocol it needs). Upgrade
path: give them the same treatment -- a read-model port for `queries` above all -- when a second
store implementation earns one.

The context imports `kernel` and `observability` and no other context; capture depends on it only
through the `SecondSink` port it declares itself, so no `candles` -> `collector_core` edge exists
(`platform/tests/test_boundaries.py` enforces it).
"""
