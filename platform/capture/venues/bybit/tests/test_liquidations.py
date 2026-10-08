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
Bybit liquidations (Story 33.1): the pure parser on frames recorded 2026-10-05
(`fixtures/bybit_all_liquidation_linear_20261005.jsonl`, `scripts/capture_hl_ws.py --topic
allLiquidation`), the side mapping, the dedup window, the feed's per-id, ack-confirmed coverage
over a fake socket (our own contract with `nautilus_pyo3.WebSocketClient`, not a Nautilus
internal) and the client's LINEAR-only routing.
"""

import asyncio
import copy
import json
import logging
from collections.abc import Awaitable
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from capture.application import sites
from capture.application.feed import ChannelRetry
from capture.application.wire_channels import WireChannels
from capture.domain.coverage import LiquidationsUnrecoverable
from capture.venues.bybit.client import LINEAR_FEED
from capture.venues.bybit.client import SPOT_FEED
from capture.venues.bybit.client import BybitClient
from capture.venues.bybit.liquidations import ACK_TIMEOUT_NS
from capture.venues.bybit.liquidations import CHECKPOINT_NS
from capture.venues.bybit.liquidations import CONNECTED
from capture.venues.bybit.liquidations import DOWN
from capture.venues.bybit.liquidations import MONITOR_NS
from capture.venues.bybit.liquidations import RECONNECT_SECONDS
from capture.venues.bybit.liquidations import RECONNECTING
from capture.venues.bybit.liquidations import REFUSED_BACKOFF_MAX_NS
from capture.venues.bybit.liquidations import TOPIC
from capture.venues.bybit.liquidations import WIRE_LAG_NS
from capture.venues.bybit.liquidations import BybitLiquidationFeed
from capture.venues.bybit.liquidations import Definition
from capture.venues.bybit.liquidations import LiquidationDedup
from capture.venues.bybit.liquidations import Malformed
from capture.venues.bybit.liquidations import Unencodable
from capture.venues.bybit.liquidations import definitions_of
from capture.venues.bybit.liquidations import extra_entry_keys
from capture.venues.bybit.liquidations import parse_liquidation_frame
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_FIXTURE = Path(__file__).parent / "fixtures" / "bybit_all_liquidation_linear_20261005.jsonl"
_RECORDED = [json.loads(line) for line in _FIXTURE.read_text().splitlines()]
_BTC = "BTCUSDT-LINEAR.BYBIT"
_ETH = "ETHUSDT-LINEAR.BYBIT"
_S_NS = 1_000_000_000
# The recorded frames' own hour: the feed stamps `ts_init` from its clock, and an entry whose `T`
# lies more than 300 s from it is refused.
_BASE_NS = 1_791_214_300 * _S_NS
# Each recorded symbol's definition precisions, read from Bybit's instruments-info on 2026-10-05
# (tickSize/qtyStep text digits, as the adapter derives them). Never mutated: `_precisions` copies.
_RECORDED_PRECISIONS = {
    "BTCUSDT": (2, 3),
    "ETHUSDT": (2, 2),
    "PENGUUSDT": (6, 0),
    "USUSDT": (6, 0),
    "PUMPFUNUSDT": (7, 0),
    "NILUSDT": (5, 0),
    "BTCPERP": (2, 3),
}


def _precisions(*extra: str) -> dict[str, tuple[int, int]]:
    return {**_RECORDED_PRECISIONS, **dict.fromkeys(extra, (2, 3))}


def _definition(
    symbol: str, precisions: tuple[int, int], suffix: str = "LINEAR"
) -> CryptoPerpetual:
    price_precision, size_precision = precisions
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(f"{symbol}-{suffix}.BYBIT"),
        raw_symbol=Symbol(symbol),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_precision,
        price_increment=Price(Decimal(1).scaleb(-price_precision), price_precision),
        size_precision=size_precision,
        size_increment=Quantity(Decimal(1).scaleb(-size_precision), size_precision),
        ts_event=0,
        ts_init=0,
    )


def _instruments(precisions: dict[str, tuple[int, int]]) -> list[CryptoPerpetual]:
    return [_definition(symbol, p) for symbol, p in precisions.items()]


def _definitions() -> dict[str, Definition]:
    return definitions_of(_instruments(_precisions()))


def _frame(topic: str, nth: int = 0) -> dict[str, Any]:
    frames = [r["raw"] for r in _RECORDED if r["raw"].get("topic") == f"allLiquidation.{topic}"]
    return copy.deepcopy(frames[nth])


def _arrival(frame: dict[str, Any]) -> int:
    """Return a plausible `ts_init`: 100 ms after the frame's first entry."""
    return frame["data"][0]["T"] * 1_000_000 + 100_000_000


def _rows(frame: dict[str, Any]) -> list[Liquidation]:
    parsed = parse_liquidation_frame(frame, _definitions(), ts_init=_arrival(frame))
    assert isinstance(parsed, list)
    assert all(isinstance(row, Liquidation) for row in parsed)
    return parsed  # type: ignore[return-value]


# -- the parser on recorded frames ------------------------------------------------------------


def test_a_recorded_frame_decodes_every_entry_at_the_definition_precisions() -> None:
    frame = _frame("BTCUSDT", 1)  # the recorded 10-entry push
    rows = _rows(frame)
    assert len(rows) == len(frame["data"]) == 10
    first = rows[0]
    assert str(first.instrument_id) == _BTC
    assert (first.price_units, first.size_units) == (8_504_570, 14)  # "85045.70", "0.014"
    assert (first.price_precision, first.size_precision) == (2, 3)
    assert first.ts_event == frame["data"][0]["T"] * 1_000_000
    assert first.ts_init == _arrival(frame)
    assert first.venue_event_id == "1791214368137:Buy:0.014:85045.70"


