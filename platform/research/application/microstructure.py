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
Microstructure (Story 27.3): the frames `research/notebooks/02_microstructure` plots, per
instrument over one bounded window -- every number it shows comes from here, from
`kernel.indicators` (spread, mid, microprice, OBI, OFI, depth, `RollingZScore`) and from
`research.domain.microstructure` (autocorrelation, signature, realised volatility, impact, hit
rate).

Each function takes what a bounded `MarketFrames` read returned (SIGNAL-01: the stored book and
trade columns; everything else is derived here on read). Every per-second series lives on the 1 s
grid of the window (`inspection.second_grid`): a missing second, a second holding two rows and --
for a book-derived value -- a crossed second is NaN, and nothing is filled or interpolated
(DATA-01). Nothing reads market data itself except `read_instrument`, through the port with the
window's `start`/`end` (MEM-01).
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from kernel.clocks import NS_PER_S
from kernel.indicators import OFI_GAP_NS
from kernel.indicators import MultiLevelOFI
from kernel.indicators import RollingZScore
from kernel.indicators import cumulative_depth
from kernel.indicators import depth_within_bps
from kernel.indicators import snapshot_depth
from kernel.indicators import volume_delta
from kernel.second_snapshot import BOOK_DEPTH

from nautilus_trader.model.instruments import Instrument
from research.application.frames import OBI_LEVELS
from research.application.inspection import BOOK_COLUMNS
from research.application.inspection import second_grid
from research.application.ports import MarketFrames
from research.domain.microstructure import autocorrelation
from research.domain.microstructure import hit_rate_by_bin
from research.domain.microstructure import price_impact
from research.domain.microstructure import realised_volatility
from research.domain.microstructure import volatility_signature
from research.domain.returns import ReturnSeries
from research.strategies.ofi_strategy import OFIStrategyConfig


# The snapshot's stored depth: every level `CatalogFrames.seconds` can return.
DEPTH_LEVELS = BOOK_DEPTH
# Distances from mid (basis points) the depth profile is cumulated to.
DEPTH_BPS = (0.5, 1.0, 2.0, 5.0, 10.0, 25.0)
# Return horizons (s) the autocorrelation is taken at, and the lags (in horizon periods).
ACF_HORIZONS_S = (1, 10, 60, 300, 3600)
ACF_LAGS = (1, 2, 3, 5, 10)
# Sampling intervals (s) of the volatility signature plot.
SIGNATURE_INTERVALS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600)
# Trailing 1 s returns in each rolling realised-volatility point (5 minutes).
RV_WINDOW = 300
# A microprice this close to the mid (in ulps of the mid) is the mid, rounded differently.
# Known limit (DATA_DICTIONARY §2.12, audit D-89): a genuine lean this small (2.5e-12 on a 13 437 mid)
# reads flat too -- two independently rounded floats cannot tell it from rounding. Upgrade path:
# judge the lean on the exact decimals (`DydxSecondSnapshot.exact`) instead of the floats.
_ROUNDING_ULPS = 4
# Quantile bins of the binned scatters (microprice edge, price impact).
BINS = 10

GRID_COLUMNS = (
    "mid",
    "spread",
    "microprice",
    *(f"obi_{n}" for n in OBI_LEVELS),
    "buy_volume",
    "sell_volume",
    "buy_count",
    "sell_count",
    "volume_delta",
)
OFI_COLUMNS = ("ofi", "ofi_z")
DEPTH_LEVEL_COLUMNS = ("level", "bid", "ask", "bid_snapshots", "ask_snapshots")
DEPTH_BPS_COLUMNS = ("bps", "bid", "ask", "bid_snapshots", "ask_snapshots")
ACF_COLUMNS = ("horizon_s", "lag", "lag_s", "rho", "pairs")
SIGNATURE_COLUMNS = ("interval_s", "variance_per_second", "n")
IMPACT_COLUMNS = ("lower", "upper", "slope", "intercept", "n")
HIT_RATE_COLUMNS = (
    "lower",
    "upper",
    "n",
    "flat",
    "rate",
    "mean_predictor",
    "mean_outcome",
)


