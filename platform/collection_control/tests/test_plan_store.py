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
The `PlanStore` contract (`collection_control.application.ports`), run against every adapter:
`load` returns only a plan the one loader accepts, `save` writes only a plan that reads back equal.
"""

import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from collection_control.application.ports import PlanStore
from collection_control.domain.plan import InstrumentEntry
from collection_control.infrastructure.plan_store import TomlPlanStore


_FILE = """
network = "testnet"
catalog_path = "my_catalog"
flush_interval_seconds = 30
config_reload_seconds = 15
open_interest_poll_seconds = 120
stale_book_seconds = 7.5
non_config_retain_hours = 8.0
liquidity_min_oi_usd = 250000.0
liquidity_check_seconds = 900
exclude = ["BAD-USD-PERP.DYDX"]

[[instruments]]
id = "BTC-USD-PERP.DYDX"
store_order_book_deltas = true
retain_hours = 24.0

[[instruments]]
id = "ETH-USD-PERP.DYDX"
"""

# One factory per adapter: (a path holding `text`) -> the store under test.
_ADAPTERS: dict[str, Callable[[Path], PlanStore]] = {
    "toml": lambda path: TomlPlanStore(path, "DYDX"),
}


def _store(adapter: str, tmp_path: Path, text: str = _FILE) -> tuple[PlanStore, Path]:
    path = tmp_path / "config.toml"
    path.write_text(text)
    return _ADAPTERS[adapter](path), path


@pytest.mark.parametrize("adapter", sorted(_ADAPTERS))
def test_a_saved_plan_loads_back_equal(adapter: str, tmp_path: Path) -> None:
    store, _path = _store(adapter, tmp_path)
    plan = store.load()
    changed = plan.add("SOL-USD-PERP.DYDX").plan.unpin("ETH-USD-PERP.DYDX").plan
    store.save(changed)
    assert store.load() == changed


@pytest.mark.parametrize("adapter", sorted(_ADAPTERS))
def test_save_keeps_every_non_plan_key(adapter: str, tmp_path: Path) -> None:
    store, path = _store(adapter, tmp_path)
    store.save(store.load().remove("ETH-USD-PERP.DYDX").plan)
    raw = tomllib.loads(path.read_text())
    assert (raw["network"], raw["stale_book_seconds"], raw["liquidity_check_seconds"]) == (
        "testnet",
        7.5,
        900,
    )
    assert raw["instruments"] == [
        {"id": "BTC-USD-PERP.DYDX", "store_order_book_deltas": True, "retain_hours": 24.0}
    ]


@pytest.mark.parametrize("adapter", sorted(_ADAPTERS))
def test_save_over_a_file_that_no_longer_parses_is_refused_and_writes_nothing(
    adapter: str, tmp_path: Path
) -> None:
    """A hand edit left a typo: the command must not be applied over an unstartable file."""
    store, path = _store(adapter, tmp_path)
    plan = store.load()
    broken = _FILE.replace("stale_book_seconds", "stale_book_secs")
    path.write_text(broken)
    with pytest.raises(ValueError, match="stale_book_secs"):
        store.save(plan.remove("ETH-USD-PERP.DYDX").plan)
    assert path.read_text() == broken


@pytest.mark.parametrize("adapter", sorted(_ADAPTERS))
@pytest.mark.parametrize(
    ("text", "match"),
    [
        (
            "".join(f'[[instruments]]\nid = "C{i}-PERP.DYDX"\n' for i in range(31)),
            "above its cap of 30",
        ),
        ('exclude = ["A-PERP.DYDX"]\n[[instruments]]\nid = "A-PERP.DYDX"\n', "A-PERP.DYDX"),
        ('[[instruments]]\nid = "A-PERP.DYDX"\n[[instruments]]\nid = "A-PERP.DYDX"\n', "once"),
        ('[[instruments]]\nid = "A"\nretain_hours = -1.0\n', "retain_hours must be >= 0"),
        ('[[instruments]]\nid = "A"\nretain_hours = nan\n', "retain_hours must be >= 0"),
        ('[[instruments]]\nid = "A"\nretain_hours = true\n', "retain_hours must be a number"),
        ("liquidity_min_oi_usd = nan\n", "liquidity_min_oi_usd must be finite"),
        ("liquidity_min_oi_usd = -1.0\n", "liquidity_min_oi_usd must be finite and >= 0"),
        ("non_config_retain_hours = inf\n", "non_config_retain_hours must be finite"),
        ("config_reload_seconds = 29.5\n", "config_reload_seconds must be an integer"),
        ("liquidity_check_seconds = true\n", "liquidity_check_seconds must be an integer"),
    ],
)
def test_load_refuses_a_plan_that_breaks_an_invariant(
    adapter: str, text: str, match: str, tmp_path: Path
) -> None:
    store, _path = _store(adapter, tmp_path, text)
    with pytest.raises(ValueError, match=match):
        store.load()


@pytest.mark.parametrize("adapter", sorted(_ADAPTERS))
def test_load_reads_the_entries_as_written(adapter: str, tmp_path: Path) -> None:
    store, _path = _store(adapter, tmp_path)
    plan = store.load()
    assert plan.instruments == (
        InstrumentEntry("BTC-USD-PERP.DYDX", store_order_book_deltas=True, retain_hours=24.0),
        InstrumentEntry("ETH-USD-PERP.DYDX"),
    )
    assert (plan.excluded, plan.min_liquidity_usd, plan.non_config_retain_hours) == (
        {"BAD-USD-PERP.DYDX"},
        250_000.0,
        8.0,
    )


# -- a flat plan (Bybit, Hyperliquid: Story 29.4) -----------------------------------------------

_FLAT = """# Bybit collector.
environment = "mainnet"
catalog_path = "/app/catalog"
instruments = ["BTCUSDT-LINEAR.BYBIT", "ETHUSDT-SPOT.BYBIT"]
book_time_source = "venue"
hold_back_seconds = 0.5
"""


def _flat(tmp_path: Path) -> tuple[TomlPlanStore, Path]:
    path = tmp_path / "config.toml"
    path.write_text(_FLAT)
    return TomlPlanStore(path, "BYBIT"), path


def test_a_flat_plan_writes_exclude_only_when_it_has_one(tmp_path: Path) -> None:
    store, path = _flat(tmp_path)
    unpinned = store.load().unpin("ETHUSDT-SPOT.BYBIT").plan
    store.save(unpinned)
    assert tomllib.loads(path.read_text())["exclude"] == ["ETHUSDT-SPOT.BYBIT"]
    assert store.load() == unpinned


def test_an_emptied_exclude_leaves_the_file_with_its_committed_key_set(tmp_path: Path) -> None:
    store, path = _flat(tmp_path)
    committed = list(tomllib.loads(path.read_text()))
    store.save(store.load().unpin("ETHUSDT-SPOT.BYBIT").plan)
    store.save(store.load().add("ETHUSDT-SPOT.BYBIT").plan)
    raw = tomllib.loads(path.read_text())
    assert list(raw) == committed
    assert raw["instruments"] == ["BTCUSDT-LINEAR.BYBIT", "ETHUSDT-SPOT.BYBIT"]


def test_a_flat_plan_is_uncapped_with_no_threshold(tmp_path: Path) -> None:
    store, _path = _flat(tmp_path)
    plan = store.load()
    assert (plan.cap, plan.min_liquidity_usd) == (None, None)
