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
Bybit linear open interest. The Rust adapter drops it on the linear ticker WS path (see
client.py), so poll the public REST tickers endpoint -- one GET returns every linear symbol.
Stdlib only, same as `dydx_collector.open_interest`.
"""

import asyncio
import time
from decimal import Decimal

from kernel.open_interest import OpenInterest
from kernel.venue_http import bybit_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json

from nautilus_trader.model.identifiers import InstrumentId


_USER_AGENT = "nautilus-bybit-collector/1.0"


def _fetch_tickers_json(environment: str) -> dict:
    url = bybit_url(environment, "/v5/market/tickers?category=linear")
    return http_json(get_request(url, _USER_AGENT))


async def fetch_open_interest(environment: str) -> list[OpenInterest]:
    tickers_json = await asyncio.to_thread(_fetch_tickers_json, environment)
    return parse_open_interest(tickers_json, ts=time.time_ns())


def parse_open_interest(tickers_json: dict, ts: int) -> list[OpenInterest]:
    # The Nautilus Bybit adapter's linear ids are "{symbol}-LINEAR.BYBIT".
    return [
        OpenInterest(
            instrument_id=InstrumentId.from_str(f"{row['symbol']}-LINEAR.BYBIT"),
            open_interest=Decimal(row["openInterest"]),
            ts_event=ts,
            ts_init=ts,
        )
        for row in tickers_json.get("result", {}).get("list", [])
        if row.get("symbol") and row.get("openInterest")
    ]
