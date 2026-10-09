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
The candles tool's pure rules (Story 31.8): every bucket of one UTC day, on every one of the ten
chart widths, judged against three sources -- the fold of the catalog's rows (exact), the
reference fold of the recorder's trades over the seconds the catalog observed (every difference
explained per second, or failing), and the coverage record (every unobserved second explained).

The reference side never imports the code it checks (DATA-02): standard library and
`verification.domain` only. The folds are the independent reference's (`fold_candles`,
`bucket_start`, `fold_second`), the comparison is `at_places` (never a tolerance), and the
production rules checked here are restated from `docs/DATA_DICTIONARY.md` section 2.5 and the
`GET /api/candles` route, never imported from `candles`, `views` or `data_api`.

Classes, per width and bucket of the day:
- observation: `traded` (a trade in a row of the bucket), `untraded` (rows, no trade) and
  `no_data` (no row) -- reported, none failing by itself; every second without a row takes its
  reason from the coverage `seconds` runs, and one without a reason is `unexplained` (failing);
- catalog fold (the six stored widths): 31.7's `judge_width` classes (`CANDLE_CLASSES`);
- served (all ten widths, when checked): `exact` / `float_noise` (reported apart) /
  `served_differs` / `served_missing` (a traded bucket not served) / `served_extra` (a served bucket
  not traded), and `partial_ok` / `partial_mismatch` (the flag absent or not the restated rule);
- reference: `exact` / `both_undefined` when the masked reference fold equals the catalog fold,
  else every differing second is in a recorder gap (`ref_recorder_gap`), a coverage trade window
  or an archive-gap marker span (`ref_explained`), or the bucket is `ref_different` (failing);
  `tick_rounded` (a known cause, not failing, `trade_check.KNOWN_CAUSES`): the buckets equal, and
  one of its seconds the reference could fold only by rounding the venue's sub-tick prints to the
  row's tick (`RoundedFold`, audit D-91) -- the same rule as `verification.trades`.

