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
Story 33.3: `LiveCandleBus` folds `liquidations:raw` into the forming bar -- real `Liquidation`
rows through their own `to_dict`/`from_dict`, no Redis (the frames are handed to
`handle_liquidations` and `_dispatch` directly).

The forming bar follows the one feed-start rule (review loop 2): its `liq_*` are known only when
its bucket starts at or after the earliest liquidation known (archived or seen live). Most tests
therefore first feed the bus one liquidation of the previous minute (`_PRIOR`), which dates the
feed before the bucket under test without being folded into it.
"""

import json
import threading
import time
from pathlib import Path

import pytest
from candles.application.forming import forming_bar
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import live_candles as lc
from views.live_candles import LIQUIDATIONS_CHANNEL
from views.live_candles import RECENT_SECONDS
from views.live_candles import SNAPSHOTS_CHANNEL
from views.live_candles import LiveCandleBus


_LINEAR = "BTCUSDT-LINEAR.BYBIT"
_SPOT = "BTCUSDT-SPOT.BYBIT"
_BAR = 60
_S = 1_000_000_000
_BASE_NS = 1_800_000_000_000_000_000  # a minute boundary
_NO_CATALOG = "no-catalog-read-in-this-test"


def _snapshot(ts: int, iid: str = _LINEAR) -> DydxSecondSnapshot:
    return make_snapshot(
        iid,
        bid_prices=[100.0],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[1.0],
        buy_volume=0.002,
        buy_count=1,
        open_price=100.0,
        high_price=100.0,
        low_price=100.0,
        close_price=100.0,
        ts_event=ts,
        price_precision=1,
        size_precision=3,
    )


def _liq(key: str, ts: int, size_units: int, iid: str = _LINEAR) -> Liquidation:
    return Liquidation(
        InstrumentId.from_str(iid), LiquidatedSide.LONG, size_units, 1000, 1, 3, key, ts, ts
    )


def _frame(*rows: Liquidation) -> list[dict]:
    return json.loads(json.dumps([Liquidation.to_dict(r) for r in rows]))


# One liquidation of the previous minute: the feed's start, before the bucket under test.
_PRIOR_NS = _BASE_NS - 30 * _S


def _fed(bus: LiveCandleBus) -> LiveCandleBus:
    bus.handle_liquidations(_frame(_liq("prior", _PRIOR_NS, 1)))
    return bus


def _subscribed(iid: str = _LINEAR, fed: bool = True) -> tuple[LiveCandleBus, object]:
    bus = LiveCandleBus(_NO_CATALOG)
    if fed:
        _fed(bus)
    queue = bus.subscribe(iid, _BAR)
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS, iid))])
    queue.get_nowait()
    return bus, queue


def test_a_liquidation_in_the_forming_bucket_republishes_the_bar_with_it() -> None:
    bus, queue = _subscribed()
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS + 5 * _S, 4)))
    bar = queue.get_nowait()["bar"]  # type: ignore[attr-defined]
    assert (bar["liq_long_v"], bar["liq_short_v"], bar["liq_n"]) == (4, 0, 1)
    assert bar == forming_bar(
        [lc._second_row(_snapshot(_BASE_NS))],
        _BAR,
        [_liq("a", _BASE_NS + 5 * _S, 4)],
        liquidations_since_ns=_PRIOR_NS,
    )


def test_a_replayed_liquidation_frame_counts_once() -> None:
    bus, queue = _subscribed()
    frame = _frame(_liq("a", _BASE_NS + 5 * _S, 4))
    bus.handle_liquidations(frame)
    queue.get_nowait()  # type: ignore[attr-defined]
    bus.handle_liquidations(frame)
    assert queue.empty()  # type: ignore[attr-defined]
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 6 * _S))])
    assert queue.get_nowait()["bar"]["liq_n"] == 1  # type: ignore[attr-defined]


def test_a_quiet_feed_bar_reads_zero_and_a_no_feed_bar_null() -> None:
    bus = _fed(LiveCandleBus(_NO_CATALOG))
    linear, spot = bus.subscribe(_LINEAR, _BAR), bus.subscribe(_SPOT, _BAR)
    bus.handle_batch(
        [DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS, iid)) for iid in (_LINEAR, _SPOT)]
    )
    assert linear.get_nowait()["bar"]["liq_n"] == 0
    assert spot.get_nowait()["bar"]["liq_n"] is None


def test_the_bucket_roll_resets_the_liquidations() -> None:
    bus, queue = _subscribed()
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS + 5 * _S, 4)))
    queue.get_nowait()  # type: ignore[attr-defined]
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 60 * _S))])
    assert queue.get_nowait()["bar"]["liq_n"] == 0  # type: ignore[attr-defined]


def test_a_liquidation_of_another_bucket_is_kept_in_the_tail_but_not_folded() -> None:
    bus, queue = _subscribed(fed=False)
    older = _liq("old", _BASE_NS - 10 * _S, 4)
    bus.handle_liquidations(_frame(older))
    assert queue.empty()  # type: ignore[attr-defined]
    tail = bus.recent_liquidations(_LINEAR, 0, 1 << 62)
    assert [r.venue_event_id for r in tail] == ["old"]


def test_the_recent_tail_is_bounded_by_recent_seconds() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS, 1)))
    bus.handle_liquidations(_frame(_liq("b", _BASE_NS + (RECENT_SECONDS + 1) * _S, 1)))
    assert [r.venue_event_id for r in bus.recent_liquidations(_LINEAR, 0, 1 << 62)] == ["b"]


@pytest.mark.parametrize(
    "payload",
    [{"not": "a list"}, [42], [{"instrument_id": _LINEAR, "side": "sideways"}]],
)
def test_a_malformed_liquidation_frame_is_ledgered_never_folded(payload: object) -> None:
    error_ledger.reset()
    bus, queue = _subscribed()
    bus.handle_liquidations(payload)
    assert queue.empty()  # type: ignore[attr-defined]
    assert error_ledger.counts() == {"live_candles.liquidation": 1}
    error_ledger.reset()


def test_a_liquidation_for_an_instrument_without_the_feed_is_ledgered() -> None:
    error_ledger.reset()
    bus = LiveCandleBus(_NO_CATALOG)
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS, 4, _SPOT)))
    assert bus.recent_liquidations(_SPOT, 0, 1 << 62) == []
    assert error_ledger.counts() == {"live_candles.liquidation": 1}
    error_ledger.reset()


def test_dispatch_routes_each_channel_and_ledgers_bad_json_per_channel() -> None:
    error_ledger.reset()
    bus, queue = _subscribed()
    handlers = {SNAPSHOTS_CHANNEL: bus.handle_batch, LIQUIDATIONS_CHANNEL: bus.handle_liquidations}
    frame = json.dumps(_frame(_liq("a", _BASE_NS + 5 * _S, 4)))
    bus._dispatch(handlers, {"channel": LIQUIDATIONS_CHANNEL, "data": frame})
    assert queue.get_nowait()["bar"]["liq_n"] == 1  # type: ignore[attr-defined]
    bus._dispatch(handlers, {"channel": LIQUIDATIONS_CHANNEL, "data": "{"})
    assert error_ledger.counts() == {"live_candles.liquidation": 1}
    error_ledger.reset()


def test_an_unknown_channel_is_ledgered_never_read_as_a_snapshot_batch() -> None:
    error_ledger.reset()
    bus, queue = _subscribed()
    handlers = {SNAPSHOTS_CHANNEL: bus.handle_batch, LIQUIDATIONS_CHANNEL: bus.handle_liquidations}
    batch = json.dumps([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 1 * _S))])
    bus._dispatch(handlers, {"channel": "snapshots:other", "data": batch})
    assert queue.empty()  # type: ignore[attr-defined]
    assert error_ledger.counts() == {"live_candles.payload": 1}
    error_ledger.reset()


class _Observer:
    """A `BarObserver` watching the linear minute pair; records `(ts_ns, liq_n)` per call."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int | None]] = []

    def watched_bars(self) -> frozenset[tuple[str, int]]:
        return frozenset({(_LINEAR, _BAR)})

    def on_bar(self, instrument_id: str, bar_seconds: int, bar: dict, ts_ns: int) -> None:
        self.calls.append((ts_ns, bar["liq_n"]))


