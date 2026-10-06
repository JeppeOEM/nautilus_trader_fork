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
The catalog tool's pure rules (Story 31.7, `docs/DATA_DICTIONARY.md` section 1.20): the file-name
parser, the order-independent row digest, the structure classes, the consolidation rehearsal's
verdict, backtest-read parity and the candle judgement, with the report values each produces.

Oracle side (DATA-02): standard library and `verification.domain` only. It parses the catalog's
file names itself (never `kernel.clocks`), and judges candles with the independent reference fold
(`reference_signals.fold_candles`, `RefBook.from_stored`) and its exact comparison rule
(`signal_compare.at_places`). `verification.subject` -- the Nautilus, archive and snapshot-codec
drivers under test -- imports this module for the one digest, so both sides hash rows alike; this
module never imports the subject.
"""

import calendar
import hashlib
import re
from bisect import bisect_left
from bisect import insort
from collections import Counter
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SECONDS_PER_DAY
from verification.domain.reference_signals import AGGREGATE_FIELDS
from verification.domain.reference_signals import KnownLiquidations
from verification.domain.reference_signals import RefBook
from verification.domain.reference_signals import RefCandle
from verification.domain.reference_signals import bucket_start
from verification.domain.reference_signals import fold_candles
from verification.domain.signal_compare import Agreement
from verification.domain.signal_compare import at_places


NS_PER_DAY = SECONDS_PER_DAY * NS_PER_S
MS_PER_S = 1_000
NS_PER_MS = 1_000_000
# How far past the day, on each side, a row of the day can sit by `ts_init` (file names span
# `ts_init`; the day is judged on `ts_init` for parity and on `ts_event` for the fold and the
# duplicate count). Measured `ts_init - ts_event` on the Story 31.2 soak (2026-09-29): snapshots
# 1.000-1.006 s on Bybit and 3.000-3.005 s on Hyperliquid (the hold-back of the venue-timed close,
# DATA-01), trades at most 9.8 s (a reconnect backfill). 60 s is six times the worst, one flush
# period. A row further apart than this is counted `beyond_margin` and fails, so the margin can
# never hide a row.
# Known limit (cost and scope): after the nightly has consolidated D - 1 and D + 1, each of those
# days is one file whose name span meets the margin, so it is a day file of D too -- structure-
# checked, opened and rehearsed with D's files, roughly doubling the run, and a defect in a
# neighbour day's file fails D as well (reported under D, by file name). Upgrade path: judge only
# the rows of a neighbour file that lie in the window (row-group pruning on `ts_init`) and attribute
# a neighbour file's own structure classes to its own day.
READ_MARGIN_NS = 60 * NS_PER_S
# The widths the candle store keeps (`candles/domain/fold.py`'s `BAR_SECONDS`, restated from
# `docs/DATA_DICTIONARY.md` section 2.5/5, never imported: candles is code the oracle checks). A
# stored width outside it is `unknown_width`.
STORE_BAR_SECONDS = (60, 300, 900, 3600, 14400, 86400)

TRADE_TYPE = "trade_tick"
SNAPSHOT_TYPE = "custom_dydx_second_snapshot"
PARITY_TYPES = (TRADE_TYPE, SNAPSHOT_TYPE)

# --- structure classes (every one fails the day when non-zero) ---------------------------------
BAD_NAME = "bad_name"
OVERLAP = "overlap"
NAME_SPAN = "name_span"
UNSORTED = "unsorted"
EMPTY = "empty"
NULL_TS = "null_ts"
OPEN_FAILED = "open_failed"
OPEN_COUNT = "open_count"
LEAF_CLASSES = (BAD_NAME, OVERLAP, NAME_SPAN, UNSORTED, EMPTY, NULL_TS, OPEN_FAILED, OPEN_COUNT)
UNKNOWN_TYPE = "unknown_type"
SCHEMAS = "schemas"
DUPLICATE_TS_EVENT = "duplicate_ts_event"

# --- rehearsal verdicts ---------------------------------------------------------------------------
IDENTICAL = "identical"
NOT_EXERCISED = "not_exercised"
DIFFERENT = "different"
STAGES = ("linked", "intraday", "nightly")

# --- parity ---------------------------------------------------------------------------------------
READ_MISMATCH = "read_mismatch"
BEYOND_MARGIN = "beyond_margin"
LEGS = ("stored", "query", "received")

# --- candle classes -------------------------------------------------------------------------------
EXACT = "exact"
FLOAT_NOISE = "float_noise"
BOTH_UNDEFINED = "both_undefined"
UNDEFINED_MISMATCH = "undefined_mismatch"
MISSING = "missing"
EXTRA = "extra"
UNKNOWN_WIDTH = "unknown_width"
CANDLE_CLASSES = (
    EXACT,
    FLOAT_NOISE,
    BOTH_UNDEFINED,
    DIFFERENT,
    UNDEFINED_MISMATCH,
    MISSING,
    EXTRA,
)
FAILING_CANDLES = frozenset({DIFFERENT, UNDEFINED_MISMATCH, MISSING, EXTRA})

_STAMP = r"(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-(\d{9})Z"
_FILE_NAME = rf"{_STAMP}_{_STAMP}\.parquet"
_EXAMPLES = 5


# --- file names -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """A file name's inclusive `ts_init` span, in UTC epoch nanoseconds."""

    start: int
    end: int

    def intersects(self, low: int, high: int) -> bool:
        """Whether the span meets the half-open window `[low, high)`."""
        return self.start < high and self.end >= low


