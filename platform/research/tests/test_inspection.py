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
`research.application.inspection` (Story 27.2): each classification and count of the catalog
inspection over hand-built inputs -- real `TradeTick`s and `Price`s, frames in `CatalogFrames`'
shape -- including every row of the story's I/O matrix.
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_MS
from kernel.clocks import NS_PER_S
from kernel.fold import fold_trades
from kernel.second_snapshot import unit_float
from observability import error_ledger

from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application import inspection
from research.tests.fixture_catalog import FixturePaths


_IID = "BTCUSDT-LINEAR.BYBIT"
_DAY0 = 20_697 * NS_PER_DAY  # 2026-09-01


def _at(second: float) -> int:
    return _DAY0 + int(second * NS_PER_S)


def _every_second(seconds: range, skip: range = range(0), offset: float = 0.0) -> list[int]:
    return [_at(s + offset) for s in seconds if s not in skip]


# --- gap_report ------------------------------------------------------------------------------


def _kinds(report: pd.DataFrame) -> list[tuple[str, int, int]]:
    return list(zip(report["kind"], report["start_ns"], report["end_ns"], strict=True))


def test_a_hole_in_seconds_and_marks_is_a_likely_outage() -> None:
    hole = range(100, 190)
    report = inspection.gap_report(
        _every_second(range(400), hole, 0.5),
        _every_second(range(400), hole, 0.1),
        _every_second(range(400), hole, 0.25),
    )
    assert _kinds(report) == [(inspection.LIKELY_OUTAGE, _at(99.5), _at(190.5))]


def test_an_outage_is_found_whatever_the_mark_cadence() -> None:
    # Marks every 20 s: a 45 s common hole is under their own gap threshold, not under the seconds'.
    hole = range(100, 145)
    marks = [t for t in _every_second(range(0, 400, 20)) if not _at(100) <= t < _at(145)]
    report = inspection.gap_report(
        _every_second(range(400), hole), _every_second(range(400), hole), marks
    )
    assert _kinds(report) == [(inspection.LIKELY_OUTAGE, _at(99), _at(145))]


def test_a_seconds_gap_without_marks_around_it_is_not_classified() -> None:
    seconds = _every_second(range(400), range(100, 150))
    no_marks = inspection.gap_report(seconds, seconds, [])
    feed_ended = inspection.gap_report(seconds, seconds, _every_second(range(120)))
    assert _kinds(no_marks) == [(inspection.NO_MARK_COVERAGE, _at(99), _at(150))]
    assert _kinds(feed_ended) == [(inspection.NO_MARK_COVERAGE, _at(99), _at(150))]


def test_a_seconds_gap_while_marks_flow_is_a_book_gap() -> None:
    report = inspection.gap_report(
        _every_second(range(400), range(100, 150)),
        _every_second(range(400)),
        _every_second(range(400)),
    )
    assert _kinds(report) == [(inspection.BOOK_GAP, _at(99), _at(150))]


def test_a_trade_gap_while_the_book_is_sampled_is_a_quiet_market() -> None:
    report = inspection.gap_report(
        _every_second(range(400)),
        _every_second(range(400), range(200, 245)),
        _every_second(range(400)),
    )
    assert _kinds(report) == [(inspection.QUIET_MARKET, _at(199), _at(245))]


def test_a_trade_gap_past_the_sampled_seconds_is_not_a_quiet_market() -> None:
    # Trades (a reconnect's backfill) run on while the window's snapshots stop at 300 s.
    trades = _every_second(range(400), range(320, 380))
    marks = _every_second(range(400))
    ended = inspection.gap_report(_every_second(range(300)), trades, marks)
    none_at_all = inspection.gap_report([], trades, marks)
    assert ended.empty
    assert none_at_all.empty


def test_a_hole_under_the_30_second_floor_is_not_flagged() -> None:
    short = range(100, 120)
    report = inspection.gap_report(
        _every_second(range(400), short),
        _every_second(range(400), short),
        _every_second(range(400), short),
    )
    assert report.empty
    assert tuple(report.columns) == inspection.GAP_COLUMNS


