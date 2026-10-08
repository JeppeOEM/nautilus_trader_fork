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
The liquidation bridge (Story 33.14) end to end: a real `DataEngine` with the client registered,
and an `Actor` subscribed exactly as `LiquidationCascadeStrategy` subscribes
(`DataType(Liquidation)`, `client_id=LIQUIDATIONS`, its instrument), so a delivered row is one the
strategy's `on_data` would see. The Redis tests publish on `liquidations:raw` of the Redis at
`REDIS_URL` (default `redis://127.0.0.1:6379`) and are skipped, saying so, when none answers. They
touch only the bridge's own connection (found by its client name), never another client of a
shared Redis; the half-open socket goes through a local relay the test black-holes.
"""

import asyncio
import json
import os
import socket
import time
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any
from urllib.parse import urlparse

import pytest
import redis.asyncio as aioredis
from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from bots.infrastructure.liquidation_data_client import BRIDGE_CLIENT_NAME
from bots.infrastructure.liquidation_data_client import CONNECTION_SITE
from bots.infrastructure.liquidation_data_client import ENTRY_SITE
from bots.infrastructure.liquidation_data_client import HEALTH_IDLE_S
from bots.infrastructure.liquidation_data_client import LIQUIDATIONS_CHANNEL
from bots.infrastructure.liquidation_data_client import SUBSCRIBE_SITE
from bots.infrastructure.liquidation_data_client import LiquidationDataClient
from bots.infrastructure.liquidation_data_client import LiquidationDataClientConfig
from bots.infrastructure.liquidation_data_client import LiquidationFeedStatus
from bots.infrastructure.liquidation_data_client import liquidation_client_factory
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.actor import Actor
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.config import ActorConfig
from nautilus_trader.core.data import Data
from nautilus_trader.data.engine import DataEngine
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.portfolio.portfolio import Portfolio


_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
BTC = "BTCUSDT-LINEAR.BYBIT"
ETH = "ETHUSDT-LINEAR.BYBIT"
_WAIT_S = 5.0


class _Sink(Actor):
    """Receives what a strategy subscribed to one instrument's liquidations would."""

    def __init__(self, name: str) -> None:
        super().__init__(ActorConfig(component_id=name))
        self.rows: list[Data] = []

    def on_data(self, data: Data) -> None:
        self.rows.append(data)


class _Node:
    """The one engine, client and subscribed sink of a test (all real Nautilus objects)."""

    def __init__(self, redis_url: str = _REDIS_URL, health_idle_s: float = HEALTH_IDLE_S) -> None:
        loop = asyncio.get_running_loop()
        clock = LiveClock()
        bus = MessageBus(TraderId("TEST-001"), clock)
        cache = Cache()
        self.status = LiquidationFeedStatus()
        config = LiquidationDataClientConfig(redis_url=redis_url, health_idle_s=health_idle_s)
        factory = liquidation_client_factory(self.status)
        client = factory.create(loop, LIQUIDATION_CLIENT_ID, config, bus, cache, clock)
        assert isinstance(client, LiquidationDataClient)
        self.client = client
        self.engine = DataEngine(bus, cache, clock)
        self.engine.register_client(client)
        self.engine.start()
        self._parts = (bus, cache, clock)
        self._portfolio = Portfolio(bus, cache, clock)

    def sink(self, instrument_id: str) -> _Sink:
        bus, cache, clock = self._parts
        sink = _Sink(f"SINK-{instrument_id}")
        sink.register_base(self._portfolio, bus, cache, clock)
        sink.start()
        sink.subscribe_data(
            DataType(Liquidation),
            client_id=ClientId(LIQUIDATION_CLIENT_ID),
            instrument_id=InstrumentId.from_str(instrument_id),
        )
        return sink


def _row(instrument_id: str, event: str) -> dict[str, object]:
    row = Liquidation(
        instrument_id=InstrumentId.from_str(instrument_id),
        side=LiquidatedSide.LONG,
        size_units=1_500,
        price_units=600_005,
        price_precision=1,
        size_precision=3,
        venue_event_id=event,
        ts_event=1_000,
        ts_init=2_000,
    )
    return Liquidation.to_dict(row)


async def _until(condition: Callable[[], bool], what: str) -> None:
    for _ in range(int(_WAIT_S / 0.02)):
        if condition():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def _run(test: Callable[[], Coroutine[Any, Any, None]]) -> None:
    error_ledger.reset()
    asyncio.run(test())


def _redis_or_skip() -> None:
    url = urlparse(_REDIS_URL)
    try:
        socket.create_connection((url.hostname or "127.0.0.1", url.port or 6379), 1).close()
    except OSError as exc:
        pytest.skip(f"no Redis at REDIS_URL={_REDIS_URL} ({exc}): the bridge needs a live channel")


