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
`research.application.backtest_runner.NodeRunner` over a real `BacktestNode` and a synthetic
catalog spanning two UTC days, built with `ParquetDataCatalog.write_data()` (Story 27.1, TEST-03):
`OFIStrategy` by string path, a single run, a two-point sweep attributed by config id, and a
`trades` run whose id is recomputed from its `BacktestRunConfig`, and the fixed order latency
(`RunSpec.latency_ms`) proven by the price a probe's order fills at.

The session-wide engine in `conftest.py` keeps the Rust logger alive across these node constructions.
"""

import math
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.performance_metrics import all_metrics
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import AccountState
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import AccountId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import AccountBalance
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.trading.strategy import Strategy
from research.application.backtest_runner import NodeRunner
from research.application.backtest_runner import build_run_config
from research.application.backtest_runner import equity_from_account
from research.application.backtest_runner import ledger_from_positions
from research.application.ports import DEFAULT_LATENCY_MS
from research.application.ports import RunResult
from research.application.ports import RunSpec
from research.domain.equity import EquityCurve
from research.domain.report import MetricReport


_IID = InstrumentId(Symbol("BTC-USD-PERP"), Venue("DYDX"))
_DAY0 = 19_700 * NS_PER_DAY
# 20:00 on day 0 to 04:00 on day 1: two UTC days, sampled every 3 s -- OFIStrategy treats a hole
# over `kernel.indicators.OFI_GAP_NS` (3 s since Story 31.3, 5 s before) as a reconnect, so a
# coarser cadence would never let its OFI build.
_START = _DAY0 + 20 * 3_600 * NS_PER_S
_END = _DAY0 + NS_PER_DAY + 4 * 3_600 * NS_PER_S
_STEP = 3 * NS_PER_S
_S = "research.strategies.ofi_strategy:"
_PARAMS = {
    "warmup_seconds": 0,
    "ofi_window": 2,
    "ofi_zscore_window": 5,
    "ofi_threshold": 0.5,
    "trade_size": "0.01",
    "trend_ema_fast": 2,
    "trend_ema_slow": 3,
}


def _instrument() -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=_IID,
        raw_symbol=Symbol("BTC-USD-PERP"),
        base_currency=BTC,
        quote_currency=USDC,
        settlement_currency=USDC,
        is_inverse=False,
        price_precision=1,
        size_precision=3,
        price_increment=Price(0.1, 1),
        size_increment=Quantity(0.001, 3),
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


def _snapshot(i: int, ts: int) -> DydxSecondSnapshot:
    """Build a drifting mid whose book imbalance swings side every ~30 s, so OFI crosses often."""
    mid = 100.0 + 5 * math.sin(i / 37.0) + i * 0.002
    tilt = math.sin(i / 5.0)
    return make_snapshot(
        instrument_id=_IID,
        bid_prices=[round(mid - 0.05 - j * 0.1, 1) for j in range(5)],
        bid_sizes=[round(5.0 + 4 * tilt, 3)] * 5,
        ask_prices=[round(mid + 0.05 + j * 0.1, 1) for j in range(5)],
        ask_sizes=[round(5.0 - 4 * tilt, 3)] * 5,
        buy_volume=1.0,
        sell_volume=1.0,
        buy_count=1,
        sell_count=1,
        ts_event=ts,
        ts_init=ts,
        price_precision=1,  # the instrument definition's (`_instrument`)
        size_precision=3,
    )


@pytest.fixture(scope="module")
def catalog(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("two_day_catalog")
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([_instrument()])
    stamps = range(_START, _END, _STEP)
    catalog.write_data([_snapshot(i, ts) for i, ts in enumerate(stamps)])
    catalog.write_data(
        [
            TradeTick(
                _IID,
                Price(100.0, 1),
                Quantity(0.01, 3),
                AggressorSide.BUYER,
                TradeId(str(i)),
                ts,
                ts,
            )
            for i, ts in enumerate(range(_START, _END, 60 * NS_PER_S))
        ]
    )
    return str(root)


def _spec(catalog: str, **overrides: object) -> RunSpec:
    fields: dict = {
        "catalog_path": catalog,
        "instrument_ids": (str(_IID),),
        "start": _START,
        "end": _END,
        "strategy_path": _S + "OFIStrategy",
        "config_path": _S + "OFIStrategyConfig",
        "params": _PARAMS,
    }
    return RunSpec(**(fields | overrides))


def _equity(curve: EquityCurve) -> list[float]:
    return curve.values.tolist()  # noqa: PD011 -- a numpy array on EquityCurve, not pandas


def _check_result(result: RunResult, starting_balance: float) -> None:
    assert result.iterations > 0
    assert len(result.trades) > 0, "the engineered imbalance should trade"
    assert isinstance(result.metrics, MetricReport)
    assert MetricReport.field_names() == tuple(all_metrics([], [], 1.0))
    assert (
        result.metrics.as_table()
        == MetricReport.from_ledger(result.trades, starting_balance).as_table()
    )
    assert result.pnl_by_day == result.trades.pnl_by_day()
    assert [day["period_start"] for day in result.pnl_by_day] == [_DAY0, _DAY0 + NS_PER_DAY]
    assert all(t.exit_ts >= t.entry_ts for t in result.trades.trades)
    # The result (matched by id) and the engine (fetched by the same id) describe one run.
    pnl = result.nautilus_stats["pnls"]["USDC"]["PnL (total)"]
    assert _equity(result.equity)[-1] - starting_balance == pytest.approx(pnl, abs=1e-9)
    assert _equity(result.equity)[0] == starting_balance
    assert (np.diff(result.equity.ts_ns) > 0).all()


def test_run_and_sweep_attribute_every_result_by_config_id(catalog: str) -> None:
    runner = NodeRunner()
    single = runner.run(_spec(catalog))
    _check_result(single, 10_000.0)
    assert single.params == _PARAMS

    grid = [{"ofi_threshold": 1.0}, {"ofi_threshold": 0.5}]
    swept = runner.sweep(_spec(catalog), grid)
    assert len(swept) == len(grid)
    assert len({r.config_id for r in swept}) == 2
    assert [r.params["ofi_threshold"] for r in swept] == [1.0, 0.5], "results in grid order"
    for result in swept:
        _check_result(result, 10_000.0)
    assert len(swept[0].trades) != len(swept[1].trades), "the two thresholds ran differently"
    assert len(swept[1].trades) == len(single.trades), "the same params replay the same run"


def test_identical_grid_points_raise_before_any_run(catalog: str) -> None:
    grid = [{"ofi_threshold": 0.5}, {"ofi_threshold": 1.0}, {}]  # {} merges to spec's 0.5
    with pytest.raises(ValueError, match="grid points 0 and 2 are identical"):
        NodeRunner().sweep(_spec(catalog), grid)


def test_a_trades_run_reports_its_own_config_id(catalog: str) -> None:
    spec = _spec(catalog, data="trades")
    result = NodeRunner().run(spec)
    instrument = ParquetDataCatalog(catalog).instruments(instrument_ids=[str(_IID)])
    expected = build_run_config(spec, instrument, dict(_PARAMS), "unused-for-trades")
    assert result.config_id == expected.id
    assert result.iterations > 0
    assert len(result.trades) == 0, (
        "OFIStrategy trades on snapshots, which a trades run has none of"
    )
    assert _equity(result.equity) == [10_000.0]


def test_bars_kind_injects_an_internal_bar_type_over_trade_ticks(catalog: str) -> None:
    spec = _spec(catalog, data="bars:1-MINUTE")
    instrument = ParquetDataCatalog(catalog).instruments(instrument_ids=[str(_IID)])
    config = build_run_config(spec, instrument, dict(_PARAMS), "unused")
    assert config.engine is not None
    strategy = config.engine.strategies[0]
    assert strategy.config["bar_type"] == f"{_IID}-1-MINUTE-LAST-INTERNAL"
    assert strategy.config["order_id_tag"] == "0"
    assert [d.data_type for d in config.data] == [TradeTick]
    assert config.data[0].start_time == _START
    assert config.dispose_on_completion is False


def test_seconds_kind_streams_snapshots_and_derived_quotes(catalog: str) -> None:
    spec = _spec(catalog)
    instrument = ParquetDataCatalog(catalog).instruments(instrument_ids=[str(_IID)])
    config = build_run_config(spec, instrument, dict(_PARAMS), "/quotes")
    assert [d.data_type for d in config.data] == [QuoteTick, DydxSecondSnapshot]
    assert config.data[0].catalog_path == "/quotes"
    # BacktestDataConfig bounds are inclusive: the spec's half-open end is passed as `end - 1`.
    assert all((d.start_time, d.end_time) == (_START, _END - 1) for d in config.data)


def test_spec_invariants(catalog: str) -> None:
    with pytest.raises(ValueError, match="not the str"):
        _spec(catalog, instrument_ids=str(_IID))
    with pytest.raises(ValueError, match="one venue"):
        _spec(catalog, instrument_ids=(str(_IID), "BTCUSDT-LINEAR.BYBIT"))
    with pytest.raises(ValueError, match="data must be"):
        _spec(catalog, data="bars:1-MINUTES")
    with pytest.raises(ValueError, match="runner sets"):
        _spec(catalog, params={"instrument_id": "X.DYDX"})
    with pytest.raises(ValueError, match="at least one"):
        _spec(catalog, instrument_ids=())
    with pytest.raises(ValueError, match="twice"):
        _spec(catalog, instrument_ids=(str(_IID), str(_IID)))
    with pytest.raises(ValueError, match="after start"):
        _spec(catalog, start=_END, end=_START)
    with pytest.raises(ValueError, match="after start"):
        _spec(catalog, data="trades", start="2026-09-05", end="2026-09-05")
    for balance in (True, 0, 10_000.0):
        with pytest.raises(ValueError, match="positive int"):
            _spec(catalog, starting_balance=balance)
    for latency in (-1, True, 300.0):
        with pytest.raises(ValueError, match="latency_ms must be"):
            _spec(catalog, latency_ms=latency)


def _positions_report(pnl: str, commissions: list[str]) -> pd.DataFrame:
    """One closed row in `Trader.generate_positions_report()`'s column shape (strings as it emits)."""
    return pd.DataFrame(
        {
            "instrument_id": [str(_IID)],
            "entry": ["SELL"],
            "peak_qty": ["0.010"],
            "ts_opened": [pd.Timestamp(_START, unit="ns", tz="UTC")],
            "ts_closed": [pd.Timestamp(_START + 60 * NS_PER_S, unit="ns", tz="UTC")],
            "commissions": [commissions],
            "realized_pnl": [pnl],
        }
    )


