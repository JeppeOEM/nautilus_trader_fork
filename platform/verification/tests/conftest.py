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
"""The verification tests' one shared fixture: the Nautilus log guard (Story 31.7)."""

from collections.abc import Iterator

import pytest

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig


@pytest.fixture(scope="session")
def nautilus_log_guard() -> Iterator[None]:
    """
    Keep one Nautilus `LogGuard` alive from the first catalog-tool test to the end of the session.

    Invariant protected (as `archive/tests/conftest.py`'s guard): Nautilus's Rust logger installs
    once per process, and when the last live `LogGuard` drops its "initialized" flag resets, so the
    next engine that initialises logging panics across the FFI boundary and aborts the process.
    The catalog tool's `BacktestNode` runs bypass logging and hold no guard; this one keeps any
    engine elsewhere in the same `make test` process (archive and research build some) from ever
    seeing the flag reset between them. Session-scoped and requested explicitly by the tests that
    run the tool end to end.
    """
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    yield
    engine.dispose()
