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
"""Bybit collector: real OrderBook/TradeTick/catalog objects, no network (TEST-03: nothing mocked)."""

import time
from decimal import Decimal
from pathlib import Path

import pytest

from bybit_collector.collector import _CROSSED_RESYNC_NS
from bybit_collector.collector import Collector
from bybit_collector.config import CollectorConfig
from bybit_collector.config import load_config
from bybit_collector.open_interest import BybitOpenInterest
from bybit_collector.open_interest import parse_open_interest
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.catalog_stats import query_second_snapshots
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"
_ID = InstrumentId.from_str(_IID)


def _collector(tmp_path: Path) -> Collector:
    return Collector(
        CollectorConfig("mainnet", str(tmp_path), 60, 300, 0.5, (_IID,)),
    )


def _deltas(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> OrderBookDeltas:
    ts = time.time_ns()
    deltas = [OrderBookDelta.clear(_ID, 0, ts, ts)]
    for side, levels in ((OrderSide.BUY, bids), (OrderSide.SELL, asks)):
        for price, size in levels:
            order = BookOrder(side, Price(price, 2), Quantity(size, 3), 0)
            deltas.append(OrderBookDelta(_ID, BookAction.ADD, order, 0, 0, ts, ts))
    return OrderBookDeltas(_ID, deltas)


def _trade(price: float, size: float, side: AggressorSide, n: int) -> TradeTick:
    ts = time.time_ns()
    return TradeTick(_ID, Price(price, 2), Quantity(size, 3), side, TradeId(str(n)), ts, ts)


def test_snapshot_from_book_and_trades(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0), (99.5, 2.0)], [(100.5, 3.0)]))
    c._process_data(_trade(100.0, 0.5, AggressorSide.BUYER, 1))
    c._process_data(_trade(100.5, 0.25, AggressorSide.SELLER, 2))

    batch, resync = c._sample(time.time_ns())

    assert resync == []
    (snap,) = batch
    assert snap.bid_prices == [100.0, 99.5]
    assert snap.ask_prices == [100.5]
    assert (snap.buy_volume, snap.sell_volume, snap.buy_count, snap.sell_count) == (0.5, 0.25, 1, 1)
    assert (snap.open_price, snap.high_price, snap.low_price, snap.close_price) == (100.0, 100.5, 100.0, 100.5)
    # Accumulators reset: a trade-less second has no fabricated prices.
    (next_snap,), _ = c._sample(time.time_ns())
    assert next_snap.open_price is None
    assert next_snap.buy_volume == 0.0


def test_crossed_book_skipped_then_resync_requested(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(101.0, 1.0)], [(100.0, 1.0)]))
    now = time.time_ns()
    assert c._sample(now) == ([], [])
    assert c._sample(now + _CROSSED_RESYNC_NS + 1)[1] == [_IID]


def test_stale_book_skipped(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    assert c._sample(time.time_ns() + 60_000_000_000) == ([], [])


@pytest.mark.asyncio
async def test_snapshot_and_open_interest_round_trip_through_catalog(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]))
    (snap,), _ = c._sample(time.time_ns())
    c._buffer[(DydxSecondSnapshot, _IID)].append(snap)
    c._process_data(BybitOpenInterest(_ID, Decimal("123.456"), 1, 1))

    await c._flush_once()

    # Read back through data_api's own reader: proves Bybit ids need no per-route code.
    (read,) = query_second_snapshots(str(tmp_path), _IID, 0, time.time_ns() + 10**9)
    assert str(read.instrument_id) == _IID
    assert read.bid_prices == [100.0]
    (oi,) = ParquetDataCatalog(str(tmp_path)).query(BybitOpenInterest, identifiers=[_IID])
    assert oi.data.open_interest == Decimal("123.456")


def test_parse_open_interest() -> None:
    payload = {"result": {"list": [
        {"symbol": "BTCUSDT", "openInterest": "1234.5"},
        {"symbol": "ETHUSDT", "openInterest": ""},
        {"openInterest": "1"},
    ]}}
    (item,) = parse_open_interest(payload, ts=7)
    assert str(item.instrument_id) == "BTCUSDT-LINEAR.BYBIT"
    assert item.open_interest == Decimal("1234.5")


def test_config_defaults_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('instruments = ["BTCUSDT-LINEAR.BYBIT"]\n')
    cfg = load_config(path)
    assert (cfg.environment, cfg.snapshot_interval_seconds, cfg.instruments) == ("mainnet", 0.5, (_IID,))
    path.write_text('environment = "prod"\n')
    with pytest.raises(ValueError, match="environment"):
        load_config(path)
