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
"""`AlertService`: the create/list/update/delete use cases behind `/api/alerts`."""

import dataclasses
from typing import Any

from alerting.application.engine import AlertEngine
from alerting.application.ports import AlertRepository
from alerting.application.ports import Deliverer
from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.domain.conditions import level_of
from alerting.domain.conditions import resolve_condition
from alerting.domain.conditions import validate_condition
from alerting.domain.policy import FiringPolicy


class NoDeliveryChannel(Exception):
    """The alert would name no channel: no webhook URL and Telegram is not configured."""


# Frozen (AD-D12): the `/api/alerts` 422 `detail` text.
_NO_CHANNEL = (
    "no delivery channel: set a webhook URL or configure TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"
)


class AlertService:
    """
    The alert CRUD an interface adapter calls; it formats and transports only.

    Invariant (every saved alert is deliverable): `create` and `update` refuse an alert its
    deliverer would send nowhere, so a fire can never be silently undeliverable; `update` and
    `delete` also drop the engine's run state, so an edited alert, or a re-created one with a
    reused id, could never inherit a stale previous value or window.
    """

    def __init__(self, store: AlertRepository, deliverer: Deliverer, engine: AlertEngine) -> None:
        self._store = store
        self._deliverer = deliverer
        self._engine = engine

    def list(self) -> list[Alert]:
        return self._store.list()

    def get(self, alert_id: str) -> Alert | None:
        return next((a for a in self._store.list() if a.id == alert_id), None)

    def create(self, **fields: Any) -> Alert:
        """
        Save a new alert built from `fields`, `level` and/or `condition` resolved by
        `conditions.resolve_condition` (`ConditionError` naming the field); raises
        `NoDeliveryChannel` when undeliverable.
        """
        condition = resolve_condition(fields.pop("level", None), fields.pop("condition", None))
        alert = new_alert(level=level_of(condition), condition=condition, **fields)
        self._require_channel(alert)
        self._store.add(alert)
        return alert

    def update(
        self,
        alert_id: str,
        *,
        condition: dict[str, Any],
        frequency: str,
        expires_at_ns: int | None,
        template: str,
        webhook_url: str,
        rearm: bool,
    ) -> Alert | None:
        """
        Replace an alert's editable fields (its instrument and bar width are its identity and stay);
        None when there is no such alert. The condition is re-validated, so `invalid_reason` is
        cleared. `triggered` survives only on an `only_once` alert not re-armed: `rearm` clears it,
        and no other frequency is ever triggered. Raises `ConditionError` or `NoDeliveryChannel`
        before anything is saved.

        The edit is built inside the store's read-modify-write (`AlertRepository.update`) from the
        stored alert, so a fire recorded while the request ran (`triggered`, `last_fired_ns`) is
        kept rather than overwritten by an older copy.
        """
        normalised = validate_condition(condition)
        keep_triggered = frequency == FiringPolicy.ONLY_ONCE and not rearm

        def edit(current: Alert) -> Alert:
            edited = dataclasses.replace(
                current,
                level=level_of(normalised),
                condition=normalised,
                frequency=frequency,
                expires_at_ns=expires_at_ns,
                template=template,
                webhook_url=webhook_url,
                triggered=current.triggered and keep_triggered,
                invalid_reason=None,
            )
            self._require_channel(edited)
            return edited

        edited = self._store.update(alert_id, edit)
        if edited is not None:
            self._engine.forget(alert_id)
        return edited

    def delete(self, alert_id: str) -> bool:
        """Remove the alert; False when there is none."""
        if not self._store.delete(alert_id):
            return False
        self._engine.forget(alert_id)
        return True

    def _require_channel(self, alert: Alert) -> None:
        if not self._deliverer.channels(alert):
            raise NoDeliveryChannel(_NO_CHANNEL)