def test_gap_report_takes_series_indexed_by_time() -> None:
    """A frame's `ts_event` column is indexed by timestamp: never read positionally by label."""
    stamps = _every_second(range(400), range(100, 190))
    series = pd.Series(stamps, index=pd.to_datetime(stamps, unit="ns", utc=True))
    report = inspection.gap_report(series, series, series)
    assert report["kind"].tolist() == [inspection.LIKELY_OUTAGE]


# --- day_status ------------------------------------------------------------------------------


def test_day_status_maps_every_verdict_and_an_absent_store(tmp_path: Path) -> None:
    store = CandleStore(db_path_for_venue(tmp_path, "BYBIT"))
    store.mark_verified(_IID, "2026-09-01", "pass", 0, 1)
    store.mark_verified(_IID, "2026-09-02", "fail", 3, 1)
    store.close()
    days = inspection.day_status(
        str(tmp_path), [_IID, "BTC-USD-PERP.DYDX"], ["2026-09-01", "2026-09-02", "2026-09-03"]
    )
    assert list(zip(days["instrument_id"], days["day"], days["status"], strict=True)) == [
        (_IID, "2026-09-01", "verified"),
        (_IID, "2026-09-02", "failed"),
        (_IID, "2026-09-03", "provisional"),
        ("BTC-USD-PERP.DYDX", "2026-09-01", inspection.STORE_ABSENT),
        ("BTC-USD-PERP.DYDX", "2026-09-02", inspection.STORE_ABSENT),
        ("BTC-USD-PERP.DYDX", "2026-09-03", inspection.STORE_ABSENT),
    ]


def test_utc_days_are_every_day_the_half_open_window_touches() -> None:
    assert inspection.utc_days(_DAY0, _DAY0 + 2 * NS_PER_DAY) == ["2026-09-01", "2026-09-02"]
    assert inspection.utc_days(_DAY0 - 1, _DAY0 + 1) == ["2026-08-31", "2026-09-01"]


# --- fold_agreement --------------------------------------------------------------------------


def _trade(second: float, size: str, side: AggressorSide, trade_id: str) -> TradeTick:
    return TradeTick(
        InstrumentId.from_str(_IID),
        Price.from_str("100.25"),
        Quantity.from_str(size),
        side,
        TradeId(trade_id),
        _at(second),
        _at(second) + 50 * NS_PER_MS,
    )


# The row precisions of the frames below (the trades are "100.25" and sizes of 3 decimals at most).
_PP, _SP = 2, 3


def _decoded(column: str, units: int | None) -> float:
    """Return a stored trade column as `CatalogFrames.seconds` shows it: the kernel's decoded float."""
    if units is None:
        return math.nan
    if column.endswith("_count"):
        return units
    return unit_float(units, _SP if column.endswith("volume") else _PP)


def _seconds(rows: list[tuple[float, list[TradeTick]]]) -> pd.DataFrame:
    """Build a seconds frame in `CatalogFrames.seconds`' shape, trade columns folded from `trades`."""
    records = []
    for second, trades in rows:
        units = fold_trades(trades).snapshot_units(_PP, _SP)._asdict()
        records.append(
            {
                "ts_event": _at(second),
                "ts_init": _at(second) + NS_PER_S,
                "price_precision": _PP,
                "size_precision": _SP,
                "bid_prices": [100.0],
                "ask_prices": [100.5],
                "mid": 100.25,
                "spread": 0.5,
                **{k: _decoded(k, v) for k, v in units.items()},
            }
        )
    frame = pd.DataFrame(records)
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame["ts_event"], unit="ns", utc=True))
    return frame


_T1 = _trade(1.2, "0.5", AggressorSide.BUYER, "a")
_T2 = _trade(1.7, "0.25", AggressorSide.SELLER, "b")


