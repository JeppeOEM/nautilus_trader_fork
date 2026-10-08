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
`research.application.aligned` (Story 27.4): bar returns read span by span over the candle store's
coverage (a hole is a NaN return, never an error), the complete return grid, funding per hour,
bucketed levels and changes, volume share, and the cross-venue view over the session fixture --
the planted Bybit lead of 2 s, the dYdX outage as NaN, a missing venue as a line; and the
liquidations against the open-interest change and the cross-venue episode pairing (Story 33.13).
"""

import math
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation

from nautilus_trader.model.identifiers import InstrumentId
from research.application import aligned
from research.application.frames import CatalogFrames
from research.application.frames import liquidations_frame
from research.application.ports import window_ns
from research.domain.correlation import CorrelationMatrix
from research.domain.correlation import correlation_matrix
from research.domain.returns import ReturnSeries
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import HOLE_SECONDS
from research.tests.fixture_catalog import FixturePaths
from research.tests.test_frames import _DAY0
from research.tests.test_frames import _IID
from research.tests.test_frames import _at
from research.tests.test_frames import _write_catalog


_BTC_BYBIT = "BTCUSDT-LINEAR.BYBIT"
_BTC_DYDX = "BTC-USD-PERP.DYDX"


def _fixture_frames(fixture: FixturePaths) -> tuple[CatalogFrames, int, int]:
    start, end = window_ns(fixture.start, fixture.end)
    return CatalogFrames(fixture.catalog_path, fixture.candles_dir), start, end


def _outage_store(root: Path) -> CatalogFrames:
    """`test_frames`' catalog with minute 1 never observed by the candle store."""
    snapshots = _write_catalog(root / "catalog")
    store = CandleStore(db_path_for_venue(root / "candles", "DYDX"))
    store.apply(_IID, [s for s in snapshots if not _at(60) <= s.ts_event < _at(120)])
    store.close()
    return CatalogFrames(str(root / "catalog"), str(root / "candles"))


def test_bar_returns_across_an_outage_and_past_the_store_are_nan_not_errors(
    tmp_path: Path,
) -> None:
    frames = _outage_store(tmp_path)
    returns = aligned.bar_returns(frames, _IID, 60, _DAY0 - NS_PER_DAY, _at(180) + NS_PER_DAY)
    # Bars at minutes 0 and 2 (minute 1 never observed): one return, across the hole, so NaN.
    assert returns.ts_ns.tolist() == [_at(120)]
    assert returns.as_dict() == {}  # the one stored return, across the hole, is not finite


def test_bar_returns_without_a_store_raise(tmp_path: Path) -> None:
    frames = CatalogFrames(str(tmp_path), str(tmp_path / "nothing"))
    with pytest.raises(FileNotFoundError, match="no candle store"):
        aligned.bar_returns(frames, _IID, 60, _DAY0, _at(180))


def test_a_partial_bar_has_no_close_so_both_its_returns_are_nan(
    fixture_archive: FixturePaths,
) -> None:
    """The dYdX outage leaves minutes 3 and 4 observed for 20 s and 10 s: `partial` bars."""
    frames, start, end = _fixture_frames(fixture_archive)
    returns = aligned.bar_returns(frames, _BTC_DYDX, 60, start, end).as_dict()
    minute = [DATA_START_NS + k * 60 * NS_PER_S for k in range(10)]
    assert [k for k in range(1, 10) if minute[k] in returns] == [1, 2, 6, 7, 8, 9]


