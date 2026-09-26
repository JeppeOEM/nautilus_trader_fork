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
The `PriceHistory` adapter: one instrument's archived per-second close prices, for the one-time
price-series backfill (moved from `ml_signals.catalog_stats.price_series` in Story 25.2).

Close prices are read through `kernel.catalog_files.query_second_ohlc` (a column projection of the
second-snapshot files, no 20-level book decode). An instrument with no trade in the window falls
back to its mark prices, read through the catalog (`MarkPriceUpdate` is a Nautilus data type the
kernel's read helpers do not project).
"""

from kernel.catalog_files import query_second_ohlc
from observability import error_ledger

from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.persistence.catalog import ParquetDataCatalog


# No upper bound: the series runs up to the newest archived second.
_OPEN_END_NS = 2**63 - 1


class CatalogPriceHistory:
    """
    `PriceHistory` over the Parquet catalog (read-only).

    Invariant: a second with no trade (close_price None) contributes nothing, never a zero price;
    the mark-price fallback applies only when the window holds no trade at all.
    """

    def __init__(self, catalog_path: str) -> None:
        self._catalog_path = catalog_path

    def series(self, instrument_id: str, start_ns: int) -> list[tuple[int, float]]:
        rows = query_second_ohlc(self._catalog_path, instrument_id, start_ns, _OPEN_END_NS)
        trades = sorted((r.ts_event, r.close_price) for r in rows if r.close_price is not None)
        if trades:
            return trades
        return self._mark_prices(instrument_id, start_ns)

    def _mark_prices(self, instrument_id: str, start_ns: int) -> list[tuple[int, float]]:
        catalog = ParquetDataCatalog(self._catalog_path)
        try:
            marks = catalog.query(MarkPriceUpdate, identifiers=[instrument_id], start=start_ns)
        except (NotImplementedError, RuntimeError) as exc:
            error_ledger.record(
                # Published ledger site name, kept from the deleted ml_signals.catalog_stats.
                "catalog_stats.mark_prices",
                f"{instrument_id} mark_price_update unreadable",
                exc,
            )
            return []
        return sorted((m.ts_event, m.value.as_double()) for m in marks)
