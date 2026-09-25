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
Story 15.5: `LiveCandleBus` (moved from `data_api/tests/` with the bus in Story 24.2, plus the
`BarObserver` port it declares) -- real `DydxSecondSnapshot` objects, no real Redis
(the class's buffer/listener logic is exercised directly via `handle_batch`, mirroring
how `test_rankings.py` unit-tests `RankingsBus.handle_message` in isolation).
"""

import asyncio
import inspect
import threading
import time
from pathlib import Path

import pytest
from candles.application.forming import forming_bar
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from views.live_candles import MAX_OBSERVED_BAR_SECONDS
from views.live_candles import BarObserver
from views.live_candles import LiveCandleBus


_IID = "BTC-USD-PERP.DYDX"
# The archive a bus's seed reads from, for tests that never seed or replace the read itself.
_NO_CATALOG = "no-catalog-read-in-this-test"
_BAR_SECONDS = 60

# Large, arbitrary, far-from-epoch base timestamp -- a multiple of 60s in ns, so bucket
# arithmetic lines up cleanly with 60s candle boundaries (mirrors test_candles.py's own
# _BASE_NS convention).
_BASE_NS = 1_800_000_000_000_000_000
assert _BASE_NS % (_BAR_SECONDS * 1_000_000_000) == 0


def _snapshot(ts_event: int, price: float, iid: str = _IID) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(iid),
        bid_prices=[price],
        bid_sizes=[1.0],
        ask_prices=[price + 1.0],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        open_price=price,
        high_price=price,
        low_price=price,
        close_price=price,
        ts_event=ts_event,
        ts_init=ts_event,
    )


def test_first_tick_for_subscribed_pair_publishes_single_snapshot_bar() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    snapshot = _snapshot(_BASE_NS, 100.0)

    bus.handle_batch([DydxSecondSnapshot.to_dict(snapshot)])

    message = queue.get_nowait()
    assert message["channel"] == f"candles:{_IID}:{_BAR_SECONDS}"
    assert message["bar"] == forming_bar([snapshot], _BAR_SECONDS)


def test_second_tick_same_bucket_keeps_same_bar_identity_updates_ohlcv() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    first_snapshot = _snapshot(_BASE_NS, 100.0)
    second_snapshot = _snapshot(_BASE_NS + 1_000_000_000, 105.0)  # 1s later, same 60s bucket

    bus.handle_batch([DydxSecondSnapshot.to_dict(first_snapshot)])
    first = queue.get_nowait()
    bus.handle_batch([DydxSecondSnapshot.to_dict(second_snapshot)])
    second = queue.get_nowait()

    assert first["bar"]["t"] == second["bar"]["t"]  # same bar identity, not two bars
    assert second["bar"]["o"] == 100.0  # open of the bucket's first snapshot, unchanged
    assert second["bar"]["h"] == 105.0
    assert second["bar"]["c"] == 105.0


def test_bucket_boundary_crossed_resets_buffer_and_new_bar_has_later_t() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    first_snapshot = _snapshot(_BASE_NS, 100.0)
    next_bucket_snapshot = _snapshot(_BASE_NS + _BAR_SECONDS * 1_000_000_000, 110.0)

    bus.handle_batch([DydxSecondSnapshot.to_dict(first_snapshot)])
    first = queue.get_nowait()
    bus.handle_batch([DydxSecondSnapshot.to_dict(next_bucket_snapshot)])
    second = queue.get_nowait()

    assert second["bar"]["t"] > first["bar"]["t"]
    assert second["bar"]["o"] == 110.0  # buffer reset, not carrying over the old bucket's open
    key = (_IID, _BAR_SECONDS)
    assert len(bus._buffers[key]) == 1
    assert bus._buffers[key][0].ts_event == next_bucket_snapshot.ts_event


def test_no_subscriber_means_no_buffer_or_listener_created() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    snapshot = _snapshot(_BASE_NS, 100.0)

    bus.handle_batch([DydxSecondSnapshot.to_dict(snapshot)])

    assert bus._buffers == {}
    assert bus._listeners == {}


def test_last_unsubscribe_tears_down_buffer_and_listeners() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)

    bus.unsubscribe(_IID, _BAR_SECONDS, queue)

    key = (_IID, _BAR_SECONDS)
    assert key not in bus._buffers
    assert key not in bus._listeners


def test_one_of_two_listeners_unsubscribing_keeps_the_pair_alive() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue_a = bus.subscribe(_IID, _BAR_SECONDS)
    queue_b = bus.subscribe(_IID, _BAR_SECONDS)

    bus.unsubscribe(_IID, _BAR_SECONDS, queue_a)
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS, 100.0))])

    key = (_IID, _BAR_SECONDS)
    assert key in bus._buffers  # still watched by queue_b
    assert queue_b.get_nowait()["bar"]["c"] == 100.0
    assert queue_a.empty()  # unsubscribed listener receives nothing further


def test_unmatched_instrument_in_batch_publishes_nothing() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    other_instrument_snapshot = _snapshot(_BASE_NS, 100.0, iid="ETH-USD-PERP.DYDX")

    bus.handle_batch([DydxSecondSnapshot.to_dict(other_instrument_snapshot)])

    assert queue.empty()


def test_malformed_batch_payload_is_skipped_without_raising() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    bus.subscribe(_IID, _BAR_SECONDS)

    bus.handle_batch({"not": "a list"})  # must log & return, never raise
    bus.handle_batch(["not a dict entry"])  # per-entry malformed, also must not raise


def test_incremental_buffer_converges_to_batch_aggregation_final_bar() -> None:
    """
    The load-bearing correctness AC: an incremental per-tick buffer run through
    `LiveCandleBus` must produce, on its final publish, exactly the bar that
    `candles.application.forming.forming_bar` produces when called once on the whole
    same-bucket snapshot set.
    """
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    snapshots = [_snapshot(_BASE_NS + i * 1_000_000_000, 100.0 + i) for i in range(10)]

    last_message = None
    for snapshot in snapshots:
        bus.handle_batch([DydxSecondSnapshot.to_dict(snapshot)])
        last_message = queue.get_nowait()

    expected = forming_bar(snapshots, _BAR_SECONDS)
    assert expected is not None  # all 10 one-second snapshots land in the same 60s bucket
    assert expected["t"] == _BASE_NS // 1_000_000
    assert last_message is not None
    assert last_message["bar"] == expected


@pytest.mark.asyncio
async def test_seed_fills_bucket_start_so_forming_bar_covers_whole_bucket(
    tmp_path: Path,
) -> None:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    bucket_ns = _BAR_SECONDS * 1_000_000_000
    now_ns = time.time_ns()
    start_ns = now_ns // bucket_ns * bucket_ns
    # Two snapshots already in the catalog for this bucket, before we "subscribe".
    early = [_snapshot(start_ns, 100.0), _snapshot(start_ns + 1, 90.0)]
    ParquetDataCatalog(str(tmp_path)).write_data(early)
    bus = LiveCandleBus(str(tmp_path))
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    await bus.seed(_IID, _BAR_SECONDS)
    bar = queue.get_nowait()["bar"]
    assert (bar["o"], bar["l"], bar["h"]) == (100.0, 90.0, 100.0)
    assert bar["v"] == 3.0  # 2 snapshots x (1.0 + 0.5)

    # A later live tick extends the seeded bucket instead of restarting it.
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(now_ns + 10, 110.0))])
    bar = queue.get_nowait()["bar"]
    assert (bar["o"], bar["l"], bar["h"], bar["c"], bar["v"]) == (100.0, 90.0, 110.0, 110.0, 4.5)


@pytest.mark.asyncio
async def test_seed_wide_bar_reads_raw_seconds_plus_unflushed_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A wide forming bar must start from the archive's raw seconds for this bucket (what the store
    is built from), extended by live seconds the catalog has not flushed yet.
    """
    from kernel.second_snapshot import SecondOHLC

    import views.live_candles as lc

    bar_seconds = 14_400
    bucket_ns = bar_seconds * 1_000_000_000
    now_ns = time.time_ns()
    start_ns = now_ns // bucket_ns * bucket_ns

    def seconds(_path: str, _iid: str, a: int, b: int) -> list[SecondOHLC]:
        assert (a, b) == (start_ns, pytest.approx(now_ns, abs=5_000_000_000))
        return [
            SecondOHLC(start_ns, 100.0, 105.0, 99.0, 104.0, 1.0, 0.5),
            SecondOHLC(start_ns + 1_000_000_000, None, None, None, None, 0.0, 0.0),
            SecondOHLC(start_ns + 2_000_000_000, 104.0, 110.0, 103.0, 108.0, 1.0, 0.5),
        ]

    monkeypatch.setattr(lc, "query_second_ohlc", seconds)
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, bar_seconds)
    # A live second the collector has not flushed yet, newer than every archived second.
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(now_ns - 1_000_000_000, 120.0))])
    queue.get_nowait()
    await bus.seed(_IID, bar_seconds)
    bar = queue.get_nowait()["bar"]
    assert (bar["o"], bar["h"], bar["l"], bar["c"]) == (100.0, 120.0, 99.0, 120.0)
    assert (
        bar["v"] == 1.5 + 1.5 + 1.5
    )  # both traded archived seconds + the live second (1.0 + 0.5 each)


