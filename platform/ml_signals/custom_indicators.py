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
Deprecated re-export shim (Story 24.2): `ml_signals.custom_indicators` moved to the views context
(`views.indicator_picker`).

Pure re-export, defines nothing: every name here *is* the `views.indicator_picker` object. Import
from `views.indicator_picker` instead, e.g. `from views.indicator_picker import replay_custom`.

`replay_indicator`/`catalog_json` are served as `views.indicator_picker.replay_custom`/
`custom_catalog_json` (renamed: they collided with the native catalog's names in the merged module).
"""

import warnings

from views.indicator_picker import CUSTOM_INDICATOR_CATALOG
from views.indicator_picker import CustomIndicatorSpec
from views.indicator_picker import ReplayFn
from views.indicator_picker import ReplayWindow
from views.indicator_picker import custom_catalog_json as catalog_json
from views.indicator_picker import replay_custom as replay_indicator


__all__ = [
    "CUSTOM_INDICATOR_CATALOG",
    "CustomIndicatorSpec",
    "ReplayFn",
    "ReplayWindow",
    "catalog_json",
    "replay_indicator",
]

REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.custom_indicators moved to views.indicator_picker (Story 24.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
