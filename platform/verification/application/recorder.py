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
The reference recorder's supervision loops: one WebSocket session per endpoint of the plan, the
REST pollers, the plan re-read and the once-a-second flush.

Invariant: every received frame and REST response is written verbatim with its local receive
time, and every connection transition leaves a `connection` line (in the venue's `connection`
channel and in each data channel of the endpoint), so a recorder gap is always visible as one and
never mistaken for a collector gap; every failure is ledgered at a `sites` constant, never a bare
log line (DATA-07). The reference side never imports the code it checks.

Keepalive and reconnect: each endpoint sends its venue's documented JSON ping; a data silence
longer than the endpoint's stale bound forces a reconnect (`stale_feed`); reconnects back off
exponentially from `backoff_initial_seconds` to `backoff_max_seconds`, and the backoff resets once
a connection stayed up `healthy_seconds`. Known limit: the backoff has no jitter -- one recorder
per venue reconnects alone, so there is no herd to spread. Upgrade path: randomise the pause if
several recorders ever share one venue.
"""

import asyncio
import base64
import contextlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from verification.application import sites
from verification.application.ports import WS_BINARY
from verification.application.ports import WS_CLOSED
from verification.application.ports import WS_TEXT
from verification.application.ports import HttpClient
from verification.application.ports import HttpResult
from verification.application.ports import Ledger
from verification.application.ports import RawSink
from verification.application.ports import WsConnection
from verification.application.ports import WsConnector
from verification.application.ports import WsEvent
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import ANOMALY_UNKNOWN
from verification.domain.subscriptions import ANOMALY_UNPARSED
from verification.domain.subscriptions import ANOMALY_VENUE_ERROR
from verification.domain.subscriptions import CONNECTION_CHANNEL
from verification.domain.subscriptions import UNPARSED_CHANNEL
from verification.domain.subscriptions import Endpoint
from verification.domain.subscriptions import RestPoll
from verification.domain.subscriptions import classify_frame
from verification.domain.subscriptions import rest_refusal


logger = logging.getLogger(__name__)

_ANOMALY_SITES = MappingProxyType(
    {
        ANOMALY_UNPARSED: sites.UNPARSED,
        ANOMALY_UNKNOWN: sites.UNKNOWN_FRAME,
        ANOMALY_VENUE_ERROR: sites.VENUE_ERROR,
    }
)
_DETAIL_CHARS = 300

# Close reasons the recorder asked for itself: no ledger entry, no backoff before the next open.
SHUTDOWN = "shutdown"
PLAN_CHANGED = "plan_changed"
FORCED_RECONNECT = "forced_reconnect"


@dataclass(frozen=True)
class Timing:
    """The recorder's cadences, in seconds (the defaults are production's)."""

    backoff_initial_seconds: float = 1.0
    backoff_max_seconds: float = 60.0
    healthy_seconds: float = 60.0
    plan_reload_seconds: float = 30.0  # the collectors re-read their config.toml every 30 s too
    flush_seconds: float = 1.0
    rest_tick_seconds: float = 1.0
    supervise_seconds: float = 1.0
    shutdown_grace_seconds: float = 10.0


@dataclass(frozen=True)
class VenueWiring:
    """
    How a plan becomes connections and requests: its endpoints (with URLs), its REST polls and a
    poll's URL. Production passes `venue_urls.kernel_wiring()`; a test points them at a local
    server.
    """

    endpoints_for: Callable[[RecordingPlan], tuple[Endpoint, ...]]
    polls_for: Callable[[RecordingPlan], tuple[RestPoll, ...]]
    rest_url: Callable[[RecordingPlan, RestPoll], str]


def _utf8(body: bytes) -> str | None:
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return None


class _Deadlines:
    """When the next ping is due and when the data feed counts as stale, on the monotonic clock."""

    def __init__(self, now: float, endpoint: Endpoint) -> None:
        self._endpoint = endpoint
        self.last_data = now
        self.next_ping = now + endpoint.ping_seconds

    def wait_seconds(self, now: float) -> float:
        return max(0.0, min(self.next_ping, self.last_data + self._endpoint.stale_seconds) - now)

    def stale(self, now: float) -> bool:
        return now - self.last_data >= self._endpoint.stale_seconds

    def ping_due(self, now: float) -> bool:
        return now >= self.next_ping


class EndpointSession:
    """
    One endpoint's supervised connection: connect, subscribe, record, keep alive, and reconnect
    with backoff until `stop`. Invariant: every open, close and error writes a connection line.
    """

    def __init__(
        self,
        venue: str,
        endpoint: Endpoint,
        connector: WsConnector,
        sink: RawSink,
        ledger: Ledger,
        timing: Timing,
    ) -> None:
        self.venue = venue
        self.endpoint = endpoint
        self._connector = connector
        self._sink = sink
        self._ledger = ledger
        self._timing = timing
        self._conn: WsConnection | None = None
        self._deliberate: str | None = None
        self._stopping = False
        self._wake = asyncio.Event()
        self._backoff = timing.backoff_initial_seconds
        self._close_detail = ""
        self._pending_reason: str | None = None
        self._skip_pause = False
        self.task: asyncio.Task[None] | None = None

    @property
    def stopping(self) -> bool:
        """Whether `stop` was called: the task ending is then expected, not a crash."""
        return self._stopping

    def start(self, reason: str) -> None:
        """Run the session in its own task; `reason` tags the first `open` line."""
        self.task = asyncio.create_task(self._run(reason), name=f"recorder-{self.endpoint.name}")

    async def stop(self, reason: str) -> None:
        """End the session: its connection closes with `reason` and no reconnect follows."""
        self._stopping = True
        self._wake.set()
        await self._close_deliberately(reason)

    async def reconnect(self, reason: str) -> None:
        """
        Close the live connection from this side; the session reconnects at once. While no
        connection is up (a connect attempt in flight, or the pause between attempts) there is
        nothing to close: the reason is kept, the current or next pause ends at once (one pause,
        so a venue that stays down is still retried with backoff, never in a tight loop), and the
        next `open` line carries the reason, so the request is never silently dropped.
        """
        if self._conn is None:
            self._pending_reason = reason
            self._skip_pause = True
            self._wake.set()
            logger.info(
                "%s: %s requested while not connected; applied to the next open",
                self._label(),
                reason,
            )
            return
        await self._close_deliberately(reason)

    async def _close_deliberately(self, reason: str) -> None:
        conn = self._conn
        if conn is None:
            return
        self._deliberate = reason
        if not await self._close_quietly(conn):
            # The connection may still be up: its next real close must not pass as this one.
            self._deliberate = None

    async def _run(self, reason: str) -> None:
        while not self._stopping:
            reason = await self._cycle(reason)

    async def _cycle(self, reason: str) -> str:
        """One connection's whole life; returns the reason the next `open` line carries."""
        conn = await self._connect(reason)
        if conn is None:
            await self._pause()
            return "reconnect"
        opened = time.monotonic()
        try:
            closed = await self._serve(conn)
        except asyncio.CancelledError:  # the stop grace expired: the close still leaves its line
            self._connection_line("close", "cancelled", detail="stop grace expired")
            raise
        self._connection_line("close", closed, detail=self._close_detail)
        if time.monotonic() - opened >= self._timing.healthy_seconds:
            self._backoff = self._timing.backoff_initial_seconds
        deliberate, self._deliberate = self._deliberate, None
        if closed == deliberate:
            return closed
        if deliberate is not None:  # a requested reconnect lost the race to a real close
            self._pending_reason = self._pending_reason or deliberate
        await self._pause()
        return "reconnect"

    async def _connect(self, reason: str) -> WsConnection | None:
        try:
            conn = await self._connector.connect(self.endpoint.url)
        except Exception as exc:  # any connect failure: written, ledgered, retried with backoff
            self._connection_line("error", "connect_failed", error=repr(exc))
            self._ledger(sites.CONNECT, f"{self._label()}: {exc!r}", exc)
            return None
        if self._stopping:
            await self._close_quietly(conn)
            return None
        self._conn, self._close_detail = conn, ""
        reason, self._pending_reason = self._pending_reason or reason, None
        self._connection_line(
            "open", reason, url=self.endpoint.url, subscriptions=list(self.endpoint.subscriptions)
        )
        logger.info("%s: open (%s)", self._label(), reason)
        return conn

    async def _serve(self, conn: WsConnection) -> str:
        try:
            for message in self.endpoint.subscribe_messages:
                await conn.send_str(message)
            return await self._pump(conn)
        except Exception as exc:  # a transport failure mid-connection: ledgered, then reconnect
            if self._deliberate is not None:
                return self._deliberate
            self._connection_line("error", "transport_error", error=repr(exc))
            self._ledger(sites.CONNECTION, f"{self._label()}: {exc!r}", exc)
            return "error"
        finally:
            self._conn = None
            await self._close_quietly(conn)

    async def _pump(self, conn: WsConnection) -> str:
        deadlines = _Deadlines(time.monotonic(), self.endpoint)
        while True:
            event = await conn.receive(deadlines.wait_seconds(time.monotonic()))
            now = time.monotonic()
            if event is not None and event.kind in (WS_TEXT, WS_BINARY):
                if self._record(event):
                    deadlines.last_data = now
            elif event is not None:
                return self._ended(event)
            if deadlines.stale(now):
                self._close_detail = f"no data frame for {self.endpoint.stale_seconds} s"
                self._ledger(sites.STALE_FEED, f"{self._label()}: {self._close_detail}")
                return "stale_feed"
            if deadlines.ping_due(now):
                await conn.send_str(self.endpoint.ping_message)
                deadlines.next_ping = now + self.endpoint.ping_seconds

    def _ended(self, event: WsEvent) -> str:
        if self._deliberate is not None:
            self._close_detail = "closed by the recorder"
            return self._deliberate
        self._close_detail = str(event.data)
        self._ledger(sites.CONNECTION, f"{self._label()}: {event.kind}: {self._close_detail}")
        return "server_closed" if event.kind == WS_CLOSED else "error"

    def _record(self, event: WsEvent) -> bool:
        """Write one received frame; return whether it was data (it resets the stale clock)."""
        line: dict[str, object] = {"kind": "frame", "recv_ns": event.recv_ns}
        line["endpoint"] = self.endpoint.name
        if isinstance(event.data, bytes):
            line["raw_b64"] = base64.b64encode(event.data).decode()
            self._sink.write(UNPARSED_CHANNEL, event.recv_ns, line)
            self._ledger(sites.UNPARSED, f"{self._label()}: binary frame, {len(event.data)} bytes")
            return False
        line["raw"] = event.data
        classified = classify_frame(self.venue, self.endpoint.name, event.data)
        self._sink.write(classified.channel, event.recv_ns, line)
        if classified.anomaly is not None:
            detail = f"{self._label()}: {event.data[:_DETAIL_CHARS]}"
            self._ledger(_ANOMALY_SITES[classified.anomaly], detail)
            return False
        return classified.channel in self.endpoint.data_channels

    def _connection_line(self, event: str, reason: str, **fields: object) -> None:
        ts_ns = time.time_ns()
        line = {"kind": "connection", "event": event, "ts_ns": ts_ns}
        line |= {"endpoint": self.endpoint.name, "reason": reason, **fields}
        for channel in (CONNECTION_CHANNEL, *self.endpoint.data_channels):
            self._sink.write(channel, ts_ns, line)
        if event != "open":
            logger.info("%s: %s (%s) %s", self._label(), event, reason, fields or "")

    async def _pause(self) -> None:
        """
        Back off before the next attempt, unless stopping or a reconnect was requested while not
        connected (that skips one pause only). The backoff doubles only after a pause was waited.
        """
        if not self._stopping and not self._skip_pause:
            with contextlib.suppress(TimeoutError):  # the backoff elapsed: reconnect
                await asyncio.wait_for(self._wake.wait(), timeout=self._backoff)
            self._backoff = min(self._backoff * 2, self._timing.backoff_max_seconds)
        self._skip_pause = False
        self._wake.clear()

    async def _close_quietly(self, conn: WsConnection) -> bool:
        """Close `conn`; return whether the close succeeded (a failure is ledgered)."""
        try:
            await conn.close()
        except Exception as exc:  # a failed close still ends the connection for us
            self._ledger(sites.CONNECTION, f"{self._label()}: close failed", exc)
            return False
        return True

    def _label(self) -> str:
        return f"{self.venue} {self.endpoint.name}"


