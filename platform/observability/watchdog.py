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
The generic `(down_since, reminder)` alert transition over a boolean (spine AD-D16).

Only the debounce lives here: alert once when a condition goes down, remind at most every
`reminder_ns` while it stays down, notify once on recovery. What counts as "down" (a silent feed,
a stale heartbeat) is the caller's verdict and stays in the caller's context, as do the texts.
"""

from dataclasses import dataclass


# Re-notify at most every 10 min while down: the OBS-01 watchdog's production cadence.
DEFAULT_REMINDER_NS: int = 600_000_000_000


@dataclass(frozen=True)
class AlertTexts:
    """
    An alert's three messages. `still` and `recovered` are `str.format` templates receiving
    `down_for_s` (seconds since the alert opened).
    """

    down: str
    still: str
    recovered: str


def transition(
    now_ns: int,
    is_down: bool,
    down_since_ns: int | None,
    last_reminder_ns: int,
    texts: AlertTexts,
    reminder_ns: int = DEFAULT_REMINDER_NS,
) -> tuple[str | None, int | None, int]:
    """
    Pure state-machine step: (message_or_None, down_since_ns, last_reminder_ns).

    Kept separate from any loop and from the notify transport so the debounce is unit-testable
    without a clock or a network.
    """
    if is_down:
        if down_since_ns is None:
            return (texts.down, now_ns, now_ns)
        if now_ns - last_reminder_ns > reminder_ns:
            down_for_s = (now_ns - down_since_ns) / 1e9
            return (texts.still.format(down_for_s=down_for_s), down_since_ns, now_ns)
        return (None, down_since_ns, last_reminder_ns)

    if down_since_ns is not None:
        down_for_s = (now_ns - down_since_ns) / 1e9
        return (texts.recovered.format(down_for_s=down_for_s), None, 0)

    return (None, None, last_reminder_ns)
