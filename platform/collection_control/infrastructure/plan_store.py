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
`PlanStore` over a venue's `config.toml`, through capture's one loader
(`capture.infrastructure.config`).
"""

from pathlib import Path
from typing import Any

import tomli_w
from capture.infrastructure.config import VENUE_SCHEMAS
from capture.infrastructure.config import load_toml
from capture.infrastructure.config import plan_toml_fields
from capture.infrastructure.config import venue_config_from_dict

from collection_control.domain.plan import CollectionPlan


def _with_plan(
    raw: dict[str, Any], plan: CollectionPlan, plan_keys: tuple[str, ...]
) -> dict[str, Any]:
    """
    Return the file's table with its plan keys replaced by the plan's: a key the plan still writes
    keeps its place, one it no longer writes is dropped, a new one is appended.
    """
    fields = plan_toml_fields(plan)
    kept = {k: fields.get(k, v) for k, v in raw.items() if k not in plan_keys or k in fields}
    return {**kept, **fields}


class TomlPlanStore:
    """
    The venue's plan in its `config.toml` (dYdX: `platform/data/dydx_config.toml`, bind-mounted over
    `/app/dydx_collector/config.toml`; Bybit and Hyperliquid: the committed
    `platform/capture/venues/<v>/config.toml`, bind-mounted read-write since Story 29.4).

    Invariant (see `PlanStore`): `load` returns only what the one loader accepts, and `save` writes
    only a plan that reads back equal through it: the file is re-read, its plan keys replaced,
    the result validated -- every other key, thresholds included, must still parse -- and only then
    written. A file that no longer parses refuses the save (`ValueError`), so a command can never
    be applied over a plan the collector could not restart from.

    Every plan key of the venue's schema is dropped before the plan's fields are merged, so a key
    the plan no longer writes (an emptied Bybit/Hyperliquid `exclude`) disappears from the file.

    Known limit: for Bybit and Hyperliquid the file is the committed `config.toml`, so a runtime
    command rewrites a tracked file: its comments are lost and the VPS checkout shows it modified
    (commit it back, or restore it, before the next `git pull`, which otherwise refuses or loses
    the operator's change). Upgrade path: move their plan to operator data beside dYdX's
    `platform/data/dydx_config.toml`.

    Known limit: every plan file is a single-file bind mount, which follows the host file's inode.
    A host-side replacement of the file -- `git checkout`/`git pull`, `sed -i`, an editor that
    saves by rename -- is never seen by the running container (the reload keeps reading the old
    inode), and every later save lands in that orphaned inode and is lost. Only an in-place write
    reaches the collector's hot reload; after a replacement, `docker compose restart` the service.
    Applies to dYdX's `platform/data/dydx_config.toml` too. Upgrade path: mount the file's
    directory instead of the file.

    Known limit: the save rewrites the file in place with `tomli_w`, so any comment in it is lost,
    and a reader landing mid-write can see a truncated file (`archive.prune_catalog.DydxPlanFile`
    refuses a file that changes while it reads it). In place because the file is a single-file bind
    mount, over which a rename fails (EBUSY). Upgrade path: mount `platform/data/` as a directory
    and write a temp file then `os.replace` it.
    """

    def __init__(self, path: Path, venue: str) -> None:
        self._path = Path(path)
        self._venue = venue

    def load(self) -> CollectionPlan:
        _, plan = venue_config_from_dict(load_toml(self._path), self._venue)
        return plan

    def save(self, plan: CollectionPlan) -> None:
        if plan.venue != self._venue:
            raise ValueError(f"a {plan.venue} plan cannot be saved to the {self._venue} file")
        raw = _with_plan(load_toml(self._path), plan, VENUE_SCHEMAS[self._venue].plan_keys)
        _, parsed = venue_config_from_dict(raw, self._venue)
        if parsed != plan:
            raise ValueError(f"{self._path}: the plan would not read back equal; not saved")
        # Serialized before the file is opened: a dump that fails must not leave it truncated.
        data = tomli_w.dumps(raw).encode()
        with self._path.open("wb") as f:
            f.write(data)
