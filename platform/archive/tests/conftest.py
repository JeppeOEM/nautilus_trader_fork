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
from collections.abc import Iterator

import pytest

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig


@pytest.fixture(scope="session")
def nautilus_log_guard() -> Iterator[None]:
    """
    Keep one Nautilus `LogGuard` alive from the first archive test that builds a backtest engine
    to the end of the pytest session.

    Invariant protected: Nautilus's Rust logger is installed once per process; when the last live
    `LogGuard` is dropped its "initialized" flag resets, and the next `BacktestEngine`/
    `BacktestNode` re-attempts the install, which panics across the FFI boundary and aborts the
    whole process (the root cause is in `research/tests/conftest.py`'s
    `_keep_nautilus_log_guard_alive`). `make test` runs `archive/tests` before `research/tests`
    in one process, so an archive backtest dropping its guard would kill the later research
    engines.

    Session-scoped, so it is released only at session end, never between tests. Requested
    explicitly (not autouse) by the test that runs a `BacktestNode`: the other archive tests
    build no engine and need no logging, and a run of them alone then initializes none.
    """
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    yield
    del engine
