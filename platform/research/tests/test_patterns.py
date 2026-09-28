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
`research.application.patterns` (Story 27.7): hand-built grids for the reset at a hole, the EMA
filter and the forward table, and a real `CatalogFrames` read of the fixture archive for the grid.
"""

import math

import numpy as np
import pandas as pd
import pytest
from kernel.candle_patterns import BULLISH
from kernel.candle_patterns import Thresholds
from kernel.clocks import NS_PER_S

from nautilus_trader.indicators import ExponentialMovingAverage
from research.application import patterns
from research.application.frames import CatalogFrames
from research.tests.fixture_catalog import DATA_END_NS
from research.tests.fixture_catalog import DATA_START_NS
from research.tests.fixture_catalog import OUTAGE_INSTRUMENT
from research.tests.fixture_catalog import FixturePaths


OHLC = tuple[float, float, float, float]
_BLACK: OHLC = (10.0, 10.2, 8.9, 9.0)
_WHITE_ENGULFING: OHLC = (8.9, 10.6, 8.8, 10.5)


def _grid(bars: list[OHLC | None], bar_seconds: int = 60) -> pd.DataFrame:
    """Return a grid as `bar_grid` does: `None` is an absent bucket (a NaN row)."""
    starts = np.arange(len(bars), dtype=np.int64) * bar_seconds * NS_PER_S
    ohlc = np.array([bar if bar is not None else (math.nan,) * 4 for bar in bars], dtype=float)
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(starts, unit="ns", utc=True),
            "ts_ns": starts,
            "o": ohlc[:, 0],
            "h": ohlc[:, 1],
            "l": ohlc[:, 2],
            "c": ohlc[:, 3],
            "partial": False,
        }
    )


def _doji(close: float) -> OHLC:
    return (close, close + 1.0, close - 1.0, close)


def _fired(grid: pd.DataFrame, pattern: str) -> list[int]:
    hits = patterns.scan(grid, Thresholds(), ema_len=2)
    return hits.loc[hits["pattern"] == pattern, "bar_index"].tolist()


def test_adjacent_bars_form_a_two_bar_pattern() -> None:
    assert _fired(_grid([_BLACK, _WHITE_ENGULFING]), "ENGULFING") == [1]


def test_the_pattern_set_resets_at_a_hole() -> None:
    assert _fired(_grid([_BLACK, None, _WHITE_ENGULFING]), "ENGULFING") == []


def test_an_empty_scan_still_has_every_column() -> None:
    hits = patterns.scan(_grid([None, None]), Thresholds(), ema_len=2)
    assert list(hits.columns) == list(patterns.HIT_COLUMNS)
    assert hits.empty


def test_the_ema_is_nan_until_initialized_and_resets_at_a_hole() -> None:
    grid = _grid([_doji(10), _doji(12), _doji(14), None, _doji(20), _doji(22)])
    ema = patterns.ema_values(grid, 2)
    expected = []
    for run in ([10.0, 12.0, 14.0], [20.0, 22.0]):
        nautilus = ExponentialMovingAverage(2)
        for close in run:
            nautilus.update_raw(close)
            expected.append(nautilus.value if nautilus.initialized else math.nan)
    assert math.isnan(ema[0])
    assert ema[1:3].tolist() == expected[1:3]
    assert np.isnan(ema[3:5]).all()  # the hole, then a fresh warm-up
    assert ema[5] == expected[4]


def test_the_ema_filter_keeps_closes_above_or_below_and_fails_a_nan_ema() -> None:
    hits = patterns.scan(_grid([_doji(10), _doji(12), _doji(11)]), Thresholds(), ema_len=2)
    doji = hits[hits["pattern"] == "DOJI"]
    assert doji["bar_index"].tolist() == [0, 1, 2]
    above = patterns.filter_hits(doji, "above", "")
    below = patterns.filter_hits(doji, "below", "")
    assert above["bar_index"].tolist() == [1]  # 12 > EMA 11.33; bar 0 has no EMA yet
    assert below["bar_index"].tolist() == [2]  # 11 < EMA 11.11
    assert len(patterns.filter_hits(doji, "any", "")) == 3


def test_the_pattern_filter_takes_comma_separated_names_and_rejects_unknown_ones() -> None:
    hits = patterns.scan(_grid([_BLACK, _WHITE_ENGULFING]), Thresholds(), ema_len=2)
    kept = patterns.filter_hits(hits, "any", " ENGULFING , HARAMI")
    assert set(kept["pattern"]) == {"ENGULFING"}
    with pytest.raises(ValueError):
        patterns.filter_hits(hits, "any", "ENGULFING,THREE_LINE_STRIKE")
    with pytest.raises(ValueError):
        patterns.filter_hits(hits, "sideways", "")


def _doji_hits(grid: pd.DataFrame) -> pd.DataFrame:
    hits = patterns.scan(grid, Thresholds(), ema_len=2)
    doji = hits[hits["pattern"] == "DOJI"].assign(instrument_id="X", timeframe=60)
    return doji.reset_index(drop=True)


def test_forward_returns_stop_at_a_hole_and_at_the_window_end() -> None:
    grid = _grid([_doji(10), _doji(11), None, _doji(12), _doji(13), _doji(14)])
    hits = _doji_hits(grid)
    returns = patterns.hit_forward_returns({("X", 60): grid}, hits, (1, 2))
    assert hits["bar_index"].tolist() == [0, 1, 3, 4, 5]
    ret_1 = returns["ret_1"].tolist()
    assert ret_1[0] == pytest.approx(0.1)
    assert math.isnan(ret_1[1])  # its next bucket is the hole
    assert ret_1[2:4] == pytest.approx([13 / 12 - 1, 14 / 13 - 1])
    assert math.isnan(ret_1[4])  # past the window's end
    assert math.isnan(returns["ret_2"].tolist()[0])  # would cross the hole


def test_the_forward_table_summarises_per_pattern_direction_and_horizon() -> None:
    grid = _grid([_doji(10), _doji(11), None, _doji(12), _doji(13), _doji(14)])
    table = patterns.forward_table({("X", 60): grid}, _doji_hits(grid), (1, 20))
    assert list(table.columns) == list(patterns.FORWARD_COLUMNS)
    rows = {(r["pattern"], r["direction"], r["horizon"]): r for r in table.to_dict("records")}
    near = rows[("DOJI", BULLISH, 1)]
    assert near["n"] == 3
    assert math.isnan(near["hit_rate"])  # DOJI's +100 marks a fire, not a direction
    assert near["mean_return"] == pytest.approx((0.1 + 1 / 12 + 1 / 13) / 3)
    far = rows[("DOJI", BULLISH, 20)]
    assert far["n"] == 0
    assert math.isnan(far["hit_rate"])


def test_a_directional_pattern_is_scored_in_its_own_sign() -> None:
    grid = _grid([_BLACK, _WHITE_ENGULFING, (10.5, 11.2, 10.4, 11.0), (11.0, 11.1, 9.0, 9.5)])
    hits = patterns.scan(grid, Thresholds(), ema_len=2).assign(instrument_id="X", timeframe=60)
    bullish = (hits["pattern"] == "ENGULFING") & (hits["direction"] == BULLISH)
    engulfing = hits[bullish].reset_index(drop=True)
    table = patterns.forward_table({("X", 60): grid}, engulfing, (1, 2))
    rows = {r["horizon"]: r for r in table.to_dict("records")}
    assert (rows[1]["n"], rows[1]["hit_rate"]) == (1, 1.0)
    assert (rows[2]["n"], rows[2]["hit_rate"]) == (1, 0.0)


def test_forward_returns_ignore_the_hits_index_labels() -> None:
    grid = _grid([_doji(10), _doji(11), _doji(12)])
    hits = _doji_hits(grid)
    doubled = pd.concat([hits, hits])  # repeated labels, as a concat without ignore_index gives
    returns = patterns.hit_forward_returns({("X", 60): grid}, doubled, (1,))
    assert returns["ret_1"].tolist()[:2] == pytest.approx([0.1, 1 / 11])
    assert returns["ret_1"].tolist()[3:5] == pytest.approx([0.1, 1 / 11])
    with pytest.raises(ValueError, match="HORIZONS"):
        patterns.hit_forward_returns({("X", 60): grid}, hits, ())
    with pytest.raises(ValueError, match="HORIZONS"):
        patterns.hit_forward_returns({("X", 60): grid}, hits, np.array([], dtype=np.int64))


def test_a_numpy_array_of_horizons_is_accepted() -> None:
    grid = _grid([_doji(10), _doji(11), _doji(12)])
    returns = patterns.hit_forward_returns({("X", 60): grid}, _doji_hits(grid), np.array([1, 2]))
    assert returns["ret_1"].tolist()[:2] == pytest.approx([0.1, 1 / 11])
    assert returns["ret_2"].tolist()[0] == pytest.approx(0.2)


def test_the_forward_table_of_no_hits_is_empty_with_columns() -> None:
    empty = pd.DataFrame(columns=list(patterns.SCAN_COLUMNS))
    table = patterns.forward_table({}, empty, (1, 5))
    assert list(table.columns) == list(patterns.FORWARD_COLUMNS)
    assert table.empty


def test_hit_window_clips_to_the_grid_and_keeps_nan_rows() -> None:
    grid = _grid([_doji(10), None, _doji(12), _doji(13)])
    assert patterns.hit_window(grid, 1, 1).index.tolist() == [0, 1, 2]
    assert patterns.hit_window(grid, 3, 5).index.tolist() == [0, 1, 2, 3]
    with pytest.raises(IndexError):
        patterns.hit_window(grid, 4, 1)
    with pytest.raises(ValueError, match="WINDOW_BARS"):
        patterns.hit_window(grid, 1, -1)


def test_select_hit_names_the_count_when_out_of_range() -> None:
    hits = _doji_hits(_grid([_doji(10)]))
    assert patterns.select_hit(hits, 0)["bar_index"] == 0
    with pytest.raises(IndexError, match="1 hit"):
        patterns.select_hit(hits, 1)


def test_no_hits_note_only_for_an_empty_table() -> None:
    assert patterns.no_hits_note(_doji_hits(_grid([_doji(10)])), "any", "") is None
    note = patterns.no_hits_note(pd.DataFrame(columns=list(patterns.SCAN_COLUMNS)), "above", "")
    assert note is not None
    assert "CONDITION='above'" in note


def test_timeframes_the_store_does_not_keep_are_skipped_with_a_line() -> None:
    kept, skipped = patterns.split_timeframes([60, 45, 3600, 60])
    assert kept == [60, 3600]
    assert len(skipped) == 1
    assert skipped[0].startswith("45 s")


def test_the_grid_of_a_real_fixture_read_marks_the_outage(fixture_archive: FixturePaths) -> None:
    frames = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir)
    grid = patterns.bar_grid(frames, OUTAGE_INSTRUMENT, 60, DATA_START_NS, DATA_END_NS)
    assert list(grid.columns) == list(patterns.GRID_COLUMNS)
    assert len(grid) == 10  # every minute of the ten on the grid, stored or not
    assert grid["ts_ns"].tolist() == list(range(DATA_START_NS, DATA_END_NS, 60 * NS_PER_S))
    bars = pd.concat(
        [
            frames.bars(OUTAGE_INSTRUMENT, 60, start=lo, end=hi)
            for lo, hi in frames.bar_coverage(
                OUTAGE_INSTRUMENT, 60, start=DATA_START_NS, end=DATA_END_NS
            )
        ]
    )
    whole = bars[~bars["partial"].astype(bool)]
    by_ts = grid.set_index("ts_ns")
    assert by_ts.loc[whole["ts_event"], "c"].tolist() == whole["c"].tolist()
    assert int(grid["c"].isna().sum()) == 10 - len(whole)  # the outage's minutes are NaN rows
    assert int(grid["partial"].sum()) >= 1  # the 90 s outage leaves partial minutes


def test_a_scan_of_the_fixture_universe_tags_every_hit(fixture_archive: FixturePaths) -> None:
    frames = CatalogFrames(fixture_archive.catalog_path, fixture_archive.candles_dir)
    window = (DATA_START_NS, DATA_END_NS)
    grids = patterns.bar_grids(frames, fixture_archive.instruments, [60, 300], window)
    hits = patterns.scan_grids(grids, Thresholds(), 3)
    assert set(grids) == {(i, tf) for i in fixture_archive.instruments for tf in (60, 300)}
    assert list(hits.columns) == list(patterns.SCAN_COLUMNS)
    assert len(hits) > 0
    for hit in hits.to_dict("records"):
        grid = grids[(hit["instrument_id"], hit["timeframe"])]
        assert grid["c"].tolist()[hit["bar_index"]] == hit["close"]
    with pytest.raises(ValueError):
        patterns.bar_grids(frames, fixture_archive.instruments, [45], window)


def test_scanning_no_grid_gives_an_empty_table_with_columns() -> None:
    hits = patterns.scan_grids({("X", 60): _grid([None])}, Thresholds(), 3)
    assert list(hits.columns) == list(patterns.SCAN_COLUMNS)
    assert hits.empty
