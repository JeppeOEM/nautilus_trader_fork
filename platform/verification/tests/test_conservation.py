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
`python3 -m verification.conservation` end to end (Story 31.2): reference lines in the recorder's
own raw format, a real catalog written by `ParquetDataCatalog.write_data` (real `TradeTick`s and
`DydxSecondSnapshot`s), and a coverage record that explains every second without a row. The clean
day passes exactly; each planted defect makes a count non-zero and the exit status 1.
"""

import json
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification import conservation
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import WINDOW_SETTLE_NS
from verification.application.conservation import day_start_ns
from verification.domain.conservation import GAP_MARKER_MARGIN_NS
from verification.domain.conservation import Explanations
from verification.domain.conservation import Intervals
from verification.domain.conservation import MalformedLine
from verification.domain.conservation import TradeChannel
from verification.domain.conservation import TradeWindow
from verification.domain.conservation import parse_coverage_line
from verification.domain.conservation import parse_gap_marker_line
from verification.domain.conservation import reference_trades
from verification.infrastructure.raw_store import channel_file
from verification.infrastructure.raw_store import encode_line
from verification.infrastructure.raw_store import hour_of


_DAY = date(2026, 9, 29)
_D0 = day_start_ns(_DAY)
_S0 = _D0 // 1_000_000_000
_NS = 1_000_000_000
_MS = 1_000_000
_BTC = "BTCUSDT-LINEAR.BYBIT"
_SOL = "SOL-USD-PERP.HYPERLIQUID"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Trade A: WS and REST, 10:00:00.500. B: 10:59:59.900, received 11:00:00.100 (the hour after its
# own). C: REST only, 09:30:00.000. Each is `(trade id, venue time ns)`.
_A = ("exec-A", _D0 + 10 * 3600 * _NS + 500 * _MS)
_B = ("exec-B", _D0 + 11 * 3600 * _NS - 100 * _MS)
_C = ("exec-C", _D0 + 9 * 3600 * _NS + 1800 * _NS)
_ROW_SECONDS = (_S0 + 36_000, _S0 + 36_001)
_NOW = _D0 + 2 * 86_400 * _NS  # the day is closed


def _connection(ts_ns: int) -> dict[str, object]:
    return {"kind": "connection", "event": "open", "ts_ns": ts_ns, "endpoint": "linear"}


def _every_hour() -> list[dict[str, object]]:
    """Return a connection line in each hour of the day, so no raw hour of it is missing."""
    return [_connection(_D0 + hour * 3600 * _NS + _NS) for hour in range(24)]


def _main(argv: list[str], now_ns: int = _NOW) -> int:
    return conservation.main(argv, clock=lambda: now_ns)


def _bybit_frame(trade: tuple[str, int], recv_ns: int) -> dict[str, object]:
    trade_id, ts_ns = trade
    item = {"T": ts_ns // _MS, "s": "BTCUSDT", "S": "Buy", "v": "0.001", "p": "84034.30"}
    raw = {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "ts": ts_ns // _MS}
    raw["data"] = [item | {"L": "PlusTick", "i": trade_id, "BT": False, "RPI": False, "seq": 1}]
    return {"kind": "frame", "recv_ns": recv_ns, "endpoint": "linear", "raw": json.dumps(raw)}


def _bybit_rest(trades: Iterable[tuple[str, int]], recv_ns: int) -> dict[str, object]:
    rows = [
        {"execId": trade_id, "symbol": "BTCUSDT", "price": "84034.40", "size": "0.001"}
        | {"side": "Buy", "time": str(ts_ns // _MS), "isBlockTrade": False, "seq": "1"}
        for trade_id, ts_ns in trades
    ]
    raw = {"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": rows}}
    request = "/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=1000"
    line = {"kind": "rest", "sent_ns": recv_ns - _MS, "endpoint": "linear", "request": request}
    return line | {"recv_ns": recv_ns, "status": 200, "raw": json.dumps(raw)}


def _write_raw(root: Path, venue: str, channel: str, lines: list[dict[str, object]]) -> None:
    """File lines by receive hour, exactly as the recorder's store does."""
    by_hour: dict[int, list[dict[str, object]]] = {}
    for line in lines:
        stamp = line.get("recv_ns", line.get("ts_ns"))
        assert isinstance(stamp, int)
        by_hour.setdefault(hour_of(stamp), []).append(line)
    for hour, hour_lines in by_hour.items():
        path = channel_file(root, venue, channel, hour)
        path.parent.mkdir(parents=True, exist_ok=True)
        with pa.CompressedOutputStream(str(path), "zstd") as stream:
            for line in hour_lines:
                stream.write(encode_line(line))


