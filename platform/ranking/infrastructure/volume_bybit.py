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
Bybit's USD 24 h volume sources, linear and spot (moved from `ranking_engine/engine.py` in Story
25.2): the public v5 market tickers' `turnover24h`, requested through `kernel.venue_http`.
"""

import asyncio
from types import MappingProxyType

from kernel.venue_http import bybit_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json
from observability import error_ledger

from ranking.domain.values import parse_usd_volume


# Bybit tickers `category` -> the Nautilus Bybit adapter's instrument id product suffix.
BYBIT_ID_SUFFIX = MappingProxyType({"linear": "LINEAR", "spot": "SPOT"})

# Known limit: Bybit spot `turnover24h` is denominated in the pair's quote asset, so only
# USDT/USDC-quoted pairs are kept and the stablecoin is taken at USD par (a depeg skews their
# volume by the depeg ratio). A non-USD-quoted spot pair (e.g. ETHBTC) has no USD volume at all and
# is left out of volume mode; if one is ever collected, the per-iid missing-volume ledger surfaces
# it. Upgrade path: convert such a pair's volume to USD via a USD price of one of its assets (the
# spot tickers row already carries `usdIndexPrice`, the base asset's USD index -- `volume24h` (base
# units) x `usdIndexPrice` would do, once verified against Bybit's docs for every pair). Linear
# contracts are all USDT- or USDC-settled, so their turnover is USD at the same par assumption.
BYBIT_SPOT_USD_QUOTES: tuple[str, ...] = ("USDT", "USDC")


def _is_bybit_usd_symbol(symbol: str, category: str) -> bool:
    return category != "spot" or symbol.endswith(BYBIT_SPOT_USD_QUOTES)


def _bybit_ticker_rows(tickers_json: dict) -> list:
    """
    Return the `result.list` rows of a successful Bybit v5 response; ValueError otherwise.

    Bybit answers errors (rate limit, maintenance) with HTTP 200, `retCode != 0` and an empty
    `result` -- read naively that is a successful poll with no volumes, which would replace the
    last good values and drop every Bybit row out of volume mode at once.
    """
    ret_code = tickers_json.get("retCode")
    rows = (tickers_json.get("result") or {}).get("list")
    if ret_code != 0 or not isinstance(rows, list):
        raise ValueError(
            f"Bybit tickers error retCode={ret_code!r} retMsg={tickers_json.get('retMsg')!r}"
        )
    return rows


def parse_bybit_volume_24h(tickers_json: dict, product_type: str) -> dict[str, float]:
    """
    Parse turnover24h (USD) per instrument from a Bybit v5 tickers response.

    `product_type` is the request's category ("linear" or "spot"); ids follow the Nautilus Bybit
    adapter's "{symbol}-LINEAR.BYBIT" / "{symbol}-SPOT.BYBIT" format, so linear and spot stay
    separate rows and are never summed.
    """
    suffix = BYBIT_ID_SUFFIX[product_type]
    rows = _bybit_ticker_rows(tickers_json)
    result: dict[str, float] = {}
    for row in rows:
        symbol = row.get("symbol")
        if not symbol or not _is_bybit_usd_symbol(symbol, product_type):
            continue
        iid = f"{symbol}-{suffix}.BYBIT"
        try:
            result[iid] = parse_usd_volume(row.get("turnover24h"))
        except (ValueError, TypeError) as exc:
            error_ledger.record(
                "ranking_engine.volume24h",
                f"{iid}: unparseable turnover24h {row.get('turnover24h')!r}",
                exc,
            )
    return result


class BybitVolumeSource:
    """
    `VolumeSource` for one Bybit category ("linear" or "spot"). Invariant: linear and spot are two
    sources with disjoint ids, so a failed spot poll never touches the linear rows.
    """

    def __init__(self, environment: str, category: str) -> None:
        if category not in BYBIT_ID_SUFFIX:
            raise ValueError(f"Bybit category {category!r} is not one of {sorted(BYBIT_ID_SUFFIX)}")
        self._environment = environment
        self._category = category
        self.name = f"bybit-{category}"

    async def fetch(self) -> dict[str, float]:
        url = bybit_url(self._environment, f"/v5/market/tickers?category={self._category}")
        tickers_json = await asyncio.to_thread(http_json, get_request(url))
        return parse_bybit_volume_24h(tickers_json, self._category)
