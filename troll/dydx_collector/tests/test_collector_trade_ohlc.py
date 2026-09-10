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
Tests for the collector's per-second trade OHLC tracking (_process_data), which
replaced raw TradeTick persistence -- see troll/docs/DATA_DICTIONARY.md's
Retention section for why.
"""

from pathlib import Path

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity

from dydx_collector.collector import Collector
from dydx_collector.config import CollectorConfig


_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_TS = 1_000_000_000


def _make_config(catalog_path: Path) -> CollectorConfig:
    return CollectorConfig(
        network=DydxNetwork.TESTNET,
        catalog_path=str(catalog_path),
        flush_interval_seconds=60,
        snapshot_interval_seconds=1.0,
        config_reload_seconds=60,
        open_interest_poll_seconds=60,
        non_config_retain_hours=24.0,
        liquidity_min_oi_usd=100_000.0,
        liquidity_check_seconds=60,
        instruments=(),
        exclude=frozenset(),
    )


def _trade(price: float, size: float, side: AggressorSide, trade_id: str, ts: int = _TS) -> TradeTick:
    return TradeTick(
        instrument_id=_IID,
        price=Price(price, 1),
        size=Quantity(size, 1),
        aggressor_side=side,
        trade_id=TradeId(trade_id),
        ts_event=ts,
        ts_init=ts,
    )


def test_trade_tick_is_not_buffered_for_catalog_write(tmp_path: Path) -> None:
    """Raw TradeTicks must never reach the catalog write buffer (Retention cutover)."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    collector._process_data(_trade(100.0, 1.0, AggressorSide.BUYER, "1"))
    assert not any(TradeTick in key for key in collector._buffer)


def test_open_high_low_close_track_a_single_second_of_trades(tmp_path: Path) -> None:
    """open=first trade, close=last trade, high/low across all trades this second."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    iid = str(_IID)
    for price in (100.0, 105.0, 98.0, 102.0):
        collector._process_data(_trade(price, 1.0, AggressorSide.BUYER, str(price)))

    assert collector._second_open_price[iid] == 100.0
    assert collector._second_high_price[iid] == 105.0
    assert collector._second_low_price[iid] == 98.0
    assert collector._second_close_price[iid] == 102.0


def test_no_trade_leaves_ohlc_trackers_empty(tmp_path: Path) -> None:
    """An instrument with zero trades this second has no entry -- distinguishes
    "no trade occurred" from a fabricated price."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    assert str(_IID) not in collector._second_open_price
    assert str(_IID) not in collector._second_close_price


def test_buy_and_sell_volume_still_tracked_alongside_ohlc(tmp_path: Path) -> None:
    """OHLC tracking is additive -- existing buy/sell volume/count aggregation unaffected."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    iid = str(_IID)
    collector._process_data(_trade(100.0, 2.0, AggressorSide.BUYER, "1"))
    collector._process_data(_trade(101.0, 3.0, AggressorSide.SELLER, "2"))

    assert collector._second_buy_volume[iid] == 2.0
    assert collector._second_sell_volume[iid] == 3.0
    assert collector._second_buy_count[iid] == 1
    assert collector._second_sell_count[iid] == 1
