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
Story 33.12: `GET`/`PUT /api/watchlist` -- the chart page's pinned instruments, the real
`views.preferences` writer against a temp file, no mocking of the writer.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.settings as settings


_URL = "/api/watchlist"
_BYBIT_BTC = "BTCUSDT-LINEAR.BYBIT"
_HL_SOL = "SOL-USD-PERP.HYPERLIQUID"


def _file(tmp_path: Path) -> Path:
    return tmp_path / "chart_watchlist.toml"


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "CHART_WATCHLIST_PATH", str(_file(tmp_path)))
    return TestClient(app_module.app)


def test_nothing_pinned_yet_is_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _client(tmp_path, monkeypatch).get(_URL)

    assert (resp.status_code, resp.json()) == (200, {"instruments": []})
    assert not _file(tmp_path).exists()


def test_a_put_returns_the_stored_list_and_a_fresh_client_reads_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _client(tmp_path, monkeypatch).put(_URL, json={"instruments": [_HL_SOL, _BYBIT_BTC]})

    assert (resp.status_code, resp.json()) == (200, {"instruments": [_HL_SOL, _BYBIT_BTC]})
    # Another client (another browser): the state is the file, not the process.
    again = _client(tmp_path, monkeypatch).get(_URL)
    assert again.json() == {"instruments": [_HL_SOL, _BYBIT_BTC]}


def test_a_put_replaces_the_whole_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    client.put(_URL, json={"instruments": [_HL_SOL, _BYBIT_BTC]})
    client.put(_URL, json={"instruments": [_BYBIT_BTC]})

    assert client.get(_URL).json() == {"instruments": [_BYBIT_BTC]}


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ([_BYBIT_BTC], "instruments"),  # not the {"instruments": [...]} object
        ({"instruments": [], "extra": 1}, "instruments"),
        ({"instruments": _BYBIT_BTC}, "instruments"),
        ({"instruments": [_BYBIT_BTC, _BYBIT_BTC]}, "instruments[1]"),  # a duplicate
        ({"instruments": [_HL_SOL, "BTCUSDT"]}, "instruments[1]"),  # no .VENUE suffix
        ({"instruments": [3]}, "instruments[0]"),
        ({"instruments": ["A" * 600 + ".X"]}, "instruments[0]"),  # over 512 characters
        ({"instruments": [f"C{i}-USD-PERP.HYPERLIQUID" for i in range(201)]}, "instruments"),
    ],
)
def test_an_invalid_body_is_a_422_naming_the_entry_and_leaves_the_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: Any, field: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    client.put(_URL, json={"instruments": [_HL_SOL]})
    before = _file(tmp_path).read_bytes()

    resp = client.put(_URL, json=body)

    assert resp.status_code == 422
    assert resp.json()["detail"].startswith(f"{field}: ")
    assert _file(tmp_path).read_bytes() == before


def test_invalid_json_is_a_400_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resp = _client(tmp_path, monkeypatch).put(
        _URL, content="{", headers={"content-type": "application/json"}
    )

    assert resp.status_code == 400
    assert not _file(tmp_path).exists()


@pytest.mark.parametrize(
    "text",
    ["not [ valid toml", "v = 2\ninstruments = []\n", 'v = 1\ninstruments = ["BTCUSDT"]\n'],
)
def test_a_corrupt_file_fails_the_get_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    _file(tmp_path).write_text(text)

    resp = client.get(_URL)

    assert resp.status_code == 500
    assert resp.json()["detail"].startswith("chart_watchlist.toml is corrupt: ")


def test_a_corrupt_file_fails_the_put_loudly_and_is_left_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _file(tmp_path).write_text("not [ valid toml")

    resp = client.put(_URL, json={"instruments": [_BYBIT_BTC]})

    assert resp.status_code == 500
    assert resp.json()["detail"].startswith("chart_watchlist.toml is corrupt: ")
    assert _file(tmp_path).read_text() == "not [ valid toml"


def test_an_unwritable_file_is_a_500_naming_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    def boom(_ids: object, _path: Path) -> None:
        raise PermissionError("read-only mount")

    monkeypatch.setattr("views.preferences.save_watchlist", boom)
    resp = client.put(_URL, json={"instruments": [_BYBIT_BTC]})

    assert resp.status_code == 500
    assert resp.json()["detail"].startswith("failed to write chart_watchlist.toml: ")


def test_the_watchlist_path_derives_from_the_preferences_directory() -> None:
    drop = {"CHART_INDICATOR_CONFIG_PATH", "SCREENER_COLUMNS_CONFIG_PATH", "CHART_PREFERENCES_DIR"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    run = subprocess.run(
        [sys.executable, "-c", "import data_api.settings as s; print(s.CHART_WATCHLIST_PATH)"],
        env={**env, "CHART_PREFERENCES_DIR": "/somewhere/prefs"},
        capture_output=True,
        text=True,
        check=False,
        cwd=Path(__file__).parents[2],
    )

    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "/somewhere/prefs/chart_watchlist.toml"
