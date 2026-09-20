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
"""Story 15.6: `GET /api/indicators/catalog`, `GET`/`PUT /api/coin/{instrument_id}/indicators`,
`GET /api/coin/{instrument_id}/indicator-values` -- real `IndicatorEntry`/`save_config`/
`load_config` objects against a temp TOML file (no mocking of persisted-resource internals,
troll/CLAUDE.md TEST-03), real `ParquetDataCatalog`/`DydxSecondSnapshot` for the values route,
mirrors `test_candles.py`/`test_indicator_series.py`'s fixture pattern."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.routes.candles as candles_routes
import data_api.routes.indicators as indicators_routes
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.chart_indicator_config import IndicatorEntry
from ml_signals.chart_indicator_config import load_config
from ml_signals.chart_indicator_config import save_config
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"

# Same base timestamp convention as test_candles.py -- a multiple of 60s in ns.
_BASE_NS = 1_800_000_000_000_000_000
assert _BASE_NS % 60_000_000_000 == 0


def _client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, catalog_path: str | None = None,
) -> TestClient:
    monkeypatch.setattr(
        indicators_routes, "CHART_INDICATOR_CONFIG_PATH", str(tmp_path / "chart_indicators.toml"),
    )
    catalog = catalog_path or str(tmp_path / "cat")
    monkeypatch.setattr(candles_routes, "CATALOG_PATH", catalog)  # indicator-values reads candles via this route
    monkeypatch.setattr(candles_routes, "CANDLES_DB_DIR", f"{catalog}-no-candle-store-dir")
    return TestClient(app_module.app)


def _write_snapshots(catalog_path: str, entries: list[tuple[int, float]]) -> None:
    entries = sorted(entries, key=lambda e: e[0])
    ParquetDataCatalog(catalog_path).write_data([
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(_IID),
            bid_prices=[price],
            bid_sizes=[1.0],
            ask_prices=[price + 1.0],
            ask_sizes=[1.0],
            buy_volume=1.0,
            sell_volume=0.5,
            buy_count=1,
            sell_count=1,
            open_price=price,
            high_price=price,
            low_price=price,
            close_price=price,
            ts_event=ts,
            ts_init=ts,
        )
        for ts, price in entries
    ])


def test_catalog_non_empty_with_both_categories_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/indicators/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body
    categories = {entry["category"] for entry in body.values()}
    assert categories == {"native", "custom"}


def test_coin_never_configured_returns_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get(f"/api/coin/{_IID}/indicators")
    assert response.status_code == 200
    assert response.json() == []


def test_put_then_get_reflects_exactly_what_was_put(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = [
        {"name": "RelativeStrengthIndex", "params": {"period": 21}, "category": "native"},
        {"name": "CumulativeVolumeDelta", "params": {}, "category": "custom"},
    ]

    put_response = client.put(f"/api/coin/{_IID}/indicators", json=payload)
    assert put_response.status_code == 200
    assert put_response.json() == {"ok": True}

    get_response = client.get(f"/api/coin/{_IID}/indicators")
    assert get_response.status_code == 200
    assert get_response.json() == payload

    # Real load_config() against the same temp file the route just wrote, not a mock --
    # proves the write actually landed as real TOML, not just an in-process fake.
    config = load_config(Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH))
    assert config[_IID] == [
        IndicatorEntry(name="RelativeStrengthIndex", params={"period": 21}, category="native"),
        IndicatorEntry(name="CumulativeVolumeDelta", params={}, category="custom"),
    ]


def test_put_preserves_other_coins_existing_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Real save_config()/load_config() round trip seeding a second instrument's entry
    before the route's own PUT for `_IID` -- proves the route's read-modify-write doesn't
    clobber sibling instruments' persisted config."""
    client = _client(tmp_path, monkeypatch)
    path = Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH)
    other_iid = "ETH-USD-PERP.DYDX"
    save_config({other_iid: [IndicatorEntry(name="EMA", params={}, category="native")]}, path)

    put_response = client.put(
        f"/api/coin/{_IID}/indicators",
        json=[{"name": "SimpleMovingAverage", "params": {}, "category": "native"}],
    )
    assert put_response.status_code == 200

    config = load_config(path)
    assert config[other_iid] == [IndicatorEntry(name="EMA", params={}, category="native")]
    assert config[_IID] == [IndicatorEntry(name="SimpleMovingAverage", params={}, category="native")]


