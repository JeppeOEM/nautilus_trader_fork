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
"""rebuild_seconds on a real tmp `ParquetDataCatalog` with real snapshots and trades (TEST-01/03)."""

import glob
import json
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from candles.application.rebuild import parse_date_ns
from kernel.fold import fold_trades
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from archive.application.rebuild_day import DayReport
from archive.application.rebuild_day import RefusedError
from archive.application.rebuild_day import covered_from
from archive.application.rebuild_day import rebuild_day
from archive.application.repair import second_snapshots
from archive.infrastructure import catalog_files
from archive.infrastructure.gap_markers import GapMarkerFiles
from archive.infrastructure.gap_markers import record_gap
from archive.infrastructure.maintenance_lock import maintenance
from archive.rebuild_seconds import main
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"
_DAY = "2026-09-10"
_D0 = parse_date_ns(_DAY)
_S = 1_000_000_000
_TRADE_COLUMNS = (
    "buy_volume",
    "sell_volume",
    "buy_count",
    "sell_count",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
)


def _at(second: float) -> int:
    return _D0 + int(second * _S)


def _trade(
    n: int, event_s: float, init_s: float, price: str = "100.0", size: str = "0.50"
) -> TradeTick:
    return TradeTick(
        InstrumentId.from_str(_IID),
        Price.from_str(price),
        Quantity.from_str(size),
        AggressorSide.BUYER,
        TradeId(str(n)),
        _at(event_s),
        _at(init_s),
    )


def _snap(second: int, live_trades: list[TradeTick], offset: float = 0.5) -> DydxSecondSnapshot:
    """Build the row the live loop wrote at mid-second, holding the trades that *arrived* by then."""
    ts = _at(second + offset)
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[99.5 - second / 100],
        bid_sizes=[1.0],
        ask_prices=[100.5],
        ask_sizes=[2.0],
        **fold_trades(live_trades).snapshot_values()._asdict(),
        ts_event=ts,
        ts_init=ts,
    )


def _catalog(tmp_path: Path) -> ParquetDataCatalog:
    return ParquetDataCatalog(str(tmp_path))


