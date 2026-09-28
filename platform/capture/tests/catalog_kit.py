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
Read capture's own written snapshots back for its tests (Story 26.2): the same query as
`views.catalog_reads.query_second_snapshots`, over `ParquetDataCatalog` directly, because capture
imports no `views` (spine AD-D2 has no capture -> views edge).
"""

from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.persistence.catalog import ParquetDataCatalog


def query_second_snapshots(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[DydxSecondSnapshot]:
    """
    `DydxSecondSnapshot` rows of `instrument_id` with `ts_event` in [start_ns, end_ns],
    CustomData-unwrapped. `query` bounds on `ts_init` and the window is `ts_event`, so the end is
    widened by the read margin and the exact `ts_event` filter decides.

    Known limit: a copy of the views reader, so the two can drift and capture's tests no longer
    prove the production reader reads what capture writes (only `data_api`/`views` tests do); it
    also keeps that reader's unwidened start (see `views.catalog_reads`). Upgrade path: move the
    read into `kernel.catalog_files`, which both contexts may import, and call it from both.
    """
    results = ParquetDataCatalog(catalog_path).query(
        data_cls=DydxSecondSnapshot,
        identifiers=[instrument_id],
        start=start_ns,
        end=end_ns + READ_SPAN_MARGIN_NS,
    )
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    return [s for s in snapshots if start_ns <= s.ts_event <= end_ns]