def tick_size(definitions: dict[str, Instrument], instrument_id: str) -> float | None:
    """
    Return the price increment, or None without a definition (spread in ticks is NaN).

    Known limit: the catalog's current definition is applied to the whole window, so a window
    before a venue's tick-size change reads its spread in the new ticks (bps are unaffected);
    upgrade path: the definition in force at each second, from the instrument's dated history.
    """
    instrument = definitions.get(instrument_id)
    return None if instrument is None else instrument.price_increment.as_double()


def _with_volume_delta(seconds: pd.DataFrame) -> pd.DataFrame:
    """Return the seconds frame plus each row's `kernel.indicators.volume_delta`."""
    names = ("buy_volume", "sell_volume")
    trades = zip(*(seconds[name].tolist() for name in names), strict=True)
    deltas = [volume_delta(dict(zip(names, row, strict=True))) for row in trades]
    return seconds.assign(volume_delta=np.asarray(deltas, dtype="float64"))


def grid(seconds: pd.DataFrame, start_ns: int, end_ns: int) -> pd.DataFrame:
    """
    `GRID_COLUMNS` on the window's 1 s grid (`inspection.second_grid`): the stored trade columns,
    `kernel.indicators`' `mid`/`spread`/`microprice`/`obi_<N>` and `volume_delta`. Invariant: the
    book columns are NaN on a crossed second, every column on a missing or shared second.
    """
    return second_grid(_with_volume_delta(seconds), start_ns, end_ns, GRID_COLUMNS)


def spread_frame(frame: pd.DataFrame, tick: float | None) -> pd.DataFrame:
    """
    Return the spread in ticks (`spread / tick`, NaN without a tick size) and in basis points of mid
    (`spread / mid * 1e4`), on the grid's index; NaN wherever the grid's spread is.
    """
    spread = frame["spread"].to_numpy(dtype="float64")
    ticks = spread / tick if tick else np.full(len(spread), math.nan)
    bps = spread / frame["mid"].to_numpy(dtype="float64") * 1e4
    return pd.DataFrame({"spread_ticks": ticks, "spread_bps": bps}, index=frame.index)


def by_hour_utc(series: pd.Series) -> pd.DataFrame:
    """
    Each value with its UTC hour of day (0-23), for a distribution by hour: only the seconds that
    hold a value -- a gap is no observation, never a zero.
    """
    present = series.dropna()
    return pd.DataFrame(
        {"hour": pd.DatetimeIndex(present.index).hour, "value": present.to_numpy()},
        columns=["hour", "value"],
    )


@dataclass(frozen=True)
class DepthSummary:
    """
    The window's depth profile from one snapshot per minute. Invariant: every sampled snapshot is
    two-sided, uncrossed and alone in its second (the minute's first such row); `by_level`/`by_bps`
    average only the snapshots whose side reaches a level or distance (`bid_snapshots` /
    `ask_snapshots` count them per side), and
    `totals` holds each minute's total bid/ask size on the minute grid, NaN where no snapshot of
    the minute qualified.
    """

    by_level: pd.DataFrame
    by_bps: pd.DataFrame
    totals: pd.DataFrame


