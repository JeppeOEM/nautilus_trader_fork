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
"""Integration tests for ranking.infrastructure.catalog_prices over a real catalog (TEST-01)."""

import tempfile

from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from ranking.infrastructure.catalog_prices import CatalogPriceHistory


_IID = "BTC-USD-PERP.DYDX"


def _write_snapshot(catalog_path: str, close_price: float | None, ts: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            make_snapshot(
                instrument_id=InstrumentId.from_str(_IID),
                bid_prices=[close_price - 1] if close_price else [100.0],
                bid_sizes=[1.0],
                ask_prices=[close_price + 1] if close_price else [102.0],
                ask_sizes=[1.0],
                buy_volume=1.0 if close_price else 0.0,
                sell_volume=0.0,
                buy_count=1 if close_price else 0,
                sell_count=0,
                open_price=close_price,
                high_price=close_price,
                low_price=close_price,
                close_price=close_price,
                ts_event=ts,
                ts_init=ts,
            )
        ]
    )


def test_series_uses_second_snapshot_close_price() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_snapshot(tmp, close_price=100.0, ts=1_000_000_000)
        _write_snapshot(tmp, close_price=101.0, ts=2_000_000_000)
        series = CatalogPriceHistory(tmp).series(_IID, start_ns=0)
    assert series == [(1_000_000_000, 100.0), (2_000_000_000, 101.0)]


def test_series_starts_at_start_ns() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_snapshot(tmp, close_price=100.0, ts=1_000_000_000)
        _write_snapshot(tmp, close_price=101.0, ts=2_000_000_000)
        series = CatalogPriceHistory(tmp).series(_IID, start_ns=1_500_000_000)
    assert series == [(2_000_000_000, 101.0)]


def test_series_skips_seconds_with_no_trade() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _write_snapshot(tmp, close_price=100.0, ts=1_000_000_000)
        _write_snapshot(tmp, close_price=None, ts=2_000_000_000)  # no trade this second
        series = CatalogPriceHistory(tmp).series(_IID, start_ns=0)
    assert series == [(1_000_000_000, 100.0)]


def test_series_falls_back_to_mark_price_when_no_trades_exist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ParquetDataCatalog(tmp).write_data(
            [
                MarkPriceUpdate(
                    instrument_id=InstrumentId.from_str(_IID),
                    value=Price(50.0, 1),
                    ts_event=1_000_000_000,
                    ts_init=1_000_000_000,
                )
            ]
        )
        series = CatalogPriceHistory(tmp).series(_IID, start_ns=0)
    assert series == [(1_000_000_000, 50.0)]
