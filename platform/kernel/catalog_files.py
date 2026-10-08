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
and of the raw trade archive's integer columns (`query_trade_columns`, Story 32.8), and of the
archived liquidations (`query_liquidations`, and their archive-side start,
`liquidation_feed_since_ns`, Story 33.3), and of the archived open interest
(`query_open_interest`, Story 33.4) and mark and index price columns (`query_price_columns`, with
`newest_ts_event_before` for a sparse page's gap jump, Story 33.4 review), and of the books at chosen
stamps (`query_snapshot_times` then `query_books_at`, Story 33.6).

Invariant: reading only. Nothing here writes, renames or deletes a catalog file, or constructs a
`ParquetDataCatalog` (the catalog object's decoder turns every row's 20-level book into Python
objects; these helpers read the Parquet files directly with `pyarrow`). The directory names come
from Nautilus's own `class_to_filename`, so they can never drift from what `write_data` writes.
Files are selected by their name's `ts_init` span (`kernel.clocks.CatalogFileSpan`) and rows by
their exact `ts_event` (MEM-01: callers read one instrument, and the rebuilds one day, at a time).
Only `query_second_ohlc`, `query_top_of_book` and the two Story 33.6 book readers widen the span by
`READ_SPAN_MARGIN_NS` (`query_index_prices`, `query_price_columns`, `query_trade_columns`, `query_liquidations`,
`query_open_interest` and `newest_ts_event_before` by `MAX_TS_INIT_SKEW_NS`);
`files_by_day` and `data_file_ranges` take the span as written, as their pre-kernel originals did --
the rebuild re-reads a whole day, so a row whose `ts_init` lands in the neighbouring file is picked
up there.

The snapshot readers decode the integer layout (Story 30.2) only through `kernel.second_snapshot`'s
column decoders (`trade_float_columns`, `top_of_book_units`, `book_float_rows`,
`price_of`/`quantity_of`); a
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
from collections.abc import Sequence
from contextlib import contextmanager
from decimal import Decimal
from types import MappingProxyType
from typing import Any
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
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import OHLC_UNIT_COLUMNS
from kernel.second_snapshot import PRECISION_COLUMNS
from kernel.second_snapshot import TOP_OF_BOOK_COLUMNS
from kernel.second_snapshot import VOLUME_UNIT_COLUMNS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from kernel.second_snapshot import book_float_rows
from kernel.second_snapshot import price_of
from kernel.second_snapshot import quantity_of
from kernel.second_snapshot import require_integer_layout
from kernel.second_snapshot import top_of_book_units
from kernel.second_snapshot import trade_float_columns
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
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
_COUNT_COLUMNS = ("buy_count", "sell_count")
_TRADE_READ_COLUMNS = (
    "ts_event",
    *PRECISION_COLUMNS,
    *OHLC_UNIT_COLUMNS,
    *VOLUME_UNIT_COLUMNS,
    *_COUNT_COLUMNS,
)
INDEX_PRICE_DIRNAME = class_to_filename(IndexPriceUpdate)  # "index_price_update"
LIQUIDATION_DIRNAME = class_to_filename(Liquidation)  # "custom_liquidation"
OPEN_INTEREST_DIRNAME = class_to_filename(OpenInterest)  # "custom_open_interest"
MARK_PRICE_DIRNAME = class_to_filename(MarkPriceUpdate)  # "mark_price_update"
FUNDING_RATE_DIRNAME = class_to_filename(FundingRateUpdate)  # "funding_rate_update"
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
    catalog_path: str,
    instrument_id: str,
    dirname: str = SNAPSHOT_DIRNAME,
    *,
    on_foreign: ForeignFileReporter | None = None,
) -> list[tuple[int, int]]:
    """
    Return ascending (start_ns, end_ns) of every Parquet file of the instrument in the catalog's
    `dirname` data directory (default: the second snapshots; Story 33.4's derivatives pages pass
    their own type's, e.g. `FUNDING_RATE_DIRNAME`). `[]` when the directory does not exist.

    Read from the catalog's filenames -- a directory listing, no Parquet I/O. Lets paging routes know
    where data actually exists instead of guessing with fixed-size probe windows (a data gap wider
    than the window otherwise reads as "no more history"). A foreign file name is reported through
    `on_foreign` and skipped (`named_spans`; refused without a hook).
    """
    spans = named_spans(_leaf_files(catalog_path, dirname, instrument_id), on_foreign)
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
    """
    One file's `SecondOHLC` rows. A null volume, count or precision is refused (`ValueError` naming
    the file and column, `_refuse_null_flow`) before any decode, exactly as `second_ohlc_arrays`
    refuses it: these rows feed the same exact order-flow fold (the live bus's seed, the `raw_1s`
    page, the catch-up), and a None unit would fail it unnamed or read as a fabricated 0.
    """
    table = _read_snapshot_columns(path, list(_TRADE_READ_COLUMNS), start_ns, end_ns)
    _refuse_null_flow(table, path)
    values = trade_float_columns(table)
    ts = table.column("ts_event").to_pylist()
    units = {
        name: table.column(name).to_pylist()
        for name in (*PRECISION_COLUMNS, "close_price", *VOLUME_UNIT_COLUMNS, *_COUNT_COLUMNS)
    }
    return [
        SecondOHLC(
            ts_event=ts[i],
            open_price=_optional(values["open_price"][i]),
            high_price=_optional(values["high_price"][i]),
            low_price=_optional(values["low_price"][i]),
            close_price=_optional(values["close_price"][i]),
            buy_volume=float(values["buy_volume"][i]),
            sell_volume=float(values["sell_volume"][i]),
            price_precision=units["price_precision"][i],
            size_precision=units["size_precision"][i],
            close_price_units=units["close_price"][i],
            buy_volume_units=units["buy_volume"][i],
            sell_volume_units=units["sell_volume"][i],
            buy_count=units["buy_count"][i],
            sell_count=units["sell_count"][i],
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


def _overlapping_snapshot_files(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[str]:
    """Return the instrument's snapshot files whose span meets [start_ns, end_ns] within the margin."""
    return [
        path
        for path in snapshot_files(catalog_path, instrument_id)
        if CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns, READ_SPAN_MARGIN_NS)
    ]


def query_snapshot_times(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> np.ndarray:
    """
    Return the `ts_event` of every second-snapshot row in [start_ns, end_ns], ascending, as an
    int64 array: the first pass of a per-bar book read (Story 33.6's `DepthWithinBps` picks each bar's
    last row from it). One column leaves the files, so a 7-day window is ~600k integers, never a
    book (MEM-01); the same file selection as `query_top_of_book`.
    """
    arrays = [
        _read_snapshot_columns(path, ["ts_event"], start_ns, end_ns)
        .column("ts_event")
        .to_numpy()
        .astype(np.int64)
        for path in _overlapping_snapshot_files(catalog_path, instrument_id, start_ns, end_ns)
    ]
    if not arrays:
        return np.empty(0, dtype=np.int64)
    return np.sort(np.concatenate(arrays))


def query_books_at(
    catalog_path: str, instrument_id: str, ts_events: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """
    `ts_event` -> the book of the second-snapshot row stamped exactly then, for the given stamps
    only: the second pass after `query_snapshot_times`. Each dict is `kernel.second_snapshot.
    book_float_rows`' (`as_floats`' book keys plus `ts_event`), the shape `kernel.indicators.
    snapshot_depth` takes. A stamp no row carries is absent from the result.

    Only the book columns and the precisions are projected, and rows are filtered by the stamps
    before any list leaves Arrow, so Python holds one decoded book per selected row (MEM-01).
    Known limit: a file is still read whole within the stamps' span (its row groups' statistics
    cannot prune scattered stamps), one file at a time in Arrow memory; upgrade path: the Parquet
    page index, or a per-bar book column written by the fold.
    """
    wanted = sorted(set(ts_events))
    if not wanted:
        return {}
    books: dict[int, dict[str, Any]] = {}
    for path in _overlapping_snapshot_files(catalog_path, instrument_id, wanted[0], wanted[-1]):
        require_integer_layout(pq.read_schema(path), path)
        table = pq.read_table(
            path, columns=list(TOP_OF_BOOK_COLUMNS), filters=[("ts_event", "in", wanted)]
        )
        for book in book_float_rows(table):
            books[book["ts_event"]] = book
    return books


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
    precision = _price_precision_label(path, table)
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


def _price_precision_label(path: str, table: pa.Table) -> int:
    """
    Return the `price_precision` label the Rust writer stores in a fixed-point price file's
    metadata (`IndexPriceUpdate`, `MarkPriceUpdate`); a file without one, or with one this build
    cannot hold, raises `ValueError` naming it -- never a guessed precision.
    """
    metadata = table.schema.metadata or {}
    if b"price_precision" not in metadata:
        raise ValueError(f"{path}: no price_precision metadata, the raw values cannot be read")
    precision = int(metadata[b"price_precision"])
    if not 0 <= precision <= FIXED_PRECISION:
        raise ValueError(
            f"{path}: price_precision {precision} is outside this build's 0..{FIXED_PRECISION}"
        )
    return precision


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

    Story 33.3 adds the same rows' integer columns as int64 arrays, never through a float:
    `close_units` (0 where nothing traded: `c` is NaN there and the fold reads no close),
    `buy_units`, `sell_units`, `buy_n`, `sell_n`, `price_precision` and `size_precision` -- the
    order-flow fold's input (`candles.domain.fold.FlowArrays`). Only the close may be null there (a
    second without a trade); a null volume, count or precision raises `ValueError` naming the file
    and column (`_flow_units`).
    """
    out = {k: np.empty(0, dtype=np.float64) for k in ("o", "h", "l", "c", "v")}
    out["ts_ms"] = np.empty(0, dtype=np.int64)
    out.update({k: np.empty(0, dtype=np.int64) for k in _FLOW_ARRAY_COLUMNS.values()})
    parts = [_ohlc_part(path) for path in paths]
    if not parts:
        return out
    joined = {k: np.concatenate([p[k] for p in parts]) for k in parts[0]}
    out["ts_ms"] = joined["ts_event"] // NS_PER_MS
    out["o"], out["h"], out["l"], out["c"] = (joined[k] for k in OHLC_UNIT_COLUMNS)
    out["v"] = joined["buy_volume"] + joined["sell_volume"]
    out.update({k: joined[k] for k in _FLOW_ARRAY_COLUMNS.values()})
    return out


def _ohlc_part(path: str) -> dict[str, np.ndarray]:
    """One file's decoded OHLC + volume columns, `ts_event` and integer flow columns."""
    try:
        with _reading(path):
            pf = pq.ParquetFile(path)
            require_integer_layout(pf.schema_arrow, path)
            table = pf.read(columns=list(_TRADE_READ_COLUMNS))
    except FileNotFoundError as exc:
        raise CatalogReadError(f"{path}: gone before it was read, refused: {exc}") from exc
    flow = _flow_units(table, path)  # first: a null volume is refused before any decode
    values = trade_float_columns(table)
    values["ts_event"] = table.column("ts_event").to_numpy().astype(np.int64)
    values.update(flow)
    return values


# Snapshot column -> `second_ohlc_arrays` key of the integer order-flow inputs (Story 33.3).
_FLOW_ARRAY_COLUMNS = MappingProxyType(
    {
        "close_price": "close_units",
        "buy_volume": "buy_units",
        "sell_volume": "sell_units",
        "buy_count": "buy_n",
        "sell_count": "sell_n",
        "price_precision": "price_precision",
        "size_precision": "size_precision",
    }
)


# The one integer input that may be null: the close of a second in which nothing traded.
_NULLABLE_FLOW_COLUMNS = frozenset({"close_price"})


def _flow_units(table: pa.Table, path: str) -> dict[str, np.ndarray]:
    """
    Return the integer columns as int64. A null close (no trade) reads 0, never NaN: the fold reads
    no close where `c` is NaN. A null anywhere else (a volume, a count, a precision) is refused with
    `ValueError` naming the file and column -- a 0 there would be a fabricated "nothing traded" the
    exact order-flow sums would carry into every bar (DATA-07).
    """
    _refuse_null_flow(table, path)
    return {
        key: table.column(name).fill_null(0).to_numpy().astype(np.int64)
        for name, key in _FLOW_ARRAY_COLUMNS.items()
    }


def _refuse_null_flow(table: pa.Table, path: str) -> None:
    """Raise `ValueError` naming `path` and the column for a null outside the close (DATA-07)."""
    for name in _FLOW_ARRAY_COLUMNS:
        nulls = table.column(name).null_count
        if nulls and name not in _NULLABLE_FLOW_COLUMNS:
            raise ValueError(
                f"{path}: {nulls} null value(s) in the integer column {name!r}, "
                f"refused: the order-flow fold never reads a missing value as 0"
            )


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


# -- liquidations (Story 33.3) -------------------------------------------------------------------------


def query_liquidations(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[Liquidation]:
    """
    Every archived `Liquidation` of the instrument with `ts_event` in the inclusive
    `[start_ns, end_ns]`, sorted by `ts_event`, each venue event once: the candle fold's liquidation
    input (the live catch-up, the rebuild, the forming bar's seed, the archive-side read).

    Files are chosen by their `ts_init` span widened by `MAX_TS_INIT_SKEW_NS`, as the trade read
    does. One `venue_event_id` stored twice (a minute file and its consolidated day file) is kept
    once; copies that disagree on anything but `ts_init` (the receive stamp of one venue event may
    differ between two deliveries) raise `ValueError` naming the id -- the caller ledgers it, never
    picks one (DATA-07). No liquidation directory (an instrument without the feed, or none yet):
    `[]`.

    A file removed between the listing and its read (the consolidation) makes the read list again
    (`_relisting`).

    MEM-01: the caller bounds the window (a day at most); this reads all of it at once.
    """
    return _relisting(
        f"{instrument_id} liquidations",
        lambda: _read_liquidations(catalog_path, instrument_id, start_ns, end_ns),
    )


def _read_liquidations(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[Liquidation]:
    directory = os.path.join(catalog_path, "data", LIQUIDATION_DIRNAME, instrument_id)
    kept: dict[str, Liquidation] = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.parquet"))):
        if not CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns, MAX_TS_INIT_SKEW_NS):
            continue
        table = pq.read_table(
            path,
            columns=list(Liquidation.schema().names),
            filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
        )
        for values in table.to_pylist():
            _keep_once(kept, Liquidation.from_dict(values), instrument_id)
    return sorted(kept.values(), key=lambda row: (row.ts_event, row.venue_event_id))


class LiquidationDuplicateError(ValueError):
    """
    One venue event stored twice with different values (`query_liquidations`): the caller ledgers
    it at its duplicate site and raises, never picks a copy (DATA-07). A `ValueError`, so a caller
    that catches every refused read still does; its own class lets a caller tell it from any other
    failed read (a listing that kept losing files).
    """


def _keep_once(kept: dict[str, Liquidation], row: Liquidation, instrument_id: str) -> None:
    """Keep the first copy of a venue event; refuse a second copy that is not the same event."""
    first = kept.setdefault(row.venue_event_id, row)
    if first is row:
        return
    same = {**Liquidation.to_dict(first), "ts_init": 0} == {
        **Liquidation.to_dict(row),
        "ts_init": 0,
    }
    if not same:
        raise LiquidationDuplicateError(
            f"{instrument_id}: liquidation {row.venue_event_id!r} is stored twice with different "
            f"values ({first!r} vs {row!r}), refused"
        )


def liquidation_feed_since_ns(catalog_path: str, instrument_id: str) -> int | None:
    """
    Return the earliest archived `Liquidation.ts_event` of the instrument, or None when none is
    archived: the archive's part of a feed instrument's feed start, from which its bars may say "no
    liquidation" (0) rather than "unknown" (null). Every archive-side fold of a feed id passes it --
    the rebuild (which lowers it to the store's persisted start), the `raw_1s` page and the
    technicals fallback (lowered to the rows folded; `candles.domain.fold.LiquidationArrays.
    since_ns`), and the live bus reads it as well -- so history archived before the feed existed
    (Story 33.1) is never stored or served as 0. A bucket is known only if it starts at or after
    the start: a straddling one is null at every width (Story 33.3 review loop 2).

    Column-projected (`ts_event` only), from the earliest file(s) by filename span: files are
    visited in span-start order and the walk stops at the first file whose span starts more than
    `MAX_TS_INIT_SKEW_NS` after the earliest `ts_event` found (no row of it, or of any later file,
    can be earlier: a row's `ts_init` trails its `ts_event` by at most that bound).

    Known limit: the archive cannot say when the feed started, only when its first liquidation
    landed, so the span between the feed's real start and the id's first archived liquidation reads
    null, not 0 -- conservative: unknown, never a false "none" -- and so does the bucket holding
    the first liquidation, unless it starts exactly at it (that liquidation is counted in no bar).
    Upgrade path: a durable per-id "feed confirmed since" marker written by capture when the liquidation
    socket subscribes, read here instead of the first row.
    """
    directory = os.path.join(catalog_path, "data", LIQUIDATION_DIRNAME, instrument_id)
    paths = glob.glob(os.path.join(directory, "*.parquet"))
    spans = sorted((CatalogFileSpan.from_path(path).start_ns, path) for path in paths)
    earliest: int | None = None
    for start_ns, path in spans:
        if earliest is not None and start_ns - MAX_TS_INIT_SKEW_NS > earliest:
            break
        column = pq.read_table(path, columns=["ts_event"]).column("ts_event")
        if column.null_count:
            raise ValueError(f"{path}: a liquidation row without `ts_event`, refused")
        if len(column):
            first = int(column.to_numpy().min())
            earliest = first if earliest is None else min(earliest, first)
    return earliest


# -- open interest (Story 33.4) ------------------------------------------------------------------------


def query_open_interest(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[OpenInterest]:
    """
    Every archived `OpenInterest` row of the instrument with `ts_event` in the inclusive
    `[start_ns, end_ns]`, sorted by `ts_event`: the derivatives read model's and the ranking
    backfill's open-interest input (`ranking` may import only `kernel`).

    Files are chosen by their `ts_init` span widened by `MAX_TS_INIT_SKEW_NS`, as the liquidation
    read does, and only the row's four columns are read. One row stored twice (a minute file and
    its consolidated day file) is kept once, keyed on its `(ts_event, ts_init)`; two copies with
    that key but different values raise `ValueError` naming them -- the caller ledgers it, never
    picks one (DATA-07). No open-interest directory (a spot id, or none archived yet): `[]`.

    A file removed between the listing and its read (the consolidation) makes the read list again
    (`_relisting`).

    MEM-01: the caller bounds the window (25 h at most); this reads all of it at once.
    """
    return _relisting(
        f"{instrument_id} open interest",
        lambda: _read_open_interest(catalog_path, instrument_id, start_ns, end_ns),
    )


def _read_open_interest(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[OpenInterest]:
    directory = os.path.join(catalog_path, "data", OPEN_INTEREST_DIRNAME, instrument_id)
    kept: dict[tuple[int, int], OpenInterest] = {}
    for path in sorted(glob.glob(os.path.join(directory, "*.parquet"))):
        if not CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns, MAX_TS_INIT_SKEW_NS):
            continue
        table = pq.read_table(
            path,
            columns=list(OpenInterest.schema().names),
            filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
        )
        for values in table.to_pylist():
            row = OpenInterest.from_dict(values)
            first = kept.setdefault((row.ts_event, row.ts_init), row)
            if first.open_interest != row.open_interest:
                raise ValueError(
                    f"{instrument_id}: open interest at ts_event {row.ts_event} is stored twice "
                    f"with different values ({first.open_interest} vs {row.open_interest}), refused"
                )
    return sorted(kept.values(), key=lambda row: (row.ts_event, row.ts_init))


# -- mark and index price columns (Story 33.4 review) -----------------------------------------------

_PRICE_DIRNAMES = frozenset({MARK_PRICE_DIRNAME, INDEX_PRICE_DIRNAME})


class PriceColumns(NamedTuple):
    """
    Fixed-point price rows (`MarkPriceUpdate`, `IndexPriceUpdate`) as int64 arrays, each stored row
    once, sorted by (`ts_event`, `ts_init`). `units` counts `10^-precision`, `precision` is the
    row's own file label, so `Decimal(units).scaleb(-precision)` is the stored price exactly (no
    float step, NAUT-01).
    """

    ts_event: np.ndarray
    ts_init: np.ndarray
    units: np.ndarray
    precision: np.ndarray


def query_price_columns(
    catalog_path: str, dirname: str, instrument_id: str, start_ns: int, end_ns: int
) -> PriceColumns:
    """
    Every mark (`MARK_PRICE_DIRNAME`) or index (`INDEX_PRICE_DIRNAME`) price row of the instrument
    with `ts_event` in the inclusive `[start_ns, end_ns]`, column-projected: the three stored
    columns as numpy arrays, never one Python object per row (MEM-01: a Bybit linear id stores about
    ten mark ticks a second, 864k a day). `query_index_prices`' file selection (the `ts_init` span
    widened by `MAX_TS_INIT_SKEW_NS`), precision label and exact raw decode (`_raw_units`: a raw
    finer than its label, or not 16 bytes wide, is refused, never rounded).

    One row stored twice (a minute file and its consolidated day file) is kept once, keyed on its
    `(ts_event, ts_init)`; two copies with that key but a different price raise `ValueError` naming
    them -- the caller ledgers it, never picks one (DATA-07). Copies compare by value, so the same
    price under two precision labels is one row. No directory: empty arrays. A file removed between
    the listing and its read (the consolidation) makes the read list again (`_relisting`).

    Known limit: the window's columns are held at once (about 32 bytes a row: a day of a Bybit
    linear id's marks is ~28 MB of arrays), so the caller bounds the window (`views.derivatives`
    reads one UTC day at a time and keeps only each bucket's last value). Upgrade path: stream each
    file with `pq.ParquetFile.iter_batches` and check copies across files by a per-file reduction.
    """
    if dirname not in _PRICE_DIRNAMES:
        raise ValueError(f"{dirname!r} is not a fixed-point price directory")
    return _relisting(
        f"{instrument_id} {dirname}",
        lambda: _read_price_columns(catalog_path, dirname, instrument_id, start_ns, end_ns),
    )


def _read_price_columns(
    catalog_path: str, dirname: str, instrument_id: str, start_ns: int, end_ns: int
) -> PriceColumns:
    directory = os.path.join(catalog_path, "data", dirname, instrument_id)
    parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]] = []
    for path in sorted(glob.glob(os.path.join(directory, "*.parquet"))):
        if not CatalogFileSpan.from_path(path).overlaps(start_ns, end_ns, MAX_TS_INIT_SKEW_NS):
            continue
        part = _price_part(path, start_ns, end_ns)
        if part is not None:
            parts.append(part)
    if not parts:
        empty = np.empty(0, dtype=np.int64)
        return PriceColumns(empty, empty, empty, empty)
    return _unique_price_rows(f"{instrument_id} {dirname}", parts)


def _price_part(
    path: str, start_ns: int, end_ns: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None:
    table = pq.read_table(
        path,
        columns=["value", "ts_event", "ts_init"],
        filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
    )
    if table.num_rows == 0:
        return None
    precision = _price_precision_label(path, table)
    units = _raw_units(path, "value", table.column("value"), precision)
    return (
        table.column("ts_event").to_numpy().astype(np.int64),
        table.column("ts_init").to_numpy().astype(np.int64),
        units,
        np.full(table.num_rows, precision, dtype=np.int64),
    )


def _unique_price_rows(
    label: str, parts: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]
) -> PriceColumns:
    """Concatenate, sort by (`ts_event`, `ts_init`), keep each key once, refuse a disagreeing copy."""
    ts_event, ts_init, units, precision = (
        np.concatenate([part[i] for part in parts]) for i in range(4)
    )
    order = np.lexsort((ts_init, ts_event))  # stable: the first copy in file order stays first
    ts_event, ts_init, units, precision = (a[order] for a in (ts_event, ts_init, units, precision))
    first = np.ones(len(order), dtype=bool)
    first[1:] = (ts_event[1:] != ts_event[:-1]) | (ts_init[1:] != ts_init[:-1])
    kept = np.maximum.accumulate(np.where(first, np.arange(len(order)), 0))
    # Only a copy whose stored units or label differ from its first is compared as a `Decimal`: a
    # day held twice (a minute file beside its day file) is ~864k identical copies, never a loop.
    differs = ~first & ((units != units[kept]) | (precision != precision[kept]))
    for copy in np.flatnonzero(differs):
        a, b = kept[copy], copy
        price_a = Decimal(int(units[a])).scaleb(-int(precision[a]))
        price_b = Decimal(int(units[b])).scaleb(-int(precision[b]))
        if price_a != price_b:
            raise ValueError(
                f"{label}: price at ts_event {int(ts_event[b])} is stored twice with different "
                f"values ({price_a} vs {price_b}), refused"
            )
    return PriceColumns(ts_event[first], ts_init[first], units[first], precision[first])


def newest_ts_event_before(
    catalog_path: str, instrument_id: str, dirnames: tuple[str, ...], before_ns: int
) -> int | None:
    """
    Return the newest stored `ts_event < before_ns` of the instrument across the `dirnames` data
    directories, None when there is none: where a sparse page's walk back jumps an empty window
    to, in one bounded pass instead of stepping window by window (Story 33.4 review).

    Reads only the `ts_event` column, newest file (by its name's `ts_init` span) first, and stops
    once no file left can hold a newer row: a row's `ts_event` is at most its `ts_init` plus
    `MAX_TS_INIT_SKEW_NS`, so a file whose span ends that far below the best found is never opened.
    Each file is read at most once per listing; a file removed between the listing and its read (the
    consolidation) makes the scan list again (`_relisting`).
    """
    return _relisting(
        f"{instrument_id} {'/'.join(dirnames)}",
        lambda: _newest_ts_event_before(catalog_path, instrument_id, dirnames, before_ns),
    )


def _newest_ts_event_before(
    catalog_path: str, instrument_id: str, dirnames: tuple[str, ...], before_ns: int
) -> int | None:
    candidates: list[tuple[int, str]] = []
    for dirname in dirnames:
        directory = os.path.join(catalog_path, "data", dirname, instrument_id)
        for path in glob.glob(os.path.join(directory, "*.parquet")):
            span = CatalogFileSpan.from_path(path)
            if span.start_ns - MAX_TS_INIT_SKEW_NS < before_ns:
                candidates.append((span.end_ns, path))
    best: int | None = None
    for end_ns, path in sorted(candidates, reverse=True):
        if best is not None and end_ns + MAX_TS_INIT_SKEW_NS <= best:
            break
        column = pq.read_table(
            path, columns=["ts_event"], filters=[("ts_event", "<", before_ns)]
        ).column("ts_event")
        if len(column):
            newest = int(pc.max(column).as_py())
            best = newest if best is None else max(best, newest)
    return best
