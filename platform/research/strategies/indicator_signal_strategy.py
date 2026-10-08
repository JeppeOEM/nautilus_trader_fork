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
"""
Indicator-signal strategy: one `signal` name picks a Nautilus (or kernel) bar indicator and the rule
that turns it into +1 (long) / -1 (short) / 0, so a notebook compares every indicator with one
parameter. It imports only `kernel` and `nautilus_trader` (and its sibling `_bars`); the six signals
of Story 33.11 read `kernel.ta`, the indicators Nautilus lacks (ZigZag, which repaints, and the
non-directional pivot levels are deliberately not signals).

**Signals** (`_SIGNALS`; every `signal_params` key below is optional, an unknown key raises). A
mean-reversion signal also has a release: the position closes when the indicator is back past its
neutral level.

| signal | defaults (`signal_params`) | long (+1) / short (-1) |
|---|---|---|
| `bollinger` | period 20, k 2.0 | close <= lower / >= upper; release at middle |
| `keltner` | period 20, k_multiplier 2.0 | close <= lower / >= upper; release at middle |
| `donchian` | period 20 | close <= lower / >= upper; release at middle |
| `rsi` | period 14, low 0.3, high 0.7, neutral 0.5 | value <= low / >= high; release at neutral |
| `stochastics` | period_k 14, period_d 3, low 20, high 80, neutral 50 | %K <= low / >= high; release |
| `cci` | period 20, low -100, high 100, neutral 0 | value <= low / >= high; release |
| `cmo` | period 14, low -50, high 50, neutral 0 | value <= low / >= high; release |
| `rvi` | period 14, low 30, high 70, neutral 50 | value <= low / >= high; release |
| `psl` | period 12, low 25, high 75, neutral 50 | value <= low / >= high; release |
| `macd` | fast 12, slow 26 | value > 0 / < 0 |
| `aroon` | period 25 | oscillator > 0 / < 0 |
| `directional_movement` | period 14 | +DI > -DI / < |
| `ichimoku` | tenkan 9, kijun 26, senkou 52, displacement 26 | close above / below the cloud |
| `linear_regression` | period 20 | slope > 0 / < 0 |
| `bias` | period 20 | value > 0 / < 0 |
| `archer` | fast 10, slow 20, signal 5 | `long_run` / `short_run` |
| `roc` | period 10 | value > 0 / < 0 |
| `efficiency_ratio` | period 10, threshold 0.3 | ER >= threshold and the period's close change > 0 / < 0 |
| `obv` | period 20 | value > 0 / < 0 |
| `vwap` | min_bars 2 | close > VWAP / < VWAP (resets each UTC day, by the bar's open time; fires from `min_bars` bars into each day) |
| `kvo` | fast 12, slow 26, signal 9 | value > 0 / < 0 |
| `pressure` | period 20 | value > 0 / < 0 |
| `fuzzy_candle` | period 10, min_size 4 (`SIZE_LARGE`) | bull / bear candle of size >= min_size |
| `logistic_trend` | lookback 5, learning_rate 0.05, buy 0.6, sell 0.4 | value > buy / < sell |
| `swings` | period 10 | the swing direction up / down |
| `supertrend` | period 10, multiplier 3.0 | `direction` +1 / -1 (`kernel.ta.Supertrend`) |
| `parabolic_sar` | step 0.02, max_step 0.2 | close > SAR / < SAR (`kernel.ta.ParabolicSAR`) |
| `adx` | period 14, adx_min 25 | +DI > -DI / < when ADX >= adx_min, else 0 (`kernel.ta.AverageDirectionalIndex`) |
| `mfi` | period 14, low 20, high 80, neutral 50 | value <= low / >= high; release (`kernel.ta.MoneyFlowIndex`) |
| `cmf` | period 20 | value > 0 / < 0 (`kernel.ta.ChaikinMoneyFlow`) |
| `awesome_oscillator` | fast 5, slow 34 | value > 0 / < 0 (`kernel.ta.AwesomeOscillator`) |

**Filters** gate entries only (never exits): `none`; `vhf` (`VerticalHorizontalFilter`, passes at
`value >= filter_min`, `filter_params` `period` 28); `volatility_ratio` (`VolatilityRatio`, passes at
`value >= filter_min`, `filter_params` `fast_period` 10, `slow_period` 30). `filter_min` defaults to
0.0, which passes everything: set it for the filter to filter (VHF about 0.3, ratio about 1.0).

**Entry.** Flat, with no live order, every indicator (and the filter's) initialized, a nonzero
signal that `allow_short` permits and the filter passes: a market order in `on_bar` of the closed
bar. At most one position. **Exit.** `opposite` closes on the opposite signal (or the release);
`bars:<n>` closes after `n` bars held, in addition to the opposite-signal / release exit (an
extra exit, never a replacement). After a close the strategy is flat and re-enters on the next
bar that shows a signal.

**Holes** as in `CandlePatternStrategy`: a bar over one step after the previous, or with zero
volume, resets every indicator; a hole bar is fed to the fresh indicators and never enters; a
zero-volume bar is not fed. A `bars:<n>` exit still runs on such a bar (it needs no indicator).

Known limit: across a hole or zero-volume bar the opposite-signal and release exits are deferred,
because they read live indicators that were just reset; an open position waits for the first bar
with initialized indicators (a `bars:<n>` exit is not deferred). Upgrade path: close on a hole by
config.

Known limit: no protective stop and no sizing beyond a fixed `trade_size`; upgrade path:
`MACrossStrategy`'s ATR stop machinery lifted into `_bars.py` once a second strategy needs it.

Known limit: `donchian`'s channel includes the current bar, so its bounds are touched only by a
close at the bar's extreme; upgrade path: a lagged channel (feed the previous bar's bounds).
"""

