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
Per-snapshot book feature computation for the chart page.

Reads DydxSecondSnapshot records from the catalog for a time range, computing
book imbalance and depth at every 1-second snapshot. The result is a dict of
named series ready for Plotly (see dashboard._render_chart_page).

Snapshot-based, not raw-delta-based (troll/CLAUDE.md's "Signal Architecture:
1s-Based, Not Event-Driven" / SIGNAL-01): OrderBookDeltas are only persisted
per-instrument when dydx_collector's store_order_book_deltas is opted in
(default off), so replaying raw deltas here would silently return empty
series for every instrument in the live catalog.
"""

from dydx_collector.second_snapshot import DydxSecondSnapshot

from ml_signals.book_features import DepthProfile
from ml_signals.book_features import book_imbalance
from ml_signals.indicators import microprice as calc_microprice
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_LEVELS = 10


def compute_chart_series(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
) -> dict[str, list[dict]]:
    """
    Read 1s book snapshots for the time window and return per-snapshot series.

    Returns a dict keyed by series name, each value a list of
    {"time": <unix_seconds_float>, "value": <float>} dicts. The price pane
    itself is rendered client-side (candlestick/line/tick widget, see
    dashboard._render_chart_page) from /data/coin/{id}/candles|ticks, not from
    this series.
    """
    catalog = ParquetDataCatalog(catalog_path)
    results = catalog.query(
        data_cls=DydxSecondSnapshot,
        identifiers=[instrument_id],
        start=start_ns,
        end=end_ns,
    )
    # query() wraps custom Data subclasses in CustomData -- unwrap via .data (same
    # pattern as dashboard._historical_lines_json).
    snapshots = [r.data if hasattr(r, "data") else r for r in results]

    series: dict[str, list[dict]] = {
        "microprice": [],
        "spread": [],
        "imbalance": [],
        "mid_imbalance": [],
        "bid_depth": [],
        "ask_depth": [],
    }

    for s in sorted(snapshots, key=lambda s: s.ts_event):
        if not s.bid_prices or not s.ask_prices:
            continue
        bid_p, ask_p = s.bid_prices[0], s.ask_prices[0]
        if bid_p >= ask_p:  # crossed/touched snapshot — skip (DATA-04)
            continue
        t = s.ts_event / 1e9

        micro_value = calc_microprice(
            {
                "bid_prices": s.bid_prices,
                "bid_sizes": s.bid_sizes,
                "ask_prices": s.ask_prices,
                "ask_sizes": s.ask_sizes,
            }
        )
        if micro_value is not None:
            series["microprice"].append({"time": t, "value": micro_value})

        series["spread"].append({"time": t, "value": ask_p - bid_p})

        profile = DepthProfile(
            bid_prices=s.bid_prices[:_LEVELS],
            bid_sizes=s.bid_sizes[:_LEVELS],
            ask_prices=s.ask_prices[:_LEVELS],
            ask_sizes=s.ask_sizes[:_LEVELS],
        )
        imbalance = book_imbalance(profile)
        series["imbalance"].append({"time": t, "value": imbalance.aggregate})
        series["bid_depth"].append({"time": t, "value": profile.total_bid_depth()})
        series["ask_depth"].append({"time": t, "value": profile.total_ask_depth()})
        # mid-layer: average of levels 2-3. per_level is zip(bid_sizes, ask_sizes) --
        # its length is min(bid, ask) level count, which can differ from profile.levels
        # (bid count alone) on a thin/illiquid side, so guard on per_level itself.
        if len(imbalance.per_level) >= 3:
            mid = (imbalance.per_level[1] + imbalance.per_level[2]) / 2
            series["mid_imbalance"].append({"time": t, "value": mid})

    return series