def test_a_liquidation_republishes_to_listeners_but_not_to_observers() -> None:
    """
    Ticks at +0 s and +10 s; a liquidation stamped +5 s arrives between them. The chart's listener
    gets its republish; the observer keeps one call per tick with a rising `ts_ns`, and sees the
    liquidation in the +10 s tick's bar.
    """
    bus, queue = _subscribed()
    observer = _Observer()
    bus.attach(observer)
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 1 * _S))])
    queue.get_nowait()  # type: ignore[attr-defined]
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS + 5 * _S, 4)))
    assert queue.get_nowait()["bar"]["liq_n"] == 1  # type: ignore[attr-defined]
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 10 * _S))])
    assert observer.calls == [(_BASE_NS + 1 * _S, 0), (_BASE_NS + 10 * _S, 1)]


@pytest.mark.asyncio
async def test_the_seed_folds_the_buckets_archived_liquidations(tmp_path: Path) -> None:
    bucket_ns = _BAR * _S
    start_ns = time.time_ns() // bucket_ns * bucket_ns
    ParquetDataCatalog(str(tmp_path)).write_data([_liq("archived", start_ns, 7)])
    bus = LiveCandleBus(str(tmp_path))
    queue = bus.subscribe(_LINEAR, _BAR)
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(start_ns + _S))])
    queue.get_nowait()
    await bus.seed(_LINEAR, _BAR)
    bar = queue.get_nowait()["bar"]
    assert (bar["liq_long_v"], bar["liq_n"]) == (7, 1)


