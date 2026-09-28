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
`research.application.ranking_history.HttpRankingHistory` against a real local HTTP server serving
data_api's `/api/metrics/history` payload shape (Story 27.1): values pass through untouched.
"""

import json
import math
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer

import pandas as pd
import pytest

from research.application.ranking_history import METRIC_COLUMNS
from research.application.ranking_history import HttpRankingHistory


_T0 = 1_790_000_000_000_000_000
_ITEMS = [
    {"ts": _T0, "price": 100.0, "pct_1h": 0.5, "volatility": None, "rank": 3.0, "volume24h": 1e6},
    {
        "ts": _T0 + 60_000_000_000,
        "price": 101.0,
        "pct_1h": 0.7,
        "volatility": 0.02,
        "rank": 2.0,
        "volume24h": None,
    },
]


class _Handler(BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self) -> None:
        _Handler.requests.append(self.path)
        items: list[dict] = []
        if self.path.startswith("/api/metrics/history/BTC-USD-PERP.DYDX"):
            items = _ITEMS
        elif self.path.startswith("/api/metrics/history/SOL-USD-PERP.DYDX"):
            items = _ITEMS[::-1]
        elif self.path.startswith("/api/metrics/history/AVAX-USD-PERP.DYDX"):
            items = [item | {"pct_1m": None} for item in _ITEMS]
        elif self.path.startswith("/api/metrics/history/LINK-USD-PERP.DYDX"):
            items = [{"price": 1.0}]
        payload: dict = {"detail": "not found"} if "DOGE" in self.path else {"items": items}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def base_url() -> Iterator[str]:
    _Handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_history_passes_ranking_values_through(base_url: str) -> None:
    df = HttpRankingHistory(base_url).history("BTC-USD-PERP.DYDX", 7)
    assert _Handler.requests == ["/api/metrics/history/BTC-USD-PERP.DYDX?days=7"]
    assert df["ts_event"].tolist() == [_T0, _T0 + 60_000_000_000]
    assert df.index.name == "ts"
    assert df.index[0] == pd.Timestamp(_T0, unit="ns", tz="UTC")
    assert df["pct_1h"].tolist() == [0.5, 0.7]
    assert math.isnan(df["volatility"].iloc[0])  # a None field stays a gap
    assert df["rank"].tolist() == [3.0, 2.0]
    assert list(df.columns) == ["ts_event", *METRIC_COLUMNS]
    assert df["pct_24h"].isna().all()  # a field the API omitted is a gap, not a missing column


def test_rows_out_of_order_raise(base_url: str) -> None:
    with pytest.raises(ValueError, match="strictly increasing"):
        HttpRankingHistory(base_url).history("SOL-USD-PERP.DYDX", 7)


def test_no_rows_is_an_empty_frame(base_url: str) -> None:
    df = HttpRankingHistory(base_url).history("ETH-USD-PERP.DYDX", 1)
    assert df.empty
    assert list(df.columns) == ["ts_event", *METRIC_COLUMNS]


def test_days_must_be_positive(base_url: str) -> None:
    with pytest.raises(ValueError, match="days"):
        HttpRankingHistory(base_url).history("BTC-USD-PERP.DYDX", 0)


def test_a_field_none_in_every_row_is_a_float_nan_column(base_url: str) -> None:
    df = HttpRankingHistory(base_url).history("AVAX-USD-PERP.DYDX", 7)
    assert df["pct_1m"].dtype == "float64"
    assert df["pct_1m"].isna().all()


def test_a_row_without_ts_or_a_payload_without_items_raises(base_url: str) -> None:
    with pytest.raises(ValueError, match="without a ts"):
        HttpRankingHistory(base_url).history("LINK-USD-PERP.DYDX", 7)
    with pytest.raises(ValueError, match="no items list"):
        HttpRankingHistory(base_url).history("DOGE-USD-PERP.DYDX", 7)
