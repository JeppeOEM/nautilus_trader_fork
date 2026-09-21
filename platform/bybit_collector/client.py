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
Thin wrapper around Bybit's Rust-backed HTTP/WebSocket clients (linear USDT perps + spot).

Same reason as `dydx_collector.client`: bypass TradingNode/DataEngine (unbounded-queue /
shutdown-wedge bug) and drive the Rust connect/reconnect/decode logic with our own asyncio
loop and callback.

Verified against crates/adapters/bybit (Story 19.3 Task 1) -- what is NOT ported from dYdX:
  * no `_at_fixed_precision`: mark/index prices are parsed at `instrument.price_precision()`
    (websocket/parse.rs), a per-instrument constant, so precision labels never drift.
  * open interest is dropped on the linear ticker path (only the option-greeks parse reads
    it) -- so `open_interest.py` polls REST, same as dYdX.
  * subscriptions are one WS request per topic; Bybit documents no per-second subscribe
    limit for public topics, so no 2/sec-style cap and no `_MAX_WS_SUBSCRIPTIONS`.

Feeds (story 22.14): every message is tagged with its socket -- `linear` / `spot`, and with
`trade_feeds = 2` the trades-only twins `linear-trades` / `spot-trades` (same feed group), whose
copies the core unions through its trade_id dedup. `feed_states()` exposes each socket's
`is_active()`, which goes false while the Rust client reconnects: the core's reconnect signal.
"""

import asyncio
import functools
import logging
from collections.abc import Callable
from typing import Any

from collector_core.book_check import BookSnapshot
from collector_core.feed import Feed
from collector_core.feed import optional_feed_step

from bybit_collector.book_snapshot import fetch_orderbook
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.core.nautilus_pyo3 import BybitProductType
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import capsule_to_data


logger = logging.getLogger(__name__)

# Bybit linear order books stream depth 1/50/200/1000; 50 comfortably covers BOOK_DEPTH=20.
ORDERBOOK_DEPTH = 50

LINEAR_FEED = Feed("linear", "linear")
SPOT_FEED = Feed("spot", "spot")
LINEAR_TRADES_FEED = Feed("linear-trades", "linear", trades_only=True)
SPOT_TRADES_FEED = Feed("spot-trades", "spot", trades_only=True)


def _trade_feed(product_type: BybitProductType) -> Feed:
    return LINEAR_TRADES_FEED if product_type == BybitProductType.LINEAR else SPOT_TRADES_FEED


class BybitClient:
    """
    Owns one Bybit HTTP client and one public WebSocket per product type (LINEAR, SPOT), plus
    a trades-only twin per product type when `trade_feeds == 2`.

    Bybit binds a public stream to one product type, so `subscribe` routes by the Nautilus
    symbol suffix. Spot subscribes trades + orderbook only, deliberately: Bybit's spot ticker
    carries no bid/ask, funding or open interest (docs: websocket/public/ticker) and mark/index
    price are perp concepts, so spot has no mark/index/funding/OI rows by design, not omission.

    `on_data` must be O(1) (append to a queue): it runs on the event loop via
    call_soon_threadsafe.
    """

    def __init__(
        self,
        on_data: Callable[[object, Feed], None],
        environment: BybitEnvironment = BybitEnvironment.MAINNET,
        trade_feeds: int = 1,
    ) -> None:
        self._on_data = on_data
        self._rest_environment = "testnet" if environment == BybitEnvironment.TESTNET else "mainnet"
        self._http = nautilus_pyo3.BybitHttpClient(  # type: ignore[attr-defined]
            testnet=environment == BybitEnvironment.TESTNET,
            demo=environment == BybitEnvironment.DEMO,
        )
        self._ws_linear = self._new_ws(BybitProductType.LINEAR, environment)
        self._ws_spot = self._new_ws(BybitProductType.SPOT, environment)
        # Independent trades-only connections (trade_feeds = 2): same public stream, own socket.
        self._ws_linear_trades: object | None = None
        self._ws_spot_trades: object | None = None
        if trade_feeds == 2:
            self._ws_linear_trades = self._new_ws(BybitProductType.LINEAR, environment)
            self._ws_spot_trades = self._new_ws(BybitProductType.SPOT, environment)

    @staticmethod
    def _new_ws(product_type: BybitProductType, environment: BybitEnvironment) -> object:
        return nautilus_pyo3.BybitWebSocketClient.new_public(  # type: ignore[attr-defined]
            product_type=product_type,
            environment=environment,
            heartbeat=20,
        )

    def _sockets(self) -> dict[Feed, Any]:
        sockets: dict[Feed, Any] = {LINEAR_FEED: self._ws_linear, SPOT_FEED: self._ws_spot}
        if self._ws_linear_trades is not None:
            sockets[LINEAR_TRADES_FEED] = self._ws_linear_trades
        if self._ws_spot_trades is not None:
            sockets[SPOT_TRADES_FEED] = self._ws_spot_trades
        return sockets

    def _trade_ws_for(self, product_type: BybitProductType) -> Any:
        """Return the trades-only socket of this product type, or None with `trade_feeds = 1`."""
        if product_type == BybitProductType.LINEAR:
            return self._ws_linear_trades
        return self._ws_spot_trades

    def feed_states(self) -> dict[Feed, bool]:
        return {feed: ws.is_active() for feed, ws in self._sockets().items()}

    def _product_type(self, instrument_id: str) -> BybitProductType:
        return nautilus_pyo3.bybit_product_type_from_symbol(  # type: ignore[attr-defined]
            instrument_id.split(".")[0],
        )

    def _ws_for(self, instrument_id: str) -> tuple[Any, BybitProductType]:
        product_type = self._product_type(instrument_id)
        if product_type == BybitProductType.LINEAR:
            return self._ws_linear, product_type
        if product_type == BybitProductType.SPOT:
            return self._ws_spot, product_type
        raise ValueError(f"unsupported Bybit product type for {instrument_id}: {product_type}")

    async def fetch_instruments(self) -> list:
        """Raw pyo3-native LINEAR + SPOT instruments; convert with `instruments_from_pyo3`."""
        linear = await self._http.request_instruments(BybitProductType.LINEAR)
        spot = await self._http.request_instruments(BybitProductType.SPOT)
        return [*linear, *spot]

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None:
        # Each WS resolves symbols through its own instrument cache.
        for instrument in instruments:
            ws, product_type = self._ws_for(str(instrument.id))
            ws.cache_instrument(instrument)
            trade_ws = self._trade_ws_for(product_type)
            if trade_ws is not None:
                trade_ws.cache_instrument(instrument)
        for feed, ws in self._sockets().items():
            connecting = ws.connect(
                loop_=loop, callback=functools.partial(self._handle_message, feed)
            )
            if feed.trades_only:
                if not await optional_feed_step(feed, "connect", connecting):
                    self._drop_trade_ws(feed)
            else:
                await connecting

    def _drop_trade_ws(self, feed: Feed) -> None:
        if feed == LINEAR_TRADES_FEED:
            self._ws_linear_trades = None
        else:
            self._ws_spot_trades = None

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
        ws, product_type = self._ws_for(instrument_id)
        await ws.subscribe_trades(iid)
        await ws.subscribe_orderbook(iid, ORDERBOOK_DEPTH)
        if product_type == BybitProductType.LINEAR:
            await ws.subscribe_ticker(iid)  # mark/index price + funding rate (perp only)
        trade_ws = self._trade_ws_for(product_type)
        if trade_ws is not None:
            await optional_feed_step(
                _trade_feed(product_type), "subscribe", trade_ws.subscribe_trades(iid)
            )

    async def unsubscribe(self, instrument_id: str) -> None:
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        ws, product_type = self._ws_for(instrument_id)
        await ws.unsubscribe_trades(iid)
        await ws.unsubscribe_orderbook(iid, ORDERBOOK_DEPTH)
        if product_type == BybitProductType.LINEAR:
            await ws.unsubscribe_ticker(iid)
        trade_ws = self._trade_ws_for(product_type)
        if trade_ws is not None:
            await optional_feed_step(
                _trade_feed(product_type), "unsubscribe", trade_ws.unsubscribe_trades(iid)
            )

    async def resync_orderbook(self, instrument_id: str) -> None:
        """Unsubscribe + resubscribe the book: Bybit answers with a fresh snapshot (Clear + levels)."""
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        ws, _ = self._ws_for(instrument_id)
        await ws.unsubscribe_orderbook(iid, ORDERBOOK_DEPTH)
        await ws.subscribe_orderbook(iid, ORDERBOOK_DEPTH)

    async def fetch_book_snapshot(self, instrument_id: str) -> BookSnapshot:
        """
        REST book with its `seq` for the aligned cross-check (story 22.5, audit D-64).

        Public market data is served by the mainnet host for the demo environment too.
        """
        return await fetch_orderbook(self._rest_environment, instrument_id, ORDERBOOK_DEPTH)

    def _handle_message(self, feed: Feed, message: object) -> None:
        # Orderbook/trade/quote arrive as PyCapsules; ticker-derived mark/index/funding
        # arrive as plain pyo3 objects (mirrors nautilus_trader/adapters/bybit/data.py).
        if nautilus_pyo3.is_pycapsule(message):
            self._on_data(capsule_to_data(message), feed)
        elif isinstance(message, nautilus_pyo3.MarkPriceUpdate):
            self._on_data(MarkPriceUpdate.from_pyo3(message), feed)
        elif isinstance(message, nautilus_pyo3.IndexPriceUpdate):
            self._on_data(IndexPriceUpdate.from_pyo3(message), feed)
        elif isinstance(message, nautilus_pyo3.FundingRateUpdate):
            self._on_data(FundingRateUpdate.from_pyo3(message), feed)
        else:
            logger.debug(f"Ignoring message of type {type(message).__name__}")
