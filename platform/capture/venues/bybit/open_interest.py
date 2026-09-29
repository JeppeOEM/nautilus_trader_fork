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
Stdlib only, same as `capture.venues.dydx.open_interest`.
"""

import asyncio
import time

from kernel.open_interest import OpenInterest
from kernel.venue_http import bybit_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json

from capture.application.ports import PolledRows
from capture.application.ports import finite_decimal
from nautilus_trader.model.identifiers import InstrumentId


_USER_AGENT = "nautilus-bybit-collector/1.0"


def _fetch_tickers_json(environment: str) -> dict:
    url = bybit_url(environment, "/v5/market/tickers?category=linear")
    return http_json(get_request(url, _USER_AGENT))


async def fetch_open_interest(environment: str) -> PolledRows:
    tickers_json = await asyncio.to_thread(_fetch_tickers_json, environment)
    return parse_open_interest(tickers_json, ts=time.time_ns())


def parse_open_interest(tickers_json: dict, ts: int) -> PolledRows:
    """
    Every linear ticker row as an `OpenInterest`; a row without a symbol or an `openInterest`, or
    whose value is not a decimal, is named in `malformed` (the poll ledgers it), never skipped.
    """
    rows: list[OpenInterest] = []
    malformed: list[tuple[str | None, str]] = []
    for row in tickers_json.get("result", {}).get("list", []):
        # The Nautilus Bybit adapter's linear ids are "{symbol}-LINEAR.BYBIT".
        iid = f"{row['symbol']}-LINEAR.BYBIT" if row.get("symbol") else None
        value = row.get("openInterest")
        if iid is None or not value:
            malformed.append((iid, f"no symbol or openInterest in {row!r:.200}"))
            continue
        open_interest = finite_decimal(value)
        if open_interest is None:
            malformed.append((iid, f"openInterest {value!r:.80} is not a finite decimal"))
            continue
        rows.append(
            OpenInterest(
                instrument_id=InstrumentId.from_str(iid),
                open_interest=open_interest,
                ts_event=ts,
                ts_init=ts,
            )
        )
    return PolledRows(rows, malformed)
