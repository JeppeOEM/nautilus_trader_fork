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
"""Self-check: candle bucketing produces correct OHLC per time bucket."""

from ml_signals.candles import build_candles


def test_buckets_two_periods_with_correct_ohlc() -> None:
    one_second = 1_000_000_000
    rows = [
        (0, 100.0, 1.0),
        (10 * one_second, 105.0, 2.0),
        (20 * one_second, 95.0, 3.0),
        (30 * one_second, 102.0, 4.0),  # last in bucket 0 -> close=102
        (61 * one_second, 200.0, 5.0),  # bucket 1 starts at 60s
        (90 * one_second, 190.0, 6.0),
    ]

    candles = build_candles(rows, period_seconds=60)

    assert len(candles) == 2
    first, second = candles
    assert first.ts_open == 0
    assert (first.open, first.high, first.low, first.close) == (100.0, 105.0, 95.0, 102.0)
    assert first.volume == 10.0
    assert second.ts_open == 60 * one_second
    assert (second.open, second.high, second.low, second.close) == (200.0, 200.0, 190.0, 190.0)
    assert second.volume == 11.0


def test_empty_input_produces_no_candles() -> None:
    assert build_candles([], period_seconds=60) == []


if __name__ == "__main__":
    test_buckets_two_periods_with_correct_ohlc()
    test_empty_input_produces_no_candles()
    print("ok")