def test_units_at_precision_two_are_exact_nautilus_values() -> None:
    row = _rows(_frame("BTCUSDT"))[1]  # "v":"0.483","p":"85123.20"
    assert row.price == Price.from_str("85123.20")
    assert row.size == Quantity.from_str("0.483")
    assert row.notional_units() == 483 * 8_512_320


def test_units_at_precision_six_are_exact_nautilus_values() -> None:
    (row,) = _rows(_frame("PENGUUSDT"))  # "v":"26510","p":"0.009485"
    assert (row.price_units, row.size_units) == (9485, 26_510)
    assert row.price == Price.from_str("0.009485")
    assert row.price.precision == 6
    assert row.size == Quantity.from_str("26510")


def test_a_recorded_wire_buy_is_a_liquidated_long_and_sell_a_liquidated_short() -> None:
    # Bybit's `S` is the liquidated position: "Buy" = a long force-closed, its forced order a sell.
    (long,) = _rows(_frame("ETHUSDT"))
    (short,) = _rows(_frame("NILUSDT"))
    assert _frame("ETHUSDT")["data"][0]["S"] == "Buy"
    assert long.side is LiquidatedSide.LONG
    assert _frame("NILUSDT")["data"][0]["S"] == "Sell"
    assert short.side is LiquidatedSide.SHORT


def test_a_usdc_perp_symbol_without_usdt_decodes_by_its_raw_symbol() -> None:
    (row,) = _rows(_frame("BTCPERP"))
    assert str(row.instrument_id) == "BTCPERP-LINEAR.BYBIT"


def test_an_inexact_entry_is_unencodable_and_the_others_still_decode() -> None:
    frame = _frame("BTCUSDT")
    frame["data"][0]["p"] = "85138.505"  # finer than the definition's 2 decimals
    parsed = parse_liquidation_frame(frame, _definitions(), ts_init=_arrival(frame))
    assert isinstance(parsed, list)
    assert isinstance(parsed[0], Unencodable)
    assert "not exact at precision 2" in parsed[0].reason
    assert isinstance(parsed[1], Liquidation)


@pytest.mark.parametrize("scale", [1, 1_000_000])  # `T` in seconds, or in ns: wrong unit
def test_a_venue_time_far_from_arrival_is_unencodable(scale: int) -> None:
    frame = _frame("ETHUSDT")
    arrival = _arrival(frame)
    frame["data"][0]["T"] = (
        frame["data"][0]["T"] // 1000 if scale == 1 else frame["data"][0]["T"] * scale
    )
    (entry,) = parse_liquidation_frame(frame, _definitions(), ts_init=arrival)  # type: ignore[misc]
    assert isinstance(entry, Unencodable)
    assert "over 300 s from arrival" in entry.reason


def test_a_symbol_without_a_linear_definition_is_unencodable() -> None:
    frame = _frame("BTCUSDT")
    parsed = parse_liquidation_frame(frame, {}, ts_init=_arrival(frame))
    assert parsed == [Unencodable("BTCUSDT", "no instrument definition")] * 2


def test_extra_entry_keys_are_tolerated_and_named() -> None:
    frame = _frame("ETHUSDT")
    frame["data"][0]["x"] = "new"
    assert len(_rows(frame)) == 1
    assert extra_entry_keys(frame) == {"x"}


