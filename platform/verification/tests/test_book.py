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
`python3 -m verification.book` end to end (Story 31.5): book frames and REST polls in the
recorder's own raw format, a real catalog written by `ParquetDataCatalog.write_data` (real
`DydxSecondSnapshot`s whose books are the test's own replay of the frames) and a coverage record.
The clean day passes exactly; every planted defect makes a failing count non-zero and the exit
status 1; every explained case passes. Then the pure pieces: the replay's `u` rules, the REST
classes, the look-back, the decode.
"""

import json
import random
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification import book as book_tool
from verification.application import sites
from verification.application.book import replay_start
from verification.application.conservation import day_hours
from verification.application.conservation import day_start_ns
from verification.domain.conservation import MalformedLine
from verification.domain.reference_book import AGREE_BRACKET
from verification.domain.reference_book import AGREE_KEY
from verification.domain.reference_book import BETWEEN_PUSHES
from verification.domain.reference_book import BIDS
from verification.domain.reference_book import DISAGREE_KEY
from verification.domain.reference_book import PERSISTENT_DISAGREEMENT
from verification.domain.reference_book import UNALIGNED
from verification.domain.reference_book import UNVALIDATED
from verification.domain.reference_book import VALIDATED
from verification.domain.reference_book import BookMessage
from verification.domain.reference_book import BookReplay
from verification.domain.reference_book import BookTop
from verification.domain.reference_book import BookUnits
from verification.domain.reference_book import ClosedSecond
from verification.domain.reference_book import ReferenceBook
from verification.domain.reference_book import RestBook
from verification.domain.reference_book import RestJudge
from verification.domain.reference_book import StoredBook
from verification.domain.reference_book import bybit_frame
from verification.domain.reference_book import classify_second
from verification.domain.reference_book import level_diff
from verification.domain.reference_book import reference_state
from verification.infrastructure.snapshot_book import decode_prices
from verification.infrastructure.snapshot_book import stored_book
from verification.tests.test_trades import _write_raw


_DAY = date(2026, 9, 29)
_D0 = day_start_ns(_DAY)
_NS = 1_000_000_000
_MS = 1_000_000
_BTC = "BTCUSDT-LINEAR.BYBIT"
_SOL = "SOL-USD-PERP.HYPERLIQUID"
_S = _D0 // _NS + 36_000  # 10:00:00
_NOW = _D0 + 2 * 86_400 * _NS  # the day is closed
_BOOK_CHANNEL = "linear.orderbook.50"
_REST_CHANNEL = "linear.rest.orderbook"

Levels = tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _Msg:
    """One book frame: venue time in ms from `_S`, its `u`, levels as wire strings."""

    at_ms: int
    u: int
    bids: Levels = ()
    asks: Levels = ()
    snapshot: bool = False
    recv_lag_ms: int = 100

    @property
    def venue_ns(self) -> int:
        return (_S * 1000 + self.at_ms) * _MS

    @property
    def seq(self) -> int:
        return 10_000 + self.at_ms  # unique and rising with venue time


_SNAPSHOT = _Msg(
    -1900,
    100,
    (("84000.10", "1.000"), ("83999.90", "2.000"), ("83999.50", "0.500")),
    (("84000.30", "1.500"), ("84000.50", "0.500"), ("84001.00", "3.000")),
    snapshot=True,
)
_BASE = (
    _SNAPSHOT,
    _Msg(-1500, 101, (("84000.10", "1.200"),)),
    _Msg(-600, 102, (), (("84000.30", "0"),)),  # the best ask deleted
    _Msg(100, 103, (("84000.20", "0.100"),)),
    _Msg(400, 104, (), (("84000.40", "0.700"),)),
    _Msg(1300, 105, (("83999.90", "2.500"),)),
    _Msg(1800, 106, (), (("84000.50", "0.600"),)),
    _Msg(2200, 107, (("84000.20", "0"),)),
    _Msg(3300, 108, (), (("84001.00", "2.000"),)),
    _Msg(4100, 109, (("84000.00", "0.300"),)),
    _Msg(5200, 110, (), (("84000.40", "0.800"),)),
    _Msg(6200, 111, (("83999.50", "0"),)),  # closes S+5
)
_ROW_SECONDS = range(_S - 2, _S + 6)
_POLL_AFTER_U = 104

Book = tuple[Levels, Levels]


def _replayed(messages: Iterable[_Msg], until_ns: int) -> Book:
    """Replay the test's own book: every message with venue time < `until_ns`, top 20 per side."""
    bids: dict[Decimal, str] = {}
    asks: dict[Decimal, str] = {}
    for msg in messages:
        if msg.venue_ns >= until_ns:
            break
        if msg.snapshot:
            bids, asks = {}, {}
        for side, levels in ((bids, msg.bids), (asks, msg.asks)):
            for price, size in levels:
                side.pop(Decimal(price), None) if Decimal(size) == 0 else side.update(
                    {Decimal(price): size}
                )
    top_bids = sorted(bids.items(), reverse=True)[:20]
    top_asks = sorted(asks.items())[:20]
    return tuple((str(p), s) for p, s in top_bids), tuple((str(p), s) for p, s in top_asks)


def _ref(second: int, messages: Iterable[_Msg] = _BASE) -> Book:
    return _replayed(messages, (second + 1) * _NS)


def _frame(msg: _Msg, symbol: str = "BTCUSDT") -> dict[str, object]:
    data = {"s": symbol, "b": [list(x) for x in msg.bids], "a": [list(x) for x in msg.asks]}
    raw = {
        "topic": f"orderbook.50.{symbol}",
        "type": "snapshot" if msg.snapshot else "delta",
        "ts": msg.venue_ns // _MS,
        "data": data | {"u": msg.u, "seq": msg.seq},
        "cts": msg.venue_ns // _MS - 2,
    }
    recv = msg.venue_ns + msg.recv_lag_ms * _MS
    return {"kind": "frame", "recv_ns": recv, "endpoint": "linear", "raw": json.dumps(raw)}


