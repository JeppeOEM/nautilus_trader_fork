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
"""Collector configuration: TOML loading + hot-reload diffing."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


@dataclass(frozen=True)
class InstrumentEntry:
    id: str
    bar_intervals: tuple[str, ...]


@dataclass(frozen=True)
class CollectorConfig:
    network: DydxNetwork
    catalog_path: str
    flush_interval_seconds: int
    config_reload_seconds: int
    open_interest_poll_seconds: int
    instruments: tuple[InstrumentEntry, ...]


def load_config(path: Path) -> CollectorConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    instruments = tuple(
        InstrumentEntry(id=entry["id"], bar_intervals=tuple(entry.get("bar_intervals", [])))
        for entry in raw.get("instruments", [])
    )

    return CollectorConfig(
        network=DydxNetwork.from_str(  # type: ignore[attr-defined]
            raw.get("network", "mainnet").lower(),
        ),
        catalog_path=raw.get("catalog_path", "catalog"),
        flush_interval_seconds=raw.get("flush_interval_seconds", 60),
        config_reload_seconds=raw.get("config_reload_seconds", 30),
        open_interest_poll_seconds=raw.get("open_interest_poll_seconds", 300),
        instruments=instruments,
    )


def diff_instruments(
    old: tuple[InstrumentEntry, ...],
    new: tuple[InstrumentEntry, ...],
) -> tuple[list[InstrumentEntry], list[InstrumentEntry]]:
    """Return (added, removed) entries by `id`, comparing old config state to new."""
    old_by_id = {entry.id: entry for entry in old}
    new_by_id = {entry.id: entry for entry in new}

    added = [entry for entry_id, entry in new_by_id.items() if entry_id not in old_by_id]
    removed = [entry for entry_id, entry in old_by_id.items() if entry_id not in new_by_id]

    return added, removed
