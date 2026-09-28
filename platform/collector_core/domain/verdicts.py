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
Deprecated re-export shim (Story 26.2): `collector_core.domain.verdicts` moved to
`capture.domain.verdicts`.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from capture.domain.verdicts import EMPTY_TOP
from capture.domain.verdicts import NO_BOOK
from capture.domain.verdicts import Accepted
from capture.domain.verdicts import Crossed
from capture.domain.verdicts import CrossVerdict
from capture.domain.verdicts import DroppedLevel
from capture.domain.verdicts import EmptyTop
from capture.domain.verdicts import NoBook
from capture.domain.verdicts import Rejected
from capture.domain.verdicts import ResyncRequested
from capture.domain.verdicts import SampleVerdict
from capture.domain.verdicts import Stale
from capture.domain.verdicts import StaleKind
from capture.domain.verdicts import StillCrossed
from capture.domain.verdicts import Uncrossed


__all__ = [
    "EMPTY_TOP",
    "NO_BOOK",
    "Accepted",
    "CrossVerdict",
    "Crossed",
    "DroppedLevel",
    "EmptyTop",
    "NoBook",
    "Rejected",
    "ResyncRequested",
    "SampleVerdict",
    "Stale",
    "StaleKind",
    "StillCrossed",
    "Uncrossed",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.domain.verdicts moved to "
    "capture.domain.verdicts (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