def test_ledger_reads_a_closed_position_and_refuses_foreign_fees() -> None:
    ledger = ledger_from_positions(_positions_report("-0.01701600 USDC", ["0.00101600 USDC"]))
    (trade,) = ledger.trades
    assert (trade.side, trade.qty, trade.realized_pnl, trade.fees) == (
        "SHORT",
        0.01,
        -0.017016,
        0.001016,
    )
    with pytest.raises(ValueError, match="commissions in"):
        ledger_from_positions(_positions_report("-0.01701600 USDC", ["0.00000100 BTC"]))
    unknown = _positions_report("-0.01701600 USDC", ["0.00101600 USDC"])
    unknown["entry"] = ["NO_ORDER_SIDE"]
    with pytest.raises(ValueError, match="neither BUY nor SELL"):
        ledger_from_positions(unknown)


def _account_state(ts: int, total: str) -> AccountState:
    return AccountState(
        AccountId("DYDX-001"),
        AccountType.MARGIN,
        USDC,
        False,
        [
            AccountBalance(
                Money.from_str(f"{total} USDC"), Money(0, USDC), Money.from_str(f"{total} USDC")
            )
        ],
        [],
        {},
        UUID4(),
        ts,
        ts,
    )


def test_equity_keeps_the_last_event_per_timestamp_in_event_order() -> None:
    events = [
        _account_state(1, "100"),
        _account_state(2, "99"),
        _account_state(2, "98"),
        _account_state(3, "97"),
    ]
    curve = equity_from_account(events, "USDC", 100.0)
    assert curve.ts_ns.tolist() == [1, 2, 3]
    assert _equity(curve) == [100.0, 98.0, 97.0]
    with pytest.raises(RuntimeError, match="precedes"):
        equity_from_account([_account_state(2, "1"), _account_state(1, "1")], "USDC", 100.0)


