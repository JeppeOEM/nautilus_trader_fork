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
price-series backfill (moved from the catalog-stats module in Story 25.2).

Close prices are read through `kernel.catalog_files.query_second_ohlc` (a column projection of the
second-snapshot files, no 20-level book decode). Only trade closes: a window in which the
instrument never traded is an empty series (its pct/volatility stay None), never mark prices
passed off as trades -- Story 31.3 deleted that fallback, which mixed a second quantity into the
trade-close series (and so into `pct_1h`/`pct_24h`/`volatility`).

Story 33.4: each point also carries the second's exact close and traded volume (buy + sell size),
from the same rows' integer units at their own precisions, so the hourly-volume backfill is this
one read, not a second one.
"""

from kernel.catalog_files import query_second_ohlc
from kernel.second_snapshot import SecondOHLC

from ranking.domain.derivs import units_decimal
from ranking.domain.price_series import PricePoint


# No upper bound: the series runs up to the newest archived second.
_OPEN_END_NS = 2**63 - 1


class CatalogPriceHistory:
    """
    `PriceHistory` over the Parquet catalog (read-only).

    Invariant: the series holds trade closes only -- a second with no trade (close_price None)
    contributes nothing, never a zero price or another price kind.
    """

    def __init__(self, catalog_path: str) -> None:
        self._catalog_path = catalog_path

    def series(self, instrument_id: str, start_ns: int) -> list[PricePoint]:
        rows = query_second_ohlc(self._catalog_path, instrument_id, start_ns, _OPEN_END_NS)
        points = [_point(r) for r in rows if r.close_price is not None]
        return sorted(points, key=lambda point: point.ts_event)


def _point(row: SecondOHLC) -> PricePoint:
    if row.close_price is None or row.close_price_units is None:
        raise ValueError(f"second {row.ts_event} has no close: not a traded second")
    volume_units = row.buy_volume_units + row.sell_volume_units
    return PricePoint(
        ts_event=row.ts_event,
        price=row.close_price,
        close=units_decimal(row.close_price_units, row.price_precision),
        volume=units_decimal(volume_units, row.size_precision),
    )
