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
Lightweight local dashboard over the dYdX catalog and live strategy signals.

ponytail: a polling dashboard (stdlib http.server + plotly.to_html, no client
JS dependency, no caching), not push-based SSE/websockets, not a real web
framework. Swap for Streamlit/Dash if you need richer UX later.

Pages:
- `/`               rankings table — all coins sortable by any metric
- `/coin/{id}`      per-coin data coverage + footprint chart + OFI/Microprice panel
- `/history/{id}`   31-day metric history charts for one coin
- `/live`           in-process live signal monitor (see `record()` below)

Usage from a running strategy::

    from ml_signals.dashboard import record, serve_in_background

    serve_in_background(port=8765)  # once, e.g. in on_start


    def on_signal(self, signal):
        record("logistic_trend", signal.value, signal.ts_event)
"""

import html
import logging
import threading
import time
from collections import defaultdict
from collections import deque
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlparse

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from ml_signals.book_features import top_of_book_series
from ml_signals.candles import TIMEFRAMES
from ml_signals.candles import build_candles
from ml_signals.catalog_stats import coverage
from ml_signals.catalog_stats import likely_outages
from ml_signals.catalog_stats import list_instruments
from ml_signals.footprint import build_footprint
from ml_signals.indicators import Microprice
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI
from ml_signals.indicators import OrderFlowImbalance
from ml_signals import chart_data as _chart_data
from ml_signals import metrics_computer
from ml_signals import metrics_store
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

CATALOG_PATH = "troll/dydx_collector/catalog"
MAX_FOOTPRINT_CANDLES = 30
FOOTPRINT_BANDS_PER_CANDLE = 4

# How often the background thread recomputes live OFI/microprice from the catalog.
LIVE_INTERVAL_SECONDS: int = 5
# How often the live snapshot is flushed to SQLite for bookkeeping history.
DB_WRITE_INTERVAL_SECONDS: int = 60

# Rankings table columns. Each entry: (store_key, header_label, format_fn, color_fn|None).
# Reorder, add, or remove rows here to control what's shown and how.
# color_fn receives the raw float value and returns a CSS color string.
RANKING_COLS: list[tuple[str, str, object, object]] = [
    ("price",      "Price",      lambda v: f"{v:.4f}",   None),
    ("ofi",        "OFI",        lambda v: f"{v:+.2f}",  lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("microprice", "Microprice", lambda v: f"{v:.4f}",   None),
    ("spread",     "Spread",     lambda v: f"{v:.6f}",   None),
    ("pct_1h",     "1h %",       lambda v: f"{v:+.2f}%", lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("pct_24h",    "24h %",      lambda v: f"{v:+.2f}%", lambda v: "#2a9d2a" if v > 0 else "#c0392b"),
    ("volatility", "Volatility", lambda v: f"{v:.6f}",   None),
]

_LOCK = threading.Lock()
_SERIES: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=2000))

# In-memory live snapshot cache — updated every LIVE_INTERVAL_SECONDS.
# Rankings page reads from here instead of SQLite so it never blocks on a DB query.
_METRICS_LOCK = threading.Lock()
_LIVE: dict[str, dict] = {}  # instrument_id → latest snapshot

_NAV = '<p><a href="/">Rankings</a> | <a href="/live">Live signals</a></p>'


def _split_tiers(catalog_path: str) -> tuple[set[str], set[str]]:
    """
    Return (subscribed_iids, illiquid_iids) by checking trade_tick directory presence.

    Subscribed = has trade_tick data (collector is writing trades for this coin).
    Illiquid   = appears in catalog (via mark/index price) but no trade data.
    """
    import glob
    import os as _os
    subscribed: set[str] = set()
    for path in glob.glob(_os.path.join(catalog_path, "data", "trade_tick", "*")):
        subscribed.add(Path(path).name)
    all_iids = set(list_instruments(catalog_path))
    return subscribed, all_iids - subscribed

_CSS = """
<style>
body { font-family: monospace; font-size: 13px; margin: 20px; background: #0d1117; color: #c9d1d9; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 6px 12px; text-align: right; border-bottom: 1px solid #21262d; }
th { background: #161b22; position: sticky; top: 0; }
th a { color: #c9d1d9; }
tr:hover td { background: #161b22; }
td:first-child, th:first-child { text-align: left; }
.sort-active { color: #f0883e; }
</style>
"""


def record(name: str, value: float, ts_event: int) -> None:
    with _LOCK:
        _SERIES[name].append((ts_event, value))


def _page(title: str, body: str, refresh_seconds: int = 60) -> str:
    return (
        f"<html><head><title>{html.escape(title)}</title>"
        f'<meta http-equiv="refresh" content="{refresh_seconds}">'
        f"{_CSS}</head>"
        f"<body>{_NAV}{body}</body></html>"
    )


def _render_rankings_page(sort_col: str = "ofi", direction: str = "desc") -> str:
    with _METRICS_LOCK:
        rows = list(_LIVE.values())
    if not rows:  # fallback at startup before first compute cycle finishes
        db_path = str(Path(CATALOG_PATH).parent / "metrics.db")
        rows = metrics_store.latest(db_path)

    subscribed_iids, illiquid_iids = _split_tiers(CATALOG_PATH)
    subscribed_rows = [r for r in rows if r["instrument_id"] in subscribed_iids]

    valid_cols = {k for k, *_ in RANKING_COLS}
    if sort_col not in valid_cols:
        sort_col = "ofi"
    reverse = direction != "asc"
    subscribed_rows.sort(
        key=lambda r: (r.get(sort_col) is None, r.get(sort_col) or 0.0),
        reverse=reverse,
    )

    def _sort_link(col: str, label: str) -> str:
        new_dir = "asc" if (col == sort_col and direction == "desc") else "desc"
        active = ' class="sort-active"' if col == sort_col else ""
        arrow = (" ↓" if direction == "desc" else " ↑") if col == sort_col else ""
        return f'<a href="/?sort={col}&dir={new_dir}"{active}>{html.escape(label)}{arrow}</a>'

    header = (
        "<tr><th>#</th>"
        f"<th>{_sort_link('instrument_id', 'Instrument')}</th>"
        + "".join(f"<th>{_sort_link(k, label)}</th>" for k, label, *_ in RANKING_COLS)
        + "<th>History</th></tr>"
    )

    def _cell(value: float | None, fmt_fn: object, color_fn: object) -> str:
        if value is None:
            return "<td>&mdash;</td>"
        text = html.escape(fmt_fn(value))  # type: ignore[operator]
        color = color_fn(value) if color_fn else None  # type: ignore[operator]
        style = f' style="color:{color}"' if color else ""
        return f"<td{style}>{text}</td>"

    body_rows = ""
    for i, row in enumerate(subscribed_rows, 1):
        iid = html.escape(row["instrument_id"])
        # Short ticker label: "ETH-USD-PERP.DYDX" → "ETH-USD"
        label = html.escape("-".join(row["instrument_id"].split("-")[:2]))
        cells = "".join(_cell(row.get(k), fmt_fn, color_fn) for k, _, fmt_fn, color_fn in RANKING_COLS)
        body_rows += (
            f"<tr><td>{i}</td>"
            f"<td><a href='/chart/{iid}'>{label}</a> <small><a href='/coin/{iid}'>cov</a></small></td>"
            f"{cells}"
            f"<td><a href='/history/{iid}'>31d</a></td></tr>"
        )

    if not subscribed_rows:
        body_rows = f"<tr><td colspan='{2 + len(RANKING_COLS) + 1}'>No snapshots yet — first compute in progress (runs every {LIVE_INTERVAL_SECONDS}s).</td></tr>"

    subscribed_table = f"<table>{header}{body_rows}</table>"

    # Illiquid: compact clickable chips — only mark/index/funding data, no book metrics
    chips = "".join(
        f'<a href="/chart/{html.escape(iid)}" title="{html.escape(iid)}" '
        f'style="display:inline-block;margin:3px;padding:2px 8px;'
        f'background:#161b22;border:1px solid #30363d;border-radius:4px">'
        f'{html.escape("-".join(iid.split("-")[:2]))}</a>'
        for iid in sorted(illiquid_iids)
    )
    illiquid_section = (
        f"<h2>Illiquid — monitoring ({len(illiquid_iids)})</h2>"
        f"<p style='font-size:11px;color:#8b949e'>OI below threshold — not subscribed to "
        f"trades/orderbook. Re-checked every 30 min. Click to see mark price data.</p>"
        f"<div style='line-height:2.4'>{chips}</div>"
    )

    body = (
        f"<h1>dYdX Monitor</h1>"
        f"<h2>Subscribed ({len(subscribed_rows)})</h2>"
        f"{subscribed_table}"
        f"{illiquid_section}"
    )
    return _page("dYdX Monitor", body, refresh_seconds=5)


def _render_history_page(symbol: str) -> str:
    db_path = str(Path(CATALOG_PATH).parent / "metrics.db")
    rows = metrics_store.history(symbol, db_path, days=31)

    if not rows:
        return _page(symbol, f"<h1>{html.escape(symbol)}</h1><p>No history yet.</p>")

    ts = [pd.Timestamp(r["ts"], unit="ns", tz="UTC") for r in rows]
    body = f"<h1>{html.escape(symbol)} — 31-day history</h1>"
    plotlyjs = "cdn"

    for field, label, *_ in RANKING_COLS:
        vals = [r.get(field) for r in rows]
        if all(v is None for v in vals):
            continue
        fig = go.Figure(go.Scatter(x=ts, y=vals, mode="lines", connectgaps=False))
        fig.update_layout(title=label, height=250, margin={"t": 40, "b": 20})
        body += fig.to_html(full_html=False, include_plotlyjs=plotlyjs)
        plotlyjs = False  # type: ignore[assignment]

    return _page(f"{symbol} history", body, refresh_seconds=30)


def _render_chart_page(symbol: str, start_ms: int, end_ms: int) -> str:
    """Per-event microstructure chart — Plotly subplots with shared x-axis."""
    data = _chart_data.compute_chart_series(
        CATALOG_PATH, symbol,
        start_ns=start_ms * 1_000_000,
        end_ns=end_ms * 1_000_000,
    )

    def ts(series: list[dict]) -> list:
        return pd.to_datetime([p["time"] for p in series], unit="s", utc=True)

    def vals(series: list[dict]) -> list:
        return [p["value"] for p in series]

    fig = make_subplots(
        rows=8, cols=1, shared_xaxes=True,
        row_heights=[0.22, 0.10, 0.10, 0.10, 0.12, 0.12, 0.12, 0.12],
        vertical_spacing=0.015,
        subplot_titles=[
            "Price + 30m EMA trend", "OFI",
            "Book imbalance L1 agg (4-level)", "Mid-layer imbalance (L2-3)",
            "Depth (4-level)", "Cancel pressure",
            "5-min cumulative delta", "Spread",
        ],
    )

    def _add(series_key: str, row: int, name: str, color: str) -> None:
        if data.get(series_key):
            fig.add_trace(go.Scattergl(
                x=ts(data[series_key]), y=vals(data[series_key]),
                mode="lines", name=name, line_color=color, line_width=1,
            ), row=row, col=1)

    # Row 1: price + trend EMAs overlaid
    if data["candles"]:
        c = data["candles"]
        fig.add_trace(go.Candlestick(
            x=pd.to_datetime([p["time"] for p in c], unit="s", utc=True),
            open=[p["open"] for p in c], high=[p["high"] for p in c],
            low=[p["low"] for p in c], close=[p["close"] for p in c],
            name="price", increasing_line_color="#26a69a", decreasing_line_color="#ef5350",
        ), row=1, col=1)
    elif data["trades"]:
        _add("trades", 1, "price", "#2962ff")
    _add("trend_fast", 1, f"EMA{8}",  "#ffd700")
    _add("trend_slow", 1, f"EMA{21}", "#ff8c00")

    # Row 2: OFI
    _add("ofi", 2, "OFI", "#f0883e")
    if data.get("ofi"):
        fig.add_hline(y=0, line_color="#555", line_width=1, row=2, col=1)

    # Row 3: 4-level aggregate imbalance
    _add("imbalance", 3, "imbalance", "#ab71ff")
    if data.get("imbalance"):
        fig.add_hline(y=0.5, line_color="#555", line_width=1, row=3, col=1)

    # Row 4: mid-layer imbalance (levels 2-3)
    _add("mid_imbalance", 4, "mid imbalance", "#c792ea")
    if data.get("mid_imbalance"):
        fig.add_hline(y=0.5, line_color="#555", line_width=1, row=4, col=1)

    # Row 5: depth
    _add("bid_depth", 5, "bid depth", "#26a69a")
    _add("ask_depth", 5, "ask depth", "#ef5350")

    # Row 6: cancel pressure
    _add("bid_cancel", 6, "bid cancel", "#26a69a")
    _add("ask_cancel", 6, "ask cancel", "#ef5350")

    # Row 7: 5-min cumulative delta
    _add("cum_delta", 7, "cum delta", "#64b5f6")
    if data.get("cum_delta"):
        fig.add_hline(y=0, line_color="#555", line_width=1, row=7, col=1)

    # Row 8: spread
    _add("spread", 8, "spread", "#78909c")

    n = len(data.get("ofi", []))
    fig.update_layout(
        height=1400, template="plotly_dark",
        title=f"{symbol} — {n:,} delta events",
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend={"orientation": "h", "y": 1.01},
    )
    # Datetime pickers above the chart — submits as GET params
    sym = html.escape(symbol)
    import datetime as _dt
    fmt = "%Y-%m-%dT%H:%M"
    start_val = _dt.datetime.fromtimestamp(start_ms / 1000).strftime(fmt)
    end_val   = _dt.datetime.fromtimestamp(end_ms   / 1000).strftime(fmt)
    form = (
        f"<form method='get' style='margin:8px 0'>"
        f"From <input type='datetime-local' name='start' value='{start_val}'> &nbsp;"
        f"To <input type='datetime-local' name='end' value='{end_val}'> &nbsp;"
        f"<button type='submit'>Load</button>"
        f"</form>"
    )
    body = form + fig.to_html(full_html=False, include_plotlyjs="cdn")
    return _page(f"{sym} chart", body, refresh_seconds=86400)  # no auto-refresh; user controls via form


def _fmt_intervals(intervals: list[tuple[int, int]], limit: int = 5) -> str:
    if not intervals:
        return "none"
    shown = ", ".join(f"{a}&rarr;{b}" for a, b in intervals[:limit])
    extra = f" (+{len(intervals) - limit} more)" if len(intervals) > limit else ""
    return shown + extra


def _render_footprint_chart(candles: list, cells: list, period_seconds: int) -> go.Figure:
    period_ns = period_seconds * 1_000_000_000
    cells_by_candle: dict[int, list] = defaultdict(list)
    for cell in cells:
        cells_by_candle[cell.ts_open].append(cell)

    fig = go.Figure()
    for candle in candles:
        x0 = pd.Timestamp(candle.ts_open, unit="ns")
        x1 = pd.Timestamp(candle.ts_open + period_ns, unit="ns")
        x_mid = pd.Timestamp(candle.ts_open + period_ns // 2, unit="ns")
        tick_width = (x1 - x0) * 0.3

        for cell in cells_by_candle.get(candle.ts_open, []):
            fillcolor = (
                "rgba(0,150,0,0.12)" if cell.bid_net >= cell.ask_net else "rgba(200,0,0,0.12)"
            )
            fig.add_shape(
                type="rect",
                x0=x0,
                x1=x1,
                y0=cell.price_low,
                y1=cell.price_high,
                line={"width": 0.5, "color": "lightgray"},
                fillcolor=fillcolor,
                layer="below",
            )
            y_mid = (cell.price_low + cell.price_high) / 2
            fig.add_annotation(
                x=x0,
                y=y_mid,
                xanchor="left",
                yanchor="middle",
                text=f"+{cell.bid_added:.0f}<br>-{cell.bid_removed:.0f}<br>={cell.bid_net:+.0f}",
                showarrow=False,
                align="left",
                font={"size": 9, "color": "green" if cell.bid_net >= 0 else "red"},
            )
            fig.add_annotation(
                x=x1,
                y=y_mid,
                xanchor="right",
                yanchor="middle",
                text=f"+{cell.ask_added:.0f}<br>-{cell.ask_removed:.0f}<br>={cell.ask_net:+.0f}",
                showarrow=False,
                align="right",
                font={"size": 9, "color": "green" if cell.ask_net >= 0 else "red"},
            )

        # Thin OHLC representation behind the footprint cells (no filled body,
        # so it doesn't hide the cells drawn on top of it).
        fig.add_shape(
            type="line",
            x0=x_mid,
            x1=x_mid,
            y0=candle.low,
            y1=candle.high,
            line={"color": "black", "width": 1},
        )
        fig.add_shape(
            type="line",
            x0=x_mid - tick_width,
            x1=x_mid,
            y0=candle.open,
            y1=candle.open,
            line={"color": "black", "width": 1.5},
        )
        fig.add_shape(
            type="line",
            x0=x_mid,
            x1=x_mid + tick_width,
            y0=candle.close,
            y1=candle.close,
            line={"color": "black", "width": 1.5},
        )

    fig.update_layout(
        title="Footprint: bid (left) / ask (right) added, removed, net resting size per band",
        height=700,
    )
    return fig


def _render_coin_page(symbol: str, timeframe: str = "1m") -> str:
    catalog = ParquetDataCatalog(CATALOG_PATH)
    cov = coverage(catalog, symbol)

    coverage_rows = "".join(
        f"<tr><td>{html.escape(data_type)}</td><td>{info['count']}</td>"
        f"<td>{info['start']}</td><td>{info['end']}</td>"
        f"<td>{info['duration_seconds']:.1f}s</td>"
        f"<td>{_fmt_intervals(info['gaps'])}</td></tr>"
        for data_type, info in cov.items()
    )

    sym = html.escape(symbol)
    body = f"<h1>{sym} &nbsp;<small><a href='/chart/{sym}'>📈 TradingView chart</a></small></h1>"
    body += (
        "<h2>Data coverage</h2>"
        '<p>"Irregular spacing" is a per-stream timing heuristic, not error detection &mdash; '
        "for trade-driven types (trades, book deltas, bars) it can't tell a quiet market apart "
        "from a dropped connection.</p>"
        "<table border='1' cellpadding='4'>"
        "<tr><th>Type</th><th>Count</th><th>Start</th><th>End</th>"
        "<th>Duration</th><th>Irregular spacing</th></tr>"
        f"{coverage_rows}</table>"
    )

    outages = likely_outages(catalog, symbol)
    body += (
        "<h2>Likely outages</h2>"
        "<p>Periods where mark price AND order book were both silent at once "
        "&mdash; mark price is venue-pushed independent of trading, so this is a much "
        "stronger signal than a single-stream gap.</p>"
        f"<p>{_fmt_intervals(outages, limit=20)}</p>"
    )

    body += "<h2>Candlestick (footprint)</h2>"
    options = "".join(
        f"<option value='{tf}'{' selected' if tf == timeframe else ''}>{tf}</option>"
        for tf in TIMEFRAMES
    )
    body += (
        f"<select onchange=\"location.href='/coin/{html.escape(symbol)}?tf=' + this.value\">"
        f"{options}</select>"
    )

    deltas_info = cov.get("order_book_deltas")
    period_seconds = TIMEFRAMES[timeframe]
    start_ns = time.time_ns() - MAX_FOOTPRINT_CANDLES * period_seconds * 1_000_000_000
    trades = catalog.trade_ticks(instrument_ids=[symbol], start=start_ns)
    plotlyjs_included = False

    if not trades:
        body += "<p>No trade data yet &mdash; no candles to show.</p>"
    elif deltas_info is None:
        body += "<p>No order book data yet &mdash; footprint unavailable.</p>"
    else:
        candle_rows = [(t.ts_event, t.price.as_double()) for t in trades]
        candles = build_candles(candle_rows, period_seconds)[-MAX_FOOTPRINT_CANDLES:]
        deltas = catalog.order_book_deltas(instrument_ids=[symbol], start=start_ns)
        cells = build_footprint(
            deltas, candles, period_seconds, bands_per_candle=FOOTPRINT_BANDS_PER_CANDLE
        )
        footprint_fig = _render_footprint_chart(candles, cells, period_seconds)
        body += footprint_fig.to_html(full_html=False, include_plotlyjs="cdn")
        plotlyjs_included = True

    if deltas_info is None:
        body += (
            "<h2>Indicators</h2><p>No order book data yet &mdash; Microprice/OFI unavailable.</p>"
        )
        return _page(symbol, body)

    deltas = catalog.order_book_deltas(instrument_ids=[symbol], start=start_ns)
    instrument_id = InstrumentId.from_str(symbol)

    micro = Microprice()
    ofi = OrderFlowImbalance(window=50)
    # ponytail: rolling deques, not a downsampled/decimated series — caps page
    # size as the collector accumulates deltas, at the cost of only ever
    # charting the most recent window. Fine for "what's happening now"; swap
    # for real downsampling if you need the full history plotted.
    max_chart_points = 2_000
    micro_points: deque[tuple[int, float]] = deque(maxlen=max_chart_points)
    ofi_points: deque[tuple[int, float]] = deque(maxlen=max_chart_points)
    for ts, bid_price, bid_size, ask_price, ask_size in top_of_book_series(deltas, instrument_id):
        micro.update_raw(bid_price, bid_size, ask_price, ask_size)
        ofi.update_raw(bid_price, bid_size, ask_price, ask_size)
        micro_points.append((ts, micro.value))
        ofi_points.append((ts, ofi.value))

    micro_fig = go.Figure(
        go.Scatter(x=[t for t, _ in micro_points], y=[v for _, v in micro_points], mode="lines"),
    )
    micro_fig.update_layout(title="Microprice", height=300)

    ofi_fig = go.Figure(
        go.Scatter(x=[t for t, _ in ofi_points], y=[v for _, v in ofi_points], mode="lines"),
    )
    ofi_fig.update_layout(title=f"Order Flow Imbalance (window={ofi.window})", height=300)

    body += "<h2>Indicators</h2>"
    body += micro_fig.to_html(
        full_html=False, include_plotlyjs=False if plotlyjs_included else "cdn"
    )
    body += ofi_fig.to_html(full_html=False, include_plotlyjs=False)

    return _page(symbol, body)


def _render_live_page() -> str:
    with _LOCK:
        series = {name: list(points) for name, points in _SERIES.items()}

    fig = go.Figure()
    for name, points in series.items():
        fig.add_trace(
            go.Scatter(
                x=[ts for ts, _ in points],
                y=[value for _, value in points],
                mode="lines",
                name=name,
            ),
        )
    fig.update_layout(title="Live strategy signals", xaxis_title="ts_event (ns)")

    body = "<h1>Live signals</h1>" + fig.to_html(full_html=False, include_plotlyjs="cdn")
    return _page("ml_signals live", body, refresh_seconds=1)


def _compute_multilevel(snaps: list, levels: int) -> tuple[float | None, float | None]:
    """Return (ofi, obi) at `levels` by replaying snapshots through fresh indicators."""
    ofi_ind = MultiLevelOFI(levels=levels, window=len(snaps))
    obi_ind = MultiLevelOBI(levels=levels)
    for s in snaps:
        ofi_ind.update_raw(s.bid_prices, s.bid_sizes, s.ask_prices, s.ask_sizes)
        obi_ind.update_raw(s.bid_sizes, s.ask_sizes)
    ofi = ofi_ind.value if ofi_ind.initialized else None
    obi = obi_ind.value if obi_ind.initialized else None
    return ofi, obi


def _metrics_from_rolling(rolling: dict) -> list[dict]:
    """Compute live metrics from in-process 1s rolling snapshots — no Parquet read."""
    now_ns = time.time_ns()
    result = []
    for iid, dq in list(rolling.items()):
        if not dq:
            continue
        snaps = list(dq)
        latest = snaps[-1]
        ofi_3, obi_3 = _compute_multilevel(snaps, levels=3)
        ofi_5, obi_5 = _compute_multilevel(snaps, levels=5)
        ofi_10, obi_10 = _compute_multilevel(snaps, levels=10)
        result.append({
            "ts": now_ns,
            "instrument_id": iid,
            "ofi": ofi_10,
            "ofi_3": ofi_3,
            "ofi_5": ofi_5,
            "ofi_10": ofi_10,
            "obi_3": obi_3,
            "obi_5": obi_5,
            "obi_10": obi_10,
            "microprice": (
                (latest.bid_prices[0] * latest.ask_sizes[0] + latest.ask_prices[0] * latest.bid_sizes[0])
                / (latest.bid_sizes[0] + latest.ask_sizes[0])
                if latest.bid_prices and latest.ask_prices and (latest.bid_sizes[0] + latest.ask_sizes[0]) > 0
                else None
            ),
            "spread": (latest.ask_prices[0] - latest.bid_prices[0]) if latest.ask_prices and latest.bid_prices else None,
        })
    return result


def _fast_loop(catalog_path: str, rolling: dict | None = None) -> None:
    """Update OFI/microprice/spread for all coins every second (rolling) or 5s (Parquet).

    When `rolling` is provided (collector-embedded mode), reads from the in-process
    1s snapshot buffer — no catalog I/O, always fresh.
    Falls back to reading 60s of order book deltas from Parquet when running standalone.
    """
    while True:
        try:
            if rolling is not None:
                book_metrics = _metrics_from_rolling(rolling)
                interval = 1
            else:
                book_metrics = metrics_computer.compute_book_metrics_all(catalog_path)
                interval = LIVE_INTERVAL_SECONDS
            with _METRICS_LOCK:
                for m in book_metrics:
                    iid = m["instrument_id"]
                    if iid in _LIVE:
                        _LIVE[iid].update({
                            "ts":         m["ts"],
                            "ofi":        m.get("ofi"),
                            "microprice": m.get("microprice"),
                            "spread":     m.get("spread"),
                        })
                    else:
                        _LIVE[iid] = m
        except Exception:
            logger.exception("Fast metrics loop failed")
        time.sleep(interval)


def _slow_loop(catalog_path: str) -> None:
    """Full snapshot (price/pct/vol + book metrics) every DB_WRITE_INTERVAL_SECONDS.

    Writes 1-min aggregates to SQLite for historical bookkeeping.
    """
    db_path = str(Path(catalog_path).parent / "metrics.db")
    while True:
        try:
            snapshots = metrics_computer.compute_all(catalog_path)
            if snapshots:
                with _METRICS_LOCK:
                    for s in snapshots:
                        _LIVE[s["instrument_id"]] = s
                metrics_store.write(snapshots, db_path)
        except Exception:
            logger.exception("Slow metrics loop failed")
        time.sleep(DB_WRITE_INTERVAL_SECONDS)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        qs = parse_qs(parsed.query)

        if path == "/live":
            html_doc = _render_live_page()
        elif path.startswith("/history/"):
            symbol = path.removeprefix("/history/")
            html_doc = _render_history_page(symbol)
        elif path.startswith("/coin/"):
            symbol = path.removeprefix("/coin/")
            if symbol not in list_instruments(CATALOG_PATH):
                self.send_response(404)
                self.end_headers()
                return
            timeframe = qs.get("tf", ["1m"])[0]
            if timeframe not in TIMEFRAMES:
                timeframe = "1m"
            html_doc = _render_coin_page(symbol, timeframe)
        elif path.startswith("/chart/"):
            symbol = path.removeprefix("/chart/")
            import datetime as _dt
            def _parse_dt(key: str, default_ms: int) -> int:
                v = qs.get(key, [None])[0]
                if v:
                    try:
                        return int(_dt.datetime.fromisoformat(v).timestamp() * 1000)
                    except ValueError:
                        pass
                return default_ms
            now_ms   = int(time.time() * 1000)
            start_ms = _parse_dt("start", now_ms - 4 * 3600 * 1000)
            end_ms   = _parse_dt("end",   now_ms)
            html_doc = _render_chart_page(symbol, start_ms, end_ms)
        else:
            sort_col = qs.get("sort", ["ofi"])[0]
            direction = qs.get("dir", ["desc"])[0]
            html_doc = _render_rankings_page(sort_col, direction)

        body = html_doc.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass  # ponytail: silence per-request access logs


def serve_in_background(port: int = 8765, catalog_path: str = CATALOG_PATH, rolling: dict | None = None) -> HTTPServer:
    global CATALOG_PATH
    CATALOG_PATH = catalog_path
    # Pre-populate _LIVE from the last SQLite snapshot so the rankings table
    # shows something immediately on startup instead of waiting for the first
    # compute cycle.
    db_path = str(Path(catalog_path).parent / "metrics.db")
    try:
        rows = metrics_store.latest(db_path)
        with _METRICS_LOCK:
            for r in rows:
                _LIVE[r["instrument_id"]] = r
        if rows:
            logger.info("Pre-loaded %d instruments from metrics.db", len(rows))
    except Exception:
        logger.warning("Could not pre-load metrics.db — starting cold", exc_info=True)

    server = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=_fast_loop, args=(catalog_path, rolling), daemon=True).start()
    threading.Thread(target=_slow_loop, args=(catalog_path,), daemon=True).start()
    return server


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--catalog", default=CATALOG_PATH)
    args = parser.parse_args()

    serve_in_background(port=args.port, catalog_path=args.catalog)
    print(f"Dashboard running at http://localhost:{args.port}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
