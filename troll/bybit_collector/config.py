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
"""Bybit collector config: the venue-neutral `CoreConfig` plus the REST open-interest poll cadence."""

from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path

from collector_core.config import CoreConfig
from collector_core.config import core_config_from_dict
from collector_core.config import load_toml


@dataclass(frozen=True)
class BybitConfig(CoreConfig):
    open_interest_poll_seconds: int = 300


def load_config(path: Path) -> BybitConfig:
    raw = load_toml(path)
    core = core_config_from_dict(
        raw, ("mainnet", "testnet"), extra_keys=("open_interest_poll_seconds",)
    )
    poll = int(raw.get("open_interest_poll_seconds", BybitConfig.open_interest_poll_seconds))
    if poll <= 0:
        raise ValueError(f"open_interest_poll_seconds must be > 0, got {poll}")
    return BybitConfig(**asdict(core), open_interest_poll_seconds=poll)
