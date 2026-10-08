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
The bots process's composition root: `python3 -m bots` (compose service `live-paper`).

Reads the environment (names and defaults frozen, AD-D12), loads the paper fleet -- or the one
exec bot when `LIVE_PAPER_REAL_MONEY_CONFIG` names a file -- claims every bot's `bots:owner`
lease (refusing to start, exit non-zero, when another live process owns one: DW-78,
`bots.application.ownership`), builds the one `TradingNode`, and schedules one `Supervisor` and
one `HistoryPublisher` per bot on the node's own event loop. The leases are released once the bots'
loops have unwound. The only module of the context that reads the environment or wires
`bots.infrastructure`.
"""

import asyncio
import logging
import os
import signal
import socket
import sys
import time
import uuid
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from observability import error_ledger

from bots.application.history import HistoryPublisher
from bots.application.ownership import BotIdCollision
from bots.application.ownership import claim_all
from bots.application.ownership import owner_value
from bots.application.ownership import reconfirm
from bots.application.ownership import release_all
from bots.application.ports import Connect
from bots.application.supervise import Supervisor
from bots.domain.config import ExecBot
from bots.domain.config import PaperFleet
from bots.infrastructure.config import resolve_config
from bots.infrastructure.fills_store import SqliteFillsStore
from bots.infrastructure.liquidation_data_client import LiquidationFeedStatus
from bots.infrastructure.nautilus_host import build_node
from bots.infrastructure.nautilus_host import cache_reader_for
from bots.infrastructure.redis import connect


logger = logging.getLogger(__name__)

_PACKAGE_DIR = Path(__file__).parent
REAL_MONEY_ENV_VAR = "LIVE_PAPER_REAL_MONEY_CONFIG"


@dataclass(frozen=True)
class Settings:
    """The process's environment, read once at start."""

    redis_url: str
    # One file shared by every bot (Story 4.6). Its directory, not the bare file, must be
    # volume-mounted for fills to survive a restart (SQLite WAL sidecars). Compose sets it; the
    # default is the host's `platform/data/live_paper/` (durable stores live under
    # `platform/data/`, never inside a package, spine AD-D13).
    fills_db_path: str
    paper_config_path: Path
    # No default, on purpose: the non-Sandbox path is reachable only by naming a file.
    real_money_path: str | None


def settings_from_env() -> Settings:
    return Settings(
        redis_url=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379"),
        fills_db_path=os.environ.get(
            "FILLS_DB_PATH", str(_PACKAGE_DIR.parent / "data/live_paper/fills.db")
        ),
        paper_config_path=_PACKAGE_DIR / "config.toml",
        real_money_path=os.environ.get(REAL_MONEY_ENV_VAR),
    )


def main() -> None:
    error_ledger.start()  # durable error ledger (story 23.3); no-op without ERROR_LEDGER_DIR
    settings = settings_from_env()
    fleet, is_real_money = resolve_config(settings.paper_config_path, settings.real_money_path)
    if is_real_money:
        assert isinstance(fleet, ExecBot)
        logger.warning(
            "Starting in %s mode via %s=%s",
            fleet.config.mode.upper(),
            REAL_MONEY_ENV_VAR,
            settings.real_money_path,
        )

    owner = _owner(fleet, settings, is_real_money)
    bus = partial(connect, settings.redis_url)
    bot_ids = [bot.bot_id for bot in fleet.bots]
    # The node adopts this loop (`TradingNode` takes the thread's current one), so the claim runs
    # on it before the node exists: its SIGTERM/SIGINT handlers are not installed yet, so a stop
    # during the wait ends the process (`_claim`) instead of being swallowed and the node started
    # anyway, and a refused claim never builds a strategy (only a lease taken while the node was
    # built, `reconfirm`, refuses after it).
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        _claim(loop, bus, bot_ids, owner)
        _host(loop, fleet, settings, owner, bus, bot_ids)
    except BotIdCollision as exc:
        # No strategy ever started. Ledgered, not only logged, so the durable ledger keeps why
        # this life never ran. Non-zero: compose's `on-failure:5` retries a few times (each
        # attempt refused again while the other process lives), then leaves the whole fleet
        # down until an operator resolves the collision -- never a silent partial fleet.
        error_ledger.record("bots.ownership", f"refusing to start: {exc}", exc)
        sys.exit(1)
    finally:
        # `node.dispose()` closes it once a node was built; every earlier way out closes it here.
        if not loop.is_closed():
            loop.close()


def _host(
    loop: asyncio.AbstractEventLoop,
    fleet: PaperFleet | ExecBot,
    settings: Settings,
    owner: str,
    bus: Connect,
    bot_ids: list[str],
) -> None:
    """Open the fills store and run the fleet, every lease already held; release them however."""
    release = partial(release_all, bus, bot_ids, owner)
    try:
        fills = SqliteFillsStore(settings.fills_db_path)
    except BaseException:
        loop.run_until_complete(release())
        raise
    try:
        _run(loop, fleet, settings, fills, owner, release, partial(reconfirm, bus, bot_ids, owner))
    finally:
        fills.close()


