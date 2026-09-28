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
Deprecated re-export shim (Story 26.2): `collector_core.config` moved to
`capture.application.config`, `capture.infrastructure.config`, `capture.venues.bybit.config`,
`capture.venues.dydx.config`.

It was split: the thresholds (`CoreConfig`, `core_config_from_dict`) are the application's,
the one venue loader (`VENUE_SCHEMAS`, `load_venue_config`, `plan_toml_fields`, ...) is
infrastructure, and each venue's own config class and caps are its
`capture/venues/<v>/config.py`.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from capture.application.config import CoreConfig
from capture.application.config import core_config_from_dict
from capture.infrastructure.config import VENUE_SCHEMAS
from capture.infrastructure.config import VenueSchema
from capture.infrastructure.config import load_toml
from capture.infrastructure.config import load_venue_config
from capture.infrastructure.config import plan_toml_fields
from capture.infrastructure.config import venue_config_from_dict
from capture.venues.bybit.config import BybitConfig
from capture.venues.dydx.config import DYDX_MAX_COLLECTED_INSTRUMENTS
from capture.venues.dydx.config import DYDX_MAX_WS_SUBSCRIPTIONS
from capture.venues.dydx.config import DydxConfig


__all__ = [
    "DYDX_MAX_COLLECTED_INSTRUMENTS",
    "DYDX_MAX_WS_SUBSCRIPTIONS",
    "VENUE_SCHEMAS",
    "BybitConfig",
    "CoreConfig",
    "DydxConfig",
    "VenueSchema",
    "core_config_from_dict",
    "load_toml",
    "load_venue_config",
    "plan_toml_fields",
    "venue_config_from_dict",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.config moved to "
    "capture (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
