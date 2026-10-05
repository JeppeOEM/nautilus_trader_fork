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
dYdX's 1 m candles for the reconciliation (`VenueKlines`), from the public indexer over
`kernel.venue_http`, parsed from its decimal strings exactly (never through float, D-52).
"""

import urllib.parse
from datetime import UTC
from datetime import datetime

from kernel.dydx_http import DYDX_NETWORKS
from kernel.dydx_http import dydx_indexer_url
from kernel.venue_http import HttpJson
from kernel.venue_http import get_request
from kernel.venue_http import http_json

from archive.domain.reconciliation import DAY_MS
from archive.domain.reconciliation import MINUTE_MS
from archive.domain.reconciliation import Kline
from archive.domain.reconciliation import kline_from_text
from archive.domain.reconciliation import next_kline_cursor
from archive.domain.reconciliation import unique_traded_in_day
from nautilus_trader.model.instruments import Instrument


_PAGE = 1000
# A non-final page is a full `_PAGE` rows of distinct minutes, so a day's 1440 minutes need at most
# ceil(1440 / _PAGE) pages; one more is margin. Beyond it the venue is not paging as verified.
_MAX_PAGES = -(-(DAY_MS // MINUTE_MS) // _PAGE) + 1


def _iso_ms(text: str) -> int:
    return int(datetime.fromisoformat(text).timestamp() * 1000)


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_dydx_candles(payload: dict, price_p: int, size_p: int) -> list[Kline]:
    """Parse the dYdX indexer's `GET /v4/candles/perpetualMarkets/{ticker}` (any order)."""
    return [
        kline_from_text(
            _iso_ms(c["startedAt"]),
            (c["open"], c["high"], c["low"], c["close"], c["baseTokenVolume"]),
            price_p,
            size_p,
        )
        for c in payload["candles"]
    ]


class DydxKlines:
    """
    `VenueKlines` for dYdX (mainnet/testnet indexer).

    Invariant: see `archive.application.ports.VenueKlines` -- exact units, a repeated minute
    refused.
    """

    def __init__(self, environment: str, http: HttpJson = http_json) -> None:
        self._network = DYDX_NETWORKS[environment]
        self._http = http

    def fetch(self, inst: Instrument, day_ms: int) -> list[Kline]:
        """
        Fetch the day's candles, paging backwards with `toISO` = the oldest `startedAt` seen.

        Verified (live 2026-09-21 on 2026-09-20 data): `fromISO` inclusive, `toISO` exclusive,
        newest first, `limit` at most 1000.

        A page that does not move the cursor, or a day needing more than `_MAX_PAGES` pages (what
        `_PAGE` implies), is a `KlineError` (`next_kline_cursor`): a venue repeating or barely
        advancing a full page is never looped on.
        """
        ticker = urllib.parse.quote(inst.raw_symbol.value)
        out: list[Kline] = []
        to_ms, pages = day_ms + DAY_MS, 0
        while True:
            url = dydx_indexer_url(
                self._network,
                f"/v4/candles/perpetualMarkets/{ticker}?resolution=1MIN"
                f"&fromISO={_iso(day_ms)}&toISO={_iso(to_ms)}&limit={_PAGE}",
            )
            page = parse_dydx_candles(
                self._http(get_request(url)), inst.price_precision, inst.size_precision
            )
            out += page
            pages += 1
            if len(page) < _PAGE or min(k.t_ms for k in page) <= day_ms:
                return unique_traded_in_day(out, inst.id.value, day_ms)
            oldest = min(k.t_ms for k in page)
            to_ms = next_kline_cursor(
                inst.id.value, pages, to_ms, oldest, backwards=True, max_pages=_MAX_PAGES
            )
