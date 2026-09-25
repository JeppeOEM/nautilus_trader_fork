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
"""`AlertService`: the create/list/delete use cases behind `/api/alerts`."""

from typing import Any

from alerting.application.engine import AlertEngine
from alerting.application.ports import AlertRepository
from alerting.application.ports import Deliverer
from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert


class NoDeliveryChannel(Exception):
    """The alert would name no channel: no webhook URL and Telegram is not configured."""


class AlertService:
    """
    The alert CRUD an interface adapter calls; it formats and transports only.

    Invariant (every saved alert is deliverable): `create` refuses an alert its deliverer would
    send nowhere, so a fire can never be silently undeliverable; `delete` also drops the engine's
    run state, so a re-created alert with a reused id could never inherit a stale previous price.
    """

    def __init__(self, store: AlertRepository, deliverer: Deliverer, engine: AlertEngine) -> None:
        self._store = store
        self._deliverer = deliverer
        self._engine = engine

    def list(self) -> list[Alert]:
        return self._store.list()

    def create(self, **fields: Any) -> Alert:
        """Save a new alert built from `fields`; raises `NoDeliveryChannel` when undeliverable."""
        alert = new_alert(**fields)
        if not self._deliverer.channels(alert):
            raise NoDeliveryChannel(
                "no delivery channel: set a webhook URL or configure "
                "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"
            )
        self._store.add(alert)
        return alert

    def delete(self, alert_id: str) -> bool:
        """Remove the alert; False when there is none."""
        if not self._store.delete(alert_id):
            return False
        self._engine.forget(alert_id)
        return True
