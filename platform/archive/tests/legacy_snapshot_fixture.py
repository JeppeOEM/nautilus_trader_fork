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
Test helper: write pre-Story-30.2 float-layout `DydxSecondSnapshot` files, as capture wrote them.

The kernel can no longer produce this layout (there is no float write path), so the old Arrow
schema is spelled out here and tables are written with pyarrow straight into a catalog leaf, under
a catalog file name (`_timestamps_to_filename`). Values are whatever floats the test passes --
including the `Price.as_double()` noise capture stored (`85891.90000000001`).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from kernel.catalog_files import SNAPSHOT_DIRNAME

from nautilus_trader.persistence.catalog.parquet import _timestamps_to_filename


LEGACY_SCHEMA = pa.schema(
    {
        "instrument_id": pa.dictionary(pa.int8(), pa.string()),
        "bid_prices": pa.list_(pa.float64()),
        "bid_sizes": pa.list_(pa.float64()),
        "ask_prices": pa.list_(pa.float64()),
        "ask_sizes": pa.list_(pa.float64()),
        "buy_volume": pa.float64(),
        "sell_volume": pa.float64(),
        "buy_count": pa.uint32(),
        "sell_count": pa.uint32(),
        "open_price": pa.float64(),
        "high_price": pa.float64(),
        "low_price": pa.float64(),
        "close_price": pa.float64(),
        "ts_event": pa.uint64(),
        "ts_init": pa.uint64(),
    },
    metadata={"type": "DydxSecondSnapshot"},
)
_OHLC = ("open_price", "high_price", "low_price", "close_price")


@dataclass
class LegacyRow:
    """One float-layout row (absolute level prices, best first)."""

    ts_event: int
    bid_prices: list[float]
    bid_sizes: list[float]
    ask_prices: list[float]
    ask_sizes: list[float]
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    ohlc: tuple[float | None, float | None, float | None, float | None] = (None,) * 4
    ts_init: int | None = None


def legacy_table(iid: str, rows: Sequence[LegacyRow], with_ohlc: bool = True) -> pa.Table:
    """Return the rows as a float-layout table; `with_ohlc=False` is a pre-OHLC file (no such columns)."""
    columns: dict[str, list] = {name: [] for name in LEGACY_SCHEMA.names}
    for row in rows:
        columns["instrument_id"].append(iid)
        for name in ("bid_prices", "bid_sizes", "ask_prices", "ask_sizes"):
            columns[name].append(getattr(row, name))
        for name in ("buy_volume", "sell_volume", "buy_count", "sell_count", "ts_event"):
            columns[name].append(getattr(row, name))
        for name, value in zip(_OHLC, row.ohlc, strict=True):
            columns[name].append(value)
        columns["ts_init"].append(row.ts_event if row.ts_init is None else row.ts_init)
    table = pa.Table.from_pydict(columns, schema=LEGACY_SCHEMA)
    return table if with_ohlc else table.drop_columns(list(_OHLC))


def write_legacy(root: Path, iid: str, rows: Sequence[LegacyRow], with_ohlc: bool = True) -> Path:
    """Write one float-layout file into the catalog at `root`, named by its rows' `ts_init` span."""
    table = legacy_table(iid, rows, with_ohlc)
    ts_init = table.column("ts_init").to_pylist()
    leaf = root / "data" / SNAPSHOT_DIRNAME / iid
    leaf.mkdir(parents=True, exist_ok=True)
    path = leaf / _timestamps_to_filename(min(ts_init), max(ts_init))
    pq.write_table(table, path)
    return path
