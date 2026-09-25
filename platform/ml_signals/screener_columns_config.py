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
Deprecated re-export shim (Story 24.2): `ml_signals.screener_columns_config` moved to the views
context (`views.preferences`).

Pure re-export, defines nothing: every name here *is* the `views.preferences` object. Import from
`views.preferences` instead, e.g. `from views.preferences import load_screener_columns`.

`load_config`/`save_config` are served as `views.preferences.load_screener_columns`/
`save_screener_columns` (renamed: `views.preferences` holds both preference files' loaders).
"""

import warnings

from views.preferences import DEFAULT_BAR_SECONDS
from views.preferences import ColumnEntry
from views.preferences import IndicatorEntry
from views.preferences import load_screener_columns as load_config
from views.preferences import save_screener_columns as save_config


__all__ = [
    "DEFAULT_BAR_SECONDS",
    "ColumnEntry",
    "IndicatorEntry",
    "load_config",
    "save_config",
]

REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.screener_columns_config moved to views.preferences (Story 24.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
