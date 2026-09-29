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
Trades proven id by id and second by second (Story 31.4), pure: the reference's own fold, the
id comparison, the second classes and the report values. No I/O, no clock.

The reference side never imports the code it checks: the archived values arrive as the raw
Parquet bytes (`fixed_size_binary[16]`, little-endian signed 128-bit at 10^16) and are decoded
here in `Decimal`; the fold is written from the dictionary's rule (`docs/DATA_DICTIONARY.md`
section 1.17), not from `kernel.fold`; the snapshot's trade columns are the stored integers.

Invariants:
- exact: every value is an integer or a `Decimal` computed in ``_exact()`, whose `Inexact` trap
  makes any rounding raise; nothing compares within a tolerance (latency is the one bounded
  quantity, and it is reported, not absorbed);
- a failing count is never folded into a passing class: `passed` is false while any of them is
  non-zero.
"""

from collections import Counter
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import fields
from dataclasses import replace
from decimal import Context
from decimal import Decimal
from decimal import Inexact
from types import MappingProxyType
from typing import Any
from typing import Protocol
from typing import cast

from verification.domain.conservation import BUYER
from verification.domain.conservation import EXAMPLES
from verification.domain.conservation import NO_AGGRESSOR
from verification.domain.conservation import NS_PER_MS
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import Explanations
from verification.domain.conservation import Intervals
from verification.domain.conservation import MalformedLine
from verification.domain.conservation import ReferenceTrade


# Nautilus's high-precision fixed-point scale (`FIXED_PRECISION`), restated: the archive stores
# every price and size as a signed 128-bit integer count of 10^-16.
FIXED_PRECISION = 16
_FIXED_BYTES = 16
# Wide enough for any 128-bit raw (39 digits) scaled either way.
_EXACT_DIGITS = 120

# How far a recorder connection gap is widened on both sides: the recorder's subscribe takes up
# to a few seconds after its `open` line (Bybit's args go out in chunks of 10, Hyperliquid's one
# message per channel), and a venue clock can run up to a second or two from ours, so a trade
# stamped just outside the close->open span can still have been missed. 5 s covers both with
# margin; a wider gap can only explain more backfilled ids and take more replayed ids out of the
# latency sample, never make a matched id fail.
RECORDER_GAP_MARGIN_NS = 5 * NS_PER_S

# `|ts_init - recv_ns|` beyond this is not a network latency: both processes stamp from the same
# host clock, and 60 s is the catalog readers' read-span margin (`kernel.clocks
# .READ_SPAN_MARGIN_NS`, restated, not imported: `kernel.clocks` is code the reference checks).
MAX_PLAUSIBLE_LATENCY_NS = 60 * NS_PER_S

LIVE = "live"
REBUILT = "rebuilt"
STAGES = (LIVE, REBUILT)

# Second classes (`docs/DATA_DICTIONARY.md` section 1.17).
EXACT = "exact"
LIVE_PROVISIONAL = "live_provisional"
REBUILD_MISMATCH = "rebuild_mismatch"
LIVE_KEPT = "live_kept"
EXPLAINED_LOSS = "explained_loss"
ARCHIVE_DIFFERS = "archive_differs"
MISSING_ROW = "missing_row"
MISSING_ROW_EXPLAINED = "missing_row_explained"
DUPLICATE_ROW = "duplicate_row"
OFF_GRID = "off_grid"
SECOND_CLASSES = (
    EXACT,
    LIVE_PROVISIONAL,
    REBUILD_MISMATCH,
    LIVE_KEPT,
    EXPLAINED_LOSS,
    ARCHIVE_DIFFERS,
    MISSING_ROW,
    MISSING_ROW_EXPLAINED,
    DUPLICATE_ROW,
    OFF_GRID,
)
FAILING_SECONDS = frozenset(
    {REBUILD_MISMATCH, ARCHIVE_DIFFERS, MISSING_ROW, DUPLICATE_ROW, OFF_GRID}
)

