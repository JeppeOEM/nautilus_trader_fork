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
The recorder's supervision loops against a real local aiohttp server on 127.0.0.1 (no network):
subscribe, record verbatim, ping, reconnect after a server close or a stale feed, connection lines
in every data channel, plan changes, REST polls and the ledger.
"""

import asyncio
import json
import time
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

from verification.application import sites
from verification.application.ports import WS_CLOSED
from verification.application.ports import WsEvent
from verification.application.recorder import Recorder
from verification.application.recorder import Timing
from verification.application.recorder import VenueWiring
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import Endpoint
from verification.domain.subscriptions import RestPoll
from verification.domain.subscriptions import rest_polls
from verification.domain.subscriptions import ws_endpoints
from verification.infrastructure.aiohttp_io import AiohttpConnector
from verification.infrastructure.aiohttp_io import AiohttpHttp
from verification.infrastructure.raw_store import FILE_SUFFIX
from verification.infrastructure.raw_store import RawStore
from verification.infrastructure.raw_store import iter_records
from verification.infrastructure.raw_store import venue_dir


_FAST = Timing(
    backoff_initial_seconds=0.05,
    backoff_max_seconds=0.2,
    healthy_seconds=30.0,
    plan_reload_seconds=0.1,
    flush_seconds=0.05,
    rest_tick_seconds=0.05,
    supervise_seconds=0.05,
    shutdown_grace_seconds=3.0,
)
_BTC = RecordingPlan("BYBIT", "mainnet", ("BTCUSDT-LINEAR.BYBIT",))
_BOOK = '{"topic":"orderbook.50.BTCUSDT","type":"snapshot","data":{"s":"BTCUSDT","u":1}}'

Behaviour = Callable[[int, web.WebSocketResponse], Awaitable[None]]


class _Ledger:
    def __init__(self) -> None:
        self.sites: list[str] = []

    def __call__(self, site: str, detail: str = "", exc: BaseException | None = None) -> None:
        self.sites.append(site)


class _Venue:
    """A local venue: one WebSocket route driven by `behaviour`, and REST routes."""

    def __init__(self, behaviour: Behaviour) -> None:
        self.behaviour = behaviour
        self.received: list[list[object]] = []  # per connection, the JSON messages received
        self.rest: list[str] = []
        self._runner: web.AppRunner | None = None
        self.port = 0

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/ws/{endpoint}", self._ws)
        app.router.add_get("/v5/market/{name}", self._rest)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self.port = self._runner.addresses[0][1]

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        index = len(self.received)
        self.received.append([])
        await self.behaviour(index, ws)
        return ws

    async def _rest(self, request: web.Request) -> web.Response:
        self.rest.append(request.path_qs)
        if request.match_info["name"] == "orderbook":
            return web.Response(status=500, text="upstream down")
        if request.match_info["name"] == "slow":
            await asyncio.sleep(2.0)
        return web.json_response({"retCode": 0, "result": {"path": request.path_qs}})

    async def read_until_closed(self, index: int, ws: web.WebSocketResponse) -> None:
        """Record every message; answer Bybit's ping as Bybit does."""
        async for message in ws:
            payload = json.loads(message.data)
            self.received[index].append(payload)
            if payload == {"op": "ping"}:
                await ws.send_str('{"success":true,"ret_msg":"pong","op":"ping"}')


Polls = Callable[[RecordingPlan], tuple[RestPoll, ...]]


def _wiring(
    venue: _Venue, tune: Callable[[Endpoint], Endpoint], polls: Polls = rest_polls
) -> VenueWiring:
    def endpoints(plan: RecordingPlan) -> tuple[Endpoint, ...]:
        found = ws_endpoints(plan, lambda name: f"ws://127.0.0.1:{venue.port}/ws/{name}")
        return tuple(tune(endpoint) for endpoint in found)

    def rest_url(plan: RecordingPlan, poll: RestPoll) -> str:
        return f"http://127.0.0.1:{venue.port}{poll.request}"

    return VenueWiring(endpoints, polls, rest_url)