def test_aligned_returns_sit_on_the_windows_complete_grid(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    table = aligned.aligned_returns(frames, fixture_archive.instruments, 60, start, end)
    assert table.ids == fixture_archive.instruments
    assert table.ts_ns[0] == start + 60 * NS_PER_S  # the first return closes the second bar
    assert table.ts_ns[-1] == end - 60 * NS_PER_S
    assert set(np.diff(table.ts_ns).tolist()) == {60 * NS_PER_S}
    counts = aligned.returns_frame(table).count()
    # Ten traded minutes, nine returns each -- but the dYdX outage leaves two partial minutes,
    # whose closes are blank, so three of its returns are NaN.
    expected = {iid: 6 if iid == _BTC_DYDX else 9 for iid in fixture_archive.instruments}
    assert counts.to_dict() == expected


def test_every_horizon_is_a_stored_bar_size(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    for horizon in aligned.HORIZONS_S:
        table = aligned.aligned_returns(frames, (_BTC_DYDX,), horizon, start, end)
        assert table.period_seconds == horizon


def test_one_second_returns_keep_the_outage_as_nan(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    table = aligned.aligned_returns(frames, (_BTC_DYDX,), 1, start, end)
    frame = aligned.returns_frame(table)
    hole = pd.to_datetime(
        [DATA_START_NS + s * NS_PER_S for s in (*HOLE_SECONDS, HOLE_SECONDS.stop)], utc=True
    )
    assert frame.loc[hole, _BTC_DYDX].isna().all()
    before = pd.Timestamp(DATA_START_NS + (HOLE_SECONDS.start - 1) * NS_PER_S, tz="UTC")
    assert math.isfinite(float(frame[_BTC_DYDX].loc[before]))


def test_on_grid_refuses_a_mixed_period() -> None:
    series = ReturnSeries(np.array([0.1]), np.array([120 * NS_PER_S]), 60)
    with pytest.raises(ValueError, match="period"):
        aligned.on_grid({"a": series}, 300, 0, 600 * NS_PER_S)


def test_by_venue_gives_a_two_by_two_matrix_per_venue(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    matrix = correlation_matrix(
        aligned.aligned_returns(frames, fixture_archive.instruments, 60, start, end)
    )
    per_venue = aligned.by_venue(matrix)
    assert list(per_venue) == ["DYDX", "BYBIT", "HYPERLIQUID"]
    for sub in per_venue.values():
        assert len(sub.ids) == 2
        a, b = sub.ids
        assert sub.rho(a, b) == matrix.rho(a, b)


def test_clustered_order_puts_each_cluster_together() -> None:
    rho = np.array([[1.0, 0.1, 0.9], [0.1, 1.0, 0.2], [0.9, 0.2, 1.0]])
    matrix = CorrelationMatrix(("a", "b", "c"), rho)
    assert aligned.clustered_order(matrix, 0.2) == ["a", "c", "b"]
    frame = aligned.matrix_frame(matrix, ["a", "c", "b"])
    assert frame.loc["a", "c"] == 0.9
    assert list(frame.columns) == ["a", "c", "b"]


def _funding(intervals: list[int | None]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts_event": [k * 3600 * NS_PER_S for k in range(len(intervals))],
            "rate": [0.0008] * len(intervals),
            "interval": intervals,
        }
    )


def test_funding_per_hour_normalises_by_the_interval_and_never_assumes_one() -> None:
    per_hour = aligned.funding_per_hour(_funding([60, 480, None, 0]))
    assert per_hour[:2] == pytest.approx([0.0008, 0.0001])
    assert np.isnan(per_hour[2:]).all()


def test_funding_intervals_are_the_distinct_values_in_order() -> None:
    assert aligned.funding_intervals(_funding([480, 60, 480, None])) == (480, 60, None)


def test_bucket_last_takes_each_buckets_last_value_and_carries_nothing() -> None:
    frame = pd.DataFrame(
        {
            "ts_event": [10 * NS_PER_S, 50 * NS_PER_S, 130 * NS_PER_S],
            "value": [1.0, 2.0, 3.0],
        }
    )
    last = aligned.bucket_last(frame, "value", 60, 0, 240 * NS_PER_S)
    assert np.array_equal(last.to_numpy(), [2.0, math.nan, 3.0, math.nan], equal_nan=True)


def test_funding_matrix_compares_per_hour_levels() -> None:
    hourly = _funding([60, 60, 60])
    eight_hourly = _funding([480, 480, 480]).assign(rate=[0.0008, 0.0016, 0.0024])
    hourly = hourly.assign(rate=[0.0001, 0.0002, 0.0003])
    levels = aligned.funding_levels({"h": hourly, "e": eight_hourly}, 3600, 0, 3 * 3600 * NS_PER_S)
    assert levels["e"].tolist() == pytest.approx(levels["h"].tolist())
    matrix = aligned.funding_matrix({"h": hourly, "e": eight_hourly}, 3600, 0, 3 * 3600 * NS_PER_S)
    assert matrix.rho("h", "e") == pytest.approx(1.0)


def test_oi_changes_are_relative_and_undefined_from_zero() -> None:
    oi = pd.DataFrame(
        {
            "ts_event": [k * 60 * NS_PER_S for k in range(4)],
            "open_interest": [100.0, 110.0, 0.0, 50.0],
        }
    )
    changes = aligned.oi_changes({"x": oi}, 60, 0, 240 * NS_PER_S).column("x")
    assert changes[0] == pytest.approx(0.1)
    assert np.isnan(changes[1:]).all()  # to zero and from zero: undefined, not a defect


def test_rolling_vs_anchor_validates_and_indexes_by_time() -> None:
    values = np.array([[0.1, 0.2], [0.2, 0.4], [-0.1, -0.2], [0.3, 0.6]])
    table = aligned.on_grid(
        {
            "a": ReturnSeries(values[:, 0].copy(), np.arange(1, 5) * 60 * NS_PER_S, 60),
            "b": ReturnSeries(values[:, 1].copy(), np.arange(1, 5) * 60 * NS_PER_S, 60),
        },
        60,
        0,
        300 * NS_PER_S,
    )
    rolling = aligned.rolling_vs_anchor(table, "a", 180)
    assert list(rolling.columns) == ["b"]
    assert rolling["b"].iloc[-1] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="anchor"):
        aligned.rolling_vs_anchor(table, "zzz", 180)
    with pytest.raises(ValueError, match="window"):
        aligned.rolling_vs_anchor(table, "a", 90)


@pytest.mark.parametrize(
    ("peak", "sentence"),
    [
        ((2, 0.9), "BYBIT leads DYDX by 2 s (peak rho 0.90)"),
        ((-3, 0.5), "DYDX leads BYBIT by 3 s (peak rho 0.50)"),
        ((0, 0.8), "BYBIT and DYDX move in the same second (peak rho 0.80 at 0 s)"),
        (
            None,
            "BYBIT vs DYDX: no lag holds a finite rho over 30+ overlapping 1 s returns,"
            " no lead stated",
        ),
        (
            (4, -0.2),
            "BYBIT vs DYDX: peak rho -0.20 at 4 s is within the noise band (0.00), no lead stated",
        ),
    ],
)
def test_lead_sentence(peak: tuple[int, float] | None, sentence: str) -> None:
    assert aligned.lead_sentence("BYBIT", "DYDX", peak) == sentence


def test_a_peak_inside_the_noise_band_is_no_lead() -> None:
    sentence = aligned.lead_sentence("BYBIT", "DYDX", (3, 0.05), noise_band=0.1)
    assert sentence == (
        "BYBIT vs DYDX: peak rho 0.05 at 3 s is within the noise band (0.10), no lead stated"
    )
    assert aligned.lead_sentence("BYBIT", "DYDX", (3, 0.15), noise_band=0.1).startswith(
        "BYBIT leads DYDX by 3 s"
    )


def _grid(volumes: list[float]) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        pd.to_datetime([k * NS_PER_S for k in range(len(volumes))], unit="ns", utc=True)
    )
    return pd.DataFrame({"buy_volume": volumes, "sell_volume": [0.0] * len(volumes)}, index=index)


def test_volume_share_is_nan_where_a_venue_observed_nothing() -> None:
    nan = math.nan
    grids = {
        "BTC-USD-PERP.DYDX": _grid([1.0, 1.0, nan, nan]),
        "BTCUSDT-LINEAR.BYBIT": _grid([3.0, 1.0, 2.0, 2.0]),
    }
    share = aligned.venue_volume_share(grids, 2)
    assert share["DYDX"].tolist()[0] == pytest.approx(2 / 6)
    assert share["BYBIT"].tolist()[0] == pytest.approx(4 / 6)
    assert share.iloc[1][["DYDX", "BYBIT"]].isna().all()  # dYdX saw no second in bucket 2
    assert share["DYDX seconds"].tolist() == [2, 0]


def test_volume_share_needs_every_leg_of_a_venue() -> None:
    grids = {
        "BTCUSDT-LINEAR.BYBIT": _grid([1.0, 1.0]),
        "BTCPERP-LINEAR.BYBIT": _grid([math.nan, math.nan]),
        "BTC-USD-PERP.DYDX": _grid([1.0, 1.0]),
    }
    share = aligned.venue_volume_share(grids, 2)
    assert share[["BYBIT", "DYDX"]].isna().all(axis=None)
    assert share["BYBIT seconds"].tolist() == [0]


def test_cross_venue_finds_three_venues_and_the_planted_lead(
    fixture_archive: FixturePaths,
) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(frames, _BTC_DYDX, start, end, 30, 3600)
    assert view.collected == fixture_archive.same_asset[_BTC_DYDX]
    assert view.venues == ("BYBIT", "DYDX", "HYPERLIQUID")
    assert view.missing == ()
    pair = next(p for p in view.pairs if (p.a, p.b) == (_BTC_BYBIT, _BTC_DYDX))
    assert pair.peak is not None
    assert pair.peak[0] == fixture_archive.defects.lead_seconds
    assert pair.sentence.startswith("BYBIT leads DYDX by 2 s")


def test_cross_venue_keeps_the_dydx_outage_as_nan(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(frames, _BTC_DYDX, start, end, 30, 3600)
    pair = next(p for p in view.pairs if (p.a, p.b) == (_BTC_BYBIT, _BTC_DYDX))
    hole = pd.to_datetime([DATA_START_NS + s * NS_PER_S for s in HOLE_SECONDS], utc=True)
    assert pair.basis_bps.loc[hole].isna().all()
    inside = pd.Timestamp(DATA_START_NS + 10 * NS_PER_S, tz="UTC")
    assert math.isfinite(pair.basis_bps.loc[inside])


def test_cross_venue_funding_differential_is_per_hour(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(frames, _BTC_DYDX, start, end, 30, 3600)
    pair = next(p for p in view.pairs if (p.a, p.b) == (_BTC_BYBIT, _BTC_DYDX))
    # Both pay 0.0001 per interval: Bybit every 480 min, dYdX every 60.
    expected = float(Decimal("0.0001") * 60 / 480 - Decimal("0.0001"))
    assert pair.funding_diff.dropna().tolist() == pytest.approx([expected, expected])
    assert view.intervals == {
        _BTC_BYBIT: (480,),
        _BTC_DYDX: (60,),
        "BTC-USD-PERP.HYPERLIQUID": (60,),
    }


def test_a_venue_with_no_data_in_the_window_is_a_line_not_an_error(
    fixture_archive: FixturePaths,
) -> None:
    frames, _, _ = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(frames, _BTC_DYDX, _DAY0, _DAY0 + 600 * NS_PER_S, 30, 3600)
    assert view.collected == ()
    assert view.pairs == ()
    assert view.missing == tuple(
        f"BTC-USD perp {venue}: not collected in this window"
        for venue in ("BYBIT", "DYDX", "HYPERLIQUID")
    )
    assert any("no cross-venue pair" in line for line in aligned.summary_lines(view))


def test_a_lead_lag_over_too_few_seconds_is_not_stated(fixture_archive: FixturePaths) -> None:
    frames, _, _ = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(
        frames, _BTC_DYDX, DATA_START_NS, DATA_START_NS + 20 * NS_PER_S, 5, 3600
    )
    assert len(view.collected) == 3
    assert {pair.peak for pair in view.pairs} == {None}
    assert all("no lead stated" in pair.sentence for pair in view.pairs)


def test_every_lag_needs_its_own_pairs_not_only_lag_zero(fixture_archive: FixturePaths) -> None:
    frames, _, _ = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(
        frames, _BTC_DYDX, DATA_START_NS, DATA_START_NS + 45 * NS_PER_S, 30, 3600
    )
    for pair in view.pairs:
        far = [rho for lag, rho in pair.lead_lag if abs(lag) > 44 - aligned.MIN_LEAD_LAG_PAIRS]
        assert far
        assert all(math.isnan(rho) for rho in far)  # under 30 pairs: never ±1 by construction


def test_a_stated_lead_clears_its_noise_band(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(frames, _BTC_DYDX, start, end, 30, 3600)
    pair = next(p for p in view.pairs if (p.a, p.b) == (_BTC_BYBIT, _BTC_DYDX))
    assert pair.peak is not None
    assert 0 < pair.noise_band < pair.peak[1]


def test_a_negative_max_lag_or_empty_bucket_is_refused(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    with pytest.raises(ValueError, match="max_lag"):
        aligned.cross_venue(frames, _BTC_DYDX, start, end, -1, 3600)
    with pytest.raises(ValueError, match="bucket_s"):
        aligned.cross_venue(frames, _BTC_DYDX, start, end, 30, 0)


def test_oi_changes_of_no_instrument_are_empty_like_funding() -> None:
    assert aligned.oi_changes({}, 300, 0, 600 * NS_PER_S).ids == ()
    assert aligned.oi_change_matrix({}, 300, 0, 600 * NS_PER_S).ids == ()


def test_an_unreadable_id_has_no_group_and_says_so(fixture_archive: FixturePaths) -> None:
    frames, start, end = _fixture_frames(fixture_archive)
    view = aligned.cross_venue(frames, "BTCUSDT-25SEP26-LINEAR.BYBIT", start, end, 30, 3600)
    assert (view.asset, view.group, view.pairs) == (None, (), ())
    assert "reads no asset" in view.missing[0]


def test_distinct_assets_takes_each_asset_once(fixture_archive: FixturePaths) -> None:
    frames, _, _ = _fixture_frames(fixture_archive)
    assert aligned.distinct_assets(frames, fixture_archive.instruments) == [
        "BTC-USD-PERP.DYDX",
        "ETH-USD-PERP.DYDX",
    ]
    assert aligned.distinct_assets(frames, ["garbage", "garbage"]) == ["garbage"]


def test_anchor_cluster_is_the_cluster_holding_the_anchor() -> None:
    clusters = {"DYDX": [["a", "b"], ["c"]], "BYBIT": [["d"]]}
    assert aligned.anchor_cluster(clusters, "b") == ("a", "b")
    with pytest.raises(ValueError, match="no cluster"):
        aligned.anchor_cluster(clusters, "z")


# --- Story 33.13: liquidations against open interest, and episodes across venues ---------------


def test_finest_sum_rescales_every_part_to_the_finest_precision_exactly() -> None:
    assert aligned.finest_sum([(25, 2), (3, 0), (1, 4)]) == (32_501, 4)  # 0.25 + 3 + 0.0001
    assert aligned.finest_sum([]) == (0, 0)


def _oi(rows: list[tuple[int, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"ts_event": ts, "open_interest": oi, "ts_init": ts} for ts, oi in rows],
        columns=["ts_event", "open_interest", "ts_init"],
    )


def _liquidation(ts_event: int, size_units: int, size_precision: int) -> Liquidation:
    iid = InstrumentId.from_str(_BTC_BYBIT)
    return Liquidation(
        iid,
        LiquidatedSide.LONG,
        size_units,
        1_000,
        0,
        size_precision,
        str(ts_event),
        ts_event,
        ts_event,
    )


def test_liquidations_vs_oi_per_bucket() -> None:
    # Four 60 s buckets: OI 100 -> 90 -> (no reading) -> 85; liquidations of 2.5 and 1 in bucket 1.
    start = _DAY0
    oi = _oi(
        [
            (start + 10 * NS_PER_S, 100.0),
            (start + 70 * NS_PER_S, 90.0),
            (start + 190 * NS_PER_S, 85.0),
        ]
    )
    liqs = liquidations_frame(
        _BTC_BYBIT,
        [_liquidation(start + 61 * NS_PER_S, 25, 1), _liquidation(start + 62 * NS_PER_S, 1, 0)],
    )
    table = aligned.liquidations_vs_oi(liqs, oi, 60, start, start + 240 * NS_PER_S)
    assert tuple(table.columns) == aligned.LIQUIDATIONS_VS_OI_COLUMNS
    assert table["liquidation_size"].tolist() == [0.0, 3.5, 0.0, 0.0]
    assert table["liquidation_notional"].tolist() == [0.0, 3_500.0, 0.0, 0.0]
    assert np.array_equal(table["oi_change"], [math.nan, -10.0, math.nan, math.nan], equal_nan=True)
    assert table["deleveraging"].tolist() == [False, True, False, False]
    assert table["share_of_oi_drop"].iloc[1] == 0.35
    assert table["share_of_oi_drop"].isna().tolist() == [True, False, True, True]
    assert table.index[1] == pd.Timestamp(start + 60 * NS_PER_S, unit="ns", tz="UTC")


def test_liquidations_vs_oi_without_a_feed_is_unknown_not_zero() -> None:
    oi = _oi([(_DAY0, 100.0), (_DAY0 + 60 * NS_PER_S, 90.0)])
    table = aligned.liquidations_vs_oi(None, oi, 60, _DAY0, _DAY0 + 120 * NS_PER_S)
    assert table["liquidation_size"].isna().all()
    assert table["oi_change"].tolist()[1] == -10.0
    assert not table["deleveraging"].any()


def _episodes(rows: list[tuple[int, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["start_ns", "direction"])


def test_cross_venue_pairs_the_nearest_same_direction_episode_within_the_lag() -> None:
    a = _episodes([(100 * NS_PER_S, -1), (500 * NS_PER_S, 1), (900 * NS_PER_S, -1)])
    b = _episodes([(103 * NS_PER_S, -1), (110 * NS_PER_S, -1), (498 * NS_PER_S, -1)])
    result = aligned.cross_venue_liquidations(a, b, 30, "BYBIT", "OTHER")
    # 500 s (+1) has only a -1 near it; 900 s has nothing within 30 s.
    assert result.pairs.to_dict("records") == [
        {"a_start_ns": 100 * NS_PER_S, "b_start_ns": 103 * NS_PER_S, "direction": -1, "lag_s": 3.0}
    ]
    assert (result.a_episodes, result.b_episodes, result.reason) == (3, 3, None)


def test_cross_venue_takes_the_nearest_pair_first_so_no_later_episode_loses_its_match() -> None:
    # a1 at 0 s and a2 at 100 s, one b at 99 s: a2 (lag -1 s) is nearer than a1 (+99 s).
    a = _episodes([(0, -1), (100 * NS_PER_S, -1)])
    b = _episodes([(99 * NS_PER_S, -1)])
    result = aligned.cross_venue_liquidations(a, b, 300, "BYBIT", "OTHER")
    assert result.pairs.to_dict("records") == [
        {"a_start_ns": 100 * NS_PER_S, "b_start_ns": 99 * NS_PER_S, "direction": -1, "lag_s": -1.0}
    ]


def test_cross_venue_lines_say_when_no_pair_started_within_the_lag() -> None:
    a = _episodes([(0, -1)])
    result = aligned.cross_venue_liquidations(a, _episodes([(900 * NS_PER_S, -1)]), 30, "A", "B")
    assert result.lines() == [
        "A vs B: 1 vs 1 episode(s)",
        "no same-direction episode pair started within the lag",
    ]
    empty = aligned.cross_venue_liquidations(a, _episodes([]), 30, "A", "B")
    assert empty.lines()[1] == "no cascade episode on B"


def test_an_empty_side_gives_no_pair_and_names_itself() -> None:
    a = _episodes([(100 * NS_PER_S, -1)])
    result = aligned.cross_venue_liquidations(a, _episodes([]), 30, "BYBIT", "HYPERLIQUID")
    assert result.pairs.empty
    assert result.reason == "no cascade episode on HYPERLIQUID"
    with pytest.raises(ValueError, match="max_lag_s"):
        aligned.cross_venue_liquidations(a, a, -1)


def test_the_first_oi_change_is_taken_against_the_bucket_before_the_window() -> None:
    start = _DAY0 + 600 * NS_PER_S
    assert aligned.oi_window_start(start + 30 * NS_PER_S, 60) == start - 60 * NS_PER_S
    oi = _oi([(start - 30 * NS_PER_S, 100.0), (start + 10 * NS_PER_S, 96.0)])
    table = aligned.liquidations_vs_oi(None, oi, 60, start, start + 60 * NS_PER_S)
    assert table["oi_change"].tolist() == [-4.0]  # the pre-window bucket is not an output row
    assert table.index[0] == pd.Timestamp(start, unit="ns", tz="UTC")


def test_cross_venue_pairing_is_one_to_one_and_a_tie_goes_to_the_later_b() -> None:
    # Both a episodes are nearest to b at 100 s; the first (by start) takes it, the second the
    # next unused one within the lag. A b equally far either side of an a: the later one.
    a = _episodes([(102 * NS_PER_S, -1), (99 * NS_PER_S, -1), (500 * NS_PER_S, 1)])
    b = _episodes(
        [(100 * NS_PER_S, -1), (110 * NS_PER_S, -1), (495 * NS_PER_S, 1), (505 * NS_PER_S, 1)]
    )
    result = aligned.cross_venue_liquidations(a, b, 30)
    pairs = result.pairs
    assert pairs["a_start_ns"].tolist() == [99 * NS_PER_S, 102 * NS_PER_S, 500 * NS_PER_S]
    assert pairs["b_start_ns"].tolist() == [100 * NS_PER_S, 110 * NS_PER_S, 505 * NS_PER_S]
    assert pairs["b_start_ns"].is_unique
    assert pairs["lag_s"].tolist() == [1.0, 8.0, 5.0]
