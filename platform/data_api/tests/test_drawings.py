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
Story 32.5: `GET`/`PUT /api/coin/{instrument_id}/drawings` and the preferences directory settings
-- the real `views.preferences` writer against a temp file, no mocking.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.settings as settings


_IID = "BTC-USD-PERP.DYDX"
_FIB = {
    "kind": "fib",
    "id": "fib-1",
    "anchors": [{"time": 1_800_000_000, "price": 100.0}, {"time": 1_800_003_600, "price": 90.0}],
    "levels": [{"ratio": 0.5, "enabled": True, "color": "#111111"}],
    "extend_right": True,
    "label_side": "right",
    "line_width": 1,
}
_LONG = {
    "kind": "position",
    "id": "position-2",
    "side": "long",
    "time": 1_800_000_000,
    "entry": 100.0,
    "stop": 99.0,
    "target": 102.0,
    "width_bars": 40,
}


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "CHART_DRAWINGS_PATH", str(tmp_path / "chart_drawings.toml"))
    return TestClient(app_module.app)


def test_nothing_saved_yet_is_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _client(tmp_path, monkeypatch).get(f"/api/coin/{_IID}/drawings")
    assert (resp.status_code, resp.json()) == (200, {"items": []})


def test_a_fib_and_a_position_survive_a_put_and_a_fresh_get(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.put(f"/api/coin/{_IID}/drawings", json={"items": [_FIB, _LONG]}).json() == {
        "ok": True
    }
    # A second client object: the state is the file, not the process.
    again = _client(tmp_path, monkeypatch).get(f"/api/coin/{_IID}/drawings")
    assert again.json() == {"items": [_FIB, _LONG]}


def test_a_put_replaces_one_instruments_list_and_keeps_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    other = "ETHUSDT-LINEAR.BYBIT"
    client.put(f"/api/coin/{_IID}/drawings", json={"items": [_FIB]})
    client.put(f"/api/coin/{other}/drawings", json={"items": [_LONG]})
    client.put(f"/api/coin/{_IID}/drawings", json={"items": []})
    assert client.get(f"/api/coin/{_IID}/drawings").json() == {"items": []}
    assert client.get(f"/api/coin/{other}/drawings").json() == {"items": [_LONG]}


def test_a_malformed_item_is_a_422_naming_the_field_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.put(
        f"/api/coin/{_IID}/drawings", json={"items": [{"kind": "fib", "id": "fib-1"}]}
    )
    assert resp.status_code == 422
    assert "anchors" in resp.json()["detail"]
    assert not (tmp_path / "chart_drawings.toml").exists()


def test_a_body_without_items_is_a_422_and_invalid_json_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    resp = client.put(f"/api/coin/{_IID}/drawings", json=[_FIB])
    assert (resp.status_code, "items" in resp.json()["detail"]) == (422, True)
    bad = client.put(f"/api/coin/{_IID}/drawings", content="{", headers={"content-type": "x/y"})
    assert bad.status_code == 400


def test_a_corrupt_file_fails_the_get_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    (tmp_path / "chart_drawings.toml").write_text("not [ valid toml")
    assert client.get(f"/api/coin/{_IID}/drawings").status_code == 500


def test_a_malformed_instrument_id_is_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/coin/BTC/drawings").status_code == 400


def _settings_run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    code = (
        "import data_api.settings as s; print(s.CHART_INDICATOR_CONFIG_PATH, s.CHART_DRAWINGS_PATH)"
    )
    return subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )


def _base_env() -> dict[str, str]:
    drop = {"CHART_INDICATOR_CONFIG_PATH", "SCREENER_COLUMNS_CONFIG_PATH", "CHART_PREFERENCES_DIR"}
    return {k: v for k, v in os.environ.items() if k not in drop}


def test_all_three_preference_paths_derive_from_the_one_directory() -> None:
    run = _settings_run({**_base_env(), "CHART_PREFERENCES_DIR": "/somewhere/prefs"})
    assert run.stdout.split() == [
        "/somewhere/prefs/chart_indicators.toml",
        "/somewhere/prefs/chart_drawings.toml",
    ]


@pytest.mark.parametrize("old", ["CHART_INDICATOR_CONFIG_PATH", "SCREENER_COLUMNS_CONFIG_PATH"])
def test_a_removed_path_variable_fails_startup_naming_the_new_one(old: str) -> None:
    run = _settings_run({**_base_env(), old: "/app/preferences/x.toml"})
    assert run.returncode != 0
    assert "CHART_PREFERENCES_DIR" in run.stderr
    assert old in run.stderr


def test_a_get_that_cannot_read_the_file_is_a_500_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    def boom(_path: Path) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr("views.preferences.load_chart_drawings", boom)
    resp = client.get(f"/api/coin/{_IID}/drawings")
    assert resp.status_code == 500
    assert "chart_drawings.toml" in resp.json()["detail"]


def test_a_put_body_that_is_not_utf8_is_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _client(tmp_path, monkeypatch).put(
        f"/api/coin/{_IID}/drawings",
        content=b"\xff\xfe{",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
