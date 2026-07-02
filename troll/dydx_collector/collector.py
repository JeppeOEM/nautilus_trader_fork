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

Owns its own asyncio loop, a typed in-memory buffer, and a configurable snapshot loop.
No TradingNode/Strategy/DataEngine involved -- see client.py for why.

Instrument tiers
----------------
pinned      : listed in config.toml [[instruments]] -- always subscribed, never demoted
              to illiquid or pruned by non_config_retain_hours. Raw order-book-delta data
              (store_order_book_deltas=True) is still subject to that instrument's own
              retain_hours, if one is configured -- pinned only guarantees the subscription
              and non-delta data are kept forever, not that an explicit per-coin retention
              setting is overridden.
liquid      : OI >= liquidity_min_oi_usd -- subscribed, data pruned after non_config_retain_hours.
illiquid    : OI below threshold -- NOT subscribed to trades/book; re-checked every
              liquidity_check_seconds; graduated to liquid if OI crosses the threshold.
              Still receives mark/index/funding/status from subscribe_markets() (global).
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
import redis.asyncio as aioredis

from dydx_collector.client import DydxClient
from dydx_collector.config import CollectorConfig
from dydx_collector.config import load_config
from dydx_collector.open_interest import _fetch_markets_json
from dydx_collector.open_interest import classify_liquidity
from dydx_collector.open_interest import fetch_open_interest
from dydx_collector.prune_catalog import prune_instrument
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

CONFIG_PATH = Path(__file__).parent / "config.toml"

_QUARANTINE_DIRNAME = "_quarantine"

# ponytail: ParquetDataCatalog.write_data() (pinned nautilus_trader 1.229.0) has no
# compression passthrough -- it calls pq.write_table() with pyarrow's "snappy" default,
# and nautilus_trader/persistence/catalog/parquet.py can't be modified (fork rule). Patch
# pyarrow's default here instead. `pq` is a shared module object (Python caches modules in
# sys.modules), so this reaches nautilus's `import pyarrow.parquet as pq` call site too.
# Ceiling: if a future nautilus_trader version passes `compression=` explicitly, this patch
# is silently ignored -- revisit on version bump.
_orig_write_table = pq.write_table


def _write_table_zstd(*args: Any, **kwargs: Any) -> None:
    kwargs.setdefault("compression", "zstd")
    _orig_write_table(*args, **kwargs)


pq.write_table = _write_table_zstd


def quarantine_corrupt_parquet(catalog_path: str) -> None:
    """Move any unreadable .parquet file (e.g. left by a mid-write crash) out of the way.

    Runs once at process start, before any new writes. A half-written file from a killed
    process would otherwise sit forever next to good data and can break catalog reads or
    consolidation. ponytail: full recursive scan on every process start; if the catalog
    grows into the hundreds of thousands of files, switch to only checking files newer
    than the last clean shutdown.
    """
    root = Path(catalog_path).resolve()
    if not root.exists():
        return

    quarantine_root = root / _QUARANTINE_DIRNAME
    for path in root.rglob("*.parquet"):
        if quarantine_root in path.parents:
            continue
        try:
            pq.ParquetFile(path)
        except Exception:
            dest = quarantine_root / path.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(dest))
            logger.warning(f"Quarantined corrupt parquet file: {path} -> {dest}")

# Skip snapshot if book hasn't received OrderBookDeltas in this many nanoseconds.
# During WS reconnect recovery the Rust client re-subscribes at 2/sec, so the last
# instrument in the sorted queue can wait up to N/2 seconds for a fresh snapshot.
# Emitting the pre-reconnect stale book state during that window produces flatlines
# on the coin chart. 5 seconds is conservative — liquid dYdX instruments receive
# book updates multiple times per second under normal conditions.
_STALE_BOOK_NS: int = 5_000_000_000  # 5 seconds

# A real crossed book gets matched by the exchange within milliseconds -- it can't
# persist. If ours stays crossed this long, the local book has desynced from the
# venue (most likely a dropped/misordered delta during the startup subscribe-throttle
# scramble) and will never self-heal from more deltas alone. Force a resubscribe,
# which always starts with a Clear + fresh full snapshot.
_CROSSED_RESYNC_NS: int = 15_000_000_000  # 15 seconds