The reference is compared with the catalog fold; the stored and served bars are proven equal to
that same fold above, so by transitivity each of them equals the masked reference exactly when
the catalog fold does -- and a bar that differs from the catalog fold already fails there.
"""

from bisect import bisect_left
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from decimal import ROUND_FLOOR
from decimal import Decimal
from decimal import localcontext
from fractions import Fraction
from itertools import product
from types import MappingProxyType

from verification.domain.catalog_check import BOTH_UNDEFINED
from verification.domain.catalog_check import EXACT
from verification.domain.catalog_check import FLOAT_NOISE
from verification.domain.catalog_check import MS_PER_S
from verification.domain.catalog_check import NS_PER_MS
from verification.domain.catalog_check import STORE_BAR_SECONDS
from verification.domain.catalog_check import StoredBar
from verification.domain.catalog_check import TradeRow
from verification.domain.catalog_check import WidthCandles
from verification.domain.catalog_check import aggregates_agree
from verification.domain.catalog_check import judge_width
from verification.domain.conservation import EXAMPLES
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import SECONDS_PER_DAY
from verification.domain.conservation import Intervals
from verification.domain.conservation import SecondCounts
from verification.domain.reference_signals import DIGITS
from verification.domain.reference_signals import WEEK_SECONDS
from verification.domain.reference_signals import KnownLiquidations
from verification.domain.reference_signals import RefBook
from verification.domain.reference_signals import RefCandle
from verification.domain.reference_signals import bucket_start
from verification.domain.reference_signals import fold_candles
from verification.domain.reference_signals import units_exactly
from verification.domain.signal_compare import FAILING
from verification.domain.signal_compare import Agreement
from verification.domain.signal_compare import at_places
from verification.domain.trade_check import TICK_ROUNDED
from verification.domain.trade_check import Foldable
from verification.domain.trade_check import OffGrid
from verification.domain.trade_check import TradeColumns
from verification.domain.trade_check import fold_second
from verification.domain.trade_check import tick_rounded
from verification.domain.trade_check import whole


# The widths `data_api` folds at read time from the raw 1 s rows (`docs/DATA_DICTIONARY.md` section
# 2.5; the chart's timeframe selector: 10m, 30m, 45m, 1W), restated, never imported.
READ_TIME_BAR_SECONDS = (600, 1800, 2700, WEEK_SECONDS)
# The widths judged bucket by bucket inside the day: every width but 1W, whose bucket spans seven.
DAY_BAR_SECONDS = (*STORE_BAR_SECONDS, *READ_TIME_BAR_SECONDS[:-1])
ALL_BAR_SECONDS = (*STORE_BAR_SECONDS, *READ_TIME_BAR_SECONDS)
# Section 2.5's `partial`: a bucket observed for fewer than 90 % of its seconds
# (`seconds_observed < 0.9 x width`). `is_partial` reads it as the exact decimal fraction 9/10 (its
# text, never its binary float), so the rule is judged exactly, not a float product of it.
PARTIAL_OBSERVED_FRACTION = 0.9
# The route's server-side cap on `limit` (`data_api/routes/candles.py`), restated.
SERVED_PAGE_LIMIT = 500
NS_PER_DAY = SECONDS_PER_DAY * NS_PER_S

# --- observation ----------------------------------------------------------------------------------
TRADED = "traded"
UNTRADED = "untraded"
NO_DATA = "no_data"
BUCKET_KINDS = (TRADED, UNTRADED, NO_DATA)
# The reason a reference trade in a second without a row is counted under when no coverage run
# names that second (the second itself is then an unexplained, failing second).
NO_REASON = "unexplained"

# --- served ---------------------------------------------------------------------------------------
SERVED_DIFFERS = "served_differs"
SERVED_MISSING = "served_missing"
SERVED_EXTRA = "served_extra"
PARTIAL_OK = "partial_ok"
PARTIAL_MISMATCH = "partial_mismatch"
SERVED_CLASSES = (
    EXACT,
    FLOAT_NOISE,
    SERVED_DIFFERS,
    SERVED_MISSING,
    SERVED_EXTRA,
    PARTIAL_OK,
    PARTIAL_MISMATCH,
)
FAILING_SERVED = frozenset({SERVED_DIFFERS, SERVED_MISSING, SERVED_EXTRA, PARTIAL_MISMATCH})

# --- reference ------------------------------------------------------------------------------------
REF_RECORDER_GAP = "ref_recorder_gap"
REF_EXPLAINED = "ref_explained"
REF_DIFFERENT = "ref_different"
REFERENCE_CLASSES = (
    EXACT,
    BOTH_UNDEFINED,
    TICK_ROUNDED,
    REF_RECORDER_GAP,
    REF_EXPLAINED,
    REF_DIFFERENT,
)
FAILING_REFERENCE = frozenset({REF_DIFFERENT})
# How many exact-half sub-tick prints one second may hold before the tick-rounded fold stops trying
# both neighbours of each (2^n folds): a second past it stays off grid, failing, never guessed.
MAX_TICK_TIES = 6

# --- 1W -------------------------------------------------------------------------------------------
WEEK_OPEN = "week_open"
WEEK_JUDGED = "judged"
OMITTED = "omitted"  # the chart-like fetch left the week out: allowed, never a truncated week
NOT_FETCHED = "not_fetched"  # the chart-like fetch would reach past now
WEEK_CLASSES = (*SERVED_CLASSES, OMITTED, NOT_FETCHED)


def is_partial(seconds_observed: int, bar_seconds: int) -> bool:
    """Section 2.5's rule: observed for fewer than 90 % of the bucket's seconds."""
    return seconds_observed < Fraction(str(PARTIAL_OBSERVED_FRACTION)) * bar_seconds


def week_start_ms(day_start_ns: int) -> int:
    """Return the Monday-anchored 1W bucket (ms) the day lies in (the reference `bucket_start`)."""
    return bucket_start(day_start_ns // NS_PER_MS, WEEK_SECONDS)


@dataclass(frozen=True)
class ServedBar:
    """
    One served item with `o` set: its values as the JSON carried them, `partial` None if absent,
    and §2.15's ten integer keys (None when the item carried null or nothing).
    """

    t: int
    o: float | None
    h: float | None
    l: float | None
    c: float | None
    v: float | None
    partial: bool | None
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
class ServedPage:
    """One served page's bars (`o` set, oldest first) and whether the route says older exist."""

    bars: tuple[ServedBar, ...]
    has_more: bool


