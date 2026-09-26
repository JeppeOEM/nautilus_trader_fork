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
Deprecated re-export shim (Story 25.2): `ranking_engine.metrics_store` moved to the ranking context
(`ranking.application.queries`).

Pure re-export, defines nothing: every name here *is* the `ranking.application.queries` object
(the store's read service; the writer is `ranking.infrastructure.metrics_store`). Import from
`ranking.application.queries` instead.
"""

import warnings

from ranking.application.queries import history
from ranking.application.queries import nearest


__all__ = [
    "history",
    "nearest",
]

REMOVE_AFTER = "25-4-collection-control-plan-intent-vs-applied-set"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "ranking_engine.metrics_store moved to ranking.application.queries (Story 25.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
