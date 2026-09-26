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

`classify_liquidity` moved to `collection_control.domain.liquidity` (Story 25.4: it is the plan's pin
admission rule, not capture's) and is served here, deprecated, from `_MOVED_NAMES`. It now returns a
`LiquidityClassification` rather than a `(liquid, illiquid)` tuple.
"""

import asyncio
import importlib
import time
import warnings
from decimal import Decimal

from kernel.open_interest import OpenInterest
from kernel.venue_http import dydx_indexer_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.identifiers import InstrumentId


# Moved names (Story 25.4): served from their new home with a DeprecationWarning until then.
MOVED_NAMES_REMOVE_AFTER = "26-2-capture-package-and-venue-packages-with-entrypoints"
_MOVED_NAMES: dict[str, str] = {
    "classify_liquidity": "collection_control.domain.liquidity.classify_liquidity",
}


def __getattr__(name: str) -> object:
    if name in _MOVED_NAMES:
        warnings.warn(
            f"dydx_collector.open_interest.{name} moved to {_MOVED_NAMES[name]} (Story 25.4); "
            f"it is served here until {MOVED_NAMES_REMOVE_AFTER}",
            DeprecationWarning,
            stacklevel=2,
        )
        # A literal module name, so `platform/tests/test_images.py` follows it into the image check.
        return getattr(importlib.import_module("collection_control.domain.liquidity"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def fetch_markets_json(network: DydxNetwork) -> dict:
    # dYdX's indexer rejects urllib's default User-Agent (403); needs a real one.
    url = dydx_indexer_url(network, "/v4/perpetualMarkets")
    return http_json(get_request(url, "nautilus-dydx-collector/1.0"))


async def fetch_open_interest(network: DydxNetwork) -> list[OpenInterest]:
    """Poll dYdX's REST indexer for current open interest across all markets."""
    markets_json = await asyncio.to_thread(fetch_markets_json, network)
    return parse_open_interest(markets_json, ts=time.time_ns())


def parse_open_interest(markets_json: dict, ts: int) -> list[OpenInterest]:
    items = []
    for market in markets_json.get("markets", {}).values():
        ticker = market.get("ticker")
        open_interest = market.get("openInterest")
        if ticker is None or open_interest is None:
            continue
        items.append(
            OpenInterest(
                instrument_id=InstrumentId.from_str(f"{ticker}-PERP.DYDX"),
                open_interest=Decimal(open_interest),
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return items
