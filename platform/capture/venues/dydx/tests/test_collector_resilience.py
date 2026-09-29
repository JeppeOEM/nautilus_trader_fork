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
Tests for VPS-longevity safety behaviors: WS callback fault isolation, the
startup corrupt-parquet quarantine scan, and dYdX's crossed-book handling (DATA-04): the level
tagging and uncross ladder are `capture.venues.dydx.policies` values the core's `LiveBook` and
`SecondSampler` run, and the resync the core's `CaptureService._resync` executes (Story 26.1).
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from observability import error_ledger

from capture.application.capture_service import CaptureService
from capture.application.feed import MAIN_FEED
from capture.application.feed import Feed
from capture.application.ports import PlanChange
from capture.domain.policies import LevelTags
from capture.domain.verdicts import Uncrossed
from capture.infrastructure.parquet_writer import quarantine_corrupt_parquet
from capture.tests.definition_kit import definition
from capture.venues.dydx.__main__ import build_capture
from capture.venues.dydx.config import DydxConfig
from capture.venues.dydx.policies import DydxUncrossPolicy
from capture.venues.dydx.policies import uncross_step
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


def _make_config(catalog_path: Path, snapshot_interval_seconds: float = 1.0) -> DydxConfig:
    return DydxConfig(
        network=DydxNetwork.TESTNET,
        catalog_path=str(catalog_path),
        flush_interval_seconds=60,
        snapshot_interval_seconds=snapshot_interval_seconds,
        config_reload_seconds=60,
        open_interest_poll_seconds=60,
        liquidity_check_seconds=60,
    )


def test_on_data_enqueues_without_processing(tmp_path: Path) -> None:
    """_on_data is just the queue hand-off now -- _ingest_loop does the real work."""
    collector = build_capture(_make_config(tmp_path / "catalog"), ())

    sentinel = object()
    collector._on_data(sentinel)

    assert collector._ingest_queue.qsize() == 1
    assert collector._ingest_queue.get_nowait() == (sentinel, MAIN_FEED)


@pytest.mark.asyncio
async def test_ingest_loop_isolates_bad_message(tmp_path: Path, monkeypatch) -> None:
    """One malformed message must not kill _ingest_loop or block later messages."""
    collector = build_capture(_make_config(tmp_path / "catalog"), ())
    processed: list[object] = []
    good_message = object()

    def _process(data: object, feed: Feed = MAIN_FEED) -> None:
        if data is good_message:
            processed.append(data)
        else:
            raise ValueError("simulated malformed message")

    monkeypatch.setattr(collector, "_process_data", _process)
    collector._on_data(object())  # bad -- would raise inside _process_data
    collector._on_data(good_message)  # must still get processed afterward

    loop_task = asyncio.create_task(collector._ingest_loop())
    await asyncio.sleep(0.05)
    collector._stop.set()
    # _ingest_loop's internal queue.get() has its own 1.0s recheck timeout (see its
    # implementation) -- give the outer wait comfortable room past that.
    await asyncio.wait_for(loop_task, timeout=2.0)

    assert processed == [good_message]


