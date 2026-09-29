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
The derivs tool's catalog reads (Story 31.6): raw Parquet through pyarrow, never through
`ParquetDataCatalog`, `kernel.open_interest` or `kernel.catalog_files` -- the reference side never
imports the code it checks.

Layout read (Nautilus's own, written by `ParquetDataCatalog.write_data`), per instrument id:
- `data/mark_price_update/<iid>/`, `data/index_price_update/<iid>/`: `value`
  (`fixed_size_binary[16]`, signed little-endian at 10^16, decoded by `trade_check.fixed_raw` /
  `decode_fixed`), `ts_event`, `ts_init`, and the file's `price_precision` schema metadata (its
  label; a file with rows in the window and no label is refused by name);
- `data/funding_rate_update/<iid>/`: `rate` (JSON-quoted decimal text), `interval` and
  `next_funding_ns` (nullable), `ts_event`, `ts_init`;
- `data/custom_open_interest/<iid>/`: `open_interest` (decimal text), `ts_event`, `ts_init`;
- `data/crypto_perpetual/<iid>/`, `data/currency_pair/<iid>/`: every stored definition row.

Every value becomes a `Decimal` or an int, never a float; a value that is not decimal text, a
null value or a null clock is refused (`ValueError` naming the file). A file or row group is
skipped only by its own `ts_event` statistics lying wholly outside the window
(`catalog_reader.read_window`).

Known limit (memory, MEM-01): one instrument-type-day of rows as Python objects. The busiest stream,
Bybit BTCUSDT's index price, measured ~7.6k rows an hour on the soak (30,347 over 12:59-17:00Z),
so ~180k `StoredValue`s a full day -- ~300 bytes each with their `Decimal` and tuple, ~55 MB -- plus
its ~180k reference frames. Upgrade path: read and judge hour windows, carrying the reference state
across them. Definitions are read whole (one row per collector start).
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from verification.domain.derivs_check import FUNDING
from verification.domain.derivs_check import INDEX
from verification.domain.derivs_check import MARK
from verification.domain.derivs_check import OPEN_INTEREST
from verification.domain.derivs_check import FileLabel
from verification.domain.derivs_check import StoredValue
from verification.domain.derivs_check import stored_decimal
from verification.domain.instrument_check import FIELDS
from verification.domain.instrument_check import PRICE_PRECISION
from verification.domain.instrument_check import SIZE_PRECISION
from verification.domain.instrument_check import StoredDefinition
from verification.domain.trade_check import decode_fixed
from verification.domain.trade_check import fixed_raw
from verification.domain.trade_check import whole_at
from verification.infrastructure.catalog_reader import read_window


TYPE_DIRS = MappingProxyType(
    {
        MARK: "mark_price_update",
        INDEX: "index_price_update",
        FUNDING: "funding_rate_update",
        OPEN_INTEREST: "custom_open_interest",
    }
)
# Every definition type a Bybit or Hyperliquid id can be written as: perps, spot pairs, and Bybit's
# dated linear futures (`BTCUSDT-25SEP26-LINEAR.BYBIT`), which the adapter writes as `CryptoFuture`.
DEFINITION_DIRS = ("crypto_perpetual", "currency_pair", "crypto_future")
SPOT_SUFFIX = "-SPOT.BYBIT"
_TS_EVENT = "ts_event"
_TS_INIT = "ts_init"
_LABEL = b"price_precision"
_COLUMNS = MappingProxyType(
    {
        MARK: ("value", _TS_EVENT, _TS_INIT),
        INDEX: ("value", _TS_EVENT, _TS_INIT),
        FUNDING: ("rate", "interval", "next_funding_ns", _TS_EVENT, _TS_INIT),
        OPEN_INTEREST: ("open_interest", _TS_EVENT, _TS_INIT),
    }
)
# Columns that must never be null: the clocks and the stored value itself.
_REQUIRED = MappingProxyType(
    {MARK: "value", INDEX: "value", FUNDING: "rate", OPEN_INTEREST: "open_interest"}
)


@dataclass(frozen=True)
class PriceRows:
    """A mark or index window: its rows and each file's label (files with rows in it only)."""

    rows: tuple[StoredValue, ...]
    files: tuple[FileLabel, ...]


def _label(path: Path) -> int:
    metadata = pq.read_schema(path).metadata or {}
    text = metadata.get(_LABEL)
    if text is None or not text.isdigit():
        raise ValueError(f"{path}: a mark/index file without its `price_precision` label")
    return int(text)


def _check_nulls(table: pa.Table, kind: str, path: Path) -> None:
    for column in (_TS_EVENT, _TS_INIT, _REQUIRED[kind]):
        if table[column].null_count:
            raise ValueError(f"{path}: a row with a null `{column}`")


def _price_row(row: Mapping[str, Any], label: int) -> StoredValue:
    raw = fixed_raw(row["value"])
    return StoredValue(
        row[_TS_EVENT], row[_TS_INIT], (decode_fixed(raw),), on_grid=whole_at(raw, label)
    )


