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
`OFIStrategy`'s forced-flow options (Story 33.13): the config refusals, the organic cumulative
delta computed exactly from real `Liquidation` rows and snapshots, and planted backtests through
`NodeRunner` with `data="seconds_liquidations"` -- `BacktestNode` streaming the snapshots and the
archived liquidations into one strategy.

The planted catalog (`BTCUSDT-LINEAR.BYBIT`, price precision 2, size 3), 600 s: one snapshot a
second (stamped `S + 0.5 s`, received 200 ms later) whose five-level book tilts with
`sin(i / 5)`, a period of ~31.4 s, so OFI flips side about every 15.7 s and the strategy reverses
there (the exit on the flip second, the entry one second later, once flat); a LONG liquidation
every 20 s for t = 0..280 and a 30 s LONG burst (one a second, t = 300..329). With a 10 s window
over a 120 s baseline the burst is the one episode (-1, longs liquidated: forced sells), open from
~301.35 s to 336 s. The trend EMAs never warm (`trend_ema_slow` 1000 minutes), so no entry is
blocked by the trend.
"""

import math
from decimal import Decimal
from typing import Any

import pandas as pd
import pytest
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.backtest_runner import NodeRunner
from research.application.liquidations import replay_cascade
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.strategies import backtest_ofi
from research.strategies.cascade_rules import PHASE_FADE_WINDOW
from research.strategies.cascade_rules import QUIET
from research.strategies.cascade_rules import CascadePhase
from research.strategies.ofi_strategy import FORCED_FLOW_SITE
from research.strategies.ofi_strategy import OFIStrategy
from research.strategies.ofi_strategy import OFIStrategyConfig


_S = "research.strategies.ofi_strategy:"
_IID = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
_NS = 1_000_000_000
_T0 = 1_788_000_000 * _NS
_SECONDS = 600
_BURST = range(300, 330)
_DETECTOR: dict[str, object] = {"cascade_window_s": 10, "cascade_baseline_s": 120}
_PARAMS: dict[str, object] = {
    "trade_size": "0.01",
    "warmup_seconds": 0,
    "ofi_window": 2,
    "ofi_zscore_window": 5,
    "ofi_threshold": 0.5,
    "trend_ema_fast": 2,
    "trend_ema_slow": 1_000,
}


def _instrument() -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=_IID,
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        size_precision=3,
        price_increment=Price.from_str("0.01"),
        size_increment=Quantity.from_str("0.001"),
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.05"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        ts_event=0,
        ts_init=0,
    )


def _config(**overrides: Any) -> OFIStrategyConfig:
    return OFIStrategyConfig(instrument_id=_IID, **overrides)


# --- config ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instrument", "overrides", "message"),
    [
        (str(_IID), {"liquidation_cascade_mode": "x"}, "liquidation_cascade_mode must be one of"),
        (
            str(_IID),
            {"liquidation_cascade_mode": "chase"},
            "liquidation_cascade_mode must be one of",
        ),
        ("BTCUSDT-SPOT.BYBIT", {"forced_flow_filter": True}, "need a liquidation feed"),
        ("BTC-USD-PERP.HYPERLIQUID", {"liquidation_cascade_mode": "fade"}, "need a liquidation"),
        ("BTC-USD-PERP.DYDX", {"liquidation_cascade_mode": "follow"}, "need a liquidation feed"),
    ],
)
def test_a_bad_forced_flow_config_raises_at_construction(
    instrument: str, overrides: dict[str, Any], message: str
) -> None:
    config = OFIStrategyConfig(instrument_id=InstrumentId.from_str(instrument), **overrides)
    with pytest.raises(ValueError, match=message):
        OFIStrategy(config)


def test_the_defaults_need_no_feed() -> None:
    OFIStrategy(OFIStrategyConfig(instrument_id=InstrumentId.from_str("BTC-USD-PERP.DYDX")))


def test_a_bad_detector_parameter_raises_only_with_a_cascade_mode() -> None:
    OFIStrategy(_config(cascade_decay_ratio=2.0))  # mode off: no detector is built
    with pytest.raises(ValueError, match="decay_ratio"):
        OFIStrategy(_config(liquidation_cascade_mode="fade", cascade_decay_ratio=2.0))


# --- the organic cumulative delta, by hand -------------------------------------------------------


def _strategy(**overrides: Any) -> OFIStrategy:
    # warmup_seconds keeps every snapshot short of a decision: no portfolio is needed.
    strategy = OFIStrategy(_config(warmup_seconds=3_600, **overrides))
    strategy.instrument = _instrument()  # what on_start sets
    return strategy


def _second(second: int, buy: float, sell: float, one_sided: bool = False) -> Any:
    ts = _T0 + second * _NS + _NS // 2
    return make_snapshot(
        instrument_id=_IID,
        bid_prices=[99.99, 99.98],
        bid_sizes=[1.0, 1.0],
        ask_prices=[] if one_sided else [100.01, 100.02],
        ask_sizes=[] if one_sided else [1.0, 1.0],
        buy_volume=buy,
        sell_volume=sell,
        ts_event=ts,
        ts_init=ts + 200_000_000,
        price_precision=2,
        size_precision=3,
    )


def _liquidation(
    ts_ns: int, side: LiquidatedSide, size_units: int, size_precision: int
) -> Liquidation:
    return Liquidation(
        _IID, side, size_units, 1_000_000, 2, size_precision, f"l{ts_ns}{side}", ts_ns, ts_ns
    )


def _deltas(strategy: OFIStrategy) -> list[float]:
    return [delta for _, delta in strategy._cum_delta_events]


def test_the_filter_takes_the_forced_flow_out_of_the_cumulative_delta_exactly() -> None:
    # Second 0: bought 0.010, sold 0.004; a long liquidation of 0.003 (a forced sell) and a short
    # one of 0.0010 at size precision 4 (a forced buy), received before the snapshot: the organic
    # delta is (10 - 1) - (4 - 3) = 8 units of 10^-3, 0.008. Second 1 starts from nothing again.
    strategy = _strategy(forced_flow_filter=True)
    strategy.on_data(_liquidation(_T0 + 100_000_000, LiquidatedSide.LONG, 3, 3))
    strategy.on_data(_liquidation(_T0 + 200_000_000, LiquidatedSide.SHORT, 10, 4))
    strategy.on_data(_second(0, 0.010, 0.004))
    strategy.on_data(_second(1, 0.002, 0.001))
    assert _deltas(strategy) == [0.008, 0.001]


def test_without_the_filter_the_delta_is_buy_minus_sell_as_before() -> None:
    strategy = _strategy()
    strategy.on_data(_liquidation(_T0 + 100_000_000, LiquidatedSide.LONG, 3, 3))  # not read
    strategy.on_data(_second(0, 0.010, 0.004))
    assert _deltas(strategy) == [0.010 - 0.004]


def test_another_instruments_liquidation_is_not_counted() -> None:
    strategy = _strategy(forced_flow_filter=True)
    other = Liquidation(
        InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT"), LiquidatedSide.LONG, 3, 1, 2, 3, "x", 0, 0
    )
    strategy.on_data(other)
    strategy.on_data(_second(0, 0.010, 0.004))
    assert _deltas(strategy) == [0.006]


def test_a_forced_size_the_snapshot_cannot_hold_makes_the_second_unknown_and_is_ledgered() -> None:
    error_ledger.reset()
    try:
        strategy = _strategy(forced_flow_filter=True)
        strategy.on_data(_liquidation(_T0, LiquidatedSide.LONG, 5, 4))  # 0.0005 at precision 3
        strategy.on_data(_second(0, 0.010, 0.004))
        assert math.isnan(_deltas(strategy)[0])
        assert error_ledger.counts() == {FORCED_FLOW_SITE: 1}
    finally:
        error_ledger.reset()


def test_an_unknown_delta_in_the_window_blocks_a_cum_delta_gated_entry() -> None:
    strategy = _strategy(forced_flow_filter=True, cum_delta_threshold=-1_000.0)
    error_ledger.reset()
    try:
        strategy.on_data(_liquidation(_T0, LiquidatedSide.LONG, 5, 4))
        strategy.on_data(_second(0, 0.010, 0.004))
    finally:
        error_ledger.reset()
    assert not strategy._filters_pass(OrderSide.BUY, _second(1, 0.0, 0.0))


def test_liquidations_across_a_feed_gap_are_discarded_and_counted_not_netted() -> None:
    # Second 0, then a 10 s hole (> OFI_GAP_NS): a long liquidation of 0.003 received in the hole
    # belongs to no snapshot second, so the post-gap snapshot's delta is its own, 0.006.
    strategy = _strategy(forced_flow_filter=True)
    strategy.on_data(_second(0, 0.001, 0.0))
    strategy.on_data(_liquidation(_T0 + 5 * _NS, LiquidatedSide.LONG, 3, 3))
    strategy.on_data(_second(10, 0.010, 0.004))
    assert _deltas(strategy) == [0.001, 0.006]
    assert (strategy.delivered_liquidations, strategy.unattributed_liquidations) == (1, 1)


def test_liquidations_before_a_one_sided_snapshot_are_discarded_and_counted() -> None:
    strategy = _strategy(forced_flow_filter=True)
    strategy.on_data(_second(0, 0.001, 0.0))
    strategy.on_data(_liquidation(_T0 + _NS, LiquidatedSide.SHORT, 2, 3))
    strategy.on_data(_second(1, 0.005, 0.0, one_sided=True))  # no delta pushed
    strategy.on_data(_second(2, 0.010, 0.004))  # nothing left to net
    assert _deltas(strategy) == [0.001, 0.006]
    assert strategy.unattributed_liquidations == 1


def test_the_fade_window_closes_at_the_first_snapshot_after_a_gap() -> None:
    # A fade window opened at 1 s; the feed then stops for 60 s (> the 10 s window): the
    # detector is advanced and the phase recomputed at the next snapshot before any entry.
    strategy = _strategy(liquidation_cascade_mode="fade", cascade_window_s=10)
    strategy.on_data(_second(0, 0.0, 0.0))
    strategy._phase = CascadePhase(PHASE_FADE_WINDOW, -1, False, True, _T0 + _NS)
    strategy.on_data(_second(60, 0.0, 0.0))
    assert strategy._phase == QUIET


def test_the_forced_flow_flag_must_be_a_bool() -> None:
    with pytest.raises(TypeError, match="forced_flow_filter must be a bool"):
        backtest_ofi.run(symbol=str(_IID), catalog_path="/nowhere", forced_flow_filter="false")


def test_a_forced_flow_result_is_refused_where_a_snapshot_result_is_read(catalog: str) -> None:
    result = _run(catalog)  # a NodeRunner RunResult
    with pytest.raises(TypeError, match="BacktestResult"):
        backtest_ofi.snapshot_result(result)


# --- the planted backtests ---------------------------------------------------------------------


def _snapshot(i: int) -> Any:
    tilt = math.sin(i / 5.0)
    ts = _T0 + i * _NS + _NS // 2
    return make_snapshot(
        instrument_id=_IID,
        bid_prices=[round(10_000 - 0.05 - j * 0.1, 2) for j in range(5)],
        bid_sizes=[round(5 + 4 * tilt, 3)] * 5,
        ask_prices=[round(10_000 + 0.05 + j * 0.1, 2) for j in range(5)],
        ask_sizes=[round(5 - 4 * tilt, 3)] * 5,
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        ts_event=ts,
        ts_init=ts + 200_000_000,
        price_precision=2,
        size_precision=3,
    )


def _planted_rows(burst_units: int = 10) -> list[Liquidation]:
    background = [
        _liquidation(_T0 + s * _NS + 300_000_000, LiquidatedSide.LONG, 10, 3)
        for s in range(0, 300, 20)
    ]
    burst = [
        _liquidation(_T0 + s * _NS + 300_000_000, LiquidatedSide.LONG, burst_units, 3)
        for s in _BURST
    ]
    return [*background, *burst]


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("ofi_forced_flow")
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([_instrument()])
    catalog.write_data([_snapshot(i) for i in range(_SECONDS)])
    catalog.write_data(_planted_rows())
    return str(root)


@pytest.fixture(scope="module")
def heavy_catalog(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Return the planted catalog with a burst of 1.000 BTC long liquidations a second (forced sells)."""
    root = tmp_path_factory.mktemp("ofi_forced_flow_heavy")
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([_instrument()])
    catalog.write_data([_snapshot(i) for i in range(_SECONDS)])
    catalog.write_data(_planted_rows(burst_units=1_000))
    return str(root)


