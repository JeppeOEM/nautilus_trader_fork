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
The only other fold in the platform is trades -> second, `kernel.fold.fold_trades`.

Every second a row exists for counts toward `seconds_observed` (the `partial` flag's input), traded
or not, and a bucket entry exists for every bucket a row touched -- a no-trade bucket carries NULL
OHLC and `v = 0.0`. Reads return only buckets that traded (`o IS NOT NULL`).
"""

from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from types import MappingProxyType

import numpy as np
from kernel.second_snapshot import SecondRow


BAR_SECONDS = (60, 300, 900, 3600, 14400, 86400)
# Known limit: every width here must divide 86_400. `infrastructure.sqlite_store.rebuild` deletes one
# UTC day's rows and refolds that day's seconds alone, so a bucket straddling midnight would be
# rewritten from half its input and then *accumulate* through the `_UPSERT` on the next day's
# rebuild -- a permanently understated bar no read could detect. A width that does not divide a day
# (1 w, 45 m, 10 m -- `domain.candle.TIMEFRAMES` already offers some to charts, which fold on read
# and are unaffected) may not be stored until the rebuild's delete/refold window is widened to the
# bucket's own span. Upgrade path: range the DELETE on bucket boundaries rather than day boundaries.
# Older 1m/5m bars are pruned (the wide ones are tiny and kept); the Parquet archive keeps everything.
# Frozen: a retention table nothing may mutate at runtime (spine AD-D10).
RETAIN_DAYS: Mapping[int, int] = MappingProxyType({60: 30, 300: 90})

DAY_MS = 86_400_000

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


def fold_rows(
    rows: Iterable[SecondRow], *, bars: Sequence[int] = BAR_SECONDS
) -> dict[tuple[int, int], list]:
    """
    (bar_seconds, bucket_start_ms) -> [o, h, l, c, volume, seconds_observed], for rows that carry
    ts_event (ns), open/high/low/close_price (None = no trade that second) and buy_volume/
    sell_volume: a `DydxSecondSnapshot` or a `SecondOHLC`. Any order.

    This folds seconds into bars; trades into seconds is `kernel.fold.fold_trades`.
    """
    rows = list(rows)
    if not rows:
        # Checked before the short-circuit: a width out of range must be refused for a quiet
        # instrument exactly as for a busy one, or a bad `/ws/live` channel looks accepted until
        # the first trade arrives.
        check_bars(bars)
        return {}
    nan = float("nan")
    return fold_arrays(
        np.fromiter((r.ts_event // 1_000_000 for r in rows), dtype=np.int64, count=len(rows)),
        np.fromiter(
            (nan if r.open_price is None else r.open_price for r in rows),
            dtype=np.float64,
            count=len(rows),
        ),
        np.fromiter(
            (nan if r.high_price is None else r.high_price for r in rows),
            dtype=np.float64,
            count=len(rows),
        ),
        np.fromiter(
            (nan if r.low_price is None else r.low_price for r in rows),
            dtype=np.float64,
            count=len(rows),
        ),
        np.fromiter(
            (nan if r.close_price is None else r.close_price for r in rows),
            dtype=np.float64,
            count=len(rows),
        ),
        np.fromiter(
            (r.buy_volume + r.sell_volume for r in rows), dtype=np.float64, count=len(rows)
        ),
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
    bars: Sequence[int] = BAR_SECONDS,
) -> dict[tuple[int, int], list]:
    """
    Vectorised `fold_rows` over column arrays (NaN = no trade that second). One aggregation for the
    live feed, the rebuild and the forming bar alike.

    `bars` defaults to the store's six widths; the forming bar and the archive-side read pass the
    single (possibly non-stored, e.g. 600 s) width they were asked for, so no caller ever needs a
    second aggregation for a width the store does not keep.
    """
    check_bars(bars)
    order = np.argsort(ts_ms, kind="stable")
    ts_ms, o, h, low, c, v = (a[order] for a in (ts_ms, o, h, low, c, v))
    traded = ~np.isnan(c)
    acc: dict[tuple[int, int], list] = {}
    for bar in bars:
        bar_ms = bar * 1000
        bucket = ts_ms // bar_ms * bar_ms
        keys, counts = np.unique(bucket, return_counts=True)
        for k, n in zip(keys.tolist(), counts.tolist(), strict=True):
            acc[(bar, k)] = [None, None, None, None, 0.0, n]
        if not traded.any():
            continue
        tb, to, th, tl, tc, tv = (a[traded] for a in (bucket, o, h, low, c, v))
        t_starts = np.unique(tb, return_index=True)[1]
        t_ends = np.append(t_starts[1:], len(tb)) - 1
        for a_, z in zip(t_starts.tolist(), t_ends.tolist(), strict=True):
            entry = acc[(bar, int(tb[a_]))]
            entry[0] = float(to[a_])
            entry[1] = float(th[a_ : z + 1].max())
            entry[2] = float(tl[a_ : z + 1].min())
            entry[3] = float(tc[z])
            entry[4] = float(tv[a_ : z + 1].sum())
    return acc
