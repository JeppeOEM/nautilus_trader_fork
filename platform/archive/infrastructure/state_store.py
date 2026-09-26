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
`JsonStateStore`: the `archive` service's scheduler cursor as `<ARCHIVE_STATE_DIR>/state.json`.

Written only through `CatalogFiles`' `write_json_atomic` (temp, fsync, rename, directory fsync),
so a crash mid-write leaves the previous state whole. The cursor is not a data verdict: nothing
outside the scheduler reads it (AD-D9 as amended by Story 25.1b).
"""

import json
from pathlib import Path

from archive.application.scheduler import SchedulerState
from archive.application.scheduler import state_from_json
from archive.application.scheduler import state_to_json
from archive.infrastructure.catalog_files import write_json_atomic


STATE_FILE_NAME = "state.json"


class JsonStateStore:
    """
    `StateStore` over one JSON file. Invariant: `save` is atomic and durable; `load` returns None
    only when the file does not exist and raises (`ValueError`/`OSError`) when it cannot be read
    as a scheduler state -- never a partial one.
    """

    def __init__(self, directory: str | Path) -> None:
        self._path = Path(directory) / STATE_FILE_NAME

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> SchedulerState | None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return state_from_json(json.loads(text))  # a JSONDecodeError is a ValueError

    def save(self, state: SchedulerState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(self._path, state_to_json(state))