# --- the reference per second -------------------------------------------------------------------


def row_columns(row: TradeRow) -> TradeColumns:
    """Return a row's eight trade columns, as the reference fold spells them."""
    return TradeColumns(
        open_price=row.open_price,
        high_price=row.high_price,
        low_price=row.low_price,
        close_price=row.close_price,
        buy_volume=row.buy_volume,
        sell_volume=row.sell_volume,
        buy_count=row.buy_count,
        sell_count=row.sell_count,
    )


@dataclass(frozen=True)
class RoundedFold:
    """
    A second the reference can fold only by rounding its sub-tick prices to the row's tick, and
    whose rounded fold is exactly the row (the known cause D-91, `TICK_ROUNDED`). `differences`
    names each rounded print as `venue <price> -> stored <price>`, in fold order.
    """

    columns: TradeColumns
    differences: tuple[str, ...]


@dataclass(frozen=True)
class _Print:
    """A reference trade with its price replaced by a tick candidate (`Foldable`)."""

    price: Decimal
    size: Decimal
    side: int
    order: tuple[int, ...]


def fold_reference_second(
    trades: Sequence[Foldable], row: TradeRow
) -> TradeColumns | RoundedFold | None:
    """
    Fold one observed second's reference trades at its row's precisions. Off grid: the
    `RoundedFold` when rounding its sub-tick prices to the tick reproduces the row, else None.
    """
    try:
        return fold_second(trades, row.price_precision, row.size_precision)
    except OffGrid:
        return _tick_rounded_fold(trades, row)


def _tick_candidates(price: Decimal, precision: int) -> tuple[Decimal, ...]:
    """Return the tick prices `price` rounds to (two for an exact half), or itself on the tick."""
    if whole(price, precision):
        return (price,)
    unit = Decimal(1).scaleb(-precision)
    low = price.quantize(unit, rounding=ROUND_FLOOR)
    return tuple(c for c in (low, low + unit) if tick_rounded(price, c, precision))


def _tick_rounded_fold(trades: Sequence[Foldable], row: TradeRow) -> RoundedFold | None:
    ordered = sorted(trades, key=lambda trade: trade.order)
    if not all(whole(trade.size, row.size_precision) for trade in ordered):
        return None  # a size off grid is no tick rounding
    options = [_tick_candidates(trade.price, row.price_precision) for trade in ordered]
    if sum(len(option) > 1 for option in options) > MAX_TICK_TIES:
        return None
    want = row_columns(row)
    for prices in product(*options):
        prints = [_Print(p, t.size, t.side, t.order) for p, t in zip(prices, ordered, strict=True)]
        if fold_second(prints, row.price_precision, row.size_precision) == want:
            differences = tuple(
                f"venue {trade.price} -> stored {price}"
                for trade, price in zip(ordered, prices, strict=True)
                if price != trade.price
            )
            return RoundedFold(want, differences)
    return None


def fold_reference(
    by_second: Mapping[int, Sequence[Foldable]],
    rows: Mapping[int, TradeRow],
    reason_of: Mapping[int, str],
) -> tuple[dict[int, TradeColumns | RoundedFold | None], Counter[str]]:
    """
    Fold the reference trades of each second that has a row (keyed by epoch second); count the
    trades of every other second under that second's coverage reason (`NO_REASON` without one):
    `trades_unobserved`, which the bars understate by design.
    """
    folded: dict[int, TradeColumns | RoundedFold | None] = {}
    unobserved: Counter[str] = Counter()
    for second, trades in by_second.items():
        row = rows.get(second)
        if row is None:
            unobserved[reason_of.get(second, NO_REASON)] += len(trades)
        else:
            folded[second] = fold_reference_second(trades, row)
    return folded, unobserved


