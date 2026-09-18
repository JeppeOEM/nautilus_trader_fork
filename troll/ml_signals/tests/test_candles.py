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
from ml_signals.candles import candle_dicts_for_window
from ml_signals.candles import candle_dicts_from_snapshots
from ml_signals.candles import choose_candle_source
from ml_signals.candles import rollup_dicts_from_rows


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


def _rollup(ts_min: int, o: float | None, h: float | None, low: float | None, c: float | None, *,
            secs: int = 60, ofi: float = 1.0, obi: float = 0.5, bid: float = 100.0) -> SimpleNamespace:
    return SimpleNamespace(
        ts_event=ts_min * 60_000_000_000, open=o, high=h, low=low, close=c,
        buy_volume=1.0, sell_volume=0.5, seconds_observed=secs, ofi_5=ofi, ofi_10=ofi, obi_5=obi, obi_10=obi,
        close_bid_price=bid, close_bid_size=1.0, close_ask_price=bid + 2.0, close_ask_size=3.0,
    )


def test_choose_candle_source_threshold_boundary() -> None:
    assert choose_candle_source(3600) == "raw_1s"
    assert choose_candle_source(3601) == "rollup_1m"


def test_rollup_rebucket_ohlcv_and_summed_ofi() -> None:
    rows = [_rollup(0, 10.0, 12.0, 9.0, 11.0), _rollup(1, 11.0, 15.0, 8.0, 14.0), _rollup(2, None, None, None, None)]
    (c,) = rollup_dicts_from_rows(rows, 3600)
    assert (c["o"], c["h"], c["l"], c["c"]) == (10.0, 15.0, 8.0, 14.0)
    assert c["v"] == 4.5  # no-trade member still counts for volume
    assert (c["ofi_5"], c["seconds_observed"]) == (3.0, 180)


def test_rollup_close_book_takes_last_member() -> None:
    rows = [_rollup(0, 1.0, 1.0, 1.0, 1.0, bid=100.0), _rollup(1, 1.0, 1.0, 1.0, 1.0, bid=200.0)]
    (c,) = rollup_dicts_from_rows(rows, 3600)
    assert (c["close_bid_price"], c["spread"]) == (200.0, 2.0)
    assert c["microprice"] == (200.0 * 3.0 + 202.0 * 1.0) / 4.0


def test_rollup_obi_is_seconds_weighted() -> None:
    rows = [_rollup(0, 1.0, 1.0, 1.0, 1.0, secs=10, obi=0.2), _rollup(1, 1.0, 1.0, 1.0, 1.0, secs=30, obi=0.6)]
    (c,) = rollup_dicts_from_rows(rows, 3600)
    assert c["obi_5"] == (0.2 * 10 + 0.6 * 30) / 40


def test_dispatch_falls_back_to_raw_when_rollup_empty() -> None:
    snaps = [_snap(0, 10.0), _snap(1_000_000_000, 12.0)]
    got = candle_dicts_for_window("X", 0, 1, 86400, lambda *_: snaps, lambda *_: [])
    assert got == [{**d, "source": "raw_1s"} for d in candle_dicts_from_snapshots(snaps, 86400)]


def test_dispatch_uses_rollup_when_present() -> None:
    got = candle_dicts_for_window("X", 0, 1, 86400, lambda *_: [], lambda *_: [_rollup(0, 1.0, 2.0, 0.5, 1.5)])
    assert [c["source"] for c in got] == ["rollup_1m"]


def test_short_window_never_touches_rollup() -> None:
    def boom(*_: object) -> list:
        raise AssertionError("rollup queried")

    assert candle_dicts_for_window("X", 0, 1, 60, lambda *_: [], boom) == []
