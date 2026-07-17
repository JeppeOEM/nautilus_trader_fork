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

Deliberately kept separate from dashboard.py: a research script or Jupyter notebook
that only wants a past rank at a timestamp should not have to import aiohttp, plotly,
redis, or every indicator class just to call fetch_rank_history().
"""

import datetime
import json
import urllib.parse
import urllib.request


def fetch_rank_history(
    instrument_id: str,
    ts_ns: int | None = None,
    dashboard_url: str = "http://127.0.0.1:8765",
) -> dict:
    """
    Fetch the rank/volume24h snapshot for instrument_id nearest to ts_ns (or the most
    recent one if ts_ns is omitted) from a running dashboard.

    Requires the ml_signals dashboard process to be up and reachable at dashboard_url --
    historical ranking is persisted to that process's metrics.db, there is no other
    queryable store of it.
    """
    iid = urllib.parse.quote(instrument_id, safe="")
    url = f"{dashboard_url.rstrip('/')}/api/rank_history/{iid}"
    if ts_ns is not None:
        # ISO 8601, matching the dashboard's existing ?start=/?end= query-param convention
        # (coin_candles_handler/chart_handler) rather than a raw epoch number. Must be
        # percent-encoded: a raw "+" in the UTC offset (e.g. "+00:00") is decoded as a
        # space by the server's query-string parser, corrupting the timestamp.
        iso = datetime.datetime.fromtimestamp(ts_ns / 1e9, tz=datetime.timezone.utc).isoformat()
        url += f"?ts={urllib.parse.quote(iso, safe='')}"
    request = urllib.request.Request(url)  # noqa: S310 (local dashboard, not a remote host)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        return json.load(response)
