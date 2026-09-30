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
The run-level catalog check every archive tool makes before it lists, locks or writes anything.

A catalog root that does not exist is a wrong mount or a typo, never an empty catalog: listed, it
would read as "no instruments" and the tool would report success over nothing (DATA-07). Every
tool refuses it the same way, at one ledger site: `archive.catalog_missing`, naming the tool.
"""

import logging
from pathlib import Path

from observability import error_ledger


logger = logging.getLogger(__name__)

# `candles.rebuild` restates it (the candles context never imports `archive`); pinned equal by
# `archive/tests/test_step_ledgers.py`.
CATALOG_MISSING_SITE = "archive.catalog_missing"


def catalog_missing(tool: str, catalog: str | Path) -> bool:
    """Return True (ledgered) when `catalog` is not an existing directory; the tool exits 1."""
    if Path(catalog).is_dir():
        return False
    error_ledger.record(
        CATALOG_MISSING_SITE,
        f"{tool}: catalog {catalog} does not exist or is not a directory (wrong mount?); "
        "nothing done",
    )
    return True
