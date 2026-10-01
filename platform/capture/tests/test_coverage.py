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
Story 31.2: every drop is counted, ledgered and explainable -- the coverage record
(`capture.domain.coverage`, `<catalog>/../coverage/<venue>.jsonl`), the arrival-age stale rule,
the time-bounded and archive-seeded dedup, and the sites that used to only log. Real Nautilus
types and a real catalog (TEST-03); the clients are in-test duck types.
"""

import asyncio
import json
import os
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

import capture.application.capture_service as capture_mod
from capture.application.capture_service import CaptureService
from capture.application.capture_service import _skipped_seconds
from capture.application.capture_service import run_forever
from capture.application.ports import PlanChange
from capture.application.trade_backfill import BackfillReport
from capture.domain import coverage
from capture.domain.coverage import SecondCoverage
from capture.domain.coverage import SecondsRun
from capture.domain.coverage import TradesBackfilled
from capture.domain.coverage import TradesDropped
from capture.domain.coverage import TradesUnrecoverable
from capture.domain.feed_group import BackfillRequest
from capture.domain.trade_intake import TradeIntake
from capture.infrastructure import coverage_file
from capture.infrastructure.coverage_file import append_lines
from capture.infrastructure.coverage_file import coverage_path
from capture.infrastructure.coverage_file import repair_torn_tail
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from capture.tests import test_venue_time as venue
from capture.tests.test_collector import _BYBIT
from capture.tests.test_collector import _LINEAR
from capture.tests.test_collector import _S
from capture.tests.test_collector import _SPOT_ID
from capture.tests.test_collector import _clocked_trade
from capture.tests.test_collector import _collector
from capture.tests.test_collector import _deltas
from capture.tests.test_collector import _FakeFetch
from capture.tests.test_collector import _fetched
from capture.tests.test_collector import _LifecycleClient
from capture.tests.test_collector import _rest
from capture.tests.test_collector import _seed_trade
from capture.tests.test_collector import _tick
from capture.tests.test_collector import _two_instrument_collector
from nautilus_trader.model.data import TradeTick


_T = 1_790_000_000 * _S


def _lines(tmp_path: Path, venue_code: str = "BYBIT") -> list[dict]:
    """Return the coverage record of a collector whose catalog is `tmp_path / "catalog"`."""
    path = coverage_path(str(tmp_path / "catalog"), venue_code)
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _live(c: CaptureService, now_ns: int) -> None:
    """Make the book and its feed look fresh at `now_ns` (the book was applied at the wall)."""
    c._book(_BYBIT).last_update_ns = now_ns
    c._feeds.last_book_message_ns = now_ns


def _flush(c: CaptureService) -> None:
    c._close_report_cycle()
    asyncio.run(c._flush_once(final=True))


# -- the pure record ------------------------------------------------------------------------------


def test_a_run_extends_only_while_the_reason_and_the_seconds_continue() -> None:
    cov = SecondCoverage()
    for second in (10, 11, 12):
        cov.note(_BYBIT, second, coverage.STALE)
    cov.note(_BYBIT, 13, coverage.ROW)
    cov.note(_BYBIT, 14, coverage.STALE)
    cov.note(_BYBIT, 16, coverage.STALE)  # a gap: a new run
    cov.note(_SPOT_ID, 10, coverage.NOT_COLLECTED)
    assert sorted(cov.take(), key=lambda r: (r.instrument_id, r.first_s)) == [
        SecondsRun(_BYBIT, coverage.STALE, 10, 12),
        SecondsRun(_BYBIT, coverage.STALE, 14, 14),
        SecondsRun(_BYBIT, coverage.STALE, 16, 16),
        SecondsRun(_SPOT_ID, coverage.NOT_COLLECTED, 10, 10),
    ]
    assert cov.take() == []  # handed out exactly once


def test_a_row_is_never_a_line_and_an_unknown_reason_is_refused() -> None:
    cov = SecondCoverage()
    cov.note(_BYBIT, 1, coverage.ROW)
    assert cov.take() == []
    with pytest.raises(ValueError, match="not a coverage reason"):
        cov.note(_BYBIT, 2, "gone")


def test_the_four_line_kinds_encode_the_documented_fields() -> None:
    assert json.loads(SecondsRun(_BYBIT, "stale", 5, 9).to_json_line()) == {
        "kind": "seconds",
        "instrument_id": _BYBIT,
        "reason": "stale",
        "first_s": 5,
        "last_s": 9,
        "count": 5,
    }
    assert json.loads(TradesDropped(_BYBIT, "stale", 1, 2, 3).to_json_line())["count"] == 3
    backfilled = json.loads(TradesBackfilled(_BYBIT, ("a", "b")).to_json_line())
    assert (backfilled["count"], backfilled["trade_ids"]) == (2, ["a", "b"])
    unrecoverable = json.loads(TradesUnrecoverable(_BYBIT, "depth", 1, 2).to_json_line())
    assert unrecoverable == {
        "kind": "trades_unrecoverable",
        "instrument_id": _BYBIT,
        "reason": "depth",
        "from_ns": 1,
        "to_ns": 2,
    }


# -- stale trades: arrival age, the ledger line and the coverage line ---------------------------


def test_a_backlog_is_archived_but_a_replay_is_stale_ledgered_and_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")
    now = c._watchdog_started_ns
    monkeypatch.setattr(capture_mod, "time", SimpleNamespace(time_ns=lambda: now + 15 * _S))
    c._process_data(_clocked_trade(1, now - _S // 5, now), _LINEAR)  # processed 15 s late: live
    c._process_data(_clocked_trade(2, now - 30 * _S, now), _LINEAR)  # 30 s old on arrival
    c._process_data(_clocked_trade(3, now - 12 * _S, now), _LINEAR)
    assert [t.trade_id.value for t in c._buffer[(TradeTick, _BYBIT)]] == ["1"]
    monkeypatch.undo()
    _flush(c)
    detail = error_ledger.last_details()["collector.stale_trade"]
    assert f"{_BYBIT}: 2 (age oldest 30.000s, youngest 12.000s)" in detail
    (dropped,) = [line for line in _lines(tmp_path) if line["kind"] == "trades_dropped"]
    assert dropped == {
        "kind": "trades_dropped",
        "instrument_id": _BYBIT,
        "reason": "stale",
        "first_ns": now - 30 * _S,
        "last_ns": now - 12 * _S,
        "count": 2,
    }


# -- dedup: evict-then-replay, the archive seed ---------------------------------------------------


def test_an_id_past_the_window_size_but_inside_the_horizon_is_never_archived_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _two_instrument_collector(tmp_path)
    c._intakes[_BYBIT] = TradeIntake(3)  # window 3
    trades = [_seed_trade(c, n) for n in range(1, 6)]  # 5 ids inside the horizon
    monkeypatch.setattr(c, "_trade_history", _FakeFetch({_BYBIT: _fetched([trades[0]])}))
    report = BackfillReport("linear", ["test"])
    asyncio.run(c._backfill_instrument(_BYBIT, trades[0].ts_event, report))
    archived = [t.trade_id.value for t in c._buffer[(TradeTick, _BYBIT)]]
    assert archived == ["1", "2", "3", "4", "5"]
    assert (report.already, report.backfilled) == (1, 0)


def _archive_trade(catalog: Path, trade: TradeTick) -> None:
    writer = _collector(catalog)._archive
    writer.write([trade])


def test_a_restart_seeds_the_window_from_the_archive(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    now = _collector(catalog)._watchdog_started_ns
    archived = _clocked_trade(7, now - 10 * _S, now - 10 * _S)
    _archive_trade(catalog, archived)
    c = _collector(catalog)  # a new process
    asyncio.run(c._prepare_ids([_BYBIT]))
    assert c._intake(_BYBIT).first_feed("7") == "archive"
    c._process_data(_clocked_trade(7, now - 10 * _S, now), _LINEAR)  # the venue replays it
    assert c._buffer[(TradeTick, _BYBIT)] == []
    assert c._intake(_BYBIT).live == []  # never folded either
    assert c._intake(_BYBIT).duplicate_feed == 1
    assert dict(c._feeds.overlap) == {}  # the previous process is not an arbitrated feed


def test_the_seed_reads_only_the_horizon(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    now = _collector(catalog)._watchdog_started_ns
    old = now - capture_mod.DEDUP_HORIZON_NS - 60 * _S
    _archive_trade(catalog, _clocked_trade(1, old, old))
    _archive_trade(catalog, _clocked_trade(2, now - _S, now - _S))
    recent = _collector(catalog)._archive.recent_trades(
        _BYBIT, now - capture_mod.DEDUP_HORIZON_NS, now, error_ledger.record
    )
    assert recent.ids == [("2", now - _S)]
    assert recent.newest_ts_event == now - _S  # the older trade is outside the horizon too


def test_an_unreadable_trade_file_is_ledgered_and_the_others_still_seed(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog = tmp_path / "catalog"
    now = _collector(catalog)._watchdog_started_ns
    _archive_trade(catalog, _clocked_trade(2, now - _S, now - _S))
    (catalog / "data" / "trade_tick" / _BYBIT / "not-a-span.parquet").write_bytes(b"x")
    c = _collector(catalog)
    asyncio.run(c._prepare_ids([_BYBIT]))
    assert c._intake(_BYBIT).first_feed("2") == "archive"
    assert error_ledger.counts() == {"collector.dedup_seed": 1}
    assert "not-a-span.parquet skipped" in error_ledger.last_details()["collector.dedup_seed"]


def test_a_seeded_copy_is_a_duplicate_before_the_stale_rule(tmp_path: Path) -> None:
    """An already-archived replay never widens a `trades_dropped` window."""
    c = _collector(tmp_path)
    now = c._watchdog_started_ns
    c._intake(_BYBIT).seed([("9", now - 30 * _S)])
    c._process_data(_clocked_trade(9, now - 30 * _S, now), _LINEAR)  # 30 s old on arrival
    counts = c._intake(_BYBIT).take_counts()
    assert (counts.stale, counts.duplicate_feed, counts.stale_span) == (0, 1, None)
    assert c._buffer[(TradeTick, _BYBIT)] == []


# -- seconds: every plan id gets a row, a rejection or not_collected ------------------------------


def test_every_plan_id_is_noted_a_row_a_rejection_or_not_collected(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path / "catalog")
    c._applied.discard(_SPOT_ID)  # planned, not subscribed
    now = c._watchdog_started_ns
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    _tick(c, now)
    c._book(_BYBIT).clear()  # the next second has no book
    _tick(c, now + _S)
    runs = c._coverage.take()
    second = now // _S
    assert sorted(runs, key=lambda r: r.instrument_id) == [
        SecondsRun(_BYBIT, coverage.NO_BOOK, second + 1, second + 1),
        SecondsRun(_SPOT_ID, coverage.NOT_COLLECTED, second, second + 1),
    ]


class _FailingSnapshotWrites:
    """Wraps a real archive writer; every snapshot batch write raises, the rest go through."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def write(self, items: list) -> None:
        if isinstance(items[0], DydxSecondSnapshot):
            raise OSError("disk full")
        self._inner.write(items)  # type: ignore[attr-defined]


