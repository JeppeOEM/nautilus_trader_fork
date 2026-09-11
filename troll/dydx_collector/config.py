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
"""Collector configuration: TOML loading/saving + hot-reload diffing."""

import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


@dataclass(frozen=True)
class InstrumentEntry:
    # Every id in `instruments` is collected -- there is no "listed but not collected"
    # state. Removed from `config.toml`'s [[instruments]] entirely (via "stop"/"unpin")
    # to stop collecting it.
    id: str
    store_order_book_deltas: bool = False
    # Retention for this instrument's raw order-book-delta data, in hours.
    # None means unlimited (never pruned) -- distinct from the global
    # non_config_retain_hours, which only applies to instruments no longer collected.
    retain_hours: float | None = None


@dataclass(frozen=True)
class CollectorConfig:
    network: DydxNetwork
    catalog_path: str
    flush_interval_seconds: int
    config_reload_seconds: int
    open_interest_poll_seconds: int
    snapshot_interval_seconds: float
    non_config_retain_hours: float
    liquidity_min_oi_usd: float
    liquidity_check_seconds: int
    instruments: tuple[InstrumentEntry, ...]
    # Permanent denylist: classify_liquidity always treats these as illiquid regardless
    # of volume, so nothing (including pin_top_liquid) ever picks them. Also doubles as
    # where a collector:control "unpin" action lands an id -- unpinning a coin is
    # exactly "add it to exclude", so it also stays out of any future liquidity ranking,
    # not just out of the collected set. bot_tui shows this whole list, whatever its
    # origin (hand-edited or via unpin), as its "unpinned" section.
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

    snapshot_interval_seconds = float(raw.get("snapshot_interval_seconds", 0.5))
    if snapshot_interval_seconds <= 0:
        raise ValueError(f"snapshot_interval_seconds must be > 0, got {snapshot_interval_seconds}")

    return CollectorConfig(
        network=DydxNetwork.from_str(  # type: ignore[attr-defined]
            raw.get("network", "mainnet").lower(),
        ),
        catalog_path=raw.get("catalog_path", "catalog"),
        flush_interval_seconds=raw.get("flush_interval_seconds", 60),
        config_reload_seconds=raw.get("config_reload_seconds", 30),
        open_interest_poll_seconds=raw.get("open_interest_poll_seconds", 300),
        snapshot_interval_seconds=snapshot_interval_seconds,
        non_config_retain_hours=raw.get("non_config_retain_hours", 4.0),
        liquidity_min_oi_usd=raw.get("liquidity_min_oi_usd", 20_000.0),
        liquidity_check_seconds=raw.get("liquidity_check_seconds", 1800),
        instruments=instruments,
        exclude=frozenset(raw.get("exclude", [])),
    )


def _instrument_to_raw(entry: InstrumentEntry) -> dict:
    raw: dict = {"id": entry.id}
    if entry.store_order_book_deltas:
        raw["store_order_book_deltas"] = entry.store_order_book_deltas
    if entry.retain_hours is not None:
        raw["retain_hours"] = entry.retain_hours
    return raw


def save_config(config: CollectorConfig, path: Path) -> None:
    """
    Persist `config` back to `path` as TOML.

    Full rewrite, not a patch -- `tomli_w` has no comment-preservation support, so any
    hand-written comments in the file are lost on a control-action-triggered save. This
    is an accepted, deliberate tradeoff (see Story 6.1 Dev Notes); revisit only if it
    becomes a real complaint.
    """
    raw = {
        "network": str(config.network),
        "catalog_path": config.catalog_path,
        "flush_interval_seconds": config.flush_interval_seconds,
        "config_reload_seconds": config.config_reload_seconds,
        "open_interest_poll_seconds": config.open_interest_poll_seconds,
        "snapshot_interval_seconds": config.snapshot_interval_seconds,
        "non_config_retain_hours": config.non_config_retain_hours,
        "liquidity_min_oi_usd": config.liquidity_min_oi_usd,
        "liquidity_check_seconds": config.liquidity_check_seconds,
        "instruments": [_instrument_to_raw(e) for e in config.instruments],
        "exclude": sorted(config.exclude),
    }
    with path.open("wb") as f:
        tomli_w.dump(raw, f)


