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

import logging
from decimal import Decimal

import pytest

from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.common.component import TimeEvent
from nautilus_trader.core.uuid import UUID4
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
    catalog_path="catalog",
    restart_gap_threshold_seconds=60,
):
    from scripts.bybit_recorder.strategy import RecorderStrategy
    from scripts.bybit_recorder.strategy import RecorderStrategyConfig

    clock = TestClock()
    trader_id = TestIdStubs.trader_id()
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    portfolio = Portfolio(msgbus=msgbus, cache=mock_cache, clock=clock)

    if instrument_depths is None:
        instrument_depths = dict.fromkeys(instrument_ids, 50)
    if instrument_bar_intervals is None:
        instrument_bar_intervals = {instrument_id: ["1-MINUTE"] for instrument_id in instrument_ids}

    config = RecorderStrategyConfig(
        instrument_ids=instrument_ids,
        linear_instrument_ids=linear_instrument_ids or [],
        instrument_depths=instrument_depths,
        instrument_bar_intervals=instrument_bar_intervals,
        catalog_path=str(catalog_path),
        instance_id_str="8f1b9c2e-1d3a-4b6c-8e7f-0a1b2c3d4e5f",
        conversion_interval_minutes=60,
        restart_gap_threshold_seconds=restart_gap_threshold_seconds,
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


def _seed_catalog_trade_ticks(catalog_path, instrument, last_ts_init):
    # Seed the catalog with TradeTick rows for `instrument` whose latest ts_init
    # is `last_ts_init`, so _log_restart_gaps can read a "last data" timestamp.
    from nautilus_trader.persistence.catalog.parquet import ParquetDataCatalog
    from nautilus_trader.test_kit.stubs.data import TestDataStubs

    catalog = ParquetDataCatalog(str(catalog_path))
    ticks = [
        TestDataStubs.trade_tick(
            instrument=instrument,
            price=50_000.0 + i,
            size=0.01,
            ts_event=last_ts_init - 1_000_000_000 * (2 - i),
            ts_init=last_ts_init - 1_000_000_000 * (2 - i),
        )
        for i in range(3)
    ]
    catalog.write_data(ticks)
    return catalog


def test_on_start_logs_warning_for_restart_gap_exceeding_threshold(
    mocker,
    mock_cache,
    tmp_path,
    caplog,
):
    # D-06 / REL-02 gap visibility: when the gap since the last recorded ts_init
    # exceeds restart_gap_threshold_seconds, on_start logs a WARNING naming the
    # instrument and the gap duration so restart-induced gaps are visible in journald.
    import logging

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    catalog_path = tmp_path / "catalog"
    last_ts = 1_700_000_000_000_000_000
    _seed_catalog_trade_ticks(catalog_path, instrument, last_ts)

    threshold_s = 60
    strategy, clock = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        linear_instrument_ids=[instrument.id],
        catalog_path=catalog_path,
        restart_gap_threshold_seconds=threshold_s,
    )
    # Advance the clock past the threshold relative to the last recorded ts_init.
    clock.set_time(last_ts + int((threshold_s + 1) * 1e9))
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    mocker.patch.object(strategy, "subscribe_order_book_deltas")
    mocker.patch.object(strategy, "subscribe_bars")
    mocker.patch.object(strategy, "subscribe_mark_prices")
    mocker.patch.object(strategy, "subscribe_index_prices")
    mocker.patch.object(strategy, "subscribe_funding_rates")

    # Act
    with caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy"):
        strategy.on_start()

    # Assert: a WARNING naming the instrument and a gap >= threshold.
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(str(instrument.id) in r.getMessage() for r in warnings)
    assert any("gap" in r.getMessage().lower() for r in warnings)


