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

Story 33.4 adds the `derivs:{iid}` and `liquidations:{iid}` channels (`views.live_derivs`,
relayed from `buses.live_derivs_bus`) on the same control messages; one `_Subscriptions` per
connection holds every kind, so `_MAX_SUBSCRIPTIONS` caps candles, derivs and liquidations
together. An unparseable channel or a non-JSON frame is ignored, never fatal: the first one per
connection is logged at WARNING, the rest only counted, and the count logged once at close (a
client looping on a bad subscribe cannot flood the log).
"""

import asyncio
import json
import logging
from typing import NamedTuple

from fastapi import APIRouter
from fastapi import WebSocket
from fastapi import WebSocketDisconnect
from views.live_derivs import derivs_channel
from views.live_derivs import liquidations_channel
from views.rankings_bus import QUEUE_MAX
from views.rankings_bus import put_drop_oldest

from data_api import alert_wiring
from data_api import buses


logger = logging.getLogger(__name__)

router = APIRouter()

# Per connection, every kind together (candles, derivs, liquidations).
_MAX_SUBSCRIPTIONS = 32

CANDLES = "candles"
DERIVS = "derivs"
LIQUIDATIONS = "liquidations"

# Same bound `routes/candles.py` clamps `/api/candles` to (1w, the timeframe selector's widest
# bar), enforced here because a subscribe channel is client-written text: an unbounded
# `bar_seconds` reaches `candles.domain.fold`'s int64 bucket arithmetic and raises there, and the
# raise escapes `LiveCandleBus.handle_batch` into its reconnect loop -- one bad channel would stop
# live candles for every connected client. Rejected, not clamped: a silently widened bar would
# publish on a channel name the client never subscribed to.
_MAX_BAR_SECONDS = 604_800


def _parse_candle_channel(channel: str) -> tuple[str, int] | None:
    """
    Parse `"candles:{iid}:{bar_seconds}"`. Returns `None` for anything else (e.g. a
    future non-candle channel, or a malformed string) -- the caller then simply ignores
    the control message rather than erroring the connection.
    """
    prefix, _, rest = channel.partition(":")
    if prefix != "candles" or not rest:
        return None
    iid, _, bar_seconds_str = rest.rpartition(":")
    if not iid or not bar_seconds_str.isdigit():
        return None
    bar_seconds = int(bar_seconds_str)
    if not 0 < bar_seconds <= _MAX_BAR_SECONDS:
        return None  # 0 would divide-by-zero, and an oversized one overflows, in the bucket math
    return iid, bar_seconds


class _Channel(NamedTuple):
    """One parsed subscribe channel; `bar_seconds` only for candles."""

    kind: str
    iid: str
    bar_seconds: int | None = None

    @property
    def name(self) -> str:
        """The canonical channel name, the one its frames carry."""
        if self.kind == CANDLES:
            return f"candles:{self.iid}:{self.bar_seconds}"
        if self.kind == DERIVS:
            return derivs_channel(self.iid)
        return liquidations_channel(self.iid)


def _parse_channel(channel: str) -> _Channel | None:
    """
    Parse `candles:{iid}:{bar_seconds}`, `derivs:{iid}` or `liquidations:{iid}`; None for anything
    else. A derivs/liquidations iid must be non-empty and hold no `:` (so a channel name maps back
    to one instrument).
    """
    kind, _, iid = channel.partition(":")
    if kind == CANDLES:
        parsed = _parse_candle_channel(channel)
        return None if parsed is None else _Channel(CANDLES, *parsed)
    if kind in (DERIVS, LIQUIDATIONS) and iid and ":" not in iid:
        return _Channel(kind, iid)
    return None


async def _forward(source: "asyncio.Queue[dict]", outbox: "asyncio.Queue[dict]") -> None:
    """
    Relay every message from one source queue into the shared per-connection outbox,
    forever, until cancelled -- runs as its own task per active subscription.
    """
    while True:
        put_drop_oldest(outbox, await source.get())


def _log_forward_error(task: "asyncio.Task[None]") -> None:
    """
    Log a failed per-channel `_forward` task. It isn't in `ws_live`'s monitored
    `asyncio.wait()` set (there can be any number of them, created/cancelled dynamically) --
    without this, an unexpected failure would silently stop that channel's stream and only
    surface as an "exception was never retrieved" warning from asyncio's default handler.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("/ws/live forward task failed: %s", exc)


def _open(channel: _Channel) -> "asyncio.Queue[dict]":
    """Register a listener queue on the channel's bus (candles: also start the pair's seed)."""
    if channel.kind == CANDLES:
        assert channel.bar_seconds is not None  # set for every parsed candles channel
        queue = buses.live_candle_bus.subscribe(channel.iid, channel.bar_seconds)
        buses.live_candle_bus.start_seed(channel.iid, channel.bar_seconds)
        return queue
    return buses.live_derivs_bus.subscribe(channel.name)


