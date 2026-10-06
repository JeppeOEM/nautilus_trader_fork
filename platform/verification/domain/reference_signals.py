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
Reference signals (Story 31.3): every derived value the platform publishes, written from
`docs/DATA_DICTIONARY.md`'s text alone, in exact `Decimal` arithmetic -- the independent side the
production functions are compared with (`verification/tests/test_reference_signals.py`).

Invariant: the reference side never imports the code it checks. This module imports the standard
library only (`decimal`, `math`, `statistics`, `bisect`, `dataclasses`, `typing`, `collections.abc`
for the type names), never `kernel.indicators`, `kernel.second_snapshot`, `candles`, `ranking`,
`views` or `research` -- it even decodes the stored snapshot layout itself (`RefBook.from_stored`,
§1.7's "decode by hand"), so the two decoders are compared too. Floats appear only where the
definition itself is statistical (the z-score, Pearson, lead-lag); every other value is an exact
`Decimal` (a division is carried to `DIGITS` significant digits).

What each function implements, by dictionary section:

- §1.7 decode: `RefBook.from_stored` (gap-encoded integer units plus the row's precisions).
- §2.1 top of book and flow: `mid`, `spread`, `microprice`, `volume_delta`, `cvd`,
  `avg_trade_size`.
- §2.2 OFI: `ofi_step`, `rolling_ofi` -- the Cont, Kukanov and Stoikov (2014) order flow imbalance
  ("The Price Impact of Order Book Events", J. Financial Econometrics 12(1)), summed over the top
  levels as in Xu, Gould and Howison (2019, "Multi-Level Order-Flow Imbalance in a Limit Order
  Book", Market Microstructure and Liquidity 4). The level rule (a level counts only when it exists
  in all four lists) and the gap rule are §2.2's as amended by this story; `zscore` is §2.2's
  z-score (population standard deviation, None when undefined).
- §2.3 OBI: `obi`.
- §2.12 depth: `depth_within_bps` (NaN beyond the stored depth).
- §2.5 candles: `bucket_start`, `fold_candles` (and §2.15's per-bar order-flow and liquidation
  columns, folded from the decoded decimals and scaled back to units at the bucket's finest
  precisions -- never from the stored integers the production fold sums).
- §2.7 Lines mode: `cvd_weighted_price`.
- §3.2/§3.3 price stats: `pct_change`, `price_as_of`, `pct_change_from`, `vol_score_1h`,
  `vol_catalog_24h`, `vol_fast`.
- §2.12 research: `simple_returns`, `resample`, `pearson`, `rolling_pearson`, `lead_lag`,
  `basis_bps`, `funding_per_hour`, `spread_ticks`, `spread_bps`, `run_cvd`.
"""

import math
import statistics
from bisect import bisect_left
from bisect import bisect_right
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import fields
from decimal import Decimal
from decimal import localcontext
from typing import Protocol


# Significant digits of every reference computation: far beyond any stored value (int64 units are
# 19 digits, a price times a size 38), so a sum or product is exact and a division is carried far
# past float64's 17 -- the reference is never the side that rounds.
DIGITS = 60
NS_PER_S = 1_000_000_000
MS_PER_S = 1_000
NS_PER_MS = 1_000_000
SECONDS_PER_DAY = 86_400
WEEK_SECONDS = 604_800
# 1970-01-01 was a Thursday: the first Monday 00:00 UTC is 4 days after the epoch (§2.5).
MONDAY_ANCHOR_S = 4 * SECONDS_PER_DAY
BPS = 10_000


def _exact[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Run `fn` in a `DIGITS`-digit decimal context (never the caller's, never the global one)."""

    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with localcontext(prec=DIGITS):
            return fn(*args, **kwargs)

    wrapped.__name__ = fn.__name__
    wrapped.__doc__ = fn.__doc__
    return wrapped


# --- §1.7: the stored row, decoded by hand --------------------------------------------------------


def _value(units: int, precision: int) -> Decimal:
    """§1.7: `value = units / 10^precision`, exactly as a decimal."""
    if type(units) is not int:
        raise ValueError(f"a stored unit must be an int, got {units!r}")
    return Decimal(units).scaleb(-precision)


def decode_prices(stored: Sequence[int], side: str, precision: int) -> tuple[Decimal, ...]:
    """
    §1.7: `p[0] = stored[0]`, then `p[i] = p[i-1] - stored[i]` for bids and `+` for asks; every
    gap is strictly positive (a zero or negative one is refused, never read).
    """
    units: list[int] = []
    for i, step in enumerate(stored):
        if i == 0:
            units.append(step)
            continue
        if step <= 0:
            raise ValueError(f"{side}: a non-positive stored gap {step}")
        units.append(units[-1] - step if side == "bid" else units[-1] + step)
    return tuple(_value(u, precision) for u in units)


def _int(row: Mapping[str, object], key: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise ValueError(f"stored field {key!r} must be an int, got {value!r}")
    return value


def _maybe_int(row: Mapping[str, object], key: str) -> int | None:
    return None if row[key] is None else _int(row, key)


def _ints(row: Mapping[str, object], key: str) -> list[int]:
    values = row[key]
    if not isinstance(values, list | tuple):
        raise ValueError(f"stored field {key!r} must be a list of int units, got {values!r}")
    units = list(values)
    if not all(type(v) is int for v in units):  # a float or a bool is not a unit
        raise ValueError(f"stored field {key!r} must hold int units, got {values!r}")
    return units


def _optional(units: int | None, precision: int) -> Decimal | None:
    return None if units is None else _value(units, precision)


@dataclass(frozen=True)
class RefBook:
    """
    One decoded stored row (§1.7): level prices best first, sizes, the trade columns, both
    precisions and `ts_event`. A value, built only by `from_stored`.
    """

    ts_event: int
    price_precision: int
    size_precision: int
    bid_prices: tuple[Decimal, ...]
    bid_sizes: tuple[Decimal, ...]
    ask_prices: tuple[Decimal, ...]
    ask_sizes: tuple[Decimal, ...]
    buy_volume: Decimal
    sell_volume: Decimal
    buy_count: int
    sell_count: int
    open_price: Decimal | None
    high_price: Decimal | None
    low_price: Decimal | None
    close_price: Decimal | None

    @classmethod
    def from_stored(cls, row: Mapping[str, object]) -> "RefBook":
        """Decode one stored/wire row (the integer layout of §1.7) without any platform code."""
        pp, sp = _int(row, "price_precision"), _int(row, "size_precision")
        return cls(
            ts_event=_int(row, "ts_event"),
            price_precision=pp,
            size_precision=sp,
            bid_prices=decode_prices(_ints(row, "bid_prices"), "bid", pp),
            bid_sizes=tuple(_value(u, sp) for u in _ints(row, "bid_sizes")),
            ask_prices=decode_prices(_ints(row, "ask_prices"), "ask", pp),
            ask_sizes=tuple(_value(u, sp) for u in _ints(row, "ask_sizes")),
            buy_volume=_value(_int(row, "buy_volume"), sp),
            sell_volume=_value(_int(row, "sell_volume"), sp),
            buy_count=_int(row, "buy_count"),
            sell_count=_int(row, "sell_count"),
            open_price=_optional(_maybe_int(row, "open_price"), pp),
            high_price=_optional(_maybe_int(row, "high_price"), pp),
            low_price=_optional(_maybe_int(row, "low_price"), pp),
            close_price=_optional(_maybe_int(row, "close_price"), pp),
        )

    @property
    def two_sided(self) -> bool:
        return bool(self.bid_prices) and bool(self.ask_prices)


# --- §2.1: top of book and trade flow ---------------------------------------------------------


@_exact
def mid(book: RefBook) -> Decimal | None:
    """`(bid_prices[0] + ask_prices[0]) / 2`; None when a side is empty. Exact at p+1 places."""
    if not book.two_sided:
        return None
    return (book.bid_prices[0] + book.ask_prices[0]) / 2


@_exact
def spread(book: RefBook) -> Decimal | None:
    """`ask_prices[0] - bid_prices[0]`, as written (negative when crossed); None on an empty side."""
    if not book.two_sided:
        return None
    return book.ask_prices[0] - book.bid_prices[0]


@_exact
def microprice(book: RefBook) -> Decimal | None:
    """
    `(bid_prices[0]*ask_sizes[0] + ask_prices[0]*bid_sizes[0]) / (bid_sizes[0]+ask_sizes[0])`;
    None on an empty side or a zero top size total (undefined: never the mid).
    """
    if not book.two_sided:
        return None
    total = book.bid_sizes[0] + book.ask_sizes[0]
    if total == 0:
        return None
    weighted = book.bid_prices[0] * book.ask_sizes[0] + book.ask_prices[0] * book.bid_sizes[0]
    return weighted / total


@_exact
def volume_delta(book: RefBook) -> Decimal:
    """`buy_volume - sell_volume` of one row."""
    return book.buy_volume - book.sell_volume


@_exact
def cvd(books: Sequence[RefBook]) -> Decimal | None:
    """§3.3: sum of `buy_volume - sell_volume` over the window; None for an empty window."""
    if not books:
        return None
    return sum((b.buy_volume - b.sell_volume for b in books), Decimal(0))


@_exact
def avg_trade_size(books: Sequence[RefBook]) -> Decimal | None:
    """`(buy_volume + sell_volume) / (buy_count + sell_count)` over the window; None at 0 trades."""
    count = sum(b.buy_count + b.sell_count for b in books)
    if count == 0:
        return None
    volume = sum((b.buy_volume + b.sell_volume for b in books), Decimal(0))
    return volume / count


# --- §2.3: order book imbalance --------------------------------------------------------------


@_exact
def obi(book: RefBook, levels: int) -> Decimal | None:
    """
    `sum(bid_sizes[:levels]) / (sum(bid_sizes[:levels]) + sum(ask_sizes[:levels]))`; None when the
    total is zero (undefined).
    """
    bid = sum(book.bid_sizes[:levels], Decimal(0))
    total = bid + sum(book.ask_sizes[:levels], Decimal(0))
    if total == 0:
        return None
    return bid / total


# --- §2.2: order flow imbalance ----------------------------------------------------------------


def _bid_term(price: Decimal, size: Decimal, prev_price: Decimal, prev_size: Decimal) -> Decimal:
    """CKS bid term: an improved price adds its size, unchanged the size change, worse withdraws."""
    if price > prev_price:
        return size
    if price == prev_price:
        return size - prev_size
    return -prev_size


def _ask_term(price: Decimal, size: Decimal, prev_price: Decimal, prev_size: Decimal) -> Decimal:
    """CKS ask term: a lower (improved) ask adds its size, unchanged the change, higher withdraws."""
    if price < prev_price:
        return size
    if price == prev_price:
        return size - prev_size
    return -prev_size


@_exact
def ofi_step(prev: RefBook, cur: RefBook, levels: int, usd: bool) -> Decimal:
    """
    One CKS contribution summed over the top `levels`: level i counts only when it exists in the
    current and previous bid and ask lists (`n = min(levels, |bid|, |ask|, |prev_bid|, |prev_ask|)`),
    so a thinner side never biases the sum; `bid_term - ask_term` per level. With `usd`, a price-up
    or unchanged term is valued at the current price, a withdrawal at the previous price (where the
    size actually rested).
    """
    n = min(
        levels, len(cur.bid_prices), len(cur.ask_prices), len(prev.bid_prices), len(prev.ask_prices)
    )
    total = Decimal(0)
    for i in range(n):
        total += _side_term(cur, prev, i, "bid", usd) - _side_term(cur, prev, i, "ask", usd)
    return total


def _side_term(cur: RefBook, prev: RefBook, i: int, side: str, usd: bool) -> Decimal:
    prices, sizes = (
        (cur.bid_prices, cur.bid_sizes) if side == "bid" else (cur.ask_prices, cur.ask_sizes)
    )
    prev_prices, prev_sizes = (
        (prev.bid_prices, prev.bid_sizes) if side == "bid" else (prev.ask_prices, prev.ask_sizes)
    )
    args = (prices[i], sizes[i], prev_prices[i], prev_sizes[i])
    term = _bid_term(*args) if side == "bid" else _ask_term(*args)
    if not usd:
        return term
    withdrawn = (prices[i] < prev_prices[i]) if side == "bid" else (prices[i] > prev_prices[i])
    return term * (prev_prices[i] if withdrawn else prices[i])


@_exact
def rolling_ofi(
    books: Sequence[RefBook], levels: int, window: int, usd: bool, gap_ns: int | None
) -> list[Decimal | None]:
    """
    §2.2's rolling OFI, one reading per row: the sum of the last `window` contributions. A row is a
    baseline -- it contributes nothing and only becomes the previous book -- when it is the first,
    or when its `ts_event` lies more than `gap_ns` (strictly) after the previous row's (a gap clears
    the previous-level state; the window keeps its earlier contributions). A reading is None until
    the first contribution exists; a baseline row reads the unchanged window.
    """
    return [reading for _, reading in _ofi_walk(books, levels, window, usd, gap_ns)]


def _ofi_walk(
    books: Sequence[RefBook], levels: int, window: int, usd: bool, gap_ns: int | None
) -> list[tuple[bool, Decimal | None]]:
    """Per row: whether it contributed, and the window's sum after it."""
    _require_window(window)
    out: list[tuple[bool, Decimal | None]] = []
    contributions: list[Decimal] = []
    prev: RefBook | None = None
    for book in books:
        contributed = prev is not None and not _gapped(prev, book, gap_ns)
        if prev is not None and contributed:
            contributions.append(ofi_step(prev, book, levels, usd))
        prev = book
        recent = contributions[-window:]
        out.append((contributed, sum(recent, Decimal(0)) if recent else None))
    return out


def _gapped(prev: RefBook, book: RefBook, gap_ns: int | None) -> bool:
    return gap_ns is not None and book.ts_event - prev.ts_event > gap_ns


@_exact
def rolling_ofi_z(
    books: Sequence[RefBook],
    levels: int,
    window: int,
    usd: bool,
    gap_ns: int | None,
    z_window: int,
) -> list[float | None]:
    """
    §2.2's z-scored OFI per row: `zscore` of the readings so far -- one reading per contributing
    row (a baseline row adds none and keeps the last z-score) -- over the last `z_window`.
    """
    readings: list[float] = []
    out: list[float | None] = []
    for contributed, reading in _ofi_walk(books, levels, window, usd, gap_ns):
        if contributed and reading is not None:
            readings.append(float(reading))
        out.append(zscore(readings, z_window) if readings else None)
    return out


def _require_window(window: int) -> None:
    if window < 1:
        raise ValueError(f"a window holds at least 1 value, got {window}")


def zscore(history: Sequence[float], window: int) -> float | None:
    """
    §2.2: `(x - mean) / std` of the newest reading against the last `window` readings (itself
    included), population standard deviation (ddof=0), in floats; None with fewer than 2 readings
    or when they are all equal (undefined).
    """
    _require_window(window)
    recent = list(history[-window:])
    if len(recent) < 2 or min(recent) == max(recent):
        return None
    std = statistics.pstdev(recent)
    if std == 0.0:
        return None
    return (recent[-1] - statistics.fmean(recent)) / std


def zscores(readings: Sequence[float], window: int) -> list[float | None]:
    """`zscore` after each reading of a stream (the z-scored OFI's published value per reading)."""
    return [zscore(readings[: i + 1], window) for i in range(len(readings))]


# --- §2.12: depth within a distance of the mid -----------------------------------------------


Distances = list[tuple[Decimal, Decimal]]


@_exact
def bps_distances(book: RefBook, levels: int) -> tuple[Distances, Distances] | None:
    """
    Per side, `(distance, size)` of the top `levels` levels, distance `|price - mid| / mid * 1e4`
    exactly; None when there is no positive mid (an empty side, or a mid <= 0: no bps scale).
    """
    centre = mid(book)
    if centre is None or centre <= 0:
        return None
    bids = [
        ((centre - p) / centre * BPS, s)
        for p, s in zip(book.bid_prices[:levels], book.bid_sizes[:levels], strict=True)
    ]
    asks = [
        ((p - centre) / centre * BPS, s)
        for p, s in zip(book.ask_prices[:levels], book.ask_sizes[:levels], strict=True)
    ]
    return bids, asks


@_exact
def depth_within_bps(
    book: RefBook, levels: int, edges: Sequence[Decimal]
) -> tuple[list[Decimal | None], list[Decimal | None]]:
    """
    §2.12: per side, the summed size of the top `levels` levels whose exact distance from the mid
    is at most each edge (bps); None (NaN) for an edge past the side's deepest stored level, and
    for every edge without a positive mid. Exact: no slack at an edge (production's float slack is
    a pinned Known limit judged by the comparator, §2.13).
    """
    sides = bps_distances(book, levels)
    if sides is None:
        blank: list[Decimal | None] = [None] * len(edges)
        return blank, list(blank)
    bids, asks = sides
    return [depth_at(bids, e) for e in edges], [depth_at(asks, e) for e in edges]


def depth_at(levels: Distances, edge: Decimal) -> Decimal | None:
    """Return the summed size within `edge`; None when the edge lies past the deepest level."""
    if not levels or edge > levels[-1][0]:
        return None
    return sum((size for distance, size in levels if distance <= edge), Decimal(0))


# --- §2.5: candles --------------------------------------------------------------------------


def bucket_start(ts_ms: int, bar_seconds: int) -> int:
    """
    Return the bucket (ms) `ts_ms` falls in: a width dividing a day is aligned to UTC midnight (the
    epoch); 604800 (1W) starts on Monday 00:00 UTC. Known limit (§2.5): any other width that does
    not divide a day is epoch-aligned.
    """
    width = bar_seconds * MS_PER_S
    anchor = MONDAY_ANCHOR_S * MS_PER_S if bar_seconds == WEEK_SECONDS else 0
    return (ts_ms - anchor) // width * width + anchor


class LiquidationRow(Protocol):
    """One archived liquidation as the oracle reads it raw (`liquidation_check.StoredLiquidation`)."""

    @property
    def venue_event_id(self) -> str: ...

    @property
    def side(self) -> str: ...

    @property
    def size_units(self) -> int: ...

    @property
    def size_precision(self) -> int: ...

    @property
    def ts_event(self) -> int: ...


@dataclass(frozen=True)
class KnownLiquidations:
    """
    A feed instrument's archived liquidations for a fold, and the bound from which they are known:
    `known_from_ns`, the instrument's earliest archived liquidation, derived by the oracle from its
    own raw read (`liquidation_reader.LiquidationCatalog.first_ts_event`), lowered to the earliest
    row given. A bucket's liquidations are known only if the bucket starts at or after it; one
    before it, or straddling it, reads None at every width (unknown: the archive cannot show the
    feed covered all of it), never 0; a known bucket without a liquidation reads 0 (§2.15, Story
    33.3 review loop 2's one feed-start rule, restated here independently of production's).
    """

    rows: tuple[LiquidationRow, ...]
    known_from_ns: int

    def since_ns(self) -> int:
        """Return the feed start: the bound lowered to the earliest row (a row proves the feed ran)."""
        return min([self.known_from_ns, *(row.ts_event for row in self.rows)])

    def first_known_bucket(self, bar_seconds: int) -> int:
        """Return the start (ms) of the first bucket starting at or after `since_ns`."""
        since_ns = self.since_ns()
        since_ms = -(-since_ns // NS_PER_MS)  # the first whole millisecond at or after it
        start = bucket_start(since_ms, bar_seconds)
        return start if start == since_ms else start + bar_seconds * MS_PER_S


def unique_liquidations(rows: Sequence[LiquidationRow]) -> list[LiquidationRow]:
    """
    Keep one copy of each `venue_event_id` (an overlapping file holds the same event twice). Copies
    that disagree are reported -- `ValueError` naming every such id -- never resolved by keeping one:
    which copy is the venue's the oracle cannot tell, so the candle check of that day fails loudly.
    """
    kept: dict[str, LiquidationRow] = {}
    conflicts: set[str] = set()
    for row in rows:
        first = kept.setdefault(row.venue_event_id, row)
        if first is not row and first != row:
            conflicts.add(row.venue_event_id)
    if conflicts:
        raise ValueError(
            f"archived liquidation copies disagree for venue_event_id {sorted(conflicts)}"
        )
    return list(kept.values())


@dataclass(frozen=True)
class RefCandle:
    """
    One folded bucket: OHLC None when nothing traded, `volume` 0 then; every row counted. §2.15's
    columns follow, as integer units at the bucket's own `price_precision`/`size_precision`: the
    order flow of every row, and the liquidations (None for an instrument without the feed). They
    default to None so a hand-built candle without them still compares on the six fields.
    """

    t: int
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: Decimal
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


# §2.15's ten columns, in the served and stored order.
AGGREGATE_FIELDS = tuple(f.name for f in fields(RefCandle))[7:]


@_exact
def fold_candles(
    books: Sequence[RefBook],
    bar_seconds: int,
    liquidations: KnownLiquidations | None = None,
) -> dict[int, RefCandle]:
    """
    §2.5: per bucket, open of the first traded second, high/low across them, close of the last,
    volume summed over the traded seconds; `seconds_observed` counts every row. Rows in `ts_event`
    order (equal stamps keep their input order).

    §2.15: the bucket's order flow (buy/sell volume and counts of every row, `pv` the sum of each
    traded second's close times its volume) and, for an instrument with the feed (`liquidations`
    not None), the liquidations whose `ts_event` falls in it, each `venue_event_id` once (a
    liquidation may make a bucket of its own, with no row) -- None for a bucket starting before the
    feed start, or straddling it (`KnownLiquidations.first_known_bucket`), whose liquidations make
    no bucket of their own. Copies of one event that disagree raise (`unique_liquidations`). All
    in units at the bucket's finest precisions (a liquidation-only bucket's price precision is 0).
    """
    buckets: dict[int, list[RefBook]] = {}
    for book in sorted(books, key=lambda b: b.ts_event):
        buckets.setdefault(bucket_start(book.ts_event // 1_000_000, bar_seconds), []).append(book)
    forced: dict[int, list[LiquidationRow]] = {}
    known_from = None
    if liquidations is not None:
        known_from = liquidations.first_known_bucket(bar_seconds)
        for row in unique_liquidations(liquidations.rows):
            t = bucket_start(row.ts_event // 1_000_000, bar_seconds)
            if t >= known_from:
                forced.setdefault(t, []).append(row)
    return {
        t: _fold_bucket(
            t,
            buckets.get(t, []),
            None if known_from is None or t < known_from else forced.get(t, []),
        )
        for t in set(buckets) | set(forced)
    }


def _fold_bucket(
    t: int, rows: list[RefBook], liquidations: list[LiquidationRow] | None
) -> RefCandle:
    traded = [r for r in rows if r.close_price is not None]
    flow = _flow(rows, liquidations)
    if not traded:
        return RefCandle(t, None, None, None, None, Decimal(0), len(rows), **flow)
    return RefCandle(
        t=t,
        open=traded[0].open_price,
        high=max(r.high_price for r in traded if r.high_price is not None),
        low=min(r.low_price for r in traded if r.low_price is not None),
        close=traded[-1].close_price,
        volume=sum((r.buy_volume + r.sell_volume for r in traded), Decimal(0)),
        seconds_observed=len(rows),
        **flow,
    )


def _flow(rows: list[RefBook], liquidations: list[LiquidationRow] | None) -> dict[str, int | None]:
    """§2.15 for one bucket, from the decoded decimals, in units at the finest precisions."""
    price_p = max((r.price_precision for r in rows), default=0)
    size_p = max(
        [*(r.size_precision for r in rows), *(liq.size_precision for liq in liquidations or ())],
        default=0,
    )
    traded = [r for r in rows if r.close_price is not None]
    pv = sum((r.close_price * (r.buy_volume + r.sell_volume) for r in traded), Decimal(0))  # type: ignore[operator]
    flow: dict[str, int | None] = {
        "buy_v": units_exactly(sum((r.buy_volume for r in rows), Decimal(0)), size_p),
        "sell_v": units_exactly(sum((r.sell_volume for r in rows), Decimal(0)), size_p),
        "buy_n": sum(r.buy_count for r in rows),
        "sell_n": sum(r.sell_count for r in rows),
        "pv": units_exactly(pv, price_p + size_p),
        "price_precision": price_p,
        "size_precision": size_p,
    }
    flow.update(_forced(liquidations, size_p))
    return flow


def _forced(liquidations: list[LiquidationRow] | None, size_p: int) -> dict[str, int | None]:
    """Return the liquidated volume per side (a `long` one is a forced sell) and the count."""
    if liquidations is None:
        return {"liq_long_v": None, "liq_short_v": None, "liq_n": None}
    sides = {"long": Decimal(0), "short": Decimal(0)}
    for row in liquidations:
        if row.side not in sides:
            raise ValueError(f"{row.venue_event_id}: stored side {row.side!r} is not long/short")
        sides[row.side] += _value(row.size_units, row.size_precision)
    return {
        "liq_long_v": units_exactly(sides["long"], size_p),
        "liq_short_v": units_exactly(sides["short"], size_p),
        "liq_n": len(liquidations),
    }


def units_exactly(value: Decimal, precision: int) -> int:
    """Return a decimal as an integer count of `10^-precision`; a finer value is refused."""
    with localcontext(prec=DIGITS):
        scaled = value.scaleb(precision)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value} is not exact at precision {precision}")
    return int(scaled)


# --- §2.7: the Lines-mode CVD-weighted price -------------------------------------------------


@_exact
def cvd_weighted_price(book: RefBook) -> Decimal | None:
    """`mid + ((buy - sell) / (buy + sell)) * (ask - bid) / 2`; the mid when nothing traded."""
    centre = mid(book)
    if centre is None:
        return None
    traded = book.buy_volume + book.sell_volume
    if traded == 0:
        return centre
    lean = (book.buy_volume - book.sell_volume) / traded
    return centre + lean * (book.ask_prices[0] - book.bid_prices[0]) / 2


# --- §3.2/§3.3: price stats -----------------------------------------------------------------

PricePoint = tuple[int, Decimal]


@_exact
def pct_change(
    series: Sequence[PricePoint], horizon_ns: int, max_shortfall_ns: int
) -> Decimal | None:
    """
    §3.3: `(latest - base) / base * 100`, `base` the first point at or after `latest_ts - horizon`;
    None when the series does not reach back to that cutoff, or when `base` lies more than
    `max_shortfall_ns` past it (the horizon would be silently shortened by a gap).
    """
    if not series:
        return None
    stamps = [ts for ts, _ in series]
    cutoff = stamps[-1] - horizon_ns
    if stamps[0] > cutoff:
        return None
    base_ts, base = series[bisect_left(stamps, cutoff)]
    if base_ts - cutoff > max_shortfall_ns:
        return None
    return (series[-1][1] - base) / base * 100


def price_as_of(series: Sequence[PricePoint], target_ns: int, tolerance_ns: int) -> Decimal | None:
    """
    §3.3 (`pct_1w`/`pct_1m` base): the newest price at or before `target_ns` and strictly within
    `tolerance_ns` of it; None when there is none.
    """
    stamps = [ts for ts, _ in series]
    index = bisect_right(stamps, target_ns) - 1
    if index < 0 or stamps[index] <= target_ns - tolerance_ns:
        return None
    return series[index][1]


@_exact
def pct_change_from(current: Decimal | None, base: Decimal | None) -> Decimal | None:
    """Return `(current - base) / base * 100`; None when either is missing or the base is 0."""
    if current is None or base is None or base == 0:
        return None
    return (current - base) / base * 100


@_exact
def pct_returns(prices: Sequence[Decimal]) -> list[Decimal]:
    """Return the consecutive `(p[i] - p[i-1]) / p[i-1]`."""
    return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]


@_exact
def vol_score_1h(points: Sequence[PricePoint], lookback_ns: int) -> Decimal | None:
    """
    §3.2 `volatility_score`: sample standard deviation (ddof=1) of consecutive mid pct returns over
    the points with `ts >= latest_ts - lookback` (an age window, both ends kept); None under 2
    returns.
    """
    if not points:
        return None
    cutoff = points[-1][0] - lookback_ns
    returns = pct_returns([p for ts, p in points if ts >= cutoff])
    return statistics.stdev(returns) if len(returns) >= 2 else None


@_exact
def vol_catalog_24h(series: Sequence[PricePoint], window_ns: int) -> Decimal | None:
    """
    §3.3 `volatility`: population standard deviation (ddof=0) of consecutive trade-close pct
    returns over the points within `window_ns` of the latest one (24 h); None under 2 returns.
    """
    if not series:
        return None
    cutoff = series[-1][0] - window_ns
    returns = pct_returns([p for ts, p in series if ts >= cutoff])
    return statistics.pstdev(returns) if len(returns) >= 2 else None


@_exact
def vol_fast(mids: Sequence[Decimal], window: int) -> Decimal | None:
    """§3.2 `volatility_fast`: sample stdev (ddof=1) of the pct returns of the last `window` mids."""
    _require_window(window)
    returns = pct_returns(list(mids[-window:]))
    return statistics.stdev(returns) if len(returns) >= 2 else None


# --- §2.12: research reads ------------------------------------------------------------------

Return = tuple[int, Decimal | None]


@_exact
def simple_returns(
    prices: Sequence[Decimal | None], ts_ns: Sequence[int], period_ns: int
) -> list[Return]:
    """
    `p[i] / p[i-1] - 1` at each point after the first, stamped with its bucket start; None (a gap)
    unless the two points sit in adjacent buckets and both prices exist.
    """
    buckets = [ts // period_ns * period_ns for ts in ts_ns]
    out: list[Return] = []
    for i in range(1, len(prices)):
        a, b = prices[i - 1], prices[i]
        adjacent = buckets[i] - buckets[i - 1] == period_ns
        out.append(
            (buckets[i], b / a - 1 if adjacent and a is not None and b is not None else None)
        )
    return out


@_exact
def resample(returns: Sequence[Return], period_ns: int, new_period_ns: int) -> list[Return]:
    """
    Compound into `new_period_ns` buckets: `prod(1 + r) - 1`; None when the bucket does not hold
    all `new / old` points or holds a gap. A bucket without any point is absent.
    """
    if new_period_ns <= 0 or period_ns <= 0 or new_period_ns % period_ns:
        raise ValueError(f"{new_period_ns} ns is not a positive multiple of {period_ns} ns")
    groups: dict[int, list[Decimal | None]] = {}
    for ts, value in returns:
        groups.setdefault(ts // new_period_ns * new_period_ns, []).append(value)
    return [(t, _compound(values, new_period_ns // period_ns)) for t, values in groups.items()]


def _compound(values: list[Decimal | None], expected: int) -> Decimal | None:
    present = [v for v in values if v is not None]
    if len(present) != expected or len(values) != expected:
        return None
    growth = Decimal(1)
    for value in present:
        growth *= 1 + value
    return growth - 1


def _pairs(a: Sequence[float | None], b: Sequence[float | None]) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for x, y in zip(a, b, strict=True):
        if x is not None and y is not None and not math.isnan(x) and not math.isnan(y):
            xs.append(x)
            ys.append(y)
    return xs, ys


def pearson(a: Sequence[float | None], b: Sequence[float | None]) -> float | None:
    """
    Pairwise-complete Pearson correlation (only rows where both are present); None under 2 pairs
    or when either side is exactly constant.
    """
    x, y = _pairs(a, b)
    if len(x) < 2 or min(x) == max(x) or min(y) == max(y):
        return None
    return statistics.correlation(x, y)


def rolling_pearson(
    a: Sequence[float | None], b: Sequence[float | None], window: int, min_pairs: int
) -> list[float | None]:
    """
    Return `pearson` of each trailing `window` rows at the window's last row; None before it fills
    and where the window holds fewer than `min_pairs` complete pairs.
    """
    out: list[float | None] = [None] * len(a)
    for end in range(window, len(a) + 1):
        x, y = a[end - window : end], b[end - window : end]
        if len(_pairs(x, y)[0]) >= min_pairs:
            out[end - 1] = pearson(x, y)
    return out


def lead_lag(
    a: Sequence[float | None], b: Sequence[float | None], max_lag: int, min_pairs: int
) -> list[tuple[int, float | None]]:
    """`(lag, pearson(a[t], b[t + lag]))` for lag in -max..max: a positive lag means `a` leads `b`."""
    n = len(a)
    if len(b) != n or not 0 <= max_lag < n:
        raise ValueError(f"lead_lag needs aligned series and 0 <= max_lag < {n}, got {max_lag}")
    out: list[tuple[int, float | None]] = []
    for lag in range(-max_lag, max_lag + 1):
        x, y = (a[: n - lag], b[lag:]) if lag >= 0 else (a[-lag:], b[: n + lag])
        out.append((lag, pearson(x, y) if len(_pairs(x, y)[0]) >= min_pairs else None))
    return out


@_exact
def basis_bps(a: Decimal | None, b: Decimal | None) -> Decimal | None:
    """Return `(a / b - 1) * 1e4`: how far `a` sits above `b`, in bps of `b`; None on a gap."""
    if a is None or b is None:
        return None
    return (a / b - 1) * BPS


@_exact
def funding_per_hour(rate: Decimal, interval_minutes: Decimal | None) -> Decimal | None:
    """`rate * 60 / interval` (interval in minutes, per row); None without a positive interval."""
    if interval_minutes is None or interval_minutes <= 0:
        return None
    return rate * 60 / interval_minutes


@_exact
def spread_ticks(spread_value: Decimal | None, tick: Decimal | None) -> Decimal | None:
    """`spread / tick`; None without a tick size or a spread."""
    if spread_value is None or not tick:
        return None
    return spread_value / tick


@_exact
def spread_bps(spread_value: Decimal | None, mid_value: Decimal | None) -> Decimal | None:
    """`spread / mid * 1e4`; None on a gap."""
    if spread_value is None or mid_value is None:
        return None
    return spread_value / mid_value * BPS


@_exact
def run_cvd(deltas: Sequence[Decimal | None]) -> list[Decimal | None]:
    """Cumulative `volume_delta` per run of sampled seconds: None on a gap, restarting after it."""
    out: list[Decimal | None] = []
    running = Decimal(0)
    for delta in deltas:
        if delta is None:
            running = Decimal(0)
            out.append(None)
            continue
        running += delta
        out.append(running)
    return out
