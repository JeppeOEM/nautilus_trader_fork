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
Tests for bot_tui.markets_state (Story 29.5): the `markets:live` reader's whole-message validation,
and a venue's staleness (`~ ` after 180 s) and expiry (gone after 900 s), both by arrival time.
"""

import asyncio
import json
import logging
from typing import Any

import pytest

from bot_tui import markets_state


_T0 = 1_800_000_000.0


def _message(venue: str = "BYBIT", markets: object = None, ts: object = 1) -> dict:
    if markets is None:
        markets = [
            {"instrument_id": "BTCUSDT-LINEAR.BYBIT", "symbol": "BTC"},
            {"instrument_id": "SOLUSDT-SPOT.BYBIT", "symbol": "SOL"},
        ]
    return {"venue": venue, "ts": ts, "markets": markets}


def test_a_valid_message_is_kept_as_its_venue_s_list() -> None:
    markets_state._handle_markets_message(_message(), now=_T0)
    assert markets_state.live_markets(_T0) == {
        "BYBIT": [("BTCUSDT-LINEAR.BYBIT", "BTC"), ("SOLUSDT-SPOT.BYBIT", "SOL")]
    }
    assert markets_state.received_at("BYBIT") == _T0


def test_each_venue_keeps_its_own_newest_list() -> None:
    markets_state._handle_markets_message(_message(), now=_T0)
    hyperliquid = [{"instrument_id": "SOL-USD-PERP.HYPERLIQUID", "symbol": "SOL"}]
    markets_state._handle_markets_message(_message("HYPERLIQUID", hyperliquid), now=_T0)
    newer = [{"instrument_id": "ETHUSDT-LINEAR.BYBIT", "symbol": "ETH"}]
    markets_state._handle_markets_message(_message("BYBIT", newer), now=_T0 + 60)
    assert markets_state.live_markets(_T0 + 60) == {
        "BYBIT": [("ETHUSDT-LINEAR.BYBIT", "ETH")],
        "HYPERLIQUID": [("SOL-USD-PERP.HYPERLIQUID", "SOL")],
    }


@pytest.mark.parametrize(
    "message",
    [
        "not a dict",
        _message(markets={"BTCUSDT-LINEAR.BYBIT": "BTC"}),  # markets not a list
        _message(markets=[{"symbol": "BTC"}]),  # no id
        _message(markets=[{"instrument_id": 7, "symbol": "BTC"}]),  # id not a string
        _message(markets=[{"instrument_id": "BTCUSDT-LINEAR.BYBIT"}]),  # no symbol
        _message(markets=[{"instrument_id": "SOL-USD-PERP.HYPERLIQUID", "symbol": "SOL"}]),
        _message(markets=["BTCUSDT-LINEAR.BYBIT"]),  # entry not an object
        _message(ts="1"),
        _message(ts=True),  # a bool is an int to isinstance, never a timestamp
        _message(venue=""),
    ],
)
def test_a_malformed_message_is_warned_and_the_last_good_list_kept(
    message: object, caplog: Any
) -> None:
    markets_state._handle_markets_message(_message(), now=_T0)
    with caplog.at_level(logging.WARNING, logger=markets_state.__name__):
        markets_state._handle_markets_message(message, now=_T0 + 60)
    assert "markets:live message malformed" in caplog.text
    assert [iid for iid, _ in markets_state.live_markets(_T0)["BYBIT"]] == [
        "BTCUSDT-LINEAR.BYBIT",
        "SOLUSDT-SPOT.BYBIT",
    ]
    assert markets_state.received_at("BYBIT") == _T0


def test_unparseable_json_is_warned_not_raised(caplog: Any) -> None:
    with caplog.at_level(logging.WARNING, logger=markets_state.__name__):
        markets_state._ingest("{not json")
    assert "parse/ingest error" in caplog.text
    assert markets_state.live_markets() == {}


def test_a_valid_message_on_the_wire_is_ingested() -> None:
    markets_state._ingest(json.dumps(_message()))
    assert markets_state.live_venues() == ["BYBIT"]


def test_a_venue_is_stale_after_three_missed_polls_and_not_before() -> None:
    markets_state._handle_markets_message(_message(), now=_T0)
    assert markets_state.venue_markets_stale("BYBIT", _T0 + 180) is False
    assert markets_state.venue_markets_stale("BYBIT", _T0 + 181) is True
    assert markets_state.live_venues(_T0 + 181) == ["BYBIT"]  # stale, never hidden early


def test_a_venue_leaves_the_browser_only_after_the_expiry() -> None:
    markets_state._handle_markets_message(_message(), now=_T0)
    assert markets_state.live_venues(_T0 + 900) == ["BYBIT"]
    assert markets_state.live_venues(_T0 + 901) == []
    assert markets_state.live_markets(_T0 + 901) == {}


def test_a_venue_never_heard_from_is_stale_and_not_live() -> None:
    assert markets_state.venue_markets_stale("BYBIT", _T0) is True
    assert markets_state.live_venues(_T0) == []


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _silent_pubsub(clock: _Clock, answers_ping: bool) -> Any:
    """Return a pubsub delivering nothing, each poll advancing `clock` 5 s; a PING queues a pong."""

    class _PubSub:
        def __init__(self) -> None:
            self.pings = 0
            self._pong = False

        async def get_message(self, **_kwargs: object) -> dict | None:
            clock.now += 5.0
            if self._pong:
                self._pong = False
                return {"type": "pong", "data": b""}
            if self.pings >= 3:
                raise asyncio.CancelledError  # end the test once the loop is proven to keep going
            return None

        async def ping(self) -> None:
            self.pings += 1
            self._pong = answers_ping

    return _PubSub()


def test_a_silent_healthy_connection_is_pinged_not_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(markets_state.time, "monotonic", clock)
    pubsub = _silent_pubsub(clock, answers_ping=True)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(markets_state._receive(pubsub))
    assert pubsub.pings == 3  # one PING per silent window, each answered: never a reconnect


def test_an_unanswered_ping_raises_so_the_listener_resubscribes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _Clock()
    monkeypatch.setattr(markets_state.time, "monotonic", clock)
    pubsub = _silent_pubsub(clock, answers_ping=False)
    with pytest.raises(ConnectionError, match="liveness PING"):
        asyncio.run(markets_state._receive(pubsub))
    assert pubsub.pings == 1
