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
Bybit REST book snapshot (`GET /v5/market/orderbook`, public, 600 req / 5 s per IP) for the
aligned live-book cross-check (story 22.5, audit D-64). Stdlib only, the same shape as
`bybit_collector.open_interest`.

The pyo3 `request_orderbook_snapshot` path is not used because the alignment key must be the
response's `seq`: Bybit's cross sequence, the counter the WS stamps on every delta's
`sequence` (crates/adapters/bybit/src/websocket/parse.rs) and the only field comparable across
book depths (`u` is per stream depth).
"""

import asyncio
import urllib.parse
import urllib.request
from decimal import Decimal

from collector_core.book_check import BookSnapshot
from collector_core.book_check import Level
from collector_core.venue_http import BYBIT_URLS
from collector_core.venue_http import HttpJson
from collector_core.venue_http import bybit_category
from collector_core.venue_http import http_json


def _levels(raw: list[list[str]]) -> list[Level]:
    return [(float(Decimal(price)), float(Decimal(size))) for price, size in raw]


def parse_orderbook(result: dict) -> BookSnapshot:
    """Parse the `result` of `/v5/market/orderbook`: `b`/`a` best-first strings and `seq`."""
    return BookSnapshot(
        bids=_levels(result["b"]), asks=_levels(result["a"]), sequence=int(result["seq"])
    )


def _request(environment: str, iid: str, depth: int) -> urllib.request.Request:
    symbol = iid.split("-", 1)[0]
    query = urllib.parse.urlencode(
        {"category": bybit_category(iid), "symbol": symbol, "limit": depth}
    )
    return urllib.request.Request(  # noqa: S310 (fixed https URL)
        f"{BYBIT_URLS[environment]}/v5/market/orderbook?{query}",
        headers={"User-Agent": "nautilus-bybit-collector/1.0"},
    )


async def fetch_orderbook(
    environment: str, iid: str, depth: int, http: HttpJson = http_json
) -> BookSnapshot:
    payload = await asyncio.to_thread(http, _request(environment, iid, depth))
    if payload.get("retCode") != 0:
        raise RuntimeError(
            f"Bybit orderbook {iid}: retCode={payload.get('retCode')} {payload.get('retMsg')}"
        )
    return parse_orderbook(payload["result"])