def _tick(iid: str, trade: tuple[str, int], ts_init_offset: int = 5 * _MS) -> TradeTick:
    trade_id, ts_ns = trade
    return TradeTick(
        InstrumentId.from_str(iid),
        Price.from_str("84034.30"),
        Quantity.from_str("0.001"),
        AggressorSide.BUYER,
        TradeId(trade_id),
        ts_ns,
        ts_ns + ts_init_offset,
    )


def _snapshot(iid: str, second: int, ts_init_offset: int = 0) -> Any:
    ts_event = second * _NS + _NS // 2
    return make_snapshot(
        instrument_id=iid,
        bid_prices=[1.0],
        bid_sizes=[1.0],
        ask_prices=[2.0],
        ask_sizes=[1.0],
        ts_event=ts_event,
        ts_init=ts_event + ts_init_offset,
    )


def _seconds_line(iid: str, reason: str, first: int, last: int) -> dict[str, object]:
    return {
        "kind": "seconds",
        "instrument_id": iid,
        "reason": reason,
        "first_s": first,
        "last_s": last,
        "count": last - first + 1,
    }


def _clean_runs(iid: str, rows: tuple[int, ...]) -> list[dict[str, object]]:
    """`not_collected` runs over every second of the day except `rows` (ascending)."""
    edges = [_S0 - 1, *rows, _S0 + 86_400]
    return [
        _seconds_line(iid, "not_collected", low + 1, high - 1)
        for low, high in pairwise(edges)
        if high - low > 1
    ]


def _write_jsonl(path: Path, lines: Iterable[Mapping[str, object] | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = [line if isinstance(line, str) else json.dumps(line) for line in lines]
    path.write_text("".join(f"{line}\n" for line in text))


def _bybit_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archived: Iterable[tuple[str, int]] = (_A, _B, _C),
    coverage: Sequence[Mapping[str, object] | str] | None = None,
) -> Path:
    """Write the Bybit scenario and point the environment at it; return the catalog root."""
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    ws = [_connection(_A[1] - _NS), _bybit_frame(_A, _A[1] + 100 * _MS)]
    ws.append(_bybit_frame(_B, _D0 + 11 * 3600 * _NS + 100 * _MS))
    _write_raw(raw, "BYBIT", "linear.publicTrade", [*_every_hour(), *ws])
    rest = [_bybit_rest([_C], _C[1] + 20 * _NS), _bybit_rest([_A], _A[1] + 20 * _NS)]
    _write_raw(raw, "BYBIT", "linear.rest.recent-trade", [*_every_hour(), *rest])
    writer = ParquetDataCatalog(str(catalog))
    catalog.mkdir(parents=True, exist_ok=True)
    ticks = sorted((_tick(_BTC, trade) for trade in archived), key=lambda t: t.ts_init)
    if ticks:
        writer.write_data(ticks)
    writer.write_data([_snapshot(_BTC, second) for second in _ROW_SECONDS])
    lines = _clean_runs(_BTC, _ROW_SECONDS) if coverage is None else coverage
    _write_jsonl(tmp_path / "coverage" / "bybit.jsonl", lines)
    plan = tmp_path / "bybit.toml"
    plan.write_text(f'instruments = ["{_BTC}"]\n')
    monkeypatch.setenv("VERIFY_DATA_DIR", str(raw))
    monkeypatch.setenv("CATALOG_PATH", str(catalog))
    monkeypatch.setenv("BYBIT_COLLECTOR_CONFIG", str(plan))
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    return catalog


def _run(capsys: pytest.CaptureFixture[str], venue: str = "BYBIT") -> tuple[int, dict[str, Any]]:
    status = _main(["--venue", venue, "--day", _DAY.isoformat(), "--json"])
    return status, json.loads(capsys.readouterr().out)


def _only(report: dict[str, Any]) -> dict[str, Any]:
    (instrument,) = report["instruments"]
    return instrument


def test_a_clean_day_explains_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    status, report = _run(capsys)
    trades, seconds = _only(report)["trades"], _only(report)["seconds"]
    assert status == 0
    assert report["passed"] is True
    assert (_only(report)["passed"], _only(report)["failing"]) == (True, 0)
    assert (trades["seen"], trades["archived"], trades["unexplained"]) == (3, 3, 0)
    assert (trades["archived_not_seen"], trades["archived_twice"]) == (0, 0)
    assert seconds["rows"] == 2
    assert seconds["explained_by_reason"] == {"not_collected": 86_398}
    assert (seconds["unexplained"], seconds["duplicate_rows"], seconds["row_and_reason"]) == (
        0,
        0,
        0,
    )


def test_a_rest_only_id_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    _, report = _run(capsys)
    assert _only(report)["trades"]["rest_only"] == 1


def test_a_deleted_archived_trade_is_unexplained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch, archived=(_B, _C))
    status, report = _run(capsys)
    trades = _only(report)["trades"]
    assert status == 1
    assert (trades["unexplained"], trades["examples_unexplained"]) == (1, ["exec-A"])
    # Story 31.11: the day verdict reads this count; `passed` is exactly `failing == 0`.
    assert (_only(report)["passed"], _only(report)["failing"]) == (False, 1)


