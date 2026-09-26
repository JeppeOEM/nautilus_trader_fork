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
The `archive` service's config loader: `platform/archive/config.toml` into a `SchedulerConfig`.

Every key is required and an unknown one refuses start, like `collector_core.config`'s venue
loader, so a typo is never silently the default.
"""

import datetime as dt
import re
import tomllib
from pathlib import Path
from typing import Any

from archive.application.scheduler import SchedulerConfig
from archive.domain.reconciliation import VENUES
from archive.domain.schedule import Schedule


_KEYS = frozenset(
    {
        "nightly_at",
        "venues",
        "catch_up_max_days",
        "lock_wait_minutes",
        "intraday_consolidate_hours",
        "step_timeout_minutes",
    }
)
_HH_MM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")


def _int(raw: dict[str, Any], key: str, minimum: int) -> int:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{key} must be an integer >= {minimum}, got {value!r}")
    return value


def _nightly_at(value: object) -> dt.time:
    match = _HH_MM.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        raise ValueError(f'nightly_at must be "HH:MM" (UTC), got {value!r}')
    return dt.time(int(match.group(1)), int(match.group(2)))


def _venues(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise ValueError(f"venues must be a non-empty list of venue names, got {value!r}")
    unknown = sorted(set(value) - set(VENUES))
    if unknown or len(set(value)) != len(value):
        raise ValueError(f"venues must be distinct names from {list(VENUES)}, got {value!r}")
    return tuple(value)


def parse_scheduler_config(raw: dict[str, Any]) -> SchedulerConfig:
    """Validate a parsed `config.toml`; `ValueError` naming the first bad or unknown key."""
    unknown = sorted(set(raw) - _KEYS)
    missing = sorted(_KEYS - set(raw))
    if unknown or missing:
        raise ValueError(f"archive config: unknown keys {unknown}, missing keys {missing}")
    hours = _int(raw, "intraday_consolidate_hours", 1)
    if 24 % hours:
        raise ValueError(f"intraday_consolidate_hours must divide 24, got {hours}")
    return SchedulerConfig(
        schedule=Schedule(_nightly_at(raw["nightly_at"]), hours),
        venues=_venues(raw["venues"]),
        catch_up_max_days=_int(raw, "catch_up_max_days", 1),
        lock_wait_minutes=_int(raw, "lock_wait_minutes", 0),
        step_timeout_minutes=_int(raw, "step_timeout_minutes", 1),
    )


def load_scheduler_config(path: str | Path) -> SchedulerConfig:
    with Path(path).open("rb") as f:
        return parse_scheduler_config(tomllib.load(f))