@pytest.mark.asyncio
async def test_seed_includes_unflushed_recent_seconds(tmp_path: Path) -> None:
    bucket_ns = _BAR_SECONDS * 1_000_000_000
    start_ns = time.time_ns() // bucket_ns * bucket_ns
    bus = LiveCandleBus(str(tmp_path))  # empty catalog: nothing flushed yet
    # Seen live before this subscriber arrived (another pair was watching the coin).
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(start_ns, 100.0))])
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    await bus.seed(_IID, _BAR_SECONDS)
    assert queue.get_nowait()["bar"]["o"] == 100.0


@pytest.mark.asyncio
async def test_seed_never_prepends_previous_bucket_after_rollover(
    tmp_path: Path,
) -> None:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    bucket_ns = _BAR_SECONDS * 1_000_000_000
    start_ns = time.time_ns() // bucket_ns * bucket_ns
    ParquetDataCatalog(str(tmp_path)).write_data([_snapshot(start_ns, 100.0)])
    bus = LiveCandleBus(str(tmp_path))
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    # A tick from the NEXT bucket lands before the seed read finishes.
    bus._buffers[(_IID, _BAR_SECONDS)] = [_snapshot(start_ns + bucket_ns, 500.0)]
    await bus.seed(_IID, _BAR_SECONDS)
    bar = queue.get_nowait()["bar"]
    assert (bar["o"], bar["h"], bar["l"]) == (500.0, 500.0, 500.0)


