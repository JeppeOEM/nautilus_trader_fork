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
- **Indicator panes** -- `indicator_series_page` (OFI/OBI/microprice/spread per bar) and
  `indicator_values_page` (the picker's configured indicators, dispatched through
  `views.indicator_picker`).
- **Book features and footprint** -- the L2 feature extraction (`depth_profile` over an `OrderBook`,
  returning the kernel's `DepthProfile` (Story 27.3 moved the type and the snapshot -> depth
  derivation, `snapshot_depth`, to `kernel.indicators`), `book_imbalance`,
  `CancellationTracker`, ...), `compute_chart_series` and `build_footprint`.

Two rendering rules, and nothing else, change what is drawn: `with_gap_markers` (one
`{"t": earlier + bar_ms}` row wherever two kept bars are more than a bar apart, shared by candles,
indicator series and indicator values so panes on one time axis break in the same place) and the
Lines-mode gap marker (one all-`None` row at `later - 1` where two seconds are more than
`SNAPSHOT_GAP_THRESHOLD_MS` apart -- DATA-01's honest break). The reader never re-validates the
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

from candles.application import queries
from candles.domain.candle import Candle
from candles.domain.candle import is_valid_candle
from kernel import catalog_files
from kernel.indicators import DepthProfile
from kernel.indicators import MultiLevelOBI
from kernel.indicators import MultiLevelOFI
from kernel.indicators import microprice as calc_microprice
from kernel.indicators import snapshot_depth
from kernel.indicators import spread as calc_spread
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from kernel.venues import venue_of
from observability import error_ledger

from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from views.catalog_reads import fetch_page
from views.catalog_reads import has_older_data
from views.catalog_reads import query_second_snapshots


if TYPE_CHECKING:  # the runtime import is deferred: indicator_picker imports this module
    from views.indicator_picker import IndicatorRequest


# =============================================================================================
# Book features (was the book-features module)
#
# L2 order book feature extraction for dYdX.
#
# NOTE on what dYdX L2 can and cannot tell you:
#   - dYdX sends aggregated price-level data (order_id=0 for every BookOrder).
#   - Each BookLevel has exactly one synthetic BookOrder = the total size at that price.
#   - Queue composition (how many individual orders make up a level) is NOT available.
#   - Large single-order presence within a level is NOT available.
#   - Both require L3/MBO data which dYdX does not expose publicly.
#
# What IS available and implemented here:
#   - Depth profile levels 1-10 (sizes and prices on both sides)
#   - Book imbalance per level and aggregate across levels 1-10
#   - Volume-weighted price distance to liquidity (how far 80% of depth sits)
#   - Cancellation rate at best levels (tracked via delta ADD/DELETE actions)
# =============================================================================================


def top_of_book_series(
    deltas: list[OrderBookDelta],
    instrument_id: InstrumentId,
) -> Iterator[tuple[int, float, float, float, float]]:
    """
    Yield (ts_event, bid_price, bid_size, ask_price, ask_size) after each delta.

    Skips crossed/touched states (bid >= ask): dYdX's venue feed can transiently
    cross mid-replay (validator ack delays -- see collector.py's crossed-book
    resync watchdog), and yielding that state here would feed a negative spread
    straight into OFI/microprice. Mirrors the guard in collector._second_loop.

    This is not the reader-side re-validation AD-3 bans (and Story 24.2 removed from the chart
    pages): the input is raw `OrderBookDelta`s, which no capture gate has seen, so this replay
    *is* the gate for its own research/backtest output, applying the same rule the collector's.
    """
    book = OrderBook(instrument_id, book_type=BookType.L2_MBP)
    for delta in sorted(deltas, key=lambda d: d.ts_init):
        book.apply_delta(delta)
        bid_price = book.best_bid_price()
        ask_price = book.best_ask_price()
        if bid_price is None or ask_price is None:
            continue
        if bid_price.as_double() >= ask_price.as_double():
            continue
        yield (
            delta.ts_event,
            bid_price.as_double(),
            book.best_bid_size().as_double(),
            ask_price.as_double(),
            book.best_ask_size().as_double(),
        )


# ---------------------------------------------------------------------------
# Depth snapshot
# ---------------------------------------------------------------------------


def depth_profile(book: OrderBook, levels: int = 10) -> DepthProfile | None:
    """
    Extract size and price at the top `levels` levels on each side.

    Returns None if either side has no quotes (book not yet initialised).
    """
    bids = book.bids()
    asks = book.asks()
    if not bids or not asks:
        return None

    bid_prices = [lv.price.as_double() for lv in bids[:levels]]
    bid_sizes = [lv.size() for lv in bids[:levels]]
    ask_prices = [lv.price.as_double() for lv in asks[:levels]]
    ask_sizes = [lv.size() for lv in asks[:levels]]

    return DepthProfile(bid_prices, bid_sizes, ask_prices, ask_sizes)


# ---------------------------------------------------------------------------
# Book imbalance
# ---------------------------------------------------------------------------


@dataclass
class BookImbalance:
    """
    Bid-to-total imbalance at each level and aggregated.

    Value of 1.0 = all depth on the bid side. 0.5 = balanced. 0.0 = all ask.
    """

    per_level: list[float]  # one entry per level; index 0 = best
    aggregate: float  # imbalance across all levels combined


def book_imbalance(profile: DepthProfile) -> BookImbalance:
    per = []
    for b, a in zip(profile.bid_sizes, profile.ask_sizes, strict=False):
        total = b + a
        per.append(b / total if total > 0 else 0.5)

    total_bid = sum(profile.bid_sizes)
    total_ask = sum(profile.ask_sizes)
    total = total_bid + total_ask
    agg = total_bid / total if total > 0 else 0.5

    return BookImbalance(per_level=per, aggregate=agg)


# ---------------------------------------------------------------------------
# Volume-weighted price distance to liquidity
# ---------------------------------------------------------------------------


@dataclass
class LiquidityDistance:
    """
    How far from the best price the meaningful liquidity sits.

    `distance_to_pct` is the price distance (in ticks / absolute price units)
    you must travel from the best to capture `pct_threshold` fraction of the
    available depth on that side.

    A small distance means support/resistance is close and dense.
    A large distance means there's a vacuum — price can move fast and far.
    """

    bid_distance: float  # price distance to capture pct_threshold of bid depth
    ask_distance: float


def liquidity_distance(
    profile: DepthProfile,
    pct_threshold: float = 0.8,
) -> LiquidityDistance:
    def _dist(best_price: float, prices: list[float], sizes: list[float]) -> float:
        total = sum(sizes)
        if total == 0:
            return 0.0
        target = total * pct_threshold
        cumulative = 0.0
        for price, size in zip(prices, sizes, strict=False):
            cumulative += size
            if cumulative >= target:
                return abs(price - best_price)
        # All levels consumed and still below threshold — return distance to deepest level
        return abs(prices[-1] - best_price) if prices else 0.0

    bid_dist = _dist(profile.bid_prices[0], profile.bid_prices, profile.bid_sizes)
    ask_dist = _dist(profile.ask_prices[0], profile.ask_prices, profile.ask_sizes)
    return LiquidityDistance(bid_distance=bid_dist, ask_distance=ask_dist)


# ---------------------------------------------------------------------------
# Cancellation rate tracker
# ---------------------------------------------------------------------------


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

        if delta.order.side == OrderSide.BUY and best_bid_price is not None:
            if delta_price == best_bid_price:
                self._events.append(("bid", action_str, delta_size))
        elif delta.order.side == OrderSide.SELL and best_ask_price is not None:
            if delta_price == best_ask_price:
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


# ---------------------------------------------------------------------------
# Convenience: compute all features in one call
# ---------------------------------------------------------------------------


@dataclass
class BookFeatures:
    depth: DepthProfile
    imbalance: BookImbalance
    liquidity: LiquidityDistance
    # None when the caller has no cancellation tracker to report (Story 10.3 -- callers that
    # only need depth/imbalance/liquidity no longer have to maintain a tracker just to satisfy
    # this field).
    cancel: CancelRate | None


def compute_features(
    book: OrderBook,
    cancel_tracker: CancellationTracker | None = None,
    levels: int = 10,
    liquidity_pct: float = 0.8,
) -> BookFeatures | None:
    profile = depth_profile(book, levels)
    if profile is None:
        return None
    return BookFeatures(
        depth=profile,
        imbalance=book_imbalance(profile),
        liquidity=liquidity_distance(profile, liquidity_pct),
        cancel=cancel_tracker.rate() if cancel_tracker is not None else None,
    )


# =============================================================================================
# Footprint (was the footprint module)
#
# Footprint chart cells: per-candle, per-price-band order-book flow.
#
# For each candle, the candle's own [low, high] range is split into
# `bands_per_candle` horizontal bands. Each band accumulates the gross resting
# size added and removed on the bid side and ask side during that candle's
# time window, from order_book_deltas.
#
# This is *resting-order* flow, not executed trade volume. dYdX's deltas are L2
# market-by-price with no order IDs (every `BookOrder.order_id` is 0), so a
# level shrinking looks identical whether it was canceled or filled by a trade
# -- there's no way to tell those apart from deltas alone. "Removed" means
# "gross resting-size decrease," not a confirmed cancel. Gross added/removed
# are tracked separately (not just net) so a churning level (e.g. +100/-40)
# doesn't look identical to a quiet one (+60/0) when both net to +60.
#
# Known limit: bands are sized relative to each candle's own high-low range, so
# adjacent candles' bands don't line up at the same absolute price (a textbook
# footprint chart usually fixes one global price step instead, so rows align
# across the whole chart). Switch `bands_per_candle` for a shared `price_step`
# if cross-candle price alignment turns out to matter.
# =============================================================================================


@dataclass
class FootprintCell:
    ts_open: int
    price_low: float
    price_high: float
    bid_added: float = 0.0
    bid_removed: float = 0.0
    ask_added: float = 0.0
    ask_removed: float = 0.0

    @property
    def bid_net(self) -> float:
        return self.bid_added - self.bid_removed

    @property
    def ask_net(self) -> float:
        return self.ask_added - self.ask_removed


def build_footprint(
    deltas: list[OrderBookDelta],
    candles: list[Candle],
    period_seconds: int,
    bands_per_candle: int = 4,
) -> list[FootprintCell]:
    period_ns = period_seconds * 1_000_000_000
    candle_by_bucket = {candle.ts_open // period_ns: candle for candle in candles}
    cells: dict[tuple[int, int], FootprintCell] = {}

    bid_levels: dict[float, float] = {}
    ask_levels: dict[float, float] = {}

    for delta in sorted(deltas, key=lambda d: d.ts_init):
        if delta.action == BookAction.CLEAR:
            bid_levels.clear()
            ask_levels.clear()
            continue

        levels = bid_levels if delta.order.side == OrderSide.BUY else ask_levels
        price = delta.order.price.as_double()
        prev = levels.get(price, 0.0)

        if delta.action == BookAction.DELETE:
            new = 0.0
            levels.pop(price, None)
        else:  # ADD or UPDATE: order.size is the absolute new resting size
            new = delta.order.size.as_double()
            levels[price] = new

        change = new - prev
        if change == 0.0:
            continue

        candle = candle_by_bucket.get(delta.ts_event // period_ns)
        if candle is None or candle.high == candle.low:
            continue
        if not (candle.low <= price <= candle.high):
            continue

        band_height = (candle.high - candle.low) / bands_per_candle
        band_index = int((price - candle.low) / band_height)
        band_index = min(band_index, bands_per_candle - 1)
        price_low = candle.low + band_index * band_height
        cell_key = (candle.ts_open, band_index)
        cell = cells.setdefault(
            cell_key,
            FootprintCell(
                ts_open=candle.ts_open, price_low=price_low, price_high=price_low + band_height
            ),
        )

        is_bid = delta.order.side == OrderSide.BUY
        if change > 0.0:
            if is_bid:
                cell.bid_added += change
            else:
                cell.ask_added += change
        else:
            if is_bid:
                cell.bid_removed += -change
            else:
                cell.ask_removed += -change

    return sorted(cells.values(), key=lambda cell: (cell.ts_open, cell.price_low))


# =============================================================================================
# Per-snapshot book feature series (was the chart-data module)
#
# Reads DydxSecondSnapshot records from the catalog for a time range, computing
# book imbalance and depth at every 1-second snapshot. The result is a dict of
# named series ready for Plotly (see dashboard._render_chart_page).
#
# Snapshot-based, not raw-delta-based (platform/CLAUDE.md's "Signal Architecture:
# 1s-Based, Not Event-Driven" / SIGNAL-01): OrderBookDeltas are only persisted
# per-instrument when dYdX's `store_order_book_deltas` is opted in
# (default off), so replaying raw deltas here would silently return empty
# series for every instrument in the live catalog.
#
# The reader never re-validates the gate (spine AD-D11, Story 24.2): a crossed second is fed
# through like any other (its spread is negative, as written), and an empty-top second is
# ledgered and raised (`EmptyTopOfBook`), exactly as in `price_series_rows`.
# =============================================================================================

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

    Raises `EmptyTopOfBook` for a second with an empty side (see `_require_top`).
    """
    # ts_event window (venue-timed rows are sampled after their ts_event, story 22.12).
    snapshots = query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns)

    series: dict[str, list[dict]] = {
        "microprice": [],
        "spread": [],
        "imbalance": [],
        "mid_imbalance": [],
        "bid_depth": [],
        "ask_depth": [],
    }

    for s in sorted(snapshots, key=lambda s: s.ts_event):
        _require_top(s)
        bid_p, ask_p = s.bid_prices[0], s.ask_prices[0]
        t = s.ts_event / 1e9

        book_sides = s.as_floats()
        micro_value = calc_microprice(book_sides)
        if micro_value is not None:
            series["microprice"].append({"time": t, "value": micro_value})

        series["spread"].append({"time": t, "value": ask_p - bid_p})

        profile = snapshot_depth(book_sides, _LEVELS)
        if profile is None:  # unreachable: `_require_top` raised on an empty side
            raise EmptyTopOfBook(f"snapshot without a top of book at ts_event={s.ts_event}")
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


# =============================================================================================
# The bar-spaced gap marker (was three copies: routes/candles.py, indicator_series.py,
# indicators.py)
# =============================================================================================


def with_gap_markers(rows: list[dict], bar_seconds: int) -> list[dict]:
    """
    Insert one explicit gap row `{"t": earlier + bar_ms}` wherever two consecutive kept rows' `t`
    (ms) differ by more than one `bar_seconds` interval (AD-F6) -- `t` is placed immediately after
    the earlier row so lightweight-charts' whitespace data renders the break starting right where
    real data stops, not at the next row's own time. One rule for candles, indicator series and
    indicator values, because those panes share one time axis and must break in the same place.

    A gap between two *pages* is not seen here (only gaps strictly inside `rows`): that seam is the
    frontend's own check across two pages (`useCandles.ts`).
    """
    bar_ms = bar_seconds * 1000
    out: list[dict] = []
    for i, row in enumerate(rows):
        if i > 0 and row["t"] - rows[i - 1]["t"] > bar_ms:
            out.append({"t": rows[i - 1]["t"] + bar_ms})
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
    snapshots, one per second, with a gap row (`_gap_row`, `t = later - 1`, every value null)
    between two seconds more than `SNAPSHOT_GAP_THRESHOLD_MS` apart.

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
            rows.append(_gap_row(curr_ts_ms - 1))
        prev_ts_ms = curr_ts_ms
        mid = (bp + ap) / 2
        micro_value = calc_microprice(s.as_floats())
        micro = micro_value if micro_value is not None else mid
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

    Slicing at a real row's own index (never mid-window) also means any gap marker sitting
    immediately before the new earliest-kept real row is naturally dropped -- the same
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
    """
    before_ms = before_ns // 1_000_000

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        snapshots = query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns)
        rows = price_series_rows(sorted(snapshots, key=lambda s: s.ts_event))
        return _take_last_n_real_rows([r for r in rows if r["t"] < before_ms], limit)

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


def _candle_window_start_ns(before_ns: int, limit: int, bar_seconds: int) -> int:
    span_seconds = limit * bar_seconds * _CANDLE_WINDOW_MULTIPLIER
    if bar_seconds < 60:
        span_seconds = max(span_seconds, _MIN_CANDLE_WINDOW_SECONDS)
    span_seconds = min(span_seconds, MAX_QUERY_SPAN_SECONDS)
    return before_ns - span_seconds * 1_000_000_000


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
    recent_rows: RecentRows,
) -> tuple[list[dict], bool]:
    """
    One page straight from the Parquet archive (slow: reads a window of tiny files). Serves
    history the candle store does not hold (older than its first day, or pruned).
    """
    before_ms = before_ns // 1_000_000
    rows_fn = partial(_catalog_plus_recent, catalog_path, recent_rows)

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        return [
            c
            for c in queries.candle_dicts_for_window(
                instrument_id,
                start_ns,
                end_ns,
                bar_seconds,
                snapshot_rows_fn=rows_fn,
            )
            if c["t"] < before_ms and _checked(instrument_id, bar_seconds, c)
        ]

    ranges = catalog_files.data_file_ranges(catalog_path, instrument_id)
    span_ns = before_ns - _candle_window_start_ns(before_ns, limit, bar_seconds)
    kept = fetch_page(fetch, ranges, before_ns, span_ns)[-limit:]
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
        instrument_id, older_before_ns, limit - len(kept), bar_seconds, catalog_path, recent_rows
    )
    return older + kept, has_more


