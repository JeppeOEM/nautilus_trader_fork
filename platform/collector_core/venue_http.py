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
Venue REST endpoints and the stdlib JSON transport shared by the kline reconciliation
(`compare_klines`, story 22.13) and the trade backfill (`trade_backfill`, story 22.14).

Stdlib `urllib` only, as every other venue REST poll in the collectors: no extra dependency, and
the pyo3 HTTP clients parse decimals through `f64` (audit D-52), which cannot prove raw-unit
equality.
"""

import json
import urllib.request
from collections.abc import Callable
from typing import Any

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


BYBIT_URLS = {"mainnet": "https://api.bybit.com", "testnet": "https://api-testnet.bybit.com"}
HYPERLIQUID_URLS = {
    "mainnet": "https://api.hyperliquid.xyz/info",
    "testnet": "https://api.hyperliquid-testnet.xyz/info",
}
DYDX_NETWORKS = {"mainnet": DydxNetwork.MAINNET, "testnet": DydxNetwork.TESTNET}
USER_AGENT = "nautilus-platform-reconcile/1.0"  # dYdX's indexer rejects urllib's default (403)

HttpJson = Callable[[urllib.request.Request], Any]


def http_json(request: urllib.request.Request) -> Any:
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 (fixed https URLs)
        return json.load(response)


def bybit_category(iid: str) -> str:
    """Bybit's REST `category` for a Nautilus Bybit id; `ValueError` for any other suffix."""
    if iid.endswith("-LINEAR.BYBIT"):
        return "linear"
    if iid.endswith("-SPOT.BYBIT"):
        return "spot"
    raise ValueError(f"{iid}: no Bybit category for this id suffix")
