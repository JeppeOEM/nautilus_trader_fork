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
from decimal import Decimal
from pathlib import Path

import pytest
from kernel.derivs_wire import DerivsTick
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from ranking.application.engine import DERIVS_BACKFILL_SITE
from ranking.application.engine import DERIVS_SITE
from ranking.application.engine import LIQUIDATION_SITE
from ranking.application.engine import MARKETS_SITE
from ranking.application.engine import RankingConfig
from ranking.application.engine import RankingEngine
from ranking.application.engine import markets_message
from ranking.application.engine import short_repr
from ranking.application.ports import CONTROL_CHANNEL
from ranking.application.ports import DERIVS_CHANNEL
from ranking.application.ports import LIQUIDATIONS_CHANNEL
from ranking.application.ports import SNAPSHOTS_CHANNEL
from ranking.application.ports import PriceHistory
from ranking.domain.board import STALE_NS
from ranking.domain.board import RankingBoard
from ranking.domain.price_series import PricePoint
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


class FakeMarkets(FakeLive):
    """A `markets:live` publisher that records, and raises for each venue listed in `fail`."""

    def __init__(self, fail: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self._fail = fail

    async def publish(self, message: str) -> None:
        if json.loads(message)["venue"] in self._fail:
            raise ConnectionError("simulated redis outage")
        await super().publish(message)


class FakePrices:
    """A `PriceHistory` counting its reads; `fail` makes every read raise."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self._fail = fail

    def series(self, instrument_id: str, start_ns: int) -> list[PricePoint]:
        self.calls.append(instrument_id)
        if self._fail:
            raise RuntimeError("simulated corrupt catalog partition")
        return []


class FakeDerivs:
    """A `DerivsHistory` returning the rows given, recording each read; `fail` makes reads raise."""

    def __init__(
        self,
        open_interest: list[OpenInterest] | None = None,
        liquidations: list[Liquidation] | None = None,
        fail: bool = False,
    ) -> None:
        self.calls: list[tuple[str, str, int, int]] = []
        self._open_interest = open_interest or []
        self._liquidations = liquidations or []
        self._fail = fail

    def open_interest(self, instrument_id: str, start_ns: int, end_ns: int) -> list[OpenInterest]:
        self.calls.append(("oi", instrument_id, start_ns, end_ns))
        if self._fail:
            raise RuntimeError("simulated corrupt open-interest partition")
        return self._open_interest

    def liquidations(self, instrument_id: str, start_ns: int, end_ns: int) -> list[Liquidation]:
        self.calls.append(("liq", instrument_id, start_ns, end_ns))
        if self._fail:
            raise RuntimeError("simulated corrupt liquidation partition")
        return self._liquidations


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
    prices: PriceHistory | None = None,
    derivs: FakeDerivs | None = None,
    clock: FakeClock | None = None,
    config: RankingConfig | None = None,
    markets: FakeMarkets | None = None,
) -> tuple[RankingEngine, FakeLive, SqliteMetricsStore]:
    clock = clock or FakeClock()
    history = SqliteMetricsStore(":memory:")
    _OPENED.append(history)
    live = FakeLive()
    engine = RankingEngine(
        b or board(clock),
        volume_sources=sources or [],
        prices=prices or FakePrices(),
        derivs=derivs or FakeDerivs(),
        history=history,
        live=live,
        markets=markets or FakeMarkets(),
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

    engine.ingest_snapshot_batch([wire])

    assert DydxSecondSnapshot.from_dict(wire).bid_prices == [100.0]
    assert b.instrument_ids() == [BTC]
    assert error_ledger.counts() == {}


@pytest.mark.parametrize(
    "broken",
    [
        {"buy_volume": None},  # a missing value: never defaulted
        {"bid_prices": [100.0]},  # a float where the layout holds integer units
        {"price_precision": None},  # a pre-30.2 float-layout entry
    ],
)
def test_an_entry_the_strict_decoder_refuses_is_ledgered_never_guessed(broken: dict) -> None:
    b = board()
    engine, _, _ = _engine(b)
    wire = {**snap_dict(BTC, 100.0, 101.0), **broken}

    engine.ingest_snapshot_batch([wire])

    assert b.instrument_ids() == []
    assert error_ledger.counts() == {"ranking_engine.snapshot_entry": 1}


def test_a_field_the_board_drops_from_a_usable_entry_is_ledgered() -> None:
    b = board()
    engine, _, _ = _engine(b)
    wire = snap_dict(BTC, 100.0, 101.0)
    wire["close_price"] = -10_000  # -1.0 in units of the entry's price precision (4)

    engine.ingest_snapshot_batch([wire])

    assert b.instrument_ids() == [BTC]  # the rest of the entry was used
    assert error_ledger.counts() == {"ranking_engine.snapshot_entry": 1}
    assert "close_price" in error_ledger.last_details()["ranking_engine.snapshot_entry"]


def test_a_duplicate_entry_is_ledgered_once_and_leaves_the_ranks_unchanged() -> None:
    """DW-218: a redelivered `snapshots:raw` entry is dropped whole by the board's ts_event gate."""
    b = board()
    b.record_volume_poll("dydx", {BTC: 1.0}, NOW_NS)
    b.refresh_volumes(NOW_NS)
    engine, _, _ = _engine(b)
    engine.ingest_snapshot_batch([snap_dict(BTC, 100.0, 101.0, ts_event=NOW_NS - SEC_NS)])
    before = b.current_ranks(NOW_NS)

    engine.ingest_snapshot_batch([snap_dict(BTC, 100.0, 101.0, ts_event=NOW_NS - SEC_NS)])

    assert error_ledger.counts() == {"ranking_engine.snapshot_entry": 1}
    assert "duplicate/out-of-order" in error_ledger.last_details()["ranking_engine.snapshot_entry"]
    assert b.current_ranks(NOW_NS) == before


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


# --- markets:live (Story 29.5) -----------------------------------------------------------------


def _markets_cycle(sources: list[FakeSource], markets: FakeMarkets, clock: FakeClock) -> None:
    asyncio.run(
        _engine(board(clock), sources=sources, clock=clock, markets=markets)[0].volume_cycle()
    )


def test_one_markets_message_per_venue_with_bybit_linear_and_spot_merged() -> None:
    markets = FakeMarkets()
    sources = [
        FakeSource("bybit-spot", {"ETHUSDT-SPOT.BYBIT": 1.0, "BTCUSDT-SPOT.BYBIT": 2.0}),
        FakeSource("dydx", {BTC: 3.0}),
        FakeSource("bybit-linear", {"SOLUSDT-LINEAR.BYBIT": 4.0}),
    ]
    _markets_cycle(sources, markets, FakeClock())

    assert [json.loads(m)["venue"] for m in markets.messages] == ["BYBIT", "DYDX"]
    assert [m["instrument_id"] for m in json.loads(markets.messages[0])["markets"]] == [
        "BTCUSDT-SPOT.BYBIT",
        "ETHUSDT-SPOT.BYBIT",
        "SOLUSDT-LINEAR.BYBIT",
    ]
    assert error_ledger.counts() == {}


def test_a_markets_message_has_the_exact_bytes_and_key_order() -> None:
    markets = FakeMarkets()
    _markets_cycle([FakeSource("dydx", {"ETH-USD-PERP.DYDX": 1.0, BTC: 2.0})], markets, FakeClock())

    assert markets.messages == [
        '{"venue": "DYDX", "ts": 1800000000000000000, "markets": ['
        '{"instrument_id": "BTC-USD-PERP.DYDX", "symbol": "BTC"}, '
        '{"instrument_id": "ETH-USD-PERP.DYDX", "symbol": "ETH"}]}'
    ]
    assert markets.messages == [markets_message("DYDX", NOW_NS, ["ETH-USD-PERP.DYDX", BTC])]


def test_an_expired_source_is_left_out_and_a_venue_with_none_publishes_nothing() -> None:
    clock = FakeClock()
    b = board(clock)
    first = [
        FakeSource("bybit-linear", {"BTCUSDT-LINEAR.BYBIT": 1.0}),
        FakeSource("bybit-spot", {"BTCUSDT-SPOT.BYBIT": 1.0}),
        FakeSource("hyperliquid", {"SOL-USD-PERP.HYPERLIQUID": 1.0}),
    ]
    asyncio.run(_engine(b, sources=first, clock=clock)[0].volume_cycle())
    clock.ns += RankingConfig().volume_max_age_ns + SEC_NS
    markets = FakeMarkets()
    refreshed = [FakeSource("bybit-spot", {"BTCUSDT-SPOT.BYBIT": 1.0})]

    asyncio.run(_engine(b, sources=refreshed, clock=clock, markets=markets)[0].volume_cycle())

    assert [json.loads(m) for m in markets.messages] == [
        {
            "venue": "BYBIT",
            "ts": clock.ns,
            "markets": [{"instrument_id": "BTCUSDT-SPOT.BYBIT", "symbol": "BTC"}],
        }
    ]


def test_a_failed_markets_publish_is_ledgered_and_the_other_venues_still_go_out() -> None:
    markets = FakeMarkets(fail=frozenset({"BYBIT"}))
    sources = [
        FakeSource("bybit-linear", {"BTCUSDT-LINEAR.BYBIT": 1.0}),
        FakeSource("dydx", {BTC: 1.0}),
        FakeSource("hyperliquid", {"SOL-USD-PERP.HYPERLIQUID": 1.0}),
    ]
    _markets_cycle(sources, markets, FakeClock())

    assert [json.loads(m)["venue"] for m in markets.messages] == ["DYDX", "HYPERLIQUID"]
    assert error_ledger.counts() == {MARKETS_SITE: 1}
    assert error_ledger.last_details()[MARKETS_SITE].startswith("BYBIT:")


def test_an_id_without_a_venue_is_ledgered_and_left_out_of_every_message() -> None:
    markets = FakeMarkets()
    _markets_cycle([FakeSource("dydx", {BTC: 1.0, "NO-SUFFIX": 1.0})], markets, FakeClock())

    assert [m["instrument_id"] for m in json.loads(markets.messages[0])["markets"]] == [BTC]
    assert len(markets.messages) == 1
    assert error_ledger.counts() == {MARKETS_SITE: 1}


# --- slow loop and backfill --------------------------------------------------------------------


def _write_snapshot(catalog_path: str, iid: str, close_price: float, ts: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            make_snapshot(
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
    # The rank entry's `price` is the live mid only (Story 31.3): no book fed yet, so None --
    # the slow loop's trade close never stands in for it.
    assert b.current_ranks(NOW_NS)[0]["price"] is None


def test_backfill_reads_each_instrument_exactly_once_across_cycles() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    prices = FakePrices()
    engine = _engine(b, prices=prices)[0]

    for _ in range(3):
        asyncio.run(engine.slow_loop_once())

    assert prices.calls == [BTC]


class _SlowPrices(FakePrices):
    """A backfill read during which `seconds` of wall clock pass, as a real catalog read may."""

    def __init__(self, clock: FakeClock, seconds: int) -> None:
        super().__init__()
        self._clock = clock
        self._seconds = seconds

    def series(self, instrument_id: str, start_ns: int) -> list[PricePoint]:
        self._clock.ns += self._seconds * SEC_NS
        return super().series(instrument_id, start_ns)


def test_a_slow_row_is_stamped_when_the_board_is_read_not_when_the_cycle_began() -> None:
    # Story 31.9's SSOT trace: a batch ingested during the cycle's awaits is in the row, so a
    # stamp taken before them would date the row before its own state.
    clock = FakeClock()
    b = board(clock)
    mark_fresh(b, BTC, NOW_NS)
    engine, _, history = _engine(b, prices=_SlowPrices(clock, seconds=7), clock=clock)

    asyncio.run(engine.slow_loop_once())

    row = history.nearest(BTC, NOW_NS)
    assert row is not None
    assert row["ts"] == NOW_NS + 7 * SEC_NS


class _DirtyPrices(FakePrices):
    """A catalog series with a duplicate ts and a non-positive price, unsorted."""

    def series(self, instrument_id: str, start_ns: int) -> list[PricePoint]:
        super().series(instrument_id, start_ns)
        return [
            _point(NOW_NS - SEC_NS, "101"),
            _point(NOW_NS - 3 * SEC_NS, "0"),
            _point(NOW_NS - SEC_NS, "101"),
        ]


def _point(ts_event: int, close: str) -> PricePoint:
    return PricePoint(ts_event, float(close), Decimal(close), Decimal("1.5"))


def test_every_backfill_drop_is_ledgered() -> None:
    """DW-218: each drop kind the backfill validation reports is one `price_backfill` entry."""
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    engine, _, history = _engine(b, prices=_DirtyPrices())

    asyncio.run(engine.slow_loop_once())

    assert error_ledger.counts() == {"ranking_engine.price_backfill": 2}
    row = history.nearest(BTC, NOW_NS)
    assert row is not None
    assert row["price"] == 101.0  # the valid point was still seeded


def test_a_failed_backfill_is_ledgered_and_never_retried() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    prices = FakePrices(fail=True)
    engine = _engine(b, prices=prices)[0]

    asyncio.run(engine.slow_loop_once())
    asyncio.run(engine.slow_loop_once())

    assert prices.calls == [BTC]
    assert error_ledger.counts() == {"ranking_engine.price_backfill": 1}


# --- derivs:raw / liquidations:raw (Story 33.4) ------------------------------------------------

LINEAR = "BTCUSDT-LINEAR.BYBIT"
SPOT = "BTCUSDT-SPOT.BYBIT"
HL = "SOL-USD-PERP.HYPERLIQUID"


def _funding_row(iid: str = LINEAR) -> dict:
    tick = DerivsTick(iid, "funding", NOW_NS, NOW_NS, Decimal("0.0001"), 28_800, NOW_NS + 1)
    return tick.to_wire()


def _liquidation(event: str = "e1", ts: int = NOW_NS - SEC_NS) -> Liquidation:
    return Liquidation(
        instrument_id=InstrumentId.from_str(LINEAR),
        side=LiquidatedSide.LONG,
        size_units=2000,
        price_units=1000,
        price_precision=1,
        size_precision=3,
        venue_event_id=event,
        ts_event=ts,
        ts_init=ts,
    )


def _slow_row(b: RankingBoard, iid: str) -> dict:
    (row,) = [r for r in b.slow_rows(NOW_NS, {}, {}) if r["instrument_id"] == iid]
    return row


def test_a_derivs_batch_reaches_the_board_and_a_bad_row_is_ledgered_alone() -> None:
    b = board()
    mark_fresh(b, LINEAR, NOW_NS)
    engine, live, _ = _engine(b)
    bad = {**_funding_row(), "value": 0.0001}  # a JSON number: not exact text

    asyncio.run(engine.handle(DERIVS_CHANNEL, json.dumps([bad, _funding_row()])))

    assert error_ledger.counts() == {DERIVS_SITE: 1}
    assert _slow_row(b, LINEAR)["funding_rate"] == 0.0001
    assert live.messages == []  # a derivs row waits for the slow row: no publish decision


def test_a_derivs_row_the_board_refuses_is_ledgered() -> None:
    b = board()
    engine = _engine(b)[0]
    older = DerivsTick(LINEAR, "mark", NOW_NS - SEC_NS, NOW_NS, Decimal(1)).to_wire()
    newer = DerivsTick(LINEAR, "mark", NOW_NS, NOW_NS, Decimal(2)).to_wire()

    asyncio.run(engine.handle(DERIVS_CHANNEL, json.dumps([newer, older])))

    assert error_ledger.counts() == {DERIVS_SITE: 1}


def test_a_liquidation_batch_reaches_the_board_and_a_bad_row_is_ledgered_alone() -> None:
    b = board()
    mark_fresh(b, LINEAR, NOW_NS)
    engine = _engine(b)[0]
    good = Liquidation.to_dict(_liquidation())

    asyncio.run(engine.handle(LIQUIDATIONS_CHANNEL, json.dumps([{"side": "long"}, good, good])))

    assert error_ledger.counts() == {LIQUIDATION_SITE: 1}
    assert _slow_row(b, LINEAR)["liq_long_1h"] == 2.0  # the redelivered row counted once


def test_a_message_of_many_bad_entries_is_ledgered_capped_with_one_summary_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Ten undecodable entries (each a 1 000-character value): the first three itemised, their repr
    cut to 200 characters, then one line naming all ten: four ledger lines, not ten.
    """
    engine = _engine()[0]
    bad = [{**_funding_row(), "value": 0.0001, "pad": "x" * 1000} for _ in range(10)]
    details: list[str] = []
    record = error_ledger.record

    def recording(site: str, detail: str, exc: BaseException | None = None) -> None:
        details.append(detail)
        record(site, detail, exc)

    monkeypatch.setattr(error_ledger, "record", recording)

    asyncio.run(engine.handle(DERIVS_CHANNEL, json.dumps(bad)))

    assert error_ledger.counts() == {DERIVS_SITE: 4}
    assert all(detail.endswith(f"... ({len(repr(bad[0]))} chars)") for detail in details[:3])
    assert all(len(detail) < 300 for detail in details)
    summary = error_ledger.last_details()[DERIVS_SITE]
    assert summary.startswith("10 derivs:raw entries of one message were malformed or refused")


def test_a_ledgered_entry_repr_is_cut_short() -> None:
    entry = {"pad": "x" * 1000}
    text = short_repr(entry)
    assert text == f"{repr(entry)[:200]}... ({len(repr(entry))} chars)"


def test_a_non_list_derivs_payload_is_one_failed_message() -> None:
    engine = _engine()[0]

    asyncio.run(engine.handle(DERIVS_CHANNEL, json.dumps({"kind": "mark"})))

    assert error_ledger.counts() == {"ranking_engine.message": 1}


def test_the_first_backfill_reads_25h_of_oi_and_1h_of_liquidations_once() -> None:
    b = board()
    for iid in (LINEAR, SPOT, HL):
        mark_fresh(b, iid, NOW_NS)
    oi = OpenInterest(InstrumentId.from_str(LINEAR), Decimal(1200), NOW_NS - SEC_NS, NOW_NS)
    derivs = FakeDerivs(open_interest=[oi], liquidations=[_liquidation()])
    engine = _engine(b, derivs=derivs)[0]

    asyncio.run(engine.slow_loop_once())
    asyncio.run(engine.slow_loop_once())

    hour = 3_600 * SEC_NS
    assert sorted(derivs.calls) == [
        ("liq", LINEAR, NOW_NS - hour, NOW_NS),
        ("oi", LINEAR, NOW_NS - 25 * hour, NOW_NS),
        ("oi", HL, NOW_NS - 25 * hour, NOW_NS),
    ]  # spot: neither; Hyperliquid: no liquidation feed; never a second read
    row = _slow_row(b, LINEAR)
    assert (row["open_interest"], row["liq_long_1h"]) == (1200.0, 2.0)


def test_a_failed_derivs_backfill_is_ledgered_and_never_retried() -> None:
    b = board()
    mark_fresh(b, LINEAR, NOW_NS)
    derivs = FakeDerivs(fail=True)
    engine = _engine(b, derivs=derivs)[0]

    asyncio.run(engine.slow_loop_once())
    asyncio.run(engine.slow_loop_once())

    assert len(derivs.calls) == 2  # one open-interest and one liquidation read, ever
    assert error_ledger.counts() == {DERIVS_BACKFILL_SITE: 2}
