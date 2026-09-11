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

from nautilus_trader.persistence.catalog import ParquetDataCatalog

from ml_signals.catalog_stats import list_instruments
from ml_signals.catalog_stats import price_stats


logger = logging.getLogger(__name__)

# 25h covers the full 24h pct-change calc with a small buffer.
# Bounding price queries avoids scanning months of trade history on every tick.
PRICE_LOOKBACK_HOURS: float = 25.0


def compute_snapshot(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    now_ns: int,
    book_metrics_fn: Callable[[str], dict],
) -> dict:
    """
    book_metrics_fn supplies the ofi/microprice/spread fields -- ranking_engine (this
    function's only caller) passes its own live-indicator-state read here
    (troll/CLAUDE.md SSOT-02): that process must never run two independent stateful
    OFI/microprice trackers for the same instrument, which a fresh from-scratch
    Parquet replay would be relative to the live trackers it already maintains
    continuously from the Redis snapshot stream.
    """
    price_start_ns = now_ns - int(PRICE_LOOKBACK_HOURS * 3_600 * 1_000_000_000)
    stats = price_stats(catalog, instrument_id, start_ns=price_start_ns)
    book = book_metrics_fn(instrument_id)
    return {
        "ts": now_ns,
        "instrument_id": instrument_id,
        "price": stats.get("price"),
        "pct_1h": stats.get("pct_change_1h"),
        "pct_24h": stats.get("pct_change_24h"),
        "volatility": stats.get("volatility"),
        **book,
    }


def compute_all(
    catalog_path: str,
    book_metrics_fn: Callable[[str], dict],
    max_workers: int = 32,
    instrument_ids: list[str] | None = None,
) -> list[dict]:
    """Full snapshot (book metrics + price stats) for every instrument.

    book_metrics_fn is forwarded to compute_snapshot() -- see that function's own
    docstring (SSOT-02).

    instrument_ids, when given, replaces the default list_instruments(catalog_path)
    scan. list_instruments() globs every instrument folder the catalog has *ever*
    held data for (currently ~300 on the live deployment, most long unpinned) --
    scanning all of them with a 25h-lookback catalog read, 32-way concurrent, was
    what OOM-crashed ranking_engine repeatedly (see troll/CLAUDE.md DATA-02 incident,
    2026-09-11). ranking_engine passes its own live-seen instrument set here instead.
    """
    now_ns = time.time_ns()
    instruments = instrument_ids if instrument_ids is not None else list_instruments(catalog_path)

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