import math
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from kernel.indicators import OnlineLogisticTrend
from kernel.ta import AverageDirectionalIndex
from kernel.ta import AwesomeOscillator
from kernel.ta import ChaikinMoneyFlow
from kernel.ta import MoneyFlowIndex
from kernel.ta import ParabolicSAR
from kernel.ta import Supertrend

from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.indicators import ArcherMovingAveragesTrends
from nautilus_trader.indicators import AroonOscillator
from nautilus_trader.indicators import Bias
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.indicators import ChandeMomentumOscillator
from nautilus_trader.indicators import CommodityChannelIndex
from nautilus_trader.indicators import DirectionalMovement
from nautilus_trader.indicators import DonchianChannel
from nautilus_trader.indicators import EfficiencyRatio
from nautilus_trader.indicators import FuzzyCandlesticks
from nautilus_trader.indicators import IchimokuCloud
from nautilus_trader.indicators import KeltnerChannel
from nautilus_trader.indicators import KlingerVolumeOscillator
from nautilus_trader.indicators import LinearRegression
from nautilus_trader.indicators import MovingAverageConvergenceDivergence
from nautilus_trader.indicators import OnBalanceVolume
from nautilus_trader.indicators import Pressure
from nautilus_trader.indicators import PsychologicalLine
from nautilus_trader.indicators import RateOfChange
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.indicators import RelativeVolatilityIndex
from nautilus_trader.indicators import Stochastics
from nautilus_trader.indicators import Swings
from nautilus_trader.indicators import VerticalHorizontalFilter
from nautilus_trader.indicators import VolatilityRatio
from nautilus_trader.indicators import VolumeWeightedAveragePrice
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.orders import Order
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy
from research.strategies._bars import BarSequence
from research.strategies._bars import check_count
from research.strategies._bars import check_trade_size
from research.strategies._bars import resolve_bar_type
from research.strategies._bars import size_problem


Params = dict[str, int | float]
Rule = Callable[[Any, Bar, Params], int]
Release = Callable[[Any, Bar, Params, int], bool]
Feed = Callable[[Any, Bar, int], None]  # (indicator, bar, bar step in ns)


def _sign(value: float) -> int:
    return 1 if value > 0 else -1 if value < 0 else 0


