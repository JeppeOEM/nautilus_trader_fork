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
import math
import time
from typing import Any

import redis.asyncio as aioredis
from kernel.venues import has_venue
from kernel.venues import market_kind
from kernel.venues import same_asset
from kernel.venues import venue_of
from observability import error_ledger
from observability.pubsub_liveness import receive_until_silent


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
# Story 33.12: a `rankings:live` message older than the listing's own staleness horizon gives no
# volumes. `updated_at` is the ranking engine's `time.time_ns()` at publish (`RankingBoard
# .build_message`), compared with this process's wall clock: both run on the one host.
_RANKINGS_FRESH_NS = int(STALE_AFTER_SECONDS * 1_000_000_000)


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


def _volume_24h(row: dict[str, Any], ledger: bool) -> float | None:
    """
    Return the row's USD 24 h volume, None when null. Anything else that is not a finite number is
    the ranking engine's bug: ledgered when `ledger` (DATA-07) and shown as unknown, never as a
    number.
    """
    volume = row.get("volume24h")
    if volume is None:
        return None
    if isinstance(volume, bool) or not isinstance(volume, int | float) or not math.isfinite(volume):
        if ledger:
            error_ledger.record(
                _LEDGER_SITE, f"rankings:live row has a non-numeric volume24h: {row!r:.300}"
            )
        return None
    return float(volume)


def _stale_ids(rankings: dict[str, Any]) -> frozenset[str]:
    """
    Return the message's `stale_instrument_ids`. `RankingsBus.handle_message` does not validate that
    key, so a missing or non-list value is no stale set, and a non-string entry names no id.
    """
    stale = rankings.get("stale_instrument_ids")
    if not isinstance(stale, list):
        return frozenset()
    return frozenset(iid for iid in stale if isinstance(iid, str))


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
        # Story 33.12: the `updated_at` of the last `rankings:live` message whose volumes were read,
        # so a bad volume is ledgered once per message, not once per `GET /api/markets`. Messages
        # arrive in order, so the newest one is the whole memo.
        self._volumes_read_for: int | None = None

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

    def with_market_details(
        self, listing: dict[str, Any], rankings: dict[str, Any] | None, now_ns: int
    ) -> dict[str, Any]:
        """
        Return `listing` (`listing`'s) with each item's `market` (`kernel.venues.market_kind`:
        `perp`/`spot`/`unknown`) and `volume24h`, the USD 24 h volume of that id's row in the cached
        `rankings:live` message (`RankingsBus.latest`, verbatim: the ranking engine is its one
        computer, SSOT-02), or None when no message has arrived, the message is older than
        `STALE_AFTER_SECONDS` by `now_ns` (wall clock: a stale volume is never served, DATA-01),
        the message lists the id in `stale_instrument_ids`, the id has no row (volatility mode keeps a volume-less row; volume mode leaves it out) or
        the row's value is null -- never 0. Story 33.12: the symbol search shows both.
        """
        volumes = self._fresh_volumes(rankings, now_ns)
        items = [
            {
                **item,
                "market": market_kind(item["instrument_id"]),
                "volume24h": volumes.get(item["instrument_id"]),
            }
            for item in listing["items"]
        ]
        return {**listing, "items": items}

    def _fresh_volumes(
        self, rankings: dict[str, Any] | None, now_ns: int
    ) -> dict[Any, float | None]:
        if rankings is None or now_ns - rankings["updated_at"] > _RANKINGS_FRESH_NS:
            return {}
        updated_at = rankings["updated_at"]
        ledger = updated_at != self._volumes_read_for
        self._volumes_read_for = updated_at
        # An id the ranking engine marks stale has no fresh market data: its volume is not served,
        # the same `—` the chart's watchlist rail shows for it (DATA-01).
        # Every row's value is still read, so a bad one is ledgered whether or not it is shown.
        stale = _stale_ids(rankings)
        volumes = {row.get("instrument_id"): _volume_24h(row, ledger) for row in rankings["ranks"]}
        return {iid: volume for iid, volume in volumes.items() if iid not in stale}

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
        await receive_until_silent(
            pubsub, MARKETS_CHANNEL, STALE_AFTER_SECONDS, self._ingest, poll_seconds=_POLL_SECONDS
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
