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
The one venue `config.toml` loader (DDD spine AD-D17): a plain TOML file, read once at startup,
returned as the capture thresholds (`CoreConfig`, or a venue subclass) plus the venue's
`CollectionPlan`. Only the plan hot-reloads (dYdX's `collection_control`, through this loader
again); the thresholds are read once per process start.

Thresholds default to the dYdX collector's production values (Story 22.1 AC #2). Each venue's
extra keys, defaults, environment names and plan shape are one `VENUE_SCHEMAS` row; every key the
row does not name is refused, so a typo can never fall back to a default silently.

This is the one capture module that imports `collection_control` (its `domain`, for the plan
aggregate the loader returns): `platform/tests/test_boundaries.py` whitelists it as a composition
root. Everything else in capture sees the plan as ids and the `ports.PlanDiff` protocol.
"""

import math
import tomllib
from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import fields
from pathlib import Path
from types import MappingProxyType
from typing import Any
from typing import Literal

from collection_control.domain.plan import CollectionPlan
from collection_control.domain.plan import InstrumentEntry

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


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


@dataclass(frozen=True)
class BybitConfig(CoreConfig):
    """The core thresholds plus the REST open-interest poll cadence (the linear WS drops OI)."""

    open_interest_poll_seconds: int = 300


@dataclass(frozen=True, kw_only=True)
class DydxConfig(CoreConfig):
    """
    The core thresholds plus dYdX's own cadences. Keyword-only, so `network` can be required;
    `environment` is always derived from `network`, so `CoreConfig` stays satisfied. The plan keys
    (`instruments`, `exclude`, `liquidity_min_oi_usd`, `non_config_retain_hours`) are the
    `CollectionPlan`'s, not the config's.
    """

    environment: str = ""
    network: DydxNetwork
    open_interest_poll_seconds: int = 300
    config_reload_seconds: int = 30
    liquidity_check_seconds: int = 1800

    def __post_init__(self) -> None:
        object.__setattr__(self, "environment", str(self.network))


_CORE_KEYS = frozenset(f.name for f in fields(CoreConfig))
_POSITIVE_KEYS = (
    "flush_interval_seconds",
    "snapshot_interval_seconds",
    "stale_book_seconds",
    "crossed_resync_seconds",
    "stale_trade_seconds",
    "seen_trade_ids",
)


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


# -- the venue schemas and the one loader (AD-D17) -------------------------------------------------

# dYdX's WS server hard-caps subscriptions per channel per connection at 32 (confirmed live via its
# own error: "Per-connection subscription limit reached for v4_trades (limit=32)"). Every collected
# instrument subscribes both v4_trades and v4_orderbook, so the cap bounds the whole collected set --
# going over it does not just drop the overflow, the repeated rejections get the whole connection
# detected as dead and endlessly reconnected (permanent "Stale book" on every instrument). 30 is
# this collector's own operating cap (Story 6.1): a deliberate 2-slot margin under the venue's 32.
DYDX_MAX_WS_SUBSCRIPTIONS = 32
DYDX_MAX_COLLECTED_INSTRUMENTS = 30

_DYDX_ENTRY_KEYS = frozenset({"id", "store_order_book_deltas", "retain_hours"})
_DYDX_POSITIVE_INTS = (
    "open_interest_poll_seconds",
    "config_reload_seconds",
    "liquidity_check_seconds",
)


@dataclass(frozen=True)
class VenueSchema:
    """
    One venue's `config.toml` shape: its environment names, the keys it adds to the core's, the
    defaults it overrides, its plan keys and how the plan is built (`build`).

    Invariant: every key a venue's file may hold is named here or in `CoreConfig` -- the loader
    refuses anything else -- so a new key cannot be read by one owner and ignored by another.
    """

    environments: tuple[str, ...]
    extra_keys: tuple[str, ...]
    defaults: Mapping[str, Any]
    plan_keys: tuple[str, ...]
    build: Callable[[dict[str, Any], dict[str, Any]], tuple[CoreConfig, CollectionPlan]]


def _positive_int(raw: dict[str, Any], key: str, default: int) -> int:
    # Strict, like `trade_feeds`: `int()` would turn a TOML `1799.9` into 1799 and `true` into 1.
    value = raw.get(key, default)
    if type(value) is not int or value <= 0:
        raise ValueError(f"{key} must be an integer > 0, got {value!r}")
    return value


def _number(value: object, key: str) -> float:
    """Return a TOML number as float; a bool is an int to Python and must not pass as one."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be a number, got {value!r}")
    return float(value)


