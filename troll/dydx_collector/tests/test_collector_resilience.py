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
startup corrupt-parquet quarantine scan, the crossed-book resync watchdog, and
the WS-sequence-gap resync (see collector.py's `_on_data` / `quarantine_corrupt_parquet` /
`_resync_book` / `_apply_or_flag_gap` / `_resync_sequence_gap`).
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

import dydx_collector.collector as collector_module
from dydx_collector.collector import Collector
from dydx_collector.collector import quarantine_corrupt_parquet
from dydx_collector.config import CollectorConfig
from dydx_collector.second_snapshot import DydxSecondSnapshot
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


def _make_config(catalog_path: Path, snapshot_interval_seconds: float = 1.0) -> CollectorConfig:
    return CollectorConfig(
        network=DydxNetwork.TESTNET,
        catalog_path=str(catalog_path),
        flush_interval_seconds=60,
        snapshot_interval_seconds=snapshot_interval_seconds,
        config_reload_seconds=60,
        open_interest_poll_seconds=60,
        non_config_retain_hours=24.0,
        liquidity_min_oi_usd=100_000.0,
        liquidity_check_seconds=60,
        instruments=(),
        exclude=frozenset(),
    )


def test_on_data_isolates_bad_message(tmp_path: Path, monkeypatch) -> None:
    collector = Collector(_make_config(tmp_path / "catalog"))

    def _boom(data: object) -> None:
        raise ValueError("simulated malformed message")

    monkeypatch.setattr(collector, "_on_data_unsafe", _boom)

    collector._on_data(object())  # must not raise -- one bad message can't kill the WS client