def _stamp_ns(parts: Sequence[str]) -> int:
    year, month, day, hour, minute, second, nanos = (int(p) for p in parts)
    if not (1 <= month <= 12 and 1 <= day <= calendar.monthrange(year, month)[1]):
        raise ValueError("not a calendar date")
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError("not a time of day")
    seconds = calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0))
    return seconds * NS_PER_S + nanos


def parse_file_name(name: str) -> Span | None:
    """
    Parse `YYYY-MM-DDTHH-MM-SS-<9 digits>Z_<same>.parquet` (UTC) into its inclusive span; None for
    any other name, an impossible date or time, or a span that ends before it starts.
    """
    match = re.fullmatch(_FILE_NAME, name)
    if match is None:
        return None
    groups = match.groups()
    try:
        span = Span(_stamp_ns(groups[:7]), _stamp_ns(groups[7:]))
    except ValueError:
        return None
    return span if span.start <= span.end else None


# --- the row digest -------------------------------------------------------------------------------


def row_digest(row: Mapping[str, object]) -> int:
    """
    One row's 128-bit digest: `blake2b(digest_size=16)` over `repr` of each `(column, value)` pair,
    sorted by column name. Values are pyarrow `to_pylist()` values (a dictionary column yields its
    string, a fixed-size binary its bytes, a list its Python list).
    """
    h = hashlib.blake2b(digest_size=16)
    for name in sorted(row):
        h.update(repr((name, row[name])).encode())
    return int.from_bytes(h.digest(), "big")


_MOD = 2**128


@dataclass(frozen=True)
class Digest:
    """
    An order-independent multiset digest: the row count and the sum of the row digests mod 2**128.
    A reordered read agrees; a dropped, added, duplicated or changed row does not.
    """

    count: int = 0
    value: int = 0

    def __add__(self, other: "Digest") -> "Digest":
        return Digest(self.count + other.count, (self.value + other.value) % _MOD)

    @classmethod
    def of(cls, rows: Iterable[Mapping[str, object]]) -> "Digest":
        count, total = 0, 0
        for row in rows:
            count += 1
            total += row_digest(row)
        return cls(count, total % _MOD)

    def text(self) -> str:
        return f"{self.count} rows {self.value:032x}"


# --- structure ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DayFiles:
    """
    A leaf's day files -- a name span meeting the day's window, or `ts_init`/`ts_event` statistics
    meeting it (a lying name or a late row can never hide a file) -- every name that does not
    parse, and the name spans of the leaf's other files (for `overlap` across the window's edge).
    """

    files: tuple[tuple[Path, Span], ...]
    bad_names: tuple[str, ...]
    others: tuple[Span, ...] = ()


