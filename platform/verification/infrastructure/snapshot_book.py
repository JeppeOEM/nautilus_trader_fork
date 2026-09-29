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
The book tool's catalog read (Story 31.5): each stored second-snapshot row's top-20 book, read raw
with pyarrow and decoded here -- the oracle must decode independently. The reference side never
imports the code it checks, so the gap layout is decoded from its published rule
(`docs/DATA_DICTIONARY.md` section 1.7), never through `kernel.second_snapshot`
(`tests/test_boundaries.py`'s `_GAP_LAYOUT_READERS` names this module for exactly that).

Layout read (Epic 30.2's integer snapshot, `<catalog>/data/custom_dydx_second_snapshot/<iid>/`):
`price_precision`/`size_precision` (uint8, per row); `bid_prices`/`ask_prices` (list<int64>:
element 0 the best price in units of 10^-price_precision, every later element the strictly
positive gap to the level above it -- bids subtract it, asks add it); `bid_sizes`/`ask_sizes`
(list<int64>, units of 10^-size_precision); `ts_event` (the second S * 10^9 + 5 * 10^8);
`ts_init` (when it was sampled). 0..20 levels per side, no padding. Every value is a Python
`int`: nothing passes through a float.

Invariant: a file of the float layout (no `price_precision` column, before Story 30.2) is refused
by name, never read as numbers it does not hold; a row whose gap is not strictly positive, whose
sizes do not pair with its prices or that lacks a precision is refused too.

Known limit (memory, MEM-01): one instrument-window of the book columns stays in Arrow (about
86,400 rows of up to 80 integers, ~60 MB for a full day), and one hour of rows at a time becomes
Python objects. Upgrade path: read row groups hour by hour through `read_window`.
"""

from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.reference_book import ASKS
from verification.domain.reference_book import BIDS
from verification.domain.reference_book import BookUnits
from verification.domain.reference_book import StoredBook
from verification.domain.reference_book import UnitLevel
from verification.infrastructure.catalog_reader import SNAPSHOT_DIR
from verification.infrastructure.catalog_reader import read_window


_PRICE_PRECISION = "price_precision"
_SIZE_PRECISION = "size_precision"
_TS_EVENT = "ts_event"
_TS_INIT = "ts_init"
_PRICES = MappingProxyType({BIDS: "bid_prices", ASKS: "ask_prices"})
_SIZES = MappingProxyType({BIDS: "bid_sizes", ASKS: "ask_sizes"})
_COLUMNS = (
    _PRICE_PRECISION,
    _SIZE_PRECISION,
    *_PRICES.values(),
    *_SIZES.values(),
    _TS_EVENT,
    _TS_INIT,
)


def _empty() -> pa.Table:
    """Return the table of a window without rows, typed as the stored columns."""
    levels = pa.list_(pa.int64())
    schema = {
        _PRICE_PRECISION: pa.uint8(),
        _SIZE_PRECISION: pa.uint8(),
        **dict.fromkeys((*_PRICES.values(), *_SIZES.values()), levels),
        _TS_EVENT: pa.uint64(),
        _TS_INIT: pa.uint64(),
    }
    return pa.schema(schema).empty_table()


class FloatLayout(ValueError):
    """A snapshot file of the float layout (before Story 30.2): its book is not exact integers."""


def decode_prices(encoded: Sequence[int], side: str) -> tuple[int, ...]:
    """
    Return the stored `[best, gap, gap, ...]` as absolute prices in units, best first: bids
    subtract each gap, asks add it. A gap that is not strictly positive raises `ValueError`.
    """
    prices: list[int] = []
    for index, value in enumerate(encoded):
        if index and value <= 0:
            raise ValueError(f"{side} book: a non-positive stored gap {value} at level {index}")
        if not index:
            prices.append(value)
        else:
            prices.append(prices[-1] - value if side == BIDS else prices[-1] + value)
    return tuple(prices)


def _side(row: dict[str, Any], side: str) -> tuple[UnitLevel, ...]:
    if row[_PRICES[side]] is None or row[_SIZES[side]] is None:
        # The stored layout writes an empty side as an empty list, never a null.
        raise ValueError(f"a snapshot row at ts_event {row[_TS_EVENT]}: a null {side} column")
    prices = decode_prices(row[_PRICES[side]], side)
    sizes = row[_SIZES[side]]
    if len(sizes) != len(prices):
        raise ValueError(
            f"a snapshot row at ts_event {row[_TS_EVENT]}: {len(prices)} {side} prices but "
            f"{len(sizes)} sizes"
        )
    return tuple(zip(prices, sizes, strict=True))


def stored_book(row: dict[str, Any]) -> StoredBook:
    """
    One row's book, decoded. A row that cannot be judged is refused (`ValueError`, never a crash):
    no `ts_event`/`ts_init` (`read_window` already refuses a file with a null `ts_event`), no
    precisions, a null book side, or a decoded price that is not positive (a gap chain running a
    bid to or below zero).
    """
    missing = [
        name
        for name in (_TS_EVENT, _TS_INIT, _PRICE_PRECISION, _SIZE_PRECISION)
        if row[name] is None
    ]
    if missing:
        raise ValueError(
            f"a snapshot row at ts_event {row[_TS_EVENT]} without {', '.join(missing)}"
        )
    book = BookUnits(_side(row, BIDS), _side(row, ASKS))
    if any(price <= 0 for price, _ in (*book.bids, *book.asks)):
        raise ValueError(f"a snapshot row at ts_event {row[_TS_EVENT]}: a price <= 0 units")
    return StoredBook(row[_TS_INIT], row[_PRICE_PRECISION], row[_SIZE_PRECISION], book)


class ArrowSnapshotBooks:
    """
    One instrument-window of stored books, in Arrow. Invariant: rows are sorted by `ts_event`, so
    an hour is one contiguous slice found by binary search, and only that hour ever becomes Python
    objects (`hour`: second -> its rows; a second holding two rows keeps both).
    """

    def __init__(self, table: pa.Table) -> None:
        self._table = table.sort_by(_TS_EVENT)
        self._ts = self._table[_TS_EVENT].to_numpy().astype(np.uint64)

    @property
    def count(self) -> int:
        return self._table.num_rows

    def hour(self, hour: int) -> dict[int, list[StoredBook]]:
        low, high = np.searchsorted(self._ts, [hour * NS_PER_HOUR, (hour + 1) * NS_PER_HOUR])
        seconds: dict[int, list[StoredBook]] = {}
        for row in self._table.slice(int(low), int(high - low)).to_pylist():
            book = stored_book(row)
            seconds.setdefault(row[_TS_EVENT] // NS_PER_S, []).append(book)
        return seconds


def _file_window(path: Path, window: tuple[int, int]) -> pa.Table | None:
    """
    Return the file's rows in the window. Its layout is judged only when it holds a row there: an
    old float-layout file of another day never blocks the checked one.
    """
    probe = read_window(path, [_TS_EVENT], *window)
    if probe is None or not probe.num_rows:
        return None
    if _PRICE_PRECISION not in pq.read_schema(path).names:
        raise FloatLayout(
            f"{path}: a float-layout snapshot file (no `{_PRICE_PRECISION}` column); rewrite it to "
            "the integer layout with `python3 -m archive.tools.migrate_snapshot_ints` first"
        )
    return read_window(path, list(_COLUMNS), *window)


class SnapshotBooks:
    """The catalog's stored snapshot books, read raw (see the module docstring)."""

    def __init__(self, catalog: Path) -> None:
        self._catalog = catalog

    def rows(self, instrument_id: str, start_ns: int, end_ns: int) -> ArrowSnapshotBooks:
        """Every snapshot row of the instrument with `ts_event` in `[start_ns, end_ns)`."""
        files = sorted((self._catalog / "data" / SNAPSHOT_DIR / instrument_id).glob("*.parquet"))
        window = (start_ns, end_ns)
        tables = [t for path in files if (t := _file_window(path, window)) is not None]
        if not tables:
            return ArrowSnapshotBooks(_empty())
        return ArrowSnapshotBooks(pa.concat_tables(tables, promote_options="permissive"))
