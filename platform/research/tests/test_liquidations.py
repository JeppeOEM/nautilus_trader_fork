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
day-sliced `read_liquidations`, which selects on `ts_init` as the backtest does; and the Story 33.13
research functions on hand-built frames of real `Liquidation`/`TradeTick` rows (every I/O matrix row
of the spec), plus `liquidation_study` on the notebooks' fixture.

The replay scenario (`window_s=10, baseline_s=60, threshold=3, decay=0.5`, every row 1 000 units):
one LONG at t = 0 starts the clock; five LONGs received at 60.5..64.5 s (the detector warm since
60 s) raise the rate to 5 x 1 000 / 10 = 500 units/s; they expire at 70.5..74.5 s, so the ticks
read 400 at 71 s, 300 at 72 s and 200 at 73 s -- under half the peak (250): spent, and below
3 x the baseline there, the episode's end. A SHORT of 3 000 units received at 200.5 s reads 300
units/s against a baseline decayed to the floor's order: a second episode, rising prices (+1),
ending at the 211 s tick after it expired at 210.5 s.
"""

import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.frames import CatalogFrames
from research.application.frames import liquidations_frame
from research.application.liquidations import DUPLICATE_SITE
from research.application.liquidations import UNSCALABLE_ROW_SITE
from research.application.liquidations import CascadeEpisode
from research.application.liquidations import CascadeEpisodes
from research.application.liquidations import LiquidationStudyConfig
from research.application.liquidations import cascade_episodes
from research.application.liquidations import forced_share
from research.application.liquidations import implied_leverage
from research.application.liquidations import liquidation_study
from research.application.liquidations import match_to_trades
from research.application.liquidations import organic_delta
from research.application.liquidations import organic_per_minute
from research.application.liquidations import read_liquidations
from research.application.liquidations import replay_cascade
from research.application.liquidations import rescaled_units
from research.application.liquidations import rows_of
from research.tests.fixture_catalog import CASCADE_INSTRUMENT
from research.tests.fixture_catalog import DATA_END_NS
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import LIQUIDATION_BACKGROUND
from research.tests.fixture_catalog import LIQUIDATION_BURST
from research.tests.fixture_catalog import FixturePaths
from research.tests.test_ofi_strategy_forced_flow import _instrument as _bybit_instrument


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


# --- Story 33.13 -----------------------------------------------------------------------------

_T0 = 20_000 * NS_PER_DAY  # a UTC midnight


def _liq(
    ts_event: int,
    side: LiquidatedSide = LiquidatedSide.LONG,
    size_units: int = 1,
    price_units: int = 95,
    precisions: tuple[int, int] = (0, 0),
) -> Liquidation:
    """One liquidation at `(price_precision, size_precision)` `precisions`, received 50 ms later."""
    return Liquidation(
        _IID,
        side,
        size_units,
        price_units,
        *precisions,
        f"e{ts_event}-{side.value}",
        ts_event,
        ts_event + 50_000_000,
    )


def _frame(rows: list[Liquidation]) -> pd.DataFrame:
    return liquidations_frame(str(_IID), rows)


def _marks(stamps_and_marks: list[tuple[int, float]]) -> pd.DataFrame:
    """Return a `MarketFrames.mark_index`-shaped frame of mark rows (and one index row, ignored)."""
    rows = [(ts, mark, math.nan, ts) for ts, mark in stamps_and_marks]
    rows.append((_T0, math.nan, 1.0, _T0))
    return pd.DataFrame(sorted(rows), columns=["ts_event", "mark", "index", "ts_init"])


def test_rows_of_rebuilds_the_exact_rows() -> None:
    rows = [_liq(_T0, size_units=7, precisions=(2, 3)), _liq(_T0 + 1, LiquidatedSide.SHORT)]
    rebuilt = rows_of(_frame(rows))
    assert [Liquidation.to_dict(r) for r in rebuilt] == [Liquidation.to_dict(r) for r in rows]


def test_rescaled_units_is_exact_or_refuses() -> None:
    assert rescaled_units(25, 2, 4) == 2_500
    assert rescaled_units(2_500, 4, 2) == 25
    with pytest.raises(Exception, match="not exact"):
        rescaled_units(2_501, 4, 2)


# -- implied leverage


def test_a_long_liquidated_at_95_under_a_mark_of_100_is_levered_20x() -> None:
    liqs = _frame([_liq(_T0 + 10 * NS_PER_S)])
    result = implied_leverage(liqs, _marks([(_T0 + 7 * NS_PER_S, 100.0)]), max_mark_age_s=5)
    row = result.per_liquidation.iloc[0]
    assert (row["mark"], row["mark_age_s"], row["leverage"]) == (100.0, 3.0, 20.0)


def test_leverage_is_nan_without_a_fresh_mark_at_the_mark_or_for_another_price_kind() -> None:
    stale = _liq(_T0 + 10 * NS_PER_S)  # the only mark before it is 6 s old
    at_mark = _liq(_T0 + 20 * NS_PER_S, price_units=100)
    other_kind = _liq(_T0 + 21 * NS_PER_S)
    liqs = _frame([stale, at_mark, other_kind])
    liqs.loc[liqs.index[2], "price_kind"] = "mark"
    marks = _marks([(_T0 + 4 * NS_PER_S, 100.0), (_T0 + 19 * NS_PER_S, 100.0)])
    result = implied_leverage(liqs, marks, max_mark_age_s=5)
    assert result.per_liquidation["leverage"].isna().tolist() == [True, True, True]
    assert math.isnan(result.per_liquidation["mark"].iloc[0])
    day = result.per_day.iloc[0]
    assert (day["count"], day["finite"]) == (3, 0)  # every row counted, none dropped
    assert math.isnan(day["p50"])


def test_leverage_reads_the_mark_float_back_as_its_decimal() -> None:
    # 65 010.05 is not a binary float: as written it equals the price, so there is no distance.
    liqs = _frame([_liq(_T0 + NS_PER_S, price_units=6_501_005, precisions=(2, 0))])
    result = implied_leverage(liqs, _marks([(_T0, 65_010.05)]))
    assert math.isnan(result.per_liquidation["leverage"].iloc[0])


def test_leverage_per_day_takes_the_percentiles_of_the_finite_rows() -> None:
    # Marks of 100 against bankruptcy prices 95, 90, 80: leverages 20, 10, 5.
    rows = [_liq(_T0 + k * NS_PER_S, price_units=p) for k, p in ((1, 95), (2, 90), (3, 80))]
    result = implied_leverage(_frame(rows), _marks([(_T0, 100.0)]))
    day = result.per_day.iloc[0]
    assert (day["count"], day["finite"], day["p50"], day["p10"]) == (3, 3, 10.0, 6.0)
    assert result.per_day.index[0] == pd.Timestamp(_T0, unit="ns", tz="UTC")


# -- organic delta


def _seconds(rows: list[tuple[int, int, int, int]]) -> pd.DataFrame:
    """Return a seconds frame of `(second, buy units, sell units, size precision)`, stamped S + 0.5 s."""
    frame = pd.DataFrame(
        [
            {
                "ts_event": _T0 + second * NS_PER_S + NS_PER_S // 2,
                "buy_volume_units": buy,
                "sell_volume_units": sell,
                "size_precision": precision,
            }
            for second, buy, sell, precision in rows
        ]
    )
    frame.index = pd.DatetimeIndex(
        pd.to_datetime(frame["ts_event"], unit="ns", utc=True), name="ts"
    )
    return frame


def test_the_organic_delta_takes_each_liquidation_out_of_the_side_it_forced() -> None:
    seconds = _seconds([(0, 10, 4, 1)])
    liqs = _frame(
        [
            _liq(_T0 + 100_000_000, LiquidatedSide.LONG, 3, precisions=(0, 1)),
            _liq(_T0 + 900_000_000, LiquidatedSide.SHORT, 1, precisions=(0, 1)),
        ]
    )
    row = organic_delta(seconds, liqs).iloc[0]
    assert (row["delta_units"], row["liq_long_units"], row["liq_short_units"]) == (6, 3, 1)
    assert row["organic_units"] == 8  # (10 - 1) - (4 - 3)
    assert row["organic"] == 0.8  # decoded once at the row's precision 1


def test_a_liquidation_is_rescaled_exactly_to_its_seconds_precision() -> None:
    # 0.3 at precision 1 is 300 units of the row's precision 3.
    seconds = _seconds([(0, 1_000, 0, 3)])
    liqs = _frame([_liq(_T0, LiquidatedSide.LONG, 3, precisions=(0, 1))])
    assert organic_delta(seconds, liqs)["organic_units"].tolist() == [1_300]


def test_a_liquidation_between_snapshots_is_unattributed_never_moved() -> None:
    seconds = _seconds([(0, 5, 5, 0), (2, 5, 5, 0)])  # second 1 has no snapshot row
    liqs = _frame([_liq(_T0 + NS_PER_S + 1, LiquidatedSide.LONG, 2)])
    result = organic_delta(seconds, liqs)
    assert result.attrs["unattributed"] == 1
    assert result["liq_long_units"].tolist() == [0, 0]
    assert result["organic_units"].tolist() == [0, 0]
    assert len(result) == 2  # the missing second stays absent


def test_a_size_the_seconds_precision_cannot_hold_is_ledgered_and_unknown() -> None:
    error_ledger.reset()
    try:
        seconds = _seconds([(0, 5, 5, 0), (1, 5, 5, 0)])
        liqs = _frame([_liq(_T0, LiquidatedSide.LONG, 15, precisions=(0, 1))])  # 1.5 at prec 0
        result = organic_delta(seconds, liqs)
        assert result["organic_units"].isna().tolist() == [True, False]
        assert math.isnan(result["organic"].iloc[0])
        assert error_ledger.counts() == {UNSCALABLE_ROW_SITE: 1}
    finally:
        error_ledger.reset()


def test_without_a_feed_the_liquidation_and_organic_columns_are_unknown() -> None:
    result = organic_delta(_seconds([(0, 10, 4, 1)]), None)
    assert result["delta_units"].tolist() == [6]
    for column in ("liq_long_units", "liq_short_units", "organic_units", "organic"):
        assert result[column].isna().all(), column


def test_organic_per_minute_sums_exactly_and_keeps_an_unknown_minute_unknown() -> None:
    seconds = _seconds([(0, 10, 4, 1), (1, 5, 0, 2), (60, 1, 0, 0)])
    liqs = _frame([_liq(_T0, LiquidatedSide.LONG, 3, precisions=(0, 1))])
    minutes = organic_per_minute(organic_delta(seconds, liqs))
    # Minute 0: delta 0.6 + 0.05, organic 0.9 + 0.05, at the finest precision 2.
    assert minutes["delta"].tolist() == [0.65, 1.0]
    assert minutes["organic"].tolist() == [0.95, 1.0]
    assert minutes["seconds_observed"].tolist() == [2, 1]
    unknown = organic_per_minute(organic_delta(seconds, None))
    assert unknown["organic"].isna().all()


# -- forced share


def test_the_forced_share_is_forced_over_traded_and_nan_when_nothing_traded() -> None:
    seconds = _seconds([(0, 5, 3, 0), (60, 0, 0, 0)])  # minute 0 traded 8, minute 1 nothing
    liqs = _frame([_liq(_T0 + NS_PER_S, size_units=2), _liq(_T0 + 61 * NS_PER_S, size_units=1)])
    minutes = forced_share(liqs, seconds).per_minute
    assert minutes["forced_units"].tolist() == [2, 1]
    assert minutes["traded_units"].tolist() == [8, 0]
    assert minutes["share"].iloc[0] == 0.25
    assert math.isnan(minutes["share"].iloc[1])
    assert minutes["seconds_observed"].tolist() == [1, 1]


def test_the_forced_share_rescales_mixed_precisions_to_the_finest() -> None:
    seconds = _seconds([(0, 5, 3, 3)])  # 0.008 traded at precision 3
    liqs = _frame([_liq(_T0, size_units=20, precisions=(0, 4))])  # 0.0020 at precision 4
    hour = forced_share(liqs, seconds).per_hour.iloc[0]
    assert (hour["forced_units"], hour["traded_units"], hour["size_precision"]) == (20, 80, 4)
    assert hour["share"] == 0.25


def test_without_a_feed_the_forced_share_is_unknown() -> None:
    minutes = forced_share(None, _seconds([(0, 5, 3, 0)])).per_minute
    assert minutes["forced_units"].isna().all()
    assert minutes["share"].isna().all()


# -- the trade match


def _trade(ts_event: int, size_units: int, aggressor: AggressorSide, k: int = 0) -> TradeTick:
    return TradeTick(
        _IID,
        Price.from_int(95),
        Quantity.from_int(size_units),
        aggressor,
        TradeId(f"t{ts_event}-{k}"),
        ts_event,
        ts_event,
    )


def test_a_long_liquidation_matches_its_seller_trade_and_its_offset() -> None:
    liqs = _frame([_liq(_T0, size_units=5)])
    match = match_to_trades(liqs, [_trade(_T0 + 400_000_000, 5, AggressorSide.SELLER)], tol_s=2)
    row = match.per_liquidation.iloc[0]
    assert (bool(row["matched"]), row["offset_s"], row["trade_size"]) == (True, 0.4, 5.0)
    assert (match.total, match.matched, match.share) == (1, 1, 1.0)


def test_each_trade_matches_once_and_each_liquidation_takes_the_earliest_unused() -> None:
    liqs = _frame([_liq(_T0, size_units=5), _liq(_T0 + NS_PER_S, LiquidatedSide.LONG, 5)])
    early = _trade(_T0 + 400_000_000, 5, AggressorSide.SELLER)
    late = _trade(_T0 + 1_500_000_000, 5, AggressorSide.SELLER, k=1)
    both = match_to_trades(liqs, [late, early], tol_s=2)
    assert both.per_liquidation["offset_s"].tolist() == [0.4, 0.5]
    alone = match_to_trades(liqs, [early], tol_s=2)
    assert alone.per_liquidation["matched"].tolist() == [True, False]


def test_a_wrong_aggressor_a_size_mismatch_or_a_late_trade_matches_nothing() -> None:
    liqs = _frame([_liq(_T0, size_units=5)])
    trades = [
        _trade(_T0, 5, AggressorSide.BUYER),  # a long liquidation is a forced sell
        _trade(_T0, 4, AggressorSide.SELLER),
        _trade(_T0 + 3 * NS_PER_S, 5, AggressorSide.SELLER, k=1),  # outside the 2 s tolerance
    ]
    match = match_to_trades(liqs, trades, tol_s=2)
    assert match.matched == 0
    assert math.isnan(match.per_liquidation["offset_s"].iloc[0])


def test_no_liquidation_has_no_match_share() -> None:
    assert match_to_trades(_frame([]), []).share is None


# -- episodes and their forward returns


def _mids(seconds: range, value: Any = None) -> pd.Series:
    index = pd.DatetimeIndex(pd.to_datetime([s * NS_PER_S for s in seconds], unit="ns", utc=True))
    values = [float(100 + s) if value is None else value for s in seconds]
    return pd.Series(values, index=index, dtype="float64")


def _episodes(rows: list[Liquidation], mids: pd.Series, end_s: int) -> pd.DataFrame:
    return cascade_episodes(
        liquidations_frame(str(_IID), rows),
        mids,
        window_s=10,
        baseline_s=60,
        intensity_threshold=3.0,
        decay_ratio=0.5,
        precisions=None,
        start_ns=0,
        end_ns=end_s * NS_PER_S,
    )


def test_cascade_episodes_are_the_replay_with_forward_returns_from_the_end() -> None:
    episodes = _episodes(_scenario(), _mids(range(300)), 300)
    replayed = _replay(_scenario(), 300, start_ns=0)
    assert episodes["start_ns"].tolist() == [e.start_ns for e in replayed]
    assert episodes["end_ns"].tolist() == [e.end_ns for e in replayed]
    assert episodes["side"].tolist() == ["long", "short"]
    assert episodes["duration_s"].tolist() == [(73 - 60.5), (211 - 200.5)]
    # 5 000 and 3 000 notional units of 10^-5 (the rows' precisions 2 + 3), in quote currency.
    assert episodes["notional"].tolist() == [0.05, 0.03]
    assert episodes["peak_rate"].tolist() == [500 / 1e5, 300 / 1e5]
    first = episodes.iloc[0]
    assert first["fwd_1m"] == (100 + 133) / (100 + 73) - 1  # mid[e + 60] / mid[e] - 1
    assert math.isnan(first["fwd_5m"])  # 73 + 300 is past the grid
    assert episodes.attrs["unscalable_rows"] == 0


def test_an_open_episode_or_a_missing_mid_has_no_forward_return() -> None:
    open_episode = _episodes(_scenario(), _mids(range(72)), 72)
    assert pd.isna(open_episode["end_ns"].iloc[0])
    assert math.isnan(open_episode["fwd_1m"].iloc[0])
    assert math.isnan(open_episode["duration_s"].iloc[0])
    no_mid = _episodes(_scenario(), _mids(range(300), math.nan), 300)
    assert no_mid["fwd_1m"].isna().all()


def test_a_grid_with_a_missing_second_is_refused() -> None:
    with pytest.raises(ValueError, match="complete 1 s grid"):
        _episodes(_scenario(), _mids(range(0, 300, 2)), 300)


# -- the study on the fixture


def test_the_study_finds_the_fixture_burst_reading_day_by_day(
    fixture_archive: FixturePaths,
) -> None:
    frames = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir)
    config = LiquidationStudyConfig(window_s=10, baseline_s=120)
    study = liquidation_study(frames, CASCADE_INSTRUMENT, DATA_START_NS, DATA_END_NS, config)
    assert study.days_touched == 2  # the fixture's ten minutes straddle UTC midnight ...
    assert study.hours == 600 / 3_600  # ... and are ten minutes, not two days
    assert study.precisions == (2, 3)  # the instrument definition's
    assert study.liquidations == len(LIQUIDATION_BACKGROUND) + len(LIQUIDATION_BURST)
    assert len(study.episodes) >= 1
    assert study.episodes["direction"].tolist() == [-1]
    assert study.match.total == study.liquidations
    assert int(study.leverage.per_day["count"].sum()) == study.liquidations
    assert study.unattributed == 0  # every liquidation has its snapshot second
    assert study.other_id == "BTC-USD-PERP.HYPERLIQUID"  # no other leg has a feed
    assert study.cross_venue.reason == "no cascade episode on BTC-USD-PERP.HYPERLIQUID"
    for frame in (study.leverage.per_liquidation, study.match.per_liquidation):
        assert frame.index.is_unique
        assert len(frame) == study.liquidations


def test_the_study_of_an_id_without_a_feed_reads_no_liquidation(
    fixture_archive: FixturePaths,
) -> None:
    frames = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir)
    study = liquidation_study(
        frames, "BTC-USD-PERP.HYPERLIQUID", DATA_START_NS, DATA_END_NS, LiquidationStudyConfig()
    )
    assert (study.has_feed, study.liquidations, len(study.episodes)) == (False, 0, 0)
    assert study.organic["organic"].isna().all()
    assert study.vs_oi["liquidation_size"].isna().all()
    assert study.lines() == [
        "BTC-USD-PERP.HYPERLIQUID has no liquidation feed (Bybit LINEAR only): no rows"
    ]


def test_a_bankruptcy_price_on_the_wrong_side_of_the_mark_is_counted_not_levered() -> None:
    # Mark 100: a long liquidated at 105 or a short at 95 is no loss-side bankruptcy price; a
    # short at 105 is 100 / 5 = 20.
    rows = [
        _liq(_T0 + NS_PER_S, LiquidatedSide.LONG, price_units=105),
        _liq(_T0 + 2 * NS_PER_S, LiquidatedSide.SHORT, price_units=95),
        _liq(_T0 + 3 * NS_PER_S, LiquidatedSide.SHORT, price_units=105),
    ]
    result = implied_leverage(_frame(rows), _marks([(_T0, 100.0)]))
    per = result.per_liquidation
    assert per["wrong_side"].tolist() == [True, True, False]
    assert per["leverage"].isna().tolist() == [True, True, False]
    assert per["leverage"].iloc[2] == 20.0
    day = result.per_day.iloc[0]
    assert (day["count"], day["finite"], day["wrong_side"]) == (3, 1, 2)


def _definition_only_catalog(root: Path) -> CatalogFrames:
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([_bybit_instrument()])
    return CatalogFrames(str(root), str(root / "candles"))


def test_a_study_with_no_other_venue_leg_says_so(tmp_path: Path) -> None:
    frames = _definition_only_catalog(tmp_path)
    study = liquidation_study(
        frames, str(_IID), _T0, _T0 + 2 * 3_600 * NS_PER_S, LiquidationStudyConfig()
    )
    assert study.other_id is None
    reason = f"no same-asset leg of {_IID} on another venue in the catalog"
    assert study.cross_venue.reason == reason
    assert study.lines()[-1] == f"cross venue: {reason}"
    assert "None" not in "\n".join(study.lines())
    assert (study.days_touched, study.hours) == (1, 2.0)


def test_a_study_of_a_backwards_window_raises(tmp_path: Path) -> None:
    frames = _definition_only_catalog(tmp_path)
    with pytest.raises(ValueError, match="must be after"):
        liquidation_study(frames, str(_IID), _T0, _T0, LiquidationStudyConfig())


def test_the_no_other_venue_line_names_no_blank_side(tmp_path: Path) -> None:
    frames = _definition_only_catalog(tmp_path)
    study = liquidation_study(
        frames, str(_IID), _T0, _T0 + 3_600 * NS_PER_S, LiquidationStudyConfig()
    )
    assert study.cross_venue.lines()[0] == f"{_IID} vs no other venue: 0 vs 0 episode(s)"
