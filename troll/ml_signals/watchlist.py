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
Lean, dependency-light fetch helper for the live Watchlist (FR-7).

Deliberately dependency-light: a backtest script or Jupyter notebook
that only wants the current coin-set should not have to import aiohttp, plotly,
redis, or every indicator class just to call fetch_watchlist().
"""

import json
import urllib.request


def fetch_watchlist(data_api_url: str = "http://127.0.0.1:9100") -> list[str]:
    """
    Fetch the current live Watchlist coin-set from a running data_api.

    Requires data_api to be up and reachable at data_api_url -- the live Watchlist only
    exists as ranking_engine's published rankings, which data_api relays at /api/rankings
    (503 until its first message arrives, which raises here rather than returning []).
    """
    url = f"{data_api_url.rstrip('/')}/api/rankings"
    request = urllib.request.Request(url)  # noqa: S310 (local data_api, not a remote host)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        items = json.load(response)["items"]
    return [r["instrument_id"] for r in items if isinstance(r, dict) and "instrument_id" in r]