# What a second's id discrepancies amount to (`SecondFacts.discrepancy`).
NO_DISCREPANCY = "none"
EXPLAINED_ONLY = "explained"
UNEXPLAINED = "unexplained"


class OffGrid(Exception):
    """A value that is not a whole number of units at the row's precision: it cannot be stored."""


def _exact() -> Context:
    """Return a context whose `Inexact` trap makes any rounding an error, never another number."""
    return Context(prec=_EXACT_DIGITS, traps=[Inexact])


def fixed_raw(value: bytes) -> int:
    """Return the archive's signed 128-bit little-endian fixed-point integer."""
    if len(value) != _FIXED_BYTES:
        raise ValueError(f"a fixed-point value of {len(value)} bytes, not {_FIXED_BYTES}")
    return int.from_bytes(value, "little", signed=True)


def decode_fixed(raw: int) -> Decimal:
    """`raw` * 10^-16, exactly."""
    return Decimal(raw).scaleb(-FIXED_PRECISION, context=_exact())


def whole_at(raw: int, precision: int) -> bool:
    """Whether a raw value is a whole number of units at `precision` (0..16)."""
    if not 0 <= precision <= FIXED_PRECISION:
        raise ValueError(f"precision {precision} outside 0..{FIXED_PRECISION}")
    return raw % 10 ** (FIXED_PRECISION - precision) == 0


def units(value: Decimal, precision: int) -> int:
    """`value` as an integer count of 10^-precision; `OffGrid` when it is not one."""
    scaled = value.scaleb(precision, context=_exact())
    if scaled != scaled.to_integral_value(context=_exact()):
        raise OffGrid(f"{value} is not a whole number of 10^-{precision}")
    return int(scaled)


@dataclass(frozen=True)
class ArchivedTrade:
    """
    One archived trade, decoded. `on_grid`: price and size are whole units at their file's
    precisions. `order` is its fold position, `(ts_event, ts_init, row position)`, replaced by
    the reference's own order when the reference saw the id.
    """

    trade_id: str
    price: Decimal
    size: Decimal
    side: int
    ts_event: int
    ts_init: int
    on_grid: bool
    order: tuple[int, ...]


@dataclass(frozen=True)
class StoredTrade:
    """One archived row as stored: the fixed-point bytes, the enum value and its file's precisions."""

    price: bytes
    size: bytes
    aggressor_side: int
    trade_id: str
    ts_event: int
    ts_init: int
    price_precision: int
    size_precision: int


def archived_trade(stored: StoredTrade, position: int) -> ArchivedTrade:
    """Decode one stored trade row (`position`: its place in the day, the last tie-break)."""
    price, size = fixed_raw(stored.price), fixed_raw(stored.size)
    on_grid = whole_at(price, stored.price_precision) and whole_at(size, stored.size_precision)
    return ArchivedTrade(
        trade_id=stored.trade_id,
        price=decode_fixed(price),
        size=decode_fixed(size),
        side=stored.aggressor_side,
        ts_event=stored.ts_event,
        ts_init=stored.ts_init,
        on_grid=on_grid,
        order=(stored.ts_event, stored.ts_init, position),
    )


@dataclass(frozen=True)
class TradeColumns:
    """The eight trade columns of one second, in stored integer units (OHLC None when empty)."""

    open_price: int | None = None
    high_price: int | None = None
    low_price: int | None = None
    close_price: int | None = None
    buy_volume: int = 0
    sell_volume: int = 0
    buy_count: int = 0
    sell_count: int = 0


@dataclass(frozen=True)
class StoredRow:
    """One snapshot row's trade columns, the precisions its integers count at, and its `ts_event`."""

    price_precision: int
    size_precision: int
    columns: TradeColumns
    ts_event: int


class Foldable(Protocol):
    """A trade the fold can place: its values, aggressor side and order key."""

    @property
    def price(self) -> Decimal: ...

    @property
    def size(self) -> Decimal: ...

    @property
    def side(self) -> int: ...

    @property
    def order(self) -> tuple[int, ...]: ...


