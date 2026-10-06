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
Read-only catalog file helpers (DDD spine AD-D3): the read twin of `venue_http` -- file-span and
leaf listing plus column-projected Parquet reads of the second-snapshot rows over the catalog root,
and of the raw trade archive's integer columns (`query_trade_columns`, Story 32.8).

Invariant: reading only. Nothing here writes, renames or deletes a catalog file, or constructs a
`ParquetDataCatalog` (the catalog object's decoder turns every row's 20-level book into Python
objects; these helpers read the Parquet files directly with `pyarrow`). The directory names come
from Nautilus's own `class_to_filename`, so they can never drift from what `write_data` writes.
Files are selected by their name's `ts_init` span (`kernel.clocks.CatalogFileSpan`) and rows by
their exact `ts_event` (MEM-01: callers read one instrument, and the rebuilds one day, at a time).
Only `query_second_ohlc` and `query_top_of_book` widen the span by `READ_SPAN_MARGIN_NS`
(`query_index_prices` and `query_trade_columns` by `MAX_TS_INIT_SKEW_NS`);
`files_by_day` and `data_file_ranges` take the span as written, as their pre-kernel originals did --
the rebuild re-reads a whole day, so a row whose `ts_init` lands in the neighbouring file is picked
up there.

The snapshot readers decode the integer layout (Story 30.2) only through `kernel.second_snapshot`'s
column decoders (`trade_float_columns`, `top_of_book_units`, `price_of`/`quantity_of`); a
float-layout file is refused with `LegacySnapshotLayoutError` (`require_integer_layout`), never
read.

