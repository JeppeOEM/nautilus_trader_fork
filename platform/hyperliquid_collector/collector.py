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
Hyperliquid market-data collector: `collector_core.Collector` over `HyperliquidClient` (Story 22.1).

Every l2Book message is a full snapshot (Clear + levels), so the local book can't drift and
`HyperliquidClient` deliberately has no `resync_orderbook`: a crossed sample is skipped and
ledgered, the next message replaces the book. Pushes arrive ~5.4s apart (raw capture, story 22.5), hence the
12s `stale_book_seconds` in config.toml. Open interest arrives over the WS -- no extra loop.
"""

import asyncio
import os
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from candles.application.prune import loop as candle_prune_loop
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import store_from_env
from collector_core.collector import Collector
from collector_core.collector import run_forever
from collector_core.config import CoreConfig
from collector_core.config import load_venue_config
from collector_core.infrastructure.parquet_writer import ParquetArchiveWriter
from collector_core.infrastructure.redis_stream import RedisLiveStream
from collector_core.infrastructure.redis_stream import redis_url_from_env

from hyperliquid_collector.client import HyperliquidClient
from hyperliquid_collector.trade_history import HyperliquidTradeHistory


CONFIG_PATH = Path(
    os.environ.get("HYPERLIQUID_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml")
)


class HyperliquidCollector(Collector):
    """
    Hyperliquid's composition root on the shared gate: every `l2Book` message is a full snapshot,
    so it takes the core's default policies (a crossed sample is skipped and ledgered; no resync,
    since the client exposes none) and adds only its client, `recentTrades` history, adapters and
    candle store. It overrides nothing but `__init__` (Story 26.1).
    """

    VENUE: ClassVar[str] = "HYPERLIQUID"

    def __init__(self, config: CoreConfig, plan_ids: Iterable[str]) -> None:
        client = HyperliquidClient(
            on_data=self._on_data,
            environment=config.environment,
            trade_feeds=config.trade_feeds,
            ledger=self._ledger,
        )
        # Composition root: this process owns Hyperliquid's candle store (`CANDLES_DB_PATH`), so it
        # opens it, hands capture the sink port and runs the retention loop -- this venue's first
        # `extra_loops` entry (its open interest arrives over the WebSocket, so it needs no poll).
        store = store_from_env(config.catalog_path)
        super().__init__(
            config,
            client,
            extra_loops=(candle_prune_loop(store),),
            plan=plan_ids,
            archive=ParquetArchiveWriter(config.catalog_path),
            live_stream=RedisLiveStream(redis_url_from_env()),
            second_sink=CandleSink(store),
            trade_history=HyperliquidTradeHistory(config.environment),
        )


def build_collector(config_path: Path = CONFIG_PATH) -> HyperliquidCollector:
    """
    Composition root: the config and the static plan through the one loader; the plan is applied
    once at start through `Collector.apply` (Hyperliquid has no live control plane, Story 25.4).
    """
    config, plan = load_venue_config(config_path, "HYPERLIQUID")
    return HyperliquidCollector(config, plan.collected)


if __name__ == "__main__":
    asyncio.run(run_forever(build_collector))
