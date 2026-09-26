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
Bybit market-data collector: `collector_core.Collector` over `BybitClient` (Story 22.1).

Bybit has a central book, so a crossed local book is corruption: the core's default crossed
policy skips the sample, ledgers it, and -- because `BybitClient` exposes `resync_orderbook` --
forces a fresh snapshot only once it stayed crossed past `crossed_resync_seconds` (DATA-03
fallback). The `u` sequence canary is `bybit_collector.policies` (DATA-08).
Open interest is dropped by the Rust bindings on the linear ticker path, so it is polled
over REST as an extra loop.
"""

import asyncio
import os
from collections.abc import Iterable
from pathlib import Path
from typing import ClassVar

from candles.application.prune import loop as candle_prune_loop
from candles.application.sink import CandleSink
from candles.infrastructure.sqlite_store import store_from_env
from collector_core import sites
from collector_core.collector import Collector
from collector_core.collector import run_forever
from collector_core.config import BybitConfig
from collector_core.config import load_venue_config
from collector_core.domain.policies import CapturePolicies
from collector_core.infrastructure.parquet_writer import ParquetArchiveWriter
from collector_core.infrastructure.redis_stream import RedisLiveStream
from collector_core.infrastructure.redis_stream import redis_url_from_env

from bybit_collector.client import BybitClient
from bybit_collector.open_interest import fetch_open_interest
from bybit_collector.policies import BybitSequenceCanary
from bybit_collector.trade_history import BybitTradeHistory
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment


CONFIG_PATH = Path(os.environ.get("BYBIT_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml"))


class BybitCollector(Collector):
    """
    Bybit's composition root on the shared gate: the client, the `u` sequence canary
    (`bybit_collector.policies`, DATA-08 -- a gap or regress drops the book, ledgers
    `collector.book_sequence` and queues a resync), the core's central-book crossed policy, the
    `recent-trade` history, the archive and live-stream adapters, the candle store and the REST
    open-interest poll. It overrides nothing but `__init__` (Story 26.1).
    """

    _config: BybitConfig

    VENUE: ClassVar[str] = "BYBIT"

    def __init__(self, config: BybitConfig, plan_ids: Iterable[str]) -> None:
        # `self._on_data`/`self._ledger` are bound methods: safe to hand out before
        # super().__init__ since no message can arrive before connect().
        env = (
            BybitEnvironment.TESTNET
            if config.environment == "testnet"
            else BybitEnvironment.MAINNET
        )
        client = BybitClient(
            on_data=self._on_data,
            environment=env,
            trade_feeds=config.trade_feeds,
            ledger=self._ledger,
        )
        # Composition root: this process owns Bybit's candle store (`CANDLES_DB_PATH`), so it
        # opens it, hands capture the sink port and runs the retention loop.
        store = store_from_env(config.catalog_path)
        super().__init__(
            config,
            client,
            extra_loops=(self._open_interest_loop, candle_prune_loop(store)),
            plan=plan_ids,
            archive=ParquetArchiveWriter(config.catalog_path),
            live_stream=RedisLiveStream(redis_url_from_env()),
            second_sink=CandleSink(store),
            policies=CapturePolicies(canary=BybitSequenceCanary()),
            trade_history=BybitTradeHistory(config.environment),
        )

    async def _open_interest_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.open_interest_poll_seconds)
            try:
                # The plan, not the applied set: ticker data is never gated by it (Story 25.4).
                wanted = set(self._plan_ids)
                for item in await fetch_open_interest(self._config.environment):
                    if str(item.instrument_id) in wanted:
                        # Straight to the buffer, not _on_data: REST-polled data must not
                        # count as WS feed liveness (story 22.5).
                        self._buffer[(type(item), str(item.instrument_id))].append(item)
            except Exception as e:
                self._ledger(sites.OPEN_INTEREST_POLL, "open interest poll failed", e)


def build_collector(config_path: Path = CONFIG_PATH) -> BybitCollector:
    """
    Composition root: the config and the static plan through the one loader; the plan is applied
    once at start through `Collector.apply` (Bybit has no live control plane, Story 25.4).
    """
    config, plan = load_venue_config(config_path, "BYBIT")
    if not isinstance(config, BybitConfig):
        raise TypeError(f"the BYBIT loader returned {type(config).__name__}, not BybitConfig")
    return BybitCollector(config, plan.collected)


if __name__ == "__main__":
    asyncio.run(run_forever(build_collector))
