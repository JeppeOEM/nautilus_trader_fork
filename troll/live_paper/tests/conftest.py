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
import pytest

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig


@pytest.fixture(scope="session", autouse=True)
def _keep_nautilus_log_guard_alive():
    """
    Root-causes the "Fatal Python error: Aborted at kernel.py:231" native abort (see
    deferred-work.md's "Resolved: root cause of the BacktestEngine/BacktestNode native
    abort" entry).

    `live_paper/tests` is a separate pytest collection root from `ml_signals/tests` and
    does not inherit that suite's own copy of this fixture -- each `TradingNode`/
    `BacktestEngine` construction re-initializes Nautilus's Rust logging subsystem unless
    at least one live `LogGuard` is kept alive for the whole session (see
    `ml_signals/tests/conftest.py` for the full mechanism writeup this mirrors verbatim).
    """
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    yield
    del engine
