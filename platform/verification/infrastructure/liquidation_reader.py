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

A file or row group is skipped only by its own `ts_event` statistics lying wholly outside the
window (`catalog_reader.read_window`). Only trades whose size equals some liquidation's become
Python objects: the size filter runs in Arrow (MEM-01), so a busy instrument-day of trades never
materialises whole.
"""

from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
from kernel.venues import has_venue

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

    def instruments(self, venue: str) -> list[str]:
        """Every instrument of `venue` with a liquidation directory (any day)."""
        root = self._catalog / "data" / LIQUIDATION_DIR
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir() and has_venue(p.name, venue))

    def liquidations(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> list[StoredLiquidation]:
        """Every stored liquidation of the instrument with `ts_event` in `[start_ns, end_ns)`."""
        rows: list[StoredLiquidation] = []
        for path in _files(self._catalog, LIQUIDATION_DIR, instrument_id):
            table = read_window(path, list(_LIQUIDATION_COLUMNS), start_ns, end_ns)
            if table is not None:
                rows += [_stored(row, path) for row in table.to_pylist()]
        return rows

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
