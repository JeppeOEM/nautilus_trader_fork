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
"""`NotifyDeliverer`: the `Deliverer` adapter over the one outbound notifier."""

from observability import notify

from alerting.domain.alert import Alert


class NotifyDeliverer:
    """
    Sends a fired alert through `observability.notify` (spine AD-D16).

    Invariant: an alert names channels only -- its own webhook when it has one, Telegram when the
    notifier's environment configures it -- and the notifier owns the transports and ledgers every
    failed send under `observability.notify.<transport>` with the alert's id in the detail.
    """

    def channels(self, alert: Alert) -> tuple[str, ...]:
        """
        Return the notification channels `alert` delivers on: its own webhook (when it has one) and
        Telegram (when configured).
        """
        named = [notify.webhook_channel(alert.webhook_url)] if alert.webhook_url else []
        if notify.telegram_configured():
            named.append(notify.TELEGRAM)
        return tuple(named)

    def deliver(self, alert: Alert, body: str) -> None:
        """
        Send `body` on every channel of `alert`. A failed send is ledgered by `notify` under the
        alert's id and never retried -- the next fire opportunity is the retry.
        """
        for channel in self.channels(alert):
            notify.notify(channel, f"alert {alert.id}", body)
