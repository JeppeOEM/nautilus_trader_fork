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
The seconds -> bars fold: the one aggregation every candle in `platform/` comes from.

Invariant (one fold): the live writer, the rebuild, the archive-side read and the chart's forming
bar all call `fold_arrays`, so a stored bar and a forming bar of the same seconds cannot disagree.
The only other fold in the platform is trades -> second, `kernel.fold.fold_trades`. The same holds
for a bucket folded in two parts (two flushes, or a liquidation landing before its seconds): the
store combines the parts with `merge_buckets`, the one merge rule.

Every second a row exists for counts toward `seconds_observed` (the `partial` flag's input), traded
or not, and a bucket entry exists for every bucket a row touched -- a no-trade bucket carries NULL
OHLC and `v = 0.0`. Reads return only buckets that traded (`o IS NOT NULL`). A liquidation may
create a bucket with no second at all (`seconds_observed = 0`); coverage readers count only
`seconds_observed > 0`, so a liquidation never makes a span look observed.

Per-bar order flow and liquidations (Story 33.3, `docs/DATA_DICTIONARY.md` §2.15): each bucket also
carries exact integer sums of the seconds' units -- `buy_v`/`sell_v` (`10^-size_precision`),
`buy_n`/`sell_n` (trades), `pv` (`sum of close_units x (buy + sell)` over the traded seconds, in
`10^-(price_precision + size_precision)`) -- and of the liquidations' sizes, `liq_long_v` (forced
sells), `liq_short_v` (forced buys) and `liq_n`. The bucket's `price_precision`/`size_precision`
are the finest (max) of its seconds and liquidations; a coarser part is rescaled by `10**k`, which
is exact. The fold and the merge sum in exact Python integers (int64 numpy only where a float
estimate proves no sum can wrap), so they never wrap, never round and never raise on magnitude: a
read-time fold (a `raw_1s` page, the forming bar, a non-stored width such as 1W or 45m) returns the
exact sum whatever its size, since it never reaches SQLite. Only the store write refuses a value
outside int64, the INTEGER column's range (`check_storable`, `CandleOverflowError`). The
liquidation group is None (unknown) when the caller passes no liquidation input (an instrument
without the feed, `kernel.liquidation.has_liquidation_feed`) and 0 for a bucket with the feed but
none; a part whose group is None makes the merged group None (a pre-migration row stays unknown).
Every path also bounds that 0 in time with one feed-start rule (`LiquidationArrays.since_ns`,
`first_bucket_at_or_after`): a bucket's liquidations are known only if it starts at or after the
feed's start; one before it, or straddling it, is None at every width. History from before the feed
existed is therefore never "no liquidation", and the live store, a rebuild of the same day and the
1m/1D bars of that day all agree bucket for bucket (Story 33.3 review loop 2, audit D-160).
A liquidation carries no price into the fold, so a liquidation-only bucket's `price_precision` is
0 (its `pv` is 0 at any precision) until a second merges in.

Known limit (Story 30.2): the snapshot is exact integers, but `o/h/l/c/v` fold its decoded floats
(`SecondRow`, `unit_float`), and those candle columns and the forming bar stay floats (REAL
columns): a bar is an aggregation read for display and indicators, so the "integers wherever a
machine moves them" rule covers the raw snapshot fields and, since Story 33.3, the order-flow and
liquidation columns, which are integers end to end; the float volume sum can drift in the last
place over a long bucket (the reconcile's `float_units` bar absorbs it; `buy_v + sell_v` is the
exact volume, `domain.candle.is_valid_candle` holds `v` to it). Upgrade path: integer OHLC columns
(units at the per-bar precisions already stored), folded from the units.

Known limit (untraded liquidations): a liquidation in a bucket with no trade is stored (the row
exists, `liq_*` counted) but not served, since a bar exists only for a traded bucket (`o IS NOT
NULL`); summing the served 1m bars' `liq_*` over an hour can therefore fall short of the 1h bar,
which holds every liquidation of the hour. Upgrade path: Story 33.4's `liquidation_bars` reads the
columns without the `o IS NOT NULL` filter (audit D-162).

