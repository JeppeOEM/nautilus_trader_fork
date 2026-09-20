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

Bybit has a central book, so a crossed local book is corruption: the core skips the
sample, ledgers it, and -- because `BybitClient` exposes `resync_orderbook` -- forces a
fresh snapshot only once it stayed crossed past `crossed_resync_seconds` (DATA-03 fallback).
Open interest is dropped by the Rust bindings on the linear ticker path, so it is polled
over REST as an extra loop.
"""

import asyncio
import os
from pathlib import Path

from collector_core.collector import Collector
from collector_core.collector import run_forever
from ml_signals import error_ledger

from bybit_collector.client import BybitClient
from bybit_collector.config import BybitConfig
from bybit_collector.config import load_config
from bybit_collector.open_interest import fetch_open_interest
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment


CONFIG_PATH = Path(os.environ.get("BYBIT_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml"))


class BybitCollector(Collector):
    _config: BybitConfig

    def __init__(self, config: BybitConfig) -> None:
        # `self._on_data` is a bound method: safe to hand out before super().__init__ since
        # no message can arrive before connect().
        env = (
            BybitEnvironment.TESTNET
            if config.environment == "testnet"
            else BybitEnvironment.MAINNET
        )
        client = BybitClient(on_data=self._on_data, environment=env)
        super().__init__(config, client, extra_loops=(self._open_interest_loop,))

    async def _open_interest_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.open_interest_poll_seconds)
            try:
                wanted = set(self._instrument_ids())
                for item in await fetch_open_interest(self._config.environment):
                    if str(item.instrument_id) in wanted:
                        # Straight to the buffer, not _on_data: REST-polled data must not
                        # count as WS feed liveness (`_last_feed_message_ns`, story 22.5).
                        self._buffer[(type(item), str(item.instrument_id))].append(item)
            except Exception as e:
                error_ledger.record("collector.open_interest_poll", "open interest poll failed", e)


if __name__ == "__main__":
    asyncio.run(run_forever(lambda: BybitCollector(load_config(CONFIG_PATH))))
