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
bot_tui's own bots:history:{bot_id}:{day,week,month,all} reader (Story 4.7; Story
4.6's read surface, architecture AD-10).

Unlike every other *_state.py module in this package, bots:history is not a pub/sub
channel -- troll/live_paper/trade_history.py refreshes these four keys on a 30s timer,
so this module polls them with plain Redis GETs instead of pubsub.listen().

Tracks a single bot at a time (open_bot()/close_bot()), mirroring coin_detail_state's
open_coin()/close_coin() shape rather than bots_state's accumulate-every-bot shape:
history is only ever rendered for the one bot open in Bot-detail (Bots-pane rows show
live status only, never history), so there is nothing to gain from polling every known
bot's history in the background, and no Redis calls happen at all while no bot is
open.
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

_RANGES = ("day", "week", "month", "all")
_POLL_INTERVAL_SECONDS: float = 15.0

# 3x trade_history.py's own 30s publish cadence (not this module's 15s poll cadence) --
# same 3x-the-producer's-refresh-interval ratio bots_state._BOT_STALE_SECONDS (15.0)
# uses against bot_status.py's 5s heartbeat, applied here to history's slower cadence.
_HISTORY_STALE_SECONDS: float = 90.0

_TRACKED_BOT_ID: str | None = None
_LATEST_HISTORY: dict[str, dict] = {}
_LATEST_RECEIVED_AT: dict[str, float] = {}


def open_bot(bot_id: str) -> None:
    """Start tracking bot_id's history -- mirrors coin_detail_state.open_coin()."""
    global _TRACKED_BOT_ID
    _TRACKED_BOT_ID = bot_id
    _LATEST_HISTORY.clear()
    _LATEST_RECEIVED_AT.clear()


def close_bot() -> None:
    """Stop tracking -- mirrors coin_detail_state.close_coin()."""
    global _TRACKED_BOT_ID
    _TRACKED_BOT_ID = None
    _LATEST_HISTORY.clear()
    _LATEST_RECEIVED_AT.clear()


def _handle_history_payload(bot_id: str, range_name: str, payload: dict) -> None:
    """
    Record the latest bots:history payload for range_name (AD-3's "readers trust the
    gate", applied per-key the same way bots_state._handle_status_message applies it
    per-message).

    bot_id is the bot this payload's GET was issued for, not necessarily the bot still
    open now: open_bot()/close_bot() run synchronously on a keypress and can switch
    _TRACKED_BOT_ID while a GET from the previous bot is still in flight. Without this
    check, that stale response would land in _LATEST_HISTORY under the *new* bot's
    view -- briefly showing one bot's trades/PnL as if they belonged to another.
    """
    if bot_id != _TRACKED_BOT_ID:
        return
    if not isinstance(payload, dict) or "trades" not in payload or "pnl_series" not in payload:
        logger.warning("bots:history payload missing trades/pnl_series, ignoring: %r", payload)
        return
    _LATEST_HISTORY[range_name] = payload
    _LATEST_RECEIVED_AT[range_name] = time.time()


def is_stale(range_name: str, now: float | None = None) -> bool:
    """Whether range_name's last-received history should count as stale/unfetched."""
    received_at = _LATEST_RECEIVED_AT.get(range_name, 0.0)
    if received_at == 0.0:
        return True
    if now is None:
        now = time.time()
    return (now - received_at) > _HISTORY_STALE_SECONDS


def get_history(range_name: str) -> dict | None:
    """
    Return the tracked bot's latest history for range_name, or None if the read
    surface is unavailable (never fetched, or stale) -- Story 4.7, AC4. Callers never
    need to check is_stale() separately; None already means "render unavailable."
    """
    if is_stale(range_name):
        return None
    return _LATEST_HISTORY.get(range_name)


async def _poll_once(client: aioredis.Redis, bot_id: str) -> None:
    for range_name in _RANGES:
        try:
            raw = await client.get(f"bots:history:{bot_id}:{range_name}")
            if raw is None:
                continue
            _handle_history_payload(bot_id, range_name, json.loads(raw))
        except Exception as exc:
            logger.warning("bots:history GET/parse error for %s:%s: %s", bot_id, range_name, exc)


async def _poll_forever(client: aioredis.Redis, interval: float) -> None:
    while True:
        if _TRACKED_BOT_ID is not None:
            await _poll_once(client, _TRACKED_BOT_ID)
        await asyncio.sleep(interval)


async def poll_loop(redis_url: str, interval: float = _POLL_INTERVAL_SECONDS) -> None:
    """
    GET-polls bots:history:{bot_id}:{day,week,month,all} for whichever single bot
    open_bot() currently names, for the lifetime of this bot_tui process's own event
    loop (a plain Redis reader, per AD-8 -- no live trading runtime here at all). One
    connection reused across poll cycles, reopened on error -- same outer
    reconnect-loop shape as trade_history.py's own run()/_history_loop(), the
    publisher side of this same wire contract.
    """
    logger.info("bot_tui bots:history poll loop starting, url=%s", redis_url)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                await _poll_forever(client, interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui bots:history poll loop error -- reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
