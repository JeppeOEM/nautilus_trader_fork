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
dYdX recorder strategy tests (DYDX-03, DYDX-04).

These tests drive the SHARED ``RecorderStrategy`` (no dYdX subclass exists — the
dYdX recorder reuses the common strategy via composition) with dYdX instrument
ids. They prove that EVERY dYdX perp subscribes all 7 feeds (because every dYdX
perp is linear per RESEARCH A1) and that funding dedup is exchange-agnostic
(keys on ``(instrument_id, rate)``).
"""

from decimal import Decimal

from nautilus_trader.common.component import MessageBus
from nautilus_trader.common.component import TestClock
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.portfolio.portfolio import Portfolio
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.test_kit.stubs.identifiers import TestIdStubs


# dYdX perpetual instrument ids (venue-suffixed ``.DYDX``). All dYdX instruments
# are perpetual derivatives — there is no spot market (RESEARCH A1).
DYDX_BTC = InstrumentId.from_str("BTC-USD-PERP.DYDX")
DYDX_ETH = InstrumentId.from_str("ETH-USD-PERP.DYDX")


def _dydx_perp(instrument_id: InstrumentId) -> CryptoPerpetual:
    """
    Build a `CryptoPerpetual` carrying a dYdX instrument id.

    There is no dYdX perp factory in the test kit, so a Binance perp stub is
    re-tagged with the dYdX id via a `to_dict`/`from_dict` roundtrip. Only the id
    matters here: the strategy's `on_start` cache-presence guard keys on id, and
    every assertion compares against this id.
    """
    base = TestInstrumentProvider.btcusdt_perp_binance()
    raw = CryptoPerpetual.to_dict(base)
    raw["id"] = str(instrument_id)
    raw["raw_symbol"] = instrument_id.symbol.value
    return CryptoPerpetual.from_dict(raw)


def _build_dydx_strategy(
    mocker,
    mock_cache,
    instrument_ids,
    instrument_bar_intervals=None,
    catalog_path="catalog",
):
    """
    Build a registered shared ``RecorderStrategy`` configured for dYdX.

    Mirrors the Bybit ``_build_strategy`` harness, but ALL configured ids are
    passed as ``linear_instrument_ids`` (every dYdX perp is linear, A1) and the
    depth is a dummy 50 (dYdX is full-depth L2; the adapter ignores the depth knob).
    """
    from scripts.common_recorder.strategy import RecorderStrategy
    from scripts.common_recorder.strategy import RecorderStrategyConfig

    clock = TestClock()
    trader_id = TestIdStubs.trader_id()
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    portfolio = Portfolio(msgbus=msgbus, cache=mock_cache, clock=clock)

    if instrument_bar_intervals is None:
        instrument_bar_intervals = {instrument_id: ["1-MINUTE"] for instrument_id in instrument_ids}

    config = RecorderStrategyConfig(
        instrument_ids=instrument_ids,
        # A1: every dYdX perp is linear -> mark/index/funding always subscribed.
        linear_instrument_ids=instrument_ids,
        # A1: dummy depth -- dYdX is full-depth L2 and the adapter ignores this.
        instrument_depths=dict.fromkeys(instrument_ids, 50),
        instrument_bar_intervals=instrument_bar_intervals,
        catalog_path=str(catalog_path),
        instance_id_str="3c4d5e6f-7a8b-49c0-a1d2-e3f405162738",
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


def test_subscribes_all_seven_feeds_for_a_dydx_perp(mocker, mock_cache):
    # DYDX-03: a single dYdX perp subscribes ALL 7 feeds (trade, quote,
    # order_book_deltas, bars, mark, index, funding) because every dYdX perp is
    # linear (A1).
    mock_cache.add_instrument(_dydx_perp(DYDX_BTC))

    strategy, _ = _build_dydx_strategy(
        mocker,
        mock_cache,
        [DYDX_BTC],
        instrument_bar_intervals={DYDX_BTC: ["1-MINUTE"]},
    )

    trade_spy = mocker.patch.object(strategy, "subscribe_trade_ticks")
    quote_spy = mocker.patch.object(strategy, "subscribe_quote_ticks")
    deltas_spy = mocker.patch.object(strategy, "subscribe_order_book_deltas")
    bars_spy = mocker.patch.object(strategy, "subscribe_bars")
    mark_spy = mocker.patch.object(strategy, "subscribe_mark_prices")
    index_spy = mocker.patch.object(strategy, "subscribe_index_prices")
    funding_spy = mocker.patch.object(strategy, "subscribe_funding_rates")

    # Act
    strategy.on_start()

    # Assert: all 7 feeds subscribed exactly once for the single perp.
    trade_spy.assert_called_once_with(DYDX_BTC)
    quote_spy.assert_called_once_with(DYDX_BTC)
    deltas_spy.assert_called_once()
    _, deltas_kwargs = deltas_spy.call_args
    assert deltas_kwargs["book_type"] == BookType.L2_MBP
    bars_spy.assert_called_once()
    mark_spy.assert_called_once_with(DYDX_BTC)
    index_spy.assert_called_once_with(DYDX_BTC)
    funding_spy.assert_called_once_with(DYDX_BTC)


def test_dydx_bar_type_is_last_external(mocker, mock_cache):
    # DYDX-03: the bar subscription uses the venue-native LAST-EXTERNAL kline and
    # carries the dYdX instrument id in the BarType string.
    mock_cache.add_instrument(_dydx_perp(DYDX_BTC))

    strategy, _ = _build_dydx_strategy(
        mocker,
        mock_cache,
        [DYDX_BTC],
        instrument_bar_intervals={DYDX_BTC: ["1-MINUTE"]},
    )
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    mocker.patch.object(strategy, "subscribe_order_book_deltas")
    mocker.patch.object(strategy, "subscribe_mark_prices")
    mocker.patch.object(strategy, "subscribe_index_prices")
    mocker.patch.object(strategy, "subscribe_funding_rates")
    bars_spy = mocker.patch.object(strategy, "subscribe_bars")

    # Act
    strategy.on_start()

    # Assert
    bars_spy.assert_called_once()
    (bar_type,), _ = bars_spy.call_args
    assert str(bar_type) == f"{DYDX_BTC}-1-MINUTE-LAST-EXTERNAL"
    assert str(bar_type).endswith("-LAST-EXTERNAL")
    assert "DYDX" in str(bar_type)


def test_all_dydx_perps_are_linear(mocker, mock_cache):
    # DYDX-03 / A1: with TWO dYdX perps configured, mark/index/funding are
    # subscribed for BOTH (no spot exclusion exists on dYdX).
    mock_cache.add_instrument(_dydx_perp(DYDX_BTC))
    mock_cache.add_instrument(_dydx_perp(DYDX_ETH))

    strategy, _ = _build_dydx_strategy(
        mocker,
        mock_cache,
        [DYDX_BTC, DYDX_ETH],
        instrument_bar_intervals={
            DYDX_BTC: ["1-MINUTE"],
            DYDX_ETH: ["1-MINUTE"],
        },
    )
    mocker.patch.object(strategy, "subscribe_trade_ticks")
    mocker.patch.object(strategy, "subscribe_quote_ticks")
    mocker.patch.object(strategy, "subscribe_order_book_deltas")
    mocker.patch.object(strategy, "subscribe_bars")
    mark_spy = mocker.patch.object(strategy, "subscribe_mark_prices")
    index_spy = mocker.patch.object(strategy, "subscribe_index_prices")
    funding_spy = mocker.patch.object(strategy, "subscribe_funding_rates")

    # Act
    strategy.on_start()

    # Assert: mark/index/funding called for BOTH perps -> all-perps-linear.
    assert mark_spy.call_count == 2
    assert index_spy.call_count == 2
    assert funding_spy.call_count == 2


def _funding_rate(instrument_id, rate, ts: int) -> FundingRateUpdate:
    return FundingRateUpdate(
        instrument_id=instrument_id,
        rate=Decimal(rate),
        ts_event=ts,
        ts_init=ts,
    )


def test_funding_dedup_drops_unchanged_rate_for_dydx(mocker, mock_cache):
    # DYDX-04: identical-rate second update for the same dYdX id does NOT persist
    # again; a changed-rate third update DOES. Proves dedup keys on
    # (instrument_id, rate) and is exchange-agnostic.
    mock_cache.add_instrument(_dydx_perp(DYDX_BTC))

    strategy, _ = _build_dydx_strategy(mocker, mock_cache, [DYDX_BTC])
    persist_spy = mocker.patch.object(strategy, "_persist_funding_rate")

    # Act: same rate twice, then a different rate.
    strategy.on_funding_rate(_funding_rate(DYDX_BTC, "0.0001", 1_000_000_000))
    strategy.on_funding_rate(_funding_rate(DYDX_BTC, "0.0001", 2_000_000_000))
    # Assert: unchanged second update deduped -> persisted only once so far.
    persist_spy.assert_called_once()

    strategy.on_funding_rate(_funding_rate(DYDX_BTC, "0.0002", 3_000_000_000))
    # Assert: changed rate triggers a second persist.
    assert persist_spy.call_count == 2


def test_funding_dedup_is_per_dydx_instrument(mocker, mock_cache):
    # DYDX-04: the SAME numeric rate for two DIFFERENT dYdX ids persists both —
    # dedup is keyed per (instrument_id, rate), not globally.
    mock_cache.add_instrument(_dydx_perp(DYDX_BTC))
    mock_cache.add_instrument(_dydx_perp(DYDX_ETH))

    strategy, _ = _build_dydx_strategy(mocker, mock_cache, [DYDX_BTC, DYDX_ETH])
    persist_spy = mocker.patch.object(strategy, "_persist_funding_rate")

    # Act: same numeric rate for two different instruments.
    strategy.on_funding_rate(_funding_rate(DYDX_BTC, "0.0001", 1_000_000_000))
    strategy.on_funding_rate(_funding_rate(DYDX_ETH, "0.0001", 1_000_000_000))

    # Assert: both persisted — dedup is per-instrument.
    assert persist_spy.call_count == 2