@dataclass(frozen=True)
class Causes:
    """
    Where a differing second may lie: a recorder gap, or a coverage/archive-gap window.

    Known limit: a cause is per second, not per trade. A capped coverage window (`cap` trades
    recorded) explains any difference in its seconds, and a catalog surplus there reads like a
    loss; `verification.trades` proves both per trade id for the same day (it spends each window's
    cap, `Explanations`, and fails an archive-only id). Upgrade path: carry each second's trade-count
    difference here, spend the window's cap and class a surplus `REF_DIFFERENT`.
    """

    recorder_gaps: Intervals
    explained: Intervals

    def of(self, second: int) -> str:
        """Class one differing second: the recorder gap first (the reference itself was blind)."""
        low, high = second * NS_PER_S, second * NS_PER_S + NS_PER_S - 1
        if _meets(self.recorder_gaps, low, high):
            return REF_RECORDER_GAP
        if _meets(self.explained, low, high):
            return REF_EXPLAINED
        return REF_DIFFERENT


def _meets(intervals: Intervals, low: int, high: int) -> bool:
    """Whether any merged span meets `[low, high]`."""
    index = bisect_right(intervals.starts, high) - 1
    return index >= 0 and intervals.ends[index] >= low


@dataclass(frozen=True)
class PreparedDay:
    """
    One instrument's day, ready to judge: the rows (`ts_event` in the day, in order), their
    decoded books, the masked reference books (each row's trade columns replaced by the reference
    fold of its second), the seconds whose row differs from the reference (sorted), those the
    reference could not fold at the row's precisions (off grid: always differing), and those it
    folded only by rounding sub-tick prints to the tick (`rounded`, each with its differences;
    their rows equal that fold, so they are never differing).
    """

    day_start_s: int
    rows: tuple[TradeRow, ...]
    books: tuple[RefBook, ...]
    masked: tuple[RefBook, ...]
    differing: tuple[int, ...]
    off_grid: frozenset[int]
    causes: Causes
    # The day's archived liquidations and their known start; None for an instrument without the
    # feed, or with nothing archived (§2.15).
    liquidations: KnownLiquidations | None = None
    rounded: Mapping[int, tuple[str, ...]] = MappingProxyType({})

    @property
    def rounded_seconds(self) -> tuple[int, ...]:
        return tuple(sorted(self.rounded))


def _masked_row(row: TradeRow, folded: TradeColumns | None) -> TradeRow:
    return row if folded is None else replace(row, **asdict(folded))


def prepare_day(
    day_start_s: int,
    rows: Sequence[TradeRow],
    reference: Mapping[int, TradeColumns | RoundedFold | None],
    causes: Causes,
    liquidations: KnownLiquidations | None = None,
) -> PreparedDay:
    """
    Build the day's folds' inputs. `reference` holds the fold of every observed second with
    reference trades; an observed second without any folds to empty columns.

    Only the first row of a second carries that second's reference fold; a further row of the
    same second (a duplicate, which the catalog tool counts) carries empty columns. Giving each
    duplicate the whole fold would double the masked reference exactly as the duplicate doubles
    the catalog fold, so the two would agree and the double count would pass unseen.
    """
    masked: list[TradeRow] = []
    differing: set[int] = set()
    off_grid: set[int] = set()
    rounded: dict[int, tuple[str, ...]] = {}
    seen: set[int] = set()
    for row in rows:
        second = row.ts_event // NS_PER_S
        found = reference.get(second, TradeColumns()) if second not in seen else TradeColumns()
        seen.add(second)
        folded = found
        if isinstance(found, RoundedFold):
            rounded[second] = found.differences
            folded = found.columns
        masked.append(_masked_row(row, folded))
        if folded is None:
            off_grid.add(second)
        if folded != row_columns(row):
            differing.add(second)
    return PreparedDay(
        day_start_s=day_start_s,
        rows=tuple(rows),
        books=tuple(row.book() for row in rows),
        masked=tuple(row.book() for row in masked),
        differing=tuple(sorted(differing)),
        off_grid=frozenset(off_grid),
        causes=causes,
        liquidations=liquidations,
        rounded=MappingProxyType(rounded),
    )


