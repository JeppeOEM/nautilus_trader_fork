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
Bot supervision: the `bots:status` heartbeat, the `bots:incidents:*` log and `bots:control`
start/stop (architecture AD-10).

The only place in `platform/` that builds a `bots:status` payload or calls a strategy's
`start()`/`stop()` in response to `bots:control` -- bot_tui and the web dashboard are pure Redis
clients of this published contract, never of `bots` internals.

`Supervisor.run` is scheduled on the `TradingNode`'s own event loop, deliberately *outside* the
strategy's component lifecycle: a strategy-internal clock timer stops firing once the component is
Stopped, so it could never hear a later "start". This task is unaffected by the strategy's
Running/Stopped state.
"""

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable

from observability import error_ledger

from bots.application.ports import CONTROL_CHANNEL
from bots.application.ports import STATUS_CHANNEL
from bots.application.ports import BotRuntime
from bots.application.ports import BusConnection
from bots.application.ports import Connect
from bots.application.ports import FillsStore
from bots.application.ports import incidents_key
from bots.domain.bot import Bot


logger = logging.getLogger(__name__)

# Published on change + heartbeat (AD-9/AD-10 precedent: ranking's 5 s default). bot_tui marks a
# bot stale after 3x this with no message. "On change" is every order event of the bot's own
# strategy (Story 29.6): a bracket exit can close a position and the strategy re-enter within a
# second, so a heartbeat alone would rarely ever publish the flat moment in between.
STATUS_HEARTBEAT_SECONDS = 5.0
# The least time between two publishes: an event burst (a trailing stop's `OrderUpdated` on every
# trail step, a scaled exit filling level by level) coalesces into at most 10 publishes a second
# instead of one Cache read, two fills.db queries and one publish per event on the node's loop.
MIN_PUBLISH_SPACING_SECONDS = 0.1
RECONNECT_SECONDS = 2.0


def build_status(bot: Bot, runtime: BotRuntime, fills: FillsStore, now: float) -> dict:
    """
    One `bots:status` payload, field order frozen (AD-10): the published language only ever gains
    fields appended after `updated_at` (Story 29.6's nine, in this order); an existing field is
    never renamed, removed or reordered, so the first thirteen stay byte-identical (replay test).

    `win_rate` is None (not 0.0) until a round trip has closed: "no trades yet" is not "0% so
    far". `closed_trades`/`win_rate` come from the append-only `fills.db`, never
    `cache.positions_closed()`, which loses every round trip but the latest under NETTING (see
    `bots.domain.fill_ledger`).
    """
    positions = runtime.positions()
    closed_trades, wins = fills.win_rate_stats(bot.id)
    return {
        "bot_id": bot.id,
        "strategy": runtime.strategy_name,
        "symbol": runtime.symbol,
        "mode": bot.mode,
        "running": runtime.is_running,
        "position_side": positions.position_side,
        "net_exposure": positions.net_exposure,
        "realized_pnl": positions.realized_pnl,
        "unrealized_pnl": positions.unrealized_pnl,
        "win_rate": wins / closed_trades if closed_trades else None,
        "closed_trades": closed_trades,
        "started_at": bot.started_at,
        "updated_at": now,
        "stop_loss": positions.stop_loss,
        "take_profit": positions.take_profit,
        "entry_price": positions.entry_price,
        "mark_price": positions.mark_price,
        "position_qty": positions.position_qty,
        "stop_loss_orders": positions.stop_loss_orders,
        "take_profit_orders": positions.take_profit_orders,
        "open_orders": positions.open_orders,
        "last_fill_at": fills.last_fill_ns(bot.id),
    }


def parse_control_message(payload: dict, bot_id: str) -> str | None:
    """
    Return a `bots:control` message's action iff it targets `bot_id` with `start` or `stop`,
    else None. Only `bot_id` and `action` are ever read: the channel carries no mode, so nothing
    here can change which config a bot runs under (AD-10).
    """
    if payload.get("bot_id") != bot_id:
        return None
    action = payload.get("action")
    if action not in ("start", "stop"):
        return None
    return action


class Supervisor:
    """
    Supervises one `Bot` over its `BotRuntime` for the process's life.

    Invariants: the incident log is seeded and `process_start` recorded once per process life,
    never per Redis reconnect (the in-memory log survives every reconnect, so a Redis blip never
    looks like a data gap or a restart); `bots:status` is published on every heartbeat tick while
    connected; only a well-formed `bots:control` message addressed to this bot starts or stops it.
    """

    def __init__(
        self,
        bot_id: str,
        mode: str,
        runtime: BotRuntime,
        fills: FillsStore,
        connect: Connect,
        *,
        clock: Callable[[], float] = time.time,
        clock_ns: Callable[[], int] = time.time_ns,
        heartbeat_seconds: float = STATUS_HEARTBEAT_SECONDS,
        min_publish_spacing: float = MIN_PUBLISH_SPACING_SECONDS,
        reconnect_seconds: float = RECONNECT_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._fills = fills
        self._connect = connect
        self._clock = clock
        self._clock_ns = clock_ns
        self._heartbeat_seconds = heartbeat_seconds
        self._min_publish_spacing = min_publish_spacing
        self._reconnect_seconds = reconnect_seconds
        self.bot = Bot(bot_id, mode, started_at=clock())
        # Set by the strategy's order events; wakes the heartbeat loop before its next tick.
        self._changed = asyncio.Event()

    async def run(self) -> None:
        logger.info("bots:status/control loop starting for bot_id=%s", self.bot.id)
        # Once per process life, like the seed: the subscription outlives every reconnect.
        self._runtime.on_order_event(self._changed.set)
        await self.seed()
        while True:
            try:
                async with self._connect() as connection, asyncio.TaskGroup() as loops:
                    # A TaskGroup, not `gather`: when one loop fails the other is cancelled
                    # before the reconnect, so a Redis blip can never leave a second heartbeat
                    # loop publishing (and observing the one `Bot`) beside the new one.
                    loops.create_task(self._heartbeat_loop(connection))
                    loops.create_task(self._control_loop(connection))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # While disconnected no status is published: bot_tui shows the bot stale.
                error_ledger.record(
                    "bots.redis",
                    f"bots:status/control connection for {self.bot.id} failed, "
                    f"reconnecting in {self._reconnect_seconds}s",
                    exc,
                )
                await asyncio.sleep(self._reconnect_seconds)

    async def seed(self) -> None:
        """
        Adopt the previous life's incident log and record this start, once per process. When the
        prior log cannot be read, this life starts from an empty log that still marks the start;
        when only the write fails, the adopted log stays in memory and is written on its next
        transition -- never replaced by an empty one.
        """
        key = incidents_key(self.bot.id)
        try:
            async with self._connect() as connection:
                raw = await connection.get(key)
                prior = json.loads(raw) if raw is not None else []
                incidents = self.bot.start(prior, self._clock())
                await connection.set(key, json.dumps(incidents))
        except Exception as exc:
            if not self.bot.started:
                self.bot.start([], self._clock())
            error_ledger.record(
                "bots.incidents_write", f"bots:incidents seed failed for {self.bot.id}", exc
            )

    async def heartbeat_tick(self, connection: BusConnection) -> None:
        now_ns = self._clock_ns()
        # A deliberately stopped bot legitimately receives no fresh data: staleness is only a
        # question while it runs (Bot.observe), or every stop would read as a feed outage.
        running = self._runtime.is_running
        if self.bot.observe(self._clock(), now_ns, self._runtime.last_data_ns, running):
            try:
                await connection.set(incidents_key(self.bot.id), json.dumps(self.bot.incidents))
            except Exception as exc:
                error_ledger.record(
                    "bots.incidents_write", f"bots:incidents write failed for {self.bot.id}", exc
                )
        # A failed build skips this tick's publish (it can race the first quote at startup)
        # rather than tearing down the connection the control loop shares.
        try:
            status = build_status(self.bot, self._runtime, self._fills, now=self._clock())
        except Exception as exc:
            error_ledger.record(
                "bots.status_build", f"bots:status not built this tick for {self.bot.id}", exc
            )
            return
        await connection.publish(STATUS_CHANNEL, json.dumps(status))

    def handle_control(self, data: str) -> None:
        try:
            payload = json.loads(data)
        except Exception as exc:
            error_ledger.record("bots.control_message", f"unparseable {CONTROL_CHANNEL}", exc)
            return
        if not isinstance(payload, dict):
            # Passed as the exception: `record` outside an `except` would log a bare traceback.
            error_ledger.record(
                "bots.control_message",
                f"{CONTROL_CHANNEL} message is not an object",
                TypeError(f"expected a JSON object, got {data!r}"),
            )
            return
        action = parse_control_message(payload, self.bot.id)
        try:
            if action == "start" and not self._runtime.is_running:
                self._runtime.start()
            elif action == "stop" and self._runtime.is_running:
                self._runtime.stop()
        except Exception as exc:
            # The strategy refused the transition: a bot fault, not a Redis one, so it must not
            # tear down the shared connection (and read as `bots.redis`).
            error_ledger.record(
                "bots.control_action", f"{action} failed for {self.bot.id} on {data!r}", exc
            )

    async def _heartbeat_loop(self, connection: BusConnection) -> None:
        while True:
            # Cleared before the tick: an order event during it publishes once more right after.
            self._changed.clear()
            await self.heartbeat_tick(connection)
            spacing = min(self._min_publish_spacing, self._heartbeat_seconds)
            await asyncio.sleep(spacing)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), self._heartbeat_seconds - spacing)

    async def _control_loop(self, connection: BusConnection) -> None:
        async for data in connection.control_messages():
            self.handle_control(data)
        # A live subscription never ends by itself: an ended stream would leave the heartbeat
        # publishing while every command went unheard, so it fails into the reconnect instead.
        raise ConnectionError(f"{CONTROL_CHANNEL} subscription ended")
