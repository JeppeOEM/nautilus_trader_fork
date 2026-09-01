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
Tests for bot_tui.coins_pane -- Story 4.1 (AC2/AC5) and Story 4.2 (AC1, filter_rows).

Pure functions only, no urwid import -- see Story 4.1's Dev Notes "Testing strategy:
pure logic vs. urwid wiring" for why.
"""

from bot_tui.coins_pane import COLD_OPEN_TEXT
from bot_tui.coins_pane import NO_MATCHES_TEXT
from bot_tui.coins_pane import coin_rows
from bot_tui.coins_pane import filter_rows


def test_coin_rows_none_ranking_returns_empty_list() -> None:
    assert coin_rows(None) == []


def test_coin_rows_volume_mode_uses_volume24h_in_given_order() -> None:
    ranking = {
        "mode": "volume",
        "ranks": [
            {
                "instrument_id": "BTC-USD-PERP",
                "rank": 1,
                "volume24h": 482.6,
                "volatility_score": 0.01,
            },
            {
                "instrument_id": "ETH-USD-PERP",
                "rank": 2,
                "volume24h": 201.3,
                "volatility_score": 0.02,
            },
        ],
    }
    assert coin_rows(ranking) == [
        (1, "BTC-USD-PERP", 482.6),
        (2, "ETH-USD-PERP", 201.3),
    ]


def test_coin_rows_volatility_mode_uses_volatility_score_in_given_order() -> None:
    ranking = {
        "mode": "volatility",
        "ranks": [
            {
                "instrument_id": "SOL-USD-PERP",
                "rank": 1,
                "volume24h": 96.4,
                "volatility_score": 0.244,
            },
            {
                "instrument_id": "BTC-USD-PERP",
                "rank": 2,
                "volume24h": 482.6,
                "volatility_score": 0.182,
            },
        ],
    }
    assert coin_rows(ranking) == [
        (1, "SOL-USD-PERP", 0.244),
        (2, "BTC-USD-PERP", 0.182),
    ]


def test_coin_rows_never_resorts_locally() -> None:
    # Deliberately out-of-volume-order -- row order must be preserved exactly as given.
    ranking = {
        "mode": "volume",
        "ranks": [
            {"instrument_id": "LOW-USD-PERP", "rank": 1, "volume24h": 1.0, "volatility_score": 0.0},
            {
                "instrument_id": "HIGH-USD-PERP",
                "rank": 2,
                "volume24h": 999.0,
                "volatility_score": 0.0,
            },
        ],
    }
    rows = coin_rows(ranking)
    assert [iid for _, iid, _ in rows] == ["LOW-USD-PERP", "HIGH-USD-PERP"]


def test_cold_open_text_is_exact_ux_copy() -> None:
    assert COLD_OPEN_TEXT == "waiting for rankings:live…"


_FILTER_ROWS = [
    (1, "BTC-USD-PERP", 482.6),
    (2, "ETH-USD-PERP", 201.3),
    (3, "SOL-USD-PERP", 96.4),
]


def test_filter_rows_substring_match_returns_only_matching_row() -> None:
    assert filter_rows(_FILTER_ROWS, "eth") == [(2, "ETH-USD-PERP", 201.3)]


def test_filter_rows_is_case_insensitive() -> None:
    assert filter_rows(_FILTER_ROWS, "btc") == [(1, "BTC-USD-PERP", 482.6)]


def test_filter_rows_empty_filter_text_returns_all_rows_unchanged() -> None:
    assert filter_rows(_FILTER_ROWS, "") == _FILTER_ROWS


def test_filter_rows_no_match_returns_empty_list() -> None:
    assert filter_rows(_FILTER_ROWS, "doge") == []


def test_filter_rows_matching_multiple_but_not_all_rows_preserves_relative_order() -> None:
    # A genuine subset match (2 of 3 rows, "t" appears in BTC/ETH but not SOL) -- unlike
    # "usd-perp", which would match all three and not actually exercise dropping a
    # non-matching row while preserving order.
    assert filter_rows(_FILTER_ROWS, "t") == [
        (1, "BTC-USD-PERP", 482.6),
        (2, "ETH-USD-PERP", 201.3),
    ]


def test_filter_rows_strips_leading_and_trailing_whitespace() -> None:
    assert filter_rows(_FILTER_ROWS, "  btc  ") == [(1, "BTC-USD-PERP", 482.6)]


def test_filter_rows_whitespace_only_filter_text_returns_all_rows_unchanged() -> None:
    assert filter_rows(_FILTER_ROWS, "   ") == _FILTER_ROWS


def test_no_matches_text_is_exact_ux_copy() -> None:
    assert NO_MATCHES_TEXT == "no matches"
