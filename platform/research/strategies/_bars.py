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
"""
What every bar-driven research strategy shares (lifted out of `candle_pattern_strategy.py`):
resolving a config's bar type, validating a count / trade size against the instrument, and the
`BarSequence` that tells holes and out-of-order bars apart. Imports only `nautilus_trader`.
"""

from decimal import Decimal
from decimal import InvalidOperation

from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarAggregation
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument


def resolve_bar_type(instrument_id: InstrumentId, bar_type: str | None) -> BarType:
    """Return `bar_type`, or `ValueError` if it is not a fixed-step time bar of the instrument."""
    text = bar_type or f"{instrument_id}-1-MINUTE-LAST-INTERNAL"
    resolved = BarType.from_str(text)
    if resolved.instrument_id != instrument_id:
        raise ValueError(f"bar_type {text!r} is not of instrument {instrument_id}")
    if not resolved.spec.is_time_aggregated():
        raise ValueError(f"bar_type {text!r} must be time-aggregated (a hole is a time gap)")
    if resolved.spec.aggregation in (BarAggregation.MONTH, BarAggregation.YEAR):
        # Nautilus gives them a nominal 30/365-day step: every 31-day month would read as a hole.
        raise ValueError(f"bar_type {text!r} has no fixed step (a hole is a gap over one step)")
    return resolved


def check_count(name: str, value: int) -> None:
    """Raise `ValueError` unless `value` is an int >= 1 (a period, a bar count)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be an int >= 1, was {value!r}")


def check_trade_size(trade_size: Decimal) -> None:
    """Raise `ValueError` unless `trade_size` is a finite decimal > 0."""
    try:
        size = Decimal(trade_size)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"trade_size must be a decimal number, was {trade_size!r}") from exc
    # `is_finite` first: comparing a Decimal NaN raises `InvalidOperation`, not `ValueError`.
    if not (size.is_finite() and size > 0):
        raise ValueError(f"trade_size must be finite and > 0, was {trade_size}")


def size_problem(size: Decimal, instrument: Instrument) -> str | None:
    """Return why `size` cannot be traded on `instrument` as given; None if it can."""
    increment = instrument.size_increment.as_decimal()
    if size % increment != 0:
        # `make_qty` would round it, silently trading another amount (DATA-07).
        return f"is not a multiple of {instrument.id}'s size_increment {increment}"
    low, high = instrument.min_quantity, instrument.max_quantity
    if low is not None and size < low.as_decimal():
        return f"is below {instrument.id}'s min_quantity {low}"
    if high is not None and size > high.as_decimal():
        return f"is above {instrument.id}'s max_quantity {high}"
    return None


class BarSequence:
    """
    The order and spacing of the bars one strategy has been fed.

    Invariant: `last_ns` is the `ts_event` of the last bar `is_hole` saw (None before the first),
    so a bar is a hole exactly when it lies more than one `step_ns` after that one; `in_order`
    never changes it. Violated by calling `is_hole` for a bar `in_order` refused.
    """

    def __init__(self, step_ns: int) -> None:
        self.step_ns = step_ns
        self.last_ns: int | None = None

    def in_order(self, bar: Bar) -> bool:
        """Return False for a bar not after the previous one (a duplicate or a late bar)."""
        return self.last_ns is None or bar.ts_event > self.last_ns

    def is_hole(self, bar: Bar) -> bool:
        """Record `bar` as the last bar; True if it is more than one step after the previous."""
        last, self.last_ns = self.last_ns, bar.ts_event
        return last is not None and bar.ts_event - last > self.step_ns
