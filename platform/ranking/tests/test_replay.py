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

Story 30.2 made `snapshots:raw` the exact integer layout. The recorded float burst is still
generated (its digest still proves the input) and each entry is re-encoded exactly at 6 price and
3 size decimals -- the digits the generator rounds to -- before it is replayed. The kernel decodes
`units / 10**p` to the nearest double of each decimal, i.e. to the very float the pre-move engine
read, so every published byte and stored row must still match the recording.

Story 31.3 changed exactly one field, deliberately: `spread` (each rank entry's and each stored
row's) is now `kernel.indicators.spread`'s double nearest the exact difference -- the plain float
subtraction of two decoded prices cancelled digits (1e-9 off for a one-tick spread). The fixture is
not re-recorded. The test runs the burst twice: once with the pre-31.3 spread (a plain float
difference, `_pre_31_3_spread`) patched into the board, which must still hash to the recording --
so no other byte moved -- and once as shipped, which must equal those bytes with every `spread`
rounded to the burst's 6 price decimals and nothing else changed (`_spread_rounded`). The story's
other ranking changes (`cvd` None on an empty window, `price` never the slow-loop close, the 300 s
pct bound, the 24 h volatility window, the bounded nearest row) do not arise in this burst: every
row is two-sided and fed, its 30 s spacing never shortens a pct base past 300 s, and it spans
under 2 h.

DW-217 changed exactly one stored row, deliberately: `metrics.db` gets no row for an instrument
that is stale when the slow loop reads the board, so the recording's row for `STALE_IID` (silent
since batch `STALE_AFTER_BATCH`, its last values stamped with the cycle's `ts`) is no longer
written (DATA-01). The fixture is not re-recorded: the stored rows must equal the recorded ones
with that row removed (`_without_stale_row`), and nothing else. No published byte changed: a stale
instrument was never ranked, and the burst has no duplicate or out-of-order snapshot for the
ingest gate to drop.

Story 33.4 appended the derivatives, liquidation and flow keys (`ADDED_33_4`) to each rank entry,
after `volatility` and before `rank`, and the matching numeric columns to `metrics.db`. They are
stripped from each message with `symbol` (`_without_added_keys`) and the stored rows are projected
onto the recorded columns (`_project`), so every pre-33.4 byte, publish decision and stored value is
still proven unchanged; `_assert_added_keys_placed` pins where the new keys sit. Story 33.7 appended
the open-interest percent changes (`ADDED_33_7`) after them, stripped and projected the same way.
"""

import asyncio
import hashlib
import json
import random
import sqlite3
from pathlib import Path

import pytest
from kernel.tests.snapshot_factory import wire

from ranking.application.engine import RankingConfig
from ranking.application.engine import RankingEngine
from ranking.application.ports import CONTROL_CHANNEL
from ranking.application.ports import SNAPSHOTS_CHANNEL
from ranking.domain import board as board_module
from ranking.domain.board import RankingBoard
from ranking.domain.board import RankingsPublisher
from ranking.infrastructure.catalog_derivs import CatalogDerivsHistory
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


# The decimals `_snapshot` rounds prices and sizes to: the instrument precisions of the burst.
PRICE_PRECISION = 6
SIZE_PRECISION = 3


def as_integer_wire(batch: str) -> str:
    """Re-encode one recorded float batch in the kernel's integer layout (exact, see docstring)."""
    return json.dumps(
        [
            wire(
                **entry,
                price_precision=PRICE_PRECISION,
                size_precision=SIZE_PRECISION,
            )
            for entry in json.loads(batch)
        ]
    )


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


# Story 33.4's appended rank-entry keys, spelled out in their published order (not imported from
# `ranking.domain.derivs`, which would make the placement check circular).
ADDED_33_4 = (
    "funding_rate",
    "funding_annualised",
    "next_funding_ns",
    "open_interest",
    "oi_change_1h",
    "oi_change_24h",
    "basis_mi_bps",
    "basis_ml_bps",
    "liq_long_1h",
    "liq_short_1h",
    "liq_notional_1h",
    "liq_ratio_1h",
    "forced_share_1h",
    "relative_volume",
    "high_24h",
    "low_24h",
    "range_position_24h",
)
# Story 33.7's appended keys: the open-interest percent changes, right after the 33.4 keys.
ADDED_33_7 = ("oi_change_1h_pct", "oi_change_24h_pct")
ADDED_KEYS = (*ADDED_33_4, *ADDED_33_7)


def _assert_added_keys_placed(message: dict) -> None:
    """Assert the 33.4 and 33.7 keys sit together right after `volatility`; `rank` stays last."""
    for rank in message["ranks"]:
        keys = list(rank)
        start = keys.index("volatility") + 1
        assert tuple(keys[start : start + len(ADDED_KEYS)]) == ADDED_KEYS
        assert keys[-1] == "rank"


def _assert_symbol_right_after_venue(message: dict) -> None:
    """Every rank carries its base symbol, placed right after `venue`."""
    for rank in message["ranks"]:
        keys = list(rank)
        assert keys[keys.index("venue") + 1] == "symbol"
        assert rank["symbol"] == EXPECTED_SYMBOLS[rank["instrument_id"]]


def _without_added_keys(message: str) -> str:
    """
    Return the message as the recording's producer published it: `symbol` (29.1) and every
    `ADDED_33_4`/`ADDED_33_7` key stripped from each rank and the rest re-serialized exactly as the engine does
    (`json.dumps`, insertion order kept). Matching the recorded hashes proves every existing field,
    byte and order is unchanged; the fixture is not re-recorded, which would prove nothing about the
    existing bytes.
    """
    decoded = json.loads(message)
    for rank in decoded["ranks"]:
        for key in ("symbol", *ADDED_KEYS):
            del rank[key]
    return json.dumps(decoded)


def _project(stored: tuple[list[str], list[list]], columns: list[str]) -> tuple[list[str], list]:
    """Return the stored rows restricted to `columns` (the recording's), in that order."""
    names, rows = stored
    indexes = [names.index(column) for column in columns]
    return columns, [[row[i] for i in indexes] for row in rows]


def _pre_31_3_spread(snapshot: dict) -> float | None:
    """Compute the spread every recorded byte was computed with: the plain float difference."""
    if not snapshot["bid_prices"] or not snapshot["ask_prices"]:
        return None
    return snapshot["ask_prices"][0] - snapshot["bid_prices"][0]


def _spread_rounded(message: str) -> str:
    """Return a pre-31.3 message with each rank's `spread` rounded as `kernel.indicators` now does."""
    decoded = json.loads(message)
    for rank in decoded["ranks"]:
        if rank["spread"] is not None:
            rank["spread"] = round(rank["spread"], PRICE_PRECISION)
    return json.dumps(decoded)


def _without_stale_row(columns: list[str], rows: list[list]) -> list[list]:
    """Return the recorded rows minus `STALE_IID`'s, which DW-217 no longer writes."""
    iid = columns.index("instrument_id")
    kept = [r for r in rows if r[iid] != STALE_IID]
    assert len(kept) == len(rows) - 1  # the recording held exactly one: the removal is not vacuous
    return kept


def _run_burst(db_path: Path, catalog_path: Path) -> list[str]:
    batches = generate_burst()
    clock = FakeClock(WALL0_NS)
    board = RankingBoard(
        RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time),
        volatility_lookback_seconds=3600,
        volume_max_age_ns=RankingConfig().volume_max_age_ns,
    )
    history = SqliteMetricsStore(str(db_path))
    live = _Live()
    engine = RankingEngine(
        board,
        volume_sources=[_Source(name, volumes) for name, volumes in VOLUMES.items()],
        prices=CatalogPriceHistory(str(catalog_path)),
        derivs=CatalogDerivsHistory(str(catalog_path)),
        history=history,
        live=live,
        markets=_Live(),  # its own channel: nothing it publishes reaches rankings:live
        config=RankingConfig(),
        clock=clock.time_ns,
    )
    asyncio.run(_replay(engine, clock, [as_integer_wire(b) for b in batches]))
    history.close()
    return live.messages


