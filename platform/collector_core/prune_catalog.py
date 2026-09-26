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
Deprecated re-export shim (Story 25.1): `collector_core.prune_catalog` moved to the archive context
(`archive.prune_catalog`).

Pure re-export, defines nothing: every name here *is* the object at its new home. Import from there
instead, e.g. `from archive.domain.retention import TRADE_TICK`. Names whose contract changed in the
move are not served here (see the new module).

`python -m collector_core.prune_catalog` still runs it; use `python -m archive.prune_catalog`.
"""

import warnings

from archive.domain.retention import TRADE_TICK
from archive.prune_catalog import main


__all__ = [
    "TRADE_TICK",
    "main",
]

REMOVE_AFTER = "25-3-bots-context-paper-and-exec-types-nautilus-acl"


# Attributed to the importing module, not to importlib's frames.
warnings.warn(
    "collector_core.prune_catalog moved to archive.prune_catalog (Story 25.1); "
    f"this shim is removed after {REMOVE_AFTER}",
    DeprecationWarning,
    skip_file_prefixes=("<frozen importlib",),
)


if __name__ == "__main__":
    # Under `python -m` the import-time warning above is attributed to runpy's frame and hidden
    # by the default filters; this one is issued from `__main__`, where it is shown.
    warnings.warn(
        f"python -m {__spec__.name} is deprecated: use python -m archive.prune_catalog (Story 25.1)",
        DeprecationWarning,
        stacklevel=1,
    )
    raise SystemExit(main())
