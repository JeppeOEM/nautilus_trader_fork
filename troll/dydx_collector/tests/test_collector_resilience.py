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
startup corrupt-parquet quarantine scan, and the crossed-book resync watchdog
(see collector.py's `_on_data` / `quarantine_corrupt_parquet` / `_resync_book`).
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


def test_apply_deltas_applies_regardless_of_sequence_jump(tmp_path: Path) -> None:
    """
    `OrderBookDelta.sequence` is dYdX's WS *connection-level* message_id, shared with
    every other channel/market on the same connection (see `_apply_deltas`'s docstring)
    -- a jump between two messages for one market is normal interleaved traffic, not a
    dropped delta, so it must apply immediately with no buffering or resync.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))

    collector._apply_deltas(_IID, _delta(4, price=100.0))
    collector._apply_deltas(_IID, _delta(55, price=101.0))  # big jump: still just applies

    book = collector._live_books[_IID]
    assert book.best_bid_price().as_double() == 101.0


def test_apply_deltas_empty_batch_is_noop(tmp_path: Path) -> None:
    """A content-less update carries no delta to apply -- must not raise on an empty list."""
    collector = Collector(_make_config(tmp_path / "catalog"))

    collector._apply_deltas(_IID, SimpleNamespace(deltas=[]))

    assert _IID not in collector._live_books


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
