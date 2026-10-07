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
Every series the chart page draws, and every page of it (Story 24.2 moved them here out of the
chart-data, book-features and footprint modules and `data_api/routes/{candles,snapshots,
indicator_series,indicators}.py`, bodies verbatim unless noted):

- **Candles** -- `candle_page`: the one candle source for the chart, its indicator panes and
  anything else that must agree with them. It reads the candles context's query services only
  (`candles.application.queries`), the store first and the archive's one seconds -> bars fold for
  what the store does not cover, and fails loud on an impossible candle (`ImpossibleCandle`).
- **Lines mode** -- `price_series_rows`/`snapshot_series_page`: bid/ask/mid/microprice/CVD-weighted
  price per archived second.
- **Indicator panes** -- `indicator_values_page` (the picker's configured indicators, dispatched
  through `views.indicator_picker`). Story 33.11 deleted `replay_bucket_samples`, the per-bar
  OFI/OBI/microprice/spread replay whose only callers were tests.
- **Cancellation pressure** -- `CancellationTracker`, the picker's cancel-pressure replay input.
  Story 33.4 deleted the rest of the old book-feature and resting-order footprint code, which no
  page drew any more (its `liquidity_distance`, moved to `kernel.indicators`, was deleted there in
  Story 33.11 with no caller either).
- **Volume footprint** -- `footprint_page` (Story 32.8): the raw trade archive's executed trades in
  integer price rows per closed bar of the same `candle_page`, historical bars only.

