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
"""The `Alert` aggregate: one saved alert, its condition and its derived lifecycle status."""

import time
import uuid
from dataclasses import dataclass
from typing import Any

from alerting.domain.conditions import level_of
from alerting.domain.conditions import validate_condition


@dataclass
class Alert:
    """
    One saved alert: a `condition` (`domain.conditions`, Story 33.8) watched on the alert's own
    `(instrument_id, bar_seconds)` and fired by its `frequency`.

    Invariant (frozen `alerts.toml`, AD-D12): the fields, their names and their order are the
    persisted key set and the `/api/alerts` response body, so none may be renamed or reordered;
    Story 33.8 appended `condition` and `invalid_reason` only. `frequency` stays the stored text
    (one of `domain.policy.FiringPolicy`'s values).

    Invariant (one condition, one level mirror): `condition` is always a normalised condition
    after construction -- an alert stored before Story 33.8 (no `condition`) reads as
    `{"kind": "price_cross", "level": level}` -- and `level` equals `condition["level"]` for the
    five price-level kinds and is None for every other kind. A construction where they disagree
    raises (`ValueError`), as a corrupt `alerts.toml` does at load: the commands that could
    violate it are construction (`new_alert`, the store's load) and `AlertService.update`, which
    builds a new `Alert` through this same check.

    `invalid_reason` is set when the condition's input is gone for good (an indicator no longer in
    the catalog, a deleted trendline): the alert is then `invalid` and never evaluated again until
    an edit re-validates it.
    """

    id: str
    instrument_id: str
    level: float | None
    frequency: str
    bar_seconds: int
    template: str
    webhook_url: str  # may be empty when Telegram is the delivery channel
    created_ns: int
    expires_at_ns: int | None = None
    triggered: bool = False  # only `only_once` alerts ever set this
    last_fired_ns: int | None = None
    condition: dict[str, Any] | None = None
    invalid_reason: str | None = None

    def __post_init__(self) -> None:
        if self.condition is None:
            if self.level is None:
                raise ValueError(f"alert {self.id} has neither a level nor a condition")
            self.condition = {"kind": "price_cross", "level": self.level}
        self.condition = validate_condition(self.condition)
        expected = level_of(self.condition)
        if self.level != expected:
            raise ValueError(
                f"alert {self.id}: level {self.level!r} disagrees with its "
                f"{self.condition['kind']} condition (expected {expected!r})"
            )
        if self.level is not None:
            self.level = float(self.level)

    @property
    def rule(self) -> dict[str, Any]:
        """The normalised condition, typed: `__post_init__` always sets it."""
        return self.condition if self.condition is not None else {}

    @property
    def kind(self) -> str:
        """The condition's kind."""
        return str(self.rule["kind"])


def status_of(alert: Alert, now_ns: int) -> str:
    """`invalid` first (its input is gone), then `triggered`, `expired`, else `active`."""
    if alert.invalid_reason is not None:
        return "invalid"
    if alert.triggered:
        return "triggered"
    if alert.expires_at_ns is not None and now_ns >= alert.expires_at_ns:
        return "expired"
    return "active"


def new_alert(**fields: Any) -> Alert:
    return Alert(id=uuid.uuid4().hex, created_ns=time.time_ns(), **fields)
