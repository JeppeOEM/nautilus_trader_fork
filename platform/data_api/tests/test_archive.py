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
Story 25.1b: `GET /api/archive/status` and `POST /api/archive/run`.

The status route is tested against an isolated `ArchiveStatusBus` swapped into `buses`, plus one
integration test publishing onto the real Redis the app subscribes to (`settings.REDIS_URL`, the
`test_rankings.py` precedent). The run route publishes to that same real Redis but on a unique
channel -- a live local `archive` service listens on the real `archive:control`, and a test
publish there would start a real maintenance run (the `test_rankings_mode.py` precedent).
"""

import datetime as dt
import json
import time
import uuid

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient
from views import archive_status_bus
from views.archive_status_bus import ArchiveStatusBus

import data_api.app as app_module
from data_api import buses
from data_api import settings
from data_api.routes import archive as archive_routes


def _status(**overrides: object) -> dict:
    status = {
        "next_run": "2026-09-27T03:07:00Z",
        "next_intraday": "2026-09-26T16:07:00Z",
        "running": None,
        "last_run": {
            "run_id": "r-1",
            "kind": "nightly",
            "day": "2026-09-25",
            "days": ["2026-09-25"],
            "started": "2026-09-26T03:07:00Z",
            "finished": "2026-09-26T03:41:12Z",
            "steps": [{"venue": "BYBIT", "name": "reconcile", "exit": 0, "duration_s": 1.5}],
        },
        "last_intraday": None,
        "backup": "disabled",
    }
    status.update(overrides)
    return status


def _unique_channel() -> str:
    return f"test:archive:control:{uuid.uuid4().hex}"


def _await_message(pubsub: redis_sync.client.PubSub, timeout: float = 5.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = pubsub.get_message(timeout=0.1)
        if message is not None and message["type"] == "message":
            return message
    return None


def _yesterday() -> str:
    return (dt.datetime.now(dt.UTC).date() - dt.timedelta(days=1)).isoformat()


def _today() -> str:
    return dt.datetime.now(dt.UTC).date().isoformat()


def _tomorrow() -> str:
    return (dt.datetime.now(dt.UTC).date() + dt.timedelta(days=1)).isoformat()


# --- GET /api/archive/status ---


def test_get_status_is_503_before_any_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buses, "archive_bus", ArchiveStatusBus())

    response = TestClient(app_module.app).get("/api/archive/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "Archive status not yet available"


def test_get_status_passes_the_cached_message_through(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = ArchiveStatusBus()
    message = _status()
    bus.handle_message(message)
    monkeypatch.setattr(buses, "archive_bus", bus)

    response = TestClient(app_module.app).get("/api/archive/status")

    assert response.status_code == 200
    assert response.json() == message


def test_get_status_is_503_once_the_heartbeat_went_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = ArchiveStatusBus()
    bus.handle_message(_status())
    assert bus.received_at is not None
    bus.received_at -= archive_status_bus.STALE_AFTER_SECONDS + 1
    monkeypatch.setattr(buses, "archive_bus", bus)

    response = TestClient(app_module.app).get("/api/archive/status")

    assert response.status_code == 503
    assert "stale" in response.json()["detail"]


def test_get_status_fills_optional_keys_with_null(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = ArchiveStatusBus()
    bus.handle_message({"next_run": "2026-09-27T03:07:00Z", "last_run": None})
    monkeypatch.setattr(buses, "archive_bus", bus)

    body = TestClient(app_module.app).get("/api/archive/status").json()

    assert body == {
        "next_run": "2026-09-27T03:07:00Z",
        "next_intraday": None,
        "running": None,
        "last_run": None,
        "last_intraday": None,
        "backup": None,
    }


def _publish_until_observed(payload: str, expected: dict, timeout: float = 10.0) -> bool:
    """Publish repeatedly until the app's own bus task has subscribed and cached it."""
    publisher = redis_sync.Redis.from_url(settings.REDIS_URL)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            publisher.publish(archive_status_bus.ARCHIVE_STATUS_CHANNEL, payload)
            if buses.archive_bus.latest == expected:
                return True
            time.sleep(0.1)
        return False
    finally:
        publisher.close()