class _EfficiencyTrend:
    """
    `EfficiencyRatio` with the close change over its period for a direction (the ratio alone is
    non-directional). Invariant: `initialized` only once both have a full period; `reset` clears both.
    """

    def __init__(self, period: int) -> None:
        self.ratio = EfficiencyRatio(period)
        self.change = RateOfChange(period)

    @property
    def initialized(self) -> bool:
        return bool(self.ratio.initialized and self.change.initialized)

    def handle_bar(self, bar: Bar) -> None:
        self.ratio.handle_bar(bar)
        self.change.handle_bar(bar)

    def reset(self) -> None:
        self.ratio.reset()
        self.change.reset()


class _DailyVwap:
    """
    `VolumeWeightedAveragePrice` that reports `initialized` only from its `min_bars`-th bar of each
    UTC day (the first bar of a day is the VWAP of one bar, which is no information yet).
    Invariant: `bars` counts the bars fed since the last day change (or reset); `reset` clears it.
    """

    def __init__(self, min_bars: int) -> None:
        self.vwap = VolumeWeightedAveragePrice()
        self.min_bars = min_bars
        self.bars = 0
        self._day: Any = None

    @property
    def initialized(self) -> bool:
        return bool(self.vwap.initialized and self.bars >= self.min_bars)

    @property
    def value(self) -> float:
        return float(self.vwap.value)

    def update(self, price: float, volume: float, opened: Any) -> None:
        if opened.date() != self._day:
            self._day = opened.date()
            self.bars = 0
        self.bars += 1
        self.vwap.update_raw(price, volume, opened)

    def reset(self) -> None:
        self.vwap.reset()
        self.bars = 0
        self._day = None


@dataclass(frozen=True)
class _Signal:
    """
    One signal: its defaults (the closed set of `signal_params` keys), the indicator it builds from
    the merged params, how a bar feeds it (None: `handle_bar`), its +1/0/-1 rule and an optional
    release (a position of that side should close).

    Invariant: `build` receives every default key, overridden by the caller's value, cast to the
    default's type; the rule reads only a built, initialized indicator.
    """

    defaults: Mapping[str, int | float]
    build: Callable[[Params], Any]
    rule: Rule
    release: Release | None = None
    feed: Feed | None = None


def _band_rule(indicator: Any, bar: Bar, params: Params) -> int:
    close = bar.close.as_double()
    if close <= indicator.lower:
        return 1
    return -1 if close >= indicator.upper else 0


def _band_release(indicator: Any, bar: Bar, params: Params, side: int) -> bool:
    close = bar.close.as_double()
    return close >= indicator.middle if side > 0 else close <= indicator.middle


def _oscillator(read: Callable[[Any], float]) -> tuple[Rule, Release]:
    """Return the (rule, release) of an oscillator read by `read`: `low`/`high`/`neutral` params."""

    def rule(indicator: Any, bar: Bar, params: Params) -> int:
        value = read(indicator)
        if value <= params["low"]:
            return 1
        return -1 if value >= params["high"] else 0

    def release(indicator: Any, bar: Bar, params: Params, side: int) -> bool:
        value = read(indicator)
        return value >= params["neutral"] if side > 0 else value <= params["neutral"]

    return rule, release


def _signed(read: Callable[[Any], float]) -> Rule:
    """Return the rule `sign(read(indicator))`."""
    return lambda indicator, bar, params: _sign(read(indicator))


def _feed_swings(indicator: Swings, bar: Bar, step_ns: int) -> None:
    indicator.update_raw(bar.high.as_double(), bar.low.as_double(), unix_nanos_to_dt(bar.ts_event))


def _feed_vwap(indicator: "_DailyVwap", bar: Bar, step_ns: int) -> None:
    # The bar's OPEN time decides its UTC day: the bar closing at midnight belongs to the old day.
    typical = (bar.high.as_double() + bar.low.as_double() + bar.close.as_double()) / 3.0
    opened = unix_nanos_to_dt(bar.ts_event - step_ns)
    indicator.update(typical, bar.volume.as_double(), opened)


def _ichimoku_rule(indicator: IchimokuCloud, bar: Bar, params: Params) -> int:
    if indicator.senkou_span_a == 0.0 or indicator.senkou_span_b == 0.0:
        # `initialized` precedes the spans: they read 0.0 for `displacement` bars after warm-up
        # and after every reset, and a 0.0 cloud would make every close "above" it.
        return 0
    close = bar.close.as_double()
    top = max(indicator.senkou_span_a, indicator.senkou_span_b)
    bottom = min(indicator.senkou_span_a, indicator.senkou_span_b)
    return 1 if close > top else -1 if close < bottom else 0


