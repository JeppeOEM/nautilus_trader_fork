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
"""
`views.preferences`' layout resource (Story 32.6): the TOML round trip, the strict validation that
names the offending key, the tolerant read of a stale coin timeframe and the `[default]` template.
"""

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest

from views.preferences import BUILTIN_DEFAULT_LAYOUT
from views.preferences import LAYOUT_BAR_SECONDS
from views.preferences import MAX_PANE_ID_LENGTH
from views.preferences import PROFILE_SESSIONS
from views.preferences import ChartLayouts
from views.preferences import IndicatorEntry
from views.preferences import LayoutError
from views.preferences import load_chart_layouts
from views.preferences import save_chart_layouts
from views.preferences import validate_layout


_IID = "BTC-USD-PERP.DYDX"
_ETH = "ETH-USD-PERP.BYBIT"


def _layout(**over: Any) -> dict[str, Any]:
    layout = copy.deepcopy(BUILTIN_DEFAULT_LAYOUT)
    layout.update(over)
    return layout


def test_the_builtin_default_is_valid_and_is_1m_candles() -> None:
    layout = validate_layout(BUILTIN_DEFAULT_LAYOUT)
    assert (layout["bar_seconds"], layout["mode"]) == (60, "candles")
    assert layout["volume"] is True
    assert layout["crosshair"] is True
    assert layout["pane_heights"] == {}


def test_a_missing_file_is_empty(tmp_path: Path) -> None:
    loaded = load_chart_layouts(tmp_path / "nope.toml")
    assert loaded == ChartLayouts()


def test_round_trip_with_pane_heights_anchors_and_default_indicators(tmp_path: Path) -> None:
    path = tmp_path / "chart_layouts.toml"
    profile = {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "kind": "fixed", "start": 10, "end": 99}
    coin = _layout(bar_seconds=3600, mode="lines", pane_heights={"rsi": 220}, visible_bars=80.5)
    coin["volume_profile"] = profile
    default = _layout(bar_seconds=900)
    sma = IndicatorEntry(name="SimpleMovingAverage", params={"period": 20}, category="native")
    save_chart_layouts(ChartLayouts({_IID: coin, _ETH: _layout()}, default, [sma]), path)
    loaded = load_chart_layouts(path)
    assert loaded.layouts[_IID] == coin
    assert loaded.layouts[_ETH] == _layout()
    assert loaded.default == default
    assert loaded.default_indicators == [sma]
    raw = tomllib.loads(path.read_text())
    assert raw[_IID]["v"] == 1
    assert raw["default"]["v"] == 1
    assert "start" not in raw[_ETH]["volume_profile"]  # an unset anchor is omitted, no null


def test_an_empty_default_table_means_no_template(tmp_path: Path) -> None:
    path = tmp_path / "l.toml"
    path.write_text("[default]\n")
    assert load_chart_layouts(path).default is None


def test_default_is_never_an_instrument_id(tmp_path: Path) -> None:
    with pytest.raises(LayoutError, match="default"):
        save_chart_layouts(ChartLayouts({"default": _layout()}), tmp_path / "l.toml")
    assert not (tmp_path / "l.toml").exists()


@pytest.mark.parametrize(
    ("over", "key"),
    [
        ({"mode": "bars"}, "mode"),
        ({"bar_seconds": 30}, "bar_seconds"),
        ({"bar_seconds": True}, "bar_seconds"),
        ({"volume": 1}, "volume"),
        ({"crosshair": "yes"}, "crosshair"),
        ({"pane_heights": {"rsi": 0}}, "pane_heights.rsi"),
        ({"pane_heights": {"rsi": 1.5}}, "pane_heights.rsi"),
        ({"pane_heights": []}, "pane_heights"),
        ({"visible_bars": 0}, "visible_bars"),
        ({"visible_bars": float("nan")}, "visible_bars"),
        ({"visible_bars": 10**9}, "visible_bars"),
        ({"surprise": 1}, "surprise"),
        ({"volume_profile": 3}, "volume_profile"),
        ({"volume_profile": {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "kind": "x"}}, "kind"),
        ({"volume_profile": {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "rows": 1}}, "rows"),
        ({"volume_profile": {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "hd": 0}}, "hd"),
        ({"volume_profile": {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "start": 1.5}}, "start"),
        (
            {"volume_profile": {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "z": 1}},
            "volume_profile.z",
        ),
        (
            {"volume_profile": {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "value_area_pct": 101}},
            "value_area_pct",
        ),
        ({"pane_heights": {"x" * (MAX_PANE_ID_LENGTH + 1): 100}}, "pane_heights"),
        (
            {"volume_profile": {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "kind": "fixed"}},
            "start",
        ),
        (
            {
                "volume_profile": {
                    **BUILTIN_DEFAULT_LAYOUT["volume_profile"],
                    "kind": "fixed",
                    "start": 200,
                    "end": 100,
                }
            },
            "start",
        ),
    ],
)
def test_validation_names_the_bad_key(over: dict[str, Any], key: str) -> None:
    with pytest.raises(LayoutError) as exc:
        validate_layout(_layout(**over))
    assert key in exc.value.key


