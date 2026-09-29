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
The views context (DDD spine AD-D11): the read models the user interfaces show -- the CQRS query
side. Every market value the web UI (`data_api` + `frontend/`) displays comes from one function
here over one input (SSOT-01..05): the interface formats and transports, it computes nothing. The
TUI (`bot_tui`) shows only bots and the collector since Story 25.1a, so it reads no read model
here.

The modules: `ranking_columns` (the ranking table's column set, and the Technicals tab's latest
values), `coin_detail` (the single-coin page's metrics.db history and archived-seconds reads),
`chart_series` (every chart series and page: candles, Lines-mode snapshots, indicator series and
values, footprint, book features, and the gap-marker rendering rules),
`indicator_picker` (the native + custom indicator catalogs and their replay dispatch),
`preferences` (the one loader/saver of `chart_indicators.toml` and `screener_columns.toml`),
`catalog_reads` (the catalog series read and the cursor-paging helpers), `live_candles` (the live
forming-bar fan-out and the `BarObserver` port) and `rankings_bus` (the `rankings:live` relay).

Three invariants. *The reader never re-validates the gate*: a row the capture gate wrote is served
as written -- a crossed second is priced like any other, and a row the gate can never write (an
empty top of book) is a loud failure (`observability.error_ledger` + an exception the route maps
to 500), never a skip. The only thing that changes what is drawn is an explicit rendering rule (the
gap markers in `chart_series`). *No module state*: nothing here holds a process-wide instance --
`data_api.buses` constructs the two buses -- and nothing reads the environment for a path, except
`indicator_picker`'s custom replays (its `Known limit:`); callers pass the catalog, candle-store and
metrics paths in. *Framework-free*: no `fastapi`, `pydantic` or `urwid`; functions return plain
dicts/dataclasses and raise their own exceptions, which each interface maps to its own errors.
`snapshots:raw` is parsed only through `kernel.second_snapshot.DydxSecondSnapshot.from_dict` (the
exact integer layout since Story 30.2); what views returns to an interface carries the integers
(`price_series_rows`' `bid_units`/`ask_units`, `catalog_snapshot_rows`' wire dicts) or derived
floats, never re-encoded float prices.

Dependency direction: views imports `kernel`, `observability` and the query services of `candles`
(`candles.application.queries`, `candles.application.forming`, `candles.domain.candle`) and of
ranking (`ranking.application.queries.history`/`nearest`) -- never a store adapter
(`candles.infrastructure`), never capture, research or an interface. `data_api` imports views.
`platform/tests/test_boundaries.py` enforces all of it, the query-service names included
(`VIEWS_QUERY_SERVICES`).
"""
