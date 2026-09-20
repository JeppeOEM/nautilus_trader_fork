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
Venue-neutral collector config: a plain TOML file, read once at startup (no live reload).

Thresholds default to the dYdX collector's production values (Story 22.1 AC #2); a venue
package adds its own keys by subclassing `CoreConfig` (see `bybit_collector.config`).
"""

import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import fields
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CoreConfig:
    environment: str  # e.g. "mainnet" | "testnet"
    catalog_path: str
    flush_interval_seconds: int = 60
    snapshot_interval_seconds: float = 1.0  # must be > 0
    stale_book_seconds: float = 5.0  # skip a sample when the book had no deltas for this long
    crossed_resync_seconds: float = 10.0  # crossed longer than this -> resync (DATA-03 fallback)
    stale_trade_seconds: float = 10.0  # older trades are subscribe-time history (DATA-06)
    seen_trade_ids: int = 2000  # bounded per-instrument trade_id dedup window (DATA-06)
    # Feed-level liveness: no WS message at all for this long = dead feed / stale after a
    # reconnect. None = same as `stale_book_seconds`. A per-instrument silence is judged
    # separately by `stale_book_seconds` (story 22.5).
    feed_stale_seconds: float | None = None
    # REST book cross-check cadence per instrument (story 22.5); 0 disables. Only runs for a
    # client exposing `fetch_book_levels`.
    book_crosscheck_seconds: float = 300.0
    instruments: tuple[str, ...] = ()


_CORE_KEYS = frozenset(f.name for f in fields(CoreConfig))
_POSITIVE_KEYS = (
    "flush_interval_seconds",
    "snapshot_interval_seconds",
    "stale_book_seconds",
    "crossed_resync_seconds",
    "stale_trade_seconds",
    "seen_trade_ids",
)


def core_config_from_dict(
    raw: dict[str, Any], environments: tuple[str, ...], extra_keys: Iterable[str] = ()
) -> CoreConfig:
    """`extra_keys`: venue-specific keys the caller reads itself (any other unknown key is a typo)."""
    allowed = _CORE_KEYS | set(extra_keys)
    unknown = set(raw) - allowed
    if unknown:
        # A misspelt threshold would otherwise silently fall back to a default.
        raise ValueError(f"unknown config keys {sorted(unknown)}; allowed: {sorted(allowed)}")
    environment = str(raw.get("environment", "mainnet")).lower()
    if environment not in environments:
        raise ValueError(f"environment must be one of {environments}, got {environment!r}")
    config = CoreConfig(
        environment=environment,
        catalog_path=raw.get("catalog_path", "catalog"),
        flush_interval_seconds=int(raw.get("flush_interval_seconds", 60)),
        snapshot_interval_seconds=float(raw.get("snapshot_interval_seconds", 1.0)),
        stale_book_seconds=float(raw.get("stale_book_seconds", 5.0)),
        crossed_resync_seconds=float(raw.get("crossed_resync_seconds", 10.0)),
        stale_trade_seconds=float(raw.get("stale_trade_seconds", 10.0)),
        seen_trade_ids=int(raw.get("seen_trade_ids", 2000)),
        feed_stale_seconds=(
            float(raw["feed_stale_seconds"]) if "feed_stale_seconds" in raw else None
        ),
        book_crosscheck_seconds=float(raw.get("book_crosscheck_seconds", 300.0)),
        instruments=tuple(dict.fromkeys(raw.get("instruments", []))),  # deduped, order kept
    )
    for name in _POSITIVE_KEYS:
        if getattr(config, name) <= 0:
            raise ValueError(f"{name} must be > 0, got {getattr(config, name)}")
    if config.feed_stale_seconds is not None and config.feed_stale_seconds <= 0:
        raise ValueError(f"feed_stale_seconds must be > 0, got {config.feed_stale_seconds}")
    if config.book_crosscheck_seconds < 0:
        raise ValueError(f"book_crosscheck_seconds must be >= 0, got {config.book_crosscheck_seconds}")
    return config


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_core_config(path: Path, environments: tuple[str, ...]) -> CoreConfig:
    return core_config_from_dict(load_toml(path), environments)
