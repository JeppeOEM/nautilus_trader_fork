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
trade_backfill: the venue parsers on recorded real responses (`fixtures/*_trades_*_20260921.json`,
captured 2026-09-21 with curl), exactness, and the fetch/paging/stop rules through a fake `http`.
Real Nautilus instruments with each venue's live precisions (verified 2026-09-21 through the
pyo3 HTTP clients: dYdX BTC-USD 0/4, Bybit BTCUSDT linear 2/3, spot 1/6, Hyperliquid BTC 1/5).
No network.
"""

import json
import urllib.request
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from collector_core.trade_backfill import BackfillError
from collector_core.trade_backfill import exact_text
from collector_core.trade_backfill import fetch_trades
from collector_core.trade_backfill import iso_to_ns
from collector_core.trade_backfill import parse_bybit_trades
from collector_core.trade_backfill import parse_dydx_trades
from collector_core.trade_backfill import parse_hyperliquid_trades
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_FIXTURES = Path(__file__).parent / "fixtures"
_S = 1_000_000_000
_MS = 1_000_000
_TS_INIT = 1_789_990_700 * _S


def _instrument(iid: str, raw: str, price_p: int, size_p: int) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(raw),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_p,
        price_increment=Price.from_str(str(Decimal(1).scaleb(-price_p))),
        size_precision=size_p,
        size_increment=Quantity.from_str(str(Decimal(1).scaleb(-size_p))),
        ts_event=0,
        ts_init=0,
    )


_DYDX = _instrument("BTC-USD-PERP.DYDX", "BTC-USD", 0, 4)
_LINEAR = _instrument("BTCUSDT-LINEAR.BYBIT", "BTCUSDT", 2, 3)
_SPOT = _instrument("BTCUSDT-SPOT.BYBIT", "BTCUSDT", 1, 6)
_HL = _instrument("BTC-USD-PERP.HYPERLIQUID", "BTC", 1, 5)


def _fixture(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


class _Recorder:
    """Fake transport: returns recorded payloads in order and keeps the requests it saw."""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request) -> Any:
        self.requests.append(request)
        return self.payloads.pop(0)


# -- exactness -------------------------------------------------------------------------------------


def test_exact_text_pads_to_the_precision_without_rounding() -> None:
    assert exact_text("84765.0", 1) == "84765.0"
    assert exact_text("84776", 2) == "84776.00"
    assert exact_text("84700.00", 1) == "84700.0"  # trailing zeros only: still exact


@pytest.mark.parametrize(("text", "precision"), [("84765.05", 1), ("0.0000001", 6), ("abc", 2)])
def test_exact_text_refuses_what_it_would_have_to_round(text: str, precision: int) -> None:
    with pytest.raises(BackfillError):
        exact_text(text, precision)


def test_iso_created_at_is_exact_integer_nanoseconds() -> None:
    assert iso_to_ns("2026-09-21T11:36:07.507Z") == 1_789_990_567_507_000_000
    assert iso_to_ns("1970-01-01T00:00:00.001Z") == _MS


def test_iso_without_timezone_is_refused() -> None:
    with pytest.raises(BackfillError):
        iso_to_ns("2026-09-21T11:36:07.507")


# -- parsers on recorded responses -----------------------------------------------------------------


def test_dydx_parser_on_the_recorded_indexer_response() -> None:
    trades = parse_dydx_trades(_fixture("dydx_trades_btc_usd_20260921.json"), _DYDX, _TS_INIT)
    assert len(trades) == 100
    first = trades[0]  # newest first, as the venue sends them
    assert first.trade_id == TradeId("06566eb00000000200000002")
    assert (first.price, first.size) == (Price.from_str("84776"), Quantity.from_str("0.0001"))
    assert (first.price.precision, first.size.precision) == (0, 4)
    assert first.aggressor_side == AggressorSide.SELLER
    assert (first.ts_event, first.ts_init) == (1_789_990_567_507_000_000, _TS_INIT)
    assert sum(t.aggressor_side == AggressorSide.BUYER for t in trades) == 64


def test_bybit_linear_parser_on_the_recorded_response() -> None:
    payload = _fixture("bybit_trades_btcusdt_linear_20260921.json")
    trades = parse_bybit_trades(payload, _LINEAR, _TS_INIT)
    assert len(trades) == 50
    first = trades[0]
    assert first.trade_id == TradeId("f1bc074e-9b1e-55fa-8c29-dcf32839e32f")
    assert (first.price, first.size) == (Price.from_str("84700.00"), Quantity.from_str("0.032"))
    assert first.aggressor_side == AggressorSide.BUYER
    assert first.ts_event == 1_789_990_671_666 * _MS


def test_bybit_spot_parser_on_the_recorded_response() -> None:
    payload = _fixture("bybit_trades_btcusdt_spot_20260921.json")
    trades = parse_bybit_trades(payload, _SPOT, _TS_INIT)
    assert len(trades) == 60  # spot's whole depth
    first = trades[0]
    assert first.trade_id == TradeId("2290000001212535061")
    assert (first.price, first.size) == (Price.from_str("84732.5"), Quantity.from_str("0.000094"))
    assert first.ts_event == 1_789_990_668_641 * _MS


def test_hyperliquid_parser_on_the_recorded_response() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    trades = parse_hyperliquid_trades(payload, _HL, _TS_INIT)
    assert len(trades) == 10  # recentTrades returns exactly the last 10
    first, last = trades[0], trades[-1]
    assert first.trade_id == TradeId("673362615859985")
    assert (first.price, first.size) == (Price.from_str("84765.0"), Quantity.from_str("0.01904"))
    assert first.aggressor_side == AggressorSide.SELLER  # "A": the ask side took
    assert first.ts_event == 1_789_990_672_083 * _MS
    assert (last.trade_id, last.aggressor_side) == (TradeId("32515815873952"), AggressorSide.BUYER)


def test_bybit_error_code_is_an_error() -> None:
    with pytest.raises(BackfillError, match="retCode"):
        parse_bybit_trades({"retCode": 10001, "retMsg": "params error"}, _LINEAR, _TS_INIT)


def test_a_price_finer_than_the_precision_is_an_error_not_a_rounded_trade() -> None:
    payload = _fixture("dydx_trades_btc_usd_20260921.json")
    payload["trades"][0]["price"] = "84776.5"  # BTC-USD's price precision is 0
    with pytest.raises(BackfillError, match="precision"):
        parse_dydx_trades(payload, _DYDX, _TS_INIT)


def test_an_unknown_taker_side_is_an_error() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    payload[0]["side"] = "X"
    with pytest.raises(BackfillError, match="side"):
        parse_hyperliquid_trades(payload, _HL, _TS_INIT)


# -- fetch: Bybit / Hyperliquid (one response, depth-bounded) --------------------------------------


def test_bybit_fetch_filters_since_orders_oldest_first_and_reports_coverage() -> None:
    payload = _fixture("bybit_trades_btcusdt_linear_20260921.json")
    rows = payload["result"]["list"]
    oldest_ns = int(rows[-1]["time"]) * _MS
    since = int(rows[9]["time"]) * _MS  # the 10 newest trades are wanted
    http = _Recorder(payload)
    fetched = fetch_trades(_LINEAR, since, 0, "mainnet", _TS_INIT, http)
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
    fetched = fetch_trades(_LINEAR, 0, 0, "mainnet", _TS_INIT, _Recorder(payload))
    assert fetched.reached_since
    assert len(fetched.trades) == 50


def test_a_full_spot_response_whose_oldest_is_after_since_is_not_reached() -> None:
    payload = _fixture("bybit_trades_btcusdt_spot_20260921.json")  # 60: spot's full depth
    http = _Recorder(payload)
    fetched = fetch_trades(_SPOT, 0, 0, "mainnet", _TS_INIT, http)
    assert not fetched.reached_since
    assert fetched.oldest_ns == int(payload["result"]["list"][-1]["time"]) * _MS
    assert "category=spot" in http.requests[0].full_url


def test_hyperliquid_fetch_posts_recent_trades_and_ten_is_a_full_page() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    http = _Recorder(payload)
    fetched = fetch_trades(_HL, 0, 0, "mainnet", _TS_INIT, http)
    assert json.loads(http.requests[0].data) == {"type": "recentTrades", "coin": "BTC"}  # type: ignore[arg-type]
    assert http.requests[0].full_url == "https://api.hyperliquid.xyz/info"
    assert not fetched.reached_since
    assert len(fetched.trades) == 10


def test_an_inexact_trade_is_rejected_and_the_others_kept() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    payload[0]["px"] = "84765.05"
    fetched = fetch_trades(_HL, 0, 0, "mainnet", _TS_INIT, _Recorder(payload))
    assert len(fetched.trades) == 9
    assert len(fetched.rejected) == 1


# -- fetch: dYdX paging ---------------------------------------------------------------------------


_T0 = 1_789_990_000 * _S


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns // _S, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S") + (
        f".{ns % _S // _MS:03d}Z"
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
    return _T0 + n * step_ms * _MS


def test_dydx_pages_backwards_until_since_and_dedups_the_inclusive_overlap() -> None:
    http = _Recorder(_dydx_page(2999, 1000), _dydx_page(2000, 1000), _dydx_page(1001, 1000))
    fetched = fetch_trades(_DYDX, _ns(1500), 0, "mainnet", _TS_INIT, http)
    assert fetched.reached_since
    assert [t.trade_id.value for t in fetched.trades] == [str(n) for n in range(1500, 3000)]
    assert fetched.oldest_ns == _ns(1001)
    assert "createdBeforeOrAt" not in http.requests[0].full_url
    assert f"createdBeforeOrAt={_iso(_ns(2000)).replace(':', '%3A')}" in http.requests[1].full_url
    assert "/v4/trades/perpetualMarket/BTC-USD?limit=1000" in http.requests[0].full_url
    assert http.requests[0].get_header("User-agent")


def test_dydx_short_page_is_history_exhausted() -> None:
    http = _Recorder(_dydx_page(499, 500))
    fetched = fetch_trades(_DYDX, _ns(0) - _S, 0, "mainnet", _TS_INIT, http)
    assert fetched.reached_since
    assert len(http.requests) == 1


def test_dydx_stops_at_the_arrival_margin_floor_unreached() -> None:
    http = _Recorder(_dydx_page(2999, 1000), _dydx_page(2000, 1000))
    fetched = fetch_trades(_DYDX, _ns(0), _ns(2500), "mainnet", _TS_INIT, http)
    assert not fetched.reached_since
    assert len(http.requests) == 1  # the first page already went past the floor
    assert fetched.oldest_ns == _ns(2000)


def test_dydx_stops_after_twenty_pages_unreached() -> None:
    pages = [_dydx_page(100_000 - 999 * k, 1000) for k in range(25)]
    http = _Recorder(*pages)
    fetched = fetch_trades(_DYDX, 0, 0, "mainnet", _TS_INIT, http)
    assert len(http.requests) == 20
    assert not fetched.reached_since


def test_dydx_page_without_progress_stops_unreached() -> None:
    same = {"trades": [{**row, "createdAt": _iso(_T0)} for row in _dydx_page(999, 1000)["trades"]]}
    http = _Recorder(same, same, same)
    fetched = fetch_trades(_DYDX, 0, 0, "mainnet", _TS_INIT, http)
    assert len(http.requests) == 2
    assert not fetched.reached_since


def test_a_bybit_response_that_is_not_an_object_is_an_error() -> None:
    with pytest.raises(BackfillError, match="not an object"):
        fetch_trades(_LINEAR, 0, 0, "mainnet", _TS_INIT, _Recorder([]))


def test_a_row_without_a_valid_time_is_rejected_alone() -> None:
    payload = _fixture("hyperliquid_recent_trades_btc_20260921.json")
    del payload[3]["time"]
    fetched = fetch_trades(_HL, 0, 0, "mainnet", _TS_INIT, _Recorder(payload))
    assert len(fetched.trades) == 9
    assert fetched.rejected[0].endswith("no valid trade time")


def test_an_inverse_bybit_id_is_refused_before_any_request() -> None:
    """Only linear and spot recent-trade depths are wire-verified (22.14): inverse is not guessed."""
    http = _Recorder()
    inverse = _instrument("BTCUSD-INVERSE.BYBIT", "BTCUSD", 1, 0)
    with pytest.raises(BackfillError, match="not wire-verified"):
        fetch_trades(inverse, 0, 0, "mainnet", _TS_INIT, http)
    assert http.requests == []