def test_rows_whose_write_failed_are_write_failed_runs(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")
    c._archive = _FailingSnapshotWrites(c._archive)  # type: ignore[assignment]
    now = c._watchdog_started_ns
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    _tick(c, now)
    _live(c, now + _S)
    _tick(c, now + _S)
    c._book(_BYBIT).clear()
    _tick(c, now + 2 * _S)  # no book: a run open when the flush re-notes the lost rows
    _flush(c)
    second = now // _S
    runs = [(r["reason"], r["first_s"], r["last_s"]) for r in _lines(tmp_path)]
    assert sorted(runs) == [
        ("no_book", second + 2, second + 2),
        ("write_failed", second, second + 1),
    ]
    assert "LOST" in error_ledger.last_details()["collector.flush_write"]


def test_a_verdict_without_its_own_reason_is_refused() -> None:
    with pytest.raises(TypeError, match="no coverage reason"):
        capture_mod._coverage_reason(object())  # type: ignore[arg-type]


def test_a_stale_book_for_five_seconds_is_one_run_and_one_flush_summary(tmp_path: Path) -> None:
    error_ledger.reset()
    c = venue._collector(tmp_path / "catalog")
    c._process_data(venue._book(100.0, 102.0, venue._SEC + 0.5))
    venue._close(c, venue._SEC)
    c._book(venue._IID).last_update_ns = venue._at(venue._SEC + 0.5)  # silent from here on
    for second in range(venue._SEC + 10, venue._SEC + 15):
        assert venue._close(c, second) == []
    _flush(c)
    (run,) = [line for line in _lines(tmp_path, "HYPERLIQUID") if line["kind"] == "seconds"]
    assert (run["reason"], run["first_s"], run["last_s"], run["count"]) == (
        "stale",
        venue._SEC + 10,
        venue._SEC + 14,
        5,
    )
    assert error_ledger.counts()["collector.second_rejected"] == 1
    assert (
        f"'{venue._IID}': {{'stale': 5}}"
        in error_ledger.last_details()["collector.second_rejected"]
    )


def test_skipped_seconds_are_the_ones_the_catch_up_cap_leaves_behind() -> None:
    now = venue._at(venue._SEC + 41.5)  # latest due second: _SEC + 40
    assert _skipped_seconds(now, 0, venue._SEC) == range(venue._SEC + 1, venue._SEC + 11)
    assert _skipped_seconds(now, 0, venue._SEC + 20) == range(venue._SEC + 21, venue._SEC + 21)
    assert not _skipped_seconds(now, 0, None)


def test_a_40s_stall_notes_the_capped_seconds_for_every_plan_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c = venue._collector(tmp_path / "catalog")
    c._plan_ids.add("ETH-USD-PERP.HYPERLIQUID")
    c._last_closed_second = venue._SEC
    wall = venue._at(venue._SEC + 41.5)
    monkeypatch.setattr(
        capture_mod, "time", SimpleNamespace(time_ns=lambda: wall, time=lambda: wall / _S)
    )

    async def one_iteration(_seconds: float) -> None:
        c.stop()

    monkeypatch.setattr(capture_mod.asyncio, "sleep", one_iteration)
    asyncio.run(c._venue_second_loop())
    capped = [r for r in c._coverage.take() if r.reason == coverage.CATCH_UP_CAP]
    assert sorted(capped, key=lambda r: r.instrument_id) == [
        SecondsRun(iid, coverage.CATCH_UP_CAP, venue._SEC + 1, venue._SEC + 10)
        for iid in sorted(c._plan_ids)
    ]
    assert "10 s" in error_ledger.last_details()["collector.skipped_seconds"]
    assert c._last_closed_second == venue._SEC + 40


def test_arrival_seconds_between_two_ticks_are_missed_ticks(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")
    c._note_missed_ticks(101, 103)
    c._note_missed_ticks(104, 103)  # consecutive ticks: nothing
    assert c._coverage.take() == [SecondsRun(_BYBIT, coverage.MISSED_TICK, 101, 103)]
    assert error_ledger.counts() == {"collector.skipped_seconds": 1}


def test_skipped_seconds_with_nothing_planned_are_not_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")
    c._plan_ids.clear()
    c._note_missed_ticks(101, 103)
    assert c._coverage.take() == []
    assert error_ledger.counts() == {}


def test_a_restart_notes_the_seconds_since_the_last_archived_row(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog = tmp_path / "catalog"
    before = _collector(catalog)
    now = before._watchdog_started_ns
    before._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    (row,) = _tick(before, now)
    _flush(before)
    after = _collector(catalog)  # a new process, 10 s later
    asyncio.run(after._prepare_ids([_BYBIT]))  # as `run()`'s initial apply does
    later = now + 10 * _S
    after._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=later))
    _live(after, later)
    _tick(after, later)
    s0 = row.ts_event // _S
    assert after._coverage.take() == [SecondsRun(_BYBIT, coverage.RESTART, s0 + 1, s0 + 9)]
    assert (
        f"{_BYBIT} {s0 + 1}..{s0 + 9} (9 s)" in error_ledger.last_details()["collector.restart_gap"]
    )
    _live(after, later + _S)
    _tick(after, later + _S)
    assert after._coverage.take() == []  # only at the first verdict


class _WireClient:
    """A client whose subscribe and unsubscribe succeed (the wire half of `apply`)."""

    async def subscribe(self, iid: str) -> None:
        return None

    async def unsubscribe(self, iid: str) -> None:
        return None


class _NoReadsInTheGate:
    """Wraps a real archive writer; a disk read after `armed` fails the test."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.armed = False

    def __getattr__(self, name: str) -> object:
        if self.armed and name in ("last_snapshot_second", "recent_trades"):
            raise AssertionError(f"{name} read inside the write gate")
        return getattr(self._inner, name)


def test_the_gate_never_reads_the_archive(tmp_path: Path) -> None:
    c = _collector(tmp_path / "catalog", client=_WireClient())
    reads = _NoReadsInTheGate(c._archive)
    c._archive = reads  # type: ignore[assignment]
    asyncio.run(c.apply(PlanChange(added=frozenset({_BYBIT}))))
    reads.armed = True
    now = c._watchdog_started_ns
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    assert len(_tick(c, now)) == 1
    c._note_skipped(now // _S + 1, now // _S + 2, coverage.MISSED_TICK, "test")


def test_a_re_added_id_gets_a_fresh_restart_gap_and_a_fresh_seed(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog = tmp_path / "catalog"
    c = _collector(catalog, client=_WireClient())
    now = c._watchdog_started_ns
    asyncio.run(c.apply(PlanChange(added=frozenset({_BYBIT}))))
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    (row,) = _tick(c, now)
    _flush(c)
    asyncio.run(c.apply(PlanChange(removed=frozenset({_BYBIT}))))
    _archive_trade(catalog, _clocked_trade(4, now - _S, now - _S))  # archived while it was out
    asyncio.run(c.apply(PlanChange(added=frozenset({_BYBIT}))))
    assert c._intake(_BYBIT).first_feed("4") == "archive"
    later = now + 20 * _S
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=later))
    _live(c, later)
    _tick(c, later)
    s0 = row.ts_event // _S
    assert c._coverage.take() == [SecondsRun(_BYBIT, coverage.RESTART, s0 + 1, s0 + 19)]


def test_a_re_added_id_s_gap_starts_after_its_rows_still_in_the_buffer(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    c = _collector(catalog, client=_WireClient())
    now = c._watchdog_started_ns
    asyncio.run(c.apply(PlanChange(added=frozenset({_BYBIT}))))
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    (row,) = _tick(c, now)
    _flush(c)
    _live(c, now + _S)
    assert len(_tick(c, now + _S)) == 1  # buffered, not flushed: the disk read cannot see it
    asyncio.run(c.apply(PlanChange(removed=frozenset({_BYBIT}))))
    asyncio.run(c.apply(PlanChange(added=frozenset({_BYBIT}))))
    later = now + 20 * _S
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=later))
    _live(c, later)
    _tick(c, later)
    s0 = row.ts_event // _S
    # From after the buffered row (s0 + 1), never over it: a row *and* a run fails the second.
    assert c._coverage.take() == [SecondsRun(_BYBIT, coverage.RESTART, s0 + 2, s0 + 19)]


# -- backfill coverage ----------------------------------------------------------------------------


def test_backfilled_ids_and_unrecoverable_windows_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _two_instrument_collector(tmp_path)
    seed = _seed_trade(c, 1)
    oldest = seed.ts_event + 12 * _S
    fetch = _FakeFetch(
        {
            _BYBIT: _fetched([_rest(5, oldest)], reached=False, oldest=oldest),
            _SPOT_ID: OSError("reset"),
        }
    )
    monkeypatch.setattr(c, "_trade_history", fetch)
    asyncio.run(c._backfill_instrument(_BYBIT, seed.ts_event, BackfillReport("l", ["t"])))
    asyncio.run(c._backfill_instrument(_SPOT_ID, seed.ts_event, BackfillReport("l", ["t"])))
    backfilled, depth, failed = c._coverage_trades
    assert backfilled == TradesBackfilled(_BYBIT, ("5",))
    assert depth == TradesUnrecoverable(_BYBIT, coverage.DEPTH, seed.ts_event, oldest)
    assert isinstance(failed, TradesUnrecoverable)
    assert (failed.instrument_id, failed.reason, failed.from_ns) == (
        _SPOT_ID,
        coverage.FETCH_FAILED,
        seed.ts_event,
    )


def test_a_backfill_interrupted_at_shutdown_records_every_unfetched_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = _two_instrument_collector(tmp_path)
    since = {_BYBIT: _seed_trade(c, 1).ts_event, _SPOT_ID: _seed_trade(c, 2, iid=_SPOT_ID).ts_event}
    c._feeds.instruments["l"].update(since)
    # Shutdown cancels the first fetch (a `BaseException`, past the per-instrument `except`).
    monkeypatch.setattr(c, "_trade_history", _FakeFetch({_BYBIT: asyncio.CancelledError()}))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(c._run_backfill("l", BackfillRequest(0, ["reconnect"], dict(since))))
    windows = [(w.instrument_id, w.reason, w.from_ns) for w in c._coverage_trades]  # type: ignore[union-attr]
    assert windows == [(iid, coverage.FETCH_FAILED, since[iid]) for iid in (_BYBIT, _SPOT_ID)]


def test_a_backfill_abandoned_at_shutdown_records_its_windows(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path)
    since = {_BYBIT: _T, _SPOT_ID: _T + _S}
    c._feeds.requests["l"] = BackfillRequest(_T, ["reconnect"], dict(since))
    c._ledger_abandoned_backfills()
    assert error_ledger.counts() == {"collector.trade_backfill": 1}
    windows = [(w.instrument_id, w.reason, w.from_ns) for w in c._coverage_trades]  # type: ignore[union-attr]
    assert windows == [(iid, coverage.FETCH_FAILED, since[iid]) for iid in (_BYBIT, _SPOT_ID)]


# -- the restart backfill (D-61, Story 31.10) ------------------------------------------------------


def test_a_restart_seeds_the_backfill_baseline_from_the_newest_archived_ts_event(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog"
    now = _collector(catalog)._watchdog_started_ns
    newest = _clocked_trade(2, now - 10 * _S, now - 10 * _S)
    _archive_trade(catalog, _clocked_trade(1, now - 20 * _S, now - 20 * _S))
    _archive_trade(catalog, newest)
    _archive_trade(catalog, _clocked_trade(3, now - 30 * _S, now - 5 * _S))  # a late backfill
    c = _collector(catalog)  # a new process
    asyncio.run(c._prepare_ids([_BYBIT]))
    assert c._intake(_BYBIT).last_trade_ts == newest.ts_event  # by venue time, not by arrival


def _restarted(tmp_path: Path) -> tuple[CaptureService, TradeTick]:
    """Return a new process over a catalog of one archived trade, after its first `apply`."""
    catalog = tmp_path / "catalog"
    now = _collector(catalog)._watchdog_started_ns
    archived = _clocked_trade(7, now - 10 * _S, now - 10 * _S)
    _archive_trade(catalog, archived)
    c = _two_instrument_collector(catalog)
    asyncio.run(c._prepare_ids([_BYBIT, _SPOT_ID]))
    c._arm_restart_backfill([_BYBIT, _SPOT_ID])  # as `run()` does after its first `apply`
    return c, archived


def test_the_first_book_message_schedules_the_restart_backfill_from_the_baseline(
    tmp_path: Path,
) -> None:
    c, archived = _restarted(tmp_path)
    assert c._restart_baselines == {_BYBIT: archived.ts_event}  # the spot id archived nothing
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]), _LINEAR)
    request = c._feeds.requests["linear"]
    assert request.reasons == [capture_mod.RESTART_BACKFILL_REASON]
    assert request.since == {_BYBIT: archived.ts_event}
    assert c._restart_baselines == {}  # once per instrument


def test_the_first_trade_schedules_it_before_the_trade_advances_the_baseline(
    tmp_path: Path,
) -> None:
    c, archived = _restarted(tmp_path)
    live = _seed_trade(c, 8)
    assert c._feeds.requests["linear"].since == {_BYBIT: archived.ts_event}
    assert c._intake(_BYBIT).last_trade_ts == live.ts_event


def test_the_restart_backfill_fetches_since_the_baseline_and_notes_the_depth_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    c, archived = _restarted(tmp_path)
    oldest = archived.ts_event + 4 * _S  # the venue's history starts after the baseline
    fetch = _FakeFetch({_BYBIT: _fetched([_rest(9, oldest)], reached=False)})
    monkeypatch.setattr(c, "_trade_history", fetch)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]), _LINEAR)
    asyncio.run(c._run_due_backfills(time.time_ns() + capture_mod._BACKFILL_SETTLE_NS))
    assert [call[:2] for call in fetch.calls] == [(_BYBIT, archived.ts_event - 5 * _S)]
    assert c._coverage_trades == [
        TradesBackfilled(_BYBIT, ("9",)),
        TradesUnrecoverable(_BYBIT, coverage.DEPTH, archived.ts_event, oldest),
    ]
    assert "restart: archived baseline" in error_ledger.last_details()["collector.trade_backfill"]


class _UnreadableTrades:
    """Wraps a real archive writer; reading its recent trades fails."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def recent_trades(self, *args: object) -> object:
        raise OSError("catalog unreadable")

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def test_an_unreadable_archive_is_ledgered_and_leaves_no_baseline(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _two_instrument_collector(tmp_path / "catalog")
    c._archive = _UnreadableTrades(c._archive)  # type: ignore[assignment]
    asyncio.run(c._prepare_ids([_BYBIT]))
    c._arm_restart_backfill([_BYBIT])
    assert error_ledger.counts() == {"collector.dedup_seed": 1}
    assert "no backfill baseline" in error_ledger.last_details()["collector.dedup_seed"]
    assert c._restart_baselines == {}


def test_an_id_leaving_the_plan_before_its_first_message_drops_its_baseline(
    tmp_path: Path,
) -> None:
    c, _ = _restarted(tmp_path)
    c._forget_verdicts(_BYBIT)
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)]), _LINEAR)
    assert c._feeds.requests == {}