def test_a_window_with_no_data_raises_rather_than_reading_as_no_trades(catalog: str) -> None:
    spec = _spec(catalog, data="trades", start=_DAY0, end=_DAY0 + 3_600 * NS_PER_S)
    with pytest.raises(RuntimeError, match="streamed no trades data"):
        NodeRunner().run(spec)


def test_a_missing_instrument_or_empty_grid_raises(catalog: str) -> None:
    with pytest.raises(ValueError, match="no instrument definition"):
        NodeRunner().run(_spec(catalog, instrument_ids=("ETH-USD-PERP.DYDX",)))
    with pytest.raises(ValueError, match="grid point"):
        NodeRunner().sweep(_spec(catalog), [])


class BarProbeConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType


class BarProbe(Strategy):
    """Buys on the first bar and flattens on the second: a round trip only bars can cause."""

    def __init__(self, config: BarProbeConfig) -> None:
        super().__init__(config)
        self._bars = 0

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        self._bars += 1
        if self._bars == 1:
            order = self.order_factory.market(
                self.config.instrument_id, OrderSide.BUY, Quantity.from_str("0.010")
            )
            self.submit_order(order)
        elif self._bars == 2:
            self.close_all_positions(self.config.instrument_id)


def test_a_bars_run_delivers_internal_bars_to_the_strategy(catalog: str) -> None:
    spec = _spec(
        catalog,
        data="bars:1-MINUTE",
        strategy_path=f"{__name__}:BarProbe",
        config_path=f"{__name__}:BarProbeConfig",
        params={},
    )
    result = NodeRunner().run(spec)
    assert len(result.trades) == 1, "two bars reached the strategy: one round trip"
    (trade,) = result.trades.trades
    assert trade.side == "LONG"
    assert trade.exit_ts - trade.entry_ts == 60 * NS_PER_S


