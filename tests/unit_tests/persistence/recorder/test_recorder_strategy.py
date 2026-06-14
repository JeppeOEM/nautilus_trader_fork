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

from decimal import Decimal

import pytest

from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs


INSTRUMENT_ID_LINEAR = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
INSTRUMENT_ID_SPOT = InstrumentId.from_str("ETHUSDT-SPOT.BYBIT")


def _build_strategy(
    mocker,
    mock_cache,
    instrument_ids,
    linear_instrument_ids=None,
    instrument_depths=None,
    instrument_bar_intervals=None,
):
    from scripts.bybit_recorder.strategy import RecorderStrategy
    from scripts.bybit_recorder.strategy import RecorderStrategyConfig

    clock = TestClock()
    trader_id = TestIdStubs.trader_id()
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    portfolio = Portfolio(msgbus=msgbus, cache=mock_cache, clock=clock)

    if instrument_depths is None:
        instrument_depths = {instrument_id: 50 for instrument_id in instrument_ids}
    if instrument_bar_intervals is None:
        instrument_bar_intervals = {instrument_id: ["1-MINUTE"] for instrument_id in instrument_ids}

    config = RecorderStrategyConfig(
        instrument_ids=instrument_ids,
        linear_instrument_ids=linear_instrument_ids or [],
        instrument_depths=instrument_depths,
        instrument_bar_intervals=instrument_bar_intervals,
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


def test_on_start_subscribes_quote_ticks_per_instrument(mocker, mock_cache):
    # Arrange
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, _ = _build_strategy(mocker, mock_cache, [instrument.id, instrument.id])
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    subscribe_spy = mocker.patch.object(strategy, "subscribe_quote_ticks")

    # Act
    strategy.on_start()

    # Assert
    assert subscribe_spy.call_count == 2


def test_on_start_subscribes_order_book_deltas_per_instrument(mocker, mock_cache):
    # Arrange
    from nautilus_trader.model.enums import BookType

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        instrument_depths={instrument.id: 50},
    )
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    subscribe_spy = mocker.patch.object(strategy, "subscribe_order_book_deltas")

    # Act
    strategy.on_start()

    # Assert
    assert subscribe_spy.call_count == 1
    _, kwargs = subscribe_spy.call_args
    assert kwargs["book_type"] == BookType.L2_MBP
    assert kwargs["depth"] == 50


def test_on_start_subscribes_bars_per_interval(mocker, mock_cache):
    # Arrange
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        instrument_bar_intervals={instrument.id: ["1-MINUTE"]},
    )
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    mocker.patch.object(strategy, "subscribe_order_book_deltas")
    subscribe_spy = mocker.patch.object(strategy, "subscribe_bars")

    # Act
    strategy.on_start()

    # Assert
    assert subscribe_spy.call_count == 1
    (bar_type,), _ = subscribe_spy.call_args
    assert str(bar_type) == f"{instrument.id}-1-MINUTE-LAST-EXTERNAL"


def test_on_start_linear_only_mark_index_gating(mocker, mock_cache):
    # Arrange
    instrument_linear = TestInstrumentProvider.btcusdt_perp_binance()
    instrument_spot = TestInstrumentProvider.adabtc_binance()
    mock_cache.add_instrument(instrument_linear)
    mock_cache.add_instrument(instrument_spot)
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [instrument_linear.id, instrument_spot.id],
        linear_instrument_ids=[instrument_linear.id],
        instrument_depths={instrument_linear.id: 50, instrument_spot.id: 50},
        instrument_bar_intervals={
            instrument_linear.id: ["1-MINUTE"],
            instrument_spot.id: ["1-MINUTE"],
        },
    )
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    mocker.patch.object(strategy, "subscribe_order_book_deltas")
    mocker.patch.object(strategy, "subscribe_bars")
    mark_spy = mocker.patch.object(strategy, "subscribe_mark_prices")
    index_spy = mocker.patch.object(strategy, "subscribe_index_prices")

    # Act
    strategy.on_start()

    # Assert
    mark_spy.assert_called_once_with(instrument_linear.id)
    index_spy.assert_called_once_with(instrument_linear.id)