def fold_second(
    trades: Iterable[Foldable], price_precision: int, size_precision: int
) -> TradeColumns:
    """
    Fold one exchange second the reference's way, in units at the row's precisions: open/close the
    first/last by `order`, high/low the max/min, buy = BUYER and sell = every other side (the
    documented NO_AGGRESSOR convention). `OffGrid` for a value the precision cannot hold.

    Known limit (REST-only tie order): a trade the reference saw only in a Bybit `recent-trade`
    poll is ordered by that poll's `recv_ns` (`ReferenceTrade.order`), so within its millisecond
    it folds after every WS trade, which production's rebuild also does for a backfilled trade
    (arrival order); the venue's own order of the tie (Bybit's `seq`) may differ. Such a tie can
    make open/close differ between the row and this fold: a loud false `rebuild_mismatch`, never
    a false pass. Upgrade path: order Bybit ties by the venue's `seq`, on both sides.
    """
    ordered = sorted(trades, key=lambda trade: trade.order)
    if not ordered:
        return TradeColumns()
    prices = [units(trade.price, price_precision) for trade in ordered]
    buys = [units(t.size, size_precision) for t in ordered if t.side == BUYER]
    sells = [units(t.size, size_precision) for t in ordered if t.side != BUYER]
    return TradeColumns(
        open_price=prices[0],
        high_price=max(prices),
        low_price=min(prices),
        close_price=prices[-1],
        buy_volume=sum(buys),
        sell_volume=sum(sells),
        buy_count=len(buys),
        sell_count=len(sells),
    )


def _line_time(line: Mapping[str, object], where: str) -> int:
    key = "ts_ns" if line.get("kind") == "connection" else "recv_ns"
    value = line.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise MalformedLine(f"{where}: a line without an integer `{key}`")
    return value


def _event(line: Mapping[str, object], where: str) -> str:
    event = line.get("event")
    if event not in ("open", "close", "error"):
        raise MalformedLine(f"{where}: connection event {event!r}")
    return str(event)


def _gap_start(gap_from: int | None, previous: int | None, since_ns: int) -> int:
    """
    Where the gap an `open` ends began: the open gap's `close`/`error`; without one (a crash's
    `startup` open, or a reconnect whose `close` lies before the read) the channel's previous
    line, or the read's start when the `open` is its first line.
    """
    if gap_from is not None:
        return gap_from
    return since_ns if previous is None else previous


def recorder_gaps(lines: Iterable[Mapping[str, object]], since_ns: int, until_ns: int) -> Intervals:
    """
    Return the recorder's connection gaps on one channel, from its own lines in file order, read
    from `since_ns` to `until_ns`: a `close` or `error` to the next `open`; an `open` with no gap
    open before it from the channel's previous line (`since_ns` when there is none: the gap began
    before the read); a gap still open at the end runs to `until_ns`. Each is widened by
    `RECORDER_GAP_MARGIN_NS` on both sides.
    """
    spans: list[tuple[int, int]] = []
    gap_from: int | None = None
    previous: int | None = None
    for number, line in enumerate(lines):
        where = f"connection line {number}"
        if not isinstance(line, Mapping):
            raise MalformedLine(f"{where}: a raw line that is not a JSON object: {line!r:.200}")
        stamp = _line_time(line, where)
        if line.get("kind") == "connection":
            if _event(line, where) == "open":
                spans.append((_gap_start(gap_from, previous, since_ns), stamp))
                gap_from = None
            elif gap_from is None:
                gap_from = stamp
        previous = stamp
    if gap_from is not None:
        spans.append((gap_from, until_ns))
    margin = RECORDER_GAP_MARGIN_NS
    return Intervals.of((low - margin, high + margin) for low, high in spans)


@dataclass(frozen=True)
class ReferenceId:
    """
    One reference id: the copy the fold and the comparison use (the first WS frame's, else the
    first poll's), that frame's `recv_ns` (None: seen only by REST) and whether another copy of
    the id disagreed with it (`reference_conflict`).
    """

    trade: ReferenceTrade
    ws_recv_ns: int | None
    conflict: bool


