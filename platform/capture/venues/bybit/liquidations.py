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
Bybit linear liquidations over a second, generic WebSocket (Story 33.1, `docs/DATA_DICTIONARY.md`
§1.26).

Why a second socket: Bybit publishes every liquidation on `allLiquidation.{symbol}` (linear and
inverse public streams; spot has none), but the Rust Bybit handler decodes only orderbook, trade,
kline and ticker topics and drops anything else as `BybitWsFrame::Unknown`
(`crates/adapters/bybit/src/websocket/handler.rs`), and `crates/` is untouchable (FORK-01). So this
feed owns one `nautilus_pyo3.WebSocketClient` (reconnect with backoff in Rust, text handler on the
event loop) against `kernel.venue_http.bybit_ws_url(env, "linear")` and decodes in Python.

Wire facts (captured 2026-10-05, `scripts/capture_hl_ws.py --topic allLiquidation`, §1.26):
  * a frame is `{"topic":"allLiquidation.BTCUSDT","type":"snapshot","ts":…,"data":[entry, …]}`,
    each entry `{"T": venue ms, "s": symbol, "S": "Buy"|"Sell", "v": size text, "p": price text}`;
  * `S` is the liquidated *position's* side: `"Buy"` is a long force-closed, so the forced order
    is a sell -- `LiquidatedSide.LONG`; `"Sell"` is `SHORT` (audit D-147);
  * `p` is the bankruptcy price, not the fill (audit D-148);
  * acks echo the request's `req_id`: `{"success":true,"op":"subscribe","req_id":…}`; a duplicate
    `ret_msg` carries `already subscribed` (benign: a reconnect's resubscribe, or the socket's own
    replay of a request sent while it reconnected); an unknown symbol `handler not found`; at most
    10 args per subscribe request on a public stream;
  * arrival - `T` measured up to 2,867 ms (`WIRE_LAG_NS`).

Rows: `parse_liquidation_frame` is pure (`Decimal` from the wire text, never `float`; precision
from the definition, `Liquidation.from_wire_text`). An inexact, unknown-symbol or implausibly
timed entry is an `Unencodable`, ledgered `collector.unencodable` and not archived while the
frame's other entries are; a frame that is not the documented shape is `Malformed`, ledgered
through `report_unknown_message`. Decoded rows go through the capture service's `ingest_rows`
(straight into the flush buffer, the plan's ids only, never `_on_data`, so never WS liveness and
never the hot path), then to `liquidations:raw`.

Liveness is the socket's state, never row arrival (a quiet hour has none): `connected`
(`is_active()`), `reconnecting`, `down` (no socket, closed or closing), reported on
`collector:status` and ledgered at each transition to `down`. Coverage is per id and confirmed by
the venue (`BybitLiquidationFeed`): an id's gap is open from the moment it is held until Bybit
acknowledges its topic on the live socket, and reopens on every reconnect, down spell or refused
subscribe; each closed (or checkpointed) gap is one `liquidations_unrecoverable` (`feed_down`)
line.

