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
bot_tui's own bots:status reader + bots:control publisher (Story 4.4, AC1-AC4;
architecture AD-10).

Unlike rankings:live's single aggregated message, bots:status is published one message
per bot (one live_paper process = one bot) -- this module accumulates the latest
message and its own per-bot received-at timestamp into two dicts keyed by bot_id, so a
crashed bot's row can go stale independently of every other bot's (AC2: "a healthy bot
next to a crashed one shows exactly one stale row, never a pane-wide flag").
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

_LATEST_STATUSES: dict[str, dict] = {}
_LATEST_RECEIVED_AT: dict[str, float] = {}

# Same threshold ranking_state.py uses for the identical staleness problem, applied to
# a different heartbeat producer (live_paper's bot_status.py, also a 5s heartbeat --
# see that module's own _STATUS_HEARTBEAT_SECONDS) -- both readers agree on what
# "stale" means for a heartbeat of this cadence.
_BOT_STALE_SECONDS: float = 15.0


def _handle_status_message(message: dict) -> None:
    """
    Record the latest bots:status message for its bot_id, validating shape first
    (AD-3's "readers trust the gate", applied per-message rather than per-batch --
    this channel has no list wrapper the way rankings:live does).
    """
    bot_id = message.get("bot_id")
    if not isinstance(bot_id, str) or not bot_id:
        logger.warning("bots:status message missing string 'bot_id', ignoring: %r", message)
        return
    _LATEST_STATUSES[bot_id] = message
    _LATEST_RECEIVED_AT[bot_id] = time.time()


def is_stale(bot_id: str, now: float | None = None) -> bool:
    """Whether bot_id's last-received status should count as stale (Story 4.4, AC2)."""
    received_at = _LATEST_RECEIVED_AT.get(bot_id, 0.0)
    if received_at == 0.0:
        return True
    if now is None:
        now = time.time()
    return (now - received_at) > _BOT_STALE_SECONDS


async def publish_control(redis_url: str, bot_id: str, action: str) -> None:
    """
    Publish a start/stop request to bots:control (Story 4.4, AC3) -- never a mode/
    paper-live parameter (AC4). Short-lived per-call connection, mirroring
    ranking_state.publish_mode_toggle's identical reasoning: `s` is a rare, human-
    triggered action, not worth a persistent publisher connection (YAGNI).
    """
    try:
        async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
            await client.publish("bots:control", json.dumps({"bot_id": bot_id, "action": action}))
    except Exception as exc:
        logger.warning("failed to publish bots:control %s for %s: %s", action, bot_id, exc)


async def _redis_listener(redis_url: str) -> None:
    """
    Subscribe to bots:status only -- bot_tui's own connection (AD-4/AD-9), separate
    from ranking_state's and coin_detail_state's own listeners (Story 4.3's own
    already-documented independent-connections tradeoff, extended here for a third
    channel).
    """
    logger.info("bot_tui bots:status listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("bot_tui bots:status listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("bots:status")
                logger.info("bot_tui bots:status listener subscribed")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        _handle_status_message(payload)
                    except Exception as exc:
                        logger.warning("bots:status message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui bots:status listener error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