def _run(
    catalog: str, params: dict[str, object] | None = None, data: str = "seconds_liquidations"
) -> RunResult:
    spec = RunSpec(
        catalog_path=catalog,
        instrument_ids=(str(_IID),),
        start=_T0,
        end=_T0 + _SECONDS * _NS,
        strategy_path=_S + "OFIStrategy",
        config_path=_S + "OFIStrategyConfig",
        params={**_PARAMS, **(params or {})},
        data=data,
    )
    return NodeRunner().run(spec)


def _orders(result: RunResult) -> list[tuple[str, float]]:
    """Each order's side and its submit time in seconds after `_T0`, in order."""
    frame = result.orders
    if frame.empty:  # the engine's report of no order has no columns
        return []
    stamps = (frame["ts_init"].astype("int64") - _T0) / _NS
    return list(zip(frame["side"].tolist(), stamps.tolist(), strict=True))


def _in_episode(orders: list[tuple[str, float]], side: str) -> set[float]:
    (episode,) = replay_cascade(
        _planted_rows(), 10, 120, 3.0, 0.5, _T0 + _SECONDS * _NS, start_ns=_T0, precisions=(2, 3)
    )
    assert episode.end_ns is not None  # the burst decays inside the window
    start, end = (episode.start_ns - _T0) / _NS, (episode.end_ns - _T0) / _NS
    return {t for s, t in orders if s == side and start <= t <= end}