def test_archive_status_message_reflected_by_rest() -> None:
    message = _status(next_run=f"2026-09-27T03:07:{int(time.time()) % 60:02d}Z")

    with TestClient(app_module.app) as client:
        delivered = _publish_until_observed(json.dumps(message), message)
        assert delivered, "archive:status message was never observed by the running bus"

        response = client.get("/api/archive/status")

    assert response.status_code == 200
    assert response.json() == message


# --- POST /api/archive/run ---


def test_production_channel_is_archive_control() -> None:
    assert archive_routes.ARCHIVE_CONTROL_CHANNEL == "archive:control"


@pytest.mark.parametrize(
    ("body", "day"),
    [
        ({"day": None}, None),
        ({}, None),
        ({"day": "2026-01-31"}, "2026-01-31"),
        ({"day": _yesterday()}, _yesterday()),
    ],
)
def test_post_run_publishes_the_exact_command(
    monkeypatch: pytest.MonkeyPatch, body: dict, day: str | None
) -> None:
    channel = _unique_channel()
    monkeypatch.setattr(archive_routes, "ARCHIVE_CONTROL_CHANNEL", channel)
    subscriber = redis_sync.Redis.from_url(archive_routes.REDIS_URL)
    pubsub = subscriber.pubsub()
    try:
        pubsub.subscribe(channel)
        # The subscribe confirmation must land before the POST, or publish() counts 0 receivers.
        confirmation = pubsub.get_message(timeout=5.0)
        assert confirmation is not None
        assert confirmation["type"] == "subscribe"

        response = TestClient(app_module.app).post("/api/archive/run", json=body)
        message = _await_message(pubsub)
    finally:
        pubsub.close()
        subscriber.close()

    assert response.status_code == 202
    assert response.json() == {"day": day}
    assert message is not None, "the POST published nothing on the channel"
    assert message["data"] == json.dumps({"command": "run_now", "day": day}).encode()


def test_post_run_with_no_subscriber_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _unique_channel()  # nobody subscribes: stands in for the scheduler being down
    monkeypatch.setattr(archive_routes, "ARCHIVE_CONTROL_CHANNEL", channel)

    response = TestClient(app_module.app).post("/api/archive/run", json={"day": None})

    assert response.status_code == 503
    assert response.json()["detail"] == f"no archive scheduler subscribed to {channel}"


def test_post_run_with_redis_down_is_503_with_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive_routes, "ARCHIVE_CONTROL_CHANNEL", _unique_channel())
    monkeypatch.setattr(archive_routes, "REDIS_URL", "redis://127.0.0.1:1")  # connect refused

    response = TestClient(app_module.app).post("/api/archive/run", json={"day": None})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("failed to publish to test:archive:control:")
    assert "ConnectionError" in detail


@pytest.mark.parametrize(
    "body",
    [
        {"day": "2026-09-2"},
        {"day": "2026-02-30"},
        {"day": "2026-13-01"},
        {"day": "20260925"},
        {"day": "2026-09-25T00:00"},
        {"day": " 2026-09-25"},
        {"day": 20260925},
        {"day": "today"},
        {"day": None, "x": 1},
        ["2026-09-25"],
        {"day": _today()},  # not closed yet: the scheduler would refuse it
        {"day": _tomorrow()},
    ],
)
def test_post_run_rejects_anything_but_a_closed_calendar_day(
    monkeypatch: pytest.MonkeyPatch, body: object
) -> None:
    def _no_redis(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a rejected request must never reach Redis")

    monkeypatch.setattr(archive_routes.aioredis.Redis, "from_url", _no_redis)

    response = TestClient(app_module.app).post("/api/archive/run", json=body)

    assert response.status_code == 422
