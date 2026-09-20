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
Thin wrapper around Bybit's Rust-backed HTTP/WebSocket clients (linear USDT perps).

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
"""

import asyncio
import logging
from collections.abc import Callable

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


class BybitClient:
    """
    Owns one Bybit HTTP client and one public linear WebSocket connection.

    `on_data` must be O(1) (append to a queue): it runs on the event loop via
    call_soon_threadsafe.
    """

    def __init__(
        self,
        on_data: Callable[[object], None],
        environment: BybitEnvironment = BybitEnvironment.MAINNET,
    ) -> None:
        self._on_data = on_data
        self._http = nautilus_pyo3.BybitHttpClient(  # type: ignore[attr-defined]
            testnet=environment == BybitEnvironment.TESTNET,
            demo=environment == BybitEnvironment.DEMO,
        )
        self._ws = nautilus_pyo3.BybitWebSocketClient.new_public(  # type: ignore[attr-defined]
            product_type=BybitProductType.LINEAR,
            environment=environment,
            heartbeat=20,
        )

    async def fetch_instruments(self) -> list:
        """Raw pyo3-native linear instruments; convert with `instruments_from_pyo3` for the catalog."""
        return await self._http.request_instruments(BybitProductType.LINEAR)

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None:
        # The WS client resolves symbols through its own instrument cache.
        for instrument in instruments:
            self._ws.cache_instrument(instrument)
        await self._ws.connect(loop_=loop, callback=self._handle_message)

    async def disconnect(self) -> None:
        if not self._ws.is_closed():
            await self._ws.close()

    async def subscribe(self, instrument_id: str) -> None:
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        await self._ws.subscribe_trades(iid)
        await self._ws.subscribe_orderbook(iid, ORDERBOOK_DEPTH)
        await self._ws.subscribe_ticker(iid)  # mark/index price + funding rate

    async def unsubscribe(self, instrument_id: str) -> None:
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        await self._ws.unsubscribe_trades(iid)
        await self._ws.unsubscribe_orderbook(iid, ORDERBOOK_DEPTH)
        await self._ws.unsubscribe_ticker(iid)

    async def resync_orderbook(self, instrument_id: str) -> None:
        """Unsubscribe + resubscribe the book: Bybit answers with a fresh snapshot (Clear + levels)."""
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        await self._ws.unsubscribe_orderbook(iid, ORDERBOOK_DEPTH)
        await self._ws.subscribe_orderbook(iid, ORDERBOOK_DEPTH)

    def _handle_message(self, message: object) -> None:
        # Orderbook/trade/quote arrive as PyCapsules; ticker-derived mark/index/funding
        # arrive as plain pyo3 objects (mirrors nautilus_trader/adapters/bybit/data.py).
        if nautilus_pyo3.is_pycapsule(message):
            self._on_data(capsule_to_data(message))
        elif isinstance(message, nautilus_pyo3.MarkPriceUpdate):
            self._on_data(MarkPriceUpdate.from_pyo3(message))
        elif isinstance(message, nautilus_pyo3.IndexPriceUpdate):
            self._on_data(IndexPriceUpdate.from_pyo3(message))
        elif isinstance(message, nautilus_pyo3.FundingRateUpdate):
            self._on_data(FundingRateUpdate.from_pyo3(message))
        else:
            logger.debug(f"Ignoring message of type {type(message).__name__}")
