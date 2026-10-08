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


# -- Story 33.12: each listed market's type and ranked 24 h volume -----------------------------------


_UPDATED_AT = 1_759_800_000_000_000_000
_NOW_NS = _UPDATED_AT + 1_000_000_000  # one second after the message


def _rankings(*rows: dict[str, Any], updated_at: int = _UPDATED_AT) -> dict[str, Any]:
    return {"mode": "volatility", "updated_at": updated_at, "ranks": list(rows)}


def with_market_details(
    listing: dict[str, Any], rankings: dict[str, Any] | None, now_ns: int = _NOW_NS
) -> dict[str, Any]:
    return MarketsBus().with_market_details(listing, rankings, now_ns)


def test_market_details_add_the_market_type_and_the_ranked_volume() -> None:
    listing = _bus().listing(None, 100.0)
    assert listing is not None
    rankings = _rankings(
        {"instrument_id": _BYBIT_BTC, "volume24h": 5_000_000},
        {"instrument_id": _HL_SOL, "volume24h": None},
    )

    items = with_market_details(listing, rankings)["items"]

    assert [(i["instrument_id"], i["market"], i["volume24h"]) for i in items] == [
        (_BYBIT_BTC, "perp", 5_000_000.0),
        (_BYBIT_BTC_SPOT, "spot", None),
        (_BYBIT_ETH, "perp", None),
        (_HL_BTC, "perp", None),
        (_HL_SOL, "perp", None),
    ]


def test_market_details_keep_the_listing_and_its_stale_venues() -> None:
    listing = _bus().listing(_BYBIT_BTC, 100.0)
    assert listing is not None

    detailed = with_market_details(listing, None)

    assert detailed["stale_venues"] == listing["stale_venues"]
    assert [{k: i[k] for k in listing["items"][0]} for i in detailed["items"]] == listing["items"]


@pytest.mark.parametrize("volume", ["1000", True, float("nan"), float("inf"), [1]])
def test_a_non_numeric_ranked_volume_is_ledgered_and_shown_as_unknown(volume: Any) -> None:
    listing = _bus().listing(None, 100.0)
    assert listing is not None
    before = _ledger_count()

    items = with_market_details(listing, _rankings({"instrument_id": _HL_BTC, "volume24h": volume}))

    assert {i["instrument_id"]: i["volume24h"] for i in items["items"]}[_HL_BTC] is None
    assert _ledger_count() == before + 1


def test_a_bad_ranked_volume_is_ledgered_once_per_rankings_message_not_per_request() -> None:
    bus = _bus()
    listing = bus.listing(None, 100.0)
    assert listing is not None
    bad = {"instrument_id": _HL_BTC, "volume24h": "1000"}
    before = _ledger_count()

    for _ in range(3):
        bus.with_market_details(listing, _rankings(bad), _NOW_NS)
    assert _ledger_count() == before + 1

    bus.with_market_details(listing, _rankings(bad, updated_at=_UPDATED_AT + 1), _NOW_NS)
    assert _ledger_count() == before + 2


def test_a_rankings_message_older_than_the_staleness_horizon_gives_no_volume() -> None:
    listing = _bus().listing(None, 100.0)
    assert listing is not None
    rankings = _rankings({"instrument_id": _BYBIT_BTC, "volume24h": 5_000_000})
    horizon_ns = int(STALE_AFTER_SECONDS * 1_000_000_000)

    fresh = with_market_details(listing, rankings, _UPDATED_AT + horizon_ns)
    stale = with_market_details(listing, rankings, _UPDATED_AT + horizon_ns + 1)

    assert {i["instrument_id"]: i["volume24h"] for i in fresh["items"]}[_BYBIT_BTC] == 5_000_000.0
    assert {i["volume24h"] for i in stale["items"]} == {None}


@pytest.mark.parametrize("stale", [[_BYBIT_BTC], [_BYBIT_BTC, 7]])
def test_an_instrument_the_ranking_marks_stale_gives_no_volume(stale: list[Any]) -> None:
    listing = _bus().listing(None, 100.0)
    assert listing is not None
    rankings = {
        **_rankings(
            {"instrument_id": _BYBIT_BTC, "volume24h": 5_000_000},
            {"instrument_id": _HL_SOL, "volume24h": 7_000_000},
        ),
        "stale_instrument_ids": stale,
    }

    volumes = {
        i["instrument_id"]: i["volume24h"] for i in with_market_details(listing, rankings)["items"]
    }

    assert (volumes[_BYBIT_BTC], volumes[_HL_SOL]) == (None, 7_000_000.0)


@pytest.mark.parametrize("stale", [None, "BTCUSDT-LINEAR.BYBIT"])
def test_a_missing_or_malformed_stale_list_marks_nothing_stale(stale: Any) -> None:
    listing = _bus().listing(None, 100.0)
    assert listing is not None
    rankings = {
        **_rankings({"instrument_id": _BYBIT_BTC, "volume24h": 5_000_000}),
        "stale_instrument_ids": stale,
    }

    volumes = {
        i["instrument_id"]: i["volume24h"] for i in with_market_details(listing, rankings)["items"]
    }

    assert volumes[_BYBIT_BTC] == 5_000_000.0
