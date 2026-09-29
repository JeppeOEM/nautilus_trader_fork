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
The `rankings:live` replay (Story 25.2, AC #2): the moved engine publishes the exact bytes the
pre-move ranking engine module published for one recorded `snapshots:raw` burst.

`fixtures/replay_burst.json` was recorded by driving the pre-move engine (module globals, `time`
patched to a fixed clock) through this sequence: a volume cycle; the burst below, one Redis
message per batch with a second volume cycle after batch 100; 12 quiet heartbeat ticks; one slow
loop cycle over an empty catalog and an empty `metrics.db`; an unknown mode, then a switch to
volatility; one more batch. It holds the burst's digest (so the generator below is proven to be
the recorded input), the sha256 of every published message, the last message in full and every
persisted `metrics.db` row. Any change to a published byte, to a publish decision or to a stored
row fails here. Story 29.1 added `symbol` to each rank entry (right after `venue`): with it
stripped, every message still hashes to the recorded bytes, so it is the only difference.
"""

import asyncio
import hashlib
import json
import random
import sqlite3
from pathlib import Path

from ranking.application.engine import RankingConfig
from ranking.application.engine import RankingEngine
from ranking.application.ports import CONTROL_CHANNEL
from ranking.application.ports import SNAPSHOTS_CHANNEL
from ranking.domain.board import RankingBoard
from ranking.domain.board import RankingsPublisher
from ranking.infrastructure.catalog_prices import CatalogPriceHistory
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from ranking.tests.support import FakeClock


_FIXTURE = Path(__file__).parent / "fixtures" / "replay_burst.json"

INSTRUMENTS = (
    "BTC-USD-PERP.DYDX",
    "ETHUSDT-LINEAR.BYBIT",
    "BTCUSDT-SPOT.BYBIT",
    "SOL-USD-PERP.HYPERLIQUID",
    "XRPUSDT-SPOT.BYBIT",  # no USD volume from any source
    "DOGE-USD-PERP.DYDX",  # goes silent after STALE_AFTER_BATCH
)
BASE_PRICES = {
    "BTC-USD-PERP.DYDX": 65000.0,
    "ETHUSDT-LINEAR.BYBIT": 3400.0,
    "BTCUSDT-SPOT.BYBIT": 65010.0,
    "SOL-USD-PERP.HYPERLIQUID": 150.0,
    "XRPUSDT-SPOT.BYBIT": 0.55,
    "DOGE-USD-PERP.DYDX": 0.12,
}
STALE_IID = "DOGE-USD-PERP.DYDX"
STALE_AFTER_BATCH = 40
WALL0_NS = 1_800_000_000_000_000_000
TS0_NS = WALL0_NS - 2 * 3_600_000_000_000  # ts_event spans > 1h, so pct_1h is defined
TS_STEP_NS = 30_000_000_000
BATCHES = 200
SEED = 2502
VOLUMES = {
    "dydx": {"BTC-USD-PERP.DYDX": 900_000_000.0, "DOGE-USD-PERP.DYDX": 40_000_000.0},
    "bybit-linear": {"ETHUSDT-LINEAR.BYBIT": 700_000_000.0},
    "bybit-spot": {"BTCUSDT-SPOT.BYBIT": 300_000_000.0},
    "hyperliquid": {"SOL-USD-PERP.HYPERLIQUID": 120_000_000.0},
}


def _snapshot(rng: random.Random, iid: str, mids: dict[str, float], k: int) -> dict:
    mid = mids[iid] = round(mids[iid] * (1 + rng.uniform(-0.002, 0.002)), 6)
    tick = mid * 0.0001
    bids = [round(mid - tick * (i + 1), 6) for i in range(5)]
    asks = [round(mid + tick * (i + 1), 6) for i in range(5)]
    traded = rng.random() < 0.7
    close = round(mid + rng.uniform(-tick, tick), 6) if traded else None
    ts = TS0_NS + k * TS_STEP_NS
    return {
        "instrument_id": iid,
        "bid_prices": bids,
        "bid_sizes": [round(rng.uniform(0.1, 5.0), 3) for _ in range(5)],
        "ask_prices": asks,
        "ask_sizes": [round(rng.uniform(0.1, 5.0), 3) for _ in range(5)],
        "buy_volume": round(rng.uniform(0.0, 3.0), 3) if traded else 0.0,
        "sell_volume": round(rng.uniform(0.0, 3.0), 3) if traded else 0.0,
        "buy_count": rng.randint(0, 9) if traded else 0,
        "sell_count": rng.randint(0, 9) if traded else 0,
        "open_price": close,
        "high_price": close,
        "low_price": close,
        "close_price": close,
        "ts_event": ts,
        "ts_init": ts + 1_000,
    }


def generate_burst() -> list[str]:
    """Generate the recorded burst: one `snapshots:raw` message (a JSON batch) per second."""
    rng = random.Random(SEED)  # noqa: S311 -- deterministic test data, not cryptography
    mids = dict(BASE_PRICES)
    return [
        json.dumps(
            [
                _snapshot(rng, iid, mids, k)
                for iid in INSTRUMENTS
                if not (iid == STALE_IID and k >= STALE_AFTER_BATCH)
            ]
        )
        for k in range(BATCHES)
    ]


class _Source:
    def __init__(self, name: str, volumes: dict[str, float]) -> None:
        self.name = name
        self._volumes = volumes

    async def fetch(self) -> dict[str, float]:
        return dict(self._volumes)


class _Live:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def publish(self, message: str) -> None:
        self.messages.append(message)


async def _replay(engine: RankingEngine, clock: FakeClock, batches: list[str]) -> None:
    await engine.volume_cycle()
    for k, raw in enumerate(batches):
        await engine.handle(SNAPSHOTS_CHANNEL, raw)
        clock.ns += 1_000_000_000
        if k == 100:
            await engine.volume_cycle()
    for _ in range(12):  # a quiet market: heartbeats only
        await engine.maybe_publish()
        clock.ns += 1_000_000_000
    await engine.slow_loop_once()
    await engine.maybe_publish()
    clock.ns += 1_000_000_000
    await engine.handle(CONTROL_CHANNEL, json.dumps({"mode": "bogus"}))
    clock.ns += 1_000_000_000
    await engine.handle(CONTROL_CHANNEL, json.dumps({"mode": "volatility"}))
    clock.ns += 1_000_000_000
    last_ts, next_ts = (str(TS0_NS + n * TS_STEP_NS) for n in (199, 200))
    await engine.handle(SNAPSHOTS_CHANNEL, batches[-1].replace(last_ts, next_ts))


def _stored_rows(db_path: Path) -> tuple[list[str], list[list]]:
    db = sqlite3.connect(db_path)
    try:
        cursor = db.execute("SELECT * FROM snapshots ORDER BY ts, instrument_id")
        rows = [list(r) for r in cursor]
        return [d[0] for d in cursor.description], rows
    finally:
        db.close()


# Story 29.1's added field, spelled out (not recomputed with `base_symbol`, which would be circular).
EXPECTED_SYMBOLS = {
    "BTC-USD-PERP.DYDX": "BTC",
    "ETHUSDT-LINEAR.BYBIT": "ETH",
    "BTCUSDT-SPOT.BYBIT": "BTC",
    "SOL-USD-PERP.HYPERLIQUID": "SOL",
    "XRPUSDT-SPOT.BYBIT": "XRP",
    "DOGE-USD-PERP.DYDX": "DOGE",
}


def _assert_symbol_right_after_venue(message: dict) -> None:
    """Every rank carries its base symbol, placed right after `venue`."""
    for rank in message["ranks"]:
        keys = list(rank)
        assert keys[keys.index("venue") + 1] == "symbol"
        assert rank["symbol"] == EXPECTED_SYMBOLS[rank["instrument_id"]]


def _without_symbol(message: str) -> str:
    """
    Return the message as a pre-29.1 producer published it: `symbol` stripped from each rank and
    the rest re-serialized exactly as the engine does (`json.dumps`, insertion order kept). Matching the
    recorded hashes proves every existing field, byte and order is unchanged; the fixture is not
    re-recorded, which would prove nothing about the existing bytes.
    """
    decoded = json.loads(message)
    for rank in decoded["ranks"]:
        del rank["symbol"]
    return json.dumps(decoded)


def test_rankings_live_bytes_and_metrics_rows_match_the_pre_move_engine(tmp_path: Path) -> None:
    fixture = json.loads(_FIXTURE.read_text())
    batches = generate_burst()
    clock = FakeClock(WALL0_NS)
    board = RankingBoard(
        RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time),
        volatility_lookback_seconds=3600,
        volume_max_age_ns=RankingConfig().volume_max_age_ns,
    )
    history = SqliteMetricsStore(str(tmp_path / "metrics.db"))
    live = _Live()
    engine = RankingEngine(
        board,
        volume_sources=[_Source(name, volumes) for name, volumes in VOLUMES.items()],
        prices=CatalogPriceHistory(str(tmp_path / "catalog")),
        history=history,
        live=live,
        markets=_Live(),  # its own channel: nothing it publishes reaches rankings:live
        config=RankingConfig(),
        clock=clock.time_ns,
    )

    asyncio.run(_replay(engine, clock, batches))
    history.close()

    assert hashlib.sha256("\n".join(batches).encode()).hexdigest() == fixture["input_sha256"]
    for message in live.messages:
        # The raw bytes are exactly `json.dumps` of their own decoding, so re-serializing in
        # `_without_symbol` cannot hide a change to the engine's serialization.
        assert json.dumps(json.loads(message)) == message
        _assert_symbol_right_after_venue(json.loads(message))
    pre_29_1 = [_without_symbol(m) for m in live.messages]
    assert pre_29_1[-1] == fixture["final_message"]
    assert [hashlib.sha256(m.encode()).hexdigest() for m in pre_29_1] == fixture["publish_sha256"]
    assert _stored_rows(tmp_path / "metrics.db") == (
        fixture["metrics_columns"],
        fixture["metrics_rows"],
    )