def _non_negative(value: object, key: str) -> float:
    """Return a finite number >= 0: TOML accepts `nan`/`inf`, which no threshold may be."""
    value = _number(value, key)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{key} must be finite and >= 0, got {value!r}")
    return float(value)


def _string_list(raw: dict[str, Any], key: str) -> list[str]:
    values = raw.get(key, [])
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise ValueError(f"{key} must be a list of instrument id strings, got {values!r}")
    return values


def _dydx_entry(raw: object) -> InstrumentEntry:
    if not isinstance(raw, dict):
        raise ValueError(f"a dYdX [[instruments]] entry must be a table, got {raw!r}")
    unknown = set(raw) - _DYDX_ENTRY_KEYS
    if unknown:
        raise ValueError(
            f"unknown [[instruments]] keys {sorted(unknown)}; allowed: {sorted(_DYDX_ENTRY_KEYS)}"
        )
    if not isinstance(raw.get("id"), str):
        raise ValueError(f"a dYdX [[instruments]] entry needs a string `id`, got {raw!r}")
    store = raw.get("store_order_book_deltas", False)
    if not isinstance(store, bool):
        raise ValueError(f"store_order_book_deltas must be true or false, got {store!r}")
    retain = raw.get("retain_hours")
    return InstrumentEntry(
        id=raw["id"],
        store_order_book_deltas=store,
        retain_hours=None if retain is None else _number(retain, "retain_hours"),
    )


def _dydx(raw: dict[str, Any], plan_raw: dict[str, Any]) -> tuple[CoreConfig, CollectionPlan]:
    if "environment" in raw:
        raise ValueError("a dYdX config names its network with `network`, not `environment`")
    network = str(raw.pop("network", "mainnet")).lower()
    networks = VENUE_SCHEMAS["DYDX"].environments
    if network not in networks:
        raise ValueError(f"network must be one of {networks}, got {network!r}")
    extra = {key: raw.pop(key) for key in _DYDX_POSITIVE_INTS if key in raw}
    core = core_config_from_dict({**raw, "environment": network}, networks)
    if core.trade_feeds != 1:
        # `DydxClient` opens one socket: a second trade feed would be accepted and never opened.
        raise ValueError(f"dYdX runs one trade feed: trade_feeds must be 1, got {core.trade_feeds}")
    config = DydxConfig(
        **{k: v for k, v in asdict(core).items() if k != "environment"},
        network=DydxNetwork.from_str(network),  # type: ignore[attr-defined]
        open_interest_poll_seconds=_positive_int(extra, "open_interest_poll_seconds", 300),
        config_reload_seconds=_positive_int(extra, "config_reload_seconds", 30),
        liquidity_check_seconds=_positive_int(extra, "liquidity_check_seconds", 1800),
    )
    entries = plan_raw.get("instruments", [])
    if not isinstance(entries, list):
        raise ValueError(f"dYdX `instruments` must be an array of tables, got {entries!r}")
    plan = CollectionPlan(
        venue="DYDX",
        instruments=tuple(_dydx_entry(entry) for entry in entries),
        cap=DYDX_MAX_COLLECTED_INSTRUMENTS,
        excluded=frozenset(_string_list(plan_raw, "exclude")),
        min_liquidity_usd=_non_negative(plan_raw["liquidity_min_oi_usd"], "liquidity_min_oi_usd"),
        non_config_retain_hours=_non_negative(
            plan_raw["non_config_retain_hours"], "non_config_retain_hours"
        ),
    )
    return config, plan


def _static_plan(venue: str, plan_raw: dict[str, Any]) -> CollectionPlan:
    """Build the plan of a flat id list, deduped in order; its cap is its own size."""
    ids = tuple(dict.fromkeys(_string_list(plan_raw, "instruments")))
    return CollectionPlan(
        venue=venue, instruments=tuple(InstrumentEntry(id=iid) for iid in ids), cap=len(ids)
    )


