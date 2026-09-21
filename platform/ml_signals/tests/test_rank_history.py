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

import json
import urllib.request
from typing import Self

import pytest

from ml_signals import rank_history


class _FakeResponse:
    def __init__(self, payload: object) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _capture_url(monkeypatch: pytest.MonkeyPatch, payload: object) -> dict:
    captured: dict = {}

    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> _FakeResponse:
        captured["url"] = request.full_url
        return _FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return captured


def test_fetch_rank_history_with_ts_passes_ts_ns(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_url(monkeypatch, {"ts": 2, "rank": 3, "volume24h": 50.0})

    result = rank_history.fetch_rank_history("BTC-USD-PERP.DYDX", ts_ns=1_700_000_000_000_000_000)

    assert result == {"ts": 2, "rank": 3, "volume24h": 50.0}
    assert captured["url"] == (
        "http://127.0.0.1:9100/api/metrics/nearest/BTC-USD-PERP.DYDX?ts_ns=1700000000000000000"
    )


def test_fetch_rank_history_without_ts_defaults_to_now(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_url(monkeypatch, {"ts": 1})
    monkeypatch.setattr(rank_history.time, "time_ns", lambda: 42)

    rank_history.fetch_rank_history("BTC-USD-PERP.DYDX")

    assert captured["url"].endswith("/api/metrics/nearest/BTC-USD-PERP.DYDX?ts_ns=42")


def test_fetch_rank_history_null_response_is_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_url(monkeypatch, None)  # data_api returns JSON null when no row exists
    assert rank_history.fetch_rank_history("DEAD-USD-PERP.DYDX") == {}


def test_fetch_rank_history_strips_trailing_slash_on_url(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_url(monkeypatch, {})

    rank_history.fetch_rank_history("BTC-USD-PERP.DYDX", ts_ns=1, data_api_url="http://x:9100/")

    assert captured["url"] == "http://x:9100/api/metrics/nearest/BTC-USD-PERP.DYDX?ts_ns=1"
