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
"""`views.collected_markets`: a venue's collected set is known only from a fresh, valid snapshot."""

import asyncio
import json
from pathlib import Path

import pytest
import redis.asyncio as aioredis
from observability import error_ledger

from views.collected_markets import COLLECTED_KEY_PREFIX
from views.collected_markets import STALE_AFTER_SECONDS
from views.collected_markets import CollectedMarkets
from views.collected_markets import collected_ids
from views.collected_markets import with_collected


_NOW = 1_800_000_000_000_000_000
_BTC = "BTCUSDT-LINEAR.BYBIT"


def _snapshot(**over: object) -> str:
    body = {"venue": "BYBIT", "ts": _NOW, "collected": [_BTC], "pending": []}
    body.update(over)
    return json.dumps(body)


@pytest.fixture(autouse=True)
def _fresh_ledger() -> None:
    error_ledger.reset()


def test_a_fresh_snapshot_gives_its_collected_ids() -> None:
    assert collected_ids("BYBIT", _snapshot(), _NOW) == frozenset({_BTC})


def test_no_snapshot_or_a_stale_one_is_unknown_not_empty() -> None:
    stale = _snapshot(ts=_NOW - int(STALE_AFTER_SECONDS * 1e9) - 1)
    assert collected_ids("BYBIT", None, _NOW) is None
    assert collected_ids("BYBIT", stale, _NOW) is None
    assert error_ledger.counts() == {}


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        _snapshot(venue="HYPERLIQUID"),
        _snapshot(ts="now"),
        _snapshot(ts=True),
        _snapshot(collected="BTC"),
        _snapshot(collected=["SOL-USD-PERP.HYPERLIQUID"]),
    ],
)
def test_a_malformed_snapshot_is_unknown_and_ledgered(raw: str) -> None:
    assert collected_ids("BYBIT", raw, _NOW) is None
    assert error_ledger.counts() == {"views.collected_markets": 1}


def test_with_collected_marks_each_item_true_false_or_unknown() -> None:
    listing = {
        "items": [
            {"instrument_id": _BTC, "venue": "BYBIT"},
            {"instrument_id": "ETHUSDT-LINEAR.BYBIT", "venue": "BYBIT"},
            {"instrument_id": "SOL-USD-PERP.HYPERLIQUID", "venue": "HYPERLIQUID"},
        ],
        "stale_venues": [],
    }
    marked = with_collected(listing, {"BYBIT": frozenset({_BTC}), "HYPERLIQUID": None})
    assert [i["collected"] for i in marked["items"]] == [True, False, None]


class _Client:
    def __init__(self, values: dict[str, str] | None, error: Exception | None = None) -> None:
        self.values = values or {}
        self.error = error

    async def mget(self, keys: list[str]) -> list[str | None]:
        if self.error is not None:
            raise self.error
        return [self.values.get(key) for key in keys]


def test_by_venue_reads_each_venues_key(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client({f"{COLLECTED_KEY_PREFIX}BYBIT": _snapshot()})
    monkeypatch.setattr(aioredis.Redis, "from_url", staticmethod(lambda *a, **k: client))
    reader = CollectedMarkets("redis://127.0.0.1:6379")
    sets = asyncio.run(reader.by_venue(["BYBIT", "HYPERLIQUID"], _NOW))
    assert sets == {"BYBIT": frozenset({_BTC}), "HYPERLIQUID": None}


def test_a_failed_read_leaves_every_venue_unknown_and_is_ledgered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _Client(None, ConnectionError("refused"))
    monkeypatch.setattr(aioredis.Redis, "from_url", staticmethod(lambda *a, **k: client))
    reader = CollectedMarkets("redis://127.0.0.1:6379")
    assert asyncio.run(reader.by_venue(["BYBIT"], _NOW)) == {"BYBIT": None}
    assert error_ledger.counts() == {"views.collected_markets": 1}


def test_the_key_prefix_mirrors_collection_control() -> None:
    ports = Path(__file__).parents[2] / "collection_control/application/ports.py"
    assert f'COLLECTED_KEY_PREFIX = "{COLLECTED_KEY_PREFIX}"' in ports.read_text()
