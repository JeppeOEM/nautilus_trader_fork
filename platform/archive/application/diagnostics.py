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
Catalog diagnostics: per-instrument data coverage, spacing gaps and likely outages (the archive
half of the former `ml_signals.catalog_stats`, moved in Story 25.1; its price series and stats
stay ranking's). Ledger sites keep their `catalog_stats.*` names (published language).

Known limit: `_load` reads a whole data type of one instrument unbounded in time (MEM-01's
anti-pattern), as the original did; these are notebook/operator diagnostics over a small catalog,
never a nightly step. Upgrade path: bound each read to a window and page through it.
"""

import numpy as np
import pandas as pd
from observability import error_ledger

from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.persistence.catalog import ParquetDataCatalog


# The catalog data types `coverage` reports on (the list `ml_signals.catalog_stats` kept until
# Story 25.2).
DATA_TYPES = (
    "trade_tick",
    "bar",
    "order_book_deltas",
    "mark_price_update",
    "index_price_update",
    "funding_rate_update",
    "instrument_status",
)


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

    Known limit: adaptive heuristic (median inter-arrival time * `min_multiple`,
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
    except (NotImplementedError, RuntimeError) as exc:
        error_ledger.record(
            "catalog_stats.likely_outages", f"{instrument_id} mark_price_update unreadable", exc
        )
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
        except (NotImplementedError, RuntimeError) as exc:
            # IndexPriceUpdate has no Arrow deserializer in this nautilus_trader version
            # (NotImplementedError); conflicting embedded schema metadata across flush batches
            # (e.g. ETH mark_price_update "price_precision" 6 vs 5) raises RuntimeError. Both
            # leave this data type out of the result -- recorded, never silent (DATA-07).
            error_ledger.record(
                "catalog_stats.coverage", f"{instrument_id} {data_type} unreadable", exc
            )
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
