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
"""Subscriptions, REST polls and frame classification (pure domain tables)."""

import json

import pytest
from kernel.venues import MalformedInstrumentId

from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import ANOMALY_UNKNOWN
from verification.domain.subscriptions import ANOMALY_UNPARSED
from verification.domain.subscriptions import ANOMALY_VENUE_ERROR
from verification.domain.subscriptions import Classified
from verification.domain.subscriptions import bybit_symbol
from verification.domain.subscriptions import classify_frame
from verification.domain.subscriptions import hyperliquid_coin
from verification.domain.subscriptions import rest_polls
from verification.domain.subscriptions import rest_refusal
from verification.domain.subscriptions import ws_endpoints


_BYBIT = RecordingPlan(
    "BYBIT",
    "mainnet",
    ("BTCUSDT-LINEAR.BYBIT", "ETHUSDT-LINEAR.BYBIT", "BTCUSDT-SPOT.BYBIT", "ETHUSDT-SPOT.BYBIT"),
)
_HL = RecordingPlan("HYPERLIQUID", "mainnet", ("SOL-USD-PERP.HYPERLIQUID",))


def _url(name: str) -> str:
    return f"wss://local/{name}"


def _args(messages: tuple[str, ...]) -> list[list[str]]:
    return [json.loads(message)["args"] for message in messages]


def test_bybit_is_one_socket_per_category_with_tickers_on_linear_only() -> None:
    linear, spot = ws_endpoints(_BYBIT, _url)
    assert (linear.name, linear.url, spot.name, spot.url) == (
        "linear",
        "wss://local/linear",
        "spot",
        "wss://local/spot",
    )
    assert linear.subscriptions == (
        "orderbook.50.BTCUSDT",
        "publicTrade.BTCUSDT",
        "tickers.BTCUSDT",
        "orderbook.50.ETHUSDT",
        "publicTrade.ETHUSDT",
        "tickers.ETHUSDT",
    )
    assert spot.subscriptions == (
        "orderbook.50.BTCUSDT",
        "publicTrade.BTCUSDT",
        "orderbook.50.ETHUSDT",
        "publicTrade.ETHUSDT",
    )
    assert linear.data_channels == ("linear.orderbook.50", "linear.publicTrade", "linear.tickers")
    assert spot.data_channels == ("spot.orderbook.50", "spot.publicTrade")
    assert json.loads(linear.ping_message) == {"op": "ping"}
    assert linear.ping_seconds == 20.0


def test_bybit_subscribe_messages_carry_at_most_ten_args() -> None:
    ids = tuple(f"C{n}USDT-SPOT.BYBIT" for n in range(7))
    (spot,) = ws_endpoints(RecordingPlan("BYBIT", "mainnet", ids), _url)
    chunks = _args(spot.subscribe_messages)
    assert [len(chunk) for chunk in chunks] == [10, 4]
    assert [arg for chunk in chunks for arg in chunk] == list(spot.subscriptions)
    assert all(json.loads(m)["op"] == "subscribe" for m in spot.subscribe_messages)


def test_hyperliquid_is_one_socket_with_three_subscriptions_per_coin() -> None:
    (endpoint,) = ws_endpoints(_HL, _url)
    assert endpoint.url == "wss://local/ws"
    assert [json.loads(m) for m in endpoint.subscribe_messages] == [
        {"method": "subscribe", "subscription": {"type": kind, "coin": "SOL"}}
        for kind in ("l2Book", "trades", "activeAssetCtx")
    ]
    assert json.loads(endpoint.ping_message) == {"method": "ping"}
    assert endpoint.ping_seconds == 30.0
    assert endpoint.data_channels == ("l2Book", "trades", "activeAssetCtx")


def test_an_empty_plan_has_no_endpoint_and_no_poll() -> None:
    empty = RecordingPlan("BYBIT", "mainnet", ())
    assert ws_endpoints(empty, _url) == ()
    assert rest_polls(empty) == ()


@pytest.mark.parametrize(
    "plan",
    [
        RecordingPlan("BYBIT", "mainnet", ("BTCUSD-INVERSE.BYBIT",)),
        RecordingPlan("HYPERLIQUID", "mainnet", ("PURR-USDC-SPOT.HYPERLIQUID",)),
        RecordingPlan("DYDX", "mainnet", ("BTC-USD-PERP.DYDX",)),
    ],
)
def test_an_unrecorded_market_is_refused(plan: RecordingPlan) -> None:
    with pytest.raises(ValueError):
        ws_endpoints(plan, _url)


def test_wire_symbols_come_from_the_id() -> None:
    assert bybit_symbol("BTCUSDT-LINEAR.BYBIT") == "BTCUSDT"
    assert bybit_symbol("BTCUSDT-25SEP26-LINEAR.BYBIT") == "BTCUSDT-25SEP26"
    assert hyperliquid_coin("SOL-USD-PERP.HYPERLIQUID") == "SOL"
    with pytest.raises(MalformedInstrumentId):
        bybit_symbol("BTC-USD-PERP.DYDX")


