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
Deprecated re-export shim (Story 23.2): `common.venues` moved to the shared kernel (kernel.venues).

Pure re-export, defines nothing: every name here *is* the kernel object (a copied class would
register a second Arrow class or break `is` dispatch). Import from the kernel instead, e.g.
`from kernel.venues import market_kind`.
"""

import warnings

from kernel.venues import VENUE_KINDS
from kernel.venues import market_kind
from kernel.venues import venue_kind


__all__ = ["VENUE_KINDS", "market_kind", "venue_kind"]

REMOVE_AFTER = "24-2-views-read-models-and-reader-side-revalidation-removed"

# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    f"common.venues moved to kernel.venues (Story 23.2); this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
