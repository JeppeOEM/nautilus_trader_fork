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

import pytest
from observability import error_ledger

import capture.venues.hyperliquid.client as hl_client
from capture.application.feed import MAIN_FEED
from capture.application.feed import Feed
from capture.application.wire_channels import WireChannels
from capture.venues.hyperliquid.client import HYPERLIQUID_WS_FRAMES_PER_SECOND
from capture.venues.hyperliquid.client import TRADES_FEED
from capture.venues.hyperliquid.client import HyperliquidClient
from capture.venues.hyperliquid.client import _at_exact_millis
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
    client = HyperliquidClient(
        lambda d, f: received.append((d, f)), trade_feeds=trade_feeds, ledger=error_ledger.record
    )
    # Unpaced for speed; the pacing itself is `capture/tests/test_wire_channels.py`'s.
    client._wire = WireChannels(1e6)
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
    client = HyperliquidClient(
        lambda d, f: received.append((d, f)), trade_feeds=2, ledger=error_ledger.record
    )
    main, trades = _StubWs(), _StubWs()

    async def refuse(*_args: Any) -> None:
        raise RuntimeError("connection refused")

    setattr(trades, "connect", refuse)  # noqa: B010 (stub method)
    client._ws, client._ws_trades = main, trades
    asyncio.run(client.connect(asyncio.new_event_loop(), [object()]))
    assert set(client.feed_states()) == {MAIN_FEED}
    assert main.calls[:2] == [("connect", "1"), ("wait_until_active", "30.0")]
    assert error_ledger.counts() == {"collector.trade_feed": 1}


_CHANNELS = [
    "subscribe_trades",
    "subscribe_book",
    "subscribe_mark_prices",
    "subscribe_index_prices",
    "subscribe_funding_rates",
    "subscribe_open_interest",
]


def _fail_once(stub: _StubWs, name: str) -> None:
    """Make `name` raise on its first call only ("Instrument not found" or a closed channel)."""
    failed: list[bool] = []

    async def call(arg: Any, *_rest: Any) -> None:
        stub.calls.append((name, str(arg)))
        if not failed:
            failed.append(True)
            raise RuntimeError(f"{name}: command channel closed")

    setattr(stub, name, call)


def _wire_calls(stub: _StubWs) -> list[str]:
    return [name for name, _ in stub.calls if name not in ("connect", "wait_until_active")]


def test_the_client_paces_at_the_named_hyperliquid_rate() -> None:
    client = HyperliquidClient(lambda d, f: None, ledger=error_ledger.record)
    assert client._wire._interval == 1 / HYPERLIQUID_WS_FRAMES_PER_SECOND


def test_a_repeated_subscribe_sends_no_duplicate_frame() -> None:
    client, _, main, trades = _client(2)
    asyncio.run(client.subscribe(_IID))
    asyncio.run(client.subscribe(_IID))
    assert _wire_calls(main) == _CHANNELS
    assert _wire_calls(trades) == ["subscribe_trades"]


def test_a_failed_trades_subscribe_runs_no_undo_and_the_retry_resends_it() -> None:
    # `trades` fails before any Rust state: nothing to undo.
    client, _, main, _ = _client(1)
    _fail_once(main, "subscribe_trades")
    with pytest.raises(RuntimeError, match="command channel closed"):
        asyncio.run(client.subscribe(_IID))
    asyncio.run(client.subscribe(_IID))
    assert _wire_calls(main) == ["subscribe_trades", *_CHANNELS]


def test_a_failed_asset_context_subscribe_is_undone_so_the_retry_sends_it() -> None:
    # The Rust client adds the type to `asset_context_subs` before its failing send; without the
    # undo a retry would find the set non-empty and send no `activeAssetCtx` frame.
    client, _, main, _ = _client(1)
    _fail_once(main, "subscribe_mark_prices")
    with pytest.raises(RuntimeError, match="command channel closed"):
        asyncio.run(client.subscribe(_IID))
    assert not client._wire.is_held(("mark", _IID))
    asyncio.run(client.subscribe(_IID))
    assert (
        _wire_calls(main)
        == [
            "subscribe_trades",
            "subscribe_book",
            "subscribe_mark_prices",
            "unsubscribe_mark_prices",  # the undo
            *_CHANNELS[2:],
        ]
    )


def test_an_unsubscribe_after_a_partial_failure_releases_only_the_held_channels_once() -> None:
    client, _, main, _ = _client(1)
    _fail_once(main, "subscribe_book")
    with pytest.raises(RuntimeError, match="command channel closed"):
        asyncio.run(client.subscribe(_IID))
    asyncio.run(client.unsubscribe(_IID))
    asyncio.run(client.unsubscribe(_IID))
    assert _wire_calls(main) == ["subscribe_trades", "subscribe_book", "unsubscribe_trades"]


def test_a_failed_trades_socket_subscribe_is_retried_by_the_next_subscribe() -> None:
    error_ledger.reset()
    client, _, main, trades = _client(2)
    _fail_once(trades, "subscribe_trades")
    asyncio.run(client.subscribe(_IID))
    asyncio.run(client.subscribe(_IID))
    assert _wire_calls(trades) == ["subscribe_trades", "subscribe_trades"]
    assert _wire_calls(main) == _CHANNELS
    assert error_ledger.counts() == {"collector.trade_feed": 1}


def test_a_failed_trades_socket_unsubscribe_raises_and_is_retried() -> None:
    client, _, _, trades = _client(2)
    asyncio.run(client.subscribe(_IID))
    _fail_once(trades, "unsubscribe_trades")
    with pytest.raises(RuntimeError, match="command channel closed"):
        asyncio.run(client.unsubscribe(_IID))
    asyncio.run(client.unsubscribe(_IID))
    assert _wire_calls(trades) == ["subscribe_trades", "unsubscribe_trades", "unsubscribe_trades"]


def test_the_channel_budget_counts_one_asset_context_per_id() -> None:
    client, _, _, _ = _client(2)
    asyncio.run(client.subscribe(_IID))
    # trades + book + twin trades + one shared activeAssetCtx
    assert client.wire_subscriptions() == 4


def test_a_subscribe_past_the_venues_channel_budget_is_refused_before_sending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _, main, _ = _client(1)
    asyncio.run(client.subscribe(_IID))  # holds 3
    monkeypatch.setattr(hl_client, "HYPERLIQUID_MAX_WS_CHANNELS", 5)
    sent = len(main.calls)
    with pytest.raises(RuntimeError, match="accepts 5 channels per IP"):
        asyncio.run(client.subscribe("ETH-USD-PERP.HYPERLIQUID"))  # needs 3 more
    assert len(main.calls) == sent
    monkeypatch.setattr(hl_client, "HYPERLIQUID_MAX_WS_CHANNELS", 6)
    asyncio.run(client.subscribe("ETH-USD-PERP.HYPERLIQUID"))
    assert client.wire_subscriptions() == 6


def test_an_undecoded_message_is_ledgered_with_its_type_never_dropped_quietly() -> None:
    error_ledger.reset()
    received: list[object] = []
    client = HyperliquidClient(lambda d, f: received.append(d), ledger=error_ledger.record)
    client._handle_message(MAIN_FEED, ["not", "a", "known", "type"])
    assert received == []
    assert error_ledger.counts() == {"collector.unknown_message": 1}
    assert "list not decoded" in error_ledger.last_details()["collector.unknown_message"]
