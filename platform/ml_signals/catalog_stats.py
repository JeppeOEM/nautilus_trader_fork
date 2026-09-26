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
Catalog analytics: per-instrument data coverage, gap detection, price/volatility stats.

Split across contexts (spine AD-D1): the price series and stats are ranking's (`list_instruments`
too, for `metrics_computer`). The coverage/gap helpers (`find_gaps`, `likely_outages`, `coverage`)
moved to `archive.application.diagnostics` in Story 25.1 and are served here, deprecated, from
`_MOVED_NAMES`. The one views read that lived here, `query_second_snapshots`, moved to
`views.catalog_reads` in Story 24.2 (the old name's forwarding ended in Story 24.4);
`overview_table` was deleted (see `_REPLACED_NAMES`).
"""

import glob
import importlib
import os
import warnings
from pathlib import Path

import numpy as np
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.persistence.catalog import ParquetDataCatalog


# Replaced names changed shape or were retired and raise, naming their successor: `_stamp_to_ns`
# (Story 23.2: a whole file stem -> a span) and `overview_table` (Story 24.2: it recomputed the
# pct-change/volatility math only the ranking context may compute -- AD-D10 -- and had no caller
# since Story 15.10 retired the dashboard; the same per-instrument stats are ranking's published
# output).
_REPLACED_NAMES: dict[str, str] = {
    "_stamp_to_ns": "kernel.clocks.CatalogFileSpan.from_stem(stem)",
    "overview_table": (
        "the rankings:live payload (GET /api/rankings) or metrics.db "
        "(ranking_engine.metrics_store.latest(db_path))"
    ),
}


# Moved names (Story 25.1): served from their new home with a DeprecationWarning until then.
MOVED_NAMES_REMOVE_AFTER = "25-3-bots-context-paper-and-exec-types-nautilus-acl"
_MOVED_NAMES: dict[str, str] = {
    "find_gaps": "archive.application.diagnostics.find_gaps",
    "likely_outages": "archive.application.diagnostics.likely_outages",
    "coverage": "archive.application.diagnostics.coverage",
}


def __getattr__(name: str) -> object:
    if name in _MOVED_NAMES:
        warnings.warn(
            f"ml_signals.catalog_stats.{name} moved to {_MOVED_NAMES[name]} (Story 25.1); "
            f"it is served here until {MOVED_NAMES_REMOVE_AFTER}",
            DeprecationWarning,
            stacklevel=2,
        )
        # A literal module name, so `platform/tests/test_images.py` follows it into the image check.
        return getattr(importlib.import_module("archive.application.diagnostics"), name)
    if name in _REPLACED_NAMES:
        raise AttributeError(
            f"ml_signals.catalog_stats.{name} was replaced by {_REPLACED_NAMES[name]}"
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
