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
Cross-context consistency test (Story 2.2, AC3): MultiLevelOFI must reach the identical state
whether fed by a direct replay over the snapshot arrays or by a real BacktestEngine run of
OFIStrategy (ofi_strategy.py) -- proving the strategy drives the shared kernel indicator exactly
as a plain replay of the same snapshots does, not an integration that happens to agree today.

The replay builds MultiLevelOFI from the strategy config's own levels, window and z-score window
(with `usd_notional=True`, as the strategy does) and applies the strategy's one input rule: a
snapshot with an empty side is not fed (it has no top of book, the rule
`kernel.catalog_files.query_top_of_book` applies too). It records the indicator's
(value, initialized) after every snapshot; the strategy's indicator is only reachable after the
run, so the parity assertion compares the final state (as the original test did). The 7 snapshots are the cumulative book after each
step of the original delta sequence -- bid add, ask add, two same-price size changes, a better
bid, a better ask, then a size change at the new best bid -- so the comparison exercises the
price-improved branch on both sides and the second level, not only same-price size changes.

Story 24.4 repaired this file: commit 1031008cdd rewrote OFIStrategy from OrderBookDeltas +
OrderFlowImbalance (the old parity partner was `book_features.top_of_book_series`, later
`views.chart_series`) into DydxSecondSnapshot + MultiLevelOFI, and changed only the import lines,
so the test failed on the removed `ma_period` keyword. Parity is now with the indicator the
strategy actually drives; research imports no `views` code.

The session-scoped BacktestEngine in conftest.py keeps Nautilus's logging guard alive, which
root-caused the cross-module engine-construction abort this file's name once worked around.
"""

from decimal import Decimal

from kernel.indicators import MultiLevelOFI
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import CustomData
from nautilus_trader.model.data import DataType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from research.strategies.ofi_strategy import OFIStrategy
from research.strategies.ofi_strategy import OFIStrategyConfig


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_USDT = _INSTRUMENT.quote_currency
_NS_PER_S = 1_000_000_000

_CONFIG = OFIStrategyConfig(
    instrument_id=_IID,
    warmup_seconds=0,
    ofi_levels=2,
    ofi_window=50,
    ofi_zscore_window=10,
    ofi_threshold=1e9,  # unreachable -- this test only cares about the OFI value, not fills
    trade_size=Decimal("0.001"),
    min_depth_levels=1,
)

# (bids, asks) as (price, size) levels, best first: the book after each step.
_BOOKS: list[tuple[list[tuple[float, float]], list[tuple[float, float]]]] = [
    ([(100.0, 10.0)], []),  # bid add -- no top of book yet
    ([(100.0, 10.0)], [(101.0, 8.0)]),  # ask add -- first top-of-book state (seeds prev)
    ([(100.0, 12.0)], [(101.0, 8.0)]),  # same-price bid size change
    ([(100.0, 12.0)], [(101.0, 6.0)]),  # same-price ask size change
    ([(100.5, 5.0), (100.0, 12.0)], [(101.0, 6.0)]),  # better bid price
    ([(100.5, 5.0), (100.0, 12.0)], [(100.8, 4.0), (101.0, 6.0)]),  # better ask price
    ([(100.5, 15.0), (100.0, 12.0)], [(100.8, 4.0), (101.0, 6.0)]),  # size change at new bid
]


def _snapshots() -> list[DydxSecondSnapshot]:
    return [
        make_snapshot(
            instrument_id=_IID,
            bid_prices=[p for p, _ in bids],
            bid_sizes=[s for _, s in bids],
            ask_prices=[p for p, _ in asks],
            ask_sizes=[s for _, s in asks],
            buy_volume=0.0,
            sell_volume=0.0,
            buy_count=0,
            sell_count=0,
            ts_event=(i + 1) * _NS_PER_S,
            ts_init=(i + 1) * _NS_PER_S,
        )
        for i, (bids, asks) in enumerate(_BOOKS)
    ]


def _run_direct_replay(
    snapshots: list[DydxSecondSnapshot],
) -> list[tuple[float | None, bool]]:
    """Direct-replay path: the snapshot arrays straight into the kernel indicator."""
    ofi = MultiLevelOFI(
        levels=_CONFIG.ofi_levels,
        window=_CONFIG.ofi_window,
        usd_notional=True,
        zscore_window=_CONFIG.ofi_zscore_window,
    )
    results = []
    for s in snapshots:
        if s.bid_prices and s.ask_prices:
            ofi.update_raw(s.bid_prices, s.bid_sizes, s.ask_prices, s.ask_sizes)
        results.append((ofi.value if ofi.initialized else None, ofi.initialized))
    return results


def _run_backtest_strategy(
    snapshots: list[DydxSecondSnapshot],
) -> list[tuple[float | None, bool]]:
    """BacktestEngine path: exactly how ofi_strategy.py's on_data feeds MultiLevelOFI."""
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=_IID.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=_USDT,
        starting_balances=[Money(10_000, _USDT)],
        book_type=BookType.L1_MBP,
    )
    engine.add_instrument(_INSTRUMENT)
    engine.add_data(
        [CustomData(DataType(DydxSecondSnapshot), s) for s in snapshots],
        client_id=ClientId(str(_IID.venue)),
    )

    strategy = OFIStrategy(_CONFIG)
    engine.add_strategy(strategy)
    try:
        engine.run()
        ofi = strategy._ofi
        result = [(ofi.value if ofi.initialized else None, ofi.initialized)]
    finally:
        engine.reset()
        engine.dispose()
    return result


def test_ofi_final_state_identical_across_direct_replay_and_backtest_strategy() -> None:
    """
    AC3: MultiLevelOFI must reach the identical final (value, initialized) state whether fed via
    the direct replay or a real BacktestEngine-run OFIStrategy, given the same snapshots
    (including genuine price moves, not just size changes) -- proving the two callers drive the
    shared indicator identically, not two integrations that just agree on today's test data.
    """
    snapshots = _snapshots()

    direct_results = _run_direct_replay(snapshots)
    backtest_result = _run_backtest_strategy(snapshots)

    assert direct_results, "expected at least one state from the direct-replay path"
    direct_final = direct_results[-1]
    assert direct_final[1] is True, (
        f"expected OFI to be initialized after {len(direct_results)} snapshots"
    )
    assert backtest_result[0] == direct_final, (
        f"direct-replay final state {direct_final} != backtest-strategy final state "
        f"{backtest_result[0]}"
    )

    # The "not initialized after the first state" behavior is also covered by test_indicators.py's
    # isolated MultiLevelOFI tests -- checked here via the (cheaper) direct-replay path only.
    first_state_only = _run_direct_replay(_snapshots()[:1])
    assert first_state_only == [(None, False)]


if __name__ == "__main__":
    test_ofi_final_state_identical_across_direct_replay_and_backtest_strategy()
    print("ok")
