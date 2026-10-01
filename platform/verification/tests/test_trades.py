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
`python3 -m verification.trades` end to end (Story 31.4): reference lines in the recorder's own
raw format, a real catalog written by `ParquetDataCatalog.write_data` (real `TradeTick`s and
`DydxSecondSnapshot`s whose trade columns are the day's fold) and a coverage record. The clean
day passes exactly; every planted defect makes a failing count non-zero and the exit status 1;
every explained case passes. Then the pure pieces: the fold, the recorder gaps, the decode.
"""

import json
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import cast

import pyarrow as pa
import pytest
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification import trades
from verification.application import sites
from verification.application.conservation import day_hours
from verification.application.conservation import day_start_ns
from verification.application.trades import check_hours
from verification.domain.conservation import BUYER
from verification.domain.conservation import NO_AGGRESSOR
from verification.domain.conservation import SELLER
from verification.domain.conservation import Explanations
from verification.domain.conservation import Intervals
from verification.domain.conservation import MalformedLine
from verification.domain.conservation import ReferenceTrade
from verification.domain.trade_check import EXPLAINED_ONLY
from verification.domain.trade_check import MAX_PLAUSIBLE_LATENCY_NS
from verification.domain.trade_check import RECORDER_GAP_MARGIN_NS
from verification.domain.trade_check import ArchivedTrade
from verification.domain.trade_check import IdContext
from verification.domain.trade_check import LatencyHistogram
from verification.domain.trade_check import OffGrid
from verification.domain.trade_check import ReferenceId
from verification.domain.trade_check import SecondFacts
from verification.domain.trade_check import StoredRow
from verification.domain.trade_check import TradeColumns
from verification.domain.trade_check import classify_second
from verification.domain.trade_check import compare_ids
from verification.domain.trade_check import decode_fixed
from verification.domain.trade_check import fixed_raw
from verification.domain.trade_check import fold_second
from verification.domain.trade_check import merge_reference
from verification.domain.trade_check import recorder_gaps
from verification.domain.trade_check import whole_at
from verification.infrastructure.catalog_reader import ParquetArchive
from verification.infrastructure.raw_store import channel_file
from verification.infrastructure.raw_store import encode_line
from verification.infrastructure.raw_store import hour_of


_DAY = date(2026, 9, 29)
_D0 = day_start_ns(_DAY)
_NS = 1_000_000_000
_MS = 1_000_000
_BTC = "BTCUSDT-LINEAR.BYBIT"
_SOL = "SOL-USD-PERP.HYPERLIQUID"
_S1 = _D0 // _NS + 36_000  # 10:00:00
_S2 = _S1 + 1
_S3 = _S1 + 5  # a row, no reference trade
_NOW = _D0 + 2 * 86_400 * _NS  # the day is closed
_WS_LAG = 50 * _MS  # venue time -> the recorder's receipt
_ARCHIVE_LAG = 51 * _MS  # venue time -> capture's receipt (`ts_init`)


@dataclass(frozen=True)
class _Trade:
    """One trade as the wire spelled it (`frame`: which WS frame of the scenario carries it)."""

    trade_id: str
    ts_ns: int
    price: str
    size: str
    side: str
    frame: int


# t1 and t2 share a millisecond and a frame (t1 first); t3 is later in S1; t4 is alone in S2.
_T1 = _Trade("t1", _S1 * _NS + 100 * _MS, "84000.10", "0.010", "Buy", 0)
_T2 = _Trade("t2", _S1 * _NS + 100 * _MS, "84000.30", "0.020", "Sell", 0)
_T3 = _Trade("t3", _S1 * _NS + 700 * _MS, "83999.90", "0.005", "Buy", 1)
_T4 = _Trade("t4", _S2 * _NS + 200 * _MS, "84001.00", "0.001", "Sell", 2)
_TRADES = (_T1, _T2, _T3, _T4)
# The rows' eight trade columns at precision 2 / 3: exactly the fold of the four trades.
_ROW_S1 = TradeColumns(8400010, 8400030, 8399990, 8399990, 15, 20, 2, 1)
_ROW_S2 = TradeColumns(8400100, 8400100, 8400100, 8400100, 0, 1, 0, 1)
_EMPTY = TradeColumns()
_SIDES = {"Buy": AggressorSide.BUYER, "Sell": AggressorSide.SELLER, "B": AggressorSide.BUYER}


def _connection(ts_ns: int, event: str = "open", reason: str = "reconnect") -> dict[str, object]:
    line = {"kind": "connection", "event": event, "ts_ns": ts_ns, "endpoint": "linear"}
    return line | {"reason": reason}


def _every_hour(ws: bool = True) -> list[dict[str, object]]:
    """
    Return a line in each hour of the day, so no raw hour of it is missing: the recorder's
    `startup` open, then (WS) a frame without trades each hour -- another `open` with no `close`
    before it would be a recorder gap -- or (REST, whose lines make no gap) a connection line.
    """
    start = _connection(_D0 + _NS, reason="startup")
    if not ws:
        return [start, *(_connection(_D0 + hour * 3600 * _NS + _NS) for hour in range(1, 24))]
    empty = json.dumps({"topic": "publicTrade.BTCUSDT", "data": []})
    frames = [
        {"kind": "frame", "recv_ns": _D0 + hour * 3600 * _NS + _NS, "endpoint": "ws", "raw": empty}
        for hour in range(1, 24)
    ]
    return [start, *frames]


def _frame(trades: list[_Trade]) -> dict[str, object]:
    items = [
        {"T": t.ts_ns // _MS, "s": "BTCUSDT", "S": t.side, "v": t.size, "p": t.price}
        | {"L": "PlusTick", "i": t.trade_id, "BT": False, "RPI": False, "seq": 1}
        for t in trades
    ]
    raw = {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "ts": trades[0].ts_ns // _MS}
    recv_ns = trades[0].ts_ns + _WS_LAG
    return {
        "kind": "frame",
        "recv_ns": recv_ns,
        "endpoint": "linear",
        "raw": json.dumps(raw | {"data": items}),
    }


def _poll(trades: list[_Trade], recv_ns: int) -> dict[str, object]:
    rows = [
        {"execId": t.trade_id, "symbol": "BTCUSDT", "price": t.price, "size": t.size}
        | {"side": t.side, "time": str(t.ts_ns // _MS), "isBlockTrade": False, "seq": "1"}
        for t in reversed(trades)  # the venue lists newest first
    ]
    raw = {"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": rows}}
    request = "/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=1000"
    line = {"kind": "rest", "sent_ns": recv_ns - _MS, "endpoint": "linear", "request": request}
    return line | {"recv_ns": recv_ns, "status": 200, "raw": json.dumps(raw)}


def _write_raw(root: Path, venue: str, channel: str, lines: list[dict[str, object]]) -> None:
    """File lines by their own hour, in time order, exactly as the recorder's store does."""
    by_hour: dict[int, list[dict[str, object]]] = {}
    for line in sorted(lines, key=_stamp):
        by_hour.setdefault(hour_of(_stamp(line)), []).append(line)
    for hour, hour_lines in by_hour.items():
        path = channel_file(root, venue, channel, hour)
        path.parent.mkdir(parents=True, exist_ok=True)
        with pa.CompressedOutputStream(str(path), "zstd") as stream:
            for line in hour_lines:
                stream.write(encode_line(line))


def _stamp(line: Mapping[str, object]) -> int:
    stamp = line.get("recv_ns", line.get("ts_ns"))
    assert isinstance(stamp, int)
    return stamp


def _tick(iid: str, trade: _Trade, ts_init_offset: int = _ARCHIVE_LAG) -> TradeTick:
    return TradeTick(
        InstrumentId.from_str(iid),
        Price.from_str(trade.price),
        Quantity.from_str(trade.size),
        _SIDES.get(trade.side, AggressorSide.NO_AGGRESSOR),
        TradeId(trade.trade_id),
        trade.ts_ns,
        trade.ts_ns + ts_init_offset,
    )


def _snapshot(iid: str, second: int, columns: TradeColumns, offset: int = 0) -> Any:
    ts_event = second * _NS + _NS // 2
    return make_snapshot(
        instrument_id=iid,
        bid_prices=[1.0],
        bid_sizes=[1.0],
        ask_prices=[2.0],
        ask_sizes=[1.0],
        ts_event=ts_event,
        ts_init=ts_event + _NS + offset,
        price_precision=2,
        size_precision=3,
        open_price=_price(columns.open_price),
        high_price=_price(columns.high_price),
        low_price=_price(columns.low_price),
        close_price=_price(columns.close_price),
        buy_volume=Decimal(columns.buy_volume).scaleb(-3),
        sell_volume=Decimal(columns.sell_volume).scaleb(-3),
        buy_count=columns.buy_count,
        sell_count=columns.sell_count,
    )


def _price(units: int | None) -> Decimal | None:
    return None if units is None else Decimal(units).scaleb(-2)


@dataclass(frozen=True)
class _Scenario:
    """One Bybit day: what the wire said, what the archive holds, the rows and the coverage."""

    wire: tuple[_Trade, ...] = _TRADES
    poll: tuple[_Trade, ...] = (_T1, _T4)
    archived: tuple[_Trade, ...] = _TRADES
    rows: Mapping[int, TradeColumns] = field(
        default_factory=lambda: {_S1: _ROW_S1, _S2: _ROW_S2, _S3: _EMPTY}
    )
    coverage: tuple[Mapping[str, object], ...] = ()
    connections: tuple[dict[str, object], ...] = ()


def _write_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: _Scenario) -> Path:
    """Write the scenario and point the environment at it; return the catalog root."""
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    groups = ([t for t in scenario.wire if t.frame == n] for n in range(3))
    frames = [_frame(group) for group in groups if group]
    ws = [*_every_hour(), *frames, *scenario.connections]
    _write_raw(raw, "BYBIT", "linear.publicTrade", ws)
    rest = [_poll(list(scenario.poll), _S2 * _NS + 20 * _NS)] if scenario.poll else []
    _write_raw(raw, "BYBIT", "linear.rest.recent-trade", [*_every_hour(ws=False), *rest])
    catalog.mkdir(parents=True, exist_ok=True)
    writer = ParquetDataCatalog(str(catalog))
    ticks = sorted((_tick(_BTC, t) for t in scenario.archived), key=lambda t: t.ts_init)
    if ticks:
        writer.write_data(ticks)
    rows = [_snapshot(_BTC, s, c) for s, c in sorted(scenario.rows.items())]
    writer.write_data(rows)
    _write_jsonl(tmp_path / "coverage" / "bybit.jsonl", scenario.coverage)
    plan = tmp_path / "bybit.toml"
    plan.write_text(f'instruments = ["{_BTC}"]\n')
    monkeypatch.setenv("VERIFY_DATA_DIR", str(raw))
    monkeypatch.setenv("CATALOG_PATH", str(catalog))
    monkeypatch.setenv("BYBIT_COLLECTOR_CONFIG", str(plan))
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    return catalog


