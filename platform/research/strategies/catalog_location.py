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
The research runners' one default catalog root (DW-200).

`$CATALOG_PATH` when set and non-empty (as `research/notebooks/_params.py`, and the images'
`/app/catalog` mount, where the file-anchored path would be the non-existent
`/app/data/catalog`), else the collectors' shared catalog root `platform/data/catalog`, anchored
on this file rather than the cwd so `cd platform` and the repo root resolve the same directory.
Only that fallback is cwd-independent: a relative `$CATALOG_PATH` is returned as given and so
resolves against the caller's cwd.
"""

import os
from pathlib import Path


def default_catalog_path() -> str:
    """
    Return the catalog root a runner uses when its caller passes none.

    Resolved per call, never as a default-argument value, so a `CATALOG_PATH` set after import
    (a notebook cell, a test's monkeypatch) is honoured.
    """
    from_env = os.environ.get("CATALOG_PATH")
    if from_env:
        return from_env
    return str(Path(__file__).resolve().parents[2] / "data" / "catalog")
