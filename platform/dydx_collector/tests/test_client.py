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
"""Self-check: re-stamping a Price at fixed precision never changes its value."""

from decimal import Decimal

from dydx_collector.client import _at_fixed_precision
from nautilus_trader.core.nautilus_pyo3 import FIXED_PRECISION
from nautilus_trader.model.objects import Price


# Includes the exact value that exposed a real corruption bug in
# Price(decimal, precision) for precision == FIXED_PRECISION: it silently
# returned 61090.5985500000026624 instead of 61090.59855.
VALUES_AT_VARYING_PRECISION = [
    ("1644.710689", 6),
    ("1647.81348", 5),
    ("61090.59855", 5),
    ("100", 0),
    ("0.000001", 6),
]


def test_value_is_preserved_exactly_across_precisions() -> None:
    for value_str, precision in VALUES_AT_VARYING_PRECISION:
        original = Price(Decimal(value_str), precision)
        fixed = _at_fixed_precision(original)

        assert fixed.as_decimal() == Decimal(value_str)
        assert fixed.precision == FIXED_PRECISION


def test_all_results_share_the_same_precision_label() -> None:
    # The whole point: ticks that started with different precisions must end
    # up with one consistent label, or the catalog's Arrow merge still breaks.
    fixed_prices = [
        _at_fixed_precision(Price(Decimal(value_str), precision))
        for value_str, precision in VALUES_AT_VARYING_PRECISION
    ]
    assert len({p.precision for p in fixed_prices}) == 1


if __name__ == "__main__":
    test_value_is_preserved_exactly_across_precisions()
    test_all_results_share_the_same_precision_label()
    print("ok")