def test_a_trade_received_in_the_next_hour_is_counted_in_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B's venue time is 10:59:59.9 and its frame is filed in hour 11: still seen, so missed."""
    _bybit_day(tmp_path, monkeypatch, archived=(_A, _C))
    status, report = _run(capsys)
    assert status == 1
    assert _only(report)["trades"]["examples_unexplained"] == ["exec-B"]


def test_a_deleted_coverage_line_leaves_seconds_unexplained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs: list[Mapping[str, object] | str] = list(_clean_runs(_BTC, _ROW_SECONDS))
    _bybit_day(tmp_path, monkeypatch, coverage=runs[1:])
    status, report = _run(capsys)
    seconds = _only(report)["seconds"]
    assert status == 1
    assert seconds["unexplained"] == 36_000
    assert seconds["examples_unexplained"] == [_S0, _S0 + 1, _S0 + 2, _S0 + 3, _S0 + 4]


def test_a_dropped_trade_inside_a_ledgered_range_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    dropped: dict[str, object] = {
        "kind": "trades_dropped",
        "instrument_id": _BTC,
        "reason": "stale",
    }
    dropped |= {"first_ns": _A[1] - _NS, "last_ns": _A[1], "count": 1}
    _bybit_day(
        tmp_path,
        monkeypatch,
        archived=(_B, _C),
        coverage=[*_clean_runs(_BTC, _ROW_SECONDS), dropped],
    )
    status, report = _run(capsys)
    trades = _only(report)["trades"]
    assert status == 0
    assert (trades["ledgered_unrecoverable"], trades["unexplained"]) == (1, 0)


def test_an_unrecoverable_window_explains_a_missing_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    window: dict[str, object] = {
        "kind": "trades_unrecoverable",
        "instrument_id": _BTC,
        "reason": "depth",
    }
    window |= {"from_ns": _C[1] - _NS, "to_ns": _C[1] + _NS}
    coverage = [*_clean_runs(_BTC, _ROW_SECONDS), window]
    _bybit_day(tmp_path, monkeypatch, archived=(_A, _B), coverage=coverage)
    status, report = _run(capsys)
    assert status == 0
    assert _only(report)["trades"]["ledgered_unrecoverable"] == 1


