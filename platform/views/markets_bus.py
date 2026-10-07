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
Story 33.9: the one `markets:live` subscriber of `data_api`, behind `GET /api/markets` (the chart's
Compare picker).

The ranking engine (`ranking.application.engine.markets_message`) publishes one message per venue
per volume cycle (60 s): `{venue, ts, markets: [{instrument_id, symbol}]}`, the venue's whole
market list -- names only, no volume, price or metric. Each message is complete for its venue, so
this bus keeps the newest valid list per venue, never a merge.

The message rules, `STALE_AFTER_SECONDS` and `EXPIRE_AFTER_SECONDS` are copies of `bot_tui`'s
`markets_state` (`_validated`, `MARKETS_STALE_SECONDS`, `MARKETS_EXPIRE_SECONDS`), and the channel
name is a local copy too: `views` imports neither `ranking` nor `bot_tui`
(`platform/tests/test_boundaries.py`). A message is validated whole -- a partial list would read as
the venue having delisted the rest -- and a malformed one is the publisher's bug, so it is ledgered
at `views.markets` (DATA-07) and the venue's previous list is kept.

Staleness is about arrival (`time.monotonic()`), never the publisher's `ts` (DATA-01): a venue
silent past `STALE_AFTER_SECONDS` is still listed but named in `stale_venues`; past
`EXPIRE_AFTER_SECONDS` it is dropped. With no live venue at all `listing` returns `None` and the
route answers 503, never an empty list posing as "no markets".
"""

import asyncio
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis
from kernel.venues import has_venue
from kernel.venues import same_asset
from kernel.venues import venue_of
from observability import error_ledger


logger = logging.getLogger(__name__)

MARKETS_CHANNEL = "markets:live"

_LEDGER_SITE = "views.markets"

# Three missed 60 s volume polls (bot_tui's `MARKETS_STALE_SECONDS`): the venue's list is no longer
# current. Also the subscription's silence liveness: past it `_receive` raises and `run`
# resubscribes (a half-open TCP connection never errors on its own); the cache is kept.
STALE_AFTER_SECONDS = 180.0
# Fifteen missed polls (bot_tui's `MARKETS_EXPIRE_SECONDS`): the venue is dropped from the listing.
EXPIRE_AFTER_SECONDS = 900.0
_POLL_SECONDS = 5.0


def validated_markets(message: object) -> tuple[str, list[tuple[str, str]]] | None:
    """
    Return `(venue, [(instrument_id, symbol), ...])` for a well-formed message, else None: a
    non-empty string `venue`, an int `ts`, a `markets` list whose every entry has a string
    `instrument_id` of that venue and a string `symbol` (bot_tui's `_validated` rules).
    """
    if not isinstance(message, dict):
        return None
    venue, ts, entries = message.get("venue"), message.get("ts"), message.get("markets")
    well_typed_ts = isinstance(ts, int) and not isinstance(ts, bool)
    if not (isinstance(venue, str) and venue and well_typed_ts and isinstance(entries, list)):
        return None
    markets: list[tuple[str, str]] = []
    for entry in entries:
        iid = entry.get("instrument_id") if isinstance(entry, dict) else None
        symbol = entry.get("symbol") if isinstance(entry, dict) else None
        if not (isinstance(iid, str) and isinstance(symbol, str) and has_venue(iid, venue)):
            return None
        markets.append((iid, symbol))
    return venue, markets


def _sort_key(item: dict[str, Any]) -> tuple[bool, str, str]:
    return (not item["same_asset"], item["venue"], item["instrument_id"])


class MarketsBus:
    """
    Caches each venue's latest valid `markets:live` list with its monotonic receive time.

    Invariant: every cached list came whole from one message `validated_markets` accepted; a
    malformed message never replaces or truncates a venue's list (`handle_message`).
    """

    def __init__(self) -> None:
        self._markets: dict[str, list[tuple[str, str]]] = {}
        self._received_at: dict[str, float] = {}

    def handle_message(self, message: object, now: float | None = None) -> None:
        validated = validated_markets(message)
        if validated is None:
            error_ledger.record(
                _LEDGER_SITE, f"malformed {MARKETS_CHANNEL} message, list kept: {message!r:.300}"
            )
            return
        venue, markets = validated
        ids = [iid for iid, _ in markets]
        if len(set(ids)) != len(ids):
            error_ledger.record(
                _LEDGER_SITE,
                f"{MARKETS_CHANNEL} message repeats an instrument_id, list kept: {message!r:.300}",
            )
            return
        self._markets[venue] = markets
        self._received_at[venue] = time.monotonic() if now is None else now

    def listing(self, instrument_id: str | None, now: float) -> dict[str, Any] | None:
        """
        Return `{items, stale_venues}` over every venue younger than `EXPIRE_AFTER_SECONDS` (`now`:
        monotonic), or None when there is none. Items are `{instrument_id, symbol, venue,
        same_asset}`, `instrument_id` itself omitted, the same-asset markets first, then by venue,
        then by id. `MalformedInstrumentId` for an `instrument_id` without a venue suffix.
        """
        if instrument_id is not None:
            venue_of(instrument_id)
        # The route is a plain `def`, so this runs on a threadpool thread while `handle_message`
        # mutates both dicts on the loop: iterate a snapshot and read each venue's list once.
        received = list(self._received_at.items())
        markets = {venue: self._markets.get(venue, []) for venue, _ in received}
        # A venue whose list is empty lists nothing: it is not counted live, so a channel of only
        # empty lists is a 503, never an empty list posing as "no markets".
        live = [
            (venue, at)
            for venue, at in received
            if now - at <= EXPIRE_AFTER_SECONDS and markets[venue]
        ]
        if not live:
            return None
        items = [
            {
                "instrument_id": iid,
                "symbol": symbol,
                "venue": venue,
                "same_asset": instrument_id is not None and same_asset(instrument_id, iid),
            }
            for venue, _ in live
            for iid, symbol in markets[venue]
            if iid != instrument_id
        ]
        stale = sorted(venue for venue, at in live if now - at > STALE_AFTER_SECONDS)
        return {"items": sorted(items, key=_sort_key), "stale_venues": stale}

    async def run(self, redis_url: str) -> None:
        """Subscribe forever, reconnecting 2 s after any error (`ArchiveStatusBus.run`'s)."""
        logger.info("MarketsBus starting, url=%s", redis_url)
        while True:
            try:
                async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                    pubsub = client.pubsub()
                    await pubsub.subscribe(MARKETS_CHANNEL)
                    logger.info("MarketsBus subscribed to %s", MARKETS_CHANNEL)
                    await self._receive(pubsub)
            except asyncio.CancelledError:
                raise  # propagate cancellation cleanly (app shutdown)
            except Exception as exc:
                logger.warning("MarketsBus subscriber error — reconnecting in 2s: %s", exc)
                await asyncio.sleep(2)

    async def _receive(self, pubsub: aioredis.client.PubSub) -> None:
        """
        Ingest messages until `STALE_AFTER_SECONDS` pass without one, then raise so `run`
        resubscribes: `listen()` blocks forever on a half-open connection. Every venue publishes
        each 60 s cycle, so 180 s of silence on the whole channel is never a healthy quiet.
        """
        heard = time.monotonic()
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=_POLL_SECONDS
            )
            if message is not None and message["type"] == "message":
                heard = time.monotonic()
                self._ingest(message["data"])
            elif time.monotonic() - heard > STALE_AFTER_SECONDS:
                raise ConnectionError(
                    f"no {MARKETS_CHANNEL} message for {STALE_AFTER_SECONDS:.0f}s"
                )

    def _ingest(self, data: str) -> None:
        try:
            payload = json.loads(data)
        except ValueError as exc:
            error_ledger.record(
                _LEDGER_SITE, f"unparseable {MARKETS_CHANNEL} message: {data!r:.300}", exc
            )
            return
        self.handle_message(payload)
