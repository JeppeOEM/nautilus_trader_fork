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
Story 33.4: the live derivatives and liquidation relay behind `/ws/live`'s `derivs:{iid}` and
`liquidations:{iid}` channels, in `LiveCandleBus`'s shape (one Redis subscriber per process,
per-listener bounded queues, a plain class whose one running instance `data_api.buses` builds).

`derivs:raw` (capture's `collector.derivs_publish` rows, one JSON array per sample tick) is
decoded only through `kernel.derivs_wire.from_wire` (SSOT-02: the one row format) and each row is
relayed as `{"channel": "derivs:<iid>", **row}` without its `instrument_id` (the channel names
it), the value still exact text. A malformed frame or row is ledgered at `live_derivs.parse` and
skipped, the rest of the frame still relayed (DATA-07).

Liquidations are not subscribed here: `LiveCandleBus` is the process's one `liquidations:raw`
subscriber (the `data_api.buses` invariant), and hands every row it decoded to
`publish_liquidations` through its attach hook; each is relayed as
`{"channel": "liquidations:<iid>", "liq": Liquidation.to_dict(row)}`.

Known limit: both channels are Redis pub/sub, at most once, and a stalled listener's queue drops
its oldest frame (`put_drop_oldest`): a live frame can be lost, never altered. A chart reads the
stored history through the derivatives routes. Upgrade path: a per-frame sequence number so a
client detects the gap and refetches.
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis
from kernel.derivs_wire import DerivsTick
from kernel.derivs_wire import from_wire
from kernel.liquidation import Liquidation
from observability import error_ledger

from views.rankings_bus import QUEUE_MAX
from views.rankings_bus import put_drop_oldest


logger = logging.getLogger(__name__)

# Capture's derivatives channel (`capture.infrastructure.redis_stream.DERIVS_CHANNEL`, the
# published language: views never imports capture, AD-D2).
DERIVS_CHANNEL = "derivs:raw"
PARSE_SITE = "live_derivs.parse"
# A frame's bad rows itemised on the ledger (each repr cut at `_REPR_MAX` chars), then one summary
# line with the total: a payload change must not write one line per row of every frame (capture's
# `_MALFORMED_SHOWN` rule).
_ROWS_SHOWN = 3
_REPR_MAX = 200


def _short_repr(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _REPR_MAX else f"{text[:_REPR_MAX]}... ({len(text)} chars)"


def derivs_channel(instrument_id: str) -> str:
    """Return the `/ws/live` channel of one instrument's derivatives rows."""
    return f"derivs:{instrument_id}"


def liquidations_channel(instrument_id: str) -> str:
    """Return the `/ws/live` channel of one instrument's liquidations."""
    return f"liquidations:{instrument_id}"


class LiveDerivsBus:
    """
    Fans out each instrument's derivatives rows and liquidations to the listeners of its channel.

    Invariant: a listener receives exactly the rows of the one channel it subscribed (its own
    instrument and kind), and a channel's listener set exists only while it has a listener -- the
    last `unsubscribe` deletes it (MEM-02). The commands that could violate it are `subscribe`/
    `unsubscribe` (the set's lifecycle) and `handle_derivs`/`publish_liquidations` (the routing).
    """

    def __init__(self) -> None:
        self._listeners: dict[str, set[asyncio.Queue[dict]]] = {}

    def subscribe(self, channel: str) -> "asyncio.Queue[dict]":
        """Register a bounded listener queue on `derivs:<iid>` or `liquidations:<iid>`."""
        queue: asyncio.Queue[dict] = asyncio.Queue(QUEUE_MAX)
        self._listeners.setdefault(channel, set()).add(queue)
        return queue

    def unsubscribe(self, channel: str, queue: "asyncio.Queue[dict]") -> None:
        """Deregister one queue; the channel's set goes with its last listener."""
        listeners = self._listeners.get(channel)
        if listeners is None:
            return
        listeners.discard(queue)
        if not listeners:
            del self._listeners[channel]

    def _relay(self, channel: str, message: dict) -> None:
        for queue in self._listeners.get(channel, ()):
            put_drop_oldest(queue, message)

    def handle_derivs(self, payload: object) -> None:
        """
        Relay one `derivs:raw` frame; a bad frame or row is ledgered and skipped, never fatal: the
        first `_ROWS_SHOWN` bad rows one line each, then one summary line naming the total.
        """
        if not isinstance(payload, list):
            error_ledger.record(
                PARSE_SITE, f"derivs:raw payload is not a list, SKIPPED: {_short_repr(payload)}"
            )
            return
        failed = 0
        for entry in payload:
            try:
                tick = from_wire(entry)
            except Exception as exc:  # one bad row never stops the frame's other rows
                failed += 1
                if failed <= _ROWS_SHOWN:
                    detail = f"derivs:raw row failed to decode, SKIPPED: {_short_repr(entry)}"
                    error_ledger.record(PARSE_SITE, detail, exc)
                continue
            self._relay_tick(tick)
        if failed > _ROWS_SHOWN:
            error_ledger.record(
                PARSE_SITE,
                f"{failed} derivs:raw rows of one frame failed to decode, "
                f"{failed - _ROWS_SHOWN} beyond the first {_ROWS_SHOWN} not itemised",
            )

    def _relay_tick(self, tick: DerivsTick) -> None:
        row = tick.to_wire()
        del row["instrument_id"]
        channel = derivs_channel(tick.instrument_id)
        self._relay(channel, {"channel": channel, **row})

    def publish_liquidations(self, rows: list[Liquidation]) -> None:
        """Relay the rows `LiveCandleBus.handle_liquidations` decoded (its attach hook)."""
        for row in rows:
            channel = liquidations_channel(row.instrument_id.value)
            if channel in self._listeners:  # `to_dict` only for a channel someone listens to
                self._relay(channel, {"channel": channel, "liq": Liquidation.to_dict(row)})

    def handle_message(self, data: object) -> None:
        """Parse one pub/sub message body; bad JSON is ledgered, never raised into `run`."""
        try:
            payload = json.loads(data)  # type: ignore[arg-type]
        except Exception as exc:
            error_ledger.record(PARSE_SITE, f"{DERIVS_CHANNEL} message is not JSON, SKIPPED", exc)
            return
        self.handle_derivs(payload)

    async def run(self, redis_url: str) -> None:
        """
        Subscribe to `derivs:raw` forever, reconnecting 2 s after any error (`LiveCandleBus.run`'s
        discipline).
        """
        logger.info("LiveDerivsBus starting, url=%s", redis_url)
        while True:
            try:
                async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(DERIVS_CHANNEL)
                    logger.info("LiveDerivsBus subscribed to %s", DERIVS_CHANNEL)
                    async for message in pubsub.listen():
                        if message["type"] == "message":
                            self.handle_message(message["data"])
            except asyncio.CancelledError:
                raise  # app shutdown
            except Exception as exc:
                logger.warning("LiveDerivsBus subscriber error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)