def _bybit(raw: dict[str, Any], plan_raw: dict[str, Any]) -> tuple[CoreConfig, CollectionPlan]:
    poll = _positive_int(raw, "open_interest_poll_seconds", BybitConfig.open_interest_poll_seconds)
    schema = VENUE_SCHEMAS["BYBIT"]
    core = core_config_from_dict(raw, schema.environments, extra_keys=schema.extra_keys)
    return BybitConfig(**asdict(core), open_interest_poll_seconds=poll), _static_plan(
        "BYBIT", plan_raw
    )


def _hyperliquid(
    raw: dict[str, Any], plan_raw: dict[str, Any]
) -> tuple[CoreConfig, CollectionPlan]:
    core = core_config_from_dict(raw, VENUE_SCHEMAS["HYPERLIQUID"].environments)
    return core, _static_plan("HYPERLIQUID", plan_raw)


VENUE_SCHEMAS: Mapping[str, VenueSchema] = MappingProxyType(
    {
        "DYDX": VenueSchema(
            environments=("mainnet", "testnet"),  # the `network` key's values
            extra_keys=("network", *_DYDX_POSITIVE_INTS),
            defaults=MappingProxyType(
                {"liquidity_min_oi_usd": 20_000.0, "non_config_retain_hours": 4.0}
            ),
            plan_keys=("instruments", "exclude", "liquidity_min_oi_usd", "non_config_retain_hours"),
            build=_dydx,
        ),
        "BYBIT": VenueSchema(
            environments=("mainnet", "testnet"),
            extra_keys=("open_interest_poll_seconds",),
            defaults=MappingProxyType({}),
            plan_keys=("instruments",),
            build=_bybit,
        ),
        "HYPERLIQUID": VenueSchema(
            environments=("mainnet", "testnet"),
            extra_keys=(),
            # l2Book pushes every ~5.4 s (max 6 s in the 22.5 raw capture, see its config.toml):
            # the core's 5 s stale guard would skip most samples, so 2x the cadence.
            defaults=MappingProxyType({"stale_book_seconds": 12.0}),
            plan_keys=("instruments",),
            build=_hyperliquid,
        ),
    }
)


def venue_config_from_dict(raw: Mapping[str, Any], venue: str) -> tuple[CoreConfig, CollectionPlan]:
    """
    Parse one venue's `config.toml` table into its thresholds and its plan. Refuses an unknown
    key, an invalid threshold and a plan that breaks an invariant (`ValueError`, naming it).
    """
    schema = VENUE_SCHEMAS[venue]
    merged = {**schema.defaults, **raw}
    plan_raw = {key: merged.pop(key) for key in schema.plan_keys if key in merged}
    return schema.build(merged, plan_raw)


def load_venue_config(path: Path, venue: str) -> tuple[CoreConfig, CollectionPlan]:
    return venue_config_from_dict(load_toml(path), venue)


def _entry_toml(entry: InstrumentEntry) -> dict[str, Any]:
    # Default fields are omitted, so a saved file lists only what an operator set.
    raw: dict[str, Any] = {"id": entry.id}
    if entry.store_order_book_deltas:
        raw["store_order_book_deltas"] = True
    if entry.retain_hours is not None:
        raw["retain_hours"] = entry.retain_hours
    return raw


def plan_toml_fields(plan: CollectionPlan) -> dict[str, Any]:
    """
    Return the writer half of the schema: the plan's keys as `venue_config_from_dict` reads them,
    for `collection_control.infrastructure.plan_store.TomlPlanStore.save` to merge into the file.
    """
    if plan.venue != "DYDX":
        return {"instruments": list(plan.collected)}
    fields_: dict[str, Any] = {
        "instruments": [_entry_toml(e) for e in plan.instruments],
        "exclude": sorted(plan.excluded),
    }
    if plan.min_liquidity_usd is not None:
        fields_["liquidity_min_oi_usd"] = plan.min_liquidity_usd
    if plan.non_config_retain_hours is not None:
        fields_["non_config_retain_hours"] = plan.non_config_retain_hours
    return fields_
