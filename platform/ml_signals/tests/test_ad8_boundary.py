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
Regression guard for Story 3.1 AC2 / ARCHITECTURE-SPINE.md's AD-8.

AD-8 binds a named list of ml_signals "reader" modules -- they must never import
TradingNode, Strategy, or DataEngine (that usage is confined to the new platform/live_paper
module). Checks for the literal substring "import Strategy" rather than bare "Strategy",
which a docstring may mention without importing the class.

The list's three backtest drivers (backtest_dydx.py, backtest_ofi.py, backtest_snapshot.py)
moved to research/strategies in Story 24.4 and are checked by research/tests/test_ad8_boundary.py;
the views/ranking reader modules are checked here. `chart_data.py` became `views/chart_series.py`
in Story 24.2 (its re-export shim was deleted in Story 24.4), so the guard follows it there.

Lives in ml_signals/tests (not live_paper/tests) because it checks source files the live_paper
Docker image deliberately does not copy (see platform/live_paper.dockerfile), so this guard can
only run where those files actually exist.
"""

from pathlib import Path


_PLATFORM_DIR = Path(__file__).resolve().parents[2]

# AD-8's named non-backtest reader modules, at their current paths (the backtest drivers are
# research's).
_READER_MODULES = (
    "ml_signals/catalog_stats.py",
    "views/chart_series.py",
    "ml_signals/metrics_computer.py",
)

_BANNED_SUBSTRINGS = ("TradingNode", "DataEngine", "import Strategy")


def test_ad8_reader_modules_never_import_tradingnode_strategy_or_dataengine() -> None:
    for name in _READER_MODULES:
        path = _PLATFORM_DIR / name
        assert path.is_file(), f"expected AD-8 reader module not found: {path}"
        text = path.read_text()
        for banned in _BANNED_SUBSTRINGS:
            assert banned not in text, f"{path} must not reference {banned!r} (AD-8)"
