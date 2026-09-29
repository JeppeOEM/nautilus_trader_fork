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
The aiohttp adapters of the recorder's ports: a WebSocket connector and a REST client.

Invariant: the receive time is `time.time_ns()` taken the moment aiohttp hands a message over,
before anything parses its payload, and a payload is passed on exactly as received (text frames
as the decoded text, REST bodies as bytes). aiohttp answers protocol-level pings itself
(`autoping`); the venues' application-level JSON pings are the recorder's.

Known limit: a TEXT frame that is not valid UTF-8 never reaches the recorder as a frame. aiohttp
decodes text frames itself, and on invalid UTF-8 it fails the connection with close code 1007
(`WSMsgType.ERROR` from `receive`), so the frame's bytes are lost. The loss is still recorded:
`receive` returns a `WS_ERROR` event, the recorder writes the `close` connection line (reason
`error`, the aiohttp error as `detail`) and ledgers `verification.recorder.connection`
(`verification/tests/test_recorder_loop.py` pins it). Neither venue documents non-UTF-8 text.
Upgrade path: receive raw frames (`aiohttp`'s `decode_text=False`) and decode them here, filing
an undecodable one as `raw_b64` in `unparsed`.

Known limit: `recv_ns` is when the recorder's event loop takes the message from aiohttp's queue,
not when its bytes reached the socket. Everything the recorder does runs on that one loop -- the
zstd compress and write of every line, `json.loads` of each frame and REST body, and at an hour's
first write the repair of a crash-truncated file (a whole-file decode and rewrite) and the
rotation's bytes-per-day glob and prune -- so frames that queue up during such a stall all carry
the stall's end as their receive time. A steady-state line costs microseconds; the repair of a
large hour file after a crash can cost hundreds of milliseconds, once. A comparator (Stories
31.4-31.9) must treat `recv_ns` as an upper bound with that skew, never as exact arrival time.
Upgrade path: run the sink's file work (`RawStore.write`/`tick`) in a dedicated writer thread fed
by a queue, so the loop only stamps and enqueues.
"""

import asyncio
import time

import aiohttp
from kernel.venue_http import USER_AGENT

from verification.application.ports import WS_BINARY
from verification.application.ports import WS_CLOSED
from verification.application.ports import WS_ERROR
from verification.application.ports import WS_TEXT
from verification.application.ports import HttpResult
from verification.application.ports import WsEvent


CONNECT_TIMEOUT_SECONDS = 15.0
CLOSE_TIMEOUT_SECONDS = 5.0
REST_TIMEOUT_SECONDS = 10.0
# aiohttp's 4 MiB default would refuse a large snapshot frame as an error; 16 MiB is far above
# any book snapshot either venue sends (a Bybit orderbook.50 snapshot is a few KiB).
MAX_MESSAGE_BYTES = 16 * 1024 * 1024
# aiohttp treats a receive timeout of 0 as "no timeout" (it would block): never pass less.
_MIN_RECEIVE_TIMEOUT = 0.001
_CLOSE_TYPES = frozenset(
    {aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSED}
)


class AiohttpWs:
    """One open aiohttp client WebSocket as the `WsConnection` port."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws

    async def send_str(self, text: str) -> None:
        await self._ws.send_str(text)

    async def receive(self, timeout: float) -> WsEvent | None:
        try:
            message = await self._ws.receive(timeout=max(timeout, _MIN_RECEIVE_TIMEOUT))
        except TimeoutError:
            return None
        recv_ns = time.time_ns()
        if message.type == aiohttp.WSMsgType.TEXT:
            return WsEvent(WS_TEXT, message.data, recv_ns)
        if message.type == aiohttp.WSMsgType.BINARY:
            return WsEvent(WS_BINARY, message.data, recv_ns)
        if message.type == aiohttp.WSMsgType.CLOSE:  # the server's own close frame: its code
            reason = f" {message.extra}" if message.extra else ""
            return WsEvent(WS_CLOSED, f"close code {message.data}{reason}", recv_ns)
        if message.type in _CLOSE_TYPES:
            return WsEvent(WS_CLOSED, f"close code {self._ws.close_code}", recv_ns)
        return WsEvent(WS_ERROR, f"{message.type.name}: {message.data!r}", recv_ns)

    async def close(self) -> None:
        await self._ws.close()


class AiohttpConnector:
    """Opens WebSockets on one shared `aiohttp.ClientSession` (the `WsConnector` port)."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def connect(self, url: str) -> AiohttpWs:
        ws = await asyncio.wait_for(
            self._session.ws_connect(
                url,
                autoping=True,
                heartbeat=None,
                timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=CLOSE_TIMEOUT_SECONDS),
                max_msg_size=MAX_MESSAGE_BYTES,
                headers={"User-Agent": USER_AGENT},
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        return AiohttpWs(ws)


class AiohttpHttp:
    """The `HttpClient` port on one shared `aiohttp.ClientSession`, one bounded request each."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=REST_TIMEOUT_SECONDS)

    async def get(self, url: str) -> HttpResult:
        headers = {"User-Agent": USER_AGENT}
        async with self._session.get(url, headers=headers, timeout=self._timeout) as response:
            body = await response.read()
            return HttpResult(response.status, body, time.time_ns())

    async def post(self, url: str, body: str) -> HttpResult:
        headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}
        async with self._session.post(
            url, data=body.encode(), headers=headers, timeout=self._timeout
        ) as response:
            payload = await response.read()
            return HttpResult(response.status, payload, time.time_ns())