Known limit (no backfill, audit D-149): Bybit has no liquidation history endpoint, so a window the
socket missed is recorded, never filled. Upgrade path: such an endpoint, should one appear.
Known limit (liveness): an active socket whose server silently stopped pushing one topic cannot be
told from a quiet market; `idle_timeout_ms` reconnects a socket that delivers nothing at all (not
even pongs), and only the nightly trade match (`verification.liquidations`) would show a single
silent topic. Upgrade path: a per-topic probe (a periodic unsubscribe/subscribe).
Known limit (pacing): a reconnect's resubscribe goes out in requests of 10 topics paced at
`BYBIT_WS_FRAMES_PER_SECOND` by its own sleep, not through `WireChannels`, so a subscribe issued
in the same instant can come one interval early. Upgrade path: a pacing hook on `WireChannels`.
"""

import asyncio
import itertools
import json
import logging
import re
import time
from collections import Counter
from collections import OrderedDict
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from typing import NamedTuple
from typing import Protocol

from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.second_snapshot import SnapshotEncodingError
from kernel.venue_http import bybit_ws_url
from kernel.venues import MalformedInstrumentId
from kernel.venues import bybit_category

from capture.application import sites
from capture.application.feed import Feed
from capture.application.feed import report_unknown_message
from capture.application.ports import Ledger
from capture.application.wire_channels import WireChannels
from capture.domain import coverage
from capture.domain.coverage import CoverageLine
from capture.domain.coverage import LiquidationsUnrecoverable
from capture.venues.bybit.client import BYBIT_WS_FRAMES_PER_SECOND
from nautilus_trader.core import nautilus_pyo3
from nautilus_trader.model.identifiers import InstrumentId


logger = logging.getLogger(__name__)

TOPIC = "allLiquidation"
LIQUIDATION_FEED = Feed("liquidations", "liquidations")
CONNECTED = "connected"
RECONNECTING = "reconnecting"
DOWN = "down"
# Bybit refuses more than 10 args in one subscribe request on a public stream.
SUBSCRIBE_ARGS_MAX = 10
# Bybit pushes a symbol's liquidations at most once per 500 ms window (measured); a repeat of an
# entry is dropped within this window of its first arrival (`LiquidationDedup`: 0 repeats seen).
DEDUP_WINDOW_NS = 5_000_000_000
HEARTBEAT_SECONDS = 20
PING = '{"op":"ping"}'
# `WebSocketConfig.idle_timeout_ms`: the installed pyo3 docstring does not describe it (its
# signature has `idle_timeout_ms=None`); `crates/network/src/websocket/config.rs` does -- in handler
# mode the read task breaks and *reconnects* when no data at all is received for this long. Bybit
# answers every 20 s `{"op":"ping"}` with a pong text frame, so 3 heartbeats of total silence mean
# a half-open socket, never a quiet market.
IDLE_TIMEOUT_MS = 3 * HEARTBEAT_SECONDS * 1000
# The monitor's tick (state, checkpoints) and how often a socket that is down is reopened and
# unconfirmed topics are resent.
MONITOR_SECONDS = 1.0
MONITOR_NS = int(MONITOR_SECONDS * 1e9)
RECONNECT_SECONDS = 10.0
# Every window starts this much before its known-good instant: a liquidation reaches us up to
# 2,867 ms after its `T` (measured, §1.26), so one with `T` just before a socket died was never
# delivered. Windows are therefore an upper bound -- they may overlap archived rows, they never
# understate a gap.
WIRE_LAG_NS = 3_000_000_000
# An open window longer than this is written up to now and restarted, so a SIGKILL loses at most
# this much of an open window's head.
# Known limit: an outage writes one line per held id per minute (a day-long outage of 60 ids, about
# 86,000 lines), which every nightly verifier streams. Upgrade path: a checkpoint that extends the
# id's last line in a compacted coverage record, or a longer interval while the socket is `down`.
CHECKPOINT_NS = 60_000_000_000
# A subscribe request unanswered this long is presumed lost (the ack, or the frame itself, died
# with a socket): its ids are released so the monitor resends them, their gaps still open. Bybit
# acks within a round trip; 30 s is many of them.
ACK_TIMEOUT_NS = 30_000_000_000
RECONNECT_NS = int(RECONNECT_SECONDS * 1e9)
# A topic Bybit refused is resent after `RECONNECT_SECONDS`, doubling per further refusal up to
# this: a delisted symbol (`handler not found`) must not ledger every 10 s forever, and a
# transient refusal is still retried.
REFUSED_BACKOFF_MAX_NS = 600_000_000_000

_WIRE_SIDES = {"Buy": LiquidatedSide.LONG, "Sell": LiquidatedSide.SHORT}
_TEXT_KEYS = ("s", "S", "v", "p")
_ENTRY_KEYS = frozenset({"T", *_TEXT_KEYS})
_ALREADY_SUBSCRIBED = "already subscribed"
# A topic named in an ack's `ret_msg`: a dated future's symbol carries a `-` (`BTCUSDT-26DEC25`),
# which a narrower class would cut to another symbol's topic (`BTCUSDT`) and confirm that one.
_TOPIC_IN_TEXT = re.compile(rf"{TOPIC}\.[A-Za-z0-9-]+")
_UNPUBLISHED_MAX = 10_000


class Definition(NamedTuple):
    """A LINEAR instrument's id and its definition's `(price, size)` precisions."""

    instrument_id: InstrumentId
    precisions: tuple[int, int]


@dataclass(frozen=True)
class Malformed:
    """A frame that is not the documented shape: nothing of it is archived."""

    reason: str


@dataclass(frozen=True)
class Unencodable:
    """One entry the type cannot hold exactly (or of a symbol without a definition)."""

    symbol: str
    reason: str


Parsed = list[Liquidation | Unencodable] | Malformed


def definitions_of(instruments: Iterable[Any]) -> dict[str, Definition]:
    """
    Every LINEAR instrument by its Bybit symbol (the topic's suffix, `raw_symbol`), with the
    definition's own precisions: the units a liquidation is stored in (DATA-04).
    """
    out: dict[str, Definition] = {}
    for instrument in instruments:
        iid = str(instrument.id)
        try:
            linear = bybit_category(iid) == "linear"
        except MalformedInstrumentId:
            linear = False
        if linear:
            precisions = (instrument.price_precision, instrument.size_precision)
            out[str(instrument.raw_symbol)] = Definition(InstrumentId.from_str(iid), precisions)
    return out


def topic_of(symbol: str) -> str:
    return f"{TOPIC}.{symbol}"


def _entry_problem(entry: object, symbol: str) -> str | None:
    """Why one entry is not the documented shape, or None (extra keys are tolerated)."""
    if not isinstance(entry, dict) or not set(entry) >= _ENTRY_KEYS:
        return f"an entry lacks one of T/s/S/v/p: {entry!r:.120}"
    if type(entry["T"]) is not int or entry["T"] <= 0:
        return f"`T` {entry['T']!r} is not a positive integer"
    if not all(isinstance(entry[key], str) for key in _TEXT_KEYS):
        return f"a non-text s/S/v/p in {entry!r:.120}"
    if entry["s"] != symbol:
        return f"entry symbol {entry['s']!r} under topic symbol {symbol!r}"
    if entry["S"] not in _WIRE_SIDES:
        return f"side `S` {entry['S']!r} is neither Buy nor Sell"
    return None


def extra_entry_keys(frame: object) -> frozenset[str]:
    """Return the keys beyond T/s/S/v/p a frame's entries carry (a wire change worth one warning)."""
    data = frame.get("data") if isinstance(frame, dict) else None
    if not isinstance(data, list):
        return frozenset()
    return frozenset(k for e in data if isinstance(e, dict) for k in e) - _ENTRY_KEYS


def _frame_entries(frame: object) -> tuple[str, list[dict]] | Malformed:
    """Return the topic's symbol and its entries, or why the frame is not the documented shape."""
    if not isinstance(frame, dict):
        return Malformed(f"not a JSON object: {frame!r:.120}")
    topic = frame.get("topic")
    if not isinstance(topic, str) or not topic.startswith(f"{TOPIC}."):
        return Malformed(f"unknown topic {topic!r:.80}")
    data = frame.get("data")
    if not isinstance(data, list) or not data:
        return Malformed(f"{topic}: `data` is not a non-empty list")
    symbol = topic.removeprefix(f"{TOPIC}.")
    for entry in data:
        if (problem := _entry_problem(entry, symbol)) is not None:
            return Malformed(f"{topic}: {problem}")
    return symbol, data


def _event_ids(entries: list[dict]) -> list[str]:
    """
    Each entry's `venue_event_id`, `"{T}:{S}:{v}:{p}"` of the wire texts, with `#k` on the k-th
    identical entry of the frame: two equal entries in one push are two liquidations, kept both.
    """
    seen: Counter[str] = Counter()
    ids = []
    for entry in entries:
        key = f"{entry['T']}:{entry['S']}:{entry['v']}:{entry['p']}"
        ids.append(key if seen[key] == 0 else f"{key}#{seen[key]}")
        seen[key] += 1
    return ids


def parse_liquidation_frame(
    frame: object, definitions: Mapping[str, Definition], ts_init: int
) -> Parsed:
    """
    Decode one `allLiquidation` frame (already JSON-decoded) into rows, or `Malformed`. An entry
    whose symbol has no LINEAR definition, whose value is finer than the definition's precision,
    or whose venue time lies more than `MAX_TS_INIT_SKEW_NS` from `ts_init` (a wrong-unit `T`, or
    one that would overflow the stored `uint64`) is an `Unencodable` in place of its row; the
    other entries still decode.
    """
    shape = _frame_entries(frame)
    if isinstance(shape, Malformed):
        return shape
    symbol, entries = shape
    definition = definitions.get(symbol)
    if definition is None:
        return [Unencodable(symbol, "no instrument definition") for _ in entries]
    parsed: list[Liquidation | Unencodable] = []
    for entry, event_id in zip(entries, _event_ids(entries), strict=True):
        ts_event = entry["T"] * 1_000_000
        if abs(ts_event - ts_init) > MAX_TS_INIT_SKEW_NS:
            reason = f"{event_id}: venue time {ts_event} ns is over 300 s from arrival {ts_init}"
            parsed.append(Unencodable(symbol, reason))
            continue
        try:
            parsed.append(_liquidation(entry, definition, event_id, ts_init))
        except SnapshotEncodingError as exc:
            parsed.append(Unencodable(symbol, f"{event_id}: {exc}"))
    return parsed


def _liquidation(entry: dict, definition: Definition, event_id: str, ts_init: int) -> Liquidation:
    return Liquidation.from_wire_text(
        definition.instrument_id,
        _WIRE_SIDES[entry["S"]],
        entry["v"],
        entry["p"],
        definition.precisions,
        event_id,
        entry["T"] * 1_000_000,
        ts_init,
    )


class LiquidationDedup:
    """
    Drops a liquidation Bybit pushes again: a row whose `(instrument_id, venue_event_id)` was
    already let through within `window_ns` of arrival, on the monotonic clock (a wall-clock step
    must not stretch or collapse the window).

    Invariant: within the window, an `(instrument_id, venue_event_id)` passes `fresh` at most once;
    entries older than the window are forgotten, so memory is bounded by one window's
    liquidations. The command that could break it: a caller archiving rows it did not pass through
    `fresh`.

    Known limit (audit D-150): the key carries no venue id, so two distinct liquidations of one
    instrument with the same `T`, side, size and bankruptcy price, pushed in different frames
    within the window, are kept as one. Measured 2026-10-05 (§1.26, two captures, 85 min, 466
    entries on up to 60 linear symbols): 0 repeats across frames and 0 identical entries inside a
    frame, every symbol pushed at most one frame per 500 ms window and every entry carried its own
    `T` -- so the window drops nothing on a healthy stream; it guards a resubscribe or a socket
    replay re-sending a push, and the collision it risks needs two forced orders of one instrument
    in the same millisecond at the same size and price. Upgrade path: a venue liquidation id,
    should Bybit add one.
    """

    def __init__(self, window_ns: int = DEDUP_WINDOW_NS) -> None:
        self._window_ns = window_ns
        self._seen: OrderedDict[tuple[str, str], int] = OrderedDict()
        self.dropped = 0

    def fresh(self, rows: Iterable[Liquidation], now_ns: int) -> list[Liquidation]:
        """`now_ns`: the monotonic arrival time (`time.monotonic_ns()`)."""
        while self._seen and next(iter(self._seen.values())) < now_ns - self._window_ns:
            self._seen.popitem(last=False)
        kept = []
        for row in rows:
            key = (row.instrument_id.value, row.venue_event_id)
            if key in self._seen:
                self.dropped += 1
            else:
                self._seen[key] = now_ns
                kept.append(row)
        return kept


class Socket(Protocol):
    """The slice of `nautilus_pyo3.WebSocketClient` this feed uses (a fake in the tests)."""

    def is_active(self) -> bool: ...

    def is_reconnecting(self) -> bool: ...

    def is_disconnecting(self) -> bool: ...

    def is_closed(self) -> bool: ...

    def send_text(self, data: bytes) -> Awaitable[object]: ...

    def disconnect(self) -> Awaitable[object]: ...


Connector = Callable[
    [asyncio.AbstractEventLoop, str, Callable[[bytes], None], Callable[[], None]],
    Awaitable[Socket],
]
Ingest = Callable[[list[Liquidation], str], list[Any]]
NoteCoverage = Callable[[list[CoverageLine]], None]
Publish = Callable[[list[Liquidation]], Awaitable[None]]


async def connect_socket(
    loop: asyncio.AbstractEventLoop,
    url: str,
    handler: Callable[[bytes], None],
    post_reconnection: Callable[[], None],
) -> Socket:
    """
    Open the pyo3 socket: Bybit's `{"op":"ping"}` every 20 s, reconnect forever with backoff, and
    reconnect after `IDLE_TIMEOUT_MS` of total silence (a half-open socket).
    """
    config = nautilus_pyo3.WebSocketConfig(  # type: ignore[attr-defined]
        url=url,
        headers=[],
        heartbeat=HEARTBEAT_SECONDS,
        heartbeat_msg=PING,
        idle_timeout_ms=IDLE_TIMEOUT_MS,
    )
    return await nautilus_pyo3.WebSocketClient.connect(  # type: ignore[attr-defined]
        loop_=loop, config=config, handler=handler, post_reconnection=post_reconnection
    )


def _request(op: str, symbols: list[str], req_id: str = "") -> bytes:
    body: dict[str, object] = {"op": op, "args": [topic_of(s) for s in symbols]}
    if req_id:
        body["req_id"] = req_id
    return json.dumps(body).encode()


class BybitLiquidationFeed:
    """
    The liquidation socket of one Bybit capture process (see the module docstring).

    Invariant (per-id coverage): an id is *confirmed* only between Bybit's success ack for its
    topic on the current socket and the next event that may have lost frames (a reconnect, the
    socket leaving `connected`, a refused subscribe, an unsubscribe); every other instant it is
    held lies in exactly one open gap (`_gaps[iid]`), and every gap ends as `feed_down` lines
    (checkpointed every `CHECKPOINT_NS`, closed on the ack, on unsubscribe, at shutdown) -- a
    liquidation is archived, or its window is recorded, or (an entry of a frame the socket did
    deliver that cannot be stored: `Malformed`, `Unencodable`) it is ledgered. One id's written
    lines never cover a span twice beyond the `WIRE_LAG_NS` head: a gap reopens no earlier than
    the end of that id's last written line (`_written_through`). Commands that could break it: a
    topic sent around `subscribe`/`unsubscribe`, an ack not routed through `_on_ack`, or a monitor
    that is not running.

    `attach` must be called before `connect` (the composition root wires the service's
    `ingest_rows`/`note_coverage` and the live stream's `publish_liquidations`); `loop()` is the
    root's extra loop.
    """

    def __init__(
        self,
        environment: str,
        *,
        ledger: Ledger,
        connector: Connector = connect_socket,
        clock: Callable[[], int] = time.time_ns,
    ) -> None:
        self._url = bybit_ws_url(environment, "linear")
        self._ledger = ledger
        self._connector = connector
        self._clock = clock
        self._socket: Socket | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wire = WireChannels(BYBIT_WS_FRAMES_PER_SECOND)
        self._definitions: dict[str, Definition] = {}
        self._symbols: dict[str, str] = {}  # instrument id -> Bybit symbol, LINEAR only
        self._wanted: dict[str, str] = {}  # held ids -> symbol
        self._gaps: dict[str, int] = {}  # held id -> since when its liquidations are unconfirmed
        # req_id -> (the ids its subscribe still answers for, when it was sent, how many it carried)
        self._requests: dict[str, tuple[list[str], int, int]] = {}
        self._req_ids = itertools.count(1)
        self._last_good_ns = 0  # the last monitor tick that saw the socket connected
        self._last_rx_ns = 0  # the last message of any kind (a pong too) the socket delivered
        self._written_through: dict[str, int] = {}  # id -> end of its last written line
        # id -> where its `not_running` restart window ended, while its topic is not held yet
        self._unheld_since: dict[str, int] = {}
        # id -> (when its refused topic may be resent, the current backoff)
        self._refused: dict[str, tuple[int, int]] = {}
        self._no_definition: set[str] = set()
        self._extra_keys: set[frozenset[str]] = set()
        self._dedup = LiquidationDedup()
        self._ingest: Ingest | None = None
        self._note_coverage: NoteCoverage | None = None
        self._publish: Publish | None = None
        self._unpublished: list[Liquidation] = []
        self._publish_due = asyncio.Event()
        self._tasks: set[asyncio.Future[None]] = set()
        self._last_state: str | None = None
        self._last_connect_ns = 0
        self._open_failures = 0  # consecutive failed opens: an outage is ledgered once
        self._closing = False

    def attach(self, ingest: Ingest, note_coverage: NoteCoverage, publish: Publish) -> None:
        self._ingest, self._note_coverage, self._publish = ingest, note_coverage, publish

    # -- state ---------------------------------------------------------------------------------

    def state(self) -> str:
        socket = self._socket
        if socket is None or self._closing or socket.is_closed() or socket.is_disconnecting():
            return DOWN
        return CONNECTED if socket.is_active() else RECONNECTING

    def held_ids(self) -> frozenset[str]:
        return frozenset(self._wanted)

    # -- per-id gaps -----------------------------------------------------------------------------

    def _open_gap(self, iid: str, since_ns: int) -> None:
        """
        Open the id's gap at `since_ns`, or keep the earlier one already open -- never before the
        end of the id's last written line: a checkpointed or closed span is not written again (a
        long outage would otherwise pull every checkpoint back to the outage's start).
        """
        if iid in self._wanted:
            since = max(since_ns, self._written_through.get(iid, 0))
            self._gaps[iid] = min(self._gaps.get(iid, since), since)

    def _emit(self, iids: Iterable[str], now_ns: int, restart: bool) -> None:
        """Write the open gaps of `iids` up to now; reopen them at now with `restart`."""
        lines: list[CoverageLine] = []
        for iid in sorted(iids):
            since = self._gaps.pop(iid, None)
            if since is None:
                continue
            until = max(since, now_ns)
            lines.append(
                LiquidationsUnrecoverable(
                    iid, coverage.FEED_DOWN, max(0, since - WIRE_LAG_NS), until
                )
            )
            self._written_through[iid] = until
            if restart:
                self._gaps[iid] = now_ns
        if lines and self._note_coverage is not None:
            self._note_coverage(lines)

    def _known_good_ns(self, now_ns: int) -> int:
        """
        Return the last instant the socket is known to have delivered: its last message (a pong
        at least every `HEARTBEAT_SECONDS` on a healthy socket), and no later than one tick before
        the last connected tick, which may already have seen the *reconnected* socket before the
        Rust client's `post_reconnection` reached the loop. A half-open socket stays `is_active()`
        until `IDLE_TIMEOUT_MS` of silence, so connected ticks alone would start its window at the
        reconnect, not at the silence. Now, when it never delivered.

        Known limit: an upper bound by up to one heartbeat (a quiet healthy socket's last message is
        the last pong). Upgrade path: none needed -- windows are upper bounds by design.
        """
        if self._last_good_ns == 0:
            return self._last_rx_ns or now_ns
        return min(self._last_rx_ns or now_ns, self._last_good_ns - MONITOR_NS)

    # -- lifecycle -----------------------------------------------------------------------------

    async def connect(self, loop: asyncio.AbstractEventLoop, instruments: list) -> None:
        """Learn the LINEAR definitions and open the socket; a failure is ledgered, never raised."""
        if self._ingest is None:
            raise RuntimeError("BybitLiquidationFeed.connect before attach(): rows would be lost")
        self._loop = loop
        self._definitions = definitions_of(instruments)
        self._symbols = {str(d.instrument_id): s for s, d in self._definitions.items()}
        await self._open()

    async def _open(self) -> bool:
        """Open a fresh socket and subscribe every held id; False (ledgered) on failure."""
        now = self._clock()
        self._last_connect_ns = now
        assert self._loop is not None
        await self._drop_socket()
        for iid in self._wanted:
            self._open_gap(iid, now)
        try:
            self._socket = await self._connector(
                self._loop, self._url, self._on_message, self._post_reconnection
            )
        except Exception as e:
            self._open_failures += 1
            if self._open_failures == 1:  # one outage, one line: the reopen repeats every 10 s
                self._ledger(
                    sites.LIQUIDATION_FEED,
                    f"liquidation socket {self._url} did not connect: reopened every "
                    f"{RECONNECT_SECONDS:.0f} s, liquidations unrecorded meanwhile",
                    e,
                )
            else:
                logger.warning(
                    f"liquidation socket still not connecting ({self._open_failures} attempts): "
                    f"{e!r}"
                )
            return False
        if self._open_failures:
            logger.info(f"liquidation socket opened after {self._open_failures} failed attempts")
            self._open_failures = 0
        self._wire = WireChannels(BYBIT_WS_FRAMES_PER_SECOND)  # a new socket holds no topic
        self._requests.clear()
        self._last_rx_ns = self._clock()  # the open itself is the first known-good instant
        await self._resend_unheld()
        return True

    async def _drop_socket(self) -> None:
        """Close the previous socket best-effort: a stale one must not keep reconnecting."""
        socket, self._socket = self._socket, None
        if socket is None or socket.is_closed():
            return
        try:
            await socket.disconnect()
        except Exception as e:
            logger.info(f"liquidation socket: closing the previous socket failed: {e!r}")

    async def disconnect(self) -> None:
        """
        Close the socket, then write every held id's window up to now (the restart covers on):
        an open gap from its start, a confirmed id from now -- so its last `WIRE_LAG_NS`, whose
        frames may still be in flight and are no longer handled (the final flush may already have
        taken the buffer), is a window too.
        """
        self._closing = True
        for task in self._tasks:
            task.cancel()
        await self._drop_socket()
        now = self._clock()
        for iid in self._wanted:
            self._open_gap(iid, now)
        self._emit(list(self._gaps), now, restart=False)
        self._close_unheld(list(self._unheld_since), now)

    # -- restart -------------------------------------------------------------------------------

    def note_restart(self, iid: str, from_ns: int, to_ns: int) -> None:
        """
        Write an id's `restart` span (capture's: after its newest archived snapshot second, to this
        process's first verdict) as a `not_running` window, its head widened by `WIRE_LAG_NS` (a
        liquidation the stopped process had not received yet). An id whose topic is not held yet
        (its subscribe failed and waits for capture's retry) stays unrecorded from the span's end:
        its subscribe opens its gap there, its unsubscribe or the shutdown writes that stretch.
        An id without a LINEAR definition has no topic, so nothing to write.
        """
        if iid not in self._symbols:
            return
        line = LiquidationsUnrecoverable(
            iid, coverage.NOT_RUNNING, max(0, from_ns - WIRE_LAG_NS), to_ns
        )
        if iid not in self._wanted:
            self._unheld_since[iid] = to_ns + 1
        if self._note_coverage is not None:
            self._note_coverage([line])

    def _close_unheld(self, iids: Iterable[str], now_ns: int) -> None:
        """Write the not-yet-held stretch after a restart window of `iids` up to now."""
        lines: list[CoverageLine] = []
        for iid in sorted(iids):
            since = self._unheld_since.pop(iid, None)
            if since is not None:
                lines.append(
                    LiquidationsUnrecoverable(iid, coverage.FEED_DOWN, since, max(since, now_ns))
                )
        if lines and self._note_coverage is not None:
            self._note_coverage(lines)

    # -- subscriptions -------------------------------------------------------------------------

    async def subscribe(self, iid: str) -> None:
        """
        Hold the id's topic; its gap is open until Bybit acknowledges it. With no connected socket
        (a failed connect, or one reconnecting) the id is only held: the reopen, the reconnect's
        resubscribe or the monitor's resend subscribes it, and capture's retry loop is not fed a
        failure per id for one known outage. Raises when the send fails, for the client to ledger
        and capture to retry. An id without a LINEAR definition is ledgered once and never
        retried: no retry can give it one.
        """
        symbol = self._symbols.get(iid)
        if symbol is None:
            if iid not in self._no_definition:
                self._no_definition.add(iid)
                self._ledger(
                    sites.LIQUIDATION_FEED,
                    f"{iid}: no LINEAR definition fetched at start, no liquidation topic",
                )
            return
        if iid not in self._wanted:
            self._wanted[iid] = symbol
            self._refused.pop(iid, None)  # a fresh hold is tried at once
            now = self._clock()
            # Not held since its restart window ended: the gap runs on from there.
            self._open_gap(iid, min(now, self._unheld_since.pop(iid, now)))
        if self._socket is not None and self.state() == CONNECTED:
            await self._hold(iid, symbol)

    def _new_request(self, iids: list[str]) -> str:
        req_id = f"liq-{next(self._req_ids)}"
        self._requests[req_id] = (iids, self._clock(), len(iids))
        return req_id

    async def _hold(self, iid: str, symbol: str) -> None:
        """
        Subscribe the id's topic unless this socket already holds it. The request is registered
        only when the frame actually goes out: a hold of a held topic sends nothing, so it must
        leave no request waiting for an ack that never comes.
        """
        socket = self._socket
        assert socket is not None
        sent: list[str] = []

        async def send() -> None:
            sent.append(self._new_request([iid]))
            await socket.send_text(_request("subscribe", [symbol], sent[-1]))

        try:
            await self._wire.hold((TOPIC, iid), send)
        except Exception:
            for req_id in sent:
                self._requests.pop(req_id, None)
            raise

    async def unsubscribe(self, iid: str) -> None:
        """
        Write the id's open gap, then release its topic. A failed send is ledgered and the topic
        forgotten anyway, so a re-add subscribes it again (Bybit's `already subscribed` then
        confirms it) instead of finding it held and waiting for an ack that never comes.
        """
        now = self._clock()
        self._emit([iid], now, restart=False)
        self._close_unheld([iid], now)  # a pending id removed before its topic was ever held
        symbol = self._wanted.pop(iid, None)
        self._refused.pop(iid, None)
        for ids, *_ in self._requests.values():  # an ack in flight must not confirm a re-add
            if iid in ids:
                ids.remove(iid)
        socket = self._socket
        if symbol is None or socket is None:
            return
        try:
            await self._wire.release(
                (TOPIC, iid), lambda: socket.send_text(_request("unsubscribe", [symbol]))
            )
        except Exception as e:
            self._ledger(
                sites.LIQUIDATION_FEED,
                f"unsubscribe {topic_of(symbol)} failed: its rows still arrive, not archived "
                "(not planned) until the socket reconnects",
                e,
            )
            self._forget([iid])

    def _forget(self, iids: Iterable[str]) -> None:
        """
        Forget that this socket holds these topics, so `_resend_unheld` sends them again. No wire
        call and no wait: an awaited release would spend pacing slots on nothing and could, across
        a reconnect, unmark the topic the new socket just subscribed.
        """
        for iid in iids:
            self._wire.forget((TOPIC, iid))

    async def _resend_unheld(self) -> None:
        """
        Subscribe every held id whose topic this socket does not hold, except a refused one still
        backing off; failures ledgered.
        """
        held = {iid for _, iid in self._wire.held()}
        now = self._clock()
        for iid, symbol in sorted(self._wanted.items()):
            if iid in held or self._socket is None or now < self._refused.get(iid, (0, 0))[0]:
                continue
            try:
                await self._hold(iid, symbol)
            except Exception as e:
                self._ledger(
                    sites.LIQUIDATION_FEED,
                    f"subscribe {topic_of(symbol)} failed: resent every {RECONNECT_SECONDS:.0f} s",
                    e,
                )

    def _post_reconnection(self) -> None:
        """Hop onto the loop: the Rust client calls this on its own thread after a reconnect."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._on_reconnected)

    def _on_reconnected(self) -> None:
        """
        Every held id is unconfirmed from the last known-good instant until its ack. Requests in
        flight died with the old connection (their acks never come; the resubscribe covers their
        ids), so they are forgotten rather than expired as lost 30 s later. Nothing after
        `disconnect()`: a late callback must not reopen gaps or start a task nobody cancels.
        """
        if self._closing:
            return
        since = self._known_good_ns(self._clock())
        for iid in self._wanted:
            self._open_gap(iid, since)
        self._requests.clear()
        self._spawn(self._resubscribe())

    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resubscribe(self) -> None:
        """
        Resubscribe every held topic after a reconnect, in requests of `SUBSCRIBE_ARGS_MAX`
        topics: Bybit keeps no subscription across connections. A chunk whose send fails is
        ledgered and released, so the monitor resends it; its gaps stay open.
        """
        iids = sorted(iid for _, iid in self._wire.held())
        socket = self._socket
        for start in range(0, len(iids), SUBSCRIBE_ARGS_MAX):
            if socket is None or socket is not self._socket:
                return  # closed or replaced meanwhile: the new socket subscribes on its own
            held = self._wire.held()  # an id unsubscribed while this sleeps is not resent
            chunk = [i for i in iids[start : start + SUBSCRIBE_ARGS_MAX] if (TOPIC, i) in held]
            if not chunk:
                continue
            req_id = self._new_request(chunk)
            symbols = [self._symbols[iid] for iid in chunk]
            try:
                await socket.send_text(_request("subscribe", symbols, req_id))
            except Exception as e:
                self._requests.pop(req_id, None)
                self._ledger(sites.LIQUIDATION_FEED, f"resubscribe of {symbols} failed", e)
                self._forget(chunk)
            await asyncio.sleep(1.0 / BYBIT_WS_FRAMES_PER_SECOND)
        logger.info(f"liquidation socket reconnected: {len(iids)} topics resubscribed")

    # -- frames --------------------------------------------------------------------------------

    def _on_message(self, raw: bytes) -> None:
        """
        Every socket message, on the event loop; a failure here is ledgered, never swallowed.
        After `disconnect()` a message still queued by the Rust client is not handled: its
        liquidations lie in the window `disconnect()` wrote.
        """
        if self._closing:
            return
        self._last_rx_ns = self._clock()
        try:
            self._handle(raw)
        except Exception as e:
            self._ledger(sites.LIQUIDATION_FEED, f"frame handling failed: {raw[:200]!r}", e)

    def _handle(self, raw: bytes) -> None:
        try:
            frame = json.loads(raw)
        except ValueError:
            report_unknown_message(raw, self._ledger, LIQUIDATION_FEED)
            return
        if isinstance(frame, dict) and "op" in frame:
            self._on_ack(frame)
            return
        self._warn_extra_keys(frame)
        parsed = parse_liquidation_frame(frame, self._definitions, self._clock())
        if isinstance(parsed, Malformed):
            text = f"{parsed.reason}; frame {raw[:200]!r}"
            report_unknown_message(text, self._ledger, LIQUIDATION_FEED)
            return
        self._accept(parsed)

    def _warn_extra_keys(self, frame: object) -> None:
        """Warn once per new entry key set (a wire change); the frame is kept."""
        extra = extra_entry_keys(frame)
        if extra and extra not in self._extra_keys:
            self._extra_keys.add(extra)
            logger.warning(f"allLiquidation entries carry new keys {sorted(extra)}; kept")

    def _on_ack(self, frame: dict) -> None:
        op = frame.get("op")
        if op not in ("subscribe", "unsubscribe", "ping", "pong"):
            report_unknown_message(frame, self._ledger, LIQUIDATION_FEED)
            return
        request = (
            self._requests.pop(str(frame.get("req_id", "")), None) if op == "subscribe" else None
        )
        iids, carried = ([], 0) if request is None else (request[0], request[2])
        reason = str(frame.get("ret_msg", ""))
        if frame.get("success") is True:
            self._confirm(iids)  # the venue confirms these topics live on this socket
        elif op == "subscribe" and _ALREADY_SUBSCRIBED in reason:
            self._on_already_subscribed(iids, carried, reason)
        else:
            self._refuse(op, iids, reason)

    def _confirm(self, iids: list[str]) -> None:
        """End the gaps of these ids, whose topics Bybit confirmed live on this socket."""
        self._emit(iids, self._clock(), restart=False)
        for iid in iids:
            self._refused.pop(iid, None)

    def _named(self, iids: list[str], reason: str) -> list[str]:
        """Return the ids of `iids` whose topic `reason` names."""
        named = set(_TOPIC_IN_TEXT.findall(reason))
        return [i for i in iids if topic_of(self._symbols.get(i, "")) in named]

    def _on_already_subscribed(self, iids: list[str], carried: int, reason: str) -> None:
        """
        `already subscribed` names the topic(s) it refused, and proves only those live: in a
        resubscribe of several topics it says nothing of the others. The named topics are
        confirmed; a message naming none confirms a request that carried one topic only (never
        one an unsubscribe trimmed to one: the name may be the trimmed id's). The rest are
        released for the monitor to resend one by one (their gaps stay open until those acks).
        """
        live = self._named(iids, reason)
        if not live and carried == 1 and not _TOPIC_IN_TEXT.search(reason):
            live = list(iids)
        self._confirm(live)
        unproven = [i for i in iids if i not in live]
        if unproven:
            logger.info(f"liquidation resubscribe: {reason}; resending {unproven} one by one")
            self._forget(unproven)

    def _refuse(self, op: object, iids: list[str], reason: str) -> None:
        """
        Ledger a refusal and release its ids for the monitor to resend. The topics the refusal
        names back off (doubling); when it names none of them, all do. The others of a
        several-topic request are resent at the next round, unpenalised: one delisted symbol
        must not hold back its healthy neighbours of a resubscribe.
        """
        now = self._clock()
        for iid in self._named(iids, reason) or iids:
            backoff = self._refused.get(iid, (0, RECONNECT_NS // 2))[1] * 2
            backoff = min(backoff, REFUSED_BACKOFF_MAX_NS)
            self._refused[iid] = (now + backoff, backoff)
        self._ledger(
            sites.LIQUIDATION_FEED,
            f"Bybit refused `{op}` of {iids}: {reason}; resent after a backoff doubling from "
            f"{RECONNECT_SECONDS:.0f} s to {REFUSED_BACKOFF_MAX_NS // 10**9} s",
        )
        self._forget(iids)

    def _accept(self, parsed: list[Liquidation | Unencodable]) -> None:
        rows = []
        for item in parsed:
            if isinstance(item, Unencodable):
                self._ledger(
                    sites.UNENCODABLE, f"liquidation {item.symbol}: {item.reason}; not archived"
                )
            else:
                rows.append(item)
        dropped_before = self._dedup.dropped
        fresh = self._dedup.fresh(rows, time.monotonic_ns())
        if self._dedup.dropped > dropped_before:
            logger.debug(f"liquidations: {self._dedup.dropped - dropped_before} repeats dropped")
        assert self._ingest is not None
        kept = self._ingest(fresh, sites.LIQUIDATION_FEED)
        if kept:
            self._queue_publish(kept)

    def _queue_publish(self, rows: list[Liquidation]) -> None:
        overflow = len(self._unpublished) + len(rows) - _UNPUBLISHED_MAX
        if overflow > 0:  # Redis stalled for long: the live view loses the oldest, never memory
            self._ledger(
                sites.LIQUIDATION_PUBLISH,
                f"{overflow} unpublished liquidations dropped from the live view (archived)",
            )
            del self._unpublished[:overflow]
        self._unpublished.extend(rows)
        self._publish_due.set()

    # -- the root's extra loop -----------------------------------------------------------------

    async def loop(self) -> None:
        """Run the monitor (state, gaps, reopen, resend) and the publisher until cancelled."""
        await asyncio.gather(self._monitor(), self._publisher())

    async def _monitor(self, every_seconds: float = MONITOR_SECONDS) -> None:
        while True:
            await asyncio.sleep(every_seconds)
            try:
                await self.check(self._clock())
            except Exception as e:  # the optional feed must never take capture down
                self._ledger(sites.LIQUIDATION_FEED, "liquidation monitor round failed", e)

    async def check(self, now_ns: int) -> None:
        """
        One monitor round: note the state, open every held id's gap while not connected,
        checkpoint gaps older than `CHECKPOINT_NS`, reopen or resend when due. Gaps close only on
        Bybit's acks, never because `is_active()` turned true.
        """
        state = self._note_state(self.state())
        if state == CONNECTED:
            self._last_good_ns = now_ns
        else:
            since = self._known_good_ns(now_ns)
            for iid in self._wanted:
                self._open_gap(iid, since)
        old = [iid for iid, since in self._gaps.items() if now_ns - since > CHECKPOINT_NS]
        self._emit(old, now_ns, restart=True)
        self._expire_requests(now_ns)
        if now_ns - self._last_connect_ns >= RECONNECT_SECONDS * 1e9:
            await self._heal(state, now_ns)

    def _expire_requests(self, now_ns: int) -> None:
        """Release the ids of every subscribe unanswered past `ACK_TIMEOUT_NS` (ledgered)."""
        lost = [r for r, (_, sent, _) in self._requests.items() if now_ns - sent > ACK_TIMEOUT_NS]
        for req_id in lost:
            iids, _, _ = self._requests.pop(req_id)
            self._ledger(
                sites.LIQUIDATION_FEED,
                f"no ack for subscribe {req_id} of {iids} in {ACK_TIMEOUT_NS // 10**9} s: "
                "resent, their windows stay open",
            )
            self._forget(iids)

    def _note_state(self, state: str) -> str:
        if state != self._last_state:
            logger.info(f"liquidation socket: {self._last_state} -> {state}")
            if state == DOWN:
                self._ledger(
                    sites.LIQUIDATION_FEED,
                    f"liquidation socket down ({len(self._wanted)} topics held): liquidations "
                    "unrecorded until it reconnects",
                )
            self._last_state = state
        return state

    async def _heal(self, state: str, now_ns: int) -> None:
        if state == DOWN and not self._closing:
            await self._open()
        elif state == CONNECTED:
            self._last_connect_ns = now_ns
            await self._resend_unheld()

    async def _publisher(self) -> None:
        while True:
            await self._publish_due.wait()
            self._publish_due.clear()
            rows, self._unpublished = self._unpublished, []
            try:
                assert self._publish is not None
                await self._publish(rows)
            except Exception as e:
                self._ledger(
                    sites.LIQUIDATION_PUBLISH,
                    f"{len(rows)} liquidations not published on liquidations:raw (archived)",
                    e,
                )