# =============================================================================================
# Indicator series: OFI/OBI/microprice/spread per bar (was data_api/routes/indicator_series.py)
#
# SSOT-02 governs *live* rolling OFI/OBI (ranking_engine is the sole owner) -- it does not apply
# here: this is a bounded, deterministic *historical* replay for one request's own fixed time
# window, the same sanctioned category as `views.indicator_picker`'s request-scoped
# `_ofi_replay`/`_cancel_pressure_replay`, not a second live computer of the published metric.
# =============================================================================================

_INDICATOR_SERIES_WINDOW_MULTIPLIER = 3


def _indicator_series_window_start_ns(before_ns: int, limit: int, bar_seconds: int) -> int:
    span_seconds = min(
        limit * bar_seconds * _INDICATOR_SERIES_WINDOW_MULTIPLIER, MAX_QUERY_SPAN_SECONDS
    )
    return before_ns - span_seconds * 1_000_000_000


def _replay_bucket_samples(
    snapshots: Sequence[DydxSecondSnapshot], bar_seconds: int
) -> dict[int, dict]:
    """
    Replay `MultiLevelOFI`/`MultiLevelOBI` in chronological order over every queried
    snapshot, and compute stateless `microprice`/`spread` per snapshot -- keeping the
    last-computed value per `bar_seconds`-wide bucket (`ts_event // (bar_seconds *
    1_000_000_000)`), same bucket-sampling technique `indicator_picker`'s
    `_ofi_bucket_samples` uses, adapted from delta-driven to snapshot-driven input.

    A page's OFI/OBI replay starts fresh at that page's own window start -- it cannot
    carry state across pages, since pages are fetched independently and out of full-
    history order (same explicit per-page-reset scope choice the CVD replay already
    documents). OFI's first snapshot in this window only seeds its `_prev_*` state and
    yields no value -- only record `ofi` once `.initialized` is True.

    A second with an empty side is not an error here: its microprice/spread are honestly `None`
    (`kernel.indicators` returns no value without a top), pinned by
    `data_api/tests/test_indicator_series.py`'s thin-book test -- nothing is skipped or invented.
    """
    bar_ns = bar_seconds * 1_000_000_000
    ofi = MultiLevelOFI(levels=10, window=50)
    obi = MultiLevelOBI(levels=10)
    buckets: dict[int, dict] = {}

    for snapshot in sorted(snapshots, key=lambda s: s.ts_event):
        ofi.update_raw(
            snapshot.bid_prices,
            snapshot.bid_sizes,
            snapshot.ask_prices,
            snapshot.ask_sizes,
        )
        obi.update_raw(snapshot.bid_sizes, snapshot.ask_sizes)
        snapshot_dict = snapshot.as_floats()
        bucket = snapshot.ts_event // bar_ns
        buckets[bucket] = {
            "t": bucket * bar_seconds * 1000,
            "ofi": ofi.value if ofi.initialized else None,
            "obi": obi.value if obi.initialized else None,
            "microprice": calc_microprice(snapshot_dict),
            "spread": calc_spread(snapshot_dict),
        }

    return buckets


