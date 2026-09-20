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
with mixed schemas, are left alone. Real catalog, real files (TEST-03).
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from ml_signals import error_ledger
from ml_signals.catalog_stats import data_file_ranges
from ml_signals.catalog_stats import query_second_ohlc

from dydx_collector.consolidate_catalog import consolidate_directory
from dydx_collector.consolidate_catalog import leaf_dirs
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId
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
    from dydx_collector.consolidate_catalog import _merge

    sources = sorted(directory.glob("*.parquet"))
    tmp = _merge(directory, sources)
    tmp.rename(tmp.with_suffix(""))  # renamed, but the sources are still there
    assert len(list(directory.glob("*.parquet"))) == 11

    assert consolidate_directory(directory, (_DAY0 + 1) * _DAY_NS, None, apply=True) == 1

    assert len(list(directory.glob("*.parquet"))) == 1
    rows = query_second_ohlc(str(tmp_path), IID, 0, (_DAY0 + 1) * _DAY_NS)
    assert [r.ts_event for r in rows] == [s.ts_event for s in written]
