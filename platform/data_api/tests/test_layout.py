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
Story 32.6: `GET`/`PUT /api/coin/{instrument_id}/layout` and the default-template endpoints -- the
real `views.preferences` writers against temp files, no mocking.
"""

import copy
import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from views import preferences
from views.catalog_reads import InstrumentPrecision
from views.catalog_reads import NoInstrumentDefinition

import data_api.app as app_module
import data_api.settings as settings
from data_api.routes import indicators as indicators_routes
from data_api.routes import layout as layout_routes


_IID = "BTC-USD-PERP.DYDX"
_ETH = "ETH-USD-PERP.BYBIT"
_UNKNOWN = "NOPE-USD-PERP.DYDX"  # well-formed, but the catalog defines no such instrument
_LINE = {"kind": "hline", "id": "hline-1", "price": 100.0}
_SMA = {"name": "SimpleMovingAverage", "params": {"period": 20}, "category": "native"}
_LAYOUT_KEYS = {
    "bar_seconds",
    "mode",
    "volume",
    "crosshair",
    "pane_heights",
    "visible_bars",
    "volume_profile",
    "footprint",  # optional on the wire, always served (Story 32.8)
    "derivatives",  # likewise (Story 33.5)
    "volume_color_by",  # likewise (Story 33.6)
}


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(settings, "CHART_LAYOUTS_PATH", str(tmp_path / "chart_layouts.toml"))
    monkeypatch.setattr(settings, "CHART_DRAWINGS_PATH", str(tmp_path / "chart_drawings.toml"))
    monkeypatch.setattr(
        indicators_routes, "CHART_INDICATOR_CONFIG_PATH", str(tmp_path / "chart_indicators.toml")
    )

    def precision(_catalog: str, iid: str) -> InstrumentPrecision:
        if iid not in (_IID, _ETH):
            raise NoInstrumentDefinition(f"{iid}: no instrument definition in the catalog")
        return InstrumentPrecision(2, 4)

    monkeypatch.setattr(layout_routes, "instrument_precision", precision)
    return TestClient(app_module.app)


def _layout(**over: object) -> dict[str, object]:
    layout = dict(preferences.BUILTIN_DEFAULT_LAYOUT)
    layout.update(over)
    return layout


def _indicators(client: TestClient, iid: str) -> list[dict[str, object]]:
    return client.get(f"/api/coin/{iid}/indicators").json()


def _names(client: TestClient, iid: str) -> list[object]:
    return [e["name"] for e in _indicators(client, iid)]


def _put(client: TestClient, iid: str, layout: dict[str, object]) -> None:
    assert client.put(f"/api/coin/{iid}/layout", json={"layout": layout}).status_code == 200


def test_first_open_with_no_default_is_the_builtin_and_is_saved(client: TestClient) -> None:
    body = client.get(f"/api/coin/{_IID}/layout").json()
    assert body["seeded"] is True
    assert set(body["layout"]) == _LAYOUT_KEYS
    assert (body["layout"]["bar_seconds"], body["layout"]["mode"]) == (60, "candles")
    assert body["layout"]["volume"] is True
    assert body["layout"]["crosshair"] is True
    assert _indicators(client, _IID) == []
    again = client.get(f"/api/coin/{_IID}/layout").json()
    assert again == {"layout": body["layout"], "seeded": False}


def test_first_open_with_a_default_copies_layout_and_indicators_not_drawings(
    client: TestClient,
) -> None:
    _put(client, _IID, _layout(bar_seconds=900))
    assert client.put(f"/api/coin/{_IID}/indicators", json=[_SMA]).status_code == 200
    assert client.put(f"/api/coin/{_IID}/drawings", json={"items": [_LINE]}).status_code == 200
    client.post(f"/api/coin/{_IID}/layout/save-as-default")

    body = client.get(f"/api/coin/{_ETH}/layout").json()
    assert body["seeded"] is True
    assert body["layout"]["bar_seconds"] == 900
    assert _names(client, _ETH) == ["SimpleMovingAverage"]
    assert client.get(f"/api/coin/{_ETH}/drawings").json() == {"items": []}
    assert client.get(f"/api/coin/{_IID}/drawings").json() == {"items": [_LINE]}


def test_put_roundtrips_and_leaves_other_coins_untouched(client: TestClient) -> None:
    client.get(f"/api/coin/{_IID}/layout")
    client.get(f"/api/coin/{_ETH}/layout")
    mine = _layout(
        bar_seconds=3600, mode="lines", volume=False, pane_heights={"rsi": 220}, visible_bars=80
    )
    response = client.put(f"/api/coin/{_ETH}/layout", json={"layout": mine})
    assert response.status_code == 200
    assert response.json()["layout"]["pane_heights"] == {"rsi": 220}
    assert client.get(f"/api/coin/{_ETH}/layout").json()["layout"] == response.json()["layout"]
    assert client.get(f"/api/coin/{_IID}/layout").json()["layout"]["bar_seconds"] == 60


def test_put_with_a_bad_mode_is_a_422_naming_it_and_writes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.put(f"/api/coin/{_IID}/layout", json={"layout": _layout(mode="bars")})
    assert response.status_code == 422
    assert "mode" in response.json()["detail"]
    assert not (tmp_path / "chart_layouts.toml").exists()


def test_put_with_a_bad_volume_color_by_is_a_422_naming_it(client: TestClient) -> None:
    """Story 33.6: the optional key takes `direction` or `delta` only."""
    layout = _layout(volume_color_by="rainbow")
    response = client.put(f"/api/coin/{_IID}/layout", json={"layout": layout})
    assert response.status_code == 422
    assert "volume_color_by" in response.json()["detail"]
    saved = client.put(
        f"/api/coin/{_IID}/layout", json={"layout": _layout(volume_color_by="delta")}
    )
    assert saved.json()["layout"]["volume_color_by"] == "delta"


def test_put_with_a_missing_body_key_or_bad_json(client: TestClient) -> None:
    assert client.put(f"/api/coin/{_IID}/layout", json={"nope": 1}).status_code == 422
    bad = client.put(f"/api/coin/{_IID}/layout", content=b"{not json")
    assert bad.status_code == 400


def test_save_as_default_copies_the_layout_and_indicator_list(
    client: TestClient, tmp_path: Path
) -> None:
    client.get(f"/api/coin/{_IID}/layout")
    _put(client, _IID, _layout(bar_seconds=300))
    client.put(f"/api/coin/{_IID}/indicators", json=[_SMA])
    response = client.post(f"/api/coin/{_IID}/layout/save-as-default")
    assert response.status_code == 200
    assert response.json()["default"]["bar_seconds"] == 300
    raw = tomllib.loads((tmp_path / "chart_layouts.toml").read_text())
    assert raw["default"]["default_indicators"][0]["name"] == "SimpleMovingAverage"


def test_save_as_default_before_any_layout_is_a_404(client: TestClient) -> None:
    assert client.post(f"/api/coin/{_IID}/layout/save-as-default").status_code == 404


def test_reset_replaces_layout_and_indicators_but_keeps_drawings(client: TestClient) -> None:
    client.get(f"/api/coin/{_ETH}/layout")
    _put(client, _ETH, _layout(bar_seconds=900))
    client.put(f"/api/coin/{_ETH}/indicators", json=[_SMA])
    client.post(f"/api/coin/{_ETH}/layout/save-as-default")

    client.get(f"/api/coin/{_IID}/layout")
    _put(client, _IID, _layout(bar_seconds=86400, mode="lines"))
    client.put(f"/api/coin/{_IID}/drawings", json={"items": [_LINE]})
    response = client.post(f"/api/coin/{_IID}/layout/reset-to-default")
    assert response.status_code == 200
    assert response.json()["layout"]["bar_seconds"] == 900
    assert response.json()["layout"]["mode"] == "candles"
    assert client.get(f"/api/coin/{_IID}/layout").json()["layout"]["bar_seconds"] == 900
    assert _names(client, _IID) == ["SimpleMovingAverage"]
    assert client.get(f"/api/coin/{_IID}/drawings").json() == {"items": [_LINE]}


def test_reset_with_no_default_is_the_builtin_and_clears_indicators(client: TestClient) -> None:
    client.get(f"/api/coin/{_IID}/layout")
    _put(client, _IID, _layout(bar_seconds=900))
    client.put(f"/api/coin/{_IID}/indicators", json=[_SMA])
    layout = client.post(f"/api/coin/{_IID}/layout/reset-to-default").json()["layout"]
    assert layout["bar_seconds"] == 60
    assert _indicators(client, _IID) == []


def test_a_stale_stored_timeframe_is_returned_as_stored(client: TestClient, tmp_path: Path) -> None:
    client.get(f"/api/coin/{_IID}/layout")
    path = tmp_path / "chart_layouts.toml"
    path.write_text(path.read_text().replace("bar_seconds = 60", "bar_seconds = 30"))
    body = client.get(f"/api/coin/{_IID}/layout").json()
    assert body["layout"]["bar_seconds"] == 30
    assert body["seeded"] is False
    # Other coins can still be saved, and saving the stale coin strictly is a 422.
    assert client.get(f"/api/coin/{_ETH}/layout").status_code == 200
    assert client.post(f"/api/coin/{_IID}/layout/save-as-default").status_code == 422


def test_an_invalid_default_indicator_fails_the_seed_loudly_and_writes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    client.get(f"/api/coin/{_IID}/layout")
    layouts = preferences.load_chart_layouts(tmp_path / "chart_layouts.toml")
    layouts.default = layouts.layouts[_IID]
    layouts.default_indicators = [preferences.IndicatorEntry("NoSuchThing", {}, "native")]
    preferences.save_chart_layouts(layouts, tmp_path / "chart_layouts.toml")
    assert client.get(f"/api/coin/{_ETH}/layout").status_code == 500
    assert _ETH not in preferences.load_chart_layouts(tmp_path / "chart_layouts.toml").layouts


def test_a_corrupt_file_is_a_500_and_a_malformed_id_a_400(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "chart_layouts.toml").write_text("not [ valid toml")
    assert client.get(f"/api/coin/{_IID}/layout").status_code == 500
    assert client.get("/api/coin/BTC/layout").status_code == 400
    assert client.put("/api/coin/BTC/layout", json={"layout": {}}).status_code == 400
    assert client.post("/api/coin/BTC/layout/reset-to-default").status_code == 400
    assert client.post("/api/coin/BTC/layout/save-as-default").status_code == 400


def test_first_open_keeps_a_pre_layout_coins_existing_indicators(client: TestClient) -> None:
    other = {"name": "ExponentialMovingAverage", "params": {"period": 9}, "category": "native"}
    client.put(f"/api/coin/{_IID}/indicators", json=[_SMA])
    client.post(f"/api/coin/{_IID}/layout/save-as-default")  # 404: no layout yet
    client.get(f"/api/coin/{_IID}/layout")  # seeds a layout for _IID
    client.post(f"/api/coin/{_IID}/layout/save-as-default")  # default_indicators = [SMA]
    # ETH predates layouts: it has indicators but no layout table.
    client.put(f"/api/coin/{_ETH}/indicators", json=[other])
    body = client.get(f"/api/coin/{_ETH}/layout").json()
    assert body["seeded"] is True
    assert _names(client, _ETH) == ["ExponentialMovingAverage"]
    # Reset still replaces.
    client.post(f"/api/coin/{_ETH}/layout/reset-to-default")
    assert _names(client, _ETH) == ["SimpleMovingAverage"]


def test_a_stale_default_never_blocks_a_coin_whose_indicators_it_would_not_touch(
    client: TestClient, tmp_path: Path
) -> None:
    client.get(f"/api/coin/{_IID}/layout")
    layouts = preferences.load_chart_layouts(tmp_path / "chart_layouts.toml")
    layouts.default = layouts.layouts[_IID]
    layouts.default_indicators = [preferences.IndicatorEntry("NoSuchThing", {}, "native")]
    preferences.save_chart_layouts(layouts, tmp_path / "chart_layouts.toml")
    client.put(f"/api/coin/{_ETH}/indicators", json=[_SMA])  # ETH predates layouts
    body = client.get(f"/api/coin/{_ETH}/layout").json()
    assert body["seeded"] is True
    assert _names(client, _ETH) == ["SimpleMovingAverage"]


def test_a_malformed_indicator_file_is_a_500_with_a_detail(
    client: TestClient, tmp_path: Path
) -> None:
    (tmp_path / "chart_indicators.toml").write_text('[[ "ETH-USD-PERP.BYBIT" ]]\nx = 1\n')
    response = client.get(f"/api/coin/{_ETH}/layout")
    assert response.status_code == 500
    assert "corrupt" in response.json()["detail"]


def test_save_as_default_refuses_an_invalid_indicator_list_and_writes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    client.get(f"/api/coin/{_IID}/layout")
    before = (tmp_path / "chart_layouts.toml").read_bytes()
    bad = tmp_path / "chart_indicators.toml"
    bad.write_text('[[ "BTC-USD-PERP.DYDX" ]]\nname = "NoSuchThing"\ncategory = "native"\n')
    response = client.post(f"/api/coin/{_IID}/layout/save-as-default")
    assert response.status_code == 422
    assert "NoSuchThing" in response.json()["detail"]
    assert (tmp_path / "chart_layouts.toml").read_bytes() == before


def test_an_id_without_an_instrument_definition_is_a_404_and_writes_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    assert client.get(f"/api/coin/{_UNKNOWN}/layout").status_code == 404
    assert client.put(f"/api/coin/{_UNKNOWN}/layout", json={"layout": _layout()}).status_code == 404
    assert client.post(f"/api/coin/{_UNKNOWN}/layout/reset-to-default").status_code == 404
    assert not (tmp_path / "chart_layouts.toml").exists()
    assert not (tmp_path / "chart_indicators.toml").exists()


def test_put_with_an_unknown_footprint_key_is_a_422_naming_it(
    client: TestClient, tmp_path: Path
) -> None:
    footprint = {**preferences.FOOTPRINT_DEFAULTS, "glow": True}
    response = client.put(f"/api/coin/{_IID}/layout", json={"layout": _layout(footprint=footprint)})
    assert response.status_code == 422
    assert response.json()["detail"].startswith("footprint.glow:")
    assert not (tmp_path / "chart_layouts.toml").exists()


def test_put_without_a_footprint_serves_footprint_off(client: TestClient) -> None:
    layout = _layout()
    del layout["footprint"]
    response = client.put(f"/api/coin/{_IID}/layout", json={"layout": layout})
    assert response.status_code == 200
    assert response.json()["layout"]["footprint"]["on"] is False


def test_put_with_a_bad_derivatives_flag_is_a_422_naming_it(client: TestClient) -> None:
    derivatives = copy.deepcopy(preferences.DERIVATIVES_DEFAULTS)
    derivatives["oi"]["on"] = "yes"
    response = client.put(
        f"/api/coin/{_IID}/layout", json={"layout": _layout(derivatives=derivatives)}
    )
    assert response.status_code == 422
    assert response.json()["detail"].startswith("derivatives.oi.on:")


def test_put_roundtrips_derivatives_settings_and_their_pane_heights(client: TestClient) -> None:
    derivatives = copy.deepcopy(preferences.DERIVATIVES_DEFAULTS)
    derivatives["oi"] = {"on": True, "style": {"oi": {"color": "#26a69a", "line_width": 2}}}
    derivatives["liquidations"] = {"on": True, "measure": "notional", "markers": False}
    layout = _layout(derivatives=derivatives, pane_heights={"price": 400, "deriv_oi": 140})
    client.put(f"/api/coin/{_IID}/layout", json={"layout": layout})
    served = client.get(f"/api/coin/{_IID}/layout").json()["layout"]
    assert (served["derivatives"], served["pane_heights"]) == (
        derivatives,
        {"price": 400, "deriv_oi": 140},
    )


def test_put_without_derivatives_serves_every_entry_off(client: TestClient) -> None:
    layout = _layout()
    del layout["derivatives"]
    client.put(f"/api/coin/{_IID}/layout", json={"layout": layout})
    served = client.get(f"/api/coin/{_IID}/layout").json()["layout"]
    assert served["derivatives"] == preferences.DERIVATIVES_DEFAULTS


def test_put_roundtrips_footprint_settings(client: TestClient) -> None:
    footprint = {
        "on": True,
        "row_ticks": 5,
        "mode": "delta",
        "imbalance_ratio": 2.5,
        "text": False,
        "buy_color": "#26a69a",
    }
    client.put(f"/api/coin/{_IID}/layout", json={"layout": _layout(footprint=footprint)})
    assert client.get(f"/api/coin/{_IID}/layout").json()["layout"]["footprint"] == footprint
