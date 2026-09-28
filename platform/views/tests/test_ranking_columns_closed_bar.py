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
`views.ranking_columns.technicals_values` reads the latest *closed* bar (Story 27.7): a newest
bucket still forming at `now_ns` is left out of the replay, for every column alike. Real
`DydxSecondSnapshot` rows in a real `ParquetDataCatalog` (the archive fallback; no candle store),
real indicator replay (TEST-03).
"""

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import ranking_columns


_IID = "BTC-USD-PERP.DYDX"
_MINUTE_NS = 60_000_000_000
# A whole minute, well inside the fallback's one-week read span of any `now_ns` used below.
_BASE_NS = 1_790_000_000 // 60 * 60 * 1_000_000_000
_MINUTES = 30


@dataclass(frozen=True)
class _Entry:
    name: str
    params: dict[str, Any] = field(default_factory=dict)
    bar_seconds: int = 60


def _close(minute: int) -> float:
    return 100.0 + minute


def _seed(catalog_path: Path) -> None:
    """One snapshot per minute for `_MINUTES` closed minutes, then one in the minute after them."""
    ParquetDataCatalog(str(catalog_path)).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(_IID),
                bid_prices=[99.0],
                bid_sizes=[1.0],
                ask_prices=[101.0],
                ask_sizes=[1.0],
                buy_volume=1.0,
                sell_volume=0.5,
                buy_count=1,
                sell_count=1,
                open_price=_close(minute) - 0.2,
                high_price=_close(minute) + 0.5,
                low_price=_close(minute) - 0.5,
                close_price=_close(minute),
                ts_event=_BASE_NS + minute * _MINUTE_NS,
                ts_init=_BASE_NS + minute * _MINUTE_NS,
            )
            for minute in range(_MINUTES + 1)
        ]
    )


def _latest_close(tmp_path: Path, now_ns: int) -> float | None:
    """Return the newest candle close the Technicals replay saw: SMA(1) is exactly that close."""
    values = ranking_columns.technicals_values(
        _IID,
        [_Entry("SimpleMovingAverage", {"period": 1})],
        now_ns,
        catalog_path=str(tmp_path / "cat"),
        candles_dir=str(tmp_path / "no-candle-store"),
    )
    return values.get("0.value")


def test_a_forming_newest_bucket_is_left_out(tmp_path: Path) -> None:
    _seed(tmp_path / "cat")
    now_ns = _BASE_NS + _MINUTES * _MINUTE_NS + _MINUTE_NS // 2  # halfway through the last minute
    assert _latest_close(tmp_path, now_ns) == _close(_MINUTES - 1)


def test_the_same_bucket_is_used_once_it_has_closed(tmp_path: Path) -> None:
    _seed(tmp_path / "cat")
    now_ns = _BASE_NS + (_MINUTES + 1) * _MINUTE_NS  # the last minute's bucket just closed
    assert _latest_close(tmp_path, now_ns) == _close(_MINUTES)


def test_closed_candles_drops_only_a_still_open_newest_bucket() -> None:
    candles = [{"t": 0}, {"t": 60_000}]
    assert ranking_columns._closed_candles(candles, 60, 119_999_999_999) == [{"t": 0}]
    assert ranking_columns._closed_candles(candles, 60, 120_000_000_000) == candles
    assert ranking_columns._closed_candles([], 60, 0) == []


def test_closed_candles_drops_every_still_open_bucket_under_clock_skew() -> None:
    # A capture clock more than a bar ahead of this host leaves two unclosed candles.
    candles = [{"t": 0}, {"t": 60_000}, {"t": 120_000}]
    assert ranking_columns._closed_candles(candles, 60, 100_000_000_000) == [{"t": 0}]