@dataclass(frozen=True)
class FileScan:
    """
    One file's raw stamps: rows, the extremes of its non-null `ts_init`, the decreases, the rows
    with a null `ts_init` or `ts_event`, and the UTC hours (`ts_init // NS_PER_HOUR`) holding a
    stamped row -- the only hours the file is opened over, so one stray stamp decades away costs
    one query, never every hour between.
    """

    rows: int
    low: int | None
    high: int | None
    decreases: int
    null_ts: int
    hours: tuple[int, ...] = ()


@dataclass(frozen=True)
class FileOpen:
    """What `ParquetDataCatalog` decoded from one file over its span, or the error it raised."""

    decoded: int
    error: str | None = None


@dataclass(frozen=True)
class LeafFile:
    """
    One day file of a leaf: its name, the name's span, the raw scan and the Nautilus open (None:
    not opened -- a type with no Nautilus class, counted once as `unknown_type`).
    """

    name: str
    span: Span
    scan: FileScan
    opened: FileOpen | None


def count_overlaps(spans: Sequence[Span]) -> int:
    """Count the pairs of spans whose inclusive intervals intersect."""
    ends: list[int] = []
    pairs = 0
    for span in sorted(spans, key=lambda s: (s.start, s.end)):
        # Every earlier span starts at or before this one; it intersects when it ends at or after.
        pairs += len(ends) - bisect_left(ends, span.start)
        insort(ends, span.end)
    return pairs


def count_cross_overlaps(spans: Sequence[Span], others: Sequence[Span]) -> int:
    """Count the pairs (one of `spans`, one of `others`) whose inclusive intervals intersect."""
    if not spans:
        return 0
    low, high = min(s.start for s in spans), max(s.end for s in spans)
    near = [o for o in others if o.start <= high and o.end >= low]
    return sum(1 for s in spans for o in near if o.start <= s.end and o.end >= s.start)


def _name_lies(item: LeafFile) -> bool:
    scan = item.scan
    if scan.low is None or scan.high is None:  # no stamp to span: `empty`/`null_ts` say why
        return False
    return (scan.low, scan.high) != (item.span.start, item.span.end)


def _file_classes(item: LeafFile) -> list[str]:
    scan, opened = item.scan, item.opened
    found = []
    if _name_lies(item):
        found.append(NAME_SPAN)
    if scan.decreases:
        found.append(UNSORTED)
    if scan.rows == 0:
        found.append(EMPTY)
    if scan.null_ts:
        found.append(NULL_TS)
    if opened is None or scan.low is None:  # not opened: no stamped row to decode
        return found
    if opened.error is not None:
        found.append(OPEN_FAILED)
    elif opened.decoded != scan.rows:
        found.append(OPEN_COUNT)
    return found


@dataclass(frozen=True)
class LeafReport:
    """One `data/<type>/<instrument>` leaf over the day: its day files, rows and structure counts."""

    data_type: str
    instrument_id: str
    files: int
    rows: int
    counts: Mapping[str, int]
    examples: tuple[str, ...]

    @property
    def failing(self) -> int:
        return sum(self.counts.values())


def _example(item: LeafFile, found: Sequence[str]) -> str:
    scan, opened = item.scan, item.opened
    decoded = "not opened" if opened is None else f"decoded {opened.decoded}"
    detail = f"rows {scan.rows} ts_init [{scan.low}, {scan.high}] {decoded}"
    error = f" error {opened.error}" if opened and opened.error else ""
    return f"{item.name}: {','.join(found)} ({detail}{error})"