@pytest.mark.parametrize(
    ("after_trade_ns", "unexplained"),
    [(30 * _NS, 0), (GAP_MARKER_MARGIN_NS, 0), (GAP_MARKER_MARGIN_NS + 1, 1)],
)
def test_an_archive_gap_marker_explains_trades_up_to_the_margin_before_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    after_trade_ns: int,
    unexplained: int,
) -> None:
    """A marker spans `ts_init`: it covers a trade whose `ts_event` precedes it by <= 60 s."""
    catalog = _bybit_day(tmp_path, monkeypatch, archived=(_B, _C))
    start = _A[1] + after_trade_ns
    marker = {"instrument_id": _BTC, "from_ns": start, "to_ns": start + 10 * _NS}
    _write_jsonl(
        catalog / "_archive_gaps" / f"{_BTC}.jsonl",
        [marker | {"reason": "write_failed", "count": 1}],
    )
    _, report = _run(capsys)
    assert _only(report)["trades"]["unexplained"] == unexplained


def test_a_backfilled_id_is_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    backfill: dict[str, object] = {"kind": "trades_backfilled", "instrument_id": _BTC, "count": 1}
    coverage = [*_clean_runs(_BTC, _ROW_SECONDS), backfill | {"trade_ids": ["exec-C"]}]
    _bybit_day(tmp_path, monkeypatch, coverage=coverage)
    status, report = _run(capsys)
    assert status == 0
    assert _only(report)["trades"]["backfilled"] == 1


def test_a_trade_archived_twice_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _bybit_day(tmp_path, monkeypatch)
    replay = _tick(_BTC, _A, ts_init_offset=2 * 3600 * _NS)  # a later copy of an archived id
    ParquetDataCatalog(str(catalog)).write_data([replay])
    status, report = _run(capsys)
    trades = _only(report)["trades"]
    assert status == 1
    assert (trades["archived_twice"], trades["unexplained"]) == (1, 0)


def test_a_second_with_two_rows_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _bybit_day(tmp_path, monkeypatch)
    ParquetDataCatalog(str(catalog)).write_data([_snapshot(_BTC, _ROW_SECONDS[0], 10 * _NS)])
    status, report = _run(capsys)
    assert status == 1
    assert _only(report)["seconds"]["duplicate_rows"] == 1


def test_a_second_with_a_row_and_a_reason_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doubled = _seconds_line(_BTC, "stale", _ROW_SECONDS[0], _ROW_SECONDS[0])
    _bybit_day(tmp_path, monkeypatch, coverage=[*_clean_runs(_BTC, _ROW_SECONDS), doubled])
    status, report = _run(capsys)
    assert status == 1
    assert _only(report)["seconds"]["row_and_reason"] == 1


@pytest.mark.parametrize(
    "bad_line",
    [
        "not json",
        '{"kind": "seconds_v2", "instrument_id": "BTCUSDT-LINEAR.BYBIT"}',
        json.dumps(_seconds_line(_BTC, "stale", 10, 12) | {"count": 2}),
        json.dumps(
            {k: v for k, v in _seconds_line(_BTC, "stale", 10, 12).items() if k != "reason"}
        ),
        "",
    ],
)
def test_a_malformed_coverage_line_is_refused_with_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_line: str
) -> None:
    runs: list[Mapping[str, object] | str] = list(_clean_runs(_BTC, _ROW_SECONDS))
    _bybit_day(tmp_path, monkeypatch, coverage=[runs[0], bad_line, runs[1]])
    with pytest.raises(SystemExit, match=r"bybit\.jsonl:2"):
        _main(["--venue", "BYBIT", "--day", _DAY.isoformat()])


def test_a_missing_raw_root_or_catalog_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat()]
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "nowhere"))
    with pytest.raises(SystemExit, match="CATALOG_PATH"):
        _main(argv)
    monkeypatch.delenv("VERIFY_DATA_DIR")
    with pytest.raises(SystemExit, match="VERIFY_DATA_DIR is required"):
        _main(argv)


def test_a_bad_day_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        _main(["--venue", "BYBIT", "--day", "29-09-2026"])
    assert raised.value.code == 2


def test_the_text_report_names_the_verdict_and_instrument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch, archived=(_B, _C))
    status = _main(["--venue", "BYBIT", "--day", _DAY.isoformat()])
    text = capsys.readouterr().out
    assert status == 1
    assert text.startswith("conservation BYBIT 2026-09-29: FAIL")
    assert "unexplained trade ids: exec-A" in text


