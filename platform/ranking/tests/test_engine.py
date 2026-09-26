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
Tests for `RankingEngine` (Story 25.2): the message handler, the volume poll cycle, the slow metrics
loop and the publish lock, driven through fake ports and a real board, SQLite store and catalog.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from ranking.application.engine import RankingConfig
from ranking.application.engine import RankingEngine
from ranking.application.ports import CONTROL_CHANNEL
from ranking.application.ports import SNAPSHOTS_CHANNEL
from ranking.domain.board import STALE_NS
from ranking.domain.board import RankingBoard
from ranking.infrastructure.catalog_prices import CatalogPriceHistory
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from ranking.tests.support import NOW_NS
from ranking.tests.support import SEC_NS
from ranking.tests.support import FakeClock
from ranking.tests.support import board
from ranking.tests.support import mark_fresh
from ranking.tests.support import snap_dict


BTC = "BTC-USD-PERP.DYDX"


class FakeSource:
    """A `VolumeSource` answering fixed volumes, raising, or hanging."""

    def __init__(self, name: str, volumes: dict[str, float] | Exception | None) -> None:
        self.name = name
        self._volumes = volumes

    async def fetch(self) -> dict[str, float]:
        if isinstance(self._volumes, Exception):
            raise self._volumes
        if self._volumes is None:
            await asyncio.sleep(10)
            return {}
        return dict(self._volumes)


class FakeLive:
    """A `LivePublisher` that records, yielding at the await like a real client."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def publish(self, message: str) -> None:
        await asyncio.sleep(0)
        self.messages.append(message)


class FakePrices:
    """A `PriceHistory` counting its reads; `fail` makes every read raise."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self._fail = fail

    def series(self, instrument_id: str, start_ns: int) -> list[tuple[int, float]]:
        self.calls.append(instrument_id)
        if self._fail:
            raise RuntimeError("simulated corrupt catalog partition")
        return []


_OPENED: list[SqliteMetricsStore] = []


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    error_ledger.reset()
    yield
    while _OPENED:
        _OPENED.pop().close()


def _engine(
    b: RankingBoard | None = None,
    *,
    sources: list[FakeSource] | None = None,
    prices: object | None = None,
    clock: FakeClock | None = None,
    config: RankingConfig | None = None,
) -> tuple[RankingEngine, FakeLive, SqliteMetricsStore]:
    clock = clock or FakeClock()
    history = SqliteMetricsStore(":memory:")
    _OPENED.append(history)
    live = FakeLive()
    engine = RankingEngine(
        b or board(clock),
        volume_sources=sources or [],
        prices=prices or FakePrices(),  # type: ignore[arg-type]
        history=history,
        live=live,
        config=config or RankingConfig(),
        clock=clock.time_ns,
    )
    return engine, live, history


# --- snapshots:raw / ranking:control -----------------------------------------------------------


def test_one_malformed_entry_is_ledgered_and_the_rest_of_the_batch_ingested() -> None:
    b = board()
    engine, live, _ = _engine(b)
    batch = [{"instrument_id": "BAD-USD-PERP.DYDX"}, snap_dict("GOOD-USD-PERP.DYDX")]

    asyncio.run(engine.handle(SNAPSHOTS_CHANNEL, json.dumps(batch)))

    assert b.instrument_ids() == ["GOOD-USD-PERP.DYDX"]  # the bad entry is not even fresh
    assert error_ledger.counts() == {"ranking_engine.snapshot_entry": 1}
    assert len(live.messages) == 1  # the handled message still publishes


def test_snapshots_are_decoded_only_through_from_dict() -> None:
    """A payload that `DydxSecondSnapshot.from_dict` accepts is ingested exactly as decoded."""
    b = board()
    engine, _, _ = _engine(b)
    wire = snap_dict(BTC, 100.0, 101.0)
    wire.pop("buy_volume")  # from_dict's documented default: no trade volume

    engine.ingest_snapshot_batch([wire])

    assert DydxSecondSnapshot.from_dict(wire).buy_volume == 0.0
    assert b.instrument_ids() == [BTC]
    assert error_ledger.counts() == {}


