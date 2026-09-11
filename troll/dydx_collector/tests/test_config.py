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
"""Config loading: snapshot interval default/override, per-coin raw-delta retention."""

import dataclasses
from pathlib import Path

import pytest

from dydx_collector.config import InstrumentEntry
from dydx_collector.config import load_config
from dydx_collector.config import save_config


def _write_toml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


def test_snapshot_interval_defaults_to_half_second(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "")
    config = load_config(path)
    assert config.snapshot_interval_seconds == 0.5


def test_snapshot_interval_override_is_honored(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "snapshot_interval_seconds = 2.0\n")
    config = load_config(path)
    assert config.snapshot_interval_seconds == 2.0


def test_instrument_retain_hours_defaults_to_unlimited(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
        [[instruments]]
        id = "BTC-USD-PERP.DYDX"
        store_order_book_deltas = true
        """,
    )
    config = load_config(path)
    assert config.instruments[0].retain_hours is None


def test_instrument_retain_hours_override_is_honored(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
        [[instruments]]
        id = "BTC-USD-PERP.DYDX"
        store_order_book_deltas = true
        retain_hours = 48.0
        """,
    )
    config = load_config(path)
    assert config.instruments[0].retain_hours == 48.0


def test_negative_retain_hours_is_rejected(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
        [[instruments]]
        id = "BTC-USD-PERP.DYDX"
        store_order_book_deltas = true
        retain_hours = -1.0
        """,
    )
    with pytest.raises(ValueError, match="retain_hours"):
        load_config(path)


def test_zero_snapshot_interval_is_rejected(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "snapshot_interval_seconds = 0\n")
    with pytest.raises(ValueError, match="snapshot_interval_seconds"):
        load_config(path)


def test_negative_snapshot_interval_is_rejected(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "snapshot_interval_seconds = -0.5\n")
    with pytest.raises(ValueError, match="snapshot_interval_seconds"):
        load_config(path)


def test_save_config_round_trips_all_fields(tmp_path: Path) -> None:
    path = _write_toml(
        tmp_path,
        """
        network = "testnet"
        catalog_path = "my_catalog"
        flush_interval_seconds = 30
        config_reload_seconds = 15
        open_interest_poll_seconds = 120
        snapshot_interval_seconds = 1.0
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
        """,
    )
    original = load_config(path)

    save_config(original, path)
    round_tripped = load_config(path)

    assert round_tripped == original


def test_save_config_omits_retain_hours_when_none(tmp_path: Path) -> None:
    path = _write_toml(tmp_path, "")
    config = load_config(path)
    config = dataclasses.replace(config, instruments=(InstrumentEntry(id="SOL-USD-PERP.DYDX"),))

    save_config(config, path)
    round_tripped = load_config(path)

    assert round_tripped.instruments[0].retain_hours is None
