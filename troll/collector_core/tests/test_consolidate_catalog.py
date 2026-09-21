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
Closed days collapse to one file per (type, instrument) with every row kept; today, and any day
with mixed schemas (column names or Arrow metadata such as `price_precision`), are left alone.
Holds for every venue's instrument ids and for Rust-native types (`MarkPriceUpdate`), and every
reader returns the same rows afterwards. Real catalog, real files (TEST-03).
"""

import fcntl
import itertools
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from ml_signals import candle_store
from ml_signals import error_ledger
from ml_signals.candle_store import BAR_SECONDS
from ml_signals.catalog_stats import data_file_ranges
from ml_signals.catalog_stats import query_second_ohlc

from collector_core.build_candles import rebuild_instrument
from collector_core.consolidate_catalog import _file_span
from collector_core.consolidate_catalog import consolidate_directory
from collector_core.consolidate_catalog import leaf_dirs
from collector_core.consolidate_catalog import main
from collector_core.consolidate_catalog import run
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


IID = "BTC-USD-PERP.DYDX"
_SEC = 1_000_000_000
_DAY_NS = 86_400 * _SEC
_DAY0 = 20_000  # a UTC day long ago


def _snap(ts: int, price: float) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(IID),
        bid_prices=[price - 1],
        bid_sizes=[1.0],
        ask_prices=[price + 1],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        open_price=price,
        high_price=price + 0.5,
        low_price=price - 0.5,
        close_price=price,
        ts_event=ts,
        ts_init=ts,
    )


def _seed(catalog: ParquetDataCatalog, day: int, minutes: int) -> list[DydxSecondSnapshot]:
    """One small file per minute, as the collector's flush produces."""
    out = []
    for m in range(minutes):
        batch = [_snap(day * _DAY_NS + (m * 60 + s) * _SEC, 100.0 + m) for s in range(0, 60, 10)]
        catalog.write_data(batch)
        out += batch
    return out


