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
Story 25.1a: `PUT /api/rankings/mode`, the web's ranking-mode switch.

The replay test runs against the real Redis the app publishes to (`settings.REDIS_URL`, the
local `dydx-redis` container, same no-mock precedent as `test_rankings.py`) but on a unique
channel: the local stack's live `ranking_engine` subscribes to the real `ranking:control`, and a
test publish there would flip the live ranking mode. The byte-identity claim is about the
payload; the channel name is asserted as the production constant.
"""

import time
import uuid

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient

import data_api.app as app_module
from data_api.routes import rankings as rankings_routes


# Recorded 2026-09-26 by running `bot_tui/ranking_state.py:publish_mode_toggle(url, "volatility")`
# at baseline commit f00ab8aeea against a capturing fake of `redis.asyncio.Redis.from_url`: it
# published this string to "ranking:control" (UTF-8 encoded on the wire). That module was deleted
# in the same change that added this route, so the literal is the only record of the TUI's bytes.
_TUI_VOLATILITY_BYTES = b'{"mode": "volatility"}'


def _unique_channel() -> str:
    return f"test:ranking:control:{uuid.uuid4().hex}"


def _await_message(pubsub: redis_sync.client.PubSub, timeout: float = 5.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = pubsub.get_message(timeout=0.1)
        if message is not None and message["type"] == "message":
            return message
    return None


def test_production_channel_is_ranking_control() -> None:
    assert rankings_routes.RANKING_CONTROL_CHANNEL == "ranking:control"


def test_put_mode_publishes_the_tuis_exact_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _unique_channel()
    monkeypatch.setattr(rankings_routes, "RANKING_CONTROL_CHANNEL", channel)
    subscriber = redis_sync.Redis.from_url(rankings_routes.REDIS_URL)
    pubsub = subscriber.pubsub()
    try:
        pubsub.subscribe(channel)
        # The subscribe confirmation must land before the PUT, or publish() counts 0 receivers.
        confirmation = pubsub.get_message(timeout=5.0)
        assert confirmation is not None
        assert confirmation["type"] == "subscribe"

        response = TestClient(app_module.app).put("/api/rankings/mode", json={"mode": "volatility"})
        message = _await_message(pubsub)
    finally:
        pubsub.close()
        subscriber.close()

    assert response.status_code == 202
    assert response.json() == {"mode": "volatility"}
    assert message is not None, "the PUT published nothing on the channel"
    assert message["data"] == _TUI_VOLATILITY_BYTES


def test_put_mode_with_no_subscriber_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    channel = _unique_channel()  # nobody subscribes: stands in for ranking_engine being down
    monkeypatch.setattr(rankings_routes, "RANKING_CONTROL_CHANNEL", channel)

    response = TestClient(app_module.app).put("/api/rankings/mode", json={"mode": "volume"})

    assert response.status_code == 503
    assert response.json()["detail"] == f"no ranking_engine subscribed to {channel}"


def test_put_mode_with_redis_down_is_503_with_the_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rankings_routes, "RANKING_CONTROL_CHANNEL", _unique_channel())
    monkeypatch.setattr(rankings_routes, "REDIS_URL", "redis://127.0.0.1:1")  # connect refused

    response = TestClient(app_module.app).put("/api/rankings/mode", json={"mode": "volume"})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail.startswith("failed to publish to test:ranking:control:")
    assert "ConnectionError" in detail


@pytest.mark.parametrize(
    "body",
    [
        {"mode": "rank"},
        {"mode": "VOLUME"},
        {"mode": None},
        {},
        {"mode": "volume", "x": 1},
        ["volume"],
    ],
)
def test_put_mode_rejects_anything_but_one_known_mode(
    monkeypatch: pytest.MonkeyPatch, body: object
) -> None:
    def _no_redis(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a rejected request must never reach Redis")

    monkeypatch.setattr(rankings_routes.aioredis.Redis, "from_url", _no_redis)

    response = TestClient(app_module.app).put("/api/rankings/mode", json=body)

    assert response.status_code == 422
