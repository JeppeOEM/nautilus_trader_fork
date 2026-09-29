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
dYdX's REST trade history for the reconnect backfill (`VenueTradeHistory`, story 22.14).

Wire facts, verified live 2026-09-21 (fixture `tests/fixtures/dydx_trades_btc_usd_20260921.json`):
`GET {indexer}/v4/trades/perpetualMarket/{ticker}?limit=1000[&createdBeforeOrAt=<iso>]` returns
`{"trades": [{id, side BUY|SELL, size, price, createdAt ISO-8601 ms}]}`, newest first;
`createdBeforeOrAt` is inclusive, so pages overlap by id. The WS parser
(`crates/adapters/dydx/src/websocket/parse.rs`) uses the same `id` and the nanoseconds of the
same `createdAt`. The indexer rejects urllib's default User-Agent (403).

Known limit: dYdX is paged back no further than `floor_ns` (the caller's fetch time minus
`kernel.clocks.MAX_TS_INIT_SKEW_NS`, 5 minutes) and never beyond `CAPABILITY.max_pages`: the
nightly rebuild and the prune find trades by `ts_init` within that margin of `ts_event`, so an
older backfilled trade would be invisible to them. A dYdX outage longer than 5 minutes stays partly
unrecovered, and says so. Upgrade path: a backfill-span marker (like `archive_gaps`) that the
rebuild and prune read to widen their window.

Known limit: dYdX pages by `createdAt` (block time, shared by every trade of a block), so a block
with more than 1000 trades of one market makes a page with no progress; paging stops there and the
rest is reported `unrecoverable`. Upgrade path: page on `createdBeforeOrAtHeight` below the block.
"""

import urllib.parse
from typing import Any

from kernel.dydx_http import DYDX_NETWORKS
from kernel.dydx_http import dydx_indexer_url
from kernel.venue_http import HttpJson
from kernel.venue_http import get_request
from kernel.venue_http import http_json

from capture.domain.trade_history import BackfillCapability
from capture.domain.trade_history import BackfillError
from capture.domain.trade_history import Fetched
from capture.domain.trade_history import RowReader
from capture.domain.trade_history import collect
from capture.domain.trade_history import iso_to_ns
from capture.domain.trade_history import side_of
from capture.domain.trade_history import tick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.instruments import Instrument


CAPABILITY = BackfillCapability(rows_per_page=1000, max_pages=20)

_SIDES = {"BUY": AggressorSide.BUYER, "SELL": AggressorSide.SELLER}


def _rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("trades"), list):
        raise BackfillError(f"dydx trades response has no trade list: {str(payload)[:200]}")
    return payload["trades"]


def _time(row: dict) -> int:
    return iso_to_ns(row["createdAt"])


def _trade(row: dict, instrument: Instrument, ts_init: int) -> TradeTick:
    side = side_of(_SIDES, row["side"])
    return tick(instrument, (row["price"], row["size"], side, str(row["id"]), _time(row)), ts_init)


READER = RowReader(_time, _trade)


def parse_dydx_trades(payload: Any, instrument: Instrument, ts_init: int) -> list[TradeTick]:
    """Parse the dYdX indexer's trades, newest first as sent; an inexact value raises."""
    return [_trade(row, instrument, ts_init) for row in _rows(payload)]


class DydxTradeHistory:
    """
    `VenueTradeHistory` over the dYdX indexer. Invariant: paging stops, with the history
    `reached`, at a short page or once the oldest trade is at or before `since_ns`; without it,
    once the oldest is older than `floor_ns`, when a full page makes no progress, or after
    `CAPABILITY.max_pages` -- the uncovered span is then reported, never assumed recovered.
    """

    capability = CAPABILITY

    def __init__(self, environment: str, http: HttpJson = http_json) -> None:
        if environment not in DYDX_NETWORKS:
            # At construction, not at the first backfill after a reconnect.
            raise ValueError(f"unknown dYdX environment {environment!r}: {sorted(DYDX_NETWORKS)}")
        self._environment = environment
        self._http = http

    def fetch(self, instrument: Instrument, since_ns: int, floor_ns: int, ts_init: int) -> Fetched:
        rows, reached = self._pages(instrument, since_ns, floor_ns)
        return collect(rows, READER, instrument, since_ns, ts_init, reached)

    def _page_url(self, instrument: Instrument, before: str | None) -> str:
        ticker = urllib.parse.quote(instrument.raw_symbol.value)
        url = dydx_indexer_url(
            DYDX_NETWORKS[self._environment],
            f"/v4/trades/perpetualMarket/{ticker}?limit={CAPABILITY.rows_per_page}",
        )
        if before is not None:
            url += f"&createdBeforeOrAt={urllib.parse.quote(before)}"
        return url

    def _pages(
        self, instrument: Instrument, since_ns: int, floor_ns: int
    ) -> tuple[list[dict], bool]:
        """Page backwards with `createdBeforeOrAt` = the oldest `createdAt` seen (see class)."""
        rows: dict[str, dict] = {}
        before: str | None = None
        for _ in range(CAPABILITY.max_pages):
            page = _rows(self._http(get_request(self._page_url(instrument, before))))
            for row in page:
                key = str(row.get("id")) if isinstance(row, dict) else repr(row)
                rows.setdefault(key, row)
            if not CAPABILITY.cut(len(page)):
                return list(rows.values()), True
            timed = [(t, row) for row in page if (t := READER.time_or_none(row)) is not None]
            if not timed:
                return list(rows.values()), False
            oldest_ns, oldest = min(timed, key=lambda pair: pair[0])
            if oldest_ns <= since_ns:
                return list(rows.values()), True
            if oldest_ns < floor_ns or oldest["createdAt"] == before:
                return list(rows.values()), False
            before = oldest["createdAt"]
        return list(rows.values()), False
