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
"""The `Alert` aggregate: one saved price alert and its derived lifecycle status."""

import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class Alert:
    """
    `level` is always a static price: a horizontal-line condition is resolved to the line's
    price by the dialog at creation time (the line itself is client-side chart state).

    Invariant (frozen `alerts.toml`, AD-D12): the fields, their names and their order are the
    persisted key set and the `/api/alerts` response body, so none may be renamed or reordered.
    `frequency` stays the stored text (one of `domain.policy.FiringPolicy`'s values).
    """

    id: str
    instrument_id: str
    level: float
    frequency: str
    bar_seconds: int
    template: str
    webhook_url: str  # may be empty when Telegram is the delivery channel
    created_ns: int
    expires_at_ns: int | None = None
    triggered: bool = False  # only `only_once` alerts ever set this
    last_fired_ns: int | None = None


def status_of(alert: Alert, now_ns: int) -> str:
    if alert.triggered:
        return "triggered"
    if alert.expires_at_ns is not None and now_ns >= alert.expires_at_ns:
        return "expired"
    return "active"


def new_alert(**fields: Any) -> Alert:
    return Alert(id=uuid.uuid4().hex, created_ns=time.time_ns(), **fields)