def test_a_clean_hyperliquid_day_matches_tid_as_the_trade_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    tid, ts_ns = 794607121811834, _D0 + 5 * 3600 * _NS + 597 * _MS
    item = {"coin": "SOL", "side": "B", "px": "119.83", "sz": "0.1", "time": ts_ns // _MS}
    frame = json.dumps({"channel": "trades", "data": [item | {"hash": "0x0", "tid": tid}]})
    line = {"kind": "frame", "recv_ns": ts_ns + 300 * _MS, "endpoint": "ws", "raw": frame}
    _write_raw(raw, "HYPERLIQUID", "trades", [*_every_hour(), line])
    catalog.mkdir()
    ParquetDataCatalog(str(catalog)).write_data([_tick(_SOL, (str(tid), ts_ns))])
    _write_jsonl(tmp_path / "coverage" / "hyperliquid.jsonl", _clean_runs(_SOL, ()))
    plan = tmp_path / "hl.toml"
    plan.write_text(f'instruments = ["{_SOL}"]\n')
    monkeypatch.setenv("VERIFY_DATA_DIR", str(raw))
    monkeypatch.setenv("CATALOG_PATH", str(catalog))
    monkeypatch.setenv("HYPERLIQUID_COLLECTOR_CONFIG", str(plan))
    status, report = _run(capsys, "HYPERLIQUID")
    assert status == 0
    assert (_only(report)["trades"]["seen"], _only(report)["trades"]["archived"]) == (1, 1)


def test_the_committed_fixtures_parse_into_reference_trades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The recorder's real frames and polls: every instrument is seen, and nothing is archived."""
    ids = [
        "BTCUSDT-LINEAR.BYBIT",
        "ETHUSDT-LINEAR.BYBIT",
        "BTCUSDT-SPOT.BYBIT",
        "ETHUSDT-SPOT.BYBIT",
    ]
    plan = tmp_path / "bybit.toml"
    plan.write_text(f"instruments = {json.dumps(ids)}\n")
    (tmp_path / "catalog").mkdir()
    monkeypatch.setenv("VERIFY_DATA_DIR", str(_FIXTURES))
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "catalog"))
    monkeypatch.setenv("BYBIT_COLLECTOR_CONFIG", str(plan))
    status, report = _run(capsys)
    trades = {entry["instrument_id"]: entry["trades"] for entry in report["instruments"]}
    assert status == 1
    assert report["coverage_present"] is False
    assert all(t["seen"] > 0 and t["unexplained"] == t["seen"] for t in trades.values())
    assert trades["BTCUSDT-LINEAR.BYBIT"]["rest_only"] > 0


def test_a_raw_line_that_is_not_a_json_object_is_refused() -> None:
    channel = TradeChannel("linear.publicTrade", "linear", rest=False)
    with pytest.raises(MalformedLine, match="not a JSON object"):
        reference_trades("BYBIT", channel, [], {})  # type: ignore[arg-type]


def test_intervals_merge_and_contain_closed_spans() -> None:
    spans = Intervals.of([(10, 20), (21, 25), (40, 50), (15, 18)])
    assert (spans.starts, spans.ends) == ((10, 40), (25, 50))
    assert [spans.contains(v) for v in (9, 10, 25, 26, 39, 50, 51)] == [
        False,
        True,
        True,
        False,
        False,
        True,
        False,
    ]


def test_a_gap_marker_is_widened_below_by_the_margin() -> None:
    line = json.dumps(
        {"instrument_id": _BTC, "from_ns": _D0, "to_ns": _D0 + 5, "reason": "x", "count": 1}
    )
    window = parse_gap_marker_line(line, "f:1")
    assert (window.from_ns, window.to_ns) == (_D0 - GAP_MARKER_MARGIN_NS, _D0 + 5)


def test_an_inverted_dropped_range_is_refused() -> None:
    dropped: dict[str, object] = {
        "kind": "trades_dropped",
        "instrument_id": _BTC,
        "reason": "stale",
    }
    dropped |= {"first_ns": 5, "last_ns": 4, "count": 1}
    with pytest.raises(MalformedLine, match="f:7: inverted span"):
        parse_coverage_line(json.dumps(dropped), "f:7")


