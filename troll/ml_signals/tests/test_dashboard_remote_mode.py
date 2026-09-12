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
Tests for DATA_API_URL remote-data mode (Story 12.2).

Two claims from the story's I/O matrix, proven end-to-end through real HTTP requests
against a real running dashboard app (aiohttp TestServer) and a real running data_api
app (a genuinely bound uvicorn server -- not FastAPI's TestClient/httpx ASGI transport,
which aiohttp.ClientSession cannot reach; see the story's Design Notes):

1. DATA_API_URL unset -> every one of the 4 call sites behaves exactly as before this
   story: `_fetch_json` is never invoked (proven by making it raise if called).
2. DATA_API_URL set to a running data_api instance -> each call site's output matches
   what local mode produces for the same seeded catalog/metrics data.

Two of the four call sites (`/api/rank_history/{id}`, `/data/coin/{id}/lines`) return
plain JSON, so local-vs-remote output is compared byte-for-byte via the same running
dashboard app (toggling DATA_API_URL between two requests). The other two
(`/history/{id}`, `/chart/{id}`) render Plotly HTML, which embeds a fresh random div id
on every call (`fig.to_html()` is not deterministic even for two calls with identical
input -- verified empirically) -- for those, the *data fed into the shared render
helper* (`_history_page_from_rows`/`_build_chart_page_html`) is captured via a spy and
compared instead, which is the actual claim Story 12.2 makes ("output matches local
mode for the same seeded data"): rendering itself is already covered by
test_dashboard_chart.py's existing tests.

A third claim, from the I/O matrix's "data_api unreachable" row, is also proven here
(not just asserted in `_fetch_json`'s docstring, per code review pass): a genuinely
unreachable `DATA_API_URL` must surface as an error response, never a fabricated empty
result (DATA-01).
"""

import datetime as _dt
import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

import data_api.app as data_api_app
import ml_signals.dashboard as dashboard_module
import ranking_engine.metrics_store as metrics_store


_IID = "BTC-USD-PERP.DYDX"


def _epoch_window_query(start_s: float, end_s: float) -> tuple[str, str]:
    """
    Local-time ISO strings that _parse_dt/_parse_query_ms invert back to the given
    epoch-second window in this same process -- mirrors _render_chart_page's own
    `datetime.fromtimestamp(...).strftime(fmt)` construction, so it round-trips
    correctly regardless of the sandbox's timezone (forward and backward conversion
    both happen in-process with the same tz).
    """
    fmt = "%Y-%m-%dT%H:%M"
    return (
        _dt.datetime.fromtimestamp(start_s).strftime(fmt),
        _dt.datetime.fromtimestamp(end_s).strftime(fmt),
    )


@pytest.fixture
def _seeded_paths(tmp_path: Path) -> tuple[str, str]:
    """A temp ParquetDataCatalog with 2 DydxSecondSnapshots + a temp metrics.db with 2 rows."""
    catalog_path = str(tmp_path / "catalog")
    ParquetDataCatalog(catalog_path).write_data([
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(_IID),
            bid_prices=[100.0 + i], bid_sizes=[1.0],
            ask_prices=[102.0 + i], ask_sizes=[1.0],
            buy_volume=1.0, sell_volume=0.5, buy_count=1, sell_count=1,
            ts_event=1_000_000_000 + i * 1_000_000_000,
            ts_init=1_000_000_000 + i * 1_000_000_000,
        )
        for i in range(2)
    ])

    db_path = str(tmp_path / "metrics" / "metrics.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    # metrics_store.history() filters on a real wall-clock cutoff (now - 31 days), unlike
    # catalog snapshot queries -- these must be recent, not epoch-relative, or history()
    # would trivially return [] and the equality assertion below would vacuously pass.
    now_ns = time.time_ns()
    metrics_store.write([
        {
            "instrument_id": _IID, "ts": now_ns - 3_600_000_000_000, "rank": 1, "volume24h": 1.0,
            "price": 101.0, "pct_1h": 1.0, "pct_24h": 2.0, "volatility": 0.1,
            "ofi": 0.0, "microprice": 101.0, "spread": 2.0,
        },
        {
            "instrument_id": _IID, "ts": now_ns - 1_800_000_000_000, "rank": 1, "volume24h": 1.0,
            "price": 102.0, "pct_1h": 1.0, "pct_24h": 2.0, "volatility": 0.1,
            "ofi": 0.0, "microprice": 102.0, "spread": 2.0,
        },
    ], db_path)
    return catalog_path, db_path


@pytest.fixture
def _data_api_url(
    _seeded_paths: tuple[str, str], monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Run data_api's real FastAPI app on a genuinely bound port, in a background thread."""
    catalog_path, db_path = _seeded_paths
    monkeypatch.setattr(data_api_app, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(data_api_app, "METRICS_DB_PATH", db_path)

    # Bind our own socket and hand the live fd to uvicorn (server.run(sockets=[...]))
    # instead of picking a "free" port and closing it for uvicorn to re-bind -- the
    # latter has a real TOCTOU race against any other process/test grabbing the same
    # port in the gap (review finding, Story 12.2 code review pass).
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(data_api_app.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "data_api uvicorn server did not start within 5s"

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive(), "data_api uvicorn server thread did not stop within 5s"


@pytest.mark.asyncio
async def test_data_api_url_unset_never_calls_fetch_json(
    monkeypatch: pytest.MonkeyPatch, _seeded_paths: tuple[str, str],
) -> None:
    """The hard byte-for-byte-identical constraint: with DATA_API_URL unset, none of the
    4 call sites may take the remote branch at all."""
    catalog_path, db_path = _seeded_paths
    monkeypatch.setattr(dashboard_module, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(dashboard_module, "METRICS_DB_PATH", db_path)
    monkeypatch.setattr(dashboard_module, "DATA_API_URL", "")

    async def _boom(session: object, url: str) -> None:
        raise AssertionError(f"_fetch_json must never be called when DATA_API_URL is unset: {url}")

    monkeypatch.setattr(dashboard_module, "_fetch_json", _boom)

    start_str, end_str = _epoch_window_query(0, 3600)
    app = dashboard_module.make_app("redis://127.0.0.1:6379", catalog_path)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(f"/history/{_IID}")).status == 200
        resp = await client.get(f"/chart/{_IID}", params={"start": start_str, "end": end_str})
        assert resp.status == 200
        assert (await client.get(f"/api/rank_history/{_IID}")).status == 200
        resp = await client.get(
            f"/data/coin/{_IID}/lines", params={"start": start_str, "end": end_str},
        )
        assert resp.status == 200


@pytest.mark.asyncio
async def test_remote_mode_surfaces_data_api_outage_as_error_not_empty_result(
    monkeypatch: pytest.MonkeyPatch, _seeded_paths: tuple[str, str],
) -> None:
    """
    DATA-01: a genuinely unreachable `DATA_API_URL` must surface as an error response,
    never a fabricated/silent empty result standing in for real data -- proven here, not
    just asserted in `_fetch_json`'s docstring (code review pass, Story 12.2).
    """
    catalog_path, db_path = _seeded_paths
    monkeypatch.setattr(dashboard_module, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(dashboard_module, "METRICS_DB_PATH", db_path)

    # A closed loopback socket's just-freed port -- connection refused immediately, no
    # need to wait out _get_http_session's 10s request timeout.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(dashboard_module, "DATA_API_URL", f"http://127.0.0.1:{dead_port}")

    app = dashboard_module.make_app("redis://127.0.0.1:6379", catalog_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/rank_history/{_IID}")
        assert resp.status >= 500, f"expected an error status, got {resp.status}"


@pytest.mark.asyncio
async def test_remote_history_page_uses_data_api_rows_matching_local(
    monkeypatch: pytest.MonkeyPatch, _seeded_paths: tuple[str, str], _data_api_url: str,
) -> None:
    catalog_path, db_path = _seeded_paths
    monkeypatch.setattr(dashboard_module, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(dashboard_module, "METRICS_DB_PATH", db_path)
    monkeypatch.setattr(dashboard_module, "DATA_API_URL", "")

    captured: list[list[dict]] = []
    original = dashboard_module._history_page_from_rows

    def _spy(symbol: str, rows: list[dict]) -> str:
        captured.append(rows)
        return original(symbol, rows)

    monkeypatch.setattr(dashboard_module, "_history_page_from_rows", _spy)

    app = dashboard_module.make_app("redis://127.0.0.1:6379", catalog_path)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get(f"/history/{_IID}")).status == 200
        local_rows = captured[-1]

        monkeypatch.setattr(dashboard_module, "DATA_API_URL", _data_api_url)
        assert (await client.get(f"/history/{_IID}")).status == 200
        remote_rows = captured[-1]

    assert local_rows, "seeded metrics rows must actually reach the renderer (not a vacuous pass)"
    assert remote_rows == local_rows


@pytest.mark.asyncio
async def test_remote_chart_page_uses_data_api_data_matching_local(
    monkeypatch: pytest.MonkeyPatch, _seeded_paths: tuple[str, str], _data_api_url: str,
) -> None:
    catalog_path, _db_path = _seeded_paths
    monkeypatch.setattr(dashboard_module, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(dashboard_module, "DATA_API_URL", "")

    captured: list[dict] = []
    original = dashboard_module._build_chart_page_html

    def _spy(
        symbol: str, start_ms: int, end_ms: int, explicit_range: bool, data: dict,
    ) -> str:
        captured.append(data)
        return original(symbol, start_ms, end_ms, explicit_range, data)

    monkeypatch.setattr(dashboard_module, "_build_chart_page_html", _spy)

    start_str, end_str = _epoch_window_query(0, 3600)
    app = dashboard_module.make_app("redis://127.0.0.1:6379", catalog_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/chart/{_IID}", params={"start": start_str, "end": end_str})
        assert resp.status == 200
        local_data = captured[-1]

        monkeypatch.setattr(dashboard_module, "DATA_API_URL", _data_api_url)
        resp = await client.get(f"/chart/{_IID}", params={"start": start_str, "end": end_str})
        assert resp.status == 200
        remote_data = captured[-1]

    assert any(local_data.get(k) for k in local_data), "seeded snapshots must produce chart series"
    assert remote_data == local_data


@pytest.mark.asyncio
async def test_remote_rank_history_matches_local_response(
    monkeypatch: pytest.MonkeyPatch, _seeded_paths: tuple[str, str], _data_api_url: str,
) -> None:
    """Plain-JSON response -- no Plotly involved, so byte-for-byte comparison is exact."""
    catalog_path, db_path = _seeded_paths
    monkeypatch.setattr(dashboard_module, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(dashboard_module, "METRICS_DB_PATH", db_path)
    monkeypatch.setattr(dashboard_module, "DATA_API_URL", "")

    app = dashboard_module.make_app("redis://127.0.0.1:6379", catalog_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(f"/api/rank_history/{_IID}")
        assert resp.status == 200
        local_body = json.loads(await resp.text())

        monkeypatch.setattr(dashboard_module, "DATA_API_URL", _data_api_url)
        resp = await client.get(f"/api/rank_history/{_IID}")
        assert resp.status == 200
        remote_body = json.loads(await resp.text())

    assert local_body, "seeded metrics rows must actually reach the response (not a vacuous pass)"
    assert remote_body == local_body


@pytest.mark.asyncio
async def test_remote_lines_matches_local_response(
    monkeypatch: pytest.MonkeyPatch, _seeded_paths: tuple[str, str], _data_api_url: str,
) -> None:
    """Plain-JSON response -- no Plotly involved, so byte-for-byte comparison is exact."""
    catalog_path, _db_path = _seeded_paths
    monkeypatch.setattr(dashboard_module, "CATALOG_PATH", catalog_path)
    monkeypatch.setattr(dashboard_module, "DATA_API_URL", "")

    start_str, end_str = _epoch_window_query(0, 3600)
    app = dashboard_module.make_app("redis://127.0.0.1:6379", catalog_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get(
            f"/data/coin/{_IID}/lines", params={"start": start_str, "end": end_str},
        )
        assert resp.status == 200
        local_body = json.loads(await resp.text())

        monkeypatch.setattr(dashboard_module, "DATA_API_URL", _data_api_url)
        resp = await client.get(
            f"/data/coin/{_IID}/lines", params={"start": start_str, "end": end_str},
        )
        assert resp.status == 200
        remote_body = json.loads(await resp.text())

    assert local_body["rows"], "seeded catalog snapshots must actually produce rows"
    assert remote_body == local_body