def _connection(ts_ns: int, event: str = "open", reason: str = "reconnect") -> dict[str, object]:
    line = {"kind": "connection", "event": event, "ts_ns": ts_ns, "endpoint": "linear"}
    return line | {"reason": reason}


def _fillers() -> list[dict[str, object]]:
    """Return a frame of another symbol in every hour (no raw hour missing) after `startup`."""
    other = [_frame(_Msg(0, 1, (("2700.00", "1.00"),)), "ETHUSDT") for _ in range(24)]
    at = [_D0 + hour * 3600 * _NS + 5 * _NS for hour in range(24)]
    frames = [f | {"recv_ns": ns} for f, ns in zip(other, at, strict=True)]
    return [_connection(_D0 + _NS, reason="startup"), *frames]


def _poll(book: Book, seq: int, recv_ns: int, status: int = 200) -> dict[str, object]:
    result: dict[str, object] = {
        "s": "BTCUSDT",
        "b": [list(x) for x in book[0]],
        "a": [list(x) for x in book[1]],
    }
    result |= {"ts": recv_ns // _MS, "u": 29_000_000, "seq": seq, "cts": recv_ns // _MS}
    body = {"retCode": 0, "retMsg": "OK", "result": result, "time": recv_ns // _MS}
    request = "/v5/market/orderbook?category=linear&symbol=BTCUSDT&limit=50"
    line = {"kind": "rest", "sent_ns": recv_ns - _MS, "endpoint": "linear", "request": request}
    return line | {"recv_ns": recv_ns, "status": status, "raw": json.dumps(body)}


def _through(messages: Iterable[_Msg], u: int) -> Book:
    """Return the book right after the (first) message with `u`, in order."""
    upto: list[_Msg] = []
    for message in messages:
        upto.append(message)
        if message.u == u:
            break
    return _replayed(upto, 10**20)


def _default_polls(messages: tuple[_Msg, ...]) -> tuple[dict[str, object], ...]:
    at = next(m for m in messages if m.u == _POLL_AFTER_U)
    return (_poll(_through(messages, _POLL_AFTER_U), at.seq, at.venue_ns + 150 * _MS),)


@dataclass(frozen=True)
class _Scenario:
    """One Bybit day: the frames, the rows (second -> book, None: no row), polls and coverage."""

    messages: tuple[_Msg, ...] = _BASE
    rows: Mapping[int, Book | None] = field(default_factory=dict)
    polls: tuple[dict[str, object], ...] | None = None
    coverage: tuple[Mapping[str, object], ...] = ()
    connections: tuple[dict[str, object], ...] = ()


def _snapshot_row(
    iid: str, second: int, book: Book, pp: int = 2, sp: int = 3, later: int = 0
) -> Any:
    """Build a stored row for `second`, sampled 1 s after its `ts_event` (`later` ns more)."""
    ts_event = second * _NS + _NS // 2
    return make_snapshot(
        instrument_id=iid,
        bid_prices=[p for p, _ in book[0]],
        bid_sizes=[s for _, s in book[0]],
        ask_prices=[p for p, _ in book[1]],
        ask_sizes=[s for _, s in book[1]],
        ts_event=ts_event,
        ts_init=ts_event + _NS + later,
        price_precision=pp,
        size_precision=sp,
    )


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, venue: str, iid: str) -> None:
    plan = tmp_path / f"{venue.lower()}.toml"
    plan.write_text(f'instruments = ["{iid}"]\n')
    monkeypatch.setenv("VERIFY_DATA_DIR", str(tmp_path / "verify"))
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "catalog"))
    monkeypatch.setenv(f"{venue}_COLLECTOR_CONFIG", str(plan))
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)


def _write_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: _Scenario) -> Path:
    """Write the scenario and point the environment at it; return the catalog root."""
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    frames = [_frame(m) for m in scenario.messages]
    _write_raw(raw, "BYBIT", _BOOK_CHANNEL, [*_fillers(), *frames, *scenario.connections])
    polls = _default_polls(scenario.messages) if scenario.polls is None else scenario.polls
    hourly = [_connection(_D0 + hour * 3600 * _NS + _NS) for hour in range(24)]
    _write_raw(raw, "BYBIT", _REST_CHANNEL, [*hourly, *polls])
    catalog.mkdir(parents=True, exist_ok=True)
    books = {second: _ref(second) for second in _ROW_SECONDS} | dict(scenario.rows)
    rows = [_snapshot_row(_BTC, s, b) for s, b in sorted(books.items()) if b is not None]
    ParquetDataCatalog(str(catalog)).write_data(rows)
    _write_jsonl(tmp_path / "coverage" / "bybit.jsonl", scenario.coverage)
    _env(monkeypatch, tmp_path, "BYBIT", _BTC)
    return catalog


def _write_jsonl(path: Path, lines: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{json.dumps(line)}\n" for line in lines))


def _run(capsys: pytest.CaptureFixture[str], venue: str = "BYBIT") -> tuple[int, dict[str, Any]]:
    status = book_tool.main(["--venue", venue, "--day", _DAY.isoformat(), "--json"], lambda: _NOW)
    return status, json.loads(capsys.readouterr().out)


def _only(report: dict[str, Any]) -> dict[str, Any]:
    (instrument,) = report["instruments"]
    return instrument


def _nonzero(counts: Mapping[str, int]) -> dict[str, int]:
    return {name: value for name, value in counts.items() if value}


def _day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: _Scenario,
) -> tuple[int, dict[str, Any]]:
    """Run one scenario: the exit status and its one instrument's report."""
    _write_day(tmp_path, monkeypatch, scenario)
    status, report = _run(capsys)
    return status, _only(report)


def test_a_clean_day_passes_with_every_second_exact_and_rest_agreeing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario())
    assert (status, instrument["passed"], instrument["reference"]) == (0, True, VALIDATED)
    assert _nonzero(instrument["seconds"]) == {"exact": 8}
    assert _nonzero(instrument["rest"]) == {AGREE_KEY: 1}
    assert _nonzero(instrument["replay"]) == {"messages": 12, "baselines": 1}
    assert (instrument["rows"], instrument["verified_seconds"]) == (8, 8)


