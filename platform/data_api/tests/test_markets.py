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
from views.rankings_bus import RankingsBus

import data_api.app as app_module
from data_api import buses
from data_api import settings


_BYBIT_BTC = "BTCUSDT-LINEAR.BYBIT"
_BYBIT_ETH = "ETHUSDT-LINEAR.BYBIT"
_HL_BTC = "BTC-USD-PERP.HYPERLIQUID"
_HL_SOL = "SOL-USD-PERP.HYPERLIQUID"
_BYBIT_SPOT = "BTCUSDT-SPOT.BYBIT"


def _message(venue: str, ids: list[str]) -> dict[str, Any]:
    markets = [{"instrument_id": iid, "symbol": iid.split("-")[0]} for iid in ids]
    return {"venue": venue, "ts": 1_759_800_000_000_000_000, "markets": markets}


def _live_bus() -> MarketsBus:
    bus = MarketsBus()
    bus.handle_message(_message("BYBIT", [_BYBIT_BTC, _BYBIT_ETH]))
    bus.handle_message(_message("HYPERLIQUID", [_HL_SOL, _HL_BTC]))
    return bus


def _ranks_bus(*rows: dict[str, Any], updated_at: int | None = None) -> RankingsBus:
    """Return a rankings bus holding one message, published now unless `updated_at` is given."""
    bus = RankingsBus()
    stamp = time.time_ns() if updated_at is None else updated_at
    bus.handle_message({"mode": "volume", "updated_at": stamp, "ranks": list(rows)})
    return bus


class _Collected:
    """Stands in for `buses.collected_markets`: fixed sets per venue (absent = unknown)."""

    def __init__(self, sets: dict[str, frozenset[str]] | None = None) -> None:
        self.sets = sets or {}

    async def by_venue(self, venues: list[str], now_ns: int) -> dict[str, frozenset[str] | None]:
        return {venue: self.sets.get(venue) for venue in venues}


@pytest.fixture(autouse=True)
def _no_collected_sets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buses, "collected_markets", _Collected())


def _isolate(monkeypatch: pytest.MonkeyPatch, rankings: RankingsBus | None = None) -> None:
    monkeypatch.setattr(buses, "markets_bus", _live_bus())
    monkeypatch.setattr(buses, "bus", rankings or RankingsBus())


def test_markets_is_503_when_no_venue_is_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(buses, "markets_bus", MarketsBus())

    response = TestClient(app_module.app).get("/api/markets")

    assert response.status_code == 503


def test_markets_lists_the_same_asset_markets_of_other_venues_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate(monkeypatch)

    response = TestClient(app_module.app).get(f"/api/markets?instrument_id={_BYBIT_BTC}")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "instrument_id": _HL_BTC,
                "symbol": "BTC",
                "venue": "HYPERLIQUID",
                "same_asset": True,
                "market": "perp",
                "volume24h": None,
                "collected": None,
            },
            {
                "instrument_id": _BYBIT_ETH,
                "symbol": "ETHUSDT",
                "venue": "BYBIT",
                "same_asset": False,
                "market": "perp",
                "volume24h": None,
                "collected": None,
            },
            {
                "instrument_id": _HL_SOL,
                "symbol": "SOL",
                "venue": "HYPERLIQUID",
                "same_asset": False,
                "market": "perp",
                "volume24h": None,
                "collected": None,
            },
        ],
        "stale_venues": [],
    }


def test_markets_without_an_instrument_id_lists_every_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate(monkeypatch)

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


def test_each_market_says_whether_its_venue_collects_it_or_null_when_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate(monkeypatch)
    monkeypatch.setattr(buses, "collected_markets", _Collected({"BYBIT": frozenset({_BYBIT_BTC})}))

    items = TestClient(app_module.app).get("/api/markets").json()["items"]

    assert {i["instrument_id"]: i["collected"] for i in items} == {
        _BYBIT_BTC: True,
        _BYBIT_ETH: False,
        _HL_BTC: None,
        _HL_SOL: None,
    }


def test_a_malformed_instrument_id_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate(monkeypatch)

    response = TestClient(app_module.app).get("/api/markets?instrument_id=BTCUSDT")

    assert response.status_code == 400


def test_each_market_carries_its_market_type_and_the_ranked_24h_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = MarketsBus()
    bus.handle_message(_message("BYBIT", [_BYBIT_BTC, _BYBIT_SPOT]))
    monkeypatch.setattr(buses, "markets_bus", bus)
    rankings = _ranks_bus(
        {"instrument_id": _BYBIT_BTC, "volume24h": 1_234_567_890.5},
        {"instrument_id": _BYBIT_SPOT, "volume24h": 98_765},
    )
    monkeypatch.setattr(buses, "bus", rankings)

    items = TestClient(app_module.app).get("/api/markets").json()["items"]

    assert [(i["instrument_id"], i["market"], i["volume24h"]) for i in items] == [
        (_BYBIT_BTC, "perp", 1_234_567_890.5),
        (_BYBIT_SPOT, "spot", 98_765.0),
    ]


def test_the_volume_is_null_when_the_rankings_have_no_value_for_the_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A volatility-mode row with a null volume, and an id the rankings do not list: `—`, never 0.
    _isolate(monkeypatch, _ranks_bus({"instrument_id": _HL_BTC, "volume24h": None}))

    items = TestClient(app_module.app).get("/api/markets").json()["items"]

    assert {i["instrument_id"]: i["volume24h"] for i in items} == {
        _BYBIT_BTC: None,
        _BYBIT_ETH: None,
        _HL_BTC: None,
        _HL_SOL: None,
    }


def test_the_volume_is_null_when_the_rankings_message_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The ranking engine went quiet: its last message's volumes are not served as live numbers.
    old_ns = time.time_ns() - int((markets_bus.STALE_AFTER_SECONDS + 60) * 1_000_000_000)
    _isolate(
        monkeypatch, _ranks_bus({"instrument_id": _HL_BTC, "volume24h": 5e6}, updated_at=old_ns)
    )

    items = TestClient(app_module.app).get("/api/markets").json()["items"]

    assert {i["volume24h"] for i in items} == {None}


def test_the_volume_is_null_for_every_market_before_any_rankings_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _isolate(monkeypatch)  # a fresh RankingsBus: `latest` is None

    response = TestClient(app_module.app).get("/api/markets")

    assert response.status_code == 200
    assert {i["volume24h"] for i in response.json()["items"]} == {None}


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
    assert {
        "instrument_id": iid,
        "symbol": "BTC",
        "venue": venue,
        "same_asset": False,
        "market": "perp",
        "volume24h": None,
        "collected": None,  # no collector keeps a collected set for this test venue
    } in response.json()["items"]