async def _eventually(condition: Callable[[], bool], seconds: float = 10.0) -> None:
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.02)


async def _record(
    root: Path,
    venue: _Venue,
    until: Callable[[Recorder], Awaitable[None]],
    ledger: _Ledger,
    read_plan: Callable[[], RecordingPlan] = lambda: _BTC,
    tune: Callable[[Endpoint], Endpoint] = lambda e: replace(e, ping_seconds=0.1),
    polls: Polls = rest_polls,
) -> None:
    await venue.start()
    try:
        store = RawStore(root, "BYBIT", 7, ledger)
        stop = asyncio.Event()
        async with aiohttp.ClientSession() as session:
            recorder = Recorder(
                _BTC,
                read_plan,
                _wiring(venue, tune, polls),
                AiohttpConnector(session),
                AiohttpHttp(session),
                store,
                ledger,
                _FAST,
            )
            running = asyncio.create_task(recorder.run(stop))
            await until(recorder)
            stop.set()
            await running
    finally:
        await venue.stop()


def _patient(endpoint: Endpoint) -> Endpoint:
    """Keep a quiet test server's connection open: no stale-feed reconnect within a test."""
    return replace(endpoint, stale_seconds=5.0)


def _lines(root: Path, channel: str) -> list[dict[str, object]]:
    paths = sorted((venue_dir(root, "BYBIT") / channel).glob(f"*{FILE_SUFFIX}"))
    return [record for path in paths for record in iter_records(path)]


def _events(lines: list[dict[str, object]]) -> list[tuple[object, object]]:
    return [(l["event"], l["reason"]) for l in lines if l["kind"] == "connection"]


def test_records_verbatim_reconnects_after_a_server_close_and_pings(tmp_path: Path) -> None:
    ledger = _Ledger()

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await ws.receive()  # the subscribe message
        venue.received[index].append("subscribed")
        await ws.send_str(_BOOK)
        await ws.send_str("not json {")
        if index == 0:
            await ws.close()  # the server drops the first connection
            return
        await venue.read_until_closed(index, ws)

    venue = _Venue(behaviour)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: {"op": "ping"} in (venue.received[1:2] or [[]])[0])
        await _eventually(lambda: len(venue.rest) >= 3)

    asyncio.run(_record(tmp_path, venue, until, ledger))

    assert _events(_lines(tmp_path, "connection")) == [
        ("open", "startup"),
        ("close", "server_closed"),
        ("open", "reconnect"),
        ("close", "shutdown"),
    ]
    book = _lines(tmp_path, "linear.orderbook.50")
    frames = [line for line in book if line["kind"] == "frame"]
    assert [line["raw"] for line in frames] == [_BOOK, _BOOK]
    assert all(isinstance(line["recv_ns"], int) and line["endpoint"] == "linear" for line in frames)
    for channel in ("linear.orderbook.50", "linear.publicTrade", "linear.tickers"):
        assert _events(_lines(tmp_path, channel)) == _events(_lines(tmp_path, "connection"))
    assert [line["raw"] for line in _lines(tmp_path, "unparsed")] == ["not json {", "not json {"]
    assert sites.CONNECTION in ledger.sites
    assert ledger.sites.count(sites.UNPARSED) == 2
    (server_close,) = [l for l in _lines(tmp_path, "connection") if l["reason"] == "server_closed"]
    assert server_close["detail"] == "close code 1000", "the server's own close frame's code"


def test_the_open_line_names_the_subscriptions_the_server_received(tmp_path: Path) -> None:
    ledger = _Ledger()

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await venue.read_until_closed(index, ws)

    venue = _Venue(behaviour)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: bool(venue.received and venue.received[0]))

    asyncio.run(_record(tmp_path, venue, until, ledger, tune=_patient))

    (subscribe, *_) = venue.received[0]
    opened = _lines(tmp_path, "connection")[0]
    assert subscribe == {"op": "subscribe", "args": opened["subscriptions"]}
    assert opened["subscriptions"] == [
        "orderbook.50.BTCUSDT",
        "publicTrade.BTCUSDT",
        "tickers.BTCUSDT",
    ]
    assert str(opened["url"]).endswith("/ws/linear")