def test_quarantine_corrupt_parquet_moves_only_bad_files(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    good_dir = catalog / "data" / "trade_tick" / _IID
    good_dir.mkdir(parents=True)

    good_file = good_dir / "0-100.parquet"
    table = pa.table({"ts_init": [0, 100]})
    pq.write_table(table, good_file)

    corrupt_file = good_dir / "100-200.parquet"
    corrupt_file.write_bytes(b"not a real parquet file")

    quarantine_corrupt_parquet(str(catalog), [_IID], error_ledger.record)

    assert good_file.exists()
    assert not corrupt_file.exists()
    quarantined = catalog / "_quarantine" / "data" / "trade_tick" / _IID / "100-200.parquet"
    assert quarantined.exists()


def test_quarantine_corrupt_parquet_missing_catalog_is_noop(tmp_path: Path) -> None:
    quarantine_corrupt_parquet(
        str(tmp_path / "does-not-exist"), [_IID], error_ledger.record
    )  # must not raise


class _FakeClient:
    """Records subscribe/unsubscribe calls without touching the network."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def unsubscribe_orderbook(self, iid: str) -> None:
        self.calls.append(f"unsubscribe:{iid}")

    async def subscribe_orderbook(self, iid: str) -> None:
        self.calls.append(f"subscribe:{iid}")

    async def unsubscribe_trades(self, iid: str) -> None:
        self.calls.append(f"unsubscribe_trades:{iid}")

    async def unsubscribe(self, iid: str) -> None:  # `DydxClient.unsubscribe`'s two topics
        await self.unsubscribe_trades(iid)
        await self.unsubscribe_orderbook(iid)

    async def resync_orderbook(self, iid: str) -> None:  # `DydxClient.resync_orderbook`
        await self.unsubscribe_orderbook(iid)
        await self.subscribe_orderbook(iid)


@pytest.mark.asyncio
async def test_resync_book_resubscribes_and_drops_local_state(tmp_path: Path) -> None:
    """
    A desynced book (never self-heals from deltas alone) must be rebuilt from
    a fresh venue snapshot -- _resync_book forces that via unsubscribe+subscribe
    and clears the local book so the next OrderBookDeltas rebuilds it clean.
    """
    iid = "BTC-USD-PERP.DYDX"
    collector = build_capture(_make_config(tmp_path / "catalog"), (iid,))
    collector._applied.add(iid)
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]

    collector._book(iid).book = OrderBook(InstrumentId.from_str(iid), BookType.L2_MBP)
    collector._book(iid).crossed_since_ns = 123

    await collector._resync(iid)

    assert fake_client.calls == [f"unsubscribe:{iid}", f"subscribe:{iid}"]
    assert collector._live_book(iid) is None
    assert collector._book(iid).crossed_since_ns is None


@pytest.mark.asyncio
async def test_resync_book_never_resubscribes_an_id_no_longer_collected(tmp_path: Path) -> None:
    """A resync queued behind a `stop` must not re-subscribe the book `apply` just ended."""
    iid = "BTC-USD-PERP.DYDX"
    collector = build_capture(_make_config(tmp_path / "catalog"), ())
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]

    await collector._resync(iid)

    assert fake_client.calls == []


@pytest.mark.asyncio
async def test_unsubscribe_drops_crossed_book_tracking_state(tmp_path: Path) -> None:
    """
    A stale `_crossed_since_ns` entry left behind by an unsubscribe would make the first
    harmless cross after a later resubscribe read as hours/days old
    (`setdefault` returns the old value), firing a false steady_state_crossed_book
    CRITICAL and an unwarranted destructive resync on data that was never actually
    stuck. Removing an id from the plan (`apply`) must clear the same per-instrument book
    state `_resync_book` already does.
    """
    iid = "BTC-USD-PERP.DYDX"
    collector = build_capture(_make_config(tmp_path / "catalog"), (iid,))
    collector._applied.add(iid)
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]

    live = collector._book(iid)
    live.book = OrderBook(InstrumentId.from_str(iid), BookType.L2_MBP)
    live.crossed_since_ns = 123
    live.crossed_prices = (100.0, 101.0)
    live.tags[(OrderSide.BUY, 100.0)] = 1

    applied = await collector.apply(PlanChange(removed=frozenset({iid})))

    assert applied.unsubscribed == {iid}

    assert fake_client.calls == [f"unsubscribe_trades:{iid}", f"unsubscribe:{iid}"]
    assert (live.book, live.crossed_since_ns, live.crossed_prices, live.tags) == (
        None,
        None,
        None,
        {},
    )


_IID = "BTC-USD-PERP.DYDX"


def _delta(sequence: int, price: float = 100.0) -> OrderBookDeltas:
    order = BookOrder(side=OrderSide.BUY, price=Price(price, 1), size=Quantity(1.0, 1), order_id=0)
    delta = OrderBookDelta(
        instrument_id=InstrumentId.from_str(_IID),
        action=BookAction.ADD,
        order=order,
        flags=0,
        sequence=sequence,
        ts_event=1,
        ts_init=1,
    )
    return OrderBookDeltas(instrument_id=InstrumentId.from_str(_IID), deltas=[delta])


def _snapshotted(tmp_path: Path) -> CaptureService:
    """Build a collector whose book for `_IID` started from a snapshot's Clear, as on the wire."""
    collector = build_capture(_make_config(tmp_path / "catalog"), ())
    clear = OrderBookDelta.clear(InstrumentId.from_str(_IID), 0, 1, 1)
    collector._apply_deltas(_IID, OrderBookDeltas(InstrumentId.from_str(_IID), [clear]))
    return collector


def test_an_incremental_delta_without_a_snapshot_builds_no_book(tmp_path: Path) -> None:
    """
    After a resync drops the book, in-flight incremental deltas must not rebuild a shallow one:
    it would be sampled, and a book existing again would keep the queued resync from running.
    """
    collector = build_capture(_make_config(tmp_path / "catalog"), ())
    collector._apply_deltas(_IID, _delta(4, price=100.0))
    assert collector._live_book(_IID) is None
    assert collector._book(_IID).before_snapshot == 1


def test_apply_deltas_applies_regardless_of_sequence_jump(tmp_path: Path) -> None:
    """
    `OrderBookDelta.sequence` is dYdX's WS *connection-level* message_id, shared with
    every other channel/market on the same connection (see `_apply_deltas`'s docstring)
    -- a jump between two messages for one market is normal interleaved traffic, not a
    dropped delta, so it must apply immediately with no buffering or resync.
    """
    collector = _snapshotted(tmp_path)

    collector._apply_deltas(_IID, _delta(4, price=100.0))
    collector._apply_deltas(_IID, _delta(55, price=101.0))  # big jump: still just applies

    book = collector._live_book(_IID)
    assert book is not None
    assert book.best_bid_price().as_double() == 101.0


def test_apply_deltas_empty_batch_is_noop(tmp_path: Path) -> None:
    """A content-less update carries no delta to apply -- must not raise on an empty list."""
    collector = build_capture(_make_config(tmp_path / "catalog"), ())

    collector._apply_deltas(_IID, SimpleNamespace(deltas=[]))  # type: ignore[arg-type]

    assert collector._live_book(_IID) is None


def _side_delta(
    side: OrderSide,
    sequence: int,
    price: float,
    size: float = 1.0,
    action: BookAction = BookAction.ADD,
) -> OrderBookDeltas:
    """
    Like `_delta`, but for either side and with a configurable size/action -- needed
    to exercise Story 5.2's per-level message-id tagging, which `_delta`'s hardcoded
    BUY-only/size=1.0 shape can't.
    """
    order = BookOrder(side=side, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
    delta = OrderBookDelta(
        instrument_id=InstrumentId.from_str(_IID),
        action=action,
        order=order,
        flags=0,
        sequence=sequence,
        ts_event=1,
        ts_init=1,
    )
    return OrderBookDeltas(instrument_id=InstrumentId.from_str(_IID), deltas=[delta])


def test_apply_deltas_tags_and_untags_price_levels(tmp_path: Path) -> None:
    """
    Story 5.2: each ADD/UPDATE tags its (side, price) with the delta's message-id
    (`sequence`) in the `LiveBook`'s `tags` (`DydxLevelTagger`); a DELETE removes that tag; a
    Clear wipes every tag for the instrument. This is the data `uncross_step` arbitrates on.
    """
    collector = _snapshotted(tmp_path)

    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=7, price=100.0))
    assert collector._book(_IID).tags[(OrderSide.BUY, 100.0)] == 7

    delete = _side_delta(OrderSide.BUY, sequence=8, price=100.0, size=0.0, action=BookAction.DELETE)
    collector._apply_deltas(_IID, delete)
    assert (OrderSide.BUY, 100.0) not in collector._book(_IID).tags

    collector._apply_deltas(_IID, _side_delta(OrderSide.SELL, sequence=9, price=101.0))
    clear_order = BookOrder(
        side=OrderSide.BUY, price=Price(1.0, 1), size=Quantity(0.0, 1), order_id=0
    )
    clear_delta = OrderBookDelta(
        instrument_id=InstrumentId.from_str(_IID),
        action=BookAction.CLEAR,
        order=clear_order,
        flags=0,
        sequence=10,
        ts_event=1,
        ts_init=1,
    )
    clear_deltas = OrderBookDeltas(instrument_id=InstrumentId.from_str(_IID), deltas=[clear_delta])
    collector._apply_deltas(_IID, clear_deltas)
    assert collector._book(_IID).tags == {}


