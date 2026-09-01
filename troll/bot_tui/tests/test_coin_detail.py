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
Tests for bot_tui.coin_detail -- Story 4.3, AC1/AC4/AC5.

Pure functions only, no urwid import. Uses real ml_signals.indicators instances
(Microprice/MultiLevelOFI/MultiLevelOBI) per troll/CLAUDE.md's TEST-03: never mock
Nautilus internals -- construct real objects or skip the test entirely.
"""

from ml_signals.indicators import Microprice
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI

from bot_tui.coin_detail import NO_ASKS_TEXT
from bot_tui.coin_detail import NO_BIDS_TEXT
from bot_tui.coin_detail import ask_lines
from bot_tui.coin_detail import bid_lines
from bot_tui.coin_detail import dashboard_chart_url
from bot_tui.coin_detail import format_indicator
from bot_tui.coin_detail import spread
from bot_tui.coin_detail import update_indicators


def _snapshot(**overrides: object) -> dict:
    base = {
        "instrument_id": "BTC-USD-PERP",
        "bid_prices": [100.0, 99.5],
        "bid_sizes": [1.0, 2.0],
        "ask_prices": [100.5, 101.0],
        "ask_sizes": [1.5, 2.5],
        "buy_volume": 10.0,
        "sell_volume": 5.0,
        "buy_count": 3,
        "sell_count": 2,
        "ts_event": 1,
        "ts_init": 1,
    }
    base.update(overrides)
    return base


def test_update_indicators_second_call_initializes_ofi_too() -> None:
    microprice, ofi, obi = (
        Microprice(),
        MultiLevelOFI(levels=10, window=300),
        MultiLevelOBI(levels=10),
    )
    update_indicators(microprice, ofi, obi, _snapshot())
    assert microprice.initialized is True
    assert obi.initialized is True
    assert ofi.initialized is False  # first call only records prev-tick state
    update_indicators(microprice, ofi, obi, _snapshot(bid_prices=[100.1, 99.6]))
    assert ofi.initialized is True


def test_update_indicators_skips_microprice_on_empty_bid_side() -> None:
    microprice, ofi, obi = (
        Microprice(),
        MultiLevelOFI(levels=10, window=300),
        MultiLevelOBI(levels=10),
    )
    update_indicators(microprice, ofi, obi, _snapshot(bid_prices=[], bid_sizes=[]))
    assert microprice.initialized is False


def test_update_indicators_skips_obi_on_empty_ask_side() -> None:
    microprice, ofi, obi = (
        Microprice(),
        MultiLevelOFI(levels=10, window=300),
        MultiLevelOBI(levels=10),
    )
    update_indicators(microprice, ofi, obi, _snapshot(ask_prices=[], ask_sizes=[]))
    assert obi.initialized is False


def test_spread_normal_book() -> None:
    assert spread(_snapshot()) == 0.5


def test_spread_none_when_bid_side_empty() -> None:
    assert spread(_snapshot(bid_prices=[])) is None


def test_spread_none_when_ask_side_empty() -> None:
    assert spread(_snapshot(ask_prices=[])) is None


def test_format_indicator_not_initialized_is_warming_up() -> None:
    assert format_indicator(123.456, initialized=False) == "warming up…"


def test_format_indicator_initialized_formats_to_fixed_precision() -> None:
    assert format_indicator(0.18234567, initialized=True) == "0.1823"


def test_format_indicator_custom_decimals() -> None:
    assert format_indicator(68421.3719, initialized=True, decimals=2) == "68421.37"


def test_bid_lines_capped_at_levels() -> None:
    prices = [float(100 - i) for i in range(20)]
    sizes = [1.0] * 20
    assert len(bid_lines(prices, sizes, levels=1)) == 1


def test_bid_lines_ends_short_on_thin_book() -> None:
    prices = [100.0, 99.0, 98.0]
    sizes = [1.0, 1.0, 1.0]
    assert len(bid_lines(prices, sizes, levels=20)) == 3


def test_ask_lines_independent_of_bid_lines_length() -> None:
    ask_prices = [float(100 + i) for i in range(20)]
    ask_sizes = [1.0] * 20
    assert len(ask_lines(ask_prices, ask_sizes, levels=20)) == 20


def test_bid_lines_empty_returns_no_bids_sentinel() -> None:
    assert bid_lines([], [], levels=20) == [NO_BIDS_TEXT]


def test_ask_lines_empty_returns_no_asks_sentinel() -> None:
    assert ask_lines([], [], levels=20) == [NO_ASKS_TEXT]


def test_dashboard_chart_url_builds_chart_route() -> None:
    assert (
        dashboard_chart_url("http://127.0.0.1:8765", "BTC-USD-PERP")
        == "http://127.0.0.1:8765/chart/BTC-USD-PERP"
    )


def test_dashboard_chart_url_strips_trailing_slash_no_double_slash() -> None:
    assert (
        dashboard_chart_url("http://127.0.0.1:8765/", "BTC-USD-PERP")
        == "http://127.0.0.1:8765/chart/BTC-USD-PERP"
    )