def _mutated(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    frame = _frame("BTCUSDT")
    change(frame)
    return frame


@pytest.mark.parametrize(
    "frame",
    [
        ["not", "an", "object"],
        _mutated(lambda f: f.pop("data")),
        _mutated(lambda f: f.update(data=[])),
        _mutated(lambda f: f["data"][0].pop("p")),
        _mutated(lambda f: f["data"][0].update(T="1791214346487")),
        _mutated(lambda f: f["data"][0].update(v=0.041)),
        _mutated(lambda f: f["data"][0].update(S="Long")),
        _mutated(lambda f: f["data"][0].update(s="ETHUSDT")),
        _mutated(lambda f: f.update(topic="liquidation.BTCUSDT")),
    ],
)
def test_a_frame_not_of_the_documented_shape_is_malformed(frame: object) -> None:
    assert isinstance(parse_liquidation_frame(frame, _definitions(), ts_init=_BASE_NS), Malformed)


def test_identical_entries_in_one_frame_are_both_kept_with_distinct_ids() -> None:
    frame = _frame("ETHUSDT")
    frame["data"].append(dict(frame["data"][0]))
    first, second = _rows(frame)
    assert first.venue_event_id == "1791214366816:Buy:105.00:2690.98"
    assert second.venue_event_id == "1791214366816:Buy:105.00:2690.98#1"


def test_definitions_are_linear_only_keyed_by_the_bybit_symbol() -> None:
    definitions = definitions_of(
        [_definition("BTCUSDT", (2, 3)), _definition("ETHUSDT", (2, 2), "SPOT")]
    )
    assert list(definitions) == ["BTCUSDT"]
    assert definitions["BTCUSDT"] == Definition(InstrumentId.from_str(_BTC), (2, 3))


# -- the dedup window ---------------------------------------------------------------------------


def test_a_repeat_within_five_seconds_is_dropped_and_after_it_kept() -> None:
    rows = _rows(_frame("BTCUSDT"))
    dedup = LiquidationDedup()
    assert dedup.fresh(rows, 10 * _S_NS) == rows
    assert dedup.fresh(rows, 14 * _S_NS) == []
    assert dedup.dropped == 2
    assert dedup.fresh(rows, 16 * _S_NS) == rows  # past the window: a new liquidation


def test_the_same_key_on_another_instrument_is_not_a_repeat() -> None:
    (eth,) = _rows(_frame("ETHUSDT"))
    other = Liquidation.from_dict({**Liquidation.to_dict(eth), "instrument_id": _BTC})
    dedup = LiquidationDedup()
    assert dedup.fresh([eth, other], 0) == [eth, other]


# -- the feed over a fake socket ------------------------------------------------------------------


class _Socket:
    """The four states and two sends `BybitLiquidationFeed` reads of a `WebSocketClient`."""

    def __init__(self, handler: Callable[[bytes], None], reconnected: Callable[[], None]) -> None:
        self.handler = handler
        self.reconnected = reconnected
        self.sent: list[dict[str, Any]] = []
        self.active = True
        self.reconnecting = False
        self.closed = False
        self.fail_sends = False

    def is_active(self) -> bool:
        return self.active

    def is_reconnecting(self) -> bool:
        return self.reconnecting

    def is_disconnecting(self) -> bool:
        return False

    def is_closed(self) -> bool:
        return self.closed

    async def send_text(self, data: bytes) -> None:
        if self.fail_sends:
            raise ConnectionError("send failed")
        self.sent.append(json.loads(data))

    async def disconnect(self) -> None:
        self.closed = True


class _Rig:
    """A feed attached to recording ports, its sockets and a settable clock."""

    def __init__(self, fail_connects: int = 0, extra: tuple[str, ...] = ()) -> None:
        self.precisions = _precisions(*extra)
        self.sockets: list[_Socket] = []
        self.ingested: list[tuple[list[Liquidation], str]] = []
        self.noted: list[Any] = []
        self.published: list[list[Liquidation]] = []
        self.now = _BASE_NS
        self._fail_connects = fail_connects
        self.feed = BybitLiquidationFeed(
            "mainnet", ledger=error_ledger.record, connector=self._connect, clock=lambda: self.now
        )
        self.feed.attach(self._ingest, self.noted.extend, self._publish)

    async def _connect(
        self,
        _loop: asyncio.AbstractEventLoop,
        url: str,
        handler: Callable[[bytes], None],
        reconnected: Callable[[], None],
    ) -> _Socket:
        assert url == "wss://stream.bybit.com/v5/public/linear"
        if self._fail_connects:
            self._fail_connects -= 1
            raise ConnectionError("connect refused")
        self.sockets.append(_Socket(handler, reconnected))
        return self.sockets[-1]

    def _ingest(self, rows: list[Liquidation], site: str) -> list[Liquidation]:
        self.ingested.append((rows, site))
        return rows

    async def _publish(self, rows: list[Liquidation]) -> None:
        self.published.append(rows)

    async def start(self, *iids: str) -> None:
        await self.feed.connect(asyncio.get_running_loop(), _instruments(self.precisions))
        self.feed._wire = WireChannels(1e6)  # unpaced for speed (pacing: test_wire_channels)
        for iid in iids:
            await self.feed.subscribe(iid)

    def deliver(self, raw: dict[str, Any]) -> None:
        self.sockets[-1].handler(json.dumps(raw).encode())

    def ack(self, request: dict[str, Any], success: bool = True, ret_msg: str = "") -> None:
        """Bybit's answer to one recorded request, echoing its `req_id`."""
        self.deliver(
            {"success": success, "ret_msg": ret_msg, "req_id": request["req_id"], "op": "subscribe"}
        )

    def ack_all(self) -> None:
        for request in self.sockets[-1].sent:
            if request["op"] == "subscribe":
                self.ack(request)


def _run(body: Callable[[], Awaitable[None]]) -> None:
    asyncio.run(body())  # type: ignore[arg-type]


def _window(iid: str, since: int, until: int) -> LiquidationsUnrecoverable:
    return LiquidationsUnrecoverable(iid, "feed_down", since - WIRE_LAG_NS, until)


def test_a_subscribe_is_a_gap_until_bybit_acknowledges_it() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC, _BTC)
        assert rig.noted == []  # not confirmed yet
        rig.now += 200_000_000
        rig.ack_all()

    _run(body)
    (request,) = rig.sockets[0].sent
    assert request["args"] == ["allLiquidation.BTCUSDT"]
    assert request["req_id"]
    assert rig.feed.held_ids() == {_BTC}
    assert rig.noted == [_window(_BTC, _BASE_NS, _BASE_NS + 200_000_000)]
    assert rig.feed._gaps == {}


def test_a_recorded_frame_is_ingested_deduped_and_published() -> None:
    error_ledger.reset()
    rig = _Rig()
    frame = _frame("BTCUSDT")

    async def body() -> None:
        await rig.start(_BTC)
        publisher = asyncio.ensure_future(rig.feed._publisher())
        rig.now = _arrival(frame)
        rig.deliver(frame)
        rig.deliver(frame)  # Bybit pushing the same entries again
        await asyncio.sleep(0)
        publisher.cancel()

    _run(body)
    (rows, site), (repeat, _) = rig.ingested
    assert [r.venue_event_id for r in rows] == [
        "1791214346487:Buy:0.041:85138.50",
        "1791214346601:Buy:0.483:85123.20",
    ]
    assert site == sites.LIQUIDATION_FEED
    assert repeat == []
    assert rig.published == [rows]
    assert error_ledger.counts() == {}


