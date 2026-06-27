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
"""Catalog analytics: per-instrument data coverage, gap detection, price/volatility stats."""

import glob
import os
from pathlib import Path

import numpy as np
import pandas as pd

from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.persistence.catalog import ParquetDataCatalog


# Order matters only for price_series()'s fallback preference below.
DATA_TYPES = (
    "trade_tick",
    "bar",
    "order_book_deltas",
    "mark_price_update",
    "index_price_update",
    "funding_rate_update",
    "instrument_status",
)


def list_instruments(catalog_path: str) -> list[str]:
    """All instrument IDs appearing in any data type partition in the catalog."""
    ids: set[str] = set()
    for data_type in DATA_TYPES:
        for path in glob.glob(os.path.join(catalog_path, "data", data_type, "*")):
            ids.add(Path(path).name)
    return sorted(ids)


def _load(catalog: ParquetDataCatalog, data_type: str, instrument_id: str) -> list:
    if data_type == "trade_tick":
        return catalog.trade_ticks(instrument_ids=[instrument_id])
    if data_type == "bar":
        return catalog.bars(instrument_ids=[instrument_id])
    if data_type == "order_book_deltas":
        return catalog.order_book_deltas(instrument_ids=[instrument_id])
    if data_type == "mark_price_update":
        return catalog.query(MarkPriceUpdate, identifiers=[instrument_id])
    if data_type == "index_price_update":
        return catalog.query(IndexPriceUpdate, identifiers=[instrument_id])
    if data_type == "funding_rate_update":
        return catalog.funding_rates(instrument_ids=[instrument_id])
    if data_type == "instrument_status":
        return catalog.instrument_status(instrument_ids=[instrument_id])
    raise ValueError(f"Unknown data type: {data_type}")  # pragma: no cover


def find_gaps(
    ts_ns: list[int],
    threshold_seconds: float | None = None,
    min_multiple: float = 5.0,
    floor_seconds: float = 30.0,
) -> list[tuple[int, int]]:
    """
    Find irregularly large spacing in a sorted-ascending timestamp series (nanoseconds).

    This is spacing, not error detection: for trade-driven types (trade_tick,
    order_book_deltas, bar) a "gap" here is just as likely to mean the market
    was quiet as it is a dropped connection. See `likely_outages()` for a
    cross-type signal that's actually indicative of a real outage.

    ponytail: adaptive heuristic (median inter-arrival time * `min_multiple`,
    floored at `floor_seconds`), not a statistical changepoint model. Pass an
    explicit `threshold_seconds` for data with a known fixed cadence instead
    of relying on the heuristic.
    """
    if len(ts_ns) < 2:
        return []

    deltas = np.diff(ts_ns) / 1e9  # seconds
    if threshold_seconds is None:
        threshold_seconds = max(float(np.median(deltas)) * min_multiple, floor_seconds)

    return [(ts_ns[i], ts_ns[i + 1]) for i, delta in enumerate(deltas) if delta > threshold_seconds]


