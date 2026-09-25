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
The firing policy: when a saved alert fires, as a pure state-machine step, and what it says.

Split from delivery so the frequency/expiration rules are testable without any network:
`evaluate()` is a pure state-machine step, `render()` a pure string substitution.
"""

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from enum import StrEnum

from alerting.domain.alert import Alert
from alerting.domain.alert import status_of


_NS_PER_S = 1_000_000_000


class FiringPolicy(StrEnum):
    """
    How often a crossing fires. Values are the stored `Alert.frequency` text and the `/api/alerts`
    `frequency` literal (frozen, AD-D12), so a `StrEnum` member compares equal to the stored string.
    """

    ONCE_PER_BAR_CLOSE = "once_per_bar_close"
    ONCE_PER_BAR = "once_per_bar"
    ONLY_ONCE = "only_once"


FREQUENCIES: tuple[str, ...] = tuple(policy.value for policy in FiringPolicy)


@dataclass
class RunState:
    """
    Per-alert, in-memory only: a restart forgets the previous price, so the first tick after
    a restart can never fire (a cross needs two observations).
    """

    last_price: float | None = None
    prev_close: float | None = None  # once_per_bar_close: close of the bar before the last one
    bucket: int | None = None
    fired_bucket: int | None = None


def _crossed(prev: float | None, cur: float | None, level: float) -> bool:
    # `cur` is optional for the once_per_bar_close caller (`state.last_price`, always set once a
    # bucket is); a missing observation never crosses.
    return prev is not None and cur is not None and (prev < level <= cur or prev > level >= cur)


def evaluate(alert: Alert, state: RunState, price: float, ts_ns: int) -> float | None:
    """
    One tick of the frequency/expiration state machine. Returns the price to report when
    `alert` fires (for once_per_bar_close that is the closed bar's close, not this tick), else None.
    """
    if status_of(alert, ts_ns) != "active":
        return None
    bucket = ts_ns // (alert.bar_seconds * _NS_PER_S)
    if alert.frequency == FiringPolicy.ONCE_PER_BAR_CLOSE:
        # Only bar closes are compared: a bar's close is the last price seen before the
        # bucket rolls over, so the crossing is decided once, on the first tick of the next bar.
        closed_bar_close = None
        if state.bucket is not None and bucket != state.bucket:
            if _crossed(state.prev_close, state.last_price, alert.level):
                closed_bar_close = state.last_price
            state.prev_close = state.last_price
        state.bucket, state.last_price = bucket, price
        return closed_bar_close
    crossed = _crossed(state.last_price, price, alert.level)
    state.last_price = price
    if not crossed:
        return None
    if alert.frequency == FiringPolicy.ONCE_PER_BAR:
        if state.fired_bucket == bucket:
            return None
        state.fired_bucket = bucket
    return price


def render(template: str, ticker: str, close: float, ts_ns: int, bar_seconds: int) -> str:
    """Plain replace over the four documented placeholders (`{{interval}}` is bar seconds)."""
    time_iso = datetime.fromtimestamp(ts_ns / _NS_PER_S, UTC).isoformat()
    values = {"ticker": ticker, "close": str(close), "time": time_iso, "interval": str(bar_seconds)}
    for name, value in values.items():
        template = template.replace("{{" + name + "}}", value)
    return template