def test_on_start_does_not_warn_when_gap_within_threshold_or_no_prior_data(
    mocker,
    mock_cache,
    tmp_path,
    caplog,
):
    # D-06: no WARNING when the gap is within threshold (a), and none (no exception)
    # when the catalog has no prior data for the instrument (b).
    import logging

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)

    # (a) Gap well under threshold.
    catalog_path_a = tmp_path / "catalog_a"
    last_ts = 1_700_000_000_000_000_000
    _seed_catalog_trade_ticks(catalog_path_a, instrument, last_ts)
    strategy_a, clock_a = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        linear_instrument_ids=[instrument.id],
        catalog_path=catalog_path_a,
        restart_gap_threshold_seconds=60,
    )
    clock_a.set_time(last_ts + 1)
    for name in (
        "subscribe_trade_ticks",
        "subscribe_quote_ticks",
        "subscribe_order_book_deltas",
        "subscribe_bars",
        "subscribe_mark_prices",
        "subscribe_index_prices",
        "subscribe_funding_rates",
    ):
        mocker.patch.object(strategy_a, name)

    with caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy"):
        strategy_a.on_start()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]

    caplog.clear()

    # (b) Empty catalog — no prior ts_init for the instrument.
    catalog_path_b = tmp_path / "catalog_b"
    catalog_path_b.mkdir(parents=True, exist_ok=True)
    strategy_b, clock_b = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        linear_instrument_ids=[instrument.id],
        catalog_path=catalog_path_b,
        restart_gap_threshold_seconds=60,
    )
    clock_b.set_time(last_ts + int(120 * 1e9))
    for name in (
        "subscribe_trade_ticks",
        "subscribe_quote_ticks",
        "subscribe_order_book_deltas",
        "subscribe_bars",
        "subscribe_mark_prices",
        "subscribe_index_prices",
        "subscribe_funding_rates",
    ):
        mocker.patch.object(strategy_b, name)

    with caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy"):
        strategy_b.on_start()  # must not raise
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


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


def test_on_stop_swallows_catalog_construction_error(mocker, mock_cache):
    # CR-01 / T-3-01: if ParquetDataCatalog(...) construction raises during
    # _run_conversion (e.g. transient filesystem error on SIGTERM), on_stop()
    # must not raise, and the per-type convert loop must NOT be reached (early
    # return -- nothing to convert into without a catalog).
    from scripts.bybit_recorder.strategy import RecorderStrategy

    # Arrange
    strategy, _ = _build_strategy(mocker, mock_cache, [])
    convert_spy = mocker.patch.object(RecorderStrategy, "_convert_finalized_feather_files")
    mocker.patch(
        "scripts.bybit_recorder.strategy.ParquetDataCatalog",
        side_effect=RuntimeError("boom"),
    )

    # Act / Assert: does not raise.
    strategy.on_stop()
    assert convert_spy.call_count == 0


def test_log_restart_gaps_swallows_catalog_construction_error(mocker, mock_cache, caplog):
    # WR-02: if ParquetDataCatalog(...) construction raises during
    # _log_restart_gaps (e.g. transient filesystem error at startup), the call
    # must not raise -- otherwise it propagates out of on_start() and aborts the
    # recorder before any subscriptions are made. The per-instrument gap-check
    # loop must NOT be reached (early return), so no per-instrument WARNING
    # records are emitted.

    # Arrange: a non-empty instrument list so a reached loop would have work.
    strategy, _ = _build_strategy(mocker, mock_cache, [INSTRUMENT_ID_LINEAR])
    mocker.patch(
        "scripts.bybit_recorder.strategy.ParquetDataCatalog",
        side_effect=RuntimeError("boom"),
    )

    # Act / Assert: does not raise.
    with caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy"):
        strategy._log_restart_gaps()

    # Assert the early-return path was taken: the per-instrument loop (which is
    # the only source of WARNING records here) was skipped.
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warning_records == []


