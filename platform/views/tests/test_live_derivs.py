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
Story 33.4: `LiveDerivsBus` relays `derivs:raw` rows per instrument and the liquidations
`LiveCandleBus` decoded -- real `kernel.derivs_wire` rows and real `Liquidation`s, no Redis (frames
are handed to `handle_derivs`/`handle_message` and `handle_liquidations` directly).
"""

import asyncio
import json
from decimal import Decimal
from typing import Any

from kernel.derivs_wire import DerivsTick
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from views.live_candles import LiveCandleBus
from views.live_derivs import LiveDerivsBus
from views.live_derivs import derivs_channel
from views.live_derivs import liquidations_channel


_BTC = "BTCUSDT-LINEAR.BYBIT"
_ETH = "ETHUSDT-LINEAR.BYBIT"
_T = 1_800_000_000_000_000_000


def _row(iid: str, kind: str = "mark", value: str = "100.50") -> dict[str, Any]:
    return {"instrument_id": iid, "kind": kind, "t": _T, "ts_init": _T + 5, "value": value}


def _drain(queue: "asyncio.Queue[dict]") -> list[dict]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def _liq(iid: str = _BTC, key: str = "a") -> Liquidation:
    return Liquidation.from_wire_text(
        InstrumentId.from_str(iid), LiquidatedSide.SHORT, "0.041", "85138.50", (2, 3), key, _T, _T
    )


def _liq_frame(row: Liquidation) -> dict:
    # 0.041 x 85138.50: 41 size units x 8 513 850 price units = 349 067 850 at 10^-(2 + 3).
    return {
        "channel": f"liquidations:{_BTC}",
        "liq": Liquidation.to_dict(row),
        "notional_units": 349_067_850,
        "notional_precision": 5,
    }


def test_each_listener_gets_exactly_its_instruments_rows() -> None:
    bus = LiveDerivsBus()
    btc_a, btc_b = bus.subscribe(derivs_channel(_BTC)), bus.subscribe(derivs_channel(_BTC))
    eth = bus.subscribe(derivs_channel(_ETH))
    bus.handle_derivs([_row(_BTC), _row(_ETH, "oi", "51234.567")])
    expected_btc = {
        "channel": f"derivs:{_BTC}",
        "kind": "mark",
        "t": _T,
        "ts_init": _T + 5,
        "value": "100.50",
        "basis_mi_bps": None,  # no index relayed yet
    }
    assert [_drain(btc_a), _drain(btc_b)] == [[expected_btc], [expected_btc]]
    assert [m["value"] for m in _drain(eth)] == ["51234.567"]


def test_a_row_nobody_listens_to_is_dropped_without_state() -> None:
    bus = LiveDerivsBus()
    bus.handle_derivs([_row(_BTC), _row(_BTC, "index", "100.00")])
    assert (bus._listeners, bus._last_prices) == ({}, {})


def test_a_mark_or_index_frame_carries_the_basis_once_both_are_known() -> None:
    """(100.50 - 100.00) / 100.00 x 10^4 = 50 bps; a later index 100.50 makes it 0."""
    bus = LiveDerivsBus()
    queue = bus.subscribe(derivs_channel(_BTC))
    bus.handle_derivs([_row(_BTC, "index", "100.00")])
    bus.handle_derivs([_row(_BTC, "mark", "100.50"), _row(_BTC, "oi", "7")])
    bus.handle_derivs([_row(_BTC, "index", "100.50")])
    frames = _drain(queue)
    assert [(m["kind"], m.get("basis_mi_bps", "absent")) for m in frames] == [
        ("index", None),
        ("mark", 50.0),
        ("oi", "absent"),
        ("index", 0.0),
    ]


def test_a_non_positive_index_has_no_basis() -> None:
    bus = LiveDerivsBus()
    queue = bus.subscribe(derivs_channel(_BTC))
    bus.handle_derivs([_row(_BTC, "index", "0"), _row(_BTC, "mark", "100.50")])
    assert [m["basis_mi_bps"] for m in _drain(queue)] == [None, None]


def test_the_basis_pairs_only_one_instruments_mark_and_index() -> None:
    bus = LiveDerivsBus()
    btc, eth = bus.subscribe(derivs_channel(_BTC)), bus.subscribe(derivs_channel(_ETH))
    bus.handle_derivs([_row(_BTC, "index", "100.00"), _row(_ETH, "mark", "100.50")])
    assert [m["basis_mi_bps"] for m in _drain(btc) + _drain(eth)] == [None, None]


def test_a_funding_frame_carries_its_annualised_rate() -> None:
    """0.0001 per 8 h: 0.0001 x 31 536 000 / 28 800 = 0.1095; no interval: None."""
    bus = LiveDerivsBus()
    queue = bus.subscribe(derivs_channel(_BTC))
    eight_hours = _row(_BTC, "funding", "0.0001") | {"interval": 28_800, "next_funding_ns": _T}
    bus.handle_derivs([eight_hours, _row(_BTC, "funding", "0.0001")])
    assert [m["annualised"] for m in _drain(queue)] == [0.1095, None]


def test_the_last_unsubscribe_drops_the_pairing_state() -> None:
    bus = LiveDerivsBus()
    channel = derivs_channel(_BTC)
    queue = bus.subscribe(channel)
    bus.handle_derivs([_row(_BTC, "index", "100.00")])
    assert bus._last_prices == {channel: {"index": Decimal("100.00")}}
    bus.unsubscribe(channel, queue)
    assert bus._last_prices == {}
    queue = bus.subscribe(channel)
    bus.handle_derivs([_row(_BTC, "mark", "100.50")])
    assert [m["basis_mi_bps"] for m in _drain(queue)] == [None]  # the old index is gone


def test_a_bad_row_is_ledgered_and_the_frames_other_rows_still_relayed() -> None:
    error_ledger.reset()
    bus = LiveDerivsBus()
    queue = bus.subscribe(derivs_channel(_BTC))
    missing_value = _row(_BTC)
    del missing_value["value"]
    bus.handle_derivs([missing_value, _row(_BTC, "basis"), _row(_BTC, "index", "100.00")])
    assert [m["kind"] for m in _drain(queue)] == ["index"]
    assert error_ledger.counts() == {"live_derivs.parse": 2}
    error_ledger.reset()


def test_a_frame_of_many_bad_rows_is_ledgered_capped_with_one_summary_line() -> None:
    """Ten rows without a value: three itemised (repr cut short), one line naming ten; one relayed."""
    error_ledger.reset()
    bus = LiveDerivsBus()
    queue = bus.subscribe(derivs_channel(_BTC))
    bad = {k: v for k, v in _row(_BTC).items() if k != "value"} | {"pad": "x" * 1000}
    bus.handle_derivs([bad] * 10 + [_row(_BTC)])
    assert len(_drain(queue)) == 1
    assert error_ledger.counts() == {"live_derivs.parse": 4}
    assert error_ledger.last_details()["live_derivs.parse"] == (
        "10 derivs:raw rows of one frame failed to decode, 7 beyond the first 3 not itemised"
    )
    error_ledger.reset()


def test_an_itemised_bad_row_names_its_repr_cut_short() -> None:
    error_ledger.reset()
    bad = {"pad": "x" * 1000}
    LiveDerivsBus().handle_derivs([bad])
    detail = error_ledger.last_details()["live_derivs.parse"]
    assert detail == f"derivs:raw row failed to decode, SKIPPED: {repr(bad)[:200]}... (1011 chars)"
    error_ledger.reset()


def test_a_frame_that_is_not_a_list_or_not_json_is_ledgered() -> None:
    error_ledger.reset()
    bus = LiveDerivsBus()
    bus.handle_derivs({"not": "a list"})
    bus.handle_message("{")
    assert error_ledger.counts() == {"live_derivs.parse": 2}
    error_ledger.reset()


def test_a_json_message_is_decoded_and_relayed() -> None:
    bus = LiveDerivsBus()
    queue = bus.subscribe(derivs_channel(_BTC))
    bus.handle_message(json.dumps([_row(_BTC, "funding", "0.0001")]))
    (message,) = _drain(queue)
    assert (message["value"], message["interval"], message["next_funding_ns"]) == (
        "0.0001",
        None,
        None,
    )


def test_the_last_unsubscribe_tears_the_channel_down() -> None:
    bus = LiveDerivsBus()
    channel = derivs_channel(_BTC)
    first, second = bus.subscribe(channel), bus.subscribe(channel)
    bus.unsubscribe(channel, first)
    assert channel in bus._listeners
    bus.unsubscribe(channel, second)
    bus.unsubscribe(channel, second)  # again: a no-op
    assert bus._listeners == {}


def test_the_candle_bus_forwards_its_decoded_liquidations() -> None:
    candles, derivs = LiveCandleBus("no-catalog-read-in-this-test"), LiveDerivsBus()
    candles.attach_liquidations(derivs)
    queue = derivs.subscribe(liquidations_channel(_BTC))
    row = _liq()
    candles.handle_liquidations([Liquidation.to_dict(row)])
    assert _drain(queue) == [_liq_frame(row)]


def test_a_replayed_liquidation_is_forwarded_once() -> None:
    """A venue event the candle bus already holds (a replayed frame) is not relayed twice."""
    candles, derivs = LiveCandleBus("no-catalog-read-in-this-test"), LiveDerivsBus()
    candles.attach_liquidations(derivs)
    queue = derivs.subscribe(liquidations_channel(_BTC))
    row = _liq()
    candles.handle_liquidations([Liquidation.to_dict(row), Liquidation.to_dict(row)])
    candles.handle_liquidations([Liquidation.to_dict(row)])
    assert _drain(queue) == [_liq_frame(row)]


def test_a_row_the_candle_bus_refused_is_not_forwarded() -> None:
    error_ledger.reset()
    candles, derivs = LiveCandleBus("no-catalog-read-in-this-test"), LiveDerivsBus()
    candles.attach_liquidations(derivs)
    spot = "BTCUSDT-SPOT.BYBIT"
    queue = derivs.subscribe(liquidations_channel(spot))
    candles.handle_liquidations([Liquidation.to_dict(_liq(spot))])  # no feed: ledgered by candles
    assert queue.empty()
    error_ledger.reset()


def test_a_detached_listener_gets_nothing() -> None:
    candles, derivs = LiveCandleBus("no-catalog-read-in-this-test"), LiveDerivsBus()
    candles.attach_liquidations(derivs)
    candles.detach_liquidations(derivs)
    queue = derivs.subscribe(liquidations_channel(_BTC))
    candles.handle_liquidations([Liquidation.to_dict(_liq())])
    assert queue.empty()


class _Broken:
    def publish_liquidations(self, rows: list[Liquidation]) -> None:
        raise RuntimeError("listener bug")


def test_a_failing_listener_is_ledgered_and_the_candle_fold_goes_on() -> None:
    error_ledger.reset()
    candles = LiveCandleBus("no-catalog-read-in-this-test")
    candles.attach_liquidations(_Broken())
    candles.handle_liquidations([Liquidation.to_dict(_liq())])
    assert [r.venue_event_id for r in candles.recent_liquidations(_BTC, 0, 1 << 62)] == ["a"]
    assert error_ledger.counts() == {"live_candles.liquidation_listener": 1}
    error_ledger.reset()


# --- Story 33.8: the observer hook ----------------------------------------------------------------


class _Recorder:
    """A `DerivsObserver` by shape: records every tick and batch it is handed."""

    def __init__(self) -> None:
        self.ticks: list[tuple[str, str, Decimal]] = []
        self.batches: list[list[str]] = []

    def on_deriv(self, tick: DerivsTick) -> None:
        self.ticks.append((tick.instrument_id, tick.kind, tick.value))

    def on_liquidation(self, rows: list[Liquidation]) -> None:
        self.batches.append([row.venue_event_id for row in rows])


class _BrokenObserver:
    def on_deriv(self, tick: DerivsTick) -> None:
        raise RuntimeError("observer bug")

    def on_liquidation(self, rows: list[Liquidation]) -> None:
        raise RuntimeError("observer bug")


def test_an_observer_sees_every_decoded_tick_with_no_listener() -> None:
    bus, observer = LiveDerivsBus(), _Recorder()
    bus.attach(observer)
    bus.attach(observer)  # twice: still called once
    bus.handle_derivs([_row(_BTC), {"bad": 1}, _row(_ETH, "funding", "0.0001")])
    assert observer.ticks == [
        (_BTC, "mark", Decimal("100.50")),
        (_ETH, "funding", Decimal("0.0001")),
    ]
    assert bus._listeners == {}  # observing creates no channel state (MEM-02)
    error_ledger.reset()


def test_an_observer_does_not_change_what_a_listener_receives() -> None:
    plain, observed = LiveDerivsBus(), LiveDerivsBus()
    observed.attach(_Recorder())
    queues = [bus.subscribe(derivs_channel(_BTC)) for bus in (plain, observed)]
    for bus in (plain, observed):
        bus.handle_derivs([_row(_BTC), _row(_BTC, "index", "100.00")])
    assert _drain(queues[0]) == _drain(queues[1])


def test_an_observer_sees_each_liquidation_batch_the_candle_bus_decoded() -> None:
    candles, derivs, observer = (
        LiveCandleBus("no-catalog-read-in-this-test"),
        LiveDerivsBus(),
        _Recorder(),
    )
    candles.attach_liquidations(derivs)
    derivs.attach(observer)
    candles.handle_liquidations(
        [Liquidation.to_dict(_liq(key="a")), Liquidation.to_dict(_liq(key="b"))]
    )
    assert observer.batches == [["a", "b"]]


def test_a_failing_observer_is_ledgered_and_the_relay_goes_on() -> None:
    error_ledger.reset()
    bus, recorder = LiveDerivsBus(), _Recorder()
    bus.attach(_BrokenObserver())
    bus.attach(recorder)
    queue = bus.subscribe(derivs_channel(_BTC))
    bus.handle_derivs([_row(_BTC)])
    bus.publish_liquidations([_liq()])
    assert len(_drain(queue)) == 1
    assert (len(recorder.ticks), len(recorder.batches)) == (1, 1)
    assert error_ledger.counts() == {"live_derivs.observer": 2}
    error_ledger.reset()


def test_a_detached_observer_is_no_longer_called() -> None:
    bus, observer = LiveDerivsBus(), _Recorder()
    bus.attach(observer)
    bus.detach(observer)
    bus.detach(observer)  # again: a no-op
    bus.handle_derivs([_row(_BTC)])
    bus.publish_liquidations([_liq()])
    assert (observer.ticks, observer.batches) == ([], [])
