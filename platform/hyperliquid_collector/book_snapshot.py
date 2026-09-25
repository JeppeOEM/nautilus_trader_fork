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
aligned live-book cross-check (story 22.5, audit D-64). Stdlib only, same as
`dydx_collector.open_interest`. The response's `time` (ms) is the alignment key: the WS
`l2Book` push carries the same `time` as its `ts_event`, and REST fetched right after a push
answers with that `time` whenever no later block changed the book.
"""

import asyncio
from decimal import Decimal

from collector_core.book_check import BookSnapshot
from collector_core.book_check import Level
from kernel.venue_http import http_json
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import post_json_request


_USER_AGENT = "nautilus-hl-collector/1.0"


def _post_l2_book(environment: str, coin: str) -> dict:
    body = {"type": "l2Book", "coin": coin}
    return http_json(post_json_request(hyperliquid_info_url(environment), body, _USER_AGENT))


def _levels(raw: list[dict]) -> list[Level]:
    return [(float(Decimal(lv["px"])), float(Decimal(lv["sz"]))) for lv in raw]


def parse_l2_book(payload: dict) -> BookSnapshot:
    """`levels[0]` = bids, `levels[1]` = asks (best-first `{px, sz}` strings); `time` in ms."""
    bids, asks = payload["levels"]
    return BookSnapshot(
        bids=_levels(bids), asks=_levels(asks), ts_event_ns=int(payload["time"]) * 1_000_000
    )


async def fetch_l2_book(environment: str, coin: str) -> BookSnapshot:
    return parse_l2_book(await asyncio.to_thread(_post_l2_book, environment, coin))
