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
Story 33.7's Rankings columns: appended after every existing column in their published order, each
`format_fn` the text the page shows (hand-computed), and the derivatives set the spot dash applies to.
"""

import pytest

from views.ranking_columns import DERIVATIVE_COLUMN_KEYS
from views.ranking_columns import RANKING_COLS


_STORY_33_7 = [
    ("open_interest", "OI"),
    ("oi_change_1h_pct", "OI Δ1h %"),
    ("oi_change_24h_pct", "OI Δ24h %"),
    ("funding_rate", "Funding"),
    ("basis_mi_bps", "Basis (bps)"),
    ("liq_notional_1h", "Liq 1h"),
    ("liq_ratio_1h", "Liq L/S"),
    ("forced_share_1h", "Forced %"),
    ("relative_volume", "Rel vol"),
    ("range_position_24h", "24h range"),
]


def _format(key: str, value: float) -> str:
    return next(fmt for k, _, fmt in RANKING_COLS if k == key)(value)


def test_the_story_33_7_columns_are_appended_last_in_order() -> None:
    assert [(k, label) for k, label, _ in RANKING_COLS[-len(_STORY_33_7) :]] == _STORY_33_7
    assert RANKING_COLS[-len(_STORY_33_7) - 1][0] == "volume24h"


@pytest.mark.parametrize(
    ("key", "value", "text"),
    [
        ("open_interest", 1234.5, "1234.50"),
        ("oi_change_1h_pct", 5.0, "+5.00%"),
        ("oi_change_24h_pct", -2.5, "-2.50%"),
        ("funding_rate", 0.0001, "0.0100%"),  # a fraction per interval, shown x100
        ("basis_mi_bps", 3.256, "+3.26"),
        ("liq_notional_1h", 120000.0, "120.0K"),
        ("liq_ratio_1h", 0.75, "75.0%"),  # 1.5 long of 2.0 liquidated
        ("forced_share_1h", 0.0123, "1.2%"),
        ("relative_volume", 1.5, "1.50\u00d7"),
        ("range_position_24h", 0.25, "25%"),
    ],
)
def test_each_new_column_formats_as_the_page_shows_it(key: str, value: float, text: str) -> None:
    assert _format(key, value) == text


def test_the_derivative_columns_are_the_eight_with_no_spot_meaning() -> None:
    assert sorted(DERIVATIVE_COLUMN_KEYS) == [
        "basis_mi_bps",
        "forced_share_1h",
        "funding_rate",
        "liq_notional_1h",
        "liq_ratio_1h",
        "oi_change_1h_pct",
        "oi_change_24h_pct",
        "open_interest",
    ]


def test_every_derivative_column_is_a_ranking_column_and_rel_vol_and_range_are_not() -> None:
    keys = {k for k, _, _ in RANKING_COLS}
    assert keys >= DERIVATIVE_COLUMN_KEYS
    assert {"relative_volume", "range_position_24h"}.isdisjoint(DERIVATIVE_COLUMN_KEYS)
