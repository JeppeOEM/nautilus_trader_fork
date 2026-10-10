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
The subject's catalog reads (Story 31.7): every day file opened through `ParquetDataCatalog`, the
24 hourly bounded queries of a day, and the re-encoding of the objects a reader returns with
`ArrowSerializer.serialize_batch` -- the serializer the catalog writes with -- so the oracle's raw
rows and the reader's objects are digested alike. Subject side (`verification.subject`): the code
under test is driven here and judged by the oracle.

How a file is opened: `query(cls, identifiers=[iid], files=[f], start, end)` over each UTC hour
its rows' `ts_init` occupy, clipped to their `[min, max]` (the application's windows, never the
name's span). `files=` always takes Nautilus's PyArrow path, which has no decoder for the
Rust-only price types (`MarkPriceUpdate`, `IndexPriceUpdate`: `NotImplementedError`); a file of a
type Nautilus reads through its Rust backend is then opened by the same bounded query without
`files=` -- what a backtest or a research read of it runs. The window is the file's own span, so
it holds that file's rows only while no other file's span meets it (`overlap`, judged apart).
The fallback exists because `files=` alone read every mark file as failed (audit D-114, a
verifier edge). Known limit (pinned Nautilus): `IndexPriceUpdate` has no decoder on either path,
so every index file is `open_failed` (audit D-113, OPEN; the one in-repo reader is
`kernel.catalog_files.query_index_prices`, `docs/DATA_DICTIONARY.md` section 1.5). Upgrade path: a
Nautilus release that decodes it.

Memory (MEM-01): one hour window of objects at a time.
"""

import inspect
from collections.abc import Iterable
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType
from types import ModuleType
from typing import Any

import kernel.liquidation
import kernel.open_interest
import kernel.second_snapshot
import pyarrow as pa

import nautilus_trader.model.data as nautilus_data
import nautilus_trader.model.instruments as nautilus_instruments
from nautilus_trader.core.data import Data
from nautilus_trader.model.data import CustomData
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.funcs import class_to_filename
from nautilus_trader.serialization.arrow.serializer import ArrowSerializer
from verification.domain.catalog_check import NS_PER_DAY
from verification.domain.catalog_check import NS_PER_HOUR
from verification.domain.catalog_check import Digest
from verification.domain.catalog_check import FileOpen
from verification.domain.catalog_check import Leg


# Objects re-encoded per serializer call (the backtest actor flushes at the same size).
ENCODE_CHUNK = 10_000
# The platform's three custom types, beside every Nautilus data and instrument class: the
# second snapshot, open interest and liquidation (DW-295: without `Liquidation` here the
# structure check flags a real Bybit day's `custom_liquidation` files as an unknown type).
_CUSTOM = (
    kernel.second_snapshot.DydxSecondSnapshot,
    kernel.open_interest.OpenInterest,
    kernel.liquidation.Liquidation,
)


def _classes_of(module: ModuleType, base: type) -> list[type]:
    return [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, base) and obj is not base
    ]


def class_map() -> MappingProxyType[str, type]:
    """
    Each catalog directory name Nautilus writes, mapped to its class: built from the Nautilus data
    and instrument classes and the platform's custom types through Nautilus's own file naming
    (`class_to_filename`), never typed by hand.
    """
    classes = [
        *_classes_of(nautilus_data, Data),
        *_classes_of(nautilus_instruments, Data),
        *_CUSTOM,
    ]
    return MappingProxyType({class_to_filename(cls): cls for cls in classes})


def unwrap(objects: Iterable[Any]) -> list[Any]:
    """Unwrap each `CustomData` a custom-type query returns; other objects stay as they are."""
    return [o.data if isinstance(o, CustomData) else o for o in objects]


def encode_table(objects: Sequence[Any], cls: type) -> pa.Table:
    """Re-encode objects with the catalog's own serializer (the one `write_data` uses)."""
    return ArrowSerializer.serialize_batch(list(objects), data_cls=cls)


