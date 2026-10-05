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

import http.client
import json
import urllib.request


class WatchlistUnavailableError(RuntimeError):
    """
    The live Watchlist could not be read from data_api.

    One error type for every way the fetch can fail (unreachable host, HTTP error such as the
    503 served before ranking_engine's first message, timeout, a body that is not the
    ``{"items": [...]}`` rankings payload), so a caller handles "no watchlist" once instead of
    catching urllib's, json's and the payload's own exception types separately.
    """


def fetch_watchlist(data_api_url: str = "http://127.0.0.1:9100") -> list[str]:
    """
    Fetch the current live Watchlist coin-set from a running data_api.

    Requires data_api to be up and reachable at data_api_url -- the live Watchlist only
    exists as ranking_engine's published rankings, which data_api relays at /api/rankings
    (503 until its first message arrives). Every failure raises WatchlistUnavailableError
    naming the URL, chained from the underlying cause, rather than returning [].
    """
    url = f"{data_api_url.rstrip('/')}/api/rankings"
    try:
        request = urllib.request.Request(url)  # noqa: S310 (local data_api, not a remote host)
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            payload = json.load(response)
    except (OSError, ValueError, http.client.HTTPException) as e:
        # URLError, HTTPError and TimeoutError are OSError; JSONDecodeError and Request's
        # "unknown url type" (a data_api_url without a scheme) are ValueError; a truncated or
        # malformed HTTP response (IncompleteRead, BadStatusLine) is an HTTPException, not an
        # OSError.
        raise WatchlistUnavailableError(
            f"cannot read the watchlist from {url} ({e!r}); "
            "is data_api up and has ranking_engine published its first rankings?",
        ) from e
    items = _items_of(payload, url)
    return [r["instrument_id"] for r in items if isinstance(r, dict) and "instrument_id" in r]


def _items_of(payload: object, url: str) -> list[object]:
    # Validate the shape explicitly: a dict without a list "items" is a data_api contract
    # break, not something to surface as a bare KeyError/TypeError.
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise WatchlistUnavailableError(
            f"unexpected rankings payload from {url}: expected an object with a list 'items', "
            f"got {type(payload).__name__}",
        )
    return items
