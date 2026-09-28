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
"""`research.domain.events` (Story 27.7): forward returns after a hit and their hit rate."""

import math

import numpy as np
import pytest

from research.domain.events import EventHitRate
from research.domain.events import forward_returns
from research.domain.events import hit_rate


def test_the_matrix_row_nan_where_a_close_is_missing() -> None:
    returns = forward_returns(np.array([1.0, 2.0, np.nan, 4.0]), [0], (1, 2, 3))
    assert list(returns) == [1, 2, 3]
    assert returns[1].tolist() == [1.0]
    assert math.isnan(returns[2][0])
    assert returns[3].tolist() == [3.0]


def test_a_horizon_past_the_end_is_nan_and_the_order_of_hits_is_kept() -> None:
    returns = forward_returns(np.array([10.0, 11.0, 12.0, 9.0]), [3, 0, 2], (1,))
    assert math.isnan(returns[1][0])
    assert returns[1][1:].tolist() == pytest.approx([0.1, -0.25])


def test_no_hits_give_empty_arrays() -> None:
    returns = forward_returns(np.array([1.0, 2.0]), [], (1, 5))
    assert {h: r.tolist() for h, r in returns.items()} == {1: [], 5: []}


def test_numpy_integer_horizons_are_accepted_and_keyed_as_ints() -> None:
    returns = forward_returns(np.array([1.0, 2.0, 4.0]), [0], np.array([1, 2]))
    assert [type(h) for h in returns] == [int, int]
    assert {h: r.tolist() for h, r in returns.items()} == {1: [1.0], 2: [3.0]}


@pytest.mark.parametrize(
    ("closes", "hits", "horizons"),
    [
        ([1.0, 2.0], [2], (1,)),
        ([1.0, 2.0], [-1], (1,)),
        ([1.0, 2.0], [0], (0,)),
        ([1.0, 2.0], [0], (1.5,)),
        ([1.0, 2.0], [0], (True,)),
        ([1.0, 0.0], [0], (1,)),
        ([[1.0, 2.0]], [0], (1,)),
    ],
)
def test_an_invalid_index_horizon_or_close_raises(
    closes: list, hits: list[int], horizons: tuple
) -> None:
    with pytest.raises(ValueError):
        forward_returns(np.array(closes), hits, horizons)


def test_hit_rate_for_a_bearish_event_counts_falls_and_skips_nan() -> None:
    summary = hit_rate(np.array([-0.1, 0.2, np.nan, -0.3, 0.0]), -100)
    assert summary.n == 4
    assert summary.rate == pytest.approx(0.5)  # the flat return is not a hit
    assert summary.mean == pytest.approx(-0.05)


def test_hit_rate_for_a_bullish_event() -> None:
    assert hit_rate(np.array([0.01, 0.02, -0.01]), 1) == pytest.approx(
        EventHitRate(2 / 3, 0.02 / 3, 3)
    )


def test_hit_rate_with_no_finite_return_is_nan_with_zero_count() -> None:
    summary = hit_rate(np.array([np.nan, np.nan]), 100)
    assert summary.n == 0
    assert math.isnan(summary.rate)
    assert math.isnan(summary.mean)
    assert hit_rate(np.array([]), -1).n == 0


def test_hit_rate_needs_a_direction() -> None:
    with pytest.raises(ValueError):
        hit_rate(np.array([0.1]), 0)