def _efficiency_rule(indicator: _EfficiencyTrend, bar: Bar, params: Params) -> int:
    if indicator.ratio.value < params["threshold"]:
        return 0
    return _sign(indicator.change.value)


def _fuzzy_rule(indicator: FuzzyCandlesticks, bar: Bar, params: Params) -> int:
    candle = indicator.value
    if candle is None or int(candle.size.value) < params["min_size"]:
        return 0
    return _sign(int(candle.direction.value))


def _logistic_rule(indicator: OnlineLogisticTrend, bar: Bar, params: Params) -> int:
    if indicator.value > params["buy"]:
        return 1
    return -1 if indicator.value < params["sell"] else 0


def _archer_rule(indicator: ArcherMovingAveragesTrends, bar: Bar, params: Params) -> int:
    return 1 if indicator.long_run else -1 if indicator.short_run else 0


def _adx_rule(indicator: AverageDirectionalIndex, bar: Bar, params: Params) -> int:
    """Return the DI side when the trend is strong enough (ADX >= `adx_min`), else 0."""
    if indicator.adx < params["adx_min"]:
        return 0
    return _sign(indicator.plus_di - indicator.minus_di)


_RSI = _oscillator(lambda i: i.value)
_STOCH = _oscillator(lambda i: i.value_k)
_CCI = _oscillator(lambda i: i.value)
_CMO = _oscillator(lambda i: i.value)
_RVI = _oscillator(lambda i: i.value)
_PSL = _oscillator(lambda i: i.value)
_MFI = _oscillator(lambda i: i.value)


def _levels(period: int, low: float, high: float, neutral: float) -> Params:
    return {"period": period, "low": low, "high": high, "neutral": neutral}


