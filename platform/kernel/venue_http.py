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
The venue REST transport (DDD spine AD-D3): the venue URL maps, the default `USER_AGENT`, the
timeout, the stdlib JSON transport and the request builders every venue REST request is built
with -- the collectors' polls, the reconnect trade backfill (`trade_backfill`, story 22.14) and
the kline reconciliation (`compare_klines`, story 22.13) -- plus the venues' public WebSocket URLs
(`bybit_ws_url`, `hyperliquid_ws_url`, Story 31.1: the verification context's reference recorder).

Invariant: one place holds every venue REST URL, so two contexts can never send the same
request to two different hosts or with two different encodings; a literal venue URL outside
the kernel fails `platform/tests/test_boundaries.py` (the ranking volume polls' own maps retired
in Story 25.2). Every built request is `https` -- the builders take a `url: str` rather than a
visible literal, so the scheme each `# noqa: S310` asserts is checked here instead.

Stdlib `urllib` only: no extra dependency, and the pyo3 HTTP clients parse decimals through
`f64` (audit D-52), which cannot prove raw-unit equality. This module imports nothing outside the
standard library -- not even `nautilus_trader` -- so the verification context's independent
reference recorder can build its URLs here without reaching `nautilus_pyo3` (Story 31.1); the
dYdX indexer's URLs, which come from the pyo3 bindings, live in `kernel.dydx_http`.
"""

import json
import urllib.request
from collections.abc import Callable
from types import MappingProxyType
from typing import Any


BYBIT_URLS = MappingProxyType(
    {"mainnet": "https://api.bybit.com", "testnet": "https://api-testnet.bybit.com"}
)
HYPERLIQUID_URLS = MappingProxyType(
    {
        "mainnet": "https://api.hyperliquid.xyz/info",
        "testnet": "https://api.hyperliquid-testnet.xyz/info",
    }
)
# Public market-data WebSockets (Story 31.1). Bybit serves one socket per product category
# (`/v5/public/linear`, `/v5/public/spot`, ...); Hyperliquid one socket for everything.
BYBIT_WS_URLS = MappingProxyType(
    {
        "mainnet": "wss://stream.bybit.com/v5/public",
        "testnet": "wss://stream-testnet.bybit.com/v5/public",
    }
)
BYBIT_WS_CATEGORIES = frozenset({"linear", "inverse", "spot"})
HYPERLIQUID_WS_URLS = MappingProxyType(
    {
        "mainnet": "wss://api.hyperliquid.xyz/ws",
        "testnet": "wss://api.hyperliquid-testnet.xyz/ws",
    }
)
USER_AGENT = "nautilus-platform-reconcile/1.0"  # dYdX's indexer rejects urllib's default (403)
# Bounds each socket operation (connect, each read), not a whole response.
TIMEOUT_S = 30

HttpJson = Callable[[urllib.request.Request], Any]


def http_json(request: urllib.request.Request) -> Any:
    """Send the request and decode the JSON response body."""
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310 (https)
        return json.load(response)


def _venue_url(url: str) -> str:
    """Return the URL, refusing a non-`https` one -- what the builders' `# noqa: S310` claims."""
    if not url.startswith("https://"):
        raise ValueError(f"venue URL must be https: {url!r}")
    return url


def get_request(url: str, user_agent: str = USER_AGENT) -> urllib.request.Request:
    """Build a GET to a venue URL, carrying a real User-Agent."""
    return urllib.request.Request(_venue_url(url), headers={"User-Agent": user_agent})  # noqa: S310


def post_json_request(
    url: str, body: object, user_agent: str = USER_AGENT
) -> urllib.request.Request:
    """Build a POST of `body` as JSON (`json.dumps` defaults) to a venue URL."""
    return urllib.request.Request(  # noqa: S310 (`_venue_url` refuses a non-https scheme)
        _venue_url(url),
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": user_agent},
    )


def rooted_path(path_and_query: str) -> str:
    """
    Return the path, refusing one without a leading `/` (bare, `v5/...` names another host).
    Shared with `kernel.dydx_http`, so every venue URL builder applies the one rule.
    """
    if not path_and_query.startswith("/"):
        raise ValueError(f"venue path must start with '/': {path_and_query!r}")
    return path_and_query


def bybit_url(environment: str, path_and_query: str) -> str:
    """Return `https://api.bybit.com` (or testnet) + `path_and_query` (`/v5/...`)."""
    return f"{BYBIT_URLS[environment]}{rooted_path(path_and_query)}"


def hyperliquid_info_url(environment: str) -> str:
    """Return Hyperliquid's one `info` endpoint for the environment."""
    return HYPERLIQUID_URLS[environment]


def _ws_base(urls: MappingProxyType[str, str], environment: str) -> str:
    """Return the environment's WebSocket base, refusing an unknown environment by name."""
    if environment not in urls:
        raise ValueError(f"unknown environment {environment!r}; expected one of {sorted(urls)}")
    return urls[environment]


def bybit_ws_url(environment: str, category: str) -> str:
    """Return Bybit's public WebSocket for `category` (`linear`/`inverse`/`spot`)."""
    if category not in BYBIT_WS_CATEGORIES:
        raise ValueError(
            f"unknown Bybit WS category {category!r}; expected one of {sorted(BYBIT_WS_CATEGORIES)}"
        )
    return f"{_ws_base(BYBIT_WS_URLS, environment)}/{category}"


def hyperliquid_ws_url(environment: str) -> str:
    """Return Hyperliquid's one public WebSocket for the environment."""
    return _ws_base(HYPERLIQUID_WS_URLS, environment)
