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
`advance` on every whole second, as the strategy's 1 s timer does. Story 33.13's
`cascade_episodes` extends this replay; forward returns, leverage and forced share are 33.13's.

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
from collections.abc import Iterable
from dataclasses import dataclass

from kernel.catalog_files import query_liquidations
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.indicators import LiquidationCascade
from kernel.liquidation import Liquidation
from kernel.second_snapshot import SnapshotEncodingError
from observability import error_ledger


logger = logging.getLogger(__name__)
# The error-ledger site of a replayed row whose notional the replay's precisions cannot hold (the
# strategy's own is `liquidation_cascade_strategy.UNSCALABLE_ROW_SITE`; the rule is the same).
UNSCALABLE_ROW_SITE = "research.liquidations.unscalable_row"
# The error-ledger site of a venue event stored twice with different values (`query_liquidations`
# refuses it, the caller records it, DATA-07); the read then raises.
DUPLICATE_SITE = "research.liquidations.duplicate"


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
        except ValueError as exc:
            error_ledger.record(DUPLICATE_SITE, f"{instrument_id} liquidations not read", exc)
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
