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
    """Root-causes the "Fatal Python error: Aborted at kernel.py:231" native abort (see
    deferred-work.md, Stories 2.2/2.3).

    Nautilus's Rust logging subsystem (crates/common/src/logging/logger.rs) resets its
    LOGGING_INITIALIZED flag to False once the last live LogGuard is dropped, so a later
    BacktestEngine/BacktestNode construction can genuinely re-initialize logging from
    scratch. But the underlying `log` crate's global logger can only ever be installed
    once per process, permanently -- a real second init attempt fails at that layer, and
    that failure is `.expect()`-panicked in the C-FFI wrapper (crates/common/src/ffi/
    logging.rs's `logging_init`), which aborts the whole Python process since the panic
    crosses an `extern "C"` boundary. Whether this triggers depends purely on Python
    object-lifetime timing -- whichever engine happens to hold the very last live
    LogGuard when it gets garbage-collected right before the next engine constructs.

    Constructing one throwaway BacktestEngine here, as the very first thing pytest does,
    and never disposing/releasing it for the life of the session keeps the guard count
    above zero throughout -- so LOGGING_INITIALIZED is never falsely reset, and no test's
    BacktestEngine/BacktestNode ever re-attempts the doomed second real init. This is why
    per-file/filename-ordering mitigations were needed before: they only ever accidentally
    avoided the guard count hitting zero at the wrong moment.
    """
    engine = BacktestEngine(config=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    yield
    del engine
