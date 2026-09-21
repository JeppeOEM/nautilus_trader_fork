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
Story 15.2: `GET /api/rankings` + `/ws/live` relay, backed by `redis_bus.RankingsBus`.

Pure-logic tests (503-before-cache, malformed-payload-skip) use an isolated
`RankingsBus()` instance -- `RankingsBus` is deliberately a plain class, not a baked-in
singleton, exactly so these tests never share state with each other or with the app's
real `redis_bus.bus`. The one integration test exercises the real `dydx-redis` Redis
container already running on this host (`docker ps` confirms `redis://127.0.0.1:6379`)
-- no mocking, no fakeredis/testcontainers dependency, per this story's TEST-03
requirement and platform/CLAUDE.md's minimize-dependencies preference.
"""

import json
import time
from collections.abc import Callable

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient

import data_api.app as app_module
from data_api import redis_bus
from data_api.redis_bus import RankingsBus


def _sample_message(**overrides: object) -> dict:
    message = {
        "mode": "volume",
        "updated_at": time.time_ns(),
        "ranks": [
            {
                "instrument_id": "BTC-USD-PERP.DYDX",
                "ofi_10_z": 0.5,
                "price": 100.1234,
            }
        ],
        "stale_instrument_ids": [],
    }
    message.update(overrides)
    return message


# --- pure-logic tests: RankingsBus.handle_message() validation ---


def test_handle_message_skips_non_dict_payload() -> None:
    bus = RankingsBus()

    bus.handle_message("not-a-dict")

    assert bus.latest is None


def test_handle_message_skips_dict_missing_list_shaped_ranks() -> None:
    bus = RankingsBus()

    bus.handle_message({"mode": "volume", "updated_at": 1, "ranks": "not-a-list"})

    assert bus.latest is None


def test_handle_message_skips_message_missing_mode_or_updated_at() -> None:
    """
    Guards `GET /api/rankings`'s plain-index reads (`latest["mode"]`, etc.) from ever
    seeing a cached message that would raise a KeyError -> 500 instead of an honest 503.
    """
    bus = RankingsBus()

    bus.handle_message({"updated_at": 1, "ranks": []})
    bus.handle_message({"mode": "volume", "ranks": []})

    assert bus.latest is None


def test_handle_message_skips_ranks_containing_a_non_dict_row() -> None:
    bus = RankingsBus()

    bus.handle_message({"mode": "volume", "updated_at": 1, "ranks": ["not-a-dict"]})

    assert bus.latest is None


def test_handle_message_keeps_previous_good_cache_on_malformed_followup() -> None:
    bus = RankingsBus()
    good = _sample_message()

    bus.handle_message(good)
    bus.handle_message({"mode": "volume", "updated_at": 2, "ranks": "not-a-list"})

    assert bus.latest == good


def test_handle_message_fans_out_to_every_subscribed_listener_queue() -> None:
    bus = RankingsBus()
    queue_a = bus.subscribe()
    queue_b = bus.subscribe()
    message = _sample_message()

    bus.handle_message(message)

    assert queue_a.get_nowait() == message
    assert queue_b.get_nowait() == message


def test_unsubscribe_stops_further_fan_out() -> None:
    bus = RankingsBus()
    queue = bus.subscribe()
    bus.unsubscribe(queue)

    bus.handle_message(_sample_message())

    assert queue.empty()


# --- GET /api/rankings: 503 before cache, using an isolated bus (never the app's real
# module-level singleton, so this never races the real-Redis integration test below) ---


def test_get_rankings_returns_503_before_any_cached_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(redis_bus, "bus", RankingsBus())
    client = TestClient(app_module.app)

    response = client.get("/api/rankings")

    assert response.status_code == 503


def test_get_rankings_renames_ranks_to_items_and_passes_everything_else_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_bus = RankingsBus()
    message = _sample_message()
    isolated_bus.handle_message(message)
    monkeypatch.setattr(redis_bus, "bus", isolated_bus)
    client = TestClient(app_module.app)

    response = client.get("/api/rankings")

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == message["ranks"]
    assert body["updated_at"] == message["updated_at"]
    assert body["mode"] == message["mode"]
    assert body["stale_instrument_ids"] == message["stale_instrument_ids"]


# --- Real-Redis integration test: publish onto the actual dydx-redis container and
# confirm both GET /api/rankings and /ws/live reflect it (epics AC8/TEST-03) ---


def _publish_until_observed(
    channel: str,
    payload: str,
    observed: Callable[[], bool],
    timeout: float = 10.0,
) -> bool:
    """
    Retry-publish loop: Redis pub/sub only delivers to already-subscribed clients, and
    the bus's background subscriber task needs a moment to connect+subscribe after the
    app's lifespan starts it. Publish repeatedly until `observed()` reports the message
    was actually received, rather than guessing a fixed sleep duration.

    Publishes via `redis_bus.REDIS_URL` (not a hardcoded literal) so this test always
    targets the same Redis instance the running `RankingsBus` subscribes to, even if
    `REDIS_URL`/`REDIS_PORT` is shifted away from the default in some environment.
    """
    publisher = redis_sync.Redis.from_url(redis_bus.REDIS_URL)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            publisher.publish(channel, payload)
            if observed():
                return True
            time.sleep(0.1)
        return False
    finally:
        publisher.close()


def test_rankings_live_message_reflected_by_rest_and_ws_relay() -> None:
    message = _sample_message(updated_at=time.time_ns())
    payload = json.dumps(message)

    with TestClient(app_module.app) as client:
        delivered = _publish_until_observed(
            redis_bus.RANKINGS_CHANNEL,
            payload,
            lambda: redis_bus.bus.latest == message,
        )
        assert delivered, "rankings:live message was never observed by the running RankingsBus"

        rest_response = client.get("/api/rankings")
        assert rest_response.status_code == 200
        rest_body = rest_response.json()
        assert rest_body["items"] == message["ranks"]
        assert rest_body["updated_at"] == message["updated_at"]
        assert rest_body["mode"] == message["mode"]

        with client.websocket_connect("/ws/live") as websocket:
            # New-client-connects-mid-stream: bus.latest is already cached, so this is
            # the immediate cached-message send, not a wait for the next tick.
            received = websocket.receive_json()
            assert received == message
            assert "ranks" in received  # ws/live never renames ranks -> items


def test_put_drop_oldest_keeps_newest_when_full() -> None:
    import asyncio

    from data_api.redis_bus import put_drop_oldest

    queue: asyncio.Queue[dict] = asyncio.Queue(2)
    for i in range(3):
        put_drop_oldest(queue, {"i": i})
    assert [queue.get_nowait()["i"], queue.get_nowait()["i"]] == [1, 2]