def judge_leaf(
    data_type: str,
    instrument_id: str,
    files: Sequence[LeafFile],
    bad_names: Sequence[str],
    others: Sequence[Span] = (),
) -> LeafReport:
    """
    Count every structure class of one leaf's day files (a bad name is a file of its own); an
    `overlap` is a pair of day files, or a day file and another leaf file, whose spans intersect.
    """
    counts: Counter[str] = Counter({BAD_NAME: len(bad_names)})
    spans = [item.span for item in files]
    counts[OVERLAP] = count_overlaps(spans) + count_cross_overlaps(spans, others)
    examples = [f"{name}: {BAD_NAME}" for name in bad_names[:_EXAMPLES]]
    for item in files:
        found = _file_classes(item)
        counts.update(found)
        if found and len(examples) < _EXAMPLES:
            examples.append(_example(item, found))
    return LeafReport(
        data_type=data_type,
        instrument_id=instrument_id,
        files=len(files),
        rows=sum(item.scan.rows for item in files),
        counts=MappingProxyType({name: counts[name] for name in LEAF_CLASSES}),
        examples=tuple(examples),
    )


def duplicate_count(ts_events: Iterable[int], day: range) -> int:
    """Rows beyond the first sharing one `ts_event`, among the rows with `ts_event` in the day."""
    kept = [ts for ts in ts_events if ts in day]
    return len(kept) - len(set(kept))


@dataclass(frozen=True)
class SchemaClass:
    """One schema signature of a type: its text, how many day files carry it, and one of them."""

    signature: str
    files: int
    example: str


def schema_signature(fields: Sequence[tuple[str, str]], metadata_keys: Iterable[str]) -> str:
    """
    Sign a schema: the ordered (name, Arrow type) list plus the sorted metadata *keys* (the
    values carry each instrument's precision labels, checked by Story 31.6, so only a changed
    key splits a class).
    """
    columns = ", ".join(f"{name}: {kind}" for name, kind in fields)
    return f"[{columns}] metadata keys {sorted(metadata_keys)}"


def schema_classes(signed: Iterable[tuple[str, str]]) -> tuple[SchemaClass, ...]:
    """Group `(signature, path)` pairs into classes, the most common first."""
    files: Counter[str] = Counter()
    example: dict[str, str] = {}
    for signature, path in signed:
        files[signature] += 1
        example.setdefault(signature, path)
    return tuple(SchemaClass(s, n, example[s]) for s, n in files.most_common())


@dataclass(frozen=True)
class TypeStructure:
    """One data type's structure over the day: its class, schema classes, leaves and duplicates."""

    data_type: str
    known: bool
    schemas: tuple[SchemaClass, ...]
    leaves: tuple[LeafReport, ...]
    duplicate_ts_event: int | None = None

    @property
    def failing(self) -> int:
        unknown = 0 if self.known else 1
        schemas = 1 if len(self.schemas) > 1 else 0
        duplicates = self.duplicate_ts_event or 0
        return unknown + schemas + duplicates + sum(leaf.failing for leaf in self.leaves)


# --- the consolidation rehearsal ------------------------------------------------------------------


@dataclass(frozen=True)
class ConsolidationRun:
    """What one archive run on the scratch copy counted: leaves abandoned, periods refused."""

    leaves_failed: int
    days_refused: int


@dataclass(frozen=True)
class Stage:
    """The day files of one type in the scratch copy after a stage, and their rows' digest."""

    files: int
    digest: Digest


@dataclass(frozen=True)
class TypeRehearsal:
    """One type through the three stages (`STAGES`) and its verdict."""

    data_type: str
    stages: tuple[Stage, ...]
    verdict: str


def rehearsal_verdict(stages: Sequence[Stage], changed: bool) -> str:
    """`different` when any stage's digest differs; else `identical` if a file was rewritten."""
    if any(stage.digest != stages[0].digest for stage in stages[1:]):
        return DIFFERENT
    return IDENTICAL if changed else NOT_EXERCISED


@dataclass(frozen=True)
class RehearsalReport:
    """The rehearsal of every type, and what the archive's own runs counted as failed."""

    types: tuple[TypeRehearsal, ...]
    leaves_failed: int
    days_refused: int

    @property
    def failing(self) -> int:
        different = sum(t.verdict == DIFFERENT for t in self.types)
        return different + self.leaves_failed + self.days_refused


# --- backtest-read parity -------------------------------------------------------------------------