def test_a_fold_that_matches_every_second_agrees() -> None:
    table = inspection.fold_agreement([_T1, _T2], _seconds([(1.5, [_T1, _T2]), (2.5, [])]))
    row = table.iloc[0]
    assert (row["day"], row["seconds"], row["trade_seconds"], row["mismatched_seconds"]) == (
        "2026-09-01",
        2,
        1,
        0,
    )
    assert (row["trade_buy_volume"], row["trade_sell_volume"]) == (0.5, 0.25)
    assert bool(row["agrees"])


def test_a_second_that_differs_is_counted_and_the_day_disagrees() -> None:
    table = inspection.fold_agreement([_T1, _T2], _seconds([(1.5, [_T1])]))
    assert int(table.iloc[0]["mismatched_seconds"]) == 1
    assert not bool(table.iloc[0]["agrees"])


def test_an_untraded_second_must_store_zero_volume_and_nan_ohlc() -> None:
    seconds = _seconds([(1.5, [])])
    seconds.loc[seconds.index[0], "open_price"] = 0.0  # a zero open is not "no trade"
    assert int(inspection.fold_agreement([], seconds).iloc[0]["mismatched_seconds"]) == 1


def test_a_duplicate_trade_id_is_folded_once_and_counted() -> None:
    replay = _trade(1.2, "0.5", AggressorSide.BUYER, "a")
    table = inspection.fold_agreement([_T1, replay, _T2], _seconds([(1.5, [_T1, _T2])]))
    assert int(table.iloc[0]["duplicate_trades"]) == 1
    assert bool(table.iloc[0]["agrees"])


def test_a_trade_second_with_no_snapshot_is_an_orphan() -> None:
    orphan = _trade(9.1, "1", AggressorSide.BUYER, "c")
    table = inspection.fold_agreement([_T1, _T2, orphan], _seconds([(1.5, [_T1, _T2])]))
    assert int(table.iloc[0]["orphan_trade_seconds"]) == 1
    assert not bool(table.iloc[0]["agrees"])


def test_two_rows_in_one_second_are_shared_not_compared() -> None:
    table = inspection.fold_agreement([_T1, _T2], _seconds([(1.1, [_T1, _T2]), (1.9, [])]))
    row = table.iloc[0]
    assert (int(row["shared_seconds"]), int(row["mismatched_seconds"])) == (1, 0)
    assert int(row["seconds"]) == 1  # distinct seconds, not rows
    assert not bool(row["agrees"])


def test_fold_agreement_splits_by_utc_day_of_the_second() -> None:
    late = _trade(-0.5, "1", AggressorSide.SELLER, "d")  # 23:59:59.5 the day before
    table = inspection.fold_agreement([late, _T1], _seconds([(-0.5, [late]), (1.5, [_T1])]))
    assert table["day"].tolist() == ["2026-08-31", "2026-09-01"]
    assert table["agrees"].tolist() == [True, True]


def test_an_empty_window_has_no_day() -> None:
    table = inspection.fold_agreement(
        [],
        pd.DataFrame(
            columns=["ts_event", "price_precision", "size_precision", *inspection.TRADE_COLUMNS]
        ),
    )
    assert table.empty
    assert tuple(table.columns) == inspection.AGREEMENT_COLUMNS


def test_a_second_only_partly_in_the_window_is_not_compared() -> None:
    # The window opens at 1.5 s: second 1 was read from 1.5 on only, so its row holds a trade
    # (_T1 at 1.2 s) the read never saw -- left out, not a mismatch.
    seconds = _seconds([(1.0, [_T1, _T2]), (2.0, [])])
    table = inspection.fold_agreement([_T2], seconds.iloc[1:], window=(_at(1.5), _at(3)))
    row = table.iloc[0]
    assert (row["seconds"], row["trade_seconds"], bool(row["agrees"])) == (1, 0, True)