def funding_row(row: Mapping[str, Any], where: str) -> StoredValue:
    """Decode one stored funding row; a `rate` that is not JSON decimal text is a `ValueError`."""
    try:
        text = json.loads(row["rate"])
    # A `TypeError` (a `rate` that is not text or bytes) is a refusal like any undecodable value,
    # never an unledgered crash.
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{where}: funding `rate` {row['rate']!r:.80} is not JSON text") from exc
    rate = stored_decimal(text, f"{where} `rate`")
    value = (rate, row["interval"], row["next_funding_ns"])
    return StoredValue(row[_TS_EVENT], row[_TS_INIT], value)


def _open_interest_row(row: Mapping[str, Any], where: str) -> StoredValue:
    value = stored_decimal(row["open_interest"], f"{where} `open_interest`")
    return StoredValue(row[_TS_EVENT], row[_TS_INIT], (value,))


class DerivsCatalog:
    """The catalog's derivative rows, definitions and spot directories, read raw (module doc)."""

    def __init__(self, catalog: Path) -> None:
        self._catalog = catalog

    def _files(self, directory: str, instrument_id: str) -> list[Path]:
        return sorted((self._catalog / "data" / directory / instrument_id).glob("*.parquet"))

    def _windows(
        self, kind: str, instrument_id: str, start_ns: int, end_ns: int
    ) -> list[tuple[Path, pa.Table]]:
        found = []
        for path in self._files(TYPE_DIRS[kind], instrument_id):
            table = read_window(path, list(_COLUMNS[kind]), start_ns, end_ns)
            if table is not None and table.num_rows:
                _check_nulls(table, kind, path)
                found.append((path, table))
        return found

    def prices(
        self, kind: str, instrument_id: str, start_ns: int, end_ns: int, day: range
    ) -> PriceRows:
        """
        Every mark (`kind` MARK) or index row with `ts_event` in the window, and the label of each
        file with a row in `day` (ns of `ts_event`): a window reaching past the day (Hyperliquid's)
        must not bring a neighbour day's label into the day's.
        """
        rows: list[StoredValue] = []
        files: list[FileLabel] = []
        for path, table in self._windows(kind, instrument_id, start_ns, end_ns):
            label = _label(path)
            events, inits = table[_TS_EVENT].to_pylist(), table[_TS_INIT].to_pylist()
            stamps = [init for event, init in zip(events, inits, strict=True) if event in day]
            if stamps:
                files.append(FileLabel(path.name, label, min(stamps), max(stamps)))
            rows += [_price_row(row, label) for row in table.to_pylist()]
        return PriceRows(tuple(rows), tuple(files))

    def funding(self, instrument_id: str, start_ns: int, end_ns: int) -> tuple[StoredValue, ...]:
        """Every funding row with `ts_event` in the window, the rate decoded from its JSON text."""
        return tuple(
            funding_row(row, str(path))
            for path, table in self._windows(FUNDING, instrument_id, start_ns, end_ns)
            for row in table.to_pylist()
        )

    def open_interest(
        self, instrument_id: str, start_ns: int, end_ns: int
    ) -> tuple[StoredValue, ...]:
        """Every open-interest row with `ts_event` in the window."""
        return tuple(
            _open_interest_row(row, str(path))
            for path, table in self._windows(OPEN_INTEREST, instrument_id, start_ns, end_ns)
            for row in table.to_pylist()
        )

    def definitions(self, instrument_id: str) -> tuple[StoredDefinition, ...]:
        """Every stored definition row of the instrument, of either definition type, every file."""
        stored = []
        for directory in DEFINITION_DIRS:
            for path in self._files(directory, instrument_id):
                columns = [*FIELDS, _TS_INIT]
                stored += [
                    _definition(row, path)
                    for row in pq.read_table(path, columns=columns).to_pylist()
                ]
        return tuple(sorted(stored, key=lambda definition: definition.ts_init))

    def spot_rows(self, start_ns: int, end_ns: int, plan_spot: tuple[str, ...]) -> dict[str, int]:
        """
        `<type directory>/<id>` -> rows with `ts_event` in the window, for every `-SPOT.BYBIT` id
        directory under the four derivative types, and every plan spot id (0 when absent).
        """
        counts: dict[str, int] = {}
        for directory in TYPE_DIRS.values():
            root = self._catalog / "data" / directory
            found = {p.name for p in root.glob(f"*{SPOT_SUFFIX}") if p.is_dir()}
            for instrument_id in sorted(found | set(plan_spot)):
                counts[f"{directory}/{instrument_id}"] = self._count(
                    directory, instrument_id, start_ns, end_ns
                )
        return counts

    def _count(self, directory: str, instrument_id: str, start_ns: int, end_ns: int) -> int:
        total = 0
        for path in self._files(directory, instrument_id):
            table = read_window(path, [_TS_EVENT], start_ns, end_ns)
            total += 0 if table is None else table.num_rows
        return total


def _definition(row: Mapping[str, Any], path: Path) -> StoredDefinition:
    where = str(path)
    if row[_TS_INIT] is None or row[PRICE_PRECISION] is None or row[SIZE_PRECISION] is None:
        raise ValueError(f"{where}: a definition row with a null clock or precision")
    fields: dict[str, Any] = {}
    for name in FIELDS:
        value = row[name]
        if name in (PRICE_PRECISION, SIZE_PRECISION) or value is None:
            fields[name] = value
        else:
            fields[name] = stored_decimal(value, f"{where} `{name}`")
    return StoredDefinition(row[_TS_INIT], MappingProxyType(fields))
