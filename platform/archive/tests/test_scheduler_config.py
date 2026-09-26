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
The `archive` service's config loader: the committed `archive/config.toml` loads, and every
unknown, missing or out-of-range key refuses start.
"""

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from archive.infrastructure.scheduler_config import load_scheduler_config
from archive.infrastructure.scheduler_config import parse_scheduler_config


_COMMITTED = Path(__file__).resolve().parents[1] / "config.toml"


def _raw(**overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "nightly_at": "03:07",
        "venues": ["DYDX", "BYBIT", "HYPERLIQUID"],
        "catch_up_max_days": 7,
        "lock_wait_minutes": 60,
        "intraday_consolidate_hours": 4,
        "step_timeout_minutes": 360,
        "backup_enabled": False,
    }
    raw.update(overrides)
    return raw


def test_the_committed_config_loads_with_the_documented_values() -> None:
    config = load_scheduler_config(_COMMITTED)
    assert config.schedule.nightly_at == dt.time(3, 7)
    assert config.schedule.intraday_every_hours == 4
    assert config.venues == ("DYDX", "BYBIT", "HYPERLIQUID")
    assert (config.catch_up_max_days, config.lock_wait_minutes) == (7, 60)
    assert config.step_timeout_minutes == 360
    assert config.backup_enabled is False


def test_backup_enabled_true_is_kept() -> None:
    assert parse_scheduler_config(_raw(backup_enabled=True)).backup_enabled is True


def test_a_zero_lock_wait_is_allowed() -> None:
    assert parse_scheduler_config(_raw(lock_wait_minutes=0)).lock_wait_minutes == 0


@pytest.mark.parametrize(
    "raw",
    [
        _raw(backup_target="r2:bucket"),
        {k: v for k, v in _raw().items() if k != "venues"},
        _raw(nightly_at="3:07"),
        _raw(nightly_at="24:00"),
        _raw(nightly_at="03:60"),
        _raw(nightly_at=307),
        _raw(venues=[]),
        _raw(venues=["DYDX", "KRAKEN"]),
        _raw(venues=["DYDX", "DYDX"]),
        _raw(venues="DYDX"),
        _raw(catch_up_max_days=0),
        _raw(catch_up_max_days=True),
        _raw(catch_up_max_days=7.0),
        _raw(lock_wait_minutes=-1),
        _raw(intraday_consolidate_hours=0),
        _raw(intraday_consolidate_hours=5),
        _raw(step_timeout_minutes=0),
        _raw(backup_enabled="no"),
        _raw(backup_enabled=1),
        _raw(backup_enabled=0),
        {k: v for k, v in _raw().items() if k != "backup_enabled"},
    ],
)
def test_a_bad_or_unknown_key_refuses_start(raw: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        parse_scheduler_config(raw)