def test_a_day_volume_past_the_quantity_range_is_summed_exactly() -> None:
    size = "30000000000000"  # 3e13 a second: the day's two together exceed QUANTITY_RAW_MAX
    first = _trade(1.1, size, AggressorSide.BUYER, "x")
    second = _trade(2.1, size, AggressorSide.BUYER, "y")
    table = inspection.fold_agreement([first, second], _seconds([(1.0, [first]), (2.0, [second])]))
    assert table.iloc[0]["trade_buy_volume"] == 6e13


# --- precision_labels ------------------------------------------------------------------------


def test_mixed_precision_labels_across_files_are_not_uniform(tmp_path: Path) -> None:
    iid = InstrumentId.from_str(_IID)
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_T1])
    catalog.write_data([MarkPriceUpdate(iid, Price.from_str("100.1"), _at(1), _at(1))])
    # A second file at another label: the dYdX incident the catalog cannot read back together.
    catalog.write_data([MarkPriceUpdate(iid, Price.from_str("100.12"), _at(2), _at(2))])
    table = inspection.precision_labels(str(tmp_path), None, _IID, _at(0), _at(10))
    assert table["stream"].tolist() == ["trade_tick", "mark_price_update", "index_price_update"]
    assert table["labels"].tolist() == [(2,), (1, 2), ()]
    assert table["files"].tolist() == [1, 2, 0]
    assert table["uniform"].tolist() == [True, False, True]
    assert table["instrument_precision"].isna().all()


def test_a_file_without_a_precision_label_is_not_uniform(tmp_path: Path) -> None:
    ParquetDataCatalog(str(tmp_path)).write_data([_T1])
    path = next((tmp_path / "data" / "trade_tick" / _IID).glob("*.parquet"))
    pq.write_table(pq.read_table(path).replace_schema_metadata(None), path)
    table = inspection.precision_labels(str(tmp_path), None, _IID, _at(0), _at(10))
    assert table["labels"].iloc[0] == (None,)
    assert not bool(table["uniform"].iloc[0])


# --- snapshot_sanity and mid_series ----------------------------------------------------------


def _book(rows: list[tuple[float, list[float], list[float]]]) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "ts_event": [_at(s) for s, _, _ in rows],
            "ts_init": [_at(s) + (1 + i) * 100 * NS_PER_MS for i, (s, _, _) in enumerate(rows)],
            "bid_prices": [b for _, b, _ in rows],
            "ask_prices": [a for _, _, a in rows],
            "mid": [(b[0] + a[0]) / 2 if b and a else math.nan for _, b, a in rows],
            "spread": [a[0] - b[0] if b and a else math.nan for _, b, a in rows],
        }
    )
    frame.index = pd.DatetimeIndex(pd.to_datetime(frame["ts_event"], unit="ns", utc=True))
    return frame


_SANE = [100.0], [101.0]


def test_snapshot_sanity_counts_crossed_touched_and_one_sided_rows() -> None:
    book = _book(
        [
            (0.5, *_SANE),
            (1.5, [101.5], [101.0]),  # crossed
            (2.5, [101.0], [101.0]),  # touched: crossed, not a negative spread
            (3.5, [], [101.0]),  # one-sided
        ]
    )
    sanity = inspection.snapshot_sanity(book)
    assert (sanity["rows"], sanity["crossed"], sanity["negative_spread"], sanity["empty_side"]) == (
        4,
        2,
        1,
        1,
    )
    assert sanity["crossed_ts"] == [_at(1.5), _at(2.5)]
    assert (sanity["lag_ms_min"], sanity["lag_ms_max"]) == (100.0, 400.0)


def test_snapshot_sanity_of_an_empty_window() -> None:
    sanity = inspection.snapshot_sanity(_book([]))
    assert (sanity["rows"], sanity["crossed"]) == (0, 0)
    assert math.isnan(sanity["lag_ms_p50"])


