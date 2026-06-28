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
"""Unit tests for new financial calculations in _metrics_from_rolling()."""

import json
from collections import deque

import pytest

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.dashboard import _coin_chart_json
from ml_signals.dashboard import _metrics_from_rolling
from nautilus_trader.model.identifiers import InstrumentId

_IID = InstrumentId.from_str("ETH-USD-PERP.DYDX")


def _snap(
    bid_prices: list[float],
    bid_sizes: list[float],
    ask_prices: list[float],
    ask_sizes: list[float],
    buy_volume: float,
    sell_volume: float,
    buy_count: int,
    sell_count: int,
    ts_event: int = 1_000_000_000,
) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=_IID,
        bid_prices=bid_prices,
        bid_sizes=bid_sizes,
        ask_prices=ask_prices,
        ask_sizes=ask_sizes,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
        buy_count=buy_count,
        sell_count=sell_count,
        ts_event=ts_event,
        ts_init=ts_event,
    )


def _rolling(*snaps: DydxSecondSnapshot) -> dict:
    return {"ETH-USD-PERP.DYDX": deque(snaps)}


def test_cvd() -> None:
    rolling = _rolling(
        _snap([99.0], [1.0], [101.0], [1.0], buy_volume=10.0, sell_volume=4.0, buy_count=1, sell_count=1),
        _snap([99.0], [1.0], [101.0], [1.0], buy_volume=2.0, sell_volume=9.0, buy_count=1, sell_count=1),
    )
    result = _metrics_from_rolling(rolling)
    assert result[0]["cvd"] == (10 + 2) - (4 + 9)


def test_microprice_lean() -> None:
    # bid=100, ask=102; bid_size=1, ask_size=3
    # microprice = (100*3 + 102*1) / (1+3) = (300 + 102) / 4 = 402/4 = 100.5
    # mid = (100 + 102) / 2 = 101.0
    # microprice_lean = 100.5 - 101.0 = -0.5
    rolling = _rolling(
        _snap([100.0], [1.0], [102.0], [3.0], buy_volume=1.0, sell_volume=1.0, buy_count=1, sell_count=1),
    )
    result = _metrics_from_rolling(rolling)
    assert result[0]["microprice_lean"] == pytest.approx(100.5 - 101.0)


def test_avg_trade_size_no_trades() -> None:
    rolling = _rolling(
        _snap([100.0], [1.0], [101.0], [1.0], buy_volume=0.0, sell_volume=0.0, buy_count=0, sell_count=0),
    )
    result = _metrics_from_rolling(rolling)
    assert result[0]["avg_trade_size"] is None


def test_avg_trade_size_value() -> None:
    # Two snaps: total buy_vol=6, sell_vol=4, buy_cnt=2, sell_cnt=3
    # avg_trade_size = (6+4) / (2+3) = 10/5 = 2.0
    rolling = _rolling(
        _snap([100.0], [1.0], [101.0], [1.0], buy_volume=3.0, sell_volume=2.0, buy_count=1, sell_count=2),
        _snap([100.0], [1.0], [101.0], [1.0], buy_volume=3.0, sell_volume=2.0, buy_count=1, sell_count=1),
    )
    result = _metrics_from_rolling(rolling)
    assert result[0]["avg_trade_size"] == pytest.approx(10.0 / 5.0)


def test_metrics_keys() -> None:
    expected_keys = {
        "ts", "instrument_id", "ofi", "ofi_3", "ofi_5", "ofi_10",
        "obi_3", "obi_5", "obi_10", "microprice", "microprice_lean",
        "spread", "cvd", "volume_delta", "buy_count", "sell_count", "avg_trade_size",
    }
    rolling = _rolling(
        _snap([100.0], [1.0], [101.0], [1.0], buy_volume=5.0, sell_volume=3.0, buy_count=2, sell_count=1),
    )
    result = _metrics_from_rolling(rolling)
    assert expected_keys.issubset(result[0].keys())


def test_chart_json_no_rolling() -> None:
    result = json.loads(_coin_chart_json("ETH-USD-PERP.DYDX", None))
    assert result == {"ts": [], "mid": [], "bid": [], "ask": [], "micro": []}


def test_chart_json_ts_conversion() -> None:
    ts_ns = 1_700_000_000_000_000_000
    rolling = _rolling(
        _snap([100.0], [1.0], [101.0], [1.0], buy_volume=1.0, sell_volume=1.0,
              buy_count=1, sell_count=1, ts_event=ts_ns),
    )
    result = json.loads(_coin_chart_json("ETH-USD-PERP.DYDX", rolling))
    assert result["ts"][0] == ts_ns // 1_000_000