def test_a_field_the_board_drops_from_a_usable_entry_is_ledgered() -> None:
    b = board()
    engine, _, _ = _engine(b)
    wire = snap_dict(BTC, 100.0, 101.0)
    wire["close_price"] = -1.0

    engine.ingest_snapshot_batch([wire])

    assert b.instrument_ids() == [BTC]  # the rest of the entry was used
    assert error_ledger.counts() == {"ranking_engine.snapshot_entry": 1}
    assert "close_price" in error_ledger.last_details()["ranking_engine.snapshot_entry"]


def test_a_non_list_snapshots_payload_is_one_failed_message() -> None:
    engine, _, _ = _engine()

    asyncio.run(engine.handle(SNAPSHOTS_CHANNEL, json.dumps({"a": 1, "b": 2})))

    assert error_ledger.counts() == {"ranking_engine.message": 1}


@pytest.mark.parametrize(
    "message", [{"mode": "nonsense"}, {"mode": None}, {}, ["volatility"], "volatility"]
)
def test_an_unrecognised_control_message_leaves_the_mode(message: object) -> None:
    b = board()
    engine, _, _ = _engine(b)
    asyncio.run(engine.handle(CONTROL_CHANNEL, json.dumps(message)))
    assert b.mode == "volume"


def test_the_last_control_message_wins_and_is_published() -> None:
    b = board()
    engine, live, _ = _engine(b)
    for mode in ("volatility", "volume", "volatility"):
        asyncio.run(engine.handle(CONTROL_CHANNEL, json.dumps({"mode": mode})))
    assert json.loads(live.messages[-1])["mode"] == "volatility"


def test_a_non_json_message_is_logged_not_raised() -> None:
    engine, live, _ = _engine()
    asyncio.run(engine.handle(SNAPSHOTS_CHANNEL, "{not json"))
    assert live.messages == []


# --- publish: change, heartbeat, lock ----------------------------------------------------------


def test_publishes_on_change_and_on_heartbeat_only() -> None:
    clock = FakeClock()
    b = board(clock)
    engine, live, _ = _engine(b, clock=clock)
    mark_fresh(b, BTC, clock.ns)

    async def _run() -> None:
        await engine.maybe_publish()  # first: always
        clock.ns += SEC_NS
        await engine.maybe_publish()  # nothing changed, inside the heartbeat
        clock.ns += 4 * SEC_NS
        await engine.maybe_publish()  # heartbeat due
        engine.switch_mode({"mode": "volatility"})
        await engine.maybe_publish()  # mode changed

    asyncio.run(_run())

    assert [json.loads(m)["mode"] for m in live.messages] == ["volume", "volume", "volatility"]


def test_the_publish_lock_prevents_a_duplicate_publish_from_concurrent_callers() -> None:
    b = board()
    engine, live, _ = _engine(b)
    mark_fresh(b, BTC, NOW_NS)

    async def _run() -> None:
        await asyncio.gather(engine.maybe_publish(), engine.maybe_publish())

    asyncio.run(_run())

    assert len(live.messages) == 1


# --- volume cycle ------------------------------------------------------------------------------


def test_a_failing_source_keeps_its_last_good_values_and_the_rest_update() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    mark_fresh(b, "BTCUSDT-LINEAR.BYBIT", NOW_NS)
    first = [
        FakeSource("dydx", {BTC: 1.0}),
        FakeSource("bybit-linear", {"BTCUSDT-LINEAR.BYBIT": 2.0}),
    ]
    asyncio.run(_engine(b, sources=first)[0].volume_cycle())

    second = [FakeSource("dydx", {BTC: 10.0}), FakeSource("bybit-linear", OSError("outage"))]
    asyncio.run(_engine(b, sources=second)[0].volume_cycle())

    assert {r["instrument_id"]: r["volume24h"] for r in b.current_ranks(NOW_NS)} == {
        BTC: 10.0,
        "BTCUSDT-LINEAR.BYBIT": 2.0,
    }
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}
    assert error_ledger.last_details()["ranking_engine.volume24h"].startswith("bybit-linear:")


def test_an_empty_poll_is_a_failure_that_keeps_the_last_good_values() -> None:
    b = board()
    mark_fresh(b, "BTCUSDT-LINEAR.BYBIT", NOW_NS)
    asyncio.run(
        _engine(b, sources=[FakeSource("bybit-linear", {"BTCUSDT-LINEAR.BYBIT": 2.0})])[
            0
        ].volume_cycle()
    )
    asyncio.run(_engine(b, sources=[FakeSource("bybit-linear", {})])[0].volume_cycle())

    assert [r["volume24h"] for r in b.current_ranks(NOW_NS)] == [2.0]
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}