def _close(channel: _Channel, queue: "asyncio.Queue[dict]") -> None:
    if channel.kind == CANDLES:
        assert channel.bar_seconds is not None
        buses.live_candle_bus.unsubscribe(channel.iid, channel.bar_seconds, queue)
    else:
        buses.live_derivs_bus.unsubscribe(channel.name, queue)


class _Subscriptions:
    """
    Per-connection bookkeeping of every active subscription, of any kind: one forwarder task and
    one bus queue per channel. Invariant: at most `_MAX_SUBSCRIPTIONS` of them, all kinds counted
    together, each channel once -- so a buggy or hostile client cannot grow per-channel state.
    """

    def __init__(self, outbox: "asyncio.Queue[dict]") -> None:
        self._outbox = outbox
        self._entries: dict[str, tuple[_Channel, asyncio.Queue[dict], asyncio.Task[None]]] = {}
        self.ignored = 0  # unparseable control frames of this connection

    def note_ignored(self, what: str, text: str) -> None:
        """
        Count one ignored inbound frame; only the connection's first is logged (WARNING), so a
        client repeating a bad one cannot flood the log. `log_ignored` reports the total at close.
        """
        self.ignored += 1
        if self.ignored == 1:
            logger.warning(
                "/ws/live ignored %s: %r (further ones on this connection are counted, "
                "logged at close)",
                what,
                text[:200],
            )

    def log_ignored(self) -> None:
        if self.ignored > 1:
            logger.info("/ws/live connection closed after ignoring %d control frames", self.ignored)

    def subscribe(self, channel: _Channel) -> None:
        name = channel.name
        if name in self._entries or len(self._entries) >= _MAX_SUBSCRIPTIONS:
            return  # idempotent, and capped
        queue = _open(channel)
        task = asyncio.create_task(_forward(queue, self._outbox))
        task.add_done_callback(_log_forward_error)
        self._entries[name] = (channel, queue, task)

    def unsubscribe(self, channel: _Channel) -> None:
        entry = self._entries.pop(channel.name, None)
        if entry is None:
            return
        _, queue, task = entry
        task.cancel()
        _close(channel, queue)

    def teardown_all(self) -> None:
        for channel, _, _ in list(self._entries.values()):
            self.unsubscribe(channel)


def _handle_control_message(message: dict, subs: _Subscriptions) -> None:
    for key in ("subscribe", "unsubscribe"):
        text = message.get(key)
        if not isinstance(text, str):
            continue
        channel = _parse_channel(text)
        if channel is None:
            subs.note_ignored(f"an unparseable {key} channel", text)
            continue  # this key didn't parse -- still check the other key, don't give up
        subs.subscribe(channel) if key == "subscribe" else subs.unsubscribe(channel)
        return


async def _sender(websocket: WebSocket, outbox: "asyncio.Queue[dict]") -> None:
    while True:
        message = await outbox.get()
        await websocket.send_json(message)


async def _reader(websocket: WebSocket, subs: _Subscriptions) -> None:
    """
    Read inbound control frames forever. A malformed (non-JSON or non-dict) frame is
    logged and skipped, never fatal -- only `WebSocketDisconnect` (raised by
    `receive_text()` once the client closes) ends this loop.
    """
    while True:
        text = await websocket.receive_text()
        try:
            message = json.loads(text)
        except Exception:
            subs.note_ignored("a non-JSON frame", text)
            continue
        if isinstance(message, dict):
            _handle_control_message(message, subs)


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    outbox: asyncio.Queue[dict] = asyncio.Queue(QUEUE_MAX)

    # Subscribe before reading/sending `latest`: a message published between reading
    # `.latest` and registering the listener queue would otherwise be missed entirely
    # for this connection (a narrow but real race). Subscribing first means that message
    # instead arrives twice (via the initial send below and the queue) -- harmless for a
    # full-snapshot relay, unlike a silent drop. Unchanged from before Story 15.5.
    rankings_queue = buses.bus.subscribe()
    alerts_queue = alert_wiring.engine.subscribe()
    subs = _Subscriptions(outbox)
    alerts_forward_task = asyncio.create_task(_forward(alerts_queue, outbox))
    rankings_forward_task = asyncio.create_task(_forward(rankings_queue, outbox))
    sender_task = asyncio.create_task(_sender(websocket, outbox))
    reader_task = asyncio.create_task(_reader(websocket, subs))

    try:
        # Inside the try/finally (unlike pre-Story-15.5 code, where this send sat before
        # the try): a client that disconnects between accept() and this send must still
        # hit `finally` below, or `rankings_queue` leaks in `buses.bus`'s listener set
        # forever.
        if buses.bus.latest is not None:
            await websocket.send_json(buses.bus.latest)
        done, _pending = await asyncio.wait(
            {sender_task, reader_task, rankings_forward_task, alerts_forward_task},
            return_when=asyncio.FIRST_COMPLETED,
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
        alerts_forward_task.cancel()
        alert_wiring.engine.unsubscribe(alerts_queue)
        subs.teardown_all()
        subs.log_ignored()
        buses.bus.unsubscribe(rankings_queue)
