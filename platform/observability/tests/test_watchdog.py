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
"""The generic `(down_since, reminder)` transition: alert once, remind on cadence, recover once."""

from observability.watchdog import DEFAULT_REMINDER_NS
from observability.watchdog import AlertTexts
from observability.watchdog import transition


_T0 = 1_000_000_000_000  # arbitrary epoch-ns anchor
_TEXTS = AlertTexts(
    down="down", still="still {down_for_s:.0f}s", recovered="back {down_for_s:.0f}s"
)


def test_up_and_never_down_is_silent() -> None:
    assert transition(_T0, False, None, 0, _TEXTS) == (None, None, 0)


def test_going_down_alerts_once_and_starts_both_clocks() -> None:
    assert transition(_T0, True, None, 0, _TEXTS) == ("down", _T0, _T0)


def test_still_down_inside_the_reminder_interval_is_silent() -> None:
    assert transition(_T0 + DEFAULT_REMINDER_NS, True, _T0, _T0, _TEXTS) == (None, _T0, _T0)


def test_still_down_past_the_reminder_interval_reminds_and_keeps_the_outage_start() -> None:
    now_ns = _T0 + DEFAULT_REMINDER_NS + 1_000_000_000
    assert transition(now_ns, True, _T0, _T0, _TEXTS) == ("still 601s", _T0, now_ns)


def test_recovery_notifies_once_and_clears_state() -> None:
    now_ns = _T0 + DEFAULT_REMINDER_NS + 1_000_000_000
    assert transition(now_ns, False, _T0, _T0, _TEXTS) == ("back 601s", None, 0)


def test_reminder_interval_is_the_callers_choice() -> None:
    now_ns = _T0 + 2_000_000_000
    assert transition(now_ns, True, _T0, _T0, _TEXTS, reminder_ns=1_000_000_000)[0] == "still 2s"
