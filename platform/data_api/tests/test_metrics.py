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
Story 17.2/15.8: `GET /api/metrics/history/{symbol}` / `GET /api/metrics/nearest/{symbol}`
-- real `ranking_engine.metrics_store` SQLite store, no mocking (TEST-01/03).
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from ranking_engine import metrics_store

import data_api.app as app_module
import data_api.routes.metrics as metrics_routes


_IID = "BTC-USD-PERP.DYDX"

# Large, arbitrary, far-from-epoch base timestamp -- comfortably inside `history()`'s
# 31-day retention cutoff regardless of when this test runs (mirrors test_candles.py's
# own `_BASE_NS` precedent for the same reason).
_BASE_NS = 1_800_000_000_000_000_000


def _client(db_path: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(metrics_routes, "METRICS_DB_PATH", db_path)
    return TestClient(app_module.app)


def test_history_reflects_full_row_and_preserves_none_gap_verbatim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_path / "metrics.db")
    full_row = {
        "ts": _BASE_NS,
        "instrument_id": _IID,
        "price": 100.5,
        "pct_1h": 0.01,
        "pct_24h": 0.02,
        "volatility": 0.03,
        "ofi": 0.04,
        "microprice": 100.4,
        "spread": 0.1,
        "rank": 1.0,
        "volume24h": 1_000_000.0,
    }
    gap_row = {
        "ts": _BASE_NS + 60_000_000_000,
        "instrument_id": _IID,
        "price": 101.0,
        # Every other metric column deliberately omitted -- metrics_store.write() stores
        # a missing key as None via r.get(c); this is the deliberate gap case the route
        # must reflect verbatim (DATA-01/AD-F6), never as 0 or a dropped key.
    }
    metrics_store.write([full_row, gap_row], db_path)
    client = _client(db_path, monkeypatch)

    response = client.get(f"/api/metrics/history/{_IID}")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2

    first, second = items
    assert first["ts"] == _BASE_NS
    assert first["price"] == 100.5
    assert first["pct_1h"] == 0.01
    assert first["ofi"] == 0.04
    assert first["rank"] == 1.0
    assert first["volume24h"] == 1_000_000.0

    assert second["ts"] == _BASE_NS + 60_000_000_000
    assert second["price"] == 101.0
    assert second["pct_1h"] is None
    assert second["ofi"] is None
    assert second["rank"] is None
    assert second["volume24h"] is None
    assert "ofi" in second  # never dropped, only null


def test_nearest_returns_row_when_data_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "metrics.db")
    metrics_store.write([{"ts": _BASE_NS, "instrument_id": _IID, "price": 100.0}], db_path)
    client = _client(db_path, monkeypatch)

    response = client.get(f"/api/metrics/nearest/{_IID}?ts_ns={_BASE_NS}")

    assert response.status_code == 200
    body = response.json()
    assert body["ts"] == _BASE_NS
    assert body["price"] == 100.0
    assert body["ofi"] is None


def test_nearest_returns_null_when_never_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = str(tmp_path / "metrics.db")
    client = _client(db_path, monkeypatch)

    response = client.get(f"/api/metrics/nearest/{_IID}?ts_ns={_BASE_NS}")

    assert response.status_code == 200
    assert response.json() is None
