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
import asyncio

import pytest

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig


# Known upstream deprecation warnings (troll/CLAUDE.md TEST-04: tracked explicitly,
# never blanket-suppressed) -- both are internal nautilus_trader calls, off-limits to
# fix here per FORK-01 (never modify nautilus_trader/crates), and both fire on every
# BacktestEngine.run() so they show up across test_bot_status.py/test_strategy.py/
# test_trade_history.py:
#   - nautilus_trader/backtest/engine.pyx:1601 (and :1418) -- `pd.Timestamp.utcnow()`,
#     deprecated by pandas in favor of `Timestamp.now('UTC')`.
#   - nautilus_trader/data/engine.pyx:2041 -- `state.start.floor(freq="d")`, pandas
#     deprecated the lowercase 'd' frequency alias in favor of 'D'.
# Revisit when this project's nautilus_trader pin (currently 1.229.0) bumps past
# whatever version fixes these upstream -- confirmed via a forced `-W error` run
# 2026-09-11 that both originate inside compiled nautilus_trader .pyx files, not in
# any troll/ code.


@pytest.fixture(autouse=True)
def _fresh_event_loop():
    """
    TradingNode's kernel construction calls asyncio.get_event_loop(), which returns
    the same thread-global "current" loop across every test in this process once one
    exists -- a loop closed by a previous test's node.dispose() would otherwise be
    handed to the next test's TradingNode construction too. Story 4.4 surfaced this:
    build_node now schedules a task via loop.create_task(...) eagerly (see
    live_paper/node.py), which raises "Event loop is closed" against a stale loop from
    an earlier test in the same file. Force a fresh, open loop before every test here,
    for the same reason bot_tui/app.py's run() explicitly creates its own loop rather
    than trusting get_event_loop().
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    if not loop.is_closed():
        # build_node() (live_paper/node.py) schedules one bot_status.run()/
        # trade_history.run() task per configured bot via loop.create_task() --
        # these tests never drive the loop, so those tasks never get their first
        # `.send()`. Cancelling (rather than just closing the loop out from under
        # them) lets each coroutine actually run once to receive CancelledError,
        # which is what silences Python's "coroutine was never awaited"
        # RuntimeWarning -- closing an un-run loop directly does not (troll/CLAUDE.md
        # TEST-04: a warning gets fixed at its source, not filtered away).
        pending = asyncio.all_tasks(loop=loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        loop.close()


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
