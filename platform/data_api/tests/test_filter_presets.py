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
Story 33.7: `GET`/`PUT /api/rankings/filter-presets` -- the Rankings page's named filter presets,
the real `views.preferences` writer against a temp file, no mocking of the writer.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from typing import get_args

import pytest
from fastapi.testclient import TestClient
from views.preferences import FILTER_OPERATORS

import data_api.app as app_module
import data_api.settings as settings
from data_api.routes.rankings import FilterPresetConditionItem


_URL = "/api/rankings/filter-presets"
_LEV = {"name": "lev", "conditions": [{"field": "funding_rate", "op": ">", "value": 0.0003}]}
_BYBIT = {"name": "bybit", "conditions": [{"field": "venue", "op": "=", "value": "BYBIT"}]}


def _file(tmp_path: Path) -> Path:
    return tmp_path / "screener_filter_presets.toml"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "SCREENER_FILTER_PRESETS_PATH", str(_file(tmp_path)))
    return TestClient(app_module.app)


def test_nothing_saved_yet_is_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _client(tmp_path, monkeypatch).get(_URL)
    assert (resp.status_code, resp.json()) == (200, {"presets": []})


def test_a_put_returns_the_stored_list_and_a_fresh_client_reads_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _client(tmp_path, monkeypatch).put(_URL, json={"presets": [_LEV, _BYBIT]})
    assert (resp.status_code, resp.json()) == (200, {"presets": [_LEV, _BYBIT]})
    # Another client (another browser): the state is the file, not the process.
    again = _client(tmp_path, monkeypatch).get(_URL)
    assert again.json() == {"presets": [_LEV, _BYBIT]}


def test_a_put_replaces_the_whole_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.put(_URL, json={"presets": [_LEV, _BYBIT]})
    client.put(_URL, json={"presets": [_BYBIT]})
    assert client.get(_URL).json() == {"presets": [_BYBIT]}


def test_a_stored_name_is_stripped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _client(tmp_path, monkeypatch).put(_URL, json={"presets": [{**_LEV, "name": " lev "}]})
    assert resp.json()["presets"][0]["name"] == "lev"


def _condition(**overrides: Any) -> dict[str, Any]:
    return {"field": "funding_rate", "op": ">", "value": 0.0003, **overrides}


@pytest.mark.parametrize(
    ("presets", "field"),
    [
        ([_LEV, _LEV], "presets[1].name"),  # duplicate name
        ([{**_LEV, "name": ""}], "presets[0].name"),
        ([{**_LEV, "name": "x" * 65}], "presets[0].name"),
        ([{**_LEV, "conditions": [_condition(op="!=")]}], "presets[0].conditions[0].op"),
        ([{**_LEV, "conditions": [_condition(value=True)]}], "presets[0].conditions[0].value"),
        ([{**_LEV, "conditions": [_condition(value="BYBIT")]}], "presets[0].conditions[0].value"),
        ([{**_LEV, "conditions": [_condition()] * 51}], "presets[0].conditions"),
        ([{**_LEV, "conditions": [_condition(value=2**53 + 1)]}], "presets[0].conditions[0].value"),
        ([{**_LEV, "name": f"p{i}"} for i in range(101)], "presets"),
    ],
)
def test_an_invalid_body_is_a_422_naming_the_field_and_leaves_the_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, presets: list[dict[str, Any]], field: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.put(_URL, json={"presets": [_BYBIT]})
    before = _file(tmp_path).read_bytes()

    resp = client.put(_URL, json={"presets": presets})

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith(f"{field}: ")
    assert _file(tmp_path).read_bytes() == before


def test_a_nan_value_is_a_422(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Python's json reads the bare `NaN` token a browser could never send from JSON.stringify.
    body = '{"presets": [{"name": "x", "conditions": [{"field": "f", "op": ">", "value": NaN}]}]}'
    resp = _client(tmp_path, monkeypatch).put(
        _URL, content=body, headers={"content-type": "application/json"}
    )
    assert (resp.status_code, resp.json()["detail"].split(":")[0]) == (
        422,
        "presets[0].conditions[0].value",
    )
    assert not _file(tmp_path).exists()


def test_a_body_that_is_not_the_presets_object_is_a_422_and_invalid_json_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.put(_URL, json=[_LEV])
    assert (resp.status_code, resp.json()["detail"].split(":")[0]) == (422, "presets")
    bad = client.put(_URL, content="{", headers={"content-type": "application/json"})
    assert bad.status_code == 400
    assert not _file(tmp_path).exists()


def test_a_corrupt_file_fails_the_get_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _file(tmp_path).write_text("not [ valid toml")
    resp = client.get(_URL)
    assert (resp.status_code, "screener_filter_presets.toml" in resp.json()["detail"]) == (
        500,
        True,
    )


def test_a_file_of_another_version_fails_the_get_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _file(tmp_path).write_text("v = 2\npresets = []\n")
    assert client.get(_URL).status_code == 500


def test_an_unwritable_file_is_a_500_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    def boom(_presets: object, _path: Path) -> None:
        raise PermissionError("read-only mount")

    monkeypatch.setattr("views.preferences.save_filter_presets", boom)
    resp = client.put(_URL, json={"presets": [_LEV]})
    assert resp.status_code == 500
    assert "screener_filter_presets.toml" in resp.json()["detail"]


def test_the_presets_path_derives_from_the_preferences_directory() -> None:
    drop = {"CHART_INDICATOR_CONFIG_PATH", "SCREENER_COLUMNS_CONFIG_PATH", "CHART_PREFERENCES_DIR"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "import data_api.settings as s; print(s.SCREENER_FILTER_PRESETS_PATH)",
        ],
        env={**env, "CHART_PREFERENCES_DIR": "/somewhere/prefs"},
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).parents[2],
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "/somewhere/prefs/screener_filter_presets.toml"


def test_the_api_model_operators_mirror_the_validator() -> None:
    # The response model's `op` Literal is a third copy of the operators (beside
    # `preferences.FILTER_OPERATORS` and `pages/filters.ts`): one added to the validator alone
    # would make every GET of a preset using it fail response validation.
    assert get_args(FilterPresetConditionItem.model_fields["op"].annotation) == FILTER_OPERATORS
