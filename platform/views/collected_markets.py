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
Which markets are collected, for the chart's symbol search (chart UX rework, 2026-10-08).

Every collector's `StatusPublisher` keeps its venue's collected set in the Redis key
`collector:collected:<VENUE>` on each `collector:status` publish (`collection_control`'s
`collected_snapshot`: `{venue, ts, collected, pending}`, `ts` wall-clock ns). A key, not the
pub/sub channel: the channel republishes only every 30 min, so a data_api started between two
publishes would know nothing for that long. The prefix is a local copy: `views` never imports
`collection_control` (`platform/tests/test_boundaries.py`).

A venue's set is known only from a well-formed snapshot younger than `STALE_AFTER_SECONDS`; a
missing, stale or malformed one leaves the venue unknown (`None`), never "nothing collected"
(DATA-01). A malformed snapshot is the publisher's bug, so it is ledgered (DATA-07).
"""

import json
import logging
from typing import Any

import redis.asyncio as aioredis
from kernel.venues import has_venue
from observability import error_ledger


logger = logging.getLogger(__name__)

COLLECTED_KEY_PREFIX = "collector:collected:"

_LEDGER_SITE = "views.collected_markets"

# Two of the publisher's 1800 s full republishes (`PLAN_STATUS_SECONDS`), bot_tui's
# `_STATUS_STALE_SECONDS`: a venue silent this long has a stopped collector.
STALE_AFTER_SECONDS = 3600.0
_REDIS_TIMEOUT_SECONDS = 1.0


def collected_ids(venue: str, raw: str | None, now_ns: int) -> frozenset[str] | None:
    """
    Return the ids `venue`'s snapshot `raw` names collected (applied, not pending), or None when
    the set is unknown: no snapshot, one older than `STALE_AFTER_SECONDS` by `now_ns`, or a
    malformed one (ledgered).
    """
    if raw is None:
        return None
    parsed = _parsed(venue, raw)
    if parsed is None:
        error_ledger.record(_LEDGER_SITE, f"malformed collected snapshot of {venue}: {raw!r:.300}")
        return None
    ts, ids = parsed
    if now_ns - ts > STALE_AFTER_SECONDS * 1_000_000_000:
        return None
    return ids


def _parsed(venue: str, raw: str) -> tuple[int, frozenset[str]] | None:
    """Return a well-formed snapshot's `(ts, collected)`, else None."""
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(snapshot, dict) or snapshot.get("venue") != venue:
        return None
    ts, ids = snapshot.get("ts"), snapshot.get("collected")
    if not isinstance(ts, int) or isinstance(ts, bool) or not isinstance(ids, list):
        return None
    if not all(isinstance(iid, str) and has_venue(iid, venue) for iid in ids):
        return None
    return ts, frozenset(ids)


def with_collected(
    listing: dict[str, Any], sets: dict[str, frozenset[str] | None]
) -> dict[str, Any]:
    """
    Return `listing` (`MarketsBus`'s) with each item's `collected`: True or False from its venue's
    set, None when that venue's set is unknown (`sets` lacks it or holds None).
    """
    items = []
    for item in listing["items"]:
        ids = sets.get(item["venue"])
        items.append({**item, "collected": None if ids is None else item["instrument_id"] in ids})
    return {**listing, "items": items}


class CollectedMarkets:
    """
    Reads the venues' collected-set keys over one lazily opened client with bounded timeouts.

    Invariant: a venue whose set cannot be read (Redis down, no key, stale or malformed) is
    reported unknown, never as collecting nothing.
    """

    def __init__(self, redis_url: str) -> None:
        self._url = redis_url
        self._client: aioredis.Redis | None = None

    async def by_venue(self, venues: list[str], now_ns: int) -> dict[str, frozenset[str] | None]:
        """Return each venue's collected ids (`collected_ids`); every venue unknown on a failed read."""
        if not venues:
            return {}
        if self._client is None:
            self._client = aioredis.Redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
                socket_timeout=_REDIS_TIMEOUT_SECONDS,
            )
        try:
            raws = await self._client.mget([f"{COLLECTED_KEY_PREFIX}{v}" for v in venues])
        except Exception as exc:
            # Broad by design: any Redis failure leaves the sets unknown, loudly (DATA-07); the
            # market list itself is still served.
            error_ledger.record(_LEDGER_SITE, "reading the collected sets failed", exc)
            return dict.fromkeys(venues)
        return {v: collected_ids(v, raw, now_ns) for v, raw in zip(venues, raws, strict=True)}

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
