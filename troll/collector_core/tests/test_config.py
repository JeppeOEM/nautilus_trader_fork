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
"""CoreConfig: dYdX's production thresholds are the defaults; env + cadence validated."""

from pathlib import Path

import pytest

from collector_core.config import core_config_from_dict
from collector_core.config import load_core_config


def test_defaults(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('instruments = ["BTCUSDT-LINEAR.BYBIT"]\n')
    cfg = load_core_config(path, ("mainnet", "testnet"))
    assert cfg.environment == "mainnet"
    assert cfg.instruments == ("BTCUSDT-LINEAR.BYBIT",)
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
        load_core_config(path, ("mainnet", "testnet"))


def test_non_positive_snapshot_interval_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("snapshot_interval_seconds = 0\n")
    with pytest.raises(ValueError, match="snapshot_interval_seconds"):
        load_core_config(path, ("mainnet",))


def test_unknown_key_rejected_unless_declared_extra(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text("stale_book_secs = 30.0\n")  # a typo must not silently mean 5.0
    with pytest.raises(ValueError, match="stale_book_secs"):
        load_core_config(path, ("mainnet",))
    assert core_config_from_dict(
        {"stale_book_secs": 30.0}, ("mainnet",), extra_keys=("stale_book_secs",)
    )


@pytest.mark.parametrize("key", ["seen_trade_ids", "stale_book_seconds", "flush_interval_seconds"])
def test_non_positive_threshold_rejected(key: str) -> None:
    with pytest.raises(ValueError, match=key):
        core_config_from_dict({key: 0}, ("mainnet",))


def test_duplicate_instruments_collapsed() -> None:
    cfg = core_config_from_dict({"instruments": ["A.X", "B.X", "A.X"]}, ("mainnet",))
    assert cfg.instruments == ("A.X", "B.X")
