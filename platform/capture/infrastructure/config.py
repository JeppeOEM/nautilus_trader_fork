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

Each venue's extra keys, defaults, environment names and plan shape are one `VENUE_SCHEMAS` row;
every key the row does not name is refused, so a typo can never fall back to a default silently.
The thresholds themselves are validated by `capture.application.config.core_config_from_dict`,
and each venue's own config class, caps and file path are its `capture/venues/<v>/config.py`.

This is the one capture module that imports `collection_control` (its `domain`, for the plan
aggregate the loader returns): `platform/tests/test_boundaries.py` whitelists it as a composition
root. Everything else in capture sees the plan as ids and the `ports.PlanDiff` protocol.
"""

import math
import tomllib
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from collection_control.domain.plan import CollectionPlan
from collection_control.domain.plan import InstrumentEntry

from capture.application.config import CoreConfig
from capture.application.config import core_config_from_dict
from capture.venues.bybit.config import BybitConfig
from capture.venues.dydx.config import DYDX_MAX_COLLECTED_INSTRUMENTS
from capture.venues.dydx.config import DydxConfig
from capture.venues.hyperliquid.config import STALE_BOOK_SECONDS
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


def load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as f:
        return tomllib.load(f)


# -- the venue schemas and the one loader (AD-D17) -------------------------------------------------

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
            # The venue's measured push cadence (`capture.venues.hyperliquid.config`).
            defaults=MappingProxyType({"stale_book_seconds": STALE_BOOK_SECONDS}),
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
