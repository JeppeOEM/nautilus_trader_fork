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
Thin wrapper around Hyperliquid's Rust-backed HTTP/WebSocket clients (perps).

Same reason as `dydx_collector.client`: bypass TradingNode/DataEngine and drive the Rust
connect/reconnect/decode logic with our own asyncio loop and callback.

Verified against crates/adapters/hyperliquid (Story 19.4 Task 1) -- independently of dYdX/Bybit:
  * precision: mark/index/book prices are all parsed at `instrument.price_precision()`
    (websocket/parse.rs), a per-instrument constant -> no `_at_fixed_precision`.
  * open interest IS forwarded (`subscribe_open_interest` -> pyo3 CustomData), so no REST
    poll workaround, unlike dYdX/Bybit.
  * every `l2Book` payload is a full snapshot (parse_ws_order_book_deltas emits Clear + all
    levels), so the local book can't drift from missed deltas -> no resync machinery.
  * NO `OrderBookDelta.sequence`/`order_id` gap check (opt-out of story 22.5's Bybit `u`
    canary): websocket/parse.rs `parse_ws_order_book_deltas` (lines 103-163) stamps
    `sequence=0` and `order_id=0` on every delta and emits Clear + all levels per message,
    so there is no venue sequence to check and nothing to desync. Book correctness is
    covered instead by the time-aligned REST cross-check (`fetch_book_snapshot`, D-64).
  * subscriptions are one WS request per topic; Hyperliquid documents a per-IP subscription
    cap (1000) far above one connection's needs, so no throttle here.

Feeds (story 22.14): messages are tagged `MAIN_FEED`, and with `trade_feeds = 2` a second,
trades-only `HyperliquidWebSocketClient` delivers as `main-trades` (same group). Hyperliquid's
REST `recentTrades` returns only the last 10 trades (verified 2026-09-21), so the second
connection is the only way to close a reconnect gap on this venue (audit D-48). `feed_states()`
exposes each socket's `is_active()`, the core's reconnect signal.

