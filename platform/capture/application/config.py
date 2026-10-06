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
Capture's thresholds (DDD spine AD-D17): `CoreConfig`, what every venue's capture service reads,
and `core_config_from_dict`, the one place a threshold is validated.

Invariant: a threshold is read only through `core_config_from_dict`, which refuses an unknown key
and an invalid value (`ValueError`, naming it) -- a `nan`/`inf` or bool for a seconds value, a
fraction or bool for a count -- so a typo can never fall back to a default or be truncated
silently. Thresholds default to the dYdX collector's production values (Story 22.1 AC #2). The
file format, the per-venue schema rows and the plan are the loader's
(`capture.infrastructure.config`); a venue's own keys are its `capture/venues/<v>/config.py`
subclass (Story 26.2 split them out of the core's config module).
"""

import math
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import fields
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
    # Set from `archive.tools.measure_lag`, never to make reconciliation pass (the nightly
    # rebuild is the correctness device, D-50).
    hold_back_seconds: float = 0.0
    # WebSocket connections carrying trades per feed group (story 22.14): 1 = the primary socket
    # only; 2 = plus an independent trades-only socket, unioned through the trade_id dedup (first
    # copy archived, the other counted `duplicate_feed`) so a one-sided outage loses nothing.
    # Only the Bybit and Hyperliquid clients open a second socket; dYdX runs one.
    trade_feeds: int = 1


_CORE_KEYS = frozenset(f.name for f in fields(CoreConfig))
_POSITIVE_KEYS = (
    "flush_interval_seconds",
    "snapshot_interval_seconds",
    "stale_book_seconds",
    "crossed_resync_seconds",
    "stale_trade_seconds",
    "seen_trade_ids",
)


def _seconds(raw: Mapping[str, Any], key: str, default: float) -> float:
    """
    Return `raw[key]` (or `default`) as a finite float. Strict: TOML accepts `nan`/`inf`, which
    every comparison after this would pass or crash on (`nan <= 0` is False), and a bool is an int
    to Python, so `true` would read as 1 s.
    """
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a finite number, got {value!r}")
    try:
        number = float(value)  # TOML integers are unbounded: 10**400 overflows a float
    except OverflowError:
        number = math.inf
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number, got {value!r}")
    return number


def _count(raw: Mapping[str, Any], key: str, default: int) -> int:
    """
    Return `raw[key]` (or `default`) as an int. Strict: `int()` would truncate a TOML `1.5` to 1
    and turn `true` into 1, both then passing the range checks as values nobody wrote.
    """
    value = raw.get(key, default)
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer, got {value!r}")
    return value


def _trade_feeds(value: Any) -> int:
    # Strict: `int()` would turn a TOML `2.5` into 2 and `true` into 1, both then passing.
    if type(value) is not int:
        raise ValueError(f"trade_feeds must be the integer 1 or 2, got {value!r}")
    return value


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
        flush_interval_seconds=_count(raw, "flush_interval_seconds", 60),
        snapshot_interval_seconds=_seconds(raw, "snapshot_interval_seconds", 1.0),
        stale_book_seconds=_seconds(raw, "stale_book_seconds", 5.0),
        crossed_resync_seconds=_seconds(raw, "crossed_resync_seconds", 10.0),
        stale_trade_seconds=_seconds(raw, "stale_trade_seconds", 10.0),
        seen_trade_ids=_count(raw, "seen_trade_ids", 2000),
        feed_stale_seconds=(
            _seconds(raw, "feed_stale_seconds", 0.0) if "feed_stale_seconds" in raw else None
        ),
        book_crosscheck_seconds=_seconds(raw, "book_crosscheck_seconds", 300.0),
        book_time_source=raw.get("book_time_source", "arrival"),
        hold_back_seconds=_seconds(raw, "hold_back_seconds", 0.0),
        trade_feeds=_trade_feeds(raw.get("trade_feeds", 1)),
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
    if config.trade_feeds not in (1, 2):
        raise ValueError(f"trade_feeds must be 1 or 2, got {config.trade_feeds}")
    return config


def _check_time_source(config: CoreConfig) -> None:
    if config.book_time_source not in ("arrival", "venue"):
        raise ValueError(
            f"book_time_source must be 'arrival' or 'venue', got {config.book_time_source!r}"
        )
    if config.hold_back_seconds < 0:
        # Finite already (`_seconds`): inf would never close a second, nan crash the collector.
        raise ValueError(f"hold_back_seconds must be >= 0, got {config.hold_back_seconds}")
    if config.hold_back_seconds > 0 and config.book_time_source != "venue":
        # An arrival-timed second has no venue boundary to wait for.
        raise ValueError("hold_back_seconds > 0 requires book_time_source = 'venue'")
    if config.book_time_source == "venue" and config.snapshot_interval_seconds != 1.0:
        # Venue rows are exchange seconds [S, S+1); the rebuild maps rows by floor second.
        raise ValueError("book_time_source = 'venue' requires snapshot_interval_seconds = 1.0")
