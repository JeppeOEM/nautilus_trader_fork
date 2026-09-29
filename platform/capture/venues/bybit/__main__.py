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
Bybit capture's composition root: `python3 -m capture.venues.bybit` (Story 26.2; was the
`BybitCollector` subclass of the Story 22.1 core).

Bybit has a central book, so a crossed local book is corruption: the core's default crossed
policy skips the sample, ledgers it, and -- because `BybitClient` exposes `resync_orderbook` --
forces a fresh snapshot only once it stayed crossed past `crossed_resync_seconds` (DATA-03
fallback). The `u` sequence canary is `capture.venues.bybit.policies` (DATA-08). Open interest is
dropped by the Rust bindings on the linear ticker path, so it is polled over REST through the
service's `poll_loop`.
"""

import asyncio
import functools
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Iterable
from pathlib import Path

from candles.application.prune import loop as candle_prune_loop
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import store_from_env
from collection_control.application.control import ControlService
from collection_control.application.reload import PLAN_RELOAD_SECONDS
from collection_control.application.reload import reload_loop
from collection_control.application.status import PLAN_STATUS_SECONDS
from collection_control.application.status import StatusPublisher
from collection_control.domain.plan import CollectionPlan
from collection_control.infrastructure.plan_store import TomlPlanStore
from collection_control.infrastructure.redis import RedisControlChannel
from collection_control.infrastructure.redis import RedisStatusBus

from capture.application import sites
from capture.application.capture_service import CaptureService
from capture.application.capture_service import run_forever
from capture.domain.policies import CapturePolicies
from capture.infrastructure.config import load_venue_config
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from capture.infrastructure.redis_stream import RedisLiveStream
from capture.infrastructure.redis_stream import redis_url_from_env
from capture.venues.bybit.client import BybitClient
from capture.venues.bybit.config import CONFIG_PATH
from capture.venues.bybit.config import BybitConfig
from capture.venues.bybit.open_interest import fetch_open_interest
from capture.venues.bybit.policies import BybitSequenceCanary
from capture.venues.bybit.trade_history import BybitTradeHistory
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment


VENUE = "BYBIT"


def build_capture(config: BybitConfig, plan_ids: Iterable[str]) -> CaptureService:
    """
    Wire Bybit onto the shared gate: the client, the `u` sequence canary
    (`capture.venues.bybit.policies`, DATA-08 -- a gap or regress drops the book, ledgers
    `collector.book_sequence` and queues a resync), the core's central-book crossed policy, the
    `recent-trade` history, the archive and live-stream adapters, the candle store and the REST
    open-interest poll (the plan's ids only).
    """
    env = BybitEnvironment.TESTNET if config.environment == "testnet" else BybitEnvironment.MAINNET
    # This process owns Bybit's candle store (`CANDLES_DB_PATH`), so it opens it, hands capture
    # the sink port and runs the retention loop.
    store = store_from_env(config.catalog_path)
    capture = CaptureService(
        config,
        lambda on_data, ledger: BybitClient(
            on_data=on_data,
            environment=env,
            trade_feeds=config.trade_feeds,
            ledger=ledger,
        ),
        (candle_prune_loop(store),),
        venue=VENUE,
        plan=plan_ids,
        archive=ParquetArchiveWriter(config.catalog_path),
        live_stream=RedisLiveStream(redis_url_from_env()),
        second_sink=CandleSink(store),
        policies=CapturePolicies(canary=BybitSequenceCanary()),
        trade_history=BybitTradeHistory(config.environment),
    )
    capture.add_loops(
        functools.partial(
            capture.poll_loop,
            lambda: fetch_open_interest(config.environment),
            config.open_interest_poll_seconds,
            site=sites.OPEN_INTEREST_POLL,
            failure="open interest poll failed",
            plan_only=True,
        ),
    )
    return capture


def control_plane(
    capture: CaptureService, plan: CollectionPlan, config_path: Path, redis_url: str
) -> tuple[Callable[[], Awaitable[None]], ...]:
    """
    Return collection control's three loops over Bybit's plan (Story 29.4, dYdX's pattern): the plan-file
    reload every `PLAN_RELOAD_SECONDS`, `collector:status` every `PLAN_STATUS_SECONDS` (the plan
    has no liquidity threshold, so no markets source) and `collector:control`, whose messages
    addressed to `BYBIT` change the plan -- saved to `config_path` (the committed `config.toml`,
    mounted read-write), then applied, then published.
    """
    status = StatusPublisher(capture, RedisStatusBus(redis_url), None, accepts_commands=True)
    control = ControlService(plan, TomlPlanStore(config_path, VENUE), capture, status, None)
    return (
        functools.partial(reload_loop, control, PLAN_RELOAD_SECONDS),
        functools.partial(status.loop, lambda: control.plan, PLAN_STATUS_SECONDS),
        functools.partial(control.control_loop, RedisControlChannel(redis_url)),
    )


def build_capture_from_file(config_path: Path = CONFIG_PATH) -> CaptureService:
    """
    Composition root: the config and the plan through the one loader; the plan is applied at start
    through `CaptureService.apply`, then changed at runtime by collection control's loops
    (`control_plane`). Called per `run_forever` attempt, so a restart starts from the plan the file
    holds now.
    """
    config, plan = load_venue_config(config_path, VENUE)
    if not isinstance(config, BybitConfig):
        raise TypeError(f"the BYBIT loader returned {type(config).__name__}, not BybitConfig")
    capture = build_capture(config, plan.collected)
    capture.add_loops(*control_plane(capture, plan, config_path, redis_url_from_env()))
    return capture


if __name__ == "__main__":
    asyncio.run(run_forever(build_capture_from_file))
