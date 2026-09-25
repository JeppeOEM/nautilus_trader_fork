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
`SecondRow` is the one name capture and candles share for a duck-typed second (Story 24.1).

Both shapes that cross the `SecondSink` port must satisfy it: the live `DydxSecondSnapshot` and the
`SecondOHLC` the catalog read returns. If either stopped satisfying it the fold would still run --
duck typing -- and only fail at the attribute, deep inside a flush.
"""

from types import SimpleNamespace

from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from kernel.second_snapshot import SecondRow
from nautilus_trader.model.identifiers import InstrumentId


_IID = "BTC-USD-PERP.DYDX"


def _ohlc() -> SecondOHLC:
    return SecondOHLC(1_000_000_000, 100.0, 101.0, 99.0, 100.5, 1.0, 0.5)


def _snapshot() -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[99.0],
        bid_sizes=[1.0],
        ask_prices=[101.0],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        ts_event=1_000_000_000,
        ts_init=1_000_000_000,
        open_price=100.0,
        high_price=101.0,
        low_price=99.0,
        close_price=100.5,
    )


def _volume(row: SecondRow) -> float:
    """Typed against the protocol, so mypy checks every call site below satisfies it."""
    return row.buy_volume + row.sell_volume


def test_second_ohlc_satisfies_the_protocol() -> None:
    assert isinstance(_ohlc(), SecondRow)
    assert _volume(_ohlc()) == 1.5


def test_the_live_snapshot_satisfies_the_protocol() -> None:
    assert isinstance(_snapshot(), SecondRow)
    assert _volume(_snapshot()) == 1.5


def test_the_two_shapes_carry_the_same_per_second_values() -> None:
    fields = ("ts_event", "open_price", "high_price", "low_price", "close_price")
    assert [getattr(_ohlc(), f) for f in fields] == [getattr(_snapshot(), f) for f in fields]


def test_a_row_missing_a_field_does_not_satisfy_the_protocol() -> None:
    """A stand-in that forgot `sell_volume` is caught here, not at the flush that folds it."""
    assert not isinstance(SimpleNamespace(ts_event=0, buy_volume=1.0), SecondRow)