def test_bybit_rest_polls_and_cadences() -> None:
    polls = {(p.channel, p.request): p.every_seconds for p in rest_polls(_BYBIT)}
    btc = "category=linear&symbol=BTCUSDT"
    spot = "category=spot&symbol=BTCUSDT"
    oi = f"/v5/market/open-interest?{btc}&intervalTime=5min&limit=1"
    assert polls[("linear.rest.instruments-info", f"/v5/market/instruments-info?{btc}")] == 30
    assert polls[("linear.rest.open-interest", oi)] == 30
    assert polls[("linear.rest.tickers", f"/v5/market/tickers?{btc}")] == 30
    assert polls[("linear.rest.recent-trade", f"/v5/market/recent-trade?{btc}&limit=1000")] == 30
    assert polls[("spot.rest.recent-trade", f"/v5/market/recent-trade?{spot}&limit=60")] == 30
    assert polls[("linear.rest.orderbook", f"/v5/market/orderbook?{btc}&limit=50")] == 60
    spot_channels = {p.channel for p in rest_polls(_BYBIT) if p.endpoint == "spot"}
    assert "spot.rest.open-interest" not in spot_channels
    assert "spot.rest.tickers" not in spot_channels
    assert len(polls) == 2 * 5 + 2 * 3
    assert {p.method for p in rest_polls(_BYBIT)} == {"GET"}


def test_hyperliquid_rest_polls_and_cadences() -> None:
    meta, book = rest_polls(_HL)
    assert (meta.method, json.loads(meta.request), meta.every_seconds, meta.channel) == (
        "POST",
        {"type": "metaAndAssetCtxs"},
        30,
        "rest.metaAndAssetCtxs",
    )
    assert (json.loads(book.request), book.every_seconds, book.channel) == (
        {"type": "l2Book", "coin": "SOL"},
        60,
        "rest.l2Book",
    )


@pytest.mark.parametrize(
    ("endpoint", "frame", "expected"),
    [
        (
            "linear",
            '{"topic":"orderbook.50.BTCUSDT","type":"delta"}',
            ("linear.orderbook.50", None),
        ),
        ("spot", '{"topic":"orderbook.50.BTCUSDT","type":"snapshot"}', ("spot.orderbook.50", None)),
        ("linear", '{"topic":"publicTrade.ETHUSDT","data":[]}', ("linear.publicTrade", None)),
        ("linear", '{"topic":"tickers.BTCUSDT","data":{}}', ("linear.tickers", None)),
        ("spot", '{"success":true,"ret_msg":"pong","op":"ping"}', ("spot.control", None)),
        (
            "linear",
            '{"success":false,"ret_msg":"error:handler not found","op":"subscribe"}',
            ("linear.control", ANOMALY_VENUE_ERROR),
        ),
        ("linear", '{"topic":"kline.1.BTCUSDT"}', ("unknown", ANOMALY_UNKNOWN)),
        ("linear", '{"hello":1}', ("unknown", ANOMALY_UNKNOWN)),
        ("linear", "[1,2]", ("unknown", ANOMALY_UNKNOWN)),
        ("linear", "not json {", ("unparsed", ANOMALY_UNPARSED)),
    ],
)
def test_bybit_frames_are_filed_by_endpoint_and_topic_kind(
    endpoint: str, frame: str, expected: tuple[str, str | None]
) -> None:
    assert classify_frame("BYBIT", endpoint, frame) == Classified(*expected)


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        ('{"channel":"l2Book","data":{}}', ("l2Book", None)),
        ('{"channel":"trades","data":[]}', ("trades", None)),
        ('{"channel":"activeAssetCtx","data":{}}', ("activeAssetCtx", None)),
        ('{"channel":"subscriptionResponse","data":{}}', ("control", None)),
        ('{"channel":"pong"}', ("control", None)),
        ('{"channel":"error","data":"Invalid subscription"}', ("control", ANOMALY_VENUE_ERROR)),
        ('{"channel":"bbo","data":{}}', ("unknown", ANOMALY_UNKNOWN)),
        ('{"channel":["l2Book"],"data":{}}', ("unknown", ANOMALY_UNKNOWN)),
        ('{"channel":{"a":1}}', ("unknown", ANOMALY_UNKNOWN)),
        ('{"data":{}}', ("unknown", ANOMALY_UNKNOWN)),
        ("\x00garbage", ("unparsed", ANOMALY_UNPARSED)),
    ],
)
def test_hyperliquid_frames_are_filed_by_their_channel(
    frame: str, expected: tuple[str, str | None]
) -> None:
    assert classify_frame("HYPERLIQUID", "ws", frame) == Classified(*expected)


@pytest.mark.parametrize(
    ("venue", "status", "text", "refusal"),
    [
        ("BYBIT", 200, '{"retCode":0,"result":{}}', None),
        ("BYBIT", 200, '{"retCode":0,"result":{"list":[{"symbol":"BTCUSDT"}]}}', None),
        (
            "BYBIT",
            200,
            '{"retCode":0,"result":{"category":"linear","list":[]}}',
            "result.list is empty",
        ),
        ("BYBIT", 200, '{"retCode":10001,"retMsg":"params error"}', "retCode 10001"),
        ("BYBIT", 200, "[]", "retCode None"),
        ("BYBIT", 403, "forbidden", "HTTP 403"),
        ("HYPERLIQUID", 200, "[{}, []]", None),
        ("HYPERLIQUID", 200, "null", "body is JSON null"),
        ("BYBIT", 200, "null", "body is JSON null"),
        ("HYPERLIQUID", 500, "{}", "HTTP 500"),
        ("HYPERLIQUID", 200, "<html>", "body is not JSON"),
        ("HYPERLIQUID", 200, None, "body is not UTF-8"),
    ],
)
def test_rest_refusals(venue: str, status: int, text: str | None, refusal: str | None) -> None:
    assert rest_refusal(venue, status, text) == refusal