def _dropped(first_ns: int, last_ns: int, count: int) -> dict[str, object]:
    line: dict[str, object] = {"kind": "trades_dropped", "instrument_id": _BTC, "reason": "stale"}
    return line | {"first_ns": first_ns, "last_ns": last_ns, "count": count}


def test_a_dropped_range_explains_at_most_its_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One recorded drop over two missing trades: the second loss is not explained by it."""
    coverage = [*_clean_runs(_BTC, _ROW_SECONDS), _dropped(_A[1], _B[1], 1)]
    _bybit_day(tmp_path, monkeypatch, archived=(_C,), coverage=coverage)
    status, report = _run(capsys)
    trades = _only(report)["trades"]
    assert status == 1
    assert (trades["ledgered_unrecoverable"], trades["unexplained"]) == (1, 1)
    assert trades["examples_unexplained"] == ["exec-B"]


@pytest.mark.parametrize(("count", "unexplained"), [(1, 1), (2, 0), (0, 0)])
def test_an_archive_gap_marker_explains_at_most_its_count_unless_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    count: int,
    unexplained: int,
) -> None:
    """A `quarantined` marker records count 0 (unknown): it stays uncapped (a Known limit)."""
    catalog = _bybit_day(tmp_path, monkeypatch, archived=(_C,))
    marker = {"instrument_id": _BTC, "from_ns": _A[1], "to_ns": _B[1] + _NS, "reason": "x"}
    _write_jsonl(catalog / "_archive_gaps" / f"{_BTC}.jsonl", [marker | {"count": count}])
    _, report = _run(capsys)
    assert _only(report)["trades"]["unexplained"] == unexplained


@pytest.mark.parametrize("after_midnight_ns", [-1, 0, DAY_SETTLE_NS - 1])
def test_a_day_not_closed_and_settled_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_midnight_ns: int
) -> None:
    """Just after midnight the day's last flush (its rows and coverage runs) is still to come."""
    _bybit_day(tmp_path, monkeypatch)
    now_ns = _D0 + 86_400 * _NS + after_midnight_ns
    with pytest.raises(SystemExit, match="day not closed"):
        _main(["--venue", "BYBIT", "--day", _DAY.isoformat()], now_ns=now_ns)


def test_a_capped_window_is_spent_on_the_trade_only_it_can_explain_last() -> None:
    """Two missing trades, two windows of one each: the wide window must be kept for the late one."""
    wide = TradeWindow(_BTC, "trades_dropped", 0, 100, 1)
    narrow = TradeWindow(_BTC, "trades_dropped", 10, 20, 1)
    unexplained, _ = Explanations.of([wide, narrow]).explain([("early", 15), ("late", 50)])
    assert unexplained == []
    unexplained, _ = Explanations.of([wide, narrow]).explain([("a", 15), ("b", 16), ("c", 50)])
    assert len(unexplained) == 1  # two budgets, three trades: exactly one stays unexplained


def test_a_missing_raw_hour_of_the_day_fails_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    channel_file(tmp_path / "verify", "BYBIT", "linear.publicTrade", hour_of(_D0) + 3).unlink()
    status, report = _run(capsys)
    assert status == 1
    assert report["missing_raw_files"] == ["linear.publicTrade/2026-09-29T03"]
    assert _only(report)["trades"]["unexplained"] == 0


def test_a_missing_coverage_record_fails_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    (tmp_path / "coverage" / "bybit.jsonl").unlink()
    status, report = _run(capsys)
    assert status == 1
    assert report["coverage_present"] is False


def _truncate(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data[:-3])  # cut inside the last zstd frame


def test_a_truncated_neighbour_hour_is_read_and_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    after = channel_file(tmp_path / "verify", "BYBIT", "linear.publicTrade", hour_of(_D0) + 24)
    _write_raw(
        tmp_path / "verify", "BYBIT", "linear.publicTrade", [_connection(_D0 + 86_401 * _NS)]
    )
    _truncate(after)
    status, report = _run(capsys)
    assert status == 0
    assert report["truncated_neighbour_files"] == ["linear.publicTrade/2026-09-30T00"]


