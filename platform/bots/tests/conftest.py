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

from bots.infrastructure.fills_store import SqliteFillsStore
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig


# Known upstream deprecation warnings (platform/CLAUDE.md TEST-04: tracked explicitly,
# never blanket-suppressed) -- both are internal nautilus_trader 1.229.0 calls, off-limits
# to fix here per FORK-01 (never modify nautilus_trader/crates). Both are pandas 3
# `pandas.errors.Pandas4Warning`s (a DeprecationWarning subclass): they fire only under the
# images' pinned pandas 3.0.4 (platform/requirements.txt, DW-244), never under the host's
# 2.3.3. Every site:
#   - `pd.Timestamp.utcnow()`, deprecated in favor of `Timestamp.now('UTC')`:
#     nautilus_trader/backtest/engine.pyx:1418 and :1601 (every BacktestEngine.run(), so
#     test_bot_status.py/test_strategy.py/test_trade_history.py) and
#     nautilus_trader/backtest/node.py:347 (every BacktestNode run).
#   - `floor(freq="d")`, the lowercase 'd' alias deprecated in favor of 'D':
#     nautilus_trader/data/aggregation.pyx:1626, :1634, :1646 and :1786
#     (`find_closest_smaller_time`, every time-bar subscription) and
#     nautilus_trader/data/engine.pyx:2041 (date-range requests).
# Both are ignored by exact message, and every other Pandas4Warning is an error, in the image
# runs (platform/Makefile's PANDAS4_WARNINGS on `make test`/`make test-live-paper`) and in
# the notebook harness (research/tests/test_notebooks.py's UPSTREAM_PANDAS4_DEPRECATIONS).
# platform/tests/test_pandas_pin.py fails any platform/ source making either call, so the
# filters only ever hide these upstream sites. Revisit when this project's nautilus_trader
# pin (currently 1.229.0) bumps past a version that fixes them upstream.


@pytest.fixture(autouse=True)
def _fresh_event_loop():
    """
    TradingNode's kernel construction calls asyncio.get_event_loop(), which returns
    the same thread-global "current" loop across every test in this process once one
    exists -- a loop closed by a previous test's node.dispose() would otherwise be
    handed to the next test's TradingNode construction too. Story 4.4 surfaced this:
    the entrypoint schedules tasks via loop.create_task(...) on the node's loop (see
    bots/__main__.py), which raises "Event loop is closed" against a stale loop from
    an earlier test in the same file. Force a fresh, open loop before every test here,
    for the same reason bot_tui/app.py's run() explicitly creates its own loop rather
    than trusting get_event_loop().
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield
    if not loop.is_closed():
        # A test that schedules a Supervisor.run()/HistoryPublisher.run() task per bot via
        # loop.create_task() (as bots/__main__.py does) --
        # these tests never drive the loop, so those tasks never get their first
        # `.send()`. Cancelling (rather than just closing the loop out from under
        # them) lets each coroutine actually run once to receive CancelledError,
        # which is what silences Python's "coroutine was never awaited"
        # RuntimeWarning -- closing an un-run loop directly does not (platform/CLAUDE.md
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

    `bots/tests` is a separate pytest collection root from `research/tests` and
    does not inherit that suite's own copy of this fixture -- each `TradingNode`/
    `BacktestEngine` construction re-initializes Nautilus's Rust logging subsystem unless
    at least one live `LogGuard` is kept alive for the whole session (see
    `research/tests/conftest.py` for the full mechanism writeup this mirrors verbatim).
    """
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    yield
    del engine


@pytest.fixture
def store(tmp_path):
    """Yield a `fills.db` in the test's tmp dir, closed afterwards (no leaked connection)."""
    fills = SqliteFillsStore(str(tmp_path / "fills.db"))
    yield fills
    fills.close()
