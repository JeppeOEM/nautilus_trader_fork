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
"""`views.catalog_reads`: the whole-row second read, and the cursor-paging helpers."""

import tempfile

from candles.application.forming import bars_from_rows
from kernel.catalog_files import query_second_ohlc
from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views.catalog_reads import fetch_page
from views.catalog_reads import has_older_data
from views.catalog_reads import query_second_snapshots


_OHLC_IID = "BTC-USD.DYDX"


def _write_ohlc_snapshots(catalog_path: str, base: int, n: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(_OHLC_IID),
                bid_prices=[99.0],
                bid_sizes=[1.0],
                ask_prices=[101.0],
                ask_sizes=[1.0],
                buy_volume=0.5 * i,
                sell_volume=0.25,
                buy_count=1,
                sell_count=1,
                open_price=100.0 + i,
                high_price=101.0 + i,
                low_price=99.5 + i,
                close_price=100.5 + i,
                ts_event=base + i * 1_000_000_000,
                ts_init=base + i * 1_000_000_000,
            )
            for i in range(n)
        ]
    )


def test_query_second_ohlc_matches_this_readers_decoder() -> None:
    """
    The two production readers of a second snapshot must agree (Story 21.5).

    `kernel.catalog_files.query_second_ohlc` projects columns; `views.catalog_reads.
    query_second_snapshots` decodes whole objects, and `repair_catalog` plus the views reads use
    it. Story 24.1 moved the candle fold into `candles/`, which may not import views -- so the
    pairing is asserted here, from the side that owns the decoder (moved with it from the
    catalog-stats tests in Story 24.2).
    """
    base = 1_800_000_000_000_000_000
    with tempfile.TemporaryDirectory() as tmp:
        _write_ohlc_snapshots(tmp, base, 120)
        lo, hi = base + 10_000_000_000, base + 90_000_000_000
        decoded = bars_from_rows(query_second_snapshots(tmp, _OHLC_IID, lo, hi), 60)
        projected = bars_from_rows(query_second_ohlc(tmp, _OHLC_IID, lo, hi), 60)
        assert projected == decoded
        assert len(projected) == 2


def test_fetch_page_jumps_a_gap_to_the_last_data_before_it() -> None:
    ranges = [(0, 100), (1_000, 1_100)]
    calls: list[tuple[int, int]] = []

    def fetch(start_ns: int, end_ns: int) -> list[int]:
        calls.append((start_ns, end_ns))
        return [start_ns] if start_ns < 100 else []

    assert fetch_page(fetch, ranges, 1_050, 200) == [-100]
    assert calls == [(850, 1_050), (-100, 100)]  # the empty window jumps straight to file 1's end


def test_has_older_data_reads_the_oldest_file_start() -> None:
    assert has_older_data([(10, 20), (30, 40)], 11)
    assert not has_older_data([(10, 20)], 10)
    assert not has_older_data([], 10)