def _values(trade: ReferenceTrade) -> tuple[Decimal, Decimal, int, int]:
    return (trade.price, trade.size, trade.side, trade.ts_ns)


def merge_reference(trades: Iterable[ReferenceTrade]) -> dict[str, ReferenceId]:
    """Group every copy of each id (WS frames, replays, polls) into one `ReferenceId`."""
    copies: dict[str, list[ReferenceTrade]] = {}
    for trade in trades:
        copies.setdefault(trade.trade_id, []).append(trade)
    merged = {}
    for trade_id, found in copies.items():
        ws = [trade for trade in found if not trade.via_rest]
        kept = min(ws or found, key=lambda trade: trade.order)
        conflict = any(_values(trade) != _values(kept) for trade in found)
        merged[trade_id] = ReferenceId(kept, kept.order[1] if ws else None, conflict)
    return merged


@dataclass(frozen=True)
class LatencyHistogram:
    """`ts_init - recv_ns` of matched live ids, in whole milliseconds (floor) -> count."""

    buckets: Mapping[int, int] = MappingProxyType({})

    def plus(self, other: "LatencyHistogram") -> "LatencyHistogram":
        return LatencyHistogram(dict(Counter(self.buckets) + Counter(other.buckets)))

    @property
    def count(self) -> int:
        return sum(self.buckets.values())

    def quantile_ms(self, numerator: int, denominator: int) -> int | None:
        """Return the nearest-rank quantile `numerator / denominator`; None when empty."""
        rank = max(1, -(-numerator * self.count // denominator))
        seen = 0
        for millis in sorted(self.buckets):
            seen += self.buckets[millis]
            if seen >= rank:
                return millis
        return None

    def summary(self) -> dict[str, int | None]:
        keys = sorted(self.buckets)
        return {
            "count": self.count,
            "min_ms": keys[0] if keys else None,
            "p50_ms": self.quantile_ms(50, 100),
            "p99_ms": self.quantile_ms(99, 100),
            "max_ms": keys[-1] if keys else None,
        }


@dataclass(frozen=True)
class IdCounts:
    """
    One instrument's id comparison (`docs/DATA_DICTIONARY.md` section 1.17). `seen` = reference
    ids with venue time in the window; `matched` + `missing_*` = `seen`; `extra_*` = archived
    ids the reference did not see; `mismatch_*` compare matched ids field by field;
    `backfilled` (matched ids on a `trades_backfilled` line) and `wire_no_aggressor` (reference
    ids whose wire side was no known token, `no_aggressor_tokens` the tokens) are evidence only.
    """

    seen: int = 0
    matched: int = 0
    backfilled: int = 0
    missing_explained: int = 0
    missing_unexplained: int = 0
    extra_explained: int = 0
    extra_unexplained: int = 0
    duplicated: int = 0
    mismatch_price: int = 0
    mismatch_size: int = 0
    mismatch_side: int = 0
    mismatch_ts_event: int = 0
    reference_conflict: int = 0
    off_precision: int = 0
    implausible_latency: int = 0
    wire_no_aggressor: int = 0
    no_aggressor_tokens: tuple[str, ...] = ()
    examples: tuple[str, ...] = ()

    def plus(self, other: "IdCounts") -> "IdCounts":
        sums = {name: getattr(self, name) + getattr(other, name) for name in _ID_SUMS}
        tokens = tuple(sorted(set(self.no_aggressor_tokens) | set(other.no_aggressor_tokens)))
        examples = (self.examples + other.examples)[:EXAMPLES]
        return replace(self, **sums, no_aggressor_tokens=tokens, examples=examples)

    @property
    def failing(self) -> int:
        return sum(getattr(self, name) for name in FAILING_IDS)


_ID_SUMS = frozenset(
    f.name for f in fields(IdCounts) if f.name not in ("no_aggressor_tokens", "examples")
)
FAILING_IDS = (
    "missing_unexplained",
    "extra_unexplained",
    "duplicated",
    "mismatch_price",
    "mismatch_size",
    "mismatch_side",
    "mismatch_ts_event",
    "reference_conflict",
    "off_precision",
    "implausible_latency",
)


@dataclass(frozen=True)
class IdContext:
    """What explains an archive-only id: the backfilled ids and the channel's recorder gaps."""

    backfilled: frozenset[str]
    recorder_gaps: Intervals


class _HourTally:
    """The mutable working state of one hour's comparison (never escapes `compare_ids`)."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.examples: list[str] = []
        self.explained: set[int] = set()
        self.failed: set[int] = set()
        self.latency: Counter[int] = Counter()
        self.folded: list[ArchivedTrade] = []

    def fail(self, name: str, trade_id: str, *ts_ns: int) -> None:
        self.counts[name] += 1
        self.failed.update(ts // NS_PER_S for ts in ts_ns)
        if len(self.examples) < EXAMPLES:
            self.examples.append(f"{name}:{trade_id}")


@dataclass(frozen=True)
class HourIds:
    """
    One hour's id comparison: the counts, the latency samples, the explanations left for the
    next hour, the archive's trades as the second fold takes them (each id once, a seen id in
    the reference's order) and, per second, whether its id discrepancies were all explained.
    """

    counts: IdCounts
    latency: LatencyHistogram
    explanations: Explanations
    folded: tuple[ArchivedTrade, ...]
    explained_seconds: frozenset[int]
    failed_seconds: frozenset[int]

    def discrepancy(self, second: int) -> str:
        if second in self.failed_seconds:
            return UNEXPLAINED
        return EXPLAINED_ONLY if second in self.explained_seconds else NO_DISCREPANCY


def _compare_fields(ref: ReferenceId, archived: ArchivedTrade, tally: _HourTally) -> None:
    trade = ref.trade
    both = (trade.ts_ns, archived.ts_event)
    checks = (
        ("mismatch_price", trade.price == archived.price),
        ("mismatch_size", trade.size == archived.size),
        ("mismatch_side", trade.side == archived.side),
        ("mismatch_ts_event", trade.ts_ns == archived.ts_event),
    )
    for name, equal in checks:
        if not equal:
            tally.fail(name, trade.trade_id, *both)
    if ref.conflict:
        tally.fail("reference_conflict", trade.trade_id, trade.ts_ns)


def _latency(ref: ReferenceId, archived: ArchivedTrade, ctx: IdContext, tally: _HourTally) -> None:
    """
    Sample `ts_init - recv_ns` of a live receipt only. A trade inside a recorder gap reached the
    recorder, if at all, in a replay: Hyperliquid's `trades` subscribe answers with the recent
    trades (the soak's startup frame held trades 55 s old), so its first WS frame is the
    reconnect, not the trade's arrival, and would be a false `implausible_latency`.
    """
    if ref.ws_recv_ns is None or archived.trade_id in ctx.backfilled:
        return
    if ctx.recorder_gaps.contains(archived.ts_event):
        return
    delta = archived.ts_init - ref.ws_recv_ns
    tally.latency[delta // NS_PER_MS] += 1
    if abs(delta) > MAX_PLAUSIBLE_LATENCY_NS:
        tally.fail("implausible_latency", archived.trade_id, archived.ts_event)


def _match(ref: ReferenceId, archived: ArchivedTrade, ctx: IdContext, tally: _HourTally) -> None:
    tally.counts["matched"] += 1
    tally.counts["backfilled"] += archived.trade_id in ctx.backfilled
    _compare_fields(ref, archived, tally)
    _latency(ref, archived, ctx, tally)
    tally.folded.append(replace(archived, order=ref.trade.order))


def _extra(archived: ArchivedTrade, ctx: IdContext, tally: _HourTally) -> None:
    explained = archived.trade_id in ctx.backfilled and ctx.recorder_gaps.contains(
        archived.ts_event
    )
    if explained:
        tally.counts["extra_explained"] += 1
        tally.explained.add(archived.ts_event // NS_PER_S)
    else:
        tally.fail("extra_unexplained", archived.trade_id, archived.ts_event)
    tally.folded.append(archived)


def _first_copies(archived: Sequence[ArchivedTrade], tally: _HourTally) -> dict[str, ArchivedTrade]:
    """Each archived id's first copy; an off-precision copy is counted (per stored row)."""
    first: dict[str, ArchivedTrade] = {}
    for trade in archived:
        if not trade.on_grid:
            tally.fail("off_precision", trade.trade_id, trade.ts_event)
        first.setdefault(trade.trade_id, trade)
    return first


def _missing(
    reference: Mapping[str, ReferenceId],
    missing_ids: Iterable[str],
    explanations: Explanations,
    tally: _HourTally,
) -> Explanations:
    missing = [(trade_id, reference[trade_id].trade.ts_ns) for trade_id in missing_ids]
    unexplained, explanations = explanations.explain(missing)
    lost = set(unexplained)
    for trade_id, ts_ns in missing:
        if trade_id in lost:
            tally.fail("missing_unexplained", trade_id, ts_ns)
        else:
            tally.counts["missing_explained"] += 1
            tally.explained.add(ts_ns // NS_PER_S)
    return explanations


def _wire_sides(reference: Mapping[str, ReferenceId], tally: _HourTally) -> tuple[str, ...]:
    """Count the ids whose wire side was no known token; return the distinct tokens."""
    unknown = [r.trade.side_token for r in reference.values() if r.trade.side == NO_AGGRESSOR]
    tally.counts["wire_no_aggressor"] += len(unknown)
    return tuple(sorted(set(unknown)))


def compare_ids(
    reference: Mapping[str, ReferenceId],
    archived: Sequence[ArchivedTrade],
    ctx: IdContext,
    explanations: Explanations,
) -> HourIds:
    """
    Compare one hour id by id: the reference ids and the archived trades whose own venue time
    lies in it. Every reference id is matched (field by field) or missing (explained by the
    coverage record and markers, spending `explanations`); every other archived id is extra.

    Example: reference {a: 1.5 @ S, b: 2 @ S}, archive {a: 1.5 @ S, c: 3 @ S}, c backfilled
    inside a recorder gap -> matched 1, missing_unexplained 1 (b), extra_explained 1 (c).
    """
    tally = _HourTally()
    first = _first_copies(archived, tally)
    tally.counts["seen"] = len(reference)
    # Sorted, never set order: the examples and the tally must not vary with hash randomization.
    seen = sorted(reference, key=lambda trade_id: (reference[trade_id].trade.ts_ns, trade_id))
    for trade_id in [i for i in seen if i in first]:
        _match(reference[trade_id], first[trade_id], ctx, tally)
    left = _missing(reference, [i for i in seen if i not in first], explanations, tally)
    extra = sorted(first.keys() - reference.keys(), key=lambda i: (first[i].ts_event, i))
    for trade_id in extra:
        _extra(first[trade_id], ctx, tally)
    tokens = _wire_sides(reference, tally)
    base = IdCounts(no_aggressor_tokens=tokens, examples=tuple(tally.examples))
    counts = replace(base, **cast(dict[str, Any], tally.counts))  # every key an int field
    return HourIds(
        counts=counts,
        latency=LatencyHistogram(dict(tally.latency)),
        explanations=left,
        folded=tuple(tally.folded),
        explained_seconds=frozenset(tally.explained),
        failed_seconds=frozenset(tally.failed),
    )


@dataclass(frozen=True)
class SecondFacts:
    """
    Everything one second is judged on: its stored rows, the reference's trades and the
    archive's (`HourIds.folded`) with venue time in it, whether the rebuild keeps its live
    values by design (`rebuild_exempt`: inside an archive-gap marker span, or before the
    instrument's first archived trade), whether a coverage `seconds` run covers it, and what its
    id discrepancies amount to (`HourIds.discrepancy`).
    """

    rows: Sequence[StoredRow]
    reference: Sequence[Foldable]
    archived: Sequence[Foldable]
    rebuild_exempt: bool
    run_covers: bool
    discrepancy: str


def _row_differs(stage: str, rebuild_exempt: bool) -> str:
    """Classify a row the rebuild must correct: it matches neither the reference nor the archive."""
    if stage == LIVE:
        return LIVE_PROVISIONAL
    return LIVE_KEPT if rebuild_exempt else REBUILD_MISMATCH


def _judge_row(
    row: TradeColumns, ref: TradeColumns, arc: TradeColumns, facts: SecondFacts, stage: str
) -> str:
    """
    Classify a row that differs from the reference fold. The archive is the rebuild's input, so
    the row is judged against it whenever the archive is trusted -- it agrees with the reference,
    or every id discrepancy of the second is explained: a row equal to a differing archive is an
    explained loss, any other row is one the rebuild must correct (never an explained loss).
    """
    if arc != ref and facts.discrepancy != EXPLAINED_ONLY:
        return ARCHIVE_DIFFERS
    if arc != ref and row == arc:
        return EXPLAINED_LOSS
    return _row_differs(stage, facts.rebuild_exempt)


def classify_second(facts: SecondFacts, stage: str) -> str:
    """
    Classify one judged second (it has a row or reference trades): the row against the
    reference fold `ref` and against `arc`, the same fold over the archive's trades.
    """
    if len(facts.rows) > 1:
        return DUPLICATE_ROW
    if not facts.rows:
        return MISSING_ROW_EXPLAINED if facts.run_covers else MISSING_ROW
    (row,) = facts.rows
    try:
        ref = fold_second(facts.reference, row.price_precision, row.size_precision)
        arc = fold_second(facts.archived, row.price_precision, row.size_precision)
    except OffGrid:
        return OFF_GRID
    if row.columns == ref:
        return EXACT
    return _judge_row(row.columns, ref, arc, facts, stage)


@dataclass(frozen=True)
class SecondTally:
    """Judged seconds per class, and the first failing seconds `(second, class)` for diagnosis."""

    classes: Mapping[str, int] = MappingProxyType({})
    examples: tuple[tuple[int, str], ...] = ()

    def plus(self, other: "SecondTally") -> "SecondTally":
        classes = dict(Counter(self.classes) + Counter(other.classes))
        return SecondTally(classes, (self.examples + other.examples)[:EXAMPLES])

    @property
    def failing(self) -> int:
        return sum(count for name, count in self.classes.items() if name in FAILING_SECONDS)


def tally_seconds(judged: Iterable[tuple[int, str]]) -> SecondTally:
    """Count `(second, class)` verdicts."""
    classes: Counter[str] = Counter()
    examples: list[tuple[int, str]] = []
    for second, verdict in judged:
        classes[verdict] += 1
        if verdict in FAILING_SECONDS and len(examples) < EXAMPLES:
            examples.append((second, verdict))
    return SecondTally(dict(classes), tuple(examples))


@dataclass(frozen=True)
class InstrumentTrades:
    """One instrument's result: it passes only with every failing id and second count at 0."""

    instrument_id: str
    ids: IdCounts
    seconds: SecondTally
    latency: LatencyHistogram

    @property
    def passed(self) -> bool:
        return self.ids.failing == 0 and self.seconds.failing == 0


@dataclass(frozen=True)
class TradesDayReport:
    """
    A venue's checked window (a closed UTC day, `hours` its hour labels): it passes only when
    every instrument does, the coverage record exists and no raw reference hour is missing. A
    `live` stage verdict is provisional: the nightly rebuild has yet to rewrite the rows.
    """

    venue: str
    day: str
    stage: str
    hours: tuple[str, str]
    coverage_file: str
    coverage_present: bool
    missing_raw_files: tuple[str, ...]
    truncated_neighbour_files: tuple[str, ...]
    instruments: tuple[InstrumentTrades, ...]

    @property
    def provisional(self) -> bool:
        return self.stage == LIVE

    @property
    def passed(self) -> bool:
        inputs_whole = self.coverage_present and not self.missing_raw_files
        return inputs_whole and all(report.passed for report in self.instruments)
