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
`views.preferences`' drawings resource (Story 32.5): the TOML round trip of every kind, and the
strict validation that names the offending field (a malformed item is refused, never dropped).
Story 33.10 adds the second drawing set, the optional `locked`/`hidden` and the line look.
"""

import copy
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from views.preferences import ANCHORED_VWAP_SOURCES
from views.preferences import DERIVATIVE_LINE_STYLES
from views.preferences import DRAWING_KINDS
from views.preferences import LINE_STYLES
from views.preferences import MAX_DRAWING_FONT_SIZE
from views.preferences import MAX_DRAWING_TEXT_LENGTH
from views.preferences import MIN_DRAWING_FONT_SIZE
from views.preferences import VWAP_SOURCES
from views.preferences import DrawingError
from views.preferences import load_chart_drawings
from views.preferences import save_chart_drawings
from views.preferences import validate_drawing
from views.preferences import validate_drawings


_IID = "BTC-USD-PERP.DYDX"
_ANCHORS = [{"time": 1_800_000_000, "price": 100.0}, {"time": 1_800_003_600, "price": 90.5}]


def _hline() -> dict[str, Any]:
    return {"kind": "hline", "id": "hline-1", "price": 61090.59855, "color": "#aabbcc"}


def _trendline() -> dict[str, Any]:
    return {"kind": "trendline", "id": "trendline-2", "anchors": copy.deepcopy(_ANCHORS)}


def _fib() -> dict[str, Any]:
    return {
        "kind": "fib",
        "id": "fib-3",
        "anchors": copy.deepcopy(_ANCHORS),
        "levels": [
            {"ratio": 0, "enabled": True, "color": "#111111"},
            {"ratio": 0.618, "enabled": False, "color": "#222222"},
        ],
        "extend_right": True,
        "label_side": "left",
        "line_width": 2,
    }


def _position(side: str = "long") -> dict[str, Any]:
    stop, target = (99.0, 102.0) if side == "long" else (101.0, 98.0)
    return {
        "kind": "position",
        "id": "position-4",
        "side": side,
        "time": 1_800_000_000,
        "entry": 100.0,
        "stop": stop,
        "target": target,
        "width_bars": 40,
        "account": 10_000.0,
        "risk_pct": 1.0,
    }


def _anchored_vp() -> dict[str, Any]:
    return {
        "kind": "anchored_vp",
        "id": "anchored_vp-1",
        "time": 1_800_000_000,
        "rows": 24,
        "value_area_pct": 70,
        "up_color": "#25a399",
        "down_color": "#ef5350",
    }


def _anchored_vwap() -> dict[str, Any]:
    return {
        "kind": "anchored_vwap",
        "id": "anchored_vwap-1",
        "time": 1_800_000_000,
        "source": "hlc3",
        "bands": True,
        "color": "#2962ff",
        "band_color": "#b26a00",
    }


_THREE = [*_ANCHORS, {"time": 1_800_007_200, "price": 95.25}]


def _two_point(kind: str) -> dict[str, Any]:
    return {"kind": kind, "id": f"{kind}-1", "anchors": copy.deepcopy(_ANCHORS)}


def _vline() -> dict[str, Any]:
    return {"kind": "vline", "id": "vline-1", "time": 1_800_000_000}


def _rect() -> dict[str, Any]:
    return {**_two_point("rect"), "fill_opacity": 0.2}


def _channel() -> dict[str, Any]:
    return {**_two_point("channel"), "offset": -4.5}


def _text() -> dict[str, Any]:
    return {
        "kind": "text",
        "id": "text-1",
        "anchor": {"time": 1_800_000_000, "price": 100.0},
        "text": "breakout\nretest",
        "font_size": 14,
        "color": "#ffffff",
    }


def _fib_extension() -> dict[str, Any]:
    return {
        **_fib(),
        "kind": "fib_extension",
        "id": "fib_extension-1",
        "anchors": copy.deepcopy(_THREE),
    }


def _all_kinds() -> list[dict[str, Any]]:
    """One item of each of the 16 kinds, in `DRAWING_KINDS` order."""
    return [
        _hline(),
        _trendline(),
        _fib(),
        _position(),
        _anchored_vp(),
        _anchored_vwap(),
        _two_point("ray"),
        _two_point("extended"),
        _vline(),
        _rect(),
        _channel(),
        _text(),
        _two_point("arrow"),
        _fib_extension(),
        _two_point("price_range"),
        _two_point("date_range"),
    ]


def test_every_kind_round_trips_through_the_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    items = [
        _hline(),
        _trendline(),
        _fib(),
        _position("long"),
        {**_position("short"), "id": "p5"},
        _anchored_vp(),
        _anchored_vwap(),
    ]
    save_chart_drawings({_IID: items}, path)
    assert load_chart_drawings(path) == {_IID: items}


def test_the_file_is_one_versioned_table_per_instrument(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    save_chart_drawings({_IID: [_hline()], "ETHUSDT-LINEAR.BYBIT": [_trendline()]}, path)
    raw = tomllib.loads(path.read_text())
    assert {iid: table["v"] for iid, table in raw.items()} == {_IID: 1, "ETHUSDT-LINEAR.BYBIT": 1}


def test_an_instrument_with_no_drawings_left_leaves_the_file(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    save_chart_drawings({_IID: [_hline()], "ETHUSDT-LINEAR.BYBIT": []}, path)
    assert list(load_chart_drawings(path)) == [_IID]


def test_a_missing_or_empty_file_is_nothing_drawn_yet(tmp_path: Path) -> None:
    assert load_chart_drawings(tmp_path / "absent.toml") == {}
    empty = tmp_path / "chart_drawings.toml"
    empty.write_text("")
    assert load_chart_drawings(empty) == {}


def test_a_save_leaves_no_temp_file_and_a_bad_item_leaves_the_file_alone(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    save_chart_drawings({_IID: [_hline()]}, path)
    before = path.read_text()
    with pytest.raises(DrawingError, match="anchors"):
        save_chart_drawings({_IID: [{"kind": "fib", "id": "f"}]}, path)
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["chart_drawings.toml"]


def test_a_failed_replace_leaves_no_temp_file_and_the_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "chart_drawings.toml"
    save_chart_drawings({_IID: [_hline()]}, path)
    before = path.read_text()

    def boom(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("views.preferences.os.replace", boom)
    with pytest.raises(OSError, match="disk full"):
        save_chart_drawings({_IID: [_hline(), {**_hline(), "id": "hline-9"}]}, path)
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["chart_drawings.toml"]


def test_a_table_of_another_version_is_refused_not_skipped(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    path.write_text(f'["{_IID}"]\nv = 2\nitems = []\n')
    with pytest.raises(DrawingError, match=r"v = 1"):
        load_chart_drawings(path)


@pytest.mark.parametrize(
    "table",
    [
        "v = 1\n",  # no item list: refused, never defaulted to "nothing drawn"
        "v = 1\nitems = []\nnote = 'hand-edited'\n",  # a stray key the next rewrite would drop
    ],
)
def test_a_table_without_exactly_v_and_items_is_refused(tmp_path: Path, table: str) -> None:
    path = tmp_path / "chart_drawings.toml"
    path.write_text(f'["{_IID}"]\n{table}')
    with pytest.raises(DrawingError, match=r"exactly `v` and `items`"):
        load_chart_drawings(path)


def _without(item: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != key}


@pytest.mark.parametrize(
    ("item", "field"),
    [
        ({"kind": "circle", "id": "x"}, "kind"),
        ({"kind": "hline", "price": 1.0}, "id"),
        ({**_hline(), "price": "100"}, "price"),
        ({**_hline(), "price": float("nan")}, "price"),
        ({**_hline(), "price": True}, "price"),
        (_without(_hline(), "price"), "price"),
        ({**_hline(), "anchors": []}, "anchors"),  # not a field of an hline
        ({**_hline(), "color": 3}, "color"),
        (_without(_fib(), "anchors"), "anchors"),
        ({**_fib(), "anchors": _ANCHORS[:1]}, "anchors"),
        ({**_trendline(), "anchors": [{"time": 1.5, "price": 1.0}, _ANCHORS[1]]}, "anchors"),
        ({**_fib(), "levels": [{"ratio": -1, "enabled": True, "color": "#fff"}]}, "levels"),
        ({**_fib(), "label_side": "middle"}, "label_side"),
        ({**_fib(), "line_width": 5}, "line_width"),
        ({**_fib(), "extend_right": "yes"}, "extend_right"),
        (_without(_fib(), "levels"), "levels"),
        (_without(_fib(), "extend_right"), "extend_right"),
        (_without(_fib(), "label_side"), "label_side"),
        (_without(_fib(), "line_width"), "line_width"),
        (
            {
                **_fib(),
                "levels": [
                    {"ratio": 0.5, "enabled": True, "color": "#1"},
                    {"ratio": 0.5, "enabled": False, "color": "#2"},
                ],
            },
            "levels",
        ),
        ({**_hline(), "price": 0}, "price"),
        ({**_hline(), "price": -1.0}, "price"),
        ({**_position(), "entry": 0.0, "stop": -1.0}, "entry"),
        ({**_position(), "stop": 0.0}, "stop"),
        ({**_position("short"), "target": -5.0}, "target"),
        ({**_position(), "width_bars": 10_001}, "width_bars"),
        ({**_position(), "time": -1}, "time"),
        ({**_position(), "time": 2**63}, "time"),
        ({**_trendline(), "anchors": [{"time": -5, "price": 1.0}, _ANCHORS[1]]}, "anchors"),
        ({**_position(), "side": "flat"}, "side"),
        ({**_position(), "target": 99.5}, "entry"),  # a long's target below its entry
        ({**_position("short"), "stop": 99.0}, "entry"),  # a short's stop below its entry
        ({**_position(), "width_bars": 0}, "width_bars"),
        (_without(_position(), "risk_pct"), "risk_pct"),
        ({**_position(), "account": -1.0}, "account"),
        ({**_anchored_vp(), "time": 1.5}, "time"),
        ({**_anchored_vp(), "time": -1}, "time"),
        (_without(_anchored_vp(), "time"), "time"),
        ({**_anchored_vp(), "rows": 1}, "rows"),
        ({**_anchored_vp(), "rows": 501}, "rows"),
        ({**_anchored_vp(), "rows": True}, "rows"),
        ({**_anchored_vp(), "value_area_pct": 0}, "value_area_pct"),
        ({**_anchored_vp(), "value_area_pct": 100.5}, "value_area_pct"),
        (_without(_anchored_vp(), "value_area_pct"), "value_area_pct"),
        ({**_anchored_vp(), "up_color": 3}, "up_color"),
        (_without(_anchored_vp(), "down_color"), "down_color"),
        ({**_anchored_vp(), "price": 1.0}, "price"),  # not a field of an anchored_vp
        ({**_anchored_vwap(), "source": "open"}, "source"),
        (_without(_anchored_vwap(), "source"), "source"),
        ({**_anchored_vwap(), "bands": 1}, "bands"),
        (_without(_anchored_vwap(), "bands"), "bands"),
        ({**_anchored_vwap(), "band_color": None}, "band_color"),
        ({**_anchored_vwap(), "time": "now"}, "time"),
        ({**_anchored_vwap(), "color": 3}, "color"),
        ({**_anchored_vwap(), "rows": 24}, "rows"),  # not a field of an anchored_vwap
    ],
)
def test_a_malformed_item_is_refused_naming_the_field(item: dict[str, Any], field: str) -> None:
    with pytest.raises(DrawingError) as raised:
        validate_drawing(item)
    assert raised.value.field == field


def test_an_item_list_error_names_the_item_and_a_repeated_id_is_refused() -> None:
    with pytest.raises(DrawingError, match=r"items\[1\]\.anchors"):
        validate_drawings([_hline(), {"kind": "fib", "id": "f"}])
    with pytest.raises(DrawingError, match=r"items\[1\]\.id"):
        validate_drawings([_hline(), _hline()])
    with pytest.raises(DrawingError, match="items"):
        validate_drawings({"kind": "hline"})


def test_the_closed_sets_mirror_the_frontend() -> None:
    root = Path(__file__).parents[2] / "frontend/src/lib"
    drawings = (root / "drawings.ts").read_text()
    kinds = re.search(r"DRAWING_KIND_NAMES: readonly string\[\] = \[([^\]]*)\]", drawings)
    assert kinds is not None
    assert tuple(re.findall(r'"(\w+)"', kinds.group(1))) == DRAWING_KINDS
    vwap = (root / "anchoredVwap.ts").read_text()
    sources = re.search(r"\bVWAP_SOURCES = \[([^\]]*)\]", vwap)
    assert sources is not None
    assert tuple(re.findall(r'"(\w+)"', sources.group(1))) == VWAP_SOURCES


def test_anchored_vwap_sources_mirror_the_frontend() -> None:
    """Story 33.6: the drawing's sources are the bar prices plus `stored`, in the client's order."""
    vwap = (Path(__file__).parents[2] / "frontend/src/lib/anchoredVwap.ts").read_text()
    sources = re.search(r"\bANCHORED_VWAP_SOURCES = \[([^\]]*)\]", vwap)
    assert sources is not None
    assert tuple(re.findall(r'"(\w+)"', sources.group(1))) == ANCHORED_VWAP_SOURCES
    assert (*VWAP_SOURCES, "stored") == ANCHORED_VWAP_SOURCES


def test_an_anchored_vwap_drawing_takes_the_stored_source(tmp_path: Path) -> None:
    item = {**_anchored_vwap(), "source": "stored"}
    assert validate_drawing(item) == item
    path = tmp_path / "chart_drawings.toml"
    save_chart_drawings({_IID: [item]}, path)
    assert load_chart_drawings(path) == {_IID: [item]}


def test_an_old_file_of_the_four_original_kinds_loads_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    items = [_hline(), _trendline(), _fib(), _position()]
    save_chart_drawings({_IID: items}, path)
    assert load_chart_drawings(path) == {_IID: items}


# -- Story 33.10: the second drawing set, lock / hide and the line look ----------------------------


def test_every_story_33_10_kind_round_trips_through_the_file_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    items = _all_kinds()
    assert [item["kind"] for item in items] == list(DRAWING_KINDS)
    save_chart_drawings({_IID: items}, path)
    assert load_chart_drawings(path) == {_IID: items}


def test_lock_hide_and_the_line_look_round_trip_on_the_kinds_that_take_them(
    tmp_path: Path,
) -> None:
    path = tmp_path / "chart_drawings.toml"
    look = {"line_width": 3, "line_style": "dotted", "locked": True, "hidden": False}
    items = [
        {**_hline(), **look},
        {**_trendline(), **look},
        {**_two_point("ray"), "line_style": "dashed"},
        {**_vline(), "line_width": 4},
        {**_rect(), **look},
        {**_channel(), "hidden": True},
        {**_text(), "locked": True, "hidden": True},
        {**_fib_extension(), "locked": False},
        {**_position(), "hidden": True},
        {**_anchored_vwap(), "locked": True},
    ]
    save_chart_drawings({_IID: items}, path)
    assert load_chart_drawings(path) == {_IID: items}


def test_an_old_file_of_the_six_pre_33_10_kinds_loads_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "chart_drawings.toml"
    items = _all_kinds()[:6]
    save_chart_drawings({_IID: items}, path)
    assert load_chart_drawings(path) == {_IID: items}
    assert all("locked" not in item and "hidden" not in item for item in items)


def test_a_text_of_the_maximum_length_and_the_font_bounds_are_kept() -> None:
    for font_size in (MIN_DRAWING_FONT_SIZE, MAX_DRAWING_FONT_SIZE):
        item = {**_text(), "text": "x" * MAX_DRAWING_TEXT_LENGTH, "font_size": font_size}
        assert validate_drawing(item) == item


def test_a_rect_takes_an_opacity_of_zero_or_one_and_a_channel_any_finite_offset() -> None:
    for opacity in (0, 1, 0.5):
        assert validate_drawing({**_rect(), "fill_opacity": opacity})["fill_opacity"] == opacity
    for offset in (0, 12.5, -0.001):
        assert validate_drawing({**_channel(), "offset": offset})["offset"] == offset


@pytest.mark.parametrize(
    ("item", "field"),
    [
        ({**_two_point("ray"), "anchors": _THREE}, "anchors"),
        ({**_two_point("extended"), "anchors": _ANCHORS[:1]}, "anchors"),
        (_without(_two_point("arrow"), "anchors"), "anchors"),
        (
            {**_two_point("price_range"), "anchors": [{"time": 1, "price": "1"}, _ANCHORS[1]]},
            "anchors",
        ),
        ({**_two_point("date_range"), "anchors": [{"time": 1}, _ANCHORS[1]]}, "anchors"),
        ({**_fib_extension(), "anchors": copy.deepcopy(_ANCHORS)}, "anchors"),
        (_without(_fib_extension(), "levels"), "levels"),
        ({**_fib_extension(), "line_width": 0}, "line_width"),
        ({**_fib_extension(), "line_style": "dashed"}, "line_style"),  # not a fib field
        ({**_two_point("ray"), "offset": 1.0}, "offset"),  # not a field of a ray
        ({**_text(), "line_width": 2}, "line_width"),  # a text has no line look
        ({**_position(), "line_style": "solid"}, "line_style"),
        ({**_anchored_vp(), "line_width": 1}, "line_width"),
        ({**_hline(), "line_width": 0}, "line_width"),
        ({**_hline(), "line_width": 5}, "line_width"),
        ({**_trendline(), "line_width": True}, "line_width"),
        ({**_trendline(), "line_width": 1.5}, "line_width"),
        ({**_two_point("arrow"), "line_style": "wavy"}, "line_style"),
        ({**_two_point("arrow"), "line_style": None}, "line_style"),
        ({**_trendline(), "locked": "yes"}, "locked"),
        ({**_fib(), "locked": 1}, "locked"),
        ({**_vline(), "hidden": None}, "hidden"),
        (_without(_vline(), "time"), "time"),
        ({**_vline(), "time": 1.5}, "time"),
        ({**_vline(), "anchors": _ANCHORS}, "anchors"),  # not a field of a vline
        (_without(_rect(), "fill_opacity"), "fill_opacity"),
        ({**_rect(), "fill_opacity": -0.1}, "fill_opacity"),
        ({**_rect(), "fill_opacity": 1.5}, "fill_opacity"),
        ({**_rect(), "fill_opacity": True}, "fill_opacity"),
        ({**_rect(), "fill_opacity": float("nan")}, "fill_opacity"),
        ({**_rect(), "anchors": _THREE}, "anchors"),
        (_without(_channel(), "offset"), "offset"),
        ({**_channel(), "offset": "1"}, "offset"),
        ({**_channel(), "offset": float("inf")}, "offset"),
        ({**_channel(), "offset": False}, "offset"),
        (_without(_text(), "anchor"), "anchor"),
        ({**_text(), "anchor": {"time": 1}}, "anchor"),
        ({**_text(), "anchor": {"time": 1.5, "price": 1.0}}, "anchor"),
        ({**_text(), "anchor": {"time": 1, "price": float("nan")}}, "anchor"),
        ({**_text(), "anchor": [1, 2]}, "anchor"),
        ({**_text(), "anchors": _ANCHORS}, "anchors"),  # a text has one `anchor`
        (_without(_text(), "text"), "text"),
        ({**_text(), "text": ""}, "text"),
        ({**_text(), "text": "   \n"}, "text"),
        ({**_text(), "text": 5}, "text"),
        ({**_text(), "text": "x" * (MAX_DRAWING_TEXT_LENGTH + 1)}, "text"),
        # A lone surrogate: no UTF-8 for the file.
        ({**_text(), "text": "half \ud83d emoji"}, "text"),
        (_without(_text(), "font_size"), "font_size"),
        ({**_text(), "font_size": MIN_DRAWING_FONT_SIZE - 1}, "font_size"),
        ({**_text(), "font_size": MAX_DRAWING_FONT_SIZE + 1}, "font_size"),
        ({**_text(), "font_size": 12.0}, "font_size"),
        ({**_text(), "font_size": True}, "font_size"),
        ({"kind": "zigzag", "id": "zigzag-1"}, "kind"),
    ],
)
def test_a_malformed_story_33_10_item_is_refused_naming_the_field(
    item: dict[str, Any], field: str
) -> None:
    with pytest.raises(DrawingError) as raised:
        validate_drawing(item)
    assert raised.value.field == field


def test_a_malformed_new_kind_in_a_list_names_the_item_and_its_field() -> None:
    with pytest.raises(DrawingError, match=r"items\[1\]\.fill_opacity"):
        validate_drawings([_hline(), {**_rect(), "fill_opacity": 2}])


def test_line_styles_mirror_the_frontend() -> None:
    root = Path(__file__).parents[2] / "frontend/src/lib"
    style = (root / "indicatorStyle.ts").read_text()
    styles = re.search(r"LINE_STYLES: readonly LineStyleName\[\] = \[([^\]]*)\]", style)
    assert styles is not None
    assert tuple(re.findall(r'"(\w+)"', styles.group(1))) == LINE_STYLES
    # The drawings re-export the one frontend definition rather than declaring a second list:
    # `export { LINE_STYLES } from "./indicatorStyle"`, or an import of it plus an `export { }`.
    drawings = (root / "drawings.ts").read_text()
    reexported = re.search(
        r'export\s*\{[^}]*\bLINE_STYLES\b[^}]*\}\s*from\s*"\./indicatorStyle"', drawings
    )
    imported = re.search(
        r'import\s*\{[^}]*\bLINE_STYLES\b[^}]*\}\s*from\s*"\./indicatorStyle"', drawings
    )
    exported = re.search(r"export\s*\{[^}]*\bLINE_STYLES\b[^}]*\}", drawings)
    assert reexported or (imported and exported)
    assert not re.search(r"\bLINE_STYLES\b[^=\n]*=", drawings)
    # One backend constant too: the derivatives' styles alias the drawings' set.
    assert DERIVATIVE_LINE_STYLES is LINE_STYLES


def test_text_and_font_bounds_mirror_the_frontend() -> None:
    drawings = (Path(__file__).parents[2] / "frontend/src/lib/drawings.ts").read_text()

    def constant(name: str) -> int:
        match = re.search(rf"export const {name} = (\d+);", drawings)
        assert match is not None, f"{name} not found"
        return int(match.group(1))

    assert constant("MAX_TEXT_LENGTH") == MAX_DRAWING_TEXT_LENGTH
    assert constant("MIN_FONT_SIZE") == MIN_DRAWING_FONT_SIZE
    assert constant("MAX_FONT_SIZE") == MAX_DRAWING_FONT_SIZE
