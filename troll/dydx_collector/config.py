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
    store_order_book_deltas: bool = False
    # Retention for this instrument's raw order-book-delta data, in hours.
    # None means unlimited (never pruned) -- distinct from the global
    # non_config_retain_hours, which only applies to non-pinned instruments.
    retain_hours: float | None = None


@dataclass(frozen=True)
class CollectorConfig:
    network: DydxNetwork
    catalog_path: str
    flush_interval_seconds: int
    snapshot_interval_seconds: float
    config_reload_seconds: int
    open_interest_poll_seconds: int
    snapshot_interval_seconds: float
    non_config_retain_hours: float
    liquidity_min_oi_usd: float
    liquidity_check_seconds: int
    instruments: tuple[InstrumentEntry, ...]
    exclude: frozenset[str]


def load_config(path: Path) -> CollectorConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    instruments = tuple(
        InstrumentEntry(
            id=entry["id"],
            store_order_book_deltas=entry.get("store_order_book_deltas", False),
            retain_hours=entry.get("retain_hours"),
        )
        for entry in raw.get("instruments", [])
    )
    for entry in instruments:
        if entry.retain_hours is not None and entry.retain_hours < 0:
            raise ValueError(f"retain_hours must be >= 0 for instrument {entry.id!r}, got {entry.retain_hours}")

    snapshot_interval_seconds = raw.get("snapshot_interval_seconds", 0.5)
    if snapshot_interval_seconds <= 0:
        raise ValueError(f"snapshot_interval_seconds must be > 0, got {snapshot_interval_seconds}")

    return CollectorConfig(
        network=DydxNetwork.from_str(  # type: ignore[attr-defined]
            raw.get("network", "mainnet").lower(),
        ),
        catalog_path=raw.get("catalog_path", "catalog"),
        flush_interval_seconds=raw.get("flush_interval_seconds", 60),
        snapshot_interval_seconds=float(raw.get("snapshot_interval_seconds", 1.0)),
        config_reload_seconds=raw.get("config_reload_seconds", 30),
        open_interest_poll_seconds=raw.get("open_interest_poll_seconds", 300),
        snapshot_interval_seconds=snapshot_interval_seconds,
        non_config_retain_hours=raw.get("non_config_retain_hours", 4.0),
        liquidity_min_oi_usd=raw.get("liquidity_min_oi_usd", 100_000.0),
        liquidity_check_seconds=raw.get("liquidity_check_seconds", 1800),
        instruments=instruments,
        exclude=frozenset(raw.get("exclude", [])),
    )


