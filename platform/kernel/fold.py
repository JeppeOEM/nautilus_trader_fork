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
The one trades -> second fold (story 22.13, audit D-46; kernel, DDD spine AD-D3/AD-D7): shared by
the live `_sample_tick`, the nightly `rebuild_seconds` and any strategy feature code, so the three
can never disagree.

Exact by construction: volumes are summed as `Quantity.raw` integers and OHLC is chosen by
comparing `Price.raw` integers. Nautilus stores every raw at one fixed scale whatever the
precision label, so trades carrying different precisions compare and sum exactly; the result is
labelled with the highest precision seen. The only float conversion is `snapshot_values()`, at
the `DydxSecondSnapshot` boundary, once per field of an exact total.

Seconds -> bars (1 m and wider) is a different fold: `candles.domain.fold.fold_arrays`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import NamedTuple

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


class SnapshotTradeValues(NamedTuple):
    """The eight trade columns of a `DydxSecondSnapshot`, as the snapshot stores them."""

    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None
    buy_volume: float
    sell_volume: float
    buy_count: int
    sell_count: int


@dataclass(frozen=True)
class SecondTradeFields:
    """One second's trade fields; OHLC and volumes are None when no trade of that side/at all."""

    open_price: Price | None = None
    high_price: Price | None = None
    low_price: Price | None = None
    close_price: Price | None = None
    buy_volume: Quantity | None = None
    sell_volume: Quantity | None = None
    buy_count: int = 0
    sell_count: int = 0

    def snapshot_values(self) -> SnapshotTradeValues:
        """Return the snapshot's trade columns: the one float conversion (0.0 / None when empty)."""
        return SnapshotTradeValues(
            open_price=_as_float(self.open_price),
            high_price=_as_float(self.high_price),
            low_price=_as_float(self.low_price),
            close_price=_as_float(self.close_price),
            buy_volume=0.0 if self.buy_volume is None else self.buy_volume.as_double(),
            sell_volume=0.0 if self.sell_volume is None else self.sell_volume.as_double(),
            buy_count=self.buy_count,
            sell_count=self.sell_count,
        )


def _as_float(price: Price | None) -> float | None:
    return None if price is None else price.as_double()


def _sum_sizes(trades: list[TradeTick]) -> Quantity | None:
    if not trades:
        return None
    total = sum(t.size.raw for t in trades)
    return Quantity.from_raw(total, max(t.size.precision for t in trades))


def fold_trades(trades: Sequence[TradeTick]) -> SecondTradeFields:
    """
    Fold one second's trades. Open/close are the first/last by `ts_event`, trades sharing a
    `ts_event` keeping their input (arrival) order -- `sorted` is stable. A trade whose aggressor
    is not BUYER counts as a sell, the live collector's contract since before this fold existed.
    """
    if not trades:
        return SecondTradeFields()
    ordered = sorted(trades, key=lambda t: t.ts_event)
    buys = [t for t in ordered if t.aggressor_side == AggressorSide.BUYER]
    sells = [t for t in ordered if t.aggressor_side != AggressorSide.BUYER]
    return SecondTradeFields(
        open_price=ordered[0].price,
        high_price=max((t.price for t in ordered), key=lambda p: p.raw),
        low_price=min((t.price for t in ordered), key=lambda p: p.raw),
        close_price=ordered[-1].price,
        buy_volume=_sum_sizes(buys),
        sell_volume=_sum_sizes(sells),
        buy_count=len(buys),
        sell_count=len(sells),
    )
