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
The reference recorder's ports (DESIGN-01: `typing.Protocol`, wired explicitly by the composition
root): the WebSocket, the HTTP client, the raw sink and the ledger. The aiohttp and zstd adapters
live in `verification/infrastructure/`; tests drive the loops against a local aiohttp server and a
real sink.
"""

from collections.abc import Mapping
from typing import NamedTuple
from typing import Protocol


WS_TEXT = "text"
WS_BINARY = "binary"
WS_CLOSED = "closed"
WS_ERROR = "error"


class WsEvent(NamedTuple):
    """
    One received WebSocket event. `recv_ns` is `time.time_ns()` taken as the adapter's receive
    returned, before anything parses the payload; `data` is the text or bytes of a message, or a
    description of a close or error.
    """

    kind: str
    data: str | bytes
    recv_ns: int


class WsConnection(Protocol):
    """One open WebSocket. `receive` returns None when `timeout` seconds pass with no event."""

    async def send_str(self, text: str) -> None: ...

    async def receive(self, timeout: float) -> WsEvent | None: ...

    async def close(self) -> None: ...


class WsConnector(Protocol):
    """Opens a WebSocket; raises on failure (the recorder writes an `error` line and backs off)."""

    async def connect(self, url: str) -> WsConnection: ...


class HttpResult(NamedTuple):
    """A REST response: its status, its body bytes verbatim and when the body was fully read."""

    status: int
    body: bytes
    recv_ns: int


class HttpClient(Protocol):
    """Sends one REST request; raises on a transport failure or timeout."""

    async def get(self, url: str) -> HttpResult: ...

    async def post(self, url: str, body: str) -> HttpResult: ...


class RawSink(Protocol):
    """
    Where every line goes: `write` files one JSON object under `channel` in the UTC hour of
    `ts_ns`; `tick` flushes and rotates (called at least every second); `close` ends every stream.
    """

    def write(self, channel: str, ts_ns: int, line: Mapping[str, object]) -> None: ...

    def tick(self, now_ns: int) -> None: ...

    def close(self) -> None: ...


class Ledger(Protocol):
    """`observability.error_ledger.record`'s signature."""

    def __call__(self, site: str, detail: str = "", exc: BaseException | None = None) -> None: ...
