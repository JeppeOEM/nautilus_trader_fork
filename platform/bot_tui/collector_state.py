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
bot_tui's own collector:status reader + collector:control publisher (Story 6.1).

Same shape as bots_state.py's bots:status/bots:control pair (Story 4.4) -- a separate,
independent Redis connection for this channel pair (AD-4/AD-9's precedent), and one
message per instrument rather than a single aggregated payload.
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

_LATEST_COLLECTOR_STATUS: dict[str, dict] = {}
_LATEST_RECEIVED_AT: dict[str, float] = {}

# Every id in collector.py's config.exclude (whether it landed there via a past "unpin"
# control action or a hand-edit of config.toml) -- published as one aggregate message
# rather than per-instrument, since these aren't currently collected and so have no
# per-instrument status of their own.
_LATEST_UNPINNED_IDS: list[str] = []

# collector.py's _status_loop publishes on its own liquidity_check_seconds cadence
# (default 1800s) *plus* immediately after any control action -- a row is only
# considered stale if nothing has updated it for several multiples of the slow cadence.
_STATUS_STALE_SECONDS: float = 3600.0


def _handle_status_message(message: dict) -> None:
    if "unpinned_ids" in message:
        ids = message["unpinned_ids"]
        if isinstance(ids, list) and all(isinstance(i, str) for i in ids):
            _LATEST_UNPINNED_IDS[:] = ids
        else:
            logger.warning("collector:status unpinned_ids message malformed, ignoring: %r", message)
        return

    iid = message.get("id")
    if not isinstance(iid, str) or not iid:
        logger.warning("collector:status message missing string 'id', ignoring: %r", message)
        return
    if message.get("removed"):
        # stop/unpin fully removed this instrument -- drop it now rather than waiting
        # up to _STATUS_STALE_SECONDS for is_stale() to notice it stopped republishing.
        _LATEST_COLLECTOR_STATUS.pop(iid, None)
        _LATEST_RECEIVED_AT.pop(iid, None)
        return
    _LATEST_COLLECTOR_STATUS[iid] = message
    _LATEST_RECEIVED_AT[iid] = time.time()


def is_stale(instrument_id: str, now: float | None = None) -> bool:
    received_at = _LATEST_RECEIVED_AT.get(instrument_id, 0.0)
    if received_at == 0.0:
        return True
    if now is None:
        now = time.time()
    return (now - received_at) > _STATUS_STALE_SECONDS


async def publish_control(redis_url: str, action: str, instrument_id: str | None = None) -> None:
    """
    Publish a start/unpin/stop/pin_top_liquid request to collector:control.
    `instrument_id` is omitted for pin_top_liquid, which targets no single id.
    Short-lived per-call connection -- same reasoning as bots_state.publish_control:
    a rare, human-triggered action, not worth a persistent publisher connection.
    """
    payload: dict = {"action": action}
    if instrument_id is not None:
        payload["id"] = instrument_id
    try:
        async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
            await client.publish("collector:control", json.dumps(payload))
    except Exception as exc:
        logger.warning("failed to publish collector:control %s: %s", action, exc)


async def _redis_listener(redis_url: str) -> None:
    logger.info("bot_tui collector:status listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("bot_tui collector:status listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("collector:status")
                logger.info("bot_tui collector:status listener subscribed")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        _handle_status_message(payload)
                    except Exception as exc:
                        logger.warning("collector:status message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui collector:status listener error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
