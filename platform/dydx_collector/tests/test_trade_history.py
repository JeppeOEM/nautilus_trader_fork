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
dYdX's `trade_history` (story 22.14): the parser on the recorded indexer response
(`fixtures/dydx_trades_btc_usd_20260921.json`, captured 2026-09-21 with curl), exactness, and
the paging/stop rules through a fake `http`. No network.
"""

from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from collector_core.domain.trade_history import BackfillError
from collector_core.tests.trade_history_kit import MS
from collector_core.tests.trade_history_kit import TS_INIT
from collector_core.tests.trade_history_kit import Recorder
from collector_core.tests.trade_history_kit import S
from collector_core.tests.trade_history_kit import fixture
from collector_core.tests.trade_history_kit import instrument

from dydx_collector.trade_history import DydxTradeHistory
from dydx_collector.trade_history import parse_dydx_trades
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_TESTS = Path(__file__).parent
_DYDX = instrument("BTC-USD-PERP.DYDX", "BTC-USD", 0, 4)
_T0 = 1_789_990_000 * S


def _fixture(name: str) -> Any:
    return fixture(_TESTS, name)


def test_dydx_parser_on_the_recorded_indexer_response() -> None:
    trades = parse_dydx_trades(_fixture("dydx_trades_btc_usd_20260921.json"), _DYDX, TS_INIT)
    assert len(trades) == 100
    first = trades[0]  # newest first, as the venue sends them
    assert first.trade_id == TradeId("06566eb00000000200000002")
    assert (first.price, first.size) == (Price.from_str("84776"), Quantity.from_str("0.0001"))
    assert (first.price.precision, first.size.precision) == (0, 4)
    assert first.aggressor_side == AggressorSide.SELLER
    assert (first.ts_event, first.ts_init) == (1_789_990_567_507_000_000, TS_INIT)
    assert sum(t.aggressor_side == AggressorSide.BUYER for t in trades) == 64


def test_a_price_finer_than_the_precision_is_an_error_not_a_rounded_trade() -> None:
    payload = _fixture("dydx_trades_btc_usd_20260921.json")
    payload["trades"][0]["price"] = "84776.5"  # BTC-USD's price precision is 0
    with pytest.raises(BackfillError, match="precision"):
        parse_dydx_trades(payload, _DYDX, TS_INIT)


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns // S, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S") + (
        f".{ns % S // MS:03d}Z"
    )


def _dydx_page(newest: int, count: int, step_ms: int = 10) -> dict:
    """`count` trades, newest first, ids `n`, `step_ms` apart, the newest at `_T0 + newest ms`."""
    rows = [
        {
            "id": str(n),
            "side": "BUY",
            "size": "0.0001",
            "price": "84000",
            "createdAt": _iso(_ns(n, step_ms)),
        }
        for n in range(newest, newest - count, -1)
    ]
    return {"trades": rows}


def _ns(n: int, step_ms: int = 10) -> int:
    return _T0 + n * step_ms * MS


def test_dydx_pages_backwards_until_since_and_dedups_the_inclusive_overlap() -> None:
    http = Recorder(_dydx_page(2999, 1000), _dydx_page(2000, 1000), _dydx_page(1001, 1000))
    fetched = DydxTradeHistory("mainnet", http).fetch(_DYDX, _ns(1500), 0, TS_INIT)
    assert fetched.reached_since
    assert [t.trade_id.value for t in fetched.trades] == [str(n) for n in range(1500, 3000)]
    assert fetched.oldest_ns == _ns(1001)
    assert "createdBeforeOrAt" not in http.requests[0].full_url
    assert f"createdBeforeOrAt={_iso(_ns(2000)).replace(':', '%3A')}" in http.requests[1].full_url
    assert "/v4/trades/perpetualMarket/BTC-USD?limit=1000" in http.requests[0].full_url
    assert http.requests[0].get_header("User-agent")


def test_dydx_short_page_is_history_exhausted() -> None:
    http = Recorder(_dydx_page(499, 500))
    fetched = DydxTradeHistory("mainnet", http).fetch(_DYDX, _ns(0) - S, 0, TS_INIT)
    assert fetched.reached_since
    assert len(http.requests) == 1


def test_dydx_stops_at_the_arrival_margin_floor_unreached() -> None:
    http = Recorder(_dydx_page(2999, 1000), _dydx_page(2000, 1000))
    fetched = DydxTradeHistory("mainnet", http).fetch(_DYDX, _ns(0), _ns(2500), TS_INIT)
    assert not fetched.reached_since
    assert len(http.requests) == 1  # the first page already went past the floor
    assert fetched.oldest_ns == _ns(2000)


def test_dydx_stops_after_twenty_pages_unreached() -> None:
    pages = [_dydx_page(100_000 - 999 * k, 1000) for k in range(25)]
    http = Recorder(*pages)
    fetched = DydxTradeHistory("mainnet", http).fetch(_DYDX, 0, 0, TS_INIT)
    assert len(http.requests) == 20
    assert not fetched.reached_since


def test_dydx_page_without_progress_stops_unreached() -> None:
    same = {"trades": [{**row, "createdAt": _iso(_T0)} for row in _dydx_page(999, 1000)["trades"]]}
    http = Recorder(same, same, same)
    fetched = DydxTradeHistory("mainnet", http).fetch(_DYDX, 0, 0, TS_INIT)
    assert len(http.requests) == 2
    assert not fetched.reached_since


def test_an_unknown_environment_is_refused_at_construction() -> None:
    # At construction, not as a per-instrument error at the first backfill after a reconnect.
    with pytest.raises(ValueError, match="unknown dYdX environment 'mainet'"):
        DydxTradeHistory("mainet")
