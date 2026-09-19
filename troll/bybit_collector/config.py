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
"""Bybit collector config: a plain TOML file, read once at startup (no live reload/control)."""

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CollectorConfig:
    environment: str  # "mainnet" | "testnet"
    catalog_path: str
    flush_interval_seconds: int
    open_interest_poll_seconds: int
    snapshot_interval_seconds: float
    instruments: tuple[str, ...]  # e.g. "BTCUSDT-LINEAR.BYBIT"


def load_config(path: Path) -> CollectorConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    snapshot_interval_seconds = float(raw.get("snapshot_interval_seconds", 0.5))
    if snapshot_interval_seconds <= 0:
        raise ValueError(f"snapshot_interval_seconds must be > 0, got {snapshot_interval_seconds}")
    environment = raw.get("environment", "mainnet").lower()
    if environment not in ("mainnet", "testnet"):
        raise ValueError(f"environment must be mainnet or testnet, got {environment!r}")
    return CollectorConfig(
        environment=environment,
        catalog_path=raw.get("catalog_path", "catalog"),
        flush_interval_seconds=raw.get("flush_interval_seconds", 60),
        open_interest_poll_seconds=raw.get("open_interest_poll_seconds", 300),
        snapshot_interval_seconds=snapshot_interval_seconds,
        instruments=tuple(raw.get("instruments", [])),
    )
