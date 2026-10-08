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

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kernel.clocks import NS_PER_S
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger
from ranking.__main__ import settings_from_env
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from views import catalog_reads
from views import coin_detail

import data_api.app as app_module
from data_api import settings
from data_api.routes.snapshots import MAX_SNAPSHOTS_LIMIT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_CATALOG_ROUTES = ("/catalog/snapshots",)
_CAP_NS = MAX_SNAPSHOTS_LIMIT * NS_PER_S
_MAX_DAYS = coin_detail.METRICS_HISTORY_MAX_DAYS


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
            make_snapshot(
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
    store = SqliteMetricsStore(db_path)
    store.write([_metrics_row(1_000_000_000), _metrics_row(2_000_000_000, price=101.0)])
    client = _client(str(tmp_path / "catalog"), db_path, monkeypatch)

    response = client.get(f"/metrics/history/{_IID}?days=31")

    assert response.status_code == 200
    assert response.json() == store.history(_IID, 31)
    store.close()


def test_metrics_nearest_route_happy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "metrics.db")
    store = SqliteMetricsStore(db_path)
    store.write([_metrics_row(1_000_000_000)])
    client = _client(str(tmp_path / "catalog"), db_path, monkeypatch)

    response = client.get(f"/metrics/nearest/{_IID}?ts_ns=1500000000")

    assert response.status_code == 200
    assert response.json() == store.nearest(_IID, 1_500_000_000)
    store.close()


def test_metrics_nearest_route_no_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = str(tmp_path / "metrics.db")
    client = _client(str(tmp_path / "catalog"), db_path, monkeypatch)

    response = client.get(f"/metrics/nearest/{_IID}?ts_ns=1000000000")

    assert response.status_code == 200
    assert response.json() is None


def test_catalog_snapshots_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshot(
        catalog_path, ts=1_000_000_000, bid_price=100.0, ask_price=101.0, close_price=100.5
    )
    client = _client(catalog_path, str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"/catalog/snapshots/{_IID}?start_ns=0&end_ns=2000000000")

    assert response.status_code == 200
    # The kernel's wire dicts, integers and precisions passed through (Story 30.2).
    expected = [
        DydxSecondSnapshot.to_dict(s)
        for s in catalog_reads.query_second_snapshots(catalog_path, _IID, 0, 2_000_000_000)
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
    # The exact top-level shape is part of the contract: a renamed or extra field must fail here,
    # not only the per-field claims below.
    assert set(empty) == {"counts", "last", "services"}
    assert empty["counts"] == {}
    assert empty["last"] == {}
    error_ledger.record("test.site", "boom")
    body = client.get("/api/errors").json()
    assert set(body) == {"counts", "last", "services"}
    assert body["counts"] == {"test.site": 1}
    assert body["last"] == {"test.site": "boom"}
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


@pytest.fixture
def _clean_ledger() -> Iterator[None]:
    """Start with an empty process-global ledger and leave it empty, even after a failure."""
    error_ledger.reset()
    yield
    error_ledger.reset()


def _refuse_read(*_args: object) -> None:
    raise AssertionError("a rejected request must not reach the catalog")


def _no_catalog_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module.coin_detail, "catalog_snapshot_rows", _refuse_read)


def _raise_os_error(*_args: object) -> None:
    raise OSError("disk unreadable")