def _with_size(book: Book, side: int, index: int, size: str) -> Book:
    levels = list(book[side])
    levels[index] = (levels[index][0], size)
    return (tuple(levels), book[1]) if side == 0 else (book[0], tuple(levels))


def _without(book: Book, side: int, index: int) -> Book:
    levels = book[side][:index] + book[side][index + 1 :]
    return (levels, book[1]) if side == 0 else (book[0], levels)


@pytest.mark.parametrize(
    ("row", "details"),
    [
        (_with_size(_ref(_S), 0, 0, "1.201"), ["bids size@0"]),  # one size +1 unit
        (_without(_ref(_S), 0, 1), ["bids missing@1"]),  # a level deleted
        (_ref(_S - 1), ["bids missing@0", "asks missing@0"]),  # holds the previous second's book
    ],
    ids=["size_plus_one_unit", "level_deleted", "row_shifted_one_second"],
)
def test_a_planted_book_defect_is_content_differs_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    row: Book,
    details: list[str],
) -> None:
    """The row of S is wrong; S-1 -> S takes two messages (103, 104), so no boundary explains it."""
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario(rows={_S: row}))
    assert status == 1
    assert _nonzero(instrument["seconds"]) == {"exact": 7, "content_differs": 1}
    assert instrument["examples"] == [[_S, "content_differs", details]]


def _late(recv_lag_ms: int) -> tuple[_Msg, ...]:
    """Message 108 (S+3.3 s, the last of S+3) received `recv_lag_ms` after its venue time."""
    return tuple(replace(m, recv_lag_ms=recv_lag_ms) if m.u == 108 else m for m in _BASE)


def test_a_row_missing_only_a_message_received_near_its_sampling_is_boundary_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Received at S+4.0 s, after the row's `ts_init` (S+4.5 s) - 1 s: capture could miss it."""
    messages = _late(700)
    without = _replayed([m for m in messages if m.u != 108], (_S + 4) * _NS)
    scenario = _Scenario(messages=messages, rows={_S + 3: without})
    status, instrument = _day(tmp_path, monkeypatch, capsys, scenario)
    assert (status, _nonzero(instrument["seconds"])) == (0, {"exact": 7, "boundary_late": 1})


def test_a_receipt_more_than_the_margin_before_sampling_is_unexplained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Received at S+3.8 s, 0.7 s before `ts_init` (S+4.5 s): capture must have had it (0.5 s)."""
    messages = _late(500)
    without = _replayed([m for m in messages if m.u != 108], (_S + 4) * _NS)
    scenario = _Scenario(messages=messages, rows={_S + 3: without})
    status, instrument = _day(tmp_path, monkeypatch, capsys, scenario)
    assert (status, _nonzero(instrument["seconds"])) == (1, {"exact": 7, "boundary_unexplained": 1})


def test_a_row_missing_a_message_received_long_before_its_sampling_is_unexplained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """S+2's last message (S+2.2 s) was received at S+2.3 s, before `ts_init` - 1 s = S+2.5 s."""
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario(rows={_S + 2: _ref(_S + 1)}))
    assert status == 1
    assert _nonzero(instrument["seconds"]) == {"exact": 7, "boundary_unexplained": 1}


def test_a_row_holding_the_next_seconds_first_message_is_boundary_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    early = _replayed(_BASE, (_S + 2) * _NS + 300 * _MS)  # S+1 plus message 107 (S+2.2 s)
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario(rows={_S + 1: early}))
    assert status == 1
    assert _nonzero(instrument["seconds"]) == {"exact": 7, "boundary_early": 1}


def test_a_u_gap_breaks_the_reference_until_the_next_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """107 arrives as u 108 (a lost message): S+2 and S+3 cannot be judged; S+4.5 re-baselines."""
    through_109 = _through(_BASE, 109)
    resnapshot = _Msg(4500, 300, through_109[0], through_109[1], snapshot=True)
    messages = (
        *[m for m in _BASE if m.u <= 106],
        replace(_BASE[7], u=108),
        replace(_BASE[8], u=109),  # after the break: waits for a snapshot
        replace(_BASE[9], u=110),
        resnapshot,
        replace(_BASE[10], u=301),
        replace(_BASE[11], u=302),
    )
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario(messages=messages))
    assert status == 0
    assert _nonzero(instrument["seconds"]) == {"exact": 6, "reference_unavailable": 2}
    replay = instrument["replay"]
    assert (replay["u_breaks"], replay["awaiting_snapshot"], replay["baselines"]) == (1, 2, 2)


def test_a_zero_level_delta_is_counted_and_advances_the_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    zero = _Msg(1000, 105)
    shifted = [replace(m, u=m.u + 1) for m in _BASE if m.u >= 105]
    messages = (*[m for m in _BASE if m.u < 105], zero, *shifted)
    polls = _default_polls(messages)
    status, instrument = _day(
        tmp_path, monkeypatch, capsys, _Scenario(messages=messages, polls=polls)
    )
    assert (status, _nonzero(instrument["seconds"])) == (0, {"exact": 8})
    replay = instrument["replay"]
    assert (replay["zero_level_messages"], replay["u_breaks"]) == (1, 0)


@pytest.mark.parametrize(
    ("covered", "verdict"), [(True, "missing_row_explained"), (False, "missing_row")]
)
def test_a_missing_row_is_explained_only_by_a_coverage_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    covered: bool,
    verdict: str,
) -> None:
    run: dict[str, object] = {"kind": "seconds", "instrument_id": _BTC, "reason": "crossed"}
    run |= {"first_s": _S + 3, "last_s": _S + 3, "count": 1}
    scenario = _Scenario(rows={_S + 3: None}, coverage=(run,) if covered else ())
    status, instrument = _day(tmp_path, monkeypatch, capsys, scenario)
    assert status == (0 if covered else 1)
    assert _nonzero(instrument["seconds"]) == {"exact": 7, verdict: 1}


