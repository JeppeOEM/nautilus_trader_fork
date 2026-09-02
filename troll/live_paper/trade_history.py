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
bots:history:{bot_id}:{day,week,month,all} publisher (Story 4.6; architecture AD-10).

Event-sourced from the message bus, not reconstructed from Cache. cache.positions_closed()
silently drops every closed position before a strategy's most recent NETTING reopen:
under OmsType.NETTING a position's ID is fixed as {instrument_id}-{strategy_id} for the
strategy's whole lifetime, and Cache._reopen_position() (nautilus_trader/cache/cache.pyx)
overwrites that same PositionId with a brand-new Position object, discarding the old one
from _index_positions_closed -- confirmed directly against
nautilus_trader/execution/engine.pyx + nautilus_trader/cache/cache.pyx and reproduced with
a real two-round-trip BacktestEngine run. Reading history back out of Cache can therefore
never be durable across a bot's full lifetime, only its latest open-close cycle -- directly
contradicting AC1's "survive a restart" framing.

Instead, this module subscribes once to every OrderFilled event on the strategy's own
message bus (topic "events.order.{strategy_id}", published by ExecutionEngine for every
order event -- see engine.pyx's _get_order_events_topic) and appends each fill to
fills_store's shared SQLite file as it happens. A fill, once observed, is never lost or
overwritten the way a Cache position is. compute_history() then just queries that store --
no Cache reconstruction logic at all.

The only place in troll/ that reads a live Strategy's fills for history -- bot_tui (Story
4.7) and the web dashboard are pure Redis GET clients of this module's published keys,
never of fills_store's sqlite file directly (same AD-4/AD-10 module-boundary rule
bot_status.py already established for bots:status/bots:control).
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from live_paper import fills_store
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.trading.strategy import Strategy


logger = logging.getLogger(__name__)

# ~30-60s band (AD-10). "On each fill" refresh (for the Redis republish) is not required
# by any of this story's ACs (none specify latency) -- timer-only is a deliberate scope
# cut, not an omission. The fill *write* to SQLite, by contrast, is on-fill (see run()) --
# that part is what durability actually depends on, not the Redis republish cadence.
_HISTORY_REFRESH_SECONDS = 30.0

_MAX_TRADES = 500
_NS_PER_SECOND = 1_000_000_000
_RANGE_WINDOW_NS = {
    "day": 24 * 3600 * _NS_PER_SECOND,
    "week": 7 * 24 * 3600 * _NS_PER_SECOND,
    "month": 30 * 24 * 3600 * _NS_PER_SECOND,
    "all": None,
}


def _fill_side(fill: OrderFilled) -> str:
    return "BUY" if fill.order_side == OrderSide.BUY else "SELL"


def _closing_realized_pnl(strategy: Strategy, fill: OrderFilled) -> float | None:
    """
    Only the fill that closes a position carries realized PnL -- documented
    simplification, not a general partial-close replay: DummyStrategy (this codebase's
    only live_paper strategy) never partially scales out of a position
    (strategy.py's _maybe_trade has no add-to-position branch), so a closed position's
    realized_pnl belongs entirely to its one closing fill.
    # ponytail: assumes one closing fill per position (true for DummyStrategy today);
    # if a future strategy partially scales out across multiple reducing fills, this
    # attributes all realized PnL to whichever fill happens to close the position
    # instead of splitting it proportionally via Position.calculate_pnl() per reducing
    # fill -- revisit only if that strategy shape is actually built.
    """
    if fill.position_id is None:
        return None
    position = strategy.cache.position(fill.position_id)
    if position is None or not position.is_closed or position.realized_pnl is None:
        return None
    return position.realized_pnl.as_double()


def _on_order_event(event: object, strategy: Strategy, bot_id: str, db_path: str) -> None:
    if not isinstance(event, OrderFilled):
        return
    # This handler runs synchronously on the strategy's own message bus dispatch (not
    # inside run()'s try/except loop) -- an unhandled exception here propagates straight
    # into live order-fill handling and takes the whole TradingNode down with it, same
    # failure shape AC4 already guards against on the timer/Redis-publish side. A fill,
    # once observed, must never be able to crash the node just because the store can't
    # be written to right now (e.g. a bad volume-mount permission) -- log and move on.
    try:
        fills_store.write_fill(
            bot_id=bot_id,
            ts=event.ts_event,
            side=_fill_side(event),
            price=event.last_px.as_double(),
            qty=event.last_qty.as_double(),
            realized_pnl=_closing_realized_pnl(strategy, event),
            db_path=db_path,
        )
    except Exception as exc:
        logger.warning("Failed to record fill to fills_store, fill not persisted: %s", exc)


def subscribe(strategy: Strategy, bot_id: str, db_path: str) -> None:
    """Start recording this strategy's fills into fills_store, from this call onward."""
    strategy.msgbus.subscribe(
        topic=f"events.order.{strategy.id}",
        handler=lambda event: _on_order_event(event, strategy, bot_id, db_path),
    )


def compute_history(bot_id: str, range_name: str, now_ns: int, db_path: str) -> dict:
    """Assemble one bots:history:{bot_id}:{range_name} wire-contract payload (AC2/AC3)."""
    window_ns = _RANGE_WINDOW_NS[range_name]
    cutoff_ns = now_ns - window_ns if window_ns is not None else None
    return {
        "bot_id": bot_id,
        "range": range_name,
        "updated_at": now_ns,
        "trades": fills_store.recent_trades(bot_id, db_path, cutoff_ns, _MAX_TRADES),
        "pnl_series": fills_store.pnl_by_day(bot_id, db_path, cutoff_ns),
    }


async def _refresh_cycle(client: aioredis.Redis, bot_id: str, db_path: str) -> None:
    now_ns = time.time_ns()
    for range_name in _RANGE_WINDOW_NS:
        blob = compute_history(bot_id, range_name, now_ns, db_path)
        await client.set(f"bots:history:{bot_id}:{range_name}", json.dumps(blob))


async def _history_loop(client: aioredis.Redis, bot_id: str, db_path: str) -> None:
    while True:
        # AC4: a bad cycle must not overwrite already-published, still-valid keys with
        # an empty fallback -- skip the whole cycle's writes and let the previous
        # blob's now-aging updated_at signal staleness to readers instead.
        try:
            await _refresh_cycle(client, bot_id, db_path)
        except Exception as exc:
            logger.warning("bots:history refresh cycle failed, keeping stale data: %s", exc)
        await asyncio.sleep(_HISTORY_REFRESH_SECONDS)


async def run(strategy: Strategy, bot_id: str, redis_url: str, db_path: str) -> None:
    """
    Record this strategy's fills into fills_store as they happen, and publish
    bots:history:{bot_id}:{day,week,month,all} on a heartbeat, for the lifetime of the
    TradingNode's own event loop.
    """
    logger.info("bots:history loop starting for bot_id=%s, url=%s", bot_id, redis_url)
    subscribe(strategy, bot_id, db_path)
    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                await _history_loop(client, bot_id, db_path)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bots:history loop error -- reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
