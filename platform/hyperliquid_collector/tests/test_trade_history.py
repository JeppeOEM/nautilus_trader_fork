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
Hyperliquid's `trade_history` (story 22.14): the parser on the recorded `recentTrades` response
(`fixtures/hyperliquid_recent_trades_btc_20260921.json`, captured 2026-09-21 with curl), and
the 10-trade-depth fetch through a fake `http`. No network.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from collector_core.domain.trade_history import BackfillError
from collector_core.tests.trade_history_kit import MS
from collector_core.tests.trade_history_kit import TS_INIT
from collector_core.tests.trade_history_kit import Recorder
from collector_core.tests.trade_history_kit import fixture
from collector_core.tests.trade_history_kit import instrument

from hyperliquid_collector.trade_history import HyperliquidTradeHistory
from hyperliquid_collector.trade_history import parse_hyperliquid_trades
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_TESTS = Path(__file__).parent
_HL = instrument("BTC-USD-PERP.HYPERLIQUID", "BTC", 1, 5)


def _fixture(name: str) -> Any:
    return fixture(_TESTS, name)


def test_hyperliquid_parser_on_the_recorded_response() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    trades = parse_hyperliquid_trades(payload, _HL, TS_INIT)
    assert len(trades) == 10  # recentTrades returns exactly the last 10
    first, last = trades[0], trades[-1]
    assert first.trade_id == TradeId("673362615859985")
    assert (first.price, first.size) == (Price.from_str("84765.0"), Quantity.from_str("0.01904"))
    assert first.aggressor_side == AggressorSide.SELLER  # "A": the ask side took
    assert first.ts_event == 1_789_990_672_083 * MS
    assert (last.trade_id, last.aggressor_side) == (TradeId("32515815873952"), AggressorSide.BUYER)


def test_an_unknown_taker_side_is_an_error() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    payload[0]["side"] = "X"
    with pytest.raises(BackfillError, match="side"):
        parse_hyperliquid_trades(payload, _HL, TS_INIT)


def test_hyperliquid_fetch_posts_recent_trades_and_ten_is_a_full_page() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    http = Recorder(payload)
    fetched = HyperliquidTradeHistory("mainnet", http).fetch(_HL, 0, 0, TS_INIT)
    assert json.loads(http.requests[0].data) == {"type": "recentTrades", "coin": "BTC"}  # type: ignore[arg-type]
    assert http.requests[0].full_url == "https://api.hyperliquid.xyz/info"
    assert not fetched.reached_since
    assert len(fetched.trades) == 10


def test_an_inexact_trade_is_rejected_and_the_others_kept() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    payload[0]["px"] = "84765.05"
    fetched = HyperliquidTradeHistory("mainnet", Recorder(payload)).fetch(_HL, 0, 0, TS_INIT)
    assert len(fetched.trades) == 9
    assert len(fetched.rejected) == 1


def test_a_row_without_a_valid_time_is_rejected_alone() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    del payload[3]["time"]
    fetched = HyperliquidTradeHistory("mainnet", Recorder(payload)).fetch(_HL, 0, 0, TS_INIT)
    assert len(fetched.trades) == 9
    assert fetched.rejected[0].endswith("no valid trade time")


def test_an_unknown_environment_is_refused_at_construction() -> None:
    # At construction, not as a per-instrument error at the first backfill after a reconnect.
    with pytest.raises(ValueError, match="unknown Hyperliquid environment 'mainet'"):
        HyperliquidTradeHistory("mainet")