# ---------------------------------------------------------------------------
# CRITICAL escalation for steady-state crossed book (Story 1.7)
# ---------------------------------------------------------------------------


def _crossed_book(iid: str = _IID) -> OrderBook:
    """Build a book with bid (101.0) >= ask (100.0) -- crossed."""
    book = OrderBook(InstrumentId.from_str(iid), BookType.L2_MBP)
    bid_order = BookOrder(
        side=OrderSide.BUY, price=Price(101.0, 1), size=Quantity(1.0, 1), order_id=0
    )
    ask_order = BookOrder(
        side=OrderSide.SELL, price=Price(100.0, 1), size=Quantity(1.0, 1), order_id=0
    )
    for order in (bid_order, ask_order):
        book.apply_delta(
            OrderBookDelta(
                instrument_id=InstrumentId.from_str(iid),
                action=BookAction.ADD,
                order=order,
                flags=0,
                sequence=1,
                ts_event=1,
                ts_init=1,
            )
        )
    return book


def _uncrossed_book(iid: str = _IID) -> OrderBook:
    """Build a book with bid (100.0) < ask (101.0) -- normal, not crossed."""
    book = OrderBook(InstrumentId.from_str(iid), BookType.L2_MBP)
    bid_order = BookOrder(
        side=OrderSide.BUY, price=Price(100.0, 1), size=Quantity(1.0, 1), order_id=0
    )
    ask_order = BookOrder(
        side=OrderSide.SELL, price=Price(101.0, 1), size=Quantity(1.0, 1), order_id=0
    )
    for order in (bid_order, ask_order):
        book.apply_delta(
            OrderBookDelta(
                instrument_id=InstrumentId.from_str(iid),
                action=BookAction.ADD,
                order=order,
                flags=0,
                sequence=1,
                ts_event=1,
                ts_init=1,
            )
        )
    return book


