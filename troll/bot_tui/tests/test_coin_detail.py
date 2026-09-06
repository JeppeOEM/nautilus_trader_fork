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
Tests for bot_tui.coin_detail -- Story 4.3, AC1/AC4/AC5; SSOT-02 migration (indicator
computation moved to ranking_engine, this module only formats/looks up already-computed
values now).

Pure functions only, no urwid import.
"""

from bot_tui.coin_detail import NO_ASKS_TEXT
from bot_tui.coin_detail import NO_BIDS_TEXT
from bot_tui.coin_detail import dashboard_chart_url
from bot_tui.coin_detail import order_book_lines
from bot_tui.coin_detail import osc52_copy_sequence
from bot_tui.coin_detail import format_indicator
from bot_tui.coin_detail import rank_row_for
from bot_tui.coin_detail import ratchet_width


def _ranking(ranks: list[dict]) -> dict:
    return {"mode": "volume", "updated_at": 1, "ranks": ranks}


def test_rank_row_for_finds_matching_instrument() -> None:
    ranking = _ranking([
        {"instrument_id": "ETH-USD-PERP", "spread": 0.1},
        {"instrument_id": "BTC-USD-PERP", "spread": 0.5},
    ])
    assert rank_row_for(ranking, "BTC-USD-PERP") == {"instrument_id": "BTC-USD-PERP", "spread": 0.5}


def test_rank_row_for_none_when_no_ranking_message_yet() -> None:
    assert rank_row_for(None, "BTC-USD-PERP") is None


def test_rank_row_for_none_when_instrument_not_yet_ranked() -> None:
    ranking = _ranking([{"instrument_id": "ETH-USD-PERP"}])
    assert rank_row_for(ranking, "BTC-USD-PERP") is None


def test_rank_row_for_skips_malformed_entry() -> None:
    ranking = _ranking(["not-a-dict", {"instrument_id": "BTC-USD-PERP", "spread": 0.5}])
    assert rank_row_for(ranking, "BTC-USD-PERP") == {"instrument_id": "BTC-USD-PERP", "spread": 0.5}


def test_format_indicator_none_is_warming_up() -> None:
    assert format_indicator(None) == "warming up…"


def test_format_indicator_formats_to_fixed_precision() -> None:
    assert format_indicator(0.18234567) == "0.18234567"


def test_format_indicator_custom_decimals() -> None:
    assert format_indicator(68421.3719, decimals=2) == "68421.37"


def _book(n_bid: int = 4, n_ask: int = 4) -> tuple[list[float], list[float], list[float], list[float]]:
    bid_prices = [float(100 - i) for i in range(n_bid)]
    bid_sizes = [1.0] * n_bid
    ask_prices = [float(101 + i) for i in range(n_ask)]
    ask_sizes = [1.0] * n_ask
    return bid_prices, bid_sizes, ask_prices, ask_sizes


def test_order_book_lines_capped_at_levels() -> None:
    bid_prices, bid_sizes, ask_prices, ask_sizes = _book(n_bid=20, n_ask=20)
    asks, _mid, bids, _sw, _pw = order_book_lines(
        bid_prices, bid_sizes, ask_prices, ask_sizes, levels=1
    )
    assert len(asks) == 1
    assert len(bids) == 1


def test_order_book_lines_ends_short_on_thin_book() -> None:
    bid_prices, bid_sizes, ask_prices, ask_sizes = _book(n_bid=3, n_ask=20)
    asks, _mid, bids, _sw, _pw = order_book_lines(
        bid_prices, bid_sizes, ask_prices, ask_sizes, levels=20
    )
    assert len(bids) == 3
    assert len(asks) == 20


def test_order_book_lines_asks_reversed_worst_to_best_nearest_middle() -> None:
    # Classic ladder: best ask (index 0, lowest price) sits nearest the middle marker,
    # i.e. last in the returned (top-to-bottom) ask list.
    bid_prices, bid_sizes, ask_prices, ask_sizes = _book(n_bid=1, n_ask=3)
    asks, _mid, _bids, _sw, _pw = order_book_lines(
        bid_prices, bid_sizes, ask_prices, ask_sizes, levels=3
    )
    # ask_prices = [101, 102, 103] -- best (101) must be the last line.
    assert asks[-1].endswith(f"{101.0:.2f}")
    assert asks[0].endswith(f"{103.0:.2f}")


def test_order_book_lines_bids_best_to_worst_nearest_middle() -> None:
    bid_prices, bid_sizes, ask_prices, ask_sizes = _book(n_bid=3, n_ask=1)
    _asks, _mid, bids, _sw, _pw = order_book_lines(
        bid_prices, bid_sizes, ask_prices, ask_sizes, levels=3
    )
    # bid_prices = [100, 99, 98] -- best (100) must be the first line.
    assert bids[0].endswith(f"{100.0:.2f}")
    assert bids[-1].endswith(f"{98.0:.2f}")


def test_order_book_lines_empty_bid_side_returns_sentinel() -> None:
    _, _, ask_prices, ask_sizes = _book()
    asks, _mid, bids, _sw, _pw = order_book_lines([], [], ask_prices, ask_sizes, levels=20)
    assert bids == [NO_BIDS_TEXT]
    assert len(asks) == 4


def test_order_book_lines_empty_ask_side_returns_sentinel() -> None:
    bid_prices, bid_sizes, _, _ = _book()
    asks, _mid, bids, _sw, _pw = order_book_lines(bid_prices, bid_sizes, [], [], levels=20)
    assert asks == [NO_ASKS_TEXT]
    assert len(bids) == 4


def test_order_book_lines_price_column_stays_aligned_with_oversized_size() -> None:
    # A meme-coin-sized level (7-digit token count) on one side must not push that
    # row's price field out of alignment with the rest -- every line across asks, the
    # mid marker, and bids must be the same total width.
    bid_prices, bid_sizes, ask_prices, ask_sizes = [100.0, 99.0], [1234567.891, 1.0], [101.0], [1.0]
    asks, mid, bids, _sw, _pw = order_book_lines(
        bid_prices, bid_sizes, ask_prices, ask_sizes, levels=2
    )
    assert len({len(line) for line in [*asks, mid, *bids]}) == 1


def test_order_book_lines_mid_averages_best_bid_and_ask() -> None:
    _asks, mid, _bids, _sw, _pw = order_book_lines([100.0], [1.0], [102.0], [1.0], levels=1)
    assert mid.endswith("101.00")


def test_order_book_lines_mid_dash_when_one_side_empty() -> None:
    _asks, mid, _bids, _sw, _pw = order_book_lines([], [], [102.0], [1.0], levels=1)
    assert mid.endswith("—")


def test_ratchet_width_stays_when_already_wide_enough() -> None:
    # "10.60" (5 chars) fits inside a column already reserved for "9.70"+margin (7) --
    # must not regrow just because the new value is longer than the *old* raw value.
    assert ratchet_width(5, 7) == 7


def test_ratchet_width_grows_with_fresh_margin_when_exceeded() -> None:
    assert ratchet_width(10, 7) == 10 + 3


def test_order_book_lines_width_never_shrinks_below_min() -> None:
    # A 9.xx-only book asked to respect a wider min (as if a prior tick had a 10.xx
    # value) must keep that width, not shrink back down -- this is the ratchet
    # app.py relies on to stop the ladder jumping left/right when a value's digit
    # count crosses a boundary.
    bid_prices, bid_sizes, ask_prices, ask_sizes = [9.0], [1.0], [9.5], [1.0]
    _asks, _mid, bids, size_w, price_w = order_book_lines(
        bid_prices, bid_sizes, ask_prices, ask_sizes, levels=1, min_size_w=20, min_price_w=20
    )
    assert size_w == 20
    assert price_w == 20
    assert len(bids[0]) == 20 + 2 + 20


def test_order_book_lines_width_grows_past_min_when_needed() -> None:
    bid_prices, bid_sizes, ask_prices, ask_sizes = [100.0], [123456.0], [9.5], [1.0]
    _asks, _mid, bids, size_w, price_w = order_book_lines(
        bid_prices, bid_sizes, ask_prices, ask_sizes, levels=1, min_size_w=5, min_price_w=5
    )
    assert size_w > 5
    assert len(bids[0]) == size_w + 2 + price_w


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


def test_osc52_copy_sequence_wraps_base64_payload_in_escape_codes() -> None:
    seq = osc52_copy_sequence("hello")
    assert seq == "\x1b]52;c;aGVsbG8=\x07"
