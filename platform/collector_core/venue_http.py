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
Deprecated re-export shim (Story 23.2): `collector_core.venue_http` moved to the shared kernel (kernel.venue_http, kernel.venues).

Pure re-export, defines nothing: every name here *is* the kernel object (a copy would leave two
definitions to drift apart and break `is`/`except` identity). Import from the kernel instead, e.g.
`from kernel import venue_http`.
"""

import warnings

from kernel.venue_http import BYBIT_URLS
from kernel.venue_http import DYDX_NETWORKS
from kernel.venue_http import HYPERLIQUID_URLS
from kernel.venue_http import USER_AGENT
from kernel.venue_http import HttpJson
from kernel.venue_http import http_json
from kernel.venues import bybit_category


__all__ = [
    "BYBIT_URLS",
    "DYDX_NETWORKS",
    "HYPERLIQUID_URLS",
    "USER_AGENT",
    "HttpJson",
    "bybit_category",
    "http_json",
]

REMOVE_AFTER = "24-2-views-read-models-and-reader-side-revalidation-removed"

# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.venue_http moved to kernel.venue_http, kernel.venues (Story 23.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