@pytest.mark.asyncio
async def test_crossed_book_resolution_is_logged_with_before_after_prices(
    tmp_path: Path, caplog
) -> None:
    """
    Story 5.1: a crossed book that clears on its own must log a resolution line proving
    a real price change happened (before/after bid+ask), so a genuine sub-second touch
    that self-heals via normal delta activity is distinguishable at a glance from a stuck
    desync that only recovers via the forced resubscribe (logged separately, as CRITICAL).
    """
    collector = build_capture(
        _make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01), ()
    )
    collector._plan_ids.add(_IID)
    collector._applied.add(_IID)
    collector._client = _FakeClient()  # type: ignore[assignment]
    # Book has already resolved to uncrossed by the time this tick runs.
    live = collector._book(_IID)
    live.book = _uncrossed_book()
    live.last_update_ns = time.time_ns()
    # Simulate having detected the crossing on a previous tick.
    live.crossed_since_ns = time.time_ns() - 500_000_000  # 0.5s ago
    live.crossed_prices = (101.0, 100.0)

    with caplog.at_level(logging.INFO):
        loop_task = asyncio.create_task(collector._second_loop())
        await asyncio.sleep(0.05)
        collector._stop.set()
        await asyncio.wait_for(loop_task, timeout=1.0)

    resolved = [r for r in caplog.records if "resolved after" in r.getMessage()]
    assert len(resolved) == 1
    msg = resolved[0].getMessage()
    assert _IID in msg
    assert "was bid=101.000000/ask=100.000000" in msg
    assert "now bid=100.000000/ask=101.000000" in msg
    assert (live.crossed_since_ns, live.crossed_prices) == (None, None)