Two rendering rules, and nothing else, change what is drawn: `with_gap_markers` (one
`{"t": earlier + k * bar_ms}` row per missing bar wherever two kept bars are more than a bar
apart, shared by candles, indicator values and the derivatives pages so panes on one time axis break in
the same place) and the Lines-mode gap run (one all-`None` row per missing second where two seconds
are more than `SNAPSHOT_GAP_THRESHOLD_MS` apart -- DATA-01's honest break). Both place their rows
with `_gap_times`, so a hole takes as many chart slots as it has missing intervals, capped at
`MAX_GAP_ROWS_PER_GAP` per hole (Story 32.1). The reader never re-validates the
capture gate: a crossed second is priced like any other, and an empty top of book, which the gate
never writes, is ledgered and raised (`EmptyTopOfBook`), never skipped (DATA-07). This removed the
AD-3 deviation the parent spine tracked (`routes/snapshots.py`'s two reader-side skips).

Each page returns `(rows, has_more)` of plain dicts shaped exactly like the route's response items;
the catalog, candle-store and live-tail inputs are passed in by the caller, never read here.
"""

from collections import deque
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

import numpy as np
from candles.application import queries
from candles.domain.candle import is_valid_candle
from candles.domain.fold import BAR_SECONDS
from candles.domain.fold import bucket_start_ms
from kernel import catalog_files
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_S
from kernel.indicators import microprice as calc_microprice
from kernel.indicators import mid_price as calc_mid_price
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from kernel.venues import venue_of
from observability import error_ledger

from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from views.catalog_reads import fetch_page
from views.catalog_reads import has_older_data
from views.catalog_reads import liquidation_feed_start
from views.catalog_reads import query_second_snapshots


if TYPE_CHECKING:  # the runtime import is deferred: indicator_picker imports this module
    from views.indicator_picker import IndicatorRequest


# =============================================================================================
# Cancellation rate tracker (what remains of the book-features module: Story 33.4 deleted its
# caller-less depth-profile, book-imbalance, feature and top-of-book series functions). Read by
# `views.indicator_picker`'s cancel-pressure replay.
# =============================================================================================


@dataclass
class CancelRate:
    """
    Ratio of size being pulled vs added at the best bid and ask.

    Positive = size being added net (level building, stable).
    Negative = size being pulled net (level depleting, fragile).

    cancel_pressure = (deleted_size - added_size) / (deleted_size + added_size)
    Range: -1 (all additions) to +1 (all cancellations).
    """

    bid_pressure: float  # positive = bids being cancelled
    ask_pressure: float


class CancellationTracker:
    """
    Stateful tracker for add/delete size events at the best price level.

    Call `update(delta, best_bid_price, best_ask_price)` on every incoming
    delta before applying it to the OrderBook (so you can compare against
    the *current* best price, not the post-delta best).

    Uses a rolling event window so old events expire as market conditions change.
    """

    def __init__(self, window: int = 200) -> None:
        # (side, action, size) for the last `window` events at best levels
        self._events: deque[tuple[str, str, float]] = deque(maxlen=window)

    def update(
        self,
        delta: OrderBookDelta,
        best_bid_price: float | None,
        best_ask_price: float | None,
    ) -> None:
        if delta.action == BookAction.CLEAR:
            self._events.clear()
            return
        # UPDATE = size change at existing level; ambiguous direction — skip.
        # Only track ADD (new level appears at best) and DELETE (level pulled entirely).
        if delta.action not in (BookAction.ADD, BookAction.DELETE):
            return

        delta_price = delta.order.price.as_double()
        delta_size = delta.order.size.as_double()
        action_str = "add" if delta.action == BookAction.ADD else "delete"

        if delta.order.side == OrderSide.BUY and delta_price == best_bid_price:
            self._events.append(("bid", action_str, delta_size))
        elif delta.order.side == OrderSide.SELL and delta_price == best_ask_price:
            self._events.append(("ask", action_str, delta_size))

    def rate(self) -> CancelRate:
        def _pressure(side: str) -> float:
            added = sum(sz for s, a, sz in self._events if s == side and a == "add")
            deleted = sum(sz for s, a, sz in self._events if s == side and a == "delete")
            total = added + deleted
            if total == 0:
                return 0.0
            # (deleted - added) / total, so positive = net cancellation pressure
            return (deleted - added) / total

        return CancelRate(
            bid_pressure=_pressure("bid"),
            ask_pressure=_pressure("ask"),
        )


# =============================================================================================
# The bar-spaced gap marker (was three copies: routes/candles.py, the deleted
# indicator_series.py, indicators.py)
# =============================================================================================


# Known limit: a hole emits at most this many gap rows (Story 32.1), so a page carries at most
# (limit - 1) * MAX_GAP_ROWS_PER_GAP gap rows (720 slots = 12 h at 1m, 12 min in Lines mode). A
# longer hole is compressed to exactly this many rows, contiguous from its start; the frontend
# (`lib/gaps.ts`, which mirrors this value) detects the compression from the spacing between the
# last gap row and the next real row and labels it. Upgrade path: one gap row carrying `span_ms`,
# drawn as one wide band, so a hole of any length costs one row.
MAX_GAP_ROWS_PER_GAP = 720


def _gap_times(earlier_ms: int, later_ms: int, interval_ms: int) -> list[int]:
    """
    Return the gap slot times between two real rows: `earlier_ms + k * interval_ms`, k = 1, 2, ...
    while the time is strictly before `later_ms`, one per missing interval, capped at the first
    `MAX_GAP_ROWS_PER_GAP` (contiguous from the hole's start).
    """
    stop = min(later_ms, earlier_ms + (MAX_GAP_ROWS_PER_GAP + 1) * interval_ms)
    return list(range(earlier_ms + interval_ms, stop, interval_ms))


def with_gap_markers(rows: list[dict], bar_seconds: int) -> list[dict]:
    """
    Insert one explicit gap row `{"t": g}` per missing bar wherever two consecutive kept rows' `t`
    (ms) differ by more than one `bar_seconds` interval (AD-F6): `g` runs `earlier + k * bar_ms`
    (`_gap_times`, capped at `MAX_GAP_ROWS_PER_GAP`), so lightweight-charts' whitespace data
    renders the break starting right where real data stops and as wide as the hole really is.
    One rule for candles, indicator series and indicator values, because those panes share one
    time axis and must break in the same place.

    A gap between two *pages* is not seen here (only gaps strictly inside `rows`): that seam is the
    frontend's own check across two pages (`useCandles.ts`).
    """
    bar_ms = bar_seconds * 1000
    out: list[dict] = []
    for i, row in enumerate(rows):
        if i > 0 and row["t"] - rows[i - 1]["t"] > bar_ms:
            out.extend({"t": g} for g in _gap_times(rows[i - 1]["t"], row["t"], bar_ms))
        out.append(row)
    return out


# Hard cap on any single query's total time span (MEM-01), independent of the limit/bar_seconds
# product that produced it -- `limit * bar_seconds * multiplier` alone would let a single request
# pull years of raw 1-second snapshots, defeating the bounded-read guarantee the pages provide.
MAX_QUERY_SPAN_SECONDS = 7 * 86_400


# =============================================================================================
# Lines mode: per-second price rows (was data_api/routes/snapshots.py)
# =============================================================================================

# A gap this wide between two consecutive archived seconds means the collector's staleness guard
# skipped stale books during a WS reconnect (DATA-01) -- render it as an explicit break, never an
# interpolated flat line (AD-F6).
SNAPSHOT_GAP_THRESHOLD_MS = 2500

# How many multiples of `limit` (in seconds, since rows are ~1/second) to look back for the main
# query window -- real snapshot coverage has gaps (thin trading, collector downtime), so a 1x window
# can come up short of `limit` rows even when enough history exists a bit further back.
_SNAPSHOT_WINDOW_MULTIPLIER = 2


class EmptyTopOfBook(Exception):
    """
    An archived second with an empty bid or ask side. Unlike a crossed second it cannot be drawn at
    all -- there is no bid or ask to plot, and any substitute would fabricate a value (DATA-01) --
    and the capture gate (`collector.py`'s `_sample_instrument`) never writes one, so its presence
    is a malfunction upstream (DATA-07): ledgered and raised, never skipped.
    """


def _gap_row(t: int) -> dict:
    return {
        "t": t,
        "bid_units": None,
        "ask_units": None,
        "price_precision": None,
        "mid": None,
        "micro": None,
        "price": None,
    }


def _require_top(snapshot: DydxSecondSnapshot) -> None:
    if snapshot.bid_prices and snapshot.ask_prices:
        return
    detail = (
        f"snapshot without a top of book for {snapshot.instrument_id.value} at "
        f"ts_event={snapshot.ts_event}: bid levels={len(snapshot.bid_prices)}, "
        f"ask levels={len(snapshot.ask_prices)}"
    )
    error_ledger.record("views.snapshot_without_top", detail)
    raise EmptyTopOfBook(detail)


def price_series_rows(snapshots: Sequence[DydxSecondSnapshot]) -> list[dict]:
    """
    Build `{t, bid_units, ask_units, price_precision, mid, micro, price}` rows from time-ordered
    snapshots, one per second, with one gap row (`_gap_row`, every value null) per missing second
    (`_gap_times` at 1000 ms, capped at `MAX_GAP_ROWS_PER_GAP`) between two seconds more than
    `SNAPSHOT_GAP_THRESHOLD_MS` apart; 2 s spacing is not a gap. `micro` is null for a second
    whose microprice is undefined (both top sizes zero): no mid stands in for it (Story 31.3).

    The best bid/ask travel as the stored exact integers and their precision (Story 30.2: values a
    machine moves stay integers; the frontend's `lib/units.ts` formats them for display). `mid`,
    `micro` and `price` are derived signals, computed from the decoded floats, and stay floats.

    price = CVD-weighted effective trade price: skews from mid toward ask on net buying, toward bid
    on net selling. Equals mid when no trades occurred in that second. Timestamps are milliseconds
    (matching the candle pages' `t` unit, so both series land on the same chart time axis).

    Every archived second is priced as written, a crossed or touched one (`bid >= ask`) exactly
    like any other (its skew term `(ask - bid)` is then negative, as written): the reader does not
    re-validate the archive (AD-3). Today's gate never writes a crossed second
    (`SecondSampler` rejects it as `Crossed`; a central book's episode is ledgered
    `collector.crossed_book`); one the archive holds
    anyway predates that gate or is a capture bug, whose fix is at the gate or through
    `repair_catalog`, never a reader-side filter (DATA-07). A second with an empty side cannot be
    drawn at all and raises `EmptyTopOfBook` (see `_require_top`).
    """
    rows: list[dict] = []
    prev_ts_ms: int | None = None
    for s in snapshots:
        _require_top(s)
        bp, ap = s.bid_prices[0], s.ask_prices[0]
        curr_ts_ms = s.ts_event // 1_000_000
        if prev_ts_ms is not None and (curr_ts_ms - prev_ts_ms) > SNAPSHOT_GAP_THRESHOLD_MS:
            rows.extend(_gap_row(g) for g in _gap_times(prev_ts_ms, curr_ts_ms, 1000))
        prev_ts_ms = curr_ts_ms
        floats = s.as_floats()
        # The kernel's one mid (SSOT-01, audit D-131), never a second inline copy of it.
        mid = calc_mid_price(floats)
        assert mid is not None  # _require_top above refused an empty side
        # None when undefined (zero top sizes): never the mid passed off as a microprice (DATA-01).
        micro = calc_microprice(floats)
        tv = s.buy_volume + s.sell_volume
        if tv > 0:
            price = mid + ((s.buy_volume - s.sell_volume) / tv) * (ap - bp) * 0.5
        else:
            price = mid
        rows.append(
            {
                "t": curr_ts_ms,
                "bid_units": s.bid_price_units[0],
                "ask_units": s.ask_price_units[0],
                "price_precision": s.price_precision,
                "mid": mid,
                "micro": micro,
                "price": price,
            }
        )
    return rows


def _snapshot_window_start_ns(before_ns: int, limit: int) -> int:
    span_seconds = min(limit * _SNAPSHOT_WINDOW_MULTIPLIER, MAX_QUERY_SPAN_SECONDS)
    return before_ns - span_seconds * 1_000_000_000


def _take_last_n_real_rows(rows: list[dict], limit: int) -> list[dict]:
    """
    Slice to the most recent `limit` REAL rows (gap markers, `bid_units is None`, are structural
    breaks, not data, and must never eat into the requested row budget) -- mirrors
    `candle_page`'s order of operations (slice to `limit` real candles first, insert gap
    markers into the kept slice second), adapted for `price_series_rows`' shape, which
    already interleaves markers with real rows in one pass.

    Slicing at a real row's own index (never mid-window) also means any gap run sitting
    immediately before the new earliest-kept real row is naturally dropped whole -- the same
    "no boundary gap at the very edge of a page" behavior `with_gap_markers` has (it only
    checks gaps strictly inside its own rows; a page-boundary gap is instead the frontend's
    own seam check across two pages, see `useCandles.ts`).
    """
    real_indices = [i for i, r in enumerate(rows) if r["bid_units"] is not None]
    if not real_indices:
        return []
    start_index = real_indices[-limit] if len(real_indices) > limit else 0
    return rows[start_index:]


def snapshot_series_page(
    catalog_path: str, instrument_id: str, before_ns: int, limit: int
) -> tuple[list[dict], bool]:
    """
    One Lines-mode page: `(rows oldest-first, has_more)` for the `limit` archived seconds before
    `before_ns`, gap rows included (never counted toward `limit`). Raises `EmptyTopOfBook`.

    The snapshots are cut at `before` *before* rows are built, so a page always ends on a real
    row: filtering rows afterwards would keep the gap run whose closing real second was the
    cursor itself.
    """
    before_ms = before_ns // 1_000_000

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        snapshots = query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns)
        kept = [s for s in snapshots if s.ts_event // 1_000_000 < before_ms]
        rows = price_series_rows(sorted(kept, key=lambda s: s.ts_event))
        return _take_last_n_real_rows(rows, limit)

    ranges = catalog_files.data_file_ranges(catalog_path, instrument_id)
    span_ns = before_ns - _snapshot_window_start_ns(before_ns, limit)
    kept = fetch_page(fetch, ranges, before_ns, span_ns)
    if not kept:
        return [], False
    return kept, has_older_data(ranges, kept[0]["t"] * 1_000_000)


# =============================================================================================
# Candles (was data_api/routes/candles.py)
# =============================================================================================

# How many multiples of `limit * bar_seconds` to look back for the archive query window.
# Real second-snapshot coverage has gaps (thin trading, collector downtime), so a 1x
# window can come up short of `limit` candles even when enough history exists a bit
# further back -- 3x gives headroom without unbounding the read (still a fixed multiple
# of a bounded window, never open-ended).
_CANDLE_WINDOW_MULTIPLIER = 3

# Floor on the archive query window for sub-minute bars. A bar only exists for a second that
# traded, so at 1s/5s the `limit * bar_seconds * 3` window (6 min at 1s) can hold 0-1 candles on a
# quiet coin -- the chart then has nothing to scroll and never refills. An hour of raw 1s is only
# ~3.6k rows.
_MIN_CANDLE_WINDOW_SECONDS = 3600

# The live tail the collector has not flushed to the catalog yet (`LiveCandleBus.recent_rows`).
RecentRows = Callable[[str, int, int], list[SecondOHLC]]
# Its liquidation twin (`LiveCandleBus.recent_liquidations`, Story 33.3).
RecentLiquidations = Callable[[str, int, int], list[Liquidation]]


class ImpossibleCandle(Exception):
    """
    A candle failing `is_valid_candle`: upstream code malfunctioned (DATA-07). Never served,
    never dropped quietly -- ledgered at `candles.invalid_candle` and raised, so the chart shows
    an error.
    """


class CandleReadError(Exception):
    """Reading the candle window failed (a missing/corrupt catalog, an I/O error)."""


def _catalog_plus_recent(
    catalog_path: str, recent_rows: RecentRows, instrument_id: str, start_ns: int, end_ns: int
) -> list[SecondOHLC]:
    """Catalog rows plus the live tail the collector has not flushed yet (see live_candles.RECENT_SECONDS)."""
    rows = catalog_files.query_second_ohlc(catalog_path, instrument_id, start_ns, end_ns)
    have = {r.ts_event for r in rows}
    tail = recent_rows(instrument_id, start_ns, end_ns)
    return rows + [r for r in tail if r.ts_event not in have]


def liquidations_plus_recent(
    catalog_path: str,
    recent_liquidations: RecentLiquidations,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
) -> list[Liquidation]:
    """
    Return the archived liquidations of the window plus the live tail the collector has not
    flushed yet, each venue event once (`venue_event_id`, D-150): the archive's copy wins, a tail row the
    archive already holds is not added again.
    """
    rows = catalog_files.query_liquidations(catalog_path, instrument_id, start_ns, end_ns)
    have = {row.venue_event_id for row in rows}
    tail = recent_liquidations(instrument_id, start_ns, end_ns)
    fresh = {row.venue_event_id: row for row in tail if row.venue_event_id not in have}
    return rows + list(fresh.values())


def _candle_window_span_ns(limit: int, bar_seconds: int) -> int:
    """
    Return the archive query window's span: `limit * bar_seconds * _CANDLE_WINDOW_MULTIPLIER` (at
    least `_MIN_CANDLE_WINDOW_SECONDS` below 1m), capped at `MAX_QUERY_SPAN_SECONDS`, then rounded
    down to whole buckets and never below one (Story 31.8). A window that starts inside a bucket
    folds that bucket from only its later seconds and serves it with no marker, so the span is
    always a whole number of buckets. One bucket wins over the cap: a bucket can only be read
    whole, and the route clamps `bar_seconds` to 1W, which equals the cap.

    Known limit: a 1W page from Parquet therefore holds at most one week per request (the cap is
    one week), so the chart pages a 1W history back one bar at a time; upgrade path: compose the
    read-time widths (10m, 30m, 45m, 1W) from stored bars instead of raw seconds.
    """
    span_seconds = limit * bar_seconds * _CANDLE_WINDOW_MULTIPLIER
    if bar_seconds < 60:
        span_seconds = max(span_seconds, _MIN_CANDLE_WINDOW_SECONDS)
    span_seconds = min(span_seconds, MAX_QUERY_SPAN_SECONDS)
    return max(1, span_seconds // bar_seconds) * bar_seconds * 1_000_000_000


def stored_bar(bar_seconds: int) -> int | None:
    """
    Return the widest candle-store width (`candles.domain.fold.BAR_SECONDS`) whose buckets tile a
    `bar_seconds` bucket exactly -- it divides the width and the width's anchor (`bucket_start_ms`:
    1W starts on a Monday) lies on its boundaries, so every wide bucket is a whole set of stored
    ones (1W from 1D, 10m from 5m) -- or None when none does (1..59 s, 90 s). A stored width is
    its own. The one rule (SSOT-02) of every reader composing a width from stored bars:
    `views.derivatives` and the CVD `all` anchor (`views.indicator_picker`).
    """
    anchor_ms = bucket_start_ms(0, bar_seconds)
    for width in sorted(BAR_SECONDS, reverse=True):
        if bar_seconds % width == 0 and anchor_ms % (width * 1000) == 0:
            return width
    return None


def bucket_end_ns(ts_ns: int, bar_seconds: int) -> int:
    """
    `ts_ns` rounded up to a bucket boundary (itself when it is one), by the one bucket rule
    (`candles.domain.fold.bucket_start_ms`: a 1W bucket starts on Monday).
    """
    ceil_ms = -(-ts_ns // 1_000_000)
    start_ms = bucket_start_ms(ceil_ms, bar_seconds)
    end_ms = start_ms if start_ms == ceil_ms else start_ms + bar_seconds * 1000
    return end_ms * 1_000_000


def _checked(instrument_id: str, bar_seconds: int, c: dict) -> dict:
    # An impossible candle means upstream code malfunctioned (DATA-07): never serve it,
    # never drop it quietly -- fail the request so the chart shows an error, and count it.
    if not is_valid_candle(c):
        detail = f"impossible candle for {instrument_id} (bar_seconds={bar_seconds}): {c!r}"
        error_ledger.record("candles.invalid_candle", detail)
        raise ImpossibleCandle(detail)
    return c


def _parquet_page(
    instrument_id: str,
    before_ns: int,
    limit: int,
    bar_seconds: int,
    catalog_path: str,
    candles_dir: str,
    recent_rows: RecentRows,
    recent_liquidations: RecentLiquidations,
) -> tuple[list[dict], bool]:
    """
    One page straight from the Parquet archive (slow: reads a window of tiny files). Serves
    history the candle store does not hold (older than its first day, or pruned). An instrument
    with a liquidation feed folds the window's archived liquidations plus the live tail
    (`liquidations_plus_recent`), bounded by the feed's start: the candle store's persisted start,
    else the archive's first liquidation (`views.catalog_reads.liquidation_feed_start`), lowered
    to the earliest row folded, so a live-tail
    row older than the archive moves the bound instead of failing the page. A bar starting before
    that start, or straddling it, or every bar when no start is known, gets null `liq_*`, never 0
    (audit D-160). One without the feed gets null `liq_*` columns throughout (Story 33.3).

    Every query window -- the first and each gap jump -- starts on a bucket boundary (Story 31.8):
    its end is rounded up to a bucket boundary and its span is whole buckets, so no served bar is
    folded from only its *later* seconds. No read ever reaches `before_ns` itself: the bucket
    holding the cursor is folded from its seconds before `before_ns` only, never from later ones
    (no look-ahead for a historical cursor), and carries `partial` by the counted
    `seconds_observed` rule when that is under 90 % of its span. On the chart's first page
    `before_ns` is now, so that is the forming bucket as observed so far.
    """
    before_ms = before_ns // 1_000_000
    rows_fn = partial(_catalog_plus_recent, catalog_path, recent_rows)
    liquidations_fn = None
    since_ns = None
    if has_liquidation_feed(instrument_id):
        since_ns = liquidation_feed_start(catalog_path, candles_dir, instrument_id)
        liquidations_fn = partial(liquidations_plus_recent, catalog_path, recent_liquidations)

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        # The readers' windows are inclusive; a row stamped exactly at `end_ns` opens the next
        # bucket, which this window must not serve as a one-second bar, and a row at or after
        # `before_ns` lies past the cursor, which no page may fold (look-ahead).
        return [
            c
            for c in queries.candle_dicts_for_window(
                instrument_id,
                start_ns,
                min(end_ns, before_ns) - 1,
                bar_seconds,
                snapshot_rows_fn=rows_fn,
                liquidation_rows_fn=liquidations_fn,
                liquidations_since_ns=since_ns,
            )
            if c["t"] < before_ms and _checked(instrument_id, bar_seconds, c)
        ]

    ranges = catalog_files.data_file_ranges(catalog_path, instrument_id)
    span_ns = _candle_window_span_ns(limit, bar_seconds)
    align = partial(bucket_end_ns, bar_seconds=bar_seconds)
    kept = fetch_page(fetch, ranges, before_ns, span_ns, align_end=align)[-limit:]
    return kept, bool(kept) and has_older_data(ranges, kept[0]["t"] * 1_000_000)


def _store_page(
    instrument_id: str, before_ns: int, limit: int, bar_seconds: int, candles_dir: str
) -> tuple[list[dict], bool, int | None]:
    """
    One page from the SQLite candle store (an indexed read, no Parquet I/O): `(candles,
    store_has_more, start of the store's coverage in ms)`. Empty when the store is missing or has
    nothing for this coin -- including a `bar_seconds` the store does not keep (600 s, 1 w, ...),
    which has no rows and so no coverage, the same result the old `BAR_SECONDS` pre-check gave.
    """
    with queries.open_store(candles_dir, venue_of(instrument_id)) as db:
        if db is None:
            return [], False, None
        kept = queries.window(db, instrument_id, bar_seconds, before_ns // 1_000_000, limit)
        if not kept:
            return (
                [],
                False,
                queries.oldest_t(db, instrument_id, bar_seconds, traded_only=False),
            )
        oldest = queries.oldest_t(db, instrument_id, bar_seconds)
        coverage = queries.oldest_t(db, instrument_id, bar_seconds, traded_only=False)
        return (
            [_checked(instrument_id, bar_seconds, c) for c in kept],
            oldest is not None and oldest < kept[0]["t"],
            coverage,
        )


def candle_page(
    instrument_id: str,
    before_ns: int,
    limit: int,
    bar_seconds: int,
    *,
    catalog_path: str,
    candles_dir: str,
    recent_rows: RecentRows,
    recent_liquidations: RecentLiquidations,
) -> tuple[list[dict], bool]:
    """
    Return the one candle source for the chart, its indicator panes and anything else that must agree
    with them: `(candles oldest-first, has_more)` for the `limit` bars before `before_ns`, without
    gap rows (`with_gap_markers` is the renderer's step). Reads the SQLite candle store, and only
    what it does not cover (history older than its first bucket, or pruned) from Parquet -- never
    when the store already reaches the archive's first file. Raises `ImpossibleCandle`.
    """
    kept, store_has_more, coverage_ms = _store_page(
        instrument_id, before_ns, limit, bar_seconds, candles_dir
    )
    if store_has_more:
        return kept, True
    ranges = catalog_files.data_file_ranges(catalog_path, instrument_id)  # a directory listing
    if coverage_ms is not None and not has_older_data(ranges, coverage_ms * 1_000_000):
        return kept, False
    if len(kept) >= limit:
        return kept, True  # older archive history exists beyond this full page
    older_before_ns = kept[0]["t"] * 1_000_000 if kept else before_ns
    older, has_more = _parquet_page(
        instrument_id,
        older_before_ns,
        limit - len(kept),
        bar_seconds,
        catalog_path,
        candles_dir,
        recent_rows,
        recent_liquidations,
    )
    return older + kept, has_more


# =============================================================================================
# Indicator values: the picker's configured indicators over the chart's own candles
# (was data_api/routes/indicators.py)
# =============================================================================================


def indicator_values_page(
    instrument_id: str,
    before_ns: int,
    limit: int,
    bar_seconds: int,
    entries: Sequence["IndicatorRequest"],
    *,
    catalog_path: str,
    candles_dir: str,
    recent_rows: RecentRows,
    recent_liquidations: RecentLiquidations,
) -> tuple[list[dict], bool, dict[str, str]]:
    """
    `(rows with gap rows, has_more, errors)`: every requested indicator replayed over the same
    `candle_page` the chart shows, so a pane can never disagree with its candles. A row is
    `{t, values: {"{indicator_id}.{output}": value}}`; a gap row carries only `t`. `errors` maps
    the `indicator_id` of each entry whose replay failed to its message -- the other entries are
    still served (one bad or stale entry must not blank every pane).

    Always a bounded *historical* replay of `[first candle, last candle + 1 bar)`; the candlestick's
    live edge has its own path (`views.live_candles`). A custom indicator whose instrument never
    opted into raw-delta capture yields `None` per point, not an error. Raises `ImpossibleCandle`,
    and `CandleReadError` for any other failure reading the candles.
    """
    # Imported here, not at module level: `indicator_picker` imports this module's
    # `CancellationTracker`, so a top-level import would be circular.
    from views import indicator_picker

    try:
        kept, has_more = candle_page(
            instrument_id,
            before_ns,
            limit,
            bar_seconds,
            catalog_path=catalog_path,
            candles_dir=candles_dir,
            recent_rows=recent_rows,
            recent_liquidations=recent_liquidations,
        )
    except ImpossibleCandle:
        raise
    except Exception as exc:
        raise CandleReadError(str(exc)) from exc
    if not kept:
        return [], False, {}
    end_ms = kept[-1]["t"] + bar_seconds * 1000
    window = indicator_picker.ReplayWindow(
        instrument_id=instrument_id,
        bar_seconds=bar_seconds,
        # MEM-01: the raw-delta replays read raw deltas over this window, so it is capped at
        # `MAX_QUERY_SPAN_SECONDS` back from the end (500 1W bars would be ~10 years). A bar before
        # the cap has no input read, so its custom value is None -- a gap, never a fabricated one.
        # Known limit: at 1D only the last 7 bars, and at 1W only the last bar, carry a cancel
        # pressure or delta OFI value; upgrade path: a stored per-bar aggregate of these inputs
        # (like the candle store's order flow), read instead of replaying raw rows. CVD and Story
        # 33.6's order-flow entries read the bars' own stored aggregates (Story 33.3), so every bar
        # of every width carries them; `DepthWithinBps` applies this same cap inside its replay,
        # since the Technicals' window is not capped.
        start_ms=max(kept[0]["t"], end_ms - MAX_QUERY_SPAN_SECONDS * 1000),
        end_ms=end_ms,
        candles_dir=candles_dir,
    )
    by_time, errors = indicator_picker.values_by_time(kept, entries, window)
    rows = [{"t": t, "values": values} for t, values in sorted(by_time.items())]
    return with_gap_markers(rows, bar_seconds), has_more, errors


# =============================================================================================
# Volume footprint: the raw trade archive bucketed per closed bar (Story 32.8)
#
# Executed trades (the resting-order-flow footprint it replaced was deleted in Story
# 33.4), read as integer units from the `TradeTick` archive (`kernel.catalog_files.query_trade_columns`)
# and bucketed into integer price rows over the chart's own `candle_page` bars, so its bar
# boundaries, gaps and timeframe are the candles' own (no second bucket rule).
#
# Known limit (historical only): only closed, settled bars are served -- the forming bar and the
# last `FOOTPRINT_SETTLE_SECONDS` never show a footprint, because the archive holds a trade only
# after the collector's flush. Upgrade path: fold the `trades` Redis stream in the candles context
# (one owner, like the live candle), never a footprint fabricated from the 1 s snapshot's
# buy/sell totals.
# =============================================================================================

# Server-enforced cap on bars per footprint page (MEM-01 on the API surface; silently clamped).
MAX_FOOTPRINT_BARS = 200

# Auto row size: the smallest `row_ticks` giving at most this many rows over a bar's trade range.
FOOTPRINT_MAX_ROWS_AUTO = 24

# A fixed row size never lays out more rows than this over one bar's trade range: past it the bar
# is bucketed at the smallest multiple of the asked size that fits, and carries that `row_ticks`
# (each bar states its own size, so nothing is silently re-gridded). 480 rows of the 1-tick case
# fit; an unbounded fixed size would put one row per traded tick of a wide 4h/1W bar on the wire.
FOOTPRINT_MAX_ROWS_FIXED = 1000

# A bar is served once `t + bar <= now - FOOTPRINT_SETTLE_SECONDS`: the reconnect trade backfill can
# still archive a trade up to `MAX_TS_INIT_SKEW_NS` (300 s) after its `ts_event`, and that write
# lands within two collector flushes (60 s each, the default `flush_interval_seconds`), so by then
# every trade of the bar is archived.
# Known limit: tied to the 60 s default flush; a longer flush interval needs a longer settle, or a
# bar settles with trades still unflushed. Upgrade path: the archive publishes a per-instrument
# flushed-through watermark and the settle reads it.
FOOTPRINT_SETTLE_SECONDS = MAX_TS_INIT_SKEW_NS // NS_PER_S + 2 * 60

# The largest integer a JSON number carries exactly into the browser (`Number.MAX_SAFE_INTEGER`).
# Known limit: a price, size, delta or total past it raises `FootprintOverflow` (ledgered) rather
# than reaching the chart rounded. Upgrade path: serialise the units as decimal strings.
MAX_JSON_SAFE_INT = 2**53 - 1

_NS_PER_S = 1_000_000_000
_NS_PER_DAY = 86_400 * _NS_PER_S


class FootprintOverflow(Exception):
    """A footprint value a JSON number cannot carry exactly (DATA-07: ledgered, never rounded)."""


@dataclass(frozen=True)
class FootprintBar:
    """
    One bar's footprint. `rows` are `{p, b, s}` (row floor price units, buy and sell size units),
    ascending `p`, only rows that traded. A bar the archive holds no trade for is a gap:
    `no_trades`, `rows` empty, `row_ticks`/`delta`/`total`/`poc_row` None -- never zeros as data.
    """

    t: int  # bar start, ms
    row_ticks: int | None
    rows: list[dict[str, int]]
    delta: int | None
    total: int | None
    poc_row: int | None
    no_trades: bool

    def as_dict(self) -> dict:
        return {
            "t": self.t,
            "row_ticks": self.row_ticks,
            "rows": self.rows,
            "delta": self.delta,
            "total": self.total,
            "poc_row": self.poc_row,
            "no_trades": self.no_trades,
        }


# Per bar: price units -> [buy units, sell units], Python ints (exact, unbounded).
_Levels = dict[int, list[int]]


def auto_row_ticks(low: int, high: int) -> int:
    """Return the smallest `row_ticks >= 1` with `high // rt - low // rt + 1 <= FOOTPRINT_MAX_ROWS_AUTO`."""
    return _fitting_row_ticks(low, high, 1, FOOTPRINT_MAX_ROWS_AUTO)


def _fitting_row_ticks(low: int, high: int, step: int, max_rows: int) -> int:
    """Return the smallest multiple of `step` laying `[low, high]` out in at most `max_rows` rows."""
    k = max(1, -(-(high - low + 1) // (max_rows * step)))
    while high // (k * step) - low // (k * step) + 1 > max_rows:
        k += 1
    return k * step


def _checked_int(instrument_id: str, t_ms: int, name: str, value: int) -> int:
    if abs(value) > MAX_JSON_SAFE_INT:
        detail = (
            f"footprint {name} {value} for {instrument_id} at bar t={t_ms} exceeds the JSON-safe "
            f"integer range"
        )
        error_ledger.record("views.footprint_overflow", detail)
        raise FootprintOverflow(detail)
    return value


def footprint_bar(
    instrument_id: str, t_ms: int, levels: _Levels, row_ticks: int | None
) -> FootprintBar:
    """
    Bucket one bar's per-price buy/sell units into rows `units // rt * rt` (a grid aligned to
    multiples of `rt`, stable across bars at a fixed size); `row_ticks` None = auto, a fixed size
    coarsened to a multiple past `FOOTPRINT_MAX_ROWS_FIXED` rows. POC = the largest `b + s`, ties
    to the lower `p`.
    """
    if not levels:
        return FootprintBar(t_ms, None, [], None, None, None, no_trades=True)
    low, high = min(levels), max(levels)
    if row_ticks is None:
        rt = auto_row_ticks(low, high)
    else:
        rt = _fitting_row_ticks(low, high, row_ticks, FOOTPRINT_MAX_ROWS_FIXED)
    buckets: dict[int, list[int]] = {}
    for price, (buy, sell) in levels.items():
        row = buckets.setdefault(price // rt * rt, [0, 0])
        row[0] += buy
        row[1] += sell
    check = partial(_checked_int, instrument_id, t_ms)
    rows = [
        {"p": check("p", p), "b": check("b", b), "s": check("s", s)}
        for p, (b, s) in sorted(buckets.items())
    ]
    buys = sum(r["b"] for r in rows)
    sells = sum(r["s"] for r in rows)
    poc = min(rows, key=lambda r: (-(r["b"] + r["s"]), r["p"]))["p"]
    return FootprintBar(
        t_ms, rt, rows, check("delta", buys - sells), check("total", buys + sells), poc, False
    )


def day_slices(start_ns: int, end_ns: int) -> Iterator[tuple[int, int]]:
    """Split `[start_ns, end_ns)` at UTC midnights (MEM-01: one instrument-day read at a time)."""
    lo = start_ns
    while lo < end_ns:
        hi = min(end_ns, (lo // _NS_PER_DAY + 1) * _NS_PER_DAY)
        yield lo, hi
        lo = hi


def _fold_slice(
    columns: catalog_files.TradeColumns, starts_ns: np.ndarray, bar_ns: int, acc: list[_Levels]
) -> None:
    """
    Add one slice's trades to the per-bar accumulators: a trade belongs to the bar whose
    `[t, t + bar)` holds its `ts_event`; one in no served bar (a candle gap) is not drawn. A
    non-BUYER aggressor counts as a sell (`kernel.fold.fold_trades`' rule, so totals equal the
    candle's volume).
    """
    idx = np.searchsorted(starts_ns, columns.ts_event, side="right") - 1
    inside = idx >= 0
    inside[inside] = columns.ts_event[inside] < starts_ns[idx[inside]] + bar_ns
    idx, price = idx[inside], columns.price[inside]
    size, buyer = columns.size[inside], columns.buyer[inside]
    if not len(idx):
        return
    order = np.lexsort((price, idx))
    idx, price, size, buyer = idx[order], price[order], size[order], buyer[order]
    first = np.flatnonzero(np.r_[True, (np.diff(idx) != 0) | (np.diff(price) != 0)])
    # Each group's sums are int64: the slice's whole volume is bounded first, so none can wrap.
    buys = np.add.reduceat(np.where(buyer, size, 0), first)
    sells = np.add.reduceat(np.where(buyer, 0, size), first)
    for k, i in enumerate(first):
        level = acc[idx[i]].setdefault(int(price[i]), [0, 0])
        level[0] += int(buys[k])
        level[1] += int(sells[k])


def _require_int64_sum(instrument_id: str, size: np.ndarray) -> None:
    """
    Refuse a slice whose total size units could wrap an int64 sum (`_fold_slice`'s precondition).
    The float64 sum of non-negative units is within a relative 1e-12 of the exact one, so a total
    under 2^62 leaves int64 (2^63) a wide margin; only a slice truly near the limit is refused.
    """
    if len(size) and float(size.sum(dtype=np.float64)) >= 2.0**62:
        detail = f"footprint size units of {instrument_id} could overflow an int64 sum"
        error_ledger.record("views.footprint_overflow", detail)
        raise FootprintOverflow(detail)


def _read_trades(
    instrument_id: str, catalog_path: str, lo: int, hi: int, precision: tuple[int, int]
) -> catalog_files.TradeColumns:
    try:
        columns = catalog_files.query_trade_columns(catalog_path, instrument_id, lo, hi, *precision)
    except catalog_files.TradeDecodeError as exc:
        error_ledger.record("views.footprint_trade_decode", str(exc), exc)
        raise
    _require_int64_sum(instrument_id, columns.size)
    return columns


def _settled_bars(candles: list[dict], bar_seconds: int, end_ns: int) -> tuple[list[int], bool]:
    """
    Return the closed bars' starts (ms) ending by `end_ns`, trimmed to the newest
    `MAX_QUERY_SPAN_SECONDS` of span (MEM-01), and whether the trim dropped one.
    """
    bar_ms = bar_seconds * 1000
    closed = [c["t"] for c in candles if (c["t"] + bar_ms) * 1_000_000 <= end_ns]
    if not closed:
        return [], False
    floor_ms = closed[-1] + bar_ms - MAX_QUERY_SPAN_SECONDS * 1000
    kept = [t for t in closed if t >= floor_ms]
    return kept, len(kept) < len(closed)


def footprint_page(
    instrument_id: str,
    before_ns: int,
    limit: int,
    bar_seconds: int,
    row_ticks: int | None,
    *,
    catalog_path: str,
    candles_dir: str,
    recent_rows: RecentRows,
    recent_liquidations: RecentLiquidations,
    price_precision: int,
    size_precision: int,
    now_ns: int,
) -> tuple[list[dict], bool]:
    """
    `(bars oldest-first, has_more)`: the footprint (`FootprintBar.as_dict`) of every closed,
    settled bar of the same `candle_page` the chart shows (`limit` clamped to `MAX_FOOTPRINT_BARS`),
    from the archive's trades in integer units at the definition's precisions. `row_ticks` None =
    auto per bar. Raises `ImpossibleCandle`, `TradeDecodeError` and `FootprintOverflow` (each
    ledgered).

    Known limit (MEM-01): trades are read one UTC day slice at a time and folded into per-bar
    accumulators before the next, so peak memory is one instrument-day of trades (~65 MB per 800k
    Bybit BTC trades). Upgrade path: row-group streaming, or a stored per-bar footprint folded by
    the candles context.
    """
    if row_ticks is not None and row_ticks < 1:
        raise ValueError(f"row_ticks must be at least 1 (or None for auto), got {row_ticks}")
    limit = max(1, min(limit, MAX_FOOTPRINT_BARS))
    # The page ends where settled bars end, so `limit` counts bars that can be served (a small
    # refresh page is not all forming and unsettled bars). The bar holding `end_ns` is not closed
    # yet and is dropped, so one bar more is asked for and the oldest one past `limit` is cut.
    end_ns = min(before_ns, now_ns - FOOTPRINT_SETTLE_SECONDS * _NS_PER_S)
    candles, has_more = candle_page(
        instrument_id,
        end_ns,
        limit + 1,
        bar_seconds,
        catalog_path=catalog_path,
        candles_dir=candles_dir,
        recent_rows=recent_rows,
        recent_liquidations=recent_liquidations,
    )
    starts_ms, trimmed = _settled_bars(candles, bar_seconds, end_ns)
    if len(starts_ms) > limit:
        starts_ms, trimmed = starts_ms[-limit:], True
    if not starts_ms:
        return [], has_more or trimmed
    bar_ns = bar_seconds * _NS_PER_S
    starts_ns = np.asarray(starts_ms, dtype=np.int64) * 1_000_000
    acc: list[_Levels] = [{} for _ in starts_ms]
    precision = (price_precision, size_precision)
    for lo, hi in day_slices(int(starts_ns[0]), int(starts_ns[-1]) + bar_ns):
        columns = _read_trades(instrument_id, catalog_path, lo, hi, precision)
        _fold_slice(columns, starts_ns, bar_ns, acc)
    bars = [
        footprint_bar(instrument_id, t, levels, row_ticks).as_dict()
        for t, levels in zip(starts_ms, acc, strict=True)
    ]
    return bars, has_more or trimmed