@dataclass(frozen=True)
class Leg:
    """One reader's rows of the day (`ts_init` in it): the digest and the stored columns it lacks."""

    digest: Digest
    missing_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredLeg:
    """The stored rows of the day (`ts_init` in it), and the rows beyond the margin."""

    digest: Digest
    beyond_margin: int


@dataclass(frozen=True)
class ParityReport:
    """One instrument and type: the three legs (`LEGS`) and the rows beyond the margin."""

    instrument_id: str
    data_type: str
    legs: Mapping[str, Leg]
    beyond_margin: int

    @property
    def mismatches(self) -> tuple[str, ...]:
        """Each pair of legs whose digests differ, and each leg that lacked a stored column."""
        pairs = [
            f"{a}/{b}"
            for i, a in enumerate(LEGS)
            for b in LEGS[i + 1 :]
            if self.legs[a].digest != self.legs[b].digest
        ]
        lacking = [
            f"{name} lacks {list(leg.missing_columns)}"
            for name, leg in self.legs.items()
            if leg.missing_columns
        ]
        return (*pairs, *lacking)

    @property
    def read_mismatch(self) -> int:
        return len(self.mismatches)

    @property
    def failing(self) -> int:
        return self.read_mismatch + self.beyond_margin


# --- candles --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredBar:
    """
    One candle-store row (`candles` table): REAL o/h/l/c (NULL when nothing traded) and v, then
    §2.15's ten INTEGER columns (NULL: unknown -- a pre-migration row, or no liquidation feed).
    """

    bar_seconds: int
    t: int
    o: float | None
    h: float | None
    l: float | None
    c: float | None
    v: float
    seconds_observed: int
    buy_v: int | None = None
    sell_v: int | None = None
    buy_n: int | None = None
    sell_n: int | None = None
    pv: int | None = None
    liq_long_v: int | None = None
    liq_short_v: int | None = None
    liq_n: int | None = None
    price_precision: int | None = None
    size_precision: int | None = None


@dataclass(frozen=True)
class TradeRow:
    """
    A received snapshot row's trade columns (integer units at the row's precisions, section 1.7):
    everything the fold reads, and nothing of the book.
    """

    ts_event: int
    price_precision: int
    size_precision: int
    open_price: int | None
    high_price: int | None
    low_price: int | None
    close_price: int | None
    buy_volume: int
    sell_volume: int
    buy_count: int
    sell_count: int

    @classmethod
    def columns(cls) -> list[str]:
        """Name the snapshot columns a trade row is taken from."""
        return list(cls.__dataclass_fields__)

    @classmethod
    def of(cls, row: Mapping[str, object]) -> "TradeRow":
        """Take the trade columns of an encoded snapshot row (a missing one: `KeyError`)."""
        values = {name: row[name] for name in cls.__dataclass_fields__}
        return cls(**values)  # type: ignore[arg-type]

    def book(self) -> RefBook:
        """
        Decode through the reference decoder. The book lists are empty: `fold_candles` reads only
        the trade columns, so the tool never carries 20 levels a side through a day's fold.
        """
        empty: list[int] = []
        row = {name: getattr(self, name) for name in self.__dataclass_fields__}
        return RefBook.from_stored(
            row | {"bid_prices": empty, "bid_sizes": empty, "ask_prices": empty, "ask_sizes": empty}
        )


def _worst(found: Iterable[Agreement]) -> str:
    kinds = set(found)
    # A definedness disagreement names the bucket first: its other fields differ as a consequence.
    for failing, name in (
        (Agreement.UNDEFINED_MISMATCH, UNDEFINED_MISMATCH),
        (Agreement.DIFFERENT, DIFFERENT),
    ):
        if failing in kinds:
            return name
    if Agreement.FLOAT_NOISE in kinds:
        return FLOAT_NOISE
    return EXACT


def aggregates_agree(found: object, ref: RefCandle) -> bool:
    """
    §2.15's ten columns of a stored or served bar against the reference: every integer and both
    precisions equal, exactly, and null only where the reference is null (null is never 0).
    """
    return all(getattr(found, name) == getattr(ref, name) for name in AGGREGATE_FIELDS)