def test_the_venue_gets_the_spec_latency_and_zero_means_no_model(catalog: str) -> None:
    instrument = ParquetDataCatalog(catalog).instruments(instrument_ids=[str(_IID)])
    assert _spec(catalog).latency_ms == DEFAULT_LATENCY_MS
    (venue,) = build_run_config(_spec(catalog), instrument, dict(_PARAMS), "/quotes").venues
    assert venue.latency_model is not None
    assert venue.latency_model.config == {"base_latency_nanos": DEFAULT_LATENCY_MS * 1_000_000}
    (venue,) = build_run_config(
        _spec(catalog, latency_ms=0), instrument, dict(_PARAMS), "/quotes"
    ).venues
    assert venue.latency_model is None


# One snapshot a second whose ask steps up by exactly 1.0 each second (ask of second i = 100.1 + i),
# so an order's fill price names the second whose book it filled against.
_LATENCY_START = _DAY0 + 12 * 3_600 * NS_PER_S
_LATENCY_SECONDS = 10


def _stepped_snapshot(i: int) -> DydxSecondSnapshot:
    ts = _LATENCY_START + i * NS_PER_S
    bid = 100.0 + i
    return make_snapshot(
        instrument_id=_IID,
        bid_prices=[bid],
        bid_sizes=[5.0],
        ask_prices=[round(bid + 0.1, 1)],
        ask_sizes=[5.0],
        ts_event=ts,
        price_precision=1,  # the instrument definition's
        size_precision=3,
    )


@pytest.fixture(scope="module")
def stepped_catalog(tmp_path_factory: pytest.TempPathFactory) -> str:
    root = tmp_path_factory.mktemp("stepped_catalog")
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([_instrument()])
    catalog.write_data([_stepped_snapshot(i) for i in range(_LATENCY_SECONDS)])
    return str(root)


# The probe's fills, `(ts_event, last_px)`: the node builds the strategy itself and a run's result
# carries no fill prices, so the probe records into this module list, which each run clears first.
_FILLS: list[tuple[int, float]] = []


class BuyOnceConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId


class BuyOnce(Strategy):
    """Sends one market buy on the first snapshot it sees, then only records its fill."""

    def __init__(self, config: BuyOnceConfig) -> None:
        super().__init__(config)
        self._sent = False

    def on_start(self) -> None:
        self.subscribe_data(DataType(DydxSecondSnapshot), instrument_id=self.config.instrument_id)

    def on_data(self, data: Data) -> None:
        if self._sent or not isinstance(data, DydxSecondSnapshot):
            return
        self._sent = True
        order = self.order_factory.market(
            self.config.instrument_id, OrderSide.BUY, Quantity.from_str("0.010")
        )
        self.submit_order(order)

    def on_order_filled(self, event: OrderFilled) -> None:
        _FILLS.append((event.ts_event, event.last_px.as_double()))


@pytest.mark.parametrize(
    ("latency_ms", "filled_second"),
    [
        (0, 0),  # no model: the quote the decision was made on
        (1, 1),  # any latency inside the second: the next second's top of book
        (300, 1),
        (1_000, 1),  # due exactly at the next quote, which is applied first
        (1_500, 2),  # past it: the quote after that
    ],
)
def test_an_order_fills_at_the_top_of_book_after_its_latency(
    stepped_catalog: str, latency_ms: int, filled_second: int
) -> None:
    spec = _spec(
        stepped_catalog,
        start=_LATENCY_START,
        end=_LATENCY_START + _LATENCY_SECONDS * NS_PER_S,
        strategy_path=f"{__name__}:BuyOnce",
        config_path=f"{__name__}:BuyOnceConfig",
        params={},
        latency_ms=latency_ms,
    )
    _FILLS.clear()
    NodeRunner().run(spec)
    expected_ask = _stepped_snapshot(filled_second).ask_prices[0]
    assert _FILLS == [(_LATENCY_START + filled_second * NS_PER_S, expected_ask)]
