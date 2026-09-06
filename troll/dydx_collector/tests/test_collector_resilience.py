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
(see collector.py's `_on_data` / `_ingest_loop` / `quarantine_corrupt_parquet` /
`_resync_book`).
"""

import asyncio
import dataclasses
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import dydx_collector.collector as collector_module
from dydx_collector.collector import Collector
from dydx_collector.collector import quarantine_corrupt_parquet
from dydx_collector.config import CollectorConfig
from dydx_collector.config import InstrumentEntry
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


def test_on_data_enqueues_without_processing(tmp_path: Path) -> None:
    """_on_data is just the queue hand-off now -- _ingest_loop does the real work."""
    collector = Collector(_make_config(tmp_path / "catalog"))

    sentinel = object()
    collector._on_data(sentinel)

    assert collector._ingest_queue.qsize() == 1
    assert collector._ingest_queue.get_nowait() is sentinel


@pytest.mark.asyncio
async def test_ingest_loop_isolates_bad_message(tmp_path: Path, monkeypatch) -> None:
    """One malformed message must not kill _ingest_loop or block later messages."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    processed: list[object] = []
    good_message = object()

    def _process(data: object) -> None:
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


def _side_delta(
    side: OrderSide,
    sequence: int,
    price: float,
    size: float = 1.0,
    action: BookAction = BookAction.ADD,
) -> OrderBookDeltas:
    """Like `_delta`, but for either side and with a configurable size/action -- needed
    to exercise Story 5.2's per-level message-id tagging, which `_delta`'s hardcoded
    BUY-only/size=1.0 shape can't."""
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
    (`sequence`) in `_level_msg_id`; a DELETE removes that tag; a Clear wipes every tag
    for the instrument. This is the data `_uncross_step` arbitrates on.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))

    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=7, price=100.0))
    assert collector._level_msg_id[_IID][(OrderSide.BUY, 100.0)] == 7

    delete = _side_delta(OrderSide.BUY, sequence=8, price=100.0, size=0.0, action=BookAction.DELETE)
    collector._apply_deltas(_IID, delete)
    assert (OrderSide.BUY, 100.0) not in collector._level_msg_id[_IID]

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
    assert collector._level_msg_id[_IID] == {}


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
    desync that only recovers via _resync_book's forced resubscribe (logged separately,
    as CRITICAL).
    """
    collector = Collector(_make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01))
    collector._config = dataclasses.replace(collector._config, instruments=(InstrumentEntry(id=_IID),))
    collector._client = _FakeClient()  # type: ignore[assignment]
    # Book has already resolved to uncrossed by the time this tick runs.
    collector._live_books[_IID] = _uncrossed_book()
    collector._last_book_update_ns[_IID] = time.time_ns()
    # Simulate having detected the crossing on a previous tick.
    collector._crossed_since_ns[_IID] = time.time_ns() - 500_000_000  # 0.5s ago
    collector._crossed_prices[_IID] = (101.0, 100.0)

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
    assert _IID not in collector._crossed_since_ns
    assert _IID not in collector._crossed_prices


@pytest.mark.asyncio
async def test_crossed_book_within_grace_window_does_not_escalate(tmp_path: Path, caplog) -> None:
    """A crossed book just detected (within _CROSSED_RESYNC_NS) is a silent skip, unchanged."""
    collector = Collector(_make_config(tmp_path / "catalog", snapshot_interval_seconds=0.01))
    collector._config = dataclasses.replace(collector._config, instruments=(InstrumentEntry(id=_IID),))
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
    collector._config = dataclasses.replace(collector._config, instruments=(InstrumentEntry(id=_IID),))
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


# ---------------------------------------------------------------------------
# Active per-level uncrossing (Story 5.2 / DATA-04) -- dYdX's own non-destructive fix,
# tried before the WARNING/timer/CRITICAL/_resync_book path above.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crossed_book_actively_uncrossed_when_both_levels_tagged(
    tmp_path: Path, caplog
) -> None:
    """
    Both crossed levels tagged (via _apply_deltas, so _level_msg_id is populated) ->
    _handle_crossed_book drops the older-tagged (bid) side itself, resolves within the
    same tick, and never reaches the WARNING/CRITICAL/_resync_book path.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    fake_client = _FakeClient()
    collector._client = fake_client  # type: ignore[assignment]
    # bid @101 tagged with the OLDER sequence -> bid is stale and must be dropped.
    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=1, price=101.0))
    collector._apply_deltas(_IID, _side_delta(OrderSide.SELL, sequence=2, price=100.0))
    book = collector._live_books[_IID]

    with caplog.at_level(logging.INFO):
        still_crossed = await collector._handle_crossed_book(_IID, book, time.time_ns())

    assert still_crossed is False
    assert book.best_bid_price() is None
    assert book.best_ask_price().as_double() == 100.0
    assert fake_client.calls == []  # no resync
    assert [r for r in caplog.records if r.name == "dydx_collector.critical"] == []
    uncrossed_logs = [r for r in caplog.records if "actively uncrossed" in r.getMessage()]
    assert len(uncrossed_logs) == 1
    assert _IID in uncrossed_logs[0].getMessage()
    assert "BUY" in uncrossed_logs[0].getMessage()