def test_follow_suppresses_the_entry_against_the_forced_flow_during_the_burst(
    catalog: str,
) -> None:
    off = _orders(_run(catalog, _DETECTOR))
    follow = _orders(_run(catalog, {**_DETECTOR, "liquidation_cascade_mode": "follow"}))
    # Off: OFI reverses into a long at 302.7 and 334.7 s, each after its exit a second earlier.
    assert _in_episode(off, "BUY") == {301.7, 302.7, 333.7, 334.7}
    # Follow lets the exits through but no long entry while longs are being liquidated ...
    assert _in_episode(follow, "BUY") == {301.7, 333.7}
    # ... and the forced side's entry (a short at 317.7 s) passes.
    assert 317.7 in _in_episode(follow, "SELL")


def test_fade_waits_for_the_fade_window_and_takes_the_other_side(catalog: str) -> None:
    fade = _orders(_run(catalog, {**_DETECTOR, "liquidation_cascade_mode": "fade"}))
    assert _in_episode(fade, "BUY") == {301.7}  # only the exit: no entry while it builds
    assert _in_episode(fade, "SELL") == set()  # nor a short
    # The first entry after the episode ended (336 s) is a long, inside the 10 s fade window.
    assert ("BUY", 337.7) in fade


def test_the_defaults_fill_exactly_as_a_run_without_the_new_fields(catalog: str) -> None:
    columns = ["side", "ts_init", "avg_px", "filled_qty"]
    before = _run(catalog, data="seconds").orders[columns].reset_index(drop=True)
    explicit = _run(
        catalog,
        {
            "forced_flow_filter": False,
            "liquidation_cascade_mode": "off",
            "cascade_window_s": 30,
            "cascade_baseline_s": 3_600,
            "cascade_intensity_threshold": 3.0,
            "cascade_decay_ratio": 0.5,
        },
    ).orders[columns]
    assert len(before) > 0
    pd.testing.assert_frame_equal(before, explicit.reset_index(drop=True))


