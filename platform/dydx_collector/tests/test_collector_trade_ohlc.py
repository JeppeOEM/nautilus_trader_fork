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
Tests for the collector's per-second trade tracking on dYdX: accepted trades are kept for the
live second (`_second_trades`, folded once per sample by `collector_core.fold.fold_trades`) and
archived raw (`_buffer[(TradeTick, iid)]`, story 22.13 -- the reverse of the earlier retention
cutover that discarded them).
"""

import time
from pathlib import Path

from collector_core.fold import fold_trades

from dydx_collector.collector import DydxCollector
from dydx_collector.config import DydxConfig
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")
_TS = time.time_ns()


def _make_config(catalog_path: Path) -> DydxConfig:
    return DydxConfig(
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


def _trade(
    price: float, size: float, side: AggressorSide, trade_id: str, ts: int = _TS
) -> TradeTick:
    return TradeTick(
        instrument_id=_IID,
        price=Price(price, 1),
        size=Quantity(size, 1),
        aggressor_side=side,
        trade_id=TradeId(trade_id),
        ts_event=ts,
        ts_init=ts,
    )


def _second(collector: DydxCollector) -> dict:
    return fold_trades(collector._second_trades.get(str(_IID), [])).snapshot_values()._asdict()


def test_trade_tick_is_buffered_for_catalog_write(tmp_path: Path) -> None:
    """Raw TradeTicks reach the catalog write buffer, both clocks untouched (story 22.13)."""
    collector = DydxCollector(_make_config(tmp_path / "catalog"))
    trade = _trade(100.0, 1.0, AggressorSide.BUYER, "1", ts=_TS - 5)
    collector._process_data(trade)
    (buffered,) = collector._buffer[(TradeTick, str(_IID))]
    assert (buffered.ts_event, buffered.ts_init) == (trade.ts_event, trade.ts_init)


def test_open_high_low_close_track_a_single_second_of_trades(tmp_path: Path) -> None:
    """open=first trade, close=last trade, high/low across all trades this second."""
    collector = DydxCollector(_make_config(tmp_path / "catalog"))
    for price in (100.0, 105.0, 98.0, 102.0):
        collector._process_data(_trade(price, 1.0, AggressorSide.BUYER, str(price)))

    second = _second(collector)
    assert second["open_price"] == 100.0
    assert second["high_price"] == 105.0
    assert second["low_price"] == 98.0
    assert second["close_price"] == 102.0


def test_no_trade_leaves_ohlc_trackers_empty(tmp_path: Path) -> None:
    """
    An instrument with zero trades this second folds to None OHLC.

    Distinguishes "no trade occurred" from a fabricated price.
    """
    collector = DydxCollector(_make_config(tmp_path / "catalog"))
    assert str(_IID) not in collector._second_trades
    assert _second(collector)["open_price"] is None
    assert _second(collector)["close_price"] is None


def test_buy_and_sell_volume_still_tracked_alongside_ohlc(tmp_path: Path) -> None:
    """OHLC tracking is additive -- buy/sell volume/count aggregation unaffected."""
    collector = DydxCollector(_make_config(tmp_path / "catalog"))
    collector._process_data(_trade(100.0, 2.0, AggressorSide.BUYER, "1"))
    collector._process_data(_trade(101.0, 3.0, AggressorSide.SELLER, "2"))

    second = _second(collector)
    assert second["buy_volume"] == 2.0
    assert second["sell_volume"] == 3.0
    assert second["buy_count"] == 1
    assert second["sell_count"] == 1


def test_discard_second_accumulators_clears_ohlc_and_volume(tmp_path: Path) -> None:
    """
    A skipped snapshot (stale/crossed/missing book) must drop trades seen during it,
    not carry them into the next valid tick.

    Regression for the bug where trades landing during a book outage sat in the
    accumulator until the next *valid* _second_loop tick popped it, stamping the
    entire outage's price range onto a single second -- a giant-range candle at
    recovery instead of an honest gap (see DATA-01/DATA-02 in platform/CLAUDE.md).
    The raw trades stay archived (the nightly rebuild reports them as orphans).
    """
    collector = DydxCollector(_make_config(tmp_path / "catalog"))
    iid = str(_IID)
    collector._process_data(_trade(100.0, 2.0, AggressorSide.BUYER, "1"))
    collector._process_data(_trade(9000.0, 3.0, AggressorSide.SELLER, "2"))

    collector._discard_second_accumulators(iid)

    assert iid not in collector._second_trades
    assert _second(collector)["high_price"] is None
    assert _second(collector)["buy_volume"] == 0.0
    assert len(collector._buffer[(TradeTick, iid)]) == 2


def test_historical_trades_from_subscribe_reply_are_dropped(tmp_path: Path) -> None:
    """Drop the old trades dYdX's subscribed reply replays: they must not enter the live second."""
    collector = DydxCollector(_make_config(tmp_path / "catalog"))
    iid = str(_IID)
    old = time.time_ns() - 3600 * 1_000_000_000
    collector._process_data(_trade(100.0, 5.0, AggressorSide.BUYER, "old", ts=old))

    assert iid not in collector._second_trades
    assert _second(collector)["buy_volume"] == 0.0
    assert collector._stale_trades_dropped[iid] == 1
    assert (TradeTick, iid) not in collector._buffer  # not archived either


def test_replayed_trade_id_is_not_counted_twice(tmp_path: Path) -> None:
    """A reconnect replays trades still inside the age window; the trade id catches them."""
    collector = DydxCollector(_make_config(tmp_path / "catalog"))
    iid = str(_IID)
    collector._process_data(_trade(100.0, 2.0, AggressorSide.BUYER, "same"))
    collector._process_data(_trade(100.0, 2.0, AggressorSide.BUYER, "same"))

    assert _second(collector)["buy_volume"] == 2.0
    assert collector._duplicate_trades_dropped[iid] == 1
    assert len(collector._buffer[(TradeTick, iid)]) == 1