async def _publish(payload: Any) -> None:
    async with aioredis.Redis.from_url(_REDIS_URL) as client:
        await client.publish(LIQUIDATIONS_CHANNEL, json.dumps(payload))


async def _publish_raw(payload: bytes) -> None:
    async with aioredis.Redis.from_url(_REDIS_URL) as client:
        await client.publish(LIQUIDATIONS_CHANNEL, payload)


async def _kill_the_bridge() -> None:
    """Kill the bridge's own connection(s) by client name: no other client of the Redis."""
    async with aioredis.Redis.from_url(_REDIS_URL, decode_responses=True) as admin:
        ids = [c["id"] for c in await admin.client_list() if c.get("name") == BRIDGE_CLIENT_NAME]
        assert ids, f"no Redis client named {BRIDGE_CLIENT_NAME}"
        for client_id in ids:
            await admin.execute_command("CLIENT", "KILL", "ID", client_id)


class _Relay:
    """
    A TCP relay to the Redis at `REDIS_URL` that can black-hole the connections open now: their
    bytes are read and dropped both ways, nothing is closed (a half-open socket, as a peer gone
    without a FIN or RST), while a connection made afterwards relays normally.
    """

    def __init__(self) -> None:
        self._open: list[list[bool]] = []  # one mutable `frozen` flag per relayed connection
        self._writers: list[asyncio.StreamWriter] = []
        self._server: asyncio.Server | None = None

    async def start(self) -> str:
        self._server = await asyncio.start_server(self._relay, "127.0.0.1", 0)
        return f"redis://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def _relay(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        url = urlparse(_REDIS_URL)
        up_reader, up_writer = await asyncio.open_connection(url.hostname, url.port or 6379)
        self._writers += [writer, up_writer]
        frozen = [False]
        self._open.append(frozen)
        await asyncio.gather(
            self._pipe(reader, up_writer, frozen),
            self._pipe(up_reader, writer, frozen),
            return_exceptions=True,
        )

    @staticmethod
    async def _pipe(
        source: asyncio.StreamReader, sink: asyncio.StreamWriter, frozen: list[bool]
    ) -> None:
        while data := await source.read(65_536):
            if not frozen[0]:
                sink.write(data)
                await sink.drain()

    def black_hole(self) -> None:
        for frozen in self._open:
            frozen[0] = True

    async def close(self) -> None:
        assert self._server is not None
        self._server.close()
        for writer in self._writers:
            writer.close()
        await self._server.wait_closed()


# --- delivery through the engine (no Redis) ------------------------------------------------------


def test_a_subscribed_rows_instrument_reaches_the_strategys_subscription_and_no_other() -> None:
    async def scenario() -> None:
        node = _Node()
        sink = node.sink(BTC)
        await _until(lambda: BTC in node.client.served, "the subscribe command")
        delivered = node.client.ingest(json.dumps([_row(BTC, "a"), _row(ETH, "b")]))
        assert delivered == 1
        assert [(str(r.instrument_id), r.venue_event_id) for r in sink.rows] == [(BTC, "a")]

    _run(scenario)


def test_each_bots_instrument_is_served_and_an_unsubscribe_removes_only_its_own() -> None:
    async def scenario() -> None:
        node = _Node()
        btc, eth = node.sink(BTC), node.sink(ETH)
        await _until(lambda: node.client.served == {BTC, ETH}, "both subscribe commands")
        node.client.ingest(json.dumps([_row(BTC, "a"), _row(ETH, "b")]))
        assert [r.venue_event_id for r in btc.rows] == ["a"]
        assert [r.venue_event_id for r in eth.rows] == ["b"]
        eth.unsubscribe_data(
            DataType(Liquidation),
            client_id=ClientId(LIQUIDATION_CLIENT_ID),
            instrument_id=InstrumentId.from_str(ETH),
        )
        await _until(lambda: node.client.served == {BTC}, "the unsubscribe command")

    _run(scenario)


def test_nothing_is_delivered_before_the_data_type_is_subscribed() -> None:
    async def scenario() -> None:
        node = _Node()
        assert node.client.ingest(json.dumps([_row(BTC, "a")])) == 0

    _run(scenario)