def _write_jsonl(path: Path, lines: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{json.dumps(line)}\n" for line in lines))


def _run(
    capsys: pytest.CaptureFixture[str], stage: str = "live", venue: str = "BYBIT"
) -> tuple[int, dict[str, Any]]:
    argv = ["--venue", venue, "--day", _DAY.isoformat(), "--stage", stage, "--json"]
    status = trades.main(argv, clock=lambda: _NOW)
    return status, json.loads(capsys.readouterr().out)


def _only(report: dict[str, Any]) -> dict[str, Any]:
    (instrument,) = report["instruments"]
    return instrument


def _nonzero(counts: Mapping[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in counts.items() if isinstance(value, int) and value}


def _day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: _Scenario,
    stage: str = "live",
) -> tuple[int, dict[str, Any], dict[str, Any], dict[str, int]]:
    """Run one scenario: the exit status, the report, its ids and its non-zero second classes."""
    _write_day(tmp_path, monkeypatch, scenario)
    status, report = _run(capsys, stage)
    instrument = _only(report)
    return status, report, instrument["ids"], _nonzero(instrument["seconds"]["classes"])


@pytest.mark.parametrize("stage", ["live", "rebuilt"])
def test_a_clean_day_passes_with_every_id_matched_and_every_second_exact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stage: str
) -> None:
    status, report, ids, seconds = _day(tmp_path, monkeypatch, capsys, _Scenario(), stage)
    assert (status, report["passed"], report["provisional"]) == (0, True, stage == "live")
    assert (_only(report)["passed"], _only(report)["failing"]) == (True, 0)
    assert _nonzero(ids) == {"seen": 4, "matched": 4}
    assert seconds == {"exact": 3}
    latency = _only(report)["latency_ms"]
    assert (latency["count"], latency["min_ms"], latency["max_ms"]) == (4, 1, 1)


