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
"""
Stories 20.1/20.2: saved alerts + the evaluation engine.

Alerts are delivered as a plain webhook POST (plus a toast pushed over `/ws/live`) -- there is
deliberately no Telegram/Discord/Slack/email integration; whatever the webhook URL points at is
the user's own infrastructure.

Persistence mirrors `ml_signals/chart_indicator_config.py` (TOML, full rewrite). The engine is a
server-side observer of `LiveCandleBus` (the one existing `snapshots:raw` subscriber), so alerts
fire with no browser tab open and there is no second Redis subscription or polling loop.

Evaluation is split so the frequency/expiration rules are testable without any network:
`evaluate()` is a pure state-machine step, `render()` a pure string substitution.
"""

import asyncio
import json
import logging
import os
import threading
import time
import tomllib
import urllib.request
import uuid
from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable

import tomli_w

from data_api.redis_bus import QUEUE_MAX
from data_api.redis_bus import put_drop_oldest


logger = logging.getLogger(__name__)

ALERTS_PATH: str = os.environ.get("ALERTS_PATH", "troll/data_api/alerts.toml")

FREQUENCIES = ("once_per_bar_close", "once_per_bar", "only_once")

_NS_PER_S = 1_000_000_000
_WEBHOOK_TIMEOUT_S = 5


@dataclass
class Alert:
    """`level` is always a static price: a horizontal-line condition is resolved to the line's
    price by the dialog at creation time (the line itself is client-side chart state)."""

    id: str
    instrument_id: str
    level: float
    frequency: str
    bar_seconds: int
    template: str
    webhook_url: str
    created_ns: int
    expires_at_ns: int | None = None
    triggered: bool = False  # only `only_once` alerts ever set this
    last_fired_ns: int | None = None


def status_of(alert: Alert, now_ns: int) -> str:
    if alert.triggered:
        return "triggered"
    if alert.expires_at_ns is not None and now_ns >= alert.expires_at_ns:
        return "expired"
    return "active"


class AlertStore:
    """In-memory list mirrored to a TOML file on every change. A corrupt file raises on load
    rather than starting empty -- the next save would otherwise silently destroy it."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._alerts: list[Alert] = self._load()

    def _load(self) -> list[Alert]:
        if not self._path.exists():
            return []
        with self._path.open("rb") as f:
            raw = tomllib.load(f)
        return [Alert(**entry) for entry in raw.get("alerts", [])]

    def _save(self) -> None:
        # TOML has no null: drop None fields, Alert's defaults restore them on load.
        entries = [{k: v for k, v in asdict(a).items() if v is not None} for a in self._alerts]
        with self._path.open("wb") as f:
            tomli_w.dump({"alerts": entries}, f)

    def list(self) -> list[Alert]:
        with self._lock:
            return list(self._alerts)

    def add(self, alert: Alert) -> None:
        with self._lock:
            self._alerts.append(alert)
            self._save()

    def delete(self, alert_id: str) -> bool:
        with self._lock:
            kept = [a for a in self._alerts if a.id != alert_id]
            if len(kept) == len(self._alerts):
                return False
            self._alerts = kept
            self._save()
            return True

    def record_fire(self, alert: Alert, ts_ns: int) -> None:
        with self._lock:
            alert.last_fired_ns = ts_ns
            alert.triggered = alert.frequency == "only_once"
            try:
                self._save()
            except OSError:
                # The in-memory state still stops an only_once repeat this run; a failed save
                # must never swallow the fire itself.
                logger.exception("alert %s fired but could not be persisted", alert.id)


store = AlertStore(Path(ALERTS_PATH))


def new_alert(**fields: Any) -> Alert:
    return Alert(id=uuid.uuid4().hex, created_ns=time.time_ns(), **fields)


@dataclass
class RunState:
    """Per-alert, in-memory only: a restart forgets the previous price, so the first tick after
    a restart can never fire (a cross needs two observations)."""

    last_price: float | None = None
    prev_close: float | None = None  # once_per_bar_close: close of the bar before the last one
    bucket: int | None = None
    fired_bucket: int | None = None


def _crossed(prev: float | None, cur: float, level: float) -> bool:
    return prev is not None and (prev < level <= cur or prev > level >= cur)


def evaluate(alert: Alert, state: RunState, price: float, ts_ns: int) -> float | None:
    """One tick of the frequency/expiration state machine. Returns the price to report when
    `alert` fires (for once_per_bar_close that is the closed bar's close, not this tick), else None."""
    if status_of(alert, ts_ns) != "active":
        return None
    bucket = ts_ns // (alert.bar_seconds * _NS_PER_S)
    if alert.frequency == "once_per_bar_close":
        # Only bar closes are compared: a bar's close is the last price seen before the
        # bucket rolls over, so the crossing is decided once, on the first tick of the next bar.
        closed_bar_close = None
        if state.bucket is not None and bucket != state.bucket:
            if _crossed(state.prev_close, state.last_price, alert.level):
                closed_bar_close = state.last_price
            state.prev_close = state.last_price
        state.bucket, state.last_price = bucket, price
        return closed_bar_close
    crossed = _crossed(state.last_price, price, alert.level)
    state.last_price = price
    if not crossed:
        return None
    if alert.frequency == "once_per_bar":
        if state.fired_bucket == bucket:
            return None
        state.fired_bucket = bucket
    return price


def render(template: str, ticker: str, close: float, ts_ns: int, bar_seconds: int) -> str:
    """Plain replace over the four documented placeholders (`{{interval}}` is bar seconds)."""
    time_iso = datetime.fromtimestamp(ts_ns / _NS_PER_S, UTC).isoformat()
    values = {"ticker": ticker, "close": str(close), "time": time_iso, "interval": str(bar_seconds)}
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)
    return template