_SIGNALS: Mapping[str, _Signal] = {
    "bollinger": _Signal(
        {"period": 20, "k": 2.0},
        lambda p: BollingerBands(p["period"], p["k"]),
        _band_rule,
        _band_release,
    ),
    "keltner": _Signal(
        {"period": 20, "k_multiplier": 2.0},
        lambda p: KeltnerChannel(p["period"], p["k_multiplier"]),
        _band_rule,
        _band_release,
    ),
    "donchian": _Signal(
        {"period": 20}, lambda p: DonchianChannel(p["period"]), _band_rule, _band_release
    ),
    "rsi": _Signal(_levels(14, 0.3, 0.7, 0.5), lambda p: RelativeStrengthIndex(p["period"]), *_RSI),
    "stochastics": _Signal(
        {"period_k": 14, "period_d": 3, "low": 20.0, "high": 80.0, "neutral": 50.0},
        lambda p: Stochastics(p["period_k"], p["period_d"]),
        *_STOCH,
    ),
    "cci": _Signal(
        _levels(20, -100.0, 100.0, 0.0), lambda p: CommodityChannelIndex(p["period"]), *_CCI
    ),
    "cmo": _Signal(
        _levels(14, -50.0, 50.0, 0.0), lambda p: ChandeMomentumOscillator(p["period"]), *_CMO
    ),
    "rvi": _Signal(
        _levels(14, 30.0, 70.0, 50.0), lambda p: RelativeVolatilityIndex(p["period"]), *_RVI
    ),
    "psl": _Signal(_levels(12, 25.0, 75.0, 50.0), lambda p: PsychologicalLine(p["period"]), *_PSL),
    "macd": _Signal(
        {"fast": 12, "slow": 26},
        lambda p: MovingAverageConvergenceDivergence(p["fast"], p["slow"]),
        _signed(lambda i: i.value),
    ),
    "aroon": _Signal(
        {"period": 25}, lambda p: AroonOscillator(p["period"]), _signed(lambda i: i.value)
    ),
    "directional_movement": _Signal(
        {"period": 14},
        lambda p: DirectionalMovement(p["period"]),
        _signed(lambda i: i.pos - i.neg),
    ),
    "ichimoku": _Signal(
        {"tenkan": 9, "kijun": 26, "senkou": 52, "displacement": 26},
        lambda p: IchimokuCloud(p["tenkan"], p["kijun"], p["senkou"], p["displacement"]),
        _ichimoku_rule,
    ),
    "linear_regression": _Signal(
        {"period": 20}, lambda p: LinearRegression(p["period"]), _signed(lambda i: i.slope)
    ),
    "bias": _Signal({"period": 20}, lambda p: Bias(p["period"]), _signed(lambda i: i.value)),
    "archer": _Signal(
        {"fast": 10, "slow": 20, "signal": 5},
        lambda p: ArcherMovingAveragesTrends(p["fast"], p["slow"], p["signal"]),
        _archer_rule,
    ),
    "roc": _Signal({"period": 10}, lambda p: RateOfChange(p["period"]), _signed(lambda i: i.value)),
    "efficiency_ratio": _Signal(
        {"period": 10, "threshold": 0.3},
        lambda p: _EfficiencyTrend(int(p["period"])),
        _efficiency_rule,
    ),
    "obv": _Signal(
        {"period": 20}, lambda p: OnBalanceVolume(p["period"]), _signed(lambda i: i.value)
    ),
    "vwap": _Signal(
        {"min_bars": 2},
        lambda p: _DailyVwap(int(p["min_bars"])),
        lambda i, bar, p: _sign(bar.close.as_double() - i.value),
        feed=_feed_vwap,
    ),
    "kvo": _Signal(
        {"fast": 12, "slow": 26, "signal": 9},
        lambda p: KlingerVolumeOscillator(p["fast"], p["slow"], p["signal"]),
        _signed(lambda i: i.value),
    ),
    "pressure": _Signal(
        {"period": 20}, lambda p: Pressure(p["period"]), _signed(lambda i: i.value)
    ),
    "fuzzy_candle": _Signal(
        {"period": 10, "min_size": 4}, lambda p: FuzzyCandlesticks(p["period"]), _fuzzy_rule
    ),
    "logistic_trend": _Signal(
        {"lookback": 5, "learning_rate": 0.05, "buy": 0.6, "sell": 0.4},
        lambda p: OnlineLogisticTrend(
            lookback=int(p["lookback"]), learning_rate=p["learning_rate"]
        ),
        _logistic_rule,
    ),
    "swings": _Signal(
        {"period": 10},
        lambda p: Swings(p["period"]),
        _signed(lambda i: i.direction),
        feed=_feed_swings,
    ),
    # Story 33.11: `kernel.ta`.
    "supertrend": _Signal(
        {"period": 10, "multiplier": 3.0},
        lambda p: Supertrend(int(p["period"]), p["multiplier"]),
        _signed(lambda i: i.direction),
    ),
    "parabolic_sar": _Signal(
        {"step": 0.02, "max_step": 0.2},
        lambda p: ParabolicSAR(p["step"], p["max_step"]),
        lambda i, bar, p: _sign(bar.close.as_double() - i.value),
    ),
    "adx": _Signal(
        {"period": 14, "adx_min": 25.0},
        lambda p: AverageDirectionalIndex(int(p["period"])),
        _adx_rule,
    ),
    "mfi": _Signal(
        _levels(14, 20.0, 80.0, 50.0), lambda p: MoneyFlowIndex(int(p["period"])), *_MFI
    ),
    "cmf": _Signal(
        {"period": 20}, lambda p: ChaikinMoneyFlow(int(p["period"])), _signed(lambda i: i.value)
    ),
    "awesome_oscillator": _Signal(
        {"fast": 5, "slow": 34},
        lambda p: AwesomeOscillator(int(p["fast"]), int(p["slow"])),
        _signed(lambda i: i.value),
    ),
}

FILTERS = ("none", "vhf", "volatility_ratio")
_FILTER_DEFAULTS: Mapping[str, Params] = {
    "none": {},
    "vhf": {"period": 28},
    "volatility_ratio": {"fast_period": 10, "slow_period": 30},
}


