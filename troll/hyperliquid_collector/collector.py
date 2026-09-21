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
from pathlib import Path

from collector_core.collector import Collector
from collector_core.collector import run_forever
from collector_core.config import CoreConfig

from hyperliquid_collector.client import HyperliquidClient
from hyperliquid_collector.config import load_config


CONFIG_PATH = Path(
    os.environ.get("HYPERLIQUID_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml")
)


class HyperliquidCollector(Collector):
    def __init__(self, config: CoreConfig) -> None:
        client = HyperliquidClient(
            on_data=self._on_data,
            environment=config.environment,
            trade_feeds=config.trade_feeds,
        )
        super().__init__(config, client)


if __name__ == "__main__":
    asyncio.run(run_forever(lambda: HyperliquidCollector(load_config(CONFIG_PATH))))
