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
HyperliquidClient feed tagging and subscription routing (story 22.14), with the pyo3 sockets
replaced by a recording stub (our own client contract); delivered messages are real objects.
"""

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any

from collector_core.feed import MAIN_FEED
from collector_core.feed import Feed
from ml_signals import error_ledger

from hyperliquid_collector.client import TRADES_FEED
from hyperliquid_collector.client import HyperliquidClient
from hyperliquid_collector.client import _at_exact_millis
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import TradeTick


_IID = "BTC-USD-PERP.HYPERLIQUID"


class _StubWs:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.callback: Callable[[object], None] | None = None
        self.active = True

    async def connect(
        self, loop: Any, instruments: list, callback: Callable[[object], None]
    ) -> None:
        self.calls.append(("connect", str(len(instruments))))
        self.callback = callback

    def is_active(self) -> bool:
        return self.active

    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        self.calls.append(("close", ""))

    def __getattr__(self, name: str) -> Callable[..., Awaitable[None]]:
        async def record(arg: Any, *_rest: Any) -> None:
            self.calls.append((name, str(arg)))

        return record


def _client(
    trade_feeds: int,
) -> tuple[HyperliquidClient, list[tuple[object, Feed]], _StubWs, _StubWs]:
    received: list[tuple[object, Feed]] = []
    client = HyperliquidClient(lambda d, f: received.append((d, f)), trade_feeds=trade_feeds)
    main, trades = _StubWs(), _StubWs()
    client._ws = main
    if trade_feeds == 2:
        client._ws_trades = trades
    loop = asyncio.new_event_loop()  # only handed to the stub sockets
    try:
        asyncio.run(client.connect(loop, [object()]))
    finally:
        loop.close()
    return client, received, main, trades


def test_one_trade_feed_is_the_single_main_socket() -> None:
    client, _, _, _ = _client(1)
    assert set(client.feed_states()) == {MAIN_FEED}


def test_both_sockets_connect_and_tag_their_messages() -> None:
    client, received, main, trades = _client(2)
    assert main.calls[:2] == trades.calls[:2] == [("connect", "1"), ("wait_until_active", "30.0")]
    trade = nautilus_pyo3.TradeTick(
        nautilus_pyo3.InstrumentId.from_str(_IID),
        nautilus_pyo3.Price.from_str("84765.0"),
        nautilus_pyo3.Quantity.from_str("0.01904"),
        nautilus_pyo3.AggressorSide.SELLER,
        nautilus_pyo3.TradeId("673362615859985"),
        1,
        2,
    )
    for socket in (main, trades):
        assert socket.callback is not None
        socket.callback(trade.as_pycapsule())
    assert [(type(d), f) for d, f in received] == [(TradeTick, MAIN_FEED), (TradeTick, TRADES_FEED)]
    trades.active = False
    assert client.feed_states() == {MAIN_FEED: True, TRADES_FEED: False}


def test_the_trades_socket_subscribes_trades_only() -> None:
    client, _, main, trades = _client(2)
    asyncio.run(client.subscribe(_IID))
    asyncio.run(client.unsubscribe(_IID))
    asyncio.run(client.disconnect())
    assert trades.calls[2:] == [
        ("subscribe_trades", _IID),
        ("unsubscribe_trades", _IID),
        ("close", ""),
    ]
    assert len([c for c in main.calls if c[0].startswith("subscribe_")]) == 6


def _hl_trade(ts_event: int) -> TradeTick:
    return TradeTick.from_pyo3(
        nautilus_pyo3.TradeTick(
            nautilus_pyo3.InstrumentId.from_str(_IID),
            nautilus_pyo3.Price.from_str("116.87"),
            nautilus_pyo3.Quantity.from_str("8.05"),
            nautilus_pyo3.AggressorSide.SELLER,
            nautilus_pyo3.TradeId("809426847166051"),
            ts_event,
            ts_event + 5,
        )
    )


def test_live_trade_time_is_restamped_to_the_venues_exact_millisecond() -> None:
    # Wire pairs from 2026-09-21 (WS value via the adapter's f64 path, then the REST ms * 10**6).
    for live, exact in [
        (1789993051206000128, 1789993051206000000),
        (1789993060924999936, 1789993060925000000),
    ]:
        fixed = _at_exact_millis(_hl_trade(live))
        assert (fixed.ts_event, fixed.ts_init) == (exact, live + 5)
        assert (fixed.trade_id, fixed.price, fixed.size) == (
            _hl_trade(live).trade_id,
            _hl_trade(live).price,
            _hl_trade(live).size,
        )


def test_an_already_exact_trade_time_is_kept() -> None:
    trade = _hl_trade(1789993061000000000)
    assert _at_exact_millis(trade) is trade


def test_a_trades_socket_that_cannot_connect_is_dropped_and_ledgered() -> None:
    error_ledger.reset()
    received: list[tuple[object, Feed]] = []
    client = HyperliquidClient(lambda d, f: received.append((d, f)), trade_feeds=2)
    main, trades = _StubWs(), _StubWs()

    async def refuse(*_args: Any) -> None:
        raise RuntimeError("connection refused")

    setattr(trades, "connect", refuse)  # noqa: B010 (stub method)
    client._ws, client._ws_trades = main, trades
    asyncio.run(client.connect(asyncio.new_event_loop(), [object()]))
    assert set(client.feed_states()) == {MAIN_FEED}
    assert main.calls[:2] == [("connect", "1"), ("wait_until_active", "30.0")]
    assert error_ledger.counts() == {"collector.trade_feed": 1}
