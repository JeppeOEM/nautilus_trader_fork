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

from types import SimpleNamespace

from ml_signals.candles import aggregate_ohlc
from ml_signals.candles import build_candles
from ml_signals.candles import candle_dicts_from_snapshots


def _snap(ts_event: int, close_price: float | None, buy_volume: float = 1.0, sell_volume: float = 0.5) -> SimpleNamespace:
    """A DydxSecondSnapshot-shaped stand-in (attribute access, same as the real class)."""
    return SimpleNamespace(
        ts_event=ts_event,
        open_price=close_price,
        high_price=close_price,
        low_price=close_price,
        close_price=close_price,
        buy_volume=buy_volume,
        sell_volume=sell_volume,
    )


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


def test_aggregate_ohlc_combines_per_second_bars_correctly() -> None:
    """High/low across the bucket's seconds, open of the first, close of the last --
    not the flat-price-list logic build_candles uses, since each row is already an
    OHLC bar, not a single trade price."""
    one_second = 1_000_000_000
    rows = [
        (0, 100.0, 103.0, 99.0, 101.0, 1.0),
        (10 * one_second, 101.0, 108.0, 100.0, 105.0, 2.0),
        (61 * one_second, 200.0, 205.0, 198.0, 202.0, 3.0),  # bucket 1 starts at 60s
    ]

    candles = aggregate_ohlc(rows, period_seconds=60)

    assert len(candles) == 2
    first, second = candles
    assert first.ts_open == 0
    assert (first.open, first.high, first.low, first.close) == (100.0, 108.0, 99.0, 105.0)
    assert first.volume == 3.0
    assert second.ts_open == 60 * one_second
    assert (second.open, second.high, second.low, second.close) == (200.0, 205.0, 198.0, 202.0)


def test_aggregate_ohlc_empty_input_produces_no_candles() -> None:
    assert aggregate_ohlc([], period_seconds=60) == []


def test_candle_dicts_from_snapshots_skips_no_trade_seconds_and_shapes_json() -> None:
    """
    Shared by dashboard.py's local-mode candle handler and data_api's /catalog/candles
    route (the fix for candles reading an empty local catalog / a ~12MB /catalog/snapshots
    payload in DATA_API_URL remote mode) -- one aggregation, JSON-ready dict output.
    A second with no trade (close_price=None) must contribute nothing, same as
    aggregate_ohlc's own contract.
    """
    one_second = 1_000_000_000
    snapshots = [
        _snap(0, close_price=100.0),
        _snap(10 * one_second, close_price=None),  # no trade this second -- skipped
        _snap(30 * one_second, close_price=102.0),
    ]

    candles = candle_dicts_from_snapshots(snapshots, period_seconds=60)

    assert len(candles) == 1
    c = candles[0]
    assert c["t"] == 0
    assert (c["o"], c["h"], c["l"], c["c"]) == (100.0, 102.0, 100.0, 102.0)
    assert c["v"] == 3.0  # two contributing seconds' buy_volume(1.0)+sell_volume(0.5) each


def test_candle_dicts_from_snapshots_all_none_close_produces_no_candles() -> None:
    snapshots = [_snap(0, close_price=None), _snap(1_000_000_000, close_price=None)]
    assert candle_dicts_from_snapshots(snapshots, period_seconds=60) == []


if __name__ == "__main__":
    test_buckets_two_periods_with_correct_ohlc()
    test_empty_input_produces_no_candles()
    test_aggregate_ohlc_combines_per_second_bars_correctly()
    test_aggregate_ohlc_empty_input_produces_no_candles()
    test_candle_dicts_from_snapshots_skips_no_trade_seconds_and_shapes_json()
    test_candle_dicts_from_snapshots_all_none_close_produces_no_candles()
    print("ok")