class RestPoller:
    """
    Runs the current plan's REST polls, each on its own schedule in its own task. Invariant:
    every poll writes one line -- the response verbatim with its status, the error, or
    `"error": "cancelled"` when a stop or a plan change cancels it in flight -- and a failed one is
    ledgered; a slow or failed poll never delays another poll or shifts its own next slot.
    """

    def __init__(
        self,
        plan_of: Callable[[], RecordingPlan],
        polls_for: Callable[[RecordingPlan], tuple[RestPoll, ...]],
        rest_url: Callable[[RecordingPlan, RestPoll], str],
        http: HttpClient,
        sink: RawSink,
        ledger: Ledger,
        tick_seconds: float,
    ) -> None:
        self._plan_of = plan_of
        self._polls_for = polls_for
        self._rest_url = rest_url
        self._http = http
        self._sink = sink
        self._ledger = ledger
        self._tick_seconds = tick_seconds

    async def run(self) -> None:
        """Keep one task per poll of the current plan; raise if one of them dies."""
        tasks: dict[RestPoll, asyncio.Task[None]] = {}
        try:
            while True:
                await self._reconcile(tasks)
                for poll, task in tasks.items():
                    if task.done():
                        cause = None if task.cancelled() else task.exception()
                        raise RuntimeError(f"REST poll {poll.channel} ended") from cause
                await asyncio.sleep(self._tick_seconds)
        finally:
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)

    async def _reconcile(self, tasks: dict[RestPoll, asyncio.Task[None]]) -> None:
        wanted = self._polls_for(self._plan_of())
        for poll in [poll for poll in tasks if poll not in wanted]:
            retired = tasks.pop(poll)
            retired.cancel()
            await asyncio.gather(retired, return_exceptions=True)
        for poll in wanted:
            if poll not in tasks:
                tasks[poll] = asyncio.create_task(self._repeat(poll), name=f"rest-{poll.channel}")

    async def _repeat(self, poll: RestPoll) -> None:
        """Poll on a fixed grid: an overrun skips to the next slot after it, never drifts."""
        due = time.monotonic()
        while True:
            await self.poll(self._plan_of(), poll)
            due += poll.every_seconds
            now = time.monotonic()
            if due <= now:
                due += poll.every_seconds * math.ceil((now - due) / poll.every_seconds)
            await asyncio.sleep(max(0.0, due - now))

    async def poll(self, plan: RecordingPlan, poll: RestPoll) -> None:
        """Send one poll and write its line."""
        line: dict[str, object] = {"kind": "rest", "sent_ns": time.time_ns()}
        line |= {"endpoint": poll.endpoint, "request": poll.request}
        try:
            result = await self._send(self._rest_url(plan, poll), poll)
        except asyncio.CancelledError:
            self._write_error(poll, line, "cancelled")  # a stop or plan change: no ledger entry
            raise
        except Exception as exc:  # timeout, DNS, reset: written with the error, ledgered
            self._write_error(poll, line, repr(exc))
            self._ledger(sites.REST, f"{plan.venue} {poll.channel}: {exc!r}", exc)
            return
        self._write_result(plan, poll, line, result)

    def _write_error(self, poll: RestPoll, line: dict[str, object], error: str) -> None:
        recv_ns = time.time_ns()
        line |= {"recv_ns": recv_ns, "status": None, "error": error}
        self._sink.write(poll.channel, recv_ns, line)

    async def _send(self, url: str, poll: RestPoll) -> HttpResult:
        if poll.method == "GET":
            return await self._http.get(url)
        return await self._http.post(url, poll.request)

    def _write_result(
        self, plan: RecordingPlan, poll: RestPoll, line: dict[str, object], result: HttpResult
    ) -> None:
        text = _utf8(result.body)
        line |= {"recv_ns": result.recv_ns, "status": result.status}
        if text is None:
            line["raw_b64"] = base64.b64encode(result.body).decode()
        else:
            line["raw"] = text
        refusal = rest_refusal(plan.venue, result.status, text)
        if refusal is not None:
            line["refusal"] = refusal
        self._sink.write(poll.channel, result.recv_ns, line)
        if refusal is not None:
            self._ledger(sites.REST, f"{plan.venue} {poll.channel} {poll.request}: {refusal}")