def test_quarantine_corrupt_parquet_moves_only_bad_files(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    good_dir = catalog / "data" / "trade_tick"
    good_dir.mkdir(parents=True)

    good_file = good_dir / "0-100.parquet"
    table = pa.table({"ts_init": [0, 100]})
    pq.write_table(table, good_file)

    corrupt_file = good_dir / "100-200.parquet"
    corrupt_file.write_bytes(b"not a real parquet file")

    quarantine_corrupt_parquet(str(catalog))

    assert good_file.exists()
    assert not corrupt_file.exists()
    quarantined = catalog / "_quarantine" / "data" / "trade_tick" / "100-200.parquet"
    assert quarantined.exists()


def test_quarantine_corrupt_parquet_missing_catalog_is_noop(tmp_path: Path) -> None:
    quarantine_corrupt_parquet(str(tmp_path / "does-not-exist"))  # must not raise


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


@pytest.mark.asyncio
async def test_resync_book_resubscribes_and_drops_local_state(tmp_path: Path) -> None:
    """
    A desynced book (never self-heals from deltas alone) must be rebuilt from
    a fresh venue snapshot -- _resync_book forces that via unsubscribe+subscribe
    and clears the local book so the next OrderBookDeltas rebuilds it clean.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]

    iid = "BTC-USD-PERP.DYDX"
    collector._live_books[iid] = OrderBook(InstrumentId.from_str(iid), BookType.L2_MBP)
    collector._crossed_since_ns[iid] = 123

    await collector._resync_book(iid)

    assert fake_client.calls == [f"unsubscribe:{iid}", f"subscribe:{iid}"]
    assert iid not in collector._live_books
    assert iid not in collector._crossed_since_ns


# ---------------------------------------------------------------------------
# WS sequence-gap resync (_apply_or_flag_gap / _resync_sequence_gap)
# ---------------------------------------------------------------------------

_IID = "BTC-USD-PERP.DYDX"


def _seq_deltas(sequence: int, price: float = 100.0, size: float = 1.0) -> OrderBookDeltas:
    """One ADD delta at the given sequence -- enough to exercise the gap check."""
    order = BookOrder(side=OrderSide.BUY, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
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


class _FakeSnapshotClient:
    """Fake DydxClient exposing only request_orderbook_snapshot, for resync tests."""

    def __init__(self, snapshot: OrderBookDeltas) -> None:
        self._snapshot = snapshot
        self.calls: list[str] = []

    async def request_orderbook_snapshot(self, instrument_id: str) -> OrderBookDeltas:
        self.calls.append(instrument_id)
        return self._snapshot


def test_apply_or_flag_gap_applies_consecutive_sequence(tmp_path: Path) -> None:
    """No prior sequence, then the very next one: both apply cleanly, no resync triggered."""
    collector = Collector(_make_config(tmp_path / "catalog"))

    assert collector._apply_or_flag_gap(_IID, _seq_deltas(100)) is True
    assert collector._apply_or_flag_gap(_IID, _seq_deltas(101)) is True

    assert collector._last_sequence[_IID] == 101
    assert _IID not in collector._resync_buffers


@pytest.mark.asyncio
async def test_apply_or_flag_gap_detects_gap_and_resyncs(tmp_path: Path) -> None:
    """
    A skipped sequence must not be applied to the live book -- it and every
    message that arrives during the REST round trip get buffered, then the
    book is rebuilt from a fresh snapshot with the buffer replayed on top.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    snapshot = _seq_deltas(200, price=99.0)
    fake_client = _FakeSnapshotClient(snapshot)
    collector._client = fake_client  # type: ignore[assignment]

    assert collector._apply_or_flag_gap(_IID, _seq_deltas(100)) is True

    # Sequence jumps 100 -> 103: a gap. Must not apply; must start buffering + resync.
    applied = collector._apply_or_flag_gap(_IID, _seq_deltas(103))
    assert applied is False
    assert _IID in collector._resync_buffers
    assert len(collector._resync_tasks) == 1

    # A further message arriving while the resync is in flight rides along in the buffer.
    collector._on_data_unsafe(_seq_deltas(104))
    assert len(collector._resync_buffers[_IID]) == 2

    (task,) = collector._resync_tasks
    await task

    assert fake_client.calls == [_IID]
    assert _IID not in collector._resync_buffers
    # Buffer replay resets tracking to the last buffered message's sequence, not the snapshot.
    assert collector._last_sequence[_IID] == 104
    # Final book reflects the snapshot's price (99.0) plus the replayed deltas (100.0),
    # not the pre-gap state -- proves the local book was actually rebuilt, not patched.
    book = collector._live_books[_IID]
    assert book.best_bid_price().as_double() == 100.0


@pytest.mark.asyncio
async def test_resync_replay_skips_buffered_batch_with_empty_deltas(tmp_path: Path) -> None:
    """
    A content-less update landing in the resync buffer (bypassing _apply_or_flag_gap's
    own empty-deltas guard, which only runs for non-buffered messages) must not crash
    the replay loop via `batch.deltas[-1]` on an empty list.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    snapshot = _seq_deltas(200, price=99.0)
    collector._client = _FakeSnapshotClient(snapshot)  # type: ignore[assignment]

    # Duck-typed stand-in: OrderBookDeltas itself rejects an empty `deltas` list at
    # construction (Condition.not_empty), but the Rust/PyO3 boundary's OrderbookUpdate
    # path has no equivalent guard (crates/adapters/dydx/src/python/websocket.rs), so
    # this exercises what that path could hand to _on_data_unsafe/_resync_sequence_gap.
    empty_batch = SimpleNamespace(deltas=[])

    collector._resync_buffers[_IID] = [_seq_deltas(101), empty_batch, _seq_deltas(102)]

    await collector._resync_sequence_gap(_IID, expected=101, received=101)

    assert collector._last_sequence[_IID] == 102
    book = collector._live_books[_IID]
    assert book.best_bid_price().as_double() == 100.0


@pytest.mark.asyncio
async def test_unsubscribe_clears_raw_ring(tmp_path: Path) -> None:
    """
    An unsubscribed market receives no further OrderBookDeltas to prune its ring via
    _record_raw -- without an explicit pop here it would freeze holding its last
    ~45s of messages forever (MEM-02: demoted/non-tracked coins must age out).
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    collector._client = _FakeClient()  # type: ignore[assignment]
    collector._record_raw(_IID, _seq_deltas(1))
    assert _IID in collector._raw_ring

    await collector._unsubscribe(_IID)

    assert _IID not in collector._raw_ring


@pytest.mark.asyncio
async def test_second_loop_skips_snapshot_while_resyncing(tmp_path: Path) -> None:
    """
    A market mid-resync must never have a 1s snapshot emitted for it -- its local
    book is known-stale until the buffered replay completes (DATA-01: never
    display stale data as live).
    """
    collector = Collector(_make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01))
    collector._pinned = {_IID}

    book = OrderBook(InstrumentId.from_str(_IID), BookType.L2_MBP)
    book.apply_delta(_seq_deltas(1, price=100.0).deltas[0])
    ask_order = BookOrder(
        side=OrderSide.SELL, price=Price(101.0, 1), size=Quantity(1.0, 1), order_id=0
    )
    book.apply_delta(
        OrderBookDelta(
            instrument_id=InstrumentId.from_str(_IID),
            action=BookAction.ADD,
            order=ask_order,
            flags=0,
            sequence=1,
            ts_event=1,
            ts_init=1,
        )
    )
    collector._live_books[_IID] = book
    collector._last_book_update_ns[_IID] = time.time_ns()
    collector._resync_buffers[_IID] = []  # market is mid-resync

    loop_task = asyncio.create_task(collector._second_loop())
    await asyncio.sleep(0.05)
    collector._stop.set()
    await asyncio.wait_for(loop_task, timeout=1.0)

    written = collector._buffer.get((DydxSecondSnapshot, _IID), [])
    assert written == [], "no snapshot may be emitted for a market mid-resync"


