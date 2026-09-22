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
dYdX open interest: the one field dropped by the Rust/PyO3 adapter bindings on
both the REST and WebSocket markets-channel paths (confirmed by reading
crates/adapters/dydx/src/python/{http,websocket}.rs -- `open_interest` is parsed
Rust-side but never forwarded to Python). Fetched here with a plain stdlib REST
poll against the same public indexer endpoint, since stdlib already covers a
single infrequent GET (no need for a dependency that may not even be in the
production image -- aiohttp is a `test`-only extra of nautilus_trader, not a
runtime dependency).

"""

import asyncio
import time
from decimal import Decimal

from kernel.open_interest import OpenInterest
from kernel.venue_http import dydx_indexer_url
from kernel.venue_http import get_request
from kernel.venue_http import http_json
from observability import error_ledger

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.model.identifiers import InstrumentId


def classify_liquidity(
    markets_json: dict,
    min_volume_usd: float,
    exclude: frozenset[str] | None = None,
    max_liquid: int | None = None,
) -> tuple[set[str], set[str]]:
    """
    Split all dYdX markets into (liquid, illiquid) by 24-hour volume (USD).

    Uses volume24H (already in USD) rather than openInterest (base-token units).
    openInterest is in tokens, not USD — comparing it directly to a USD threshold
    incorrectly marks BTC/ETH/SOL as illiquid (BTC=458 tokens < 100_000).

    Instruments in `exclude` are placed in illiquid regardless of volume.

    `max_liquid`, if given, keeps only the highest-volume markets in `liquid` and
    demotes the rest to `illiquid` -- the caller is responsible for sizing this to
    leave room for pinned instruments (see collector.py's _MAX_WS_SUBSCRIPTIONS:
    dYdX's WS connection hard-caps subscriptions per channel at 32, so subscribing
    more markets than that gets the overflow rejected and the whole connection
    stuck in a reconnect loop, not just those markets skipped).

    Returns sets of instrument ID strings (`"{ticker}-PERP.DYDX"` format).
    Markets with missing or unparseable volume are treated as illiquid.
    """
    _exclude = exclude or frozenset()
    illiquid: set[str] = set()
    volumes: dict[str, float] = {}
    for market in markets_json.get("markets", {}).values():
        ticker = market.get("ticker")
        if ticker is None:
            continue
        iid = f"{ticker}-PERP.DYDX"
        if iid in _exclude:
            illiquid.add(iid)
            continue
        try:
            vol = float(market.get("volume24H") or 0)
        except (ValueError, TypeError) as exc:
            # No volume is not zero volume (DATA-01): an unparseable value must not silently
            # reclassify a coin as illiquid. Leave it unclassified, loudly.
            error_ledger.record(
                "open_interest.volume24h",
                f"{iid}: unparseable volume24H {market.get('volume24H')!r}",
                exc,
            )
            continue
        if vol >= min_volume_usd:
            volumes[iid] = vol
        else:
            illiquid.add(iid)

    if max_liquid is not None and len(volumes) > max_liquid:
        overflow = sorted(volumes, key=volumes.get)[: len(volumes) - max_liquid]
        illiquid.update(overflow)
        for iid in overflow:
            del volumes[iid]

    return set(volumes), illiquid


def _fetch_markets_json(network: DydxNetwork) -> dict:
    # dYdX's indexer rejects urllib's default User-Agent (403); needs a real one.
    url = dydx_indexer_url(network, "/v4/perpetualMarkets")
    return http_json(get_request(url, "nautilus-dydx-collector/1.0"))


async def fetch_open_interest(network: DydxNetwork) -> list[OpenInterest]:
    """Poll dYdX's REST indexer for current open interest across all markets."""
    markets_json = await asyncio.to_thread(_fetch_markets_json, network)
    return parse_open_interest(markets_json, ts=time.time_ns())


def parse_open_interest(markets_json: dict, ts: int) -> list[OpenInterest]:
    items = []
    for market in markets_json.get("markets", {}).values():
        ticker = market.get("ticker")
        open_interest = market.get("openInterest")
        if ticker is None or open_interest is None:
            continue
        items.append(
            OpenInterest(
                instrument_id=InstrumentId.from_str(f"{ticker}-PERP.DYDX"),
                open_interest=Decimal(open_interest),
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return items
