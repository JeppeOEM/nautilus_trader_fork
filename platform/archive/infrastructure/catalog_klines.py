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
The `--kline-source catalog` adapter: story 22.9's backfilled `<iid>-1-MINUTE-LAST-EXTERNAL`
bars as `VenueKlines`. Their values went through the adapters' f64 parse (D-52), so
`archive.compare_klines` only reports with it and never writes `verified_days`.
"""

from archive.domain.reconciliation import DAY_MS
from archive.domain.reconciliation import MINUTE_MS
from archive.domain.reconciliation import Kline
from archive.domain.reconciliation import kline_from_text
from archive.domain.reconciliation import traded_in_day
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_MS_NS = 1_000_000


class CatalogKlines:
    """
    `VenueKlines` over the catalog's own backfilled venue bars (close-stamped: a bar opens at
    `ts_event - 60 s`).

    Invariant: never a source of a verdict -- its values are f64-parsed (D-52), so the
    composition root pairs it with no `VerifiedDays` port and it can release no trade file.
    """

    def __init__(self, catalog: ParquetDataCatalog) -> None:
        self._catalog = catalog

    def fetch(self, inst: Instrument, day_ms: int) -> list[Kline]:
        bar_type = f"{inst.id.value}-1-MINUTE-LAST-EXTERNAL"
        start_ns = (day_ms + MINUTE_MS) * _MS_NS
        bars: list[Bar] = self._catalog.bars(
            bar_types=[bar_type], start=start_ns, end=start_ns + DAY_MS * _MS_NS
        )
        return traded_in_day(
            [
                kline_from_text(
                    b.ts_event // _MS_NS - MINUTE_MS,
                    (str(b.open), str(b.high), str(b.low), str(b.close), str(b.volume)),
                    inst.price_precision,
                    inst.size_precision,
                )
                for b in bars
            ],
            day_ms,
        )
