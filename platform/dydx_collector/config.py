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
Deprecated re-export shim (Story 25.4): dYdX's config is read by capture's one venue loader, and
the plan it holds is the collection-control context's aggregate.

- `DydxConfig` -> `collector_core.config.DydxConfig` (the thresholds and cadences only: the plan
  keys moved to the plan).
- `InstrumentEntry` -> `collection_control.domain.plan.InstrumentEntry`.
- `load_config(path)` -> `collector_core.config.load_venue_config(path, "DYDX")`, which returns
  `(DydxConfig, CollectionPlan)` -- a changed shape, so it is not re-exported.
- `save_config(config, path)` -> `collection_control.infrastructure.plan_store.TomlPlanStore(path,
  "DYDX").save(plan)` -- a changed shape (it saves a plan), so it is not re-exported.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from collection_control.domain.plan import InstrumentEntry
from collector_core.config import DydxConfig


__all__ = [
    "DydxConfig",
    "InstrumentEntry",
]

REMOVE_AFTER = "26-2-capture-package-and-venue-packages-with-entrypoints"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "dydx_collector.config moved to collector_core.config (DydxConfig, load_venue_config) and "
    "collection_control (InstrumentEntry, TomlPlanStore) (Story 25.4); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
