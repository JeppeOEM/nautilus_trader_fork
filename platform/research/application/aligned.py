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
Aligned returns and cross-venue frames (Story 27.4): every number `research/notebooks/
03_correlation` shows comes from here and from `research.domain.correlation`.

Two sources of returns, never a third fold: bars of 60 s and up are the candle store's
(`MarketFrames.bars`, read span by span over `MarketFrames.bar_coverage`, so a bucket never
observed is a hole and a NaN return, never an error and never a fill); 1 s returns are the window's
1 s mid grid (`inspection.second_grid`: a missing, shared or crossed second is NaN). Every
correlation is pairwise-complete and nothing is forward-filled (DATA-01). Same-asset matching is
`kernel.venues.asset_key`'s (through `MarketFrames.same_symbol`); nothing here parses an id beyond
`venue_of`. Every read is bounded by the window's `start_ns`/`end_ns` (MEM-01).
"""

import math
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd
from kernel.clocks import NS_PER_S
from kernel.venues import VENUE_KINDS
from kernel.venues import AssetKey
from kernel.venues import asset_key
from kernel.venues import venue_of

from research.application.inspection import second_grid
from research.application.microstructure import second_returns
from research.application.ports import MarketFrames
from research.domain.correlation import AlignedReturns
from research.domain.correlation import CorrelationMatrix
from research.domain.correlation import align
from research.domain.correlation import basis_bps
from research.domain.correlation import cluster
from research.domain.correlation import correlation_matrix
from research.domain.correlation import correlation_of
from research.domain.correlation import lagged_pairs
from research.domain.correlation import lead_lag
from research.domain.correlation import peak_lag
from research.domain.correlation import rolling_correlation
from research.domain.returns import ReturnSeries


# Return horizons (s) of the correlation matrices: 1 m, 5 m, 1 h, 1 d -- all sizes the candle
# store keeps (`candles.domain.fold.BAR_SECONDS`), so each comes from the store's own fold.
HORIZONS_S = (60, 300, 3600, 86400)
VOLUME_COLUMNS = ("buy_volume", "sell_volume")
SECOND_COLUMNS = ("mid", *VOLUME_COLUMNS)
NOT_COLLECTED = "not collected in this window"
# Finite 1 s return pairs a lead-lag lag needs before it has a rho at all (`lead_lag`'s `min_pairs`).
MIN_LEAD_LAG_PAIRS = 30
# A peak is stated as a lead only above `NOISE_Z / sqrt(pairs)`: the ~95% band of the sample rho of
# two independent series. Known limit: 1 s returns are autocorrelated (bid-ask bounce), which widens
# the true band; upgrade path: a block-bootstrap band per pair.
NOISE_Z = 2.0


def _utc_index(ts_ns: Iterable[int]) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(np.asarray(ts_ns, dtype="int64"), unit="ns", utc=True))


def _index_ns(index: pd.Index) -> np.ndarray:
    """Return a UTC `DatetimeIndex` as int64 ns since the epoch."""
    return pd.DatetimeIndex(index).tz_convert(None).to_numpy(dtype="datetime64[ns]").astype("int64")


def bar_returns(
    frames: MarketFrames, instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
) -> ReturnSeries:
    """
    Close-to-close returns of the candle store's `bar_seconds` bars in `[start_ns, end_ns)`
    (`ReturnSeries.from_prices` on each bar's `c`). Invariant: the window is read span by span
    over `bar_coverage`, so no read crosses a hole; a return across a never-observed bucket, an
    untraded bucket (no bar, no close) or the store's edge is NaN, never a bridged return. A
    `partial` bar (under 90% of its span observed: an outage inside the bucket, or the store's
    newest bucket still forming) has no trustworthy close, so both returns touching it are NaN too.
    """
    closes: list[float] = []
    stamps: list[int] = []
    for lo, hi in frames.bar_coverage(instrument_id, bar_seconds, start=start_ns, end=end_ns):
        bars = frames.bars(instrument_id, bar_seconds, start=lo, end=hi)
        closes += [
            math.nan if partial else close
            for close, partial in zip(bars["c"].tolist(), bars["partial"].tolist(), strict=True)
        ]
        stamps += bars["ts_event"].tolist()
    return ReturnSeries.from_prices(closes, stamps, bar_seconds)


def second_frame(
    frames: MarketFrames, instrument_id: str, start_ns: int, end_ns: int
) -> pd.DataFrame:
    """
    `mid`, `buy_volume` and `sell_volume` on the window's 1 s grid (`inspection.second_grid`):
    NaN on a second with no row or two rows, `mid` NaN on a crossed second too. The seconds frame
    (the book lists) is dropped on return, so memory holds one instrument's lists at a time.
    """
    seconds = frames.seconds(instrument_id, start=start_ns, end=end_ns)
    return second_grid(seconds, start_ns, end_ns, SECOND_COLUMNS)


def _return_grid(
    period_s: int, start_ns: int, end_ns: int, series: Iterable[ReturnSeries]
) -> np.ndarray:
    """
    Every return stamp of a `period_s` grid over the window: from the second whole bucket (the
    first return) to the last whole bucket, plus any stamp a series holds beyond it.
    """
    period_ns = period_s * NS_PER_S
    first = -(-start_ns // period_ns) * period_ns
    stop = end_ns // period_ns * period_ns
    grid = np.arange(first + period_ns, stop, period_ns, dtype=np.int64)
    return np.unique(np.concatenate([grid, *(s.ts_ns for s in series)]))


def on_grid(
    series_by_id: Mapping[str, ReturnSeries], period_s: int, start_ns: int, end_ns: int
) -> AlignedReturns:
    """
    Align the series on the complete `period_s` grid of the window (not only the stamps some series
    holds), so a row is one period of time and a rolling window of k rows spans k periods; a stamp
    no series holds is an all-NaN row, never filled.
    """
    ts = _return_grid(period_s, start_ns, end_ns, series_by_id.values())
    matrix = np.full((len(ts), len(series_by_id)), np.nan)
    for j, series in enumerate(series_by_id.values()):
        if series.period_seconds != period_s:
            raise ValueError(f"series has period {series.period_seconds}s, not {period_s}s")
        matrix[np.searchsorted(ts, series.ts_ns), j] = series.values  # noqa: PD011 -- numpy
    return AlignedReturns(tuple(series_by_id), ts, matrix, period_s)


def aligned_returns(
    frames: MarketFrames, ids: Sequence[str], bar_seconds: int, start_ns: int, end_ns: int
) -> AlignedReturns:
    """
    Every id's returns at `bar_seconds` on the window's complete grid (`on_grid`): the candle
    store's bars for 60 s and up (`bar_returns`), the 1 s mid grid for `bar_seconds == 1`
    (`second_returns`, one instrument's seconds read at a time).
    """
    series = {}
    for iid in ids:
        if bar_seconds == 1:
            series[iid] = second_returns(second_frame(frames, iid, start_ns, end_ns))
        else:
            series[iid] = bar_returns(frames, iid, bar_seconds, start_ns, end_ns)
    return on_grid(series, bar_seconds, start_ns, end_ns)


def by_venue(matrix: CorrelationMatrix) -> dict[str, CorrelationMatrix]:
    """
    Return the matrix restricted to each venue's ids (venues in first-appearance order). Pairwise-complete
    correlation depends only on its own pair, so each entry equals the full matrix's.
    """
    positions: dict[str, list[int]] = {}
    for j, iid in enumerate(matrix.ids):
        positions.setdefault(venue_of(iid), []).append(j)
    return {
        venue: CorrelationMatrix(
            tuple(matrix.ids[j] for j in idx),
            matrix.values[np.ix_(idx, idx)],  # noqa: PD011 -- a numpy array, not pandas
        )
        for venue, idx in positions.items()
    }


def clustered_order(matrix: CorrelationMatrix, threshold: float) -> list[str]:
    """
    Return the ids cluster by cluster (`research.domain.correlation.cluster`, single linkage at distance
    `threshold`), so a heatmap in this order shows each cluster as a block.
    """
    return [iid for group in cluster(matrix, threshold) for iid in group]


def matrix_frame(matrix: CorrelationMatrix, order: Sequence[str] | None = None) -> pd.DataFrame:
    """Return the matrix as a labelled frame, rows and columns in `order` (default `matrix.ids`)."""
    ids = list(matrix.ids if order is None else order)
    frame = pd.DataFrame(matrix.values, index=list(matrix.ids), columns=list(matrix.ids))
    return frame.loc[ids, ids]


def bucket_last(
    frame: pd.DataFrame, column: str, bucket_s: int, start_ns: int, end_ns: int
) -> pd.Series:
    """
    `column`'s last finite value in each `bucket_s` bucket (by `ts_event`) on the complete bucket
    grid of the window, indexed by the bucket's UTC start. Invariant: a bucket with no finite value
    is NaN -- a value is never carried into a later bucket.
    """
    bucket_ns = bucket_s * NS_PER_S
    buckets = np.arange(start_ns // bucket_ns, -(-end_ns // bucket_ns), dtype="int64")
    values = frame[column].to_numpy(dtype="float64")
    present = np.isfinite(values)
    keys = frame["ts_event"].to_numpy(dtype="int64")[present] // bucket_ns
    last = pd.Series(values[present], index=keys).groupby(level=0).last()
    return pd.Series(
        last.reindex(buckets).to_numpy(dtype="float64"), index=_utc_index(buckets * bucket_ns)
    )


def funding_per_hour(funding: pd.DataFrame) -> np.ndarray:
    """
    Each funding row's rate per hour, `rate * 60 / interval` (`interval` in minutes, as the venue
    publishes it, per row: e.g. 60 on dYdX and Hyperliquid, 60/240/480 by symbol on Bybit), so venues on different schedules
    compare. Invariant: NaN where `interval` is None or not positive -- an interval is never
    assumed.
    """
    rate = funding["rate"].to_numpy(dtype="float64")
    interval = np.array(
        [math.nan if v is None else float(v) for v in funding["interval"].tolist()], dtype="float64"
    )
    per_hour = np.full(len(rate), math.nan)
    known = np.isfinite(interval) & (interval > 0)
    per_hour[known] = rate[known] * 60.0 / interval[known]
    return per_hour


def funding_intervals(funding: pd.DataFrame) -> tuple[int | None, ...]:
    """Return the distinct funding intervals (minutes) the rows carry, in first-seen order; None kept."""
    seen: dict[int | None, None] = {}
    for value in funding["interval"].tolist():
        missing = value is None or (isinstance(value, float) and math.isnan(value))
        seen[None if missing else int(value)] = None
    return tuple(seen)


def funding_levels(
    funding_by_id: Mapping[str, pd.DataFrame], bucket_s: int, start_ns: int, end_ns: int
) -> pd.DataFrame:
    """Each id's last per-hour funding rate in each bucket (`bucket_last`), one column per id."""
    return pd.DataFrame(
        {
            iid: bucket_last(
                frame.assign(per_hour=funding_per_hour(frame)),
                "per_hour",
                bucket_s,
                start_ns,
                end_ns,
            )
            for iid, frame in funding_by_id.items()
        }
    )


def funding_matrix(
    funding_by_id: Mapping[str, pd.DataFrame], bucket_s: int, start_ns: int, end_ns: int
) -> CorrelationMatrix:
    """Pairwise-complete correlation of the bucketed per-hour funding levels (`correlation_of`)."""
    levels = funding_levels(funding_by_id, bucket_s, start_ns, end_ns)
    return correlation_of(tuple(levels.columns), levels.to_numpy(dtype="float64"))


def oi_changes(
    oi_by_id: Mapping[str, pd.DataFrame], bucket_s: int, start_ns: int, end_ns: int
) -> AlignedReturns:
    """
    Each id's relative open-interest change per bucket: the bucket's last OI
    (`bucket_last`) through `ReturnSeries.from_prices`, so a bucket without a reading breaks the
    change on both sides. An OI of zero or less is NaN here: a relative change from it is
    undefined (a new or delisted market), not a defect of the price kind `from_prices` refuses.
    """
    series = {}
    for iid, frame in oi_by_id.items():
        last = bucket_last(frame, "open_interest", bucket_s, start_ns, end_ns)
        values = last.to_numpy(dtype="float64")
        # Known limit (DATA_DICTIONARY §2.12, audit D-89): an OI of 0 is NaN, so a real drop *to* 0
        # (a defined -100 % change) reads undefined too, not only the change *from* 0. Upgrade
        # path: keep the -100 % and leave only the change from a non-positive base undefined.
        positive = np.where(np.isfinite(values) & (values > 0), values, math.nan)
        series[iid] = ReturnSeries.from_prices(
            positive.tolist(), _index_ns(last.index).tolist(), bucket_s
        )
    if not series:
        return AlignedReturns((), np.empty(0, dtype=np.int64), np.empty((0, 0)), bucket_s)
    return align(series)


def oi_change_matrix(
    oi_by_id: Mapping[str, pd.DataFrame], bucket_s: int, start_ns: int, end_ns: int
) -> CorrelationMatrix:
    """Pairwise-complete correlation of the relative open-interest changes (`oi_changes`)."""
    return correlation_matrix(oi_changes(oi_by_id, bucket_s, start_ns, end_ns))


def rolling_vs_anchor(aligned: AlignedReturns, anchor: str, window_s: int) -> pd.DataFrame:
    """
    `rolling_correlation` of every other id against `anchor` over a trailing `window_s` of rows
    (`window_s // period_seconds` rows of the complete grid, `on_grid`), indexed by UTC time.
    `window_s` must be a multiple of the period spanning at least two rows, and `anchor` one of the
    ids, else `ValueError`.
    """
    if anchor not in aligned.ids:
        raise ValueError(f"anchor {anchor} is not among {aligned.ids}")
    rows, rest = divmod(window_s, aligned.period_seconds)
    if rest or rows < 2:
        raise ValueError(f"window {window_s}s is not >= 2 whole {aligned.period_seconds}s periods")
    base = aligned.column(anchor)
    return pd.DataFrame(
        {
            iid: rolling_correlation(base, aligned.column(iid), rows)
            for iid in aligned.ids
            if iid != anchor
        },
        index=_utc_index(aligned.ts_ns),
    )


def lead_sentence(a: str, b: str, peak: tuple[int, float] | None, noise_band: float = 0.0) -> str:
    """
    Put the lead-lag peak in words: a positive peak lag means `a` leads `b` (`lead_lag`'s contract),
    a negative one that `b` leads `a`, zero that they move in the same second. Invariant: a peak at
    or below `noise_band` (never below 0: a negative rho is no lead) is stated as no lead, as is no
    peak at all.
    """
    if peak is None:
        return (
            f"{a} vs {b}: no lag holds a finite rho over {MIN_LEAD_LAG_PAIRS}+ overlapping"
            " 1 s returns, no lead stated"
        )
    lag, rho = peak
    if rho <= max(noise_band, 0.0):
        return (
            f"{a} vs {b}: peak rho {rho:.2f} at {lag} s is within the noise band"
            f" ({max(noise_band, 0.0):.2f}), no lead stated"
        )
    if lag > 0:
        return f"{a} leads {b} by {lag} s (peak rho {rho:.2f})"
    if lag < 0:
        return f"{b} leads {a} by {-lag} s (peak rho {rho:.2f})"
    return f"{a} and {b} move in the same second (peak rho {rho:.2f} at 0 s)"


def traded_volume(grid: pd.DataFrame) -> np.ndarray:
    """
    Each grid second's traded volume, `buy_volume + sell_volume` (`VOLUME_COLUMNS`). Invariant: NaN
    on a second with no snapshot row (or two), never 0 -- a stored row always sets both columns,
    so a finite value means the second was observed.
    """
    return (
        grid[list(VOLUME_COLUMNS)]
        .sum(axis=1, min_count=len(VOLUME_COLUMNS))
        .to_numpy(dtype="float64")
    )


def _leg_buckets(grid: pd.DataFrame, bucket_s: int) -> pd.DataFrame:
    """One leg's traded volume and observed seconds per bucket (a NaN second adds nothing)."""
    traded = traded_volume(grid)
    keys = _index_ns(grid.index) // (bucket_s * NS_PER_S)
    frame = pd.DataFrame({"volume": traded, "seconds": np.isfinite(traded).astype("int64")})
    return frame.groupby(keys).sum()


def venue_volume_share(grids: Mapping[str, pd.DataFrame], bucket_s: int) -> pd.DataFrame:
    """
    Each venue's share of the asset's traded volume (base units, `buy_volume + sell_volume` of the
    1 s grids, summed over the venue's legs) per `bucket_s` bucket, plus `<VENUE> seconds`, the
    fewest seconds any of its legs observed in the bucket. Invariant: a bucket where some venue
    observed no second, or where nothing traded, has a NaN share for every venue -- a missing venue
    is never a zero that inflates the others. Known limit: a venue observed for only part of a
    bucket still counts only those seconds (read the `seconds` columns); upgrade path: shares over
    the seconds every venue observed.
    """
    volume: dict[str, pd.Series] = {}
    seconds: dict[str, pd.Series] = {}
    for iid, grid in grids.items():
        venue, leg = venue_of(iid), _leg_buckets(grid, bucket_s)
        volume[venue] = volume[venue].add(leg["volume"]) if venue in volume else leg["volume"]
        # A venue observed a bucket only where every one of its legs did.
        seconds[venue] = (
            pd.concat([seconds[venue], leg["seconds"]], axis=1).min(axis=1)
            if venue in seconds
            else leg["seconds"]
        )
    if not volume:
        return pd.DataFrame()
    volumes, observed = pd.DataFrame(volume), pd.DataFrame(seconds)
    total = volumes.sum(axis=1)
    usable = (observed > 0).all(axis=1) & (total > 0)
    share = volumes.div(total.where(usable), axis=0)
    out = pd.concat([share, observed.add_suffix(" seconds")], axis=1)
    out.index = _utc_index(out.index.to_numpy(dtype="int64") * bucket_s * NS_PER_S)
    return out


@dataclass(frozen=True)
class VenuePair:
    """
    Two legs of one asset over the window. Invariant: `basis_bps` is on the window's 1 s grid, NaN
    wherever either mid is; `lead_lag` is `(lag, rho)` of `a`'s 1 s returns against `b`'s
    (positive lag: `a` leads; NaN at a lag under `MIN_LEAD_LAG_PAIRS` pairs), `peak` its
    `peak_lag`, `noise_band` the rho the peak must exceed (`NOISE_Z / sqrt(pairs at the peak)`, NaN
    without a peak) and `sentence` that in words;
    `funding_diff` is `a`'s per-hour funding minus `b`'s per bucket, NaN where either is absent.
    """

    a: str
    b: str
    basis_bps: pd.Series
    lead_lag: list[tuple[int, float]]
    peak: tuple[int, float] | None
    noise_band: float
    sentence: str
    funding_diff: pd.Series


@dataclass(frozen=True)
class CrossVenue:
    """
    One asset across the venues. Invariant: `group` is `MarketFrames.same_symbol`'s (sorted by
    venue then id, empty when `asset` is None); `collected` the group's legs with at least one
    observed second in the window; `missing` one line per `VENUE_KINDS` venue with no collected leg
    ("<asset> <VENUE>: not collected in this window"), so an absent venue is stated, never raised
    or drawn empty; `pairs` every pair of collected legs; `volume_share` per bucket
    (`venue_volume_share`, one share column per `venues` entry); `venues` the collected legs'
    distinct venues, in `collected` order; `intervals` each collected leg's distinct funding
    intervals.
    """

    instrument_id: str
    asset: AssetKey | None
    group: tuple[str, ...]
    collected: tuple[str, ...]
    missing: tuple[str, ...]
    pairs: tuple[VenuePair, ...]
    volume_share: pd.DataFrame
    venues: tuple[str, ...]
    intervals: dict[str, tuple[int | None, ...]]


def asset_label(asset: AssetKey) -> str:
    """`BTC-USD perp`: how a line names the asset."""
    return f"{asset.base}-{asset.quote} {asset.kind}"


def _labels(collected: Sequence[str]) -> dict[str, str]:
    """Each leg's name in a sentence: its venue, or its id where the venue has two legs."""
    venues = [venue_of(iid) for iid in collected]
    return {
        iid: v if venues.count(v) == 1 else iid for iid, v in zip(collected, venues, strict=True)
    }


def _missing_lines(asset: AssetKey, collected: Sequence[str]) -> tuple[str, ...]:
    present = {venue_of(iid) for iid in collected}
    return tuple(
        f"{asset_label(asset)} {venue}: {NOT_COLLECTED}"
        for venue in sorted(VENUE_KINDS)
        if venue not in present
    )


def _returns_lead_lag(
    a: pd.DataFrame, b: pd.DataFrame, max_lag: int
) -> tuple[list[tuple[int, float]], tuple[int, float] | None, float]:
    """
    `lead_lag` of two grids' 1 s returns (the lag capped by the series length), its `peak_lag` and
    the peak's noise band. Invariant: every lag needs `MIN_LEAD_LAG_PAIRS` finite pairs of its own,
    so a peak read off a handful of pairs (where rho is ±1 by construction) never exists.
    """
    ra, rb = second_returns(a).values, second_returns(b).values  # noqa: PD011 -- numpy
    if len(ra) < 2:
        return [], None, math.nan
    lags = lead_lag(ra, rb, min(max_lag, len(ra) - 1), min_pairs=MIN_LEAD_LAG_PAIRS)
    peak = peak_lag(lags)
    if peak is None:
        return lags, None, math.nan
    return lags, peak, NOISE_Z / math.sqrt(lagged_pairs(ra, rb, peak[0]))


def _pair(
    pair: tuple[str, str],
    grids: Mapping[str, pd.DataFrame],
    funding: pd.DataFrame,
    labels: Mapping[str, str],
    max_lag: int,
) -> VenuePair:
    """One `VenuePair` from the two legs' 1 s grids and the bucketed per-hour funding levels."""
    a, b = pair
    mid_a = grids[a]["mid"].to_numpy(dtype="float64")
    basis = pd.Series(
        basis_bps(mid_a, grids[b]["mid"].to_numpy(dtype="float64")), index=grids[a].index
    )
    lags, peak, noise_band = _returns_lead_lag(grids[a], grids[b], max_lag)
    return VenuePair(
        a=a,
        b=b,
        basis_bps=basis,
        lead_lag=lags,
        peak=peak,
        noise_band=noise_band,
        sentence=lead_sentence(labels[a], labels[b], peak, noise_band),
        funding_diff=funding[a] - funding[b],
    )


def cross_venue(
    frames: MarketFrames,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    max_lag: int,
    bucket_s: int,
) -> CrossVenue:
    """
    Compare the asset of `instrument_id` across the venues (`CrossVenue`'s invariant): every
    same-asset leg's 1 s grid (`second_frame`, one leg's seconds read at a time), then per pair of
    collected legs the mid basis in bps, the lead-lag of 1 s returns up to `max_lag` s with its peak in
    words, the per-hour funding differential per `bucket_s` bucket, and the volume share per
    venue per `bucket_s` bucket. An id `kernel.venues` cannot read has no group: one line says so.

    Two clocks: the 1 s grid is keyed on `ts_event`, venue time on Bybit and Hyperliquid but
    arrival time on dYdX (its book carries no venue time), so a peak lag also holds each venue's
    own stamping latency. `max_lag < 0` or `bucket_s <= 0` raises `ValueError`.

    Known limit: each leg's seconds (with the book lists) are read for the whole window in one
    `MarketFrames.seconds` call before `second_frame` keeps three columns, so peak memory is one
    leg's window and grows with its length; upgrade path: read and reduce the window day by day.
    """
    if max_lag < 0:
        raise ValueError(f"max_lag must be >= 0, got {max_lag}")
    if bucket_s <= 0:
        raise ValueError(f"bucket_s must be > 0, got {bucket_s}")
    asset = asset_key(instrument_id)
    if asset is None:
        line = f"{instrument_id}: kernel.venues reads no asset from this id, no cross-venue view"
        return CrossVenue(instrument_id, None, (), (), (line,), (), pd.DataFrame(), (), {})
    group = tuple(frames.same_symbol(instrument_id))
    grids = {}
    for iid in group:
        grid = second_frame(frames, iid, start_ns, end_ns)
        if np.isfinite(traded_volume(grid)).any():  # at least one observed second
            grids[iid] = grid
    collected = tuple(grids)
    funding = {iid: frames.funding(iid, start=start_ns, end=end_ns) for iid in collected}
    levels = funding_levels(funding, bucket_s, start_ns, end_ns)
    labels = _labels(collected)
    return CrossVenue(
        instrument_id=instrument_id,
        asset=asset,
        group=group,
        collected=collected,
        missing=_missing_lines(asset, collected),
        pairs=tuple(
            _pair(pair, grids, levels, labels, max_lag) for pair in combinations(collected, 2)
        ),
        volume_share=venue_volume_share(grids, bucket_s),
        venues=tuple(dict.fromkeys(venue_of(iid) for iid in collected)),
        intervals={iid: funding_intervals(frame) for iid, frame in funding.items()},
    )


def summary_lines(view: CrossVenue) -> list[str]:
    """
    Return the printable account of one `CrossVenue`: the legs compared, each missing venue's
    "not collected" line, each leg's funding intervals and each pair's lead-lag sentence -- or
    that fewer than two venues' legs hold data, so there is no pair to compare.
    """
    lines = [f"{view.instrument_id}: legs {list(view.group)}, collected {list(view.collected)}"]
    lines += list(view.missing)
    lines += [f"{iid}: funding intervals (min) {list(v)}" for iid, v in view.intervals.items()]
    lines += [pair.sentence for pair in view.pairs]
    if not view.pairs:
        lines.append(f"{view.instrument_id}: under two collected legs, no cross-venue pair")
    return lines


def distinct_assets(frames: MarketFrames, ids: Sequence[str]) -> list[str]:
    """
    Return the first id of each asset among `ids` (`MarketFrames.same_symbol` groups), in order, so
    the cross-venue section compares each asset once; an id without an asset key stands alone.
    """
    seen: set[str] = set()
    firsts = []
    for iid in ids:
        if iid in seen:
            continue
        firsts.append(iid)
        seen.update(frames.same_symbol(iid) or [iid])
    return firsts


def anchor_cluster(clusters: Mapping[str, list[list[str]]], anchor: str) -> tuple[str, ...]:
    """
    Return the cluster holding `anchor` among the per-venue clusters (`cluster` per `by_venue`
    matrix): the single-venue universe a `RunSpec` can take (one venue per run, its Known limit).
    `ValueError` when no cluster holds it.
    """
    for groups in clusters.values():
        for group in groups:
            if anchor in group:
                return tuple(group)
    raise ValueError(f"{anchor} is in no cluster")


def returns_frame(returns: AlignedReturns) -> pd.DataFrame:
    """Return the aligned returns as a frame, one column per id, indexed by UTC bucket start."""
    return pd.DataFrame(returns.matrix, index=_utc_index(returns.ts_ns), columns=list(returns.ids))
