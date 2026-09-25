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
Deprecated re-export shim (Story 24.2): `data_api.live_candles` moved to the views context
(`views.live_candles`).

Pure re-export, defines nothing: every name here *is* the `views.live_candles` object. Import from
`views.live_candles` instead, e.g. `from views.live_candles import LiveCandleBus`.

The running app's instance is no longer here (views holds no module state): it is
`data_api.buses.live_candle_bus`, so `live_candle_bus` raises, naming it.
"""

import warnings

from views.live_candles import RECENT_SECONDS
from views.live_candles import SNAPSHOTS_CHANNEL
from views.live_candles import LiveCandleBus


__all__ = [
    "RECENT_SECONDS",
    "SNAPSHOTS_CHANNEL",
    "LiveCandleBus",
]

REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"

_REPLACED_NAMES: dict[str, str] = {
    "live_candle_bus": "data_api.buses.live_candle_bus",
}


def __getattr__(name: str) -> object:
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"data_api.live_candles.{name} was replaced by {_REPLACED_NAMES[name]} (Story 24.2)"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "data_api.live_candles moved to views.live_candles (Story 24.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
