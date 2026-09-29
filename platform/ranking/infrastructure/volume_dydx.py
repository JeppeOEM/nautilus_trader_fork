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
dYdX's USD 24 h volume source (moved from the ranking engine module in Story 25.2): the public
indexer's `perpetualMarkets` `volume24H`, requested through `kernel.venue_http`.
"""

import asyncio

from kernel.dydx_http import dydx_indexer_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json
from observability import error_ledger

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from ranking.domain.values import parse_usd_volume


def parse_volume_24h(markets_json: dict) -> dict[str, float]:
    """Parse volume24H (USD) per instrument from a dYdX perpetualMarkets response."""
    result: dict[str, float] = {}
    for market in markets_json.get("markets", {}).values():
        ticker = market.get("ticker")
        if ticker is None:
            continue
        try:
            # A missing/empty value is unparseable, never 0: no volume is not zero volume (DATA-01).
            vol = parse_usd_volume(market.get("volume24H"))
        except (ValueError, TypeError) as exc:
            # Leave the coin out, loudly.
            error_ledger.record(
                "ranking_engine.volume24h",
                f"{ticker}: unparseable volume24H {market.get('volume24H')!r}",
                exc,
            )
            continue
        result[f"{ticker}-PERP.DYDX"] = vol
    return result


class DydxVolumeSource:
    """
    `VolumeSource` for dYdX perpetuals. Invariant: ids are `{ticker}-PERP.DYDX`, exactly as the
    dYdX adapter builds them, from the network the collector itself uses (`DYDX_NETWORK`).
    """

    name = "dydx"

    def __init__(self, network: DydxNetwork) -> None:
        self._network = network

    async def fetch(self) -> dict[str, float]:
        request = get_request(dydx_indexer_url(self._network, "/v4/perpetualMarkets"))
        markets_json = await asyncio.to_thread(http_json, request)
        return parse_volume_24h(markets_json)
