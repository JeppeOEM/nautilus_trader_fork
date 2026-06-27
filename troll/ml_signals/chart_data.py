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
book imbalance, depth, and cancel pressure at every single event. The result
is a dict of named series ready for Lightweight Charts.

Performance note: replaying a large delta range (full day) takes seconds.
The chart page defaults to 4 hours. Let the user expand via the time pickers.
"""

from collections import deque

from nautilus_trader.indicators import ExponentialMovingAverage
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from dydx_collector.minute_bars import DydxMinuteBar
from ml_signals.book_features import CancellationTracker
from ml_signals.book_features import compute_features
from ml_signals.indicators import Microprice
from ml_signals.indicators import OrderFlowImbalance


_CUM_DELTA_SECONDS = 300  # 5 min


def compute_chart_series(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
    ofi_window: int = 20,
    trend_ema_fast: int = 8,
    trend_ema_slow: int = 21,
) -> dict[str, list[dict]]:
    """
    Replay book deltas for the time window and return per-event series.

    Returns a dict keyed by series name, each value a list of
    {"time": <unix_seconds_float>, "value": <float>} dicts for
    Lightweight Charts line series, plus "candles" for the price pane.
    """
    catalog = ParquetDataCatalog(catalog_path)
    iid = InstrumentId.from_str(instrument_id)

    # Extend start slightly for trend EMA warmup on bars (load extra history)
    bars_start_ns = start_ns - trend_ema_slow * 30 * 60 * 1_000_000_000

    deltas = catalog.order_book_deltas(instrument_ids=[instrument_id], start=start_ns, end=end_ns)
    trades = catalog.trade_ticks(instrument_ids=[instrument_id], start=start_ns, end=end_ns)
    # DydxMinuteBar is the primary candle source (computed by the collector from trades).
    # Fall back to standard bars for backward compat with old catalog data.
    minute_bars = catalog.query(DydxMinuteBar, identifiers=[instrument_id], start=bars_start_ns, end=end_ns)
    if not minute_bars:
        minute_bars = catalog.bars(instrument_ids=[instrument_id], start=bars_start_ns, end=end_ns)

    candles: list[dict] = []
    trade_line: list[dict] = []
    trend_fast = ExponentialMovingAverage(trend_ema_fast)
    trend_slow = ExponentialMovingAverage(trend_ema_slow)
    trend_fast_series: list[dict] = []
    trend_slow_series: list[dict] = []

    for bar in sorted(minute_bars, key=lambda b: b.ts_event):
        close = bar.close if isinstance(bar, DydxMinuteBar) else bar.close.as_double()
        open_ = bar.open if isinstance(bar, DydxMinuteBar) else bar.open.as_double()
        high  = bar.high if isinstance(bar, DydxMinuteBar) else bar.high.as_double()
        low   = bar.low  if isinstance(bar, DydxMinuteBar) else bar.low.as_double()
        trend_fast.update_raw(close)
        trend_slow.update_raw(close)
        if bar.ts_event >= start_ns:
            t = bar.ts_event / 1e9
            candles.append({"time": t, "open": open_, "high": high, "low": low, "close": close})
            if trend_fast.initialized:
                trend_fast_series.append({"time": t, "value": trend_fast.value})
            if trend_slow.initialized:
                trend_slow_series.append({"time": t, "value": trend_slow.value})

    for tick in sorted(trades, key=lambda t: t.ts_event):
        trade_line.append({"time": tick.ts_event / 1e9, "value": tick.price.as_double()})

    # 5-min rolling cumulative delta from trade ticks
    cum_delta_series: list[dict] = []
    window_ns = _CUM_DELTA_SECONDS * 1_000_000_000
    cum_buf: deque[tuple[int, float]] = deque()
    for tick in sorted(trades, key=lambda t: t.ts_event):
        signed = tick.size.as_double()
        if tick.aggressor_side == AggressorSide.SELLER:
            signed = -signed
        cum_buf.append((tick.ts_event, signed))
        cutoff = tick.ts_event - window_ns
        while cum_buf and cum_buf[0][0] < cutoff:
            cum_buf.popleft()
        cum_delta_series.append({
            "time": tick.ts_event / 1e9,
            "value": sum(sz for _, sz in cum_buf),
        })

    if not deltas:
        return {
            "candles": candles, "trades": trade_line,
            "ofi": [], "microprice": [], "spread": [],
            "imbalance": [], "mid_imbalance": [], "bid_depth": [], "ask_depth": [],
            "bid_cancel": [], "ask_cancel": [],
            "cum_delta": cum_delta_series,
            "trend_fast": trend_fast_series, "trend_slow": trend_slow_series,
        }

    # Replay deltas — compute features at every event
    book  = OrderBook(iid, BookType.L2_MBP)
    ofi   = OrderFlowImbalance(window=ofi_window)
    micro = Microprice()
    cancel = CancellationTracker(window=ofi_window * 5)

    series: dict[str, list[dict]] = {
        "ofi": [], "microprice": [], "spread": [],
        "imbalance": [], "mid_imbalance": [], "bid_depth": [], "ask_depth": [],
        "bid_cancel": [], "ask_cancel": [],
    }

    for delta in sorted(deltas, key=lambda d: d.ts_init):
        t = delta.ts_event / 1e9

        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        cancel.update(
            delta,
            best_bid.as_double() if best_bid else None,
            best_ask.as_double() if best_ask else None,
        )
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
        features = compute_features(book, cancel)

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

        cr = cancel.rate()
        series["bid_cancel"].append({"time": t, "value": cr.bid_pressure})
        series["ask_cancel"].append({"time": t, "value": cr.ask_pressure})

    series["candles"]     = candles
    series["trades"]      = trade_line
    series["cum_delta"]   = cum_delta_series
    series["trend_fast"]  = trend_fast_series
    series["trend_slow"]  = trend_slow_series
    return series