# --- judging one bucket ---------------------------------------------------------------------------


def served_agreement(bar: ServedBar, ref: RefCandle, places: tuple[int, int]) -> str:
    """
    o/h/l/c at the bucket's price places and v at its size places (`at_places`), and §2.15's ten
    keys exactly equal in units (`aggregates_agree`: a null where the reference has a value, or
    the reverse, differs).
    """
    price, size = places
    found = [
        at_places(s, r, price)
        for s, r in zip(
            (bar.o, bar.h, bar.l, bar.c), (ref.open, ref.high, ref.low, ref.close), strict=True
        )
    ]
    found.append(at_places(bar.v, ref.volume, size))
    if any(agreement in FAILING for agreement in found) or not aggregates_agree(bar, ref):
        return SERVED_DIFFERS
    return FLOAT_NOISE if Agreement.FLOAT_NOISE in found else EXACT


def partial_verdict(bar: ServedBar, seconds_observed: int, bar_seconds: int) -> str:
    """Check the served flag: present, and equal to the restated rule over the bucket's rows."""
    if bar.partial is None or bar.partial != is_partial(seconds_observed, bar_seconds):
        return PARTIAL_MISMATCH
    return PARTIAL_OK


def judge_served_bucket(
    bar: ServedBar | None, ref: RefCandle | None, places: tuple[int, int], bar_seconds: int
) -> tuple[str, ...]:
    """Class a served bucket: missing or extra, else its agreement and its partial check."""
    traded = ref is not None and ref.close is not None
    if bar is None:
        return (SERVED_MISSING,) if traded else ()
    if ref is None or not traded:
        return (SERVED_EXTRA,)
    return (
        served_agreement(bar, ref, places),
        partial_verdict(bar, ref.seconds_observed, bar_seconds),
    )


def judge_reference(
    catalog: RefCandle,
    masked: RefCandle,
    differing: Sequence[int],
    day: PreparedDay,
    rounded: Sequence[int] = (),
) -> str:
    """
    Judge the masked reference against the catalog fold of one bucket with rows: equal (and
    nothing off grid) is `exact`/`both_undefined`, or the known `tick_rounded` when the bucket
    holds a `rounded` second; otherwise every differing second's cause decides.
    """
    if catalog == masked and not day.off_grid.intersection(differing):
        if rounded:
            return TICK_ROUNDED
        return BOTH_UNDEFINED if catalog.close is None else EXACT
    causes = {day.causes.of(second) for second in differing}
    for verdict in (REF_DIFFERENT, REF_RECORDER_GAP, REF_EXPLAINED):
        if verdict in causes:
            return verdict
    return REF_DIFFERENT  # the folds differ with no differing second: never a pass


def bucket_kind(ref: RefCandle | None) -> str:
    """
    `no_data` without a row (a bucket only a liquidation made has none), `untraded` with rows but
    no trade, else `traded`.
    """
    if ref is None or ref.seconds_observed == 0:
        return NO_DATA
    return UNTRADED if ref.close is None else TRADED


# --- one width of the day -------------------------------------------------------------------------


@dataclass(frozen=True)
class WidthReport:
    """
    One width's buckets of the day: their kinds, the catalog-fold classes (stored widths only),
    the served classes (None: not checked) and the reference classes, with a few examples.
    """

    bar_seconds: int
    buckets: Mapping[str, int]
    catalog: WidthCandles | None
    served: Mapping[str, int] | None
    reference: Mapping[str, int]
    examples: tuple[str, ...]

    @property
    def failing(self) -> int:
        served = sum(self.served[name] for name in FAILING_SERVED) if self.served else 0
        catalog = self.catalog.failing if self.catalog else 0
        return catalog + served + sum(self.reference[name] for name in FAILING_REFERENCE)