def projected(
    table: pa.Table, columns: Sequence[str]
) -> tuple[list[dict[str, object]], tuple[str, ...]]:
    """Project encoded rows to the stored columns; also return the stored columns it lacks."""
    missing = tuple(c for c in columns if c not in table.column_names)
    present = [c for c in columns if c in table.column_names]
    return table.select(present).to_pylist(), missing


def digest_encoded(objects: Sequence[Any], cls: type, columns: Sequence[str]) -> Leg:
    """
    Digest objects re-encoded and projected, `ENCODE_CHUNK` at a time: an hour of the busiest
    trades is ~300k objects, and their rows as Python dicts would double the hour's memory.
    """
    total, missing = Digest(), set[str]()
    for start in range(0, len(objects), ENCODE_CHUNK):
        rows, lacking = projected(encode_table(objects[start : start + ENCODE_CHUNK], cls), columns)
        total += Digest.of(rows)
        missing.update(lacking)
    return Leg(total, tuple(sorted(missing)))


def _error_text(exc: Exception) -> str:
    if isinstance(exc, NotImplementedError) and not str(exc):
        # Raised by Nautilus's decoder itself, with no message: say what was tried.
        return "NotImplementedError: no decoder on the files= (PyArrow) or the bounded (Rust) path"
    return f"{type(exc).__name__}: {exc}"


class NautilusReads:
    """
    `ParquetDataCatalog` over one catalog root, read-only. Invariant: every read is bounded by
    `start=` and `end=` (MEM-01), and a reader's objects reach the oracle only as re-encoded rows.
    """

    def __init__(self, catalog: Path) -> None:
        self._catalog = ParquetDataCatalog(str(catalog))
        self._classes = class_map()

    def known(self, data_type: str) -> bool:
        return data_type in self._classes

    def _count(self, cls: type, iid: str, window: tuple[int, int], files: list[str] | None) -> int:
        start, end = window
        return len(self._catalog.query(cls, identifiers=[iid], start=start, end=end, files=files))

    def _open_windows(
        self, cls: type, iid: str, path: Path, windows: Sequence[tuple[int, int]]
    ) -> int:
        try:
            return sum(self._count(cls, iid, window, [str(path)]) for window in windows)
        except NotImplementedError:  # the PyArrow path has no decoder: the Rust backend's read
            return sum(self._count(cls, iid, window, None) for window in windows)

    def open_file(
        self, data_type: str, iid: str, path: Path, windows: Sequence[tuple[int, int]]
    ) -> FileOpen:
        """
        Decode one file over the given inclusive windows (the hours its rows occupy); the error,
        if the catalog raised. A file gone since it was listed raises `FileNotFoundError` (the
        catalog changed under the run).
        """
        cls = self._classes[data_type]
        try:
            return FileOpen(self._open_windows(cls, iid, path, windows))
        except Exception as exc:  # the verdict is what the reader under test raised
            if not path.exists():
                raise FileNotFoundError(path) from exc
            return FileOpen(0, _error_text(exc))

    def hourly(self, data_type: str, iid: str, day_start_ns: int, columns: Sequence[str]) -> Leg:
        """Read the query leg: 24 hourly `query(cls, identifiers=[iid], start=h, end=h+1h-1)`."""
        cls = self._classes[data_type]
        total, missing = Digest(), set[str]()
        for start in range(day_start_ns, day_start_ns + NS_PER_DAY, NS_PER_HOUR):
            objects = self._catalog.query(
                cls, identifiers=[iid], start=start, end=start + NS_PER_HOUR - 1
            )
            leg = digest_encoded(unwrap(objects), cls, columns)
            total += leg.digest
            missing.update(leg.missing_columns)
        return Leg(total, tuple(sorted(missing)))

    def settlement(self, iid: str) -> str | None:
        """Return the definition's settlement (else quote) currency; None without a definition."""
        found = self._catalog.instruments(instrument_ids=[iid])
        if not found:
            return None
        instrument = max(found, key=lambda i: i.ts_init)  # the definition in force last
        currency = getattr(instrument, "settlement_currency", None) or instrument.quote_currency
        return str(currency)
