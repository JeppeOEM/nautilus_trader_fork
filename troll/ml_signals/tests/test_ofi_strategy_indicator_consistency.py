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
Cross-context consistency test (Story 2.2, AC3): OrderFlowImbalance must produce identical
output whether fed via the direct-replay path (book_features.top_of_book_series, used by
metrics_computer.py/chart_data.py) or via a real BacktestEngine run of OFIStrategy
(ofi_strategy.py) -- proving both consuming contexts drive the identical shared indicator
class the same way, not two integrations that happen to agree today.

Scope note: this proves integration-code parity (how each caller invokes update_raw) on
well-formed, non-crossing book data -- it is not a claim that the two callers behave
identically in every book state. top_of_book_series (book_features.py) explicitly skips
crossed-book states (bid >= ask); OFIStrategy.on_order_book_deltas (ofi_strategy.py) only
guards against a None bid/ask, with no crossed-book skip. That asymmetry is a real, pre-existing
gap between the two callers, out of scope to fix here (ofi_strategy.py is out of scope for this
story) -- logged in deferred-work.md, not silently glossed over.

Uses single-delta-per-OrderBookDeltas-batch synthetic data deliberately: OFIStrategy updates
its indicator once per received *batch* (after applying every delta in it), while
top_of_book_series yields once per individual delta. A multi-delta batch would make the two
paths call update_raw() a different number of times -- single-delta batches sidestep that.
The data also includes a genuine best-price move on each side (via a fresh, better-priced ADD),
not just size changes at a fixed price, so the comparison exercises OrderFlowImbalance's
price-improved/price-worsened branches, not only its price-unchanged branch.

