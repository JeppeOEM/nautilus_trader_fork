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
"""The watermark aggregate: which seconds an `accept` takes, and which it has already taken."""

from candles.domain.candle_series import CandleSeries
from candles.tests.test_candle_store import _IID
from candles.tests.test_candle_store import _second


def test_a_fresh_series_accepts_every_row_oldest_first() -> None:
    rows = [_second(2, 102.0), _second(0, 100.0), _second(1, 101.0)]
    series = CandleSeries(_IID)
    fresh, through = series.accept(rows)
    assert [r.ts_event for r in fresh] == sorted(r.ts_event for r in rows)
    assert through == max(r.ts_event for r in rows) == series.through_ns


def test_a_replayed_batch_is_accepted_zero_times() -> None:
    rows = [_second(s, 100.0 + s) for s in range(5)]
    series = CandleSeries(_IID)
    series.accept(rows)
    assert series.accept(rows) == ([], rows[-1].ts_event)
    assert series.accept(rows[1:3]) == ([], rows[-1].ts_event)


def test_only_the_seconds_past_the_watermark_are_taken() -> None:
    rows = [_second(s, 100.0 + s) for s in range(5)]
    series = CandleSeries(_IID, through_ns=rows[2].ts_event)
    fresh, through = series.accept(rows)
    assert [r.ts_event for r in fresh] == [rows[3].ts_event, rows[4].ts_event]
    assert through == rows[4].ts_event


def test_a_hole_behind_the_watermark_is_left_to_the_rebuild() -> None:
    """A second filled into the archive late is never re-applied: it would double-count its bucket."""
    rows = [_second(s, 100.0 + s) for s in range(5)]
    series = CandleSeries(_IID)
    series.accept([rows[0], rows[4]])
    assert series.accept(rows[1:4]) == ([], rows[4].ts_event)


def test_an_empty_accept_leaves_the_watermark_where_it_was() -> None:
    series = CandleSeries(_IID, through_ns=123)
    assert series.accept([]) == ([], 123)
    assert series.through_ns == 123


def test_buckets_counts_every_accepted_second_traded_or_not() -> None:
    rows = [_second(0, 100.0), _second(1, None), _second(2, 101.0)]
    buckets = CandleSeries(_IID).buckets(rows)
    minute = buckets[(60, rows[0].ts_event // 1_000_000 // 60_000 * 60_000)]
    assert minute[5] == 3  # seconds_observed
    assert (minute[0], minute[3]) == (100.0, 101.1)  # open of the first traded, close of the last