@pytest.mark.parametrize(
    "payload",
    [
        [{"params": {}, "category": "native"}],  # missing "name"
        [{"name": "RSI", "params": {}}],  # missing "category"
    ],
)
def test_malformed_put_payload_returns_400_not_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: list[dict],
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.put(f"/api/coin/{_IID}/indicators", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]  # a real, non-empty error message, not a bare 400


def test_malformed_put_body_not_a_json_array_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.put(
        f"/api/coin/{_IID}/indicators",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400


def test_corrupt_toml_fails_loud_with_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH).write_text("not [ valid toml")
    response = client.get(f"/api/coin/{_IID}/indicators")
    assert response.status_code == 500
    assert "corrupt" in response.json()["detail"]


def test_indicator_values_reload_reproduces_same_series(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chart-page-reload edge case (FR42): the same entry, re-fetched via the values route
    twice against the exact same window, reproduces the identical series both times -- not
    merely round-tripped in storage."""
    catalog_path = str(tmp_path / "cat")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)

    spec = json.dumps([{"name": "RelativeStrengthIndex", "params": {"period": 2}}])
    url = f"/api/coin/{_IID}/indicator-values"
    query = {
        "before_ns": _BASE_NS + 60_000_000_000, "limit": 5, "bar_seconds": 60, "entries": spec,
    }

    first = client.get(url, params=query)
    second = client.get(url, params=query)
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["items"]
    keys = {k for item in first.json()["items"] for k in item["values"]}
    assert keys == {"RelativeStrengthIndex_period=2.value"}
    # Not just key-presence/reproducibility -- prove the dispatch actually ran and
    # produced real numbers, not five None points that would "reproduce" identically too.
    values = [item["values"]["RelativeStrengthIndex_period=2.value"] for item in first.json()["items"]]
    assert any(v is not None for v in values)


def test_indicator_values_unknown_indicator_is_a_per_entry_error_not_a_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "cat")
    _write_snapshots(catalog_path, [(_BASE_NS, 100.0)])
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)
    spec = json.dumps([
        {"name": "NotARealIndicator", "params": {}},
        {"name": "SimpleMovingAverage", "params": {"period": 2}},
    ])
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000, "limit": 5, "bar_seconds": 60, "entries": spec,
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert "NotARealIndicator" in body["errors"]
    assert any(k.startswith("SimpleMovingAverage") for k in body["items"][0]["values"])


def test_put_rejects_unknown_indicator_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = [{"name": "NotARealIndicator", "params": {}, "category": "native"}]

    assert client.put(f"/api/coin/{_IID}/indicators", json=payload).status_code == 400


def test_indicator_values_dispatches_custom_indicator_via_replay_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CumulativeVolumeDelta` is only in `custom_indicators.CUSTOM_INDICATOR_CATALOG`, so a
    successful response here proves `_replay_entry`'s custom-catalog branch (the `ReplayWindow`
    path, untested by every other case in this file) actually dispatches, not just the native
    branch every other test exercises. `custom_indicators.py` reads its own module-level
    `_CATALOG_PATH` (a separate constant from this route's `CATALOG_PATH`), so both are
    monkeypatched to the same temp catalog."""
    import ml_signals.custom_indicators as custom_indicators_module

    catalog_path = str(tmp_path / "cat")
    monkeypatch.setattr(custom_indicators_module, "_CATALOG_PATH", catalog_path)
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)

    spec = json.dumps([{"name": "CumulativeVolumeDelta", "params": {}}])
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000, "limit": 5, "bar_seconds": 60, "entries": spec,
        },
    )
    assert response.status_code == 200
    keys = {k for item in response.json()["items"] for k in item["values"]}
    assert keys == {"CumulativeVolumeDelta.value"}


def test_indicator_values_too_many_entries_returns_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "cat")
    _write_snapshots(catalog_path, [(_BASE_NS, 100.0)])
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)
    spec = json.dumps([{"name": "RSI", "params": {}}] * 51)
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000, "limit": 5, "bar_seconds": 60, "entries": spec,
        },
    )
    assert response.status_code == 400


def test_indicator_values_reports_has_more_when_older_data_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Enough history precedes the requested page that `_has_more`'s probe query must find
    it -- the pagination-continues branch every other case in this file leaves untested."""
    catalog_path = str(tmp_path / "cat")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(20)]
    _write_snapshots(catalog_path, entries)
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)

    spec = json.dumps([{"name": "RelativeStrengthIndex", "params": {"period": 2}}])
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000, "limit": 3, "bar_seconds": 60, "entries": spec,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["has_more"] is True
