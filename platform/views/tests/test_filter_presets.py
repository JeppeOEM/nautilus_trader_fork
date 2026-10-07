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
Story 33.7: `screener_filter_presets.toml` -- the Rankings page's named filter presets, validated by
`views.preferences.validate_filter_presets` (every refusal names its field, nothing is dropped) and
written whole by `save_filter_presets`.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from views.preferences import FILTER_OPERATORS
from views.preferences import MAX_FILTER_PRESETS
from views.preferences import MAX_PRESET_CONDITIONS
from views.preferences import FilterPreset
from views.preferences import FilterPresetCondition
from views.preferences import FilterPresetError
from views.preferences import load_filter_presets
from views.preferences import save_filter_presets
from views.preferences import validate_filter_presets


def _condition(**overrides: Any) -> dict[str, Any]:
    return {"field": "funding_rate", "op": ">", "value": 0.0003, **overrides}


def _preset(name: str = "lev", *conditions: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "conditions": list(conditions) or [_condition()]}


def _body(*presets: dict[str, Any]) -> dict[str, Any]:
    return {"presets": list(presets)}


def _refusal(body: Any) -> str:
    with pytest.raises(FilterPresetError) as caught:
        validate_filter_presets(body)
    return caught.value.field


def test_a_valid_body_becomes_presets_with_the_name_stripped() -> None:
    body = _body(_preset("  lev  ", _condition(), _condition(field="venue", op="=", value="BYBIT")))

    assert validate_filter_presets(body) == [
        FilterPreset(
            name="lev",
            conditions=(
                FilterPresetCondition(field="funding_rate", op=">", value=0.0003),
                FilterPresetCondition(field="venue", op="=", value="BYBIT"),
            ),
        )
    ]


def test_presets_round_trip_through_the_file(tmp_path: Path) -> None:
    path = tmp_path / "screener_filter_presets.toml"
    presets = validate_filter_presets(
        _body(
            _preset("lev"),
            _preset("big", _condition(field="volume24h", op=">=", value=1_000_000)),
            _preset("bybit", _condition(field="venue", op="=", value="BYBIT")),
        )
    )

    save_filter_presets(presets, path)

    assert load_filter_presets(path) == presets


def test_the_written_text_is_pinned(tmp_path: Path) -> None:
    path = tmp_path / "screener_filter_presets.toml"
    presets = validate_filter_presets(
        _body(_preset("lev", _condition(), _condition(field="venue", op="=", value="BYBIT")))
    )

    save_filter_presets(presets, path)

    assert path.read_text() == (
        "v = 1\n"
        "\n"
        "[[presets]]\n"
        'name = "lev"\n'
        "conditions = [\n"
        '    { field = "funding_rate", op = ">", value = 0.0003 },\n'
        '    { field = "venue", op = "=", value = "BYBIT" },\n'
        "]\n"
    )
    assert tomllib.loads(path.read_text())["presets"][0]["conditions"][0]["value"] == 0.0003


def test_an_empty_list_is_stored_and_loads_empty(tmp_path: Path) -> None:
    path = tmp_path / "screener_filter_presets.toml"

    save_filter_presets([], path)

    assert path.read_text() == "v = 1\npresets = []\n"
    assert load_filter_presets(path) == []


def test_a_missing_file_loads_empty(tmp_path: Path) -> None:
    assert load_filter_presets(tmp_path / "absent.toml") == []


@pytest.mark.parametrize(
    "text",
    [
        "v = 2\npresets = []\n",  # another version
        "presets = []\n",  # no version
        "v = true\npresets = []\n",  # a bool version (`True == 1` in Python)
        "v = 1.0\npresets = []\n",  # a float version
        "v = 1\npresets = []\nstray = 1\n",  # a stray key
        'v = 1\n[[presets]]\nname = "x"\nconditions = []\n',  # a malformed preset
    ],
)
def test_a_file_that_is_not_a_v1_presets_file_is_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "screener_filter_presets.toml"
    path.write_text(text)

    with pytest.raises(FilterPresetError):
        load_filter_presets(path)