# ---------------------------------------------------------------------------
# Bounded raw-message ring + housekeeping log (_record_raw / _flush_housekeeping_log)
# ---------------------------------------------------------------------------


def test_record_raw_prunes_messages_older_than_ring_window(tmp_path: Path, monkeypatch) -> None:
    """The ring is time-bounded, not count-bounded -- stale entries drop on every append."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    ring_ns = collector_module._RING_BUFFER_NS

    fake_now = [1_000_000_000_000]
    monkeypatch.setattr(collector_module.time, "monotonic_ns", lambda: fake_now[0])

    collector._record_raw(_IID, _seq_deltas(1))
    fake_now[0] += ring_ns + 1  # advance past the window
    collector._record_raw(_IID, _seq_deltas(2))

    ring = collector._raw_ring[_IID]
    assert len(ring) == 1
    assert ring[0][1].deltas[0].sequence == 2


def test_on_data_unsafe_records_raw_even_while_resyncing(tmp_path: Path) -> None:
    """Ring capture must not stop just because a market entered resync mode."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    collector._resync_buffers[_IID] = []  # simulate an in-flight resync

    collector._on_data_unsafe(_seq_deltas(5))

    assert len(collector._raw_ring[_IID]) == 1


def test_flush_housekeeping_log_emits_and_clears_ring(tmp_path: Path, caplog) -> None:
    """
    On a confirmed gap, the ring's raw context must reach the housekeeping log (not the
    routine collector log) and then be cleared so a repeated trigger doesn't re-log the
    same overlapping window.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    collector._record_raw(_IID, _seq_deltas(100))
    collector._record_raw(_IID, _seq_deltas(101))

    with caplog.at_level(logging.WARNING, logger="dydx_collector.housekeeping"):
        collector._flush_housekeeping_log(_IID, expected=102, received=105)

    assert _IID not in collector._raw_ring
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "dydx_collector.housekeeping"

    payload = json.loads(record.message)
    assert payload["instrument_id"] == _IID
    assert payload["expected_sequence"] == 102
    assert payload["received_sequence"] == 105
    assert len(payload["raw_messages"]) == 2
    assert payload["raw_messages"][0]["deltas"][0]["sequence"] == 100


@pytest.mark.asyncio
async def test_resync_sequence_gap_flushes_housekeeping_log(tmp_path: Path, caplog) -> None:
    """End-to-end: a detected gap's full resync flow ends with a housekeeping-log flush."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    snapshot = _seq_deltas(200, price=99.0)
    collector._client = _FakeSnapshotClient(snapshot)  # type: ignore[assignment]

    assert collector._apply_or_flag_gap(_IID, _seq_deltas(100)) is True

    with caplog.at_level(logging.WARNING, logger="dydx_collector.housekeeping"):
        applied = collector._apply_or_flag_gap(_IID, _seq_deltas(103))
        assert applied is False
        (task,) = collector._resync_tasks
        await task

    assert _IID not in collector._raw_ring
    housekeeping_records = [r for r in caplog.records if r.name == "dydx_collector.housekeeping"]
    assert len(housekeeping_records) == 1
    payload = json.loads(housekeeping_records[0].message)
    assert payload["expected_sequence"] == 101
    assert payload["received_sequence"] == 103


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


