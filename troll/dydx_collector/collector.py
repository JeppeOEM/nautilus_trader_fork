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

Owns its own asyncio loop, a typed in-memory buffer, and a MinuteBarBuilder.
No TradingNode/Strategy/DataEngine involved -- see client.py for why.

Instrument tiers
----------------
pinned      : listed in config.toml [[instruments]] -- always subscribed, kept forever.
liquid      : OI >= liquidity_min_oi_usd -- subscribed, data pruned after non_config_retain_hours.
illiquid    : OI below threshold -- NOT subscribed to trades/book; re-checked every
              liquidity_check_seconds; graduated to liquid if OI crosses the threshold.
              Still receives mark/index/funding/status from subscribe_markets() (global).
"""

import asyncio
import json
import logging
import os
import signal
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

from dydx_collector.client import DydxClient
from dydx_collector.config import CollectorConfig
from dydx_collector.config import load_config
from dydx_collector.minute_bars import DydxMinuteBar
from dydx_collector.minute_bars import MinuteBarBuilder
from dydx_collector.open_interest import _fetch_markets_json
from dydx_collector.open_interest import classify_liquidity
from dydx_collector.open_interest import fetch_open_interest
from dydx_collector.prune_catalog import prune_instrument
from dydx_collector.second_snapshot import BOOK_DEPTH
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import instruments_from_pyo3
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.toml"

# Skip snapshot if book hasn't received OrderBookDeltas in this many nanoseconds.
# During WS reconnect recovery the Rust client re-subscribes at 2/sec, so the last
# instrument in the sorted queue can wait up to N/2 seconds for a fresh snapshot.
# Emitting the pre-reconnect stale book state during that window produces flatlines
# on the coin chart. 5 seconds is conservative — liquid dYdX instruments receive
# book updates multiple times per second under normal conditions.
_STALE_BOOK_NS: int = 5_000_000_000  # 5 seconds


def _buffer_key(data: Any) -> tuple[type, str]:
    return type(data), str(data.instrument_id)


async def _publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None:
    """Publish a batch of DydxSecondSnapshot objects to Redis channel snapshots:1s.

    Empty batches are silently dropped. Publish failures are logged and swallowed —
    missing one tick is acceptable per the architecture.
    """
    if not snapshots:
        return
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    try:
        await redis_client.publish("snapshots:1s", payload)
    except Exception as e:
        logger.warning("Redis publish failed: %s", e)


class Collector:
    def __init__(self, config: CollectorConfig) -> None:
        self._config = config

        catalog_path = Path(config.catalog_path).resolve()
        catalog_path.mkdir(parents=True, exist_ok=True)
        self._catalog = ParquetDataCatalog(str(catalog_path))

        self._client = DydxClient(on_data=self._on_data, network=config.network)
        self._buffer: dict[tuple[type, str], list[Any]] = defaultdict(list)
        self._bar_builder = MinuteBarBuilder()

        # Instrument tiers (populated in run())
        self._pinned: set[str] = {e.id for e in config.instruments}
        self._liquid: set[str] = set()
        self._illiquid: set[str] = set()
        # Instruments for which raw OrderBookDeltas are written to the catalog
        self._delta_store: set[str] = {e.id for e in config.instruments if e.store_order_book_deltas}

        # Trade volume/count accumulated between consecutive 1s ticks, reset each second
        self._second_buy_volume: dict[str, float] = defaultdict(float)
        self._second_sell_volume: dict[str, float] = defaultdict(float)
        self._second_buy_count: dict[str, int] = defaultdict(int)
        self._second_sell_count: dict[str, int] = defaultdict(int)

        # Real-time order books — updated immediately on every OrderBookDeltas callback,
        # independent of the 60s flush cycle. _second_loop reads from here, not _bar_builder._books.
        self._live_books: dict[str, OrderBook] = {}
        # Wall-clock ns of the last OrderBookDeltas received per instrument.
        # Used by _second_loop to skip stale books (staleness = no updates for > _STALE_BOOK_NS).
        self._last_book_update_ns: dict[str, int] = {}

        self._redis: aioredis.Redis | None = None
        self._stop = asyncio.Event()

    def _on_data(self, data: Any) -> None:
        self._buffer[_buffer_key(data)].append(data)
        if isinstance(data, OrderBookDeltas):
            iid = str(data.instrument_id)
            if iid not in self._live_books:
                self._live_books[iid] = OrderBook(data.instrument_id, BookType.L2_MBP)
            book = self._live_books[iid]
            for delta in data.deltas:
                book.apply_delta(delta)
            self._last_book_update_ns[iid] = time.time_ns()
        elif isinstance(data, TradeTick):
            iid = str(data.instrument_id)
            if data.aggressor_side == AggressorSide.BUYER:
                self._second_buy_volume[iid] += data.size.as_double()
                self._second_buy_count[iid] += 1
            else:
                self._second_sell_volume[iid] += data.size.as_double()
                self._second_sell_count[iid] += 1

    def _flush_once(self) -> None:
        now_ns = time.time_ns()
        trades_by_iid: dict[str, list[TradeTick]] = defaultdict(list)
        deltas_by_iid: dict[str, list[OrderBookDeltas]] = defaultdict(list)
        marks_by_iid: dict[str, list[MarkPriceUpdate]] = defaultdict(list)

        for key, items in list(self._buffer.items()):
            if not items:
                continue
            self._buffer[key] = []
            dtype, iid = key

            if dtype is TradeTick:
                trades_by_iid[iid].extend(items)
            elif dtype is OrderBookDeltas:
                deltas_by_iid[iid].extend(items)
                if iid not in self._delta_store:
                    continue
            elif dtype is MarkPriceUpdate:
                marks_by_iid[iid].extend(items)

            try:
                self._catalog.write_data(items)
            except Exception:
                logger.exception(f"Failed to write {key}, dropping {len(items)} items")

        # Build enriched 1-min bars for subscribed instruments that had book/trade activity
        subscribed = self._pinned | self._liquid
        bar_instruments = (set(trades_by_iid) | set(deltas_by_iid)) & subscribed
        bars: list[DydxMinuteBar] = []
        for iid in bar_instruments:
            bars.extend(self._bar_builder.update(
                instrument_id=iid,
                trades=trades_by_iid.get(iid, []),
                delta_batches=deltas_by_iid.get(iid, []),
                marks=marks_by_iid.get(iid, []),
                now_ns=now_ns,
            ))
        if bars:
            try:
                self._catalog.write_data(bars)
            except Exception:
                logger.exception(f"Failed to write {len(bars)} minute bars")

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

    async def _subscribe(self, iid: str) -> None:
        await self._client.subscribe_trades(iid)
        await self._client.subscribe_orderbook(iid)
        logger.info(f"Subscribed {iid}")

    async def _unsubscribe(self, iid: str) -> None:
        await self._client.unsubscribe_trades(iid)
        await self._client.unsubscribe_orderbook(iid)
        logger.info(f"Unsubscribed {iid}")

    async def _liquidity_check_loop(self) -> None:
        """Periodically graduate illiquid→liquid (subscribe) or liquid→illiquid (unsubscribe)."""
        while not self._stop.is_set():
            await asyncio.sleep(self._config.liquidity_check_seconds)
            try:
                markets_json = await asyncio.to_thread(_fetch_markets_json, self._config.network)
                liquid, illiquid = classify_liquidity(
                    markets_json, self._config.liquidity_min_oi_usd, self._config.exclude
                )

                for iid in liquid & self._illiquid:
                    await self._subscribe(iid)
                    self._liquid.add(iid)
                    self._illiquid.discard(iid)

                for iid in illiquid & self._liquid:
                    await self._unsubscribe(iid)
                    self._illiquid.add(iid)
                    self._liquid.discard(iid)

                logger.info(
                    f"Liquidity check: {len(self._liquid)} liquid, "
                    f"{len(self._illiquid)} illiquid, {len(self._pinned)} pinned"
                )
            except Exception:
                logger.exception("Liquidity check failed")

    async def _reload_config_loop(self) -> None:
        """Hot-reload: only updates the pinned set; subscriptions are managed by liquidity tier."""
        while not self._stop.is_set():
            await asyncio.sleep(self._config.config_reload_seconds)
            new_config = load_config(CONFIG_PATH)
            old_pinned = self._pinned
            self._pinned = {e.id for e in new_config.instruments}

            # Subscribe any newly-pinned coins that were sitting in the illiquid pool
            for iid in self._pinned - old_pinned:
                if iid in self._illiquid:
                    await self._subscribe(iid)
                    self._illiquid.discard(iid)

            self._config = new_config

    async def _second_loop(self) -> None:
        """Sample L2 book every second; raw levels + trade volume only — signals computed on read."""
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            now_ns = time.time_ns()
            batch: list[DydxSecondSnapshot] = []
            for iid in self._pinned | self._liquid:
                book = self._live_books.get(iid)
                if book is None:
                    continue
                if book.best_bid_price() is None or book.best_ask_price() is None:
                    continue
                # Skip crossed/touched book — can occur briefly during reconnect snapshot replay
                if book.best_bid_price().as_double() >= book.best_ask_price().as_double():
                    logger.warning(
                        "Crossed book for %s (bid=%.6f >= ask=%.6f) — skipping snapshot",
                        iid,
                        book.best_bid_price().as_double(),
                        book.best_ask_price().as_double(),
                    )
                    continue

                # Staleness guard: skip if no OrderBookDeltas have arrived recently.
                # During WS reconnect recovery the book retains its pre-reconnect state
                # until the re-subscription snapshot arrives. Emitting that stale state
                # produces a flatline on the coin chart. See _STALE_BOOK_NS for threshold.
                last_book_update_ns = self._last_book_update_ns.get(iid, 0)
                if now_ns - last_book_update_ns > _STALE_BOOK_NS:
                    logger.warning(
                        "Stale book for %s (no OrderBookDeltas for %.1fs) — skipping snapshot",
                        iid,
                        (now_ns - last_book_update_ns) / 1e9,
                    )
                    continue

                bid_levels = book.bids()[:BOOK_DEPTH]
                ask_levels = book.asks()[:BOOK_DEPTH]
                buy_volume = self._second_buy_volume.pop(iid, 0.0)
                sell_volume = self._second_sell_volume.pop(iid, 0.0)
                buy_count = self._second_buy_count.pop(iid, 0)
                sell_count = self._second_sell_count.pop(iid, 0)

                snapshot = DydxSecondSnapshot(
                    instrument_id=InstrumentId.from_str(iid),
                    bid_prices=[lv.price.as_double() for lv in bid_levels],
                    bid_sizes=[lv.size() for lv in bid_levels],
                    ask_prices=[lv.price.as_double() for lv in ask_levels],
                    ask_sizes=[lv.size() for lv in ask_levels],
                    buy_volume=buy_volume,
                    sell_volume=sell_volume,
                    buy_count=buy_count,
                    sell_count=sell_count,
                    ts_event=now_ns,
                    ts_init=now_ns,
                )
                batch.append(snapshot)
                self._on_data(snapshot)  # routes to buffer → Parquet flush

            await _publish_snapshot_batch(self._redis, batch)

    async def _prune_loop(self) -> None:
        """Prune non-pinned instruments' catalog data older than non_config_retain_hours."""
        # Run roughly 4x per retention window (every retain/4 hours, minimum 15 min)
        interval = max(self._config.non_config_retain_hours * 900, 900)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            catalog_path = str(Path(self._config.catalog_path).resolve())
            non_pinned = (self._liquid | self._illiquid) - self._pinned
            freed = 0
            for iid in non_pinned:
                freed += prune_instrument(catalog_path, iid, self._config.non_config_retain_hours)
            if freed:
                logger.info(f"Pruned {freed / 1024 / 1024:.1f} MB from {len(non_pinned)} non-pinned instruments")

    async def run(self) -> None:
        self._redis = aioredis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
        )

        instruments = await self._client.fetch_instruments()
        instruments_by_id = {i.id.value: i for i in instruments}

        self._catalog.write_data(instruments_from_pyo3(list(instruments_by_id.values())))

        loop = asyncio.get_running_loop()
        await self._client.connect(loop, list(instruments_by_id.values()))
        await self._client.subscribe_markets()

        # Classify by volume; pinned coins bypass the threshold; excluded coins never subscribed
        markets_json = await asyncio.to_thread(_fetch_markets_json, self._config.network)
        liquid, illiquid = classify_liquidity(
            markets_json, self._config.liquidity_min_oi_usd, self._config.exclude
        )

        known = set(instruments_by_id)
        to_subscribe = ((self._pinned | liquid) & known) - self._config.exclude
        self._liquid = (liquid & known) - self._pinned
        self._illiquid = known - to_subscribe

        for iid in sorted(to_subscribe):
            await self._subscribe(iid)

        logger.info(
            f"Started: {len(to_subscribe)} subscribed "
            f"({len(self._pinned)} pinned, {len(self._liquid)} liquid), "
            f"{len(self._illiquid)} illiquid (monitoring)"
        )

        flush_task = asyncio.create_task(self._flush_loop())
        reload_task = asyncio.create_task(self._reload_config_loop())
        oi_task = asyncio.create_task(self._open_interest_loop())
        liquidity_task = asyncio.create_task(self._liquidity_check_loop())
        prune_task = asyncio.create_task(self._prune_loop())
        second_task = asyncio.create_task(self._second_loop())

        try:
            await self._stop.wait()
        finally:
            for task in (flush_task, reload_task, oi_task, liquidity_task, prune_task, second_task):
                task.cancel()
            await self._client.disconnect()
            self._flush_once()
            if self._redis is not None:
                await self._redis.aclose()

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
