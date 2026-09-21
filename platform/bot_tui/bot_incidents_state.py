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
bot_tui's own bots:incidents:{bot_id} reader.

live_paper/bot_status.py owns the incident log (data-staleness spans + process-start
markers, see that module's docstring) and persists the whole list under one Redis
STRING key, rewritten on every transition -- not a pub/sub channel, so this polls with
plain Redis GETs, mirroring bot_history_state.py's own poll_loop shape exactly (same
single-bot open_bot()/close_bot() tracking, same reasoning: incidents are only ever
rendered for the one bot open in Bot-detail).
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

_POLL_INTERVAL_SECONDS: float = 15.0

_TRACKED_BOT_ID: str | None = None
_LATEST_INCIDENTS: dict[str, list[dict]] = {}
_LATEST_RECEIVED_AT: dict[str, float] = {}


def open_bot(bot_id: str) -> None:
    """Start tracking bot_id's incidents -- mirrors bot_history_state.open_bot()."""
    global _TRACKED_BOT_ID
    _TRACKED_BOT_ID = bot_id
    _LATEST_INCIDENTS.clear()
    _LATEST_RECEIVED_AT.clear()


def close_bot() -> None:
    """Stop tracking -- mirrors bot_history_state.close_bot()."""
    global _TRACKED_BOT_ID
    _TRACKED_BOT_ID = None
    _LATEST_INCIDENTS.clear()
    _LATEST_RECEIVED_AT.clear()


def _handle_incidents_payload(bot_id: str, payload: list) -> None:
    """
    Record the latest bots:incidents payload for bot_id -- same "does this GET's
    response still belong to the currently-open bot" guard bot_history_state's own
    _handle_history_payload applies (open_bot()/close_bot() can switch _TRACKED_BOT_ID
    while a GET from the previous bot is still in flight).
    """
    if bot_id != _TRACKED_BOT_ID:
        return
    if not isinstance(payload, list):
        logger.warning("bots:incidents payload not a list, ignoring: %r", payload)
        return
    _LATEST_INCIDENTS[bot_id] = payload
    _LATEST_RECEIVED_AT[bot_id] = time.time()


def get_incidents(bot_id: str) -> list[dict] | None:
    """
    Return the tracked bot's latest incidents list, or None if never fetched yet.

    Unlike bot_history_state.get_history, there is no staleness timeout here -- an
    incidents list only changes on a real transition (see bot_status.py's own
    _incident_transition), so an unchanged list for a long time is the expected,
    healthy state, not a sign this read path has gone stale.
    """
    return _LATEST_INCIDENTS.get(bot_id)


async def _poll_once(client: aioredis.Redis, bot_id: str) -> None:
    try:
        raw = await client.get(f"bots:incidents:{bot_id}")
        if raw is None:
            return
        _handle_incidents_payload(bot_id, json.loads(raw))
    except Exception as exc:
        logger.warning("bots:incidents GET/parse error for %s: %s", bot_id, exc)


async def _poll_forever(client: aioredis.Redis, interval: float) -> None:
    while True:
        if _TRACKED_BOT_ID is not None:
            await _poll_once(client, _TRACKED_BOT_ID)
        await asyncio.sleep(interval)


async def poll_loop(redis_url: str, interval: float = _POLL_INTERVAL_SECONDS) -> None:
    """
    GET-polls bots:incidents:{bot_id} for whichever single bot open_bot() currently
    names, for the lifetime of this bot_tui process's own event loop -- same outer
    reconnect-loop shape as bot_history_state.poll_loop.
    """
    logger.info("bot_tui bots:incidents poll loop starting, url=%s", redis_url)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                await _poll_forever(client, interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui bots:incidents poll loop error -- reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