@pytest.mark.asyncio
async def test_crossed_book_within_grace_window_does_not_escalate(tmp_path: Path, caplog) -> None:
    """A crossed book just detected (within _CROSSED_RESYNC_NS) is a silent skip, unchanged."""
    collector = Collector(_make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01))
    collector._pinned = {_IID}
    collector._client = _FakeClient()  # type: ignore[assignment]
    collector._live_books[_IID] = _crossed_book()
    collector._last_book_update_ns[_IID] = time.time_ns()
    # No pre-seeded _crossed_since_ns -- first detection sets it to "now" this tick.

    with caplog.at_level(logging.CRITICAL, logger="dydx_collector.critical"):
        loop_task = asyncio.create_task(collector._second_loop())
        await asyncio.sleep(0.05)
        collector._stop.set()
        await asyncio.wait_for(loop_task, timeout=1.0)

    assert [r for r in caplog.records if r.name == "dydx_collector.critical"] == []
    assert collector._client.calls == []  # _resync_book must not have fired either


@pytest.mark.asyncio
async def test_crossed_book_past_grace_window_escalates_critical(tmp_path: Path, caplog) -> None:
    """
    A crossed book that has persisted past _CROSSED_RESYNC_NS with no known cause (not
    mid-resync) is steady-state desync -- CRITICAL to the distinct logger, plus the
    existing _resync_book recovery still fires. Not re-logged on every _second_loop tick
    within one ~15s window: _resync_book resets _crossed_since_ns on every call, so
    re-entering this branch takes another full _CROSSED_RESYNC_NS -- naturally spacing
    repeats to match _resync_book's own retry cadence rather than the (much faster)
    snapshot_interval_seconds tick rate.
    """
    collector = Collector(_make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01))
    collector._pinned = {_IID}
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]
    collector._live_books[_IID] = _crossed_book()
    collector._last_book_update_ns[_IID] = time.time_ns()
    collector._crossed_since_ns[_IID] = time.time_ns() - collector_module._CROSSED_RESYNC_NS - 1

    with caplog.at_level(logging.CRITICAL, logger="dydx_collector.critical"):
        loop_task = asyncio.create_task(collector._second_loop())
        await asyncio.sleep(0.05)
        collector._stop.set()
        await asyncio.wait_for(loop_task, timeout=1.0)

    critical_records = [r for r in caplog.records if r.name == "dydx_collector.critical"]
    assert len(critical_records) == 1, (
        "must not re-escalate on every _second_loop tick within one _CROSSED_RESYNC_NS window"
    )
    payload = json.loads(critical_records[0].message)
    assert payload["instrument_id"] == _IID
    assert payload["reason"] == "steady_state_crossed_book"
    # _resync_book's existing recovery behavior must still fire alongside the escalation.
    assert f"unsubscribe:{_IID}" in fake_client.calls
    assert f"subscribe:{_IID}" in fake_client.calls


@pytest.mark.asyncio
async def test_second_loop_never_reads_book_for_market_mid_resync(tmp_path: Path, caplog) -> None:
    """
    A market mid-sequence-gap-resync (Story 1.5) must never reach the crossed-book check
    at all, even if its (unread, known-stale) book would otherwise be crossed -- the
    `_resync_buffers` guard runs first and `continue`s before this story's new logic.
    """
    collector = Collector(_make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01))
    collector._pinned = {_IID}
    collector._client = _FakeClient()  # type: ignore[assignment]
    collector._live_books[_IID] = _crossed_book()
    collector._last_book_update_ns[_IID] = time.time_ns()
    collector._crossed_since_ns[_IID] = time.time_ns() - collector_module._CROSSED_RESYNC_NS - 1
    collector._resync_buffers[_IID] = []  # market is mid-resync

    with caplog.at_level(logging.CRITICAL, logger="dydx_collector.critical"):
        loop_task = asyncio.create_task(collector._second_loop())
        await asyncio.sleep(0.05)
        collector._stop.set()
        await asyncio.wait_for(loop_task, timeout=1.0)

    assert [r for r in caplog.records if r.name == "dydx_collector.critical"] == []
    assert collector._client.calls == []