def test_a_removed_archived_trade_is_missing_and_its_second_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The archive lost t3, and the rebuilt row agrees with the archive, not the venue."""
    row = TradeColumns(8400010, 8400030, 8400010, 8400030, 10, 20, 1, 1)
    scenario = _Scenario(archived=(_T1, _T2, _T4), rows={_S1: row, _S2: _ROW_S2, _S3: _EMPTY})
    status, _, ids, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert status == 1
    assert (ids["missing_unexplained"], ids["examples"]) == (1, ["missing_unexplained:t3"])
    assert seconds == {"exact": 2, "archive_differs": 1}


def test_an_archived_size_one_unit_off_is_a_size_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bigger = replace(_T4, size="0.002")
    row = replace(_ROW_S2, sell_volume=2)
    scenario = _Scenario(archived=(_T1, _T2, _T3, bigger), rows={_S1: _ROW_S1, _S2: row})
    status, report, ids, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert status == 1
    assert _nonzero(ids) == {"seen": 4, "matched": 4, "mismatch_size": 1}
    assert seconds == {"exact": 1, "archive_differs": 1}
    # Story 31.11: the failing id plus the failing second, the count the day verdict reads.
    assert (_only(report)["passed"], _only(report)["failing"]) == (False, 2)


def test_a_ts_event_moved_into_the_next_second_fails_both_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    moved = replace(_T3, ts_ns=_S2 * _NS + 100 * _MS)
    s1 = TradeColumns(8400010, 8400030, 8400010, 8400030, 10, 20, 1, 1)
    s2 = TradeColumns(8399990, 8400100, 8399990, 8400100, 5, 1, 1, 1)
    scenario = _Scenario(archived=(_T1, _T2, moved, _T4), rows={_S1: s1, _S2: s2})
    status, _, ids, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert status == 1
    assert ids["mismatch_ts_event"] == 1
    assert seconds == {"archive_differs": 2}


_EXTRA = _Trade("x1", _S3 * _NS + 300 * _MS, "84002.00", "0.004", "Buy", 9)
_ROW_S3_EXTRA = TradeColumns(8400200, 8400200, 8400200, 8400200, 4, 0, 1, 0)


def _backfilled(*trade_ids: str) -> dict[str, object]:
    line: dict[str, object] = {"kind": "trades_backfilled", "instrument_id": _BTC}
    return line | {"count": len(trade_ids), "trade_ids": list(trade_ids)}


def _recorder_gap(around_ns: int) -> tuple[dict[str, object], ...]:
    return (
        _connection(around_ns - 2 * _NS, "close", "server_closed"),
        _connection(around_ns + 2 * _NS, "open", "reconnect"),
    )


@pytest.mark.parametrize(
    ("backfilled", "gap", "explained"),
    [(True, True, True), (True, False, False), (False, True, False)],
)
def test_an_archive_only_id_is_explained_only_when_backfilled_inside_a_recorder_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    backfilled: bool,
    gap: bool,
    explained: bool,
) -> None:
    scenario = _Scenario(
        archived=(*_TRADES, _EXTRA),
        rows={_S1: _ROW_S1, _S2: _ROW_S2, _S3: _ROW_S3_EXTRA},
        coverage=(_backfilled("x1"),) if backfilled else (),
        connections=_recorder_gap(_EXTRA.ts_ns) if gap else (),
    )
    status, _, ids, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert status == (0 if explained else 1)
    assert (ids["extra_explained"], ids["extra_unexplained"]) == (explained, not explained)
    assert seconds.get("explained_loss", 0) == explained


@pytest.mark.parametrize(
    ("covered", "verdict"), [(False, "missing_row"), (True, "missing_row_explained")]
)
def test_a_second_holding_only_an_archive_only_id_and_no_row_is_judged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    covered: bool,
    verdict: str,
) -> None:
    """x1 is backfilled inside a recorder gap (an explained id), but S3 has no row at all."""
    run: dict[str, object] = {"kind": "seconds", "instrument_id": _BTC, "reason": "stale"}
    run |= {"first_s": _S3, "last_s": _S3, "count": 1}
    scenario = _Scenario(
        archived=(*_TRADES, _EXTRA),
        rows={_S1: _ROW_S1, _S2: _ROW_S2},
        coverage=(_backfilled("x1"), run) if covered else (_backfilled("x1"),),
        connections=_recorder_gap(_EXTRA.ts_ns),
    )
    status, _, ids, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert (status, ids["extra_explained"]) == (0 if covered else 1, 1)
    assert seconds == {"exact": 2, verdict: 1}


def test_a_late_trade_the_live_row_missed_is_provisional_live_and_a_mismatch_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """t4 arrived after S2 closed: archived, not folded live; the rebuild must place it."""
    scenario = _Scenario(rows={_S1: _ROW_S1, _S2: _EMPTY})
    status, report, _, seconds = _day(tmp_path, monkeypatch, capsys, scenario, "live")
    assert (status, report["provisional"], seconds) == (
        0,
        True,
        {"exact": 1, "live_provisional": 1},
    )
    status, report = _run(capsys, "rebuilt")
    assert (status, _nonzero(_only(report)["seconds"]["classes"])) == (
        1,
        {"exact": 1, "rebuild_mismatch": 1},
    )


def test_a_row_inside_an_archive_gap_marker_is_kept_live_by_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch, _Scenario(rows={_S1: _ROW_S1, _S2: _EMPTY}))
    marker = {"instrument_id": _BTC, "from_ns": _S2 * _NS, "to_ns": _S2 * _NS + _NS}
    _write_jsonl(
        catalog / "_archive_gaps" / f"{_BTC}.jsonl",
        [marker | {"reason": "write_failed", "count": 1}],
    )
    status, report = _run(capsys, "rebuilt")
    assert status == 0
    assert _nonzero(_only(report)["seconds"]["classes"]) == {"exact": 1, "live_kept": 1}


def test_a_row_before_the_first_archived_trade_is_kept_live_by_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Nothing archived yet at S1 - 60: the rebuild keeps that row's live values."""
    early = _S1 - 60
    scenario = _Scenario(rows={early: _ROW_S2, _S1: _ROW_S1, _S2: _ROW_S2})
    status, _, _, seconds = _day(tmp_path, monkeypatch, capsys, scenario, "rebuilt")
    assert (status, seconds) == (0, {"exact": 2, "live_kept": 1})