def test_a_silent_feed_is_reconnected_and_ledgered_even_while_pings_are_answered(
    tmp_path: Path,
) -> None:
    ledger = _Ledger()

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await venue.read_until_closed(index, ws)  # answers pings, sends no data

    venue = _Venue(behaviour)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(venue.received) >= 2)

    def tune(endpoint: Endpoint) -> Endpoint:
        return replace(endpoint, ping_seconds=0.05, stale_seconds=0.3)

    asyncio.run(_record(tmp_path, venue, until, ledger, tune=tune))

    assert _events(_lines(tmp_path, "connection"))[:3] == [
        ("open", "startup"),
        ("close", "stale_feed"),
        ("open", "reconnect"),
    ]
    assert sites.STALE_FEED in ledger.sites
    assert len(_lines(tmp_path, "linear.control")) >= 2, "the pongs are filed, not counted as data"


def test_a_plan_change_reconnects_with_the_new_set_and_a_bad_plan_is_kept_out(
    tmp_path: Path,
) -> None:
    ledger = _Ledger()
    reads: list[RecordingPlan | Exception] = [_BTC, ValueError("half-written file")]
    both = RecordingPlan("BYBIT", "mainnet", ("BTCUSDT-LINEAR.BYBIT", "ETHUSDT-LINEAR.BYBIT"))

    def read_plan() -> RecordingPlan:
        outcome = reads.pop(0) if reads else both
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await venue.read_until_closed(index, ws)

    venue = _Venue(behaviour)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(venue.received) >= 2 and bool(venue.received[1]))
        assert recorder.plan == both

    asyncio.run(_record(tmp_path, venue, until, ledger, read_plan=read_plan, tune=_patient))

    assert _events(_lines(tmp_path, "connection")) == [
        ("open", "startup"),
        ("close", "plan_changed"),
        ("open", "plan_changed"),
        ("close", "shutdown"),
    ]
    assert venue.received[1][0] == {
        "op": "subscribe",
        "args": [
            f"{kind}.{symbol}"
            for symbol in ("BTCUSDT", "ETHUSDT")
            for kind in ("orderbook.50", "publicTrade", "tickers")
        ],
    }
    assert ledger.sites.count(sites.PLAN) == 1


def test_rest_polls_write_every_response_and_ledger_the_failures(tmp_path: Path) -> None:
    ledger = _Ledger()

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await venue.read_until_closed(index, ws)

    venue = _Venue(behaviour)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(venue.rest) >= 4)

    asyncio.run(_record(tmp_path, venue, until, ledger, tune=_patient))

    (info,) = _lines(tmp_path, "linear.rest.instruments-info")
    assert info["status"] == 200
    assert info["request"] == "/v5/market/instruments-info?category=linear&symbol=BTCUSDT"
    assert json.loads(str(info["raw"]))["retCode"] == 0
    assert int(str(info["recv_ns"])) >= int(str(info["sent_ns"]))
    (book,) = _lines(tmp_path, "linear.rest.orderbook")
    assert (book["status"], book["raw"]) == (500, "upstream down")
    assert ledger.sites.count(sites.REST) == 1
    assert {line["kind"] for line in _lines(tmp_path, "linear.rest.recent-trade")} == {"rest"}


def test_unreachable_endpoints_are_written_ledgered_and_retried(tmp_path: Path) -> None:
    ledger = _Ledger()

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await venue.read_until_closed(index, ws)

    venue = _Venue(behaviour)

    def refused(endpoint: Endpoint) -> Endpoint:
        return replace(endpoint, url="ws://127.0.0.1:9/nothing-listens-here")

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: ledger.sites.count(sites.CONNECT) >= 2)

    asyncio.run(_record(tmp_path, venue, until, ledger, tune=refused))

    errors = _lines(tmp_path, "connection")
    assert len(errors) >= 2
    assert {(line["event"], line["reason"]) for line in errors} == {("error", "connect_failed")}
    assert all("error" in line for line in errors)


