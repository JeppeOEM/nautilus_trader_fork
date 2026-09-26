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
bot_tui's own `archive:status` reader (Story 25.1b), shown as one line at the bottom of the
Collector pane.

Same shape as collector_state.py's collector:status listener: an independent Redis connection,
module-level latest state, reconnect forever. `archive.scheduler` publishes its whole status in
every message (after every step, on start and on a 30 s heartbeat), so only the newest valid one
is kept. The channel name is a local copy: bot_tui never imports `archive`
(`platform/tests/test_boundaries.py`). Read-only -- the "run now" command is the web's
(`POST /api/archive/run`), not the TUI's.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

ARCHIVE_STATUS_CHANNEL = "archive:status"

_LATEST_ARCHIVE_STATUS: dict | None = None
_LATEST_RECEIVED_AT: float = 0.0

# The scheduler republishes at least every 30 s (its heartbeat), so four missed heartbeats means
# the service or the Redis path is down and the shown line is no longer current.
_STATUS_STALE_SECONDS: float = 120.0


def _handle_status_message(message: object) -> None:
    """
    Keep `message` if it is a dict carrying `next_run` and `last_run` (the scheduler's wire
    contract; `running`/`last_intraday` are optional); otherwise log and keep the previous one.
    `collector_pane.format_archive_line` reads the nested fields defensively, so this check is
    only the contract's required keys, tolerant of additive ones.
    """
    global _LATEST_ARCHIVE_STATUS, _LATEST_RECEIVED_AT
    if not isinstance(message, dict) or "next_run" not in message or "last_run" not in message:
        logger.warning("archive:status message malformed, ignoring: %r", message)
        return
    _LATEST_ARCHIVE_STATUS = message
    _LATEST_RECEIVED_AT = time.time()


def is_stale(now: float | None = None) -> bool:
    if _LATEST_RECEIVED_AT == 0.0:
        return True
    if now is None:
        now = time.time()
    return (now - _LATEST_RECEIVED_AT) > _STATUS_STALE_SECONDS


def _ingest(data: str) -> None:
    try:
        _handle_status_message(json.loads(data))
    except Exception as exc:
        logger.warning("archive:status message parse/ingest error: %s", exc)


async def _receive(pubsub: aioredis.client.PubSub) -> None:
    """
    Ingest messages until `_STATUS_STALE_SECONDS` pass without one, then raise so the listener
    resubscribes: `listen()` would block forever on a half-open connection, leaving the line
    stale until the TUI restarts.
    """
    heard = time.monotonic()
    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
        if message is not None and message["type"] == "message":
            heard = time.monotonic()
            _ingest(message["data"])
        elif time.monotonic() - heard > _STATUS_STALE_SECONDS:
            raise ConnectionError(
                f"no {ARCHIVE_STATUS_CHANNEL} message for {_STATUS_STALE_SECONDS:.0f}s"
            )


async def _redis_listener(redis_url: str) -> None:
    logger.info("bot_tui archive:status listener starting, url=%s", redis_url)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe(ARCHIVE_STATUS_CHANNEL)
                logger.info("bot_tui archive:status listener subscribed")
                await _receive(pubsub)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui archive:status listener error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
