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
`snapshot_backtest._quotes`: the derived QuoteTicks are written in `ts_init` order even when the
top-of-book rows (sorted by `ts_event`) disagree, which `ParquetDataCatalog.write_data` requires.
"""

from pathlib import Path

from kernel.catalog_files import TopOfBook
from kernel.clocks import NS_PER_S

from nautilus_trader.model.data import QuoteTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from research.strategies.snapshot_backtest import _quotes


_T0 = 1_760_000_000 * NS_PER_S


def test_quotes_are_ordered_by_ts_init_and_the_derived_catalog_accepts_them(
    tmp_path: Path,
) -> None:
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    # Sorted by ts_event, as query_top_of_book returns them, but the second row arrived first:
    # its ts_init is the smaller one (within the kernel's MAX_TS_INIT_SKEW_NS).
    tops = [
        TopOfBook(_T0, _T0 + 3 * NS_PER_S, 100.0, 1.0, 100.1, 2.0),
        TopOfBook(_T0 + NS_PER_S, _T0 + 2 * NS_PER_S, 100.2, 1.0, 100.3, 2.0),
    ]

    quotes = _quotes(instrument, tops)

    assert [(q.ts_event, q.ts_init) for q in quotes] == [
        (_T0 + NS_PER_S, _T0 + 2 * NS_PER_S),
        (_T0, _T0 + 3 * NS_PER_S),
    ]
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data(quotes)  # raises on a decreasing ts_init
    assert [q.ts_init for q in catalog.query(QuoteTick)] == [q.ts_init for q in quotes]
