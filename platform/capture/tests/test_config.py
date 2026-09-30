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
CoreConfig: dYdX's production thresholds are the defaults; env + cadence validated. The one venue
loader (`load_venue_config`, Story 25.4): each venue's keys, defaults and plan shape.
"""

from pathlib import Path

import pytest

from capture.application.config import core_config_from_dict
from capture.infrastructure.config import load_venue_config
from capture.infrastructure.config import plan_toml_fields
from capture.infrastructure.config import venue_config_from_dict
from capture.venues.dydx.config import DYDX_MAX_COLLECTED_INSTRUMENTS
from capture.venues.dydx.config import DydxConfig


def _load(path: Path) -> object:
    config, _plan = load_venue_config(path, "BYBIT")
    return config


def test_defaults(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('instruments = ["BTCUSDT-LINEAR.BYBIT"]\n')
    cfg, plan = load_venue_config(path, "BYBIT")
    assert cfg.environment == "mainnet"
    assert plan.collected == ("BTCUSDT-LINEAR.BYBIT",)
    assert (cfg.snapshot_interval_seconds, cfg.stale_book_seconds, cfg.crossed_resync_seconds) == (
        1.0,
        5.0,
        10.0,
    )
    assert (cfg.stale_trade_seconds, cfg.seen_trade_ids, cfg.flush_interval_seconds) == (
        10.0,
        2000,
        60,
    )


def test_environment_validated(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('environment = "prod"\n')
    with pytest.raises(ValueError, match="environment"):
        _load(path)


def test_non_positive_snapshot_interval_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("snapshot_interval_seconds = 0\n")
    with pytest.raises(ValueError, match="snapshot_interval_seconds"):
        _load(path)


def test_unknown_key_rejected_unless_declared_extra(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("stale_book_secs = 30.0\n")  # a typo must not silently mean 5.0
    with pytest.raises(ValueError, match="stale_book_secs"):
        _load(path)
    assert core_config_from_dict(
        {"stale_book_secs": 30.0}, ("mainnet",), extra_keys=("stale_book_secs",)
    )


@pytest.mark.parametrize("key", ["seen_trade_ids", "stale_book_seconds", "flush_interval_seconds"])
def test_non_positive_threshold_rejected(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        core_config_from_dict({key: 0}, ("mainnet",))


def test_duplicate_instruments_collapsed() -> None:
    ids = ["A.BYBIT", "B.BYBIT", "A.BYBIT"]
    _config, plan = venue_config_from_dict({"instruments": ids}, "BYBIT")
    assert plan.collected == ("A.BYBIT", "B.BYBIT")
    assert plan.cap is None  # Bybit/Hyperliquid plans are uncapped (Story 29.4)


def test_instruments_is_the_plans_not_a_core_key() -> None:
    with pytest.raises(ValueError, match="instruments"):
        core_config_from_dict({"instruments": ["A.X"]}, ("mainnet",))


def test_hyperliquid_defaults_its_stale_guard() -> None:
    config, _plan = venue_config_from_dict({}, "HYPERLIQUID")
    assert config.stale_book_seconds == 12.0


@pytest.mark.parametrize("venue", ["BYBIT", "HYPERLIQUID"])
def test_a_flat_plan_reads_an_optional_exclude(venue: str) -> None:
    a, b = f"A.{venue}", f"B.{venue}"
    _config, plan = venue_config_from_dict({"instruments": [a], "exclude": [b]}, venue)
    assert (plan.collected, plan.excluded, plan.cap) == ((a,), {b}, None)


@pytest.mark.parametrize("venue", ["BYBIT", "HYPERLIQUID"])
def test_a_flat_plan_writes_exclude_only_when_non_empty(venue: str) -> None:
    a = f"A.{venue}"
    _config, plan = venue_config_from_dict({"instruments": [a]}, venue)
    assert plan_toml_fields(plan) == {"instruments": [a]}
    unpinned = plan.unpin(a).plan
    assert plan_toml_fields(unpinned) == {"instruments": [], "exclude": [a]}
    assert venue_config_from_dict(plan_toml_fields(unpinned), venue)[1] == unpinned


def test_a_flat_plan_refuses_an_id_both_collected_and_excluded() -> None:
    with pytest.raises(ValueError, match="both collects and excludes"):
        venue_config_from_dict({"instruments": ["A.BYBIT"], "exclude": ["A.BYBIT"]}, "BYBIT")


@pytest.mark.parametrize("key", ["instruments", "exclude"])
def test_a_hand_edited_id_of_another_venue_is_refused(key: str) -> None:
    """Story 29.4: a foreign id can never be loaded, nor hot-reloaded, into a venue's plan."""
    with pytest.raises(ValueError, match="ids of another venue"):
        venue_config_from_dict({key: ["SOL-USD-PERP.HYPERLIQUID"]}, "BYBIT")