def test_a_rest_poll_disagreeing_at_its_own_seq_invalidates_the_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    at = next(m for m in _BASE if m.u == _POLL_AFTER_U)
    wrong = _with_size(_through(_BASE, _POLL_AFTER_U), 1, 0, "0.701")
    polls = (_poll(wrong, at.seq, at.venue_ns + 150 * _MS),)
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario(polls=polls))
    assert (status, instrument["reference"]) == (1, "invalid")
    assert _nonzero(instrument["rest"]) == {DISAGREE_KEY: 1}
    assert _nonzero(instrument["seconds"]) == {"exact": 8}  # the rows are fine; the oracle is not


def test_a_day_without_any_agreeing_poll_is_unvalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failed = _poll(_through(_BASE, 104), 0, (_S + 1) * _NS, status=503)
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario(polls=(failed,)))
    assert (status, instrument["reference"]) == (1, UNVALIDATED)
    assert _nonzero(instrument["rest"]) == {"failed": 1}


def test_a_recorder_disconnect_makes_the_reference_unavailable_until_a_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    close = _connection((_S + 3) * _NS + 500 * _MS, "close", "server_closed")
    status, instrument = _day(tmp_path, monkeypatch, capsys, _Scenario(connections=(close,)))
    assert status == 0
    # S+3 (closed by 109 after the close line) .. S+5: no snapshot comes back.
    assert _nonzero(instrument["seconds"]) == {"exact": 5, "reference_unavailable": 3}
    assert instrument["replay"]["awaiting_snapshot"] == 3


def test_two_rows_in_one_second_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch, _Scenario())
    again = _snapshot_row(_BTC, _S, _ref(_S), later=3600 * _NS)  # another flush's file
    ParquetDataCatalog(str(catalog)).write_data([again])
    status, report = _run(capsys)
    assert status == 1
    assert _nonzero(_only(report)["seconds"]) == {"exact": 7, "duplicate_row": 1}


def test_a_float_layout_snapshot_file_is_refused_and_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _write_day(tmp_path, monkeypatch, _Scenario())
    table = pa.table(
        {
            "bid_prices": pa.array([[84000.1]], pa.list_(pa.float64())),
            "ts_event": pa.array([_S * _NS], pa.uint64()),
        }
    )
    pq.write_table(table, catalog / "data" / "custom_dydx_second_snapshot" / _BTC / "old.parquet")
    before = error_ledger.counts().get(sites.BOOK_REFUSED, 0)
    with pytest.raises(SystemExit, match=r"float-layout snapshot file.*migrate_snapshot_ints"):
        book_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    assert error_ledger.counts().get(sites.BOOK_REFUSED, 0) == before + 1


def test_a_float_layout_file_outside_the_day_does_not_block_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch, _Scenario())
    table = pa.table(
        {
            "bid_prices": pa.array([[84000.1]], pa.list_(pa.float64())),
            "ts_event": pa.array([_D0 - 3600 * _NS], pa.uint64()),  # the day before
        }
    )
    pq.write_table(table, catalog / "data" / "custom_dydx_second_snapshot" / _BTC / "old.parquet")
    status, report = _run(capsys)
    assert (status, _nonzero(_only(report)["seconds"])) == (0, {"exact": 8})


def test_a_frame_line_without_a_text_raw_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    bad = {"kind": "frame", "recv_ns": _S * _NS, "endpoint": "linear"}
    _write_raw(tmp_path / "verify", "BYBIT", _BOOK_CHANNEL, [*_fillers(), bad])
    with pytest.raises(SystemExit, match="a frame line without a text `raw`"):
        book_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)


