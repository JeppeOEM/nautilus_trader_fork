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
Bybit's 1 m klines for the reconciliation (`VenueKlines`), `GET /v5/market/kline` over
`kernel.venue_http`, parsed from its decimal strings exactly (never through float, D-52).
"""

import urllib.parse

from kernel.venue_http import HttpJson
from kernel.venue_http import bybit_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json
from kernel.venues import bybit_category

from archive.domain.reconciliation import DAY_MS
from archive.domain.reconciliation import MINUTE_MS
from archive.domain.reconciliation import Kline
from archive.domain.reconciliation import KlineError
from archive.domain.reconciliation import kline_from_text
from archive.domain.reconciliation import next_kline_cursor
from archive.domain.reconciliation import unique_traded_in_day
from nautilus_trader.model.instruments import Instrument


_PAGE = 1000
# A non-final page is a full `_PAGE` rows of distinct minutes, so a day's 1440 minutes need at most
# ceil(1440 / _PAGE) pages; one more is margin. Beyond it the venue is not paging as verified.
_MAX_PAGES = -(-(DAY_MS // MINUTE_MS) // _PAGE) + 1
# Known limit: inverse klines report volume in contracts, not the base asset our fold sums; an
# `-INVERSE.BYBIT` id is refused until its units are verified against the venue (then add it).
BYBIT_KLINE_CATEGORIES = frozenset({"linear", "spot"})


def parse_bybit_klines(payload: dict, price_p: int, size_p: int) -> list[Kline]:
    """Parse Bybit's `GET /v5/market/kline`; `retCode != 0` is an error."""
    if payload.get("retCode") != 0:
        raise KlineError(f"bybit retCode {payload.get('retCode')}: {payload.get('retMsg')}")
    return [
        kline_from_text(int(row[0]), (row[1], row[2], row[3], row[4], row[5]), price_p, size_p)
        for row in payload["result"]["list"]
    ]


def kline_category(iid: str) -> str:
    """Return the id's wire-verified kline `category`; `KlineError` for any other."""
    try:
        category = bybit_category(iid)
    except ValueError as e:
        raise KlineError(f"{iid}: no Bybit kline category for this id suffix") from e
    if category not in BYBIT_KLINE_CATEGORIES:
        raise KlineError(f"{iid}: Bybit {category} klines are not wire-verified")
    return category


class BybitKlines:
    """
    `VenueKlines` for Bybit linear and spot.

    Invariant: see `archive.application.ports.VenueKlines` -- exact units, a repeated minute
    refused, and only wire-verified categories (`BYBIT_KLINE_CATEGORIES`).
    """

    def __init__(self, environment: str, http: HttpJson = http_json) -> None:
        self._environment = environment
        self._http = http

    def fetch(self, inst: Instrument, day_ms: int) -> list[Kline]:
        """
        Fetch the day's klines, paging backwards with `end` = the oldest start seen - 1 ms.

        Verified (live 2026-09-21): `start` and `end` both inclusive on the kline's start time,
        newest first, `limit` up to 1000.

        A page that does not move the cursor, or a day needing more than `_MAX_PAGES` pages (what
        `_PAGE` implies), is a `KlineError` (`next_kline_cursor`): a venue repeating or barely
        advancing a full page is never looped on.
        """
        category = kline_category(inst.id.value)
        out: list[Kline] = []
        end_ms, pages = day_ms + DAY_MS - 1, 0
        while True:
            url = bybit_url(
                self._environment,
                f"/v5/market/kline?category={category}"
                f"&symbol={urllib.parse.quote(inst.raw_symbol.value)}&interval=1"
                f"&start={day_ms}&end={end_ms}&limit={_PAGE}",
            )
            page = parse_bybit_klines(
                self._http(get_request(url)), inst.price_precision, inst.size_precision
            )
            out += page
            pages += 1
            if len(page) < _PAGE or min(k.t_ms for k in page) <= day_ms:
                return unique_traded_in_day(out, inst.id.value, day_ms)
            oldest = min(k.t_ms for k in page)
            end_ms = next_kline_cursor(
                inst.id.value, pages, end_ms, oldest - 1, backwards=True, max_pages=_MAX_PAGES
            )