def test_run_arms_the_restart_backfill_after_its_first_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = tmp_path / "catalog"
    now = _collector(catalog)._watchdog_started_ns
    archived = _clocked_trade(7, now - 10 * _S, now - 10 * _S)
    _archive_trade(catalog, archived)
    monkeypatch.setattr(capture_mod, "instruments_from_pyo3", lambda pyo3: [])
    c = _collector(catalog, client=_LifecycleClient([]))
    c._applied.clear()  # `run()` applies the plan itself
    c._stop.set()  # run() reaches the loops, sees the stop and unwinds
    asyncio.run(c.run())
    assert c._restart_baselines == {_BYBIT: archived.ts_event}  # armed; no message came


class _FailingSubscribe(_LifecycleClient):
    async def subscribe(self, iid: str) -> None:
        raise ConnectionError("subscribe refused")


def test_run_arms_an_id_whose_first_subscribe_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retry loop subscribes it later: its first message then backfills the restart gap."""
    catalog = tmp_path / "catalog"
    now = _collector(catalog)._watchdog_started_ns
    archived = _clocked_trade(7, now - 10 * _S, now - 10 * _S)
    _archive_trade(catalog, archived)
    monkeypatch.setattr(capture_mod, "instruments_from_pyo3", lambda pyo3: [])
    c = _collector(catalog, client=_FailingSubscribe([]))
    c._applied.clear()
    c._stop.set()
    asyncio.run(c.run())
    assert c._last_applied is not None
    assert c._last_applied.failed == frozenset({_BYBIT})
    assert c._restart_baselines == {_BYBIT: archived.ts_event}


# -- the coverage write -----------------------------------------------------------------------------


class _FailingAppends:
    """Wraps a real archive writer; its first `failures` coverage appends raise."""

    def __init__(self, inner: object, failures: int) -> None:
        self._inner = inner
        self.failures = failures
        self.written: list[str] = []

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def append_coverage(self, venue_code: str, lines: Sequence[str]) -> int:
        if self.failures:
            self.failures -= 1
            raise OSError("disk full")
        self.written.extend(lines)
        return 0


def test_a_failed_coverage_write_is_ledgered_kept_and_written_next_flush(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")
    archive = _FailingAppends(c._archive, failures=1)
    c._archive = archive  # type: ignore[assignment]
    c._coverage.note(_BYBIT, 1, coverage.STALE)
    asyncio.run(c._flush_once())
    assert error_ledger.counts() == {"collector.coverage_write": 1}
    assert archive.written == []
    c._coverage.note(_BYBIT, 5, coverage.NO_BOOK)
    asyncio.run(c._flush_once())
    assert [json.loads(line)["reason"] for line in archive.written] == ["stale", "no_book"]


def test_a_failed_append_on_the_final_flush_is_ledgered_as_lost(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")
    c._archive = _FailingAppends(c._archive, failures=1)  # type: ignore[assignment]
    c._coverage.note(_BYBIT, 1, coverage.STALE)
    asyncio.run(c._flush_once(final=True))  # shutdown: no next flush to keep them for
    detail = error_ledger.last_details()["collector.coverage_write"]
    assert "at shutdown, 1 lines LOST" in detail
    assert c._coverage_unwritten == []


class _HeldAppends(_FailingAppends):
    """A `_FailingAppends` whose appends block in their thread until `release` is set."""

    def __init__(self, inner: object, failures: int) -> None:
        super().__init__(inner, failures)
        self.release = threading.Event()

    def append_coverage(self, venue_code: str, lines: Sequence[str]) -> int:
        assert self.release.wait(5)
        return super().append_coverage(venue_code, lines)


def test_a_cancelled_flush_s_append_is_settled_by_the_final_flush(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")
    archive = _HeldAppends(c._archive, failures=1)
    c._archive = archive  # type: ignore[assignment]
    c._coverage.note(_BYBIT, 1, coverage.STALE)

    async def shutdown_mid_append() -> None:
        flush = asyncio.ensure_future(c._flush_once())
        await asyncio.sleep(0.05)  # the append is running in its thread
        flush.cancel()
        with pytest.raises(asyncio.CancelledError):
            await flush
        archive.release.set()  # ... and then fails
        c._coverage.note(_BYBIT, 5, coverage.NO_BOOK)
        await c._flush_once(final=True)

    asyncio.run(shutdown_mid_append())
    assert error_ledger.counts() == {"collector.coverage_write": 1}  # never lost unledgered
    assert [json.loads(line)["reason"] for line in archive.written] == ["stale", "no_book"]


def test_unwritten_lines_past_the_bound_are_dropped_oldest_first_and_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.setattr(capture_mod, "_COVERAGE_PENDING_MAX", 2)
    c = _collector(tmp_path / "catalog")
    c._archive = _FailingAppends(c._archive, failures=5)  # type: ignore[assignment]
    for second in (1, 3, 5):
        c._coverage.note(_BYBIT, second, coverage.STALE)
    asyncio.run(c._flush_once())
    assert [json.loads(line)["first_s"] for line in c._coverage_unwritten] == [3, 5]
    assert (
        "1 oldest unwritten lines LOST" in error_ledger.last_details()["collector.coverage_write"]
    )


def test_a_failed_append_leaves_the_file_as_it_was_and_the_retry_writes_each_line_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "coverage" / "bybit.jsonl"
    append_lines(path, ['{"a":1}'])
    real_fsync = os.fsync
    calls = {"n": 0}

    def failing_fsync(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")
        real_fsync(fd)

    monkeypatch.setattr(coverage_file.os, "fsync", failing_fsync)
    with pytest.raises(OSError, match="disk full"):
        append_lines(path, ['{"b":2}', '{"c":3}'])
    assert path.read_text() == '{"a":1}\n'
    append_lines(path, ['{"b":2}', '{"c":3}'])
    assert path.read_text().splitlines() == ['{"a":1}', '{"b":2}', '{"c":3}']


def test_a_torn_tail_is_cut_before_the_first_append_and_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    path = tmp_path / "coverage" / "bybit.jsonl"
    path.parent.mkdir()
    path.write_bytes(b'{"a":1}\n{"kind":"sec')  # a SIGKILL mid-line
    c = _collector(tmp_path / "catalog")
    c._coverage.note(_BYBIT, 1, coverage.STALE)
    asyncio.run(c._flush_once())
    lines = path.read_text().splitlines()
    assert lines[0] == '{"a":1}'
    assert [json.loads(line)["reason"] for line in lines[1:]] == ["stale"]
    detail = error_ledger.last_details()["collector.coverage_write"]
    assert detail.startswith("torn tail repaired: 12 bytes")
    assert repair_torn_tail(path) == 0  # whole lines only now


def test_a_torn_tail_longer_than_one_read_chunk_is_cut_at_the_last_newline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(coverage_file, "_TAIL_CHUNK_BYTES", 4)  # the record is read back, not whole
    path = tmp_path / "bybit.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":2}\n{"kind":"sec')
    assert repair_torn_tail(path) == 12
    assert path.read_bytes() == b'{"a":1}\n{"b":2}\n'
    path.write_bytes(b'{"kind":"sec')  # no whole line at all
    assert repair_torn_tail(path) == 12
    assert path.read_bytes() == b""


def test_a_repair_whose_append_failed_is_reported_by_the_next_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ParquetArchiveWriter(str(tmp_path / "catalog"))
    path = coverage_path(writer.catalog_path, "BYBIT")
    path.parent.mkdir()
    path.write_bytes(b'{"a":1}\n{"kind":"sec')
    real_fsync = os.fsync
    calls = {"n": 0}

    def append_fsync_fails_once(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] == 2:  # the repair's fsync is the first
            raise OSError("disk full")
        real_fsync(fd)

    monkeypatch.setattr(coverage_file.os, "fsync", append_fsync_fails_once)
    with pytest.raises(OSError, match="disk full"):
        writer.append_coverage("BYBIT", ['{"b":2}'])
    assert writer.append_coverage("BYBIT", ['{"b":2}']) == 12
    assert writer.append_coverage("BYBIT", ['{"c":3}']) == 0
    assert path.read_text().splitlines() == ['{"a":1}', '{"b":2}', '{"c":3}']


def test_after_a_failed_append_the_tail_is_checked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = ParquetArchiveWriter(str(tmp_path / "catalog"))
    path = coverage_path(writer.catalog_path, "BYBIT")
    writer.append_coverage("BYBIT", ['{"a":1}'])

    def fails_and_leaves_a_fragment(target: Path, lines: Sequence[str]) -> None:
        with target.open("ab") as f:
            f.write(b'{"b"')  # a partial write whose rollback failed too
        raise OSError("EIO")

    monkeypatch.setattr(
        "capture.infrastructure.parquet_writer.append_lines", fails_and_leaves_a_fragment
    )
    with pytest.raises(OSError, match="EIO"):
        writer.append_coverage("BYBIT", ['{"b":2}'])
    monkeypatch.setattr("capture.infrastructure.parquet_writer.append_lines", append_lines)
    assert writer.append_coverage("BYBIT", ['{"b":2}']) == 4
    assert path.read_text().splitlines() == ['{"a":1}', '{"b":2}']


def test_any_coverage_failure_keeps_the_lines_and_the_sink_still_gets_the_seconds(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    c = _collector(tmp_path / "catalog")

    class _Broken(_FailingAppends):
        def append_coverage(self, venue_code: str, lines: Sequence[str]) -> int:
            raise RuntimeError("not an OSError")

    c._archive = _Broken(c._archive, failures=0)  # type: ignore[assignment]
    now = c._watchdog_started_ns
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    _tick(c, now)
    c._coverage.note(_SPOT_ID, now // _S, coverage.NOT_COLLECTED)
    asyncio.run(c._flush_once())
    assert error_ledger.counts() == {"collector.coverage_write": 1}
    assert len(c._coverage_unwritten) == 1
    assert c._second_sink.seconds(_BYBIT) == [now]  # type: ignore[union-attr]


def test_the_coverage_file_is_next_to_the_catalog(tmp_path: Path) -> None:
    c = _collector(tmp_path / "catalog")
    c._coverage.note(_BYBIT, 1, coverage.STALE)
    asyncio.run(c._flush_once())
    assert (tmp_path / "coverage" / "bybit.jsonl").exists()
    assert [line["reason"] for line in _lines(tmp_path)] == ["stale"]


# -- the log-only sites, now ledgered -------------------------------------------------------------


class _FailingStream:
    async def publish(self, snapshots: list) -> None:
        raise ConnectionError("redis down")

    async def publish_hotpath(self, venue: str, report: dict) -> None:
        raise ConnectionError("redis down")

    async def close(self) -> None:
        return None


def test_a_failed_live_publish_is_ledgered_and_parquet_is_unaffected(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    c._live_stream = _FailingStream()
    now = c._watchdog_started_ns
    c._process_data(_deltas([(100.0, 1.0)], [(100.5, 1.0)], ts=now))
    rows = _tick(c, now)
    asyncio.run(c._publish(rows))
    assert error_ledger.counts() == {"collector.snapshot_publish": 1}
    assert len(c._buffer[(type(rows[0]), _BYBIT)]) == 1


def test_a_crash_is_ledgered_with_its_traceback_and_the_service_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    built: list[CaptureService] = []

    def build() -> CaptureService:
        c = _collector(tmp_path)

        async def run() -> None:
            if not built[1:]:
                raise RuntimeError("loop died")
            capture_mod.signal.raise_signal(capture_mod.signal.SIGTERM)
            await real_sleep(0.05)  # the loop runs the signal handler: shutting down

        c.run = run  # type: ignore[method-assign]
        built.append(c)
        return c

    real_sleep = asyncio.sleep
    monkeypatch.setattr(capture_mod.asyncio, "sleep", lambda _s: real_sleep(0))
    asyncio.run(run_forever(build, init_rust_logging=False))
    assert len(built) == 2
    assert error_ledger.counts()["collector.crash"] == 1
    assert "restarting in 1s" in error_ledger.last_details()["collector.crash"]


def test_a_candle_store_a_day_behind_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    c._second_sink._through[_BYBIT] = 0  # type: ignore[union-attr]  # watermark at the epoch
    c._catch_up_candle_store()
    assert "more than a day behind" in error_ledger.last_details()["collector.candle_store_behind"]