def test_the_counters_count_only_the_days_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A snapshot and a delta in the look-back hour, and a delta in the hour after the day."""
    before = replace(_SNAPSHOT, at_ms=(_D0 // _NS - _S) * 1000 - 1_800_000)
    early = _Msg(before.at_ms + 1000, 101, (("84000.10", "1.200"),))
    day = list(_BASE[2:])
    after = _Msg((_D0 // _NS + 86_400 - _S) * 1000 + 60_000, 112, (("83999.00", "1.000"),))
    rows = {second: _ref(second, (before, early, *day)) for second in _ROW_SECONDS}
    polls = _default_polls((before, early, *day))
    whole: dict[str, object] = {"kind": "seconds", "instrument_id": _BTC, "reason": "stale"}
    whole |= {"first_s": _D0 // _NS, "last_s": _D0 // _NS + 86_399, "count": 86_400}
    scenario = _Scenario((before, early, *day, after), rows, polls, (whole,))
    _write_day(tmp_path, monkeypatch, scenario)
    # The recorder connected before the look-back hour: no `startup` line inside the day.
    frames = [_frame(m) for m in scenario.messages]
    _write_raw(tmp_path / "verify", "BYBIT", _BOOK_CHANNEL, [*_fillers()[1:], *frames])
    status, report = _run(capsys)
    instrument = _only(report)
    # The book is available all day (the look-back snapshot to the delta after it closes it).
    seconds = {"exact": 8, "missing_row_explained": 86_392}
    assert (status, _nonzero(instrument["seconds"])) == (0, seconds)
    replay = instrument["replay"]
    assert (replay["messages"], replay["baselines"]) == (len(day), 0)


def test_an_instrument_without_a_book_channel_is_refused_not_a_stop_iteration() -> None:
    from verification.application.book import _feed

    with pytest.raises(ValueError, match=r"no book channel for BTCUSDT-SPOT\.BYBIT"):
        _feed("BYBIT", "BTCUSDT-SPOT.BYBIT", (), "BTCUSDT")


def test_a_book_frame_without_u_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    bad = _frame(_BASE[3])
    raw = json.loads(str(bad["raw"]))
    del raw["data"]["u"]
    _write_raw(
        tmp_path / "verify", "BYBIT", _BOOK_CHANNEL, [*_fillers(), bad | {"raw": json.dumps(raw)}]
    )
    with pytest.raises(SystemExit, match="book field `u`"):
        book_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)


def test_an_open_day_is_refused_and_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    before = error_ledger.counts().get(sites.BOOK_REFUSED, 0)
    with pytest.raises(SystemExit, match="day not closed"):
        book_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _D0 + _NS)
    assert error_ledger.counts().get(sites.BOOK_REFUSED, 0) == before + 1


def test_an_unexpected_exception_is_ledgered_as_a_crash_and_re_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())

    def crash(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(book_tool, "check_day", crash)
    with pytest.raises(RuntimeError, match="boom"):
        book_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    assert "crashed: RuntimeError('boom')" in error_ledger.last_details()[sites.BOOK_REFUSED]


def test_a_missing_coverage_record_or_raw_hour_fails_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from verification.infrastructure.raw_store import channel_file
    from verification.infrastructure.raw_store import hour_of

    _write_day(tmp_path, monkeypatch, _Scenario())
    (tmp_path / "coverage" / "bybit.jsonl").unlink()
    channel_file(tmp_path / "verify", "BYBIT", _REST_CHANNEL, hour_of(_D0) + 3).unlink()
    status, report = _run(capsys)
    assert (status, report["coverage_present"]) == (1, False)
    assert report["missing_raw_files"] == [f"{_REST_CHANNEL}/2026-09-29T03"]
    assert _only(report)["passed"] is True  # the instrument itself is clean


def test_the_text_report_puts_the_rest_agreement_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario(rows={_S: _without(_ref(_S), 0, 1)}))
    status = book_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    text = capsys.readouterr().out
    assert status == 1
    assert text.startswith("book BYBIT 2026-09-29: FAIL")
    assert "integer units (Epic 30.2)" in text
    assert text.index("  rest ") < text.index("  replay ") < text.index("  seconds ")
    assert f"failing second {_S} (epoch s): content_differs bids missing@1" in text


# --- Hyperliquid ---------------------------------------------------------------------------------


def _l2(time_ms: int, bid: str, ask: str) -> dict[str, Any]:
    levels = [[{"px": bid, "sz": "10.5", "n": 2}], [{"px": ask, "sz": "3.25", "n": 1}]]
    return {"coin": "SOL", "time": time_ms, "levels": levels}


def _hl_frame(time_ms: int, bid: str, ask: str) -> dict[str, object]:
    body = json.dumps({"channel": "l2Book", "data": _l2(time_ms, bid, ask)})
    return {"kind": "frame", "recv_ns": time_ms * _MS + 400 * _MS, "endpoint": "ws", "raw": body}


def _hl_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    books: list[tuple[int, str, str]],
    rows: list[Any],
    unrecorded: frozenset[int] = frozenset(),
) -> None:
    """
    Write a Hyperliquid day: the `l2Book` pushes `(time ms, bid, ask)`, a recorder `startup` and
    an `error` line at 00:00:02 of every other hour (a raw file per hour, none in `unrecorded`,
    hours of the day), one REST poll at `books[2]`'s time, and `rows`.
    """
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    start = _connection(_D0 + _NS, reason="startup") | {"endpoint": "ws"}
    busy = {t // 1000 // 3600 - _D0 // _NS // 3600 for t, _, _ in books}
    filler = [
        {**start, "event": "error", "ts_ns": _D0 + h * 3600 * _NS + 2 * _NS}
        for h in range(1, 24)
        if h not in busy and h not in unrecorded
    ]
    frames = [_hl_frame(t, b, a) for t, b, a in books]
    _write_raw(raw, "HYPERLIQUID", "l2Book", [start, *filler, *frames])
    t, b, a = books[2]
    poll = {"kind": "rest", "sent_ns": t * _MS, "endpoint": "info"}
    poll |= {"request": json.dumps({"type": "l2Book", "coin": "SOL"}), "recv_ns": t * _MS + _NS}
    poll |= {"status": 200, "raw": json.dumps(_l2(t, b, a))}
    rest_filler = [
        {**start, "event": "error", "ts_ns": _D0 + h * 3600 * _NS + 2 * _NS}
        for h in range(1, 24)
        if h not in unrecorded
    ]
    _write_raw(raw, "HYPERLIQUID", "rest.l2Book", [start, *rest_filler, poll])
    catalog.mkdir()
    if rows:
        ParquetDataCatalog(str(catalog)).write_data(rows)
    _write_jsonl(tmp_path / "coverage" / "hyperliquid.jsonl", [])
    _env(monkeypatch, tmp_path, "HYPERLIQUID", _SOL)


def _hl_rows(books: list[tuple[int, str, str]], upto: int | None = None) -> list[Any]:
    """Build a row per second from each push to the next one (the last push closes nothing)."""
    return [
        _snapshot_row(_SOL, second, (((b, "10.5"),), ((a, "3.25"),)))
        for i, (t, b, a) in enumerate(books[:-1])
        for second in range(t // 1000, (books[i + 1][0] // 1000) if upto is None else upto)
    ]


_HL_BOOKS = [(_S * 1000 + 300 + 5000 * i, f"{120 + i}.10", f"{120 + i}.20") for i in range(4)]


def test_a_clean_hyperliquid_day_skips_the_subscribe_reply_and_agrees_by_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The first book after the recorder's `open` is its own subscribe reply: not judged."""
    _hl_day(tmp_path, monkeypatch, _HL_BOOKS, _hl_rows(_HL_BOOKS))
    status, report = _run(capsys, "HYPERLIQUID")
    instrument = _only(report)
    assert (status, instrument["reference"]) == (0, VALIDATED)
    assert _nonzero(instrument["rest"]) == {AGREE_KEY: 1}
    # _HL_BOOKS[0] is the recorder's subscribe reply (after the hour-9 `error` line).
    assert _nonzero(instrument["seconds"]) == {"exact": 10, "reference_unavailable": 5}
    assert instrument["replay"]["subscribe_replies"] == 1


