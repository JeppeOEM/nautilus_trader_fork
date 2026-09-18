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
"""Story 17.5: screener-wide Technicals column persistence + bulk latest-indicator-values route.

Real TOML file, real `ParquetDataCatalog`/`DydxSecondSnapshot`, real indicator replay (no mocking,
troll/CLAUDE.md TEST-03). The values test cross-checks against the chart's own per-coin
indicator-values route for the same instrument/params: same computation, different destination.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.routes.indicators as indicators_routes
import data_api.routes.rankings as rankings_routes
from data_api import redis_bus
from data_api.redis_bus import RankingsBus
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_MINUTE_NS = 60_000_000_000


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    catalog = str(tmp_path / "cat")
    monkeypatch.setattr(rankings_routes, "SCREENER_COLUMNS_CONFIG_PATH", str(tmp_path / "cols.toml"))
    monkeypatch.setattr(rankings_routes, "CATALOG_PATH", catalog)
    monkeypatch.setattr(indicators_routes, "CATALOG_PATH", catalog)
    return TestClient(app_module.app)


def _seed_recent_minutes(tmp_path: Path, count: int = 45) -> None:
    """One trade-carrying snapshot per minute ending just before now -> `count` 1m candles."""
    end = time.time_ns() // _MINUTE_NS * _MINUTE_NS
    ParquetDataCatalog(str(tmp_path / "cat")).write_data([
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(_IID),
            bid_prices=[100.0], bid_sizes=[1.0], ask_prices=[101.0], ask_sizes=[1.0],
            buy_volume=1.0, sell_volume=0.5, buy_count=1, sell_count=1,
            open_price=price, high_price=price + 0.5, low_price=price - 0.5, close_price=price,
            ts_event=ts, ts_init=ts,
        )
        for i in range(count)
        for ts, price in [(end - (count - i) * _MINUTE_NS, 100.0 + (i * 7) % 13 + i * 0.1)]
    ])


def test_columns_default_to_empty_and_roundtrip_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/rankings/technicals-columns").json() == []

    cols = [
        {"name": "RelativeStrengthIndex", "params": {"period": 7}, "category": "native"},
        {"name": "MovingAverageConvergenceDivergence", "params": {}, "category": "native"},
    ]
    assert client.put("/api/rankings/technicals-columns", json=cols).json() == {"ok": True}
    assert client.get("/api/rankings/technicals-columns").json() == cols

    assert client.put("/api/rankings/technicals-columns", json=cols[::-1]).status_code == 200
    assert [c["name"] for c in client.get("/api/rankings/technicals-columns").json()] == [
        "MovingAverageConvergenceDivergence", "RelativeStrengthIndex",
    ]


def test_put_rejects_malformed_columns_with_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.put("/api/rankings/technicals-columns", json=[{"params": {}}])
    assert response.status_code == 400


def test_columns_are_independent_of_per_coin_indicator_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indicators_routes, "CHART_INDICATOR_CONFIG_PATH", str(tmp_path / "chart.toml"))
    client = _client(tmp_path, monkeypatch)
    client.put(f"/api/coin/{_IID}/indicators", json=[{"name": "AverageTrueRange", "category": "native"}])
    assert client.get("/api/rankings/technicals-columns").json() == []


def test_values_503_before_first_rankings_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redis_bus, "bus", RankingsBus())
    client = _client(tmp_path, monkeypatch)
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}}])
    assert client.get("/api/rankings/technicals-values", params={"entries": entries}).status_code == 503


def test_values_match_the_chart_route_for_single_and_multi_value_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_recent_minutes(tmp_path)
    bus = RankingsBus()
    bus.latest = {"mode": "volume", "updated_at": 1, "ranks": [{"instrument_id": _IID}, {"instrument_id": "NEW-USD-PERP.DYDX"}]}
    monkeypatch.setattr(redis_bus, "bus", bus)
    client = _client(tmp_path, monkeypatch)
    entries = json.dumps([
        {"name": "RelativeStrengthIndex", "params": {"period": 14}},
        {"name": "MovingAverageConvergenceDivergence", "params": {}},
    ])

    got = client.get("/api/rankings/technicals-values", params={"entries": entries}).json()["values"]

    chart = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={"before_ns": time.time_ns() + 3_600 * 1_000_000_000, "entries": entries, "limit": 120},
    ).json()["items"]
    # Chart keys are "{indicator_id}.{attr}"; this route keys by entry position instead.
    expected = {}
    for key, value in chart[-1]["values"].items():
        attr = key.rsplit(".", 1)[1]
        index = 0 if key.startswith("RelativeStrengthIndex") else 1
        expected[f"{index}.{attr}"] = value
    assert got[_IID] == expected
    assert len(got[_IID]) > 1  # MACD's multiple outputs fan out beside RSI's single one
    assert got["NEW-USD-PERP.DYDX"] == {}  # no catalog data: honest empty, never a fabricated value


def test_values_reject_bad_entries_with_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = RankingsBus()
    bus.latest = {"mode": "volume", "updated_at": 1, "ranks": []}
    monkeypatch.setattr(redis_bus, "bus", bus)
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/rankings/technicals-values", params={"entries": "not json"}).status_code == 400
