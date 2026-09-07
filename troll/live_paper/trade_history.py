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
from ml_signals import performance_metrics

from live_paper import fills_store
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import PositionId
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


# Running total of realized PnL already attributed to a still-open position's earlier
# reducing fills, keyed by PositionId -- reconciled onto (and popped by) the fill that
# actually closes the position, see _fill_realized_pnl's own docstring. Self-cleans: an
# entry exists only between a position's first reducing fill and its close, so this
# never grows past the number of currently-open, partially-reduced positions.
_pending_realized_pnl: dict[PositionId, float] = {}


def _fill_pnl(strategy: Strategy, fill: OrderFilled) -> tuple[float | None, float | None]:
    """
    (realized_pnl, position_realized_pnl) attributed to this one fill.

    realized_pnl is proportional to this fill's own contribution to closing the
    position, not dumped entirely onto whichever fill happens to close it. A single
    closing *order* can still fill across several partial venue-side fills (normal
    dYdX behavior, independent of whether the strategy itself ever adds to a
    position) -- each reducing fill needs its own share, or the trade blotter shows
    one tiny-qty row carrying an entire close's PnL next to other rows that look like
    unrelated null-PnL fills. Every reducing fill before the last is given a
    pre-commission estimate via Position.calculate_pnl(avg_px_open, this fill's own
    price, this fill's own qty); the position's actual Position.realized_pnl
    (commission-net, known only once fully closed) is reconciled onto the fill that
    closes the position, as (total - sum of the earlier fills' estimates) -- so this
    column still sums to exactly Position.realized_pnl for the round trip as a whole.

    position_realized_pnl is the round trip's true total, set ONLY on the fill that
    closes the position (None on every other fill) -- fills_store.win_rate_stats()/
    ml_signals.performance_metrics feed on this instead of realized_pnl, since those
    are inherently per-completed-trade stats and a multi-fill close must still count
    as exactly one trade, not several.

    Both are (None, None) for a fill that only opens or adds to a position
    (order_side == position.entry), matching this codebase's "None means not
    applicable" convention (see coin_detail.py's format_indicator).
    """
    if fill.position_id is None:
        return None, None
    position = strategy.cache.position(fill.position_id)
    if position is None or fill.order_side == position.entry:
        return None, None
    if not position.is_closed:
        estimate = position.calculate_pnl(
            position.avg_px_open, fill.last_px.as_double(), fill.last_qty
        ).as_double()
        _pending_realized_pnl[position.id] = (
            _pending_realized_pnl.get(position.id, 0.0) + estimate
        )
        return estimate, None
    if position.realized_pnl is None:
        return None, None
    total = position.realized_pnl.as_double()
    pending = _pending_realized_pnl.pop(position.id, 0.0)
    return total - pending, total


def _write_fill(fill: dict) -> None:
    """
    The actual (blocking) sqlite3 write -- called either straight off the event loop
    thread (see _on_order_event) or, in BacktestEngine (no running loop, purely
    synchronous), directly inline where blocking has no live-responsiveness cost.
    Never raises: catches its own failure so a fire-and-forget executor call never
    leaves an unretrieved exception on its Future, and a direct/synchronous call never
    propagates into the strategy's own event handling either way.
    """
    try:
        fills_store.write_fill(**fill)
    except Exception:
        # No app-level retry: sqlite3.connect's default 5s busy_timeout already retries
        # transient lock contention internally (e.g. two bot containers sharing
        # fills.db), and a retry here wouldn't help the remaining failure modes (disk
        # full, bad volume-mount permissions). This fill is now permanently missing from
        # closed_trades/win_rate/history everywhere fills_store is read, with nothing
        # else in the system to surface that -- ERROR (not WARNING) + every field of the
        # lost fill is what makes the loss discoverable and manually recoverable from
        # Dozzle, instead of a silent, permanent undercount.
        logger.error("Fill permanently lost, not persisted to fills_store: %s", fill, exc_info=True)