def test_a_truncated_hour_of_the_day_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    _truncate(channel_file(tmp_path / "verify", "BYBIT", "linear.publicTrade", hour_of(_A[1])))
    with pytest.raises(SystemExit, match="truncated"):
        _main(["--venue", "BYBIT", "--day", _DAY.isoformat()])


def test_the_coverage_record_lies_beside_the_resolved_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Capture resolves the catalog root first; a symlinked root's coverage is its target's."""
    catalog = _bybit_day(tmp_path, monkeypatch)
    elsewhere = tmp_path / "links"
    elsewhere.mkdir()
    (elsewhere / "catalog").symlink_to(catalog)
    monkeypatch.setenv("CATALOG_PATH", str(elsewhere / "catalog"))
    status, report = _run(capsys)
    assert status == 0
    assert report["coverage_file"] == str(tmp_path.resolve() / "coverage" / "bybit.jsonl")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"p": "8.4e4"}, "`p` '8.4e4' is not a decimal string"),
        ({"p": 84034.3}, "`p` is 84034.3, not str"),
        ({"v": "-0.001"}, "`v` '-0.001' is not a decimal string"),
        ({"S": None}, "`S` is None, not str"),
    ],
)
def test_a_trade_with_a_malformed_price_size_or_side_is_refused(
    change: dict[str, object], message: str
) -> None:
    """Values are the wire's decimal strings, never floats; a trade without a side is refused."""
    frame = _bybit_frame(_A, _A[1] + 100 * _MS)
    raw = json.loads(str(frame["raw"]))
    raw["data"][0] = {k: v for k, v in (raw["data"][0] | change).items() if v is not None}
    channel = TradeChannel("linear.publicTrade", "linear", rest=False)
    with pytest.raises(MalformedLine, match=message.replace("(", r"\(")):
        reference_trades(
            "BYBIT", channel, frame | {"raw": json.dumps(raw)}, {("linear", "BTCUSDT"): _BTC}
        )


def test_a_wire_trade_keeps_its_exact_decimal_values_side_and_order() -> None:
    frame = _bybit_frame(_A, _A[1] + 100 * _MS)
    channel = TradeChannel("linear.publicTrade", "linear", rest=False)
    (trade,) = reference_trades("BYBIT", channel, frame, {("linear", "BTCUSDT"): _BTC})
    assert (trade.price, trade.size) == (Decimal("84034.30"), Decimal("0.001"))
    assert (trade.side, trade.side_token) == (1, "Buy")
    assert trade.order == (_A[1], _A[1] + 100 * _MS, 0)


# Story 31.10's window mode. `_W0` is 10:00:00 of the day: trade A (10:00:00.500) lies in its
# first second, and the two snapshot rows (`_ROW_SECONDS`) in its first two seconds.
_W0 = _S0 + 36_000


