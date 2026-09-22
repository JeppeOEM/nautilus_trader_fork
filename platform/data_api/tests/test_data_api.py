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
"""Integration tests: each route is a thin wrapper matching its wrapped function's output verbatim."""

from pathlib import Path

import pytest
from collector_core.second_snapshot import DydxSecondSnapshot
from fastapi.testclient import TestClient
from ml_signals import catalog_stats as _catalog_stats
from ml_signals import chart_data as _chart_data
from ranking_engine import metrics_store

import data_api.app as app_module
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"


def _client(catalog_path: str, metrics_db_path: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(app_module, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(app_module, "METRICS_DB_PATH", metrics_db_path)
    return TestClient(app_module.app)


def _write_snapshot(
    catalog_path: str,
    ts: int,
    bid_price: float = 100.0,
    ask_price: float = 101.0,
    close_price: float | None = 100.5,
) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(_IID),
                bid_prices=[bid_price],
                bid_sizes=[1.0],
                ask_prices=[ask_price],
                ask_sizes=[1.0],
                buy_volume=1.0,
                sell_volume=0.5,
                buy_count=1,
                sell_count=1,
                open_price=close_price,
                high_price=close_price,
                low_price=close_price,
                close_price=close_price,
                ts_event=ts,
                ts_init=ts,
            )
        ]
    )


def _metrics_row(ts: int, price: float = 100.0) -> dict:
    return {
        "ts": ts,
        "instrument_id": _IID,
        "price": price,
        "pct_1h": 1.0,
        "pct_24h": 2.0,
        "volatility": 0.1,
        "ofi": 0.0,
        "microprice": price,
        "spread": 0.5,
        "rank": 1.0,
        "volume24h": 1000.0,
    }


def test_metrics_history_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "metrics.db")
    metrics_store.write(
        [_metrics_row(1_000_000_000), _metrics_row(2_000_000_000, price=101.0)], db_path
    )
    client = _client(str(tmp_path / "catalog"), db_path, monkeypatch)

    response = client.get(f"/metrics/history/{_IID}?days=31")

    assert response.status_code == 200
    assert response.json() == metrics_store.history(_IID, db_path, 31)


def test_metrics_nearest_route_happy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "metrics.db")
    metrics_store.write([_metrics_row(1_000_000_000)], db_path)
    client = _client(str(tmp_path / "catalog"), db_path, monkeypatch)

    response = client.get(f"/metrics/nearest/{_IID}?ts_ns=1500000000")

    assert response.status_code == 200
    assert response.json() == metrics_store.nearest(_IID, 1_500_000_000, db_path)


def test_metrics_nearest_route_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "metrics.db")
    client = _client(str(tmp_path / "catalog"), db_path, monkeypatch)

    response = client.get(f"/metrics/nearest/{_IID}?ts_ns=1000000000")

    assert response.status_code == 200
    assert response.json() is None


def test_catalog_chart_series_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshot(catalog_path, ts=1_000_000_000, bid_price=100.0, ask_price=101.0)
    _write_snapshot(catalog_path, ts=2_000_000_000, bid_price=100.5, ask_price=101.5)
    client = _client(catalog_path, str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"/catalog/chart-series/{_IID}?start_ns=0&end_ns=3000000000")

    assert response.status_code == 200
    assert response.json() == _chart_data.compute_chart_series(catalog_path, _IID, 0, 3_000_000_000)


def test_catalog_snapshots_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshot(
        catalog_path, ts=1_000_000_000, bid_price=100.0, ask_price=101.0, close_price=100.5
    )
    client = _client(catalog_path, str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"/catalog/snapshots/{_IID}?start_ns=0&end_ns=2000000000")

    assert response.status_code == 200
    expected = [
        {
            "bid_prices": s.bid_prices,
            "bid_sizes": s.bid_sizes,
            "ask_prices": s.ask_prices,
            "ask_sizes": s.ask_sizes,
            "buy_volume": s.buy_volume,
            "sell_volume": s.sell_volume,
            "ts_event": s.ts_event,
            "open_price": s.open_price,
            "high_price": s.high_price,
            "low_price": s.low_price,
            "close_price": s.close_price,
        }
        for s in _catalog_stats.query_second_snapshots(catalog_path, _IID, 0, 2_000_000_000)
    ]
    assert response.json() == expected


def test_errors_route_reports_the_ledger() -> None:
    """Fixture test on the old response shape (story 23.3 AC #6): `counts`/`last` unchanged."""
    from fastapi.testclient import TestClient
    from observability import error_ledger

    import data_api.app as app_module

    error_ledger.reset()
    client = TestClient(app_module.app)
    empty = client.get("/api/errors").json()
    assert empty["counts"] == {}
    assert empty["last"] == {}
    error_ledger.record("test.site", "boom")
    body = client.get("/api/errors").json()
    assert body["counts"] == {"test.site": 1} and body["last"] == {"test.site": "boom"}
    error_ledger.reset()


def test_errors_route_services_block_reads_durable_ledgers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Story 23.3 AC #6: `services` summarises every ledger file under `ERROR_LEDGER_DIR`."""
    import json

    from fastapi.testclient import TestClient
    from observability import error_ledger

    import data_api.app as app_module

    lines = [
        {"ts_ns": 10, "site": "process_start", "suppressed": 0},
        {"ts_ns": 20, "site": "collector.book_sequence", "suppressed": 0},
        {"ts_ns": 30, "site": "collector.book_sequence", "suppressed": 1},
    ]
    errors_dir = tmp_path / "errors"
    errors_dir.mkdir()
    (errors_dir / "collector.jsonl").write_text("".join(json.dumps(r) + "\n" for r in lines))
    monkeypatch.setattr(app_module, "ERROR_LEDGER_DIR", str(errors_dir))

    error_ledger.reset()
    client = TestClient(app_module.app)
    body = client.get("/api/errors").json()
    assert set(body["services"]) == {"collector"}
    summary = body["services"]["collector"]
    assert summary["last_start_ns"] == 10
    assert summary["since_start"] == {"collector.book_sequence": 3}
    assert summary["since"] is None

    body = client.get("/api/errors", params={"since_ns": 25}).json()
    assert body["services"]["collector"]["since"] == {"collector.book_sequence": 2}
    error_ledger.reset()


def test_errors_route_services_block_is_empty_without_a_ledger_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing `ERROR_LEDGER_DIR` is an empty `services` block, never an error."""
    from fastapi.testclient import TestClient
    from observability import error_ledger

    import data_api.app as app_module

    monkeypatch.setattr(app_module, "ERROR_LEDGER_DIR", str(tmp_path / "missing"))
    error_ledger.reset()
    client = TestClient(app_module.app)
    assert client.get("/api/errors").json()["services"] == {}
    error_ledger.reset()