def test_mid_series_is_blank_on_missing_crossed_and_one_sided_seconds() -> None:
    book = _book([(0.5, *_SANE), (1.5, [101.5], [101.0]), (2.5, [], [101.0]), (4.5, *_SANE)])
    mid = inspection.mid_series(book, _at(0), _at(6))
    assert np.array_equal(
        mid.to_numpy(), [100.5, math.nan, math.nan, math.nan, 100.5, math.nan], equal_nan=True
    )
    assert mid.index[0] == pd.Timestamp(_at(0), unit="ns", tz="UTC")
    assert mid.index.name == "ts"


def test_mid_series_keeps_the_row_of_a_second_the_window_starts_inside() -> None:
    mid = inspection.mid_series(_book([(0.5, *_SANE), (1.5, *_SANE)]), _at(0.3), _at(2))
    assert mid.to_numpy().tolist() == [100.5, 100.5]
    assert mid.index[0] == pd.Timestamp(_at(0), unit="ns", tz="UTC")


def test_mid_series_blanks_a_second_holding_two_rows() -> None:
    mid = inspection.mid_series(_book([(0.2, *_SANE), (0.7, *_SANE)]), _at(0), _at(1))
    assert math.isnan(mid.iloc[0])


def _with_trades(book: pd.DataFrame) -> pd.DataFrame:
    return book.assign(buy_volume=[float(i + 1) for i in range(len(book))])


def test_second_grid_blanks_book_columns_on_a_crossed_second_and_keeps_its_trades() -> None:
    book = _with_trades(_book([(0.5, *_SANE), (1.5, [101.5], [101.0])]))
    grid = inspection.second_grid(book, _at(0), _at(2), ("mid", "spread", "buy_volume"))
    assert np.isnan(grid[["mid", "spread"]].to_numpy()[1]).all()
    assert grid["buy_volume"].tolist() == [1.0, 2.0]


def test_second_grid_is_nan_in_every_column_on_missing_and_shared_seconds() -> None:
    book = _with_trades(_book([(0.2, *_SANE), (0.7, *_SANE), (2.5, *_SANE)]))
    grid = inspection.second_grid(book, _at(0), _at(3), ("mid", "buy_volume"))
    assert np.isnan(grid.to_numpy()[:2]).all()
    assert grid.iloc[2].tolist() == [100.5, 3.0]


def test_second_grid_treats_only_the_named_book_columns_as_book_derived() -> None:
    book = _with_trades(_book([(0.5, [101.5], [101.0])]))
    grid = inspection.second_grid(book, _at(0), _at(1), ("mid",), book_columns=())
    assert grid["mid"].tolist() == [101.25]


def test_mid_series_is_second_grid_mid() -> None:
    book = _book([(0.5, *_SANE), (1.5, [101.5], [101.0]), (3.5, *_SANE)])
    grid = inspection.second_grid(book, _at(0), _at(4), ("mid",))
    assert inspection.mid_series(book, _at(0), _at(4)).equals(grid["mid"])


# --- ledger_window ---------------------------------------------------------------------------


def _line(ts_ns: int, site: str, suppressed: int = 0) -> str:
    return json.dumps(
        {
            "ts_ns": ts_ns,
            "service": "collector",
            "pid": 7,
            "site": site,
            "detail": "",
            "exc_type": None,
            "suppressed": suppressed,
        }
    )


def test_a_missing_ledger_directory_is_absent(tmp_path: Path) -> None:
    ledger = inspection.ledger_window(str(tmp_path / "nope"), _at(0), _at(60))
    assert ledger.state == inspection.LEDGER_ABSENT
    assert ledger.counts.empty


def test_a_ledger_path_that_is_a_file_is_unreadable_not_absent(tmp_path: Path) -> None:
    errors = tmp_path / "errors"
    errors.write_text("")
    assert inspection.ledger_window(str(errors), _at(0), _at(60)).state == (
        inspection.LEDGER_UNREADABLE
    )


def test_a_ledger_with_nothing_in_the_window_is_empty(tmp_path: Path) -> None:
    (tmp_path / "collector.jsonl").write_text(_line(_at(120), "collector.resync") + "\n")
    assert inspection.ledger_window(str(tmp_path), _at(0), _at(60)).state == inspection.LEDGER_EMPTY