def test_with_no_feed_start_known_the_bar_is_null_and_a_straddled_bucket_stays_null() -> None:
    """
    Nothing archived, nothing seen: the bar's `liq_*` are null, never 0. The first liquidation
    (+5 s) dates the feed inside the forming minute, which straddles it: still null, its row not
    counted. The next minute starts after the start: a known 0.
    """
    bus, queue = _subscribed(fed=False)
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS + 5 * _S, 4)))
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 6 * _S))])
    assert [m["bar"]["liq_n"] for m in _drain(queue)] == [None, None]
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 60 * _S))])
    assert queue.get_nowait()["bar"]["liq_n"] == 0  # type: ignore[attr-defined]


def _drain(queue: object) -> list[dict]:
    messages = []
    while not queue.empty():  # type: ignore[attr-defined]
        messages.append(queue.get_nowait())  # type: ignore[attr-defined]
    return messages


def test_a_frame_of_many_liquidations_republishes_each_pair_once() -> None:
    """A cascade of three rows in one frame: one fold and one message per pair, holding all 3."""
    bus, queue = _subscribed()
    rows = [_liq(key, _BASE_NS + s * _S, 2) for key, s in (("a", 5), ("b", 6), ("c", 7))]
    bus.handle_liquidations(_frame(*rows))
    (message,) = _drain(queue)
    assert (message["bar"]["liq_long_v"], message["bar"]["liq_n"]) == (6, 3)


def test_a_fresh_buffer_takes_its_buckets_liquidations_from_the_recent_tail() -> None:
    """A liquidation seen before anyone watched the pair: the buffer's first tick folds it."""
    bus = _fed(LiveCandleBus(_NO_CATALOG))
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS + 5 * _S, 4)))
    queue = bus.subscribe(_LINEAR, _BAR)
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + 6 * _S))])
    assert queue.get_nowait()["bar"]["liq_n"] == 1  # type: ignore[attr-defined]


def test_the_recent_tail_holds_a_replayed_event_once() -> None:
    bus = LiveCandleBus(_NO_CATALOG)
    row = _liq("a", _BASE_NS, 1)
    bus.handle_liquidations(_frame(row, row))
    bus.handle_liquidations(_frame(row))
    assert [r.venue_event_id for r in bus.recent_liquidations(_LINEAR, 0, 1 << 62)] == ["a"]