class _BrokenSink(RawStore):
    def tick(self, now_ns: int) -> None:
        raise RuntimeError("disk controller on fire")


def test_a_dead_loop_is_ledgered_and_stops_the_recorder(tmp_path: Path) -> None:
    ledger = _Ledger()

    async def run() -> None:
        async with aiohttp.ClientSession() as session:
            recorder = Recorder(
                RecordingPlan("BYBIT", "mainnet", ()),
                lambda: RecordingPlan("BYBIT", "mainnet", ()),
                _wiring(_Venue(lambda index, ws: asyncio.sleep(0)), lambda e: e),
                AiohttpConnector(session),
                AiohttpHttp(session),
                _BrokenSink(tmp_path, "BYBIT", 7, ledger),
                ledger,
                _FAST,
            )
            await recorder.run(asyncio.Event())

    with pytest.raises(RuntimeError, match="recorder-flush ended while recording"):
        asyncio.run(run())
    assert ledger.sites == [sites.CRASH]


def test_a_slow_poll_delays_no_other_poll_and_a_cancelled_one_still_writes_its_line(
    tmp_path: Path,
) -> None:
    ledger = _Ledger()

    def polls(plan: RecordingPlan) -> tuple[RestPoll, ...]:
        return tuple(
            RestPoll("linear", name, "GET", f"/v5/market/{name}", 0.05, f"linear.rest.{name}")
            for name in ("slow", "fast")
        )

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await venue.read_until_closed(index, ws)

    venue = _Venue(behaviour)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: venue.rest.count("/v5/market/fast") >= 10, seconds=1.5)
        assert venue.rest.count("/v5/market/slow") == 1, "still in its first request"

    asyncio.run(_record(tmp_path, venue, until, ledger, tune=_patient, polls=polls))

    assert len(_lines(tmp_path, "linear.rest.fast")) >= 10
    (slow,) = _lines(tmp_path, "linear.rest.slow")
    assert (slow["status"], slow["error"]) == (None, "cancelled")
    assert sites.REST not in ledger.sites, "a cancelled poll is a stop, not a failure"


def test_an_invalid_utf8_text_frame_ends_the_connection_on_the_record(tmp_path: Path) -> None:
    ledger = _Ledger()

    async def behaviour(index: int, ws: web.WebSocketResponse) -> None:
        await ws.receive()  # the subscribe message
        if index == 0:
            await ws.send_frame(b"\xff\xfe not utf-8", aiohttp.WSMsgType.TEXT)
        await venue.read_until_closed(index, ws)

    venue = _Venue(behaviour)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(venue.received) >= 2)

    asyncio.run(_record(tmp_path, venue, until, ledger, tune=_patient))

    closed = [l for l in _lines(tmp_path, "connection") if l["event"] == "close"]
    assert closed[0]["reason"] == "error"
    assert "ERROR" in str(closed[0]["detail"])
    assert sites.CONNECTION in ledger.sites


class _FakeConn:
    """A socket whose close takes a while, as a real close handshake can."""

    def __init__(self, closing: asyncio.Event) -> None:
        self._closed = asyncio.Event()
        self._closing = closing

    async def send_str(self, text: str) -> None:
        return None

    async def receive(self, timeout: float) -> WsEvent | None:
        try:
            await asyncio.wait_for(self._closed.wait(), timeout)
        except TimeoutError:
            return None
        return WsEvent(WS_CLOSED, "close code 1000", time.time_ns())

    async def close(self) -> None:
        self._closing.set()
        await asyncio.sleep(0.3)
        self._closed.set()


class _UnclosableConn(_FakeConn):
    """A socket whose close fails; the server drops it a moment later."""

    async def close(self) -> None:
        self._closing.set()
        asyncio.get_running_loop().call_later(0.2, self._closed.set)
        raise OSError("close handshake failed")


