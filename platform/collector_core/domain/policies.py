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
Deprecated re-export shim (Story 26.2): `collector_core.domain.policies` moved to
`capture.domain.policies`.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from capture.domain.policies import BookTimeSource
from capture.domain.policies import CapturePolicies
from capture.domain.policies import CentralBookCrossPolicy
from capture.domain.policies import CrossedBookPolicy
from capture.domain.policies import LevelTagger
from capture.domain.policies import LevelTags
from capture.domain.policies import SequenceCanary
from capture.domain.policies import SequenceVerdict


__all__ = [
    "BookTimeSource",
    "CapturePolicies",
    "CentralBookCrossPolicy",
    "CrossedBookPolicy",
    "LevelTagger",
    "LevelTags",
    "SequenceCanary",
    "SequenceVerdict",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.domain.policies moved to "
    "capture.domain.policies (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
