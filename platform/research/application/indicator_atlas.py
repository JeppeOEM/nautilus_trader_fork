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
The indicator atlas service, behind `research/notebooks/07_indicator_atlas`: every bar indicator of
`nautilus_trader.indicators` (`catalog`), replayed over the candle store's bars on the complete
bucket grid (`replay`), plus the platform's snapshot indicators over the 1 s frames
(`snapshot_indicators`) and the fuzzy-candle vector (`fuzzy_frame`). It computes no indicator of
its own: every value is read off the Nautilus (or `kernel.indicators`) object that produced it, and
a notebook only draws what these functions return.

Gaps stay visible (DATA-01), the `research.application.patterns` way: a bucket the collector never
observed, an untraded one or a `partial` bar is a NaN row of the grid; every indicator is reset at
such a row and reads NaN until it is initialized again, so no value is carried across a hole and no
window ever spans one.

`docs/NAUTILUS_INDICATOR_BACKTEST_CATALOG.md` section 1 is the reference this table implements:
the constructors, the `update_raw` inputs and the outputs below are the ones it lists.

Known limit: every size is a fixed default (or an override of `catalog`'s `periods`), not a
fitted one; the atlas draws what an indicator reads, it never tunes it. Upgrade path: the
`IndicatorSignalStrategy` sweep in `04_backtest_evaluation` for the sizes that pay.
"""

import enum
import math
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import Any

import numpy as np
import pandas as pd
from kernel.clocks import NS_PER_MS
from kernel.clocks import NS_PER_S
from kernel.indicators import OFI_GAP_NS
from kernel.indicators import Microprice
from kernel.indicators import MultiLevelOBI
from kernel.indicators import MultiLevelOFI
from kernel.indicators import OrderFlowImbalance

from nautilus_trader.core.datetime import unix_nanos_to_dt
from nautilus_trader.indicators import AdaptiveMovingAverage
from nautilus_trader.indicators import ArcherMovingAveragesTrends
from nautilus_trader.indicators import AroonOscillator
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.indicators import Bias
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.indicators import ChandeMomentumOscillator
from nautilus_trader.indicators import CommodityChannelIndex
from nautilus_trader.indicators import DirectionalMovement
from nautilus_trader.indicators import DonchianChannel
from nautilus_trader.indicators import DoubleExponentialMovingAverage
from nautilus_trader.indicators import EfficiencyRatio
from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.indicators import FuzzyCandlesticks
from nautilus_trader.indicators import HullMovingAverage
from nautilus_trader.indicators import IchimokuCloud
from nautilus_trader.indicators import Indicator
from nautilus_trader.indicators import KeltnerChannel
from nautilus_trader.indicators import KeltnerPosition
from nautilus_trader.indicators import KlingerVolumeOscillator
from nautilus_trader.indicators import LinearRegression
from nautilus_trader.indicators import MovingAverageConvergenceDivergence
from nautilus_trader.indicators import OnBalanceVolume
from nautilus_trader.indicators import Pressure
from nautilus_trader.indicators import PsychologicalLine
from nautilus_trader.indicators import RateOfChange
from nautilus_trader.indicators import RelativeStrengthIndex
from nautilus_trader.indicators import RelativeVolatilityIndex
from nautilus_trader.indicators import SimpleMovingAverage
from nautilus_trader.indicators import Stochastics
from nautilus_trader.indicators import Swings
from nautilus_trader.indicators import VariableIndexDynamicAverage
from nautilus_trader.indicators import VerticalHorizontalFilter
from nautilus_trader.indicators import VolatilityRatio
from nautilus_trader.indicators import VolumeWeightedAveragePrice
from nautilus_trader.indicators import WeightedMovingAverage
from nautilus_trader.indicators import WilderMovingAverage
from research.application import patterns
from research.application.ports import MarketFrames


OVERLAY = "overlay"
PANE = "pane"
FUZZY_NAME = "FuzzyCandlesticks"
FUZZY_COLUMNS = ("direction", "size", "body", "upper_wick", "lower_wick")
PANE_HEIGHT = 220
# The grid fields an indicator's `update_raw` can take, in `IndicatorSpec.inputs`: the bar's
# open/high/low/close/volume, its time (a `datetime`) and its typical price (high + low + close) / 3.
INPUT_FIELDS = ("o", "h", "l", "c", "v", "t", "typical")
SNAPSHOT_COLUMNS = ("mid", "microprice", "ofi_top", "ofi_levels", "obi_levels")

_HLC = ("h", "l", "c")
_HLCV = ("h", "l", "c", "v")
_CLOSE = ("c",)


@dataclass(frozen=True)
class IndicatorSpec:
    """
    One bar indicator of the atlas.

    Invariant: `build()` returns a fresh, uninitialized indicator of the same sizes every call;
    `update_raw` takes exactly `inputs` (names from `INPUT_FIELDS`) in that order; `outputs` are
    attribute paths (dotted for a nested value, e.g. `value.direction`) that read as a number once
    the indicator is `initialized`; `placement` says whether the outputs share the price axis
    (`overlay`) or need their own scale (`pane`).
    """

    name: str
    family: str
    placement: str
    build: Callable[[], Indicator]
    outputs: tuple[str, ...]
    inputs: tuple[str, ...]


class Sizer:
    """
    Resolve one indicator's sizes from the caller's override map.

    Invariant: a size is `periods["<owner>.<key>"]`, else `periods["<key>"]`, else the default
    given at the call; an int size must be a whole number (else `ValueError`), so a fractional
    override never silently truncates. Known limit: a key no indicator reads is ignored, because the
    gallery shares one map with different keys; upgrade path: a per-service key registry.
    """

    def __init__(self, periods: Mapping[str, float], owner: str) -> None:
        self._periods = periods
        self._owner = owner

    def _lookup(self, key: str, default: float) -> float:
        return self._periods.get(f"{self._owner}.{key}", self._periods.get(key, default))

    def _number(self, key: str, default: float) -> float:
        value = self._lookup(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ValueError(f"{self._owner}.{key} must be a finite number, got {value!r}")
        return value

    def i(self, key: str, default: int) -> int:
        value = self._number(key, default)
        if value != int(value):
            raise ValueError(f"{self._owner}.{key} must be a whole number, got {value!r}")
        return int(value)

    def f(self, key: str, default: float) -> float:
        return float(self._number(key, default))


Build = Callable[[Sizer], Indicator]


@dataclass(frozen=True)
class _Entry:
    name: str
    family: str
    placement: str
    build: Build
    outputs: tuple[str, ...]
    inputs: tuple[str, ...]


def _sized(ctor: Callable[[int], Indicator], key: str, default: int, sizer: Sizer) -> Indicator:
    """Build `ctor(size)` with the one size `key` resolves to (the single-period indicators)."""
    return ctor(sizer.i(key, default))


def _moving_averages() -> list[_Entry]:
    plain: list[tuple[str, Callable[[int], Indicator]]] = [
        ("SimpleMovingAverage", SimpleMovingAverage),
        ("ExponentialMovingAverage", ExponentialMovingAverage),
        ("DoubleExponentialMovingAverage", DoubleExponentialMovingAverage),
        ("WilderMovingAverage", WilderMovingAverage),
        ("HullMovingAverage", HullMovingAverage),
        ("WeightedMovingAverage", WeightedMovingAverage),
        ("VariableIndexDynamicAverage", VariableIndexDynamicAverage),
    ]
    entries = [
        _Entry(
            name, "moving average", OVERLAY, partial(_sized, ctor, "period", 20), ("value",), _CLOSE
        )
        for name, ctor in plain
    ]
    # `MovingAverageFactory` has no ADAPTIVE branch (returns None), so it is built explicitly.
    entries.append(
        _Entry(
            "AdaptiveMovingAverage",
            "moving average",
            OVERLAY,
            lambda s: AdaptiveMovingAverage(
                s.i("period", 10), s.i("alpha_fast", 2), s.i("alpha_slow", 30)
            ),
            ("value",),
            _CLOSE,
        )
    )
    return entries


def _momentum() -> list[_Entry]:
    def one(
        name: str, ctor: Callable[[int], Indicator], default: int, outputs: tuple[str, ...]
    ) -> _Entry:
        return _Entry(
            name, "momentum", PANE, partial(_sized, ctor, "period", default), outputs, _CLOSE
        )

    return [
        one("RelativeStrengthIndex", RelativeStrengthIndex, 14, ("value",)),
        one("RateOfChange", RateOfChange, 10, ("value",)),
        one("ChandeMomentumOscillator", ChandeMomentumOscillator, 14, ("value",)),
        _Entry(
            "Stochastics",
            "momentum",
            PANE,
            lambda s: Stochastics(s.i("period_k", 14), s.i("period_d", 3)),
            ("value_k", "value_d"),
            _HLC,
        ),
        _Entry(
            "CommodityChannelIndex",
            "momentum",
            PANE,
            lambda s: CommodityChannelIndex(s.i("period", 20)),
            ("value",),
            _HLC,
        ),
        one("EfficiencyRatio", EfficiencyRatio, 10, ("value",)),
        one("RelativeVolatilityIndex", RelativeVolatilityIndex, 14, ("value",)),
        one("PsychologicalLine", PsychologicalLine, 12, ("value",)),
    ]


def _trend() -> list[_Entry]:
    return [
        _Entry(
            "ArcherMovingAveragesTrends",
            "trend",
            PANE,
            lambda s: ArcherMovingAveragesTrends(
                s.i("fast", 10), s.i("slow", 20), s.i("signal", 5)
            ),
            ("long_run", "short_run"),
            _CLOSE,
        ),
        _Entry(
            "AroonOscillator",
            "trend",
            PANE,
            lambda s: AroonOscillator(s.i("period", 25)),
            ("aroon_up", "aroon_down", "value"),
            ("h", "l"),
        ),
        _Entry(
            "DirectionalMovement",
            "trend",
            PANE,
            lambda s: DirectionalMovement(s.i("period", 14)),
            ("pos", "neg", "value"),
            ("h", "l"),
        ),
        _Entry(
            "MovingAverageConvergenceDivergence",
            "trend",
            PANE,
            lambda s: MovingAverageConvergenceDivergence(s.i("fast", 12), s.i("slow", 26)),
            ("value",),
            _CLOSE,
        ),
        _Entry(
            "IchimokuCloud",
            "trend",
            OVERLAY,
            lambda s: IchimokuCloud(
                s.i("tenkan", 9), s.i("kijun", 26), s.i("senkou", 52), s.i("displacement", 26)
            ),
            ("tenkan_sen", "kijun_sen", "senkou_span_a", "senkou_span_b", "chikou_span"),
            _HLC,
        ),
        _Entry(
            "LinearRegression",
            "trend",
            OVERLAY,
            lambda s: LinearRegression(s.i("period", 20)),
            ("value",),
            _CLOSE,
        ),
        _Entry("Bias", "trend", PANE, lambda s: Bias(s.i("period", 20)), ("value",), _CLOSE),
        _Entry(
            "Swings",
            "trend",
            OVERLAY,
            lambda s: Swings(s.i("period", 10)),
            ("direction", "changed", "high_price", "low_price"),
            ("h", "l", "t"),
        ),
    ]


def _volatility() -> list[_Entry]:
    def bands(name: str, build: Build, inputs: tuple[str, ...]) -> _Entry:
        return _Entry(name, "volatility", OVERLAY, build, ("upper", "middle", "lower"), inputs)

    return [
        _Entry(
            "AverageTrueRange",
            "volatility",
            PANE,
            lambda s: AverageTrueRange(s.i("period", 14)),
            ("value",),
            _HLC,
        ),
        bands(
            "BollingerBands",
            lambda s: BollingerBands(s.i("period", 20), s.f("k", 2.0)),
            _HLC,
        ),
        bands("DonchianChannel", lambda s: DonchianChannel(s.i("period", 20)), ("h", "l")),
        bands(
            "KeltnerChannel",
            lambda s: KeltnerChannel(s.i("period", 20), s.f("k_multiplier", 2.0)),
            _HLC,
        ),
        _Entry(
            "KeltnerPosition",
            "volatility",
            PANE,
            lambda s: KeltnerPosition(s.i("period", 20), s.f("k_multiplier", 2.0)),
            ("value",),
            _HLC,
        ),
        _Entry(
            "VerticalHorizontalFilter",
            "volatility",
            PANE,
            lambda s: VerticalHorizontalFilter(s.i("period", 28)),
            ("value",),
            _CLOSE,
        ),
        _Entry(
            "VolatilityRatio",
            "volatility",
            PANE,
            lambda s: VolatilityRatio(s.i("fast", 10), s.i("slow", 30)),
            ("value",),
            _HLC,
        ),
    ]


def _volume() -> list[_Entry]:
    return [
        _Entry(
            "OnBalanceVolume",
            "volume",
            PANE,
            lambda s: OnBalanceVolume(s.i("period", 20)),
            ("value",),
            ("o", "c", "v"),
        ),
        # The typical price, as `IndicatorSignalStrategy`'s `vwap` feeds it.
        _Entry(
            "VolumeWeightedAveragePrice",
            "volume",
            OVERLAY,
            lambda s: VolumeWeightedAveragePrice(),
            ("value",),
            ("typical", "v", "t"),
        ),
        _Entry(
            "KlingerVolumeOscillator",
            "volume",
            PANE,
            lambda s: KlingerVolumeOscillator(s.i("fast", 12), s.i("slow", 26), s.i("signal", 9)),
            ("value",),
            _HLCV,
        ),
        _Entry(
            "Pressure",
            "volume",
            PANE,
            lambda s: Pressure(s.i("period", 20)),
            ("value", "value_cumulative"),
            _HLCV,
        ),
    ]


def _fuzzy() -> list[_Entry]:
    return [
        _Entry(
            FUZZY_NAME,
            "candles",
            PANE,
            lambda s: FuzzyCandlesticks(s.i("period", 10)),
            (
                "value.direction",
                "value.size",
                "value.body_size",
                "value.upper_wick_size",
                "value.lower_wick_size",
            ),
            ("o", "h", "l", "c"),
        )
    ]


def _entries() -> list[_Entry]:
    return [*_moving_averages(), *_momentum(), *_trend(), *_volatility(), *_volume(), *_fuzzy()]


def catalog(periods: Mapping[str, float] | None = None) -> list[IndicatorSpec]:
    """
    Return one `IndicatorSpec` per bar indicator of `nautilus_trader.indicators` (all eight moving
    averages, the momentum, trend, volatility and volume classes and `FuzzyCandlesticks`), in
    family order.

    Invariant: every spec is built once here, so a size Nautilus refuses (a slow period not above
    the fast, an Ichimoku with a shorter Kijun than Tenkan) raises `ValueError` at the call, never
    mid-replay; `periods` overrides sizes by `"<Indicator>.<key>"` or the generic `"<key>"`
    (`period`, `fast`, `slow`, `signal`, `k`, `k_multiplier`, ...) and an empty map is the sizes
    for the real archive.
    """
    given = periods or {}
    specs = [
        IndicatorSpec(
            e.name,
            e.family,
            e.placement,
            partial(e.build, Sizer(given, e.name)),
            e.outputs,
            e.inputs,
        )
        for e in _entries()
    ]
    for spec in specs:
        spec.build()
    return specs


def families(specs: Sequence[IndicatorSpec]) -> dict[str, list[IndicatorSpec]]:
    """
    Group `specs` by family, keeping the order of first appearance in both the families and the
    specs inside each. Invariant: every spec is in exactly one group; nothing is dropped.
    """
    grouped: dict[str, list[IndicatorSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.family, []).append(spec)
    return grouped


def count_present(values: pd.Series) -> int:
    """Return how many of `values` are not NaN (the bars or readings that exist, never a fill)."""
    return int(values.notna().sum())


def pane_figure_height(panes: int) -> int:
    """Return the pixel height of a figure of `panes` stacked panes (the price pane counts)."""
    return PANE_HEIGHT * max(panes, 1)


def bar_grid_with_volume(
    frames: MarketFrames, instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
) -> pd.DataFrame:
    """
    Return `patterns.bar_grid` with a `v` (volume) column on the same rows.

    Invariant: `v` is NaN exactly where the row's `o`/`h`/`l`/`c` are (a hole or a `partial` bar),
    never filled; the volume comes from the same stored bars, read span by span over
    `bar_coverage` like the prices.
    """
    grid = patterns.bar_grid(frames, instrument_id, bar_seconds, start_ns, end_ns)
    volume = np.full(len(grid), np.nan)
    if len(grid):
        bar_ns = bar_seconds * NS_PER_S
        first = int(grid["ts_ns"].iloc[0])
        for lo, hi in frames.bar_coverage(instrument_id, bar_seconds, start=start_ns, end=end_ns):
            bars = frames.bars(instrument_id, bar_seconds, start=lo, end=hi)
            rows = (bars["t"].to_numpy(dtype=np.int64) * NS_PER_MS - first) // bar_ns
            kept = ~bars["partial"].to_numpy(dtype=bool)
            volume[rows[kept]] = bars.loc[kept, "v"].to_numpy(dtype=float)
    return grid.assign(v=volume)


def _read(indicator: Indicator, path: str) -> float:
    value: Any = indicator
    for part in path.split("."):
        value = getattr(value, part)
    if isinstance(value, enum.Enum):
        value = value.value
    return float(value)


def _input_columns(grid: pd.DataFrame, needs_volume: bool) -> dict[str, Sequence[Any]]:
    high, low, close = (grid[k].to_numpy(dtype=float) for k in ("h", "l", "c"))
    columns: dict[str, Sequence[Any]] = {
        "o": grid["o"].tolist(),
        "h": high.tolist(),
        "l": low.tolist(),
        "c": close.tolist(),
        "typical": ((high + low + close) / 3.0).tolist(),
        "t": [unix_nanos_to_dt(int(ns)) for ns in grid["ts_ns"]],
    }
    if needs_volume:
        columns["v"] = grid["v"].tolist()
    return columns


def _usable(grid: pd.DataFrame, spec: IndicatorSpec) -> np.ndarray:
    """Return the rows a spec may be fed: a complete bar, and a traded one if it reads volume."""
    mask = grid[["o", "h", "l", "c"]].notna().all(axis=1).to_numpy()
    if "v" in spec.inputs:
        volume = grid["v"].to_numpy(dtype=float)
        mask = mask & (volume > 0)  # NaN > 0 is False: a hole stays unusable
    return mask


# Outputs that read 0.0 after `initialized` (the upstream displacement lag: `IchimokuCloud` reports
# `initialized` before its spans and chikou exist), so a 0.0 there is "not yet", never a price.
_ZERO_WHILE_DISPLACED = {"IchimokuCloud": ("senkou_span_a", "senkou_span_b", "chikou_span")}


def _replay_one(
    grid: pd.DataFrame, spec: IndicatorSpec, columns: Mapping[str, Sequence[Any]]
) -> pd.DataFrame:
    usable = _usable(grid, spec)
    indicator = spec.build()
    values = np.full((len(grid), len(spec.outputs)), np.nan)
    for i, row in enumerate(zip(*(columns[name] for name in spec.inputs), strict=True)):
        if not usable[i]:
            indicator.reset()
            continue
        indicator.update_raw(*row)
        if indicator.initialized:
            values[i] = [_read(indicator, path) for path in spec.outputs]
    names = [path.removeprefix("value.") if "." in path else path for path in spec.outputs]
    frame = pd.DataFrame(values, columns=names, index=pd.Index(grid["timestamp"], name="timestamp"))
    for name in _ZERO_WHILE_DISPLACED.get(spec.name, ()):
        frame[name] = frame[name].mask(frame[name] == 0.0)
    return frame


def replay(grid: pd.DataFrame, specs: Sequence[IndicatorSpec]) -> dict[str, pd.DataFrame]:
    """
    Replay every spec over `grid` (`bar_grid_with_volume`'s columns; `v` only if a spec reads it)
    and return one frame per spec name, indexed by the grid's `timestamp`, one column per output.

    Invariant: one row per grid row; an indicator is reset at every unusable row (a NaN bar, or a
    zero-volume one for a spec that reads volume) and its outputs are NaN there and until it is
    `initialized` again -- never the last value, never a placeholder. `Swings` and
    `VolumeWeightedAveragePrice` get each bar's open time as their `datetime`. The input columns
    are built once for all specs. `IchimokuCloud`'s `senkou_span_a`, `senkou_span_b` and
    `chikou_span` are NaN while they read 0.0 (they stay 0.0 for `displacement` bars after
    `initialized`, the upstream lag).
    Known limit: a volume spec treats a zero-volume bar as a hole (VWAP divides by volume), as
    `IndicatorSignalStrategy` does; upgrade path: feed it and let the indicator define the value.
    """
    columns = _input_columns(grid, any("v" in spec.inputs for spec in specs))
    return {spec.name: _replay_one(grid, spec, columns) for spec in specs}


def fuzzy_frame(replayed: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    """
    Return the fuzzy-candle vector per bar from `replay`'s result: `FUZZY_COLUMNS` (direction, size,
    body, upper wick, lower wick) as the enums' integer values.

    Invariant: nullable ints, `<NA>` exactly where the replay read NaN (before initialization or at
    a hole); a `replayed` without `FuzzyCandlesticks` raises `KeyError`, never an empty frame.
    """
    frame = replayed[FUZZY_NAME]
    return pd.DataFrame(
        {
            name: frame[column].astype("Int64")
            for name, column in zip(FUZZY_COLUMNS, frame.columns, strict=True)
        }
    )


def swing_points(swings: pd.DataFrame) -> pd.DataFrame:
    """
    Return one row per new swing high or low of `replay`'s `Swings` frame: `timestamp`, `price`,
    `kind` (`high`/`low`), oldest first.

    Invariant: a point is a bar where `Swings` reports a swing price it did not report on the bar
    before (NaN before it, at a hole or until initialized: no point), so a held price is drawn
    once, at the bar that set it.
    """
    points = []
    for kind in ("high", "low"):
        price = swings[f"{kind}_price"]
        new = price.notna() & (price != price.shift())
        points.append(
            pd.DataFrame(
                {"timestamp": price.index[new], "price": price[new].to_numpy(), "kind": kind}
            )
        )
    return pd.concat(points).sort_values("timestamp", kind="stable").reset_index(drop=True)


class _Trackers:
    """
    The four snapshot indicators of one instrument's replay.

    Invariant: they all see the same rows in order; `gap()` drops the previous book of both OFI
    trackers together, so neither diffs a post-gap book against a pre-gap one.
    """

    def __init__(self, levels: int, window: int) -> None:
        self.micro = Microprice()
        self.top_ofi = OrderFlowImbalance(window)
        self.deep_ofi = MultiLevelOFI(levels, window)
        self.deep_obi = MultiLevelOBI(levels)

    def gap(self) -> None:
        self.deep_ofi.clear_prev_state()
        # Known limit: `OrderFlowImbalance` has no `clear_prev_state`, so it is reset and also loses
        # its window; upgrade path: the same method on the kernel class.
        self.top_ofi.reset()

    def feed(self, row: tuple[Any, ...], baseline: bool) -> tuple[float, float, float, float]:
        """Feed one two-sided row; a baseline row reads NaN OFI (no previous book to diff)."""
        _, bid_p, bid_s, ask_p, ask_s = row
        top = (float(bid_p[0]), float(bid_s[0]), float(ask_p[0]), float(ask_s[0]))
        self.micro.update_raw(*top)
        self.top_ofi.update_raw(*top)
        self.deep_ofi.update_raw(bid_p, bid_s, ask_p, ask_s)
        self.deep_obi.update_raw(bid_s, ask_s)
        micro = self.micro.value if self.micro.initialized else math.nan
        if baseline:
            return micro, math.nan, math.nan, self.deep_obi.value
        return micro, self.top_ofi.value, self.deep_ofi.value, self.deep_obi.value


def snapshot_indicators(seconds: pd.DataFrame, levels: int, window: int) -> pd.DataFrame:
    """
    Return `SNAPSHOT_COLUMNS` per second of `CatalogFrames.seconds`: the second's `mid`, then
    `Microprice`, top-of-book `OrderFlowImbalance`, `MultiLevelOFI` over `levels` and
    `MultiLevelOBI` over `levels` (`kernel.indicators`; OFI windows of `window` readings).

    Invariant: the rows are `seconds`' rows in order; a row with an empty side is skipped (NaN); after
    a gap over `kernel.indicators.OFI_GAP_NS` the OFI trackers lose their previous book and the
    row that only sets the new baseline reads NaN OFI, never the carried value -- the rule
    `microstructure.ofi_readings` and the live ranking share.
    """
    trackers = _Trackers(levels, window)
    out = np.full((len(seconds), 4), np.nan)
    last_ts: int | None = None
    names = ("ts_event", "bid_prices", "bid_sizes", "ask_prices", "ask_sizes")
    for position, row in enumerate(zip(*(seconds[n].tolist() for n in names), strict=True)):
        ts, bid_p, _, ask_p, _ = row
        if not len(bid_p) or not len(ask_p):
            continue
        gap = last_ts is not None and ts - last_ts > OFI_GAP_NS
        if gap:
            trackers.gap()
        out[position] = trackers.feed(row, last_ts is None or gap)
        last_ts = ts
    return pd.DataFrame(
        {
            "mid": seconds["mid"].to_numpy(dtype=float),
            **dict(zip(SNAPSHOT_COLUMNS[1:], out.T, strict=True)),
        },
        index=seconds.index,
    )