@pytest.mark.asyncio
async def test_crossed_book_within_grace_window_does_not_escalate(tmp_path: Path, caplog) -> None:
    """A crossed book just detected (within `crossed_resync_seconds`) is a skip, no escalation."""
    collector = build_capture(
        _make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01), ()
    )
    collector._plan_ids.add(_IID)
    collector._applied.add(_IID)
    collector._client = _FakeClient()  # type: ignore[assignment]
    collector._book(_IID).book = _crossed_book()
    collector._book(_IID).last_update_ns = time.time_ns()
    # No pre-seeded crossed-since -- first detection sets it to "now" this tick.

    with caplog.at_level(logging.CRITICAL, logger="capture.critical"):
        loop_task = asyncio.create_task(collector._second_loop())
        await asyncio.sleep(0.05)
        collector._stop.set()
        await asyncio.wait_for(loop_task, timeout=1.0)

    assert [r for r in caplog.records if r.name == "capture.critical"] == []
    assert collector._client.calls == []  # no resync either


@pytest.mark.asyncio
async def test_crossed_book_past_grace_window_escalates_critical(tmp_path: Path, caplog) -> None:
    """
    A crossed book that has persisted past `crossed_resync_seconds` with no known cause (not
    mid-resync) is steady-state desync -- CRITICAL to the distinct logger, plus the forced
    resync still fires. Not re-logged on every _second_loop tick within one window: the resync
    drops the book and its crossed-since, so re-entering this branch takes another full grace
    window -- naturally spacing repeats to match the resync's own retry cadence rather than the
    (much faster) snapshot_interval_seconds tick rate.
    """
    error_ledger.reset()
    collector = build_capture(
        _make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01), ()
    )
    collector._plan_ids.add(_IID)
    collector._applied.add(_IID)
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]
    collector._book(_IID).book = _crossed_book()
    collector._book(_IID).last_update_ns = time.time_ns()
    collector._book(_IID).crossed_since_ns = (
        time.time_ns() - int(collector._config.crossed_resync_seconds * 1e9) - 1
    )

    with caplog.at_level(logging.CRITICAL, logger="capture.critical"):
        loop_task = asyncio.create_task(collector._second_loop())
        await asyncio.sleep(0.05)
        collector._stop.set()
        await asyncio.wait_for(loop_task, timeout=1.0)

    critical_records = [r for r in caplog.records if r.name == "capture.critical"]
    assert len(critical_records) == 1, (
        "must not re-escalate on every _second_loop tick within one _CROSSED_RESYNC_NS window"
    )
    payload = json.loads(critical_records[0].message)
    assert payload["instrument_id"] == _IID
    assert payload["reason"] == "steady_state_crossed_book"
    # The DATA-03 fallback itself still fires alongside the escalation, and is ledgered.
    assert f"unsubscribe:{_IID}" in fake_client.calls
    assert f"subscribe:{_IID}" in fake_client.calls
    assert error_ledger.counts().get("collector.resync") == 1


# ---------------------------------------------------------------------------
# Active per-level uncrossing (Story 5.2 / DATA-04) -- dYdX's own non-destructive fix,
# tried before the WARNING/timer/CRITICAL/_resync_book path above.
# ---------------------------------------------------------------------------


def _gate(collector: CaptureService) -> list:
    """One pass of the core gate over `_IID` with a live feed (the policies run inside it)."""
    collector._plan_ids.add(_IID)
    collector._applied.add(_IID)
    collector._instruments = {_IID: definition(_IID, 1, 1)}  # `_side_delta`'s precisions
    now = time.time_ns()
    collector._feeds.last_book_message_ns = now
    return asyncio.run(collector._sample_tick(now))