def bucket_places(rows: Iterable[TradeRow], bar_seconds: int) -> dict[int, tuple[int, int]]:
    """Return the maximum price and size precision among each bucket's rows."""
    places: dict[int, tuple[int, int]] = {}
    for row in rows:
        t = bucket_start(row.ts_event // NS_PER_MS, bar_seconds)
        price, size = places.get(t, (0, 0))
        places[t] = (max(price, row.price_precision), max(size, row.size_precision))
    return places


def day_grid(day_start_s: int, bar_seconds: int) -> range:
    """Every bucket start (ms) of the day at a width dividing it."""
    start = day_start_s * MS_PER_S
    return range(start, start + SECONDS_PER_DAY * MS_PER_S, bar_seconds * MS_PER_S)


@dataclass(frozen=True)
class _Folds:
    """One width's two folds over the day, and each bucket's places."""

    catalog: Mapping[int, RefCandle]
    masked: Mapping[int, RefCandle]
    places: Mapping[int, tuple[int, int]]


def _served_classes(
    day: PreparedDay,
    folds: _Folds,
    bar_seconds: int,
    served: Mapping[int, ServedBar],
) -> tuple[Counter[str], list[str]]:
    counts: Counter[str] = Counter()
    examples: list[str] = []
    for t in sorted(set(day_grid(day.day_start_s, bar_seconds)) | set(served)):
        ref = folds.catalog.get(t)
        verdicts = judge_served_bucket(served.get(t), ref, folds.places.get(t, (0, 0)), bar_seconds)
        counts.update(verdicts)
        shown = [v for v in verdicts if v in FAILING_SERVED | {FLOAT_NOISE}]
        if shown and len(examples) < EXAMPLES:
            examples.append(f"{bar_seconds} s t={t}: {'/'.join(shown)} served {served.get(t)}")
    return counts, examples


def _reference_classes(
    day: PreparedDay, folds: _Folds, bar_seconds: int
) -> tuple[Counter[str], list[str]]:
    counts: Counter[str] = Counter()
    examples: list[str] = []
    width_ms = bar_seconds * MS_PER_S
    for t, catalog in sorted(folds.catalog.items()):
        low = bisect_left(day.differing, t // MS_PER_S)
        high = bisect_left(day.differing, (t + width_ms) // MS_PER_S)
        seconds = day.differing[low:high]
        rounded = _within(day.rounded_seconds, t, width_ms)
        verdict = judge_reference(catalog, folds.masked[t], seconds, day, rounded)
        counts[verdict] += 1
        if (
            verdict in FAILING_REFERENCE | {REF_RECORDER_GAP, REF_EXPLAINED}
            and len(examples) < EXAMPLES
        ):
            examples.append(f"{bar_seconds} s t={t}: {verdict} seconds {list(seconds[:5])}")
    return counts, examples


def _within(seconds: Sequence[int], t: int, width_ms: int) -> Sequence[int]:
    """Return the sorted `seconds` inside the bucket starting at `t` ms."""
    low = bisect_left(seconds, t // MS_PER_S)
    return seconds[low : bisect_left(seconds, (t + width_ms) // MS_PER_S)]


def _counts(counter: Counter[str], names: Sequence[str]) -> Mapping[str, int]:
    return MappingProxyType({name: counter[name] for name in names})


def judge_day_width(
    day: PreparedDay,
    bar_seconds: int,
    stored: Sequence[StoredBar] | None,
    served: Mapping[int, ServedBar] | None,
) -> WidthReport:
    """Judge every bucket of the day at one width dividing it (`DAY_BAR_SECONDS`)."""
    folds = _Folds(
        fold_candles(day.books, bar_seconds, day.liquidations),
        fold_candles(day.masked, bar_seconds, day.liquidations),
        bucket_places(day.rows, bar_seconds),
    )
    grid = day_grid(day.day_start_s, bar_seconds)
    kinds = Counter(bucket_kind(folds.catalog.get(t)) for t in grid)
    catalog = None
    if stored is not None:
        catalog = judge_width(bar_seconds, day.rows, day.books, stored, day.liquidations)
    reference, examples = _reference_classes(day, folds, bar_seconds)
    served_counts: Mapping[str, int] | None = None
    if served is not None:
        found, served_examples = _served_classes(day, folds, bar_seconds, served)
        served_counts, examples = _counts(found, SERVED_CLASSES), examples + served_examples
    return WidthReport(
        bar_seconds=bar_seconds,
        buckets=_counts(kinds, BUCKET_KINDS),
        catalog=catalog,
        served=served_counts,
        reference=_counts(reference, REFERENCE_CLASSES),
        examples=tuple(examples[:EXAMPLES] + list(catalog.examples if catalog else ())),
    )


# --- 1W -------------------------------------------------------------------------------------------


def merge_candles(t: int, parts: Sequence[RefCandle]) -> RefCandle:
    """
    Fold consecutive buckets (in time order) into one starting at `t`: the week from its days,
    so a week is folded one day in memory at a time (MEM-01), by section 2.5's same rule.
    """
    seconds = sum(part.seconds_observed for part in parts)
    traded = [part for part in parts if part.close is not None]
    flow = merge_aggregates(parts)
    if not traded:
        return RefCandle(t, None, None, None, None, Decimal(0), seconds, **flow)
    return RefCandle(
        t=t,
        open=traded[0].open,
        high=max(p.high for p in traded if p.high is not None),
        low=min(p.low for p in traded if p.low is not None),
        close=traded[-1].close,
        volume=sum((p.volume for p in traded), Decimal(0)),
        seconds_observed=seconds,
        **flow,
    )


# Each §2.15 column's units: (price places, size places) it is counted in.
_UNIT_OF = MappingProxyType(
    {
        "buy_v": (0, 1),
        "sell_v": (0, 1),
        "buy_n": (0, 0),
        "sell_n": (0, 0),
        "pv": (1, 1),
        "liq_long_v": (0, 1),
        "liq_short_v": (0, 1),
        "liq_n": (0, 0),
    }
)


def merge_aggregates(parts: Sequence[RefCandle]) -> dict[str, int | None]:
    """
    Section 2.15's columns of consecutive buckets as one: each part's units read as decimals at
    its own precisions, summed, and counted again at the finest of them; a column None in any
    part is None (unknown stays unknown).
    """
    price_p = max((p.price_precision or 0 for p in parts), default=0)
    size_p = max((p.size_precision or 0 for p in parts), default=0)
    out: dict[str, int | None] = {"price_precision": price_p, "size_precision": size_p}
    for name, (uses_price, uses_size) in _UNIT_OF.items():
        values = [getattr(p, name) for p in parts]
        if any(v is None for v in values):
            out[name] = None
            continue
        out[name] = _merged_units(values, parts, (uses_price, uses_size), (price_p, size_p))
    if all(out[name] is None for name in _UNIT_OF):
        out["price_precision"] = out["size_precision"] = None
    return out


def _merged_units(
    values: list[int],
    parts: Sequence[RefCandle],
    uses: tuple[int, int],
    places: tuple[int, int],
) -> int:
    """Sum one column's parts as exact decimals (`DIGITS`) and count it at `places`."""
    uses_price, uses_size = uses
    with localcontext(prec=DIGITS):
        total = sum(
            (
                Decimal(v).scaleb(
                    -(uses_price * (p.price_precision or 0) + uses_size * (p.size_precision or 0))
                )
                for v, p in zip(values, parts, strict=True)
            ),
            Decimal(0),
        )
    return units_exactly(total, uses_price * places[0] + uses_size * places[1])


@dataclass(frozen=True)
class WeekFacts:
    """
    The 1W bucket holding the day: whether it is closed, the catalog fold of its seven days (None:
    no row in the week) and its places, and the served bar of each fetch -- `aligned` (before the
    week's end) and `chart` (a day later, as the chart's first page asks); `chart_fetched` False
    when that would reach past now; `served_checked` False with `--no-served`.
    """

    week_start: int
    closed: bool
    reference: RefCandle | None
    places: tuple[int, int]
    aligned: ServedBar | None
    chart: ServedBar | None
    chart_fetched: bool
    served_checked: bool


@dataclass(frozen=True)
class WeekReport:
    """The 1W bucket's verdict: `week_open` (not failing) or `judged` with its served classes."""

    week_start: int
    status: str
    kind: str | None
    served: Mapping[str, int] | None
    examples: tuple[str, ...]

    @property
    def failing(self) -> int:
        return sum(self.served[name] for name in FAILING_SERVED) if self.served else 0


def _chart_classes(week: WeekFacts) -> tuple[str, ...]:
    if not week.chart_fetched:
        return (NOT_FETCHED,)
    if week.chart is None:
        return (OMITTED,)  # a chart page may leave the forming week out, never truncate it
    return judge_served_bucket(week.chart, week.reference, week.places, WEEK_SECONDS)


def judge_week(week: WeekFacts) -> WeekReport:
    """Judge the week holding the day, once closed: each served fetch omits it or serves it whole."""
    if not week.closed:
        return WeekReport(week.week_start, WEEK_OPEN, None, None, ())
    kind = bucket_kind(week.reference)
    if not week.served_checked:
        return WeekReport(week.week_start, WEEK_JUDGED, kind, None, ())
    aligned = judge_served_bucket(week.aligned, week.reference, week.places, WEEK_SECONDS)
    found = Counter((*aligned, *_chart_classes(week)))
    examples = tuple(
        f"1W t={week.week_start}: {verdict} aligned {week.aligned} chart {week.chart} "
        f"reference {week.reference}"
        for verdict in sorted(set(found) & FAILING_SERVED)
    )
    return WeekReport(week.week_start, WEEK_JUDGED, kind, _counts(found, WEEK_CLASSES), examples)


# --- the day --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InstrumentCandles:
    """
    One instrument's day: its rows, second accounting (`tally_seconds`: only `unexplained` fails
    here -- duplicates are the catalog tool's), the reference trades of unobserved seconds by
    reason, each day width, stored rows of an unknown width, and the week. `rounded`: the seconds
    of the known cause `tick_rounded` (D-91) with their differences, reported, never failing.
    """

    instrument_id: str
    rows: int
    seconds: SecondCounts
    trades_unobserved: Mapping[str, int]
    widths: tuple[WidthReport, ...]
    unknown_width: int
    week: WeekReport
    rounded: Mapping[int, tuple[str, ...]] = MappingProxyType({})

    @property
    def failing(self) -> int:
        widths = sum(width.failing for width in self.widths)
        return self.seconds.unexplained + self.unknown_width + widths + self.week.failing


@dataclass(frozen=True)
class CandlesDayReport:
    """
    The candles tool's verdict over one venue's closed UTC day. `passed` only with every failing
    count 0 *and* the served bars checked: without them (`--no-served`) the run is `provisional`
    and its verdict never PASS, whatever it found (the exit status still says whether anything
    failed, so a provisional run is scriptable).
    """

    venue: str
    day: str
    served_checked: bool
    data_api: str | None
    candle_store: str
    coverage_file: str
    missing_raw_files: tuple[str, ...]
    truncated_neighbour_files: tuple[str, ...]
    instruments: tuple[InstrumentCandles, ...]

    @property
    def failing(self) -> int:
        return len(self.missing_raw_files) + sum(i.failing for i in self.instruments)

    @property
    def provisional(self) -> bool:
        return not self.served_checked

    @property
    def passed(self) -> bool:
        return self.failing == 0 and self.served_checked

    @property
    def verdict(self) -> str:
        if self.failing:
            return "FAIL"
        return "PROVISIONAL" if self.provisional else "PASS"
