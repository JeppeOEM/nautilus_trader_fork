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
Dispatch/metadata for dYdX-specific chart indicators that are not `nautilus_trader.indicators`
classes and cannot be computed from OHLCV candle fields alone (CVD, Cancel Pressure, OFI --
Stories 10.2-10.4). Mirrors `chart_indicators.py`'s catalog/replay/catalog_json shape for
params/panel/dispatch, but deliberately does not mirror its `enum_params` round-tripping (no
custom indicator needs an enum-typed param yet -- add it if one does, DESIGN-01) and a custom
indicator's `replay` receives a `ReplayWindow` (instrument id + window bounds) alongside the
candle list, since it needs to fetch its own order-book/trade-level/second-snapshot rows for
that window -- a candle dict alone (o/h/l/c/v) doesn't carry that data.

Registered indicators grow one story at a time: CumulativeVolumeDelta (Story 10.2), Cancel
Pressure/OFI to follow (Stories 10.3-10.4). Per DESIGN-02, the two catalogs stay unaware of
each other's contents -- this module never reads `INDICATOR_CATALOG`, `chart_indicators.py`
never reads `CUSTOM_INDICATOR_CATALOG`, and they're merged only at the dashboard.py call site.
The one shared import below (`Panel`) is a type alias, not a coupling to catalog internals.
"""

import os
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nautilus_trader.model.data import OrderBookDelta

from ml_signals.book_features import CancellationTracker
from ml_signals.chart_indicators import Panel
from ml_signals.indicators import trade_aggregates


# Duplicated from dashboard.py's own module-level constant (same env var, same default) --
# this module cannot import it from there without a circular import (dashboard.py imports
# this module). One line, not worth a shared-constants module for just this (DESIGN-01) --
# `_second_snapshots` below duplicates a larger chunk (the catalog-query + CustomData-unwrap
# logic itself); that duplication is worth revisiting into a shared helper once a second
# real call site needs the identical pattern (Story 10.3/10.4 need a different one, for
# OrderBookDelta, so this may end up staying a one-off).
_CATALOG_PATH = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")


@dataclass(frozen=True)
class ReplayWindow:
    """The window context a custom indicator's `replay` needs beyond the candle list itself.

    `start_ms`/`end_ms` are `None` for the live (not-yet-closed) window -- mirrors
    `coin_indicators_handler`'s existing live/historical branch in dashboard.py, which already
    picks `_live_candles_json` vs `_historical_candles_json` on exactly this same condition.
    """

    instrument_id: str
    bar_seconds: int
    start_ms: int | None
    end_ms: int | None


ReplayFn = Callable[[list[dict], dict[str, Any], ReplayWindow], dict[str, list[float | None]]]


@dataclass
class CustomIndicatorSpec:
    # JSON-safe default params (same role as IndicatorSpec.params in chart_indicators.py).
    params: dict[str, Any]
    panel: Panel
    # Computes every registered output attribute for the given candles/params/window, aligned
    # 1:1 with `candles` -- identical output contract to chart_indicators.replay_indicator.
    replay: ReplayFn


CUSTOM_INDICATOR_CATALOG: dict[str, CustomIndicatorSpec] = {}


def replay_indicator(
    candles: list[dict], name: str, params: dict[str, Any], window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """Look up `name` in `CUSTOM_INDICATOR_CATALOG` and run its `replay` function."""
    if name not in CUSTOM_INDICATOR_CATALOG:
        raise ValueError(f"Unknown custom indicator: {name!r}")
    spec = CUSTOM_INDICATOR_CATALOG[name]
    merged = {**spec.params, **params}
    return spec.replay(candles, merged, window)


def catalog_json() -> dict[str, Any]:
    """`CUSTOM_INDICATOR_CATALOG` serialized for the merged `/data/indicators/catalog` response."""
    return {
        name: {"params": spec.params, "panel": spec.panel}
        for name, spec in CUSTOM_INDICATOR_CATALOG.items()
    }


def _second_snapshots(window: ReplayWindow) -> list[dict]:
    """Fetch this window's DydxSecondSnapshot rows from the catalog, as plain dicts.

    Mirrors dashboard.py's _historical_lines_json exactly (same catalog-query +
    CustomData-unwrap pattern) -- duplicated here rather than imported, since importing
    from dashboard.py would be circular (dashboard.py imports this module).
    """
    from dydx_collector.second_snapshot import DydxSecondSnapshot
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(_CATALOG_PATH)
    results = catalog.query(
        data_cls=DydxSecondSnapshot, identifiers=[window.instrument_id],
        start=window.start_ms * 1_000_000, end=window.end_ms * 1_000_000,
    )
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    # buy_count/sell_count are required by trade_aggregates()'s reduction below even though
    # _cvd_replay only consumes the volume totals it returns -- not dead data, just an unused
    # part of a shared function's output.
    return [
        {
            "buy_volume": s.buy_volume, "sell_volume": s.sell_volume,
            "buy_count": s.buy_count, "sell_count": s.sell_count, "ts_event": s.ts_event,
        }
        for s in snapshots
    ]


def _cvd_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """Per-candle running-cumulative buy_volume - sell_volume for the currently-requested
    window -- an unbounded, request-anchored total (resets to 0 at whichever candle happens
    to be first in the current view), not the 5-minute rolling/decaying oscillator the old
    chart_data.py row computed. This is a deliberate scope choice for the picker version (see
    epics.md Story 10.2 AC #1) -- panning/resizing the visible window changes where the sum
    restarts, so read it as "net flow within the current view," not an absolute level.

    Unrelated to ofi_strategy.py's own "cum_delta" signal (a live Strategy's independent
    5-minute rolling-window implementation) -- same name, different metric, different code.

    Historical only -- the old fixed row was never live either (it always replayed the
    date-range form's explicit window, never an in-process live buffer). Live requests get
    None for every candle, a real gap, not a fabricated value (DATA-01).

    A candle bucket with zero snapshot rows is *not* treated as "no volume" (which would
    silently paper over a genuine second-snapshot collection gap as a flat/unchanged value,
    DATA-01) -- it gets None, and the running total resumes from its last real value on the
    next bucket that does have data.
    """
    if window.start_ms is None or window.end_ms is None:
        return {"value": [None] * len(candles)}
    bar_ns = window.bar_seconds * 1_000_000_000
    buckets: dict[int, list[dict]] = defaultdict(list)
    for row in _second_snapshots(window):
        buckets[(row["ts_event"] // bar_ns) * bar_ns].append(row)
    running_total = 0.0
    values: list[float | None] = []
    for candle in candles:
        rows = buckets.get(candle["t"] * 1_000_000, [])
        if not rows:
            values.append(None)
            continue
        buy_vol, sell_vol, _, _ = trade_aggregates(rows)
        running_total += buy_vol - sell_vol
        values.append(running_total)
    return {"value": values}


CUSTOM_INDICATOR_CATALOG["CumulativeVolumeDelta"] = CustomIndicatorSpec(
    params={}, panel="oscillator", replay=_cvd_replay,
)


def _order_book_deltas(window: ReplayWindow) -> list[OrderBookDelta]:
    """Fetch this window's OrderBookDelta rows from the catalog, sorted by ts_init --
    same catalog-query pattern chart_data.py's own replay loop already uses."""
    from nautilus_trader.persistence.catalog import ParquetDataCatalog

    catalog = ParquetDataCatalog(_CATALOG_PATH)
    deltas = catalog.order_book_deltas(
        instrument_ids=[window.instrument_id],
        start=window.start_ms * 1_000_000, end=window.end_ms * 1_000_000,
    )
    return sorted(deltas, key=lambda d: d.ts_init)


# Cap on how many consecutive empty buckets forward-fill will carry a value across before
# giving up and reporting None again -- forward-filling a *quiet* market is correct (the
# level genuinely hasn't changed), but forward-filling forever across a real ingestion outage
# would render a stale reading as confidently current, which is exactly what DATA-01 forbids.
# ponytail: a flat bucket-count cap, not a time-aware one -- revisit if a real outage shorter
# than this many buckets still reads as a false "live" value in practice.
_MAX_FORWARD_FILL_BUCKETS = 10


def _cancel_pressure_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """Per-candle bid/ask cancel pressure, forward-filled: within a candle's bucket, the
    tracker's state as of the LAST delta processed becomes that candle's value; a bucket with
    no delta events carries forward the last known value, up to `_MAX_FORWARD_FILL_BUCKETS`
    (DATA-01 -- a real ingestion gap must eventually read as unknown again, not confidently
    stale forever). Unlike CVD's running-cumulative sum (a flow, correctly reset per bucket),
    cancel pressure is a *level* -- the book's current cancellation-pressure state -- so
    persisting the last real observation across a quiet bucket is the metric's own correct
    behavior, not fabrication. Candles before the first delta is processed are None (real
    warm-up, same as any other indicator).

    A BookAction.CLEAR (troll/CLAUDE.md DATA-03: typically a forced resync, a destructive
    worst-case recovery) resets the tracker's rolling window to empty -- its immediate
    post-clear rate() is a meaningless (0.0, 0.0), not a real neutral reading, so that bucket
    is recorded as an explicit reset rather than a sample: forward-fill breaks there instead
    of treating it as genuine data or silently continuing the pre-clear value.

    Reuses book_features.CancellationTracker unchanged -- no new cancellation math (DESIGN-02).
    Historical only, same reasoning as CVD (Story 10.2): the old fixed row was never live.
    """
    if window.start_ms is None or window.end_ms is None:
        none_col: list[float | None] = [None] * len(candles)
        return {"bid_pressure": none_col, "ask_pressure": list(none_col)}
    from nautilus_trader.model.book import OrderBook
    from nautilus_trader.model.enums import BookAction
    from nautilus_trader.model.enums import BookType
    from nautilus_trader.model.identifiers import InstrumentId

    book = OrderBook(InstrumentId.from_str(window.instrument_id), BookType.L2_MBP)
    tracker = CancellationTracker(window=params["window"])
    bar_ns = window.bar_seconds * 1_000_000_000
    # None value = explicit reset (a CLEAR happened in this bucket); absent key = no event at
    # all in this bucket (an ordinary gap, still eligible for bounded forward-fill).
    bucket_samples: dict[int, tuple[float, float] | None] = {}
    for delta in _order_book_deltas(window):
        best_bid = book.best_bid_price()
        best_ask = book.best_ask_price()
        best_bid_p = best_bid.as_double() if best_bid else None
        best_ask_p = best_ask.as_double() if best_ask else None
        tracker.update(delta, best_bid_p, best_ask_p)
        book.apply_delta(delta)
        bucket = (delta.ts_event // bar_ns) * bar_ns
        if delta.action == BookAction.CLEAR:
            bucket_samples[bucket] = None
            continue
        rate = tracker.rate()
        bucket_samples[bucket] = (rate.bid_pressure, rate.ask_pressure)

    bid_out: list[float | None] = []
    ask_out: list[float | None] = []
    last_bid: float | None = None
    last_ask: float | None = None
    gap_buckets = 0
    for candle in candles:
        key = candle["t"] * 1_000_000
        if key in bucket_samples:
            sample = bucket_samples[key]
            last_bid, last_ask = sample if sample is not None else (None, None)
            gap_buckets = 0
        else:
            gap_buckets += 1
            if gap_buckets > _MAX_FORWARD_FILL_BUCKETS:
                last_bid = last_ask = None
        bid_out.append(last_bid)
        ask_out.append(last_ask)
    return {"bid_pressure": bid_out, "ask_pressure": ask_out}


CUSTOM_INDICATOR_CATALOG["CancelPressure"] = CustomIndicatorSpec(
    params={"window": 200}, panel="histogram", replay=_cancel_pressure_replay,
)


def _ofi_bucket_samples(window: ReplayWindow, ofi_window: int) -> dict[int, float]:
    """Replay `_order_book_deltas(window)` and return last-value-in-bucket `OrderFlowImbalance`
    samples, keyed by bucket start (ns). Drives the indicator exactly as chart_data.py's
    retired fixed row did -- `book.apply_delta(delta)` FIRST, then read post-delta top-of-book,
    skipping the delta if either side is `None`, THEN `ofi.update_raw(...)` (DESIGN-02:
    unchanged reuse). This call order is the opposite of Cancel Pressure's
    `CancellationTracker.update`, which needs PRE-delta best prices -- do not conflate the two.
    """
    from nautilus_trader.model.book import OrderBook
    from nautilus_trader.model.enums import BookType
    from nautilus_trader.model.identifiers import InstrumentId

    from ml_signals.indicators import OrderFlowImbalance

    book = OrderBook(InstrumentId.from_str(window.instrument_id), BookType.L2_MBP)
    ofi = OrderFlowImbalance(window=ofi_window)
    bar_ns = window.bar_seconds * 1_000_000_000
    bucket_samples: dict[int, float] = {}
    for delta in _order_book_deltas(window):
        book.apply_delta(delta)
        bid_price = book.best_bid_price()
        ask_price = book.best_ask_price()
        if bid_price is None or ask_price is None:
            continue
        ofi.update_raw(
            bid_price.as_double(), book.best_bid_size().as_double(),
            ask_price.as_double(), book.best_ask_size().as_double(),
        )
        if not ofi.initialized:
            continue
        bucket = (delta.ts_event // bar_ns) * bar_ns
        bucket_samples[bucket] = ofi.value
    return bucket_samples


def _ofi_replay(
    candles: list[dict], params: dict[str, Any], window: ReplayWindow,
) -> dict[str, list[float | None]]:
    """Per-candle top-of-book Order Flow Imbalance, last-value-in-bucket and forward-filled
    (bounded by `_MAX_FORWARD_FILL_BUCKETS`, same DATA-01 reasoning as Cancel Pressure, Story
    10.3): `OrderFlowImbalance.value` is a continuously-recomputed trailing rolling-window sum,
    not a per-bucket flow, so persisting the last sampled value across a quiet bucket reflects
    real state, not fabrication. `None` before `ofi.initialized` first becomes True (real
    warm-up) and before the first delta is processed at all.

    Reuses `_order_book_deltas` (Story 10.3) -- OFI is the second, not third, book-delta
    consumer this module now shares that helper with (DESIGN-01: no further extraction needed).
    Historical only, same reasoning as CVD/Cancel Pressure: the old fixed row was never live.
    """
    if window.start_ms is None or window.end_ms is None:
        return {"value": [None] * len(candles)}
    bucket_samples = _ofi_bucket_samples(window, params["window"])

    out: list[float | None] = []
    last_value: float | None = None
    gap_buckets = 0
    for candle in candles:
        key = candle["t"] * 1_000_000
        if key in bucket_samples:
            last_value = bucket_samples[key]
            gap_buckets = 0
        else:
            gap_buckets += 1
            if gap_buckets > _MAX_FORWARD_FILL_BUCKETS:
                last_value = None
        out.append(last_value)
    return {"value": out}


CUSTOM_INDICATOR_CATALOG["OrderFlowImbalance"] = CustomIndicatorSpec(
    params={"window": 20}, panel="oscillator", replay=_ofi_replay,
)