def _rows(tmp_path: Path) -> dict[int, DydxSecondSnapshot]:
    snaps = second_snapshots(str(tmp_path), _IID, _D0, _D0 + 86_400 * _S)
    return {(s.ts_event - _D0) // _S: s for s in snaps}


def _rebuild(tmp_path: Path, apply: bool = True) -> DayReport:
    """Rebuild the day as the CLI wires it: `CatalogFiles` under the maintenance flock."""
    with maintenance(tmp_path) as writer:
        assert writer is not None
        return rebuild_day(str(tmp_path), _IID, _D0, writer, GapMarkerFiles(tmp_path), apply)


def _snapshot_files(tmp_path: Path) -> list[str]:
    return sorted(glob.glob(str(tmp_path / "data" / "custom_dydx_second_snapshot" / _IID / "*")))


def _late_trade_day(tmp_path: Path) -> tuple[TradeTick, TradeTick]:
    """
    Second 50 holds an on-time trade (the archive starts there); trade `late` happened in second
    100 but arrived 2.3 s later, so the live loop folded it into second 102's row.
    """
    early = _trade(1, 50.2, 50.3, "101.0")
    late = _trade(2, 100.2, 102.3, "100.0", "0.25")
    catalog = _catalog(tmp_path)
    catalog.write_data(
        [_snap(s, [early] if s == 50 else [late] if s == 102 else []) for s in range(50, 106)]
    )
    catalog.write_data([early])
    catalog.write_data([late])
    return early, late


def test_late_trade_moves_to_its_exchange_second_and_the_arrival_row_is_cleared(
    tmp_path: Path,
) -> None:
    _, late = _late_trade_day(tmp_path)
    report = _rebuild(tmp_path)
    rows = _rows(tmp_path)
    assert (rows[100].open_price, rows[100].buy_volume, rows[100].buy_count) == (100.0, 0.25, 1)
    assert (rows[102].open_price, rows[102].buy_volume, rows[102].buy_count) == (None, 0.0, 0)
    assert rows[50].open_price == 101.0  # an on-time trade is where it was
    assert (report.rebuilt, report.changed, report.without_trades) == (56, 2, 54)
    assert report.files_rewritten == 1
    assert late.ts_event // _S != late.ts_init // _S


def test_rerun_changes_nothing_and_writes_no_file(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    _rebuild(tmp_path)
    before = [(Path(f).stat().st_mtime_ns, Path(f).read_bytes()) for f in _snapshot_files(tmp_path)]
    report = _rebuild(tmp_path)
    assert (report.changed, report.files_rewritten) == (0, 0)
    assert [
        (Path(f).stat().st_mtime_ns, Path(f).read_bytes()) for f in _snapshot_files(tmp_path)
    ] == before


def test_book_columns_timestamps_and_schema_are_untouched(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    (path,) = _snapshot_files(tmp_path)
    before = pq.read_table(path)
    _rebuild(tmp_path)
    after = pq.read_table(path)
    untouched = [c for c in before.column_names if c not in _TRADE_COLUMNS]
    assert after.select(untouched).equals(before.select(untouched))
    assert after.schema.equals(before.schema, check_metadata=True)
    assert pq.read_schema(path).equals(before.schema, check_metadata=True)
    assert not glob.glob(path + "*.tmp")


def test_report_only_writes_nothing(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    (path,) = _snapshot_files(tmp_path)
    before = Path(path).read_bytes()
    report = _rebuild(tmp_path, apply=False)
    assert report.changed == 2
    assert report.files_rewritten == 0
    assert Path(path).read_bytes() == before


def test_rows_before_the_archive_keep_live_values_and_count_not_covered(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    before_archive = _trade(9, 10.2, 10.3, "105.0")  # folded live, never archived
    catalog.write_data([_snap(s, [before_archive] if s == 10 else []) for s in range(10, 20)])
    catalog.write_data([_trade(1, 15.2, 15.3)])  # the archive starts at 15.3
    report = _rebuild(tmp_path)
    assert _rows(tmp_path)[10].open_price == 105.0
    assert (report.not_covered, report.rebuilt) == (5, 5)  # rows 10..14; 15.5 > 15.3 is covered


def test_orphan_trades_and_duplicates_are_counted_never_dropped_silently(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    once = _trade(1, 30.2, 30.3)
    catalog.write_data([_snap(s, [once] if s == 30 else []) for s in range(30, 35)])
    catalog.write_data([once])
    catalog.write_data([_trade(1, 30.2, 31.0)])  # the same trade replayed after a restart
    catalog.write_data([_trade(2, 40.1, 40.2), _trade(3, 40.7, 40.8), _trade(4, 41.5, 41.6)])
    report = _rebuild(tmp_path)
    assert (report.duplicates, report.orphan_trades, report.orphan_seconds) == (1, 3, 2)
    assert _rows(tmp_path)[30].buy_count == 1


def test_open_day_is_refused(tmp_path: Path) -> None:
    error_ledger.reset()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert main(["--catalog", str(tmp_path), "--day", today, "--apply"]) == 1
    assert error_ledger.counts() == {"rebuild.open_day": 1}


def test_two_rows_in_one_second_refuse_the_instrument_day(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog = _catalog(tmp_path)
    catalog.write_data([_snap(60, [], offset=0.2), _snap(60, [], offset=0.7), _snap(61, [])])
    catalog.write_data([_trade(1, 60.3, 60.4)])
    before = [Path(f).read_bytes() for f in _snapshot_files(tmp_path)]
    assert main(["--catalog", str(tmp_path), "--day", _DAY, "--apply"]) == 2
    assert error_ledger.counts() == {"rebuild.duplicate_second": 1}
    assert [Path(f).read_bytes() for f in _snapshot_files(tmp_path)] == before


def test_mixed_schema_day_is_refused_and_files_unchanged(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog = _catalog(tmp_path)
    catalog.write_data([_snap(s, []) for s in range(70, 72)])
    catalog.write_data([_trade(1, 70.3, 70.4)])
    (first,) = _snapshot_files(tmp_path)
    old_schema = pq.read_table(first).drop_columns(["open_price", "high_price"])
    pq.write_table(
        old_schema, first.replace("T00-01-10", "T00-02-10").replace("T00-01-11", "T00-02-11")
    )
    before = [Path(f).read_bytes() for f in _snapshot_files(tmp_path)]
    assert len(before) == 2
    assert main(["--catalog", str(tmp_path), "--day", _DAY, "--apply"]) == 2
    assert error_ledger.counts() == {"rebuild.mixed_schema": 1}
    assert [Path(f).read_bytes() for f in _snapshot_files(tmp_path)] == before


def test_cli_rebuilds_only_the_requested_venue(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    assert main(["--catalog", str(tmp_path), "--day", _DAY, "--venue", "DYDX", "--apply"]) == 0
    assert _rows(tmp_path)[102].open_price == 100.0  # BYBIT untouched
    assert main(["--catalog", str(tmp_path), "--day", _DAY, "--venue", "BYBIT", "--apply"]) == 0
    assert _rows(tmp_path)[102].open_price is None


def test_the_first_archived_trade_is_covered_even_when_it_arrived_seconds_later(
    tmp_path: Path,
) -> None:
    # Regression (live dYdX run, 2026-09-21): the archive's first trade happened 1.3 s before it
    # arrived; judging coverage by the file name (a ts_init) left that trade's own row uncovered.
    catalog = _catalog(tmp_path)
    first = _trade(1, 5.2, 6.5)
    catalog.write_data([_snap(s, [first] if s == 6 else []) for s in range(3, 9)])
    catalog.write_data([first])
    report = _rebuild(tmp_path)
    rows = _rows(tmp_path)
    assert (rows[5].open_price, rows[6].open_price) == (100.0, None)
    assert (report.not_covered, report.orphan_trades) == (2, 0)  # rows 3 and 4


def test_rows_in_an_archive_gap_keep_live_values_and_their_trades_are_orphans(
    tmp_path: Path,
) -> None:
    # E.g. a pruned verified day between an older unverified day and this one: `covered_from`
    # still reaches back, but the archive no longer holds the trades these rows were folded from.
    catalog = _catalog(tmp_path)
    kept_trade = _trade(1, 20.2, 20.3, "101.0")
    catalog.write_data([_snap(s, [kept_trade] if s == 20 else []) for s in range(20, 26)])
    catalog.write_data([kept_trade])
    record_gap(str(tmp_path), _IID, _at(20), _at(22.9), "pruned", 0)
    report = _rebuild(tmp_path)
    assert _rows(tmp_path)[20].open_price == 101.0
    # Rows 20..22 sit inside the gap: kept, and counted apart from pre-archive rows.
    assert (report.in_gap, report.not_covered, report.rebuilt, report.orphan_trades) == (3, 0, 3, 1)
    assert "in gap 3" in report.line(_DAY)


def test_a_malformed_gap_marker_refuses_the_instrument_day(tmp_path: Path) -> None:
    error_ledger.reset()
    _late_trade_day(tmp_path)
    (tmp_path / "_archive_gaps").mkdir()
    (tmp_path / "_archive_gaps" / f"{_IID}.jsonl").write_text("{not json\n")
    assert main(["--catalog", str(tmp_path), "--day", _DAY, "--apply"]) == 2
    assert error_ledger.counts() == {"rebuild.error": 1}
    assert _rows(tmp_path)[102].open_price == 100.0  # untouched


def test_trades_of_an_instrument_without_snapshots_that_day_are_orphans(tmp_path: Path) -> None:
    _catalog(tmp_path).write_data([_trade(1, 40.1, 40.2), _trade(2, 41.1, 41.2)])
    report = _rebuild(tmp_path)
    assert (report.orphan_trades, report.orphan_seconds) == (2, 2)
    assert main(["--catalog", str(tmp_path), "--day", _DAY]) == 0  # found via trade_tick alone


def test_a_zero_row_trade_file_proves_no_coverage(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.write_data([_trade(1, 5.2, 5.3)])
    (path,) = glob.glob(str(tmp_path / "data" / "trade_tick" / _IID / "*.parquet"))
    empty = Path(path).with_name(
        "2026-09-10T00-00-01-000000000Z_2026-09-10T00-00-02-000000000Z.parquet"
    )
    pq.write_table(pq.read_table(path).slice(0, 0), str(empty))  # e.g. a torn flush
    assert covered_from(str(tmp_path), _IID) == _at(5)  # the empty file is skipped, no crash
    Path(path).unlink()
    assert covered_from(str(tmp_path), _IID) is None


def test_apply_on_the_open_day_is_refused_even_with_include_open_day(tmp_path: Path) -> None:
    error_ledger.reset()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    args = ["--catalog", str(tmp_path), "--day", today, "--apply", "--include-open-day"]
    assert main(args) == 1
    assert error_ledger.counts() == {"rebuild.open_day": 1}


def test_include_open_day_without_apply_only_reports(tmp_path: Path) -> None:
    error_ledger.reset()
    today = time.strftime("%Y-%m-%d", time.gmtime())
    assert main(["--catalog", str(tmp_path), "--day", today, "--include-open-day"]) == 0
    assert error_ledger.counts() == {}


def test_the_result_file_names_the_rebuilt_and_the_refused(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    catalog.write_data([_snap(60, [], offset=0.2), _snap(60, [], offset=0.7)])  # refused
    other = "ETHUSDT-LINEAR.BYBIT"
    result = tmp_path / "result.json"
    args = ["--catalog", str(tmp_path), "--day", _DAY, "--venue", "BYBIT"]
    args += ["--instrument", _IID, "--instrument", other, "--apply", "--result-file", str(result)]
    assert main(args) == 2
    # `other` has no snapshot row on the day: nothing was rebuilt, so it is in neither list.
    assert json.loads(result.read_text()) == {
        "venue": "BYBIT",
        "day": _DAY,
        "rebuilt": [],
        "refused": [_IID],
    }


def test_a_result_file_needs_apply(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--catalog", str(tmp_path), "--day", _DAY, "--result-file", str(tmp_path / "r")])


def test_a_change_to_a_row_of_the_open_day_refuses_the_instrument_day_before_any_write(
    tmp_path: Path,
) -> None:
    _late_trade_day(tmp_path)
    before = [Path(f).read_bytes() for f in _snapshot_files(tmp_path)]
    with maintenance(tmp_path, now_ns=lambda: _D0 + 3_600 * _S) as writer:  # "today" is _DAY
        assert writer is not None
        with pytest.raises(RefusedError) as refused:
            rebuild_day(str(tmp_path), _IID, _D0, writer, GapMarkerFiles(tmp_path), True)
    assert refused.value.site == "rebuild.open_day"
    assert [Path(f).read_bytes() for f in _snapshot_files(tmp_path)] == before
    assert not glob.glob(str(tmp_path / "data" / "*" / "*" / "*.tmp"))


def test_a_result_file_needs_a_venue(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(
            [
                "--catalog",
                str(tmp_path),
                "--day",
                _DAY,
                "--apply",
                "--result-file",
                str(tmp_path / "r"),
            ]
        )


def test_the_result_file_carries_the_normalized_day(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    result = tmp_path / "result.json"
    args = ["--catalog", str(tmp_path), "--day", "2026-9-10", "--venue", "BYBIT", "--apply"]
    assert main([*args, "--result-file", str(result)]) == 0
    assert json.loads(result.read_text()) == {
        "venue": "BYBIT",
        "day": "2026-09-10",
        "rebuilt": [_IID],
        "refused": [],
    }


# -- the midnight files (Story 25.1 resolution): B crosses midnight, C starts after it -------------

_D1 = _D0 + 86_400 * _S  # the open day at the nightly's 00:30


def _row(ts_event: int, ts_init: int, trades: list[TradeTick]) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[99.5],
        bid_sizes=[1.0],
        ask_prices=[100.5],
        ask_sizes=[2.0],
        **fold_trades(trades).snapshot_values()._asdict(),
        ts_event=ts_event,
        ts_init=ts_init,
    )


def _midnight_catalog(tmp_path: Path) -> tuple[str, str]:
    """
    Venue-time capture around midnight: file B (`ts_init` 23:59:02 D -> 00:00:01 D+1) holds rows of
    D's seconds 23:58:59..23:59:58; file C (00:00:02.5 -> 00:01:01 D+1) holds D's last row
    (23:59:59, sampled at 00:00:02.5) and D+1's rows 00:00:00..00:00:58. A late trade of 23:59:30
    was folded live into 23:59:33's row; D's last trade (23:59:59) was never folded live; a trade
    of D+1 (00:00:10) was not folded live either -- the rebuild of D must not place it.
    """
    first = _trade(1, 86_339.1, 86_339.2, "99.0")  # 23:58:59.1, the archive's first trade
    late = _trade(2, 86_370.2, 86_373.1, "100.0", "0.25")  # 23:59:30.2, arrived 23:59:33.1
    last = _trade(3, 86_399.2, 86_399.4, "101.0")  # 23:59:59.2
    next_day = _trade(4, 86_410.2, 86_410.3, "102.0")  # 00:00:10.2 of D+1
    day_b = range(86_339, 86_399)  # seconds of D in file B
    b = [
        _row(_at(s + 0.5), _at(s + 3), [first] if s == 86_339 else [late] if s == 86_373 else [])
        for s in day_b
    ]
    c = [_row(_at(86_399.5), _at(86_402.5), [])]
    c += [_row(_at(s + 0.5), _at(s + 3), []) for s in range(86_400, 86_459)]
    catalog = _catalog(tmp_path)
    catalog.write_data(b)
    catalog.write_data(c)
    catalog.write_data([first, late, last, next_day])
    file_b, file_c = _snapshot_files(tmp_path)
    return file_b, file_c


def _open_day_rows(path: str) -> list[dict]:
    table = pq.read_table(path)
    return [r for r in table.to_pylist() if r["ts_event"] >= _D1]


def test_the_midnight_rebuild_rewrites_b_and_c_and_leaves_every_open_day_row(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    file_b, file_c = _midnight_catalog(tmp_path)
    assert Path(file_b).name.startswith("2026-09-10T23-59-02")
    assert Path(file_c).name.startswith("2026-09-11T00-00-02-500000000Z_2026-09-11T00-01-01")
    open_before = {path: _open_day_rows(path) for path in (file_b, file_c)}
    assert len(open_before[file_c]) == 59
    with maintenance(tmp_path, now_ns=lambda: _D1 + 30 * 60 * _S) as writer:  # 00:30 of D+1
        assert writer is not None
        report = rebuild_day(str(tmp_path), _IID, _D0, writer, GapMarkerFiles(tmp_path), True)
    assert error_ledger.counts() == {}  # no `rebuild.open_day` on the normal nightly path
    assert report.files_rewritten == 2
    assert (report.rebuilt, report.changed, report.orphan_trades) == (61, 3, 0)
    rows = _rows(tmp_path)
    assert (rows[86_370].open_price, rows[86_370].buy_volume) == (100.0, 0.25)  # late trade placed
    assert rows[86_373].buy_count == 0  # its arrival row cleared
    assert rows[86_399].open_price == 101.0  # D's 23:59:59 row, in file C, rebuilt
    # Every row of the open day is value- and order-identical (the 00:00:10 trade not placed).
    assert {path: _open_day_rows(path) for path in (file_b, file_c)} == open_before
    assert not glob.glob(str(tmp_path / "data" / "*" / "*" / "*.tmp"))


def test_a_rewrite_that_would_change_an_open_day_row_is_refused_with_the_files_intact(
    tmp_path: Path,
) -> None:
    """The same catalog, rebuilt as if D+1 were D: its rows are the open day's, so refused."""
    error_ledger.reset()
    _midnight_catalog(tmp_path)
    before = [Path(f).read_bytes() for f in _snapshot_files(tmp_path)]
    with maintenance(tmp_path, now_ns=lambda: _D1 + 30 * 60 * _S) as writer:
        assert writer is not None
        with pytest.raises(RefusedError) as refused:
            rebuild_day(str(tmp_path), _IID, _D1, writer, GapMarkerFiles(tmp_path), True)
    assert refused.value.site == "rebuild.open_day"
    assert [Path(f).read_bytes() for f in _snapshot_files(tmp_path)] == before
    assert not glob.glob(str(tmp_path / "data" / "*" / "*" / "*.tmp"))


def test_a_failed_verification_of_a_later_file_leaves_every_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All of the day's files are staged before the first rename: a refusal changes none."""
    _midnight_catalog(tmp_path)
    before = [Path(f).read_bytes() for f in _snapshot_files(tmp_path)]
    real = catalog_files.same_schema
    calls = []

    def second_fails(written: pa.Schema, table: pa.Schema) -> bool:
        calls.append(1)
        return len(calls) < 2 and real(written, table)

    monkeypatch.setattr(catalog_files, "same_schema", second_fails)
    with maintenance(tmp_path, now_ns=lambda: _D1 + 30 * 60 * _S) as writer:
        assert writer is not None
        with pytest.raises(RefusedError) as refused:
            rebuild_day(str(tmp_path), _IID, _D0, writer, GapMarkerFiles(tmp_path), True)
    assert refused.value.site == "rebuild.verify"
    assert [Path(f).read_bytes() for f in _snapshot_files(tmp_path)] == before
    assert not glob.glob(str(tmp_path / "data" / "*" / "*" / "*.tmp"))


def test_a_rename_failing_part_way_is_ledgered_as_partially_rebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Known limit: renames are atomic one by one, not as a set -- never reported untouched."""
    error_ledger.reset()
    file_b, file_c = _midnight_catalog(tmp_path)
    c_before = Path(file_c).read_bytes()
    real_replace = catalog_files.os.replace

    def fail_second(src: str, dst: str) -> None:
        if dst == Path(file_c):
            raise OSError(5, "Input/output error")
        real_replace(src, dst)

    monkeypatch.setattr(catalog_files.os, "replace", fail_second)
    args = ["--catalog", str(tmp_path), "--day", _DAY, "--venue", "BYBIT", "--apply"]
    result = tmp_path / "result.json"
    with maintenance(tmp_path, now_ns=lambda: _D1 + 30 * 60 * _S) as writer:
        assert writer is not None
        with pytest.raises(RefusedError) as refused:
            rebuild_day(str(tmp_path), _IID, _D0, writer, GapMarkerFiles(tmp_path), True)
    assert refused.value.site == "rebuild.error"
    assert "PARTIALLY rebuilt -- 1 of 2 file(s) replaced" in str(refused.value)
    assert Path(file_c).read_bytes() == c_before  # whole, old content
    assert _rows(tmp_path)[86_370].open_price == 100.0  # file B has the new content
    assert not glob.glob(str(tmp_path / "data" / "*" / "*" / "*.tmp"))
    monkeypatch.undo()
    # The rebuild is idempotent: the rerun completes the day.
    assert main([*args, "--result-file", str(result)]) == 0
    assert json.loads(result.read_text())["rebuilt"] == [_IID]
    assert _rows(tmp_path)[86_399].open_price == 101.0


def test_a_report_only_run_takes_no_lock(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    with maintenance(tmp_path) as held:  # e.g. a nightly running right now
        assert held is not None
        assert main(["--catalog", str(tmp_path), "--day", _DAY]) == 0
        assert main(["--catalog", str(tmp_path), "--day", _DAY, "--apply"]) == 1


def test_a_missing_catalog_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    assert main(["--catalog", str(tmp_path / "nope"), "--day", _DAY, "--apply"]) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}
