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
dYdX open interest: the one field dropped by the Rust/PyO3 adapter bindings on
both the REST and WebSocket markets-channel paths (confirmed by reading
crates/adapters/dydx/src/python/{http,websocket}.rs -- `open_interest` is parsed
Rust-side but never forwarded to Python). Fetched here with a plain stdlib REST
poll against the same public indexer endpoint, since stdlib already covers a
single infrequent GET (no need for a dependency that may not even be in the
production image -- aiohttp is a `test`-only extra of nautilus_trader, not a
runtime dependency).

`classify_liquidity` lives in `collection_control.domain.liquidity` (Story 25.4: it is the plan's
pin admission rule, not capture's); its deprecated alias here was removed in Story 26.2.
"""

import asyncio
import time

from kernel.dydx_http import dydx_indexer_url
from kernel.open_interest import OpenInterest
from kernel.venue_http import get_request
from kernel.venue_http import http_json

from capture.application.ports import PolledRows
from capture.application.ports import finite_decimal
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.identifiers import InstrumentId


def fetch_markets_json(network: DydxNetwork) -> dict:
    # dYdX's indexer rejects urllib's default User-Agent (403); needs a real one.
    url = dydx_indexer_url(network, "/v4/perpetualMarkets")
    return http_json(get_request(url, "nautilus-dydx-collector/1.0"))


async def fetch_open_interest(network: DydxNetwork) -> PolledRows:
    """Poll dYdX's REST indexer for current open interest across all markets."""
    markets_json = await asyncio.to_thread(fetch_markets_json, network)
    return parse_open_interest(markets_json, ts=time.time_ns())


def parse_open_interest(markets_json: dict, ts: int) -> PolledRows:
    """
    Every market as an `OpenInterest`; a market without a ticker or an `openInterest`, or whose
    value is not a decimal, is named in `malformed` (the poll ledgers it), never skipped.
    """
    rows: list[OpenInterest] = []
    malformed: list[tuple[str | None, str]] = []
    for key, market in markets_json.get("markets", {}).items():
        ticker = market.get("ticker")
        iid = f"{ticker}-PERP.DYDX" if ticker else None
        open_interest = market.get("openInterest")
        if iid is None or open_interest is None:
            malformed.append((iid, f"market {key!r} has no ticker or openInterest"))
            continue
        value = finite_decimal(open_interest)
        if value is None:
            malformed.append((iid, f"openInterest {open_interest!r:.80} is not a finite decimal"))
            continue
        rows.append(
            OpenInterest(
                instrument_id=InstrumentId.from_str(iid),
                open_interest=value,
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return PolledRows(rows, malformed)
