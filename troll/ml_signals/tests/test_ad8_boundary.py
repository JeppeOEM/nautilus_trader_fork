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
TradingNode, Strategy, or DataEngine (that usage is confined to the new troll/live_paper
module). Checks for the literal substring "import Strategy" rather than bare "Strategy",
since backtest_dydx.py/backtest_ofi.py legitimately reference strategies via
`ImportableStrategyConfig`/`strategy_path="..."` string paths (AD-6's mandated pattern) --
a bare "Strategy" substring check would false-positive on `ImportableStrategyConfig` and on
docstring/string mentions of `LogisticTrendStrategy`/`OFIStrategy`, none of which import the
`Strategy` class itself. Confirmed via `grep -rn "import Strategy" ml_signals` that neither
file contains that literal substring today.

Also includes backtest_snapshot.py even though AD-8's spine text doesn't name it explicitly
-- it's the same category of backtest-driver module as backtest_dydx.py/backtest_ofi.py, and
checking it costs nothing.

Lives in ml_signals/tests (not live_paper/tests) because it checks ml_signals' own source
files -- the live_paper Docker image deliberately does not copy ml_signals in at all (see
troll/live_paper.dockerfile), so this guard can only run where those files actually exist.
"""

from pathlib import Path


_ML_SIGNALS_DIR = Path(__file__).resolve().parent.parent

# AD-8's exact named reader-module list, plus backtest_snapshot.py (see module docstring).
_READER_MODULES = (
    "dashboard.py",
    "catalog_stats.py",
    "chart_data.py",
    "metrics_computer.py",
    "backtest_dydx.py",
    "backtest_ofi.py",
    "backtest_snapshot.py",
)

_BANNED_SUBSTRINGS = ("TradingNode", "DataEngine", "import Strategy")


def test_ad8_reader_modules_never_import_tradingnode_strategy_or_dataengine() -> None:
    for name in _READER_MODULES:
        path = _ML_SIGNALS_DIR / name
        assert path.is_file(), f"expected AD-8 reader module not found: {path}"
        text = path.read_text()
        for banned in _BANNED_SUBSTRINGS:
            assert banned not in text, f"{path} must not reference {banned!r} (AD-8)"
