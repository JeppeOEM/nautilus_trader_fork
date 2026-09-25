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
Deprecated re-export shim (Story 24.2): `ml_signals.chart_data` moved to the views context
(`views.chart_series`).

Pure re-export, defines nothing: every name here *is* the `views.chart_series` object. Import from
`views.chart_series` instead, e.g. `from views.chart_series import compute_chart_series`.
"""

import warnings

from views.chart_series import compute_chart_series


__all__ = [
    "compute_chart_series",
]

REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.chart_data moved to views.chart_series (Story 24.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
