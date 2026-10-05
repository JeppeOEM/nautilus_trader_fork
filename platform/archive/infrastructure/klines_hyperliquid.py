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
Hyperliquid's 1 m candles for the reconciliation (`VenueKlines`), `POST /info`
`candleSnapshot` over `kernel.venue_http`, parsed from its decimal strings exactly (D-52).
"""

from kernel.venue_http import HttpJson
from kernel.venue_http import http_json
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import post_json_request

from archive.domain.reconciliation import DAY_MS
from archive.domain.reconciliation import MINUTE_MS
from archive.domain.reconciliation import Kline
from archive.domain.reconciliation import kline_from_text
from archive.domain.reconciliation import next_kline_cursor
from archive.domain.reconciliation import unique_traded_in_day
from nautilus_trader.model.instruments import Instrument


def parse_hyperliquid_candles(payload: list, price_p: int, size_p: int) -> list[Kline]:
    """Parse Hyperliquid's `POST /info {"type": "candleSnapshot"}`."""
    return [
        kline_from_text(int(c["t"]), (c["o"], c["h"], c["l"], c["c"], c["v"]), price_p, size_p)
        for c in payload
    ]


class HyperliquidKlines:
    """
    `VenueKlines` for Hyperliquid.

    Invariant: see `archive.application.ports.VenueKlines` -- exact units, a repeated minute
    refused.
    """

    def __init__(self, environment: str, http: HttpJson = http_json) -> None:
        self._environment = environment
        self._http = http

    def fetch(self, inst: Instrument, day_ms: int) -> list[Kline]:
        """
        Fetch the day's candles, paging forward from the newest open seen.

        Verified (live 2026-09-21): `startTime`/`endTime` both inclusive on the candle's open
        time, oldest first; a whole day (1440) came back in one response, and the forward paging
        still reads everything should the server cap a response below a day.

        A page that does not move the cursor, or a day needing more than `MAX_KLINE_PAGES` pages,
        is a `KlineError` (`next_kline_cursor`): a venue repeating a full page is never looped on.
        """
        out: list[Kline] = []
        start_ms, end_ms, pages = day_ms, day_ms + DAY_MS - 1, 0
        while True:
            body = {
                "type": "candleSnapshot",
                "req": {
                    "coin": inst.raw_symbol.value,
                    "interval": "1m",
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            }
            request = post_json_request(hyperliquid_info_url(self._environment), body)
            page = parse_hyperliquid_candles(
                self._http(request), inst.price_precision, inst.size_precision
            )
            if not page:
                break
            out += page
            pages += 1
            after_newest = max(k.t_ms for k in page) + MINUTE_MS
            if after_newest > end_ms:
                break
            start_ms = next_kline_cursor(
                inst.id.value, pages, start_ms, after_newest, backwards=False
            )
        return unique_traded_in_day(out, inst.id.value, day_ms)
