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
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from collector_core.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

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


def query_second_snapshots(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
) -> list[DydxSecondSnapshot]:
    """
    DydxSecondSnapshot rows for `instrument_id` in [start_ns, end_ns], CustomData-unwrapped.

    Shared by dashboard.py's _historical_lines_json and custom_indicators.py's
    _second_snapshots -- both projected different fields off this same query, so only
    the catalog-query + CustomData-unwrap boilerplate lives here.
    """
    catalog = ParquetDataCatalog(catalog_path)
    # `query` bounds on ts_init, the window is ts_event: a venue-timed row (story 22.12) is
    # sampled up to 1 + hold_back s (a catch-up: more) after its ts_event, so the end is widened
    # and the exact ts_event filter decides. ts_init >= ts_event, so the start needs no margin.
    results = catalog.query(
        data_cls=DydxSecondSnapshot,
        identifiers=[instrument_id],
        start=start_ns,
        end=end_ns + _FILE_MARGIN_NS,
    )
    # query() wraps custom Data subclasses in CustomData -- unwrap via .data to reach the
    # actual DydxSecondSnapshot (confirmed via direct introspection this session).
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    return [s for s in snapshots if start_ns <= s.ts_event <= end_ns]


class SecondOHLC(NamedTuple):
    """The per-second fields candle aggregation needs (duck-types `DydxSecondSnapshot` there)."""

    ts_event: int
    open_price: float | None
    high_price: float | None
    low_price: float | None
    close_price: float | None
    buy_volume: float
    sell_volume: float


_OHLC_COLUMNS = [
    "ts_event",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "buy_volume",
    "sell_volume",
]
# Catalog filenames span ts_init, rows are filtered on ts_event; ts_init trails ts_event by well
# under this, so files this close to the window are read and the exact ts_event filter decides.
_FILE_MARGIN_NS = 60_000_000_000