def test_ws_and_rest_disagreeing_about_one_id_is_a_reference_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(poll=(_T1, replace(_T4, price="84001.50")))
    status, _, ids, _ = _day(tmp_path, monkeypatch, capsys, scenario)
    assert status == 1
    assert (ids["reference_conflict"], ids["examples"]) == (1, ["reference_conflict:t4"])


def test_an_unknown_side_token_is_no_aggressor_counted_and_folded_to_sell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bybit's `S: ""` (its adapter's NoAggressor): the S1 row is unchanged, t2 sells either way."""
    blank = replace(_T2, side="")
    wire = (_T1, blank, _T3, _T4)
    scenario = _Scenario(wire=wire, archived=wire)
    status, _, ids, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert status == 0
    assert (ids["wire_no_aggressor"], ids["no_aggressor_tokens"]) == (1, [""])
    assert seconds == {"exact": 3}


def test_a_trade_side_the_archive_flipped_is_a_side_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    flipped = replace(_T4, side="Buy")
    scenario = _Scenario(archived=(_T1, _T2, _T3, flipped))
    status, _, ids, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert (status, ids["mismatch_side"]) == (1, 1)
    assert seconds == {"exact": 3}  # the live row (the venue's fold) is right; the archive is not


def test_a_price_mismatch_and_an_id_stored_twice_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(archived=(_T1, _T2, _T3, replace(_T4, price="84001.01")))
    catalog = _write_day(tmp_path, monkeypatch, scenario)
    ParquetDataCatalog(str(catalog)).write_data([_tick(_BTC, _T1, 2 * 3600 * _NS)])
    status, report = _run(capsys)
    ids = _only(report)["ids"]
    assert status == 1
    assert (ids["mismatch_price"], ids["duplicated"]) == (1, 1)


def test_rows_missing_or_doubled_are_classified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch, _Scenario(rows={_S1: _ROW_S1, _S3: _EMPTY}))
    ParquetDataCatalog(str(catalog)).write_data([_snapshot(_BTC, _S3, _EMPTY, 3600 * _NS)])
    status, report = _run(capsys)
    assert status == 1
    assert _nonzero(_only(report)["seconds"]["classes"]) == {
        "exact": 1,
        "missing_row": 1,
        "duplicate_row": 1,
    }


def test_a_missing_row_inside_a_coverage_seconds_run_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run: dict[str, object] = {"kind": "seconds", "instrument_id": _BTC, "reason": "stale"}
    run |= {"first_s": _S2, "last_s": _S2, "count": 1}
    scenario = _Scenario(rows={_S1: _ROW_S1}, coverage=(run,))
    status, _, _, seconds = _day(tmp_path, monkeypatch, capsys, scenario)
    assert (status, seconds) == (0, {"exact": 1, "missing_row_explained": 1})


def test_a_dropped_trade_the_coverage_record_explains_is_an_explained_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dropped: dict[str, object] = {
        "kind": "trades_dropped",
        "instrument_id": _BTC,
        "reason": "stale",
    }
    dropped |= {"first_ns": _T4.ts_ns, "last_ns": _T4.ts_ns, "count": 1}
    scenario = _Scenario(archived=(_T1, _T2, _T3), rows={_S1: _ROW_S1, _S2: _EMPTY})
    status, _, ids, seconds = _day(
        tmp_path, monkeypatch, capsys, replace(scenario, coverage=(dropped,))
    )
    assert (status, ids["missing_explained"]) == (0, 1)
    assert seconds == {"exact": 1, "explained_loss": 1}


def test_a_missing_coverage_record_or_raw_hour_fails_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    (tmp_path / "coverage" / "bybit.jsonl").unlink()
    channel_file(tmp_path / "verify", "BYBIT", "linear.publicTrade", hour_of(_D0) + 3).unlink()
    status, report = _run(capsys)
    assert (status, report["coverage_present"]) == (1, False)
    assert report["missing_raw_files"] == ["linear.publicTrade/2026-09-29T03"]


@pytest.mark.parametrize(
    ("argv_stage", "now_ns", "message"),
    [
        ("live", _D0 + 3600 * _NS, "day not closed"),
        ("provisional", _NOW, "unknown --stage 'provisional'"),
    ],
)
def test_an_open_day_or_an_unknown_stage_is_refused_and_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, argv_stage: str, now_ns: int, message: str
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    before = error_ledger.counts().get(sites.TRADES_REFUSED, 0)
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--stage", argv_stage]
    with pytest.raises(SystemExit, match=message):
        trades.main(argv, clock=lambda: now_ns)
    assert error_ledger.counts().get(sites.TRADES_REFUSED, 0) == before + 1


def test_a_malformed_wire_price_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario(wire=(_T1, replace(_T2, price="8.4e4"), _T3, _T4)))
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--stage", "live"]
    with pytest.raises(SystemExit, match="not a decimal string"):
        trades.main(argv, clock=lambda: _NOW)


def test_an_over_long_wire_decimal_is_refused_not_rounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wire decimal longer than the exact context holds raises `Inexact` in the fold: refused."""
    over_long = replace(_T2, price="84000." + "1" * 130)
    _write_day(tmp_path, monkeypatch, _Scenario(wire=(_T1, over_long, _T3, _T4)))
    before = error_ledger.counts().get(sites.TRADES_REFUSED, 0)
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--stage", "live"]
    with pytest.raises(SystemExit, match="trades refused"):
        trades.main(argv, clock=lambda: _NOW)
    assert error_ledger.counts().get(sites.TRADES_REFUSED, 0) == before + 1


