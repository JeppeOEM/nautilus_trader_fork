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
The liquidation bridge (Story 33.14): a Nautilus `LiveMarketDataClient` that feeds the Bybit
collector's `liquidations:raw` Redis channel (`capture/infrastructure/redis_stream.py`, one JSON
array of `kernel.liquidation.Liquidation.to_dict` per frame) into the paper node, so a strategy
subscribed with `subscribe_data(DataType(Liquidation), client_id=ClientId("LIQUIDATIONS"),
instrument_id=...)` -- `LiquidationCascadeStrategy` -- receives the rows the collector archived.
Besides `nautilus_host.py` the one module of `platform/` importing `nautilus_trader.live`
(`tests/test_boundaries.py`'s `TRADING_NODE_HOSTS`): a custom data client is a `LiveDataClient`
subclass by Nautilus's own design.

The node reaches it only through public API: `TradingNode.add_data_client_factory("LIQUIDATIONS",
factory)` and a `LiquidationDataClientConfig` under that key in `TradingNodeConfig.data_clients`
(`nautilus_host.build_node`, only for a fleet with a cascade bot). Rows reach the `DataEngine`
through `_handle_data(CustomData(DataType(Liquidation), row))`, which publishes them on the
custom-data topic of their type and `instrument_id`. No engine or node private attribute is read.

**Delivery.** Each entry is decoded by `Liquidation.from_dict`, the one parser of the channel, after
`checked_entry` refuses one whose integer fields are not JSON integers (`from_dict`'s `int()` would
truncate a `1.5` or read a `"12"`, DATA-04) or whose size or price is not positive (the detector
refuses a non-positive notional). An entry that does not decode is recorded at `bots.liquidation_feed.entry` (DATA-07: the first
`_ENTRIES_SHOWN` of one message one line each, then one summary line naming the total, the ranking
engine's precedent) and its siblings are still delivered. A message that fails past decoding (the
engine or a strategy's handler raising out of `_handle_data`) is recorded at the same site, row by
row, and the message's other rows are still delivered: one row's failure is never its siblings', nor
a connection drop. Payloads arrive as bytes and are decoded by
`json.loads`, so a non-UTF-8 payload is one bad entry, not a decode error inside redis-py's reader
(which would read as a dropped connection). A row is delivered only for an instrument
a `Liquidation` subscribe command named and no unsubscribe has removed since (`SubscribeData` carries the
instrument in its `DataType`'s metadata, so the engine forwards one command per instrument, not one
per type): a row of an instrument no bot trades never reaches the engine. A subscribe command
without an instrument, or for another data type, serves nothing and is recorded at
`bots.liquidation_feed.subscribe`.

**Connection.** `_connect` starts one feed task that subscribes the channel with `redis.asyncio`
(redis-py's own silent reconnect disabled, `Retry(NoBackoff(), 0)`, so every drop is seen here) and
reconnects with a doubling backoff from `RECONNECT_MIN_S` to `RECONNECT_MAX_S`. The connection is
named `BRIDGE_CLIENT_NAME` (Redis `CLIENT LIST`), with `archive.infrastructure.redis_bus`'s socket
settings: a `CONNECT_TIMEOUT_S` connect timeout and TCP keepalive. A half-open socket (the peer gone
without a FIN or RST: nothing closes, nothing answers) is caught by the health probe of
`RedisControlChannel`: after `health_idle_s` (default `HEALTH_IDLE_S`) without traffic a PING goes
out, and a second idle window with no PONG raises, so a dead socket is detected within
`2 * health_idle_s` (60 s by default) instead of blocking the read forever while the status still
reads connected. Every drop, failed connect or failed probe is an ERROR recorded at
`bots.liquidation_feed.connection`. The shared `LiquidationFeedStatus` (one per node, made by the
host and handed to the factory and to the cascade bots' cache readers) holds the connection state:
while the feed is down the bots' reported `last_data_ns` is capped at `disconnected_since_ns`, so
they read stale after `DATA_STALE_NS`. A quiet market (no liquidation for an hour) changes nothing
there: only the connection can make the feed read dead. Nor can a stopped Bybit collector: with
Redis up the channel is simply silent, which reads as a quiet market here; the collector's own
status and watchdog are that alarm (`bots/README.md`).

Known limit: Redis pub/sub keeps nothing, so a liquidation published while the bridge is down (a
reconnect, a restart) is never delivered; the collector archived it and the bot's catalog replay
(`bots.signal_replay`) holds it, so `verification.bot_parity` reports it `replay_only`. Upgrade
path: a Redis stream (`XADD`/`XREAD` from the last id) in place of the channel.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from collections.abc import Iterator
from dataclasses import dataclass

import redis.asyncio as aioredis
from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import Liquidation
from observability import error_ledger
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock
from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.config import LiveDataClientConfig
from nautilus_trader.data.messages import SubscribeData
from nautilus_trader.data.messages import UnsubscribeData
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.live.factories import LiveDataClientFactory
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.data import DataType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import Venue


LIQUIDATIONS_CHANNEL = "liquidations:raw"
ENTRY_SITE = "bots.liquidation_feed.entry"
CONNECTION_SITE = "bots.liquidation_feed.connection"
SUBSCRIBE_SITE = "bots.liquidation_feed.subscribe"
RECONNECT_MIN_S = 1.0
RECONNECT_MAX_S = 30.0
# The bridge's Redis connection name, so an operator (or a test) can find exactly it.
BRIDGE_CLIENT_NAME = "liquidation-bridge"
CONNECT_TIMEOUT_S = 1.0
# The idle time before a PING; a dead socket is detected within twice this (the module docstring).
HEALTH_IDLE_S = 30.0
# A message's bad entries itemised on the ledger (each repr cut at `_REPR_MAX` chars), then one
# summary line: a payload change must not write one line per entry of every message.
_ENTRIES_SHOWN = 3
_REPR_MAX = 200
# A channel entry's fields that must be JSON integers, and those of them that must be positive.
_INT_FIELDS = (
    "size_units",
    "price_units",
    "price_precision",
    "size_precision",
    "ts_event",
    "ts_init",
)
_POSITIVE_FIELDS = ("size_units", "price_units")


class LiquidationDataClientConfig(LiveDataClientConfig, frozen=True):
    """
    The bridge's config: `redis_url` is the bots' own `REDIS_URL` (the collector publishes on the
    same Redis); `health_idle_s` the health probe's idle time (a dead socket is detected within
    twice it).
    """

    redis_url: str = "redis://127.0.0.1:6379"
    health_idle_s: float = HEALTH_IDLE_S


@dataclass
class LiquidationFeedStatus:
    """
    The bridge's connection state, shared by the client and the cascade bots' cache readers.

    Invariant: `disconnected_since_ns` is None exactly while the channel is subscribed
    (`connected`), else the first moment the feed was seen down since it last was up (the start of
    the first connect attempt before any). Written only by `LiquidationDataClient`.
    """

    connected: bool = False
    disconnected_since_ns: int | None = None
    last_data_ns: int = 0


def _short_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _REPR_MAX else f"{text[:_REPR_MAX]}... ({len(text)} chars)"


def checked_entry(entry: object) -> object:
    """
    Return `entry` unchanged when its integer fields are JSON integers, the precisions and stamps
    are not negative and the size and price are positive; else raise `ValueError` naming the field.
    A missing key or a non-object entry is left to `Liquidation.from_dict` to refuse.
    """
    if not isinstance(entry, dict):
        return entry
    for name in _INT_FIELDS:
        if name not in entry:
            continue
        value = entry[name]
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"`{name}` must be an integer, got {value!r}")
        if value <= 0 if name in _POSITIVE_FIELDS else value < 0:
            raise ValueError(f"`{name}` out of range: {value!r}")
    return entry


def decode_message(payload: object) -> Iterator[Liquidation | tuple[str, Exception]]:
    """
    Yield each entry of one `liquidations:raw` message as a `Liquidation`, or `(detail, cause)`
    for one that does not decode. A payload that is not a JSON array is one bad entry.
    """
    try:
        batch = json.loads(payload) if isinstance(payload, str | bytes) else payload
    except ValueError as exc:
        yield f"{LIQUIDATIONS_CHANNEL} payload is not JSON: {_short_repr(payload)}", exc
        return
    if not isinstance(batch, list):
        detail = f"{LIQUIDATIONS_CHANNEL} payload is a {type(batch).__name__}, not a list"
        yield detail, TypeError(detail)
        return
    for entry in batch:
        try:
            yield Liquidation.from_dict(checked_entry(entry))  # type: ignore[arg-type]
        except Exception as exc:  # any malformed entry: a missing key, a wrong type, a bad id
            yield f"malformed {LIQUIDATIONS_CHANNEL} entry SKIPPED: {_short_repr(entry)}", exc


class LiquidationDataClient(LiveMarketDataClient):
    """
    Bridges `liquidations:raw` into the node (the module docstring).

    Invariant: a row reaches `_handle_data` only for an id subscribed through `_subscribe` and not
    since removed by `_unsubscribe`; every undecodable entry, undeliverable message, instrument-less
    subscription and connection failure is on the error ledger; `status` is the connection state
    the bots' heartbeat reads, never left connected past a dead socket's detection bound.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        config: LiquidationDataClientConfig,
        status: LiquidationFeedStatus,
    ) -> None:
        super().__init__(
            loop=loop,
            client_id=ClientId(LIQUIDATION_CLIENT_ID),
            # A venue of its own: with None the engine would make this client its default route
            # for every command no venue client claims.
            venue=Venue(LIQUIDATION_CLIENT_ID),
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=InstrumentProvider(),
            config=config,
        )
        self._redis_url = config.redis_url
        self._health_idle_s = config.health_idle_s
        self._served: frozenset[str] = frozenset()
        self._data_type = DataType(Liquidation)
        self.status = status
        self._feed_task: asyncio.Task | None = None

    @property
    def served(self) -> frozenset[str]:
        """The instrument ids whose rows are delivered now."""
        return self._served

    # --- subscriptions -------------------------------------------------------------------------

    async def _subscribe(self, command: SubscribeData) -> None:
        if command.data_type.type is not Liquidation:
            # Served `Liquidation` rows, its subscriber would receive nothing it can read.
            error_ledger.record(
                SUBSCRIBE_SITE,
                f"{command.data_type} subscribed on {LIQUIDATION_CLIENT_ID} by "
                f"{command.client_id}: only Liquidation is served, nothing served",
            )
            return
        if command.instrument_id is None:
            # A type-wide subscription would serve every instrument: refused, never guessed.
            error_ledger.record(
                SUBSCRIBE_SITE,
                f"{command.data_type} subscribed without an instrument by {command.client_id}: "
                "nothing served",
            )
            return
        self._served = self._served | {command.instrument_id.value}

    async def _unsubscribe(self, command: UnsubscribeData) -> None:
        if command.instrument_id is not None:
            self._served = self._served - {command.instrument_id.value}

    # --- connection ----------------------------------------------------------------------------

    async def _connect(self) -> None:
        if self._feed_task is None or self._feed_task.done():
            self._mark_down()
            self._feed_task = self._loop.create_task(self._feed(), name="liquidation_feed")

    async def _disconnect(self) -> None:
        task, self._feed_task = self._feed_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._mark_down()

    def _mark_down(self) -> None:
        self.status.connected = False
        if self.status.disconnected_since_ns is None:
            self.status.disconnected_since_ns = self._clock.timestamp_ns()

    def _mark_up(self) -> None:
        self.status.connected = True
        self.status.disconnected_since_ns = None

    async def _feed(self) -> None:
        """Hold the subscription for the client's life: reconnect with backoff, ledger each drop."""
        delay = RECONNECT_MIN_S
        while True:
            try:
                await self._listen()
                raise ConnectionError(f"the {LIQUIDATIONS_CHANNEL} subscription ended")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # every connection failure is reconnected, never fatal
                if self.status.connected:
                    delay = RECONNECT_MIN_S
                self._mark_down()
                detail = f"{LIQUIDATIONS_CHANNEL} feed down ({exc}), reconnecting in {delay:.0f} s"
                self._log.error(f"{detail}: {exc!r}")
                error_ledger.record(CONNECTION_SITE, detail, exc)
            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_S)

    async def _listen(self) -> None:
        """Subscribe and deliver until the connection fails (raises)."""
        client = aioredis.Redis.from_url(
            self._redis_url,
            retry=Retry(NoBackoff(), 0),
            socket_connect_timeout=CONNECT_TIMEOUT_S,
            socket_keepalive=True,
            client_name=BRIDGE_CLIENT_NAME,
        )
        async with client:
            pubsub = client.pubsub()
            try:
                await pubsub.subscribe(LIQUIDATIONS_CHANNEL)
                self._mark_up()
                self._log.info(f"Subscribed {LIQUIDATIONS_CHANNEL}")
                async for payload in self._messages(pubsub):
                    self._deliver(payload)
            finally:
                await pubsub.aclose()

    async def _messages(self, pubsub: aioredis.client.PubSub) -> AsyncIterator[bytes]:
        """
        Yield each published payload; raise once the socket is presumed dead: an idle window
        (`health_idle_s`) sends a PING and a second one without any traffic raises.
        """
        awaiting_pong = False
        while True:
            message = await pubsub.get_message(timeout=self._health_idle_s)
            if message is None:
                if awaiting_pong:
                    raise ConnectionError(
                        f"{LIQUIDATIONS_CHANNEL}: no PONG within {self._health_idle_s:g} s, "
                        "connection presumed dead"
                    )
                await pubsub.ping()
                awaiting_pong = True
                continue
            awaiting_pong = False  # any traffic proves the connection alive
            if message["type"] == "message":
                yield message["data"]

    def _deliver(self, payload: bytes) -> None:
        """
        `ingest` one message; a failure past its rows (`ingest` itself) is that message's, ledgered,
        never the connection's.
        """
        try:
            self.ingest(payload)
        except Exception as exc:  # the engine's or a handler's: this message alone is lost
            detail = f"{LIQUIDATIONS_CHANNEL} message not delivered: {_short_repr(payload)}"
            self._log.error(f"{detail}: {exc!r}")
            error_ledger.record(ENTRY_SITE, detail, exc)

    # --- delivery ------------------------------------------------------------------------------

    def ingest(self, payload: object) -> int:
        """
        Deliver one message's rows of the served instruments; return how many were delivered.
        Undecodable entries and rows the engine or a handler raised on are ledgered (the module
        docstring), never the rest of the message.
        """
        delivered = problems = 0
        for item in decode_message(payload):
            if isinstance(item, Liquidation):
                if item.instrument_id.value in self._served:
                    delivered += self._deliver_row(item)
                continue
            problems += 1
            if problems <= _ENTRIES_SHOWN:
                error_ledger.record(ENTRY_SITE, *item)
        if problems > _ENTRIES_SHOWN:
            error_ledger.record(
                ENTRY_SITE,
                f"{problems} {LIQUIDATIONS_CHANNEL} entries of one message were malformed, "
                f"{problems - _ENTRIES_SHOWN} beyond the first {_ENTRIES_SHOWN} not itemised",
            )
        return delivered

    def _deliver_row(self, row: Liquidation) -> int:
        """Hand one row to the engine; 1 when delivered, else 0 with the failure ledgered."""
        self.status.last_data_ns = self._clock.timestamp_ns()  # the feed carried it either way
        try:
            self._handle_data(CustomData(self._data_type, row))
        except Exception as exc:  # the engine's or a handler's: this row alone is lost
            detail = f"{LIQUIDATIONS_CHANNEL} row {row!r} not delivered"
            self._log.error(f"{detail}: {exc!r}")
            error_ledger.record(ENTRY_SITE, detail, exc)
            return 0
        return 1


def liquidation_client_factory(status: LiquidationFeedStatus) -> type[LiveDataClientFactory]:
    """
    Return a `LiveDataClientFactory` whose clients share `status` (the host hands the same object
    to the cascade bots' cache readers): a closure, so no registry or node internal is needed.
    """

    class LiquidationDataClientFactory(LiveDataClientFactory):
        @staticmethod
        def create(
            loop: asyncio.AbstractEventLoop,
            name: str,
            config: LiveDataClientConfig,
            msgbus: MessageBus,
            cache: Cache,
            clock: LiveClock,
        ) -> LiquidationDataClient:
            if not isinstance(config, LiquidationDataClientConfig):
                raise TypeError(f"{name}: needs a LiquidationDataClientConfig, got {config!r}")
            return LiquidationDataClient(loop, msgbus, cache, clock, config, status)

    return LiquidationDataClientFactory
