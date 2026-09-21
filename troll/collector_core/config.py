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

import math
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import fields
from pathlib import Path
from typing import Any
from typing import Literal


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
    # Which clock decides a live row's book and trades (story 22.12). "arrival": the book as
    # received by mid-second and trades folded into their arrival second (dYdX: its deltas carry
    # no venue timestamp, D-49). "venue": exchange second S is closed at wall
    # S + 1 + hold_back_seconds from deltas/trades ordered and bucketed by their `ts_event`.
    book_time_source: Literal["arrival", "venue"] = "arrival"
    # Venue mode only: extra wait before closing a second so fewer in-flight messages miss it.
    # Set from `collector_core.measure_lag`, never to make reconciliation pass (the nightly
    # rebuild is the correctness device, D-50).
    hold_back_seconds: float = 0.0
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
        book_time_source=raw.get("book_time_source", "arrival"),
        hold_back_seconds=float(raw.get("hold_back_seconds", 0.0)),
        instruments=tuple(dict.fromkeys(raw.get("instruments", []))),  # deduped, order kept
    )
    for name in _POSITIVE_KEYS:
        if getattr(config, name) <= 0:
            raise ValueError(f"{name} must be > 0, got {getattr(config, name)}")
    if config.feed_stale_seconds is not None and config.feed_stale_seconds <= 0:
        raise ValueError(f"feed_stale_seconds must be > 0, got {config.feed_stale_seconds}")
    if config.book_crosscheck_seconds < 0:
        raise ValueError(
            f"book_crosscheck_seconds must be >= 0, got {config.book_crosscheck_seconds}"
        )
    _check_time_source(config)
    return config


def _check_time_source(config: CoreConfig) -> None:
    if config.book_time_source not in ("arrival", "venue"):
        raise ValueError(
            f"book_time_source must be 'arrival' or 'venue', got {config.book_time_source!r}"
        )
    if not math.isfinite(config.hold_back_seconds) or config.hold_back_seconds < 0:
        # TOML accepts nan/inf: inf would never close a second, nan would crash the collector.
        raise ValueError(
            f"hold_back_seconds must be finite and >= 0, got {config.hold_back_seconds}"
        )
    if config.hold_back_seconds > 0 and config.book_time_source != "venue":
        # An arrival-timed second has no venue boundary to wait for.
        raise ValueError("hold_back_seconds > 0 requires book_time_source = 'venue'")
    if config.book_time_source == "venue" and config.snapshot_interval_seconds != 1.0:
        # Venue rows are exchange seconds [S, S+1); the rebuild maps rows by floor second.
        raise ValueError("book_time_source = 'venue' requires snapshot_interval_seconds = 1.0")


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


def load_core_config(path: Path, environments: tuple[str, ...]) -> CoreConfig:
    return core_config_from_dict(load_toml(path), environments)
