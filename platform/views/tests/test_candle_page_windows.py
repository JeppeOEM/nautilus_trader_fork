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
`views.chart_series.candle_page`'s Parquet fallback reads whole buckets only (Story 31.8).

Before the fix its query window `[before_ns - span, before_ns]` was not bucket aligned, so the
oldest bar of a page was folded from part of its seconds and served with no marker: always on the
chart's first 1W page (the span is capped at one week), on a capped 30m/45m page, and on a gap-jump
window ending at a file's last row. Each served bar here must equal the fold of every row of its
whole bucket.
"""

from pathlib import Path

import pytest
from candles.application.forming import bars_from_rows
from kernel.catalog_files import query_second_ohlc
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import chart_series


_IID = "BTC-USD-PERP.DYDX"
_S = 1_000_000_000
_DAY = 86_400 * _S
_WEEK = 7 * _DAY
# A Monday 00:00 UTC (1970-01-05 plus whole weeks): a 1W, 1D, 45m and 30m bucket start at once.
_MONDAY = 4 * _DAY + 2_900 * _WEEK
_OHLC = ("t", "o", "h", "l", "c", "v")


def _write(catalog_path: str, stamps: list[int]) -> None:
    """One traded second per stamp, its price rising with its index (so every bar's o/c differ)."""
    ParquetDataCatalog(catalog_path).write_data(
        [
            make_snapshot(
                instrument_id=_IID,
                bid_prices=[99.0],
                bid_sizes=[1.0],
                ask_prices=[101.0],
                ask_sizes=[1.0],
                buy_volume=1.0,
                sell_volume=0.5,
                buy_count=1,
                sell_count=1,
                open_price=100.0 + i,
                high_price=100.5 + i,
                low_price=99.5 + i,
                close_price=100.25 + i,
                ts_event=ts,
            )
            for i, ts in enumerate(sorted(stamps))
        ]
    )


def _page(tmp_path: Path, before_ns: int, limit: int, bar_seconds: int) -> tuple[list[dict], bool]:
    return chart_series.candle_page(
        _IID,
        before_ns,
        limit,
        bar_seconds,
        catalog_path=str(tmp_path),
        candles_dir=str(tmp_path / "no-candle-store"),
        recent_rows=lambda *_: [],
    )


def _whole_bucket_bars(tmp_path: Path, bar_seconds: int) -> dict[int, dict]:
    rows = query_second_ohlc(str(tmp_path), _IID, 0, 2**62)
    return {bar["t"]: bar for bar in bars_from_rows(rows, bar_seconds)}


def _assert_every_bar_is_whole(tmp_path: Path, served: list[dict], bar_seconds: int) -> None:
    whole = _whole_bucket_bars(tmp_path, bar_seconds)
    assert served
    for bar in served:
        assert {k: bar[k] for k in _OHLC} == whole[bar["t"]]


def test_the_first_1w_page_mid_week_never_serves_a_truncated_previous_week(
    tmp_path: Path,
) -> None:
    before_ns = _MONDAY + 2 * _DAY + 13 * 3600 * _S  # Wednesday 13:00, the chart's "now"
    _write(str(tmp_path), list(range(_MONDAY - _WEEK, before_ns, 6 * 3600 * _S)))

    served, has_more = _page(tmp_path, before_ns, 120, 604_800)

    _assert_every_bar_is_whole(tmp_path, served, 604_800)
    assert [bar["t"] for bar in served] == [_MONDAY // 1_000_000]  # one week per request
    assert has_more
    older, older_has_more = _page(tmp_path, served[0]["t"] * 1_000_000, 120, 604_800)
    _assert_every_bar_is_whole(tmp_path, older, 604_800)
    assert [bar["t"] for bar in older] == [(_MONDAY - _WEEK) // 1_000_000]
    assert not older_has_more


@pytest.mark.parametrize("bar_seconds", [1800, 2700])
def test_a_capped_page_on_sparse_data_never_serves_a_truncated_oldest_bar(
    tmp_path: Path, bar_seconds: int
) -> None:
    before_ns = _MONDAY + 1000 * _S  # inside a 30m and a 45m bucket
    # One traded second every 7 minutes over 8 days: a 45m bucket holds 6-7 of them, so a window
    # starting inside one folds it from only its later seconds.
    _write(str(tmp_path), list(range(before_ns - 8 * _DAY, before_ns, 420 * _S)))

    served, has_more = _page(tmp_path, before_ns, 500, bar_seconds)

    _assert_every_bar_is_whole(tmp_path, served, bar_seconds)
    assert len(served) == 7 * 86_400 // bar_seconds  # the cap, whole buckets, < limit
    assert has_more


def test_a_gap_jump_window_never_cuts_a_bucket(tmp_path: Path) -> None:
    hour = 3600 * _S
    newest_bucket = _MONDAY - 2 * _DAY
    oldest_bucket = newest_bucket - 6 * hour
    # Two traded hours six hours apart, the file ending mid-bucket: the empty first window jumps to
    # the file's last row, and a 6 h window back from it would start 30 min into the older hour.
    _write(
        str(tmp_path),
        [
            oldest_bucket + 600 * _S,
            oldest_bucket + 3000 * _S,
            newest_bucket,
            newest_bucket + 1800 * _S,
        ],
    )

    served, has_more = _page(tmp_path, _MONDAY, 2, 3600)

    _assert_every_bar_is_whole(tmp_path, served, 3600)
    assert [bar["t"] for bar in served] == [newest_bucket // 1_000_000]
    assert has_more


def test_a_file_ending_on_a_boundary_keeps_its_boundary_bar_across_a_gap(tmp_path: Path) -> None:
    hour = 3600 * _S
    boundary = _MONDAY - 2 * _DAY
    # The file's last row lies exactly on an hour boundary, so it opens that hour's bucket. The gap
    # jump must land past it (`data_file_ranges` ends are inclusive), or the window `[.., B)`
    # never reads it and the bar vanishes.
    _write(str(tmp_path), [boundary - 3000 * _S, boundary])

    served, _ = _page(tmp_path, _MONDAY, 2, 3600)

    _assert_every_bar_is_whole(tmp_path, served, 3600)
    assert [bar["t"] for bar in served] == [(boundary - hour) // 1_000_000, boundary // 1_000_000]


def test_a_historical_mid_bucket_cursor_folds_only_the_seconds_before_it(tmp_path: Path) -> None:
    minute = _MONDAY - _DAY
    before_ns = minute + 30 * _S
    # Every second of the minute traded; the cursor sits half way through it.
    _write(str(tmp_path), [minute + i * _S for i in range(60)])

    served, _ = _page(tmp_path, before_ns, 10, 60)

    seen = [r for r in query_second_ohlc(str(tmp_path), _IID, 0, 2**62) if r.ts_event < before_ns]
    (expected,) = bars_from_rows(seen, 60)
    assert [{k: bar[k] for k in _OHLC} for bar in served] == [expected]
    assert served[0]["partial"] is True  # 30 of 60 seconds observed
