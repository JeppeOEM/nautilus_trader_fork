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
"""Hyperliquid-specific pieces only: config (core tests live in collector_core)."""

from pathlib import Path

import pytest

from hyperliquid_collector.config import load_config



def test_config_defaults_and_validation(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text('instruments = ["BTC-USD-PERP.HYPERLIQUID"]\n')
    cfg = load_config(path)  # venue default: 30s stale guard (l2Book pushes ~5s apart)
    assert (cfg.environment, cfg.snapshot_interval_seconds, cfg.stale_book_seconds) == (
        "mainnet",
        1.0,
        30.0,
    )
    path.write_text("stale_book_seconds = 12.0\n")
    assert load_config(path).stale_book_seconds == 12.0
    path.write_text('environment = "prod"\n')
    with pytest.raises(ValueError, match="environment"):
        load_config(path)