def test_an_unrecorded_hour_breaks_the_reference_like_a_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Hour 11 has no raw file: its seconds hold the last hour-10 book in the rows, and must never be
    judged against it; hour 12's first push is then treated as after a reconnect.
    """
    later = [(_S * 1000 + 7_200_000 + 300 + 5000 * i, "130.10", "130.20") for i in range(3)]
    books = [*_HL_BOOKS, *later]
    stale = (((_HL_BOOKS[3][1], "10.5"),), ((_HL_BOOKS[3][2], "3.25"),))
    hour_11 = [_snapshot_row(_SOL, _S + 3600 + k, stale) for k in range(5)]
    rows = [*_hl_rows(_HL_BOOKS), *hour_11, *_hl_rows(later[1:])]
    _hl_day(tmp_path, monkeypatch, books, rows, frozenset({11}))
    status, report = _run(capsys, "HYPERLIQUID")
    instrument = _only(report)
    assert report["missing_raw_files"] == ["l2Book/2026-09-29T11", "rest.l2Book/2026-09-29T11"]
    assert status == 1
    # Hour 11's 5 stale rows are never compared; hour 12 resumes after its first push.
    assert _nonzero(instrument["seconds"]) == {"exact": 15, "reference_unavailable": 10}
    assert instrument["replay"]["subscribe_replies"] == 2


def test_a_day_without_any_reference_book_message_never_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The raw files exist, but no frame of the instrument: "no reference data", not a PASS."""
    _write_day(
        tmp_path, monkeypatch, _Scenario(messages=(), rows=dict.fromkeys(_ROW_SECONDS), polls=())
    )
    status, report = _run(capsys)
    instrument = _only(report)
    assert (status, instrument["passed"], instrument["reference"]) == (1, False, UNVALIDATED)
    assert (instrument["rows"], _nonzero(instrument["seconds"])) == (0, {})
    book_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    assert "no reference data" in capsys.readouterr().out


# --- the pure pieces -----------------------------------------------------------------------------


def _message(
    key: int, bids: Levels = (), asks: Levels = (), baseline: bool = False, u: int | None = None
) -> BookMessage:
    """Build a message at venue time `_S` + `key` ms, keyed `key`, `u` = `key` unless given."""

    def levels(items: Levels) -> tuple[tuple[Decimal, Decimal], ...]:
        return tuple((Decimal(p), Decimal(s)) for p, s in items)

    ns = (_S * 1000 + key) * _MS
    return BookMessage(ns, ns, baseline, key if u is None else u, key, levels(bids), levels(asks))


def _top(book: ReferenceBook) -> tuple[list[tuple[str, str]], list[tuple[str, str]]] | None:
    top = book.top()
    if top is None:
        return None
    return [(str(p), str(s)) for p, s in top.bids], [(str(p), str(s)) for p, s in top.asks]


def test_the_reference_book_applies_only_contiguous_deltas_after_a_snapshot() -> None:
    book = ReferenceBook(replaces=False)
    book.apply(_message(1, (("10", "1"),)))
    assert (_top(book), book.counts["awaiting_snapshot"]) == (None, 1)
    book.apply(_message(5, (("10", "1"), ("9", "2")), (("11", "1"),), baseline=True))
    book.apply(_message(6, (("9", "0"), ("9.5", "4"))))
    assert _top(book) == ([("10", "1"), ("9.5", "4")], [("11", "1")])
    book.apply(_message(6, (("8", "1"),)))  # a regress is a break too
    assert (_top(book), book.counts["u_breaks"]) == (None, 1)


def test_the_undo_reverts_exactly_the_last_message() -> None:
    book = ReferenceBook(replaces=False)
    book.apply(_message(5, (("10", "1"), ("9", "2")), (("11", "1"),), baseline=True))
    before = book.top()
    book.apply(_message(6, (("10", "0"), ("9", "3"), ("9", "5")), (("10.5", "2"),)))
    assert book.top_before_last() == before
    assert (book.size_before_last(BIDS, Decimal(9)), book.size(BIDS, Decimal(9))) == (
        Decimal(2),
        Decimal(5),
    )


def test_a_connection_line_breaks_the_book_until_the_next_snapshot() -> None:
    judge = RestJudge()
    book = ReferenceBook(replaces=False)
    replay = BookReplay(book, range(_S, _S + 3), [], judge)
    replay.message(_message(100, (("10", "1"),), baseline=True))
    replay.connection()
    closed = replay.message(_message(1500, (("10", "2"),)))  # u jumps too: no baseline
    assert [c.ref for c in closed] == [None]
    closed = replay.message(_message(2100, (("10", "3"),), (("11", "1"),), baseline=True))
    assert [c.second for c in closed] == [_S + 1]
    assert [c.second for c in replay.finish()] == [_S + 2]


def test_the_close_rule_keeps_the_seconds_last_message_and_the_next_ones_first() -> None:
    book = ReferenceBook(replaces=False)
    replay = BookReplay(book, range(_S, _S + 1), [], RestJudge())
    replay.message(_message(100, (("10", "1"),), baseline=True))
    replay.message(_message(101, (("10", "2"),)))
    assert replay.message(_message(102, (("10", "3"),))) == []  # all three lie in S
    (closed,) = replay.message(replace(_message(103, (("10", "4"),)), venue_ns=(_S + 1) * _NS))
    size = lambda top: None if top is None else str(top.bids[0][1])  # noqa: E731
    assert (size(closed.ref), size(closed.before), size(closed.after)) == ("3", "2", "4")


def _poll_book(bids: Levels, key: int) -> RestBook:
    return RestBook(True, key, 0, tuple((Decimal(p), Decimal(s)) for p, s in bids), ())


def _judged(book_messages: list[BookMessage], polls: list[RestBook]) -> RestJudge:
    judge = RestJudge()
    replay = BookReplay(ReferenceBook(replaces=False), range(0), polls, judge)
    for message in book_messages:
        replay.message(message)
    replay.finish()
    return judge


