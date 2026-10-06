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
`_parse_candle_channel` and `_Subscriptions` (Story 33.4 generalised it to the derivs and
liquidations channels, one cap for all kinds) -- tested directly against real
`asyncio.Queue`/`LiveCandleBus` objects, no real WebSocket/Redis needed (the real-Redis
`/ws/live` relay path itself is already covered end-to-end for `rankings:live` by
test_rankings.py's `test_rankings_live_message_reflected_by_rest_and_ws_relay`, which
this story's rewrite must keep passing unmodified -- proving the multiplexer's rankings
forwarder is unaffected by the new candle-subscription machinery added here).
"""

import asyncio

import pytest
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from views.live_candles import LiveCandleBus
from views.live_derivs import LiveDerivsBus

from data_api import buses
from data_api.ws.live import _MAX_SUBSCRIPTIONS
from data_api.ws.live import _Channel
from data_api.ws.live import _handle_control_message
from data_api.ws.live import _parse_candle_channel
from data_api.ws.live import _parse_channel
from data_api.ws.live import _Subscriptions
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
        ("candles:BTC-USD-PERP.DYDX:0", None),  # 0 would divide-by-zero in the bucket math
        ("candles:BTC-USD-PERP.DYDX:604800", (_IID, 604_800)),  # 1w, the widest bar served
        # Above the bound, `candles.domain.fold` raises from numpy's int64 bucket arithmetic, and
        # that raise escapes LiveCandleBus.handle_batch into its reconnect loop -- one such channel
        # would stop live candles for every connected client, so it must never reach a subscription.
        ("candles:BTC-USD-PERP.DYDX:604801", None),
        ("candles:BTC-USD-PERP.DYDX:99999999999999999999", None),
    ],
)
def test_parse_candle_channel(channel: str, expected: tuple[str, int] | None) -> None:
    assert _parse_candle_channel(channel) == expected


@pytest.fixture
def isolated_bus(monkeypatch: pytest.MonkeyPatch) -> LiveCandleBus:
    """
    Return a fresh `LiveCandleBus`, isolated from the module-level `live_candle_bus` the
    running app uses -- `_Subscriptions` (ws/live.py) calls through
    `buses.live_candle_bus` dynamically, so patching the module attribute is
    enough to redirect it without editing ws/live.py's own code.
    """
    bus = LiveCandleBus("no-catalog-read-in-this-test")
    monkeypatch.setattr(buses, "live_candle_bus", bus)
    return bus


@pytest.mark.asyncio
async def test_subscribe_registers_with_live_candle_bus_and_forwards_into_outbox(
    isolated_bus: LiveCandleBus,
) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _Subscriptions(outbox)

    subs.subscribe(_Channel("candles", _IID, 60))
    await asyncio.sleep(0)  # let the forwarder task start awaiting its source queue
    assert (_IID, 60) in isolated_bus._listeners

    isolated_bus.handle_batch([DydxSecondSnapshot.to_dict(_a_snapshot())])
    message = await asyncio.wait_for(outbox.get(), timeout=1.0)
    assert message["channel"] == "candles:BTC-USD-PERP.DYDX:60"

    subs.teardown_all()  # cancel the forwarder task -- otherwise it leaks past the test


@pytest.mark.asyncio
async def test_unsubscribe_tears_down_live_candle_bus_state(isolated_bus: LiveCandleBus) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _Subscriptions(outbox)
    channel = _Channel("candles", _IID, 60)
    subs.subscribe(channel)
    await asyncio.sleep(0)

    subs.unsubscribe(channel)
    await asyncio.sleep(0)

    assert (_IID, 60) not in isolated_bus._listeners
    assert (_IID, 60) not in isolated_bus._buffers


@pytest.mark.asyncio
async def test_teardown_all_unsubscribes_every_active_channel(isolated_bus: LiveCandleBus) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _Subscriptions(outbox)
    subs.subscribe(_Channel("candles", _IID, 60))
    subs.subscribe(_Channel("candles", _IID, 300))
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
    subs = _Subscriptions(outbox)
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
    subs = _Subscriptions(outbox)

    _handle_control_message({"subscribe": 123}, subs)  # not a string
    _handle_control_message({"subscribe": "rankings:live"}, subs)  # wrong prefix
    _handle_control_message({"ping": "hello"}, subs)  # unknown key

    assert isolated_bus._listeners == {}


@pytest.mark.asyncio
async def test_only_the_first_unparseable_channel_of_a_connection_is_logged(
    isolated_bus: LiveCandleBus, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Three bad subscribes ("derivs:" has an empty id) on one connection: one WARNING, the three
    counted, and one INFO line at close naming the count.
    """
    subs = _Subscriptions(asyncio.Queue())
    with caplog.at_level("INFO", logger="data_api.ws.live"):
        for _ in range(3):
            _handle_control_message({"subscribe": "derivs:"}, subs)
        subs.log_ignored()

    assert subs.ignored == 3
    assert [(r.levelname, "derivs:" in r.getMessage()) for r in caplog.records] == [
        ("WARNING", True),
        ("INFO", False),
    ]
    assert "ignoring 3 control frames" in caplog.records[1].getMessage()