def _owner(fleet: PaperFleet | ExecBot, settings: Settings, is_real_money: bool) -> str:
    """Build this process life's `bots:owner` lease value, once (it is compared verbatim)."""
    config = settings.real_money_path if is_real_money else None
    return owner_value(
        mode=fleet.mode_label,
        config=config or str(settings.paper_config_path),
        host=socket.gethostname(),
        token=uuid.uuid4().hex,
        claimed_at=time.time(),
    )


def _claim(loop: asyncio.AbstractEventLoop, bus: Connect, bot_ids: list[str], owner: str) -> None:
    """
    Claim every lease, or raise. A stop mid-wait -- SIGTERM (`docker stop`) or an interrupt
    (Ctrl-C) -- cancels the claim and releases what it held, so the next start is not kept
    waiting a TTL; a SIGTERM then exits with 128 + its number.
    """
    claim = loop.create_task(claim_all(bus, bot_ids, owner))
    # Python runs as PID 1 in the container (no init), and the kernel never delivers a signal
    # whose action is the default to PID 1: without this handler `docker stop` would wait out
    # its whole grace period and SIGKILL. Removed before the node installs its own.
    loop.add_signal_handler(signal.SIGTERM, claim.cancel)
    try:
        loop.run_until_complete(claim)
    except (KeyboardInterrupt, asyncio.CancelledError) as stop:
        claim.cancel()
        loop.run_until_complete(asyncio.gather(claim, return_exceptions=True))
        loop.run_until_complete(release_all(bus, bot_ids, owner))
        if isinstance(stop, KeyboardInterrupt):
            raise
        raise SystemExit(128 + signal.SIGTERM) from None
    finally:
        loop.remove_signal_handler(signal.SIGTERM)


def _run(
    loop: asyncio.AbstractEventLoop,
    fleet: PaperFleet | ExecBot,
    settings: Settings,
    fills: SqliteFillsStore,
    owner: str,
    release: Callable[[], Awaitable[None]],
    renew: Callable[[], Awaitable[None]],
) -> None:
    """
    Host the fleet on one node until it stops; the node is disposed however this exits. Every
    lease is already held (`main`), renewed by `renew` once the node is built (building can
    outlast a TTL) and released by `release` on every way out: right away when the node cannot
    be built, else once the bots' loops have unwound.
    """
    # One liquidation-feed status per node: the bridge writes it, the cascade bots' readers read it.
    liquidation_status = LiquidationFeedStatus()
    try:
        node, hosted = build_node(fleet, settings.redis_url, liquidation_status)
    except BaseException:
        loop.run_until_complete(release())
        raise
    # Held here: the loop keeps only weak references to its tasks.
    tasks: list[asyncio.Task] = []
    histories: list[HistoryPublisher] = []
    try:
        if node.get_event_loop() is not loop:
            # Every supervisor renews its lease on this loop: on another, none would ever run.
            raise RuntimeError("TradingNode did not adopt the claim's event loop")
        # Before any bot task exists: running the loop now must run the renewal alone.
        loop.run_until_complete(renew())
        # Only the supervisor reads bots:control; the history publisher's connection never
        # subscribes.
        control_bus = partial(connect, settings.redis_url, subscribe_control=True)
        bus = partial(connect, settings.redis_url)
        for bot, strategy in hosted:
            runtime = cache_reader_for(bot, strategy, liquidation_status)
            supervisor = Supervisor(
                bot.bot_id, fleet.mode_label, runtime, fills, control_bus, owner=owner
            )
            # The anchor of each bot's equity curve for the return-based history metrics.
            anchor = fleet.starting_balance_anchor(bot.bot_id)
            history = HistoryPublisher(bot.bot_id, runtime, fills, bus, anchor)
            histories.append(history)
            tasks += [loop.create_task(supervisor.run()), loop.create_task(history.run())]
        node.run()
    finally:
        _stop(loop, tasks, histories, release)
        node.dispose()


def _stop(
    loop: asyncio.AbstractEventLoop,
    tasks: list[asyncio.Task],
    histories: list[HistoryPublisher],
    release: Callable[[], Awaitable[None]],
) -> None:
    """
    Cancel the bots' loops and let them unwind, then wait out every queued fill write, before
    the node disposes its executor (cancelling queued work) and closes the loop, and the store
    closes. The leases go last, even when a drain failed: a supervisor still renewing would
    re-claim a lease right after its release.
    """
    for task in tasks:
        task.cancel()
    if loop.is_closed() or loop.is_running():
        error_ledger.record(
            "bots.ownership",
            "bots:owner leases not released: the loop cannot run the release now",
            RuntimeError(f"event loop closed={loop.is_closed()} running={loop.is_running()}"),
        )
        return
    try:
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        loop.run_until_complete(asyncio.gather(*(history.drain() for history in histories)))
    finally:
        loop.run_until_complete(release())


if __name__ == "__main__":
    main()