def judge_bar(stored: StoredBar, ref: RefCandle, places: tuple[int, int]) -> str:
    """
    One bucket: o/h/l/c exact at the bucket's price places, v at its size places
    (`at_places`, never a tolerance), `seconds_observed` int-equal, and §2.15's columns exactly
    equal in units (`aggregates_agree`). `both_undefined` is a bucket nothing traded in on both
    sides (NULL o/h/l/c, volume 0).
    """
    price, size = places
    prices = [
        at_places(s, r, price)
        for s, r in zip(
            (stored.o, stored.h, stored.l, stored.c),
            (ref.open, ref.high, ref.low, ref.close),
            strict=True,
        )
    ]
    volume = at_places(stored.v, ref.volume, size)
    verdict = _worst([*prices, volume])
    if verdict != UNDEFINED_MISMATCH and stored.seconds_observed != ref.seconds_observed:
        return DIFFERENT  # after definedness, which `_worst` names first
    if verdict != UNDEFINED_MISMATCH and not aggregates_agree(stored, ref):
        return DIFFERENT
    if verdict == EXACT and all(p is Agreement.BOTH_UNDEFINED for p in prices):
        return BOTH_UNDEFINED
    return verdict


@dataclass(frozen=True)
class WidthCandles:
    """One stored width's buckets of the day, classed (`CANDLE_CLASSES`), with a few examples."""

    bar_seconds: int
    counts: Mapping[str, int]
    examples: tuple[str, ...]

    @property
    def failing(self) -> int:
        return sum(self.counts[name] for name in FAILING_CANDLES)


@dataclass(frozen=True)
class CandleReport:
    """One instrument's candle store against the reference fold of its received rows."""

    instrument_id: str
    rows: int
    widths: tuple[WidthCandles, ...]
    unknown_width: int

    @property
    def failing(self) -> int:
        return self.unknown_width + sum(width.failing for width in self.widths)

    @property
    def float_noise(self) -> int:
        return sum(width.counts[FLOAT_NOISE] for width in self.widths)