def test_rankings_live_bytes_and_metrics_rows_match_the_pre_move_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = json.loads(_FIXTURE.read_text())
    assert (
        hashlib.sha256("\n".join(generate_burst()).encode()).hexdigest() == fixture["input_sha256"]
    )
    with monkeypatch.context() as patched:
        patched.setattr(board_module, "calc_spread", _pre_31_3_spread)
        messages = _run_burst(tmp_path / "pre.db", tmp_path / "catalog")

    for message in messages:
        # The raw bytes are exactly `json.dumps` of their own decoding, so re-serializing in
        # `_without_symbol` cannot hide a change to the engine's serialization.
        assert json.dumps(json.loads(message)) == message
        _assert_symbol_right_after_venue(json.loads(message))
        _assert_added_keys_placed(json.loads(message))
    recorded = [_without_added_keys(m) for m in messages]
    assert recorded[-1] == fixture["final_message"]
    assert [hashlib.sha256(m.encode()).hexdigest() for m in recorded] == fixture["publish_sha256"]
    stored = _stored_rows(tmp_path / "pre.db")
    # Story 33.4's columns are appended after every recorded one (AD-D12), never between.
    assert stored[0][: len(fixture["metrics_columns"])] == fixture["metrics_columns"]
    assert _project(stored, fixture["metrics_columns"]) == (
        fixture["metrics_columns"],
        _without_stale_row(fixture["metrics_columns"], fixture["metrics_rows"]),
    )


def test_story_31_3_changed_only_the_spread_rounding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As shipped: the pre-31.3 bytes (proven above) with every `spread` rounded, nothing else."""
    with monkeypatch.context() as patched:
        patched.setattr(board_module, "calc_spread", _pre_31_3_spread)
        before = _run_burst(tmp_path / "pre.db", tmp_path / "catalog")
    after = _run_burst(tmp_path / "now.db", tmp_path / "catalog")

    assert after == [_spread_rounded(m) for m in before]
    assert after != before  # the rounding did change bytes: the comparison is not vacuous
    columns, rows = _stored_rows(tmp_path / "pre.db")
    spread = columns.index("spread")
    rounded = [
        [round(v, PRICE_PRECISION) if i == spread and v is not None else v for i, v in enumerate(r)]
        for r in rows
    ]
    assert _stored_rows(tmp_path / "now.db") == (columns, rounded)