def test_crossed_book_actively_uncrossed_when_both_levels_tagged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Both crossed levels tagged (via the `LevelTagger`) -> the uncross policy drops the
    older-tagged (bid) side itself, the gate accepts the second in the same tick, and the
    WARNING/CRITICAL/resync path is never reached. The bid side keeps a second, untouched level
    (99.0) so dropping the stale 101.0 level does not empty that side -- the ordinary, benign case,
    logged at INFO. See test_crossed_book_uncrossing_the_last_level_logs_a_warning below for the
    case where the dropped level was the side's only one.
    """
    collector = _snapshotted(tmp_path)
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]
    # bid @101 tagged with the OLDER sequence -> bid is stale and must be dropped.
    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=1, price=101.0))
    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=1, price=99.0))
    collector._apply_deltas(_IID, _side_delta(OrderSide.SELL, sequence=2, price=100.0))
    book = collector._live_book(_IID)
    assert book is not None

    with caplog.at_level(logging.INFO):
        rows = _gate(collector)

    assert len(rows) == 1  # not crossed any more: the second was written
    assert book.best_bid_price().as_double() == 99.0  # surviving, untouched bid level
    assert book.best_ask_price().as_double() == 100.0
    assert fake_client.calls == []  # no resync
    assert [r for r in caplog.records if r.name == "capture.critical"] == []
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    uncrossed_logs = [r for r in caplog.records if "actively uncrossed" in r.getMessage()]
    assert len(uncrossed_logs) == 1
    assert _IID in uncrossed_logs[0].getMessage()
    assert "BUY" in uncrossed_logs[0].getMessage()


def test_crossed_book_uncrossing_the_last_level_logs_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    DATA-02: dropping the *only* remaining level on one side (leaving that side fully
    empty) is a real one-sided-book data-loss event, not routine self-healing -- it must
    be distinguishable from the benign case above, not logged identically at INFO. The
    one-sided book is then an empty top of book, so the gate writes no row for it (Story 26.1:
    before it, the uncrossed-but-one-sided book was sampled with an empty side).
    """
    collector = _snapshotted(tmp_path)
    collector._client = _FakeClient()  # type: ignore[assignment]
    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=1, price=101.0))
    collector._apply_deltas(_IID, _side_delta(OrderSide.SELL, sequence=2, price=100.0))
    book = collector._live_book(_IID)
    assert book is not None

    with caplog.at_level(logging.INFO):
        rows = _gate(collector)

    assert rows == []
    assert book.best_bid_price() is None  # bid side is now fully empty
    assert [r for r in caplog.records if "actively uncrossed" in r.getMessage()] == []
    warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "Crossed book" in r.getMessage()
    ]
    assert len(warnings) == 1
    assert "LAST remaining" in warnings[0].getMessage()
    assert "BUY" in warnings[0].getMessage()
    assert any("Empty top of book" in r.getMessage() for r in caplog.records)


def test_crossed_book_tie_break_uses_smaller_size(tmp_path: Path) -> None:
    """
    Equal message-ids on both crossed levels -> the smaller-size side is stale (dYdX's
    own documented tie-break), not an arbitrary/undefined choice.
    """
    collector = _snapshotted(tmp_path)
    collector._client = _FakeClient()  # type: ignore[assignment]
    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=5, price=101.0, size=0.5))
    collector._apply_deltas(_IID, _side_delta(OrderSide.SELL, sequence=5, price=100.0, size=2.0))
    book = collector._live_book(_IID)
    assert book is not None

    policy = DydxUncrossPolicy(resync_after_ns=10**12)
    verdict = policy.step(book, collector._book(_IID).tags, None, time.time_ns())

    assert isinstance(verdict, Uncrossed)  # resolved by the tie-break, no escalation
    assert verdict.dropped[0].side == OrderSide.BUY
    assert book.best_bid_price() is None  # smaller-size bid (0.5) was the stale side
    assert book.best_ask_price().as_double() == 100.0


def test_uncross_step_falls_back_when_a_level_is_untagged() -> None:
    """
    A crossed book with no tags (e.g. right after a resync, before any delta has repopulated
    them) cannot be arbitrated -- `uncross_step` must decline rather than guess, leaving the book
    and tags untouched, so the policy falls back to the escalation ladder.
    """
    book = _crossed_book()
    tags: LevelTags = {}

    dropped = uncross_step(book, tags, time.time_ns())

    assert dropped is None
    assert book.best_bid_price().as_double() == 101.0
    assert book.best_ask_price().as_double() == 100.0
    assert tags == {}
