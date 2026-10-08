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
The liquidations tool's catalog reads (Story 33.1): raw Parquet through pyarrow, never through
`ParquetDataCatalog` or `kernel.liquidation` -- the reference side never imports the code it
checks.

Layout read (Nautilus's own, written by `ParquetDataCatalog.write_data`):
- `data/custom_liquidation/<iid>/*.parquet`: `venue_event_id`, `side` (`"long"`/`"short"`),
  `size_units`, `size_precision`, `ts_event` (`docs/DATA_DICTIONARY.md` §1.26);
- `data/trade_tick/<iid>/*.parquet`: `size` (16-byte fixed point), `aggressor_side`, `ts_event`.

A liquidation file is first chosen by its name's `ts_init` span widened by `NAME_SPAN_MARGIN_NS`
on both sides (an unparseable name is always read), then a row group is skipped only by its own
`ts_event` statistics lying wholly outside the window (`catalog_reader.read_window`), so a window
read opens the files around it, never the instrument's whole history. Only trades whose size equals some liquidation's become
Python objects: the size filter runs in Arrow (MEM-01), so a busy instrument-day of trades never
materialises whole.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from kernel.venues import has_venue

from verification.domain.catalog_check import parse_file_name
from verification.domain.conservation import NS_PER_S
from verification.domain.liquidation_check import Fill
from verification.domain.liquidation_check import StoredLiquidation
from verification.domain.trade_check import fixed_raw
from verification.infrastructure.catalog_reader import TRADE_DIR
from verification.infrastructure.catalog_reader import read_window


LIQUIDATION_DIR = "custom_liquidation"
_LIQUIDATION_COLUMNS = ("venue_event_id", "side", "size_units", "size_precision", "ts_event")
_FILL_COLUMNS = ("size", "aggressor_side", "ts_event")
# The trade archive's fixed point: a signed little-endian 128-bit integer at 16 decimals
# (`trade_check.fixed_raw`), the encoding a liquidation's size raw is compared in.
_FIXED_BYTES = 16
# How far a liquidation file's name span (`ts_init`) is widened, both sides, to choose the files
# that can hold a `ts_event` window: 300 s, the bound production's reader widens the same files by
# (`kernel.clocks.MAX_TS_INIT_SKEW_NS`, restated, not imported: the reference never imports the
# code it checks). Known limit: a row whose `ts_init` trails its `ts_event` by more is missed by
# both readers alike, so the oracle would not catch it. Upgrade path: read each file's own
# `ts_event` statistics (one footer per file) in place of the name, at that per-file cost.
NAME_SPAN_MARGIN_NS = 300 * NS_PER_S


def _files(catalog: Path, data_dir: str, instrument_id: str) -> list[Path]:
    return sorted((catalog / "data" / data_dir / instrument_id).glob("*.parquet"))


def _stored(row: dict[str, object], path: Path) -> StoredLiquidation:
    if any(value is None for value in row.values()):
        raise ValueError(f"{path}: a liquidation row with a null column: {row}")
    return StoredLiquidation(
        venue_event_id=str(row["venue_event_id"]),
        side=str(row["side"]),
        size_units=int(row["size_units"]),  # type: ignore[call-overload]
        size_precision=int(row["size_precision"]),  # type: ignore[call-overload]
        ts_event=int(row["ts_event"]),  # type: ignore[call-overload]
    )


class LiquidationCatalog:
    """The catalog's liquidations and the trades that could match them, read raw."""

    def __init__(self, catalog: Path) -> None:
        self._catalog = catalog
        # `first_ts_event` per instrument, read once per run: a tool run is one process over one
        # closed day (and its week), and the archive's first liquidation does not move under it.
        self._first: dict[str, int | None] = {}

    def instruments(self, venue: str) -> list[str]:
        """Every instrument of `venue` with a liquidation directory (any day)."""
        root = self._catalog / "data" / LIQUIDATION_DIR
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir() and has_venue(p.name, venue))

    def liquidations(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> list[StoredLiquidation]:
        """
        Every stored liquidation of the instrument with `ts_event` in `[start_ns, end_ns)`, read
        from the files whose name span meets the window widened by `NAME_SPAN_MARGIN_NS` (and any
        file whose name does not parse).
        """
        low, high = start_ns - NAME_SPAN_MARGIN_NS, end_ns + NAME_SPAN_MARGIN_NS
        rows: list[StoredLiquidation] = []
        for path in _files(self._catalog, LIQUIDATION_DIR, instrument_id):
            span = parse_file_name(path.name)
            if span is not None and not span.intersects(low, high):
                continue
            table = read_window(path, list(_LIQUIDATION_COLUMNS), start_ns, end_ns)
            if table is not None:
                rows += [_stored(row, path) for row in table.to_pylist()]
        return rows

    def first_ts_event(self, instrument_id: str) -> int | None:
        """
        Return the instrument's earliest stored liquidation `ts_event` (None when none is stored):
        the oracle's own known-from bound for the candle columns (§2.15), derived independently of
        production's (`kernel.catalog_files.liquidation_feed_since_ns` walks file-name spans; this
        reads every file's `ts_event` column and takes the minimum, trusting no name). A null
        `ts_event` is refused.

        Computed once per instrument per run and cached (the candles tool asks for each day of the
        judged week and the catalog tool per instrument-day).

        Known limit: the one read walks the `ts_event` column of every liquidation file of the
        instrument, O(files) -- small (one 8-byte column of a sparse feed). Upgrade path: the row
        groups' own statistics once a file without them can be told apart.
        """
        if instrument_id not in self._first:
            self._first[instrument_id] = self._read_first_ts_event(instrument_id)
        return self._first[instrument_id]

    def _read_first_ts_event(self, instrument_id: str) -> int | None:
        first: int | None = None
        for path in _files(self._catalog, LIQUIDATION_DIR, instrument_id):
            column = pq.read_table(path, columns=["ts_event"]).column("ts_event")
            if column.null_count:
                raise ValueError(f"{path}: a liquidation row without `ts_event`")
            if len(column):
                low = int(column.to_numpy().min())
                first = low if first is None else min(first, low)
        return first

    def fills(self, instrument_id: str, window: tuple[int, int], sizes: set[int]) -> list[Fill]:
        """Return the archived trades in `window` whose size (a 16-decimal raw) is one of `sizes`."""
        if not sizes:
            return []
        wanted = [size.to_bytes(_FIXED_BYTES, "little", signed=True) for size in sorted(sizes)]
        fills: list[Fill] = []
        for path in _files(self._catalog, TRADE_DIR, instrument_id):
            table = read_window(path, list(_FILL_COLUMNS), *window)
            if table is None:
                continue
            value_set = pa.array(wanted, table.schema.field("size").type)
            for row in table.filter(pc.is_in(table["size"], value_set=value_set)).to_pylist():
                fills.append(Fill(fixed_raw(row["size"]), row["aggressor_side"], row["ts_event"]))
        return fills
