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
"""Story 33.9: `MarketsBus`'s whole-message check, per-venue cache and listing (pure, no Redis)."""

import asyncio
import json
import threading
from typing import Any

import pytest
from kernel.venues import MalformedInstrumentId
from observability import error_ledger

from views import markets_bus
from views.markets_bus import EXPIRE_AFTER_SECONDS
from views.markets_bus import STALE_AFTER_SECONDS
from views.markets_bus import MarketsBus


_BYBIT_BTC = "BTCUSDT-LINEAR.BYBIT"
_BYBIT_ETH = "ETHUSDT-LINEAR.BYBIT"
_BYBIT_BTC_SPOT = "BTCUSDT-SPOT.BYBIT"
_HL_BTC = "BTC-USD-PERP.HYPERLIQUID"
_HL_SOL = "SOL-USD-PERP.HYPERLIQUID"


def _message(venue: str, ids: list[str], ts: Any = 1_759_800_000_000_000_000) -> dict[str, Any]:
    markets = [{"instrument_id": iid, "symbol": iid.split("-")[0]} for iid in ids]
    return {"venue": venue, "ts": ts, "markets": markets}


def _bus() -> MarketsBus:
    bus = MarketsBus()
    bus.handle_message(_message("BYBIT", [_BYBIT_BTC, _BYBIT_BTC_SPOT, _BYBIT_ETH]), now=100.0)
    bus.handle_message(_message("HYPERLIQUID", [_HL_SOL, _HL_BTC]), now=100.0)
    return bus


def _ids(listing: dict[str, Any] | None) -> list[str]:
    assert listing is not None
    return [item["instrument_id"] for item in listing["items"]]


def _ledger_count() -> int:
    return error_ledger.counts().get("views.markets", 0)


def test_channel_is_markets_live() -> None:
    assert markets_bus.MARKETS_CHANNEL == "markets:live"


def test_the_thresholds_are_bot_tuis() -> None:
    assert (STALE_AFTER_SECONDS, EXPIRE_AFTER_SECONDS) == (180.0, 900.0)


def test_no_venue_lists_nothing_as_none() -> None:
    assert MarketsBus().listing(None, now=0.0) is None


def test_a_valid_message_lists_every_market_with_its_venue() -> None:
    listing = _bus().listing(None, now=100.0)
    assert listing is not None
    assert listing["stale_venues"] == []
    assert listing["items"][0] == {
        "instrument_id": _BYBIT_BTC,
        "symbol": "BTCUSDT",
        "venue": "BYBIT",
        "same_asset": False,
    }
    assert _ids(listing) == [_BYBIT_BTC, _BYBIT_BTC_SPOT, _BYBIT_ETH, _HL_BTC, _HL_SOL]


def test_a_newer_message_replaces_the_venues_list() -> None:
    bus = _bus()
    bus.handle_message(_message("BYBIT", [_BYBIT_ETH]), now=110.0)
    assert _ids(bus.listing(None, now=110.0)) == [_BYBIT_ETH, _HL_BTC, _HL_SOL]


def test_same_asset_markets_come_first_and_self_is_omitted() -> None:
    listing = _bus().listing(_BYBIT_BTC, now=100.0)
    # BTCUSDT-SPOT is BTC/USD spot, not the same asset as the linear perp (kernel.venues).
    assert _ids(listing) == [_HL_BTC, _BYBIT_BTC_SPOT, _BYBIT_ETH, _HL_SOL]
    assert listing is not None
    assert [item["same_asset"] for item in listing["items"]] == [True, False, False, False]


def test_a_malformed_instrument_id_raises() -> None:
    with pytest.raises(MalformedInstrumentId):
        _bus().listing("BTCUSDT", now=100.0)


@pytest.mark.parametrize(
    "message",
    [
        "not-a-dict",
        [],
        {"ts": 1, "markets": []},
        _message("", [_BYBIT_BTC]),
        _message("BYBIT", [_BYBIT_BTC], ts="1"),
        _message("BYBIT", [_BYBIT_BTC], ts=True),
        {"venue": "BYBIT", "ts": 1, "markets": {}},
        {"venue": "BYBIT", "ts": 1, "markets": [{"instrument_id": _BYBIT_BTC}]},
        {"venue": "BYBIT", "ts": 1, "markets": [{"instrument_id": 1, "symbol": "BTC"}]},
        {"venue": "BYBIT", "ts": 1, "markets": ["BTCUSDT-LINEAR.BYBIT"]},
        _message("BYBIT", [_BYBIT_BTC, _HL_SOL]),  # an id of another venue
        _message("BYBIT", [_BYBIT_BTC, _BYBIT_ETH, _BYBIT_BTC]),  # an id repeated
    ],
)
def test_a_malformed_message_is_ledgered_and_the_previous_list_kept(message: object) -> None:
    bus = _bus()
    before = _ledger_count()
    bus.handle_message(message, now=150.0)
    assert _ledger_count() == before + 1
    assert _ids(bus.listing(None, now=150.0)) == [
        _BYBIT_BTC,
        _BYBIT_BTC_SPOT,
        _BYBIT_ETH,
        _HL_BTC,
        _HL_SOL,
    ]