def test_pongs_and_duplicates_are_quiet_and_a_refusal_is_ledgered_released_and_resent() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC, _ETH)
        btc, eth = rig.sockets[0].sent
        rig.deliver(_RECORDED[1]["raw"])  # the recorded pong
        rig.ack(btc, success=False, ret_msg="error:already subscribed,topic:allLiquidation.BTCUSDT")
        rig.ack(eth, success=False, ret_msg="error:handler not found,topic:allLiquidation.ETHUSDT")
        await asyncio.sleep(0)  # the release runs as a task
        assert set(rig.feed._gaps) == {_ETH}  # BTC confirmed, ETH still unconfirmed
        rig.now += int(RECONNECT_SECONDS * _S_NS)
        await rig.feed.check(rig.now)

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}
    assert "handler not found" in error_ledger.last_details()[sites.LIQUIDATION_FEED]
    assert [r["args"] for r in rig.sockets[0].sent] == [
        ["allLiquidation.BTCUSDT"],
        ["allLiquidation.ETHUSDT"],
        ["allLiquidation.ETHUSDT"],  # resent by the monitor
    ]


def test_a_subscribe_left_unanswered_is_ledgered_released_and_resent_its_gap_open() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.now += ACK_TIMEOUT_NS + 1  # the ack never came
        await rig.feed.check(rig.now)

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}
    assert "no ack for subscribe" in error_ledger.last_details()[sites.LIQUIDATION_FEED]
    assert [r["args"] for r in rig.sockets[0].sent] == [
        ["allLiquidation.BTCUSDT"],
        ["allLiquidation.BTCUSDT"],  # resent by the monitor
    ]
    assert set(rig.feed._gaps) == {_BTC}  # still unconfirmed: the window stays open
    assert len(rig.feed._requests) == 1  # the expired request is gone, only the resend waits


def test_an_undecodable_frame_or_unknown_topic_is_an_unknown_message() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.sockets[-1].handler(b"not json")
        rig.deliver({"topic": "publicTrade.BTCUSDT", "data": []})

    _run(body)
    assert error_ledger.counts() == {sites.UNKNOWN_MESSAGE: 2}
    assert "unknown topic" in error_ledger.last_details()[sites.UNKNOWN_MESSAGE]
    assert rig.ingested == []


def test_a_failure_while_handling_a_frame_is_ledgered() -> None:
    error_ledger.reset()
    rig = _Rig()

    def broken(_rows: list[Liquidation], _site: str) -> list[Liquidation]:
        raise RuntimeError("buffer gone")

    rig.feed.attach(broken, rig.noted.extend, rig._publish)
    frame = _frame("BTCUSDT")

    async def body() -> None:
        await rig.start(_BTC)
        rig.now = _arrival(frame)
        rig.deliver(frame)

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}
    assert "frame handling failed" in error_ledger.last_details()[sites.LIQUIDATION_FEED]


def test_an_unencodable_entry_is_ledgered_and_the_frame_rest_archived() -> None:
    error_ledger.reset()
    rig = _Rig()
    frame = _frame("BTCUSDT")
    frame["data"][1]["v"] = "0.4831"

    async def body() -> None:
        await rig.start(_BTC)
        rig.now = _arrival(frame)
        rig.deliver(frame)

    _run(body)
    assert error_ledger.counts() == {sites.UNENCODABLE: 1}
    assert [len(rows) for rows, _ in rig.ingested] == [1]


def test_new_entry_keys_warn_once_and_keep_the_frame(caplog: pytest.LogCaptureFixture) -> None:
    rig = _Rig()
    frame = _frame("ETHUSDT")
    frame["data"][0]["x"] = "new"

    async def body() -> None:
        await rig.start(_ETH)
        rig.now = _arrival(frame)
        rig.deliver(frame)
        rig.deliver(
            _frame("ETHUSDT") | {"data": [frame["data"][0] | {"T": frame["data"][0]["T"] + 1}]}
        )

    with caplog.at_level(logging.WARNING, logger="capture.venues.bybit.liquidations"):
        _run(body)
    assert [len(rows) for rows, _ in rig.ingested] == [1, 1]
    assert len([r for r in caplog.records if "new keys" in r.message]) == 1


def test_reconnecting_keeps_every_held_gap_open_until_the_acks() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC, _ETH)
        rig.ack_all()
        rig.noted.clear()
        await rig.feed.check(rig.now)  # a connected tick: the known-good instant
        good = rig.now
        rig.sockets[0].active, rig.sockets[0].reconnecting = False, True
        rig.now += 2 * _S_NS
        await rig.feed.check(rig.now)
        assert rig.feed.state() == RECONNECTING
        rig.sockets[0].active, rig.sockets[0].reconnecting = True, False
        rig.now += _S_NS
        await rig.feed.check(rig.now)
        assert rig.noted == []  # `is_active()` alone confirms nothing
        # one tick before the known-good tick, but never inside the subscribe's own written line
        assert good - MONITOR_NS < _BASE_NS
        assert rig.feed._gaps == dict.fromkeys([_BTC, _ETH], _BASE_NS)
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()  # what `post_reconnection` schedules on the loop
        await asyncio.gather(*rig.feed._tasks)
        rig.now += _S_NS
        rig.ack_all()

    _run(body)
    assert rig.noted == [_window(_BTC, _BASE_NS, rig.now), _window(_ETH, _BASE_NS, rig.now)]
    assert error_ledger.counts() == {}  # reconnecting is not down


