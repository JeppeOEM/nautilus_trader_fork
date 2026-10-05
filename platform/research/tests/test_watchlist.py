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
"""Unit tests for watchlist.fetch_watchlist and its BacktestDataConfig compatibility (Story 1.3)."""

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from email.message import Message
from typing import Self

import pytest

from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId
from research import watchlist


_URL = "http://127.0.0.1:9100/api/rankings"


class _FakeResponse:
    def __init__(self, payload: object, *, body: bytes | None = None) -> None:
        self._body = json.dumps(payload).encode() if body is None else body

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_fetch_watchlist_parses_instrument_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            {"items": [{"instrument_id": i} for i in ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]]}
        ),
    )
    result = watchlist.fetch_watchlist("http://127.0.0.1:9100")
    assert result == ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]


def test_fetch_watchlist_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse({"items": []}),
    )
    assert watchlist.fetch_watchlist() == []


def test_watchlist_ids_are_backtest_data_config_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: the returned coin-set must accept-as-is into a BacktestDataConfig list, no per-coin editing."""
    ids = ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX", "SOL-USD-PERP.DYDX"]
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse({"items": [{"instrument_id": i} for i in ids]}),
    )

    fetched = watchlist.fetch_watchlist()
    configs = [
        BacktestDataConfig(
            catalog_path="platform/data/catalog",
            data_cls=TradeTick,
            instrument_id=InstrumentId.from_str(iid),
        )
        for iid in fetched
    ]

    assert len(configs) == len(ids)
    assert [c.instrument_id for c in configs] == [InstrumentId.from_str(iid) for iid in ids]


_Urlopen = Callable[..., object]


def _raising(exc: Exception) -> _Urlopen:
    def _urlopen(request: urllib.request.Request, timeout: float | None = None) -> None:
        raise exc

    return _urlopen


def _fetch_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    urlopen: _Urlopen,
) -> watchlist.WatchlistUnavailableError:
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(watchlist.WatchlistUnavailableError) as info:
        watchlist.fetch_watchlist("http://127.0.0.1:9100/")
    assert _URL in str(info.value)
    return info.value


def test_fetch_watchlist_unreachable_names_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _fetch_unavailable(monkeypatch, _raising(urllib.error.URLError("refused")))
    assert isinstance(error.__cause__, urllib.error.URLError)


def test_fetch_watchlist_http_503_names_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    http_503 = urllib.error.HTTPError(_URL, 503, "Service Unavailable", Message(), None)
    error = _fetch_unavailable(monkeypatch, _raising(http_503))
    assert error.__cause__ is http_503


def test_fetch_watchlist_truncated_response_names_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    # IncompleteRead is an http.client.HTTPException, not an OSError.
    truncated = http.client.IncompleteRead(b'{"items": [')
    error = _fetch_unavailable(monkeypatch, _raising(truncated))
    assert error.__cause__ is truncated


def test_fetch_watchlist_scheme_less_url_names_the_url() -> None:
    # Request() itself raises ValueError ("unknown url type") for a URL with no scheme at all,
    # before any I/O. ("127.0.0.1:9100" is not this case: it parses "127.0.0.1" as the scheme
    # and fails later, in urlopen, as a URLError.)
    with pytest.raises(watchlist.WatchlistUnavailableError) as info:
        watchlist.fetch_watchlist("data-api")
    assert "data-api/api/rankings" in str(info.value)
    assert isinstance(info.value.__cause__, ValueError)


def test_fetch_watchlist_non_json_body_names_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    error = _fetch_unavailable(
        monkeypatch,
        lambda request, timeout=None: _FakeResponse(None, body=b"<html>bad gateway</html>"),
    )
    assert isinstance(error.__cause__, json.JSONDecodeError)


@pytest.mark.parametrize("payload", [{"detail": "warming up"}, {"items": None}, ["BTC"]])
def test_fetch_watchlist_payload_without_items_list_names_the_url(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    _fetch_unavailable(monkeypatch, lambda request, timeout=None: _FakeResponse(payload))