def test_on_stop_swallows_funding_writer_flush_error(mocker, mock_cache):
    # CR-01 / T-3-01: if the strategy-owned funding writer's flush() raises
    # during _run_conversion, on_stop() must not raise, and the per-type
    # convert loop must still run for all 7 types.
    from scripts.bybit_recorder.strategy import RecorderStrategy

    # Arrange
    strategy, _ = _build_strategy(mocker, mock_cache, [])
    convert_spy = mocker.patch.object(RecorderStrategy, "_convert_finalized_feather_files")
    funding_writer = mocker.Mock()
    funding_writer.flush.side_effect = RuntimeError("boom")
    strategy._funding_writer = funding_writer

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


def _write_recorder_toml(
    tmp_path,
    linear_depth=50,
    spot_depth=50,
    heartbeat_interval_seconds=None,
    restart_gap_threshold_seconds=None,
):
    config_path = tmp_path / "recorder.toml"
    heartbeat_line = (
        f"heartbeat_interval_seconds = {heartbeat_interval_seconds}\n"
        if heartbeat_interval_seconds is not None
        else ""
    )
    restart_gap_line = (
        f"restart_gap_threshold_seconds = {restart_gap_threshold_seconds}\n"
        if restart_gap_threshold_seconds is not None
        else ""
    )
    config_path.write_text(
        f"""
[recorder]
trader_id = "BYBIT-COLLECTOR-001"
catalog_path = "catalog"
streaming_path = "catalog/streaming"
conversion_interval_minutes = 60
{heartbeat_line}{restart_gap_line}environment = "mainnet"

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


def _make_time_event(name: str, ts_ns: int) -> TimeEvent:
    return TimeEvent(name, UUID4(), ts_ns, ts_ns)


def test_on_start_sets_heartbeat_timer(mocker, mock_cache):
    # REL-03: a second native timer named "heartbeat" is registered alongside
    # the existing "convert-stream" timer.
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, clock = _build_strategy(mocker, mock_cache, [instrument.id, instrument.id])
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    mocker.patch.object(strategy, "subscribe_order_book_deltas")
    mocker.patch.object(strategy, "subscribe_bars")

    # Act
    strategy.on_start()

    # Assert
    assert "convert-stream" in clock.timer_names
    assert "heartbeat" in clock.timer_names


def test_on_trade_tick_updates_last_seen(mocker, mock_cache):
    # REL-03: on_trade_tick records ("trade", instrument_id) -> timestamp_ns().
    from nautilus_trader.test_kit.stubs.data import TestDataStubs

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, clock = _build_strategy(mocker, mock_cache, [instrument.id])

    ts = 1_700_000_000_000_000_000
    clock.set_time(ts)
    tick = TestDataStubs.trade_tick(instrument=instrument, ts_event=ts, ts_init=ts)

    # Act
    strategy.on_trade_tick(tick)

    # Assert
    assert strategy._last_seen[("trade", instrument.id)] == clock.timestamp_ns()


def test_on_funding_rate_updates_last_seen_before_dedup(mocker, mock_cache):
    # REL-03 / Pitfall 4: even though the SAME funding rate twice only persists
    # once (dedup), the last-seen timestamp must still be recorded both times --
    # i.e. recorded BEFORE the dedup early-return.
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, clock = _build_strategy(
        mocker,
        mock_cache,
        [instrument.id],
        linear_instrument_ids=[instrument.id],
    )
    persist_spy = mocker.patch.object(strategy, "_persist_funding_rate")

    # Act: same rate twice
    strategy.on_funding_rate(_funding_rate(instrument.id, "0.0001", 1_000_000_000))
    strategy.on_funding_rate(_funding_rate(instrument.id, "0.0001", 2_000_000_000))

    # Assert: persisted only once, but last-seen recorded regardless
    persist_spy.assert_called_once()
    assert ("funding", instrument.id) in strategy._last_seen
    assert strategy._last_seen[("funding", instrument.id)] == clock.timestamp_ns()


def test_heartbeat_warns_when_stream_stale(mocker, mock_cache, caplog):
    # REL-03: when idle time for a stream exceeds its configured stale
    # threshold, _heartbeat logs a WARNING containing "Stale stream" and the
    # instrument id.
    from nautilus_trader.test_kit.stubs.data import TestDataStubs

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, clock = _build_strategy(mocker, mock_cache, [instrument.id])

    ts = 1_700_000_000_000_000_000
    clock.set_time(ts)
    tick = TestDataStubs.trade_tick(instrument=instrument, ts_event=ts, ts_init=ts)
    strategy.on_trade_tick(tick)

    # Advance the clock PAST the "trade" stale threshold.
    threshold_s = strategy._stale_threshold_s("trade")
    clock.set_time(ts + int((threshold_s + 1) * 1e9))

    # Act
    with caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy"):
        strategy._heartbeat(_make_time_event("heartbeat", clock.timestamp_ns()))

    # Assert
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Stale stream" in r.getMessage() and str(instrument.id) in r.getMessage() for r in warnings)


def test_heartbeat_no_warn_when_stream_fresh(mocker, mock_cache, caplog):
    # REL-03: when idle time is UNDER the stale threshold, _heartbeat must not
    # emit a "Stale stream" WARNING.
    from nautilus_trader.test_kit.stubs.data import TestDataStubs

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    mock_cache.add_instrument(instrument)
    strategy, clock = _build_strategy(mocker, mock_cache, [instrument.id])

    ts = 1_700_000_000_000_000_000
    clock.set_time(ts)
    tick = TestDataStubs.trade_tick(instrument=instrument, ts_event=ts, ts_init=ts)
    strategy.on_trade_tick(tick)

    # Advance the clock but stay UNDER the "trade" stale threshold.
    threshold_s = strategy._stale_threshold_s("trade")
    clock.set_time(ts + int((threshold_s - 1) * 1e9))

    # Act
    with caplog.at_level(logging.WARNING, logger="scripts.bybit_recorder.strategy"):
        strategy._heartbeat(_make_time_event("heartbeat", clock.timestamp_ns()))

    # Assert
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("Stale stream" in r.getMessage() for r in warnings)


def test_load_recorder_config_rejects_nonpositive_heartbeat_interval(tmp_path):
    # REL-03 / T-3-05 (V5 fail-fast): a non-positive heartbeat_interval_seconds
    # must raise ValueError echoing the bad value, mirroring the existing depth
    # validation.
    from scripts.bybit_recorder.config import load_recorder_config

    config_path = _write_recorder_toml(tmp_path, heartbeat_interval_seconds=0)

    with pytest.raises(ValueError, match=r"heartbeat_interval_seconds"):
        load_recorder_config(config_path)


def test_load_recorder_config_accepts_default_heartbeat_config(tmp_path):
    # REL-03: when heartbeat_interval_seconds / stale thresholds are absent from
    # recorder.toml, load_recorder_config carries the documented defaults.
    from scripts.bybit_recorder.config import load_recorder_config

    config_path = _write_recorder_toml(tmp_path)

    recorder_cfg, _ = load_recorder_config(config_path)

    assert recorder_cfg.heartbeat_interval_seconds == 30
    assert recorder_cfg.stale_threshold_default_seconds == 90
    assert recorder_cfg.stale_threshold_seconds == {}


def test_load_recorder_config_rejects_nonpositive_restart_gap_threshold(tmp_path):
    # WR-01 / T-3-05 (V5 fail-fast): a non-positive restart_gap_threshold_seconds
    # must raise ValueError echoing the field name, mirroring the existing
    # heartbeat-interval validation. A 0/negative value would invert the
    # intended restart-gap-warning behavior (fire on every restart).
    from scripts.bybit_recorder.config import load_recorder_config

    config_path = _write_recorder_toml(tmp_path, restart_gap_threshold_seconds=0)

    with pytest.raises(ValueError, match=r"restart_gap_threshold_seconds"):
        load_recorder_config(config_path)
