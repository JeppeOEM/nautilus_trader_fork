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
The one outbound notification transport (spine AD-D16): `notify(channel, title, body)`.

A caller names a *channel*, never a transport; the adapter behind each channel is chosen here
from the environment (read at call time, so a test can point it at a local server):

- `OPERATOR` ("operator"): POST `body` to the ntfy.sh-compatible `WATCHDOG_NTFY_URL` with a
  `Title: <title>` header; with the variable unset, `body` is logged at CRITICAL instead.
- `TELEGRAM` ("telegram"): Bot API `sendMessage` with JSON `{"chat_id", "text": body}` at
  `TELEGRAM_API_BASE` (default `https://api.telegram.org`), needing `TELEGRAM_BOT_TOKEN` and
  `TELEGRAM_CHAT_ID`.
- `webhook_channel(url)` ("webhook:<url>"): POST `body` to that URL, `Content-Type`
  `application/json` when the body parses as JSON, else `text/plain`.

Payload shapes are the frozen published contract (AD-D12). `title` only reaches the ntfy header;
for the other adapters it names the notification in the error ledger. A failed send is never
retried (the caller's next event is the retry) and is ledgered, never logged-and-forgotten
(DATA-07), under `observability.notify.<transport>`. Every transport's failure is ledgered
without the URL: an ntfy topic URL is a bearer secret, the Telegram URL carries the bot token and
a webhook URL often a hook secret, and a traceback or an exception message can repeat the URL.
What is kept is what diagnoses the failure and cannot carry the URL's path (the topic, the token,
the hook secret): the exception's type name, an HTTP status (`HTTPError.code`) and a socket-level
reason (`URLError.reason` when it is an `OSError`, e.g. `[Errno 111] Connection refused`; an SSL
or DNS reason may name the host, never the path), so a 401 reads differently from a 404 or a
refused connection. A malformed URL (from the environment or an alert) is such a failure too:
the request is built inside the guarded block, so it never raises out of `notify` into a
watchdog loop or an alert thread.
"""

import json
import logging
import os
import urllib.error
import urllib.request

from observability import error_ledger


logger = logging.getLogger(__name__)

OPERATOR = "operator"
TELEGRAM = "telegram"
_WEBHOOK_PREFIX = "webhook:"

_NTFY_TIMEOUT_S = 10
_ALERT_TIMEOUT_S = 5
_DEFAULT_TELEGRAM_API_BASE = "https://api.telegram.org"


class NotificationFailed(Exception):
    """
    The ledgered stand-in for a failed send on any transport: carries the original exception's
    type name and its URL-free detail (`_failure_detail`), so neither its message nor a
    traceback can leak a secret URL.
    """


def webhook_channel(url: str) -> str:
    """Return the channel name for a generic webhook at `url`."""
    if not url:
        raise ValueError("a webhook channel needs a URL")
    return _WEBHOOK_PREFIX + url


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def notify(channel: str, title: str, body: str) -> None:
    """
    Send `body` on `channel`. Blocking network I/O: call it from a worker thread
    (`asyncio.to_thread`) on an event loop. An unknown or unconfigured channel is a programming
    error and raises `ValueError`; a failed send is ledgered and returns.
    """
    if channel == OPERATOR:
        _send_ntfy(title, body)
    elif channel == TELEGRAM:
        _send_telegram(title, body)
    elif channel.startswith(_WEBHOOK_PREFIX) and len(channel) > len(_WEBHOOK_PREFIX):
        _send_webhook(channel[len(_WEBHOOK_PREFIX) :], title, body)
    else:
        raise ValueError(f"unknown notification channel {channel!r}")


def _post(request: urllib.request.Request, timeout_s: int) -> None:
    with urllib.request.urlopen(request, timeout=timeout_s):  # noqa: S310 (operator-set URLs)
        pass


def _send_ntfy(title: str, body: str) -> None:
    url = os.environ.get("WATCHDOG_NTFY_URL")
    if not url:
        logger.critical(body)
        return
    try:
        # Inside the try: a malformed WATCHDOG_NTFY_URL raises ValueError here, and a watchdog
        # loop must not die of it.
        request = urllib.request.Request(  # noqa: S310 (fixed, operator-configured URL)
            url,
            data=body.encode(),
            headers={"Title": title},
            method="POST",
        )
        _post(request, _NTFY_TIMEOUT_S)
    except Exception as e:
        _record_sanitized("observability.notify.ntfy", f"{title}: ntfy POST failed", e)


def _send_telegram(title: str, body: str) -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        raise ValueError("telegram channel used without TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID")
    base = os.environ.get("TELEGRAM_API_BASE", _DEFAULT_TELEGRAM_API_BASE)
    try:
        # Inside the try: a malformed TELEGRAM_API_BASE raises ValueError here.
        request = urllib.request.Request(  # noqa: S310 (fixed Bot API base)
            f"{base}/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": body}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        _post(request, _ALERT_TIMEOUT_S)
    except Exception as e:
        _record_sanitized("observability.notify.telegram", f"{title}: telegram send failed", e)


def _send_webhook(url: str, title: str, body: str) -> None:
    try:
        json.loads(body)
        content_type = "application/json"
    except (ValueError, RecursionError):  # RecursionError: pathologically nested JSON
        content_type = "text/plain"
    try:
        # Inside the try: a URL the route's scheme check let through can still be malformed.
        request = urllib.request.Request(  # noqa: S310 (scheme validated on alert create)
            url,
            data=body.encode(),
            headers={"Content-Type": content_type},
            method="POST",
        )
        _post(request, _ALERT_TIMEOUT_S)
    except Exception as e:
        _record_sanitized("observability.notify.webhook", f"{title}: webhook POST failed", e)


def _failure_detail(exc: Exception) -> str:
    """Return the exception's type name plus the parts of a urllib failure that cannot repeat the URL's path."""
    name = type(exc).__name__
    if isinstance(exc, urllib.error.HTTPError):
        return f"{name}: HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, OSError):
        return f"{name}: {exc.reason}"  # errno text or an SSL/DNS reason: the host at most
    return name  # e.g. ValueError("unknown url type: '<the URL>'"): the message is the URL


def _record_sanitized(site: str, detail: str, exc: Exception) -> None:
    error_ledger.record(site, detail, NotificationFailed(_failure_detail(exc)))
