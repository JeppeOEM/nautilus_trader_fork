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
import time
from pathlib import Path

import pyarrow.parquet as pq
from ml_signals import error_ledger
from ml_signals.catalog_stats import query_second_snapshots

from collector_core.archive_gaps import record_gap
from collector_core.build_candles import _parse_date_ns
from collector_core.fold import fold_trades
from collector_core.rebuild_seconds import covered_from
from collector_core.rebuild_seconds import main
from collector_core.rebuild_seconds import rebuild_day
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"
_DAY = "2026-09-10"
_D0 = _parse_date_ns(_DAY)
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
    snaps = query_second_snapshots(str(tmp_path), _IID, _D0, _D0 + 86_400 * _S)
    return {(s.ts_event - _D0) // _S: s for s in snaps}


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
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=True)
    rows = _rows(tmp_path)
    assert (rows[100].open_price, rows[100].buy_volume, rows[100].buy_count) == (100.0, 0.25, 1)
    assert (rows[102].open_price, rows[102].buy_volume, rows[102].buy_count) == (None, 0.0, 0)
    assert rows[50].open_price == 101.0  # an on-time trade is where it was
    assert (report.rebuilt, report.changed, report.without_trades) == (56, 2, 54)
    assert report.files_rewritten == 1
    assert late.ts_event // _S != late.ts_init // _S


def test_rerun_changes_nothing_and_writes_no_file(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    rebuild_day(str(tmp_path), _IID, _D0, apply=True)
    before = [(Path(f).stat().st_mtime_ns, Path(f).read_bytes()) for f in _snapshot_files(tmp_path)]
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=True)
    assert (report.changed, report.files_rewritten) == (0, 0)
    assert [
        (Path(f).stat().st_mtime_ns, Path(f).read_bytes()) for f in _snapshot_files(tmp_path)
    ] == before


def test_book_columns_timestamps_and_schema_are_untouched(tmp_path: Path) -> None:
    _late_trade_day(tmp_path)
    (path,) = _snapshot_files(tmp_path)
    before = pq.read_table(path)
    rebuild_day(str(tmp_path), _IID, _D0, apply=True)
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
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=False)
    assert report.changed == 2
    assert report.files_rewritten == 0
    assert Path(path).read_bytes() == before


def test_rows_before_the_archive_keep_live_values_and_count_not_covered(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    before_archive = _trade(9, 10.2, 10.3, "105.0")  # folded live, never archived
    catalog.write_data([_snap(s, [before_archive] if s == 10 else []) for s in range(10, 20)])
    catalog.write_data([_trade(1, 15.2, 15.3)])  # the archive starts at 15.3
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=True)
    assert _rows(tmp_path)[10].open_price == 105.0
    assert (report.not_covered, report.rebuilt) == (5, 5)  # rows 10..14; 15.5 > 15.3 is covered


def test_orphan_trades_and_duplicates_are_counted_never_dropped_silently(tmp_path: Path) -> None:
    catalog = _catalog(tmp_path)
    once = _trade(1, 30.2, 30.3)
    catalog.write_data([_snap(s, [once] if s == 30 else []) for s in range(30, 35)])
    catalog.write_data([once])
    catalog.write_data([_trade(1, 30.2, 31.0)])  # the same trade replayed after a restart
    catalog.write_data([_trade(2, 40.1, 40.2), _trade(3, 40.7, 40.8), _trade(4, 41.5, 41.6)])
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=True)
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
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=True)
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
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=True)
    assert _rows(tmp_path)[20].open_price == 101.0
    assert (report.not_covered, report.rebuilt, report.orphan_trades) == (3, 3, 1)


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
    report = rebuild_day(str(tmp_path), _IID, _D0, apply=True)
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
