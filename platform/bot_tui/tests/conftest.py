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
bot_tui's tests are pure Python/dict/urwid-widget-construction logic -- no
BacktestEngine/BacktestNode is ever constructed anywhere in this package, so the
LOGGING_INITIALIZED native-abort guard fixture that research/tests/conftest.py needs
(see that file's docstring) does not apply here.
"""

import pytest

from bot_tui import archive_state
from bot_tui import bot_history_state
from bot_tui import bots_state
from bot_tui import collector_state
from bot_tui import markets_state


@pytest.fixture(autouse=True)
def _reset_bots_state() -> None:
    """
    bots_state's globals are module-level state shared across every test in this
    package. Individual test files call their own `_reset()` helper, but that only
    guards against ordering within one file -- a test in another file that forgets to
    reset first would otherwise inherit whatever the previous test left behind. This
    autouse fixture makes that reset unconditional regardless of collection order.
    """
    bots_state._LATEST_STATUSES = {}
    bots_state._LATEST_RECEIVED_AT = {}


@pytest.fixture(autouse=True)
def _reset_bot_history_state() -> None:
    """Guarantee the same cross-test-file isolation as above, for bot_history_state's globals."""
    bot_history_state.close_bot()


@pytest.fixture(autouse=True)
def _reset_archive_state() -> None:
    """Isolate archive_state's latest `archive:status` across test files (Story 25.1b)."""
    archive_state._LATEST_ARCHIVE_STATUS = None
    archive_state._LATEST_RECEIVED_AT = None


@pytest.fixture(autouse=True)
def _reset_collector_state() -> None:
    """Isolate collector_state's rows and per-venue plan aggregates across test files."""
    collector_state._LATEST_COLLECTOR_STATUS = {}
    collector_state._LATEST_RECEIVED_AT = {}
    collector_state._LATEST_PLANS = {}
    collector_state._PLAN_RECEIVED_AT = {}
    collector_state._REPUBLISHED_SINCE_PLAN = {}
    collector_state._LAST_REFUSAL = {}
    collector_state._REFUSAL_RECEIVED_AT = {}
    collector_state._SENT_ADDS = {}
    collector_state._ADD_REFUSALS = {}


@pytest.fixture(autouse=True)
def _reset_markets_state() -> None:
    """Isolate markets_state's per-venue market lists across test files (Story 29.5)."""
    markets_state._LATEST_MARKETS = {}
    markets_state._RECEIVED_AT = {}