def test_an_unreadable_ledger_file_is_not_an_empty_window(tmp_path: Path) -> None:
    (tmp_path / "collector.jsonl").mkdir()  # listed as a ledger file, cannot be read as one
    try:
        ledger = inspection.ledger_window(str(tmp_path), _at(0), _at(60))
    finally:
        error_ledger.reset()  # the reader counted the failure in this process's ledger
    assert ledger.state == inspection.LEDGER_UNREADABLE


def test_ledger_counts_fold_the_carry_and_leave_restarts_apart(tmp_path: Path) -> None:
    lines = [
        _line(_at(1), "process_start"),
        _line(_at(2), "collector.resync"),
        _line(_at(3), "collector.resync", suppressed=4),
        _line(_at(60), "collector.resync"),  # at `end`: outside the half-open window
    ]
    (tmp_path / "collector.jsonl").write_text("\n".join(lines) + "\n")
    ledger = inspection.ledger_window(str(tmp_path), _at(0), _at(60))
    assert ledger.state == inspection.LEDGER_RECORDS
    assert ledger.counts.to_dict("records") == [
        {"service": "collector", "site": "collector.resync", "count": 6}
    ]
    assert ledger.restarts.to_dict("records") == [{"service": "collector", "restarts": 1}]


# --- metadata over the fixture archive -------------------------------------------------------


def test_inventory_lists_every_definition_with_venue_and_kind(
    fixture_archive: FixturePaths,
) -> None:
    inventory = inspection.instrument_inventory(fixture_archive.catalog_path)
    assert inventory["instrument_id"].tolist() == sorted(fixture_archive.instruments)
    assert tuple(inventory.columns[:4]) == inspection.INVENTORY_COLUMNS
    assert set(inventory["market_kind"]) == {"perp"}
    assert set(inventory["type"]) == {"CryptoPerpetual"}


def test_file_coverage_keeps_only_files_overlapping_the_window(
    fixture_archive: FixturePaths,
) -> None:
    iid = fixture_archive.instruments[1]
    every = inspection.file_coverage(fixture_archive.catalog_path, iid, 0, 2**62)
    first_end = int(every["end_ns"].iloc[0])
    # A file's span is `ts_init`: it can hold rows up to the skew bound before it.
    within_skew = first_end + MAX_TS_INIT_SKEW_NS
    kept = inspection.file_coverage(fixture_archive.catalog_path, iid, within_skew, 2**62)
    after = inspection.file_coverage(fixture_archive.catalog_path, iid, within_skew + 1, 2**62)
    assert len(kept) == len(every)
    assert len(after) == len(every) - 1
    assert inspection.file_coverage(fixture_archive.catalog_path, iid, 0, 1).empty


def test_summary_counts_each_section() -> None:
    iid = "X.BYBIT"
    gaps = pd.DataFrame(
        {"kind": [inspection.LIKELY_OUTAGE, inspection.QUIET_MARKET, inspection.NO_MARK_COVERAGE]}
    )
    days = pd.DataFrame(
        {
            "instrument_id": [iid, iid, iid],
            "status": ["verified", "provisional", "unrecognised verdict 'x'"],
        }
    )
    agreement = pd.DataFrame({"agrees": [True, False]})
    sanity = {"rows": 3, "crossed": 1, "negative_spread": 0, "empty_side": 2}
    precision = pd.DataFrame({"uniform": [True, False]})
    row = inspection.summary(
        [iid], {iid: gaps}, days, {iid: agreement}, {iid: sanity}, {iid: precision}
    ).iloc[0]
    assert [row[kind] for kind in inspection.GAP_KINDS] == [1, 0, 1, 1]
    assert (row["verified"], row["provisional"], row["failed"], row["other_day_status"]) == (
        1,
        1,
        0,
        1,
    )
    assert (row["fold_days_disagreeing"], row["crossed"], row["empty_side"]) == (1, 1, 2)
    assert not bool(row["precision_uniform"])