File faults (DATA-07), one policy per shape:
- A `*.parquet` name the catalog did not write (a hand-copied file, an editor's leftover) is
  reported through the caller's `on_foreign(FOREIGN_FILE_SITE, detail)` hook and skipped
  (`named_spans`); with no hook it is refused (`ValueError`), as before. `kernel` imports no
  context, so the hook is how a production caller ledgers it (`observability.error_ledger.record`).
- A file removed between the listing and its read (the nightly consolidation replacing minute
  files by their day file) makes a self-listing reader list again, up to `_LISTING_ATTEMPTS`
  listings; a listing that keeps losing files is refused (`CatalogReadError`, or
  `TradeDecodeError` for `query_trade_columns`). A file is never skipped with its rows lost, and
  a second (a trade) a relist meets in both the day file and a source is returned once.
- A file Parquet cannot read (truncated, corrupt) is refused with `CatalogReadError` naming it.
`second_ohlc_arrays` reads a path list its caller built, so it cannot list again: a vanished file
is refused (`CatalogReadError`) too -- its caller writes candles and never writes a partial read.
"""

import glob
import os
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NamedTuple
from typing import Protocol

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_MS
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.clocks import CatalogFileSpan
from kernel.second_snapshot import OHLC_UNIT_COLUMNS
from kernel.second_snapshot import PRECISION_COLUMNS
from kernel.second_snapshot import TOP_OF_BOOK_COLUMNS
from kernel.second_snapshot import VOLUME_UNIT_COLUMNS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from kernel.second_snapshot import price_of
from kernel.second_snapshot import quantity_of
from kernel.second_snapshot import require_integer_layout
from kernel.second_snapshot import top_of_book_units
from kernel.second_snapshot import trade_float_columns
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.instruments import CryptoFuture
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import FIXED_PRECISION_BYTES
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.funcs import class_to_filename


SNAPSHOT_DIRNAME = class_to_filename(DydxSecondSnapshot)  # "custom_dydx_second_snapshot"
_TRADE_READ_COLUMNS = ("ts_event", *PRECISION_COLUMNS, *OHLC_UNIT_COLUMNS, *VOLUME_UNIT_COLUMNS)
INDEX_PRICE_DIRNAME = class_to_filename(IndexPriceUpdate)  # "index_price_update"
# Every instrument-definition leaf a collected id is written as: perps, spot pairs, and Bybit's dated
# linear futures (`BTCUSDT-25SEP26-LINEAR.BYBIT`, written as `CryptoFuture`). Archive retention keeps
# a dropped coin's definitions while its trades stay (DW-208).
DEFINITION_DIRNAMES = (
    class_to_filename(CryptoPerpetual),  # "crypto_perpetual"
    class_to_filename(CurrencyPair),  # "currency_pair"
    class_to_filename(CryptoFuture),  # "crypto_future"
)
# The error-ledger site a production caller's `on_foreign` hook records a foreign file name at
# (published language: `GET /api/errors`, the durable ledger files).
FOREIGN_FILE_SITE = "catalog.foreign_file"
# A file listed for a read can be removed before it is opened: the nightly consolidation writes the
# merged day file first and only then removes its minute sources, so a fresh listing always holds
# the day file. A self-listing reader lists again up to this many times in all; a listing that keeps
# losing files is refused (`CatalogReadError`, or `TradeDecodeError` for the trade reader), and the
# caller ledgers it. A successful relist is not ledgered: no row was lost.
# A listing taken between the day file's rename and the sources' removal -- the likeliest state of
# a relist, since the removal is what made the first listing fail -- holds both, so the snapshot
# readers keep one copy per second (`_one_copy_per_second`), as the trade reader keeps one per
# trade.
# Known limit: the relist is immediate, and the consolidation removes a day's minute sources one by
# one, so a read whose every listing races that removal is refused (a ledgered 500 the client can
# retry). `query_index_prices` keeps every row: an update's second is not unique to it, so a
# relisted read during a consolidation can return an update twice (its one caller is `research`,
# not a served route). Upgrade path: back off between listings, or list under the maintenance
# lock, and de-duplicate index rows on (`ts_event`, `ts_init`, price).
_LISTING_ATTEMPTS = 2

# `(site, detail)`: what a reader reports a skipped foreign file name through. Compatible with
# `observability.error_ledger.record(site, detail="", exc=None)`, which production callers pass.
ForeignFileReporter = Callable[[str, str], None]


class CatalogReadError(ValueError):
    """
    A catalog Parquet file that cannot be read: truncated or corrupt, or files that kept
    disappearing between the listing and the read. The message names the file (or the instrument
    and the listings tried). Never skipped: the caller fails the request or refuses the instrument
    (DATA-07).
    """


def named_spans(
    paths: Iterable[str], on_foreign: ForeignFileReporter | None
) -> list[tuple[str, CatalogFileSpan]]:
    """
    Return each path with the `ts_init` span its catalog file name records, in the given order.

    A name the catalog did not write (`CatalogFileSpan.from_path` refuses it) is reported as
    `on_foreign(FOREIGN_FILE_SITE, "<path>: not a catalog file name (...); skipped")` and left out:
    the catalog's writers never produced it, and refusing it would fail the whole instrument over a
    stray file (operator decision DW-181). With `on_foreign` None the `ValueError` propagates (the
    caller decides).
    Known limit: `ParquetDataCatalog.query` (and so a `BacktestDataConfig` backtest) does read a
    file whose name it cannot parse, so a stray copy of real rows reaches a backtest twice while
    these readers skip it; the ledger line is what makes it visible. Upgrade path: the archive
    quarantines a foreign name out of the leaf, as the collector does for an unreadable file.
    """
    named: list[tuple[str, CatalogFileSpan]] = []
    for path in paths:
        try:
            span = CatalogFileSpan.from_path(path)
        except ValueError as exc:
            if on_foreign is None:
                raise
            on_foreign(FOREIGN_FILE_SITE, f"{path}: not a catalog file name ({exc}); skipped")
            continue
        named.append((path, span))
    return named


@contextmanager
def _reading(path: str) -> Iterator[None]:
    """
    Map a Parquet open/read failure of `path` to `CatalogReadError` naming it. `FileNotFoundError`
    (an `OSError`) passes through untouched: a vanished file is the listing's fault, which a
    self-listing reader answers by listing again.
    """
    try:
        yield
    except FileNotFoundError:
        raise
    except (pa.ArrowException, OSError) as exc:
        raise CatalogReadError(f"{path}: unreadable, refused: {exc}") from exc


def _relisting[T](instrument_id: str, read: Callable[[], T]) -> T:
    """
    Run `read` (which lists the files itself) until no listed file has vanished before its open,
    at most `_LISTING_ATTEMPTS` times; then refuse with `CatalogReadError` naming the instrument.
    """
    for _ in range(_LISTING_ATTEMPTS - 1):
        try:
            return read()
        except FileNotFoundError:
            continue
    try:
        return read()
    except FileNotFoundError as exc:
        raise CatalogReadError(
            f"{instrument_id}: catalog files kept disappearing during the read "
            f"({_LISTING_ATTEMPTS} listings): {exc}"
        ) from exc


def _reporting_once(on_foreign: ForeignFileReporter | None) -> ForeignFileReporter | None:
    """
    Wrap `on_foreign` for one read: a relist meets the same foreign file again, and one read
    reports it once. None stays None (the read refuses a foreign name).
    """
    if on_foreign is None:
        return None
    reported: set[str] = set()

    def report_once(site: str, detail: str) -> None:
        if detail not in reported:
            reported.add(detail)
            on_foreign(site, detail)

    return report_once


def _read_overlapping[R](
    listing: Callable[[], list[str]],
    instrument_id: str,
    window: tuple[int, int, int],
    read_file: Callable[[str], list[R]],
    on_foreign: ForeignFileReporter | None,
) -> list[R]:
    """
    Return the rows `read_file` gives for every listed file whose span overlaps `window`
    (`(start_ns, end_ns, margin_ns)`), in listing order, listing again on a vanished file; a
    foreign name is reported once per read, however many listings meet it.
    """
    start_ns, end_ns, margin_ns = window
    report = _reporting_once(on_foreign)

    def read_all() -> list[R]:
        rows: list[R] = []
        for path, span in named_spans(listing(), report):
            if span.overlaps(start_ns, end_ns, margin_ns):
                rows.extend(read_file(path))
        return rows

    return _relisting(instrument_id, read_all)


class _SecondRow(Protocol):
    @property
    def ts_event(self) -> int: ...


def _one_copy_per_second[S: _SecondRow](instrument_id: str, rows: list[S]) -> list[S]:
    """
    Sort `rows` by `ts_event` and keep the first copy of each `ts_event`. A snapshot row is one
    second of one instrument, so a repeated `ts_event` is the same row listed in two files (a
    consolidated day file beside a source not yet removed); a copy that disagrees with the kept one
    is an archive fault, refused with `CatalogReadError` (DATA-07), never one of them silently
    picked. The key is the exact `ts_event`, not its floor second: two rows of one floor second at
    different `ts_event`s (a sampling anomaly, not a listing artefact) both pass, as before this
    helper; `archive.application.rebuild_day` refuses that day (`rebuild.duplicate_second`).
    """
    rows.sort(key=lambda r: r.ts_event)
    kept: list[S] = []
    for row in rows:
        if kept and kept[-1].ts_event == row.ts_event:
            if kept[-1] != row:
                raise CatalogReadError(
                    f"{instrument_id}: second {row.ts_event} is stored twice with different "
                    f"values ({kept[-1]} vs {row}), refused"
                )
            continue
        kept.append(row)
    return kept


def _leaf_files(catalog_path: str, dirname: str, instrument_id: str) -> list[str]:
    """Every Parquet file of one data directory's instrument leaf (a directory listing, unordered)."""
    return glob.glob(os.path.join(catalog_path, "data", dirname, instrument_id, "*.parquet"))


def snapshot_files(catalog_path: str, instrument_id: str) -> list[str]:
    """Every second-snapshot Parquet file of the instrument (a directory listing, unordered)."""
    return _leaf_files(catalog_path, SNAPSHOT_DIRNAME, instrument_id)


def data_file_ranges(
    catalog_path: str, instrument_id: str, *, on_foreign: ForeignFileReporter | None = None
) -> list[tuple[int, int]]:
    """
    Return ascending (start_ns, end_ns) of every second-snapshot Parquet file for the instrument.

    Read from the catalog's filenames -- a directory listing, no Parquet I/O. Lets paging routes know
    where data actually exists instead of guessing with fixed-size probe windows (a data gap wider
    than the window otherwise reads as "no more history"). A foreign file name is reported through
    `on_foreign` and skipped (`named_spans`; refused without a hook).
    """
    spans = named_spans(snapshot_files(catalog_path, instrument_id), on_foreign)
    return sorted((span.start_ns, span.end_ns) for _, span in spans)


def files_by_day(
    catalog_path: str,
    iid: str,
    start_ns: int,
    end_ns: int,
    *,
    on_foreign: ForeignFileReporter | None = None,
) -> dict[int, list[str]]:
    """
    UTC day index -> that day's snapshot files, from one directory listing. A file whose span
    crosses midnight is listed under both days; the rebuild filters rows by timestamp. A day's list
    is whole only when [start_ns, end_ns] covers that whole day: a day outside it can appear here
    through a file crossing the range's first or last midnight, with that one file only, and a day
    the range covers in part lists only its files overlapping the range. A caller must never
    rebuild or judge a day from a partial list (audit D-145): it lists over whole days. A foreign
    file name is reported through `on_foreign` and skipped (`named_spans`; refused without a hook).
    """
    days: dict[int, list[str]] = {}
    for path, span in named_spans(snapshot_files(catalog_path, iid), on_foreign):
        if not span.overlaps(start_ns, end_ns):
            continue
        for day in range(span.start_ns // NS_PER_DAY, span.end_ns // NS_PER_DAY + 1):
            days.setdefault(day, []).append(path)
    return days


def query_second_ohlc(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    *,
    on_foreign: ForeignFileReporter | None = None,
) -> list[SecondOHLC]:
    """
    Per-second OHLC + volume rows in [start_ns, end_ns] (ts_event), read straight from the
    catalog's Parquet files with only the trade columns candles need (plus the two precisions they
    are decoded at, `trade_float_columns`).

    `catalog_stats.query_second_snapshots` deserialises every row's 20-level book into Python
    objects through the catalog decoder -- ~95% of a candle request's time (Story 21.5 profile)
    for fields candles never look at. Same files, same rows, same values; just a column projection.

    A foreign file name is reported through `on_foreign` and skipped; a vanished file makes the
    read list again; an unreadable file, or a listing that keeps losing files, raises
    `CatalogReadError` (module docstring). One row per second: a second two listed files both hold
    is returned once, or refused when the copies disagree (`_one_copy_per_second`).
    """
    rows = _read_overlapping(
        lambda: snapshot_files(catalog_path, instrument_id),
        instrument_id,
        (start_ns, end_ns, READ_SPAN_MARGIN_NS),
        lambda path: _ohlc_rows(path, start_ns, end_ns),
        on_foreign,
    )
    return _one_copy_per_second(instrument_id, rows)


def _read_snapshot_columns(path: str, columns: list[str], start_ns: int, end_ns: int) -> pa.Table:
    """
    Read the given columns of one integer-layout snapshot file, rows with `ts_event` in the window.
    A vanished file raises `FileNotFoundError`, an unreadable one `CatalogReadError` (`_reading`).
    """
    with _reading(path):
        require_integer_layout(pq.read_schema(path), path)
        return pq.read_table(
            path,
            columns=columns,
            filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
        )


def _optional(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def _ohlc_rows(path: str, start_ns: int, end_ns: int) -> list[SecondOHLC]:
    table = _read_snapshot_columns(path, list(_TRADE_READ_COLUMNS), start_ns, end_ns)
    values = trade_float_columns(table)
    ts = table.column("ts_event").to_pylist()
    return [
        SecondOHLC(
            ts[i],
            *(_optional(values[name][i]) for name in OHLC_UNIT_COLUMNS),
            *(float(values[name][i]) for name in VOLUME_UNIT_COLUMNS),
        )
        for i in range(table.num_rows)
    ]


class TopOfBook(NamedTuple):
    """
    Level 0 of one second-snapshot row: what a quote needs, without the 20-level book, as exact
    `Price`/`Quantity` values at the row's stored precisions (`Price.from_raw`, no float step).
    """

    ts_event: int
    ts_init: int
    bid_price: Price
    bid_size: Quantity
    ask_price: Price
    ask_size: Quantity


def query_top_of_book(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    *,
    on_foreign: ForeignFileReporter | None = None,
) -> list[TopOfBook]:
    """
    Level-0 bid/ask price and size of every second-snapshot row in [start_ns, end_ns] (ts_event),
    sorted by `ts_event`; the same file selection and file-fault handling as `query_second_ohlc`.

    A row with an empty side is omitted: it has no top of book to quote. Only element 0 of each
    list column leaves Arrow, so a multi-day window never builds the 20-level book in Python
    (MEM-01), which is what the catalog decoder would do.
    """
    rows = _read_overlapping(
        lambda: snapshot_files(catalog_path, instrument_id),
        instrument_id,
        (start_ns, end_ns, READ_SPAN_MARGIN_NS),
        lambda path: _top_rows(path, start_ns, end_ns),
        on_foreign,
    )
    return _one_copy_per_second(instrument_id, rows)


def _top_rows(path: str, start_ns: int, end_ns: int) -> list[TopOfBook]:
    table = _read_snapshot_columns(path, list(TOP_OF_BOOK_COLUMNS), start_ns, end_ns)
    return [
        TopOfBook(
            top.ts_event,
            top.ts_init,
            price_of(top.bid_price, top.price_precision),
            quantity_of(top.bid_size, top.size_precision),
            price_of(top.ask_price, top.price_precision),
            quantity_of(top.ask_size, top.size_precision),
        )
        for top in top_of_book_units(table)
    ]


class IndexPrice(NamedTuple):
    """One `IndexPriceUpdate` row: the price is a `Price` at its file's own precision label."""

    ts_event: int
    ts_init: int
    price: Price


def query_index_prices(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    *,
    on_foreign: ForeignFileReporter | None = None,
) -> list[IndexPrice]:
    """
    Every `IndexPriceUpdate` row of the instrument in [start_ns, end_ns] (ts_event), sorted by
    `ts_event`. Files are chosen by their `ts_init` span widened by `MAX_TS_INIT_SKEW_NS` (these are
    not snapshot rows, so the whole writer skew bound applies, not `READ_SPAN_MARGIN_NS`).

    `ParquetDataCatalog.query(IndexPriceUpdate, ...)` raises NotImplementedError in the pinned
    nautilus_trader (1.228: no Arrow decoder, and the Rust backend has no such data type), so this
    reads the three columns directly. The Rust writer stores `value` as the fixed-point raw integer
    (little-endian, `FIXED_PRECISION_BYTES` wide in this build) and the precision label in the
    file's `price_precision` metadata; `Price.from_raw` rebuilds the exact value with no float step
    (NAUT-01). A file without that label, whose raw width is not this build's, or holding a raw
    value finer than its label, raises `ValueError` naming it -- never a guessed precision.
    File faults are handled as in `query_second_ohlc` (module docstring).
    """
    rows = _read_overlapping(
        lambda: _leaf_files(catalog_path, INDEX_PRICE_DIRNAME, instrument_id),
        instrument_id,
        (start_ns, end_ns, MAX_TS_INIT_SKEW_NS),
        lambda path: _index_rows(path, start_ns, end_ns),
        on_foreign,
    )
    rows.sort(key=lambda r: r.ts_event)
    return rows


def _index_rows(path: str, start_ns: int, end_ns: int) -> list[IndexPrice]:
    with _reading(path):
        table = pq.read_table(
            path,
            columns=["value", "ts_event", "ts_init"],
            filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
        )
    metadata = table.schema.metadata or {}
    if b"price_precision" not in metadata:
        raise ValueError(f"{path}: no price_precision metadata, the raw values cannot be read")
    precision = int(metadata[b"price_precision"])
    if not 0 <= precision <= FIXED_PRECISION:
        raise ValueError(
            f"{path}: price_precision {precision} is outside this build's 0..{FIXED_PRECISION}"
        )
    step = 10 ** (FIXED_PRECISION - precision)
    rows = []
    for raw, ts_event, ts_init in zip(
        table.column("value").to_pylist(),
        table.column("ts_event").to_pylist(),
        table.column("ts_init").to_pylist(),
        strict=True,
    ):
        if raw is None:
            raise ValueError(f"{path}: a null raw price at ts_event {ts_event}")
        if len(raw) != FIXED_PRECISION_BYTES:
            raise ValueError(
                f"{path}: a {len(raw)}-byte raw price, this build reads {FIXED_PRECISION_BYTES}"
            )
        value = int.from_bytes(raw, "little", signed=True)
        if value % step:
            # `Price.from_raw` would accept it and mislabel the value (the mixed-precision history).
            raise ValueError(
                f"{path}: raw price {value} at ts_event {ts_event} has more digits than the "
                f"file's price_precision {precision}"
            )
        price = Price.from_raw(value, precision)
        rows.append(IndexPrice(ts_event, ts_init, price))
    return rows


class PrecisionLabel(NamedTuple):
    """One Parquet file's `price_precision` metadata label (None when the file carries none)."""

    path: str
    price_precision: int | None


def price_precision_labels(
    catalog_path: str,
    data_cls: type,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    *,
    on_foreign: ForeignFileReporter | None = None,
) -> list[PrecisionLabel]:
    """
    Return the `price_precision` label of every `data_cls` file of the instrument whose `ts_init` span,
    widened by `MAX_TS_INIT_SKEW_NS`, overlaps [start_ns, end_ns], sorted by path. Schema metadata
    only, no row is read: `ParquetDataCatalog` refuses to read files whose labels disagree (the
    dYdX mark/index incident, CLAUDE.md), so disagreeing labels are only visible here. File faults
    are handled as in `query_second_ohlc` (module docstring).
    """
    dirname = class_to_filename(data_cls)
    return _read_overlapping(
        lambda: sorted(_leaf_files(catalog_path, dirname, instrument_id)),
        instrument_id,
        (start_ns, end_ns, MAX_TS_INIT_SKEW_NS),
        lambda path: [_precision_label(path)],
        on_foreign,
    )


def _precision_label(path: str) -> PrecisionLabel:
    with _reading(path):
        metadata = pq.read_schema(path).metadata or {}
    label = metadata.get(b"price_precision")
    return PrecisionLabel(path, None if label is None else _precision(path, label))


def _precision(path: str, label: bytes) -> int:
    try:
        return int(label)
    except ValueError:
        raise ValueError(f"{path}: price_precision metadata is not an integer: {label!r}") from None


def second_ohlc_arrays(paths: list[str]) -> dict[str, np.ndarray]:
    """
    Read OHLC + volume columns of the given snapshot files as arrays.

    Keys `ts_ms`, `o`, `h`, `l`, `c`, `v` (NaN = no trade that second), decoded from units at each
    row's precisions (`trade_float_columns`); each file opened once. The rebuild's read path: no
    per-row Python objects, no per-call directory scan. A float-layout file raises
    `LegacySnapshotLayoutError`. A file that is gone or unreadable raises `CatalogReadError` naming
    it: the list is the caller's, so it cannot be listed again here, and the caller (the candle
    rebuild) must refuse rather than write candles from a partial read.
    """
    out = {k: np.empty(0, dtype=np.float64) for k in ("o", "h", "l", "c", "v")}
    out["ts_ms"] = np.empty(0, dtype=np.int64)
    parts = [_ohlc_part(path) for path in paths]
    if not parts:
        return out
    joined = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    out["ts_ms"] = joined["ts_event"] // NS_PER_MS
    out["o"], out["h"], out["l"], out["c"] = (joined[k] for k in OHLC_UNIT_COLUMNS)
    out["v"] = joined["buy_volume"] + joined["sell_volume"]
    return out


def _ohlc_part(path: str) -> dict[str, np.ndarray]:
    """One file's decoded OHLC + volume columns and `ts_event`, for `second_ohlc_arrays`."""
    try:
        with _reading(path):
            pf = pq.ParquetFile(path)
            require_integer_layout(pf.schema_arrow, path)
            table = pf.read(columns=list(_TRADE_READ_COLUMNS))
    except FileNotFoundError as exc:
        raise CatalogReadError(f"{path}: gone before it was read, refused: {exc}") from exc
    values = trade_float_columns(table)
    values["ts_event"] = table.column("ts_event").to_numpy().astype(np.int64)
    return values


# -- raw trades (Story 32.8) -------------------------------------------------------------------------

TRADE_DIRNAME = class_to_filename(TradeTick)  # "trade_tick"
_TRADE_TICK_COLUMNS = ("price", "size", "aggressor_side", "ts_event", "trade_id")
_TRADE_PRECISION_LABELS = (b"price_precision", b"size_precision")
# `decimal128`'s widest precision: its 16-byte value layout is exactly a `binary(16)` raw's (an
# i128, little-endian), which is what lets the decode reinterpret the buffers without a copy.
_DECIMAL128_DIGITS = 38
_DECIMAL128_BYTES = 16


class TradeDecodeError(ValueError):
    """
    A stored trade that cannot be read exactly: a raw finer than the instrument's precision, a raw
    outside int64 units, a null, a raw width this build does not write, a file without its
    precision labels or one Parquet cannot read. Never rounded, never skipped: the caller fails the request (DATA-07).
    """


class TradeColumns(NamedTuple):
    """
    A window's archived trades as parallel arrays, sorted by `ts_event`: `price`/`size` are integer
    counts of `10^-price_precision`/`10^-size_precision` at the precisions the read was asked for
    (the instrument definition's), `buyer` is True for an `AggressorSide.BUYER` trade only.
    """

    price: np.ndarray  # int64 units
    size: np.ndarray  # int64 units
    buyer: np.ndarray  # bool
    ts_event: np.ndarray  # int64 ns


def trade_files(catalog_path: str, instrument_id: str) -> list[str]:
    """Every `TradeTick` Parquet file of the instrument, sorted by name (a directory listing)."""
    return sorted(_leaf_files(catalog_path, TRADE_DIRNAME, instrument_id))


def query_trade_columns(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    price_precision: int,
    size_precision: int,
    *,
    on_foreign: ForeignFileReporter | None = None,
) -> TradeColumns:
    """
    Every archived trade of the instrument with `ts_event` in the half-open `[start_ns, end_ns)`,
    as integer units (`TradeColumns`), read column-projected (`price`, `size`, `aggressor_side`,
    `ts_event`, `trade_id`) straight from the Parquet files and never through `float`.

    Files are chosen by their `ts_init` span widened by `MAX_TS_INIT_SKEW_NS` (the largest
    `ts_init - ts_event` a writer may produce, the rebuild's own trade margin), consolidated day
    files and live minute files alike. A trade stored twice (a restart replay, or a minute file
    and the day file holding the same rows) shares its `ts_event` and `trade_id`, and is kept once;
    two copies that disagree on price, size or side raise `TradeDecodeError`.
    Units are taken at the precisions given, not each file's label, so history written under
    different labels aggregates on one grid; a raw finer than them raises `TradeDecodeError`.
    Known limit: after a venue coarsens an instrument's tick (or lot) the definition's precision
    is coarser than the trades archived before the change, so every window holding those trades
    is refused until they leave the trade retention. Upgrade path: decode each file at its own
    label and aggregate on the finest precision of the window, served alongside the units.

    A file removed between the listing and its read (the consolidation replacing minute files by
    their day file) makes the read list again, up to `_LISTING_ATTEMPTS` listings in all. A
    foreign file name is reported through `on_foreign` once per read, however many listings meet
    it, and skipped (`named_spans`; refused without a hook).

    MEM-01: the caller bounds the window; this reads all of it at once.
    """
    if not (0 <= price_precision <= FIXED_PRECISION and 0 <= size_precision <= FIXED_PRECISION):
        raise TradeDecodeError(
            f"{instrument_id}: precisions ({price_precision}, {size_precision}) are outside this "
            f"build's 0..{FIXED_PRECISION}"
        )
    report = _reporting_once(on_foreign)
    for attempt in range(1, _LISTING_ATTEMPTS + 1):
        try:
            parts = [
                _trade_part(path, start_ns, end_ns, price_precision, size_precision)
                for path, span in named_spans(trade_files(catalog_path, instrument_id), report)
                if span.overlaps(start_ns, end_ns, MAX_TS_INIT_SKEW_NS)
            ]
            break
        except FileNotFoundError as exc:
            if attempt == _LISTING_ATTEMPTS:
                raise TradeDecodeError(
                    f"{instrument_id}: trade files kept disappearing during the read "
                    f"({_LISTING_ATTEMPTS} listings): {exc}"
                ) from exc
    parts = [part for part in parts if len(part[0])]
    if not parts:
        empty = np.empty(0, dtype=np.int64)
        return TradeColumns(empty, empty.copy(), np.empty(0, dtype=bool), empty.copy())
    return _unique_sorted(instrument_id, parts)


def _trade_part(
    path: str, start_ns: int, end_ns: int, price_precision: int, size_precision: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pa.Array]:
    """
    One file's trades in the window: `(price, size, aggressor_side, ts_event, trade_id)` arrays.
    A file that is gone raises `FileNotFoundError` (the caller lists again); one that cannot be
    read (truncated, corrupt) is an archive fault, `TradeDecodeError` naming it (DATA-07).
    """
    try:
        metadata = pq.read_schema(path).metadata or {}
        missing = [label.decode() for label in _TRADE_PRECISION_LABELS if label not in metadata]
        if missing:
            raise TradeDecodeError(f"{path}: no {', '.join(missing)} metadata, refused")
        table = pq.read_table(
            path,
            columns=list(_TRADE_TICK_COLUMNS),
            filters=[("ts_event", ">=", start_ns), ("ts_event", "<", end_ns)],
        )
    except FileNotFoundError:
        raise
    except (pa.ArrowInvalid, OSError) as exc:
        raise TradeDecodeError(f"{path}: unreadable, refused: {exc}") from exc
    for name in _TRADE_TICK_COLUMNS:
        if table.column(name).null_count:
            raise TradeDecodeError(f"{path}: a null {name}, refused")
    return (
        _raw_units(path, "price", table.column("price"), price_precision),
        _raw_units(path, "size", table.column("size"), size_precision),
        table.column("aggressor_side").to_numpy(),
        table.column("ts_event").to_numpy().astype(np.int64),
        table.column("trade_id").combine_chunks(),
    )


def _raw_units(path: str, name: str, column: pa.ChunkedArray, precision: int) -> np.ndarray:
    """
    Return the fixed-point raws of one column as int64 units at `precision`, exactly: the `binary(16)`
    buffers reinterpreted as `decimal128(38, FIXED_PRECISION)`, rescaled by Arrow (which refuses a
    lossy rescale), reinterpreted as `decimal128(38, 0)` and cast to int64 (overflow refused).
    """
    # The decode reinterprets the raws as `decimal128`, so it reads 16-byte raws only, whatever
    # width this build writes (a 64-bit-precision build's 8-byte raws are refused, never misread).
    if column.type != pa.binary(_DECIMAL128_BYTES) or FIXED_PRECISION_BYTES != _DECIMAL128_BYTES:
        raise TradeDecodeError(
            f"{path}: column {name} is {column.type}, this build reads "
            f"{pa.binary(_DECIMAL128_BYTES)} raws"
        )
    raws = column.combine_chunks()
    as_raw = pa.decimal128(_DECIMAL128_DIGITS, FIXED_PRECISION)
    as_units = pa.decimal128(_DECIMAL128_DIGITS, 0)
    try:
        exact = pc.cast(_reinterpret(raws, as_raw), pa.decimal128(_DECIMAL128_DIGITS, precision))
        units = pc.cast(_reinterpret(exact, as_units), pa.int64())
    except pa.ArrowInvalid as exc:
        raise TradeDecodeError(
            f"{path}: column {name} is not exact in int64 units at precision {precision}: {exc}"
        ) from exc
    return units.to_numpy()


def _reinterpret(array: pa.Array, to: pa.DataType) -> pa.Array:
    return pa.Array.from_buffers(to, len(array), array.buffers(), offset=array.offset)


def _unique_sorted(
    instrument_id: str,
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, pa.Array]],
) -> TradeColumns:
    """
    Concatenate the files' parts, keep the first copy of each (`ts_event`, `trade_id`), sort. A
    copy is the same trade only when its price, size and side agree too; two stored versions of one
    trade that disagree are an archive fault, refused with `TradeDecodeError` (DATA-07), never one
    of them silently picked. Sides compare as stored, so a SELLER and a NO_AGGRESSOR copy disagree
    although both fold as a sell.
    """
    price, size, side, ts = (np.concatenate([part[i] for part in parts]) for i in range(4))
    ids = pa.concat_arrays([part[4].cast(pa.large_string()) for part in parts])
    codes = ids.dictionary_encode().indices.to_numpy()
    order = np.lexsort((codes, ts))  # stable: the first copy in file order stays first
    ts_sorted, codes_sorted = ts[order], codes[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = (ts_sorted[1:] != ts_sorted[:-1]) | (codes_sorted[1:] != codes_sorted[:-1])
    _require_agreeing_copies(instrument_id, order, first, (price, size, side), ids)
    keep = order[first]
    return TradeColumns(price[keep], size[keep], side[keep] == int(AggressorSide.BUYER), ts[keep])


def _require_agreeing_copies(
    instrument_id: str,
    order: np.ndarray,
    first: np.ndarray,
    values: tuple[np.ndarray, np.ndarray, np.ndarray],
    ids: pa.Array,
) -> None:
    """Refuse a repeated (`ts_event`, `trade_id`) whose copy differs from the kept first copy."""
    group_first = order[np.maximum.accumulate(np.where(first, np.arange(len(order)), 0))]
    copies = order[~first]
    kept = group_first[~first]
    differs = np.zeros(len(copies), dtype=bool)
    for column in values:
        differs |= column[copies] != column[kept]
    if differs.any():
        bad = int(copies[np.argmax(differs)])
        raise TradeDecodeError(
            f"{instrument_id}: trade {ids[bad].as_py()!r} is stored twice with different "
            f"price, size or side, refused"
        )
