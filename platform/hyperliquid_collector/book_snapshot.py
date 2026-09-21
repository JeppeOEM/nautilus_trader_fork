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
Hyperliquid REST book snapshot (`POST /info {"type":"l2Book"}`, weight 2 of 1200/min) for the
live-book cross-check (story 22.5). Stdlib only, same as `dydx_collector.open_interest`.
"""

import asyncio
import json
import urllib.request
from decimal import Decimal


_URLS = {
    "mainnet": "https://api.hyperliquid.xyz/info",
    "testnet": "https://api.hyperliquid-testnet.xyz/info",
}


def _post_l2_book(environment: str, coin: str) -> dict:
    request = urllib.request.Request(  # noqa: S310 (fixed https URL)
        _URLS[environment],
        data=json.dumps({"type": "l2Book", "coin": coin}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "nautilus-hl-collector/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def parse_l2_book(payload: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """`levels[0]` = bids, `levels[1]` = asks, each best-first `{px, sz}` decimal strings."""
    bids, asks = payload["levels"]
    return (
        [(float(Decimal(lv["px"])), float(Decimal(lv["sz"]))) for lv in bids],
        [(float(Decimal(lv["px"])), float(Decimal(lv["sz"]))) for lv in asks],
    )


async def fetch_l2_book(environment: str, coin: str) -> tuple[list, list]:
    return parse_l2_book(await asyncio.to_thread(_post_l2_book, environment, coin))
