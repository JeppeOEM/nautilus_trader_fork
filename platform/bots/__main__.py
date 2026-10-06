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
exec bot when `LIVE_PAPER_REAL_MONEY_CONFIG` names a file -- builds the one `TradingNode`, and
schedules one `Supervisor` and one `HistoryPublisher` per bot on the node's own event loop. The
only module of the context that reads the environment or wires `bots.infrastructure`.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from observability import error_ledger

from bots.application.history import HistoryPublisher
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

    fills = SqliteFillsStore(settings.fills_db_path)
    try:
        _run(fleet, settings, fills)
    finally:
        fills.close()


def _run(fleet: PaperFleet | ExecBot, settings: Settings, fills: SqliteFillsStore) -> None:
    """Host the fleet on one node until it stops; the node is disposed however this exits."""
    # One liquidation-feed status per node: the bridge writes it, the cascade bots' readers read it.
    liquidation_status = LiquidationFeedStatus()
    node, hosted = build_node(fleet, settings.redis_url, liquidation_status)
    # Held here: the loop keeps only weak references to its tasks.
    tasks: list[asyncio.Task] = []
    histories: list[HistoryPublisher] = []
    loop = node.get_event_loop()
    try:
        assert loop is not None, "TradingNode's kernel loop must exist once constructed"
        # Only the supervisor reads bots:control; the history publisher's connection never
        # subscribes.
        control_bus = partial(connect, settings.redis_url, subscribe_control=True)
        bus = partial(connect, settings.redis_url)
        for bot, strategy in hosted:
            runtime = cache_reader_for(bot, strategy, liquidation_status)
            supervisor = Supervisor(bot.bot_id, fleet.mode_label, runtime, fills, control_bus)
            # The anchor of each bot's equity curve for the return-based history metrics.
            anchor = fleet.starting_balance_anchor(bot.bot_id)
            history = HistoryPublisher(bot.bot_id, runtime, fills, bus, anchor)
            histories.append(history)
            tasks += [loop.create_task(supervisor.run()), loop.create_task(history.run())]
        node.run()
    finally:
        if loop is not None:
            _stop(loop, tasks, histories)
        node.dispose()


def _stop(
    loop: asyncio.AbstractEventLoop, tasks: list[asyncio.Task], histories: list[HistoryPublisher]
) -> None:
    """
    Cancel the bots' loops and let them unwind, then wait out every queued fill write, before
    the node disposes its executor (cancelling queued work) and the store closes.
    """
    for task in tasks:
        task.cancel()
    if not loop.is_closed() and not loop.is_running():
        loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        loop.run_until_complete(asyncio.gather(*(history.drain() for history in histories)))


if __name__ == "__main__":
    main()
