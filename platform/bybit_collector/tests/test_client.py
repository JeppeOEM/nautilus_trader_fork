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
BybitClient feed tagging and subscription routing (story 22.14). The pyo3 sockets are replaced by
a stub recording what the client asks of them -- our own client contract, not a Nautilus
internal; the messages delivered through the captured callbacks are real pyo3/Nautilus objects.
"""

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

from collector_core.feed import Feed
from observability import error_ledger

from bybit_collector.client import LINEAR_FEED
from bybit_collector.client import LINEAR_TRADES_FEED
from bybit_collector.client import SPOT_FEED
from bybit_collector.client import SPOT_TRADES_FEED
from bybit_collector.client import BybitClient
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick


_LINEAR = "BTCUSDT-LINEAR.BYBIT"
_SPOT = "BTCUSDT-SPOT.BYBIT"


class _StubWs:
    """Records calls; keeps the callback `connect` was given."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.callback: Callable[[object], None] | None = None
        self.active = True

    def cache_instrument(self, instrument: Any) -> None:
        self.calls.append(("cache_instrument", str(instrument.id)))

    async def connect(self, loop_: Any, callback: Callable[[object], None]) -> None:
        self.callback = callback

    def is_active(self) -> bool:
        return self.active

    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        self.calls.append(("close", ""))

    def __getattr__(self, name: str) -> Callable[..., Awaitable[None]]:
        async def record(iid: Any, *_rest: Any) -> None:
            self.calls.append((name, str(iid)))

        return record


def _client(trade_feeds: int) -> tuple[BybitClient, list[tuple[object, Feed]], dict[Feed, _StubWs]]:
    received: list[tuple[object, Feed]] = []
    client = BybitClient(lambda data, feed: received.append((data, feed)), trade_feeds=trade_feeds)
    stubs = {feed: _StubWs() for feed in client.feed_states()}
    client._ws_linear, client._ws_spot = stubs[LINEAR_FEED], stubs[SPOT_FEED]
    if trade_feeds == 2:
        client._ws_linear_trades = stubs[LINEAR_TRADES_FEED]
        client._ws_spot_trades = stubs[SPOT_TRADES_FEED]
    instruments = [SimpleNamespace(id=_LINEAR), SimpleNamespace(id=_SPOT)]
    loop = asyncio.new_event_loop()  # only handed to the stub sockets
    try:
        asyncio.run(client.connect(loop, instruments))
    finally:
        loop.close()
    return client, received, stubs


def _trade_capsule() -> object:
    trade = nautilus_pyo3.TradeTick(
        nautilus_pyo3.InstrumentId.from_str(_LINEAR),
        nautilus_pyo3.Price.from_str("84700.00"),
        nautilus_pyo3.Quantity.from_str("0.032"),
        nautilus_pyo3.AggressorSide.BUYER,
        nautilus_pyo3.TradeId("f1bc074e-9b1e-55fa-8c29-dcf32839e32f"),
        1,
        2,
    )
    return trade.as_pycapsule()


def test_one_trade_feed_has_only_the_two_book_sockets() -> None:
    client, _, _ = _client(1)
    assert set(client.feed_states()) == {LINEAR_FEED, SPOT_FEED}
    assert client._ws_linear_trades is None


def test_every_message_is_tagged_with_its_socket() -> None:
    _, received, stubs = _client(2)
    for feed in (LINEAR_FEED, LINEAR_TRADES_FEED):
        callback = stubs[feed].callback
        assert callback is not None
        callback(_trade_capsule())
    mark = nautilus_pyo3.MarkPriceUpdate(
        nautilus_pyo3.InstrumentId.from_str(_LINEAR), nautilus_pyo3.Price.from_str("1.5"), 1, 2
    )
    callback = stubs[LINEAR_FEED].callback
    assert callback is not None
    callback(mark)
    assert [(type(d), f) for d, f in received] == [
        (TradeTick, LINEAR_FEED),
        (TradeTick, LINEAR_TRADES_FEED),
        (MarkPriceUpdate, LINEAR_FEED),
    ]


def test_trades_only_sockets_cache_their_product_and_subscribe_trades_only() -> None:
    client, _, stubs = _client(2)
    asyncio.run(client.subscribe(_LINEAR))
    asyncio.run(client.unsubscribe(_LINEAR))
    assert stubs[LINEAR_TRADES_FEED].calls == [
        ("cache_instrument", _LINEAR),
        ("subscribe_trades", _LINEAR),
        ("unsubscribe_trades", _LINEAR),
    ]
    assert [name for name, _ in stubs[LINEAR_FEED].calls] == [
        "cache_instrument",
        "subscribe_trades",
        "subscribe_orderbook",
        "subscribe_ticker",
        "unsubscribe_trades",
        "unsubscribe_orderbook",
        "unsubscribe_ticker",
    ]
    assert stubs[SPOT_TRADES_FEED].calls == [("cache_instrument", _SPOT)]


def test_feed_states_report_each_sockets_activity_and_disconnect_closes_all() -> None:
    client, _, stubs = _client(2)
    stubs[SPOT_TRADES_FEED].active = False
    assert client.feed_states() == {
        LINEAR_FEED: True,
        SPOT_FEED: True,
        LINEAR_TRADES_FEED: True,
        SPOT_TRADES_FEED: False,
    }
    asyncio.run(client.disconnect())
    assert all(("close", "") in stub.calls for stub in stubs.values())


def test_a_failing_trades_only_socket_never_takes_the_primary_down() -> None:
    error_ledger.reset()
    client, _, stubs = _client(2)

    async def refuse(iid: Any, *_rest: Any) -> None:
        raise RuntimeError("subscribe rejected")

    setattr(stubs[LINEAR_TRADES_FEED], "subscribe_trades", refuse)  # noqa: B010 (stub method)
    asyncio.run(client.subscribe(_LINEAR))
    assert ("subscribe_orderbook", _LINEAR) in stubs[LINEAR_FEED].calls
    assert error_ledger.counts() == {"collector.trade_feed": 1}
