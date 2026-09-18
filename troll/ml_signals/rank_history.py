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
Lean, dependency-light fetch helper for historical coin ranking (FR-8).

Deliberately dependency-light: a research script or Jupyter notebook that only wants a
past rank at a timestamp should not have to import aiohttp, plotly, redis, or every
indicator class just to call fetch_rank_history(). Reads data_api's /api/metrics/nearest.
"""

import json
import time
import urllib.parse
import urllib.request


def fetch_rank_history(
    instrument_id: str,
    ts_ns: int | None = None,
    data_api_url: str = "http://127.0.0.1:9100",
) -> dict:
    """
    Fetch the rank/volume24h snapshot for instrument_id nearest to ts_ns (or the most
    recent one if ts_ns is omitted) from a running data_api.

    Requires data_api to be up and reachable at data_api_url -- historical ranking is
    persisted to its metrics.db, there is no other queryable store of it. Returns {} if
    there is no row.
    """
    iid = urllib.parse.quote(instrument_id, safe="")
    ts_ns = time.time_ns() if ts_ns is None else ts_ns
    url = f"{data_api_url.rstrip('/')}/api/metrics/nearest/{iid}?ts_ns={ts_ns}"
    request = urllib.request.Request(url)  # noqa: S310 (local data_api, not a remote host)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.load(response) or {}
