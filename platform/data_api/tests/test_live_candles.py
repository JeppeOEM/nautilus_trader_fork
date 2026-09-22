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
Story 15.5: `LiveCandleBus` -- real `DydxSecondSnapshot` objects, no real Redis
(the class's buffer/listener logic is exercised directly via `handle_batch`, mirroring
how `test_rankings.py` unit-tests `RankingsBus.handle_message` in isolation).
"""

import time
from pathlib import Path

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from ml_signals.candles import candle_dicts_from_snapshots

from data_api import settings
from data_api.live_candles import LiveCandleBus
from nautilus_trader.model.identifiers import InstrumentId


_IID = "BTC-USD-PERP.DYDX"
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
    bus = LiveCandleBus()
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    snapshot = _snapshot(_BASE_NS, 100.0)

    bus.handle_batch([DydxSecondSnapshot.to_dict(snapshot)])

    message = queue.get_nowait()
    assert message["channel"] == f"candles:{_IID}:{_BAR_SECONDS}"
    expected = candle_dicts_from_snapshots([snapshot], _BAR_SECONDS)[-1]
    assert message["bar"] == expected


def test_second_tick_same_bucket_keeps_same_bar_identity_updates_ohlcv() -> None:
    bus = LiveCandleBus()
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
    bus = LiveCandleBus()
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
    bus = LiveCandleBus()
    snapshot = _snapshot(_BASE_NS, 100.0)

    bus.handle_batch([DydxSecondSnapshot.to_dict(snapshot)])

    assert bus._buffers == {}
    assert bus._listeners == {}


def test_last_unsubscribe_tears_down_buffer_and_listeners() -> None:
    bus = LiveCandleBus()
    queue = bus.subscribe(_IID, _BAR_SECONDS)

    bus.unsubscribe(_IID, _BAR_SECONDS, queue)

    key = (_IID, _BAR_SECONDS)
    assert key not in bus._buffers
    assert key not in bus._listeners


def test_one_of_two_listeners_unsubscribing_keeps_the_pair_alive() -> None:
    bus = LiveCandleBus()
    queue_a = bus.subscribe(_IID, _BAR_SECONDS)
    queue_b = bus.subscribe(_IID, _BAR_SECONDS)

    bus.unsubscribe(_IID, _BAR_SECONDS, queue_a)
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(_BASE_NS, 100.0))])

    key = (_IID, _BAR_SECONDS)
    assert key in bus._buffers  # still watched by queue_b
    assert queue_b.get_nowait()["bar"]["c"] == 100.0
    assert queue_a.empty()  # unsubscribed listener receives nothing further


def test_unmatched_instrument_in_batch_publishes_nothing() -> None:
    bus = LiveCandleBus()
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    other_instrument_snapshot = _snapshot(_BASE_NS, 100.0, iid="ETH-USD-PERP.DYDX")

    bus.handle_batch([DydxSecondSnapshot.to_dict(other_instrument_snapshot)])

    assert queue.empty()


def test_malformed_batch_payload_is_skipped_without_raising() -> None:
    bus = LiveCandleBus()
    bus.subscribe(_IID, _BAR_SECONDS)

    bus.handle_batch({"not": "a list"})  # must log & return, never raise
    bus.handle_batch(["not a dict entry"])  # per-entry malformed, also must not raise


def test_incremental_buffer_converges_to_batch_aggregation_final_bar() -> None:
    """
    The load-bearing correctness AC: an incremental per-tick buffer run through
    `LiveCandleBus` must produce, on its final publish, exactly the bar that
    `candle_dicts_from_snapshots` produces when called once on the whole same-bucket
    snapshot set.
    """
    bus = LiveCandleBus()
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    snapshots = [_snapshot(_BASE_NS + i * 1_000_000_000, 100.0 + i) for i in range(10)]

    last_message = None
    for snapshot in snapshots:
        bus.handle_batch([DydxSecondSnapshot.to_dict(snapshot)])
        last_message = queue.get_nowait()

    expected = candle_dicts_from_snapshots(snapshots, _BAR_SECONDS)
    assert len(expected) == 1  # all 10 one-second snapshots land in the same 60s bucket
    assert last_message is not None
    assert last_message["bar"] == expected[-1]


@pytest.mark.asyncio
async def test_seed_fills_bucket_start_so_forming_bar_covers_whole_bucket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    bucket_ns = _BAR_SECONDS * 1_000_000_000
    now_ns = time.time_ns()
    start_ns = now_ns // bucket_ns * bucket_ns
    # Two snapshots already in the catalog for this bucket, before we "subscribe".
    early = [_snapshot(start_ns, 100.0), _snapshot(start_ns + 1, 90.0)]
    ParquetDataCatalog(str(tmp_path)).write_data(early)
    monkeypatch.setattr(settings, "CATALOG_PATH", str(tmp_path))

    bus = LiveCandleBus()
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

    import data_api.live_candles as lc

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
    bus = LiveCandleBus()
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
async def test_seed_includes_unflushed_recent_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        settings, "CATALOG_PATH", str(tmp_path)
    )  # empty catalog: nothing flushed yet
    bucket_ns = _BAR_SECONDS * 1_000_000_000
    start_ns = time.time_ns() // bucket_ns * bucket_ns
    bus = LiveCandleBus()
    # Seen live before this subscriber arrived (another pair was watching the coin).
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(start_ns, 100.0))])
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    await bus.seed(_IID, _BAR_SECONDS)
    assert queue.get_nowait()["bar"]["o"] == 100.0


@pytest.mark.asyncio
async def test_seed_never_prepends_previous_bucket_after_rollover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    bucket_ns = _BAR_SECONDS * 1_000_000_000
    start_ns = time.time_ns() // bucket_ns * bucket_ns
    ParquetDataCatalog(str(tmp_path)).write_data([_snapshot(start_ns, 100.0)])
    monkeypatch.setattr(settings, "CATALOG_PATH", str(tmp_path))
    bus = LiveCandleBus()
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    # A tick from the NEXT bucket lands before the seed read finishes.
    bus._buffers[(_IID, _BAR_SECONDS)] = [_snapshot(start_ns + bucket_ns, 500.0)]
    await bus.seed(_IID, _BAR_SECONDS)
    bar = queue.get_nowait()["bar"]
    assert (bar["o"], bar["h"], bar["l"]) == (500.0, 500.0, 500.0)


@pytest.mark.asyncio
async def test_seed_failure_allows_a_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    import data_api.live_candles as lc

    def boom(*_a, **_k):
        raise OSError("catalog unreadable")

    monkeypatch.setattr(lc, "query_second_ohlc", boom)
    bus = LiveCandleBus()
    bus.subscribe(_IID, _BAR_SECONDS)
    with pytest.raises(OSError):
        await bus.seed(_IID, _BAR_SECONDS)
    assert (_IID, _BAR_SECONDS) not in bus._seeded


def test_recent_rows_keep_traded_seconds_and_expire_old_ones() -> None:
    bus = LiveCandleBus()
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
    bus = LiveCandleBus()
    queue = bus.subscribe(_IID, _BAR_SECONDS)
    last_second = _snapshot(_BASE_NS + 59_500_000_000, 100.0)
    late = DydxSecondSnapshot.from_dict(
        {**DydxSecondSnapshot.to_dict(last_second), "ts_init": _BASE_NS + 61_500_000_000}
    )

    bus.handle_batch([DydxSecondSnapshot.to_dict(late)])

    assert queue.get_nowait()["bar"]["t"] == _BASE_NS // 1_000_000
