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
Deprecated re-export shim (Story 24.2): `data_api.routes.paging` moved to the views context
(`views.catalog_reads`).

Pure re-export, defines nothing: every name here *is* the `views.catalog_reads` object. Import from
`views.catalog_reads` instead, e.g. `from views.catalog_reads import fetch_page`.
"""

import warnings

from views.catalog_reads import fetch_page
from views.catalog_reads import has_older_data


__all__ = [
    "fetch_page",
    "has_older_data",
]

REMOVE_AFTER = "24-4-research-pure-consumer-and-broken-tests-repaired"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "data_api.routes.paging moved to views.catalog_reads (Story 24.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
