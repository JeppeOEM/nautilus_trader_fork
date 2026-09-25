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
Deprecated re-export shim (Story 24.3): `data_api.alerts` moved to the alerting context
(`platform/alerting/`).

Pure re-export, defines nothing: every name here *is* the `alerting` object. Import from its new
home instead: `Alert`/`new_alert`/`status_of` from `alerting.domain.alert`;
`FREQUENCIES`/`RunState`/`evaluate`/`render` from `alerting.domain.policy`; `AlertEngine` from
`alerting.application.engine` (its constructor now takes a `Deliverer`, not a `post` callable);
`AlertStore`/`ALERTS_PATH` from `alerting.infrastructure.toml_store` (composition roots only).

The running app's instances are no longer here (alerting holds no module state): `store` and
`engine` are `data_api.alert_wiring`'s, and `deliver`/`channels`/`telegram_configured` became
methods of the `NotifyDeliverer` adapter and the notifier's own check, so each raises, naming it.
"""

import warnings

from alerting.application.engine import AlertEngine
from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.domain.alert import status_of
from alerting.domain.policy import FREQUENCIES
from alerting.domain.policy import RunState
from alerting.domain.policy import evaluate
from alerting.domain.policy import render
from alerting.infrastructure.toml_store import ALERTS_PATH
from alerting.infrastructure.toml_store import AlertStore


__all__ = [
    "ALERTS_PATH",
    "FREQUENCIES",
    "Alert",
    "AlertEngine",
    "AlertStore",
    "RunState",
    "evaluate",
    "new_alert",
    "render",
    "status_of",
]

REMOVE_AFTER = "25-1-archive-context-archiveday-one-deleter-one-rewriter"

_REPLACED_NAMES: dict[str, str] = {
    "store": "data_api.alert_wiring.store",
    "engine": "data_api.alert_wiring.engine",
    "deliver": "alerting.infrastructure.deliverer.NotifyDeliverer.deliver",
    "channels": "alerting.infrastructure.deliverer.NotifyDeliverer.channels",
    "telegram_configured": "observability.notify.telegram_configured",
}


def __getattr__(name: str) -> object:
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"data_api.alerts.{name} was replaced by {_REPLACED_NAMES[name]} (Story 24.3)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "data_api.alerts moved to the alerting context (Story 24.3); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