class _FakeConnector:
    """Hands out `_FakeConn`s, failing the first `failures` attempts; remembers each caller."""

    def __init__(self, failures: int = 0, conn: type[_FakeConn] = _FakeConn) -> None:
        self.failures = failures
        self.sessions: list[asyncio.Task[object]] = []
        self.closing = asyncio.Event()
        self._conn = conn

    async def connect(self, url: str) -> _FakeConn:
        task = asyncio.current_task()
        assert task is not None
        self.sessions.append(task)
        if self.failures:
            self.failures -= 1
            raise OSError("connection refused")
        return self._conn(self.closing)


class _ClosingSink(RawStore):
    """Remembers which session tasks were still running when the recorder closed it."""

    def __init__(self, root: Path, ledger: _Ledger, connector: _FakeConnector) -> None:
        super().__init__(root, "BYBIT", 7, ledger)
        self._connector = connector
        self.running_at_close: list[asyncio.Task[object]] | None = None

    def close(self) -> None:
        self.running_at_close = [t for t in self._connector.sessions if not t.done()]
        super().close()


async def _run_fake(
    root: Path,
    connector: _FakeConnector,
    until: Callable[[Recorder], Awaitable[None]],
    read_plan: Callable[[], RecordingPlan],
    timing: Timing = _FAST,
    tune: Callable[[Endpoint], Endpoint] = _patient,
    ledger: _Ledger | None = None,
) -> _ClosingSink:
    ledger = ledger if ledger is not None else _Ledger()
    sink = _ClosingSink(root, ledger, connector)
    venue = _Venue(lambda index, ws: asyncio.sleep(0))
    wiring = _wiring(venue, tune, polls=lambda plan: ())
    async with aiohttp.ClientSession() as session:
        recorder = Recorder(
            _BTC, read_plan, wiring, connector, AiohttpHttp(session), sink, ledger, timing
        )
        stop = asyncio.Event()
        running = asyncio.create_task(recorder.run(stop))
        await until(recorder)
        stop.set()
        await running
    return sink


def test_a_shutdown_during_a_plan_change_stops_every_session_before_the_sink_closes(
    tmp_path: Path,
) -> None:
    both = RecordingPlan("BYBIT", "mainnet", ("BTCUSDT-LINEAR.BYBIT", "ETHUSDT-LINEAR.BYBIT"))
    connector = _FakeConnector()

    async def until(recorder: Recorder) -> None:
        await asyncio.wait_for(connector.closing.wait(), 5.0)  # the plan change is closing

    sink = asyncio.run(_run_fake(tmp_path, connector, until, lambda: both))

    assert sink.running_at_close == []
    events = [(l["event"], l["reason"]) for l in _lines(tmp_path, "connection")]
    assert events[0] == ("open", "startup")
    assert events[-1][0] == "close"
    assert events.count(("open", "startup")) == len([e for e in events if e[0] == "close"])


def test_a_reconnect_requested_during_backoff_ends_it_and_tags_the_next_open(
    tmp_path: Path,
) -> None:
    connector = _FakeConnector(failures=1)
    slow_backoff = replace(_FAST, backoff_initial_seconds=30.0, backoff_max_seconds=30.0)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(connector.sessions) == 1)
        await asyncio.sleep(0.05)  # the failed attempt is now in its 30 s backoff
        await recorder.reconnect_all()
        await _eventually(lambda: len(connector.sessions) == 2, seconds=2.0)

    asyncio.run(_run_fake(tmp_path, connector, until, lambda: _BTC, timing=slow_backoff))

    events = [(l["event"], l["reason"]) for l in _lines(tmp_path, "connection")]
    assert events[:2] == [("error", "connect_failed"), ("open", "forced_reconnect")]


def test_a_reconnect_that_loses_the_race_to_a_stale_feed_still_tags_the_next_open(
    tmp_path: Path,
) -> None:
    """The feed goes stale while the requested close is still in its (slow) handshake."""
    connector = _FakeConnector()

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(connector.sessions) == 1)
        await recorder.reconnect_all()  # the close takes 0.3 s, the stale bound is 0.1 s
        await _eventually(lambda: len(connector.sessions) == 2, seconds=3.0)

    def quick_stale(endpoint: Endpoint) -> Endpoint:
        return replace(endpoint, stale_seconds=0.1)

    asyncio.run(_run_fake(tmp_path, connector, until, lambda: _BTC, tune=quick_stale))

    events = [(l["event"], l["reason"]) for l in _lines(tmp_path, "connection")]
    assert events[:3] == [
        ("open", "startup"),
        ("close", "stale_feed"),
        ("open", "forced_reconnect"),
    ]