def test_a_reconnect_shorter_than_a_tick_is_still_a_window() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.ack_all()
        rig.noted.clear()
        await rig.feed.check(rig.now)
        rig.now += 500_000_000
        rig.feed._on_reconnected()  # between two ticks: the monitor never saw it
        await asyncio.gather(*rig.feed._tasks)
        rig.ack_all()

    _run(body)
    # from the end of the subscribe's own line (the ack at `_BASE_NS`), not a tick before it
    assert rig.noted == [_window(_BTC, _BASE_NS, _BASE_NS + 500_000_000)]


def test_a_reconnect_resubscribes_every_held_topic_in_requests_of_ten() -> None:
    extra = tuple(f"X{n}USDT" for n in range(5))
    rig = _Rig(extra=extra)
    iids = [f"{symbol}-LINEAR.BYBIT" for symbol in rig.precisions]

    async def body() -> None:
        await rig.start(*iids)
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()
        await asyncio.gather(*rig.feed._tasks)

    _run(body)
    assert [len(request["args"]) for request in rig.sockets[0].sent] == [10, 2]
    assert len({request["req_id"] for request in rig.sockets[0].sent}) == 2


def test_a_failed_resubscribe_chunk_is_released_and_resent() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.sockets[0].fail_sends = True
        rig.feed._on_reconnected()
        await asyncio.gather(*rig.feed._tasks)
        assert rig.feed._wire.held() == frozenset()
        rig.sockets[0].fail_sends = False
        rig.now += int(RECONNECT_SECONDS * _S_NS)
        await rig.feed.check(rig.now)

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}
    assert len(rig.sockets[0].sent) == 2  # the first subscribe, then the monitor's resend
    assert _BTC in rig.feed._gaps


def test_a_gap_open_past_a_minute_is_checkpointed() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)  # never acknowledged
        rig.now += CHECKPOINT_NS + _S_NS
        await rig.feed.check(rig.now)

    _run(body)
    assert rig.noted == [_window(_BTC, _BASE_NS, rig.now)]
    assert rig.feed._gaps == {_BTC: rig.now}


def test_a_failed_connect_is_down_ledgered_and_reopened_with_the_held_topics() -> None:
    error_ledger.reset()
    rig = _Rig(fail_connects=1)

    async def body() -> None:
        await rig.start(_BTC)  # held while down: nothing to send it on, nothing raised
        assert rig.feed.state() == DOWN
        await rig.feed.check(rig.now + _S_NS)
        rig.now += int(RECONNECT_SECONDS * _S_NS)
        await rig.feed.check(rig.now)  # reopens, subscribes BTC
        rig.now += _S_NS
        rig.ack_all()

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 2}  # the connect, then `down`
    assert [r["args"] for r in rig.sockets[0].sent] == [["allLiquidation.BTCUSDT"]]
    assert rig.feed.state() == CONNECTED
    assert rig.noted == [_window(_BTC, _BASE_NS, rig.now)]


def test_a_reopen_closes_the_previous_socket_and_a_failed_one_leaves_none() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig._fail_connects = 1
        await rig.feed._open()
        assert rig.sockets[0].closed
        assert rig.feed._socket is None
        await rig.feed.subscribe(_ETH)  # only held: no socket, no send, no raise

    _run(body)
    assert rig.feed.held_ids() == {_BTC, _ETH}
    assert len(rig.sockets[0].sent) == 1


def test_a_failed_subscribe_send_raises_and_leaves_the_topic_unheld() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start()
        rig.sockets[0].fail_sends = True
        with pytest.raises(ConnectionError):
            await rig.feed.subscribe(_BTC)
        rig.sockets[0].fail_sends = False
        await rig.feed.subscribe(_BTC)  # the retry sends it

    _run(body)
    assert [r["args"] for r in rig.sockets[0].sent] == [["allLiquidation.BTCUSDT"]]


def test_an_id_without_a_definition_is_ledgered_once_and_never_raises() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start()
        await rig.feed.subscribe("NOPEUSDT-LINEAR.BYBIT")
        await rig.feed.subscribe("NOPEUSDT-LINEAR.BYBIT")

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}
    assert rig.feed.held_ids() == frozenset()


def test_unsubscribe_writes_the_open_gap_and_sends_once() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.now += _S_NS
        await rig.feed.unsubscribe(_BTC)
        await rig.feed.unsubscribe(_BTC)

    _run(body)
    assert rig.noted == [_window(_BTC, _BASE_NS, _BASE_NS + _S_NS)]
    assert rig.sockets[0].sent[-1] == {"op": "unsubscribe", "args": ["allLiquidation.BTCUSDT"]}
    assert len(rig.sockets[0].sent) == 2
    assert rig.feed.held_ids() == frozenset()


def test_shutdown_writes_every_open_gap() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.now += _S_NS
        await rig.feed.disconnect()

    _run(body)
    assert rig.noted == [_window(_BTC, _BASE_NS, _BASE_NS + _S_NS)]
    assert rig.feed.state() == DOWN


def test_a_failed_publish_is_ledgered_and_the_rows_stay_archived() -> None:
    error_ledger.reset()
    rig = _Rig()
    frame = _frame("BTCUSDT")

    async def failing(_rows: list[Liquidation]) -> None:
        raise ConnectionError("redis down")

    rig.feed.attach(rig._ingest, rig.noted.extend, failing)

    async def body() -> None:
        await rig.start(_BTC)
        publisher = asyncio.ensure_future(rig.feed._publisher())
        rig.now = _arrival(frame)
        rig.deliver(frame)
        await asyncio.sleep(0)
        publisher.cancel()

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_PUBLISH: 1}
    assert len(rig.ingested[0][0]) == 2