def indicator_series_page(
    catalog_path: str, instrument_id: str, before_ns: int, limit: int, bar_seconds: int
) -> tuple[list[dict], bool]:
    """
    One indicator-series page: `(rows oldest-first with gap rows, has_more)` of
    `{t, ofi, obi, microprice, spread}` for the `limit` bars before `before_ns`.
    """
    before_ms = before_ns // 1_000_000

    def fetch(start_ns: int, end_ns: int) -> list[dict]:
        snapshots = query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns)
        buckets = _replay_bucket_samples(snapshots, bar_seconds)
        return [p for _, p in sorted(buckets.items()) if p["t"] < before_ms]

    ranges = catalog_files.data_file_ranges(catalog_path, instrument_id)
    span_ns = before_ns - _indicator_series_window_start_ns(before_ns, limit, bar_seconds)
    kept = fetch_page(fetch, ranges, before_ns, span_ns)[-limit:]
    if not kept:
        return [], False
    has_more = has_older_data(ranges, kept[0]["t"] * 1_000_000)
    return with_gap_markers(kept, bar_seconds), has_more


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
        )
    except ImpossibleCandle:
        raise
    except Exception as exc:
        raise CandleReadError(str(exc)) from exc
    if not kept:
        return [], False, {}
    window = indicator_picker.ReplayWindow(
        instrument_id=instrument_id,
        bar_seconds=bar_seconds,
        start_ms=kept[0]["t"],
        end_ms=kept[-1]["t"] + bar_seconds * 1000,
    )
    by_time, errors = indicator_picker.values_by_time(kept, entries, window)
    rows = [{"t": t, "values": values} for t, values in sorted(by_time.items())]
    return with_gap_markers(rows, bar_seconds), has_more, errors
