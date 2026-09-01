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
bots:status publisher + bots:control subscriber (Story 4.4; architecture AD-10).

The only place in troll/ that reads a live Strategy's portfolio/cache to build a
bots:status payload, and the only place that calls Strategy.start()/stop() in response
to a bots:control message -- bot_tui and the web dashboard are pure Redis clients of
this module's published contract, never of live_paper internals directly (AD-4/AD-10
module-boundary rule).

Runs as a plain asyncio task scheduled on the TradingNode's own event loop
(node.get_event_loop().create_task(run(...)) in node.py), deliberately *outside* the
Strategy's own component lifecycle rather than inside one of its clock timers. A
Strategy-internal timer (like strategy.py's own book-snapshot timer) stops firing once
the component transitions to Stopped -- it could publish a final status on stop, but
could never later act on a "start" command, since a stopped component's timers don't
run. This loop's own task is unaffected by the Strategy's Running/Stopped state, so it
can always hear a future "start" and call strategy.start() again.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from nautilus_trader.trading.strategy import Strategy


logger = logging.getLogger(__name__)

# Heartbeat cadence -- matches ranking_engine's own RANKING_HEARTBEAT_SECONDS default
# (5s), the precedent this project already established for "published on change +
# heartbeat" Redis channels (architecture AD-9/AD-10).
_STATUS_HEARTBEAT_SECONDS = 5.0


def build_status(
    strategy: Strategy,
    bot_id: str,
    mode: str,
    started_at: float,
    now: float,
) -> dict:
    """
    Compute one bots:status wire-contract payload for a single running strategy/bot
    (Story 4.4, AC1).

    win_rate is None (not 0.0) until at least one position has closed -- distinguishing
    "no trades yet" from "0% win rate so far" mirrors this codebase's own established
    "None means genuinely unknown, not a fabricated default" convention (see
    coin_detail.py's format_indicator "warming up..." sentinel for the same idea
    applied to a different signal).
    """
    instrument_id = strategy.config.instrument_id

    position_side = "flat"
    if strategy.portfolio.is_net_long(instrument_id):
        position_side = "long"
    elif strategy.portfolio.is_net_short(instrument_id):
        position_side = "short"

    net_exposure_money = strategy.portfolio.net_exposure(instrument_id)
    realized_pnl_money = strategy.portfolio.realized_pnl(instrument_id)
    unrealized_pnl_money = strategy.portfolio.unrealized_pnl(instrument_id)

    closed_positions = strategy.cache.positions_closed(strategy_id=strategy.id)
    wins = sum(
        1
        for position in closed_positions
        if position.realized_pnl is not None and position.realized_pnl.as_double() > 0
    )
    win_rate = wins / len(closed_positions) if closed_positions else None

    return {
        "bot_id": bot_id,
        "strategy": type(strategy).__name__,
        "symbol": str(instrument_id),
        "mode": mode,
        "running": strategy.is_running,
        "position_side": position_side,
        "net_exposure": net_exposure_money.as_double() if net_exposure_money is not None else 0.0,
        "realized_pnl": realized_pnl_money.as_double() if realized_pnl_money is not None else 0.0,
        "unrealized_pnl": (
            unrealized_pnl_money.as_double() if unrealized_pnl_money is not None else 0.0
        ),
        "win_rate": win_rate,
        "closed_trades": len(closed_positions),
        "started_at": started_at,
        "updated_at": now,
    }


def _parse_control_message(payload: dict, bot_id: str) -> str | None:
    """
    Validate a bots:control message shape and return its action iff it targets this
    bot_id (Story 4.4, AC3/AC4) -- AD-3's "readers trust the gate" applied to control
    input as well as market data: a malformed or not-for-us message is silently
    ignored, never a crash. The message never carries a mode/paper-live field (AC4) --
    only "bot_id"/"action" are read here, so there is nothing in this parser that could
    ever be used to change which config a bot runs under.
    """
    if payload.get("bot_id") != bot_id:
        return None
    action = payload.get("action")
    if action not in ("start", "stop"):
        return None
    return action


async def _heartbeat_loop(
    client: aioredis.Redis, strategy: Strategy, bot_id: str, mode: str, started_at: float
) -> None:
    while True:
        status = build_status(strategy, bot_id, mode, started_at, now=time.time())
        await client.publish("bots:status", json.dumps(status))
        await asyncio.sleep(_STATUS_HEARTBEAT_SECONDS)


async def _control_loop(pubsub: aioredis.client.PubSub, strategy: Strategy, bot_id: str) -> None:
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            payload = json.loads(message["data"])
        except Exception as exc:
            logger.warning("bots:control message parse error: %s", exc)
            continue
        action = _parse_control_message(payload, bot_id)
        if action == "start" and not strategy.is_running:
            strategy.start()
        elif action == "stop" and strategy.is_running:
            strategy.stop()


async def run(strategy: Strategy, bot_id: str, mode: str, redis_url: str) -> None:
    """
    Publish bots:status on a heartbeat and act on bots:control start/stop commands
    addressed to this bot_id, for the lifetime of the TradingNode's own event loop.

    One connection serves both roles (publish + pubsub) for this loop's lifetime,
    reopened together on any error -- if the heartbeat side errors while the control
    side is still blocked in pubsub.listen(), that stale listener task is left to fail
    on its own closed connection rather than being explicitly cancelled here. An
    accepted, already-precedented looseness in this codebase (see
    coin_detail_state.py's own two-independent-uncoordinated-backoff tradeoff,
    deferred-work.md) -- not worth extra machinery for a personal, single-bot tool.
    """
    started_at = time.time()
    logger.info("bots:status/control loop starting for bot_id=%s, url=%s", bot_id, redis_url)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("bots:control")
                await asyncio.gather(
                    _heartbeat_loop(client, strategy, bot_id, mode, started_at),
                    _control_loop(pubsub, strategy, bot_id),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bots:status/control loop error -- reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