Trade `ts_event` is re-stamped to the venue's exact millisecond (`_at_exact_millis`): the adapter
converts Hyperliquid's integer-ms `time` to ns through `f64` (crates/adapters/hyperliquid/src/
common/parse.rs `millis_to_nanos`), and at ~1.79e18 ns the f64 spacing is 256 ns, so live values
land up to 128 ns off the venue's (wire, 2026-09-21: 12 of 19 trades off, e.g. `...206000128` for
`...206000000`; audit D-62). A REST-backfilled copy of the same trade is exact, so without this the
archive would hold two clocks for one venue.
"""

import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any

from collector_core.book_check import BookSnapshot
from collector_core.feed import MAIN_FEED
from collector_core.feed import Feed
from collector_core.feed import optional_feed_step
from kernel.open_interest import OpenInterest

from hyperliquid_collector.book_snapshot import fetch_l2_book
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.data import capsule_to_data


logger = logging.getLogger(__name__)

TRADES_FEED = Feed("main-trades", "main", trades_only=True)

_NS_PER_MS = 1_000_000


def _at_exact_millis(trade: TradeTick) -> TradeTick:
    """
    Return `trade` with `ts_event` rounded to the nearest millisecond, in integer arithmetic.

    Exact recovery, not invention: the venue sends integer milliseconds and the adapter's f64
    error is at most 128 ns, far inside half a millisecond. A second boundary (`s * 10**9`,
    divisible by 512) is already exact, so the correction never moves a trade across a second.
    """
    ts_event = (trade.ts_event + _NS_PER_MS // 2) // _NS_PER_MS * _NS_PER_MS
    if ts_event == trade.ts_event:
        return trade
    return TradeTick(
        trade.instrument_id,
        trade.price,
        trade.size,
        trade.aggressor_side,
        trade.trade_id,
        ts_event,
        trade.ts_init,
    )


class HyperliquidClient:
    """
    Owns one Hyperliquid HTTP client and one WebSocket connection, plus an independent
    trades-only connection when `trade_feeds == 2`.

    `on_data` must be O(1) (append to a queue): it runs on the event loop via
    call_soon_threadsafe.
    """

    def __init__(
        self,
        on_data: Callable[[object, Feed], None],
        environment: str = "mainnet",
        trade_feeds: int = 1,
    ) -> None:
        self._on_data = on_data
        env = nautilus_pyo3.HyperliquidEnvironment.from_str(environment)  # type: ignore[attr-defined]
        self._http = nautilus_pyo3.HyperliquidHttpClient(environment=env)  # type: ignore[attr-defined]
        self._ws: Any = nautilus_pyo3.HyperliquidWebSocketClient(environment=env)  # type: ignore[attr-defined]
        self._ws_trades: Any = (
            nautilus_pyo3.HyperliquidWebSocketClient(environment=env)  # type: ignore[attr-defined]
            if trade_feeds == 2
            else None
        )
        self._environment = environment
        self._coins: dict[str, str] = {}  # instrument id -> wire coin (the instrument's raw_symbol)

    def _sockets(self) -> dict[Feed, Any]:
        sockets = {MAIN_FEED: self._ws}
        if self._ws_trades is not None:
            sockets[TRADES_FEED] = self._ws_trades
        return sockets

    def feed_states(self) -> dict[Feed, bool]:
        return {feed: ws.is_active() for feed, ws in self._sockets().items()}

    async def fetch_instruments(self) -> list:
        """Raw pyo3-native perp instruments; convert with `instruments_from_pyo3` for the catalog."""
        instruments = await self._http.load_instrument_definitions(
            include_spot=False, include_perps=True
        )
        self._coins = {str(i.id): i.raw_symbol.value for i in instruments}
        return instruments

    async def fetch_book_snapshot(self, instrument_id: str) -> BookSnapshot:
        """REST l2Book with its `time` for the aligned cross-check (story 22.5, audit D-64)."""
        return await fetch_l2_book(self._environment, self._coins[instrument_id])

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None:
        await self._connect_socket(self._ws, MAIN_FEED, loop, instruments)
        if self._ws_trades is not None and not await optional_feed_step(
            TRADES_FEED,
            "connect",
            self._connect_socket(self._ws_trades, TRADES_FEED, loop, instruments),
        ):
            self._ws_trades = None

    async def _connect_socket(
        self, ws: Any, feed: Feed, loop: asyncio.AbstractEventLoop, instruments: list
    ) -> None:
        await ws.connect(loop, instruments, functools.partial(self._handle_message, feed))
        await ws.wait_until_active(30.0)

    async def disconnect(self) -> None:
        """Close every socket, even when one close fails (the first failure is re-raised)."""
        failure: Exception | None = None
        for ws in self._sockets().values():
            try:
                if not ws.is_closed():
                    await ws.close()
            except Exception as e:
                failure = failure or e
        if failure is not None:
            raise failure

    async def subscribe(self, instrument_id: str) -> None:
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        await self._ws.subscribe_trades(iid)
        await self._ws.subscribe_book(iid)
        await self._ws.subscribe_mark_prices(iid)
        await self._ws.subscribe_index_prices(iid)
        await self._ws.subscribe_funding_rates(iid)
        await self._ws.subscribe_open_interest(iid)
        if self._ws_trades is not None:
            await optional_feed_step(
                TRADES_FEED, "subscribe", self._ws_trades.subscribe_trades(iid)
            )

    async def unsubscribe(self, instrument_id: str) -> None:
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        await self._ws.unsubscribe_trades(iid)
        await self._ws.unsubscribe_book(iid)
        await self._ws.unsubscribe_mark_prices(iid)
        await self._ws.unsubscribe_index_prices(iid)
        await self._ws.unsubscribe_funding_rates(iid)
        await self._ws.unsubscribe_open_interest(iid)
        if self._ws_trades is not None:
            await optional_feed_step(
                TRADES_FEED, "unsubscribe", self._ws_trades.unsubscribe_trades(iid)
            )

    def _handle_message(self, feed: Feed, message: object) -> None:
        # Trades/book/mark/index arrive as PyCapsules; funding as a pyo3 object; open
        # interest as a pyo3 CustomData wrapper (mirrors nautilus_trader/adapters/hyperliquid/data.py).
        if nautilus_pyo3.is_pycapsule(message):
            data = capsule_to_data(message)
            if isinstance(data, TradeTick):
                data = _at_exact_millis(data)
            self._on_data(data, feed)
        elif isinstance(message, nautilus_pyo3.FundingRateUpdate):
            self._on_data(FundingRateUpdate.from_pyo3(message), feed)
        elif isinstance(message, nautilus_pyo3.CustomData) and isinstance(
            message.data,
            nautilus_pyo3.HyperliquidOpenInterest,  # type: ignore[attr-defined]
        ):
            self._on_data(OpenInterest.from_pyo3(message.data), feed)
        else:
            logger.debug(f"Ignoring message of type {type(message).__name__}")
