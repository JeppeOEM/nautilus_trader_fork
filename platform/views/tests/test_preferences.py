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

`chart_indicators.toml` (Story 10.5, its tests moved here in Story 24.2)
and `screener_columns.toml` (Story 17.5): load/save round-trips, and -- the AD-D12 freeze -- the
exact text each writer produces, recorded from the pre-move writers
(the `chart_indicator_config` and `screener_columns_config` modules' `save_config`
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
    # Positional on purpose: the chart entry's Story 32.3 keys are keyword-only, so a column's
    # fourth field is still its bar size.
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


def test_pre_story_32_3_chart_file_loads_with_defaults_and_saves_back_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chart_indicators.toml"
    path.write_text(_CHART_INDICATORS_TEXT)

    entry = load_chart_indicators(path)["BTC-USD-PERP.DYDX"][0]

    assert (entry.source, entry.hidden, entry.style) == ("close", False, {})


def test_source_hidden_and_style_round_trip_and_are_written_only_when_not_default(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chart_indicators.toml"
    style = {"value": {"color": "#00ff00", "line_width": 2, "line_style": "dotted"}}
    config = {
        "BTC-USD-PERP.DYDX": [
            IndicatorEntry(
                "SimpleMovingAverage",
                {"period": 20},
                "native",
                source="hl2",
                hidden=True,
                style=style,
            ),
            IndicatorEntry("SimpleMovingAverage", {"period": 20}, "native"),
        ]
    }

    save_chart_indicators(config, path)

    assert load_chart_indicators(path) == config
    text = path.read_text()
    assert text.count("source") == 1
    assert text.count("hidden") == 1


def test_wrong_typed_source_hidden_style_fall_back_to_defaults_with_one_warning_each(
    tmp_path: Path, caplog
) -> None:
    path = tmp_path / "chart_indicators.toml"
    path.write_text(
        '[["BTC-USD-PERP.DYDX"]]\nname = "SimpleMovingAverage"\ncategory = "native"\n'
        'source = 5\nhidden = "yes"\nstyle = { value = 3 }\n'
    )

    with caplog.at_level("WARNING", logger="views.preferences"):
        entry = load_chart_indicators(path)["BTC-USD-PERP.DYDX"][0]

    assert (entry.source, entry.hidden, entry.style) == ("close", False, {})
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 3


def test_a_style_the_put_route_would_refuse_falls_back_to_the_default_on_load(
    tmp_path: Path, caplog
) -> None:
    # A nested table or a non-finite float would load, then make every save of the coin a 400
    # (the client echoes the entry back) or its GET a 500 (JSON has no NaN): one rule for both.
    path = tmp_path / "chart_indicators.toml"
    for style in ("{ value = { color = { r = 1 } } }", "{ value = { line_width = nan } }"):
        path.write_text(
            '[["BTC-USD-PERP.DYDX"]]\nname = "SimpleMovingAverage"\ncategory = "native"\n'
            f"style = {style}\n"
        )
        caplog.clear()
        with caplog.at_level("WARNING", logger="views.preferences"):
            entry = load_chart_indicators(path)["BTC-USD-PERP.DYDX"][0]
        assert entry.style == {}, style
        assert len(caplog.records) == 1, style