@pytest.mark.asyncio
async def test_crossed_book_tie_break_uses_smaller_size(tmp_path: Path) -> None:
    """Equal message-ids on both crossed levels -> the smaller-size side is stale (dYdX's
    own documented tie-break), not an arbitrary/undefined choice."""
    collector = Collector(_make_config(tmp_path / "catalog"))
    collector._client = _FakeClient()  # type: ignore[assignment]
    collector._apply_deltas(_IID, _side_delta(OrderSide.BUY, sequence=5, price=101.0, size=0.5))
    collector._apply_deltas(_IID, _side_delta(OrderSide.SELL, sequence=5, price=100.0, size=2.0))
    book = collector._live_books[_IID]

    still_crossed = await collector._handle_crossed_book(_IID, book, time.time_ns())

    assert still_crossed is False
    assert book.best_bid_price() is None  # smaller-size bid (0.5) was the stale side
    assert book.best_ask_price().as_double() == 100.0


def test_uncross_step_falls_back_when_a_level_is_untagged(tmp_path: Path) -> None:
    """
    A crossed book built without going through _apply_deltas (e.g. right after
    _resync_book, before any delta has repopulated _level_msg_id) has no tags to
    arbitrate with -- _uncross_step must decline rather than guess, leaving the book
    and _level_msg_id untouched so the caller falls back to the existing resync path.
    """
    collector = Collector(_make_config(tmp_path / "catalog"))
    book = _crossed_book()
    collector._live_books[_IID] = book

    dropped = collector._uncross_step(_IID, book)

    assert dropped is False
    assert book.best_bid_price().as_double() == 101.0
    assert book.best_ask_price().as_double() == 100.0


# ---------------------------------------------------------------------------
# Incident reporting (Story 5.1)
# ---------------------------------------------------------------------------


def test_classify_incident_crossed_book_warning_text() -> None:
    incident_type, iid = collector_module._classify_incident(
        "Crossed book for NEAR-USD-PERP.DYDX (bid=2.266000 >= ask=2.266000) — skipping "
        "snapshot [last bid delta 0.1s ago, last ask delta 0.1s ago]"
    )
    assert incident_type == "crossed_book"
    assert iid == "NEAR-USD-PERP.DYDX"


def test_classify_incident_critical_json_payload_is_exact_not_heuristic() -> None:
    message = json.dumps({"instrument_id": _IID, "reason": "steady_state_crossed_book"})
    incident_type, iid = collector_module._classify_incident(message)
    assert incident_type == "steady_state_crossed_book"
    assert iid == _IID


def test_classify_incident_unclassified_fallback() -> None:
    incident_type, iid = collector_module._classify_incident("Something unexpected happened")
    assert incident_type == "unclassified"
    assert iid is None