def test_rest_polls_are_classified_by_key_bracket_and_between_pushes() -> None:
    messages = [
        _message(10, (("10", "1"),), baseline=True),
        _message(11, (("10", "2"),)),
        _message(12, (("10", "4"),)),
    ]
    polls = [
        _poll_book((("10", "2"),), 11),  # key 11: equal
        _poll_book((("10", "9"),), 12),  # key 12: unequal
        _poll_book((("10", "1"),), 5),  # before the first message: unaligned
        _poll_book((("10", "3"),), 99),  # after the last: unaligned
    ]
    judge = _judged(messages, polls)
    assert _nonzero(judge.counts) == {AGREE_KEY: 1, DISAGREE_KEY: 1, UNALIGNED: 2}


def test_between_pushes_and_bracket_polls_are_placed_between_two_keys() -> None:
    messages = [_message(k, (("10", str(k)),), baseline=True) for k in (10, 20, 30)]
    polls = [_poll_book((("10", "20"),), 25), _poll_book((("10", "25"),), 26)]
    judge = _judged(messages, polls)
    assert _nonzero(judge.counts) == {AGREE_BRACKET: 1, BETWEEN_PUSHES: 1}


def test_a_level_contradicted_twice_untouched_is_a_persistent_disagreement() -> None:
    """A lost reference message: level 9 stays 2 in the reference while REST says 7, twice."""
    base = (("10", "1"), ("9", "2"))
    messages = [
        _message(10, base, baseline=True, u=1),
        _message(20, (("10", "3"),), u=2),
        _message(30, (("10", "4"),), u=3),
        _message(40, (("10", "5"),), u=4),
    ]
    rest = [
        _poll_book((("10", "3.5"), ("9", "7")), 25),
        _poll_book((("10", "4.5"), ("9", "7")), 35),
    ]
    judge = _judged(messages, rest)
    assert _nonzero(judge.counts) == {BETWEEN_PUSHES: 2, PERSISTENT_DISAGREEMENT: 1}
    assert judge.examples == [f"{PERSISTENT_DISAGREEMENT}@key=35:bids 9"]
    touched = [*messages[:2], _message(30, (("10", "4"), ("9", "2")), u=3), messages[3]]
    assert _judged(touched, rest).counts[PERSISTENT_DISAGREEMENT] == 0  # 9 was touched between
    after = [*messages[:3], _message(40, (("10", "5"), ("9", "2")), u=4)]
    assert _judged(after, rest).counts[PERSISTENT_DISAGREEMENT] == 1  # touched after both polls


def test_the_reference_state_needs_reference_data_and_an_agreeing_poll() -> None:
    assert reference_state({AGREE_BRACKET: 1, BETWEEN_PUSHES: 9}, rows=5, messages=9) == VALIDATED
    assert reference_state({BETWEEN_PUSHES: 9}, rows=0, messages=9) == UNVALIDATED
    assert reference_state({}, rows=1, messages=9) == UNVALIDATED
    assert reference_state({}, rows=0, messages=9) == VALIDATED  # nothing stored, nothing polled
    assert reference_state({}, rows=0, messages=0) == UNVALIDATED  # no reference data
    assert reference_state({AGREE_KEY: 3, DISAGREE_KEY: 1}, rows=5, messages=9) == "invalid"


def _stored(
    bids: list[tuple[int, int]], asks: list[tuple[int, int]], ts_init: int = 0
) -> StoredBook:
    return StoredBook(ts_init, 1, 0, BookUnits(tuple(bids), tuple(asks)))


def _closed(ref: BookTop | None, before: BookTop | None = None) -> ClosedSecond:
    return ClosedSecond(_S, ref, before, None, None)


def _book_top(bids: list[tuple[str, str]], asks: list[tuple[str, str]]) -> BookTop:
    def levels(items: list[tuple[str, str]]) -> tuple[tuple[Decimal, Decimal], ...]:
        return tuple((Decimal(p), Decimal(s)) for p, s in items)

    return BookTop(levels(bids), levels(asks))


def test_a_reference_finer_than_the_rows_precision_is_off_grid() -> None:
    row = _stored([(100, 1)], [(101, 1)])
    verdict = classify_second(_closed(_book_top([("10.05", "1")], [("10.1", "1")])), [row], False)
    assert verdict is not None
    assert verdict.verdict == "off_grid"


def test_no_row_and_no_reference_is_not_judged() -> None:
    assert classify_second(_closed(None), [], False) is None


def test_level_diff_names_the_first_differing_level_and_its_kind() -> None:
    ref = BookUnits(((100, 1), (99, 1), (98, 1)), ((101, 1),))
    assert level_diff(BookUnits(((100, 1), (98, 1)), ((101, 1),)), ref) == ("bids missing@1",)
    assert level_diff(BookUnits(((100, 1), (99, 1), (98, 1), (97, 1)), ((101, 2),)), ref) == (
        "bids extra@3",
        "asks size@0",
    )
    assert level_diff(BookUnits(((100, 1), (96, 1), (98, 1)), ((101, 1),)), ref) == (
        "bids price@1",
    )


def test_the_replay_starts_at_the_latest_baseline_or_break_before_the_day() -> None:
    from verification.application.book import _feed
    from verification.domain.conservation import TradeChannel

    channels = (
        TradeChannel(_BOOK_CHANNEL, "linear", False),
        TradeChannel(_REST_CHANNEL, "linear", True),
    )
    feed = _feed("BYBIT", _BTC, channels, "BTCUSDT")
    day = day_hours(_DAY)
    snapshot = _frame(_SNAPSHOT)
    delta = _frame(_BASE[1])
    files = {day.start - 3: [snapshot], day.start - 2: [delta], day.start - 1: [delta]}
    assert replay_start(feed, day, _Records(files)) == day.start - 3
    broken = files | {day.start - 1: [delta, _connection(_D0 - _NS, "close")]}
    assert replay_start(feed, day, _Records(broken)) == day.start - 1  # the break is replayed
    gap = {hour: lines for hour, lines in files.items() if hour != day.start - 2}
    assert replay_start(feed, day, _Records(gap)) == day.start  # a missing hour ends the look
    assert replay_start(feed, day, _Records({})) == day.start