Filename note: deliberately named to sort alphabetically AFTER test_ofi_strategy.py, not
test_indicator_consistency.py. Constructing a second BacktestEngine-based test in a file that
collects BEFORE test_ofi_strategy.py causes a fatal native abort partway through
test_ofi_strategy.py's own engine construction -- reproduced with a byte-identical copy of
test_ofi_strategy.py's own first test run from a separate module, so this is not specific to
this file's parameters or content; it is a pre-existing fragility in how this pinned
nautilus_trader version's BacktestEngine/kernel logging init behaves across module boundaries
within one pytest process. See deferred-work.md for the full investigation notes, including a
caveat about this filename-ordering mitigation's limits -- it is the pragmatic workaround for
the currently-used Docker-based test invocation, not a root-cause fix.
"""

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

from ml_signals.book_features import top_of_book_series
from ml_signals.indicators import OrderFlowImbalance
from ml_signals.ofi_strategy import OFIStrategy
from ml_signals.ofi_strategy import OFIStrategyConfig


_INSTRUMENT = TestInstrumentProvider.btcusdt_binance()
_IID = _INSTRUMENT.id
_PP = _INSTRUMENT.price_precision
_SP = _INSTRUMENT.size_precision
_USDT = _INSTRUMENT.quote_currency


def _delta(action: BookAction, side: OrderSide, price: float, size: float, ts: int, seq: int) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, _PP), size=Quantity(size, _SP), order_id=0)
    return OrderBookDelta(instrument_id=_IID, action=action, order=order, flags=0, sequence=seq, ts_event=ts, ts_init=ts)


def _single_delta_batches() -> list[OrderBookDeltas]:
    """
    7 single-delta batches: bid add, ask add (first top-of-book state), two same-price size
    updates, then a better-priced bid ADD and a better-priced ask ADD (genuine price moves,
    not just size changes), then one more size update. Each batch has exactly one delta --
    keeps the strategy's per-batch update_raw() calls in lockstep with top_of_book_series's
    per-delta yields.
    """
    ts = 1_000_000_000
    step = 1_000_000_000
    steps = [
        (BookAction.ADD, OrderSide.BUY, 100.0, 10.0),      # bid only -- no top-of-book state yet
        (BookAction.ADD, OrderSide.SELL, 101.0, 8.0),       # 1st top-of-book state (seeds prev state)
        (BookAction.UPDATE, OrderSide.BUY, 100.0, 12.0),    # same-price size change (bid_price == prev)
        (BookAction.UPDATE, OrderSide.SELL, 101.0, 6.0),    # same-price size change (ask_price == prev)
        (BookAction.ADD, OrderSide.BUY, 100.5, 5.0),        # better bid price -- bid_price > prev branch
        (BookAction.ADD, OrderSide.SELL, 100.8, 4.0),       # better ask price -- ask_price < prev branch
        (BookAction.UPDATE, OrderSide.BUY, 100.5, 15.0),    # same-price size change at the new best bid
    ]
    batches = []
    for i, (action, side, price, size) in enumerate(steps):
        batches.append(OrderBookDeltas(instrument_id=_IID, deltas=[_delta(action, side, price, size, ts, i)]))
        ts += step
    return batches


def _run_direct_replay(batches: list[OrderBookDeltas]) -> list[tuple[float | None, bool]]:
    """Direct-replay path: exactly how metrics_computer.py/chart_data.py feed OFI."""
    deltas = [d for batch in batches for d in batch.deltas]
    ofi = OrderFlowImbalance(window=50)
    results = []
    for _ts, bid_p, bid_s, ask_p, ask_s in top_of_book_series(deltas, _IID):
        ofi.update_raw(bid_p, bid_s, ask_p, ask_s)
        results.append((ofi.value if ofi.initialized else None, ofi.initialized))
    return results


def _run_backtest_strategy(batches: list[OrderBookDeltas]) -> list[tuple[float | None, bool]]:
    """BacktestEngine path: exactly how ofi_strategy.py's on_order_book_deltas feeds OFI."""
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(
        venue=_IID.venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=_USDT,
        starting_balances=[Money(10_000, _USDT)],
        book_type=BookType.L2_MBP,
    )
    engine.add_instrument(_INSTRUMENT)
    engine.add_data(batches)

    config = OFIStrategyConfig(
        instrument_id=_IID,
        ofi_window=50,
        # ma_period is irrelevant to this test -- it only gates the MA/history-append path
        # (ofi_strategy.py's every-ofi_window-th-event sampling), which this test never reaches
        # since it only reads the strategy's own _ofi indicator directly, not its MA output.
        ma_period=1,
        buy_threshold=1e9,   # unreachable -- this test only cares about the OFI value, not fills
        sell_threshold=-1e9,
        trade_size=Decimal("0.001"),
        min_depth_levels=1,
    )
    strategy = OFIStrategy(config)
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
    AC3: OrderFlowImbalance must reach the identical final (value, initialized) state whether
    fed via the direct-replay path or a real BacktestEngine-run OFIStrategy, given the same
    input delta sequence (including genuine price moves, not just size changes) -- proving the
    two callers drive the shared indicator identically, not two integrations that just agree
    on today's test data.
    """
    batches = _single_delta_batches()

    direct_results = _run_direct_replay(batches)
    backtest_result = _run_backtest_strategy(batches)

    assert direct_results, "expected at least one top-of-book update from the direct-replay path"
    direct_final = direct_results[-1]
    assert direct_final[1] is True, f"expected OFI to be initialized after {len(direct_results)} top-of-book updates"
    assert backtest_result[0] == direct_final, (
        f"direct-replay final state {direct_final} != backtest-strategy final state {backtest_result[0]}"
    )

    # The "not initialized after the first top-of-book state" behavior is already covered by
    # test_indicators.py's isolated OrderFlowImbalance test -- checked here via the (cheaper)
    # direct-replay path only, rather than constructing a second BacktestEngine for it: this
    # environment cannot reliably support two BacktestEngine constructions from two different
    # test modules in one pytest process before the second module's own engine construction
    # (see the crash investigation in this file's module docstring and deferred-work.md), so
    # this test intentionally builds exactly one engine, not two.
    first_state_only = _run_direct_replay(_single_delta_batches()[:2])
    assert first_state_only == [(None, False)]


if __name__ == "__main__":
    test_ofi_final_state_identical_across_direct_replay_and_backtest_strategy()
    print("ok")
