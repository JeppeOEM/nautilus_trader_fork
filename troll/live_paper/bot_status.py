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

Also owns bots:incidents:{bot_id} (operator request: a WS/feed-health incident log
viewable from bot_tui without digging through logs): a bounded, Redis-persisted list of
{type, started_at, ended_at} spans -- "data_stale" (strategy.last_data_ns silent for
_DATA_STALE_NS, this module's own OBS-01-derived proxy for "this bot's WS feed is
having problems", since no typed reconnect event exists to hook) and "process_start"
(zero-duration marker, logged once per container start so a restart is never invisible
just because no data-staleness incident happened to accompany it). See
_incident_transition's own docstring for the append-on-stale/close-on-recover state
machine, and run()'s own comments for why seeding/orphan-closing happens once per
process life, not once per Redis reconnect.
"""

import asyncio
import json
import logging
import time

import redis.asyncio as aioredis

from live_paper import fills_store
from nautilus_trader.model.enums import PriceType
from nautilus_trader.trading.strategy import Strategy


logger = logging.getLogger(__name__)

# Heartbeat cadence -- matches ranking_engine's own RANKING_HEARTBEAT_SECONDS default
# (5s), the precedent this project already established for "published on change +
# heartbeat" Redis channels (architecture AD-9/AD-10).
_STATUS_HEARTBEAT_SECONDS = 5.0

# WS/feed-health incident log (operator request: "monitor WS connection of the bots,
# flag interruptions, keep a log of restarts/downtime/stale connections viewable from
# the TUI"). No typed reconnect event exists to hook -- the dYdX adapter's Rust client
# handles reconnects internally with no Python callback for it -- so this reuses
# dydx_collector's own OBS-01 doctrine instead: 30s+ silence on a live instrument's
# quote feed is a pipeline failure, never a quiet market. Same threshold value as
# dydx_collector.collector._WATCHDOG_STALE_NS, applied here to a single bot's own feed.
_DATA_STALE_NS: int = 30_000_000_000  # 30 seconds

# Bounded incident history (MEM-01) -- oldest entries drop off first.
_MAX_INCIDENTS: int = 50


def _incidents_redis_key(bot_id: str) -> str:
    return f"bots:incidents:{bot_id}"


def _incident_transition(
    now: float, is_stale: bool, incidents: list[dict]
) -> tuple[list[dict], bool]:
    """
    Pure state-machine step for the per-bot data-staleness incident log. Mirrors the
    shape of dydx_collector.collector._watchdog_transition (not reused directly -- AD-4
    only permits cross-module reuse of pure, I/O-free *utilities*, and this one
    persists a different representation: a list of {started_at, ended_at} spans, not a
    notify-string).

    Appends a new open incident (ended_at=None) on a stale-start transition; closes the
    most recent open incident on recovery; otherwise returns incidents unchanged.
    Returns (incidents, changed) so the caller only writes to Redis on an actual
    transition, never on every heartbeat tick.
    """
    open_incident = incidents[-1] if incidents and incidents[-1]["ended_at"] is None else None
    if is_stale:
        if open_incident is None:
            new_incident = {"type": "data_stale", "started_at": now, "ended_at": None}
            return [*incidents, new_incident], True
        return incidents, False
    if open_incident is not None:
        closed = {**open_incident, "ended_at": now}
        return [*incidents[:-1], closed], True
    return incidents, False


def _close_orphaned_incident(incidents: list[dict], now: float) -> list[dict]:
    """
    Close any incident left open (ended_at=None) by a previous process life. A crash/
    restart mid-incident means the loop that would have closed it is gone -- without
    this the log would show a permanently "ongoing" incident from a process that isn't
    even the one currently running. Called once at process startup, before the
    heartbeat loop's own transitions begin.
    """
    if not incidents or incidents[-1]["ended_at"] is not None:
        return incidents
    closed = {**incidents[-1], "ended_at": now, "note": "closed by restart"}
    return [*incidents[:-1], closed]


def _record_process_start(incidents: list[dict], now: float) -> list[dict]:
    """
    Zero-duration marker appended once per process start -- "has there been a
    restart" must be visible in the same log as data-staleness spans, not just
    inferred from their absence.
    """
    return [*incidents, {"type": "process_start", "started_at": now, "ended_at": now}]


def _trim_incidents(incidents: list[dict]) -> list[dict]:
    return incidents[-_MAX_INCIDENTS:]


def _is_feed_stale(last_data_ns: int, started_at: float, now_ns: int) -> bool:
    """
    True once _DATA_STALE_NS has passed with no data -- measured from the last quote
    if one has ever arrived, otherwise from process start. Falling back to started_at
    (rather than never firing while last_data_ns stays 0) is what lets a startup
    connection failure -- the WS/API is down for the bot's entire life -- still open a
    data_stale incident; last_data_ns==0 is otherwise indistinguishable from "healthy,
    just no quote yet" and would suppress staleness detection forever.
    """
    reference_ns = last_data_ns if last_data_ns != 0 else int(started_at * 1e9)
    return (now_ns - reference_ns) > _DATA_STALE_NS


def build_status(
    strategy: Strategy,
    bot_id: str,
    mode: str,
    started_at: float,
    now: float,
    db_path: str,
) -> dict:
    """
    Compute one bots:status wire-contract payload for a single running strategy/bot
    (Story 4.4, AC1).

    win_rate is None (not 0.0) until at least one position has closed -- distinguishing
    "no trades yet" from "0% win rate so far" mirrors this codebase's own established
    "None means genuinely unknown, not a fabricated default" convention (see
    coin_detail.py's format_indicator "warming up..." sentinel for the same idea
    applied to a different signal).

    closed_trades/win_rate are read from fills_store (Story 4.6's durable, event-
    sourced fill log), never cache.positions_closed() -- under OmsType.NETTING, a
    position's ID is fixed for a strategy's whole lifetime, so Cache overwrites the
    same PositionId (and discards the prior closed entry) every time it reopens. A bot
    that has traded more than once could show a closed_trades/win_rate count that only
    reflects its latest open-close cycle if read from positions_closed() -- confirmed
    directly against nautilus_trader/execution/engine.pyx + cache/cache.pyx and
    reproduced in trade_history.py's own tests (Story 4.6's story file, second AC2
    correction). fills_store, by contrast, is append-only and never overwritten.
    """
    instrument_id = strategy.config.instrument_id

    # strategy_id-scoped, never strategy.portfolio.*(instrument_id) -- AD-11:
    # Portfolio's net_exposure/realized_pnl/unrealized_pnl are account+instrument
    # scoped in the Rust core (cache.positions_open(strategy_id=None, ...)), i.e. they
    # silently sum across every strategy in the node trading this instrument. Under
    # the one-node-many-bots model that would blend two bots' numbers together the
    # moment they share an instrument_id, so each figure is computed here directly
    # from this strategy's own positions instead.
    positions_open = strategy.cache.positions_open(
        instrument_id=instrument_id,
        strategy_id=strategy.id,
    )
    positions_closed = strategy.cache.positions_closed(
        instrument_id=instrument_id,
        strategy_id=strategy.id,
    )

    position_side = "flat"
    net_exposure = 0.0
    unrealized_pnl = 0.0
    if positions_open:
        # NETTING (this project's only OMS type): at most one open position per
        # strategy+instrument.
        position = positions_open[0]
        position_side = "long" if position.is_long else "short" if position.is_short else "flat"
        price = strategy.cache.price(instrument_id, PriceType.MID)
        if price is not None:
            # notional_value() (not signed_qty * price) -- matches Portfolio's own
            # net_exposure calc (crates/portfolio/src/portfolio.rs), which scales by
            # the instrument's multiplier; signed_qty alone would drop that factor.
            sign = 1.0 if position.is_long else -1.0
            net_exposure = sign * position.notional_value(price).as_double()
            unrealized_pnl = position.unrealized_pnl(price).as_double()

    realized_pnl = sum(
        position.realized_pnl.as_double()
        for position in (*positions_open, *positions_closed)
        if position.realized_pnl is not None
    )

    closed_trades, wins = fills_store.win_rate_stats(bot_id, db_path)
    win_rate = wins / closed_trades if closed_trades else None

    return {
        "bot_id": bot_id,
        "strategy": type(strategy).__name__,
        "symbol": str(instrument_id),
        "mode": mode,
        "running": strategy.is_running,
        "position_side": position_side,
        "net_exposure": net_exposure,
        "realized_pnl": realized_pnl,
        "unrealized_pnl": unrealized_pnl,
        "win_rate": win_rate,
        "closed_trades": closed_trades,
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
    client: aioredis.Redis,
    strategy: Strategy,
    bot_id: str,
    mode: str,
    started_at: float,
    db_path: str,
    incidents: list[dict],
) -> None:
    while True:
        # incidents is mutated in place (not reassigned) so the same list object
        # run() holds keeps reflecting reality across a Redis reconnect -- this
        # function is re-scheduled fresh each reconnect (see run()'s docstring), but
        # incidents itself must survive that so a reconnect doesn't look like a data
        # gap ever happened.
        now_ns = time.time_ns()
        # A deliberately-stopped bot (operator "s" + confirm) legitimately stops
        # receiving fresh data -- without this guard, "stopped on purpose" and "feed
        # actually died" both silently look identical (a growing last_data_ns gap),
        # opening a misleading data_stale incident for a stop nobody would call a feed
        # outage. Staleness is only a meaningful question while the bot is running.
        is_stale = strategy.is_running and _is_feed_stale(strategy.last_data_ns, started_at, now_ns)
        new_incidents, changed = _incident_transition(time.time(), is_stale, incidents)
        if changed:
            incidents[:] = _trim_incidents(new_incidents)
            try:
                await client.set(_incidents_redis_key(bot_id), json.dumps(incidents))
            except Exception:
                logger.exception("bots:incidents write failed for %s", bot_id)
        # build_status() can briefly raise during live startup -- portfolio methods
        # like net_exposure()/unrealized_pnl() need a last quote price that doesn't
        # exist yet if the heartbeat loop's first tick lands before the data client's
        # first quote arrives (this loop is scheduled independently of the Strategy's
        # own lifecycle -- see this module's docstring -- so there's no ordering
        # guarantee against TradingNode's own startup sequencing). Confirmed live
        # 2026-09-02: self-heals within ~2 ticks once the first quote lands. Catching
        # it here (instead of letting it propagate to run()'s outer except) means one
        # bad tick just skips a publish, rather than tearing down the whole
        # connection -- which would also cancel _control_loop's pubsub.listen() via
        # asyncio.gather and risk missing a bots:control message during the same
        # startup window for no reason related to the control channel itself.
        try:
            status = build_status(
                strategy, bot_id, mode, started_at, now=time.time(), db_path=db_path
            )
        except Exception as exc:
            logger.debug("bots:status build skipped this tick (expected at startup): %s", exc)
        else:
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


async def run(strategy: Strategy, bot_id: str, mode: str, redis_url: str, db_path: str) -> None:
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

    # Seeded once per process life, not once per Redis reconnect below -- otherwise a
    # mere Redis blip (unrelated to this bot's own market-data feed) would relogin as
    # a spurious "process_start" and re-run orphan-closing every time. incidents is
    # then mutated in place by every _heartbeat_loop invocation across reconnects
    # (see that function's own comment).
    incidents: list[dict] = []
    try:
        async with aioredis.Redis.from_url(redis_url, decode_responses=True) as seed_client:
            raw = await seed_client.get(_incidents_redis_key(bot_id))
            if raw is not None:
                incidents = json.loads(raw)
            now = time.time()
            incidents = _trim_incidents(
                _record_process_start(_close_orphaned_incident(incidents, now), now)
            )
            await seed_client.set(_incidents_redis_key(bot_id), json.dumps(incidents))
    except Exception as exc:
        logger.warning(
            "bots:incidents seed failed for %s, starting this run with an empty log: %s",
            bot_id,
            exc,
        )
        incidents = []

    while True:
        try:
            async with aioredis.Redis.from_url(redis_url, decode_responses=True) as client:
                pubsub = client.pubsub()
                await pubsub.subscribe("bots:control")
                await asyncio.gather(
                    _heartbeat_loop(client, strategy, bot_id, mode, started_at, db_path, incidents),
                    _control_loop(pubsub, strategy, bot_id),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("bots:status/control loop error -- reconnecting in 2s: %s", exc)
            await asyncio.sleep(2)
