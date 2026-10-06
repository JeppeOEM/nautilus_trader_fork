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
"""`basis_bps` and `funding_annualised` (Story 33.4) against hand-computed values and None cases."""

from decimal import Decimal

import pytest

from kernel.indicators import basis_bps
from kernel.indicators import funding_annualised


def test_basis_of_mark_over_index_is_exact_basis_points() -> None:
    # (100.5 - 100.0) / 100.0 * 10 000 = 50
    assert basis_bps(Decimal("100.5"), Decimal("100.0")) == Decimal(50)


def test_basis_below_the_reference_is_negative() -> None:
    # (99 - 100) / 100 * 10 000 = -100
    assert basis_bps(Decimal(99), Decimal(100)) == Decimal(-100)


def test_basis_against_a_close_is_a_float_only_at_the_edge() -> None:
    # (100.5 - 100.4) / 100.4 * 10 000 = 1000 / 100.4 = 9.960159...
    basis = basis_bps(Decimal("100.5"), Decimal("100.4"))
    assert basis is not None
    assert float(basis) == pytest.approx(9.9601593625498)


@pytest.mark.parametrize("ref", [Decimal(0), Decimal(-1)])
def test_basis_is_none_against_a_non_positive_reference(ref: Decimal) -> None:
    assert basis_bps(Decimal(100), ref) is None


def test_eight_hour_funding_annualises_over_1095_intervals() -> None:
    # 31 536 000 s / 28 800 s = 1095 intervals a year; 0.0001 x 1095 = 0.1095
    assert funding_annualised(Decimal("0.0001"), 28_800) == Decimal("0.1095")


def test_hourly_funding_annualises_over_8760_intervals() -> None:
    # Hyperliquid funds hourly: 0.0000125 x 8760 = 0.1095
    assert funding_annualised(Decimal("0.0000125"), 3_600) == Decimal("0.1095")


@pytest.mark.parametrize("interval_s", [None, 0, -3_600])
def test_funding_without_a_positive_interval_is_none(interval_s: int | None) -> None:
    assert funding_annualised(Decimal("0.0001"), interval_s) is None
