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

Three pages:
- `/`            all known coins as a data table (price, volatility, % change 1h/24h)
- `/coin/{id}`   per-coin data coverage + gaps, a footprint candlestick chart
                 (?tf=1m|5m|10m|15m|30m|45m|1h), plus a Microprice/OFI panel
- `/live`        in-process live signal monitor (see `record()` below)

Usage from a running strategy::

    from ml_signals.dashboard import record, serve_in_background

    serve_in_background(port=8765)  # once, e.g. in on_start


    def on_signal(self, signal):
        record("logistic_trend", signal.value, signal.ts_event)
"""

import html
import threading
from collections import defaultdict
from collections import deque
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlparse

import pandas as pd
import plotly.graph_objects as go

from ml_signals.book_features import top_of_book_series
from ml_signals.candles import TIMEFRAMES
from ml_signals.candles import build_candles
from ml_signals.catalog_stats import coverage
from ml_signals.catalog_stats import likely_outages
from ml_signals.catalog_stats import list_instruments
from ml_signals.catalog_stats import overview_table
from ml_signals.footprint import build_footprint
from ml_signals.indicators import Microprice
from ml_signals.indicators import OrderFlowImbalance
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


CATALOG_PATH = "troll/dydx_collector/catalog"
MAX_FOOTPRINT_CANDLES = 30
FOOTPRINT_BANDS_PER_CANDLE = 4

_LOCK = threading.Lock()
_SERIES: dict[str, deque[tuple[int, float]]] = defaultdict(lambda: deque(maxlen=2000))

_NAV = '<p><a href="/">Overview</a> | <a href="/live">Live signals</a></p>'


def record(name: str, value: float, ts_event: int) -> None:
    with _LOCK:
        _SERIES[name].append((ts_event, value))


def _page(title: str, body: str, refresh_seconds: int = 15) -> str:
    return (
        f"<html><head><title>{html.escape(title)}</title>"
        f'<meta http-equiv="refresh" content="{refresh_seconds}"></head>'
        f"<body>{_NAV}{body}</body></html>"
    )


def _render_overview_page() -> str:
    rows = overview_table(CATALOG_PATH)

    def _fmt(value: float | None, suffix: str = "") -> str:
        return f"{value:.4f}{suffix}" if value is not None else "&mdash;"

    table_rows = "".join(
        f"<tr><td><a href='/coin/{html.escape(row['instrument_id'])}'>"
        f"{html.escape(row['instrument_id'])}</a></td>"
        f"<td>{_fmt(row['price'])}</td>"
        f"<td>{_fmt(row['volatility'])}</td>"
        f"<td>{_fmt(row['pct_change_1h'], '%')}</td>"
        f"<td>{_fmt(row['pct_change_24h'], '%')}</td></tr>"
        for row in rows
    )
    body = (
        "<h1>Coins</h1>"
        "<table border='1' cellpadding='4'>"
        "<tr><th>Instrument</th><th>Price</th><th>Volatility</th>"
        "<th>% chg 1h</th><th>% chg 24h</th></tr>"
        f"{table_rows}</table>"
    )
    return _page("ml_signals overview", body)


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

    body = f"<h1>{html.escape(symbol)}</h1>"
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
    trades = catalog.trade_ticks(instrument_ids=[symbol])
    plotlyjs_included = False

    if not trades:
        body += "<p>No trade data yet &mdash; no candles to show.</p>"
    elif deltas_info is None:
        body += "<p>No order book data yet &mdash; footprint unavailable.</p>"
    else:
        period_seconds = TIMEFRAMES[timeframe]
        candle_rows = [(t.ts_event, t.price.as_double()) for t in trades]
        candles = build_candles(candle_rows, period_seconds)[-MAX_FOOTPRINT_CANDLES:]
        deltas = catalog.order_book_deltas(instrument_ids=[symbol])
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

    deltas = catalog.order_book_deltas(instrument_ids=[symbol])
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
    return _page("ml_signals live", body, refresh_seconds=5)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/live":
            html_doc = _render_live_page()
        elif path.startswith("/coin/"):
            symbol = path.removeprefix("/coin/")
            if symbol not in list_instruments(CATALOG_PATH):
                self.send_response(404)
                self.end_headers()
                return
            timeframe = parse_qs(parsed.query).get("tf", ["1m"])[0]
            if timeframe not in TIMEFRAMES:
                timeframe = "1m"
            html_doc = _render_coin_page(symbol, timeframe)
        else:
            html_doc = _render_overview_page()

        body = html_doc.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass  # ponytail: silence per-request access logs


def serve_in_background(port: int = 8765, catalog_path: str = CATALOG_PATH) -> HTTPServer:
    global CATALOG_PATH
    CATALOG_PATH = catalog_path
    server = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
