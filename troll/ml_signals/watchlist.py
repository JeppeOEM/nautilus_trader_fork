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

Deliberately kept separate from dashboard.py: a backtest script or Jupyter notebook
that only wants the current coin-set should not have to import aiohttp, plotly,
redis, or every indicator class just to call fetch_watchlist().
"""

import json
import urllib.request


def fetch_watchlist(dashboard_url: str = "http://127.0.0.1:8765") -> list[str]:
    """
    Fetch the current live Watchlist coin-set from a running dashboard.

    Requires the ml_signals dashboard process to be up and reachable at dashboard_url --
    the live Watchlist only exists in that process's memory (fed from Redis), there is
    no other queryable store of "current live ranking" yet (see Story 1.4 for history).
    """
    url = f"{dashboard_url}/api/watchlist"
    request = urllib.request.Request(url)  # noqa: S310 (local dashboard, fixed scheme)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.load(response)["instrument_ids"]