def _bucket_places(rows: Sequence[TradeRow], bar_seconds: int) -> dict[int, tuple[int, int]]:
    """Return the maximum price and size precision among each bucket's rows."""
    places: dict[int, tuple[int, int]] = {}
    for row in rows:
        t = bucket_start(row.ts_event // NS_PER_MS, bar_seconds)
        price, size = places.get(t, (0, 0))
        places[t] = (max(price, row.price_precision), max(size, row.size_precision))
    return places


def _bar_line(bar_seconds: int, t: int, verdict: str, detail: str) -> str:
    return f"{bar_seconds} s t={t}: {verdict} ({detail})"


def judge_width(
    bar_seconds: int,
    rows: Sequence[TradeRow],
    books: Sequence[RefBook],
    bars: Sequence[StoredBar],
    liquidations: KnownLiquidations | None = None,
) -> WidthCandles:
    """
    Class every bucket of one width: both sides present, only the reference, only the store. The
    day's `liquidations` (None: the instrument has no feed, or nothing archived; buckets before
    their known start read None) go into the reference fold, so a
    liquidation-only store row is matched, never `extra`.
    """
    reference = fold_candles(books, bar_seconds, liquidations)
    places = _bucket_places(rows, bar_seconds)
    stored = {bar.t: bar for bar in bars}
    counts: Counter[str] = Counter()
    examples: list[str] = []
    for t in sorted(set(reference) | set(stored)):
        if t not in stored:
            verdict, detail = MISSING, f"reference {reference[t]}"
        elif t not in reference:
            verdict, detail = EXTRA, f"stored {stored[t]}"
        else:
            # A liquidation-only bucket has no row, so no places: its o/h/l/c/v are undefined
            # on both sides and compared at any.
            verdict = judge_bar(stored[t], reference[t], places.get(t, (0, 0)))
            detail = f"stored {stored[t]} reference {reference[t]}"
        counts[verdict] += 1
        if verdict in FAILING_CANDLES | {FLOAT_NOISE} and len(examples) < _EXAMPLES:
            examples.append(_bar_line(bar_seconds, t, verdict, detail))
    return WidthCandles(
        bar_seconds, MappingProxyType({n: counts[n] for n in CANDLE_CLASSES}), tuple(examples)
    )


def judge_candles(
    instrument_id: str,
    rows: Sequence[TradeRow],
    bars: Sequence[StoredBar],
    unknown_width: int,
    liquidations: KnownLiquidations | None = None,
) -> CandleReport:
    """
    Judge every stored width over the day's received rows (`ts_event` in the day) and, for an
    instrument with a liquidation feed, the day's archived liquidations (None without one).
    """
    books = [row.book() for row in rows]
    widths = tuple(
        judge_width(width, rows, books, [b for b in bars if b.bar_seconds == width], liquidations)
        for width in STORE_BAR_SECONDS
    )
    return CandleReport(instrument_id, len(rows), widths, unknown_width)


# --- the day --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogDayReport:
    """The catalog tool's verdict over one venue's closed UTC day."""

    venue: str
    day: str
    catalog: str
    candle_store: str
    instruments: tuple[str, ...]
    structure: tuple[TypeStructure, ...]
    rehearsal: RehearsalReport
    parity: tuple[ParityReport, ...]
    candles: tuple[CandleReport, ...]

    @property
    def failing(self) -> int:
        return (
            sum(t.failing for t in self.structure)
            + self.rehearsal.failing
            + sum(p.failing for p in self.parity)
            + sum(c.failing for c in self.candles)
        )

    @property
    def passed(self) -> bool:
        return self.failing == 0


@dataclass(frozen=True)
class Received:
    """
    What the backtest actor received for one instrument: each parity type's leg, and the trade
    columns of every snapshot with `ts_event` in the day (the candle fold's input).
    """

    legs: Mapping[str, Leg]
    trade_rows: tuple[TradeRow, ...]

    @classmethod
    def empty(cls) -> "Received":
        return cls(MappingProxyType({t: Leg(Digest()) for t in PARITY_TYPES}), ())

    def __add__(self, other: "Received") -> "Received":
        legs = {
            t: Leg(
                self.legs[t].digest + other.legs[t].digest,
                tuple(sorted({*self.legs[t].missing_columns, *other.legs[t].missing_columns})),
            )
            for t in PARITY_TYPES
        }
        return Received(MappingProxyType(legs), (*self.trade_rows, *other.trade_rows))


def backtest_windows(day_start_ns: int) -> list[tuple[int, int]]:
    """
    Return the inclusive `ts_init` windows the received leg is read in, covering
    `[D - M, D + 1 + M)` exactly: the margin before the day, each hour of it, the margin after.
    """
    end = day_start_ns + NS_PER_DAY
    hours = [(h, h + NS_PER_HOUR - 1) for h in range(day_start_ns, end, NS_PER_HOUR)]
    return [
        (day_start_ns - READ_MARGIN_NS, day_start_ns - 1),
        *hours,
        (end, end + READ_MARGIN_NS - 1),
    ]


def day_window(day_start_ns: int) -> tuple[int, int]:
    """Return the `ts_init` window whose files are the day's: `[D - M, D + 1 + M)`."""
    return day_start_ns - READ_MARGIN_NS, day_start_ns + NS_PER_DAY + READ_MARGIN_NS


def hour_windows(span: Span, hours: Iterable[int]) -> list[tuple[int, int]]:
    """
    One inclusive `[start, end]` window per given UTC hour, clipped to the span: a file is opened
    over the hours its rows occupy only, each no wider than the rows' own `[min, max]`.
    """
    return [
        (max(span.start, h * NS_PER_HOUR), min(span.end, (h + 1) * NS_PER_HOUR - 1))
        for h in sorted(hours)
    ]


def is_day(ts_ns: int, day_start_ns: int) -> bool:
    """Whether a stamp lies in the UTC day starting at `day_start_ns`."""
    return day_start_ns <= ts_ns < day_start_ns + NS_PER_DAY
