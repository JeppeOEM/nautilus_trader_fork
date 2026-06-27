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
dYdX market data collector entrypoint.

Owns its own asyncio loop and a plain in-memory buffer flushed periodically to a
ParquetDataCatalog. No TradingNode/Strategy/DataEngine involved -- see client.py
and memory project_dydx_collector_python_pivot for why.

"""

import asyncio
import logging
import signal
from collections import defaultdict
from pathlib import Path
from typing import Any

from dydx_collector.client import DydxClient
from dydx_collector.config import CollectorConfig
from dydx_collector.config import InstrumentEntry
from dydx_collector.config import diff_instruments
from dydx_collector.config import load_config
from dydx_collector.open_interest import fetch_open_interest
from nautilus_trader.model.data import Bar
from nautilus_trader.model.instruments import instruments_from_pyo3
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.toml"


def _buffer_key(data: Any) -> tuple[type, str]:
    if isinstance(data, Bar):
        return type(data), str(data.bar_type)
    return type(data), str(data.instrument_id)


def _bar_type(instrument_id: str, interval: str) -> str:
    return f"{instrument_id}-{interval}-LAST-EXTERNAL"


class Collector:
    def __init__(self, config: CollectorConfig) -> None:
        self._config = config

        catalog_path = Path(config.catalog_path).resolve()
        catalog_path.mkdir(parents=True, exist_ok=True)
        self._catalog = ParquetDataCatalog(str(catalog_path))

        self._client = DydxClient(on_data=self._on_data, network=config.network)
        self._buffer: dict[tuple[type, str], list[Any]] = defaultdict(list)
        self._active: dict[str, InstrumentEntry] = {e.id: e for e in config.instruments}
        self._stop = asyncio.Event()

    def _on_data(self, data: Any) -> None:
        self._buffer[_buffer_key(data)].append(data)

    def _flush_once(self) -> None:
        for key, items in list(self._buffer.items()):
            if not items:
                continue
            self._buffer[key] = []
            try:
                self._catalog.write_data(items)
            except Exception:
                logger.exception(f"Failed to write {key}, dropping {len(items)} items")

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.flush_interval_seconds)
            self._flush_once()

    async def _open_interest_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.open_interest_poll_seconds)
            try:
                for item in await fetch_open_interest(self._config.network):
                    self._on_data(item)
            except Exception:
                logger.exception("Failed to poll open interest")

    async def _subscribe(self, entry: InstrumentEntry) -> None:
        await self._client.subscribe_trades(entry.id)
        await self._client.subscribe_orderbook(entry.id)
        for interval in entry.bar_intervals:
            await self._client.subscribe_bars(_bar_type(entry.id, interval))
        logger.info(f"Subscribed {entry.id}")

    async def _unsubscribe(self, entry: InstrumentEntry) -> None:
        await self._client.unsubscribe_trades(entry.id)
        await self._client.unsubscribe_orderbook(entry.id)
        for interval in entry.bar_intervals:
            await self._client.unsubscribe_bars(_bar_type(entry.id, interval))
        logger.info(f"Unsubscribed {entry.id}")

    async def _reload_config_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.config_reload_seconds)
            new_config = load_config(CONFIG_PATH)

            if not new_config.instruments:
                # All-instruments mode — nothing to diff; subscriptions are managed at startup.
                self._config = new_config
                continue

            added, removed = diff_instruments(
                tuple(self._active.values()),
                new_config.instruments,
            )
            for entry in added:
                await self._subscribe(entry)
                self._active[entry.id] = entry
            for entry in removed:
                await self._unsubscribe(entry)
                del self._active[entry.id]

            self._config = new_config

    async def run(self) -> None:
        # Raw pyo3-native instruments: what connect()'s instrument cache expects.
        instruments = await self._client.fetch_instruments()
        instruments_by_id = {i.id.value: i for i in instruments}

        if not self._active:
            # No explicit list → subscribe to every instrument on dYdX.
            # bar_intervals=() skips bar subscriptions to avoid hitting the
            # 2/sec subscribe rate limit during the long startup burst.
            self._active = {
                iid: InstrumentEntry(id=iid, bar_intervals=())
                for iid in instruments_by_id
            }
            logger.info(f"Auto-subscribing all {len(self._active)} dYdX instruments")

        wanted = [
            instruments_by_id[entry.id]
            for entry in self._active.values()
            if entry.id in instruments_by_id
        ]
        missing = [entry.id for entry in self._active.values() if entry.id not in instruments_by_id]
        if missing:
            logger.warning(f"Configured instruments not found on dYdX: {missing}")
        if wanted:
            # Catalog/Arrow serializer needs the Cython model, not the pyo3 one.
            self._catalog.write_data(instruments_from_pyo3(wanted))

        loop = asyncio.get_running_loop()
        await self._client.connect(loop, wanted)
        await self._client.subscribe_markets()

        for entry in self._active.values():
            if entry.id in instruments_by_id:
                await self._subscribe(entry)

        flush_task = asyncio.create_task(self._flush_loop())
        reload_task = asyncio.create_task(self._reload_config_loop())
        oi_task = asyncio.create_task(self._open_interest_loop())

        try:
            await self._stop.wait()
        finally:
            flush_task.cancel()
            reload_task.cancel()
            oi_task.cancel()
            await self._client.disconnect()
            self._flush_once()

    def stop(self) -> None:
        self._stop.set()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = load_config(CONFIG_PATH)
    collector = Collector(config)

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, collector.stop)
    loop.add_signal_handler(signal.SIGTERM, collector.stop)

    await collector.run()


if __name__ == "__main__":
    asyncio.run(main())
