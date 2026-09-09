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
"""Persisted chart indicator config: TOML load/save round-trip (Story 10.5)."""

from pathlib import Path

from ml_signals.chart_indicator_config import IndicatorEntry
from ml_signals.chart_indicator_config import load_config
from ml_signals.chart_indicator_config import save_config


def test_load_config_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    path = tmp_path / "chart_indicators.toml"
    assert load_config(path) == {}


def test_save_then_load_round_trips_multi_instrument_mixed_category_selection(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chart_indicators.toml"
    config = {
        "BTC-USD-PERP.DYDX": [
            IndicatorEntry(name="RelativeStrengthIndex", params={"period": 14}, category="native"),
            IndicatorEntry(name="CumulativeVolumeDelta", params={}, category="custom"),
        ],
        "ETH-USD-PERP.DYDX": [
            IndicatorEntry(name="CancelPressure", params={"window": 200}, category="custom"),
        ],
    }

    save_config(config, path)
    round_tripped = load_config(path)

    assert round_tripped == config


def test_save_config_is_a_full_rewrite_not_a_patch(tmp_path: Path) -> None:
    path = tmp_path / "chart_indicators.toml"
    save_config(
        {"BTC-USD-PERP.DYDX": [IndicatorEntry(name="OFI", params={}, category="custom")]}, path,
    )

    save_config(
        {"ETH-USD-PERP.DYDX": [IndicatorEntry(name="OFI", params={}, category="custom")]}, path,
    )

    round_tripped = load_config(path)
    assert "BTC-USD-PERP.DYDX" not in round_tripped
    assert "ETH-USD-PERP.DYDX" in round_tripped
