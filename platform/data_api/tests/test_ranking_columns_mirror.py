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
Story 25.1a: the web rankings table's hand-declared TS column list mirrors
`views.ranking_columns.RANKING_COLS`. While bot_tui's Coins pane also rendered `RANKING_COLS`,
a drift between the two lists showed up as two UIs disagreeing; with rankings web-only, the TS
mirror is the list's one renderer, so this test is what keeps the Python list honest.
"""

import os
import re
from pathlib import Path

from views.ranking_columns import DERIVATIVE_COLUMN_KEYS
from views.ranking_columns import RANKING_COLS


# `frontend/` is source, never shipped in an image: `make test` mounts the checkout at
# PLATFORM_SOURCE_DIR; a host run finds it two levels up (as in test_frontend_contract.py).
_SOURCE = os.environ.get("PLATFORM_SOURCE_DIR")
_PLATFORM_DIR = Path(_SOURCE) if _SOURCE else Path(__file__).resolve().parents[2]
_RANKINGS_PAGE = _PLATFORM_DIR / "frontend" / "src" / "pages" / "RankingsPage.tsx"
_TS_ARRAY = re.compile(r"const RANKING_COLS: RankingColumn\[\] = \[(.*?)\n\];", re.DOTALL)
_TS_ENTRY = re.compile(r'\{\s*key:\s*"([^"]+)",\s*label:\s*"([^"]+)"')
_TS_DERIVATIVES = re.compile(r"const DERIVATIVE_COLUMNS = new Set<string>\(\[(.*?)\]\);", re.DOTALL)


def _ts_columns() -> list[tuple[str, str]]:
    array = _TS_ARRAY.search(_RANKINGS_PAGE.read_text())
    assert array is not None, f"RANKING_COLS array not found in {_RANKINGS_PAGE}"
    return _TS_ENTRY.findall(array.group(1))


def test_ts_mirror_has_rankings_cols_key_label_sequence() -> None:
    assert _ts_columns() == [(key, label) for key, label, _format in RANKING_COLS]


def test_ts_derivative_columns_mirror_the_derivative_column_keys() -> None:
    """Story 33.7: the columns a spot row dashes are the same set on both sides."""
    found = _TS_DERIVATIVES.search(_RANKINGS_PAGE.read_text())
    assert found is not None, f"DERIVATIVE_COLUMNS set not found in {_RANKINGS_PAGE}"
    assert set(re.findall(r'"([^"]+)"', found.group(1))) == DERIVATIVE_COLUMN_KEYS