def _merged(owner: str, defaults: Mapping[str, int | float], given: Mapping[str, object]) -> Params:
    """Return `defaults` overridden by `given`, each cast to its default's type; unknown raises."""
    unknown = set(given) - set(defaults)
    if unknown:
        raise ValueError(f"{owner}: unknown params {sorted(unknown)}; valid: {sorted(defaults)}")
    merged: Params = dict(defaults)
    for key, value in given.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError(f"{owner}.{key} must be a finite number, got {value!r}")
        if isinstance(defaults[key], int) and value != int(value):
            raise ValueError(f"{owner}.{key} must be a whole number, got {value!r}")
        merged[key] = type(defaults[key])(value)
    return merged


def _check_thresholds(owner: str, params: Params) -> None:
    """Raise `ValueError` for an oscillator whose `low < neutral < high` or a `sell < buy` fails."""
    if {"low", "neutral", "high"} <= params.keys():
        low, neutral, high = params["low"], params["neutral"], params["high"]
        if not low < neutral < high:
            raise ValueError(
                f"{owner}: need low < neutral < high, got low={low}, neutral={neutral}, high={high}"
            )
    if {"buy", "sell"} <= params.keys() and not params["sell"] < params["buy"]:
        raise ValueError(
            f"{owner}: need sell < buy, got sell={params['sell']}, buy={params['buy']}"
        )


