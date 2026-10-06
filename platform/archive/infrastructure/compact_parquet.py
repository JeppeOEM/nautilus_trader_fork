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
"""
The archive's one choice of Parquet write options (Story 30.1): the most compact lossless
settings the epic-30 measurements support, for every file the archive writes itself (not the
`write_data` files of `archive.backfill_bars`; `archive.repair_catalog` rewrites through
`CatalogFiles`, so a repaired file gets them, DW-204).

`compact_write_options` is the only place `pq.write_table` options are chosen for a catalog file
(DATA-05); `archive.infrastructure.catalog_files.CatalogFiles`, the one rewriter, is the only
caller that writes with them -- this module chooses, it never writes. The live minute files keep
Nautilus's own `write_data` encoding (FORK-01; `kernel.parquet_compat` only makes it zstd); a
file gets these settings when archive merges or rewrites it (nightly and intraday consolidation,
the nightly snapshot rebuild, the repair, the migration tools, `archive.tools.recompress`).

The settings and why:

- zstd at `COMPACT_ZSTD_LEVEL` (16) instead of pyarrow's default level (see the constant).
- `DELTA_BINARY_PACKED`, dictionary off, for every integer timestamp column (`ts_event`,
  `ts_init`, funding's `next_funding_ns`: `timestamp_columns`): consecutive nanosecond stamps
  differ by about a second, so their deltas pack into a few bits, where a dictionary of unique
  64-bit values only adds a dictionary page. Parquet refuses a column encoding together with a
  dictionary, so those columns must be named out of `use_dictionary`.
- Dictionary on for every other leaf column (a boolean leaf is bit-packed by Parquet whatever
  is asked), named by its Parquet leaf path: pyarrow silently leaves a nested column PLAIN when
  `use_dictionary` names only its top-level field (measured on the snapshot's `bid_prices`), so
  a list column is `<name>.list.element` and a struct child is `<name>.<child>` (`_leaf_paths`;
  any other nested type raises rather than silently going PLAIN).
- One row group per instrument-day up to `_MAX_ROW_GROUP_ROWS` rows, with column statistics kept,
  so `ts_event`/`ts_init` filter pushdown still prunes row groups of a bigger file.

Measured on one consolidated Bybit BTC day (epic 30 preamble, at zstd level 19), bytes per row,
pyarrow's default zstd write -> these settings: second snapshot 96.8 -> 59.2 (-39 %), mark price
22.3 -> 10.7 (-52 %), index price 21.4 -> 9.4 (-56 %), funding rate 22.4 -> 12.3 (-45 %), trade
tick 19.9 -> 14.1 (-29 %).

Rejected by the same measurements -- do not reintroduce: `BYTE_STREAM_SPLIT` on the float book
columns (+43 % bytes), and float32 book columns (no size gain after zstd, and lossy: a float64
price does not survive the round trip).
"""

from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


# zstd level of every archive write: 16, not the 19 the epic-30 sizes were measured at. Story 30.1
# timed one synthetic instrument-day written with these options (DATA_DICTIONARY.md §6): level 19
# took 20.7x the default level's time on trade ticks (172,264 rows: 0.513 s vs 0.025 s) and 14.5x
# on mark prices, over the epic's 10x budget; the rule is then the smallest level within 2 % of
# level 19's size for every type. 16 is it: second snapshot 8,914,680 B vs 8,793,072 B at 19
# (+1.4 %; 15 is +3.1 %), trade ticks -0.7 %, mark prices +0.5 %; its write time is 5.2x the default
# level on the snapshot day (0.85 s vs 0.16 s) and 10.8x on the trade day (0.27 s vs 0.025 s).
COMPACT_ZSTD_LEVEL = 16
# Row cap of one row group: one instrument-day of 1-second rows (86,400) is one group, a denser
# day (trade ticks) splits into groups of at most this many rows, each with its own statistics.
_MAX_ROW_GROUP_ROWS = 1_048_576
_TIMESTAMP_ENCODING = "DELTA_BINARY_PACKED"


def timestamp_columns(schema: pa.Schema) -> list[str]:
    """
    Return the top-level integer columns holding a nanosecond timestamp: named `ts_*` or `*_ns`
    (`ts_event`, `ts_init`, `next_funding_ns`). Counts, flags, sequences and precisions keep their
    dictionary.
    """
    return [
        field.name
        for field in schema
        if pa.types.is_integer(field.type)
        and (field.name.startswith("ts_") or field.name.endswith("_ns"))
    ]


def _leaf_paths(name: str, type_: pa.DataType) -> list[str]:
    """Return the Parquet leaf column paths of one field (how `use_dictionary` names them)."""
    if (
        pa.types.is_list(type_)
        or pa.types.is_large_list(type_)
        or pa.types.is_fixed_size_list(type_)
    ):
        return _leaf_paths(f"{name}.list.element", type_.value_type)
    if pa.types.is_struct(type_):
        return [
            path
            for i in range(type_.num_fields)
            for path in _leaf_paths(f"{name}.{type_.field(i).name}", type_.field(i).type)
        ]
    if pa.types.is_nested(type_):
        raise ValueError(
            f"column {name}: nested type {type_} has no known Parquet leaf naming; refusing to "
            "write it with a silently PLAIN encoding"
        )
    return [name]


def compact_write_options(schema: pa.Schema) -> dict[str, Any]:
    """Return the `pq.write_table` keyword arguments of every archive write of a `schema` table."""
    timestamps = timestamp_columns(schema)
    options: dict[str, Any] = {
        "compression": "zstd",
        "compression_level": COMPACT_ZSTD_LEVEL,
        "use_dictionary": [
            path
            for field in schema
            if field.name not in timestamps
            for path in _leaf_paths(field.name, field.type)
        ],
        "row_group_size": _MAX_ROW_GROUP_ROWS,
        "write_statistics": True,
    }
    if timestamps:
        options["column_encoding"] = dict.fromkeys(timestamps, _TIMESTAMP_ENCODING)
    return options


def is_compact(path: Path) -> bool:
    """
    Whether the file already carries these settings: its first row group's `ts_event` chunk is
    `DELTA_BINARY_PACKED`, which Nautilus's `write_data` never produces. A file with no row group
    has nothing to recompress (True); one with no `ts_event` column is not a catalog file
    (`ValueError`).

    Known limit: only the timestamp encoding is checked, not the zstd level, the dictionary set or
    the row-group cap, so after a change of those settings every file written under the old ones
    still reads as compact and `archive.tools.recompress` skips it (Parquet does not record a
    zstd level to compare). Upgrade path: a `--force` flag on the tool that rewrites every closed
    file in scope whatever this returns.
    """
    metadata = pq.read_metadata(path)
    if metadata.num_row_groups == 0:
        return True
    group = metadata.row_group(0)
    for i in range(group.num_columns):
        chunk = group.column(i)
        if chunk.path_in_schema == "ts_event":
            return _TIMESTAMP_ENCODING in chunk.encodings
    raise ValueError(f"{path}: no ts_event column, not a catalog data file")
