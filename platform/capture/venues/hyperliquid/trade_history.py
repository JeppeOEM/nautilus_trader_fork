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
Hyperliquid's REST trade history for the reconnect backfill (`VenueTradeHistory`, story 22.14).

Wire facts, verified live 2026-09-21 (fixture `tests/fixtures/hyperliquid_recent_trades_btc_20260921.json`):
`POST /info {"type": "recentTrades", "coin"}` returns `[{coin, side B|A, px, sz, time ms, tid}]`,
newest first, **exactly the last 10 trades**; `startTime` is ignored. The WS id is `tid`.

Known limit: that 10-trade depth (`CAPABILITY`) means a backfill covers only a very short outage;
the rest is reported `unrecoverable`. Upgrade path: `trade_feeds = 2`.
"""

from typing import Any

from kernel.venue_http import HYPERLIQUID_URLS
from kernel.venue_http import HttpJson
from kernel.venue_http import http_json
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import post_json_request

from capture.domain.trade_history import BackfillCapability
from capture.domain.trade_history import BackfillError
from capture.domain.trade_history import Fetched
from capture.domain.trade_history import RowReader
from capture.domain.trade_history import collect
from capture.domain.trade_history import ms_to_ns
from capture.domain.trade_history import side_of
from capture.domain.trade_history import tick
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.instruments import Instrument


CAPABILITY = BackfillCapability(rows_per_page=10)

_SIDES = {"B": AggressorSide.BUYER, "A": AggressorSide.SELLER}


def _rows(payload: Any) -> list[dict]:
    if not isinstance(payload, list):
        raise BackfillError(f"hyperliquid recentTrades is not a list: {str(payload)[:200]}")
    return payload


def _time(row: dict) -> int:
    return ms_to_ns(row["time"])


def _trade(row: dict, instrument: Instrument, ts_init: int) -> TradeTick:
    side = side_of(_SIDES, row["side"])
    return tick(instrument, (row["px"], row["sz"], side, str(row["tid"]), _time(row)), ts_init)


READER = RowReader(_time, _trade)


def parse_hyperliquid_trades(payload: Any, instrument: Instrument, ts_init: int) -> list[TradeTick]:
    """Parse Hyperliquid's `recentTrades`, newest first as sent; an inexact value raises."""
    return [_trade(row, instrument, ts_init) for row in _rows(payload)]


class HyperliquidTradeHistory:
    """
    `VenueTradeHistory` over Hyperliquid's `recentTrades`. Invariant: a 10-row response may be
    cut by the venue's depth, so it covers `since` only if its oldest trade does.
    """

    capability = CAPABILITY

    def __init__(self, environment: str, http: HttpJson = http_json) -> None:
        if environment not in HYPERLIQUID_URLS:
            # At construction, not at the first backfill after a reconnect.
            raise ValueError(
                f"unknown Hyperliquid environment {environment!r}: {sorted(HYPERLIQUID_URLS)}"
            )
        self._environment = environment
        self._http = http

    def fetch(self, instrument: Instrument, since_ns: int, floor_ns: int, ts_init: int) -> Fetched:
        body = {"type": "recentTrades", "coin": instrument.raw_symbol.value}
        request = post_json_request(hyperliquid_info_url(self._environment), body)
        rows = _rows(self._http(request))
        return collect(rows, READER, instrument, since_ns, ts_init, not CAPABILITY.cut(len(rows)))
