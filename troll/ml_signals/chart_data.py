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
Per-event book feature computation for the chart page.

Replays OrderBookDelta from the catalog for a time range, computing OFI,
book imbalance, and depth at every single event. The result is a dict of
named series ready for Lightweight Charts.

Performance note: replaying a large delta range (full day) takes seconds.
The chart page defaults to 4 hours. Let the user expand via the time pickers.
"""

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from ml_signals.book_features import compute_features
from ml_signals.indicators import Microprice
from ml_signals.indicators import OrderFlowImbalance


def compute_chart_series(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    ofi_window: int = 20,
) -> dict[str, list[dict]]:
    """
    Replay book deltas for the time window and return per-event series.

    Returns a dict keyed by series name, each value a list of
    {"time": <unix_seconds_float>, "value": <float>} dicts. The price pane
    itself is rendered client-side (candlestick/line/tick widget, see
    dashboard._render_chart_page) from /data/coin/{id}/candles|ticks, not from
    this series.
    """
    catalog = ParquetDataCatalog(catalog_path)
    iid = InstrumentId.from_str(instrument_id)

    deltas = catalog.order_book_deltas(instrument_ids=[instrument_id], start=start_ns, end=end_ns)

    if not deltas:
        return {
            "ofi": [], "microprice": [], "spread": [],
            "imbalance": [], "mid_imbalance": [], "bid_depth": [], "ask_depth": [],
        }

    # Replay deltas — compute features at every event
    book  = OrderBook(iid, BookType.L2_MBP)
    ofi   = OrderFlowImbalance(window=ofi_window)
    micro = Microprice()

    series: dict[str, list[dict]] = {
        "ofi": [], "microprice": [], "spread": [],
        "imbalance": [], "mid_imbalance": [], "bid_depth": [], "ask_depth": [],
    }

    for delta in sorted(deltas, key=lambda d: d.ts_init):
        t = delta.ts_event / 1e9
        book.apply_delta(delta)

        bid_price = book.best_bid_price()
        ask_price = book.best_ask_price()
        if bid_price is None or ask_price is None:
            continue

        bid_p = bid_price.as_double()
        bid_s = book.best_bid_size().as_double()
        ask_p = ask_price.as_double()
        ask_s = book.best_ask_size().as_double()

        ofi.update_raw(bid_p, bid_s, ask_p, ask_s)
        micro.update_raw(bid_p, bid_s, ask_p, ask_s)
        features = compute_features(book)

        if ofi.initialized:
            series["ofi"].append({"time": t, "value": ofi.value})
        if micro.initialized:
            series["microprice"].append({"time": t, "value": micro.value})

        series["spread"].append({"time": t, "value": ask_p - bid_p})

        if features is not None:
            series["imbalance"].append({"time": t, "value": features.imbalance.aggregate})
            series["bid_depth"].append({"time": t, "value": features.depth.total_bid_depth()})
            series["ask_depth"].append({"time": t, "value": features.depth.total_ask_depth()})
            # mid-layer: average of levels 2-3
            if features.depth.levels >= 3:
                mid = (features.imbalance.per_level[1] + features.imbalance.per_level[2]) / 2
                series["mid_imbalance"].append({"time": t, "value": mid})

    return series
