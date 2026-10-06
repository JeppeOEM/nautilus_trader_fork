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
`research.application.liquidations` (Story 33.14): `replay_cascade` on hand-built rows and the
day-sliced `read_liquidations`, which selects on `ts_init` as the backtest does.

The replay scenario (`window_s=10, baseline_s=60, threshold=3, decay=0.5`, every row 1 000 units):
one LONG at t = 0 starts the clock; five LONGs received at 60.5..64.5 s (the detector warm since
60 s) raise the rate to 5 x 1 000 / 10 = 500 units/s; they expire at 70.5..74.5 s, so the ticks
read 400 at 71 s, 300 at 72 s and 200 at 73 s -- under half the peak (250): spent, and below
3 x the baseline there, the episode's end. A SHORT of 3 000 units received at 200.5 s reads 300
units/s against a baseline decayed to the floor's order: a second episode, rising prices (+1),
ending at the 211 s tick after it expired at 210.5 s.
"""

from pathlib import Path
from typing import Any

import pytest
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.liquidations import DUPLICATE_SITE
from research.application.liquidations import UNSCALABLE_ROW_SITE
from research.application.liquidations import CascadeEpisode
from research.application.liquidations import CascadeEpisodes
from research.application.liquidations import read_liquidations
from research.application.liquidations import replay_cascade


_IID = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
_HALF = NS_PER_S // 2


def _row(
    ts_ns: int,
    side: LiquidatedSide = LiquidatedSide.LONG,
    size_units: int = 1,
    precisions: tuple[int, int] = (2, 3),
) -> Liquidation:
    # 1 size unit x 1 000 price units: 1 000 notional units at the row's precisions.
    return Liquidation(_IID, side, size_units, 1_000, *precisions, str(ts_ns), ts_ns, ts_ns)


def _scenario() -> list[Liquidation]:
    burst = [_row((60 + k) * NS_PER_S + _HALF) for k in range(5)]
    short = _row(200 * NS_PER_S + _HALF, LiquidatedSide.SHORT, size_units=3)
    return [_row(0), *burst, short]


def _replay(rows: list[Liquidation], end_s: int, **kwargs: Any) -> CascadeEpisodes:
    return replay_cascade(rows, 10, 60, 3.0, 0.5, end_s * NS_PER_S, **kwargs)


def test_the_replay_finds_each_episode_with_its_direction_peak_and_notional() -> None:
    assert _replay(_scenario(), 300, start_ns=0) == [
        CascadeEpisode(60 * NS_PER_S + _HALF, 73 * NS_PER_S, -1, 500.0, 5_000),
        CascadeEpisode(200 * NS_PER_S + _HALF, 211 * NS_PER_S, 1, 300.0, 3_000),
    ]


def test_the_row_order_does_not_matter() -> None:
    assert _replay(list(reversed(_scenario())), 300, start_ns=0) == _replay(
        _scenario(), 300, start_ns=0
    )


def test_an_episode_still_open_at_the_end_has_no_end() -> None:
    episodes = _replay(_scenario(), 72, start_ns=0)
    assert episodes == [CascadeEpisode(60 * NS_PER_S + _HALF, None, -1, 500.0, 5_000)]


def test_rows_at_or_after_the_end_are_not_replayed() -> None:
    assert _replay(_scenario(), 60, start_ns=0) == []


def test_no_rows_no_episode() -> None:
    assert _replay([], 300) == []


def test_a_finer_row_is_taken_at_the_definition_precisions() -> None:
    # 1 000 size units at 10^-4 x 1 000 price units at 10^-3 = 1 000 000 units of 10^-7, exactly
    # 10 000 units of the definition's 10^-5.
    rows = [_row(0), _row(60 * NS_PER_S + _HALF, size_units=1_000, precisions=(3, 4))]
    (episode,) = _replay(rows, 120, start_ns=0, precisions=(2, 3))
    assert episode.notional_units == 10_000


def test_a_row_the_precision_cannot_hold_is_skipped_counted_and_ledgered() -> None:
    # 1 000 units of 10^-9 is 0.1 unit at 10^-5: skipped, the replay goes on (as the strategy).
    error_ledger.reset()
    try:
        bad = _row(30 * NS_PER_S, precisions=(5, 4))
        episodes = _replay([*_scenario(), bad], 300, start_ns=0, precisions=(2, 3))
        assert episodes == _replay(_scenario(), 300, start_ns=0, precisions=(2, 3))
        assert episodes.unscalable_rows == 1
        assert error_ledger.counts() == {UNSCALABLE_ROW_SITE: 1}
    finally:
        error_ledger.reset()


def test_a_clean_replay_skips_nothing() -> None:
    assert _replay(_scenario(), 300, start_ns=0).unscalable_rows == 0


def test_read_liquidations_slices_by_utc_day_and_keeps_the_window_half_open(
    tmp_path: Path,
) -> None:
    midnight = 20_000 * NS_PER_DAY
    stamps = [midnight - 2 * NS_PER_S, midnight - 1, midnight, midnight + 5 * NS_PER_S]
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([_row(ts) for ts in stamps])
    read = read_liquidations(str(tmp_path), str(_IID), midnight - 2 * NS_PER_S, stamps[-1])
    assert [row.ts_event for row in read] == stamps[:3]


def _received(ts_event: int, ts_init: int) -> Liquidation:
    return Liquidation(
        _IID, LiquidatedSide.LONG, 1, 1_000, 2, 3, f"{ts_event}-{ts_init}", ts_event, ts_init
    )


def test_read_liquidations_selects_on_ts_init_like_the_backtest(tmp_path: Path) -> None:
    start, end = 20_000 * NS_PER_DAY + 3_600 * NS_PER_S, 20_000 * NS_PER_DAY + 7_200 * NS_PER_S
    kept = [
        _received(start - 200 * NS_PER_S, start),  # stamped before, received at the start
        _received(start - 1, start + 5 * NS_PER_S),
        _received(end - 2 * NS_PER_S, end - 1),  # received in the window's last nanosecond
    ]
    dropped = [
        _received(start - 2 * NS_PER_S, start - 1),  # received before the window
        _received(end - 3 * NS_PER_S, end),  # stamped inside, received at the (exclusive) end
    ]
    ParquetDataCatalog(str(tmp_path)).write_data(sorted(kept + dropped, key=lambda r: r.ts_init))
    read = read_liquidations(str(tmp_path), str(_IID), start, end)
    assert [(row.ts_event, row.ts_init) for row in read] == [
        (row.ts_event, row.ts_init) for row in kept
    ]


def test_read_liquidations_of_an_id_without_rows_is_empty(tmp_path: Path) -> None:
    assert read_liquidations(str(tmp_path), "BTCUSDT-SPOT.BYBIT", 0, NS_PER_DAY) == []


def test_read_liquidations_records_and_raises_on_a_disagreeing_duplicate(tmp_path: Path) -> None:
    midnight = 20_000 * NS_PER_DAY
    first = _row(midnight + NS_PER_S)
    forged = _row(midnight + NS_PER_S, size_units=2)  # the same venue event id, another size
    filler = _row(midnight + 2 * NS_PER_S)  # a wider file span: a second file
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([first])
    catalog.write_data([forged, filler], skip_disjoint_check=True)
    error_ledger.reset()
    try:
        with pytest.raises(ValueError, match="stored twice"):
            read_liquidations(str(tmp_path), str(_IID), midnight, midnight + NS_PER_DAY)
        assert error_ledger.counts() == {DUPLICATE_SITE: 1}
    finally:
        error_ledger.reset()
