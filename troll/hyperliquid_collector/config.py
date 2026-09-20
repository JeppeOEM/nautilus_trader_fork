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
"""Hyperliquid collector config: the venue-neutral `CoreConfig`, nothing extra (see config.toml)."""

from pathlib import Path

from collector_core.config import CoreConfig
from collector_core.config import core_config_from_dict
from collector_core.config import load_toml


def load_config(path: Path) -> CoreConfig:
    raw = load_toml(path)
    # l2Book pushes every ~5.4 s (max 6 s in the 22.5 raw capture, see config.toml): the core's
    # 5 s stale guard would skip most samples, so this venue defaults to 2x the cadence.
    raw.setdefault("stale_book_seconds", 12.0)
    return core_config_from_dict(raw, ("mainnet", "testnet"))
