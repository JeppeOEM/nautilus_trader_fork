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
`/ws/live` -- relays every `rankings:live` message completely verbatim (including the
raw `ranks` key -- epics AC2: "no reshaping beyond channel subscription"). Unlike
`GET /api/rankings`, this endpoint does NOT rename `ranks` to `items`.

On connect, immediately sends the bus's cached `latest` message (if any) before waiting
on the listener queue, so a new client doesn't wait a full heartbeat for its first paint
(I/O matrix: "New WS client connects mid-stream"). This part of the behavior is
completely unchanged from before Story 15.5.

Story 15.5 adds inbound `{"subscribe": "candles:{iid}:{bar_seconds}"}` /
{"unsubscribe": ...}` control messages -- this exact message shape is this story's own
invention, not a Redis wire format (see `_parse_candle_channel`'s docstring). Handling
both an outbound relay (rankings, always-on) and a variable set of outbound live-candle
relays plus inbound control reads on the same socket needs a small per-connection
multiplexer: one shared `outbox` queue fed by one forwarder task per active source
(the `RankingsBus` queue, plus one per live-candle subscription), a `_sender` task that
drains `outbox` into the socket, and a `_reader` task that turns inbound control
messages into subscribe/unsubscribe calls against `LiveCandleBus`. Whichever of
`_sender`/`_reader` notices the disconnect first (a `WebSocketDisconnect` from
`receive_text()`, or a send failure) tears down everything else.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from data_api import live_candles, redis_bus


logger = logging.getLogger(__name__)

router = APIRouter()


def _parse_candle_channel(channel: str) -> tuple[str, int] | None:
    """Parse `"candles:{iid}:{bar_seconds}"`. Returns `None` for anything else (e.g. a
    future non-candle channel, or a malformed string) -- the caller then simply ignores
    the control message rather than erroring the connection."""
    prefix, _, rest = channel.partition(":")
    if prefix != "candles" or not rest:
        return None
    iid, _, bar_seconds_str = rest.rpartition(":")
    if not iid or not bar_seconds_str.isdigit():
        return None
    return iid, int(bar_seconds_str)


async def _forward(source: "asyncio.Queue[dict]", outbox: "asyncio.Queue[dict]") -> None:
    """Relay every message from one source queue into the shared per-connection outbox,
    forever, until cancelled -- runs as its own task per active subscription."""
    while True:
        outbox.put_nowait(await source.get())


class _CandleSubscriptions:
    """Per-connection bookkeeping for this connection's active live-candle
    subscriptions: one forwarder task + `LiveCandleBus` queue per subscribed channel."""

    def __init__(self, outbox: "asyncio.Queue[dict]") -> None:
        self._outbox = outbox
        self._entries: dict[str, tuple[str, int, "asyncio.Queue[dict]", "asyncio.Task[None]"]] = {}

    def subscribe(self, channel: str, iid: str, bar_seconds: int) -> None:
        if channel in self._entries:
            return  # already subscribed for this connection -- idempotent
        queue = live_candles.live_candle_bus.subscribe(iid, bar_seconds)
        task = asyncio.create_task(_forward(queue, self._outbox))
        self._entries[channel] = (iid, bar_seconds, queue, task)

    def unsubscribe(self, channel: str) -> None:
        entry = self._entries.pop(channel, None)
        if entry is None:
            return
        iid, bar_seconds, queue, task = entry
        task.cancel()
        live_candles.live_candle_bus.unsubscribe(iid, bar_seconds, queue)

    def teardown_all(self) -> None:
        for channel in list(self._entries):
            self.unsubscribe(channel)


def _handle_control_message(message: dict, subs: _CandleSubscriptions) -> None:
    for key in ("subscribe", "unsubscribe"):
        channel = message.get(key)
        if not isinstance(channel, str):
            continue
        parsed = _parse_candle_channel(channel)
        if parsed is not None:
            iid, bar_seconds = parsed
            subs.subscribe(channel, iid, bar_seconds) if key == "subscribe" else subs.unsubscribe(channel)
        return


async def _sender(websocket: WebSocket, outbox: "asyncio.Queue[dict]") -> None:
    while True:
        message = await outbox.get()
        await websocket.send_json(message)


async def _reader(websocket: WebSocket, subs: _CandleSubscriptions) -> None:
    """Reads inbound control frames forever. A malformed (non-JSON or non-dict) frame is
    logged and skipped, never fatal -- only `WebSocketDisconnect` (raised by
    `receive_text()` once the client closes) ends this loop."""
    while True:
        text = await websocket.receive_text()
        try:
            message = json.loads(text)
        except Exception:
            logger.warning("/ws/live received a non-JSON frame, ignoring: %r", text)
            continue
        if isinstance(message, dict):
            _handle_control_message(message, subs)


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    outbox: "asyncio.Queue[dict]" = asyncio.Queue()

    # Subscribe before reading/sending `latest`: a message published between reading
    # `.latest` and registering the listener queue would otherwise be missed entirely
    # for this connection (a narrow but real race). Subscribing first means that message
    # instead arrives twice (via the initial send below and the queue) -- harmless for a
    # full-snapshot relay, unlike a silent drop. Unchanged from before Story 15.5.
    rankings_queue = redis_bus.bus.subscribe()
    if redis_bus.bus.latest is not None:
        await websocket.send_json(redis_bus.bus.latest)

    subs = _CandleSubscriptions(outbox)
    rankings_forward_task = asyncio.create_task(_forward(rankings_queue, outbox))
    sender_task = asyncio.create_task(_sender(websocket, outbox))
    reader_task = asyncio.create_task(_reader(websocket, subs))

    try:
        done, _pending = await asyncio.wait(
            {sender_task, reader_task}, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            exc = task.exception()
            if exc is not None:
                raise exc
    except WebSocketDisconnect:
        logger.info("/ws/live client disconnected")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A network-level drop (e.g. connection reset mid-send) can raise something
        # other than WebSocketDisconnect depending on the ASGI server -- treat any of
        # these as a disconnect rather than crashing the route.
        logger.info("/ws/live connection closed: %s", exc)
    finally:
        sender_task.cancel()
        reader_task.cancel()
        rankings_forward_task.cancel()
        subs.teardown_all()
        redis_bus.bus.unsubscribe(rankings_queue)
