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
`observability.notify`: every adapter against a real local HTTP server (no mock of `urllib`), the
frozen payload shapes (AD-D12), and failures landing in the error ledger without a secret URL.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

import pytest

from observability import error_ledger
from observability import notify


_DEAD_URL = "http://127.0.0.1:1"  # nothing listens on port 1: connection refused


_Requests = list[tuple[str, dict[str, str], bytes]]


def _serve(received: _Requests) -> HTTPServer:
    """Start a local server recording every POST as (path, headers, body); caller `_stop`s it."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            received.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _stop(server: HTTPServer) -> None:
    """Stop serving and close the listening socket: shutdown() alone leaves it open until GC."""
    server.shutdown()
    server.server_close()


def _url(server: HTTPServer) -> str:
    return f"http://127.0.0.1:{server.server_port}"


def test_operator_posts_the_body_with_a_title_header_to_ntfy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: _Requests = []
    server = _serve(received)
    try:
        monkeypatch.setenv("WATCHDOG_NTFY_URL", f"{_url(server)}/topic")
        notify.notify(notify.OPERATOR, "BybitClient", "feed may be down")
    finally:
        _stop(server)
    [(path, headers, body)] = received
    assert (path, headers["Title"], body) == ("/topic", "BybitClient", b"feed may be down")


def test_operator_without_ntfy_url_logs_critical(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv("WATCHDOG_NTFY_URL", raising=False)
    with caplog.at_level(logging.CRITICAL, logger="observability.notify"):
        notify.notify(notify.OPERATOR, "collector", "every book stale")
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.CRITICAL, "every book stale")
    ]


def test_failed_ntfy_post_is_ledgered_without_the_topic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    error_ledger.reset()
    monkeypatch.setenv("WATCHDOG_NTFY_URL", f"{_DEAD_URL}/secret-topic")
    notify.notify(notify.OPERATOR, "collector", "x")
    assert error_ledger.counts() == {"observability.notify.ntfy": 1}
    assert "URLError" in caplog.text
    assert "secret-topic" not in caplog.text
    error_ledger.reset()


def _serve_status(status: int) -> HTTPServer:
    """Start a local server answering every POST with `status`; caller `_stop`s it."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(status)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_rejected_send_keeps_the_http_status_not_the_url(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A 401 must read differently from a 404 in the ledger; the topic URL still never appears."""
    error_ledger.reset()
    server = _serve_status(401)
    try:
        monkeypatch.setenv("WATCHDOG_NTFY_URL", f"{_url(server)}/secret-topic")
        notify.notify(notify.OPERATOR, "collector", "x")
    finally:
        _stop(server)
    assert error_ledger.counts() == {"observability.notify.ntfy": 1}
    assert "HTTPError: HTTP 401" in caplog.text
    assert "secret-topic" not in caplog.text
    error_ledger.reset()


def test_refused_connection_keeps_the_socket_reason_not_the_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    error_ledger.reset()
    notify.notify(notify.webhook_channel(f"{_DEAD_URL}/hook-secret"), "alert a1", "x")
    assert "URLError: [Errno" in caplog.text
    assert "Connection refused" in caplog.text
    assert "hook-secret" not in caplog.text
    error_ledger.reset()


@pytest.mark.parametrize(
    ("channel", "env", "value", "site"),
    [
        ("operator", "WATCHDOG_NTFY_URL", "not a url secret-topic", "observability.notify.ntfy"),
        ("telegram", "TELEGRAM_API_BASE", "no-scheme.example", "observability.notify.telegram"),
    ],
)
def test_malformed_url_is_ledgered_not_raised(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    channel: str,
    env: str,
    value: str,
    site: str,
) -> None:
    error_ledger.reset()
    monkeypatch.setenv(env, value)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    notify.notify(channel, "t", "b")  # must not raise into a watchdog loop or alert thread
    assert error_ledger.counts() == {site: 1}
    assert "ValueError" in caplog.text
    assert "secret" not in caplog.text
    error_ledger.reset()


def test_malformed_webhook_url_is_ledgered_not_raised() -> None:
    error_ledger.reset()
    notify.notify(notify.webhook_channel("no-scheme.example/hook"), "alert a1", "x")
    assert error_ledger.counts() == {"observability.notify.webhook": 1}
    error_ledger.reset()


def test_deeply_nested_json_body_is_sent_as_text() -> None:
    received: _Requests = []
    server = _serve(received)
    body = "[" * 100_000 + "]" * 100_000
    try:
        notify.notify(notify.webhook_channel(f"{_url(server)}/hook"), "alert a1", body)
    finally:
        _stop(server)
    [(_, headers, _)] = received
    assert headers["Content-Type"] == "text/plain"


def test_telegram_sends_chat_id_and_text_to_the_bot_api(monkeypatch: pytest.MonkeyPatch) -> None:
    received: _Requests = []
    server = _serve(received)
    try:
        monkeypatch.setenv("TELEGRAM_API_BASE", _url(server))
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
        notify.notify(notify.TELEGRAM, "alert a1", "BTC crossed 65000")
    finally:
        _stop(server)
    [(path, headers, body)] = received
    assert path == "/bot123:abc/sendMessage"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {"chat_id": "42", "text": "BTC crossed 65000"}


def test_failed_telegram_send_is_ledgered_without_the_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    error_ledger.reset()
    monkeypatch.setenv("TELEGRAM_API_BASE", _DEAD_URL)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    notify.notify(notify.TELEGRAM, "alert a1", "x")
    details = error_ledger.last_details()
    assert error_ledger.counts() == {"observability.notify.telegram": 1}
    assert details["observability.notify.telegram"] == "alert a1: telegram send failed"
    assert "URLError" in caplog.text  # the type name is kept for diagnosis
    assert "secret-token" not in caplog.text
    error_ledger.reset()


def test_telegram_without_credentials_is_a_programming_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert notify.telegram_configured() is False
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        notify.notify(notify.TELEGRAM, "alert a1", "x")


@pytest.mark.parametrize(
    ("body", "content_type"),
    [('{"close": 1.5}', "application/json"), ("BTC crossed", "text/plain")],
)
def test_webhook_posts_the_raw_body_with_its_content_type(body: str, content_type: str) -> None:
    received: _Requests = []
    server = _serve(received)
    try:
        notify.notify(notify.webhook_channel(f"{_url(server)}/hook"), "alert a1", body)
    finally:
        _stop(server)
    [(path, headers, sent)] = received
    assert (path, headers["Content-Type"], sent) == ("/hook", content_type, body.encode())


def test_failed_webhook_is_ledgered_without_the_url(caplog: pytest.LogCaptureFixture) -> None:
    error_ledger.reset()
    notify.notify(notify.webhook_channel(f"{_DEAD_URL}/hook-secret"), "alert a1", "x")
    assert error_ledger.counts() == {"observability.notify.webhook": 1}
    assert "hook-secret" not in caplog.text
    error_ledger.reset()


@pytest.mark.parametrize("channel", ["nope", "webhook:", "Operator"])
def test_unknown_channel_raises_and_sends_nothing(channel: str) -> None:
    with pytest.raises(ValueError, match="unknown notification channel"):
        notify.notify(channel, "t", "b")


def test_webhook_channel_needs_a_url() -> None:
    with pytest.raises(ValueError, match="needs a URL"):
        notify.webhook_channel("")
