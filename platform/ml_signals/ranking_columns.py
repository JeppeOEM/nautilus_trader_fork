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
Deprecated re-export shim (Story 24.2): `ml_signals.ranking_columns` moved to the views context
(`views.ranking_columns`).

Pure re-export, defines nothing: every name here *is* the `views.ranking_columns` object. Import
from `views.ranking_columns` instead, e.g. `from views.ranking_columns import RANKING_COLS`.
"""

import warnings

from views.ranking_columns import NEGATIVE_COLOR
from views.ranking_columns import POSITIVE_COLOR
from views.ranking_columns import RANKING_COLS


__all__ = [
    "NEGATIVE_COLOR",
    "POSITIVE_COLOR",
    "RANKING_COLS",
]

REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ml_signals.ranking_columns moved to views.ranking_columns (Story 24.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
