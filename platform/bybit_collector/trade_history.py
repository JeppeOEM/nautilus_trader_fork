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
Bybit's REST trade history for the reconnect backfill (`VenueTradeHistory`, story 22.14).

Wire facts, verified live 2026-09-21 (fixtures `tests/fixtures/bybit_trades_*_20260921.json`):
`GET /v5/market/recent-trade?category=linear|spot&symbol=&limit=1000` returns
`result.list[{execId, price, size, side Buy|Sell (taker), time ms}]`, newest first, no paging.
Linear returned 1000 trades (28-64 s of BTCUSDT); **spot returns at most 60**, even with
`limit=1000` (1.3-7 s of BTCUSDT). The WS id is `i` (`websocket/parse.rs`).

Known limit: that depth (`CAPABILITIES`) bounds a backfill: a longer outage is reported
`unrecoverable` with the uncovered seconds. Upgrade path: `trade_feeds = 2` (a second, independent
trades-only connection), which closes a one-sided outage without REST at all.
Known limit: only the wire-verified categories; an `-INVERSE.BYBIT` id is refused, not guessed,
until its recent-trade depth is verified against the venue (then add it to `CAPABILITIES`).
"""

import urllib.parse
from typing import Any

from collector_core.domain.trade_history import BackfillCapability
from collector_core.domain.trade_history import BackfillError
from collector_core.domain.trade_history import Fetched
from collector_core.domain.trade_history import RowReader
from collector_core.domain.trade_history import collect
from collector_core.domain.trade_history import ms_to_ns
from collector_core.domain.trade_history import side_of
from collector_core.domain.trade_history import tick
from kernel.venue_http import BYBIT_URLS
from kernel.venue_http import HttpJson
from kernel.venue_http import bybit_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json
from kernel.venues import bybit_category

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.instruments import Instrument


# A response this long may have been cut by the venue's depth: its oldest trade bounds coverage.
CAPABILITIES = {"linear": BackfillCapability(1000), "spot": BackfillCapability(60)}
_REQUEST_LIMIT = 1000

_SIDES = {"Buy": AggressorSide.BUYER, "Sell": AggressorSide.SELLER}


def _rows(payload: Any) -> list[dict]:
    if not isinstance(payload, dict):
        raise BackfillError(f"bybit recent-trade response is not an object: {str(payload)[:200]}")
    if payload.get("retCode") != 0:
        raise BackfillError(f"bybit retCode {payload.get('retCode')}: {payload.get('retMsg')}")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("list"), list):
        raise BackfillError(f"bybit recent-trade response has no trade list: {str(payload)[:200]}")
    return result["list"]


def _time(row: dict) -> int:
    return ms_to_ns(row["time"])


def _trade(row: dict, instrument: Instrument, ts_init: int) -> TradeTick:
    side = side_of(_SIDES, row["side"])
    values = (row["price"], row["size"], side, str(row["execId"]), _time(row))
    return tick(instrument, values, ts_init)


READER = RowReader(_time, _trade)


def parse_bybit_trades(payload: Any, instrument: Instrument, ts_init: int) -> list[TradeTick]:
    """Parse Bybit's `recent-trade`, newest first as sent; `retCode != 0` or inexactness raises."""
    return [_trade(row, instrument, ts_init) for row in _rows(payload)]


class BybitTradeHistory:
    """
    `VenueTradeHistory` over Bybit's `recent-trade`. Invariant: a category without a
    wire-verified `CAPABILITIES` entry is refused (`BackfillError`), and a response as long as
    the category's depth covers `since` only if its oldest trade does.
    """

    def __init__(self, environment: str, http: HttpJson = http_json) -> None:
        if environment not in BYBIT_URLS:
            # At construction, not at the first backfill after a reconnect.
            raise ValueError(f"unknown Bybit environment {environment!r}: {sorted(BYBIT_URLS)}")
        self._environment = environment
        self._http = http

    def fetch(self, instrument: Instrument, since_ns: int, floor_ns: int, ts_init: int) -> Fetched:
        category = bybit_category(instrument.id.value)
        capability = CAPABILITIES.get(category)
        if capability is None:
            raise BackfillError(
                f"{instrument.id}: Bybit {category} trade backfill is not wire-verified"
            )
        url = bybit_url(
            self._environment,
            f"/v5/market/recent-trade?category={category}"
            f"&symbol={urllib.parse.quote(instrument.raw_symbol.value)}&limit={_REQUEST_LIMIT}",
        )
        rows = _rows(self._http(get_request(url)))
        return collect(rows, READER, instrument, since_ns, ts_init, not capability.cut(len(rows)))
