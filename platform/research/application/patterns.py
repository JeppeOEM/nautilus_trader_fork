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
The candlestick scanner service (Story 27.7), behind `research/notebooks/06_candlestick_scanner`:
the candle store's bars on a complete bucket grid, `kernel.candle_patterns.CandlePatternSet`
streamed over them with an EMA filter, the hits table, the window around one hit, and forward
returns after the hits. It computes no statistic of its own: patterns are the kernel's, the EMA is
`nautilus_trader.indicators.ExponentialMovingAverage`, and forward returns and hit rates are
`research.domain.events`'. No aggregate.

Gaps stay visible (DATA-01). Bars are read only through `MarketFrames.bars`, span by span over
`bar_coverage`, so no read crosses a never-observed bucket; an absent bucket (never observed, or
observed with no trade) is a NaN row of the grid, and so is a `partial` bar (under 90% of its span
observed: its OHLC is not the bucket's). The pattern set and the EMA reset at every NaN row -- a
two- or three-bar pattern never compares bars that are not adjacent in time -- and a forward return
that would cross one, or run past the window, is NaN, never filled.
"""

import math
from collections.abc import Mapping
from collections.abc import Sequence

import numpy as np
import pandas as pd
from candles.domain.fold import BAR_SECONDS
from kernel.candle_patterns import NON_DIRECTIONAL
from kernel.candle_patterns import CandlePatternSet
from kernel.candle_patterns import PatternName
from kernel.candle_patterns import Thresholds
from kernel.clocks import NS_PER_MS
from kernel.clocks import NS_PER_S

from nautilus_trader.indicators import ExponentialMovingAverage
from research.application.ports import MarketFrames
from research.domain.events import forward_returns
from research.domain.events import hit_rate


GRID_COLUMNS = ("timestamp", "ts_ns", "o", "h", "l", "c", "partial")
HIT_COLUMNS = (
    "timestamp",
    "ts_ns",
    "bar_index",
    "pattern",
    "direction",
    "open",
    "high",
    "low",
    "close",
    "ema",
)
# The scanner's table: each hit tagged with where it was found.
SCAN_COLUMNS = ("instrument_id", "timeframe", *HIT_COLUMNS)
FORWARD_COLUMNS = ("timeframe", "pattern", "direction", "horizon", "hit_rate", "mean_return", "n")
CONDITIONS = ("any", "above", "below")

GridKey = tuple[str, int]


def split_timeframes(timeframes: Sequence[int]) -> tuple[list[int], list[str]]:
    """
    Return the timeframes the candle store keeps (`BAR_SECONDS`, in the order asked, each once)
    and one line per other timeframe saying it is skipped: bars come only from the store's own
    fold, never from a resample here.
    """
    kept = [tf for tf in dict.fromkeys(timeframes) if tf in BAR_SECONDS]
    skipped = [
        f"{tf} s is not a candle-store bar size {BAR_SECONDS}: skipped, never resampled"
        for tf in dict.fromkeys(timeframes)
        if tf not in BAR_SECONDS
    ]
    return kept, skipped


def bar_grid(
    frames: MarketFrames, instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
) -> pd.DataFrame:
    """
    Return the bars of `[start_ns, end_ns)` on the complete bucket grid (every bucket whose whole
    bar lies in the window, oldest first, `GRID_COLUMNS`). Invariant: one row per bucket; a bucket
    with no stored traded bar, or a `partial` one, has NaN `o`/`h`/`l`/`c` -- never filled. Read
    span by span over `bar_coverage`, so no read crosses a hole.
    """
    bar_ns = bar_seconds * NS_PER_S
    first = -(-start_ns // bar_ns) * bar_ns
    starts = np.arange(first, end_ns // bar_ns * bar_ns, bar_ns, dtype=np.int64)
    ohlc = np.full((len(starts), 4), np.nan)
    partial = np.zeros(len(starts), dtype=bool)
    for lo, hi in frames.bar_coverage(instrument_id, bar_seconds, start=start_ns, end=end_ns):
        bars = frames.bars(instrument_id, bar_seconds, start=lo, end=hi)
        rows = (bars["t"].to_numpy(dtype=np.int64) * NS_PER_MS - first) // bar_ns
        flags = bars["partial"].to_numpy(dtype=bool)
        partial[rows] = flags
        ohlc[rows[~flags]] = bars.loc[~flags, ["o", "h", "l", "c"]].to_numpy(dtype=float)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(starts, unit="ns", utc=True),
            "ts_ns": starts,
            "o": ohlc[:, 0],
            "h": ohlc[:, 1],
            "l": ohlc[:, 2],
            "c": ohlc[:, 3],
            "partial": partial,
        }
    )


def _present(grid: pd.DataFrame) -> np.ndarray:
    return grid[["o", "h", "l", "c"]].notna().all(axis=1).to_numpy()


def ema_values(grid: pd.DataFrame, ema_len: int) -> np.ndarray:
    """
    Return `ExponentialMovingAverage(ema_len)` of the closes, one value per grid row. Invariant:
    NaN on a NaN row and until the EMA is initialized again after it (it resets at every hole).
    """
    ema = ExponentialMovingAverage(ema_len)
    values = np.full(len(grid), np.nan)
    for i, (present, close) in enumerate(zip(_present(grid), grid["c"], strict=True)):
        if not present:
            ema.reset()
            continue
        ema.update_raw(float(close))
        values[i] = ema.value if ema.initialized else math.nan
    return values


def with_ema(grid: pd.DataFrame, ema_len: int) -> pd.DataFrame:
    """Return a copy of `grid` with an `ema` column (`ema_values`), for charting a hit's window."""
    return grid.assign(ema=ema_values(grid, ema_len))


def scan(grid: pd.DataFrame, thresholds: Thresholds, ema_len: int) -> pd.DataFrame:
    """
    Stream `CandlePatternSet(thresholds)` over one grid and return every hit (`HIT_COLUMNS`, one row
    per pattern fired on a bar, in bar then `PatternName` order). Invariant: the set resets at
    every NaN row, so a pattern only ever spans adjacent buckets; `ema` is `ema_values`' on the hit
    bar (NaN until initialized). An empty result still has every column.

    Known limit: pure-Python streaming, ~80 us per bar for all 22 patterns (measured), so a year of
    1 m bars is ~40 s per instrument and every grid of the call is held in memory at once. Fine
    for the notebook's instrument-at-a-time windows; upgrade path: vectorise the single-bar shapes
    in numpy and stream one grid at a time when the universe scan outgrows it.
    """
    patterns = CandlePatternSet(thresholds)
    ema = ema_values(grid, ema_len)
    present = _present(grid)
    ohlc = grid[["o", "h", "l", "c"]].to_numpy(dtype=float)
    stamps = grid["timestamp"].tolist()
    ts_ns = grid["ts_ns"].tolist()
    rows: list[tuple] = []
    for i, (o, h, l, c) in enumerate(ohlc.tolist()):
        if not present[i]:
            patterns.reset()
            continue
        patterns.update_raw(o, h, l, c)
        rows += [
            (stamps[i], ts_ns[i], i, name.value, value, o, h, l, c, ema[i])
            for name, value in patterns.fired()
        ]
    return pd.DataFrame(rows, columns=list(HIT_COLUMNS))


def bar_grids(
    frames: MarketFrames,
    instruments: Sequence[str],
    timeframes: Sequence[int],
    window: tuple[int, int],
) -> dict[GridKey, pd.DataFrame]:
    """
    Return `bar_grid` for every instrument at every timeframe over `window` (`start_ns`,
    `end_ns`), keyed `(instrument_id, timeframe)`, read one instrument and timeframe at a time.
    Every timeframe must be one the store keeps (`split_timeframes` first), else `ValueError`.
    """
    unkept = [tf for tf in timeframes if tf not in BAR_SECONDS]
    if unkept:
        raise ValueError(f"{unkept} are not candle-store bar sizes {BAR_SECONDS}")
    return {
        (instrument_id, timeframe): bar_grid(frames, instrument_id, timeframe, *window)
        for instrument_id in instruments
        for timeframe in timeframes
    }


def scan_grids(
    grids: Mapping[GridKey, pd.DataFrame], thresholds: Thresholds, ema_len: int
) -> pd.DataFrame:
    """
    Return `scan` of every grid in one table (`SCAN_COLUMNS`): each hit tagged with its
    `instrument_id` and `timeframe`, so its `bar_index` indexes `grids[(instrument_id,
    timeframe)]`. An empty result still has every column.
    """
    found = [
        scan(grid, thresholds, ema_len).assign(instrument_id=instrument_id, timeframe=timeframe)
        for (instrument_id, timeframe), grid in grids.items()
    ]
    found = [hits for hits in found if len(hits)]
    if not found:
        return pd.DataFrame(columns=list(SCAN_COLUMNS))
    return pd.concat(found, ignore_index=True).loc[:, list(SCAN_COLUMNS)]


def grid_summary(grids: Mapping[GridKey, pd.DataFrame]) -> pd.DataFrame:
    """Per grid: buckets on the grid, traded bars, NaN rows (holes) and partial bars."""
    rows = []
    for (instrument_id, timeframe), grid in grids.items():
        present = _present(grid)
        rows.append(
            {
                "instrument_id": instrument_id,
                "timeframe": timeframe,
                "buckets": len(grid),
                "bars": int(present.sum()),
                "nan_rows": int((~present).sum()),
                "partial": int(grid["partial"].sum()),
            }
        )
    return pd.DataFrame(
        rows, columns=["instrument_id", "timeframe", "buckets", "bars", "nan_rows", "partial"]
    )


def filter_hits(hits: pd.DataFrame, condition: str, pattern_filter: str) -> pd.DataFrame:
    """
    Keep the hits whose close is `above`/`below` their EMA (`any` keeps all; a NaN EMA fails both
    `above` and `below`) and, when `pattern_filter` is not blank, whose pattern is one of its
    comma-separated `PatternName` names. An unknown condition or name raises `ValueError`.
    """
    if condition not in CONDITIONS:
        raise ValueError(f"CONDITION must be one of {CONDITIONS}, not {condition!r}")
    names = [n.strip() for n in pattern_filter.split(",") if n.strip()]
    unknown = [n for n in names if n not in PatternName.__members__]
    if unknown:
        raise ValueError(f"unknown pattern(s) {unknown}; known: {[p.name for p in PatternName]}")
    keep = np.ones(len(hits), dtype=bool)
    if condition == "above":
        keep &= (hits["close"] > hits["ema"]).to_numpy()
    elif condition == "below":
        keep &= (hits["close"] < hits["ema"]).to_numpy()
    if names:
        keep &= hits["pattern"].isin(names).to_numpy()
    return hits.loc[keep].reset_index(drop=True)


def select_hit(hits: pd.DataFrame, index: int) -> pd.Series:
    """Return hit number `index` of the (filtered) table; `IndexError` naming the count if none."""
    if not 0 <= index < len(hits):
        raise IndexError(f"HIT_INDEX={index}, but the table holds {len(hits)} hit(s)")
    return hits.iloc[index]


def hit_window(grid: pd.DataFrame, bar_index: int, window_bars: int) -> pd.DataFrame:
    """
    Return the grid rows from `window_bars` before `bar_index` to `window_bars` after it (clipped
    to the grid), NaN rows kept so a gap shows as a gap on the chart.
    """
    if not 0 <= bar_index < len(grid):
        raise IndexError(f"bar {bar_index} is outside the grid's {len(grid)} rows")
    if window_bars < 0:
        raise ValueError(f"WINDOW_BARS must be >= 0, was {window_bars}")
    return grid.iloc[max(0, bar_index - window_bars) : bar_index + window_bars + 1]


def _forward_by_run(
    closes: np.ndarray, bar_indices: np.ndarray, horizons: Sequence[int]
) -> dict[int, np.ndarray]:
    """
    `forward_returns` of each hit within its own run of adjacent bars (the rows between two NaN
    rows), so a horizon reaching a hole runs past the run's end and is NaN.
    """
    present = np.isfinite(closes)
    run_of = np.cumsum(~present)
    out = {h: np.full(len(bar_indices), np.nan) for h in horizons}
    for run in np.unique(run_of[bar_indices]):
        rows = np.flatnonzero(present & (run_of == run))
        mine = np.flatnonzero(run_of[bar_indices] == run)
        part = forward_returns(
            closes[rows[0] : rows[-1] + 1], bar_indices[mine] - rows[0], horizons
        )
        for horizon in horizons:
            out[horizon][mine] = part[horizon]
    return out


def hit_forward_returns(
    grids: Mapping[GridKey, pd.DataFrame], hits: pd.DataFrame, horizons: Sequence[int]
) -> pd.DataFrame:
    """
    Return `hits` with one `ret_<h>` column per horizon: the simple return from the hit bar's close
    to the close `h` bars later on its own grid, NaN across a hole or past the window.
    """
    if len(horizons) == 0:
        raise ValueError("HORIZONS must name at least one horizon")
    hits = hits.reset_index(drop=True)  # positional rows: a concatenated table may repeat labels
    columns = {f"ret_{h}": np.full(len(hits), np.nan) for h in horizons}
    for _, group in hits.groupby(["instrument_id", "timeframe"]):
        key = (str(group["instrument_id"].iloc[0]), int(group["timeframe"].iloc[0]))
        closes = grids[key]["c"].to_numpy(dtype=float)
        returns = _forward_by_run(closes, group["bar_index"].to_numpy(dtype=np.int64), horizons)
        positions = group.index.to_numpy()
        for horizon in horizons:
            columns[f"ret_{horizon}"][positions] = returns[horizon]
    return hits.assign(**columns)


def forward_table(
    grids: Mapping[GridKey, pd.DataFrame], hits: pd.DataFrame, horizons: Sequence[int]
) -> pd.DataFrame:
    """
    Per timeframe, pattern and direction, and per horizon: the hit rate, mean forward return and
    count of measurable hits (`research.domain.events.hit_rate`), long format (`FORWARD_COLUMNS`).
    A group whose returns are all NaN reads `n` 0 and NaN rate and mean, never dropped. A
    `NON_DIRECTIONAL` pattern (`DOJI`) has a mean and `n` but a NaN hit rate: its +100 marks that it
    fired, not a direction, so a "hit" in that sign would only measure drift.
    """
    returns = hit_forward_returns(grids, hits, horizons)
    rows = []
    for (timeframe, pattern, direction), group in returns.groupby(
        ["timeframe", "pattern", "direction"], sort=True
    ):
        sign = int(group["direction"].iloc[0])
        for horizon in horizons:
            summary = hit_rate(group[f"ret_{horizon}"].to_numpy(dtype=float), sign)
            if PatternName(str(pattern)) in NON_DIRECTIONAL:
                summary = summary._replace(rate=math.nan)
            rows.append((timeframe, pattern, direction, horizon, *summary))
    return pd.DataFrame(rows, columns=list(FORWARD_COLUMNS))


def no_hits_note(hits: pd.DataFrame, condition: str, pattern_filter: str) -> str | None:
    """Return the sentence printed instead of a chart when the filtered table is empty, else None."""
    if len(hits):
        return None
    wanted = f"pattern filter {pattern_filter!r}" if pattern_filter.strip() else "any pattern"
    return (
        f"No hit in this window for {wanted} with CONDITION={condition!r}: nothing to chart "
        "and no forward return to measure."
    )
