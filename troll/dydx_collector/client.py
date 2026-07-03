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
Thin wrapper around dYdX's Rust-backed HTTP/WebSocket clients.

Deliberately bypasses TradingNode/Strategy/DataEngine: that live-runtime path
has a documented unbounded-queue-growth + shutdown-wedge bug under high message
load (see memory project_dydx_collector_python_pivot). This client drives the
same Rust connection/reconnect/throttle/decode logic directly with our own
asyncio loop and callback, so the collector never touches the buggy layer.

"""

import asyncio
import logging
from collections.abc import Callable

from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.core.nautilus_pyo3 import FIXED_PRECISION
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import InstrumentStatus
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import capsule_to_data
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price


logger = logging.getLogger(__name__)


def _at_fixed_precision(price: Price) -> Price:
    """
    Re-stamp a Price at nautilus's max fixed-point precision, exactly.

    dYdX's oracle (mark/index) price feed derives each tick's `Price.precision`
    from however many decimal digits that specific value has left after
    stripping trailing zeros (crates/adapters/dydx/src/common/parse.rs's
    parse_price), so consecutive ticks for the same instrument can carry
    different precision labels. ParquetDataCatalog stamps that label into
    each file's Arrow metadata and correctly refuses to read/merge files
    whose labels disagree -- which is the actual error this works around.

    Uses Decimal.scaleb() (exact power-of-ten shift) + Price.from_raw()
    (stores the integer as-is) rather than `Price(decimal, precision)`:
    the latter has a real bug in this nautilus_trader version for some
    decimal/precision combinations -- e.g. `Price(Decimal("61090.59855"), 16)`
    silently returns `61090.5985500000026624` via what looks like an internal
    float64 round-trip. scaleb()+from_raw() never touches a float, so no
    digit is ever invented or dropped.
    """
    raw = int(price.as_decimal().scaleb(FIXED_PRECISION))
    return Price.from_raw(raw, FIXED_PRECISION)


class DydxClient:
    """
    Owns one dYdX HTTP connection and one WebSocket connection.

    Decoded data (TradeTick, OrderBookDeltas, Bar, MarkPriceUpdate, IndexPriceUpdate)
    is pushed to `on_data` as it arrives. `on_data` must be O(1) (e.g. append to a
    deque) -- it runs directly on the event loop via call_soon_threadsafe, so any
    slow work here would back up the same way the rejected DataEngine path did.
    """

    def __init__(
        self,
        on_data: Callable[[object], None],
        network: DydxNetwork = DydxNetwork.MAINNET,
    ) -> None:
        self._on_data = on_data
        self._http = nautilus_pyo3.DydxHttpClient(network=network)  # type: ignore[attr-defined]
        self._ws = nautilus_pyo3.DydxWebSocketClient.new_public(  # type: ignore[attr-defined]
            url=nautilus_pyo3.get_dydx_ws_url(network),  # type: ignore[attr-defined]
            heartbeat=20,
        )

    async def fetch_instruments(self) -> list[Instrument]:
        """
        Fetch all dYdX instruments via REST.

        Returns the raw pyo3-native instrument objects (not the Cython `Instrument`
        model) -- this is the representation `connect()` and the WS client's
        instrument cache expect. Convert via
        `nautilus_trader.model.instruments.instruments_from_pyo3()` before writing
        to a `ParquetDataCatalog`.
        """
        return await self._http.request_instruments(None, None)

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list[Instrument]) -> None:
        await self._ws.connect(loop_=loop, instruments=instruments, callback=self._handle_message)
        await self._ws.wait_until_active(timeout_secs=30.0)

    async def disconnect(self) -> None:
        if not self._ws.is_closed():
            await self._ws.disconnect()

    async def subscribe_trades(self, instrument_id: str) -> None:
        await self._ws.subscribe_trades(nautilus_pyo3.InstrumentId.from_str(instrument_id))

    async def unsubscribe_trades(self, instrument_id: str) -> None:
        await self._ws.unsubscribe_trades(nautilus_pyo3.InstrumentId.from_str(instrument_id))

    async def subscribe_orderbook(self, instrument_id: str) -> None:
        await self._ws.subscribe_orderbook(nautilus_pyo3.InstrumentId.from_str(instrument_id))

    async def unsubscribe_orderbook(self, instrument_id: str) -> None:
        await self._ws.unsubscribe_orderbook(nautilus_pyo3.InstrumentId.from_str(instrument_id))

    async def subscribe_bars(self, bar_type: str) -> None:
        await self._ws.subscribe_bars(nautilus_pyo3.BarType.from_str(bar_type))

    async def unsubscribe_bars(self, bar_type: str) -> None:
        await self._ws.unsubscribe_bars(nautilus_pyo3.BarType.from_str(bar_type))

    async def subscribe_markets(self) -> None:
        """Subscribe to mark/index price + instrument status updates for all instruments."""
        await self._ws.subscribe_markets()

    async def request_orderbook_snapshot(self, instrument_id: str) -> OrderBookDeltas:
        """
        Fetch a full order book snapshot via REST (used to resync after a WS sequence gap).

        Returns a synthetic CLEAR+ADD `OrderBookDeltas` built from the REST bids/asks
        (`request_orderbook_snapshot` on the Rust HTTP client, `crates/adapters/dydx/src/http/client.rs`).
        The response carries no sequence/anchor field -- confirmed via `OrderbookResponse`
        in `crates/adapters/dydx/src/http/models.rs`, unlike e.g. Binance's `lastUpdateId`.

        Returns the Cython `OrderBookDeltas` (matches every other data path in this
        collector), not the raw pyo3-native type the Rust client returns -- same
        `.from_pyo3()` conversion the official adapter uses at
        `nautilus_trader/adapters/dydx/data.py:527-531`.
        """
        pyo3_deltas = await self._http.request_orderbook_snapshot(
            nautilus_pyo3.InstrumentId.from_str(instrument_id),
        )
        return OrderBookDeltas.from_pyo3(pyo3_deltas)

    def _handle_message(self, message: object) -> None:
        # Trades/orderbook/bars arrive wrapped in a PyCapsule; markets-channel
        # updates (mark/index price, funding, instrument status) arrive as plain
        # pyo3-native objects instead -- both paths mirror
        # nautilus_trader/adapters/dydx/data.py's `_handle_msg`.
        if nautilus_pyo3.is_pycapsule(message):
            self._on_data(capsule_to_data(message))
            return

        if isinstance(message, nautilus_pyo3.MarkPriceUpdate):
            update = MarkPriceUpdate.from_pyo3(message)
            self._on_data(
                MarkPriceUpdate(
                    instrument_id=update.instrument_id,
                    value=_at_fixed_precision(update.value),
                    ts_event=update.ts_event,
                    ts_init=update.ts_init,
                ),
            )
        elif isinstance(message, nautilus_pyo3.IndexPriceUpdate):
            update = IndexPriceUpdate.from_pyo3(message)
            self._on_data(
                IndexPriceUpdate(
                    instrument_id=update.instrument_id,
                    value=_at_fixed_precision(update.value),
                    ts_event=update.ts_event,
                    ts_init=update.ts_init,
                ),
            )
        elif isinstance(message, nautilus_pyo3.FundingRateUpdate):
            self._on_data(FundingRateUpdate.from_pyo3(message))
        elif isinstance(message, nautilus_pyo3.InstrumentStatus):
            self._on_data(InstrumentStatus.from_pyo3(message))
        elif not isinstance(message, dict):
            logger.debug(f"Ignoring message of type {type(message).__name__}")
