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
`views.preferences`: the two UI preference files (Story 24.2 merged their loaders here).

`chart_indicators.toml` (Story 10.5, moved from `ml_signals/tests/test_chart_indicator_config.py`)
and `screener_columns.toml` (Story 17.5): load/save round-trips, and -- the AD-D12 freeze -- the
exact text each writer produces, recorded from the pre-move writers
(`ml_signals.chart_indicator_config.save_config` / `ml_signals.screener_columns_config.save_config`
at `f879c11ca3`), so a key rename or a serialisation change fails here, not on a deployed file.
"""

from pathlib import Path

from views.preferences import DEFAULT_BAR_SECONDS
from views.preferences import ColumnEntry
from views.preferences import IndicatorEntry
from views.preferences import load_chart_indicators
from views.preferences import load_chart_indicators as load_config
from views.preferences import load_screener_columns
from views.preferences import save_chart_indicators
from views.preferences import save_chart_indicators as save_config
from views.preferences import save_screener_columns


# Recorded from the pre-move writers (see module docstring) -- never regenerate these from the
# code under test: that would turn the freeze into a tautology.
_CHART_INDICATORS_TEXT = (
    '"BTC-USD-PERP.DYDX" = [\n'
    '    { name = "RelativeStrengthIndex", params = { period = 21 }, category = "native" },\n'
    '    { name = "BollingerBands", params = { k = 2.5, ma_type = "EXPONENTIAL" }, '
    'category = "native" },\n'
    '    { name = "CumulativeVolumeDelta", params = {}, category = "custom" },\n'
    "]\n"
    '"ETHUSDT-LINEAR.BYBIT" = [\n'
    '    { name = "SimpleMovingAverage", params = {}, category = "native" },\n'
    "]\n"
)
_SCREENER_COLUMNS_TEXT = (
    "[[columns]]\n"
    'name = "RelativeStrengthIndex"\n'
    'category = "native"\n'
    "bar_seconds = 60\n"
    "\n"
    "[columns.params]\n"
    "period = 14\n"
    "\n"
    "[[columns]]\n"
    'name = "AverageTrueRange"\n'
    'category = "native"\n'
    "bar_seconds = 3600\n"
    "\n"
    "[columns.params]\n"
    "\n"
    "[[columns]]\n"
    'name = "OrderFlowImbalance"\n'
    'category = "custom"\n'
    "bar_seconds = 14400\n"
    "\n"
    "[columns.params]\n"
    "window = 20\n"
)


def test_chart_indicators_file_text_round_trips_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "chart_indicators.toml"
    path.write_text(_CHART_INDICATORS_TEXT)

    save_chart_indicators(load_chart_indicators(path), path)

    assert path.read_text() == _CHART_INDICATORS_TEXT


def test_screener_columns_file_text_round_trips_byte_identical(tmp_path: Path) -> None:
    path = tmp_path / "screener_columns.toml"
    path.write_text(_SCREENER_COLUMNS_TEXT)

    loaded = load_screener_columns(path)
    save_screener_columns(loaded, path)

    assert path.read_text() == _SCREENER_COLUMNS_TEXT
    assert loaded[1] == ColumnEntry("AverageTrueRange", {}, "native", 3600)


def test_screener_columns_missing_file_or_bar_size_takes_the_defaults(tmp_path: Path) -> None:
    path = tmp_path / "screener_columns.toml"
    assert load_screener_columns(path) == []
    path.write_text('[[columns]]\nname = "RelativeStrengthIndex"\ncategory = "native"\n')
    (column,) = load_screener_columns(path)
    assert (column.params, column.bar_seconds) == ({}, DEFAULT_BAR_SECONDS)


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
        {"BTC-USD-PERP.DYDX": [IndicatorEntry(name="OFI", params={}, category="custom")]},
        path,
    )

    save_config(
        {"ETH-USD-PERP.DYDX": [IndicatorEntry(name="OFI", params={}, category="custom")]},
        path,
    )

    round_tripped = load_config(path)
    assert "BTC-USD-PERP.DYDX" not in round_tripped
    assert "ETH-USD-PERP.DYDX" in round_tripped
