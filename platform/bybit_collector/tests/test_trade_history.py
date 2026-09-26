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
Bybit's `trade_history` (story 22.14): the parser on recorded `recent-trade` responses
(`fixtures/bybit_trades_*_20260921.json`, captured 2026-09-21 with curl), and the depth-bounded
fetch through a fake `http`. No network.
"""

from pathlib import Path
from typing import Any

import pytest
from collector_core.domain.trade_history import BackfillError
from collector_core.tests.trade_history_kit import MS
from collector_core.tests.trade_history_kit import TS_INIT
from collector_core.tests.trade_history_kit import Recorder
from collector_core.tests.trade_history_kit import fixture
from collector_core.tests.trade_history_kit import instrument

from bybit_collector.trade_history import BybitTradeHistory
from bybit_collector.trade_history import parse_bybit_trades
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_TESTS = Path(__file__).parent
_LINEAR = instrument("BTCUSDT-LINEAR.BYBIT", "BTCUSDT", 2, 3)
_SPOT = instrument("BTCUSDT-SPOT.BYBIT", "BTCUSDT", 1, 6)


def _fixture(name: str) -> Any:
    return fixture(_TESTS, name)


def test_bybit_linear_parser_on_the_recorded_response() -> None:
    payload = _fixture("bybit_trades_btcusdt_linear_20260921.json")
    trades = parse_bybit_trades(payload, _LINEAR, TS_INIT)
    assert len(trades) == 50
    first = trades[0]
    assert first.trade_id == TradeId("f1bc074e-9b1e-55fa-8c29-dcf32839e32f")
    assert (first.price, first.size) == (Price.from_str("84700.00"), Quantity.from_str("0.032"))
    assert first.aggressor_side == AggressorSide.BUYER
    assert first.ts_event == 1_789_990_671_666 * MS


def test_bybit_spot_parser_on_the_recorded_response() -> None:
    payload = _fixture("bybit_trades_btcusdt_spot_20260921.json")
    trades = parse_bybit_trades(payload, _SPOT, TS_INIT)
    assert len(trades) == 60  # spot's whole depth
    first = trades[0]
    assert first.trade_id == TradeId("2290000001212535061")
    assert (first.price, first.size) == (Price.from_str("84732.5"), Quantity.from_str("0.000094"))
    assert first.ts_event == 1_789_990_668_641 * MS


def test_bybit_error_code_is_an_error() -> None:
    with pytest.raises(BackfillError, match="retCode"):
        parse_bybit_trades({"retCode": 10001, "retMsg": "params error"}, _LINEAR, TS_INIT)


def test_bybit_fetch_filters_since_orders_oldest_first_and_reports_coverage() -> None:
    payload = _fixture("bybit_trades_btcusdt_linear_20260921.json")
    rows = payload["result"]["list"]
    oldest_ns = int(rows[-1]["time"]) * MS
    since = int(rows[9]["time"]) * MS  # the 10 newest trades are wanted
    http = Recorder(payload)
    fetched = BybitTradeHistory("mainnet", http).fetch(_LINEAR, since, 0, TS_INIT)
    assert [t.trade_id.value for t in fetched.trades][-1] == rows[0]["execId"]
    assert all(t.ts_event >= since for t in fetched.trades)
    assert [t.ts_event for t in fetched.trades] == sorted(t.ts_event for t in fetched.trades)
    assert (fetched.reached_since, fetched.oldest_ns) == (True, oldest_ns)
    url = http.requests[0].full_url
    assert url == (
        "https://api.bybit.com/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=1000"
    )


def test_a_short_bybit_response_reached_since_even_if_its_oldest_is_newer() -> None:
    payload = _fixture("bybit_trades_btcusdt_linear_20260921.json")  # 50 < the 1000 depth
    fetched = BybitTradeHistory("mainnet", Recorder(payload)).fetch(_LINEAR, 0, 0, TS_INIT)
    assert fetched.reached_since
    assert len(fetched.trades) == 50


def test_a_full_spot_response_whose_oldest_is_after_since_is_not_reached() -> None:
    payload = _fixture("bybit_trades_btcusdt_spot_20260921.json")  # 60: spot's full depth
    http = Recorder(payload)
    fetched = BybitTradeHistory("mainnet", http).fetch(_SPOT, 0, 0, TS_INIT)
    assert not fetched.reached_since
    assert fetched.oldest_ns == int(payload["result"]["list"][-1]["time"]) * MS
    assert "category=spot" in http.requests[0].full_url


def test_a_bybit_response_that_is_not_an_object_is_an_error() -> None:
    with pytest.raises(BackfillError, match="not an object"):
        BybitTradeHistory("mainnet", Recorder([])).fetch(_LINEAR, 0, 0, TS_INIT)


def test_an_inverse_bybit_id_is_refused_before_any_request() -> None:
    """Only linear and spot recent-trade depths are wire-verified (22.14): inverse is not guessed."""
    http = Recorder()
    inverse = instrument("BTCUSD-INVERSE.BYBIT", "BTCUSD", 1, 0)
    with pytest.raises(BackfillError, match="not wire-verified"):
        BybitTradeHistory("mainnet", http).fetch(inverse, 0, 0, TS_INIT)
    assert http.requests == []


def test_an_unknown_environment_is_refused_at_construction() -> None:
    # At construction, not as a per-instrument error at the first backfill after a reconnect.
    with pytest.raises(ValueError, match="unknown Bybit environment 'mainet'"):
        BybitTradeHistory("mainet")