def test_unparseable_json_is_ledgered() -> None:
    bus = MarketsBus()
    before = _ledger_count()
    bus._ingest("{not json")
    assert _ledger_count() == before + 1
    assert bus.listing(None, now=0.0) is None


def test_unparseable_json_is_ledgered_with_a_truncated_repr() -> None:
    MarketsBus()._ingest("{" + "x" * 1000)
    detail = error_ledger.last_details()["views.markets"]
    assert "x" * 290 in detail
    assert "x" * 400 not in detail


def test_a_venue_with_an_empty_list_is_not_live() -> None:
    bus = MarketsBus()
    bus.handle_message(_message("BYBIT", []), now=100.0)
    assert bus.listing(None, now=100.0) is None
    bus.handle_message(_message("HYPERLIQUID", [_HL_SOL]), now=100.0)
    listing = bus.listing(None, now=100.0 + STALE_AFTER_SECONDS + 1)
    assert _ids(listing) == [_HL_SOL]
    assert listing is not None
    assert listing["stale_venues"] == ["HYPERLIQUID"]  # the empty BYBIT is not named either


def test_listing_survives_venues_arriving_from_another_thread() -> None:
    # `GET /api/markets` is a plain `def` route: `listing` runs on a threadpool thread while the
    # loop's `handle_message` inserts venues. Iterating the live dicts would raise "dictionary
    # changed size during iteration".
    bus = MarketsBus()
    bus.handle_message(_message("BYBIT", [_BYBIT_BTC]), now=100.0)
    errors: list[BaseException] = []
    done = threading.Event()

    def list_forever() -> None:
        try:
            while not done.is_set():
                bus.listing(None, now=100.0)
        except BaseException as exc:  # surfaced below, never swallowed
            errors.append(exc)

    reader = threading.Thread(target=list_forever)
    reader.start()
    try:
        for n in range(3000):
            bus.handle_message(_message(f"V{n}", [f"BTC-USD-PERP.V{n}"]), now=100.0)
    finally:
        done.set()
        reader.join()
    assert errors == []
    assert len(_ids(bus.listing(None, now=100.0))) == 3001


def test_a_venue_silent_past_180s_is_listed_as_stale() -> None:
    bus = _bus()
    bus.handle_message(_message("HYPERLIQUID", [_HL_SOL]), now=200.0)
    listing = bus.listing(None, now=100.0 + STALE_AFTER_SECONDS + 1)
    assert listing is not None
    assert listing["stale_venues"] == ["BYBIT"]
    assert _BYBIT_BTC in _ids(listing)


def test_a_venue_silent_past_900s_is_dropped() -> None:
    bus = _bus()
    bus.handle_message(_message("HYPERLIQUID", [_HL_SOL]), now=200.0)
    listing = bus.listing(None, now=100.0 + EXPIRE_AFTER_SECONDS + 1)
    assert _ids(listing) == [_HL_SOL]
    assert listing is not None
    assert listing["stale_venues"] == ["HYPERLIQUID"]


def test_every_venue_expired_lists_none() -> None:
    assert _bus().listing(None, now=100.0 + EXPIRE_AFTER_SECONDS + 1) is None


class _SilentPubSub:
    async def get_message(self, ignore_subscribe_messages: bool, timeout: float) -> None:
        await asyncio.sleep(0)


class _OnePubSub:
    def __init__(self, data: str) -> None:
        self._data: str | None = data

    async def get_message(self, ignore_subscribe_messages: bool, timeout: float) -> Any:
        data, self._data = self._data, None
        return None if data is None else {"type": "message", "data": data}


def test_receive_raises_after_silence_so_run_resubscribes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(markets_bus, "STALE_AFTER_SECONDS", -1.0)
    with pytest.raises(ConnectionError, match="markets:live"):
        asyncio.run(MarketsBus()._receive(_SilentPubSub()))  # type: ignore[arg-type]


def test_receive_ingests_a_message_before_its_silence_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(markets_bus, "STALE_AFTER_SECONDS", -1.0)
    bus = MarketsBus()
    pubsub = _OnePubSub(json.dumps(_message("BYBIT", [_BYBIT_BTC])))
    with pytest.raises(ConnectionError):
        asyncio.run(bus._receive(pubsub))  # type: ignore[arg-type]
    assert bus._markets == {"BYBIT": [(_BYBIT_BTC, "BTCUSDT")]}
