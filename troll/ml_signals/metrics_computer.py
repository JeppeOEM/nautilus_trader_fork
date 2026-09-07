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
Compute metric snapshots for all instruments in the catalog.

Called periodically by the dashboard's background thread. Each snapshot holds
the metrics defined in metrics_store.COLS. Add new computation here and a
matching column in metrics_store.COLS to extend what gets tracked.
"""

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from ml_signals.book_features import top_of_book_series
from ml_signals.catalog_stats import list_instruments
from ml_signals.catalog_stats import price_stats
from ml_signals.indicators import Microprice
from ml_signals.indicators import OrderFlowImbalance


logger = logging.getLogger(__name__)

# 1-minute window keeps catalog reads fast and OFI/microprice responsive.
# Wider windows are more stable but slower and staler.
OFI_LOOKBACK_SECONDS: int = 60
# ponytail: small window so OFI initializes even on quiet coins (< 10 events/min).
# The signal is noisier than a 50-update window but at least non-None for all active coins.
OFI_WINDOW: int = 5

# 25h covers the full 24h pct-change calc with a small buffer.
# Bounding price queries avoids scanning months of trade history on every tick.
PRICE_LOOKBACK_HOURS: float = 25.0

# A gap this large between consecutive top-of-book updates means the book was
# desynced/reconnecting for a while (see collector.py's crossed-book resync
# watchdog) -- treat the next update as a fresh start rather than feeding OFI
# a delta computed across the missing period. Matches the dashboard's live-path
# gap threshold in _ingest_batch.
_BOOK_GAP_NS: int = 3_000_000_000  # 3 seconds


def _book_metrics(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    now_ns: int,
) -> dict:
    """OFI, microprice, and spread from the last OFI_LOOKBACK_SECONDS of book deltas."""
    start_ns = now_ns - OFI_LOOKBACK_SECONDS * 1_000_000_000
    try:
        deltas = catalog.order_book_deltas(instrument_ids=[instrument_id], start=start_ns)
    except Exception:
        return {}
    if not deltas:
        return {}

    ofi = OrderFlowImbalance(window=OFI_WINDOW)
    micro = Microprice()
    last_bid = last_ask = None
    prev_ts = None

    for ts, bid_p, bid_s, ask_p, ask_s in top_of_book_series(deltas, InstrumentId.from_str(instrument_id)):
        if prev_ts is not None and ts - prev_ts > _BOOK_GAP_NS:
            ofi.reset()  # discard OFI window spanning the gap, don't feed it a stale delta
        ofi.update_raw(bid_p, bid_s, ask_p, ask_s)
        micro.update_raw(bid_p, bid_s, ask_p, ask_s)
        last_bid, last_ask = bid_p, ask_p
        prev_ts = ts

    return {
        "ofi": ofi.value if ofi.initialized else None,
        "microprice": micro.value if micro.initialized else None,
        "spread": (last_ask - last_bid) if last_bid is not None and last_ask is not None else None,
    }


def compute_snapshot(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    now_ns: int,
    book_metrics_fn: Callable[[str], dict] | None = None,
) -> dict:
    """
    book_metrics_fn, when given, replaces the default from-Parquet _book_metrics() for
    the ofi/microprice/spread fields -- ranking_engine (this function's only real
    caller) passes its own live-indicator-state read here (troll/CLAUDE.md SSOT-02):
    that process must never run two independent stateful OFI/microprice trackers for
    the same instrument, which a fresh from-scratch Parquet replay every call would be
    relative to the live trackers it already maintains continuously from the Redis
    snapshot stream. None (the default) keeps the original from-Parquet behavior for
    any other/future caller with no live state of its own to read.
    """
    price_start_ns = now_ns - int(PRICE_LOOKBACK_HOURS * 3_600 * 1_000_000_000)
    stats = price_stats(catalog, instrument_id, start_ns=price_start_ns)
    book = (
        book_metrics_fn(instrument_id)
        if book_metrics_fn is not None
        else _book_metrics(catalog, instrument_id, now_ns)
    )
    return {
        "ts": now_ns,
        "instrument_id": instrument_id,
        "price": stats.get("price"),
        "pct_1h": stats.get("pct_change_1h"),
        "pct_24h": stats.get("pct_change_24h"),
        "volatility": stats.get("volatility"),
        **book,
    }


def compute_book_metrics_all(catalog_path: str, max_workers: int = 32) -> list[dict]:
    """Fast path: only OFI/microprice/spread from the last OFI_LOOKBACK_SECONDS.

    Runs in a few seconds even for hundreds of instruments because it only
    reads one minute of order book deltas per coin.
    """
    now_ns = time.time_ns()
    instruments = list_instruments(catalog_path)

    def _one(iid: str) -> dict | None:
        try:
            book = _book_metrics(ParquetDataCatalog(catalog_path), iid, now_ns)
            if not book:
                return None
            return {"ts": now_ns, "instrument_id": iid, **book}
        except Exception:
            logger.warning("Book metrics failed for %s", iid, exc_info=True)
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_one, instruments))
    snapshots = [r for r in results if r is not None]
    logger.info("Book metrics computed for %d/%d instruments", len(snapshots), len(instruments))
    return snapshots


def compute_all(
    catalog_path: str,
    max_workers: int = 32,
    book_metrics_fn: Callable[[str], dict] | None = None,
) -> list[dict]:
    """Full snapshot (book metrics + price stats) for every instrument.

    Slower than compute_book_metrics_all due to the 25h price lookback.
    Run this on a longer interval for bookkeeping; use compute_book_metrics_all
    for the live-update loop.

    book_metrics_fn is forwarded to compute_snapshot() -- see that function's own
    docstring (SSOT-02).
    """
    now_ns = time.time_ns()
    instruments = list_instruments(catalog_path)

    def _one(iid: str) -> dict | None:
        try:
            return compute_snapshot(ParquetDataCatalog(catalog_path), iid, now_ns, book_metrics_fn)
        except Exception:
            logger.warning("Snapshot failed for %s", iid, exc_info=True)
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        results = list(ex.map(_one, instruments))

    snapshots = [r for r in results if r is not None]
    logger.info("Full snapshots computed for %d/%d instruments", len(snapshots), len(instruments))
    return snapshots
