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
Story 15.5: `ws/live.py`'s new subscribe/unsubscribe multiplexing pieces --
`_parse_candle_channel` and `_CandleSubscriptions` -- tested directly against real
`asyncio.Queue`/`LiveCandleBus` objects, no real WebSocket/Redis needed (the real-Redis
`/ws/live` relay path itself is already covered end-to-end for `rankings:live` by
test_rankings.py's `test_rankings_live_message_reflected_by_rest_and_ws_relay`, which
this story's rewrite must keep passing unmodified -- proving the multiplexer's rankings
forwarder is unaffected by the new candle-subscription machinery added here).
"""

import asyncio

import pytest
from kernel.second_snapshot import DydxSecondSnapshot

from data_api import live_candles
from data_api.live_candles import LiveCandleBus
from data_api.ws.live import _CandleSubscriptions
from data_api.ws.live import _handle_control_message
from data_api.ws.live import _parse_candle_channel
from nautilus_trader.model.identifiers import InstrumentId


_IID = "BTC-USD-PERP.DYDX"


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        ("candles:BTC-USD-PERP.DYDX:60", (_IID, 60)),
        ("candles:ETH-USD-PERP.DYDX:1", ("ETH-USD-PERP.DYDX", 1)),
        ("rankings:live", None),  # wrong prefix
        ("candles:BTC-USD-PERP.DYDX", None),  # missing bar_seconds
        ("candles:BTC-USD-PERP.DYDX:sixty", None),  # non-numeric bar_seconds
        ("candles::60", None),  # empty iid
    ],
)
def test_parse_candle_channel(channel: str, expected: tuple[str, int] | None) -> None:
    assert _parse_candle_channel(channel) == expected


@pytest.fixture
def isolated_bus(monkeypatch: pytest.MonkeyPatch) -> LiveCandleBus:
    """
    A fresh `LiveCandleBus`, isolated from the module-level `live_candle_bus` the
    running app uses -- `_CandleSubscriptions` (ws/live.py) calls through
    `live_candles.live_candle_bus` dynamically, so patching the module attribute is
    enough to redirect it without editing ws/live.py's own code.
    """
    bus = LiveCandleBus()
    monkeypatch.setattr(live_candles, "live_candle_bus", bus)
    return bus


@pytest.mark.asyncio
async def test_subscribe_registers_with_live_candle_bus_and_forwards_into_outbox(
    isolated_bus: LiveCandleBus,
) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _CandleSubscriptions(outbox)

    subs.subscribe("candles:BTC-USD-PERP.DYDX:60", _IID, 60)
    await asyncio.sleep(0)  # let the forwarder task start awaiting its source queue
    assert (_IID, 60) in isolated_bus._listeners

    isolated_bus.handle_batch([DydxSecondSnapshot.to_dict(_a_snapshot())])
    message = await asyncio.wait_for(outbox.get(), timeout=1.0)
    assert message["channel"] == "candles:BTC-USD-PERP.DYDX:60"

    subs.teardown_all()  # cancel the forwarder task -- otherwise it leaks past the test


@pytest.mark.asyncio
async def test_unsubscribe_tears_down_live_candle_bus_state(isolated_bus: LiveCandleBus) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _CandleSubscriptions(outbox)
    channel = "candles:BTC-USD-PERP.DYDX:60"
    subs.subscribe(channel, _IID, 60)
    await asyncio.sleep(0)

    subs.unsubscribe(channel)
    await asyncio.sleep(0)

    assert (_IID, 60) not in isolated_bus._listeners
    assert (_IID, 60) not in isolated_bus._buffers


@pytest.mark.asyncio
async def test_teardown_all_unsubscribes_every_active_channel(isolated_bus: LiveCandleBus) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _CandleSubscriptions(outbox)
    subs.subscribe("candles:BTC-USD-PERP.DYDX:60", _IID, 60)
    subs.subscribe("candles:BTC-USD-PERP.DYDX:300", _IID, 300)
    await asyncio.sleep(0)

    subs.teardown_all()
    await asyncio.sleep(0)

    assert isolated_bus._listeners == {}
    assert isolated_bus._buffers == {}


@pytest.mark.asyncio
async def test_handle_control_message_subscribe_and_unsubscribe_dispatch(
    isolated_bus: LiveCandleBus,
) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _CandleSubscriptions(outbox)
    channel = "candles:BTC-USD-PERP.DYDX:60"

    _handle_control_message({"subscribe": channel}, subs)
    await asyncio.sleep(0)
    assert (_IID, 60) in isolated_bus._listeners

    _handle_control_message({"unsubscribe": channel}, subs)
    await asyncio.sleep(0)
    assert (_IID, 60) not in isolated_bus._listeners


@pytest.mark.asyncio
async def test_handle_control_message_ignores_malformed_or_unparseable(
    isolated_bus: LiveCandleBus,
) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _CandleSubscriptions(outbox)

    _handle_control_message({"subscribe": 123}, subs)  # not a string
    _handle_control_message({"subscribe": "rankings:live"}, subs)  # wrong prefix
    _handle_control_message({"ping": "hello"}, subs)  # unknown key

    assert isolated_bus._listeners == {}


def _a_snapshot() -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[100.0],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        open_price=100.0,
        high_price=100.0,
        low_price=100.0,
        close_price=100.0,
        ts_event=1_800_000_000_000_000_000,
        ts_init=1_800_000_000_000_000_000,
    )