def test_a_hung_source_times_out_without_stalling_the_others() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    config = RankingConfig(volume_fetch_timeout_s=0.05)
    sources = [FakeSource("dydx", {BTC: 1.0}), FakeSource("hyperliquid", None)]

    asyncio.run(_engine(b, sources=sources, config=config)[0].volume_cycle())

    assert [r["volume24h"] for r in b.current_ranks(NOW_NS)] == [1.0]
    assert error_ledger.last_details()["ranking_engine.volume24h"].startswith("hyperliquid:")


def test_each_fresh_instrument_without_volume_is_ledgered_once_per_cycle() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    mark_fresh(b, "ETHBTC-SPOT.BYBIT", NOW_NS)  # collected, but no USD volume exists for it
    mark_fresh(b, "SOL-USD-PERP.HYPERLIQUID", NOW_NS)  # venue lists no such coin
    mark_fresh(b, "DEAD-USD-PERP.DYDX", NOW_NS - STALE_NS - 1)  # stale: not counted
    sources = [
        FakeSource("dydx", {BTC: 1.0}),
        FakeSource("hyperliquid", {"BTC-USD-PERP.HYPERLIQUID": 5.0}),
    ]
    engine = _engine(b, sources=sources)[0]

    asyncio.run(engine.volume_cycle())
    assert error_ledger.counts() == {"ranking_engine.volume24h": 2}
    asyncio.run(engine.volume_cycle())
    assert error_ledger.counts() == {"ranking_engine.volume24h": 4}


def test_an_expired_source_is_ledgered_and_leaves_volume_mode() -> None:
    clock = FakeClock()
    b = board(clock)
    engine = _engine(b, sources=[FakeSource("dydx", {BTC: 1.0})], clock=clock)[0]
    asyncio.run(engine.volume_cycle())
    clock.ns += RankingConfig().volume_max_age_ns + SEC_NS
    mark_fresh(b, BTC, clock.ns)

    asyncio.run(_engine(b, clock=clock)[0].volume_cycle())

    assert b.current_ranks(clock.ns) == []
    # one entry for the expired source, one for BTC now missing a volume
    assert error_ledger.counts() == {"ranking_engine.volume24h": 2}


# --- slow loop and backfill --------------------------------------------------------------------


def _write_snapshot(catalog_path: str, iid: str, close_price: float, ts: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(iid),
                bid_prices=[close_price - 1],
                bid_sizes=[1.0],
                ask_prices=[close_price + 1],
                ask_sizes=[1.0],
                buy_volume=1.0,
                sell_volume=0.0,
                buy_count=1,
                sell_count=0,
                open_price=close_price,
                high_price=close_price,
                low_price=close_price,
                close_price=close_price,
                ts_event=ts,
                ts_init=ts,
            )
        ]
    )


def test_slow_loop_backfills_from_the_catalog_and_persists_rank_and_volume(tmp_path: Path) -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    b.record_volume_poll("dydx", {BTC: 50_000_000.0}, NOW_NS)
    b.refresh_volumes(NOW_NS)
    _write_snapshot(str(tmp_path), BTC, close_price=100.0, ts=NOW_NS - SEC_NS)
    engine, _, history = _engine(b, prices=CatalogPriceHistory(str(tmp_path)))

    asyncio.run(engine.slow_loop_once())

    row = history.nearest(BTC, NOW_NS)
    assert row is not None
    assert (row["price"], row["rank"], row["volume24h"]) == (100.0, 1, 50_000_000.0)
    assert b.current_ranks(NOW_NS)[0]["price"] == 100.0  # the slow cache feeds the rank entry


def test_backfill_reads_each_instrument_exactly_once_across_cycles() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    prices = FakePrices()
    engine = _engine(b, prices=prices)[0]

    for _ in range(3):
        asyncio.run(engine.slow_loop_once())

    assert prices.calls == [BTC]


def test_a_failed_backfill_is_ledgered_and_never_retried() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    prices = FakePrices(fail=True)
    engine = _engine(b, prices=prices)[0]

    asyncio.run(engine.slow_loop_once())
    asyncio.run(engine.slow_loop_once())

    assert prices.calls == [BTC]
    assert error_ledger.counts() == {"ranking_engine.price_backfill": 1}