def query_second_ohlc(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[SecondOHLC]:
    """
    Per-second OHLC + volume rows in [start_ns, end_ns] (ts_event), read straight from the
    catalog's Parquet files with only the seven columns candles need.

    `query_second_snapshots` deserialises every row's 20-level book into Python objects through
    the catalog decoder -- ~95% of a candle request's time (Story 21.5 profile) for fields
    candles never look at. Same files, same rows, same values; just a column projection.
    """
    pattern = os.path.join(
        catalog_path, "data", "custom_dydx_second_snapshot", instrument_id, "*.parquet"
    )
    rows: list[SecondOHLC] = []
    for path in glob.glob(pattern):
        start, _, end = Path(path).stem.partition("_")
        if (
            _stamp_to_ns(end) < start_ns - _FILE_MARGIN_NS
            or _stamp_to_ns(start) > end_ns + _FILE_MARGIN_NS
        ):
            continue
        # Files from before the OHLC fields existed lack those columns: they read as None (the
        # candle path skips a second with no close), never as a crash.
        names = pq.read_schema(path).names
        present = [c for c in _OHLC_COLUMNS if c in names]
        table = pq.read_table(
            path,
            columns=present,
            filters=[("ts_event", ">=", start_ns), ("ts_event", "<=", end_ns)],
        )
        cols = {c: table.column(c).to_pylist() for c in present}
        n = table.num_rows
        rows.extend(
            SecondOHLC(
                *(
                    cols[c][i] if c in cols else (0.0 if c.endswith("volume") else None)
                    for c in _OHLC_COLUMNS
                )
            )
            for i in range(n)
        )
    rows.sort(key=lambda r: r.ts_event)
    return rows


def second_ohlc_arrays(paths: list[str]) -> dict[str, np.ndarray]:
    """
    Read OHLC + volume columns of the given snapshot files as arrays.

    Keys `ts_ms`, `o`, `h`, `l`, `c`, `v` (NaN = no trade that second); each file opened once. The
    rebuild's read path: no per-row Python objects, no per-call directory scan.
    """
    tables = []
    for path in paths:
        pf = pq.ParquetFile(path)
        present = [c for c in _OHLC_COLUMNS if c in pf.schema_arrow.names]
        tables.append(pf.read(columns=present))
    out = {k: np.empty(0, dtype=np.float64) for k in ("o", "h", "l", "c", "v")}
    out["ts_ms"] = np.empty(0, dtype=np.int64)
    if not tables:
        return out
    n = sum(t.num_rows for t in tables)
    ts = np.concatenate([t.column("ts_event").to_numpy() for t in tables]).astype(np.int64)
    out["ts_ms"] = ts // 1_000_000

    def col(name: str, default: float) -> np.ndarray:
        parts = [
            t.column(name).to_numpy(zero_copy_only=False).astype(np.float64)
            if name in t.column_names
            else np.full(t.num_rows, default)
            for t in tables
        ]
        return np.concatenate(parts) if parts else np.empty(0)

    out["o"], out["h"], out["l"], out["c"] = (
        col(k, np.nan) for k in ("open_price", "high_price", "low_price", "close_price")
    )
    out["v"] = col("buy_volume", 0.0) + col("sell_volume", 0.0)
    assert len(out["ts_ms"]) == n
    return out


def _stamp_to_ns(stamp: str) -> int:
    """`2026-06-30T17-17-34-103475440Z` (a catalog filename bound) -> epoch ns."""
    date, _, clock = stamp.rstrip("Z").partition("T")
    hour, minute, second, nanos = clock.split("-")
    moment = datetime.strptime(f"{date} {hour}:{minute}:{second}", "%Y-%m-%d %H:%M:%S")
    return int(moment.replace(tzinfo=UTC).timestamp()) * 1_000_000_000 + int(nanos)


def data_file_ranges(catalog_path: str, instrument_id: str) -> list[tuple[int, int]]:
    """
    Return ascending (start_ns, end_ns) of every second-snapshot Parquet file for the instrument.

    Read from the catalog's filenames -- a directory listing, no Parquet I/O. Lets paging routes know
    where data actually exists instead of guessing with fixed-size probe windows (a data gap wider
    than the window otherwise reads as "no more history").
    """
    ranges = []
    for path in glob.glob(
        os.path.join(
            catalog_path, "data", "custom_dydx_second_snapshot", instrument_id, "*.parquet"
        )
    ):
        start, _, end = Path(path).stem.partition("_")
        ranges.append((_stamp_to_ns(start), _stamp_to_ns(end)))
    return sorted(ranges)


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


def price_series(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    start_ns: int | None = None,
) -> list[tuple[int, float]]:
    """
    (ts_event, price) pairs in ascending order. Preference: second-snapshot close → mark price.

    DydxSecondSnapshot.close_price is the per-second traded price every reader uses (raw
    TradeTicks are archived again since story 22.13, but only for 7 days after their day is
    verified -- platform/docs/DATA_DICTIONARY.md §1.1/§5). Seconds with no trade have
    close_price=None and are skipped, not treated as a zero-price tick.
    """
    results = catalog.query(DydxSecondSnapshot, identifiers=[instrument_id], start=start_ns)
    # query() wraps custom Data subclasses in CustomData -- unwrap via .data (same
    # pattern as chart_data.py's compute_chart_series).
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    trades = sorted((s.ts_event, s.close_price) for s in snapshots if s.close_price is not None)
    if trades:
        return trades

    # Fallback for instruments with no trades (illiquid/new): use mark price.
    # catalog.bars() is intentionally omitted — the collector never writes Bar objects.
    try:
        marks = catalog.query(MarkPriceUpdate, identifiers=[instrument_id], start=start_ns)
    except (NotImplementedError, RuntimeError) as exc:
        error_ledger.record(
            "catalog_stats.mark_prices", f"{instrument_id} mark_price_update unreadable", exc
        )
        marks = []
    if marks:
        return sorted((m.ts_event, m.value.as_double()) for m in marks)

    return []


def price_stats_from_series(series: list[tuple[int, float]]) -> dict:
    """
    Latest price, pct change over the last 1h/24h, and return volatility (stdev),
    computed from an already-fetched (ts_event, price) series.

    Extracted from price_stats() (Story 13.2) so ranking_engine's in-memory
    PriceSeriesStore can call this exact same formula against its own ring-buffer
    series, instead of a second, independently-written (and potentially drifting)
    implementation -- one formula, two callers (SSOT-02, DATA-02).

    `pct_change_1h`/`pct_change_24h` are None when the series doesn't yet span
    that long — no extrapolation from partial history.
    """
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


def price_stats(
    catalog: ParquetDataCatalog,
    instrument_id: str,
    start_ns: int | None = None,
) -> dict:
    """
    Latest price, pct change over the last 1h/24h, and return volatility (stdev).

    Thin wrapper: fetches the series then delegates the math to
    price_stats_from_series() -- see that function's docstring.
    """
    return price_stats_from_series(price_series(catalog, instrument_id, start_ns=start_ns))


def overview_table(catalog_path: str) -> list[dict]:
    """One row per known instrument with price/volatility/pct-change stats."""
    catalog = ParquetDataCatalog(catalog_path)
    rows = []
    # Known limit: recomputed from scratch on every call, ~300 catalog lookups for
    # instruments with no price data at all. Fine for a personal dashboard;
    # add a TTL cache if the overview page becomes measurably slow.
    for instrument_id in list_instruments(catalog_path):
        rows.append({"instrument_id": instrument_id, **price_stats(catalog, instrument_id)})
    return rows
