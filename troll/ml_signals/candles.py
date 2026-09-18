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
"""Bucket trade prices into OHLC candles at an arbitrary timeframe."""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from ml_signals.indicators import microprice
from ml_signals.indicators import spread


logger = logging.getLogger(__name__)


TIMEFRAMES = {
    "1m": 60,
    "5m": 300,
    "10m": 600,
    "15m": 900,
    "30m": 1800,
    "45m": 2700,
    "1h": 3600,
    "1d": 86400,
    "1w": 604800,
}

# Wider than this, the raw 1s archive is too big to rescan per request; read the minute rollup.
ROLLUP_THRESHOLD_SECONDS = TIMEFRAMES["1h"]


@dataclass
class Candle:
    ts_open: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def build_candles(rows: list[tuple[int, float, float]], period_seconds: int) -> list[Candle]:
    """
    Bucket (ts_event, price, size) trade rows into `period_seconds`-wide OHLC candles.

    The last candle covers "now" and is necessarily partial — callers re-fetch
    trades from the catalog and recompute on every request, so it fills in
    live as new trades land. No special-cased "live candle" code needed.
    """
    period_ns = period_seconds * 1_000_000_000
    buckets: dict[int, list[tuple[float, float]]] = {}
    for ts_event, price, size in sorted(rows, key=lambda row: row[0]):
        buckets.setdefault(ts_event // period_ns, []).append((price, size))

    return [
        Candle(
            ts_open=bucket_key * period_ns,
            open=prices_sizes[0][0],
            high=max(p for p, _ in prices_sizes),
            low=min(p for p, _ in prices_sizes),
            close=prices_sizes[-1][0],
            volume=sum(s for _, s in prices_sizes),
        )
        for bucket_key, prices_sizes in sorted(buckets.items())
    ]


def aggregate_ohlc(
    rows: list[tuple[int, float, float, float, float, float]],
    period_seconds: int,
) -> list[Candle]:
    """
    Re-bucket already-built 1-second OHLC rows into wider `period_seconds` candles.

    Rows are (ts_event, open, high, low, close, volume) — one per second, e.g. from
    DydxSecondSnapshot's open/high/low/close_price fields (raw TradeTicks are no
    longer persisted, see troll/docs/DATA_DICTIONARY.md's Retention section). Unlike
    `build_candles`, which derives OHLC from a flat list of trade prices, this
    combines pre-built OHLC correctly: open of the first second in the bucket, high/
    low across all of them, close of the last, volume summed.
    """
    period_ns = period_seconds * 1_000_000_000
    buckets: dict[int, list[tuple[int, float, float, float, float, float]]] = {}
    for row in sorted(rows, key=lambda r: r[0]):
        buckets.setdefault(row[0] // period_ns, []).append(row)

    return [
        Candle(
            ts_open=bucket_key * period_ns,
            open=members[0][1],
            high=max(r[2] for r in members),
            low=min(r[3] for r in members),
            close=members[-1][4],
            volume=sum(r[5] for r in members),
        )
        for bucket_key, members in sorted(buckets.items())
    ]


def candle_dicts_from_snapshots(snapshots: list, period_seconds: int) -> list[dict]:
    """
    Aggregate DydxSecondSnapshot-like objects' per-second OHLC into JSON-ready candle dicts.

    Accepts anything with .ts_event/.open_price/.high_price/.low_price/.close_price/
    .buy_volume/.sell_volume attributes -- both dashboard.py's local-mode catalog.query()
    results and data_api's query_second_snapshots() results satisfy this, so one function
    serves both without a dict round-trip.

    Shared so the aggregation runs exactly once, server-side (on whichever box actually
    holds the catalog), and only the resulting handful of candle bars ever crosses the
    network -- not the full per-snapshot order-book depth (bid/ask price/size arrays,
    up to 20 levels each) that /catalog/snapshots returns for Lines mode. Routing candles
    through that endpoint (an earlier fix, before this one) was itself the bug: a 4-hour
    window's snapshots serialize to ~12MB of mostly-unused order-book depth, which over an
    SSH tunnel took 30-60s to transfer -- consistently exceeding any sane client timeout.
    The aggregated candles for the same window are a few KB.
    """
    raw = [
        (s.ts_event, s.open_price, s.high_price, s.low_price, s.close_price, s.buy_volume + s.sell_volume)
        for s in snapshots
        if s.close_price is not None
    ]
    if not raw:
        return []
    candle_data = aggregate_ohlc(raw, period_seconds=period_seconds)
    return [
        {"t": c.ts_open // 1_000_000, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
        for c in candle_data
    ]


def choose_candle_source(bar_seconds: int) -> str:
    return "rollup_1m" if bar_seconds > ROLLUP_THRESHOLD_SECONDS else "raw_1s"


def _weighted_mean(rows: list, attr: str) -> float:
    total = sum(r.seconds_observed for r in rows)
    return sum(getattr(r, attr) * r.seconds_observed for r in rows) / total if total else 0.0


def _rollup_bucket_dict(bucket_key: int, members: list, period_ns: int) -> dict | None:
    """One candle dict from a bucket's rollups; None if no member traded (matches raw path)."""
    traded = [r for r in members if r.open is not None]
    if not traded:
        return None
    last = members[-1]
    book = {
        "bid_prices": [last.close_bid_price],
        "bid_sizes": [last.close_bid_size],
        "ask_prices": [last.close_ask_price],
        "ask_sizes": [last.close_ask_size],
    }
    return {
        "t": bucket_key * period_ns // 1_000_000,
        "o": traded[0].open,
        "h": max(r.high for r in traded),
        "l": min(r.low for r in traded),
        "c": traded[-1].close,
        "v": sum(r.buy_volume + r.sell_volume for r in members),
        "seconds_observed": sum(r.seconds_observed for r in members),
        "ofi_5": sum(r.ofi_5 for r in members),
        "ofi_10": sum(r.ofi_10 for r in members),
        "obi_5": _weighted_mean(members, "obi_5"),
        "obi_10": _weighted_mean(members, "obi_10"),
        "close_bid_price": last.close_bid_price,
        "close_ask_price": last.close_ask_price,
        "microprice": microprice(book),
        "spread": spread(book),
    }


def rollup_dicts_from_rows(rows: list, period_seconds: int) -> list[dict]:
    """Re-bucket DydxMinuteRollup-like rows into JSON-ready candle dicts (rollup analogue of
    `candle_dicts_from_snapshots`). Flows are summed; top-of-book takes the last member."""
    period_ns = period_seconds * 1_000_000_000
    buckets: dict[int, list] = {}
    for r in sorted(rows, key=lambda r: r.ts_event):
        buckets.setdefault(r.ts_event // period_ns, []).append(r)
    dicts = (_rollup_bucket_dict(k, m, period_ns) for k, m in sorted(buckets.items()))
    return [d for d in dicts if d is not None]


def candle_dicts_for_window(
    iid: str,
    start_ns: int,
    end_ns: int,
    bar_seconds: int,
    snapshot_rows_fn: Callable[[str, int, int], list],
    rollup_rows_fn: Callable[[str, int, int], list],
) -> list[dict]:
    """
    Serve wide bars from the minute rollup, narrow ones from raw 1s; every dict carries `source`.

    If the rollup only covers the tail of the range (pre-feature history), the buckets before
    the first rollup's are served from raw 1s, so the chart never silently truncates (DATA-01).
    The first rollup's own bucket is rollup-only (never mixes sources), so it may be partial.
    No rollup at all: all raw.
    """
    if choose_candle_source(bar_seconds) != "rollup_1m":
        return _raw_candles(iid, start_ns, end_ns, bar_seconds, snapshot_rows_fn)
    rollups = rollup_rows_fn(iid, start_ns, end_ns)
    if not rollups:
        logger.warning("No minute rollup for %s in [%d, %d]; falling back to raw 1s", iid, start_ns, end_ns)
        return _raw_candles(iid, start_ns, end_ns, bar_seconds, snapshot_rows_fn)
    period_ns = bar_seconds * 1_000_000_000
    first_bucket_start = min(r.ts_event for r in rollups) // period_ns * period_ns
    leading: list[dict] = []
    if start_ns < first_bucket_start:
        logger.warning("Minute rollup for %s starts at %d; raw 1s before it", iid, first_bucket_start)
        leading = _raw_candles(iid, start_ns, first_bucket_start - 1, bar_seconds, snapshot_rows_fn)
    return leading + [{**c, "source": "rollup_1m"} for c in rollup_dicts_from_rows(rollups, bar_seconds)]


def _raw_candles(
    iid: str, start_ns: int, end_ns: int, bar_seconds: int, snapshot_rows_fn: Callable[[str, int, int], list]
) -> list[dict]:
    raw = candle_dicts_from_snapshots(snapshot_rows_fn(iid, start_ns, end_ns), bar_seconds)
    return [{**c, "source": "raw_1s"} for c in raw]