def _failing_for(width: int, real: object) -> object:
    def fold(rows: list, bar_seconds: int, *args: object, **kwargs: object) -> dict | None:
        if bar_seconds == width:
            raise RuntimeError("a malformed row")
        return real(rows, bar_seconds, *args, **kwargs)  # type: ignore[operator]

    return fold


def test_a_failing_pair_is_ledgered_and_isolated_on_both_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The 5m pair's fold raises: on the snapshot path and on the liquidation path it is ledgered at
    `live_candles.publish` per publish, and the 1m pair of the same instrument still publishes --
    nothing escapes to `run()`, whose reconnect would drop every subscription.
    """
    bus = _fed(LiveCandleBus(_NO_CATALOG))
    minute, five = bus.subscribe(_LINEAR, _BAR), bus.subscribe(_LINEAR, 300)
    monkeypatch.setattr(lc, "forming_bar", _failing_for(300, lc.forming_bar))
    error_ledger.reset()
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS))])
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS + 5 * _S, 4)))
    assert [m["bar"]["liq_n"] for m in _drain(minute)] == [0, 1]
    assert five.empty()  # type: ignore[attr-defined]
    assert error_ledger.counts() == {"live_candles.publish": 2}
    error_ledger.reset()


@pytest.mark.asyncio
async def test_the_archive_dates_the_feed_before_the_bucket(tmp_path: Path) -> None:
    """
    A liquidation archived a minute earlier is the start. The archive is never read on the event
    loop: the first publish schedules a background read and reads null (unknown, never a false
    0); once it lands, the next tick reads a known 0.
    """
    ParquetDataCatalog(str(tmp_path)).write_data([_liq("archived", _PRIOR_NS, 7)])
    bus = LiveCandleBus(str(tmp_path))
    queue = bus.subscribe(_LINEAR, _BAR)
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS))])
    assert queue.get_nowait()["bar"]["liq_n"] is None  # type: ignore[attr-defined]
    await bus._feed_refresh_tasks[_LINEAR]
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + _S))])
    assert queue.get_nowait()["bar"]["liq_n"] == 0  # type: ignore[attr-defined]


def _no_archive_read_on_the_loop(monkeypatch: pytest.MonkeyPatch, thread: list[str]) -> None:
    """Record the thread every archive feed-start read runs on."""
    real = lc.liquidation_feed_since_ns

    def read(catalog_path: str, iid: str, *, on_foreign: object) -> int | None:
        assert on_foreign is error_ledger.record  # a stray file name is ledgered, then skipped
        thread.append(threading.current_thread().name)
        return real(catalog_path, iid)

    monkeypatch.setattr(lc, "liquidation_feed_since_ns", read)


@pytest.mark.asyncio
async def test_the_feed_start_is_read_off_the_loop_at_most_once_per_refresh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ten ticks of an observer-only (unseeded) pair: one read, in a worker thread."""
    threads: list[str] = []
    _no_archive_read_on_the_loop(monkeypatch, threads)
    bus = LiveCandleBus(str(tmp_path))
    bus.subscribe(_LINEAR, _BAR)
    for s in range(5):
        bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + s * _S))])
    await bus._feed_refresh_tasks[_LINEAR]
    for s in range(5, 10):
        bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + s * _S))])
    assert len(threads) == 1
    assert threads[0] != threading.main_thread().name