class IndicatorSignalStrategyConfig(StrategyConfig, frozen=True, forbid_unknown_fields=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
    bar_type : str | None
        A Nautilus time bar type of the instrument; None is `<instrument_id>-1-MINUTE-LAST-INTERNAL`.
    trade_size : Decimal
        Base-asset quantity per entry.
    signal : str
        A key of `_SIGNALS` (see the module docstring's table).
    signal_params : dict
        Overrides of that signal's defaults; an unknown key raises.
    filter : str
        `none`, `vhf` or `volatility_ratio`.
    filter_params : dict
        Overrides of that filter's constructor defaults.
    filter_min : float
        The filter's pass level (`value >= filter_min`).
    exit : str
        `opposite` (close on the opposite signal or the release) or `bars:<n>` (the same, plus a
        close after `n` bars held: an additional exit, not a replacement).
    allow_short : bool
        Whether a -1 signal opens a short; it closes a long either way.

    Unknown fields are rejected, so a typo'd parameter fails the build.
    """

    instrument_id: InstrumentId
    bar_type: str | None = None
    trade_size: Decimal = Decimal("0.01")
    signal: str = "logistic_trend"
    signal_params: dict = {}
    filter: str = "none"
    filter_params: dict = {}
    filter_min: float = 0.0
    exit: str = "opposite"
    allow_short: bool = True


def _exit_bars(exit_rule: str) -> int | None:
    """Return `n` for `bars:<n>`, None for `opposite`; `ValueError` otherwise."""
    if exit_rule == "opposite":
        return None
    name, _, count = exit_rule.partition(":")
    if name != "bars" or not count.isdigit():
        raise ValueError(f"exit must be 'opposite' or 'bars:<n>', not {exit_rule!r}")
    check_count("exit bars", int(count))
    return int(count)


def _build_filter(config: IndicatorSignalStrategyConfig) -> Any:
    params = _merged("filter_params", _FILTER_DEFAULTS[config.filter], config.filter_params)
    if config.filter == "vhf":
        return VerticalHorizontalFilter(params["period"])
    if config.filter == "volatility_ratio":
        return VolatilityRatio(params["fast_period"], params["slow_period"])
    return None


class IndicatorSignalStrategy(Strategy):
    """
    Trades one named indicator signal with an optional regime filter (rules in the module docstring).

    Invariant: at most one position and no entry while any order of this strategy is live; every
    indicator restarts at a hole, so no signal spans one. A bad config (unknown signal, filter,
    exit or parameter) raises `ValueError` here, before any node runs it.
    """

    def __init__(self, config: IndicatorSignalStrategyConfig) -> None:
        super().__init__(config)
        if config.signal not in _SIGNALS:
            raise ValueError(f"signal must be one of {sorted(_SIGNALS)}, not {config.signal!r}")
        if config.filter not in FILTERS:
            raise ValueError(f"filter must be one of {FILTERS}, not {config.filter!r}")
        check_trade_size(config.trade_size)
        self._bar_type = resolve_bar_type(config.instrument_id, config.bar_type)
        self._sequence = BarSequence(int(self._bar_type.spec.timedelta.value))
        self._exit_bars = _exit_bars(config.exit)
        self._spec = _SIGNALS[config.signal]
        self._params = _merged("signal_params", self._spec.defaults, config.signal_params)
        _check_thresholds("signal_params", self._params)
        self._indicator = self._spec.build(self._params)
        self._filter = _build_filter(config)
        self.instrument: Instrument | None = None

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.config.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.config.instrument_id}")
            self.stop()
            return
        problem = size_problem(Decimal(self.config.trade_size), instrument)
        if problem is not None:
            self.log.error(f"trade_size {self.config.trade_size} {problem}")
            self.stop()
            return
        self.instrument = instrument
        self.subscribe_bars(self._bar_type)

    def on_bar(self, bar: Bar) -> None:
        if not self._sequence.in_order(bar):
            self.log.warning(
                f"{bar.bar_type}: bar at {bar.ts_event} is not after the last, skipped"
            )
            return
        traded = bar.volume.raw > 0
        hole = self._sequence.is_hole(bar)
        if hole or not traded:
            self._reset_indicators()
            position = self._open_position()
            if position is not None:
                self._manage_bars_exit(position, bar)
        if not traded:
            return
        self._feed(bar)
        if hole or not self._initialized():
            return
        side = self._spec.rule(self._indicator, bar, self._params)
        position = self._open_position()
        if position is not None:
            self._manage(position, bar, side)
        elif side and not self._live_orders():
            self._maybe_enter(side)

    def _feed(self, bar: Bar) -> None:
        if self._spec.feed is not None:
            self._spec.feed(self._indicator, bar, self._sequence.step_ns)
        else:
            self._indicator.handle_bar(bar)
        if self._filter is not None:
            self._filter.handle_bar(bar)

    def _initialized(self) -> bool:
        return bool(
            self._indicator.initialized and (self._filter is None or self._filter.initialized)
        )

    def _reset_indicators(self) -> None:
        self._indicator.reset()
        if self._filter is not None:
            self._filter.reset()

    def _filter_passes(self) -> bool:
        return self._filter is None or self._filter.value >= self.config.filter_min

    def _open_position(self) -> Position | None:
        positions = self.cache.positions_open(
            instrument_id=self.config.instrument_id, strategy_id=self.id
        )
        return positions[0] if positions else None

    def _live_orders(self) -> list[Order]:
        iid = self.config.instrument_id
        open_orders = self.cache.orders_open(instrument_id=iid, strategy_id=self.id)
        inflight = self.cache.orders_inflight(instrument_id=iid, strategy_id=self.id)
        return list({o.client_order_id: o for o in (*open_orders, *inflight)}.values())

    def _manage(self, position: Position, bar: Bar, side: int) -> None:
        if self._live_orders():
            return  # a close is already working
        held = 1 if position.side == PositionSide.LONG else -1
        release = self._spec.release
        done = side == -held or (
            release is not None and release(self._indicator, bar, self._params, held)
        )
        if done:
            self.close_position(position)
        else:
            self._manage_bars_exit(position, bar)

    def _manage_bars_exit(self, position: Position, bar: Bar) -> None:
        """Close `position` once `bars:<n>` bars have been held; needs no indicator."""
        if self._exit_bars is None or self._live_orders():
            return
        held = -(-(bar.ts_event - position.ts_opened) // self._sequence.step_ns)
        if held >= self._exit_bars:
            self.close_position(position)

    def _maybe_enter(self, side: int) -> None:
        if (side < 0 and not self.config.allow_short) or not self._filter_passes():
            return
        assert self.instrument is not None  # on_start stopped the strategy otherwise
        self.submit_order(
            self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY if side > 0 else OrderSide.SELL,
                quantity=self.instrument.make_qty(self.config.trade_size),
            )
        )

    def on_reset(self) -> None:
        self._reset_indicators()
        self._sequence.last_ns = None
        self.instrument = None
