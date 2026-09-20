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
  * subscriptions are one WS request per topic; Hyperliquid documents a per-IP subscription
    cap (1000) far above one connection's needs, so no throttle here.
"""

import asyncio
import logging
from collections.abc import Callable

from hyperliquid_collector.open_interest import HyperliquidOpenInterest
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import capsule_to_data


logger = logging.getLogger(__name__)


class HyperliquidClient:
    """
    Owns one Hyperliquid HTTP client and one WebSocket connection.

    `on_data` must be O(1) (append to a queue): it runs on the event loop via
    call_soon_threadsafe.
    """

    def __init__(self, on_data: Callable[[object], None], environment: str = "mainnet") -> None:
        self._on_data = on_data
        env = nautilus_pyo3.HyperliquidEnvironment.from_str(environment)  # type: ignore[attr-defined]
        self._http = nautilus_pyo3.HyperliquidHttpClient(environment=env)  # type: ignore[attr-defined]
        self._ws = nautilus_pyo3.HyperliquidWebSocketClient(environment=env)  # type: ignore[attr-defined]

    async def fetch_instruments(self) -> list:
        """Raw pyo3-native perp instruments; convert with `instruments_from_pyo3` for the catalog."""
        return await self._http.load_instrument_definitions(include_spot=False, include_perps=True)

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None:
        await self._ws.connect(loop, instruments, self._handle_message)
        await self._ws.wait_until_active(30.0)

    async def disconnect(self) -> None:
        if not self._ws.is_closed():
            await self._ws.close()

    async def subscribe(self, instrument_id: str) -> None:
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        await self._ws.subscribe_trades(iid)
        await self._ws.subscribe_book(iid)
        await self._ws.subscribe_mark_prices(iid)
        await self._ws.subscribe_index_prices(iid)
        await self._ws.subscribe_funding_rates(iid)
        await self._ws.subscribe_open_interest(iid)

    async def unsubscribe(self, instrument_id: str) -> None:
        iid = nautilus_pyo3.InstrumentId.from_str(instrument_id)
        await self._ws.unsubscribe_trades(iid)
        await self._ws.unsubscribe_book(iid)
        await self._ws.unsubscribe_mark_prices(iid)
        await self._ws.unsubscribe_index_prices(iid)
        await self._ws.unsubscribe_funding_rates(iid)
        await self._ws.unsubscribe_open_interest(iid)  # type: ignore[attr-defined]

    def _handle_message(self, message: object) -> None:
        # Trades/book/mark/index arrive as PyCapsules; funding as a pyo3 object; open
        # interest as a pyo3 CustomData wrapper (mirrors nautilus_trader/adapters/hyperliquid/data.py).
        if nautilus_pyo3.is_pycapsule(message):
            self._on_data(capsule_to_data(message))
        elif isinstance(message, nautilus_pyo3.FundingRateUpdate):
            self._on_data(FundingRateUpdate.from_pyo3(message))
        elif isinstance(message, nautilus_pyo3.CustomData) and isinstance(
            message.data,
            nautilus_pyo3.HyperliquidOpenInterest,  # type: ignore[attr-defined]
        ):
            self._on_data(HyperliquidOpenInterest.from_pyo3(message.data))
        else:
            logger.debug(f"Ignoring message of type {type(message).__name__}")