def _overlapping_intervals(
    a: list[tuple[int, int]],
    b: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Pairwise intersections between two lists of sorted, non-overlapping (start, end) intervals."""
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        start = max(a[i][0], b[j][0])
        end = min(a[i][1], b[j][1])
        if start < end:
            result.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return result


def likely_outages(catalog: ParquetDataCatalog, instrument_id: str) -> list[tuple[int, int]]:
    """
    Periods where mark_price_update AND order_book_deltas were both silent at once.

    mark_price_update is pushed by the venue on its own clock, independent of
    trading activity, so a gap there alone is already a decent outage signal.
    Requiring order_book_deltas to be silent over the same window too filters
    out the case where a single stream gap is just a deserialization hiccup
    rather than a real disconnect.
    """
    try:
        mark_rows = _load(catalog, "mark_price_update", instrument_id)
    except (NotImplementedError, RuntimeError):
        return []
    book_rows = _load(catalog, "order_book_deltas", instrument_id)
    if not mark_rows or not book_rows:
        return []

    mark_gaps = find_gaps(sorted(row.ts_event for row in mark_rows))
    book_gaps = find_gaps(sorted(row.ts_event for row in book_rows))
    return _overlapping_intervals(mark_gaps, book_gaps)


def coverage(catalog: ParquetDataCatalog, instrument_id: str) -> dict[str, dict]:
    """Per-data-type row count, start/end timestamp, duration, and detected gaps."""
    result = {}
    for data_type in DATA_TYPES:
        try:
            rows = _load(catalog, data_type, instrument_id)
        except (NotImplementedError, RuntimeError):
            # ponytail: IndexPriceUpdate has no Arrow deserializer in this
            # nautilus_trader version (NotImplementedError); some instruments'
            # files also have conflicting embedded schema metadata across
            # flush batches, e.g. observed for ETH mark_price_update
            # ("price_precision" 6 vs 5") -> RuntimeError from the Arrow
            # reader. Either way, this is a catalog/environmental issue, not
            # something fixable here — skip the data type rather than crash.
            continue
        if not rows:
            continue

        ts = sorted(row.ts_event for row in rows)
        result[data_type] = {
            "count": len(ts),
            "start": pd.Timestamp(ts[0], unit="ns", tz="UTC"),
            "end": pd.Timestamp(ts[-1], unit="ns", tz="UTC"),
            "duration_seconds": (ts[-1] - ts[0]) / 1e9,
            "gaps": find_gaps(ts),
        }
    return result


def price_series(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    start_ns: int | None = None,
) -> list[tuple[int, float]]:
    """(ts_event, price) pairs, preferring trade ticks and falling back to bar closes."""
    trades = catalog.trade_ticks(instrument_ids=[instrument_id], start=start_ns)
    if trades:
        return sorted((t.ts_event, t.price.as_double()) for t in trades)

    bars = catalog.bars(instrument_ids=[instrument_id], start=start_ns)
    if bars:
        return sorted((b.ts_event, b.close.as_double()) for b in bars)

    return []


def price_stats(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    start_ns: int | None = None,
) -> dict:
    """
    Latest price, pct change over the last 1h/24h, and return volatility (stdev).

    `pct_change_1h`/`pct_change_24h` are None when the catalog doesn't yet span
    that long — no extrapolation from partial history.
    """
    series = price_series(catalog, instrument_id, start_ns=start_ns)
    if not series:
        return {"price": None, "pct_change_1h": None, "pct_change_24h": None, "volatility": None}

    ts = np.array([t for t, _ in series])
    px = np.array([p for _, p in series])
    latest_ts, latest_px = ts[-1], px[-1]

    def _pct_change(hours: float) -> float | None:
        cutoff = latest_ts - int(hours * 3_600 * 1e9)
        if ts[0] > cutoff:
            return None  # not enough history collected yet
        base_px = px[np.searchsorted(ts, cutoff)]
        return float((latest_px - base_px) / base_px * 100.0)

    returns = np.diff(px) / px[:-1]
    volatility = float(np.std(returns)) if len(returns) > 1 else None

    return {
        "price": float(latest_px),
        "pct_change_1h": _pct_change(1),
        "pct_change_24h": _pct_change(24),
        "volatility": volatility,
    }


def overview_table(catalog_path: str) -> list[dict]:
    """One row per known instrument with price/volatility/pct-change stats."""
    catalog = ParquetDataCatalog(catalog_path)
    rows = []
    # ponytail: recomputed from scratch on every call, ~300 catalog lookups for
    # instruments with no price data at all. Fine for a personal dashboard;
    # add a TTL cache if the overview page becomes measurably slow.
    for instrument_id in list_instruments(catalog_path):
        rows.append({"instrument_id": instrument_id, **price_stats(catalog, instrument_id)})
    return rows