def _iso(second: int) -> str:
    return datetime.fromtimestamp(second, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_window(
    capsys: pytest.CaptureFixture[str], first_s: int, end_s: int, now_ns: int = _NOW
) -> tuple[int, dict[str, Any]]:
    argv = ["--venue", "BYBIT", "--start", _iso(first_s), "--end", _iso(end_s), "--json"]
    status = _main(argv, now_ns=now_ns)
    return status, json.loads(capsys.readouterr().out)


def test_a_clean_window_counts_only_its_own_trades_and_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    status, report = _run_window(capsys, _W0, _W0 + 5)
    trades, seconds = _only(report)["trades"], _only(report)["seconds"]
    assert status == 0
    assert (report["start"], report["end"]) == ("2026-09-29T10:00:00Z", "2026-09-29T10:00:05Z")
    assert (trades["seen"], trades["archived"], trades["unexplained"]) == (1, 1, 0)
    assert (seconds["expected"], seconds["rows"]) == (5, 2)
    assert seconds["explained_by_reason"] == {"not_collected": 3}  # the day-long runs, clipped


def test_a_planted_rowless_reasonless_second_in_a_window_is_unexplained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _clean_runs(_BTC, (*_ROW_SECONDS, _W0 + 3))  # leaves 10:00:03 without a run
    _bybit_day(tmp_path, monkeypatch, coverage=runs)
    status, report = _run_window(capsys, _W0, _W0 + 5)
    seconds = _only(report)["seconds"]
    assert status == 1
    assert (seconds["unexplained"], seconds["examples_unexplained"]) == (1, [_W0 + 3])


def test_an_unexplained_second_just_before_the_window_is_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _clean_runs(_BTC, (_W0 - 1, *_ROW_SECONDS))  # 09:59:59 has neither row nor run
    _bybit_day(tmp_path, monkeypatch, coverage=runs)
    status, report = _run_window(capsys, _W0, _W0 + 5)
    assert status == 0
    assert _only(report)["seconds"]["unexplained"] == 0


def test_a_missing_trade_outside_the_window_is_not_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A (10:00:00.5) is not archived: unexplained in a window holding it, absent from the next."""
    _bybit_day(tmp_path, monkeypatch, archived=(_B, _C))
    status, report = _run_window(capsys, _W0 + 1, _W0 + 5)
    assert (status, _only(report)["trades"]["seen"]) == (0, 0)
    status, report = _run_window(capsys, _W0, _W0 + 5)
    assert (status, _only(report)["trades"]["unexplained"]) == (1, 1)


@pytest.mark.parametrize(("first_s", "end_s", "seen"), [(0, 5, 1), (-5, 0, 0)])
def test_a_trade_on_the_window_edge_counts_at_the_start_not_the_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    first_s: int,
    end_s: int,
    seen: int,
) -> None:
    """C's venue time is exactly 09:30:00.000: in `[09:30:00, ...)`, not in `[..., 09:30:00)`."""
    _bybit_day(tmp_path, monkeypatch)
    c_second = _C[1] // _NS
    status, report = _run_window(capsys, c_second + first_s, c_second + end_s)
    assert status == 0
    assert _only(report)["trades"]["seen"] == seen


@pytest.mark.parametrize(
    "now_ns",
    [
        (_W0 + 5) * _NS + WINDOW_SETTLE_NS - 1,  # not settled after its end
        (_W0 + 1800) * _NS,  # settled, but its hour (10:00) is still being recorded
        (_W0 + 3600) * _NS - 1,
    ],
)
def test_an_unsettled_window_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, now_ns: int
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    argv = ["--venue", "BYBIT", "--start", _iso(_W0), "--end", _iso(_W0 + 5)]
    with pytest.raises(SystemExit, match="window not settled"):
        _main(argv, now_ns=now_ns)


def test_a_window_is_judged_once_its_hour_ended_and_it_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    status, _ = _run_window(capsys, _W0, _W0 + 5, now_ns=(_W0 + 3600) * _NS)
    assert status == 0


def test_a_day_report_is_unchanged_by_the_window_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    _, report = _run(capsys)
    assert "start" not in report
    assert "end" not in report
    assert report["day"] == "2026-09-29"
    assert _only(report)["seconds"]["expected"] == 86_400


def test_a_window_reads_the_missing_raw_hours_it_touches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _bybit_day(tmp_path, monkeypatch)
    channel_file(tmp_path / "verify", "BYBIT", "linear.publicTrade", hour_of(_D0) + 3).unlink()
    status, _ = _run_window(capsys, _W0, _W0 + 5)  # hour 03 is not touched
    assert status == 0
    channel_file(tmp_path / "verify", "BYBIT", "linear.publicTrade", hour_of(_D0) + 10).unlink()
    status, report = _run_window(capsys, _W0, _W0 + 5)
    assert status == 1
    assert report["missing_raw_files"] == ["linear.publicTrade/2026-09-29T10"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--start", "2026-09-29T10:00:00Z"],  # no --end
        ["--start", "2026-09-29T10:00:00.5Z", "--end", "2026-09-29T10:00:05Z"],
        ["--start", "2026-09-29T10:00:00+02:00", "--end", "2026-09-29T10:00:05Z"],
        ["--start", "2026-09-29T10:00:05Z", "--end", "2026-09-29T10:00:05Z"],
        ["--day", "2026-09-29", "--start", "2026-09-29T10:00:00Z"],
        ["--day", "2026-09-29", "--end", "2026-09-29T10:00:00Z"],
    ],
)
def test_a_bad_window_is_a_usage_error(extra: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        _main(["--venue", "BYBIT", *extra])
    assert raised.value.code == 2
