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
Regression guard for Story 3.1 AC2 / ARCHITECTURE-SPINE.md's AD-8, research half.

AD-8 binds a named list of "reader" modules -- they must never import TradingNode, Strategy, or
DataEngine (that usage is confined to platform/bots). Checks for the literal substring
"import Strategy" rather than bare "Strategy", since backtest_dydx.py/backtest_ofi.py legitimately
reference strategies via `ImportableStrategyConfig`/`strategy_path="..."` string paths (AD-6's
mandated pattern) -- a bare "Strategy" substring check would false-positive on
`ImportableStrategyConfig` and on docstring/string mentions of `IndicatorSignalStrategy`/
`OFIStrategy`, none of which import the `Strategy` class itself.

Also includes backtest_snapshot.py even though AD-8's spine text doesn't name it explicitly --
it's the same category of backtest-driver module as backtest_dydx.py/backtest_ofi.py, and
checking it costs nothing.

The three backtest drivers moved to research/strategies in Story 24.4; the list's other reader
modules moved out of research: catalog_stats split into archive/ and ranking/, metrics_computer
into ranking/, and chart_data became views/chart_series.py in Story 24.2. Those are checked by
their contexts' AD-8 guards (ranking/ and views/tests/test_ad8_boundary.py) and, for archive/, by
tests/test_boundaries.py's platform-wide TradingNode guard.
Lives here (not bots/tests) because the live-paper Docker image deliberately ships no research code
(see platform/bots.dockerfile), so this guard can only run where those files actually exist.
"""

from pathlib import Path


_STRATEGIES_DIR = Path(__file__).resolve().parent.parent / "strategies"

# The backtest drivers of AD-8's named reader-module list, plus backtest_snapshot.py.
_READER_MODULES = (
    "backtest_dydx.py",
    "backtest_ofi.py",
    "backtest_snapshot.py",
)

_BANNED_SUBSTRINGS = ("TradingNode", "DataEngine", "import Strategy")


def test_ad8_reader_modules_never_import_tradingnode_strategy_or_dataengine() -> None:
    for name in _READER_MODULES:
        path = _STRATEGIES_DIR / name
        assert path.is_file(), f"expected AD-8 reader module not found: {path}"
        text = path.read_text()
        for banned in _BANNED_SUBSTRINGS:
            assert banned not in text, f"{path} must not reference {banned!r} (AD-8)"
