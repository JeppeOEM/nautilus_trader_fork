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

from pathlib import Path

import pytest

from dydx_collector.config import load_config


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
