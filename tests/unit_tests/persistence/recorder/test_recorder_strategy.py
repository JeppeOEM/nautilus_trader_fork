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

import pytest

from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs


INSTRUMENT_ID_LINEAR = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
INSTRUMENT_ID_SPOT = InstrumentId.from_str("ETHUSDT-SPOT.BYBIT")


def _build_strategy(mocker, mock_cache, instrument_ids):
    from scripts.bybit_recorder.strategy import RecorderStrategy
    from scripts.bybit_recorder.strategy import RecorderStrategyConfig

    clock = TestClock()
    trader_id = TestIdStubs.trader_id()
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    portfolio = Portfolio(msgbus=msgbus, cache=mock_cache, clock=clock)

    config = RecorderStrategyConfig(
        instrument_ids=instrument_ids,
        catalog_path="catalog",
        instance_id_str="8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f",
        conversion_interval_minutes=60,
    )
    strategy = RecorderStrategy(config=config)
    strategy.register(
        trader_id=trader_id,
        portfolio=portfolio,
        msgbus=msgbus,
        cache=mock_cache,
        clock=clock,
    )
    return strategy, clock


def test_on_start_raises_listing_all_missing_instruments(mocker, mock_cache):
    # Arrange
    # Neither INSTRUMENT_ID_LINEAR nor INSTRUMENT_ID_SPOT is registered in the
    # cache, so cache.instrument(id) naturally returns None for both.
    strategy, _ = _build_strategy(mocker, mock_cache, [INSTRUMENT_ID_LINEAR, INSTRUMENT_ID_SPOT])

    # Act / Assert
    with pytest.raises(
        RuntimeError,
        match=r"BTCUSDT-LINEAR\.BYBIT.*ETHUSDT-SPOT\.BYBIT|ETHUSDT-SPOT\.BYBIT.*BTCUSDT-LINEAR\.BYBIT",
    ):
        strategy.on_start()


def test_on_start_missing_instrument_does_not_call_stop(mocker, mock_cache):
    # A3 RESULT: on_start's RuntimeError propagates out of Strategy.start()
    # (Component.start() re-raises -- component.pyx "raise  # Halt state
    # transition"), through Trader._start() and TradingNode.run_async(), and is
    # caught (but NOT re-raised by default) in TradingNode.run(). The strategy
    # itself never calls self.stop() on this path -- this is proven at the unit
    # level here. recorder.py's main() now calls node.run(raise_exception=True)
    # so the RuntimeError propagates out of main() and the process exits
    # non-zero (verified empirically by running `python -c "..."` against a
    # built-but-unconnectable node: RuntimeError propagated, exit code 1).
    # A full subprocess exit-code check against the real entrypoint
    # (scripts/bybit_recorder/recorder.py) requires Bybit connectivity to reach
    # on_start (TradingNode.run_async only calls trader.start() after
    # _await_engines_connected() succeeds) -- that live confirmation is
    # deferred to the Task 3 human-verify checkpoint (D-05/D-06/T-01-10).

    # Arrange
    strategy, _ = _build_strategy(mocker, mock_cache, [INSTRUMENT_ID_LINEAR, INSTRUMENT_ID_SPOT])
    stop_spy = mocker.patch.object(strategy, "stop")

    # Act / Assert
    with pytest.raises(RuntimeError, match="Missing instruments"):
        strategy.on_start()

    stop_spy.assert_not_called()


def test_on_start_subscribes_trade_ticks_per_instrument(mocker, mock_cache):
    # Arrange
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, _ = _build_strategy(mocker, mock_cache, [instrument.id, instrument.id])
    subscribe_spy = mocker.patch.object(strategy, "subscribe_trade_ticks")

    # Act
    strategy.on_start()

    # Assert
    assert subscribe_spy.call_count == 2


def test_on_start_sets_conversion_timer(mocker, mock_cache):
    # Arrange
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, clock = _build_strategy(mocker, mock_cache, [instrument.id, instrument.id])
    mocker.patch.object(strategy, "subscribe_trade_ticks")

    # Act
    strategy.on_start()

    # Assert
    assert "convert-stream" in clock.timer_names