def _minute_samples(seconds: pd.DataFrame) -> list[int]:
    """Positions of the first two-sided, uncrossed, unshared row of each minute."""
    keys = pd.Series(seconds["ts_event"].to_numpy(dtype="int64") // NS_PER_S)
    spread = seconds["spread"].to_numpy(dtype="float64")
    valid = (spread > 0) & ~keys.duplicated(keep=False).to_numpy()
    minutes = keys.to_numpy() // 60
    chosen = pd.Series(minutes[valid]).drop_duplicates().index
    return np.flatnonzero(valid)[chosen].tolist()


def depth_summary(
    seconds: pd.DataFrame,
    start_ns: int,
    end_ns: int,
    levels: int = DEPTH_LEVELS,
    bps_edges: Sequence[float] = DEPTH_BPS,
) -> DepthSummary:
    """
    Sample the first qualifying snapshot of each minute (`DepthSummary`'s invariant) and derive
    its depth with `kernel.indicators`' `snapshot_depth`, `cumulative_depth` and `depth_within_bps`:
    the mean cumulative size by level and by distance from mid, per side, and the per-minute total
    size of the stored levels (the top `levels`, not the whole venue book). One snapshot a minute
    bounds the Python work to the window's minutes (MEM-01). A level's mean is over the snapshots
    that reach it (`snapshots`), so where thin and deep books mix, the mean curve can dip past the
    level the thin books end at: read it with its side's count.
    """
    bid_cum, ask_cum, bid_bps, ask_bps, total_rows = [], [], [], [], []
    for position in _minute_samples(seconds):
        row = seconds.iloc[position]
        profile = snapshot_depth(row.to_dict(), levels)
        if profile is None:  # `_minute_samples` picks two-sided rows only
            raise ValueError(f"sampled snapshot at {row['ts_event']} has an empty side")
        bids, asks = cumulative_depth(profile)
        bid_cum.append(pd.Series(bids, index=range(1, len(bids) + 1)))
        ask_cum.append(pd.Series(asks, index=range(1, len(asks) + 1)))
        within_bid, within_ask = depth_within_bps(profile, bps_edges)
        bid_bps.append(within_bid)
        ask_bps.append(within_ask)
        total_rows.append((int(row["ts_event"]) // (60 * NS_PER_S), bids[-1], asks[-1]))
    by_level = pd.DataFrame(
        {
            "level": range(1, levels + 1),
            "bid": _column_means(bid_cum, levels),
            "ask": _column_means(ask_cum, levels),
            "bid_snapshots": _column_counts(bid_cum, levels),
            "ask_snapshots": _column_counts(ask_cum, levels),
        },
        columns=list(DEPTH_LEVEL_COLUMNS),
    )
    bid_frame = pd.DataFrame(bid_bps, columns=list(bps_edges), dtype="float64")
    ask_frame = pd.DataFrame(ask_bps, columns=list(bps_edges), dtype="float64")
    by_bps = pd.DataFrame(
        {
            "bps": list(bps_edges),
            "bid": bid_frame.mean().to_numpy(),
            "ask": ask_frame.mean().to_numpy(),
            "bid_snapshots": bid_frame.count().to_numpy(),
            "ask_snapshots": ask_frame.count().to_numpy(),
        },
        columns=list(DEPTH_BPS_COLUMNS),
    )
    return DepthSummary(by_level, by_bps, _minute_totals(total_rows, start_ns, end_ns))


def _column_means(rows: list[pd.Series], levels: int) -> np.ndarray:
    frame = pd.DataFrame(rows, columns=range(1, levels + 1), dtype="float64")
    return frame.mean().to_numpy()


def _column_counts(rows: list[pd.Series], levels: int) -> np.ndarray:
    frame = pd.DataFrame(rows, columns=range(1, levels + 1), dtype="float64")
    return frame.count().to_numpy()


def _minute_totals(
    rows: list[tuple[int, float, float]], start_ns: int, end_ns: int
) -> pd.DataFrame:
    minute_ns = 60 * NS_PER_S
    minutes = np.arange(start_ns // minute_ns, -(-end_ns // minute_ns), dtype="int64")
    found = pd.DataFrame(rows, columns=["minute", "bid", "ask"]).set_index("minute")
    totals = found.reindex(minutes)
    index = pd.DatetimeIndex(pd.to_datetime(minutes * minute_ns, unit="ns", utc=True), name="ts")
    return pd.DataFrame(
        {
            "total_bid": totals["bid"].to_numpy(dtype="float64"),
            "total_ask": totals["ask"].to_numpy(dtype="float64"),
        },
        index=index,
    )


def _z_of_finite(values: np.ndarray, window: int) -> np.ndarray:
    """
    `RollingZScore` fed the finite values in order; NaN where the input is (a gap) and at the first
    reading, whose z-score is undefined (the indicator's 0.0 there is a placeholder, not a value).
    """
    z = RollingZScore(window)
    out = np.full(len(values), math.nan)
    for count, position in enumerate(np.flatnonzero(np.isfinite(values)).tolist()):
        z.update_raw(float(values[position]))
        if count:
            out[position] = z.value
    return out


def obi_zscores(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    `obi_<N>_z` per `OBI_LEVELS`: each grid OBI z-scored by `kernel.indicators.RollingZScore` over
    its last `window` readings (the OFI strategy's `ofi_zscore_window`). A gap is not a reading:
    it stays NaN and the window carries on over it, as the strategy's OFI z-score history does
    across a feed gap. Known limit: `RollingZScore` does O(`window`) numpy work per reading
    (~25 us), so four OBI levels over a day of seconds take ~9 s; upgrade path: a running-sum
    z-score in `RollingZScore`, cross-checked against the deque form before it replaces it.
    """
    return pd.DataFrame(
        {
            f"obi_{n}_z": _z_of_finite(frame[f"obi_{n}"].to_numpy(dtype="float64"), window)
            for n in OBI_LEVELS
        },
        index=frame.index,
    )


def ofi_readings(seconds: pd.DataFrame, config: OFIStrategyConfig) -> pd.DataFrame:
    """
    Replay `MultiLevelOFI` over the rows exactly as `OFIStrategy.on_data` does: every two-sided
    row in `ts_event` order (crossed ones included), `usd_notional=True`, the config's
    `ofi_levels`/`ofi_window`, and `clear_prev_state()` after a gap over
    `kernel.indicators.OFI_GAP_NS` (the one gap rule, Story 31.3). Returns the
    rows with `ofi` (the raw windowed OFI) and `ofi_z` (`RollingZScore` over the config's
    `ofi_zscore_window`, the formula `MultiLevelOFI(zscore_window=...)` delegates to, so it equals
    the strategy's own value). A row that only sets the baseline (the first, or the first after
    a gap) or has an empty side produced no reading: NaN, never the last value. `OFIStrategy`
    (and `SnapshotStrategy`) likewise take no OFI-driven decision on the baseline row after a
    gap, where `MultiLevelOFI.update_raw` makes no new reading and `value` still holds the
    pre-gap one; only `OFIStrategy`'s trend-flip exit, which does not read OFI, may fire there.
    """
    ofi = MultiLevelOFI(levels=config.ofi_levels, window=config.ofi_window, usd_notional=True)
    zscore = RollingZScore(config.ofi_zscore_window)
    raw, z = np.full(len(seconds), math.nan), np.full(len(seconds), math.nan)
    last_ts: int | None = None
    columns = [
        seconds[name].tolist()
        for name in ("ts_event", "bid_prices", "bid_sizes", "ask_prices", "ask_sizes")
    ]
    for position, (ts, bid_p, bid_s, ask_p, ask_s) in enumerate(zip(*columns, strict=True)):
        if not len(bid_p) or not len(ask_p):
            continue
        gap = last_ts is not None and ts - last_ts > OFI_GAP_NS
        if gap:
            ofi.clear_prev_state()
        baseline = last_ts is None or gap
        last_ts = ts
        ofi.update_raw(bid_p, bid_s, ask_p, ask_s)
        if baseline:
            continue
        zscore.update_raw(ofi.value)
        raw[position], z[position] = ofi.value, zscore.value
    return seconds[["ts_event", "spread"]].assign(ofi=raw, ofi_z=z)


def ofi_replay(
    seconds: pd.DataFrame, config: OFIStrategyConfig, start_ns: int, end_ns: int
) -> pd.DataFrame:
    """
    `ofi_readings` on the 1 s grid: NaN on a missing, shared or crossed second (a crossed row is
    still fed to the replay, as the strategy would, but its value is not plotted).
    """
    return second_grid(
        ofi_readings(seconds, config),
        start_ns,
        end_ns,
        OFI_COLUMNS,
        book_columns=BOOK_COLUMNS | set(OFI_COLUMNS),
    )


def microprice_edge(frame: pd.DataFrame) -> pd.DataFrame:
    """
    `predictor` = microprice - mid at second t, `outcome` = mid[t+1] - mid[t] (the next grid
    second); NaN wherever either input is -- a gap is never bridged to a later second. With equal
    top sizes the microprice *is* the mid, but the two kernel values round differently (~43% of
    such books differ by an ulp), so a predictor within `_ROUNDING_ULPS` ulps of the mid is 0
    (flat), never a random sign that would pull every hit rate toward 0.5.
    """
    mid = frame["mid"].to_numpy(dtype="float64")
    outcome = np.r_[mid[1:] - mid[:-1], math.nan] if len(mid) else mid
    predictor = frame["microprice"].to_numpy(dtype="float64") - mid
    with np.errstate(invalid="ignore"):
        rounding = np.abs(predictor) <= _ROUNDING_ULPS * np.spacing(np.abs(mid))
    return pd.DataFrame(
        {"predictor": np.where(rounding, 0.0, predictor), "outcome": outcome},
        index=frame.index,
    )


def trade_flow(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Buy/sell volume and counts per grid second and the CVD (cumulative `volume_delta`) per run of
    sampled seconds. Invariant: CVD is NaN on a missing (or shared) second and restarts from 0
    after it -- the trades of an unsampled second are unknown, so carrying the level across the
    gap would count them as zero.
    """
    flow = frame[["buy_volume", "sell_volume", "buy_count", "sell_count"]].copy()
    delta = frame["volume_delta"]
    run = delta.isna().cumsum()
    flow["cvd"] = delta.groupby(run).cumsum().where(delta.notna())
    return flow


def impact_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    """
    `signed_volume` (`volume_delta` of second t) and `mid_change` = mid[t+1] - mid[t-1], which
    spans all of second t's trades whatever the book's sampling instant inside the second.
    Known limit: the 2 s window also holds part of seconds t-1 and t+1, so it adds their drift
    and some of their own trades' impact, and neighbouring windows overlap (serially correlated
    residuals: read the slope, not an OLS standard error); upgrade path: a trade-timestamp-aligned
    mid from the raw `OrderBookDelta` archive where it is stored.
    """
    mid = frame["mid"].to_numpy(dtype="float64")
    change = np.full(len(mid), math.nan)
    if len(mid) > 2:
        change[1:-1] = mid[2:] - mid[:-2]
    return pd.DataFrame(
        {"signed_volume": frame["volume_delta"].to_numpy(dtype="float64"), "mid_change": change},
        index=frame.index,
    )


def quantile_edges(values: npt.ArrayLike, bins: int = BINS) -> np.ndarray:
    """
    Distinct quantile edges splitting the finite values into up to `bins` bins (fewer when values
    repeat); fewer than 2 edges when there are not two distinct finite values.
    """
    array = np.asarray(values, dtype="float64")
    finite = array[np.isfinite(array)]
    if not len(finite):
        return np.empty(0)
    return np.unique(np.quantile(finite, np.linspace(0.0, 1.0, bins + 1)))


def impact_table(inputs: pd.DataFrame, bins: int = BINS) -> pd.DataFrame:
    """
    `research.domain.microstructure.price_impact` per `|signed_volume|` quantile bucket of the
    complete pairs; an empty table when the volumes do not span two distinct values.
    """
    complete = inputs.dropna()
    edges = quantile_edges(complete["signed_volume"].abs().to_numpy(), bins)
    if len(edges) < 2:
        return pd.DataFrame(columns=list(IMPACT_COLUMNS))
    fit = price_impact(complete["signed_volume"], complete["mid_change"], edges)
    return pd.DataFrame(fit._asdict(), columns=list(IMPACT_COLUMNS))


def hit_rate_table(edge: pd.DataFrame, bins: int = BINS) -> pd.DataFrame:
    """
    `research.domain.microstructure.hit_rate_by_bin` of the microprice edge per predictor quantile
    bin of the complete pairs; an empty table when the predictor does not span two distinct values.
    """
    complete = edge.dropna()
    edges = quantile_edges(complete["predictor"].to_numpy(), bins)
    if len(edges) < 2:
        return pd.DataFrame(columns=list(HIT_RATE_COLUMNS))
    rate = hit_rate_by_bin(complete["predictor"], complete["outcome"], edges)
    return pd.DataFrame(rate._asdict(), columns=list(HIT_RATE_COLUMNS))


def basis_frame(mark_index: pd.DataFrame, start_ns: int, end_ns: int) -> pd.DataFrame:
    """
    `mark`, `index` and `basis` = mark - index per grid second: each stream's last value inside
    the second (by `ts_event`). Invariant: `basis` is NaN wherever either stream has no row in that
    second -- the two streams are never carried onto each other's seconds.
    """
    grid_seconds = np.arange(start_ns // NS_PER_S, -(-end_ns // NS_PER_S), dtype="int64")
    keys = mark_index["ts_event"].to_numpy(dtype="int64") // NS_PER_S
    data = {}
    for name in ("mark", "index"):
        values = mark_index[name].to_numpy(dtype="float64")
        present = np.isfinite(values)
        last = pd.Series(values[present], index=keys[present]).groupby(level=0).last()
        data[name] = last.reindex(grid_seconds).to_numpy(dtype="float64")
    data["basis"] = data["mark"] - data["index"]
    index = pd.DatetimeIndex(
        pd.to_datetime(grid_seconds * NS_PER_S, unit="ns", utc=True), name="ts"
    )
    return pd.DataFrame(data, index=index)


def _grid_ns(index: pd.Index) -> np.ndarray:
    """Return a UTC grid index as int64 ns since the epoch."""
    return pd.DatetimeIndex(index).tz_convert(None).to_numpy(dtype="datetime64[ns]").astype("int64")


def second_returns(frame: pd.DataFrame) -> ReturnSeries:
    """Return the grid mid's 1 s returns (`ReturnSeries.from_prices`): NaN across every gap."""
    return ReturnSeries.from_prices(
        frame["mid"].to_numpy(dtype="float64").tolist(),
        _grid_ns(frame.index).tolist(),
        1,
    )


def autocorrelation_table(
    returns: ReturnSeries,
    horizons_s: Sequence[int] = ACF_HORIZONS_S,
    lags: Sequence[int] = ACF_LAGS,
) -> pd.DataFrame:
    """
    `research.domain.microstructure.autocorrelation` of the returns compounded to each horizon
    (`ReturnSeries.resample`, so every horizon comes from the same 1 s series; a bucket holding a
    gap is NaN), at each lag in horizon periods. A horizon the window cannot fill has NaN with 0
    pairs, never an error.
    """
    rows = []
    for horizon in horizons_s:
        acf = autocorrelation(returns.resample(horizon), lags)
        rows += [
            {"horizon_s": horizon, "lag": lag, "lag_s": lag * horizon, "rho": rho, "pairs": n}
            for lag, rho, n in zip(
                acf.lags.tolist(), acf.rho.tolist(), acf.pairs.tolist(), strict=True
            )
        ]
    return pd.DataFrame(rows, columns=list(ACF_COLUMNS))


def signature_table(
    returns: ReturnSeries, intervals_s: Sequence[int] = SIGNATURE_INTERVALS_S
) -> pd.DataFrame:
    """`research.domain.microstructure.volatility_signature` as a table, one row per interval."""
    signature = volatility_signature(returns, intervals_s)
    return pd.DataFrame(
        {
            "interval_s": signature.intervals_s,
            "variance_per_second": signature.variance_per_second,
            "n": signature.n,
        },
        columns=list(SIGNATURE_COLUMNS),
    )


def rolling_volatility(returns: ReturnSeries, window: int = RV_WINDOW) -> pd.Series:
    """`research.domain.microstructure.realised_volatility` (annualised) indexed by UTC time."""
    index = pd.DatetimeIndex(pd.to_datetime(returns.ts_ns, unit="ns", utc=True), name="ts")
    return pd.Series(realised_volatility(returns, window), index=index, name="realised_vol")


def ofi_threshold_lines(config: OFIStrategyConfig) -> tuple[float, float]:
    """Return the OFI strategy's entry levels, `+ofi_threshold` and `-ofi_threshold`."""
    return config.ofi_threshold, -config.ofi_threshold


def warmup_end_ns(seconds: pd.DataFrame, config: OFIStrategyConfig) -> int | None:
    """
    When the OFI strategy would first act on this window: its first two-sided row plus
    `warmup_seconds` (`OFIStrategy.on_data` evaluates nothing before it); None without a
    two-sided row. A z-score crossing before it is not a trade the strategy would take.
    """
    # `mid` is set exactly where both sides hold a level (`kernel.indicators.mid_price`).
    two_sided = seconds["ts_event"][seconds["mid"].notna()]
    if not len(two_sided):
        return None
    return int(two_sided.iloc[0]) + config.warmup_seconds * NS_PER_S


def warmup_span(warmup_end: int | None, start_ns: int, end_ns: int) -> tuple[int, int]:
    """
    Return the span of the window the OFI strategy spends warming up, `[start_ns, warmup end)`
    clamped to the window: all of it without a two-sided row or when the warm-up outlasts it.
    """
    return start_ns, end_ns if warmup_end is None else max(start_ns, min(warmup_end, end_ns))


def by_horizon(table: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Split an `autocorrelation_table` per horizon (seconds), in the table's order."""
    horizons = dict.fromkeys(table["horizon_s"].tolist())
    return {int(h): table.loc[table["horizon_s"] == h] for h in horizons}


def coverage_line(instrument_id: str, stream: str, frame: pd.DataFrame) -> str:
    """One printable line: how many rows `stream` has, or that it was not collected."""
    if len(frame):
        return f"{instrument_id}: {stream} {len(frame)} rows"
    return f"{instrument_id}: {stream} not collected in this window"


@dataclass(frozen=True)
class InstrumentMicrostructure:
    """
    One instrument's frames over one window. Invariant: `grid`, `ofi`, `obi_z` and `basis` share
    the window's 1 s grid index, `depth.totals` its minute grid; `funding` and `open_interest`
    and `mark_index` are the event rows as read (an empty frame where the stream was not
    collected, e.g. Bybit spot); `snapshot_rows` counts the snapshot rows read; `tick` is None
    without an instrument definition; `warmup_end_ns` is when the OFI strategy would first act
    (`warmup_end_ns`), None without a two-sided row.
    """

    instrument_id: str
    tick: float | None
    snapshot_rows: int
    grid: pd.DataFrame
    ofi: pd.DataFrame
    obi_z: pd.DataFrame
    depth: DepthSummary
    basis: pd.DataFrame
    mark_index: pd.DataFrame
    funding: pd.DataFrame
    open_interest: pd.DataFrame
    warmup_end_ns: int | None


def read_instrument(
    frames: MarketFrames,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    config: OFIStrategyConfig,
    tick: float | None,
) -> InstrumentMicrostructure:
    """
    Read one instrument's window once (`seconds`, `mark_index`, `funding`, `open_interest`, each
    bounded by `start_ns`/`end_ns`) and derive every frame the notebook plots; the seconds frame
    (the window's book lists) is dropped on return, so memory holds one instrument's lists at once.
    """
    seconds = frames.seconds(instrument_id, start=start_ns, end=end_ns)
    frame = grid(seconds, start_ns, end_ns)
    mark_index = frames.mark_index(instrument_id, start=start_ns, end=end_ns)
    return InstrumentMicrostructure(
        instrument_id=instrument_id,
        tick=tick,
        snapshot_rows=len(seconds),
        grid=frame,
        ofi=ofi_replay(seconds, config, start_ns, end_ns),
        obi_z=obi_zscores(frame, config.ofi_zscore_window),
        depth=depth_summary(seconds, start_ns, end_ns),
        basis=basis_frame(mark_index, start_ns, end_ns),
        mark_index=mark_index,
        funding=frames.funding(instrument_id, start=start_ns, end=end_ns),
        open_interest=frames.open_interest(instrument_id, start=start_ns, end=end_ns),
        warmup_end_ns=warmup_end_ns(seconds, config),
    )


# Fewest pairs an autocorrelation sign is read from; below it the estimate is `n/a`.
_MIN_ACF_PAIRS = 10


def _sign(rho: float, pairs: int) -> str:
    """
    `+`/`-` only where `rho` lies outside the white-noise band `+-2 / sqrt(pairs)` (~95%, Bartlett);
    `~0` inside it (no evidence of either sign), `n/a` when non-finite or under `_MIN_ACF_PAIRS`.
    """
    if not math.isfinite(rho) or pairs < _MIN_ACF_PAIRS:
        return "n/a"
    if abs(rho) <= 2.0 / math.sqrt(pairs):
        return "~0"
    return "+" if rho > 0 else "-"


def _ratio(numerator: float, denominator: float) -> float:
    """`numerator / denominator`, NaN when either is non-finite or the denominator is not > 0."""
    if not (math.isfinite(numerator) and math.isfinite(denominator)) or denominator <= 0:
        return math.nan
    return numerator / denominator


def observations(data: InstrumentMicrostructure, acf: pd.DataFrame) -> dict[str, Any]:
    """
    Return the closing section's row for one instrument, from the sections' own results: median
    and p90 spread (bps), the spread's hourly-median range (a regime change shows as a wide
    range), the widest hour (seasonality), bid/ask depth asymmetry (mean total bid / mean total
    ask, NaN without ask depth) and the lag-1 autocorrelation sign at each horizon (`+` momentum,
    `-` mean reversion, `~0` inside the white-noise band, `n/a` too few pairs; `_sign`).
    """
    bps = spread_frame(data.grid, data.tick)["spread_bps"]
    hourly = by_hour_utc(bps).groupby("hour")["value"].median()
    totals = data.depth.totals
    lag_one = acf.loc[acf["lag"] == 1].set_index("horizon_s")
    return {
        "instrument_id": data.instrument_id,
        "spread_bps_median": float(bps.median()),
        "spread_bps_p90": float(bps.quantile(0.9)),
        "spread_bps_hourly_min": float(hourly.min()) if len(hourly) else math.nan,
        "spread_bps_hourly_max": float(hourly.max()) if len(hourly) else math.nan,
        "widest_hour_utc": int(hourly.idxmax()) if len(hourly) else None,
        "depth_bid_over_ask": _ratio(
            float(totals["total_bid"].mean()), float(totals["total_ask"].mean())
        ),
        **{
            f"acf_sign_{h}s": _sign(float(row["rho"]), int(row["pairs"]))
            for h, row in lag_one.iterrows()
        },
    }