def test_on_start_subscribes_funding_rates_linear_only(mocker, mock_cache):
    # Arrange
    instrument_linear = TestInstrumentProvider.btcusdt_perp_binance()
    instrument_spot = TestInstrumentProvider.adabtc_binance()
    mock_cache.add_instrument(instrument_linear)
    mock_cache.add_instrument(instrument_spot)
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [instrument_linear.id, instrument_spot.id],
        linear_instrument_ids=[instrument_linear.id],
        instrument_depths={instrument_linear.id: 50, instrument_spot.id: 50},
        instrument_bar_intervals={
            instrument_linear.id: ["1-MINUTE"],
            instrument_spot.id: ["1-MINUTE"],
        },
    )
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    mocker.patch.object(strategy, "subscribe_order_book_deltas")
    mocker.patch.object(strategy, "subscribe_bars")
    mocker.patch.object(strategy, "subscribe_mark_prices")
    mocker.patch.object(strategy, "subscribe_index_prices")
    funding_spy = mocker.patch.object(strategy, "subscribe_funding_rates")

    # Act
    strategy.on_start()

    # Assert
    funding_spy.assert_called_once_with(instrument_linear.id)


def test_on_stop_runs_final_conversion_per_type(mocker, mock_cache):
    # REL-02: on_stop() runs a final flush+convert (via _run_conversion ->
    # _convert_finalized_feather_files per type) before the kernel closes its "*"
    # writer. Spy on _convert_finalized_feather_files so no real filesystem write
    # happens; assert one call per recorded type + FundingRateUpdate (7 total).
    from nautilus_trader.model.data import Bar
    from nautilus_trader.model.data import FundingRateUpdate as _FundingRateUpdate
    from nautilus_trader.model.data import IndexPriceUpdate
    from nautilus_trader.model.data import MarkPriceUpdate
    from nautilus_trader.model.data import OrderBookDeltas
    from nautilus_trader.model.data import QuoteTick
    from nautilus_trader.model.data import TradeTick
    from scripts.bybit_recorder.strategy import RecorderStrategy

    # Arrange
    strategy, _ = _build_strategy(mocker, mock_cache, [])
    convert_spy = mocker.patch.object(RecorderStrategy, "_convert_finalized_feather_files")

    # Act
    strategy.on_stop()

    # Assert: one convert call per type, in the expected order, with a catalog
    # instance and the corresponding data_cls.
    expected_types = [
        TradeTick,
        QuoteTick,
        OrderBookDeltas,
        Bar,
        MarkPriceUpdate,
        IndexPriceUpdate,
        _FundingRateUpdate,
    ]
    assert convert_spy.call_count == 7
    called_types = [call.args[1] for call in convert_spy.call_args_list]
    assert called_types == expected_types


def test_on_stop_flushes_funding_writer(mocker, mock_cache):
    # REL-02 / Pitfall 1: on_stop flushes the STRATEGY-OWNED funding writer (never
    # the kernel "*" writer). When the funding writer is None, on_stop must not raise.
    from scripts.bybit_recorder.strategy import RecorderStrategy

    # Arrange: stub the per-type convert so no real conversion runs.
    mocker.patch.object(RecorderStrategy, "_convert_finalized_feather_files")
    strategy, _ = _build_strategy(mocker, mock_cache, [])

    # Case A: funding writer present -> flushed.
    funding_writer = mocker.Mock()
    strategy._funding_writer = funding_writer
    strategy.on_stop()
    funding_writer.flush.assert_called_once()

    # Case B: funding writer None -> no raise.
    strategy._funding_writer = None
    strategy.on_stop()  # must not raise


def test_on_stop_swallows_per_type_conversion_error(mocker, mock_cache):
    # T-3-02: a transient convert error for one type must not re-raise out of the
    # on_stop hook nor block the remaining types. With a side_effect raising for the
    # FIRST type, on_stop still attempts all 7 types and does not raise.
    from scripts.bybit_recorder.strategy import RecorderStrategy

    # Arrange
    strategy, _ = _build_strategy(mocker, mock_cache, [])
    side_effects = [RuntimeError("boom")] + [None] * 6
    convert_spy = mocker.patch.object(
        RecorderStrategy,
        "_convert_finalized_feather_files",
        side_effect=side_effects,
    )

    # Act / Assert: does not raise.
    strategy.on_stop()
    assert convert_spy.call_count == 7


def _funding_rate(instrument_id, rate, ts: int) -> FundingRateUpdate:
    return FundingRateUpdate(
        instrument_id=instrument_id,
        rate=Decimal(rate),
        ts_event=ts,
        ts_init=ts,
    )