def test_a_bad_entry_is_ledgered_and_its_sibling_still_delivered() -> None:
    async def scenario() -> None:
        node = _Node()
        sink = node.sink(BTC)
        await _until(lambda: BTC in node.client.served, "the subscribe command")
        assert node.client.ingest(json.dumps([5, _row(BTC, "a")])) == 1
        assert [r.venue_event_id for r in sink.rows] == ["a"]
        assert error_ledger.counts() == {ENTRY_SITE: 1}

    _run(scenario)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("size_units", 1.5),  # `int()` would truncate it to 1
        ("price_units", "600005"),  # `int()` would read the text
        ("size_units", True),
        ("size_units", 0),
        ("price_units", -600_005),
        ("price_precision", -1),
        ("ts_init", 2_000.0),
    ],
)
def test_an_entry_of_a_non_integer_or_out_of_range_field_is_ledgered_not_delivered(
    field: str, value: object
) -> None:
    async def scenario() -> None:
        node = _Node()
        sink = node.sink(BTC)
        await _until(lambda: BTC in node.client.served, "the subscribe command")
        bad = {**_row(BTC, "bad"), field: value}
        assert node.client.ingest(json.dumps([bad, _row(BTC, "a")])) == 1
        assert [r.venue_event_id for r in sink.rows] == ["a"]
        assert error_ledger.counts() == {ENTRY_SITE: 1}
        assert "'bad'" in error_ledger.last_details()[ENTRY_SITE]

    _run(scenario)


class _OtherData(Data):
    """A custom data type the bridge does not serve."""


class _FailingSink(_Sink):
    """A subscriber whose handler raises on the row `boom`, as a strategy's `on_data` may."""

    def on_data(self, data: Data) -> None:
        if isinstance(data, Liquidation) and data.venue_event_id == "boom":
            raise RuntimeError("the handler raised")
        super().on_data(data)


def test_a_row_a_handler_raises_on_is_ledgered_and_its_later_siblings_delivered() -> None:
    async def scenario() -> None:
        node = _Node()
        bus, cache, clock = node._parts
        sink = _FailingSink("SINK-FAILING")
        sink.register_base(node._portfolio, bus, cache, clock)
        sink.start()
        sink.subscribe_data(
            DataType(Liquidation),
            client_id=ClientId(LIQUIDATION_CLIENT_ID),
            instrument_id=InstrumentId.from_str(BTC),
        )
        await _until(lambda: BTC in node.client.served, "the subscribe command")
        rows = [_row(BTC, "a"), _row(BTC, "boom"), _row(BTC, "b")]
        assert node.client.ingest(json.dumps(rows)) == 2
        assert [r.venue_event_id for r in sink.rows] == ["a", "b"]
        assert error_ledger.counts() == {ENTRY_SITE: 1}
        assert "boom" in error_ledger.last_details()[ENTRY_SITE]

    _run(scenario)


def test_a_message_of_many_bad_entries_itemises_three_then_one_summary() -> None:
    async def scenario() -> None:
        node = _Node()
        node.client.ingest(json.dumps([{"nope": i} for i in range(5)]))
        assert error_ledger.counts() == {ENTRY_SITE: 4}
        assert "5 liquidations:raw entries" in error_ledger.last_details()[ENTRY_SITE]

    _run(scenario)


def test_a_payload_that_is_not_an_array_is_one_ledgered_entry() -> None:
    async def scenario() -> None:
        node = _Node()
        assert node.client.ingest('{"instrument_id": "x"}') == 0
        assert node.client.ingest("not json") == 0
        assert error_ledger.counts() == {ENTRY_SITE: 2}

    _run(scenario)


# --- over Redis ----------------------------------------------------------------------------------


def test_rows_published_on_the_channel_are_delivered_and_the_status_reads_connected() -> None:
    _redis_or_skip()

    async def scenario() -> None:
        node = _Node()
        sink = node.sink(BTC)
        await node.client._connect()
        await _until(lambda: node.status.connected, "the channel subscription")
        assert node.status.disconnected_since_ns is None
        await _publish([_row(BTC, "a"), 7, _row(ETH, "b")])
        await _until(lambda: len(sink.rows) == 1, "the delivered row")
        assert [r.venue_event_id for r in sink.rows] == ["a"]
        assert error_ledger.counts() == {ENTRY_SITE: 1}
        assert node.status.last_data_ns > 0
        await node.client._disconnect()

    _run(scenario)


def test_a_dropped_connection_is_ledgered_read_down_and_reconnected() -> None:
    _redis_or_skip()

    async def scenario() -> None:
        node = _Node()
        sink = node.sink(BTC)
        await node.client._connect()
        await _until(lambda: node.status.connected, "the channel subscription")
        await _kill_the_bridge()
        await _until(lambda: not node.status.connected, "the drop")
        assert node.status.disconnected_since_ns is not None
        assert error_ledger.counts() == {CONNECTION_SITE: 1}
        await _until(lambda: node.status.connected, "the reconnect")
        assert node.status.disconnected_since_ns is None
        await _publish([_row(BTC, "after")])
        await _until(lambda: len(sink.rows) == 1, "a row after the reconnect")
        await node.client._disconnect()
        assert not node.status.connected

    _run(scenario)


