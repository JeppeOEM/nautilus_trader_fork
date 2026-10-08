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
"""Bars folded straight from second rows: the chart's forming candle and the archive-side read."""

from collections.abc import Iterable
from collections.abc import Sequence

from kernel.liquidation import Liquidation
from kernel.second_snapshot import SecondRow

from candles.domain.fold import fold_rows


def bars_from_rows(
    rows: Sequence[SecondRow],
    bar_seconds: int,
    liquidations: Iterable[Liquidation] | None = None,
    *,
    liquidations_since_ns: int | None = None,
) -> list[dict]:
    """
    Every traded bucket of `rows` at `bar_seconds`, oldest first, as `{t (ms), o, h, l, c, v}`
    followed by the ten Story 33.3 columns (`domain.fold.AGGREGATE_KEYS`: the order-flow sums, the
    liquidation sums -- None when `liquidations` is None, an instrument without the feed -- and the
    bar's precisions). `liquidations_since_ns` is the feed's start (None: every bucket known): a
    bucket starting before it, or straddling it, reads None `liq_*`, never 0
    (`domain.fold.LiquidationArrays.since_ns`). An archive-side caller resolves it with
    `domain.fold.archive_liquidations`; the live bus passes its archive-and-tail minimum.

    The same `fold_arrays` the store writes, at one arbitrary width -- so a bar read this way and
    the stored bar of the same seconds and liquidations are equal by construction, not by
    convention. Buckets with no trade are dropped (`o is None`): a bar only exists for a second that
    traded, which is also what `queries.window`'s `o IS NOT NULL` returns.
    """
    folded = fold_rows(
        rows,
        liquidations=liquidations,
        liquidations_since_ns=liquidations_since_ns,
        bars=(bar_seconds,),
    )
    return [
        {
            "t": t,
            "o": bucket.o,
            "h": bucket.h,
            "l": bucket.l,
            "c": bucket.c,
            "v": bucket.v,
            **bucket.aggregates(),
        }
        for (_bar, t), bucket in sorted(folded.items())
        if bucket.o is not None
    ]


def forming_bar(
    rows: Sequence[SecondRow],
    bar_seconds: int,
    liquidations: Iterable[Liquidation] | None = None,
    *,
    liquidations_since_ns: int | None = None,
) -> dict | None:
    """
    Return the currently-forming bar over `rows` and the bucket's `liquidations` (None: no feed),
    bounded by the feed's start `liquidations_since_ns` (`bars_from_rows`):
    `{t (ms), o, h, l, c, v, buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n,
    price_precision, size_precision}`, or None if untraded.

    `rows` is the live buffer of the bucket being formed, so its newest traded bucket *is* the
    forming bar. `bar_seconds` need not be one of `BAR_SECONDS` -- the chart offers widths the store
    does not keep (600 s, 1800 s, a week), and they fold identically.

    None is the no-trade case, a no-op rather than an error: a bucket in which nothing traded has no
    bar, exactly as the store has no row for it (a liquidation alone does not make a bar).

    Known limit: this returns a dict, not a `nautilus_trader.model.data.Bar`, because the shape is
    the frozen `/ws/live` payload (`{"channel": ..., "bar": {t,o,h,l,c,v, ...}}`, pinned by
    `frontend/src/hooks/useLiveCandle.ts` and by byte-for-byte route tests; Story 33.3 appended its
    ten keys after the six, AD-D12's added-keys-only rule) and a `Bar` would need a
    `BarType`/`InstrumentId` this path never constructs plus a serializer at the WS boundary.
    Upgrade path: return a `Bar` and serialize it once the views context owns the wire format.
    """
    bars = bars_from_rows(
        rows, bar_seconds, liquidations, liquidations_since_ns=liquidations_since_ns
    )
    return bars[-1] if bars else None