def test_a_missing_field_is_refused_naming_it() -> None:
    layout = _layout()
    del layout["crosshair"]
    with pytest.raises(LayoutError, match="crosshair"):
        validate_layout(layout)


def test_a_stale_timeframe_and_mode_are_tolerated_on_read_only(tmp_path: Path) -> None:
    path = tmp_path / "l.toml"
    save_chart_layouts(ChartLayouts({_IID: _layout()}), path)
    path.write_text(
        path.read_text()
        .replace("bar_seconds = 60", "bar_seconds = 30")
        .replace('mode = "candles"', 'mode = "bars"')
    )
    loaded = load_chart_layouts(path)
    assert (loaded.layouts[_IID]["bar_seconds"], loaded.layouts[_IID]["mode"]) == (30, "bars")
    # Saving other coins back must not trip over the stale one.
    save_chart_layouts(loaded, path)
    with pytest.raises(LayoutError, match="bar_seconds"):
        validate_layout(loaded.layouts[_IID])


def test_a_stale_default_is_refused_loudly(tmp_path: Path) -> None:
    path = tmp_path / "l.toml"
    save_chart_layouts(ChartLayouts({}, _layout()), path)
    path.write_text(path.read_text().replace("bar_seconds = 60", "bar_seconds = 30"))
    with pytest.raises(LayoutError, match=r"default\.bar_seconds"):
        load_chart_layouts(path)


def test_a_stray_key_or_wrong_version_in_the_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "l.toml"
    save_chart_layouts(ChartLayouts({_IID: _layout()}), path)
    good = path.read_text()
    path.write_text(good.replace("v = 1", "v = 2"))
    with pytest.raises(LayoutError, match=_IID):
        load_chart_layouts(path)
    path.write_text(good + "bogus = 1\n")
    with pytest.raises(LayoutError, match="bogus"):
        load_chart_layouts(path)


def test_a_failed_save_leaves_the_old_file(tmp_path: Path) -> None:
    path = tmp_path / "l.toml"
    save_chart_layouts(ChartLayouts({_IID: _layout()}), path)
    before = path.read_bytes()
    with pytest.raises(LayoutError):
        save_chart_layouts(ChartLayouts({_IID: _layout(), _ETH: _layout(surprise=1)}), path)
    assert path.read_bytes() == before


def test_session_periods_mirror_the_frontend() -> None:
    source = (Path(__file__).parents[2] / "frontend/src/lib/sessionProfile.ts").read_text()
    line = next(x for x in source.splitlines() if x.startswith("export const SESSION_PERIODS"))
    listed = tuple(part.strip(' "') for part in line.split("[", 2)[2].split("]")[0].split(","))
    assert listed == PROFILE_SESSIONS


def test_bar_seconds_mirror_the_frontend() -> None:
    source = (Path(__file__).parents[2] / "frontend/src/timeframes.ts").read_text()
    listed = tuple(int(x.split("seconds:")[1].split("}")[0]) for x in source.split("{ label:")[1:])
    assert listed == LAYOUT_BAR_SECONDS


def test_a_real_indicator_instance_id_is_a_storable_pane_id() -> None:
    # The longest catalog ids (CandlePattern with its defaults, MACD, Keltner) exceed 64 characters.
    pane = (
        "CandlePattern_body_ratio=0.3,doji_body_ratio=0.1,marubozu_shadow_ratio=0.05,"
        "pattern=ENGULFING,shadow_ratio=2.0,star_gap=True,trend_bars=3,tweezer_ratio=0.05:hl2"
    )
    assert validate_layout(_layout(pane_heights={pane: 180}))["pane_heights"] == {pane: 180}


def test_a_session_outside_the_closed_set_is_refused_naming_it() -> None:
    profile = {**BUILTIN_DEFAULT_LAYOUT["volume_profile"], "session": "day"}
    with pytest.raises(LayoutError) as exc:
        validate_layout(_layout(volume_profile=profile))
    assert exc.value.key == "volume_profile.session"


def test_a_file_error_carries_the_bare_reason(tmp_path: Path) -> None:
    path = tmp_path / "l.toml"
    save_chart_layouts(ChartLayouts({_IID: _layout()}), path)
    path.write_text(path.read_text().replace("hd = false", "hd = 1"))
    with pytest.raises(LayoutError) as exc:
        load_chart_layouts(path)
    assert exc.value.key == f"{_IID}.volume_profile.hd"
    assert exc.value.reason == "must be a boolean"
