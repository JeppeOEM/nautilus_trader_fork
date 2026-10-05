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
bot_tui's own `markets:live` reader (Story 29.5), the Collector pane's market browser's only input
of which markets a venue lists.

Same shape as archive_state.py's `archive:status` listener: an independent Redis connection,
module-level latest state, reconnect forever. The ranking engine publishes one message per venue
per volume cycle (60 s), each the venue's whole market list -- names only (`instrument_id`,
`symbol`), no volume, price or metric (operator decision 2026-09-26) -- so only the newest valid
message per venue is kept. The channel name is a published-language literal on both sides, like
`collector:status`: bot_tui never imports `ranking` (`platform/tests/test_boundaries.py`).

A venue that stops publishing is never hidden early (DATA-01): its rows read stale (`~ `) after
`MARKETS_STALE_SECONDS`, and the venue leaves the browser only after `MARKETS_EXPIRE_SECONDS`.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis
from kernel.venues import has_venue


logger = logging.getLogger(__name__)

MARKETS_CHANNEL = "markets:live"

# Three missed 60 s volume polls: past this a venue's list is no longer current, and its rows are
# marked stale. Also how long the listener waits in silence before it checks the connection.
MARKETS_STALE_SECONDS: float = 180.0

# How long the listener waits for Redis's answer to its liveness PING before resubscribing.
PING_ANSWER_SECONDS: float = 10.0

# Fifteen missed polls: past this the venue (ranking engine or its volume source down) is dropped
# from the browser rather than offering a list nothing has confirmed for a quarter hour.
MARKETS_EXPIRE_SECONDS: float = 900.0

# Per venue, the latest valid message's markets as (instrument_id, symbol), and its local receive
# time (this TUI's `time.monotonic()`: the staleness is about arrival, never the publisher's `ts`
# -- DATA-01 -- and a wall-clock step must never age or refresh a list, DW-60). Every `now` taken
# below is a monotonic reading too.
_LATEST_MARKETS: dict[str, list[tuple[str, str]]] = {}
_RECEIVED_AT: dict[str, float] = {}


def _validated(message: object) -> tuple[str, list[tuple[str, str]]] | None:
    """
    Return `(venue, markets)` for a well-formed message, else None. Validated whole: a `ts` that
    is not an int, `markets` not a list, an entry without a string `instrument_id` and `symbol`, or
    an id of another venue than the message's rejects the message -- never a partial list, which
    would read as the venue having delisted the rest.
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


def _handle_markets_message(message: object, now: float | None = None) -> None:
    """Keep a valid message as its venue's list; a malformed one is logged and the last kept."""
    validated = _validated(message)
    if validated is None:
        logger.warning("markets:live message malformed, ignoring: %.300r", message)
        return
    venue, markets = validated
    _LATEST_MARKETS[venue] = markets
    _RECEIVED_AT[venue] = time.monotonic() if now is None else now


def _age(venue: str, now: float | None) -> float | None:
    received_at = _RECEIVED_AT.get(venue)
    if received_at is None:
        return None
    return (time.monotonic() if now is None else now) - received_at


def venue_markets_stale(venue: str, now: float | None = None) -> bool:
    """Return whether `venue`'s list is missing or older than `MARKETS_STALE_SECONDS`."""
    age = _age(venue, now)
    return age is None or age > MARKETS_STALE_SECONDS


def live_venues(now: float | None = None) -> list[str]:
    """Return every venue with a list younger than `MARKETS_EXPIRE_SECONDS`, sorted."""
    return sorted(
        venue
        for venue in _LATEST_MARKETS
        if (age := _age(venue, now)) is not None and age <= MARKETS_EXPIRE_SECONDS
    )


def live_markets(now: float | None = None) -> dict[str, list[tuple[str, str]]]:
    """Return each live (not expired) venue's markets, keyed by venue."""
    return {venue: _LATEST_MARKETS[venue] for venue in live_venues(now)}


def received_at(venue: str) -> float | None:
    """Return when (monotonic clock) this TUI received `venue`'s current list (the rebuild key)."""
    return _RECEIVED_AT.get(venue)


def _ingest(data: str) -> None:
    try:
        _handle_markets_message(json.loads(data))
    except Exception as exc:
        logger.warning("markets:live message parse/ingest error: %s", exc)


async def _receive(pubsub: aioredis.client.PubSub) -> None:
    """
    Ingest messages for as long as the connection answers. After `MARKETS_STALE_SECONDS` of
    silence it sends a PING; a pong (or any message) resets the wait, and none within
    `PING_ANSWER_SECONDS` raises so the listener resubscribes. `listen()` alone would block forever
    on a half-open connection, leaving every venue to age out although the publisher still runs,
    while a legitimate silence (no venue has a fresh volume source, so the ranking engine publishes
    nothing) keeps a healthy connection instead of reconnecting every few minutes.
    """
    heard = time.monotonic()
    pinged_at: float | None = None
    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
        now = time.monotonic()
        if message is not None:
            heard, pinged_at = now, None
            if message["type"] == "message":
                _ingest(message["data"])
        elif pinged_at is not None and now - pinged_at > PING_ANSWER_SECONDS:
            raise ConnectionError(f"no answer to a {MARKETS_CHANNEL} liveness PING")
        elif pinged_at is None and now - heard > MARKETS_STALE_SECONDS:
            await pubsub.ping()
            pinged_at = now


async def _redis_listener(redis_url: str) -> None:
    logger.info("bot_tui markets:live listener starting, url=%s", redis_url)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe(MARKETS_CHANNEL)
                logger.info("bot_tui markets:live listener subscribed")
                await _receive(pubsub)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui markets:live listener error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
