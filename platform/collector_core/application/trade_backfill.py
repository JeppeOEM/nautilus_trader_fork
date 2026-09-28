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
Deprecated re-export shim (Story 26.2): `collector_core.application.trade_backfill` moved to
`capture.application.trade_backfill`.

Pure re-export, defines nothing: every name here *is* its successor object.
"""

import warnings

from capture.application.trade_backfill import BACKFILL_LOOKBACK_NS
from capture.application.trade_backfill import BackfillReport
from capture.application.trade_backfill import admit_backfill
from capture.application.trade_backfill import restamped


__all__ = [
    "BACKFILL_LOOKBACK_NS",
    "BackfillReport",
    "admit_backfill",
    "restamped",
]

REMOVE_AFTER = "26-3-closeout-shims-gone-spines-reconciled"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.application.trade_backfill moved to "
    "capture.application.trade_backfill (Story 26.2); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
