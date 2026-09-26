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
Deprecated re-export shim (Story 25.4): `BybitConfig` moved to capture's one venue loader,
`collector_core.config` (`load_config(path)` -> `collector_core.config.load_venue_config(path,
"BYBIT")`, which returns `(BybitConfig, CollectionPlan)` -- a changed shape, so it is not
re-exported).

Pure re-export, defines nothing: every name here *is* the `collector_core.config` object.
"""

import warnings

from collector_core.config import BybitConfig


__all__ = [
    "BybitConfig",
]

REMOVE_AFTER = "26-2-capture-package-and-venue-packages-with-entrypoints"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "bybit_collector.config moved to collector_core.config (Story 25.4); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