@pytest.mark.asyncio
async def test_a_stale_feed_start_is_refreshed_and_a_failed_read_keeps_the_old_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A read older than `FEED_SINCE_REFRESH_SECONDS` is refreshed at the next publish; a failing
    refresh is ledgered once and the previous start stands (the bar stays a known 0), and no
    second read runs until the next refresh is due.
    """
    bus = LiveCandleBus(_NO_CATALOG)
    bus._set_archive_feed_since(_LINEAR, _PRIOR_NS)
    bus._archive_feed_read_at[_LINEAR] -= lc.FEED_SINCE_REFRESH_SECONDS + 1
    calls: list[str] = []

    def failing(catalog_path: str, iid: str, *, on_foreign: object) -> int | None:
        calls.append(iid)
        raise OSError("catalog unreadable")

    monkeypatch.setattr(lc, "liquidation_feed_since_ns", failing)
    queue = bus.subscribe(_LINEAR, _BAR)
    error_ledger.reset()
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS))])
    await bus._feed_refresh_tasks[_LINEAR]
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS + _S))])
    assert [m["bar"]["liq_n"] for m in _drain(queue)] == [0, 0]
    assert calls == [_LINEAR]
    assert error_ledger.counts() == {"live_candles.feed_since": 1}
    error_ledger.reset()


@pytest.mark.asyncio
async def test_a_first_tick_into_the_seeded_bucket_keeps_the_seeds_liquidations(
    tmp_path: Path,
) -> None:
    """
    The seed finds no seconds but one archived liquidation at the 1D bucket's start -- not in the
    live tail (never seen live; usually hours older than `RECENT_SECONDS`). The pair's first tick
    lands in that same bucket, so the liquidation stays: liq_long_v 7, liq_n 1.
    """
    day_ns = 86_400 * _S
    start_ns = time.time_ns() // day_ns * day_ns
    ParquetDataCatalog(str(tmp_path)).write_data([_liq("archived", start_ns, 7)])
    bus = LiveCandleBus(str(tmp_path))
    queue = bus.subscribe(_LINEAR, 86_400)
    await bus.seed(_LINEAR, 86_400)
    assert queue.empty()  # type: ignore[attr-defined]  # no second yet: nothing to publish
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(time.time_ns()))])
    bar = queue.get_nowait()["bar"]  # type: ignore[attr-defined]
    assert (bar["liq_long_v"], bar["liq_n"]) == (7, 1)


@pytest.mark.asyncio
async def test_a_failed_seed_publish_is_ledgered_and_the_pair_reseeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The seed's publish raises: it is ledgered at `live_candles.publish` (the tick path's isolation),
    nothing escapes the seed, and the pair is not left marked seeded, so the next seed runs.
    """
    bucket_ns = _BAR * _S
    start_ns = time.time_ns() // bucket_ns * bucket_ns
    ParquetDataCatalog(str(tmp_path)).write_data([_liq("archived", start_ns, 7)])
    bus = LiveCandleBus(str(tmp_path))
    queue = bus.subscribe(_LINEAR, _BAR)
    bus._buffers[(_LINEAR, _BAR)] = [lc._second_row(_snapshot(time.time_ns()))]
    real = lc.forming_bar
    monkeypatch.setattr(lc, "forming_bar", _failing_for(_BAR, real))
    error_ledger.reset()
    await bus.seed(_LINEAR, _BAR)
    assert (_LINEAR, _BAR) not in bus._seeded
    assert error_ledger.counts() == {"live_candles.publish": 1}
    error_ledger.reset()
    monkeypatch.setattr(lc, "forming_bar", real)
    await bus.seed(_LINEAR, _BAR)
    assert (_LINEAR, _BAR) in bus._seeded
    assert queue.get_nowait()["bar"]["liq_n"] == 1  # type: ignore[attr-defined]


def test_the_recent_tail_read_survives_the_loop_appending_while_it_filters() -> None:
    """
    The sync routes read the tail from the threadpool while the event loop appends to it: a bound
    whose comparison appends a row mid-filter stands in for that thread switch. The read filters
    its own snapshot (the two rows present when it started), never raising "deque mutated during
    iteration".
    """
    bus = LiveCandleBus(_NO_CATALOG)
    bus.handle_liquidations(_frame(_liq("a", _BASE_NS, 1), _liq("b", _BASE_NS + _S, 1)))
    added = iter(range(10))

    class _AppendingBound(int):
        def __le__(self, other: object) -> bool:
            bus.handle_liquidations(_frame(_liq(f"new{next(added)}", _BASE_NS + 2 * _S, 1)))
            return int(self) <= other  # type: ignore[operator]

    rows = bus.recent_liquidations(_LINEAR, _AppendingBound(0), 1 << 62)
    assert [r.venue_event_id for r in rows] == ["a", "b"]
    # The race really ran: the bound appended while the read filtered (else this test is vacuous).
    after = bus.recent_liquidations(_LINEAR, 0, 1 << 62)
    assert any(r.venue_event_id.startswith("new") for r in after)
