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
"""`MarketsSource` over dYdX's indexer `perpetualMarkets` (the request is built in the kernel)."""

import asyncio
from typing import Any

from dydx_collector.open_interest import fetch_markets_json

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


class DydxMarkets:
    """
    The dYdX markets snapshot. Invariant (see `MarketsSource`): the indexer's JSON or an exception,
    fetched off the event loop so a slow indexer never stalls capture's sampling.
    """

    def __init__(self, network: DydxNetwork) -> None:
        self._network = network

    async def fetch(self) -> dict[str, Any]:
        return await asyncio.to_thread(fetch_markets_json, self._network)
