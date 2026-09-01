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
bot_tui's own rankings:live reader (Story 4.1, AC1/AC2/AC5; Story 4.2, AC2/AC5;
architecture AD-9).

bot_tui is a separate OS process from ml_signals.dashboard and ranking_engine -- it
cannot share an in-process Redis subscription with either, so it holds its own
connection and its own copy of the latest rankings:live message, mirroring
dashboard.py's _redis_listener/_handle_rankings_message pattern.

Cold-open (AC5, "has a message ever arrived") and staleness-after-first-message (AC5,
"heartbeat timeout after messages were already flowing") are both tracked here as of
Story 4.2 -- see Story 4.1's Dev Notes "Two staleness stories, not one" for why this
was deliberately split across the two stories.
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

# None until the first valid rankings:live message arrives -- this is the entire
# cold-open signal AC5 depends on (`_LATEST_RANKING is None`).
_LATEST_RANKING: dict | None = None

# Wall-clock seconds (time.time(), not time.time_ns()) of the last valid message --
# 0.0 pre-first-message. Mirrors ml_signals/dashboard.py's own identical
# _LATEST_RANKING_RECEIVED_AT pattern for the identical staleness-detection problem
# (own copy, not an import -- separate OS process, same reasoning as the Redis
# connection above).
_LATEST_RANKING_RECEIVED_AT: float = 0.0

# Reused verbatim from ml_signals/dashboard.py's own already-shipped value: 3x
# ranking_engine's RANKING_HEARTBEAT_SECONDS default of 5s -- "a single missed
# heartbeat shouldn't immediately flag stale, but two consecutive misses should."
# Both dashboard and bot_tui are independent readers of the same heartbeat; using the
# same threshold means they agree on what "stale" means for the same signal.
_RANKING_STALE_SECONDS: float = 15.0


def _handle_rankings_message(message: dict) -> None:
    """
    Record the latest rankings:live message, validating its shape first.

    Mirrors ml_signals/dashboard.py's _handle_rankings_message guard: a malformed
    payload (unexpected shape, wrong producer, truncated JSON that still parses) must
    not corrupt _LATEST_RANKING. The previous valid state (or None, pre-first-message)
    is kept instead -- and, as of Story 4.2, _LATEST_RANKING_RECEIVED_AT is only bumped
    on a message that actually passes this guard, so a malformed message can't look
    "fresh".
    """
    global _LATEST_RANKING, _LATEST_RANKING_RECEIVED_AT
    if not isinstance(message.get("ranks"), list):
        logger.warning("rankings:live message missing list-shaped 'ranks', ignoring: %r", message)
        return
    _LATEST_RANKING = message
    _LATEST_RANKING_RECEIVED_AT = time.time()


def is_stale(received_at: float, now: float | None = None) -> bool:
    """
    Return whether the ranking last received at `received_at` should count as stale.

    `now` defaults to time.time() but callers in tests always pass an explicit value
    for determinism (no real clock in tests). Strictly-greater-than semantics, matching
    dashboard.py's own `ranking_age_s > _RANKING_STALE_SECONDS` exactly (not `>=`).

    `received_at == 0.0` (never received) is always stale -- callers only invoke this
    once `_LATEST_RANKING is not None` (i.e. post-cold-open), but this guards the
    boundary explicitly rather than relying on caller discipline alone.
    """
    if received_at == 0.0:
        return True
    if now is None:
        now = time.time()
    return (now - received_at) > _RANKING_STALE_SECONDS


def toggle_mode(current_mode: str | None) -> str:
    """
    Return the other Ranking Mode -- "volume" <-> "volatility" (Story 4.2, AC2).

    current_mode=None (cold-open, no message ever received) returns "volatility":
    ranking_engine's own default starting mode is "volume" (engine.py's _ACTIVE_MODE),
    so toggling from "unknown" is equivalent to toggling from "volume".
    """
    if current_mode == "volume" or current_mode is None:
        return "volatility"
    return "volume"


async def publish_mode_toggle(redis_url: str, mode: str) -> None:
    """
    Publish a Ranking Mode switch request to ranking:control (Story 4.2, AC2).

    Opens a short-lived connection per call rather than a persistent publisher held
    open for the app's whole lifetime -- `m` is pressed a handful of times per session,
    not per second, so this is simple, correct, and not worth the added
    state-management complexity of a long-lived publisher client alongside the
    existing listener connection (YAGNI, troll/CLAUDE.md DESIGN-01).

    Message shape is exactly what ranking_engine.engine._handle_control_message reads
    (only "mode", "volume" or "volatility") -- no extra fields.
    """
    try:
        async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
            await client.publish("ranking:control", json.dumps({"mode": mode}))
    except Exception as exc:
        logger.warning("failed to publish ranking:control mode toggle: %s", exc)


async def _redis_listener(redis_url: str) -> None:
    """
    Subscribe to rankings:live only -- bot_tui's own connection, never shared with
    dashboard's or ranking_engine's (separate OS processes, per AD-4/AD-9).

    Outer while True reconnects on any non-cancellation exception, matching
    dashboard.py's/ranking_engine's own listener discipline. Malformed JSON is logged
    and skipped -- the subscriber loop always continues.
    """
    logger.info("bot_tui rankings:live listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("bot_tui rankings:live listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("rankings:live")
                logger.info("bot_tui rankings:live listener subscribed")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        _handle_rankings_message(payload)
                    except Exception as exc:
                        logger.warning("rankings:live message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui rankings:live listener error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