Known limit (stored int64 ceiling): a stored width's sums must fit int64 (9.22e18). The one that
binds first is `pv`, price units x size units. Bybit BTCUSDT spot has size precision 6
(`basePrecision` 0.000001: `verification/tests/test_derivs.py`'s instrument item, and the recorded
wire sizes such as `0.002822` in `capture/venues/bybit/tests/fixtures/
bybit_trades_btcusdt_spot_20260921.json`) and price precision 1 to 2 (tick 0.1 in that item), so
~1e5 USDT is at most ~1e7 price units; a busy day trades ~2e4 BTC = ~2e10 size units, so the 1D
bar's `pv` is ~2e17, a factor ~45 under the ceiling; `buy_v`/`sell_v` (~2e10) and the counts are
far below. A non-stored width is not bound by it (1W `pv` ~1e18 is still exact). A breach
fails the store write loudly (`CandleOverflowError`, ledgered `collector.candle_store` live, a
failed rebuild otherwise), never a wrapped or REAL value. Upgrade path: a wider stored encoding of
`pv` (TEXT decimal or two INTEGER limbs) before any instrument's stored 1D `pv` nears 1e18.

Known limit (`pv`): the VWAP numerator weights each second's whole traded volume at that second's
close, the second-close VWAP, not every trade at its own price: inside a second that swept several
levels the close stands in for them all. Upgrade path: Story 32.8's raw trade reader
(`kernel.catalog_files.query_trade_columns`) summing `price x size` per trade into the bar.
"""

from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from dataclasses import fields
from types import MappingProxyType
from typing import NamedTuple

import numpy as np
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.second_snapshot import SecondRow


BAR_SECONDS = (60, 300, 900, 3600, 14400, 86400)
# Known limit: every width here must divide 86_400. `infrastructure.sqlite_store.rebuild` deletes one
# UTC day's rows and refolds that day's seconds alone, so a bucket straddling midnight would be
# rewritten from half its input and then *accumulate* through the merge (`merge_buckets`) on the
# next day's rebuild -- a permanently understated bar no read could detect. A width that does not divide a day
# (1 w, 45 m, 10 m -- `domain.candle.TIMEFRAMES` already offers some to charts, which fold on read
# and are unaffected) may not be stored until the rebuild's delete/refold window is widened to the
# bucket's own span. Upgrade path: range the DELETE on bucket boundaries rather than day boundaries.
# Older 1m/5m bars are pruned (the wide ones are tiny and kept); the Parquet archive keeps everything.
# Frozen: a retention table nothing may mutate at runtime (spine AD-D10).
RETAIN_DAYS: Mapping[int, int] = MappingProxyType({60: 30, 300: 90})

DAY_MS = 86_400_000
WEEK_SECONDS = 604_800
# 1970-01-01 (the epoch) was a Thursday; the first Monday 00:00 UTC is 4 days later. Weekly buckets
# start there -- the venues' weekly klines and the frontend's week both start on Monday.
MONDAY_ANCHOR_MS = 4 * DAY_MS

# `bucket = ts_ms // (bar * 1000)` is numpy int64 arithmetic, so a `bar` whose millisecond form
# does not fit int64 raises `OverflowError: Python int too large to convert to C long` from inside
# the fold. Callers take `bar_seconds` from client input (`/ws/live`'s subscribe channel), so the
# bound is checked here, where the limit actually lives, rather than trusted at each boundary.
MAX_BAR_SECONDS = (2**63 - 1) // 1000


def check_bars(bars: Sequence[int]) -> None:
    """Refuse a width the int64 bucket arithmetic cannot carry, before any array work."""
    for bar in bars:
        # `ts_ms // 0` is a numpy RuntimeWarning and one bogus bar at t=0, not an exception: the
        # old `aggregate_ohlc` raised ZeroDivisionError here, and a warning is a failure (TEST-04).
        if bar <= 0:
            raise ValueError(f"bar_seconds must be positive, got {bar}")
        if bar > MAX_BAR_SECONDS:
            raise ValueError(f"bar_seconds exceeds the int64 bucket limit: {bar}")


def bucket_start_ms[T: (int, np.ndarray)](ts_ms: T, bar_seconds: int) -> T:
    """
    Return the start (ms) of the `bar_seconds`-wide bucket each `ts_ms` falls in: the one bucket rule
    of every candle, forming bar and bar-spaced pane in `platform/` (Story 31.3). An int or an
    int64 array.

    A width that divides a day is aligned to UTC midnight (the epoch); 604800 s (1W) starts on
    Monday 00:00 UTC. Known limit: any other width that does not divide a day is epoch-aligned --
    none is offered today (`domain.candle.TIMEFRAMES` holds day divisors and 1W only); upgrade
    path: give such a width its own documented anchor here before offering it.
    """
    bar_ms = bar_seconds * 1000
    anchor = MONDAY_ANCHOR_MS if bar_seconds == WEEK_SECONDS else 0
    return (ts_ms - anchor) // bar_ms * bar_ms + anchor  # type: ignore[return-value]


INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)
# A float estimate of a sum's magnitude below this bound proves the int64 sum of the same terms
# cannot wrap: a float64 sum of n terms is off by at most ~n x 2^-53 relative, far under the factor
# of two left to 2^63. Above it the fold recomputes that width in Python integers, exactly, at any
# magnitude; whether the result fits the store is the store write's check (`check_storable`).
_SAFE_ESTIMATE = 2.0**62

FLOW_KEYS = ("buy_v", "sell_v", "buy_n", "sell_n", "pv")
LIQUIDATION_KEYS = ("liq_long_v", "liq_short_v", "liq_n")
PRECISION_KEYS = ("price_precision", "size_precision")
# The 10 keys Story 33.3 appends to every candle dict, `/ws/live` bar and `CandleItem`, in order.
AGGREGATE_KEYS = (*FLOW_KEYS, *LIQUIDATION_KEYS, *PRECISION_KEYS)


class CandleOverflowError(ArithmeticError):
    """
    An exact per-bar sum outside int64, the store's INTEGER: refused at the store write
    (`check_storable`), never wrapped or rounded. The live sink and the rebuild fail loudly on it
    (capture ledgers `collector.candle_store`). The fold and the merge never raise it: a read-time
    width keeps its exact Python-int sum.
    """


class FlowArrays(NamedTuple):
    """
    The seconds' integer order-flow inputs, parallel to the fold's `ts_ms` (int64 arrays):
    `close_units` (read only where the second traded), the two volumes in `10^-size_precision`,
    the trade counts, and each row's precisions.
    """

    close_units: np.ndarray
    buy_units: np.ndarray
    sell_units: np.ndarray
    buy_n: np.ndarray
    sell_n: np.ndarray
    price_precision: np.ndarray
    size_precision: np.ndarray

    @classmethod
    def empty(cls) -> "FlowArrays":
        """No seconds: a liquidation-only fold."""
        return cls(*(np.empty(0, dtype=np.int64) for _ in cls._fields))

    @classmethod
    def of(cls, rows: Sequence[SecondRow]) -> "FlowArrays":
        """Read the integer fields of `rows` (`SecondRow`'s units), never their floats."""
        n = len(rows)

        def column(values: Iterable[int]) -> np.ndarray:
            return np.fromiter(values, dtype=np.int64, count=n)

        return cls(
            column(0 if r.close_price_units is None else r.close_price_units for r in rows),
            column(r.buy_volume_units for r in rows),
            column(r.sell_volume_units for r in rows),
            column(r.buy_count for r in rows),
            column(r.sell_count for r in rows),
            column(r.price_precision for r in rows),
            column(r.size_precision for r in rows),
        )

    def take(self, index: np.ndarray) -> "FlowArrays":
        return FlowArrays(*(column[index] for column in self))


class LiquidationArrays(NamedTuple):
    """
    Liquidations as the fold reads them: `ts_ms` (int64, from `ts_event`), `long` (bool: the
    liquidated side was `LONG`, a forced sell), `size_units` and `size_precision` (int64),
    `venue_event_id` (the dedup key, kept for the callers that dedup; the fold does not) and
    `ts_ns` (int64, the exact `ts_event`, for the feed-start bound).

    Passing `None` instead means "no liquidation feed": the `liq_*` columns are None. An empty
    `LiquidationArrays` means "the feed, and nothing in these buckets": they are 0.

    `since_ns` bounds that 0 in time: the feed's start (`known_from`, the one feed-start rule). A
    bucket's liquidations are known only if the bucket **starts at or after** it; a bucket before
    it, or straddling it, is None at every width (Story 33.3 review loop 2, audit D-160), so no
    width claims a count for a span part of which no feed covered, and the 1m bars and the 1D bar
    of one day agree. Each path supplies its own bound -- the store's persisted start and the
    archive's first liquidation (`kernel.catalog_files.liquidation_feed_since_ns`) for the rebuild;
    the store's persisted start, else the archive's, for the `raw_1s` page and the technicals
    fallback; the store's persisted `liquidation_feed_since` for the live sink; the archive's and
    the live tail's minimum for the live bus -- and the fold lowers it to its own
    earliest row, so a row older than the given bound moves the bound rather than raising.
    None means every bucket folded is known (a fold whose feed is proven running throughout, e.g.
    a unit test); a path that knows no start at all passes no `LiquidationArrays` (None) instead.
    """

    ts_ms: np.ndarray
    long: np.ndarray
    size_units: np.ndarray
    size_precision: np.ndarray
    venue_event_id: np.ndarray
    ts_ns: np.ndarray
    since_ns: int | None = None

    @classmethod
    def empty(cls, since_ns: int | None = None) -> "LiquidationArrays":
        return cls.of((), since_ns)

    @classmethod
    def of(cls, rows: Iterable[Liquidation], since_ns: int | None = None) -> "LiquidationArrays":
        """Project `Liquidation` rows (exact integer sizes; never a float)."""
        rows = list(rows)
        n = len(rows)
        ts_ns = np.fromiter((r.ts_event for r in rows), dtype=np.int64, count=n)
        return cls(
            ts_ns // 1_000_000,
            np.fromiter((r.side is LiquidatedSide.LONG for r in rows), dtype=bool, count=n),
            np.fromiter((r.size_units for r in rows), dtype=np.int64, count=n),
            np.fromiter((r.size_precision for r in rows), dtype=np.int64, count=n),
            np.array([r.venue_event_id for r in rows], dtype=object),
            ts_ns,
            since_ns,
        )

    def effective_since_ns(self) -> int | None:
        """Return the bound lowered to the earliest row (a row proves the feed ran then)."""
        if self.since_ns is None or not len(self.ts_ns):
            return self.since_ns
        return min(self.since_ns, int(self.ts_ns.min()))

    def known_from(self, bar: int) -> int | None:
        """
        Return the first bucket start (ms) at `bar` whose liquidations are known -- the first that
        starts at or after `effective_since_ns` -- or None when every bucket's are. Never raises.
        """
        since_ns = self.effective_since_ns()
        return None if since_ns is None else first_bucket_at_or_after(since_ns, bar)


def first_bucket_at_or_after(since_ns: int, bar_seconds: int) -> int:
    """
    Return the start (ms) of the first `bar_seconds` bucket whose start is at or after `since_ns`:
    the bucket holding `since_ns` when it starts exactly there, else the next one. The feed-start
    rule's one boundary (`LiquidationArrays.known_from`), so a straddling bucket is never known.
    """
    since_ms = -(-since_ns // 1_000_000)  # ceil: a start in ms at or after `since_ns`
    start = int(bucket_start_ms(since_ms, bar_seconds))
    return start if start == since_ms else start + bar_seconds * 1000


def feed_since_ns(bound_ns: int | None, liquidations: Iterable[Liquidation]) -> int | None:
    """
    Return the earliest of `bound_ns` (a path's own feed-start record; None: none) and the rows'
    `ts_event`: the feed start every path derives the same way. None when neither knows one.
    """
    starts = [row.ts_event for row in liquidations]
    if bound_ns is not None:
        starts.append(bound_ns)
    return min(starts) if starts else None


def archive_liquidations(
    liquidations: Sequence[Liquidation] | None, archive_since_ns: int | None
) -> tuple[Sequence[Liquidation] | None, int | None]:
    """
    Resolve an archive-side fold's liquidation input: `(rows, since_ns)` for `fold_rows`. A no-feed
    instrument (`liquidations` None) stays None; a feed one whose start is known nowhere (nothing
    archived, no row given) becomes None too -- every bucket null, never 0 -- and otherwise the
    bound is `feed_since_ns(archive_since_ns, rows)`, so a live-tail row older than the archive's
    first liquidation moves the bound instead of contradicting it.
    """
    if liquidations is None:
        return None, None
    since_ns = feed_since_ns(archive_since_ns, liquidations)
    return (None, None) if since_ns is None else (liquidations, since_ns)


@dataclass(slots=True)
class FoldedBucket:
    """
    One folded bucket: the six float/count fields every candle had (`o/h/l/c` None when nothing
    traded, `v`, `seconds_observed`), then Story 33.3's ten integer columns (module docstring).
    A None flow or liquidation group is unknown, never 0; the precisions are None only when both
    groups are.
    """

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

    def aggregates(self) -> dict[str, int | None]:
        """Return the ten integer columns as `{key: value}`, in `AGGREGATE_KEYS` order."""
        return {key: getattr(self, key) for key in AGGREGATE_KEYS}

    def has_flow(self) -> bool:
        return self.buy_v is not None

    def has_liquidations(self) -> bool:
        return self.liq_n is not None


BUCKET_FIELDS = tuple(f.name for f in fields(FoldedBucket))


def fold_rows(
    rows: Iterable[SecondRow],
    *,
    liquidations: Iterable[Liquidation] | None = None,
    liquidations_since_ns: int | None = None,
    bars: Sequence[int] = BAR_SECONDS,
) -> dict[tuple[int, int], FoldedBucket]:
    """
    (bar_seconds, bucket_start_ms) -> `FoldedBucket`, for rows that carry the `SecondRow` fields
    (a `DydxSecondSnapshot` or a `SecondOHLC`) and, when the instrument has a liquidation feed, its
    `Liquidation` rows (None: no feed, the `liq_*` columns are None). Any order.
    `liquidations_since_ns` is the feed start, `LiquidationArrays.since_ns` (None: every bucket
    known; an archive-side caller resolves it with `archive_liquidations`).

    This folds seconds into bars; trades into seconds is `kernel.fold.fold_trades`.
    """
    rows = list(rows)
    liquidation_arrays = None
    if liquidations is not None:
        liquidation_arrays = LiquidationArrays.of(liquidations, liquidations_since_ns)
    if not rows and (liquidation_arrays is None or not len(liquidation_arrays.ts_ms)):
        # Checked before the short-circuit: a width out of range must be refused for a quiet
        # instrument exactly as for a busy one, or a bad `/ws/live` channel looks accepted until
        # the first trade arrives.
        check_bars(bars)
        return {}
    nan = float("nan")

    def floats(values: Iterable[float]) -> np.ndarray:
        return np.fromiter(values, dtype=np.float64, count=len(rows))

    return fold_arrays(
        np.fromiter((r.ts_event // 1_000_000 for r in rows), dtype=np.int64, count=len(rows)),
        floats(nan if r.open_price is None else r.open_price for r in rows),
        floats(nan if r.high_price is None else r.high_price for r in rows),
        floats(nan if r.low_price is None else r.low_price for r in rows),
        floats(nan if r.close_price is None else r.close_price for r in rows),
        floats(r.buy_volume + r.sell_volume for r in rows),
        flow=FlowArrays.of(rows),
        liquidations=liquidation_arrays,
        bars=bars,
    )


def fold_arrays(
    ts_ms: np.ndarray,
    o: np.ndarray,
    h: np.ndarray,
    low: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
    *,
    flow: FlowArrays,
    liquidations: LiquidationArrays | None = None,
    bars: Sequence[int] = BAR_SECONDS,
) -> dict[tuple[int, int], FoldedBucket]:
    """
    Vectorised `fold_rows` over column arrays (NaN = no trade that second). One aggregation for the
    live feed, the rebuild and the forming bar alike.

    `bars` defaults to the store's six widths; the forming bar and the archive-side read pass the
    single (possibly non-stored, e.g. 600 s) width they were asked for, so no caller ever needs a
    second aggregation for a width the store does not keep. `flow` is parallel to `ts_ms`;
    `liquidations` None means no feed (module docstring). Never raises on a sum's magnitude: the
    sums are exact Python ints, and only a store write refuses one outside int64.
    """
    check_bars(bars)
    order = np.argsort(ts_ms, kind="stable")
    ts_ms, o, h, low, c, v = (a[order] for a in (ts_ms, o, h, low, c, v))
    flow = flow.take(order)
    traded = ~np.isnan(c)
    acc: dict[tuple[int, int], FoldedBucket] = {}
    for bar in bars:
        bucket = bucket_start_ms(ts_ms, bar)
        known_from = None if liquidations is None else liquidations.known_from(bar)
        _fold_seconds(acc, bar, bucket, flow, traded, _LiquidationsKnown(liquidations, known_from))
        if traded.any():
            _fold_ohlcv(
                acc, bar, bucket[traded], o[traded], h[traded], low[traded], c[traded], v[traded]
            )
        if liquidations is not None:
            _fold_liquidations(acc, bar, liquidations, known_from)
    return acc


class _LiquidationsKnown(NamedTuple):
    """Whether a bucket's liquidations are known: a feed, from bucket start `since` (None: all)."""

    feed: LiquidationArrays | None
    since: int | None

    def zero(self, t: int) -> int | None:
        """0 for a known bucket (the feed, and none yet), None for an unknown one."""
        if self.feed is None or (self.since is not None and t < self.since):
            return None
        return 0


def _fold_seconds(
    acc: dict[tuple[int, int], FoldedBucket],
    bar: int,
    bucket: np.ndarray,
    flow: FlowArrays,
    traded: np.ndarray,
    known: _LiquidationsKnown,
) -> None:
    """Create every touched bucket with its observed count and exact order-flow sums."""
    if not len(bucket):
        return
    keys, starts, counts = np.unique(bucket, return_index=True, return_counts=True)
    sums = _flow_sums(flow, traded, starts, counts)
    for i, (k, n) in enumerate(zip(keys.tolist(), counts.tolist(), strict=True)):
        buy_v, sell_v, buy_n, sell_n, pv, price_p, size_p = (column[i] for column in sums)
        liquidation_zero = known.zero(k)
        acc[(bar, k)] = FoldedBucket(
            None, None, None, None, 0.0, n,
            buy_v, sell_v, buy_n, sell_n, pv,
            liquidation_zero, liquidation_zero, liquidation_zero,
            price_p, size_p,
        )  # fmt: skip


def _fold_ohlcv(
    acc: dict[tuple[int, int], FoldedBucket],
    bar: int,
    tb: np.ndarray,
    to: np.ndarray,
    th: np.ndarray,
    tl: np.ndarray,
    tc: np.ndarray,
    tv: np.ndarray,
) -> None:
    """Fold the float columns over the traded seconds, exactly as before Story 33.3."""
    t_starts = np.unique(tb, return_index=True)[1]
    t_ends = np.append(t_starts[1:], len(tb)) - 1
    for a_, z in zip(t_starts.tolist(), t_ends.tolist(), strict=True):
        entry = acc[(bar, int(tb[a_]))]
        entry.o = float(to[a_])
        entry.h = float(th[a_ : z + 1].max())
        entry.l = float(tl[a_ : z + 1].min())
        entry.c = float(tc[z])
        entry.v = float(tv[a_ : z + 1].sum())


def _flow_sums(
    flow: FlowArrays, traded: np.ndarray, starts: np.ndarray, counts: np.ndarray
) -> list[list[int]]:
    """
    Per bucket (rows sorted, each bucket contiguous from `starts`): `buy_v, sell_v, buy_n, sell_n,
    pv` at the bucket's finest precisions, then those precisions -- as Python ints. Each row is
    rescaled by `10**k` to its bucket's precision; int64 numpy when a float estimate proves no sum
    can wrap, else `_exact_flow_sums`.
    """
    price_p = np.maximum.reduceat(flow.price_precision, starts)
    size_p = np.maximum.reduceat(flow.size_precision, starts)
    row_bucket = np.repeat(np.arange(len(starts)), counts)
    k_size = size_p[row_bucket] - flow.size_precision
    k_pv = k_size + price_p[row_bucket] - flow.price_precision
    close = np.where(traded, flow.close_units, 0)
    terms = (
        (flow.buy_units, k_size),
        (flow.sell_units, k_size),
        (flow.buy_n, np.zeros_like(k_size)),
        (flow.sell_n, np.zeros_like(k_size)),
    )
    estimates = [np.abs(x.astype(np.float64)) * np.power(10.0, k) for x, k in terms]
    volume_estimate = np.abs(flow.buy_units.astype(np.float64)) + np.abs(flow.sell_units)
    estimates.append(np.abs(close.astype(np.float64)) * volume_estimate * np.power(10.0, k_pv))
    if any(np.add.reduceat(e, starts).max() >= _SAFE_ESTIMATE for e in estimates):
        return _exact_flow_sums(flow, traded, starts, counts, (price_p, size_p))
    scaled = [x * np.power(10, k).astype(np.int64) for x, k in terms]
    volume = flow.buy_units + flow.sell_units
    scaled.append(close * volume * np.power(10, k_pv).astype(np.int64))
    sums = [np.add.reduceat(x, starts).tolist() for x in scaled]
    return [*sums, price_p.tolist(), size_p.tolist()]


def _exact_flow_sums(
    flow: FlowArrays,
    traded: np.ndarray,
    starts: np.ndarray,
    counts: np.ndarray,
    precisions: tuple[np.ndarray, np.ndarray],
) -> list[list[int]]:
    """`_flow_sums` in Python integers, for a width whose sums may leave int64: exact, unbounded."""
    rows = [column.tolist() for column in flow]
    close, buy, sell, buy_n, sell_n, row_price_p, row_size_p = rows
    is_traded = traded.tolist()
    out: list[list[int]] = [[] for _ in range(7)]
    for b, (a, n) in enumerate(zip(starts.tolist(), counts.tolist(), strict=True)):
        price_p, size_p = int(precisions[0][b]), int(precisions[1][b])
        sums = [0, 0, 0, 0, 0]
        for i in range(a, a + n):
            size_scale = 10 ** (size_p - row_size_p[i])
            sums[0] += buy[i] * size_scale
            sums[1] += sell[i] * size_scale
            sums[2] += buy_n[i]
            sums[3] += sell_n[i]
            if is_traded[i]:
                price_scale = 10 ** (price_p - row_price_p[i])
                sums[4] += close[i] * (buy[i] + sell[i]) * size_scale * price_scale
        for column, value in zip(out, [*sums, price_p, size_p], strict=True):
            column.append(value)
    return out


def check_storable(bucket: FoldedBucket) -> None:
    """
    Refuse (`CandleOverflowError`) a bucket whose integer column falls outside int64, the store's
    INTEGER range: called by the store write before binding, never by the fold or the merge (a
    read-time fold's exact sum never reaches SQLite, module docstring).
    """
    for key, value in bucket.aggregates().items():
        if value is not None and not INT64_MIN <= value <= INT64_MAX:
            raise CandleOverflowError(f"{key} = {value} is outside int64: refused, never wrapped")


def _fold_liquidations(
    acc: dict[tuple[int, int], FoldedBucket],
    bar: int,
    liquidations: LiquidationArrays,
    known_from: int | None,
) -> None:
    """
    Add each liquidation to its bucket, creating a liquidation-only bucket where none exists. A
    liquidation in a bucket before `known_from` (one straddling the feed start) is not counted:
    that bucket's `liq_*` stay None, never a partial count.
    """
    columns = zip(
        bucket_start_ms(liquidations.ts_ms, bar).tolist(),
        liquidations.long.tolist(),
        liquidations.size_units.tolist(),
        liquidations.size_precision.tolist(),
        strict=True,
    )
    for t, is_long, size, size_p in columns:
        if known_from is not None and t < known_from:
            continue
        entry = acc.get((bar, t))
        if entry is None:
            entry = acc[(bar, t)] = FoldedBucket(
                None, None, None, None, 0.0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, size_p
            )
        _rescale(entry, entry.price_precision or 0, max(entry.size_precision or 0, size_p))
        scaled = size * 10 ** (entry.size_precision - size_p)
        if is_long:
            entry.liq_long_v += scaled
        else:
            entry.liq_short_v += scaled
        entry.liq_n = (entry.liq_n or 0) + 1


def _rescale(bucket: FoldedBucket, price_p: int, size_p: int) -> None:
    """Rescale a bucket's non-null groups in place to finer (never coarser) precisions."""
    if bucket.price_precision is None or bucket.size_precision is None:
        return  # both groups unknown: nothing to rescale
    k_size, k_price = size_p - bucket.size_precision, price_p - bucket.price_precision
    if k_size < 0 or k_price < 0:
        raise ValueError(f"rescale to coarser precisions ({price_p}, {size_p}) refused")
    for key, value in _scaled_values(bucket, k_price, k_size).items():
        setattr(bucket, key, value)
    bucket.price_precision, bucket.size_precision = price_p, size_p


def _scaled_values(bucket: FoldedBucket, k_price: int, k_size: int) -> dict[str, int | None]:
    """Return a bucket's integer columns times `10**k` for their units, in key order."""
    scale = {
        "buy_v": k_size,
        "sell_v": k_size,
        "pv": k_price + k_size,
        "liq_long_v": k_size,
        "liq_short_v": k_size,
    }
    values = {key: getattr(bucket, key) for key in (*FLOW_KEYS, *LIQUIDATION_KEYS)}
    return {
        key: None if value is None else value * 10 ** scale.get(key, 0)
        for key, value in values.items()
    }


def merge_buckets(old: FoldedBucket, new: FoldedBucket) -> FoldedBucket:
    """
    Combine two parts of one bucket (a stored row and a newly folded fragment): the store's one
    merge rule, pure.

    `o` keeps the older part's when set, `c` takes the newer part's when set, `h`/`l` are the NULL-
    safe max/min (a part with no trade never erases or poisons them), and `v`/`seconds_observed`
    add -- byte for byte the SQL `_UPSERT` this replaced (Story 33.3). The integer groups rescale to
    the finer precisions of the two and add exactly; a group None on either side is None in the
    result (unknown stays unknown). Known limit: so a liquidation merged into a stored part whose
    liquidation group is null (pre-migration, or before the feed start) is counted in no bar of
    that width until the day's rebuild recounts it from the archive, while the store has already
    claimed its id (`sqlite_store.apply_liquidations`' Known limit). Exact Python ints at any magnitude: the store write, not the
    merge, refuses a result outside int64 (`check_storable`).
    """
    merged = FoldedBucket(
        o=old.o if old.o is not None else new.o,
        h=_null_safe(max, old.h, new.h),
        l=_null_safe(min, old.l, new.l),
        c=new.c if new.c is not None else old.c,
        v=old.v + new.v,
        seconds_observed=old.seconds_observed + new.seconds_observed,
    )
    price_p = max(old.price_precision or 0, new.price_precision or 0)
    size_p = max(old.size_precision or 0, new.size_precision or 0)
    a = _scaled_to(old, price_p, size_p)
    b = _scaled_to(new, price_p, size_p)
    for keys in (FLOW_KEYS, LIQUIDATION_KEYS):
        if all(a[k] is not None and b[k] is not None for k in keys):
            for key in keys:
                setattr(merged, key, a[key] + b[key])  # type: ignore[operator]
    if merged.has_flow() or merged.has_liquidations():
        merged.price_precision, merged.size_precision = price_p, size_p
    return merged


def _scaled_to(bucket: FoldedBucket, price_p: int, size_p: int) -> dict[str, int | None]:
    if bucket.price_precision is None or bucket.size_precision is None:
        return dict.fromkeys((*FLOW_KEYS, *LIQUIDATION_KEYS))
    k_price, k_size = price_p - bucket.price_precision, size_p - bucket.size_precision
    return _scaled_values(bucket, k_price, k_size)


def _null_safe(pick: object, a: float | None, b: float | None) -> float | None:
    if a is None:
        return b
    if b is None:
        return a
    return pick(a, b)  # type: ignore[operator]
