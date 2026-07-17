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
"""Watchdog debounce state machine: alert once on stale, remind periodically, notify on recovery."""

from dydx_collector.collector import _WATCHDOG_REMINDER_NS
from dydx_collector.collector import _watchdog_transition


_T0 = 1_000_000_000_000  # arbitrary epoch-ns anchor


def test_healthy_feed_produces_no_message() -> None:
    message, down_since_ns, last_reminder_ns = _watchdog_transition(
        _T0, is_stale=False, down_since_ns=None, last_reminder_ns=0
    )
    assert message is None
    assert down_since_ns is None
    assert last_reminder_ns == 0


def test_transition_to_stale_alerts_once_and_starts_clock() -> None:
    message, down_since_ns, last_reminder_ns = _watchdog_transition(
        _T0, is_stale=True, down_since_ns=None, last_reminder_ns=0
    )
    assert message is not None
    assert down_since_ns == _T0
    assert last_reminder_ns == _T0


def test_still_stale_before_reminder_interval_is_silent() -> None:
    message, down_since_ns, last_reminder_ns = _watchdog_transition(
        _T0 + 1_000_000_000,  # 1s later
        is_stale=True,
        down_since_ns=_T0,
        last_reminder_ns=_T0,
    )
    assert message is None
    assert down_since_ns == _T0
    assert last_reminder_ns == _T0


def test_still_stale_past_reminder_interval_reminds_and_resets_timer() -> None:
    now_ns = _T0 + _WATCHDOG_REMINDER_NS + 1
    message, down_since_ns, last_reminder_ns = _watchdog_transition(
        now_ns, is_stale=True, down_since_ns=_T0, last_reminder_ns=_T0
    )
    assert message is not None
    assert down_since_ns == _T0  # original outage start is preserved
    assert last_reminder_ns == now_ns


def test_recovery_clears_state_and_notifies() -> None:
    now_ns = _T0 + 5_000_000_000
    message, down_since_ns, last_reminder_ns = _watchdog_transition(
        now_ns, is_stale=False, down_since_ns=_T0, last_reminder_ns=_T0
    )
    assert message is not None
    assert down_since_ns is None
    assert last_reminder_ns == 0
