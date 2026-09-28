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
Deprecated re-export shim (Story 26.2): `collector_core.domain.trade_history` moved to
`capture.domain.trade_history`.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from capture.domain.trade_history import BackfillCapability
from capture.domain.trade_history import BackfillError
from capture.domain.trade_history import Fetched
from capture.domain.trade_history import RowReader
from capture.domain.trade_history import collect
from capture.domain.trade_history import exact_text
from capture.domain.trade_history import iso_to_ns
from capture.domain.trade_history import ms_to_ns
from capture.domain.trade_history import side_of
from capture.domain.trade_history import tick


__all__ = [
    "BackfillCapability",
    "BackfillError",
    "Fetched",
    "RowReader",
    "collect",
    "exact_text",
    "iso_to_ns",
    "ms_to_ns",
    "side_of",
    "tick",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.domain.trade_history moved to "
    "capture.domain.trade_history (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