def test_a_hyperliquid_look_back_ending_in_a_break_marks_the_days_first_book_a_reply() -> None:
    """
    The recorder reconnected at 23:59:59 the day before: the day's first `l2Book` is its own
    subscribe reply (audit D-101), so the replay must start before the connection line.
    """
    from verification.application.book import _events
    from verification.application.book import _feed
    from verification.domain.conservation import TradeChannel

    channels = (TradeChannel("l2Book", "", False), TradeChannel("rest.l2Book", "", True))
    feed = _feed("HYPERLIQUID", _SOL, channels, "SOL")
    day = day_hours(_DAY)
    before = _D0 // _MS - 5000
    reconnect = _connection(_D0 - _NS, "open") | {"endpoint": "ws"}
    files = {
        day.start - 1: [_hl_frame(before, "120.10", "120.20"), reconnect],
        day.start: [_hl_frame(_D0 // _MS + 300, "121.10", "121.20")],
    }
    start = replay_start(feed, day, _Records(files))
    assert start == day.start - 1
    book = ReferenceBook(replaces=True)
    for event in _events(feed, range(start, day.start + 1), _Records(files)):
        if event is None:
            book.disconnect()
        else:
            book.apply(event)
    assert (book.counts["subscribe_replies"], book.available) == (1, False)


@dataclass
class _Records:
    """An in-memory raw store: one channel's lines per hour."""

    files: dict[int, list[dict[str, object]]]

    def exists(self, channel: str, hour: int) -> bool:
        return hour in self.files

    def records(self, channel: str, hour: int) -> list[dict[str, object]]:
        return self.files.get(hour, [])

    def truncated_neighbours(self) -> tuple[str, ...]:
        return ()


def test_a_bybit_frame_is_parsed_exactly_and_a_bad_value_refused() -> None:
    symbol, message = bybit_frame(_frame(_BASE[1]), "t")
    assert (symbol, message.u, message.key, message.bids) == (
        "BTCUSDT",
        101,
        _BASE[1].seq,
        ((Decimal("84000.10"), Decimal("1.200")),),
    )
    bad = json.loads(str(_frame(_BASE[1])["raw"]))
    bad["data"]["b"] = [["8.4e4", "1"]]
    with pytest.raises(MalformedLine, match="not a decimal string"):
        bybit_frame({"kind": "frame", "recv_ns": 1, "raw": json.dumps(bad)}, "t")


def test_the_gap_decode_subtracts_for_bids_adds_for_asks_and_refuses_a_non_positive_gap() -> None:
    assert decode_prices([100, 1, 4], "bids") == (100, 99, 95)
    assert decode_prices([100, 1, 4], "asks") == (100, 101, 105)
    assert decode_prices([], "bids") == ()
    with pytest.raises(ValueError, match="non-positive stored gap 0"):
        decode_prices([100, 0], "asks")
    with pytest.raises(ValueError, match="non-positive stored gap -3"):
        decode_prices([100, -3], "bids")


def _row(**changes: object) -> dict[str, Any]:
    row: dict[str, Any] = {"ts_event": _S * _NS, "ts_init": _S * _NS + 7}
    row |= {"price_precision": 2, "size_precision": 3, "bid_prices": [100, 60], "bid_sizes": [1, 1]}
    return row | {"ask_prices": [101], "ask_sizes": [1]} | changes


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"ts_init": None}, "without ts_init"),
        ({"ts_event": None}, "without ts_event"),
        ({"size_precision": None}, "without size_precision"),
        ({"ask_prices": None}, "a null asks column"),  # an empty side is [], never null
        ({"bid_sizes": None}, "a null bids column"),
        ({"bid_prices": [100, 100]}, "a price <= 0 units"),  # the second bid decodes to 0
        ({"bid_prices": [5, 9]}, "a price <= 0 units"),
    ],
)
def test_a_row_that_cannot_be_judged_is_refused_as_a_value_error(
    changes: dict[str, object], message: str
) -> None:
    assert stored_book(_row()).book == BookUnits(((100, 1), (40, 1)), ((101, 1),))
    with pytest.raises(ValueError, match=message):
        stored_book(_row(**changes))


def _random_side(rng: random.Random, best: int, sign: int) -> list[tuple[int, int]]:
    prices = [best]
    for _ in range(rng.randrange(0, 20)):
        prices.append(prices[-1] + sign * rng.randrange(1, 10**6))
    return [(price, rng.randrange(1, 10**12)) for price in prices]


@pytest.mark.parametrize("seed", range(20))
def test_a_random_book_round_trips_through_the_production_encoder_and_this_decoder(
    seed: int,
) -> None:
    rng = random.Random(seed)  # noqa: S311 (a seeded property test, not security)
    pp, sp = rng.randrange(0, 9), rng.randrange(0, 9)
    best_bid = rng.randrange(10**6, 10**12)
    bids = _random_side(rng, best_bid, -1) if rng.random() > 0.1 else []
    asks = _random_side(rng, best_bid + rng.randrange(1, 10**4), 1) if rng.random() > 0.1 else []
    snapshot = DydxSecondSnapshot(
        instrument_id=make_snapshot(instrument_id=_BTC).instrument_id,
        price_precision=pp,
        size_precision=sp,
        bid_price_units=[p for p, _ in bids],
        bid_size_units=[s for _, s in bids],
        ask_price_units=[p for p, _ in asks],
        ask_size_units=[s for _, s in asks],
        buy_volume_units=0,
        sell_volume_units=0,
        buy_count=0,
        sell_count=0,
        ts_event=_S * _NS,
        ts_init=_S * _NS + 7,
    )
    decoded = stored_book(DydxSecondSnapshot.to_dict(snapshot) | {"ts_init": _S * _NS + 7})
    assert decoded == StoredBook(_S * _NS + 7, pp, sp, BookUnits(tuple(bids), tuple(asks)))
