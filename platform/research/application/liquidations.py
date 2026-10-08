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
The liquidation cascade episodes of the archive (Story 33.14): `read_liquidations` reads one
instrument's `Liquidation` rows a UTC day at a time through `kernel.catalog_files.
query_liquidations` (MEM-01), and `replay_cascade` replays `kernel.indicators.LiquidationCascade`
over them -- the strategy's own detector, never a second cascade definition (SSOT-02) -- with an
`advance` on every whole second, as the strategy's 1 s timer does.

The liquidation research of Story 33.13, every function pure over frames (`MarketFrames`'
liquidations, seconds and mark/index frames) except the one day-sliced service, `liquidation_study`,
behind `research/notebooks/09_liquidations`:

- `cascade_episodes`: `replay_cascade` over the frame's exact rows, one row per episode with the
  1 s mid's forward returns from its end (`research.domain.events.forward_returns`);
- `implied_leverage`: `mark / |mark - bankruptcy price|` per liquidation against the latest mark
  at or before it, and its distribution per UTC day;
- `forced_share`: liquidated size over traded size per minute and per hour
  (`kernel.indicators.units_ratio`);
- `match_to_trades`: each liquidation's forced trade in the trade archive (same size, forced
  aggressor, the earliest unused trade within a tolerance), the rule of the independent
  `verification.domain.liquidation_check`, restated here and never imported (DATA-02);
- `organic_delta`: each second's trade delta without its forced flow
  (`kernel.indicators.organic_delta_units`, the 33.6 per-bar formula at second resolution).

Rows exist for Bybit LINEAR only and every price is the bankruptcy price (`frames.PRICE_KIND`): an
id without the feed is given as `None` and its liquidation columns are NaN, never 0 and never
invented rows. Every sum is exact in integer units (DATA-04) and a float exists only at a
function's output.

Both select and order on `ts_init`, the receive time: the strategy feeds a row at its `ts_init`
and the backtest's `BacktestDataConfig` bounds are on `ts_init`, so the sample and the backtest see
the same rows -- but for a venue event stored twice: `query_liquidations` keeps it once (and
refuses two copies that disagree, recorded at `DUPLICATE_SITE` and raised), while the backtest
streams every stored row.

Known limit: a venue event stored twice (a minute file beside its consolidated day file while the
consolidation runs; one Bybit re-push received across a collector restart, past the capture's
dedup window) is counted once by the sample and twice by the backtest, and in the live bot when it
was published twice. Both are rare and transient; the sample's episode count can then differ from
the backtest's. Upgrade path: one dedup rule in a custom `BacktestDataConfig` loader, if a sample
ever shows it.

Known limit: `read_liquidations` returns the whole window as one list (each day is read, then the
days are joined): Bybit's BTC liquidations are thousands a day, a few hundred KB, so a research
window of days to weeks fits; upgrade path: a generator over the days, merged on `ts_init` with a
one-skew-wide hold-back, if a window ever holds more.
"""

import logging
import math
from collections.abc import Iterable
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd
from kernel.catalog_files import LiquidationDuplicateError
from kernel.catalog_files import query_liquidations
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.indicators import LiquidationCascade
from kernel.indicators import organic_delta_units
from kernel.indicators import units_ratio
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.second_snapshot import SnapshotEncodingError
from kernel.second_snapshot import unit_float
from kernel.venues import venue_of
from observability import error_ledger

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from research.application.aligned import CASCADE_PAIR_COLUMNS
from research.application.aligned import CascadeLeadLag
from research.application.aligned import cross_venue_liquidations
from research.application.aligned import finest_sum
from research.application.aligned import liquidations_vs_oi
from research.application.aligned import oi_window_start
from research.application.frames import INSTRUMENT_ATTR
from research.application.frames import PRICE_KIND
from research.application.frames import liquidations_frame
from research.application.inspection import second_grid
from research.application.ports import MarketFrames
from research.domain.events import forward_returns


logger = logging.getLogger(__name__)
# The error-ledger site of a replayed row whose notional the replay's precisions cannot hold (the
# strategy's own is `liquidation_cascade_strategy.UNSCALABLE_ROW_SITE`; the rule is the same).
UNSCALABLE_ROW_SITE = "research.liquidations.unscalable_row"
# The error-ledger site of a venue event stored twice with different values (`query_liquidations`
# refuses it, the caller records it, DATA-07); the read then raises.
DUPLICATE_SITE = "research.liquidations.duplicate"
# The error-ledger site of any other refused liquidation read; the read then raises.
READ_FAILED_SITE = "research.liquidations.read_failed"


@dataclass(frozen=True)
class CascadeEpisode:
    """
    One episode of the replayed detector.

    Invariant: `start_ns` is the detector's clock at the first `active` update, `end_ns` the clock
    at its `episode_ended` update (None when the replay ended inside the episode); `direction` is
    the episode's (-1: longs liquidated, the price falling; +1: shorts); `peak_rate` the highest
    total rate in units per second and `notional_units` the episode's notional, both at the
    replay's one precision (units of `10^-(price_precision + size_precision)`).
    """

    start_ns: int
    end_ns: int | None
    direction: int
    peak_rate: float
    notional_units: int


class CascadeEpisodes(list[CascadeEpisode]):
    """
    `replay_cascade`'s episodes, in order, plus `unscalable_rows`: the rows it skipped because
    the replay's precisions cannot hold their notional (each recorded in the error ledger).

    Invariant: a plain list of `CascadeEpisode` in every other respect (equal to the list of the
    same episodes), so the skip count travels with the result it qualifies.
    """

    def __init__(self, episodes: Iterable[CascadeEpisode] = (), unscalable_rows: int = 0) -> None:
        super().__init__(episodes)
        self.unscalable_rows = unscalable_rows


def read_liquidations(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[Liquidation]:
    """
    Return the instrument's archived liquidations with `ts_init` in `[start_ns, end_ns)` (the
    backtest's selection), in `(ts_init, venue_event_id)` order, each venue event once; `[]`
    without the feed. `query_liquidations` selects on `ts_event` (inclusive bounds), so the read
    is widened by `MAX_TS_INIT_SKEW_NS` before the start (a row received inside the window may be
    stamped up to that much earlier) and filtered on `ts_init`; it goes one UTC day at a time
    (each day ends a nanosecond before the next). Two stored copies of one venue event that
    disagree are recorded at `DUPLICATE_SITE` and the `ValueError` raised, never one picked.
    """
    rows: list[Liquidation] = []
    day_start = max(0, start_ns - MAX_TS_INIT_SKEW_NS)
    while day_start < end_ns:
        day_end = min((day_start // NS_PER_DAY + 1) * NS_PER_DAY, end_ns)
        try:
            day = query_liquidations(catalog_path, instrument_id, day_start, day_end - 1)
        except LiquidationDuplicateError as exc:
            error_ledger.record(DUPLICATE_SITE, f"{instrument_id} liquidations not read", exc)
            raise
        except ValueError as exc:
            error_ledger.record(READ_FAILED_SITE, f"{instrument_id} liquidations not read", exc)
            raise
        rows += [row for row in day if start_ns <= row.ts_init < end_ns]
        day_start = day_end
    return sorted(rows, key=lambda row: (row.ts_init, row.venue_event_id))


def _precisions(rows: list[Liquidation], precisions: tuple[int, int] | None) -> tuple[int, int]:
    if precisions is not None:
        return precisions
    if not rows:
        return (0, 0)  # nothing to scale
    return (rows[0].price_precision, rows[0].size_precision)


def replay_cascade(
    rows: Iterable[Liquidation],
    window_s: int,
    baseline_s: int,
    intensity_threshold: float,
    decay_ratio: float,
    end_ns: int,
    start_ns: int | None = None,
    precisions: tuple[int, int] | None = None,
) -> CascadeEpisodes:
    """
    Replay `LiquidationCascade` over `rows` (by `ts_init`, the receive time the strategy feeds)
    with an `advance` on every whole second after the one at or before `start_ns` (default: the
    first row's `ts_init`) up to `end_ns` (exclusive) -- the strategy's 1 s timer started at
    `start_ns`, whose first event is one interval after the whole second at or before its start --
    a second's tick before a row received in that very nanosecond (the backtest's order), and
    return its episodes in order.

    Every row's notional is taken at `precisions` (the instrument definition's `(price, size)`;
    default: the first row's) through `Liquidation.notional_units_at`. A row that precision cannot
    hold is never rounded: as in the strategy, it is skipped, recorded in the error ledger
    (`UNSCALABLE_ROW_SITE`) and counted in the result's `unscalable_rows`.
    """
    ordered = sorted(rows, key=lambda row: (row.ts_init, row.venue_event_id))
    scale = _precisions(ordered, precisions)
    if start_ns is None:
        start_ns = ordered[0].ts_init if ordered else end_ns
    cascade = LiquidationCascade(window_s, baseline_s, intensity_threshold, decay_ratio)
    episodes = CascadeEpisodes()
    tick = start_ns // NS_PER_S * NS_PER_S + NS_PER_S  # the strategy timer's first event
    for row in ordered:
        if row.ts_init >= end_ns:
            break
        while tick <= row.ts_init:
            _update(cascade, episodes, tick, None, scale)
            tick += NS_PER_S
        _update(cascade, episodes, row.ts_init, row, scale)
    while tick < end_ns:
        _update(cascade, episodes, tick, None, scale)
        tick += NS_PER_S
    _close_open_episode(cascade, episodes)
    return episodes


def _update(
    cascade: LiquidationCascade,
    episodes: CascadeEpisodes,
    ts_ns: int,
    row: Liquidation | None,
    scale: tuple[int, int],
) -> None:
    if row is None:
        cascade.advance(ts_ns)
    else:
        units = _units(row, scale, episodes)
        if units is None:
            return  # skipped and counted: the detector is not updated, as in the strategy
        cascade.update_liquidation(row.side, units, ts_ns)
    if cascade.episode_ended:
        episodes.append(_episode(cascade, cascade.clock_ns))


def _units(row: Liquidation, scale: tuple[int, int], episodes: CascadeEpisodes) -> int | None:
    """Return the row's notional at `scale`; None, ledgered and counted, when it is not exact."""
    try:
        return row.notional_units_at(*scale)
    except SnapshotEncodingError as exc:
        episodes.unscalable_rows += 1
        error_ledger.record(
            UNSCALABLE_ROW_SITE,
            f"{row.instrument_id} liquidation {row.venue_event_id} skipped "
            f"({episodes.unscalable_rows} so far): {exc}",
            exc,
        )
        return None


def _episode(cascade: LiquidationCascade, end_ns: int | None) -> CascadeEpisode:
    assert cascade.episode_start_ns is not None  # an episode is open
    return CascadeEpisode(
        start_ns=cascade.episode_start_ns,
        end_ns=end_ns,
        direction=cascade.episode_direction,
        peak_rate=cascade.peak_rate,
        notional_units=cascade.episode_notional_units,
    )


def _close_open_episode(cascade: LiquidationCascade, episodes: CascadeEpisodes) -> None:
    """Keep an episode the replay ended inside, with no end."""
    if cascade.episode_start_ns is not None and not cascade.episode_ended:
        episodes.append(_episode(cascade, None))


# ---------------------------------------------------------------------------------------------
# Story 33.13: the liquidation research over frames
# ---------------------------------------------------------------------------------------------

# The forward-return horizons of an episode (seconds after its end) and their column names.
FORWARD_HORIZONS_S = {"fwd_1m": 60, "fwd_5m": 300, "fwd_15m": 900, "fwd_60m": 3_600}
EPISODE_COLUMNS = (
    "start_ns",
    "end_ns",
    "direction",
    "side",
    "duration_s",
    "peak_rate",
    "notional",
    *FORWARD_HORIZONS_S,
)
LEVERAGE_COLUMNS = ("ts_event", "side", "price", "mark", "mark_age_s", "leverage", "wrong_side")
LEVERAGE_PERCENTILES = (10, 25, 50, 75, 90)
LEVERAGE_DAY_COLUMNS = (
    "count",
    "finite",
    "wrong_side",
    *(f"p{q}" for q in LEVERAGE_PERCENTILES),
)
FORCED_SHARE_COLUMNS = (
    "forced_units",
    "traded_units",
    "size_precision",
    "seconds_observed",
    "share",
)
MATCH_COLUMNS = ("ts_event", "venue_event_id", "side", "size", "matched", "offset_s", "trade_size")
ORGANIC_COLUMNS = (
    "delta_units",
    "liq_long_units",
    "liq_short_units",
    "organic_units",
    "organic",
    "size_precision",
)
ORGANIC_MINUTE_COLUMNS = ("delta", "organic", "seconds_observed")
# A liquidated long is closed by a forced sell, so its trade's aggressor is the seller.
_FORCED_AGGRESSOR = {
    LiquidatedSide.LONG: AggressorSide.SELLER,
    LiquidatedSide.SHORT: AggressorSide.BUYER,
}
# The significant digits a mark is read back to from the mark/index frame's float: a double holds
# 15 (DBL_DIG) exactly, so the decimal of a mark of at most 15 significant digits is recovered as
# written, while the float itself (`Price.as_double`) can sit an ulp off it -- a mark equal to the
# bankruptcy price must read as no distance, not as a leverage of 10^16.
# Known limit: a mark of more than 15 significant digits is read rounded to 15; upgrade path: the
# exact `MarkPriceUpdate` objects (`MarketFrames.objects`) instead of the frame's float.
_MARK_DIGITS = 15
# `direction` (-1 longs liquidated, +1 shorts) -> the liquidated side it names.
_SIDE_OF_DIRECTION = {-1: LiquidatedSide.LONG.value, 1: LiquidatedSide.SHORT.value}


def rows_of(liqs: pd.DataFrame) -> list[Liquidation]:
    """
    Rebuild the exact `Liquidation` rows of a liquidations frame (`frames.liquidations_frame`)
    from its integer columns -- never from the decoded floats -- in frame order. The instrument is
    the frame's `attrs[INSTRUMENT_ATTR]`; a non-empty frame without it raises `ValueError`.
    """
    if liqs.empty:
        return []
    if INSTRUMENT_ATTR not in liqs.attrs:
        raise ValueError(f"a liquidations frame needs attrs[{INSTRUMENT_ATTR!r}]")
    iid = InstrumentId.from_str(str(liqs.attrs[INSTRUMENT_ATTR]))
    columns = zip(
        liqs["side"].tolist(),
        liqs["size_units"].tolist(),
        liqs["price_units"].tolist(),
        liqs["price_precision"].tolist(),
        liqs["size_precision"].tolist(),
        liqs["venue_event_id"].tolist(),
        liqs["ts_event"].tolist(),
        liqs["ts_init"].tolist(),
        strict=True,
    )
    return [
        Liquidation(
            iid,
            LiquidatedSide(side),
            int(su),
            int(pu),
            int(pp),
            int(sp),
            str(vid),
            int(te),
            int(ti),
        )
        for side, su, pu, pp, sp, vid, te, ti in columns
    ]


def rescaled_units(units: int, precision: int, target: int) -> int:
    """
    Return `units` of `10^-precision` as units of `10^-target`, exactly: a multiplication to a
    finer target, an exact division to a coarser one; `SnapshotEncodingError` when the coarser
    target cannot hold the value (never rounded, DATA-04).
    """
    if target >= precision:
        return units * 10 ** (target - precision)
    quotient, remainder = divmod(units, 10 ** (precision - target))
    if remainder:
        raise SnapshotEncodingError(
            f"{units} units of 10^-{precision} are not exact at 10^-{target}"
        )
    return quotient


def _index_ns(index: pd.Index) -> np.ndarray:
    """Return a (UTC) `DatetimeIndex` as int64 ns since the epoch."""
    return pd.DatetimeIndex(index).to_numpy(dtype="datetime64[ns]").astype("int64")


def _grid_seconds(mids: pd.Series) -> np.ndarray:
    """Return a 1 s grid's index as whole UTC seconds, refusing a grid with a missing second."""
    seconds = _index_ns(mids.index) // NS_PER_S
    if len(seconds) > 1 and not bool((seconds[1:] - seconds[:-1] == 1).all()):
        raise ValueError("mids must be a complete 1 s grid (a missing second is a NaN row)")
    return seconds


def _episode_returns(episodes: CascadeEpisodes, mids: pd.Series) -> dict[str, np.ndarray]:
    """Each episode's forward mid returns from its end second; NaN when open or off the grid."""
    seconds = _grid_seconds(mids)
    out = {name: np.full(len(episodes), math.nan) for name in FORWARD_HORIZONS_S}
    if not len(seconds):
        return out
    ends = {k: e.end_ns // NS_PER_S for k, e in enumerate(episodes) if e.end_ns is not None}
    hits = [k for k, end in ends.items() if seconds[0] <= end <= seconds[-1]]
    positions = [int(ends[k] - seconds[0]) for k in hits]
    returns = forward_returns(
        mids.to_numpy(dtype="float64"), positions, list(FORWARD_HORIZONS_S.values())
    )
    for name, horizon in FORWARD_HORIZONS_S.items():
        out[name][hits] = returns[horizon]
    return out


def _notional_scale(rows: list[Liquidation], precisions: tuple[int, int] | None) -> int:
    """Return the replay's notional precision (`price + size`): `replay_cascade`'s own choice of scale."""
    ordered = sorted(rows, key=lambda row: (row.ts_init, row.venue_event_id))
    return sum(_precisions(ordered, precisions))


def cascade_episodes(
    liqs: pd.DataFrame,
    mids: pd.Series,
    *,
    window_s: int,
    baseline_s: int,
    intensity_threshold: float,
    decay_ratio: float,
    precisions: tuple[int, int] | None,
    start_ns: int,
    end_ns: int,
) -> pd.DataFrame:
    """
    Return one row per episode of `replay_cascade` over the frame's exact rows (`rows_of`), from
    `start_ns` to `end_ns` at `precisions` (None: the first row's): `start_ns`, `end_ns` (NA while
    open), `direction` and the liquidated `side` it names (`long` for -1), `duration_s` (NaN while
    open), `peak_rate` (quote per second) and `notional` (quote at the bankruptcy price), plus the
    mid's forward return over each `FORWARD_HORIZONS_S` horizon from the episode's end second,
    `mids[e + h] / mids[e] - 1` on the 1 s grid `mids` (indexed by second, `inspection.
    second_grid`'s). An open episode, an end off the grid, a missing mid or a horizon past the grid
    is NaN, never filled. `attrs["unscalable_rows"]` is the replay's skip count.
    """
    rows = rows_of(liqs)
    episodes = replay_cascade(
        rows,
        window_s,
        baseline_s,
        intensity_threshold,
        decay_ratio,
        end_ns,
        start_ns=start_ns,
        precisions=precisions,
    )
    scale = _notional_scale(rows, precisions)
    frame = pd.DataFrame(
        {
            "start_ns": [e.start_ns for e in episodes],
            "end_ns": pd.array([e.end_ns for e in episodes], dtype="Int64"),
            "direction": [e.direction for e in episodes],
            "side": [_SIDE_OF_DIRECTION.get(e.direction) for e in episodes],
            "duration_s": [
                math.nan if e.end_ns is None else (e.end_ns - e.start_ns) / NS_PER_S
                for e in episodes
            ],
            "peak_rate": [e.peak_rate / 10.0**scale for e in episodes],
            "notional": [unit_float(e.notional_units, scale) for e in episodes],
            **_episode_returns(episodes, mids),
        },
        columns=list(EPISODE_COLUMNS),
    )
    frame.attrs["unscalable_rows"] = episodes.unscalable_rows
    return frame


@dataclass(frozen=True)
class ImpliedLeverage:
    """
    `implied_leverage`'s result.

    Invariant: `per_liquidation` has one row per liquidation (`LEVERAGE_COLUMNS`, NaN `leverage`
    where it is undefined, `wrong_side` True where the bankruptcy price is on the wrong side of the
    mark); `per_day` one row per UTC day holding a liquidation, `count` every row of the day (NaN
    leverage included), `finite` those with a leverage, `wrong_side` those on the wrong side, and
    the percentiles over the finite ones alone (NaN when there are none).
    """

    per_liquidation: pd.DataFrame
    per_day: pd.DataFrame


def _leverage(
    price_units: int, price_precision: int, mark: float, price_kind: str, side: str
) -> tuple[float, bool]:
    """
    Return `(mark / |mark - price|, wrong_side)` with the price exact from its units and the mark
    as the decimal of its float to `_MARK_DIGITS` significant digits. The leverage is NaN without
    a mark, for a price that is not a bankruptcy price, and when the price is not strictly on the
    loss side of the mark -- a long's bankruptcy price below it, a short's above it: a long at or
    above the mark (or a short at or below it) is `wrong_side` (a stale mark, or a row that is no
    ordinary liquidation), never read as a leverage.
    """
    if price_kind != PRICE_KIND or not math.isfinite(mark):
        return math.nan, False
    exact_mark = Decimal(f"{mark:.{_MARK_DIGITS}g}")
    price = Decimal(price_units).scaleb(-price_precision)
    long = side == LiquidatedSide.LONG.value
    if (price >= exact_mark) if long else (price <= exact_mark):
        return math.nan, True
    return float(exact_mark / abs(exact_mark - price)), False


def _latest_marks(
    liq_ts: np.ndarray, mark_index: pd.DataFrame, max_age_ns: int
) -> tuple[np.ndarray, np.ndarray]:
    """For each stamp, the latest mark at or before it within `max_age_ns` and its age (ns)."""
    marks = mark_index[np.isfinite(mark_index["mark"].to_numpy(dtype="float64"))]
    mark_ts = marks["ts_event"].to_numpy(dtype="int64")
    values = marks["mark"].to_numpy(dtype="float64")
    found = np.searchsorted(mark_ts, liq_ts, side="right") - 1
    mark = np.full(len(liq_ts), math.nan)
    age = np.full(len(liq_ts), math.nan)
    known = found >= 0
    age[known] = (liq_ts[known] - mark_ts[found[known]]).astype("float64")
    fresh = known & (age <= max_age_ns)
    mark[fresh] = values[found[fresh]]
    age[~fresh] = math.nan
    return mark, age


def _leverage_per_day(per_liquidation: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    days = per_liquidation["ts_event"].to_numpy(dtype="int64") // NS_PER_DAY
    leverage = per_liquidation["leverage"].to_numpy(dtype="float64")
    wrong = per_liquidation["wrong_side"].to_numpy(dtype=bool)
    for day in sorted(set(days.tolist())):
        values = leverage[days == day]
        finite = values[np.isfinite(values)]
        wrong_side = int(wrong[days == day].sum())
        quantiles = (
            np.percentile(finite, LEVERAGE_PERCENTILES)
            if finite.size
            else np.full(len(LEVERAGE_PERCENTILES), math.nan)
        )
        rows[day * NS_PER_DAY] = [len(values), int(finite.size), wrong_side, *quantiles.tolist()]
    index = pd.DatetimeIndex(pd.to_datetime(list(rows), unit="ns", utc=True), name="day")
    return pd.DataFrame(list(rows.values()), index=index, columns=list(LEVERAGE_DAY_COLUMNS))


def implied_leverage(
    liqs: pd.DataFrame, mark_index: pd.DataFrame, max_mark_age_s: int = 5
) -> ImpliedLeverage:
    """
    Each liquidation's implied leverage, `mark / |mark - price|` (`_leverage`), against the latest
    mark of `mark_index` (`MarketFrames.mark_index`) at or before its `ts_event` and at most
    `max_mark_age_s` older; a liquidation with no such mark has NaN `mark`, `mark_age_s` and
    leverage, and one whose bankruptcy price is not on its loss side of the mark is NaN and
    counted `wrong_side` (`_leverage`). `per_day` (UTC) counts every row and takes the
    10/25/50/75/90th percentiles of the finite leverages. E.g. a long liquidated at a bankruptcy
    price of 95 with the mark at 100: 100 / 5 = 20.

    Known limit: what this measures is not the leverage the trader chose. A position is liquidated
    when the mark reaches its liquidation price, where the equity left is the maintenance margin
    (plus the closing fee); the bankruptcy price lies that margin beyond it. So at the trigger
    `mark / |mark - bankruptcy|` is about `1 / (maintenance margin rate + fee rate)` for an
    isolated position, i.e. it reflects the venue's risk tier of the position's size (Bybit's
    maintenance margin tiers), not its opening leverage, and a cross-margined position's distance
    is set by the whole account. A mark-priced row (Hyperliquid's liquidation fills) would make
    the distance 0, so any `price_kind` but `bankruptcy` is NaN -- none exists yet (no Hyperliquid
    feed, Story 33.2; audit D-221). Upgrade path: the position's own margin and leverage, which no
    public feed carries.
    """
    liq_ts = liqs["ts_event"].to_numpy(dtype="int64")
    mark, age = _latest_marks(liq_ts, mark_index, max_mark_age_s * NS_PER_S)
    columns = zip(
        liqs["price_units"].tolist(),
        liqs["price_precision"].tolist(),
        mark.tolist(),
        liqs["price_kind"].tolist(),
        liqs["side"].tolist(),
        strict=True,
    )
    results = [_leverage(int(pu), int(pp), m, str(k), str(sd)) for pu, pp, m, k, sd in columns]
    per_liquidation = pd.DataFrame(
        {
            "ts_event": liq_ts,
            "side": liqs["side"].tolist(),
            "price": liqs["price"].to_numpy(dtype="float64"),
            "mark": mark,
            "mark_age_s": age / NS_PER_S,
            "leverage": [value for value, _ in results],
            "wrong_side": [wrong for _, wrong in results],
        },
        index=liqs.index,
        columns=list(LEVERAGE_COLUMNS),
    )
    return ImpliedLeverage(per_liquidation, _leverage_per_day(per_liquidation))


@dataclass(frozen=True)
class ForcedShare:
    """
    `forced_share`'s result: `per_minute` and `per_hour`, each indexed by the bucket's UTC start
    with `FORCED_SHARE_COLUMNS`.

    Invariant: `forced_units` and `traded_units` are exact sums at the bucket's finest
    `size_precision`; `share` is `units_ratio` of the two, NaN at 0 traded; a bucket with neither
    a snapshot nor a liquidation is absent, never a 0; without a feed `forced_units` is NA and
    `share` NaN.
    """

    per_minute: pd.DataFrame
    per_hour: pd.DataFrame


def _bucket_units(
    stamps: Sequence[int], units: Sequence[int], precisions: Sequence[int], bucket_ns: int
) -> dict[int, tuple[int, int]]:
    """Each bucket's exact sum (`finest_sum`) of the values whose stamp falls in it."""
    parts: dict[int, list[tuple[int, int]]] = {}
    for ts, value, precision in zip(stamps, units, precisions, strict=True):
        parts.setdefault(int(ts) // bucket_ns, []).append((int(value), int(precision)))
    return {key: finest_sum(values) for key, values in parts.items()}


def _share_row(
    forced: tuple[int, int] | None, traded: tuple[int, int], seconds: int
) -> dict[str, object]:
    precision = max(traded[1], forced[1] if forced is not None else 0)
    traded_units = rescaled_units(*traded, precision)
    forced_units = None if forced is None else rescaled_units(*forced, precision)
    ratio = (
        None
        if forced_units is None
        else units_ratio(forced_units, precision, traded_units, precision)
    )
    return {
        "forced_units": forced_units,
        "traded_units": traded_units,
        "size_precision": precision,
        "seconds_observed": seconds,
        "share": math.nan if ratio is None else ratio,
    }


def _forced_units(liqs: pd.DataFrame, bucket_ns: int) -> dict[int, tuple[int, int]]:
    """Each bucket's exact liquidated size, by `ts_event`."""
    return _bucket_units(
        liqs["ts_event"].tolist(),
        liqs["size_units"].tolist(),
        liqs["size_precision"].tolist(),
        bucket_ns,
    )


def _forced_share_buckets(
    liqs: pd.DataFrame | None, seconds: pd.DataFrame, bucket_s: int
) -> pd.DataFrame:
    bucket_ns = bucket_s * NS_PER_S
    stamps = seconds["ts_event"].tolist()
    traded = _bucket_units(
        stamps,
        (seconds["buy_volume_units"] + seconds["sell_volume_units"]).tolist(),
        seconds["size_precision"].tolist(),
        bucket_ns,
    )
    observed = pd.Series(stamps, dtype="int64").floordiv(bucket_ns).value_counts().to_dict()
    forced = None if liqs is None else _forced_units(liqs, bucket_ns)
    keys = sorted(set(traded) | set(forced or {}))
    rows = [
        _share_row(
            None if forced is None else forced.get(key, (0, 0)),
            traded.get(key, (0, 0)),
            int(observed.get(key, 0)),
        )
        for key in keys
    ]
    frame = pd.DataFrame(rows, columns=list(FORCED_SHARE_COLUMNS))
    frame["forced_units"] = pd.array(frame["forced_units"].tolist(), dtype="Int64")
    frame.index = pd.DatetimeIndex(
        pd.to_datetime([key * bucket_ns for key in keys], unit="ns", utc=True), name="ts"
    )
    return frame


def forced_share(liqs: pd.DataFrame | None, seconds: pd.DataFrame) -> ForcedShare:
    """
    Return the share of traded volume that was forced, per minute and per hour (`ForcedShare`): the
    liquidated size (`liqs` by `ts_event`; None without a feed) over the snapshots' traded size,
    `buy_volume_units + sell_volume_units` (the `seconds` frame by its rows' `ts_event`), both
    exact at the bucket's finest size precision, as `kernel.indicators.units_ratio` -- the
    `ForcedShare` indicator's formula (Story 33.6). E.g. 2 units liquidated over 8 traded: 0.25.
    A share can exceed 1 when the bucket's snapshots miss some of its trades (a gap).
    """
    return ForcedShare(
        _forced_share_buckets(liqs, seconds, 60), _forced_share_buckets(liqs, seconds, 3_600)
    )


@dataclass(frozen=True)
class TradeMatch:
    """
    `match_to_trades`'s result.

    Invariant: `per_liquidation` has one row per liquidation in `(ts_event, venue_event_id)` order
    (`MATCH_COLUMNS`): `matched`, the matched trade's `offset_s` (trade minus liquidation `ts_event`)
    and `trade_size`, both NaN when unmatched; `total`, `matched` and `share` are read from it.
    """

    per_liquidation: pd.DataFrame

    @property
    def total(self) -> int:
        return len(self.per_liquidation)

    @property
    def matched(self) -> int:
        return int(self.per_liquidation["matched"].astype(bool).sum())

    @property
    def share(self) -> float | None:
        """The matched share; None with no liquidation (no share, never 0)."""
        return self.matched / self.total if self.total else None


# One (aggressor, exact size raw) key's trades by time: their `ts_event`s and their positions.
_TradeEntries = tuple[np.ndarray, list[int]]


def _trade_index(trades: Sequence[TradeTick]) -> dict[tuple[AggressorSide, int], _TradeEntries]:
    """
    Index trades by (aggressor, exact size raw), each key's `ts_event`s sorted once (with their
    positions), so a lookup is one binary search, never a per-liquidation rebuild.
    """
    grouped: dict[tuple[AggressorSide, int], list[tuple[int, int]]] = {}
    for position, trade in enumerate(trades):
        key = (trade.aggressor_side, trade.size.raw)
        grouped.setdefault(key, []).append((trade.ts_event, position))
    index = {}
    for key, entries in grouped.items():
        entries.sort()
        index[key] = (
            np.array([ts for ts, _ in entries], dtype="int64"),
            [position for _, position in entries],
        )
    return index


def _earliest_unused(
    entries: _TradeEntries, ts_event: int, tol_ns: int, used: set[int]
) -> tuple[int, int] | None:
    """Return the earliest unused entry with `ts_event` in `[ts - tol, ts + tol]`, or None."""
    stamps, positions = entries
    low = int(np.searchsorted(stamps, ts_event - tol_ns, side="left"))
    for k in range(low, len(positions)):
        ts = int(stamps[k])
        if ts > ts_event + tol_ns:
            return None
        if positions[k] not in used:
            return ts, positions[k]
    return None


def match_to_trades(
    liqs: pd.DataFrame, trades: Sequence[TradeTick], tol_s: float = 2
) -> TradeMatch:
    """
    Match each liquidation to its forced trade: a trade of the same exact size (the liquidation's
    units as a `Quantity` against `trade.size.raw`) whose aggressor is the forced side (a
    liquidated long is a seller-aggressor trade, a short a buyer one) and whose `ts_event` lies
    within `tol_s` of the liquidation's. Liquidations go earliest first and each takes the
    earliest unused trade, so each trade is used once -- `verification.domain.
    liquidation_check`'s rule (a maximum matching with equal windows), restated, never imported.

    Known limit: a liquidation filled by several trades (split fills) matches none of them, so the
    matched share undercounts the forced trades (audit D-222); upgrade path: a sum-of-fills match
    over the window, as the oracle's own Known limit names.
    """
    rows = sorted(rows_of(liqs), key=lambda row: (row.ts_event, row.venue_event_id))
    index = _trade_index(trades)
    used: set[int] = set()
    out = []
    for row in rows:
        key = (_FORCED_AGGRESSOR[row.side], row.size.raw)
        entries = index.get(key)
        found = (
            None
            if entries is None
            else _earliest_unused(entries, row.ts_event, int(tol_s * NS_PER_S), used)
        )
        if found is not None:
            used.add(found[1])
        out.append(
            {
                "ts_event": row.ts_event,
                "venue_event_id": row.venue_event_id,
                "side": row.side.value,
                "size": row.size.as_double(),
                "matched": found is not None,
                "offset_s": math.nan if found is None else (found[0] - row.ts_event) / NS_PER_S,
                "trade_size": math.nan if found is None else trades[found[1]].size.as_double(),
            }
        )
    return TradeMatch(pd.DataFrame(out, columns=list(MATCH_COLUMNS)))


def _second_rows(seconds: pd.DataFrame) -> dict[int, list[int]]:
    """Each snapshot second (`ts_event // 1 s`) -> the positions of its rows (two: ambiguous)."""
    rows: dict[int, list[int]] = {}
    for position, ts in enumerate(seconds["ts_event"].tolist()):
        rows.setdefault(int(ts) // NS_PER_S, []).append(position)
    return rows


@dataclass
class _Attribution:
    """The liquidated size per row and side, the rows with unknown forced flow, the strays."""

    long_units: list[int]
    short_units: list[int]
    unknown: set[int]
    unattributed: int = 0


def _attribute(seconds: pd.DataFrame, liqs: pd.DataFrame) -> _Attribution:
    """
    Place each liquidation in its second's row at the row's size precision (`organic_delta`'s
    rule): a second with no row counts it unattributed, a second with two rows makes them unknown
    (and counts it), a size the row's precision cannot hold is ledgered and makes the row unknown.
    """
    by_second = _second_rows(seconds)
    precisions = seconds["size_precision"].tolist()
    out = _Attribution([0] * len(seconds), [0] * len(seconds), set())
    for row in rows_of(liqs):
        positions = by_second.get(row.ts_event // NS_PER_S, [])
        if len(positions) != 1:
            out.unattributed += 1
            out.unknown.update(positions)
            continue
        (position,) = positions
        try:
            units = rescaled_units(row.size_units, row.size_precision, int(precisions[position]))
        except SnapshotEncodingError as exc:
            error_ledger.record(
                UNSCALABLE_ROW_SITE,
                f"{row.instrument_id} liquidation {row.venue_event_id}: its second's organic "
                f"delta is unknown: {exc}",
                exc,
            )
            out.unknown.add(position)
            continue
        side = out.long_units if row.side == LiquidatedSide.LONG else out.short_units
        side[position] += units
    return out


def _organic_columns(
    seconds: pd.DataFrame, liqs: pd.DataFrame | None
) -> tuple[dict[str, list], int]:
    """
    Return the forced-flow columns of `organic_delta` (None where the flow is unknown) and the count of
    unattributed liquidations.
    """
    buy = [int(v) for v in seconds["buy_volume_units"].tolist()]
    sell = [int(v) for v in seconds["sell_volume_units"].tolist()]
    if liqs is None:
        unknown = [None] * len(buy)
        names = ("liq_long_units", "liq_short_units", "organic_units")
        return dict.fromkeys(names, unknown), 0
    placed = _attribute(seconds, liqs)
    known = [k not in placed.unknown for k in range(len(buy))]
    columns = {
        "liq_long_units": [
            u if ok else None for u, ok in zip(placed.long_units, known, strict=True)
        ],
        "liq_short_units": [
            u if ok else None for u, ok in zip(placed.short_units, known, strict=True)
        ],
        "organic_units": [
            organic_delta_units(b, s, lu, su) if ok else None
            for b, s, lu, su, ok in zip(
                buy, sell, placed.long_units, placed.short_units, known, strict=True
            )
        ],
    }
    return columns, placed.unattributed


def organic_delta(seconds: pd.DataFrame, liqs: pd.DataFrame | None) -> pd.DataFrame:
    """
    One row per `seconds` row (its index), in its `10^-size_precision` units: `delta_units`
    (`buy_volume_units - sell_volume_units`), `liq_long_units`/`liq_short_units` (the sizes of the
    liquidations placed in that second, rescaled exactly to the row's size precision),
    `organic_units` (`kernel.indicators.organic_delta_units`, the 33.6 per-bar formula at second
    resolution), `organic` (decoded once) and the row's `size_precision`;
    `attrs["unattributed"]` counts the liquidations no row took. E.g. buy 10, sell 4, liq_long 3,
    liq_short 1: (10 - 1) - (4 - 3) = 8.

    A liquidation belongs to the second `ts_event // 1 s` and a row to the second of its own
    `ts_event`: the capture service's rule for trades on an exchange-timed venue (Bybit,
    `book_time_source = "venue"`): `capture/domain/trade_intake.py`'s `TradeIntake.fold` buckets a
    trade by `second = trade.ts_event // S_NS`, and `capture/domain/sampler.py`'s
    `SecondSampler._row` stamps that second's row `ts_event = second * S_NS + _HALF_S_NS`
    (`archive.rebuild_seconds` re-folds on the same `ts_event` second). A liquidation in a second
    with no row (an unsampled second, a gap) is counted `unattributed`, never moved to a
    neighbour; one in a second with two rows makes both rows' forced flow unknown. A size the
    row's precision cannot hold is ledgered at `UNSCALABLE_ROW_SITE` and its row's liquidation
    and organic columns are NA (DATA-07). `liqs = None` (no feed) makes them NA on every row.

    Known limit: the venue's liquidation `ts_event` and its forced trade's `ts_event` are two
    stamps of one event that may fall in adjacent seconds, so a forced trade near a second's edge
    can be netted in the next second (audit D-220); upgrade path: net the matched forced trade
    (`match_to_trades`) in its own second.
    """
    columns, unattributed = _organic_columns(seconds, liqs)
    precisions = [int(p) for p in seconds["size_precision"].tolist()]
    buy, sell = seconds["buy_volume_units"].tolist(), seconds["sell_volume_units"].tolist()
    frame = pd.DataFrame(index=seconds.index)
    frame["delta_units"] = pd.array(
        [int(b) - int(s) for b, s in zip(buy, sell, strict=True)], dtype="Int64"
    )
    for name in ("liq_long_units", "liq_short_units", "organic_units"):
        frame[name] = pd.array(columns[name], dtype="Int64")
    frame["organic"] = [
        math.nan if units is None else unit_float(units, p)
        for units, p in zip(columns["organic_units"], precisions, strict=True)
    ]
    frame["size_precision"] = precisions
    frame.attrs["unattributed"] = unattributed
    return frame


def organic_per_minute(organic: pd.DataFrame) -> pd.DataFrame:
    """
    Sum an `organic_delta` frame per UTC minute (by its index): `delta` and `organic` decoded from
    exact sums at the minute's finest size precision, and `seconds_observed`. A minute holding a
    second with an unknown organic delta has a NaN `organic` (never the known part alone); a
    minute with no row is absent.
    """
    keys = (_index_ns(organic.index) // (60 * NS_PER_S)).tolist()
    parts: dict[int, tuple[list, list]] = {}
    columns = zip(
        keys,
        organic["delta_units"].tolist(),
        organic["organic_units"].tolist(),
        organic["size_precision"].tolist(),
        strict=True,
    )
    for key, delta, units, precision in columns:
        deltas, organics = parts.setdefault(key, ([], []))
        deltas.append((int(delta), int(precision)))
        organics.append(None if pd.isna(units) else (int(units), int(precision)))
    rows = [
        {
            "delta": unit_float(*finest_sum(deltas)),
            "organic": math.nan if None in organics else unit_float(*finest_sum(organics)),
            "seconds_observed": len(deltas),
        }
        for deltas, organics in parts.values()
    ]
    index = pd.DatetimeIndex(
        pd.to_datetime([key * 60 * NS_PER_S for key in parts], unit="ns", utc=True), name="ts"
    )
    return pd.DataFrame(rows, index=index, columns=list(ORGANIC_MINUTE_COLUMNS))


@dataclass(frozen=True)
class LiquidationStudyConfig:
    """
    The study's parameters: the cascade detector's (`LiquidationCascadeStrategy`'s defaults), the
    oldest mark an implied leverage takes, the trade match's tolerance, the OI bucket and the
    cross-venue lag. Invariant: each is passed unchanged to the one function that reads it.
    """

    window_s: int = 30
    baseline_s: int = 3_600
    intensity_threshold: float = 3.0
    decay_ratio: float = 0.5
    max_mark_age_s: int = 5
    match_tol_s: int = 2
    oi_bucket_s: int = 300
    max_lag_s: int = 300


@dataclass(frozen=True)
class LiquidationStudy:
    """
    Everything `research/notebooks/09_liquidations` shows for one instrument and window.

    Invariant: every frame is one of this module's (or `aligned`'s) functions over the window's
    reads, the per-day results joined day after day (the per-liquidation frames on a fresh unique
    index); `has_feed` is False exactly for an id without a liquidation feed, whose liquidation
    columns are then NaN/NA and whose counts are 0; `days_touched` counts the UTC days the window
    reaches (a two-hour window across midnight touches two) and `hours` is its length;
    `precisions` are the instrument definition's `(price, size)` the episodes are scaled at (None
    without a definition: the first row's, `replay_cascade`'s default); `other_id` is the same
    asset's leg on another venue the episodes are paired with (None when the catalog defines none,
    which `cross_venue.reason` then says).
    """

    instrument_id: str
    has_feed: bool
    days_touched: int
    hours: float
    precisions: tuple[int, int] | None
    liquidations: int
    episodes: pd.DataFrame
    leverage: ImpliedLeverage
    forced_share: ForcedShare
    match: TradeMatch
    organic: pd.DataFrame
    organic_minutes: pd.DataFrame
    unattributed: int
    vs_oi: pd.DataFrame
    other_id: str | None
    cross_venue: CascadeLeadLag

    def lines(self) -> list[str]:
        """Return the study's sample in words: its days, rows, episodes, matches and every caveat."""
        if not self.has_feed:
            return [f"{self.instrument_id} has no liquidation feed (Bybit LINEAR only): no rows"]
        share = "n/a" if self.match.share is None else f"{self.match.share:.1%}"
        scale = (
            "the first row's precisions (no instrument definition)"
            if self.precisions is None
            else f"the definition's precisions {self.precisions}"
        )
        return [
            f"{self.instrument_id}: {self.hours:g} h over {self.days_touched} UTC day(s) touched, "
            f"{self.liquidations} liquidations, {len(self.episodes)} cascade episode(s) at "
            f"{scale}, {self.episodes.attrs.get('unscalable_rows', 0)} unscalable row(s) skipped",
            f"matched to a forced trade: {self.match.matched} of {self.match.total} ({share})",
            f"liquidations in a second with no single snapshot row (none, or two: unattributed): "
            f"{self.unattributed}",
            f"cross venue: {self.cross_venue.reason or f'paired with {self.other_id}'}",
        ]


@dataclass
class _DayParts:
    """The reduced per-day results `liquidation_study` joins (the day's reads are dropped)."""

    rows: list[Liquidation]
    mids: list[pd.Series]
    leverage: list[ImpliedLeverage]
    forced: list[ForcedShare]
    match: list[pd.DataFrame]
    organic: list[pd.DataFrame]
    oi: list[pd.DataFrame]

    def leverage_result(self) -> ImpliedLeverage:
        return ImpliedLeverage(
            _joined([p.per_liquidation for p in self.leverage], LEVERAGE_COLUMNS, reindex=True),
            _joined([p.per_day for p in self.leverage], LEVERAGE_DAY_COLUMNS),
        )

    def forced_result(self) -> ForcedShare:
        return ForcedShare(
            _joined([p.per_minute for p in self.forced], FORCED_SHARE_COLUMNS),
            _joined([p.per_hour for p in self.forced], FORCED_SHARE_COLUMNS),
        )


# A 1 s mid grid with no second: forward returns of no episode (the other leg's, a window of no
# snapshot).
_EMPTY_GRID = pd.Series([], index=pd.DatetimeIndex([], tz="UTC"), dtype="float64")


def _day_slices(start_ns: int, end_ns: int) -> list[tuple[int, int]]:
    """Return the window cut at each UTC midnight: `[start, end)` slices of at most one day."""
    slices = []
    lo = start_ns
    while lo < end_ns:
        hi = min((lo // NS_PER_DAY + 1) * NS_PER_DAY, end_ns)
        slices.append((lo, hi))
        lo = hi
    return slices


def _study_day(
    frames: MarketFrames,
    iid: str,
    day: tuple[int, int],
    config: LiquidationStudyConfig,
    parts: _DayParts,
) -> None:
    """
    Read one UTC day and keep only its reduced results (MEM-01): the trades widened by the match
    tolerance and the marks by the mark age, so a liquidation near midnight still sees them.

    Known limit: each day is matched on its own, so a trade within the tolerance of midnight can
    serve one liquidation of each day (audit D-222); upgrade path: carry the used trade ids into
    the next day's match.
    """
    lo, hi = day
    seconds = frames.seconds(iid, start=lo, end=hi)
    reduced = seconds[["ts_event", "buy_volume_units", "sell_volume_units", "size_precision"]]
    parts.mids.append(second_grid(seconds, lo, hi, ("mid",))["mid"])
    del seconds  # the book lists: one day at a time
    feed = has_liquidation_feed(iid)
    liqs = frames.liquidations(iid, start=lo, end=hi) if feed else None
    parts.forced.append(forced_share(liqs, reduced))
    parts.organic.append(organic_delta(reduced, liqs))
    parts.oi.append(frames.open_interest(iid, start=lo, end=hi))
    if liqs is None:
        return
    parts.rows += rows_of(liqs)
    tol_ns = config.match_tol_s * NS_PER_S
    trades = frames.objects(TradeTick, iid, start=lo - tol_ns, end=hi + tol_ns)
    parts.match.append(match_to_trades(liqs, trades, config.match_tol_s).per_liquidation)
    marks = frames.mark_index(iid, start=lo - config.max_mark_age_s * NS_PER_S, end=hi)
    parts.leverage.append(implied_leverage(liqs, marks, config.max_mark_age_s))


def _other_leg(frames: MarketFrames, iid: str) -> str | None:
    """
    Return the same asset's leg on another venue to pair episodes with: the first with a
    liquidation feed, else the first on Hyperliquid, else the first other one (`same_symbol`'s
    order); None when the catalog defines none.
    """
    others = [other for other in frames.same_symbol(iid) if venue_of(other) != venue_of(iid)]
    for preferred in (
        [other for other in others if has_liquidation_feed(other)],
        [other for other in others if venue_of(other) == "HYPERLIQUID"],
        others,
    ):
        if preferred:
            return preferred[0]
    return None


def _episodes(
    liqs: pd.DataFrame,
    mids: pd.Series,
    config: LiquidationStudyConfig,
    window: tuple[int, int],
    precisions: tuple[int, int] | None,
) -> pd.DataFrame:
    return cascade_episodes(
        liqs,
        mids,
        window_s=config.window_s,
        baseline_s=config.baseline_s,
        intensity_threshold=config.intensity_threshold,
        decay_ratio=config.decay_ratio,
        precisions=precisions,
        start_ns=window[0],
        end_ns=window[1],
    )


def _other_episodes(
    frames: MarketFrames, other: str | None, config: LiquidationStudyConfig, window: tuple[int, int]
) -> pd.DataFrame:
    """Return the other leg's episodes (no mids: only their starts are paired); empty without a feed."""
    rows: list[Liquidation] = []
    if other is not None and has_liquidation_feed(other):
        for lo, hi in _day_slices(*window):
            rows += rows_of(frames.liquidations(other, start=lo, end=hi))
    precisions = None if other is None else frames.definition_precisions(other)
    frame = liquidations_frame(other or "", rows)
    return _episodes(frame, _EMPTY_GRID, config, window, precisions)


def _cross_venue(
    iid: str, other: str | None, episodes: pd.DataFrame, other_episodes: pd.DataFrame, lag: int
) -> CascadeLeadLag:
    """Pair the episodes with the other leg's; with no other leg, say so (never a `None` name)."""
    if other is None:
        pairs = pd.DataFrame(columns=list(CASCADE_PAIR_COLUMNS))
        reason = f"no same-asset leg of {iid} on another venue in the catalog"
        return CascadeLeadLag(iid, "", pairs, len(episodes), 0, reason)
    return cross_venue_liquidations(episodes, other_episodes, lag, iid, other)


def _joined(
    frames_: list[pd.DataFrame], columns: Sequence[str], reindex: bool = False
) -> pd.DataFrame:
    """
    Concatenate per-day frames, or the empty frame with `columns` when there are none; `reindex`
    gives the result a fresh unique index (per-liquidation rows: two can share a stamp).
    """
    kept = [f for f in frames_ if not f.empty]
    if not kept:
        return pd.DataFrame(columns=list(columns))
    return pd.concat(kept, ignore_index=reindex)


def liquidation_study(
    frames: MarketFrames,
    iid: str,
    start_ns: int,
    end_ns: int,
    config: LiquidationStudyConfig,
) -> LiquidationStudy:
    """
    Run the whole liquidation study of `iid` over `[start_ns, end_ns)`, reading one UTC day at a
    time and keeping only reduced columns (MEM-01, `_study_day`): the cascade episodes with their
    forward returns, the implied leverage per liquidation and per day, the forced share per minute
    and hour, the trade match, the organic delta per second and per minute, the liquidations
    against the open-interest change per `oi_bucket_s`, and the episodes paired with the same
    asset's leg on another venue (`cross_venue_liquidations`; Hyperliquid has no feed, so that
    side is empty and says so). An id without a feed reads no liquidation and reports NaN, never 0.
    The episodes are scaled at the instrument definition's precisions
    (`MarketFrames.definition_precisions`), the scale the strategies feed the detector at; the OI
    is also read over the bucket before the window, so the first bucket's change is defined.
    `end_ns <= start_ns` raises `ValueError` (never an empty study that reads as "no
    liquidations").

    Known limit: the detector starts cold at `start_ns`, as a backtest's strategy does at its
    start: it is not `initialized` before `baseline_s` has passed, so no episode can open in the
    window's first `baseline_s` (an hour by default) and a window shorter than that has none;
    upgrade path: replay from `start_ns - baseline_s` and report only the episodes starting in
    the window.

    Known limit: the rows are read per UTC day on their venue `ts_event` (`MarketFrames.
    liquidations`) and replayed on their `ts_init`, so a row whose receive skew carries it across
    a window edge (stamped before `start_ns` and received after it, or received at or after
    `end_ns`) is in the read and not the replay, or the reverse -- milliseconds at two edges,
    where `read_liquidations`/the backtest select on `ts_init` alone; upgrade path: widen the
    liquidation read by `MAX_TS_INIT_SKEW_NS` and select on `ts_init`, as `read_liquidations`
    does.

    Known limit: the seconds and the liquidations are both cut on `ts_event`, but a second's row
    is stamped `S + 0.5 s`, so a window edge that is not a whole second can leave a liquidation of
    the edge second in the read while its row is out (or the reverse), counting it `unattributed`;
    every UTC-date `START`/`END` (notebook 09's) is whole; upgrade path: floor both edges to the
    second before reading.
    """
    if end_ns <= start_ns:
        raise ValueError(f"end_ns {end_ns} must be after start_ns {start_ns}")
    window = (start_ns, end_ns)
    # The bucket before the window, so the first OI change has a previous reading.
    pre_window = frames.open_interest(
        iid, start=oi_window_start(start_ns, config.oi_bucket_s), end=start_ns
    )
    parts = _DayParts([], [], [], [], [], [], [pre_window])
    for day in _day_slices(start_ns, end_ns):
        _study_day(frames, iid, day, config, parts)
    feed = has_liquidation_feed(iid)
    liqs = liquidations_frame(iid, parts.rows)
    mids = pd.concat(parts.mids) if parts.mids else _EMPTY_GRID
    precisions = frames.definition_precisions(iid)
    episodes = _episodes(liqs, mids, config, window, precisions)
    other = _other_leg(frames, iid)
    other_episodes = _other_episodes(frames, other, config, window)
    organic = _joined(parts.organic, ORGANIC_COLUMNS)
    oi = _joined(parts.oi, ("ts_event", "open_interest", "ts_init"))
    return LiquidationStudy(
        instrument_id=iid,
        has_feed=feed,
        days_touched=len(_day_slices(start_ns, end_ns)),
        hours=(end_ns - start_ns) / (3_600 * NS_PER_S),
        precisions=precisions,
        liquidations=len(parts.rows),
        episodes=episodes,
        leverage=parts.leverage_result(),
        forced_share=parts.forced_result(),
        match=TradeMatch(_joined(parts.match, MATCH_COLUMNS, reindex=True)),
        organic=organic,
        organic_minutes=organic_per_minute(organic),
        unattributed=sum(int(f.attrs.get("unattributed", 0)) for f in parts.organic),
        vs_oi=liquidations_vs_oi(liqs if feed else None, oi, config.oi_bucket_s, *window),
        other_id=other,
        cross_venue=_cross_venue(iid, other, episodes, other_episodes, config.max_lag_s),
    )
