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
Story 33.9: `GET /api/markets`, against an isolated `MarketsBus` swapped into `buses`, plus one
integration test publishing onto the Redis the app subscribes to (`settings.REDIS_URL`, the
`test_archive.py` precedent) on a unique channel (the bus reads `MARKETS_CHANNEL` at subscribe
time), so a live data_api or bot_tui on that Redis never sees its fake venue. It skips when no Redis
answers (`bots/tests/test_liquidation_data_client.py`'s `_redis_or_skip`).
"""

import json
import socket
import time
import uuid
from typing import Any
from urllib.parse import urlparse

import pytest
import redis as redis_sync
from fastapi.testclient import TestClient
from views import markets_bus
from views.markets_bus import MarketsBus

import data_api.app as app_module
from data_api import buses
from data_api import settings


_BYBIT_BTC = "BTCUSDT-LINEAR.BYBIT"
_BYBIT_ETH = "ETHUSDT-LINEAR.BYBIT"
_HL_BTC = "BTC-USD-PERP.HYPERLIQUID"
_HL_SOL = "SOL-USD-PERP.HYPERLIQUID"


def _message(venue: str, ids: list[str]) -> dict[str, Any]:
    markets = [{"instrument_id": iid, "symbol": iid.split("-")[0]} for iid in ids]
    return {"venue": venue, "ts": 1_759_800_000_000_000_000, "markets": markets}


def _live_bus() -> MarketsBus:
    bus = MarketsBus()
    bus.handle_message(_message("BYBIT", [_BYBIT_BTC, _BYBIT_ETH]))
    bus.handle_message(_message("HYPERLIQUID", [_HL_SOL, _HL_BTC]))
    return bus


def test_markets_is_503_when_no_venue_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buses, "markets_bus", MarketsBus())

    response = TestClient(app_module.app).get("/api/markets")

    assert response.status_code == 503


def test_markets_lists_the_same_asset_markets_of_other_venues_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(buses, "markets_bus", _live_bus())

    response = TestClient(app_module.app).get(f"/api/markets?instrument_id={_BYBIT_BTC}")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"instrument_id": _HL_BTC, "symbol": "BTC", "venue": "HYPERLIQUID", "same_asset": True},
            {
                "instrument_id": _BYBIT_ETH,
                "symbol": "ETHUSDT",
                "venue": "BYBIT",
                "same_asset": False,
            },
            {
                "instrument_id": _HL_SOL,
                "symbol": "SOL",
                "venue": "HYPERLIQUID",
                "same_asset": False,
            },
        ],
        "stale_venues": [],
    }


def test_markets_without_an_instrument_id_lists_every_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(buses, "markets_bus", _live_bus())

    body = TestClient(app_module.app).get("/api/markets").json()

    assert [item["instrument_id"] for item in body["items"]] == [
        _BYBIT_BTC,
        _BYBIT_ETH,
        _HL_BTC,
        _HL_SOL,
    ]
    assert not any(item["same_asset"] for item in body["items"])


def test_markets_names_a_stale_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = MarketsBus()
    bus.handle_message(_message("BYBIT", [_BYBIT_BTC]), now=time.monotonic() - 200.0)
    monkeypatch.setattr(buses, "markets_bus", bus)

    body = TestClient(app_module.app).get("/api/markets").json()

    assert body["stale_venues"] == ["BYBIT"]


def test_a_malformed_instrument_id_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buses, "markets_bus", _live_bus())

    response = TestClient(app_module.app).get("/api/markets?instrument_id=BTCUSDT")

    assert response.status_code == 400


def _redis_or_skip() -> None:
    url = urlparse(settings.REDIS_URL)
    try:
        socket.create_connection((url.hostname or "127.0.0.1", url.port or 6379), 1).close()
    except OSError as exc:
        pytest.skip(f"no Redis at REDIS_URL={settings.REDIS_URL} ({exc}): needs a live channel")


def _publish_until_listed(payload: str, iid: str, timeout: float = 10.0) -> bool:
    """Publish repeatedly until the app's own bus task has subscribed and cached it."""
    publisher = redis_sync.Redis.from_url(settings.REDIS_URL)
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            publisher.publish(markets_bus.MARKETS_CHANNEL, payload)
            listing = buses.markets_bus.listing(None, time.monotonic())
            if listing is not None and any(i["instrument_id"] == iid for i in listing["items"]):
                return True
            time.sleep(0.1)
        return False
    finally:
        publisher.close()


def test_a_markets_live_message_is_reflected_by_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    _redis_or_skip()
    token = uuid.uuid4().hex[:8].upper()
    monkeypatch.setattr(markets_bus, "MARKETS_CHANNEL", f"test:markets:live:{token}")
    monkeypatch.setattr(buses, "markets_bus", MarketsBus())
    venue = f"TEST{token}"
    iid = f"BTC-USD-PERP.{venue}"

    with TestClient(app_module.app) as client:
        delivered = _publish_until_listed(json.dumps(_message(venue, [iid])), iid)
        assert delivered, "the markets message was never observed by the running bus"

        response = client.get("/api/markets")

    assert response.status_code == 200
    assert {"instrument_id": iid, "symbol": "BTC", "venue": venue, "same_asset": False} in (
        response.json()["items"]
    )
