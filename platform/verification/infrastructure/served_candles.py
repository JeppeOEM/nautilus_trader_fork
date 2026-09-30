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
The candles tool's read of the bars the chart is served (Story 31.8): the local `data_api`'s
`GET <base>/api/candles/{instrument_id}?before_ns=&limit=&bar_seconds=` over stdlib `urllib`, paged
back the way the chart scrolls. The read-time widths (10m, 30m, 45m, 1W) never exist at rest, so
the response is the only production artifact to check; reading it over HTTP keeps the reference
side from importing `views`, `candles` or `data_api` -- the reference side never imports the code it
checks (DATA-02, `tests/test_boundaries.py`'s `NON_VENUE_HTTP_CLIENTS`).

The response contract (`data_api/routes/candles.py`, `docs/DATA_DICTIONARY.md` section 2.5):
`{"items": [{"t", "o", "h", "l", "c", "v", "partial"}], "has_more", "venue", "market"}`, items
oldest first, every `t < before_ns`; an item whose `o` is null is a gap row, ignored. A page that
does not have this shape is refused (`Unservable`), as is an unreachable server or a non-200 status:
a verdict over a page the tool could not read would be a guess.
"""

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any
from urllib.parse import quote
from urllib.parse import urlencode
from urllib.parse import urlsplit

from verification.domain.candle_check import SERVED_PAGE_LIMIT
from verification.domain.candle_check import ServedBar
from verification.domain.candle_check import ServedPage
from verification.domain.catalog_check import NS_PER_MS
from verification.domain.conservation import NS_PER_S


# The whole page's deadline: a local server answers one bounded page in well under a second; one
# that does not within this is refused rather than waited on.
TIMEOUT_S = 60
_ITEM_KEYS = frozenset({"t", "o", "h", "l", "c", "v", "partial"})
_VALUE_KEYS = ("o", "h", "l", "c", "v")
_HTTP_SCHEMES = frozenset({"http", "https"})

# `url -> (HTTP status, body)`; raises `Unservable` when no response arrived at all.
Fetch = Callable[[str], tuple[int, bytes]]


class Unservable(Exception):
    """The data_api gave no readable page: unreachable, a non-200 status, or a malformed body."""


def urllib_fetch(url: str) -> tuple[int, bytes]:
    """
    GET `url`; an HTTP error status is returned with its body, a transport error refused.

    `http.client.HTTPException` is a transport error too: a malformed status line
    (`BadStatusLine`) or a body cut short (`IncompleteRead`) derives from neither `URLError` nor
    `OSError`, and must be a refusal, never a crash. So is a URL that is not HTTP (a mistyped
    `--data-api file:///...`): `urlopen` would answer it without a status at all.
    """
    if urlsplit(url).scheme not in _HTTP_SCHEMES:
        raise Unservable(f"not an HTTP data_api URL: {url}")
    request = urllib.request.Request(url)  # noqa: S310 (the local data_api, never a venue)
    try:
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise Unservable(f"data_api unreachable at {url}: {exc!r}") from exc


def _number(item: dict[str, Any], key: str, where: str) -> float | None:
    value = item.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise Unservable(f"{where}: `{key}` is {value!r}, not a number")
    return float(value)


def _bar(item: Any, where: str) -> ServedBar | None:
    """Parse one item; None for a gap row (`o` null)."""
    if not isinstance(item, dict) or not set(item) <= _ITEM_KEYS or "t" not in item:
        raise Unservable(f"{where}: an item that is not a candle object: {item!r:.200}")
    t, partial = item["t"], item.get("partial")
    if isinstance(t, bool) or not isinstance(t, int):
        raise Unservable(f"{where}: `t` is {t!r}, not an integer")
    if partial is not None and not isinstance(partial, bool):
        raise Unservable(f"{where}: `partial` is {partial!r}, not a boolean")
    o, h, low, c, v = (_number(item, key, where) for key in _VALUE_KEYS)
    if o is None:
        return None
    return ServedBar(t=t, o=o, h=h, l=low, c=c, v=v, partial=partial)


def parse_page(body: bytes, before_ms: int, where: str) -> ServedPage:
    """Parse one response body strictly; every bar must lie before the page's cursor, oldest first."""
    try:
        page = json.loads(body)
    except ValueError as exc:
        raise Unservable(f"{where}: not JSON: {body[:200]!r}") from exc
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        raise Unservable(f"{where}: no `items` list: {body[:200]!r}")
    if not isinstance(page.get("has_more"), bool):
        raise Unservable(f"{where}: `has_more` is not a boolean")
    bars = tuple(bar for item in page["items"] if (bar := _bar(item, where)) is not None)
    stamps = [bar.t for bar in bars]
    if stamps != sorted(set(stamps)) or any(t >= before_ms for t in stamps):
        raise Unservable(f"{where}: bars not strictly ascending before {before_ms}: {stamps[:10]}")
    return ServedPage(bars, page["has_more"])


class ServedCandles:
    """The served bars of one data_api (`base_url`), read through `fetch` (urllib by default)."""

    def __init__(self, base_url: str, fetch: Fetch = urllib_fetch) -> None:
        self._base = base_url.rstrip("/")
        self._fetch = fetch

    @property
    def base_url(self) -> str:
        return self._base

    def page(self, instrument_id: str, before_ns: int, limit: int, bar_seconds: int) -> ServedPage:
        """One page: the bars before `before_ns`, as the chart requests them."""
        query = urlencode({"before_ns": before_ns, "limit": limit, "bar_seconds": bar_seconds})
        url = f"{self._base}/api/candles/{quote(instrument_id, safe='')}?{query}"
        status, body = self._fetch(url)
        if status != 200:
            raise Unservable(f"{url}: HTTP {status}: {body[:200]!r}")
        return parse_page(body, before_ns // NS_PER_MS, url)

    def day(
        self, instrument_id: str, bar_seconds: int, start_ns: int, end_ns: int
    ) -> dict[int, ServedBar]:
        """
        Every served bar with `t` in `[start_ns, end_ns)`, paged back from `before_ns = end_ns`
        (`limit` the day's bucket count, at most the route's cap) until a page reaches the day's
        start or the route says nothing older exists. An empty page that claims more is refused
        (`Unservable`).
        """
        start_ms = start_ns // NS_PER_MS
        limit = min(SERVED_PAGE_LIMIT, max(1, (end_ns - start_ns) // (bar_seconds * NS_PER_S)))
        found: dict[int, ServedBar] = {}
        before_ns = end_ns
        while True:
            page = self.page(instrument_id, before_ns, limit, bar_seconds)
            if not page.bars and page.has_more:
                # Nothing before the cursor yet more older: the route contradicts itself, and
                # stopping here would read as "nothing older" (a silent short day).
                raise Unservable(
                    f"{instrument_id} {bar_seconds}s before {before_ns}: an empty page with"
                    " `has_more` true"
                )
            found.update((bar.t, bar) for bar in page.bars if bar.t >= start_ms)
            if not page.bars or not page.has_more or page.bars[0].t <= start_ms:
                return found
            before_ns = page.bars[0].t * NS_PER_MS