@pytest.mark.asyncio
async def test_seed_failure_allows_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import views.live_candles as lc

    def boom(*_a, **_k):
        raise OSError("catalog unreadable")

    monkeypatch.setattr(lc, "query_second_ohlc", boom)
    bus = LiveCandleBus(_NO_CATALOG)
    bus.subscribe(_IID, _BAR_SECONDS)
    with pytest.raises(OSError):
        await bus.seed(_IID, _BAR_SECONDS)
    assert (_IID, _BAR_SECONDS) not in bus._seeded


def _blocking_read(monkeypatch: pytest.MonkeyPatch) -> threading.Event:
    """Make the seed's catalog read block until the returned event is set."""
    import views.live_candles as lc

    release = threading.Event()

    def read(*_a: object, **_k: object) -> list:
        release.wait(timeout=10)
        return []

    monkeypatch.setattr(lc, "query_second_ohlc", read)
    return release


@pytest.mark.asyncio
async def test_seed_is_held_by_the_bus_and_outlives_a_departing_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One seed serves every listener of a pair, so one connection leaving must not cancel it."""
    release = _blocking_read(monkeypatch)
    bus = LiveCandleBus(_NO_CATALOG)
    key = (_IID, _BAR_SECONDS)
    leaving = bus.subscribe(_IID, _BAR_SECONDS)
    bus.subscribe(_IID, _BAR_SECONDS)
    try:
        bus.start_seed(_IID, _BAR_SECONDS)
        bus.start_seed(_IID, _BAR_SECONDS)  # a second subscriber: no second read
        task = bus._seed_tasks[key]
        await asyncio.sleep(0)
        bus.unsubscribe(_IID, _BAR_SECONDS, leaving)
        assert not task.cancelled()
    finally:
        release.set()
    await task
    assert key in bus._seeded
    assert key not in bus._seed_tasks


@pytest.mark.asyncio
async def test_last_listener_leaving_cancels_the_seed_and_unmarks_the_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = _blocking_read(monkeypatch)
    bus = LiveCandleBus(_NO_CATALOG)
    key = (_IID, _BAR_SECONDS)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    try:
        bus.start_seed(_IID, _BAR_SECONDS)
        task = bus._seed_tasks[key]
        await asyncio.sleep(0)
        bus.unsubscribe(_IID, _BAR_SECONDS, queue)
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
    assert key not in bus._seeded
    assert key not in bus._seed_tasks


@pytest.mark.asyncio
async def test_a_failed_bus_seed_is_ledgered(monkeypatch: pytest.MonkeyPatch) -> None:
    import views.live_candles as lc

    def boom(*_a: object, **_k: object) -> list:
        raise OSError("catalog unreadable")

    monkeypatch.setattr(lc, "query_second_ohlc", boom)
    error_ledger.reset()
    bus = LiveCandleBus(_NO_CATALOG)
    bus.subscribe(_IID, _BAR_SECONDS)
    bus.start_seed(_IID, _BAR_SECONDS)
    task = bus._seed_tasks[(_IID, _BAR_SECONDS)]
    with pytest.raises(OSError):
        await task
    await asyncio.sleep(0)  # the done-callback
    assert error_ledger.counts() == {"live_candles.seed": 1}
    error_ledger.reset()


def test_recent_rows_keep_traded_seconds_and_expire_old_ones() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    base = 1_800_000_000_000_000_000
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(base, 10.0))])
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(base + 700 * 1_000_000_000, 11.0))])
    rows = bus.recent_rows(_IID, 0, base * 2)
    assert [r.close_price for r in rows] == [11.0]  # the 700s-old row aged out (RECENT_SECONDS)


def test_a_venue_timed_row_published_late_lands_in_its_exchange_time_bucket() -> None:
    """
    Story 22.12: a Bybit/Hyperliquid row for the bucket's last second is sampled (ts_init)
    after the boundary; the bar it belongs to is decided by ts_event alone.
    """
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    last_second = _snapshot(_BASE_NS + 59_500_000_000, 100.0)
    late = DydxSecondSnapshot.from_dict(
        {**DydxSecondSnapshot.to_dict(last_second), "ts_init": _BASE_NS + 61_500_000_000}
    )

    bus.handle_batch([DydxSecondSnapshot.to_dict(late)])

    assert queue.get_nowait()["bar"]["t"] == _BASE_NS // 1_000_000


class _RecordingObserver:
    """A test double that is a `BarObserver` by shape alone -- it neither imports nor subclasses it."""

    def __init__(self, watched: frozenset[tuple[str, int]] = frozenset()) -> None:
        self.watched = watched
        self.seen: list[tuple[str, int, dict, int]] = []

    def watched_bars(self) -> frozenset[tuple[str, int]]:
        return self.watched

    def on_bar(self, instrument_id: str, bar_seconds: int, bar: dict, ts_ns: int) -> None:
        self.seen.append((instrument_id, bar_seconds, bar, ts_ns))


def test_bar_observer_is_satisfied_structurally_by_a_plain_class() -> None:
    """
    The port alerting implements in Story 24.3 without importing views' internals: any object with
    these `watched_bars`/`on_bar` signatures is one (mypy checks the annotated assignment below).
    """
    observer: BarObserver = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bar = forming_bar([_snapshot(_BASE_NS, 100.0)], _BAR_SECONDS)
    assert bar is not None

    observer.on_bar(_IID, _BAR_SECONDS, bar, _BASE_NS)

    assert isinstance(observer, _RecordingObserver)
    assert observer.seen == [(_IID, _BAR_SECONDS, bar, _BASE_NS)]
    assert observer.watched_bars() == frozenset({(_IID, _BAR_SECONDS)})
    protocol_params = list(inspect.signature(BarObserver.on_bar).parameters)
    double_params = list(inspect.signature(_RecordingObserver.on_bar).parameters)
    assert (
        double_params == protocol_params == ["self", "instrument_id", "bar_seconds", "bar", "ts_ns"]
    )
    watched_protocol = inspect.signature(BarObserver.watched_bars)
    watched_double = inspect.signature(_RecordingObserver.watched_bars)
    assert list(watched_double.parameters) == list(watched_protocol.parameters) == ["self"]
    assert watched_double.return_annotation == watched_protocol.return_annotation


def _batch(*snapshots: DydxSecondSnapshot) -> list[dict]:
    return [DydxSecondSnapshot.to_dict(s) for s in snapshots]


def test_observer_only_pair_is_folded_and_observed_with_no_listener() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    observer = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(observer)
    first, second = _snapshot(_BASE_NS, 100.0), _snapshot(_BASE_NS + 1_000_000_000, 105.0)

    bus.handle_batch(_batch(first, second))

    assert bus._listeners == {}
    assert observer.seen == [
        (_IID, _BAR_SECONDS, forming_bar([first], _BAR_SECONDS), first.ts_event),
        (_IID, _BAR_SECONDS, forming_bar([first, second], _BAR_SECONDS), second.ts_event),
    ]


def test_charted_and_observed_pair_hands_the_observer_the_published_bar() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    observer = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(observer)
    snapshot = _snapshot(_BASE_NS, 100.0)

    bus.handle_batch(_batch(snapshot))

    published = queue.get_nowait()["bar"]
    [(_iid, _bs, observed, ts_ns)] = observer.seen
    assert observed is published  # the identical object, not an equal re-fold
    assert ts_ns == snapshot.ts_event
    assert queue.empty()


def test_observer_is_not_called_for_a_pair_it_does_not_watch() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    observer = _RecordingObserver(frozenset({(_IID, 300)}))
    bus.attach(observer)

    bus.handle_batch(_batch(_snapshot(_BASE_NS, 100.0)))

    assert queue.get_nowait()["bar"]["c"] == 100.0
    assert [(iid, bs) for iid, bs, _bar, _ts in observer.seen] == [(_IID, 300)]


def test_untraded_second_starting_a_bucket_is_neither_published_nor_observed() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    observer = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(observer)
    untraded = DydxSecondSnapshot.from_dict(
        {**DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS, 100.0)), "close_price": None}
    )

    bus.handle_batch(_batch(untraded))

    assert observer.seen == []


def test_unwatched_pair_buffer_is_pruned_on_the_next_batch() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    observer = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(observer)
    bus.handle_batch(_batch(_snapshot(_BASE_NS, 100.0)))
    assert (_IID, _BAR_SECONDS) in bus._buffers

    observer.watched = frozenset()  # e.g. the alert was deleted, triggered or expired
    bus.handle_batch(_batch(_snapshot(_BASE_NS + 1_000_000_000, 101.0)))

    assert bus._buffers == {}
    assert len(observer.seen) == 1


def test_detached_observer_is_not_called_and_its_buffers_are_dropped() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    observer = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(observer)
    bus.handle_batch(_batch(_snapshot(_BASE_NS, 100.0)))

    bus.detach(observer)
    bus.handle_batch(_batch(_snapshot(_BASE_NS + 1_000_000_000, 101.0)))

    assert bus._buffers == {}
    assert len(observer.seen) == 1


def test_last_unsubscribe_keeps_the_buffer_of_an_observed_pair() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    observer = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(observer)
    first = _snapshot(_BASE_NS, 100.0)
    bus.handle_batch(_batch(first))

    bus.unsubscribe(_IID, _BAR_SECONDS, queue)
    second = _snapshot(_BASE_NS + 1_000_000_000, 90.0)
    bus.handle_batch(_batch(second))

    key = (_IID, _BAR_SECONDS)
    assert key not in bus._listeners
    assert [r.ts_event for r in bus._buffers[key]] == [first.ts_event, second.ts_event]
    assert observer.seen[-1][2] == forming_bar([first, second], _BAR_SECONDS)


class _RaisingObserver:
    def __init__(self, raise_in: str) -> None:
        self.raise_in = raise_in

    def watched_bars(self) -> frozenset[tuple[str, int]]:
        if self.raise_in == "watched_bars":
            raise RuntimeError("watched_bars broke")
        return frozenset({(_IID, _BAR_SECONDS)})

    def on_bar(self, instrument_id: str, bar_seconds: int, bar: dict, ts_ns: int) -> None:
        raise RuntimeError("on_bar broke")


@pytest.mark.parametrize("raise_in", ["watched_bars", "on_bar"])
def test_raising_observer_is_ledgered_and_never_stops_the_others(raise_in: str) -> None:
    error_ledger.reset()
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    bus.attach(_RaisingObserver(raise_in))
    healthy = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(healthy)

    bus.handle_batch(_batch(_snapshot(_BASE_NS, 100.0)))

    assert queue.get_nowait()["bar"]["c"] == 100.0
    assert [bar["c"] for _iid, _bs, bar, _ts in healthy.seen] == [100.0]
    assert error_ledger.counts() == {"live_candles.observer": 1}
    assert f"_RaisingObserver.{raise_in}" in error_ledger.last_details()["live_candles.observer"]
    error_ledger.reset()


def test_observed_pair_with_a_non_positive_width_is_ledgered_and_never_folded() -> None:
    """A bad width (a hand-edited `bar_seconds = 0`) must not raise out of the batch for everyone."""
    error_ledger.reset()
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    observer = _RecordingObserver(frozenset({(_IID, 0), (_IID, _BAR_SECONDS)}))
    bus.attach(observer)

    bus.handle_batch(_batch(_snapshot(_BASE_NS, 100.0)))

    assert queue.get_nowait()["bar"]["c"] == 100.0
    assert [bs for _iid, bs, _bar, _ts in observer.seen] == [_BAR_SECONDS]
    assert (_IID, 0) not in bus._buffers
    assert error_ledger.counts() == {"live_candles.observer": 1}
    assert "invalid pairs" in error_ledger.last_details()["live_candles.observer"]
    error_ledger.reset()


@pytest.mark.parametrize(
    "bad",
    [(_IID, MAX_OBSERVED_BAR_SECONDS + 1), (_IID, True), (_IID, 1.5), (_IID,), _IID, (1, 60)],
)
def test_observed_malformed_or_oversized_pair_is_ledgered_and_never_folded(bad: object) -> None:
    """A width past one day never rolls over (unbounded buffer); a malformed pair must not raise."""
    error_ledger.reset()
    bus = LiveCandleBus(_NO_CATALOG)
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    observer = _RecordingObserver(frozenset({bad, (_IID, _BAR_SECONDS)}))  # type: ignore[arg-type]
    bus.attach(observer)

    bus.handle_batch(_batch(_snapshot(_BASE_NS, 100.0)))

    assert queue.get_nowait()["bar"]["c"] == 100.0
    assert [bs for _iid, bs, _bar, _ts in observer.seen] == [_BAR_SECONDS]
    assert set(bus._buffers) == {(_IID, _BAR_SECONDS)}
    assert error_ledger.counts() == {"live_candles.observer": 1}
    error_ledger.reset()


def test_observed_one_day_width_is_folded() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    observer = _RecordingObserver(frozenset({(_IID, MAX_OBSERVED_BAR_SECONDS)}))
    bus.attach(observer)

    bus.handle_batch(_batch(_snapshot(_BASE_NS, 100.0)))

    assert [bs for _iid, bs, _bar, _ts in observer.seen] == [MAX_OBSERVED_BAR_SECONDS]


def test_seed_publish_is_not_handed_to_observers(monkeypatch: pytest.MonkeyPatch) -> None:
    import views.live_candles as lc

    bus = LiveCandleBus(_NO_CATALOG)
    now_ns = time.time_ns()
    seeded = _snapshot(now_ns // 60_000_000_000 * 60_000_000_000, 100.0)
    monkeypatch.setattr(lc, "query_second_ohlc", lambda *_a: [lc._second_row(seeded)])
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    observer = _RecordingObserver(frozenset({(_IID, _BAR_SECONDS)}))
    bus.attach(observer)

    asyncio.run(bus.seed(_IID, _BAR_SECONDS))

    assert queue.get_nowait()["bar"]["c"] == 100.0
    assert observer.seen == []
