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
"""The two ports alerting's application layer needs: where alerts are kept and how they are sent."""

from typing import Protocol

from alerting.domain.alert import Alert


class AlertRepository(Protocol):
    """
    The saved alerts, shared by the CRUD service and the engine.

    Invariant (durable before acknowledged, frozen key set): `add`/`delete` persist before they
    return, so an alert the API answered 201 for survives a restart; `record_fire` marks the fire
    in memory *first* and never raises on a failed persist, so an `only_once` alert cannot repeat
    this run and a full disk can never swallow the fire itself. The stored shape is the frozen
    `alerts.toml` key set (AD-D12).
    """

    def list(self) -> list[Alert]:
        """Return a snapshot copy of every saved alert, in creation order."""
        ...

    def add(self, alert: Alert) -> None:
        """Save `alert`; raises when it cannot be persisted."""
        ...

    def delete(self, alert_id: str) -> bool:
        """Remove the alert with `alert_id`; False when there is none."""
        ...

    def record_fire(self, alert: Alert, ts_ns: int) -> None:
        """Stamp `last_fired_ns` (and `triggered` for `only_once`) on `alert`; never raises."""
        ...


class Deliverer(Protocol):
    """
    How a fired alert reaches its owner.

    Invariant (an alert names channels, never transports, AD-D16): `channels` derives the channel
    names from the alert and the environment and `deliver` sends on each of them through the one
    outbound notifier, which owns the transports and ledgers a failed send. `deliver` is blocking
    network I/O and never raises for a failed send -- the next fire opportunity is the retry.
    An alert with no channel cannot be delivered at all, which is why creation refuses it.
    """

    def channels(self, alert: Alert) -> tuple[str, ...]:
        """Return the notification channels `alert` delivers on (empty: undeliverable)."""
        ...

    def deliver(self, alert: Alert, body: str) -> None:
        """Send `body` on every channel of `alert`."""
        ...
