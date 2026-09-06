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
Tests for bot_tui.coins_pane -- Story 4.1 (AC2/AC5), Story 4.2 (AC1, filter_rows), and
the full-metric-parity redesign (troll/CLAUDE.md SSOT-03): coin_rows now returns
whole rank-entry dicts verbatim (every RANKING_COLS field ranking_engine publishes),
not a narrow (rank, id, score) tuple.

Pure functions only, no urwid import -- see Story 4.1's Dev Notes "Testing strategy:
pure logic vs. urwid wiring" for why.
"""

from ml_signals.ranking_columns import NEGATIVE_COLOR
from ml_signals.ranking_columns import POSITIVE_COLOR
from ml_signals.ranking_columns import RANKING_COLS

from bot_tui.coins_pane import COLD_OPEN_TEXT
from bot_tui.coins_pane import NO_MATCHES_TEXT
from bot_tui.coins_pane import coin_header_text
from bot_tui.coins_pane import coin_rows
from bot_tui.coins_pane import filter_rows
from bot_tui.coins_pane import format_coin_row
from bot_tui.coins_pane import stale_feed_banner_text


def _row(iid: str, rank: int, **overrides: object) -> dict:
    row = {"instrument_id": iid, "rank": rank, "volume24h": 1.0, "volatility_score": None}
    row.update(overrides)
    return row


def test_coin_rows_none_ranking_returns_empty_list() -> None:
    assert coin_rows(None) == []


def test_coin_rows_returns_rank_entries_verbatim_in_given_order() -> None:
    # Deliberately out-of-volume-order -- row order must be preserved exactly as given,
    # and every field on the entry (not just rank/instrument_id/score) passes through.
    ranking = {
        "mode": "volume",
        "ranks": [
            _row("LOW-USD-PERP", 1, volume24h=1.0, spread=0.5),
            _row("HIGH-USD-PERP", 2, volume24h=999.0, spread=0.1),
        ],
    }
    rows = coin_rows(ranking)
    assert [row["instrument_id"] for row in rows] == ["LOW-USD-PERP", "HIGH-USD-PERP"]
    assert rows[0]["spread"] == 0.5
    assert rows[1]["spread"] == 0.1


def test_cold_open_text_is_exact_ux_copy() -> None:
    assert COLD_OPEN_TEXT == "waiting for rankings:live…"


_FILTER_ROWS = [
    _row("BTC-USD-PERP", 1),
    _row("ETH-USD-PERP", 2),
    _row("SOL-USD-PERP", 3),
]


def test_filter_rows_substring_match_returns_only_matching_row() -> None:
    assert filter_rows(_FILTER_ROWS, "eth") == [_row("ETH-USD-PERP", 2)]


def test_filter_rows_is_case_insensitive() -> None:
    assert filter_rows(_FILTER_ROWS, "btc") == [_row("BTC-USD-PERP", 1)]


def test_filter_rows_empty_filter_text_returns_all_rows_unchanged() -> None:
    assert filter_rows(_FILTER_ROWS, "") == _FILTER_ROWS


def test_filter_rows_no_match_returns_empty_list() -> None:
    assert filter_rows(_FILTER_ROWS, "doge") == []


def test_filter_rows_matching_multiple_but_not_all_rows_preserves_relative_order() -> None:
    # A genuine subset match (2 of 3 rows, "t" appears in BTC/ETH but not SOL) -- unlike
    # "usd-perp", which would match all three and not actually exercise dropping a
    # non-matching row while preserving order.
    assert filter_rows(_FILTER_ROWS, "t") == [_row("BTC-USD-PERP", 1), _row("ETH-USD-PERP", 2)]


def test_filter_rows_strips_leading_and_trailing_whitespace() -> None:
    assert filter_rows(_FILTER_ROWS, "  btc  ") == [_row("BTC-USD-PERP", 1)]


def test_filter_rows_whitespace_only_filter_text_returns_all_rows_unchanged() -> None:
    assert filter_rows(_FILTER_ROWS, "   ") == _FILTER_ROWS


def test_no_matches_text_is_exact_ux_copy() -> None:
    assert NO_MATCHES_TEXT == "no matches"


def test_coin_header_text_includes_rank_instrument_and_every_ranking_col_label() -> None:
    header = coin_header_text()
    assert "#" in header
    assert "INSTRUMENT" in header
    assert "OFI10z" in header
    assert "Vol24h" in header


def test_format_coin_row_missing_value_renders_em_dash() -> None:
    row = _row("BTC-USD-PERP", 1, ofi_10_z=None)
    segments = format_coin_row(row)
    assert any(isinstance(s, str) and "—" in s for s in segments)


def test_format_coin_row_positive_color_maps_to_pnl_pos() -> None:
    row = _row("BTC-USD-PERP", 1, ofi_10_z=1.5)
    segments = format_coin_row(row)
    colored = [s for s in segments if isinstance(s, tuple)]
    assert ("pnl-pos", "     +1.50 ") in colored


def test_format_coin_row_negative_color_maps_to_pnl_neg() -> None:
    row = _row("BTC-USD-PERP", 1, ofi_10_z=-1.5)
    segments = format_coin_row(row)
    colored = [s for s in segments if isinstance(s, tuple)]
    assert ("pnl-neg", "     -1.50 ") in colored


def test_format_coin_row_uncolored_column_stays_plain_string() -> None:
    row = _row("BTC-USD-PERP", 1, spread=0.123456)
    segments = format_coin_row(row)
    assert any(isinstance(s, str) and "0.123456" in s for s in segments)


def test_stale_feed_banner_text_empty_when_nothing_stale() -> None:
    assert stale_feed_banner_text([]) == ""


def test_stale_feed_banner_text_lists_ids() -> None:
    assert stale_feed_banner_text(["SOL-USD-PERP.DYDX"]) == "stale feed: SOL-USD-PERP.DYDX"


def test_stale_feed_banner_text_truncates_with_more_count() -> None:
    ids = ["A", "B", "C", "D", "E"]
    assert stale_feed_banner_text(ids) == "stale feed: A, B, C (+2 more)"


def test_ranking_columns_only_uses_the_two_shared_colors() -> None:
    # Sanity check underpinning format_coin_row's color-name mapping: if a column ever
    # returns a third color, it would silently render uncolored instead of erroring.
    for _key, _label, _fmt, color_fn in RANKING_COLS:
        if color_fn is not None:
            assert color_fn(1.0) in (POSITIVE_COLOR, NEGATIVE_COLOR)
            assert color_fn(-1.0) in (POSITIVE_COLOR, NEGATIVE_COLOR)