class Recorder:
    """
    One venue's reference recorder. Invariant: exactly one session runs per endpoint of the
    current plan; a plan change closes every old session (`plan_changed` close lines) before the
    new plan's sessions open (`plan_changed` open lines), and an unreadable plan keeps the last
    good one (ledgered).
    """

    def __init__(
        self,
        plan: RecordingPlan,
        read_plan: Callable[[], RecordingPlan],
        wiring: VenueWiring,
        connector: WsConnector,
        http: HttpClient,
        sink: RawSink,
        ledger: Ledger,
        timing: Timing,
    ) -> None:
        self.plan = plan
        self._read_plan = read_plan
        self._endpoints_for = wiring.endpoints_for
        self._polls = RestPoller(
            lambda: self.plan,
            wiring.polls_for,
            wiring.rest_url,
            http,
            sink,
            ledger,
            timing.rest_tick_seconds,
        )
        self._connector = connector
        self._sink = sink
        self._ledger = ledger
        self._timing = timing
        self.sessions: list[EndpointSession] = []
        self._crashed: set[asyncio.Task[None]] = set()  # ledgered once, by `_raise_if_ended`

    async def run(self, stop: asyncio.Event) -> None:
        """Record until `stop` is set, then close every connection and stream cleanly."""
        self.sessions = self._start(self.plan, self._endpoints_for(self.plan), "startup")
        background = [
            asyncio.create_task(self._plan_loop(), name="recorder-plan"),
            asyncio.create_task(self._flush_loop(), name="recorder-flush"),
            asyncio.create_task(self._polls.run(), name="recorder-rest"),
        ]
        try:
            while not stop.is_set():
                running = [s.task for s in self.sessions if s.task and not s.stopping]
                self._raise_if_ended([*background, *running])
                with contextlib.suppress(TimeoutError):  # supervise again
                    await asyncio.wait_for(stop.wait(), timeout=self._timing.supervise_seconds)
        finally:
            await self._shutdown(background)

    async def reconnect_all(self, reason: str = FORCED_RECONNECT) -> None:
        """Close every live connection from this side; each session reconnects at once."""
        await asyncio.gather(*(session.reconnect(reason) for session in self.sessions))

    def _start(
        self, plan: RecordingPlan, endpoints: tuple[Endpoint, ...], reason: str
    ) -> list[EndpointSession]:
        sessions = [
            EndpointSession(plan.venue, e, self._connector, self._sink, self._ledger, self._timing)
            for e in endpoints
        ]
        for session in sessions:
            session.start(reason)
        if not sessions:
            logger.warning("%s: the plan names no instrument; nothing is recorded", plan.venue)
        return sessions

    def _raise_if_ended(self, tasks: list[asyncio.Task[None]]) -> None:
        """Ledger and raise a loop that ended while recording: a bug that stops the process."""
        for task in tasks:
            if not task.done():
                continue
            cause = None if task.cancelled() else task.exception()
            self._crashed.add(task)
            self._ledger(sites.CRASH, f"{task.get_name()} ended while recording", cause)
            raise RuntimeError(f"{task.get_name()} ended while recording") from cause

    async def _plan_loop(self) -> None:
        while True:
            await asyncio.sleep(self._timing.plan_reload_seconds)
            try:
                plan = self._read_plan()
                endpoints = self._endpoints_for(plan)
            except (OSError, ValueError) as exc:
                self._ledger(sites.PLAN, f"{self.plan.venue}: keeping the last good plan", exc)
                continue
            if plan != self.plan:
                await self._apply(plan, endpoints)

    async def _apply(self, plan: RecordingPlan, endpoints: tuple[Endpoint, ...]) -> None:
        logger.info("%s: plan changed to %s", plan.venue, list(plan.instruments))
        await self._stop_sessions(PLAN_CHANGED)
        self.plan = plan
        self.sessions = self._start(plan, endpoints, PLAN_CHANGED)

    async def _flush_loop(self) -> None:
        while True:
            self._sink.tick(time.time_ns())
            await asyncio.sleep(self._timing.flush_seconds)

    async def _stop_sessions(self, reason: str) -> None:
        """
        Stop every tracked session. They stay in `self.sessions` until their tasks have ended, so
        a shutdown that interrupts a plan change still finds -- and stops -- the old sessions
        before the sink is closed.
        """
        sessions = list(self.sessions)
        await asyncio.gather(*(session.stop(reason) for session in sessions))
        tasks = [session.task for session in sessions if session.task is not None]
        if tasks:
            await self._await_stopped(tasks)
        self.sessions = [session for session in self.sessions if session not in sessions]

    async def _await_stopped(self, tasks: list[asyncio.Task[None]]) -> None:
        done, pending = await asyncio.wait(tasks, timeout=self._timing.shutdown_grace_seconds)
        for task in pending:
            task.cancel()  # stuck in a connect attempt: its `stop` has nothing to close
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            if task in self._crashed:
                continue  # already ledgered as the crash that ended the recording
            if not task.cancelled() and task.exception() is not None:
                self._ledger(sites.CRASH, f"{task.get_name()} failed", task.exception())

    async def _shutdown(self, background: list[asyncio.Task[None]]) -> None:
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await self._stop_sessions(SHUTDOWN)
        self._sink.close()