def test_a_failed_deliberate_close_does_not_disguise_the_next_real_close(tmp_path: Path) -> None:
    connector = _FakeConnector(conn=_UnclosableConn)
    ledger = _Ledger()

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(connector.sessions) == 1)
        await recorder.reconnect_all()
        await _eventually(lambda: len(connector.sessions) == 2, seconds=3.0)

    asyncio.run(_run_fake(tmp_path, connector, until, lambda: _BTC, ledger=ledger))

    events = [(l["event"], l["reason"]) for l in _lines(tmp_path, "connection")]
    assert events[:2] == [("open", "startup"), ("close", "server_closed")]
    assert ledger.sites.count(sites.CONNECTION) >= 2  # the failed close, then the real close


def test_a_reconnect_while_the_venue_stays_down_skips_one_pause_never_all(
    tmp_path: Path,
) -> None:
    """A forced reconnect during an outage retries once at once, then backs off again."""
    connector = _FakeConnector(failures=1000)
    slow_backoff = replace(_FAST, backoff_initial_seconds=30.0, backoff_max_seconds=30.0)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(connector.sessions) == 1)
        await recorder.reconnect_all()
        await _eventually(lambda: len(connector.sessions) == 2, seconds=2.0)
        await asyncio.sleep(0.5)  # a skipped pause per attempt would retry hundreds of times

    asyncio.run(_run_fake(tmp_path, connector, until, lambda: _BTC, timing=slow_backoff))

    assert len(connector.sessions) == 2


class _WedgedSendConn(_FakeConn):
    """A socket whose subscribe send never completes, however it is closed."""

    async def send_str(self, text: str) -> None:
        await asyncio.Event().wait()

    async def close(self) -> None:
        self._closing.set()


def test_a_session_cancelled_at_the_stop_grace_still_writes_its_close_line(
    tmp_path: Path,
) -> None:
    connector = _FakeConnector(conn=_WedgedSendConn)
    short_grace = replace(_FAST, shutdown_grace_seconds=0.2)

    async def until(recorder: Recorder) -> None:
        await _eventually(lambda: len(connector.sessions) == 1)

    asyncio.run(_run_fake(tmp_path, connector, until, lambda: _BTC, timing=short_grace))

    events = [(l["event"], l["reason"]) for l in _lines(tmp_path, "connection")]
    assert events == [("open", "startup"), ("close", "cancelled")]
    trades = [l for l in _lines(tmp_path, "linear.publicTrade") if l["kind"] == "connection"]
    assert [l["event"] for l in trades] == ["open", "close"], "in every data channel too"


class _OpenRefusingSink(_ClosingSink):
    """Raises on the first `open` connection line: a bug that kills the session task."""

    def write(self, channel: str, ts_ns: int, line: Mapping[str, object]) -> None:
        if line.get("event") == "open":
            raise RuntimeError("sink bug")
        super().write(channel, ts_ns, line)


def test_a_crashed_session_is_ledgered_once(tmp_path: Path) -> None:
    ledger = _Ledger()
    connector = _FakeConnector()
    sink = _OpenRefusingSink(tmp_path, ledger, connector)
    venue = _Venue(lambda index, ws: asyncio.sleep(0))
    wiring = _wiring(venue, _patient, polls=lambda plan: ())

    async def run() -> None:
        async with aiohttp.ClientSession() as session:
            recorder = Recorder(
                _BTC, lambda: _BTC, wiring, connector, AiohttpHttp(session), sink, ledger, _FAST
            )
            await recorder.run(asyncio.Event())

    with pytest.raises(RuntimeError, match="ended while recording"):
        asyncio.run(run())

    assert ledger.sites.count(sites.CRASH) == 1
