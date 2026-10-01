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
from decimal import Decimal
from pathlib import Path

import pytest
from candles.application.forming import bars_from_rows
from kernel.catalog_files import query_second_ohlc
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views.catalog_reads import InstrumentPrecision
from views.catalog_reads import NoInstrumentDefinition
from views.catalog_reads import fetch_page
from views.catalog_reads import has_older_data
from views.catalog_reads import instrument_precision
from views.catalog_reads import query_second_snapshots


_OHLC_IID = "BTC-USD.DYDX"


def _write_ohlc_snapshots(catalog_path: str, base: int, n: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            make_snapshot(
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


def test_fetch_page_aligns_the_first_end_and_every_gap_jump_end() -> None:
    ranges = [(0, 130), (1_000, 1_100)]
    calls: list[tuple[int, int]] = []

    def fetch(start_ns: int, end_ns: int) -> list[int]:
        calls.append((start_ns, end_ns))
        return [start_ns] if start_ns < 100 else []

    def up_to_hundreds(ns: int) -> int:
        return -(-ns // 100) * 100

    assert fetch_page(fetch, ranges, 1_050, 200, align_end=up_to_hundreds) == [0]
    assert calls == [(900, 1_100), (0, 200)]  # file 1 ends at 130: the jump rounds up to 200


def _definition(
    iid: str, price_precision: int, size_precision: int, ts_init: int = 0
) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(iid.split(".")[0]),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_precision,
        price_increment=Price.from_str(f"{Decimal(1).scaleb(-price_precision):f}"),
        size_precision=size_precision,
        size_increment=Quantity.from_str(f"{Decimal(1).scaleb(-size_precision):f}"),
        ts_event=ts_init,
        ts_init=ts_init,
    )


def test_instrument_precision_reads_the_definitions_own_decimals(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data(
        [_definition("BTC-USD-PERP.DYDX", 2, 3), _definition("PEPE-USD-PERP.DYDX", 6, 0)]
    )
    assert instrument_precision(str(tmp_path), "BTC-USD-PERP.DYDX") == InstrumentPrecision(2, 3)
    assert instrument_precision(str(tmp_path), "PEPE-USD-PERP.DYDX") == InstrumentPrecision(6, 0)


def test_instrument_precision_uses_the_latest_definition(tmp_path: Path) -> None:
    ParquetDataCatalog(str(tmp_path)).write_data(
        [
            _definition("BTC-USD-PERP.DYDX", 2, 3, ts_init=1),
            _definition("BTC-USD-PERP.DYDX", 1, 4, ts_init=2),
        ]
    )
    assert instrument_precision(str(tmp_path), "BTC-USD-PERP.DYDX") == InstrumentPrecision(1, 4)


def test_instrument_precision_without_a_definition_names_the_id(tmp_path: Path) -> None:
    ParquetDataCatalog(str(tmp_path)).write_data([_definition("BTC-USD-PERP.DYDX", 2, 3)])
    with pytest.raises(NoInstrumentDefinition, match=r"ETH-USD-PERP\.DYDX"):
        instrument_precision(str(tmp_path), "ETH-USD-PERP.DYDX")
