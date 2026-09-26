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
Regression guard for architecture AD-8: the views read models are readers and must never import
TradingNode, Strategy or DataEngine (that usage is confined to platform/bots). Checks every
non-test module of `views/`, so a new read model is covered without editing this file (the guard
lived in `ml_signals/tests` for `views/chart_series.py` until Story 25.2 deleted `ml_signals`).
Checks the literal substring "import Strategy" rather than bare "Strategy", which a docstring may
mention without importing the class.
"""

from pathlib import Path


_VIEWS_DIR = Path(__file__).resolve().parent.parent

_BANNED_SUBSTRINGS = ("TradingNode", "DataEngine", "import Strategy")


def test_ad8_views_modules_never_import_tradingnode_strategy_or_dataengine() -> None:
    modules = [p for p in _VIEWS_DIR.rglob("*.py") if "tests" not in p.parts]
    assert (_VIEWS_DIR / "chart_series.py") in modules
    for path in modules:
        text = path.read_text()
        for banned in _BANNED_SUBSTRINGS:
            assert banned not in text, f"{path} must not reference {banned!r} (AD-8)"
