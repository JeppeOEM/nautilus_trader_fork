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
`NotifyDeliverer`: an alert names channels, `observability.notify` owns the transports (moved from
`data_api/tests/test_alerts.py` in Story 24.3; a real local HTTP server stands in for the Bot API).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer

import pytest
from observability import error_ledger

from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.infrastructure.deliverer import NotifyDeliverer


def _alert(**kw: object) -> Alert:
    fields: dict[str, object] = {
        "instrument_id": "BTC-USD-PERP.DYDX",
        "level": 100.0,
        "frequency": "only_once",
        "bar_seconds": 60,
        "template": "{{ticker}} {{close}}",
        "webhook_url": "http://localhost:1/hook",
    }
    fields.update(kw)
    return new_alert(**fields)


def test_failed_webhook_is_ledgered_with_alert_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    error_ledger.reset()
    alert = _alert(webhook_url="http://127.0.0.1:1/hook")  # nothing listens on port 1
    NotifyDeliverer().deliver(alert, "x")
    assert error_ledger.counts() == {"observability.notify.webhook": 1}
    assert f"alert {alert.id}" in error_ledger.last_details()["observability.notify.webhook"]
    error_ledger.reset()


def test_alert_names_channels_never_transports(monkeypatch: pytest.MonkeyPatch) -> None:
    deliverer = NotifyDeliverer()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert deliverer.channels(_alert(webhook_url="")) == ()
    assert deliverer.channels(_alert(webhook_url="https://h/x")) == ("webhook:https://h/x",)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert deliverer.channels(_alert(webhook_url="https://h/x")) == (
        "webhook:https://h/x",
        "telegram",
    )


def test_deliver_sends_telegram_message_to_bot_api(monkeypatch: pytest.MonkeyPatch) -> None:
    received: list[tuple[str, dict]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            received.append((self.path, json.loads(self.rfile.read(length))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("TELEGRAM_API_BASE", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    NotifyDeliverer().deliver(_alert(webhook_url=""), "BTC crossed 65000")
    server.shutdown()
    assert received == [("/bot123:abc/sendMessage", {"chat_id": "42", "text": "BTC crossed 65000"})]