_DYDX = """
network = "testnet"
stale_book_seconds = 7.5
liquidity_min_oi_usd = 250000
exclude = ["BAD-USD-PERP.DYDX"]

[[instruments]]
id = "BTC-USD-PERP.DYDX"
store_order_book_deltas = true
retain_hours = 24.0

[[instruments]]
id = "ETH-USD-PERP.DYDX"
"""


def test_dydx_core_keys_are_now_honoured(tmp_path: Path) -> None:
    """Story 25.4: dYdX's file goes through the core's strict loader, so its thresholds apply."""
    path = tmp_path / "c.toml"
    path.write_text(_DYDX)
    config, plan = load_venue_config(path, "DYDX")
    assert isinstance(config, DydxConfig)
    assert (config.environment, config.stale_book_seconds) == ("testnet", 7.5)
    assert plan.collected == ("BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX")
    assert plan.delta_retain_hours == {"BTC-USD-PERP.DYDX": 24.0}
    assert (plan.cap, plan.min_liquidity_usd, plan.non_config_retain_hours) == (
        DYDX_MAX_COLLECTED_INSTRUMENTS,
        250_000.0,
        4.0,
    )


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ({"environment": "mainnet"}, "network"),
        ({"network": "prod"}, "network"),
        ({"stale_book_secs": 1.0}, "stale_book_secs"),
        ({"instruments": [{"id": "A-PERP.DYDX", "bar_intervals": []}]}, "bar_intervals"),
        ({"instruments": [{"store_order_book_deltas": True}]}, "id"),
        ({"instruments": [{"id": "A-PERP.DYDX", "store_order_book_deltas": 1}]}, "true or false"),
        ({"config_reload_seconds": 0}, "config_reload_seconds"),
        ({"trade_feeds": 2}, "dYdX runs one trade feed"),
        (
            {"instruments": [{"id": f"C{i}-PERP.DYDX"} for i in range(31)]},
            "above its cap of 30",
        ),
        ({"instruments": [{"id": "A-PERP.DYDX"}], "exclude": ["A-PERP.DYDX"]}, "A-PERP.DYDX"),
    ],
)
def test_dydx_file_is_refused_on_any_unknown_key_or_broken_invariant(
    raw: dict[str, object], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        venue_config_from_dict(raw, "DYDX")


def test_plan_toml_fields_read_back_as_the_same_plan(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(_DYDX)
    _config, plan = load_venue_config(path, "DYDX")
    fields = plan_toml_fields(plan)
    assert fields["instruments"][1] == {"id": "ETH-USD-PERP.DYDX"}  # defaults omitted
    assert venue_config_from_dict({"network": "testnet", **fields}, "DYDX")[1] == plan


def test_trade_feeds_defaults_to_one_and_accepts_two() -> None:
    assert core_config_from_dict({}, ("mainnet",)).trade_feeds == 1
    assert core_config_from_dict({"trade_feeds": 2}, ("mainnet",)).trade_feeds == 2


@pytest.mark.parametrize("value", [0, 3])
def test_trade_feeds_outside_one_or_two_fails_closed(value: int) -> None:
    with pytest.raises(ValueError, match="trade_feeds"):
        core_config_from_dict({"trade_feeds": value}, ("mainnet",))


@pytest.mark.parametrize("value", [2.5, True, "2"])
def test_trade_feeds_must_be_an_integer(value: object) -> None:
    with pytest.raises(ValueError, match="trade_feeds must be the integer"):
        core_config_from_dict({"trade_feeds": value}, ("mainnet",))


def test_dydx_ws_raw_sink_is_off_unless_the_plan_says_true(tmp_path: Path) -> None:
    """The raw WS sink is a plan-file switch, off by default (operator decision 2026-09-30)."""
    path = tmp_path / "c.toml"
    path.write_text(_DYDX)
    config, _ = load_venue_config(path, "DYDX")
    assert isinstance(config, DydxConfig)
    assert config.ws_raw_sink is False
    path.write_text("ws_raw_sink = true\n" + _DYDX)  # top level, above the tables
    config, _ = load_venue_config(path, "DYDX")
    assert isinstance(config, DydxConfig)
    assert config.ws_raw_sink is True


@pytest.mark.parametrize("value", ["1", '"true"', "1.0"])
def test_dydx_ws_raw_sink_must_be_a_boolean(tmp_path: Path, value: str) -> None:
    path = tmp_path / "c.toml"
    path.write_text(f"ws_raw_sink = {value}\n" + _DYDX)
    with pytest.raises(ValueError, match="ws_raw_sink must be true or false"):
        load_venue_config(path, "DYDX")
