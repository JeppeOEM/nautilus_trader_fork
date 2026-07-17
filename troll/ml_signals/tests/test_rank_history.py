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
"""Unit tests for rank_history.fetch_rank_history (Story 1.4)."""

import asyncio
import json
import tempfile
import urllib.request
from pathlib import Path
from typing import Self

import pytest
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer

import ml_signals.dashboard as dashboard_module
import ml_signals.metrics_store as metrics_store
from ml_signals import rank_history


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_fetch_rank_history_without_ts_omits_query_param(monkeypatch) -> None:
    captured = {}

    def _urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse({"ts": 1, "rank": 1, "volume24h": 100.0})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    result = rank_history.fetch_rank_history("BTC-USD-PERP.DYDX")

    assert result == {"ts": 1, "rank": 1, "volume24h": 100.0}
    assert captured["url"] == "http://127.0.0.1:8765/api/rank_history/BTC-USD-PERP.DYDX"


def test_fetch_rank_history_with_ts_adds_iso_query_param(monkeypatch) -> None:
    captured = {}

    def _urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse({"ts": 2, "rank": 3, "volume24h": 50.0})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    result = rank_history.fetch_rank_history("BTC-USD-PERP.DYDX", ts_ns=1_700_000_000_000_000_000)

    assert result == {"ts": 2, "rank": 3, "volume24h": 50.0}
    assert captured["url"].startswith("http://127.0.0.1:8765/api/rank_history/BTC-USD-PERP.DYDX?ts=")


def test_fetch_rank_history_empty_response(monkeypatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse({}),
    )
    assert rank_history.fetch_rank_history("DEAD-USD-PERP.DYDX") == {}


def test_fetch_rank_history_strips_trailing_slash_on_dashboard_url(monkeypatch) -> None:
    captured = {}

    def _urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse({})

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    rank_history.fetch_rank_history("BTC-USD-PERP.DYDX", dashboard_url="http://127.0.0.1:8765/")

    assert captured["url"] == "http://127.0.0.1:8765/api/rank_history/BTC-USD-PERP.DYDX"


@pytest.mark.asyncio
async def test_fetch_rank_history_resolves_real_historical_timestamp_via_http(monkeypatch) -> None:
    """End-to-end regression: fetch_rank_history's ts_ns query must round-trip through a real
    aiohttp server/handler and return the requested historical row, not "now" data.

    Exercises the actual query-string encoding/decoding that the mocked-urlopen tests above
    cannot -- this is what catches an unescaped '+' in the ISO UTC offset (a raw '+' in a
    query string is decoded as a space by aiohttp/yarl, breaking datetime.fromisoformat and
    silently falling back to "nearest to now" instead of the requested timestamp).
    """
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setattr(dashboard_module, "CATALOG_PATH", str(Path(tmp_dir) / "catalog"))
    db_path = str(Path(tmp_dir) / "metrics.db")
    iid = "BTC-USD-PERP.DYDX"
    old_ts = 1_700_000_000_000_000_000
    new_ts = old_ts + 60_000_000_000
    base_row = {
        "price": None, "pct_1h": None, "pct_24h": None, "volatility": None,
        "ofi": None, "microprice": None, "spread": None,
    }
    metrics_store.write([
        {"instrument_id": iid, "ts": old_ts, "rank": 1, "volume24h": 1.0, **base_row},
        {"instrument_id": iid, "ts": new_ts, "rank": 2, "volume24h": 2.0, **base_row},
    ], db_path)

    app = dashboard_module.make_app("redis://127.0.0.1:6379", str(Path(tmp_dir) / "catalog"))
    async with TestClient(TestServer(app)) as client:
        # fetch_rank_history uses blocking urllib -- run off-thread so the event loop
        # this same TestServer needs to answer the request on isn't blocked by the caller.
        result = await asyncio.to_thread(
            rank_history.fetch_rank_history, iid, ts_ns=old_ts, dashboard_url=str(client.make_url("")),
        )

    assert result["ts"] == old_ts
    assert result["rank"] == 1
