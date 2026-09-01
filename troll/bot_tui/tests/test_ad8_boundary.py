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
Regression guard for Story 4.1 / ARCHITECTURE-SPINE.md's AD-8.

AD-8 binds a named list of "reader"/derived-data modules -- bot_tui is one of them
(alongside dydx_collector/ml_signals/ranking_engine) -- that must never import
TradingNode, Strategy, or DataEngine (that usage is confined to troll/live_paper).
Near-verbatim copy of ranking_engine/tests/test_ad8_boundary.py's pattern, adapted to
this module's own reader files.
"""

from pathlib import Path


_BOT_TUI_DIR = Path(__file__).resolve().parent.parent

_READER_MODULES = (
    "app.py",
    "ranking_state.py",
    "coins_pane.py",
    "coin_detail.py",
    "coin_detail_state.py",
)

_BANNED_SUBSTRINGS = ("TradingNode", "DataEngine", "import Strategy")


def test_ad8_reader_modules_never_import_tradingnode_strategy_or_dataengine() -> None:
    for name in _READER_MODULES:
        path = _BOT_TUI_DIR / name
        assert path.is_file(), f"expected AD-8 reader module not found: {path}"
        text = path.read_text()
        for banned in _BANNED_SUBSTRINGS:
            assert banned not in text, f"{path} must not reference {banned!r} (AD-8)"