def test_a_redis_that_never_answers_reads_down_from_the_first_attempt() -> None:
    async def scenario() -> None:
        node = _Node()
        node.client._redis_url = "redis://127.0.0.1:1"  # nothing listens on port 1
        await node.client._connect()
        await _until(lambda: CONNECTION_SITE in error_ledger.counts(), "the failed connect")
        assert not node.status.connected
        assert node.status.disconnected_since_ns is not None
        await node.client._disconnect()

    _run(scenario)


def test_a_half_open_socket_is_detected_within_twice_the_health_idle_time() -> None:
    _redis_or_skip()
    idle_s = 0.2

    async def scenario() -> None:
        relay = _Relay()
        node = _Node(redis_url=await relay.start(), health_idle_s=idle_s)
        sink = node.sink(BTC)
        await node.client._connect()
        await _until(lambda: node.status.connected, "the channel subscription")
        await asyncio.sleep(3 * idle_s)  # idle probes answered: still connected
        assert node.status.connected
        relay.black_hole()
        began = time.monotonic()
        await _until(lambda: not node.status.connected, "the dead socket's detection")
        assert time.monotonic() - began < 2 * idle_s + 0.5
        assert error_ledger.counts() == {CONNECTION_SITE: 1}
        assert "no PONG" in error_ledger.last_details()[CONNECTION_SITE]
        await _until(lambda: node.status.connected, "the reconnect over a fresh connection")
        await _publish([_row(BTC, "after")])
        await _until(lambda: len(sink.rows) == 1, "a row after the reconnect")
        await node.client._disconnect()
        await relay.close()

    _run(scenario)


def test_a_non_utf8_payload_is_one_bad_entry_and_the_subscription_holds() -> None:
    _redis_or_skip()

    async def scenario() -> None:
        node = _Node()
        sink = node.sink(BTC)
        await node.client._connect()
        await _until(lambda: node.status.connected, "the channel subscription")
        await _publish_raw(b"\xff\xfe[not utf-8")
        await _publish([_row(BTC, "a")])
        await _until(lambda: len(sink.rows) == 1, "the row after the bad payload")
        assert error_ledger.counts() == {ENTRY_SITE: 1}
        assert node.status.connected
        await node.client._disconnect()

    _run(scenario)


def test_a_message_the_engine_fails_on_is_an_entry_failure_not_a_drop() -> None:
    _redis_or_skip()

    async def scenario() -> None:
        node = _Node()
        sink = node.sink(BTC)
        ingest = node.client.ingest
        failures = [RuntimeError("the engine raised")]

        def failing_once(payload: object) -> int:
            if failures:
                raise failures.pop()
            return ingest(payload)

        node.client.ingest = failing_once  # type: ignore[method-assign]
        await node.client._connect()
        await _until(lambda: node.status.connected, "the channel subscription")
        await _publish([_row(BTC, "lost")])
        await _publish([_row(BTC, "a")])
        await _until(lambda: len(sink.rows) == 1, "the next message's row")
        assert [r.venue_event_id for r in sink.rows] == ["a"]
        assert error_ledger.counts() == {ENTRY_SITE: 1}
        assert "not delivered" in error_ledger.last_details()[ENTRY_SITE]
        assert node.status.connected
        await node.client._disconnect()

    _run(scenario)


def test_a_subscription_of_another_data_type_is_ledgered_and_serves_nothing() -> None:
    async def scenario() -> None:
        node = _Node()
        sink = _Sink("SINK-OTHER")
        bus, cache, clock = node._parts
        sink.register_base(node._portfolio, bus, cache, clock)
        sink.start()
        sink.subscribe_data(
            DataType(_OtherData),
            client_id=ClientId(LIQUIDATION_CLIENT_ID),
            instrument_id=InstrumentId.from_str(BTC),
        )
        await _until(lambda: SUBSCRIBE_SITE in error_ledger.counts(), "the refused subscription")
        assert error_ledger.counts() == {SUBSCRIBE_SITE: 1}
        assert node.client.served == frozenset()

    _run(scenario)


def test_a_subscription_without_an_instrument_is_ledgered_and_serves_nothing() -> None:
    async def scenario() -> None:
        node = _Node()
        sink = _Sink("SINK-ANY")
        bus, cache, clock = node._parts
        sink.register_base(node._portfolio, bus, cache, clock)
        sink.start()
        sink.subscribe_data(DataType(Liquidation), client_id=ClientId(LIQUIDATION_CLIENT_ID))
        await _until(lambda: SUBSCRIBE_SITE in error_ledger.counts(), "the refused subscription")
        assert error_ledger.counts() == {SUBSCRIBE_SITE: 1}
        assert node.client.served == frozenset()

    _run(scenario)