def _buffer_key(data: Any) -> tuple[type, str]:
    return type(data), str(data.instrument_id)


def _prune_interval_seconds(
    non_config_retain_hours: float, delta_retain_hours: dict[str, float | None]
) -> float:
    """Prune-loop cadence: roughly 4x per the shortest active retention window, minimum 15 min.

    Considers both the global non_config_retain_hours and any finite per-coin
    retain_hours, so a short per-coin window isn't left stale by a larger global one.
    """
    active_retain_hours = [
        non_config_retain_hours,
        *(h for h in delta_retain_hours.values() if h is not None),
    ]
    return max(min(active_retain_hours) * 900, 900)


def _prune_delta_retention(catalog_path: str, delta_retain_hours: dict[str, float | None]) -> int:
    """Prune order_book_deltas for instruments with a finite per-coin retain_hours.

    A `None` value means unlimited retention -- that instrument is skipped entirely.
    Extracted from _prune_loop so the None-skip behavior can be unit-tested directly.
    """
    freed = 0
    for iid, retain_hours in delta_retain_hours.items():
        if retain_hours is None:
            continue
        freed += prune_instrument(catalog_path, iid, retain_hours, data_types=["order_book_deltas"])
    return freed


async def _publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None:
    """Publish a batch of DydxSecondSnapshot objects to Redis channel snapshots:raw.

    Empty batches are silently dropped. Publish failures are logged and swallowed —
    missing one tick is acceptable per the architecture.
    """
    if not snapshots:
        return
    payload = json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])
    try:
        await redis_client.publish("snapshots:raw", payload)
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

        # Instrument tiers (populated in run())
        self._pinned: set[str] = {e.id for e in config.instruments}
        self._liquid: set[str] = set()
        self._illiquid: set[str] = set()
        # Instruments for which raw OrderBookDeltas are written to the catalog
        self._delta_store: set[str] = {e.id for e in config.instruments if e.store_order_book_deltas}
        # Per-coin raw-delta retention, in hours; None means unlimited (never pruned).
        # Independent of the pinned/non-pinned tier split used for non_config_retain_hours below.
        self._delta_retain_hours: dict[str, float | None] = {
            e.id: e.retain_hours for e in config.instruments if e.store_order_book_deltas
        }

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

        # Wall-clock ns when a book was first observed continuously crossed;
        # cleared as soon as it's seen uncrossed. Drives the resync watchdog below.
        self._crossed_since_ns: dict[str, int] = {}

        self._redis: aioredis.Redis | None = None
        self._stop = asyncio.Event()

    def _on_data(self, data: Any) -> None:
        # Called directly from the Rust WS client's callback thread/loop -- one malformed
        # or unexpected message must never take down the whole connection, so isolate it here.
        try:
            self._on_data_unsafe(data)
        except Exception:
            logger.exception(f"Failed to process {type(data).__name__}, dropping")

    def _on_data_unsafe(self, data: Any) -> None:
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
        for key, items in list(self._buffer.items()):
            if not items:
                continue
            self._buffer[key] = []
            dtype, iid = key

            if dtype is OrderBookDeltas and iid not in self._delta_store:
                continue

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

    async def _subscribe(self, iid: str) -> None:
        await self._client.subscribe_trades(iid)
        await self._client.subscribe_orderbook(iid)
        logger.info(f"Subscribed {iid}")

    async def _unsubscribe(self, iid: str) -> None:
        await self._client.unsubscribe_trades(iid)
        await self._client.unsubscribe_orderbook(iid)
        logger.info(f"Unsubscribed {iid}")

    async def _resync_book(self, iid: str) -> None:
        """Force a fresh order-book snapshot for a desynced instrument via resubscribe."""
        logger.warning(f"Resyncing desynced order book for {iid}")
        await self._client.unsubscribe_orderbook(iid)
        await self._client.subscribe_orderbook(iid)
        self._live_books.pop(iid, None)
        self._crossed_since_ns.pop(iid, None)

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
        """Hot-reload: updates the pinned set and per-coin delta-store/retention config."""
        while not self._stop.is_set():
            await asyncio.sleep(self._config.config_reload_seconds)
            new_config = load_config(CONFIG_PATH)
            old_pinned = self._pinned
            self._pinned = {e.id for e in new_config.instruments}
            self._delta_store = {e.id for e in new_config.instruments if e.store_order_book_deltas}
            self._delta_retain_hours = {
                e.id: e.retain_hours for e in new_config.instruments if e.store_order_book_deltas
            }

            # Subscribe any newly-pinned coins that were sitting in the illiquid pool
            for iid in self._pinned - old_pinned:
                if iid in self._illiquid:
                    await self._subscribe(iid)
                    self._illiquid.discard(iid)

            self._config = new_config

    async def _second_loop(self) -> None:
        """Sample L2 book at snapshot_interval_seconds; raw levels + trade volume only — signals computed on read."""
        while not self._stop.is_set():
            await asyncio.sleep(self._config.snapshot_interval_seconds)
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
                    crossed_since = self._crossed_since_ns.setdefault(iid, now_ns)
                    if now_ns - crossed_since > _CROSSED_RESYNC_NS:
                        await self._resync_book(iid)
                    continue
                self._crossed_since_ns.pop(iid, None)

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
        """Prune non-pinned instruments' catalog data, and any per-coin raw-delta retention."""
        while not self._stop.is_set():
            # Recomputed each iteration so a hot-reloaded retain_hours takes effect promptly.
            interval = _prune_interval_seconds(
                self._config.non_config_retain_hours, self._delta_retain_hours
            )
            await asyncio.sleep(interval)
            catalog_path = str(Path(self._config.catalog_path).resolve())
            non_pinned = (self._liquid | self._illiquid) - self._pinned
            freed = 0
            for iid in non_pinned:
                freed += prune_instrument(catalog_path, iid, self._config.non_config_retain_hours)
            if freed:
                logger.info(f"Pruned {freed / 1024 / 1024:.1f} MB from {len(non_pinned)} non-pinned instruments")

            delta_freed = _prune_delta_retention(catalog_path, self._delta_retain_hours)
            if delta_freed:
                logger.info(f"Pruned {delta_freed / 1024 / 1024:.1f} MB of raw order-book deltas (per-coin retention)")

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

        tasks = [
            asyncio.create_task(self._flush_loop()),
            asyncio.create_task(self._reload_config_loop()),
            asyncio.create_task(self._open_interest_loop()),
            asyncio.create_task(self._liquidity_check_loop()),
            asyncio.create_task(self._prune_loop()),
            asyncio.create_task(self._second_loop()),
        ]
        stop_task = asyncio.create_task(self._stop.wait())

        try:
            # Each loop already retries recoverable per-iteration errors internally (network
            # blips, etc). If one still dies, that's an unexpected bug -- surface it here so
            # the top-level restart loop in main() can do a full clean reconnect rather than
            # silently running degraded forever.
            done, _pending = await asyncio.wait(
                [*tasks, stop_task], return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                if task is not stop_task:
                    task.result()  # re-raises if the task died with an exception
        finally:
            stop_task.cancel()
            for task in tasks:
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

    quarantine_corrupt_parquet(config.catalog_path)

    shutting_down = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, shutting_down.set)
    loop.add_signal_handler(signal.SIGTERM, shutting_down.set)

    backoff_seconds = 1.0
    while not shutting_down.is_set():
        collector = Collector(config)
        watcher = asyncio.create_task(_stop_on_shutdown(shutting_down, collector))
        try:
            await collector.run()
            backoff_seconds = 1.0  # clean stop (signal) -- reset for any future crash
        except Exception:
            logger.exception(f"Collector crashed, restarting in {backoff_seconds:.0f}s")
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 60.0)
        finally:
            watcher.cancel()


async def _stop_on_shutdown(shutting_down: asyncio.Event, collector: Collector) -> None:
    await shutting_down.wait()
    collector.stop()


if __name__ == "__main__":
    asyncio.run(main())
