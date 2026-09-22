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
Deprecated re-export shim (Story 23.1): the error ledger moved to `observability.error_ledger`.

Pure re-export, defines nothing: every name here *is* the `observability.error_ledger` object, so
both paths share one counter. Import `from observability import error_ledger` instead.
"""

import warnings

from observability.error_ledger import counts
from observability.error_ledger import last_details
from observability.error_ledger import record
from observability.error_ledger import reset


__all__ = ["counts", "last_details", "record", "reset"]

REMOVE_AFTER = "24-1-candles-context-behind-the-secondsink-port"

# Attributed to the importing module, not to importlib's frames, so a `-W error::...:<pkg>` filter
# catches a stale caller.
warnings.warn(
    "ml_signals.error_ledger moved to observability.error_ledger (Story 23.1); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)
