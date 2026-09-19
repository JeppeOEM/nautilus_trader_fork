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
Hyperliquid market-data collector: own asyncio loop, ingest queue, buffer and flush timer
over `HyperliquidClient`, writing to the shared `ParquetDataCatalog` (Nautilus's own
write_data()). No TradingNode/Strategy/DataEngine.

Sibling of `bybit_collector`/`dydx_collector`, not a shared base (Story 19.4, DESIGN-01).
Hyperliquid-specific decisions (see client.py for the verified wire facts):
  * every l2Book message is a full snapshot (Clear + levels), applied atomically, so the
    local book can't drift: a crossed sample is just skipped -- the next message replaces
    the whole book, no resubscribe/uncross machinery.
  * l2Book pushes were observed ~5s apart, so the stale guard is config (default 30s), and
    the per-second sampler repeats the last authoritative book between pushes.
  * open interest arrives over the WS -- no REST poll loop.

Snapshots reuse `DydxSecondSnapshot` (venue-neutral schema) so data_api serves Hyperliquid
ids with zero per-route code (Story 19.2 AC3).
"""

import asyncio
import logging
import os
import signal
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from hyperliquid_collector.client import HyperliquidClient
from hyperliquid_collector.config import CollectorConfig
from hyperliquid_collector.config import load_config
from dydx_collector.second_snapshot import BOOK_DEPTH
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import instruments_from_pyo3
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

CONFIG_PATH = Path(os.environ.get("HYPERLIQUID_COLLECTOR_CONFIG", Path(__file__).parent / "config.toml"))

_INGEST_YIELD_EVERY = 64


class Collector:
    def __init__(self, config: CollectorConfig) -> None:
        self._config = config
        catalog_path = Path(config.catalog_path).resolve()
        catalog_path.mkdir(parents=True, exist_ok=True)
        self._catalog = ParquetDataCatalog(str(catalog_path))

        self._client = HyperliquidClient(
            on_data=self._on_data,
            environment=config.environment,
        )
        self._buffer: dict[tuple[type, str], list[Any]] = defaultdict(list)
        self._ingest_queue: asyncio.Queue[Any] = asyncio.Queue()
        self._stop = asyncio.Event()

        self._live_books: dict[str, OrderBook] = {}
        self._last_book_update_ns: dict[str, int] = {}
        # Per-second trade accumulators: [open, high, low, close, buy_vol, sell_vol, buy_n, sell_n]
        self._trades: dict[str, list[float]] = {}

    def _on_data(self, data: Any) -> None:
        # Runs on the event loop from the Rust callback: O(1) only.
        try:
            self._ingest_queue.put_nowait(data)
        except Exception:
            logger.exception(f"Failed to enqueue {type(data).__name__}, dropping")

    async def _ingest_loop(self) -> None:
        processed = 0
        while not self._stop.is_set():
            try:
                data = await asyncio.wait_for(self._ingest_queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            try:
                self._process_data(data)
            except Exception:
                logger.exception(f"Failed to process {type(data).__name__}, dropping")
            processed += 1
            if processed % _INGEST_YIELD_EVERY == 0:
                await asyncio.sleep(0)

    def _process_data(self, data: Any) -> None:
        if isinstance(data, OrderBookDeltas):
            iid = str(data.instrument_id)
            book = self._live_books.setdefault(iid, OrderBook(data.instrument_id, BookType.L2_MBP))
            for delta in data.deltas:
                book.apply_delta(delta)
            self._last_book_update_ns[iid] = time.time_ns()
        elif isinstance(data, TradeTick):
            self._accumulate_trade(data)
        else:  # mark/index price, funding rate, open interest -> catalog as-is
            self._buffer[(type(data), str(data.instrument_id))].append(data)

    def _accumulate_trade(self, trade: TradeTick) -> None:
        price, size = trade.price.as_double(), trade.size.as_double()
        acc = self._trades.get(str(trade.instrument_id))
        if acc is None:
            acc = self._trades[str(trade.instrument_id)] = [price, price, price, price, 0.0, 0.0, 0, 0]
        acc[1], acc[2], acc[3] = max(acc[1], price), min(acc[2], price), price
        i = 4 if trade.aggressor_side == AggressorSide.BUYER else 5
        acc[i] += size
        acc[i + 2] += 1

    def _sample(self, now_ns: int) -> list[DydxSecondSnapshot]:
        """One snapshot per healthy book."""
        batch: list[DydxSecondSnapshot] = []
        stale_ns = int(self._config.stale_book_seconds * 1e9)
        for iid in self._config.instruments:
            book = self._live_books.get(iid)
            trades = self._trades.pop(iid, None)  # always reset: never carry trades across an outage
            bid, ask = (book.best_bid_price(), book.best_ask_price()) if book else (None, None)
            if bid is None or ask is None:
                continue
            if bid.as_double() >= ask.as_double():
                logger.warning("Crossed book for %s (bid=%s ask=%s), skipping sample", iid, bid, ask)
                continue
            if now_ns - self._last_book_update_ns.get(iid, 0) > stale_ns:
                logger.warning("Stale book for %s, skipping sample", iid)
                continue
            batch.append(self._snapshot(iid, book, trades, now_ns))
        return batch

    @staticmethod
    def _snapshot(iid: str, book: OrderBook, trades: list[float] | None, now_ns: int) -> DydxSecondSnapshot:
        bids, asks = book.bids()[:BOOK_DEPTH], book.asks()[:BOOK_DEPTH]
        # No trades this second -> None prices, never a fabricated one.
        o, h, lo, c, bv, sv, bn, sn = trades or (None, None, None, None, 0.0, 0.0, 0, 0)
        return DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(iid),
            bid_prices=[lv.price.as_double() for lv in bids],
            bid_sizes=[lv.size() for lv in bids],
            ask_prices=[lv.price.as_double() for lv in asks],
            ask_sizes=[lv.size() for lv in asks],
            buy_volume=bv,
            sell_volume=sv,
            buy_count=int(bn),
            sell_count=int(sn),
            open_price=o,
            high_price=h,
            low_price=lo,
            close_price=c,
            ts_event=now_ns,
            ts_init=now_ns,
        )

    async def _second_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.snapshot_interval_seconds)
            for snapshot in self._sample(time.time_ns()):
                self._buffer[(DydxSecondSnapshot, str(snapshot.instrument_id))].append(snapshot)

    async def _flush_once(self) -> None:
        for key, items in list(self._buffer.items()):
            if not items:
                continue
            self._buffer[key] = []
            try:
                # Real disk I/O -- off the event loop so _second_loop isn't stalled.
                await asyncio.to_thread(self._catalog.write_data, items)
            except Exception:
                logger.exception(f"Failed to write {key}, dropping {len(items)} items")

    async def _flush_loop(self) -> None:
        while not self._stop.is_set():
            await asyncio.sleep(self._config.flush_interval_seconds)
            await self._flush_once()

    async def run(self) -> None:
        instruments = await self._client.fetch_instruments()
        by_id = {i.id.value: i for i in instruments}
        self._catalog.write_data(instruments_from_pyo3(list(by_id.values())))

        await self._client.connect(asyncio.get_running_loop(), list(by_id.values()))
        unknown = set(self._config.instruments) - set(by_id)
        if unknown:
            logger.warning("Configured instruments not found on Hyperliquid, skipping: %s", sorted(unknown))
        for iid in sorted(set(self._config.instruments) & set(by_id)):
            await self._client.subscribe(iid)
            logger.info(f"Subscribed {iid}")

        tasks = [
            asyncio.create_task(loop())
            for loop in (self._ingest_loop, self._flush_loop, self._second_loop)
        ]
        stop_task = asyncio.create_task(self._stop.wait())
        try:
            # A loop dying is an unexpected bug: surface it so main() does a clean restart.
            done, _ = await asyncio.wait([*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not stop_task:
                    task.result()
        finally:
            stop_task.cancel()
            for task in tasks:
                task.cancel()
            await self._client.disconnect()
            await self._flush_once()

    def stop(self) -> None:
        self._stop.set()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config(CONFIG_PATH)

    shutting_down = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, shutting_down.set)
    loop.add_signal_handler(signal.SIGTERM, shutting_down.set)

    backoff_seconds = 1.0
    while not shutting_down.is_set():
        collector = Collector(config)

        async def _stop_on_shutdown(c: Collector = collector) -> None:
            await shutting_down.wait()
            c.stop()

        watcher = asyncio.create_task(_stop_on_shutdown())
        try:
            await collector.run()
            backoff_seconds = 1.0
        except Exception:
            logger.exception(f"Collector crashed, restarting in {backoff_seconds:.0f}s")
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60.0)
        finally:
            watcher.cancel()


if __name__ == "__main__":
    asyncio.run(main())
