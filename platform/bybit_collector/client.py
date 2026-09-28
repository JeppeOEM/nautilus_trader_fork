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
Deprecated re-export shim (Story 26.2): `bybit_collector.client` moved to
`capture.venues.bybit.client`.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from capture.venues.bybit.client import LINEAR_FEED
from capture.venues.bybit.client import LINEAR_TRADES_FEED
from capture.venues.bybit.client import ORDERBOOK_DEPTH
from capture.venues.bybit.client import SPOT_FEED
from capture.venues.bybit.client import SPOT_TRADES_FEED
from capture.venues.bybit.client import BybitClient


__all__ = [
    "LINEAR_FEED",
    "LINEAR_TRADES_FEED",
    "ORDERBOOK_DEPTH",
    "SPOT_FEED",
    "SPOT_TRADES_FEED",
    "BybitClient",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "bybit_collector.client moved to "
    "capture.venues.bybit.client (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