def test_an_unexpected_exception_is_ledgered_as_a_crash_and_re_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())

    def crash(*_: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(trades, "check_day", crash)
    before = error_ledger.counts().get(sites.TRADES_REFUSED, 0)
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--stage", "live"]
    with pytest.raises(RuntimeError, match="boom"):
        trades.main(argv, clock=lambda: _NOW)
    assert error_ledger.counts().get(sites.TRADES_REFUSED, 0) == before + 1
    assert "crashed: RuntimeError('boom')" in error_ledger.last_details()[sites.TRADES_REFUSED]


def test_the_text_report_names_the_verdict_stage_and_failing_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario(archived=(_T1, _T2, _T4)))
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--stage", "live"]
    status = trades.main(argv, clock=lambda: _NOW)
    text = capsys.readouterr().out
    assert status == 1
    # A live failure is never one the rebuild corrects: no "provisional" rerun advice on it.
    assert text.startswith("trades BYBIT 2026-09-29 stage live: FAIL (live stage: none of")
    assert "failing ids: missing_unexplained:t3" in text


def test_only_a_passing_live_verdict_is_labelled_provisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--stage", "live"]
    status = trades.main(argv, clock=lambda: _NOW)
    assert status == 0
    assert capsys.readouterr().out.startswith(
        "trades BYBIT 2026-09-29 stage live: PASS (PROVISIONAL"
    )