def test_the_forced_flow_filter_runs_through_the_node(catalog: str) -> None:
    # Every planted liquidation is 0.010 long and every second sold 0.5 and bought 1.0, so the
    # filter only shifts the delta: with no cum-delta gate the run trades exactly as the baseline.
    filtered = _run(catalog, {"forced_flow_filter": True})
    assert _orders(filtered) == _orders(_run(catalog))


def test_the_filter_changes_decisions_through_the_node(heavy_catalog: str) -> None:
    # Each second sells 0.5 and buys 1.0: over a 5 s window the raw cum delta is ~+3, under the
    # 4.0 gate, so the baseline never enters. Through the burst every second's 1.000 BTC long
    # liquidation (a forced sell) comes out of the sells: the organic delta is +1.5 a second, the
    # cum delta ~+9, and the filter run enters long when OFI says buy -- only BacktestNode's
    # delivery of the `Liquidation` rows to the strategy can make that difference.
    gate: dict[str, object] = {"cum_delta_threshold": 4.0, "cum_delta_seconds": 5}
    baseline = _orders(_run(heavy_catalog, gate))
    filtered = _orders(_run(heavy_catalog, {**gate, "forced_flow_filter": True}))
    assert baseline == []
    assert filtered != []
    burst = (_BURST.start, _BURST.stop + 5)
    assert all(burst[0] <= t <= burst[1] for side, t in filtered if side == "BUY")
