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
bot_tui's own snapshots:raw reader (Story 4.3, AC1; architecture AD-9).

Separate connection from ranking_state.py's rankings:live listener -- deliberately not
multiplexed onto it even though dashboard.py itself subscribes to both channels on one
connection. ranking_state.py is already-shipped, already-reviewed Story 4.1/4.2 code;
adding a second subscribed channel and dispatch branch to it is a larger, riskier
change than one new, fully independent sibling module.

snapshots:raw carries every currently-published instrument's batch every tick -- there
is no per-instrument channel to selectively join. This module receives the full batch
for the whole app session and discards every row except the one currently open in
Coin-detail (a deliberate bandwidth/CPU trade-off, not an oversight).

This module's only remaining job is the raw order-book ladder (bid/ask price/size
arrays) -- there is no scalar-message equivalent for a full depth array, so this is the
one place Coin-detail still needs the raw tick stream. Every *derived* indicator
(microprice, OFI, OBI, spread, cvd, ...) used to be computed here too, independently of
both ranking_engine's and dashboard's own copies -- a real SSOT-02 violation
(troll/CLAUDE.md): three separate processes independently running the same rolling
indicators against the same feed. That computation is deleted; Coin-detail now reads
those values straight off the matching ranking_state._LATEST_RANKING rank entry
instead (see coin_detail.rank_row_for) -- already-received, no new subscription.
"""

import asyncio
import json
import logging
import os
import time

import redis.asyncio as aioredis


logger = logging.getLogger(__name__)

REDIS_URL: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")

# The one instrument Coin-detail currently has open, or None if no coin is open (the
# builder is on the Coins/Bots pane) -- every snapshots:raw message is discarded while
# this is None.
_CURRENT_INSTRUMENT_ID: str | None = None
_LATEST_SNAPSHOT: dict | None = None
_LATEST_SNAPSHOT_RECEIVED_AT: float = 0.0


def open_coin(instrument_id: str) -> None:
    """Start tracking this coin's raw ladder, called once per Enter keypress."""
    global _CURRENT_INSTRUMENT_ID, _LATEST_SNAPSHOT, _LATEST_SNAPSHOT_RECEIVED_AT
    _CURRENT_INSTRUMENT_ID = instrument_id
    _LATEST_SNAPSHOT = None
    _LATEST_SNAPSHOT_RECEIVED_AT = 0.0


def close_coin() -> None:
    """Drop the open coin's state, called once per esc-from-Coin-detail."""
    global _CURRENT_INSTRUMENT_ID, _LATEST_SNAPSHOT, _LATEST_SNAPSHOT_RECEIVED_AT
    _CURRENT_INSTRUMENT_ID = None
    _LATEST_SNAPSHOT = None
    _LATEST_SNAPSHOT_RECEIVED_AT = 0.0


def _handle_snapshot_batch(batch: object) -> None:
    """
    Mirrors ranking_state._handle_rankings_message's defensive-shape-guard discipline.
    Scans a snapshots:raw batch for the one row matching _CURRENT_INSTRUMENT_ID; every
    other row (typically ~19 of ~20 in a full watchlist batch) is discarded. A row
    missing instrument_id or shaped unexpectedly is skipped, not fatal.
    """
    global _LATEST_SNAPSHOT, _LATEST_SNAPSHOT_RECEIVED_AT
    if not isinstance(batch, list):
        logger.warning("snapshots:raw message not list-shaped, ignoring: %r", batch)
        return
    if _CURRENT_INSTRUMENT_ID is None:
        return
    for row in batch:
        if not isinstance(row, dict) or row.get("instrument_id") != _CURRENT_INSTRUMENT_ID:
            continue
        _LATEST_SNAPSHOT = row
        _LATEST_SNAPSHOT_RECEIVED_AT = time.time()
        return


async def _redis_listener(redis_url: str) -> None:
    """
    Subscribe to snapshots:raw only -- bot_tui's own, separate connection (see module
    docstring for why this is deliberately not shared with ranking_state.py's).

    Outer while True reconnects on any non-cancellation exception, matching every
    other Redis listener in this codebase. Malformed JSON is logged and skipped -- the
    subscriber loop always continues.
    """
    logger.info("bot_tui snapshots:raw listener starting, url=%s", redis_url)
    while True:
        try:
            logger.info("bot_tui snapshots:raw listener connecting...")
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("snapshots:raw")
                logger.info("bot_tui snapshots:raw listener subscribed")
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        payload = json.loads(message["data"])
                        _handle_snapshot_batch(payload)
                    except Exception as exc:
                        logger.warning("snapshots:raw message parse/ingest error: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bot_tui snapshots:raw listener error — reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
