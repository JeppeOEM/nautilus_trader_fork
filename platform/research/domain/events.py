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
Event studies (Story 27.7): what price did after an event -- a candlestick pattern hit -- at fixed
horizons, and how often it went the event's way. Numpy only.

NaN is a gap: a forward return is NaN where it cannot be measured (past the end of the series, or
either close NaN), never filled or clipped, and the summaries are taken over the finite returns
only. A caller whose series has holes (never-observed or untraded buckets) splits it into runs of
adjacent bars first (`research.application.patterns.forward_table` does), so a return that would
cross a hole runs past its run's end and is NaN.
"""

import numbers
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np


class EventHitRate(NamedTuple):
    """
    Invariant: `n` counts the finite returns; `rate` is the share of them strictly in the event's
    direction (a flat return is not a hit) and `mean` their plain mean -- both NaN exactly when
    `n` is 0, never a division warning.
    """

    rate: float
    mean: float
    n: int


def _check_forward_inputs(closes: np.ndarray, hits: np.ndarray, horizons: Sequence[int]) -> None:
    if closes.ndim != 1:
        raise ValueError(f"closes must be one-dimensional, not shape {closes.shape}")
    finite = closes[np.isfinite(closes)]
    if np.any(finite <= 0):
        raise ValueError("closes must be positive (NaN marks a missing close)")
    if hits.size and (hits.min() < 0 or hits.max() >= len(closes)):
        raise ValueError(f"hit indices must lie in [0, {len(closes)}), got {hits.tolist()}")
    bad = [
        h for h in horizons if isinstance(h, bool) or not isinstance(h, numbers.Integral) or h < 1
    ]
    if bad:
        raise ValueError(f"horizons must be ints >= 1, got {bad}")


def forward_returns(
    closes: np.ndarray, hit_indices: Sequence[int], horizons: Sequence[int]
) -> dict[int, np.ndarray]:
    """
    Return, per horizon `h`, the simple forward return `closes[i + h] / closes[i] - 1` of every hit
    index `i`, in `hit_indices`' order.

    Invariant: each array has one entry per hit; an entry is NaN where `i + h` is past the end or
    either close is NaN -- never filled from a neighbour. Raises `ValueError` for an index outside
    `closes`, a horizon that is not an int >= 1, a non-positive close or a non-1-D `closes`.
    """
    prices = np.asarray(closes, dtype=float)
    hits = np.asarray(hit_indices, dtype=np.int64).reshape(-1)
    _check_forward_inputs(prices, hits, horizons)
    returns: dict[int, np.ndarray] = {}
    for horizon in horizons:
        targets = hits + horizon
        inside = targets < len(prices)
        values = np.full(len(hits), np.nan)
        values[inside] = prices[targets[inside]] / prices[hits[inside]] - 1.0
        returns[int(horizon)] = values
    return returns


def hit_rate(returns: np.ndarray, direction: int) -> EventHitRate:
    """
    Summarise one set of forward returns for an event of `direction` (> 0 bullish, < 0 bearish;
    e.g. a pattern's +100/-100): the share of finite returns with that sign, their mean and count.
    `direction` 0 raises `ValueError` -- a non-directional event has no hit.
    """
    if direction == 0:
        raise ValueError("direction must be non-zero: +1/+100 bullish, -1/-100 bearish")
    values = np.asarray(returns, dtype=float).reshape(-1)
    finite = values[np.isfinite(values)]
    n = len(finite)
    if n == 0:
        return EventHitRate(float("nan"), float("nan"), 0)
    hits = int(np.count_nonzero(np.sign(finite) == np.sign(direction)))
    return EventHitRate(hits / n, float(finite.mean()), n)