def test_scan_ws_raw_window_filters_by_ticker_and_time(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(collector_module, "_WS_RAW_LOG_DIR", tmp_path)
    log_file = tmp_path / "ws_raw_debug_test.log"
    in_window_ns = 1_800_000_000_000_000_000
    log_file.write_text(
        collector_module._ns_to_iso(in_window_ns - 1_000_000_000)
        + ' [DEBUG] x: [WS_RAW] {"id":"BTC-USD","type":"channel_data"}\n'
        + collector_module._ns_to_iso(in_window_ns)
        + ' [DEBUG] x: [WS_RAW] {"id":"ETH-USD","type":"channel_data"}\n'  # wrong ticker
        + collector_module._ns_to_iso(in_window_ns + 60_000_000_000)
        + ' [DEBUG] x: [WS_RAW] {"id":"BTC-USD","type":"channel_data"}\n'  # outside window
    )

    matches = collector_module._scan_ws_raw_window(
        "BTC-USD", in_window_ns - 5_000_000_000, in_window_ns + 5_000_000_000
    )

    assert len(matches) == 1
    assert '"id":"BTC-USD"' in matches[0]


def test_write_incident_report_contains_header_and_evidence(tmp_path: Path, monkeypatch) -> None:
    ws_raw_dir = tmp_path / "ws_raw"
    ws_raw_dir.mkdir()
    incident_dir = tmp_path / "incidents"
    monkeypatch.setattr(collector_module, "_WS_RAW_LOG_DIR", ws_raw_dir)
    monkeypatch.setattr(collector_module, "_INCIDENT_DIR", incident_dir)

    trigger_ns = 1_800_000_000_000_000_000
    # _IID is "BTC-USD-PERP.DYDX" -- ticker (what [WS_RAW]'s "id" field carries) is "BTC-USD".
    (ws_raw_dir / "ws_raw_debug_test.log").write_text(
        collector_module._ns_to_iso(trigger_ns)
        + ' [DEBUG] x: [WS_RAW] {"id":"BTC-USD","type":"channel_data"}\n'
    )

    report_path = collector_module._write_incident_report(
        "crossed_book",
        _IID,
        "WARNING",
        "dydx_collector.collector",
        "Crossed book for X",
        trigger_ns,
    )

    content = Path(report_path).read_text()
    assert "=== dYdX Collector Incident Report ===" in content
    assert "Type: crossed_book" in content
    assert f"Instrument: {_IID}" in content
    assert "Raw WS evidence (BTC-USD, 1 messages)" in content
    assert '"id":"BTC-USD"' in content


def test_write_incident_report_without_instrument_has_no_evidence_section(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(collector_module, "_WS_RAW_LOG_DIR", tmp_path / "ws_raw")
    monkeypatch.setattr(collector_module, "_INCIDENT_DIR", tmp_path / "incidents")

    report_path = collector_module._write_incident_report(
        "second_loop_lag", None, "WARNING", "dydx_collector.collector", "tick arrived late", 1
    )

    content = Path(report_path).read_text()
    assert "Instrument: -" in content
    assert "No instrument identified" in content


@pytest.mark.asyncio
async def test_incident_handler_debounces_repeated_same_type_and_instrument(
    tmp_path: Path, monkeypatch
) -> None:
    """
    A crossed book logs a fresh WARNING every _second_loop tick while it persists --
    the handler must produce at most one report per debounce window, not one per tick.
    """
    monkeypatch.setattr(collector_module, "_INCIDENT_DEBOUNCE_NS", 10_000_000_000)
    written: list[tuple[str, str | None]] = []

    async def _fake_report_incident(incident_type, iid, level, logger_name, message, trigger_ns):
        written.append((incident_type, iid))

    monkeypatch.setattr(collector_module, "_report_incident", _fake_report_incident)

    handler = collector_module._IncidentHandler()
    test_logger = logging.getLogger("test_incident_handler")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    try:
        for _ in range(3):
            test_logger.warning(f"Crossed book for {_IID} (bid=1 >= ask=1)")
            await asyncio.sleep(0)  # let the scheduled task run
    finally:
        test_logger.removeHandler(handler)

    assert written == [("crossed_book", _IID)]


def test_prune_incident_reports_survives_concurrent_callers(tmp_path: Path, monkeypatch) -> None:
    """
    Two incidents landing on different to_thread worker threads both call
    _prune_incident_reports() -- before the lock, one thread's unlink() racing
    another's stat() raised FileNotFoundError (seen in production). 20 files over
    a tiny cap forces real pruning work on every thread, not a no-op.
    """
    monkeypatch.setattr(collector_module, "_INCIDENT_DIR", tmp_path)
    monkeypatch.setattr(collector_module, "_INCIDENT_DIR_MAX_BYTES", 100)
    for i in range(20):
        (tmp_path / f"report_{i}.log").write_text("x" * 50)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(collector_module._prune_incident_reports) for _ in range(8)]
        for f in futures:
            f.result()  # re-raises FileNotFoundError here if the race still exists

    remaining = list(tmp_path.glob("*.log"))
    assert sum(f.stat().st_size for f in remaining) <= 100


@pytest.mark.asyncio
async def test_incident_handler_emit_from_worker_thread_schedules_report(
    monkeypatch,
) -> None:
    """
    _notify()'s failure path logs from inside asyncio.to_thread (a plain
    ThreadPoolExecutor thread, no running loop of its own) -- emit() must still be
    able to schedule _report_incident onto the collector's event loop from there.
    """
    written: list[str] = []

    async def _fake_report_incident(incident_type, iid, level, logger_name, message, trigger_ns):
        written.append(incident_type)

    monkeypatch.setattr(collector_module, "_report_incident", _fake_report_incident)

    handler = collector_module._IncidentHandler(asyncio.get_running_loop())
    test_logger = logging.getLogger("test_incident_handler_thread")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.WARNING)
    try:
        await asyncio.to_thread(test_logger.warning, f"Stale book for {_IID}")
        for _ in range(5):  # give run_coroutine_threadsafe's hop back to the loop time to land
            await asyncio.sleep(0)
    finally:
        test_logger.removeHandler(handler)

    assert written == ["stale_book"]
