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
`SnapshotStrategy` skips a one-sided row whole, as `OFIStrategy` does (Story 31.3, review P14).

Its own module, with no `BacktestEngine`: `test_snapshot_strategy.py` must build exactly one engine
(its docstring: a second construction in one file aborts natively). The strategy is not registered,
so a row that reached its portfolio checks would raise -- the skip is what keeps it silent.
"""

from decimal import Decimal

from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.model.identifiers import InstrumentId
from research.strategies.snapshot_strategy import SnapshotStrategy
from research.strategies.snapshot_strategy import SnapshotStrategyConfig


_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")


def test_a_one_sided_row_neither_feeds_ofi_nor_moves_the_gap_clock() -> None:
    strategy = SnapshotStrategy(SnapshotStrategyConfig(instrument_id=_IID, trade_size=Decimal(1)))
    two_sided = make_snapshot(
        _IID, [100.0], [1.0], [101.0], [1.0], ts_event=1_000_000_000, price_precision=1
    )
    one_sided = make_snapshot(
        _IID, [], [], [101.0], [2.0], ts_event=2_000_000_000, price_precision=1
    )

    strategy.on_data(two_sided)  # the baseline: OFI not initialized yet, returns before trading
    strategy.on_data(one_sided)

    assert strategy._last_ts == 1_000_000_000
    assert not strategy._ofi.initialized