def test_on_funding_rate_dedups_unchanged_rate(mocker, mock_cache):
    # Arrange
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        linear_instrument_ids=[instrument.id],
    )
    persist_spy = mocker.patch.object(strategy, "_persist_funding_rate")

    # Act: same rate twice
    strategy.on_funding_rate(_funding_rate(instrument.id, "0.0001", 1_000_000_000))
    strategy.on_funding_rate(_funding_rate(instrument.id, "0.0001", 2_000_000_000))

    # Assert: persisted only once
    persist_spy.assert_called_once()


def test_on_funding_rate_persists_changed_rate(mocker, mock_cache):
    # Arrange
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        linear_instrument_ids=[instrument.id],
    )
    persist_spy = mocker.patch.object(strategy, "_persist_funding_rate")

    # Act: rate A then rate B (changed)
    strategy.on_funding_rate(_funding_rate(instrument.id, "0.0001", 1_000_000_000))
    strategy.on_funding_rate(_funding_rate(instrument.id, "0.0002", 2_000_000_000))

    # Assert: persisted twice
    assert persist_spy.call_count == 2


def test_on_funding_rate_dedup_is_per_instrument(mocker, mock_cache):
    # Arrange
    instrument_a = TestInstrumentProvider.btcusdt_perp_binance()
    instrument_b = TestInstrumentProvider.adabtc_binance()
    mock_cache.add_instrument(instrument_a)
    mock_cache.add_instrument(instrument_b)
    strategy, _ = _build_strategy(
        mocker,
        mock_cache,
        [instrument_a.id, instrument_b.id],
        linear_instrument_ids=[instrument_a.id, instrument_b.id],
    )
    persist_spy = mocker.patch.object(strategy, "_persist_funding_rate")

    # Act: same numeric rate for two different instruments
    strategy.on_funding_rate(_funding_rate(instrument_a.id, "0.0001", 1_000_000_000))
    strategy.on_funding_rate(_funding_rate(instrument_b.id, "0.0001", 1_000_000_000))

    # Assert: both persisted — dedup is per-instrument
    assert persist_spy.call_count == 2


def _write_recorder_toml(tmp_path, linear_depth=50, spot_depth=50):
    config_path = tmp_path / "recorder.toml"
    config_path.write_text(
        f"""
[recorder]
trader_id = "BYBIT-COLLECTOR-001"
catalog_path = "catalog"
streaming_path = "catalog/streaming"
conversion_interval_minutes = 60
environment = "mainnet"

[[instruments.linear]]
id = "BTCUSDT-LINEAR.BYBIT"
depth = {linear_depth}
bar_intervals = ["1-MINUTE"]

[[instruments.spot]]
id = "ETHUSDT-SPOT.BYBIT"
depth = {spot_depth}
bar_intervals = ["1-MINUTE"]
""",
    )
    return config_path


def test_load_recorder_config_rejects_spot_depth_over_50(tmp_path):
    from scripts.bybit_recorder.config import load_recorder_config

    config_path = _write_recorder_toml(tmp_path, linear_depth=50, spot_depth=200)

    with pytest.raises(ValueError, match=r"200"):
        load_recorder_config(config_path)


def test_load_recorder_config_accepts_spot_depth_50(tmp_path):
    from scripts.bybit_recorder.config import load_recorder_config

    config_path = _write_recorder_toml(tmp_path, linear_depth=50, spot_depth=50)

    recorder_cfg, _ = load_recorder_config(config_path)

    assert recorder_cfg is not None


def test_load_recorder_config_rejects_linear_depth_not_in_discrete_set(tmp_path):
    from scripts.bybit_recorder.config import load_recorder_config

    config_path = _write_recorder_toml(tmp_path, linear_depth=75, spot_depth=50)

    with pytest.raises(ValueError, match=r"75"):
        load_recorder_config(config_path)


def test_load_recorder_config_accepts_linear_depth_200_and_1000(tmp_path):
    from scripts.bybit_recorder.config import load_recorder_config

    for linear_depth in (200, 1000):
        config_path = _write_recorder_toml(tmp_path, linear_depth=linear_depth, spot_depth=50)

        recorder_cfg, _ = load_recorder_config(config_path)

        assert recorder_cfg is not None