def test_closed_days_become_one_file_each_and_today_is_untouched(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    written = (
        _seed(catalog, _DAY0, 30) + _seed(catalog, _DAY0 + 1, 20) + _seed(catalog, _DAY0 + 2, 10)
    )
    now_ns = (_DAY0 + 2) * _DAY_NS + 3600 * _SEC  # "today" is day 2
    (directory,) = leaf_dirs(str(tmp_path))
    assert len(list(directory.glob("*.parquet"))) == 60
    before = query_second_ohlc(str(tmp_path), IID, 0, now_ns)

    assert consolidate_directory(directory, now_ns, None, apply=True) == 2

    spans = data_file_ranges(str(tmp_path), IID)
    closed = [s for s in spans if s[1] < (_DAY0 + 2) * _DAY_NS]
    assert len(closed) == 2  # one file per closed day
    assert len(spans) == 2 + 10  # today's ten minute-files still there
    after = query_second_ohlc(str(tmp_path), IID, 0, now_ns)
    assert (
        [r.ts_event for r in after]
        == [r.ts_event for r in before]
        == sorted(s.ts_event for s in written)
    )
    assert [r.close_price for r in after] == [r.close_price for r in before]

    assert consolidate_directory(directory, now_ns, None, apply=True) == 0  # idempotent


def test_a_day_with_mixed_schemas_is_refused_loudly(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed(catalog, _DAY0, 5)
    (directory,) = leaf_dirs(str(tmp_path))
    # An old-schema file (no OHLC columns) in the same day, named like the catalog names files.
    (sample,) = list(directory.glob("*.parquet"))[:1]
    table = pq.read_table(str(sample))
    old = table.drop_columns(["open_price", "high_price", "low_price", "close_price"])
    ts0 = _DAY0 * _DAY_NS + 3600 * _SEC
    old = old.set_column(
        old.schema.get_field_index("ts_event"),
        "ts_event",
        pa.array([ts0 + i * _SEC for i in range(old.num_rows)], pa.uint64()),
    )
    old = old.set_column(old.schema.get_field_index("ts_init"), "ts_init", old.column("ts_event"))
    pq.write_table(
        old, str(directory / f"{pd_stamp(ts0)}_{pd_stamp(ts0 + (old.num_rows - 1) * _SEC)}.parquet")
    )
    files_before = sorted(directory.glob("*.parquet"))
    counts_before = dict(error_ledger.counts())

    assert consolidate_directory(directory, (_DAY0 + 1) * _DAY_NS, None, apply=True) == 0

    assert sorted(directory.glob("*.parquet")) == files_before
    assert (
        error_ledger.counts()["consolidate.mixed_schema"]
        == counts_before.get("consolidate.mixed_schema", 0) + 1
    )


def pd_stamp(ns: int) -> str:
    import pandas as pd

    return (
        pd.Timestamp(ns, unit="ns", tz="UTC").strftime("%Y-%m-%dT%H-%M-%S") + f"-{ns % _SEC:09d}Z"
    )


def test_an_interrupted_run_is_finished_without_duplicating_rows(tmp_path: Path) -> None:
    """
    Merged file renamed into place, sources not yet deleted (a crash in between): the next run
    verifies the covering file and deletes the sources instead of merging duplicates.
    """
    catalog = ParquetDataCatalog(str(tmp_path))
    written = _seed(catalog, _DAY0, 10)
    (directory,) = leaf_dirs(str(tmp_path))
    from collector_core.consolidate_catalog import _merge

    sources = sorted(directory.glob("*.parquet"))
    tmp = _merge(directory, sources)
    tmp.rename(tmp.with_name(tmp.name.removesuffix(".consolidate.tmp")))  # sources still there
    assert len(list(directory.glob("*.parquet"))) == 11

    assert consolidate_directory(directory, (_DAY0 + 1) * _DAY_NS, None, apply=True) == 1

    assert len(list(directory.glob("*.parquet"))) == 1
    rows = query_second_ohlc(str(tmp_path), IID, 0, (_DAY0 + 1) * _DAY_NS)
    assert [r.ts_event for r in rows] == [s.ts_event for s in written]


# --- Story 22.11: every venue, reader equivalence, midnight crossing, metadata guard, run/exit ---

_VENUE_IIDS = ("BTC-USD-PERP.DYDX", "BTC-USDT-LINEAR.BYBIT", "BTC-USD-PERP.HYPERLIQUID")
_MARK_IID = "ETH-USDT-LINEAR.BYBIT"
_DAY0_2 = (_DAY0 + 2) * _DAY_NS  # start of "today" in the tests below


def _venue_snaps(iid: str, timestamps: list[int]) -> list[DydxSecondSnapshot]:
    """Build the one snapshot class every venue's collector writes, for `iid`."""
    return [
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(iid),
            bid_prices=[99.0 + i],
            bid_sizes=[1.0],
            ask_prices=[101.0 + i],
            ask_sizes=[1.0],
            buy_volume=1.0,
            sell_volume=0.5,
            buy_count=1,
            sell_count=1,
            open_price=100.0 + i,
            high_price=100.5 + i,
            low_price=99.5 + i,
            close_price=100.0 + i,
            ts_event=ts,
            ts_init=ts,
        )
        for i, ts in enumerate(timestamps)
    ]


def _minute_stamps(day: int, minute: int) -> list[int]:
    return [day * _DAY_NS + (minute * 60 + s) * _SEC for s in range(0, 60, 10)]


def _seed_minutes(catalog: ParquetDataCatalog, iid: str, day: int, minutes: int) -> None:
    """One snapshot file per minute for `iid`, as every collector's flush produces."""
    for m in range(minutes):
        catalog.write_data(_venue_snaps(iid, _minute_stamps(day, m)))


def _seed_mark_minutes(
    catalog: ParquetDataCatalog, day: int, minutes: int, price: str = "2500.5"
) -> None:
    """One real `MarkPriceUpdate` file per minute (a Rust-native type, not a custom one)."""
    iid = InstrumentId.from_str(_MARK_IID)
    for m in range(minutes):
        catalog.write_data(
            [MarkPriceUpdate(iid, Price.from_str(price), ts, ts) for ts in _minute_stamps(day, m)]
        )


def _files_per_day(directory: Path) -> dict[int, int]:
    days: dict[int, int] = {}
    for path in directory.glob("*.parquet"):
        a, b = _file_span(path)
        key = a // _DAY_NS if a // _DAY_NS == b // _DAY_NS else -1  # -1: crosses midnight
        days[key] = days.get(key, 0) + 1
    return days


def _files_span_crosses(path: Path) -> bool:
    a, b = _file_span(path)
    return a // _DAY_NS != b // _DAY_NS


def test_every_venue_and_data_type_is_consolidated_by_run(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    for iid in _VENUE_IIDS:
        for offset, minutes in ((0, 12), (1, 8), (2, 5)):
            _seed_minutes(catalog, iid, _DAY0 + offset, minutes)
    for offset, minutes in ((0, 7), (1, 6), (2, 4)):
        _seed_mark_minutes(catalog, _DAY0 + offset, minutes)
    now_ns = (_DAY0 + 2) * _DAY_NS + 3600 * _SEC  # "today" is day 2
    leaves = leaf_dirs(str(tmp_path))
    assert sorted(d.name for d in leaves) == sorted((*_VENUE_IIDS, _MARK_IID))
    assert {d.parent.name for d in leaves} == {"custom_dydx_second_snapshot", "mark_price_update"}
    today = {
        d: sorted(f.name for f in d.glob("*.parquet") if _file_span(f)[0] >= _DAY0_2)
        for d in leaves
    }
    files_before = sum(len(list(d.glob("*.parquet"))) for d in leaves)

    stats = run(str(tmp_path), None, None, apply=True, now_ns=now_ns)

    assert (stats.days_done, stats.days_refused) == (2 * len(leaves), 0)
    assert stats.files_before == files_before == 3 * 25 + 17
    assert stats.files_after == 3 * (2 + 5) + (2 + 4)
    assert stats.bytes_before > stats.bytes_after > 0
    assert stats.wall_seconds >= 0
    assert stats.peak_rss_mb > 0
    (mark_leaf,) = [d for d in leaves if d.name == _MARK_IID]
    for merged in (f for f in mark_leaf.glob("*.parquet") if _file_span(f)[1] < _DAY0_2):
        assert pq.read_schema(str(merged)).metadata == {
            b"instrument_id": _MARK_IID.encode(),
            b"price_precision": b"1",
        }
    for directory in leaves:
        per_day = _files_per_day(directory)
        assert per_day[_DAY0] == per_day[_DAY0 + 1] == 1
        assert (
            sorted(f.name for f in directory.glob("*.parquet") if _file_span(f)[0] >= _DAY0_2)
            == today[directory]
        )

    again = run(str(tmp_path), None, None, apply=True, now_ns=now_ns)
    assert (again.days_done, again.days_refused) == (0, 0)  # idempotent
    assert again.files_before == again.files_after == stats.files_after


def _catalog_ts_events(path: Path, iid: str) -> list[int]:
    rows = ParquetDataCatalog(str(path)).query(data_cls=DydxSecondSnapshot, identifiers=[iid])
    return [(r.data if hasattr(r, "data") else r).ts_event for r in rows]


def _candle_windows(db_path: str, iid: str) -> dict[int, list[dict]]:
    db = candle_store.connect_rw(db_path)
    windows = {bar: candle_store.window(db, iid, bar, 1 << 62, 10_000) for bar in BAR_SECONDS}
    db.close()
    return windows


def test_readers_return_the_same_rows_after_consolidation(tmp_path: Path) -> None:
    catalog_dir = tmp_path / "cat"
    catalog = ParquetDataCatalog(str(catalog_dir))
    _seed_minutes(catalog, IID, _DAY0, 30)
    _seed_minutes(catalog, IID, _DAY0 + 1, 20)
    _seed_minutes(catalog, IID, _DAY0 + 2, 10)
    now_ns = _DAY0_2 + 3600 * _SEC
    start, end = _DAY0 * _DAY_NS, _DAY0_2 + _DAY_NS - 1
    (directory,) = leaf_dirs(str(catalog_dir))
    source_metadata = pq.read_schema(str(sorted(directory.glob("*.parquet"))[0])).metadata
    ts_before = _catalog_ts_events(catalog_dir, IID)
    ohlc_before = query_second_ohlc(str(catalog_dir), IID, start, end)
    assert rebuild_instrument(
        str(tmp_path / "before.db"), str(catalog_dir), IID, start, end
    ) == len(ts_before)

    assert run(str(catalog_dir), None, None, apply=True, now_ns=now_ns).days_done == 2

    assert _catalog_ts_events(catalog_dir, IID) == ts_before == sorted(ts_before)
    assert query_second_ohlc(str(catalog_dir), IID, start, end) == ohlc_before
    closed = [f for f in directory.glob("*.parquet") if _file_span(f)[1] < _DAY0_2]
    assert len(closed) == 2
    for merged in closed:
        assert pq.read_schema(str(merged)).metadata == source_metadata
    spans = data_file_ranges(str(catalog_dir), IID)
    assert len(spans) == 2 + 10
    assert all(a <= b for a, b in spans)
    assert all(prev[1] < nxt[0] for prev, nxt in itertools.pairwise(spans))
    assert spans[0][0] == ts_before[0]
    assert spans[-1][1] == ts_before[-1]
    assert rebuild_instrument(str(tmp_path / "after.db"), str(catalog_dir), IID, start, end) == len(
        ts_before
    )
    before = _candle_windows(str(tmp_path / "before.db"), IID)
    after = _candle_windows(str(tmp_path / "after.db"), IID)
    assert all(before[bar] for bar in BAR_SECONDS)
    assert after == before


def test_a_midnight_crossing_file_is_left_alone_and_both_days_still_merge(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed_minutes(catalog, IID, _DAY0, 5)  # 00:00-00:05 of day 0
    crossing_start = (_DAY0 + 1) * _DAY_NS - 30 * _SEC  # 23:59:30 -> 00:00:30
    catalog.write_data(_venue_snaps(IID, [crossing_start + s * _SEC for s in range(0, 61, 10)]))
    for m in range(60, 65):  # 01:00-01:05 of day 1, clear of the crossing batch
        catalog.write_data(_venue_snaps(IID, _minute_stamps(_DAY0 + 1, m)))
    (directory,) = leaf_dirs(str(tmp_path))
    (crossing,) = [f for f in directory.glob("*.parquet") if _files_span_crosses(f)]
    crossing_bytes = crossing.read_bytes()
    now_ns = _DAY0_2 + 3600 * _SEC

    assert consolidate_directory(directory, now_ns, None, apply=True) == 2

    assert _files_per_day(directory) == {_DAY0: 1, _DAY0 + 1: 1, -1: 1}
    assert crossing.read_bytes() == crossing_bytes
    assert consolidate_directory(directory, now_ns, None, apply=True) == 0
    rows = query_second_ohlc(str(tmp_path), IID, 0, now_ns)
    assert len(rows) == 5 * 6 + 7 + 5 * 6


def test_a_day_with_differing_precision_metadata_is_refused_and_run_exits_1(
    tmp_path: Path,
) -> None:
    """
    Same columns, different `price_precision` label (a venue tick-size change mid-day): merging
    would relabel part of the rows (`pa.concat_tables` keeps the first metadata), so it is refused.
    """
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed_mark_minutes(catalog, _DAY0, 3, price="2500.5")
    iid = InstrumentId.from_str(_MARK_IID)
    ts = _DAY0 * _DAY_NS + 3600 * _SEC
    catalog.write_data([MarkPriceUpdate(iid, Price.from_str("2500.50"), ts, ts)])
    (directory,) = leaf_dirs(str(tmp_path))
    precisions = {
        pq.read_schema(str(f)).metadata[b"price_precision"] for f in directory.glob("*.parquet")
    }
    assert precisions == {b"1", b"2"}
    names = {pq.read_schema(str(f)).names.__repr__() for f in directory.glob("*.parquet")}
    assert len(names) == 1  # the old names-only guard would have merged this day
    files_before = sorted(directory.glob("*.parquet"))
    refused_before = error_ledger.counts().get("consolidate.mixed_schema", 0)
    now_ns = (_DAY0 + 1) * _DAY_NS

    stats = run(str(tmp_path), None, None, apply=True, now_ns=now_ns)

    assert (stats.days_done, stats.days_refused) == (0, 1)
    assert stats.files_before == stats.files_after == 4
    assert sorted(directory.glob("*.parquet")) == files_before
    assert error_ledger.counts()["consolidate.mixed_schema"] == refused_before + 1
    # The CLI turns the refusal into a non-zero exit (DATA-07); no --apply needed to see it.
    assert main(["--catalog", str(tmp_path)]) == 1
    assert sorted(directory.glob("*.parquet")) == files_before


def test_a_clean_run_exits_0_and_report_only_changes_nothing(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed_minutes(catalog, IID, _DAY0, 4)
    (directory,) = leaf_dirs(str(tmp_path))
    files_before = sorted(directory.glob("*.parquet"))

    assert main(["--catalog", str(tmp_path), "--days", "100000"]) == 0

    assert sorted(directory.glob("*.parquet")) == files_before
    assert main(["--catalog", str(tmp_path), "--apply"]) == 0
    assert len(list(directory.glob("*.parquet"))) == 1


def test_a_leftover_tmp_file_is_removed_only_by_an_apply_run(tmp_path: Path) -> None:
    """
    A crash inside `_merge` leaves `<name>.parquet.consolidate.tmp`; it is removed. Another tool's
    `*.parquet.tmp` (`migrate_open_interest`, `normalize_snapshot_schema`) is never touched.
    """
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed_minutes(catalog, IID, _DAY0, 3)
    (directory,) = leaf_dirs(str(tmp_path))
    from collector_core.consolidate_catalog import _merge

    tmp = _merge(directory, sorted(directory.glob("*.parquet")))
    assert tmp.name.endswith(".parquet.consolidate.tmp")
    foreign = directory / "someone-elses.parquet.tmp"
    foreign.write_bytes(b"in flight")
    now_ns = (_DAY0 + 1) * _DAY_NS

    run(str(tmp_path), None, None, apply=False, now_ns=now_ns)
    assert tmp.exists()

    stats = run(str(tmp_path), None, None, apply=True, now_ns=now_ns)
    assert not tmp.exists()
    assert foreign.read_bytes() == b"in flight"
    assert (stats.days_done, stats.days_refused, stats.files_after) == (1, 0, 1)
    assert len(query_second_ohlc(str(tmp_path), IID, 0, now_ns)) == 3 * 6


_BAR_TYPE = "BTCUSDT-LINEAR.BYBIT-1-MINUTE-LAST-EXTERNAL"
_MIN = 60 * _SEC


def _bars(closes: list[int]) -> list[Bar]:
    bar_type = BarType.from_str(_BAR_TYPE)
    return [
        Bar(
            bar_type,
            Price.from_str("100.0"),
            Price.from_str("101.0"),
            Price.from_str("99.0"),
            Price.from_str("100.5"),
            Quantity.from_str("3"),
            ts,
            ts,
        )
        for ts in closes
    ]


def test_bar_leaves_are_never_consolidated_so_a_backfill_hole_stays_visible(
    tmp_path: Path,
) -> None:
    """
    `backfill_bars` writes one file per contiguous run and plans from file intervals: merging the
    two runs below would name one file over the 10-minute hole and seal it as covered (DATA-05).
    """
    catalog = ParquetDataCatalog(str(tmp_path))
    start = _DAY0 * _DAY_NS
    catalog.write_data(_bars([start + m * _MIN for m in range(1, 11)]))
    catalog.write_data(_bars([start + m * _MIN for m in range(21, 31)]))
    _seed_minutes(catalog, IID, _DAY0, 3)
    bar_dir = tmp_path / "data" / "bar" / _BAR_TYPE
    files_before = sorted(bar_dir.glob("*.parquet"))
    assert len(files_before) == 2
    gaps_before = catalog.get_missing_intervals_for_request(
        start, start + 40 * _MIN, Bar, _BAR_TYPE
    )

    stats = run(str(tmp_path), None, None, apply=True, now_ns=(_DAY0 + 1) * _DAY_NS)

    assert stats.days_done == 1  # the snapshot leaf only
    assert [d.parent.name for d in leaf_dirs(str(tmp_path))] == ["custom_dydx_second_snapshot"]
    assert sorted(bar_dir.glob("*.parquet")) == files_before
    gaps_after = catalog.get_missing_intervals_for_request(start, start + 40 * _MIN, Bar, _BAR_TYPE)
    assert gaps_after == gaps_before
    assert any(low <= start + 15 * _MIN <= high for low, high in gaps_after)


def test_an_unreadable_file_refuses_its_day_only_and_the_run_still_finishes(
    tmp_path: Path,
) -> None:
    """A truncated file (a crash mid-write) must not abort every other leaf, nor pass silently."""
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed_minutes(catalog, IID, _DAY0, 3)
    _seed_minutes(catalog, IID, _DAY0 + 1, 3)
    _seed_minutes(catalog, _VENUE_IIDS[1], _DAY0, 3)
    broken_dir = tmp_path / "data" / "custom_dydx_second_snapshot" / IID
    victim = sorted(broken_dir.glob("*.parquet"))[0]  # a day-0 file
    victim.write_bytes(victim.read_bytes()[:40])
    day0_files = sorted(
        f for f in broken_dir.glob("*.parquet") if _file_span(f)[0] < (_DAY0 + 1) * _DAY_NS
    )
    errors_before = error_ledger.counts().get("consolidate.error", 0)
    now_ns = (_DAY0 + 2) * _DAY_NS

    stats = run(str(tmp_path), None, None, apply=True, now_ns=now_ns)

    assert (stats.days_done, stats.days_refused, stats.leaves_failed) == (2, 1, 0)
    assert error_ledger.counts()["consolidate.error"] == errors_before + 1
    assert (
        sorted(f for f in broken_dir.glob("*.parquet") if _file_span(f)[0] < (_DAY0 + 1) * _DAY_NS)
        == day0_files
    )
    assert _files_per_day(broken_dir)[_DAY0 + 1] == 1
    other = tmp_path / "data" / "custom_dydx_second_snapshot" / _VENUE_IIDS[1]
    assert _files_per_day(other) == {_DAY0: 1}
    assert main(["--catalog", str(tmp_path), "--apply"]) == 1


def test_a_covering_file_with_equal_row_count_but_other_rows_is_not_trusted(
    tmp_path: Path,
) -> None:
    """
    A file written into an already-consolidated day by another writer, with as many rows as the
    merged file, is not an interrupted run: nothing may be deleted as "already covered".
    """
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed_minutes(catalog, IID, _DAY0, 2)  # 12 rows, 00:00:00-00:01:50
    now_ns = (_DAY0 + 1) * _DAY_NS
    (directory,) = leaf_dirs(str(tmp_path))
    assert consolidate_directory(directory, now_ns, None, apply=True) == 1
    (merged,) = list(directory.glob("*.parquet"))
    inside = [_DAY0 * _DAY_NS + 5 * _SEC + i * _SEC for i in range(12)]  # 12 new rows inside
    catalog.write_data(_venue_snaps(IID, inside), skip_disjoint_check=True)
    files_before = sorted(directory.glob("*.parquet"))
    assert len(files_before) == 2
    refused_before = error_ledger.counts().get("consolidate.row_count", 0)

    assert consolidate_directory(directory, now_ns, None, apply=True) == 0

    assert sorted(directory.glob("*.parquet")) == files_before
    assert merged.exists()
    assert error_ledger.counts()["consolidate.row_count"] == refused_before + 1


def test_a_second_concurrent_run_refuses_to_start(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path))
    _seed_minutes(catalog, IID, _DAY0, 3)
    (directory,) = leaf_dirs(str(tmp_path))
    files_before = sorted(directory.glob("*.parquet"))

    with (tmp_path / ".consolidate.lock").open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(["--catalog", str(tmp_path), "--apply"]) == 1
        assert sorted(directory.glob("*.parquet")) == files_before

    assert main(["--catalog", str(tmp_path), "--apply"]) == 0
    assert len(list(directory.glob("*.parquet"))) == 1


def test_a_missing_catalog_exits_1(tmp_path: Path) -> None:
    assert main(["--catalog", str(tmp_path / "nope"), "--apply"]) == 1
