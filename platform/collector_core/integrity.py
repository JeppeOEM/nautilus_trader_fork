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
Deprecated re-export shim (Story 25.1): `collector_core.integrity` moved to the kernel
(`kernel.second_snapshot`).

Pure re-export, defines nothing: every name here *is* the object at its new home. Import from there
instead, e.g. `from kernel.second_snapshot import OHLC_BOOK_TOLERANCE`. Names whose contract changed
in the move are not served here (see the new module).
"""

import warnings

from kernel.second_snapshot import OHLC_BOOK_TOLERANCE as TOLERANCE
from kernel.second_snapshot import ohlc_outside_book


__all__ = [
    "TOLERANCE",
    "ohlc_outside_book",
]

REMOVE_AFTER = "25-3-bots-context-paper-and-exec-types-nautilus-acl"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.integrity moved to kernel.second_snapshot (Story 25.1); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