def test_a_clean_hyperliquid_day_matches_tid_side_and_millisecond_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    trade = _Trade("794607121811834", _S1 * _NS + 597 * _MS, "119.83", "0.1", "B", 0)
    item: dict[str, object] = {"coin": "SOL", "side": "B", "px": trade.price, "sz": trade.size}
    item |= {"time": trade.ts_ns // _MS, "hash": "0x0", "tid": int(trade.trade_id)}
    frame = json.dumps({"channel": "trades", "data": [item]})
    line = {"kind": "frame", "recv_ns": trade.ts_ns + 300 * _MS, "endpoint": "ws", "raw": frame}
    _write_raw(raw, "HYPERLIQUID", "trades", [*_every_hour(), line])
    catalog.mkdir()
    writer = ParquetDataCatalog(str(catalog))
    writer.write_data([_tick(_SOL, trade, 310 * _MS)])
    row = TradeColumns(11983, 11983, 11983, 11983, 100, 0, 1, 0)  # 0.1 at size precision 3
    writer.write_data([_snapshot(_SOL, _S1, row)])
    _write_jsonl(tmp_path / "coverage" / "hyperliquid.jsonl", [])
    plan = tmp_path / "hl.toml"
    plan.write_text(f'instruments = ["{_SOL}"]\n')
    monkeypatch.setenv("VERIFY_DATA_DIR", str(raw))
    monkeypatch.setenv("CATALOG_PATH", str(catalog))
    monkeypatch.setenv("HYPERLIQUID_COLLECTOR_CONFIG", str(plan))
    status, report = _run(capsys, venue="HYPERLIQUID")
    instrument = _only(report)
    assert status == 0
    assert _nonzero(instrument["ids"]) == {"seen": 1, "matched": 1}
    assert _nonzero(instrument["seconds"]["classes"]) == {"exact": 1}
    assert instrument["latency_ms"]["p50_ms"] == 10


# --- the pure pieces ---------------------------------------------------------------------------


def _ref(
    order: tuple[int, ...], price: str, size: str, side: int, via_rest: bool = False
) -> ReferenceTrade:
    return ReferenceTrade(
        _BTC, f"id{order}", order[0], via_rest, Decimal(price), Decimal(size), side, "", order
    )


def test_the_fold_orders_ties_by_receipt_then_position_and_sells_no_aggressor() -> None:
    trades_ = [
        _ref((5, 20, 0), "10.2", "1", BUYER),  # same time, received later
        _ref((5, 10, 1), "10.1", "2", NO_AGGRESSOR),  # same time and frame, second in it
        _ref((5, 10, 0), "10.0", "3", SELLER),  # first
    ]
    assert fold_second(trades_, 1, 0) == TradeColumns(100, 102, 100, 102, 1, 5, 1, 2)


def test_the_fold_of_no_trade_is_empty_and_an_off_grid_value_raises() -> None:
    assert fold_second([], 2, 3) == TradeColumns()
    with pytest.raises(OffGrid):
        fold_second([_ref((1, 1, 0), "84528.67", "1", BUYER)], 1, 0)


def _lines(*events: tuple[str, int, str]) -> list[dict[str, object]]:
    return [
        {"kind": "frame", "recv_ns": ts} if kind == "frame" else _connection(ts, kind, reason)
        for kind, ts, reason in events
    ]


def test_a_close_to_open_gap_is_widened_by_the_margin_on_both_sides() -> None:
    lines = _lines(
        ("frame", 100, ""), ("close", 200, "error"), ("error", 250, "x"), ("open", 300, "reconnect")
    )
    gaps = recorder_gaps(lines, 0, 10**6)
    assert (gaps.starts, gaps.ends) == (
        (200 - RECORDER_GAP_MARGIN_NS,),
        (300 + RECORDER_GAP_MARGIN_NS,),
    )


def test_a_startup_open_without_a_close_starts_the_gap_at_the_previous_line() -> None:
    """A crash: the last frame, then the restarted process's `startup` open."""
    since = -(10**12)  # far enough before the first line that its own gap stays apart
    lines = _lines(("frame", 50, ""), ("frame", 100, ""), ("open", 400, "startup"))
    gaps = recorder_gaps(lines, since, 10**6)
    assert (gaps.starts, gaps.ends) == (
        (100 - RECORDER_GAP_MARGIN_NS,),
        (400 + RECORDER_GAP_MARGIN_NS,),
    )


@pytest.mark.parametrize("reason", ["startup", "reconnect"])
def test_an_open_first_in_the_read_starts_its_gap_at_the_reads_start(reason: str) -> None:
    """A gap that began before the read: its `close` (or the crash) lies in an unread hour."""
    since, opened = 10**12, 10**12 + 7 * _NS
    gaps = recorder_gaps(_lines(("open", opened, reason), ("frame", opened + _NS, "")), since, 0)
    assert (gaps.starts, gaps.ends) == (
        (since - RECORDER_GAP_MARGIN_NS,),
        (opened + RECORDER_GAP_MARGIN_NS,),
    )


def test_a_reconnect_open_without_its_close_starts_the_gap_at_the_previous_line() -> None:
    lines = _lines(("frame", 10**12, ""), ("open", 10**12 + 9 * _NS, "reconnect"))
    gaps = recorder_gaps(lines, 0, 0)
    assert gaps.starts[-1] == 10**12 - RECORDER_GAP_MARGIN_NS


def test_a_gap_still_open_runs_to_the_end_and_a_bad_line_is_refused() -> None:
    gaps = recorder_gaps(_lines(("close", 200, "shutdown")), 0, 1000)
    assert gaps.ends == (1000 + RECORDER_GAP_MARGIN_NS,)
    with pytest.raises(MalformedLine):
        recorder_gaps([{"kind": "connection", "event": "opened", "ts_ns": 1}], 0, 10)


def test_the_archive_decode_is_signed_exact_and_checks_whole_units() -> None:
    raw = -5 * 10**14  # -0.05 at 10^16
    encoded = raw.to_bytes(16, "little", signed=True)
    assert fixed_raw(encoded) == raw
    assert decode_fixed(raw) == Decimal("-0.05")
    assert (whole_at(raw, 2), whole_at(raw, 1)) == (True, False)


def test_the_archive_reads_each_files_precision_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _write_day(tmp_path, monkeypatch, _Scenario())
    values = ParquetArchive(catalog).trade_values(_BTC, _D0, _D0 + 86_400 * _NS)
    decoded = {t.trade_id: t for t in values.hour(hour_of(_T1.ts_ns))}
    assert decoded["t1"].price == Decimal("84000.10")
    assert decoded["t1"].size == Decimal("0.010")
    assert decoded["t2"].side == SELLER
    assert all(t.on_grid for t in decoded.values())
    assert values.first_ts_event == _T1.ts_ns


def test_reference_copies_merge_to_the_first_ws_copy_and_flag_a_disagreement() -> None:
    ws = _ref((5, 30, 0), "1.0", "1", BUYER)
    later_ws = replace(ws, order=(5, 40, 0))
    poll = replace(ws, via_rest=True, order=(5, 99, 0), price=Decimal("1.00"))
    merged = merge_reference([later_ws, poll, ws])[ws.trade_id]
    assert (merged.trade, merged.ws_recv_ns, merged.conflict) == (ws, 30, False)
    conflicting = merge_reference([ws, replace(poll, size=Decimal(2))])[ws.trade_id]
    assert conflicting.conflict is True


def test_latency_quantiles_are_nearest_rank_over_whole_milliseconds() -> None:
    histogram = LatencyHistogram({-3: 1, 0: 97, 7: 1, 40: 1})
    assert histogram.summary() == {
        "count": 100,
        "min_ms": -3,
        "p50_ms": 0,
        "p99_ms": 7,
        "max_ms": 40,
    }


def test_a_rest_only_trade_folds_after_a_same_millisecond_ws_trade() -> None:
    """
    The `fold_second` Known limit, pinned: a REST-only trade is ordered by its poll's `recv_ns`,
    so in its millisecond it closes the second whatever the venue's own order was.
    """
    ws = _ref((5, 30, 0), "10.0", "1", BUYER)
    rest_only = replace(_ref((5, 99, 0), "10.1", "1", SELLER, via_rest=True), trade_id="r")
    merged = merge_reference([rest_only, ws])
    folded = fold_second([m.trade for m in merged.values()], 1, 0)
    assert merged["r"].ws_recv_ns is None
    assert (folded.open_price, folded.close_price) == (100, 101)


def _second(row: TradeColumns, archived: list[ReferenceTrade], discrepancy: str) -> SecondFacts:
    return SecondFacts(
        rows=[StoredRow(0, 0, row, 0)],
        reference=[_ref((5, 30, 0), "10", "1", BUYER)],
        archived=archived,
        rebuild_exempt=False,
        run_covers=False,
        discrepancy=discrepancy,
    )


@pytest.mark.parametrize(
    ("row", "stage", "verdict"),
    [
        (
            _EMPTY,
            "rebuilt",
            "explained_loss",
        ),  # the row is the archive's fold: the loss explains it
        (TradeColumns(11, 11, 11, 11, 1, 0, 1, 0), "rebuilt", "rebuild_mismatch"),
        (TradeColumns(11, 11, 11, 11, 1, 0, 1, 0), "live", "live_provisional"),
    ],
)
def test_an_explained_second_passes_only_when_its_row_is_the_archives_fold(
    row: TradeColumns, stage: str, verdict: str
) -> None:
    """The reference has one trade the archive explainably lacks (arc empty != ref)."""
    assert classify_second(_second(row, [], EXPLAINED_ONLY), stage) == verdict


def test_an_unexplained_archive_difference_fails_whatever_the_row() -> None:
    assert classify_second(_second(_EMPTY, [], "unexplained"), "live") == "archive_differs"


def _ref_id(trade_id: str, ts_ns: int) -> ReferenceId:
    trade = ReferenceTrade(
        _BTC, trade_id, ts_ns, False, Decimal(1), Decimal(1), BUYER, "Buy", (ts_ns, ts_ns, 0)
    )
    return ReferenceId(trade, ts_ns, False)


def _archived(trade_id: str, ts_event: int, ts_init: int) -> ArchivedTrade:
    order = (ts_event, ts_init, 0)
    return ArchivedTrade(trade_id, Decimal(1), Decimal(1), BUYER, ts_event, ts_init, True, order)


_NO_CONTEXT = IdContext(frozenset(), Intervals.of([]))


def test_failing_id_examples_follow_venue_time_not_set_order() -> None:
    reference = {f"m{n}": _ref_id(f"m{n}", _S1 * _NS + (9 - n) * _MS) for n in range(8)}
    extra = [_archived(f"x{n}", _S2 * _NS + (9 - n) * _MS, _S2 * _NS) for n in range(3)]
    ids = compare_ids(reference, extra, _NO_CONTEXT, Explanations.of([]))
    assert ids.counts.examples == tuple(f"missing_unexplained:m{n}" for n in (7, 6, 5, 4, 3))


def test_an_implausible_latency_fails_its_id_and_its_second() -> None:
    ts = _S1 * _NS
    late = _archived("t", ts, ts + MAX_PLAUSIBLE_LATENCY_NS + _MS)
    ids = compare_ids({"t": _ref_id("t", ts)}, [late], _NO_CONTEXT, Explanations.of([]))
    assert (ids.counts.implausible_latency, ids.counts.failing) == (1, 1)
    assert ids.failed_seconds == frozenset({_S1})


def test_an_id_first_seen_in_a_replay_inside_a_recorder_gap_is_no_latency_sample() -> None:
    """Hyperliquid's subscribe answers with recent trades: the recorder's copy is the reconnect."""
    ts = _S1 * _NS
    replayed = _archived("t", ts, ts + MAX_PLAUSIBLE_LATENCY_NS + _MS)
    in_gap = IdContext(frozenset(), Intervals.of([(ts - _NS, ts + _NS)]))
    ids = compare_ids({"t": _ref_id("t", ts)}, [replayed], in_gap, Explanations.of([]))
    assert (ids.counts.matched, ids.counts.implausible_latency, ids.latency.count) == (1, 0, 0)


def test_a_window_of_hours_with_a_step_is_refused() -> None:
    whole = day_hours(_DAY)
    with pytest.raises(ValueError, match="not a window"):
        check_hours(
            cast(Any, None), _DAY, range(whole.start, whole.stop, 2), cast(Any, None), "live"
        )