def post_webhook(alert: Alert, body: str) -> None:
    """Failures are logged with the alert id and never retried -- the next fire opportunity
    is the retry."""
    try:
        json.loads(body)
        content_type = "application/json"
    except ValueError:
        content_type = "text/plain"
    request = urllib.request.Request(
        alert.webhook_url,
        data=body.encode(),
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_WEBHOOK_TIMEOUT_S):  # noqa: S310 (scheme validated on create)
            pass
    except Exception as exc:
        logger.warning("alert %s webhook POST failed: %s", alert.id, exc)


class AlertEngine:
    def __init__(
        self, alert_store: AlertStore, post: Callable[[Alert, str], None] = post_webhook,
    ) -> None:
        self._store = alert_store
        self._post = post
        self._state: dict[str, RunState] = {}
        self._listeners: set["asyncio.Queue[dict]"] = set()

    def subscribe(self) -> "asyncio.Queue[dict]":
        """Toast feed for one `/ws/live` connection."""
        queue: "asyncio.Queue[dict]" = asyncio.Queue(QUEUE_MAX)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[dict]") -> None:
        self._listeners.discard(queue)

    def forget(self, alert_id: str) -> None:
        self._state.pop(alert_id, None)

    def on_snapshot(self, snapshot: Any) -> None:
        """A second with no trade has no close_price -- nothing to evaluate."""
        price = snapshot.close_price
        if price is None:
            return
        instrument_id = snapshot.instrument_id.value
        for alert in self._store.list():
            if alert.instrument_id != instrument_id:
                continue
            state = self._state.setdefault(alert.id, RunState())
            fire_price = evaluate(alert, state, price, snapshot.ts_event)
            if fire_price is not None:
                self._fire(alert, fire_price, snapshot.ts_event)

    def _fire(self, alert: Alert, price: float, ts_ns: int) -> None:
        message = render(alert.template, alert.instrument_id, price, ts_ns, alert.bar_seconds)
        self._store.record_fire(alert, ts_ns)
        threading.Thread(target=self._post, args=(alert, message), daemon=True).start()
        toast = {"channel": "alerts", "alert": {"id": alert.id, "message": message}}
        for queue in self._listeners:
            put_drop_oldest(queue, toast)


engine = AlertEngine(store)