def test_an_unparseable_file_raises(tmp_path: Path) -> None:
    path = tmp_path / "screener_filter_presets.toml"
    path.write_text("v = = 1")

    with pytest.raises(tomllib.TOMLDecodeError):
        load_filter_presets(path)


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ([], "presets"),  # not the {"presets": [...]} object
        ({"presets": [], "extra": 1}, "presets"),
        ({"presets": {}}, "presets"),
        (_body(*[_preset(f"p{i}") for i in range(MAX_FILTER_PRESETS + 1)]), "presets"),
        ({"presets": ["lev"]}, "presets[0]"),  # a preset that is not an object
        (_body(_preset("lev"), _preset(" lev ")), "presets[1].name"),  # duplicate once stripped
        (_body(_preset("   ")), "presets[0].name"),
        (_body(_preset("x" * 65)), "presets[0].name"),
        (_body({"name": 3, "conditions": [_condition()]}), "presets[0].name"),
        (_body({"conditions": [_condition()]}), "presets[0].name"),
        (_body({**_preset(), "colour": "red"}), "presets[0].colour"),
        (_body({"name": "lev", "conditions": []}), "presets[0].conditions"),
        (
            _body(_preset("lev", *[_condition()] * (MAX_PRESET_CONDITIONS + 1))),
            "presets[0].conditions",
        ),
        (_body(_preset("lev", _condition(), _condition(op="!="))), "presets[0].conditions[1].op"),
        (_body(_preset("lev", _condition(value=float("nan")))), "presets[0].conditions[0].value"),
        (_body(_preset("lev", _condition(value=float("inf")))), "presets[0].conditions[0].value"),
        (_body(_preset("lev", _condition(value=True))), "presets[0].conditions[0].value"),
        (_body(_preset("lev", _condition(value=None))), "presets[0].conditions[0].value"),
        (_body(_preset("lev", _condition(value="BYBIT"))), "presets[0].conditions[0].value"),
        (
            _body(_preset("lev", _condition(op="=", value="x" * 513))),
            "presets[0].conditions[0].value",
        ),
        (_body(_preset("lev", _condition(value=2**53 + 1))), "presets[0].conditions[0].value"),
        (_body(_preset("lev", _condition(value=-(2**53) - 1))), "presets[0].conditions[0].value"),
        (_body(_preset("lev", _condition(value=10**400))), "presets[0].conditions[0].value"),
        (_body(_preset("lev", _condition(field=""))), "presets[0].conditions[0].field"),
        (_body(_preset("lev", _condition(field="f" * 513))), "presets[0].conditions[0].field"),
        (_body(_preset("lev", _condition(field=7))), "presets[0].conditions[0].field"),
        (_body(_preset("lev", {"field": "x", "op": ">"})), "presets[0].conditions[0].value"),
        (
            _body(_preset("lev", {**_condition(), "precision": 2})),
            "presets[0].conditions[0].precision",
        ),
    ],
)
def test_every_refusal_names_its_field(body: Any, field: str) -> None:
    assert _refusal(body) == field


def test_the_bounds_themselves_are_accepted() -> None:
    body = _body(
        _preset("x" * 64, *[_condition()] * MAX_PRESET_CONDITIONS),
        *[_preset(f"p{i}") for i in range(MAX_FILTER_PRESETS - 1)],
    )

    assert len(validate_filter_presets(body)) == MAX_FILTER_PRESETS


def test_an_integer_a_browser_number_holds_exactly_is_accepted() -> None:
    # 2**53 + 2 and 10**16 lie beyond 2**53 but are exact doubles: `JSON.parse` reads them back
    # unchanged, so only an integer a double would round (2**53 + 1) is refused.
    exact = [2**53, -(2**53), 2**53 + 2, 10**16]
    body = _body(_preset("big", *[_condition(op=">=", value=v) for v in exact]))

    assert [c.value for c in validate_filter_presets(body)[0].conditions] == exact


def test_a_refused_save_leaves_the_file_untouched(tmp_path: Path) -> None:
    path = tmp_path / "screener_filter_presets.toml"
    save_filter_presets(validate_filter_presets(_body(_preset("lev"))), path)
    before = path.read_bytes()
    bad = FilterPreset(name="", conditions=(FilterPresetCondition("f", ">", 1.0),))

    with pytest.raises(FilterPresetError):
        save_filter_presets([bad], path)

    assert path.read_bytes() == before


def test_the_operators_mirror_the_frontend() -> None:
    filters = (Path(__file__).parents[2] / "frontend/src/pages/filters.ts").read_text()
    found = re.search(r"\bFILTER_OPERATORS: FilterOperator\[\] = \[([^\]]*)\]", filters)
    assert found is not None
    assert tuple(re.findall(r'"([^"]+)"', found.group(1))) == FILTER_OPERATORS