def _on_order_event(event: object, strategy: Strategy, bot_id: str, db_path: str) -> None:
    if not isinstance(event, OrderFilled):
        return
    # This handler runs synchronously on the strategy's own message bus dispatch (not
    # inside run()'s try/except loop) -- live_paper's whole TradingNode runs on one
    # single-threaded event loop (this codebase's documented architecture), so a
    # blocking sqlite3 commit right here would freeze the entire bot -- unable to
    # process new market data or react to a price move -- for however long the disk
    # write takes, not just the sub-millisecond common case. Offloading the write to a
    # thread (loop.run_in_executor) keeps this handler itself non-blocking in live
    # mode. BacktestEngine has no running event loop at all (purely synchronous
    # replay, per this codebase's own architecture docs) -- there, offloading would add
    # complexity for no benefit, so this falls back to writing inline.
    realized_pnl, position_realized_pnl = _fill_pnl(strategy, event)
    fill = {
        "bot_id": bot_id,
        "ts": event.ts_event,
        "side": _fill_side(event),
        "price": event.last_px.as_double(),
        "qty": event.last_qty.as_double(),
        "realized_pnl": realized_pnl,
        "db_path": db_path,
        "position_realized_pnl": position_realized_pnl,
    }
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is None:
        _write_fill(fill)
    else:
        loop.run_in_executor(None, _write_fill, fill)


def subscribe(strategy: Strategy, bot_id: str, db_path: str) -> None:
    """Start recording this strategy's fills into fills_store, from this call onward."""
    strategy.msgbus.subscribe(
        topic=f"events.order.{strategy.id}",
        handler=lambda event: _on_order_event(event, strategy, bot_id, db_path),
    )


def compute_history(
    bot_id: str,
    range_name: str,
    now_ns: int,
    db_path: str,
    starting_balance: float | None = None,
) -> dict:
    """
    Assemble one bots:history:{bot_id}:{range_name} wire-contract payload (AC2/AC3),
    now including a "metrics" dict (Sharpe/Sortino/Calmar/max drawdown/profit factor/
    win rate/expectancy/avg-max win-loss) computed via
    ml_signals.performance_metrics.all_metrics -- the single shared implementation
    bot_tui and any future ML/backtest evaluation code both read (SSOT-02).

    starting_balance anchors the equity curve return-based stats (Sharpe etc.) are
    computed from; None (real-money mode has no fixed config value for this) skips
    those and returns only the trade-level stats -- see all_metrics()'s own docstring.
    all-time pnl_by_day (not range-cutoff) is always what equity is built from, so a
    day/week/month view's returns are still anchored against true account history, not
    a fabricated in-window starting balance -- see performance_metrics.equity_returns's
    own docstring for why.
    """
    window_ns = _RANGE_WINDOW_NS[range_name]
    cutoff_ns = now_ns - window_ns if window_ns is not None else None
    return {
        "bot_id": bot_id,
        "range": range_name,
        "updated_at": now_ns,
        "trades": fills_store.recent_trades(bot_id, db_path, cutoff_ns, _MAX_TRADES),
        "pnl_series": fills_store.pnl_by_day(bot_id, db_path, cutoff_ns),
        "metrics": performance_metrics.all_metrics(
            # One value per completed round trip, not per reducing fill -- see
            # position_realized_pnls()'s own docstring; trade_stats()'s win_rate/
            # expectancy/avg-max win-loss would otherwise count a multi-fill close as
            # several trades.
            realized_pnls=fills_store.position_realized_pnls(bot_id, db_path, cutoff_ns),
            pnl_by_day=fills_store.pnl_by_day(bot_id, db_path, cutoff_ns=None),
            starting_balance=starting_balance,
            cutoff_ns=cutoff_ns,
        ),
    }


async def _refresh_cycle(
    client: aioredis.Redis, bot_id: str, db_path: str, starting_balance: float | None
) -> None:
    now_ns = time.time_ns()
    for range_name in _RANGE_WINDOW_NS:
        blob = compute_history(bot_id, range_name, now_ns, db_path, starting_balance)
        await client.set(f"bots:history:{bot_id}:{range_name}", json.dumps(blob))


async def _history_loop(
    client: aioredis.Redis, bot_id: str, db_path: str, starting_balance: float | None
) -> None:
    while True:
        # AC4: a bad cycle must not overwrite already-published, still-valid keys with
        # an empty fallback -- skip the whole cycle's writes and let the previous
        # blob's now-aging updated_at signal staleness to readers instead.
        try:
            await _refresh_cycle(client, bot_id, db_path, starting_balance)
        except Exception as exc:
            logger.warning("bots:history refresh cycle failed, keeping stale data: %s", exc)
        await asyncio.sleep(_HISTORY_REFRESH_SECONDS)


async def run(
    strategy: Strategy,
    bot_id: str,
    redis_url: str,
    db_path: str,
    starting_balance: float | None = None,
) -> None:
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
                await _history_loop(client, bot_id, db_path, starting_balance)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bots:history loop error -- reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
