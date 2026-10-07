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
The engine's windows (Story 33.8 review): the prefix-sum window answers every query exactly as
summing its retained entries one by one would, through out-of-order inserts, a finer precision,
evictions and compaction; the as-of series finds a window's base by bisect as the linear scan did.
"""

import random
from decimal import Decimal
from fractions import Fraction

from alerting.application.windows import AsOfSeries
from alerting.application.windows import SumWindow


def _exact(amount: tuple[int, int]) -> Fraction:
    units, precision = amount
    return Fraction(units, 10**precision)


def _naive(entries: list[tuple[int, int, int]], start: int, end: int) -> Fraction:
    return sum(
        (Fraction(units, 10**p) for ts, units, p in entries if start < ts <= end), Fraction(0)
    )


def test_the_running_sum_equals_a_recomputed_sum_after_evictions() -> None:
    rng = random.Random(33_8)  # noqa: S311 -- a deterministic generator, not cryptography
    window = SumWindow(1)
    retained: list[tuple[int, int, int]] = []
    newest = 0
    for step in range(3000):
        # Mostly in order, sometimes late (a lagging row), with a finer precision appearing midway.
        ts = (
            newest + rng.randint(1, 5)
            if rng.random() > 0.1
            else max(0, newest - rng.randint(0, 20))
        )
        units, precision = rng.randint(0, 10_000), 3 if step < 1500 else rng.choice((3, 5))
        window.insert(ts, [(units, precision)], key=f"k{step}")
        retained.append((ts, units, precision))
        newest = max(newest, ts)
        horizon = newest - 200
        window.trim(horizon)
        retained = [e for e in retained if e[0] > horizon]
        for _ in range(3):
            start = newest - rng.randint(0, 250)
            end = newest - rng.randint(0, 30)
            assert _exact(window.total(start, end)[0]) == _naive(retained, start, end)
    assert len(window) == len(retained)
    assert window._head < 64 or window._head * 2 < len(window._ts)  # compacted as it went


def test_a_key_is_counted_once_while_retained_and_forgotten_when_evicted() -> None:
    window = SumWindow(2)
    assert window.insert(10, [(5, 0), (1, 1)], key="a") is True
    assert window.insert(10, [(5, 0), (1, 1)], key="a") is False
    assert window.total(0, 10) == [(5, 0), (1, 1)]
    window.trim(10)
    assert "a" not in window
    assert window.insert(11, [(5, 0), (1, 1)], key="a") is True


def test_the_as_of_series_finds_the_newest_value_at_or_before_a_time() -> None:
    series = AsOfSeries()
    points = [(t * 30, Decimal(100 + t)) for t in range(200)]
    for t, value in points:
        assert series.append(t, value) is True
    assert series.append(points[-1][0], Decimal(1)) is False  # a repeat: ignored
    for probe in (-1, 0, 29, 30, 3001, 10_000):
        expected = next((v for t, v in reversed(points) if t <= probe), None)
        assert series.at_or_before(probe) == expected
    series.trim(3000)
    assert next(iter(series)) == (3000, Decimal(200))  # the base at the horizon survives
    assert series.at_or_before(2999) is None