def _a_snapshot() -> DydxSecondSnapshot:
    return make_snapshot(
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


# -- the derivs and liquidations channels (Story 33.4) --------------------------------------------

_BYBIT = "BTCUSDT-LINEAR.BYBIT"


@pytest.mark.parametrize(
    ("channel", "expected"),
    [
        (f"derivs:{_BYBIT}", _Channel("derivs", _BYBIT)),
        (f"liquidations:{_BYBIT}", _Channel("liquidations", _BYBIT)),
        (f"candles:{_BYBIT}:60", _Channel("candles", _BYBIT, 60)),
        ("derivs:", None),  # empty iid
        ("liquidations", None),  # no iid at all
        (f"derivs:{_BYBIT}:60", None),  # a `:` in the iid: not one instrument
        (f"funding:{_BYBIT}", None),  # unknown kind
        (f"candles:{_BYBIT}:0", None),  # the candle rules still apply
    ],
)
def test_parse_channel(channel: str, expected: _Channel | None) -> None:
    assert _parse_channel(channel) == expected


def test_a_parsed_channel_names_itself_canonically() -> None:
    assert [
        _Channel("candles", _BYBIT, 60).name,
        _Channel("derivs", _BYBIT).name,
        _Channel("liquidations", _BYBIT).name,
    ] == [f"candles:{_BYBIT}:60", f"derivs:{_BYBIT}", f"liquidations:{_BYBIT}"]


@pytest.fixture
def derivs_bus(monkeypatch: pytest.MonkeyPatch) -> LiveDerivsBus:
    bus = LiveDerivsBus()
    monkeypatch.setattr(buses, "live_derivs_bus", bus)
    return bus


def _funding_row(iid: str) -> dict:
    return {
        "instrument_id": iid,
        "kind": "funding",
        "t": 1,
        "ts_init": 2,
        "value": "0.0001",
        "interval": 28_800,
        "next_funding_ns": 3,
    }


@pytest.mark.asyncio
async def test_a_derivs_subscription_receives_only_its_instruments_rows(
    derivs_bus: LiveDerivsBus,
) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _Subscriptions(outbox)
    _handle_control_message({"subscribe": f"derivs:{_BYBIT}"}, subs)
    await asyncio.sleep(0)
    derivs_bus.handle_derivs([_funding_row("ETHUSDT-LINEAR.BYBIT"), _funding_row(_BYBIT)])
    message = await asyncio.wait_for(outbox.get(), timeout=1.0)
    assert message == {
        "channel": f"derivs:{_BYBIT}",
        "kind": "funding",
        "t": 1,
        "ts_init": 2,
        "value": "0.0001",
        "interval": 28_800,
        "next_funding_ns": 3,
        "annualised": 0.1095,  # 0.0001 x 31 536 000 / 28 800, the server's (Story 33.5)
    }
    await asyncio.sleep(0)
    assert outbox.empty()
    subs.teardown_all()
    assert derivs_bus._listeners == {}


@pytest.mark.asyncio
async def test_a_liquidations_subscription_receives_the_forwarded_rows(
    derivs_bus: LiveDerivsBus,
) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _Subscriptions(outbox)
    _handle_control_message({"subscribe": f"liquidations:{_BYBIT}"}, subs)
    await asyncio.sleep(0)
    row = Liquidation.from_wire_text(
        InstrumentId.from_str(_BYBIT), LiquidatedSide.LONG, "0.041", "85138.50", (2, 3), "k", 5, 6
    )
    derivs_bus.publish_liquidations([row])
    message = await asyncio.wait_for(outbox.get(), timeout=1.0)
    assert message == {
        "channel": f"liquidations:{_BYBIT}",
        "liq": Liquidation.to_dict(row),
        "notional_units": 41 * 8_513_850,  # size x bankruptcy price at 10^-(2 + 3) (Story 33.5)
        "notional_precision": 5,
    }
    _handle_control_message({"unsubscribe": f"liquidations:{_BYBIT}"}, subs)
    assert derivs_bus._listeners == {}


@pytest.mark.asyncio
async def test_the_cap_counts_candles_derivs_and_liquidations_together(
    isolated_bus: LiveCandleBus, derivs_bus: LiveDerivsBus
) -> None:
    outbox: asyncio.Queue[dict] = asyncio.Queue()
    subs = _Subscriptions(outbox)
    half = _MAX_SUBSCRIPTIONS // 2
    for k in range(half):
        _handle_control_message({"subscribe": f"candles:{_IID}:{k + 1}"}, subs)
        _handle_control_message({"subscribe": f"derivs:I{k}.BYBIT"}, subs)
    assert _MAX_SUBSCRIPTIONS == 32
    _handle_control_message({"subscribe": f"liquidations:{_BYBIT}"}, subs)  # the 33rd
    _handle_control_message({"subscribe": f"candles:{_IID}:999"}, subs)  # and a 34th
    await asyncio.sleep(0)
    assert (len(isolated_bus._listeners), len(derivs_bus._listeners)) == (half, half)
    assert f"liquidations:{_BYBIT}" not in derivs_bus._listeners
    subs.teardown_all()


@pytest.mark.asyncio
async def test_a_repeated_subscribe_is_one_subscription(derivs_bus: LiveDerivsBus) -> None:
    subs = _Subscriptions(asyncio.Queue())
    for _ in range(3):
        _handle_control_message({"subscribe": f"derivs:{_BYBIT}"}, subs)
    assert [len(q) for q in derivs_bus._listeners.values()] == [1]
    subs.teardown_all()


@pytest.mark.asyncio
async def test_a_malformed_derivs_or_liquidations_subscribe_is_logged_and_ignored(
    derivs_bus: LiveDerivsBus, caplog: pytest.LogCaptureFixture
) -> None:
    subs = _Subscriptions(asyncio.Queue())
    with caplog.at_level("WARNING", logger="data_api.ws.live"):
        for text in ("derivs:", "liquidations:a:b", "derivs"):
            _handle_control_message({"subscribe": text}, subs)
    assert derivs_bus._listeners == {}
    # Each counted; only the first is logged (pinned by
    # `test_only_the_first_unparseable_channel_of_a_connection_is_logged`).
    assert subs.ignored == 3
    assert sum("unparseable subscribe channel" in r.getMessage() for r in caplog.records) == 1
