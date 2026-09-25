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
Deprecated re-export shim (Story 24.2): `data_api.redis_bus` moved to the views context
(`views.rankings_bus`).

Pure re-export, defines nothing: every name here *is* the `views.rankings_bus` object. Import from
`views.rankings_bus` instead, e.g. `from views.rankings_bus import RankingsBus`.

The running app's instance and the Redis URL are no longer here (views holds no module state and
reads no interface settings): `bus` and `REDIS_URL` raise, naming `data_api.buses.bus` and
`data_api.settings.REDIS_URL`.
"""

import warnings

from views.rankings_bus import QUEUE_MAX
from views.rankings_bus import RANKINGS_CHANNEL
from views.rankings_bus import RankingsBus
from views.rankings_bus import put_drop_oldest


__all__ = [
    "QUEUE_MAX",
    "RANKINGS_CHANNEL",
    "RankingsBus",
    "put_drop_oldest",
]

REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"

_REPLACED_NAMES: dict[str, str] = {
    "bus": "data_api.buses.bus",
    "REDIS_URL": "data_api.settings.REDIS_URL",
}


def __getattr__(name: str) -> object:
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"data_api.redis_bus.{name} was replaced by {_REPLACED_NAMES[name]} (Story 24.2)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "data_api.redis_bus moved to views.rankings_bus (Story 24.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