def test_a_long_outage_writes_one_line_per_checkpoint_never_the_whole_span_again() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.ack_all()
        rig.noted.clear()
        await rig.feed.check(rig.now)
        rig.sockets[0].active, rig.sockets[0].reconnecting = False, True
        for _ in range(150):  # 150 s reconnecting, one monitor tick per second
            rig.now += _S_NS
            await rig.feed.check(rig.now)

    _run(body)
    assert len(rig.noted) == 2  # checkpoints at ~61 s and ~122 s, not one per tick
    first, second = rig.noted
    assert first.from_ns == _BASE_NS - WIRE_LAG_NS
    assert second.from_ns == first.to_ns - WIRE_LAG_NS


def test_a_half_open_socket_window_starts_at_its_last_message() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.ack_all()  # the last message the socket delivered, at `_BASE_NS`
        rig.noted.clear()
        for _ in range(60):  # still `is_active()`: a half-open socket, silent
            rig.now += _S_NS
            await rig.feed.check(rig.now)
        rig.feed._on_reconnected()  # the idle timeout's reconnect
        await asyncio.gather(*rig.feed._tasks)
        rig.ack_all()

    _run(body)
    assert rig.noted == [_window(_BTC, _BASE_NS, rig.now)]


def test_already_subscribed_in_a_batch_confirms_only_the_named_topic() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC, _ETH)
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()
        await asyncio.gather(*rig.feed._tasks)
        (batch,) = rig.sockets[0].sent
        rig.now += _S_NS
        rig.ack(
            batch, success=False, ret_msg="error:already subscribed,topic:allLiquidation.BTCUSDT"
        )
        await asyncio.gather(*rig.feed._tasks)
        assert set(rig.feed._gaps) == {_ETH}  # unproven: released, its gap open
        rig.now += int(RECONNECT_SECONDS * _S_NS)
        await rig.feed.check(rig.now)

    _run(body)
    assert rig.sockets[0].sent[-1]["args"] == ["allLiquidation.ETHUSDT"]  # resent alone


def test_a_refused_topic_backs_off_doubling_and_is_capped() -> None:
    error_ledger.reset()
    rig = _Rig()
    resends: list[int] = []

    async def body() -> None:
        await rig.start(_ETH)
        start = rig.now
        for _ in range(4):
            request = rig.sockets[0].sent[-1]
            rig.ack(request, success=False, ret_msg="error:handler not found")
            await asyncio.gather(*rig.feed._tasks)
            sent = len(rig.sockets[0].sent)
            while len(rig.sockets[0].sent) == sent:
                rig.now += _S_NS
                await rig.feed.check(rig.now)
            resends.append((rig.now - start) // _S_NS)
            start = rig.now

    _run(body)
    assert resends == [10, 20, 40, 80]
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 4}  # one per refusal, not per 10 s
    assert REFUSED_BACKOFF_MAX_NS == 600 * _S_NS


def test_a_retry_of_a_held_topic_sends_nothing_and_waits_for_no_ack() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        await rig.feed.subscribe(_BTC)  # capture's retry loop, the topic already held
        rig.ack_all()
        rig.now += ACK_TIMEOUT_NS + 1
        await rig.feed.check(rig.now)

    _run(body)
    assert len(rig.sockets[0].sent) == 1
    assert rig.feed._requests == {}
    assert error_ledger.counts() == {}  # no false "no ack"


def test_a_subscribe_while_reconnecting_only_holds_and_is_sent_once_connected() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start()
        rig.sockets[0].active, rig.sockets[0].reconnecting = False, True
        await rig.feed.subscribe(_BTC)  # no send, no raise
        assert rig.sockets[0].sent == []
        rig.sockets[0].active, rig.sockets[0].reconnecting = True, False
        rig.now += int(RECONNECT_SECONDS * _S_NS)
        await rig.feed.check(rig.now)

    _run(body)
    assert [r["args"] for r in rig.sockets[0].sent] == [["allLiquidation.BTCUSDT"]]
    assert rig.feed._gaps == {_BTC: _BASE_NS}


def test_a_failed_unsubscribe_then_a_re_add_subscribes_again() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.ack_all()
        rig.sockets[0].fail_sends = True
        await rig.feed.unsubscribe(_BTC)
        rig.sockets[0].fail_sends = False
        await rig.feed.subscribe(_BTC)
        rig.ack(rig.sockets[0].sent[-1], success=False, ret_msg="error:already subscribed")

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}  # the failed unsubscribe
    assert [r["op"] for r in rig.sockets[0].sent] == ["subscribe", "subscribe"]
    assert rig.feed._gaps == {}  # the re-add is confirmed


def test_an_ack_in_flight_does_not_confirm_a_re_add() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        first = rig.sockets[0].sent[-1]
        await rig.feed.unsubscribe(_BTC)
        await rig.feed.subscribe(_BTC)
        rig.ack(first)  # the first subscribe's ack, arriving after the re-add

    _run(body)
    assert set(rig.feed._gaps) == {_BTC}