@pytest.mark.parametrize("route", _CATALOG_ROUTES)
def test_catalog_route_rejects_a_reversed_window(
    route: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_catalog_reads(monkeypatch)
    client = _client(str(tmp_path / "catalog"), str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"{route}/{_IID}?start_ns=2000000000&end_ns=1000000000")

    assert response.status_code == 422
    assert "start_ns" in response.json()["detail"]
    assert "end_ns" in response.json()["detail"]


@pytest.mark.parametrize("route", _CATALOG_ROUTES)
def test_catalog_route_rejects_a_window_wider_than_the_cap(
    route: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_catalog_reads(monkeypatch)
    client = _client(str(tmp_path / "catalog"), str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"{route}/{_IID}?start_ns=0&end_ns={_CAP_NS}")

    assert response.status_code == 422
    assert f"{MAX_SNAPSHOTS_LIMIT} s" in response.json()["detail"]


@pytest.mark.parametrize("route", _CATALOG_ROUTES)
def test_catalog_route_serves_the_widest_window_under_the_cap(
    route: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshot(catalog_path, ts=1_000_000_000)
    client = _client(catalog_path, str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"{route}/{_IID}?start_ns=0&end_ns={_CAP_NS - 1}")

    assert response.status_code == 200
    assert len(response.json()) == 1


@pytest.mark.parametrize("route", _CATALOG_ROUTES)
def test_catalog_route_reads_at_the_highest_accepted_timestamp(
    route: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshot(catalog_path, ts=1_000_000_000)
    client = _client(catalog_path, str(tmp_path / "metrics.db"), monkeypatch)
    max_ts = app_module._MAX_TS_NS

    response = client.get(f"{route}/{_IID}?start_ns={max_ts}&end_ns={max_ts}")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("route", _CATALOG_ROUTES)
@pytest.mark.parametrize(
    ("start_ns", "end_ns"),
    [
        (-1, 1_000_000_000),
        (app_module._MAX_TS_NS + 1, app_module._MAX_TS_NS + 1),
        (2**63 - 2, 2**63 - 1),
        (10**30, 10**30),
    ],
)
def test_catalog_route_rejects_timestamps_outside_the_readable_range(
    route: str, start_ns: int, end_ns: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_catalog_reads(monkeypatch)
    client = _client(str(tmp_path / "catalog"), str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"{route}/{_IID}?start_ns={start_ns}&end_ns={end_ns}")

    assert response.status_code == 422


def test_catalog_snapshots_route_unknown_id_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshot(catalog_path, ts=1_000_000_000)
    client = _client(catalog_path, str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get("/catalog/snapshots/ZZZ-USD-PERP.DYDX?start_ns=0&end_ns=2000000000")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize("route", _CATALOG_ROUTES)
def test_catalog_route_rejects_an_id_without_a_venue(
    route: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_catalog_reads(monkeypatch)
    client = _client(str(tmp_path / "catalog"), str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"{route}/garbage?start_ns=0&end_ns=1000000000")

    assert response.status_code == 400
    assert "garbage" in response.json()["detail"]


@pytest.mark.parametrize(
    ("route", "module", "name"),
    [
        ("/catalog/snapshots", app_module.coin_detail, "catalog_snapshot_rows"),
    ],
)
@pytest.mark.usefixtures("_clean_ledger")
def test_catalog_read_failure_is_ledgered_and_explained(
    route: str, module: object, name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, name, _raise_os_error)
    client = _client(str(tmp_path / "catalog"), str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"{route}/{_IID}?start_ns=0&end_ns=1000000000")

    assert response.status_code == 500
    assert response.json()["detail"].startswith("failed to read catalog:")
    assert "disk unreadable" in response.json()["detail"]
    assert error_ledger.counts()["data_api.catalog_read"] == 1


@pytest.mark.parametrize("days", [0, -1, _MAX_DAYS + 1, 10**12])
def test_metrics_history_route_rejects_days_outside_retention(
    days: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(str(tmp_path / "catalog"), str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"/metrics/history/{_IID}?days={days}")

    assert response.status_code == 422


@pytest.mark.parametrize("days", [1, _MAX_DAYS])
def test_metrics_history_route_serves_days_within_retention(
    days: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(str(tmp_path / "catalog"), str(tmp_path / "metrics.db"), monkeypatch)

    response = client.get(f"/metrics/history/{_IID}?days={days}")

    assert response.status_code == 200
    assert response.json() == []


def test_metrics_db_default_matches_the_ranking_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = str(tmp_path / "catalog")
    monkeypatch.setenv("CATALOG_PATH", catalog_path)
    monkeypatch.delenv("METRICS_DB_PATH", raising=False)

    writer_path = settings_from_env().metrics_db_path

    assert writer_path == settings.default_metrics_db_path(catalog_path)
