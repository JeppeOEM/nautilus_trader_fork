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
"""Bybit REST `/v5/market/orderbook` parse and request shape (story 22.5, audit D-64)."""

import asyncio
import urllib.request

import pytest

from bybit_collector.book_snapshot import _request
from bybit_collector.book_snapshot import fetch_orderbook
from bybit_collector.book_snapshot import parse_orderbook


_RESULT = {
    "s": "BTCUSDT",
    "b": [["85849.8", "2.454"], ["85849.7", "0.01"]],
    "a": [["85849.9", "0.75"], ["85854.3", "0.002"]],
    "ts": 1758477592296,
    "u": 132229098,
    "seq": 9876543210123,
    "cts": 1758477592290,
}


def test_parse_orderbook_best_first_with_cross_sequence() -> None:
    snap = parse_orderbook(_RESULT)
    assert snap.bids == [(85849.8, 2.454), (85849.7, 0.01)]
    assert snap.asks == [(85849.9, 0.75), (85854.3, 0.002)]
    assert snap.sequence == 9876543210123
    assert snap.ts_event_ns is None


@pytest.mark.parametrize(
    ("iid", "category"), [("BTCUSDT-LINEAR.BYBIT", "linear"), ("BTCUSDT-SPOT.BYBIT", "spot")]
)
def test_request_targets_the_public_orderbook_endpoint(iid: str, category: str) -> None:
    request = _request("mainnet", iid, 50)
    assert request.full_url == (
        f"https://api.bybit.com/v5/market/orderbook?category={category}&symbol=BTCUSDT&limit=50"
    )
    assert _request("testnet", iid, 50).full_url.startswith("https://api-testnet.bybit.com/")


def test_fetch_orderbook_uses_the_injected_http_and_raises_on_ret_code() -> None:
    seen: list[str] = []

    def ok(request: urllib.request.Request) -> dict:
        seen.append(request.full_url)
        return {"retCode": 0, "retMsg": "OK", "result": _RESULT}

    snap = asyncio.run(fetch_orderbook("mainnet", "BTCUSDT-LINEAR.BYBIT", 50, http=ok))
    assert snap.sequence == _RESULT["seq"]
    assert seen[0].endswith("limit=50")

    def rejected(request: urllib.request.Request) -> dict:
        return {"retCode": 10001, "retMsg": "params error"}

    with pytest.raises(RuntimeError, match="retCode=10001"):
        asyncio.run(fetch_orderbook("mainnet", "BTCUSDT-LINEAR.BYBIT", 50, http=rejected))