def test_a_reconnect_forgets_requests_in_flight_and_nothing_follows_shutdown() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)  # its ack never comes: the socket reconnects
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()
        await asyncio.gather(*rig.feed._tasks)
        rig.ack_all()  # only the resubscribe's request waits
        rig.now += ACK_TIMEOUT_NS + 1
        await rig.feed.check(rig.now)
        await rig.feed.disconnect()
        rig.feed._on_reconnected()  # a late callback

    _run(body)
    assert error_ledger.counts() == {}
    assert rig.feed._tasks == set()


def test_an_id_unsubscribed_during_a_resubscribe_is_not_resent() -> None:
    extra = tuple(f"X{n}USDT" for n in range(10))
    rig = _Rig(extra=extra)
    iids = sorted(f"{symbol}-LINEAR.BYBIT" for symbol in rig.precisions)

    async def body() -> None:
        await rig.start(*iids)
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()
        await asyncio.sleep(0)  # the first chunk of ten is out
        await rig.feed.unsubscribe(iids[-1])
        await asyncio.gather(*rig.feed._tasks)

    _run(body)
    subscribed = [a for r in rig.sockets[0].sent if r["op"] == "subscribe" for a in r["args"]]
    assert len(subscribed) == len(iids) - 1


def test_already_subscribed_naming_no_topic_confirms_a_one_topic_request() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.ack(rig.sockets[0].sent[0], success=False, ret_msg="error:already subscribed")

    _run(body)
    assert rig.feed._gaps == {}


def test_a_restart_span_is_not_running_with_its_wire_lag_head() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC)
        rig.ack_all()
        rig.noted.clear()
        rig.feed.note_restart(_BTC, 100 * _S_NS, 110 * _S_NS - 1)
        rig.feed.note_restart("BTCUSDT-SPOT.BYBIT", 100 * _S_NS, 110 * _S_NS - 1)  # no topic

    _run(body)
    assert rig.noted == [
        LiquidationsUnrecoverable(_BTC, "not_running", 100 * _S_NS - WIRE_LAG_NS, 110 * _S_NS - 1)
    ]
    assert rig.feed._unheld_since == {}


def test_a_restarted_id_not_held_yet_stays_a_window_until_its_subscribe_is_acked() -> None:
    rig = _Rig()
    end = _BASE_NS - 30 * _S_NS  # the restart span ended at the first verdict, 30 s ago

    async def body() -> None:
        await rig.start()
        rig.feed.note_restart(_BTC, end - 600 * _S_NS, end)  # its subscribe failed at start
        rig.noted.clear()
        await rig.feed.subscribe(_BTC)  # capture's retry, 30 s later
        rig.now += _S_NS
        rig.ack_all()

    _run(body)
    assert rig.noted == [_window(_BTC, end + 1, rig.now)]


def test_a_restarted_id_never_held_is_written_at_unsubscribe_and_shutdown() -> None:
    rig = _Rig()
    end = _BASE_NS - 30 * _S_NS

    async def body() -> None:
        await rig.start()
        rig.feed.note_restart(_BTC, end - 600 * _S_NS, end)
        rig.feed.note_restart(_ETH, end - 600 * _S_NS, end)
        rig.noted.clear()
        await rig.feed.unsubscribe(_BTC)  # removed from the plan while pending
        rig.now += _S_NS
        await rig.feed.disconnect()

    _run(body)
    assert rig.noted == [
        LiquidationsUnrecoverable(_BTC, "feed_down", end + 1, _BASE_NS),
        LiquidationsUnrecoverable(_ETH, "feed_down", end + 1, _BASE_NS + _S_NS),
    ]
    assert rig.feed._unheld_since == {}


def test_shutdown_writes_a_confirmed_ids_last_wire_lag_and_handles_no_later_frame() -> None:
    rig = _Rig()
    frame = _frame("BTCUSDT")

    async def body() -> None:
        await rig.start(_BTC)
        rig.ack_all()
        rig.noted.clear()
        rig.now += 10 * _S_NS
        await rig.feed.disconnect()
        rig.now = _arrival(frame)
        rig.deliver(frame)  # queued by the Rust client before the close, run after it

    _run(body)
    assert rig.noted == [_window(_BTC, _BASE_NS + 10 * _S_NS, _BASE_NS + 10 * _S_NS)]
    assert rig.ingested == []


def test_already_subscribed_names_a_dated_future_whole() -> None:
    rig = _Rig(extra=("BTCUSDT-26DEC25",))
    dated = "BTCUSDT-26DEC25-LINEAR.BYBIT"

    async def body() -> None:
        await rig.start(_BTC, dated)
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()
        await asyncio.gather(*rig.feed._tasks)
        (batch,) = rig.sockets[0].sent
        rig.ack(
            batch,
            success=False,
            ret_msg="error:already subscribed,topic:allLiquidation.BTCUSDT-26DEC25",
        )

    _run(body)
    assert set(rig.feed._gaps) == {_BTC}  # the dated future confirmed, never the perpetual


def test_already_subscribed_never_confirms_the_id_left_by_an_unsubscribe() -> None:
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC, _ETH)
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()
        await asyncio.gather(*rig.feed._tasks)
        (batch,) = rig.sockets[0].sent
        await rig.feed.unsubscribe(_BTC)  # the request now answers for ETH alone
        rig.ack(
            batch, success=False, ret_msg="error:already subscribed,topic:allLiquidation.BTCUSDT"
        )

    _run(body)
    assert set(rig.feed._gaps) == {_ETH}
    assert (TOPIC, _ETH) not in rig.feed._wire.held()  # resent by the next monitor round


