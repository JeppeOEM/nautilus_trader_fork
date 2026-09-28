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
Hyperliquid capture's composition root: `python3 -m capture.venues.hyperliquid` (Story 26.2; was
the `HyperliquidCollector` subclass of the Story 22.1 core).

Every l2Book message is a full snapshot (Clear + levels), so the local book can't drift and
`HyperliquidClient` deliberately has no `resync_orderbook`: a crossed sample is skipped and
ledgered, the next message replaces the book. Pushes arrive ~5.4s apart (raw capture, story 22.5),
hence the 12s `stale_book_seconds` (`capture.venues.hyperliquid.config`). Open interest arrives
over the WS -- no poll.
"""

import asyncio
from collections.abc import Iterable
from pathlib import Path

from candles.application.prune import loop as candle_prune_loop
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import store_from_env

from capture.application.capture_service import CaptureService
from capture.application.capture_service import run_forever
from capture.application.config import CoreConfig
from capture.infrastructure.config import load_venue_config
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from capture.infrastructure.redis_stream import RedisLiveStream
from capture.infrastructure.redis_stream import redis_url_from_env
from capture.venues.hyperliquid.client import HyperliquidClient
from capture.venues.hyperliquid.config import CONFIG_PATH
from capture.venues.hyperliquid.trade_history import HyperliquidTradeHistory


VENUE = "HYPERLIQUID"


def build_capture(config: CoreConfig, plan_ids: Iterable[str]) -> CaptureService:
    """
    Wire Hyperliquid onto the shared gate: every `l2Book` message is a full snapshot, so it takes
    the core's default policies (a crossed sample is skipped and ledgered; no resync, since the
    client exposes none) and adds only its client, `recentTrades` history, adapters and candle
    store.
    """
    # This process owns Hyperliquid's candle store (`CANDLES_DB_PATH`), so it opens it, hands
    # capture the sink port and runs the retention loop -- this venue's only extra loop (its open
    # interest arrives over the WebSocket, so it needs no poll).
    store = store_from_env(config.catalog_path)
    return CaptureService(
        config,
        lambda on_data, ledger: HyperliquidClient(
            on_data=on_data,
            environment=config.environment,
            trade_feeds=config.trade_feeds,
            ledger=ledger,
        ),
        (candle_prune_loop(store),),
        venue=VENUE,
        plan=plan_ids,
        archive=ParquetArchiveWriter(config.catalog_path),
        live_stream=RedisLiveStream(redis_url_from_env()),
        second_sink=CandleSink(store),
        trade_history=HyperliquidTradeHistory(config.environment),
    )


def build_capture_from_file(config_path: Path = CONFIG_PATH) -> CaptureService:
    """
    Composition root: the config and the static plan through the one loader; the plan is applied
    once at start through `CaptureService.apply` (Hyperliquid has no live control plane, Story
    25.4).
    """
    config, plan = load_venue_config(config_path, VENUE)
    return build_capture(config, plan.collected)


if __name__ == "__main__":
    asyncio.run(run_forever(build_capture_from_file))