def test_a_refusal_backs_off_only_the_topic_it_names() -> None:
    error_ledger.reset()
    rig = _Rig()

    async def body() -> None:
        await rig.start(_BTC, _ETH)
        rig.sockets[0].sent.clear()
        rig.feed._on_reconnected()
        await asyncio.gather(*rig.feed._tasks)
        (batch,) = rig.sockets[0].sent
        rig.ack(
            batch, success=False, ret_msg="error:handler not found,topic:allLiquidation.ETHUSDT"
        )
        assert rig.feed._wire.held() == frozenset()  # released at once, no task, no pacing
        rig.sockets[0].sent.clear()
        rig.now += _S_NS
        rig.feed._last_connect_ns = rig.now - int(RECONNECT_SECONDS * _S_NS)  # a resend round
        await rig.feed.check(rig.now)

    _run(body)
    assert set(rig.feed._refused) == {_ETH}
    # BTC resent at the next round; ETH still backs off
    assert [r["args"] for r in rig.sockets[0].sent] == [["allLiquidation.BTCUSDT"]]


def test_a_long_connect_outage_is_ledgered_once() -> None:
    error_ledger.reset()
    rig = _Rig(fail_connects=5)

    async def body() -> None:
        await rig.start(_BTC)
        for _ in range(5):
            rig.now += int(RECONNECT_SECONDS * _S_NS)
            await rig.feed.check(rig.now)

    _run(body)
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 2}  # the first failure, then `down`
    assert rig.feed.state() == CONNECTED
    assert rig.feed._open_failures == 0


# -- the client's routing -----------------------------------------------------------------------


class _Ws:
    """A Bybit pyo3 socket stand-in recording the client's calls; `fail` names calls that raise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail: set[str] = set()

    def is_active(self) -> bool:
        return True

    def is_closed(self) -> bool:
        return False

    async def close(self) -> None:
        self.calls.append(("close", ""))

    def __getattr__(self, name: str) -> Callable[..., Awaitable[None]]:
        async def record(iid: Any, *_rest: Any) -> None:
            self.calls.append((name, str(iid)))
            if name in self.fail:
                raise ConnectionError(f"{name} failed")

        return record


class _Feed:
    """Records the client's calls on its liquidation feed; `fail` makes `subscribe` raise."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail = False

    async def connect(self, _loop: object, instruments: list) -> None:
        self.calls.append(("connect", str(len(instruments))))

    async def disconnect(self) -> None:
        self.calls.append(("disconnect", ""))

    async def subscribe(self, iid: str) -> None:
        self.calls.append(("subscribe", iid))
        if self.fail:
            raise ConnectionError("send failed")

    async def unsubscribe(self, iid: str) -> None:
        self.calls.append(("unsubscribe", iid))

    def state(self) -> str:
        return CONNECTED

    def held_ids(self) -> frozenset[str]:
        return frozenset({_BTC})

    def note_restart(self, iid: str, from_ns: int, to_ns: int) -> None:
        self.calls.append(("note_restart", f"{iid} {from_ns} {to_ns}"))


def _client(feed: _Feed) -> tuple[BybitClient, _Ws]:
    client = BybitClient(lambda *_: None, ledger=error_ledger.record, liquidations=feed)  # type: ignore[arg-type]
    client._wire = WireChannels(1e6)
    linear = _Ws()
    client._ws_linear, client._ws_spot = linear, _Ws()
    return client, linear


def test_only_a_linear_id_gets_a_liquidation_topic() -> None:
    feed = _Feed()
    client, _ = _client(feed)

    async def body() -> None:
        await client.subscribe(_BTC)
        await client.subscribe("BTCUSDT-SPOT.BYBIT")
        await client.subscribe("BTCUSDT-SPOT.BYBIT")
        await client.unsubscribe(_BTC)

    asyncio.run(body())
    assert feed.calls == [("subscribe", _BTC), ("unsubscribe", _BTC)]
    assert client._liquidation_refused == {"BTCUSDT-SPOT.BYBIT"}


def test_a_failed_liquidation_subscribe_is_ledgered_as_a_channel_retry() -> None:
    error_ledger.reset()
    feed = _Feed()
    feed.fail = True
    client, linear = _client(feed)
    with pytest.raises(ChannelRetry):
        asyncio.run(client.subscribe(_BTC))
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}
    assert ("subscribe_orderbook", _BTC) in linear.calls  # the required channels went first


def test_a_raised_main_release_still_releases_the_liquidation_topic() -> None:
    feed = _Feed()
    client, linear = _client(feed)

    async def body() -> None:
        await client.subscribe(_BTC)
        linear.fail.add("unsubscribe_trades")
        with pytest.raises(ConnectionError):
            await client.unsubscribe(_BTC)

    asyncio.run(body())
    assert ("unsubscribe", _BTC) in feed.calls


def test_the_client_reports_the_feed_state_hands_restarts_and_closes_it() -> None:
    feed = _Feed()
    client, _ = _client(feed)
    assert client.liquidation_state() == CONNECTED
    client.note_liquidation_restart(_BTC, 1, 2)
    assert ("note_restart", f"{_BTC} 1 2") in feed.calls
    asyncio.run(client.disconnect())
    assert ("disconnect", "") in feed.calls
    assert set(client.feed_states()) == {LINEAR_FEED, SPOT_FEED}


def test_a_client_without_the_feed_reports_none() -> None:
    client = BybitClient(lambda *_: None, ledger=error_ledger.record)
    assert client.liquidation_state() is None
    client.note_liquidation_restart(_BTC, 1, 2)  # no feed: nothing to write it
