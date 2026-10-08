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
The derivatives and liquidations read model (Story 33.4, `docs/DATA_DICTIONARY.md` §2.16): the one
reader of the archived funding, open interest, mark, index and liquidations, behind the five
`GET /api/coin/{iid}/...` routes (SSOT-02: 33.5's panes and anything else read these pages).

Every page returns `(items oldest first, has_more)` of plain dicts shaped like the route's items:

- **Event pages** (`funding_page`, `liquidations_page`): the newest `limit` events before
  `before_ns`, `t`/`ts_event` in ns (the live `derivs:`/`liquidations:` frames' unit).
- **Bucket pages** (`open_interest_page`, `mark_index_page`, `liquidation_bars`): one row per
  `candles.domain.fold.bucket_start_ms` bucket, `t` in ms like the candles, the last value of the
  bucket, gap rows by `views.chart_series.with_gap_markers` (a bucket without data is a gap, never
  filled).

Invariants:
- **Bounded (MEM-01).** A page reads at most `MAX_QUERY_SPAN_SECONDS` of archive, walking back in
  windows, an empty one jumping straight to the newest older row (`_first_window`), each read one
  UTC day at a time, never at or after `before_ns` (no look-ahead). Mark and index are read
  column-projected (`catalog_files.query_price_columns`) and reduced to each bucket's last value,
  never one Python object per tick.
- **Exact (DATA-04).** Values stay `Decimal` (or integer units) through the arithmetic: prices,
  rates and open interest are emitted as plain positional decimal text (`exact_text`), basis and
  annualised funding as a float made from the `Decimal` result at this edge only.
- **Null vs 0.** A missing input is None: a bucket without an index has no `basis_mi_bps`, an id
  without a liquidation feed has null liquidation bars and no liquidation events.
- **Spot reads nothing.** A spot id has no derivatives: every page is `([], False)`.

A failed archive or store read is ledgered at `READ_SITE` and raised as `DerivativesReadError`.
"""

import sqlite3
from collections.abc import Callable
from collections.abc import Iterable
from decimal import Decimal
from functools import partial

import numpy as np
from candles.application import queries
from candles.domain.fold import BAR_SECONDS
from candles.domain.fold import bucket_start_ms
from candles.domain.fold import first_bucket_at_or_after
from kernel import catalog_files
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.derivs_wire import DerivsTick
from kernel.derivs_wire import exact_text
from kernel.derivs_wire import to_tick
from kernel.indicators import basis_bps
from kernel.indicators import funding_annualised
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.venues import market_kind
from kernel.venues import venue_of
from observability import error_ledger

from nautilus_trader.model.data import CustomData
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views.catalog_reads import has_older_data
from views.chart_series import MAX_QUERY_SPAN_SECONDS
from views.chart_series import RecentLiquidations
from views.chart_series import bucket_end_ns
from views.chart_series import day_slices
from views.chart_series import liquidations_plus_recent
from views.chart_series import stored_bar
from views.chart_series import with_gap_markers


READ_SITE = "derivatives.read"
# The only price a Bybit liquidation carries (`kernel.liquidation`: the bankruptcy price); a
# constant of this read model, never a stored column (Bybit is the only feed).
PRICE_KIND = "bankruptcy"

_MS = 1_000_000
_MAX_SPAN_NS = MAX_QUERY_SPAN_SECONDS * 1_000_000_000
_NO_BOUND_MS = 1 << 62  # past every stored bucket: `newest_row_t`'s "newest of all"
# The three `liq_*` keys of a `queries.liquidation_window` row (views may not import
# `candles.domain.fold.LIQUIDATION_KEYS`: only the listed query services, tests/test_boundaries.py).
_LIQUIDATION_KEYS = ("liq_long_v", "liq_short_v", "liq_n")


class DerivativesReadError(Exception):
    """Reading a derivatives page failed (a corrupt or unreadable file); already ledgered."""


class UnsupportedBarSeconds(Exception):
    """
    `liquidation_bars` was asked for a width no candle-store width tiles
    (`views.chart_series.stored_bar`): a request error (HTTP 400), never an empty or null page
    read as "no liquidations".
    """


def is_spot(instrument_id: str) -> bool:
    """Whether the id is spot: no funding, OI, mark, index or liquidations exist for it."""
    return market_kind(instrument_id) == "spot"


def _guarded[T](instrument_id: str, read: Callable[[], T]) -> T:
    """
    Run one page read; ledger and raise `DerivativesReadError` on any failed read (DATA-07): a file
    (`OSError`), a corrupt or disagreeing row (`ValueError`), the candle store (`sqlite3.Error`),
    a value's arithmetic (`ArithmeticError`: `decimal.InvalidOperation`, `OverflowError`) or a
    collection changed under the read (`RuntimeError`).
    """
    try:
        return read()
    except (OSError, ValueError, sqlite3.Error, ArithmeticError, RuntimeError) as exc:
        detail = f"{instrument_id}: derivatives read failed: {exc}"
        error_ledger.record(READ_SITE, detail, exc)
        raise DerivativesReadError(detail) from exc


def _same(ns: int) -> int:
    # An `align_end` that keeps the end: each fetch is `[start, end)`, so a gap jump lands one past
    # the newest older row and still reads it.
    return ns


NewestBefore = Callable[[int], int | None]


def _newest_before(catalog_path: str, instrument_id: str, *dirnames: str) -> NewestBefore:
    """Bind `catalog_files.newest_ts_event_before` to one instrument's data directories."""
    return partial(
        catalog_files.newest_ts_event_before,
        catalog_path,
        instrument_id,
        dirnames,
        on_foreign=error_ledger.record,
    )


def _first_window[T](
    fetch: Callable[[int, int], list[T]],
    newest_before: NewestBefore,
    before_ns: int,
    span_ns: int,
    align: Callable[[int], int],
) -> list[T]:
    """
    First non-empty `fetch` window `[end - span_ns, end)` walking back from `align(before_ns)`; `[]`
    only when nothing older exists. An empty window jumps straight to the window ending just past
    the newest row before it (`newest_before`: one bounded read of the type's `ts_event` column),
    so a sparse series costs two fetches and one scan, never one fetch per empty window.
    `views.catalog_reads.fetch_page` steps back one window at a time while its window lies inside a
    file, which a 1 s window over a 300 s open-interest poll turned into thousands of reads of one
    file (Story 33.4 review); its candle callers keep it.

    `align` maps every end to a bucket boundary at or above it, and `span_ns` is whole buckets, so a
    window's start is a fixed point of `align` and each jump ends at or before it: the walk always
    progresses, and the jumped-to window holds the newest older row.
    """
    end_ns = align(before_ns)
    while True:
        start_ns = end_ns - span_ns
        rows = fetch(start_ns, end_ns)
        if rows:
            return rows
        newest = newest_before(start_ns)
        if newest is None:
            return []
        end_ns = align(newest + 1)


def _walk_back[T](
    fetch: Callable[[int, int], list[T]],
    newest_before: NewestBefore,
    before_ns: int,
    limit: int,
    window: tuple[int, Callable[[int], int]],
    t_ns: Callable[[T], int],
) -> list[T]:
    """
    Collect windows of `fetch` (`[start, end)`, oldest first) walking back from `before_ns`, each
    jumping gaps (`_first_window`), until `limit` rows are held or `MAX_QUERY_SPAN_SECONDS` of windows
    were read. `window` is `(span_ns, align_end)`; the next window ends at the oldest row so far.

    Known limit: the cap counts windows, so a page of many sparse rows reads up to
    `MAX_QUERY_SPAN_SECONDS / span_ns` windows (each at most two fetches and one `ts_event` scan);
    `_bucket_window` sizes the span from `limit`, which bounds it near `sqrt(7 days / bar_seconds)`
    windows (about 780 at 1 s, each a read of a small sparse file). Upgrade path: read the type's
    `ts_event` column once per request and fetch only the windows that hold rows.
    """
    span_ns, align = window
    rows: list[T] = []
    end_ns = before_ns
    for _ in range(max(1, _MAX_SPAN_NS // span_ns)):
        found = _first_window(fetch, newest_before, end_ns, span_ns, align)
        if not found:
            break
        rows = found + rows
        if len(rows) >= limit:
            break
        end_ns = t_ns(found[0])
    return rows


def _kept[T](
    rows: list[T], limit: int, ranges: list[tuple[int, int]], t_ns: Callable[[T], int]
) -> tuple[list[T], bool]:
    """
    Return the newest `limit` rows, and whether anything older exists (dropped or archived).

    Rows sharing the oldest kept row's time are all kept, so the page may exceed `limit` by that
    group: the next page's cursor is that time (`t < before_ns`), so a cut inside the group would
    lose its older half for good (a Bybit liquidation cascade stamps many entries alike). A
    walk-back window never splits such a group (each next window ends at the oldest row's time).
    """
    start = max(0, len(rows) - limit)
    while start > 0 and t_ns(rows[start - 1]) == t_ns(rows[start]):
        start -= 1
    kept = rows[start:]
    return kept, start > 0 or (bool(kept) and has_older_data(ranges, t_ns(kept[0])))


# -- archive reads: every one `[start_ns, end_ns)` of `ts_event`, sorted --------------------------


def _catalog_ticks(
    catalog_path: str, data_cls: type, instrument_id: str, start_ns: int, end_ns: int
) -> list[DerivsTick]:
    """
    Return the `FundingRateUpdate` rows with `start_ns <= ts_event < end_ns`, as exact `DerivsTick`s
    (`kernel.derivs_wire.to_tick`, the live frames' one conversion), each stored row once
    (`_unique_ticks`). The catalog bounds rows by `ts_init`, which trails `ts_event` by at most
    `MAX_TS_INIT_SKEW_NS` (and may lead it by a clock step), so the read is widened by that bound on
    both sides and `ts_event` decides (the `research.application.frames.CatalogFrames._query`
    pattern).

    Known limit: this decodes every row into a Python object, unlike the column-projected mark and
    index read (`_last_prices`). Funding is change-deduped upstream (one row per change of the
    venue's rate or next-funding strings, audit D-103/D-108: about a hundred a day for BTCUSDT), so
    a day window holds hundreds of rows, not hundreds of thousands. Upgrade path: a
    `catalog_files` column reader of `rate` (`rust_decimal`'s JSON text) beside `query_price_columns`.
    """
    rows = ParquetDataCatalog(catalog_path).query(
        data_cls,
        identifiers=[instrument_id],
        start=max(0, start_ns - MAX_TS_INIT_SKEW_NS),
        end=end_ns + MAX_TS_INIT_SKEW_NS,
    )
    data = (row.data if isinstance(row, CustomData) else row for row in rows)
    ticks = (to_tick(row) for row in data if start_ns <= row.ts_event < end_ns)
    return _unique_ticks(instrument_id, (t for t in ticks if t is not None))


def _unique_ticks(instrument_id: str, ticks: Iterable[DerivsTick]) -> list[DerivsTick]:
    """
    Sorted, each row once: one row stored twice (a minute file and its consolidated day file) is
    kept once, keyed on its `(ts_event, ts_init)` as `catalog_files.query_open_interest` and
    `catalog_files.query_price_columns` do; two copies with that key but different values raise
    `ValueError` (ledgered by `_guarded`, never one copy picked, DATA-07).
    """
    kept: dict[tuple[int, int], DerivsTick] = {}
    for tick in ticks:
        first = kept.setdefault((tick.t, tick.ts_init), tick)
        if first != tick:
            raise ValueError(
                f"{instrument_id}: {tick.kind} at ts_event {tick.t} is stored twice with different "
                f"values ({first} vs {tick}), refused"
            )
    return sorted(kept.values(), key=lambda t: (t.t, t.ts_init))


def _oi_ticks(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[DerivsTick]:
    rows = catalog_files.query_open_interest(
        catalog_path, instrument_id, start_ns, end_ns - 1, on_foreign=error_ledger.record
    )
    return [tick for tick in map(to_tick, rows) if tick is not None]


TickRead = Callable[[int, int], list[DerivsTick]]


def _bucket_ns(row: dict) -> int:
    return row["t"] * _MS


def _last_per_bucket(
    read: TickRead, start_ns: int, end_ns: int, bar_seconds: int
) -> dict[int, Decimal]:
    """`{bucket t (ms): the bucket's last value}` of `read` over `[start_ns, end_ns)`, day by day."""
    last: dict[int, Decimal] = {}
    for lo, hi in day_slices(start_ns, end_ns):
        for tick in read(lo, hi):
            last[bucket_start_ms(tick.t // _MS, bar_seconds)] = tick.value
    return last


def _last_prices(
    catalog_path: str,
    dirname: str,
    instrument_id: str,
    bar_seconds: int,
    start_ns: int,
    end_ns: int,
) -> dict[int, Decimal]:
    """
    `{bucket t (ms): the bucket's last price}` of the mark (`MARK_PRICE_DIRNAME`) or index
    (`INDEX_PRICE_DIRNAME`) rows over `[start_ns, end_ns)`: one UTC day of columns at a time
    (`catalog_files.query_price_columns`, each stored row once, a disagreeing copy refused), cut to
    each bucket's newest row with numpy, so what is kept is bounded by the buckets, not the ticks
    (MEM-01). Each price is `Decimal(units).scaleb(-precision)`, the stored value exactly.
    """
    last: dict[int, Decimal] = {}
    for lo, hi in day_slices(start_ns, end_ns):
        rows = catalog_files.query_price_columns(
            catalog_path, dirname, instrument_id, lo, hi - 1, on_foreign=error_ledger.record
        )
        if not len(rows.ts_event):
            continue
        buckets = bucket_start_ms(rows.ts_event // _MS, bar_seconds)
        newest = np.ones(len(buckets), dtype=bool)  # sorted by ts_event: a bucket's run ends last
        newest[:-1] = buckets[1:] != buckets[:-1]
        for t, units, precision in zip(
            buckets[newest].tolist(),
            rows.units[newest].tolist(),
            rows.precision[newest].tolist(),
            strict=True,
        ):
            last[t] = Decimal(units).scaleb(-precision)
    return last


def _bucket_window(bar_seconds: int, limit: int) -> tuple[int, Callable[[int], int]]:
    """
    One walk-back window: the request's `limit` buckets, at most a UTC day of them and at least one
    whole bucket, aligned to bucket boundaries so no bucket is split between two windows (Story
    31.8's rule for the candles). Sized from the request so a narrow page (`bar_seconds=1,
    limit=120`: two minutes) never decodes a day of ticks; `_walk_back` keeps reading windows
    until `limit` buckets are held or `MAX_QUERY_SPAN_SECONDS` was read.

    Known limit: a window is at most a UTC day and the span cap is `MAX_QUERY_SPAN_SECONDS` (one
    week), so a wide page holds at most `MAX_QUERY_SPAN_SECONDS / bar_seconds` buckets whatever its
    `limit`: 42 at 4h, 7 at 1D, one at 1W (as the candles' Parquet page does); `has_more` stays
    true and the client pages on. Upgrade path: the same one, composing wide buckets from stored
    narrower ones.
    """
    span_s = max(1, min(86_400, limit * bar_seconds) // bar_seconds) * bar_seconds
    return span_s * 1_000_000_000, partial(bucket_end_ns, bar_seconds=bar_seconds)


# -- funding -------------------------------------------------------------------------------------


def _funding_item(tick: DerivsTick) -> dict:
    annualised = funding_annualised(tick.value, tick.interval)
    return {
        "t": tick.t,
        "rate": exact_text(tick.value),
        "interval": tick.interval,
        "next_funding_ns": tick.next_funding_ns,
        "annualised": None if annualised is None else float(annualised),
    }


def funding_page(
    instrument_id: str, before_ns: int, limit: int, *, catalog_path: str
) -> tuple[list[dict], bool]:
    """
    Return the newest `limit` `FundingRateUpdate`s with `ts_event < before_ns`, oldest first: `{t (ns),
    rate (exact text), interval (s, None when the venue sent none), next_funding_ns, annualised
    (float, None without an interval)}`.
    """
    if is_spot(instrument_id):
        return [], False

    def read() -> tuple[list[dict], bool]:
        dirname = catalog_files.FUNDING_RATE_DIRNAME
        fetch = partial(_catalog_ticks, catalog_path, FundingRateUpdate, instrument_id)
        newest = _newest_before(catalog_path, instrument_id, dirname)
        ranges = catalog_files.data_file_ranges(
            catalog_path, instrument_id, dirname, on_foreign=error_ledger.record
        )
        ticks = _walk_back(fetch, newest, before_ns, limit, (NS_PER_DAY, _same), lambda t: t.t)
        kept, has_more = _kept(ticks, limit, ranges, lambda t: t.t)
        return [_funding_item(tick) for tick in kept], has_more

    return _guarded(instrument_id, read)


# -- open interest -------------------------------------------------------------------------------


def _oi_rows(
    read: TickRead, bar_seconds: int, before_ns: int, start_ns: int, end_ns: int
) -> list[dict]:
    last = _last_per_bucket(read, start_ns, min(end_ns, before_ns), bar_seconds)
    return [{"t": t, "oi": last[t]} for t in sorted(last)]


def _with_oi_change(rows: list[dict], previous: Decimal | None) -> list[dict]:
    """
    Each row's `oi_change` against the previous *known* bucket (a gap does not reset it); the first
    against `previous`, None when that is unknown. Values become exact text here.
    """
    out = []
    for row in rows:
        change = None if previous is None else row["oi"] - previous
        out.append({"t": row["t"], "oi": exact_text(row["oi"]), "oi_change": _text(change)})
        previous = row["oi"]
    return out


def _text(value: Decimal | None) -> str | None:
    return None if value is None else exact_text(value)


def open_interest_page(
    instrument_id: str, before_ns: int, limit: int, bar_seconds: int, *, catalog_path: str
) -> tuple[list[dict], bool]:
    """
    Return the newest `limit` buckets of open interest before `before_ns`, oldest first, gap-marked:
    `{t (ms), oi (the bucket's last, exact text), oi_change (against the previous known bucket,
    exact text)}`. The first bucket of the page is compared against the newest known bucket before
    it, found by one more bounded walk back (gaps jumped, as `_walk_back` does), so a bucket's
    change never depends on where the page was cut; None only when no older open interest exists.
    """
    if is_spot(instrument_id):
        return [], False

    def read() -> tuple[list[dict], bool]:
        dirname = catalog_files.OPEN_INTEREST_DIRNAME
        ticks = partial(_oi_ticks, catalog_path, instrument_id)
        fetch = partial(_oi_rows, ticks, bar_seconds, before_ns)
        newest = _newest_before(catalog_path, instrument_id, dirname)
        ranges = catalog_files.data_file_ranges(
            catalog_path, instrument_id, dirname, on_foreign=error_ledger.record
        )
        window = _bucket_window(bar_seconds, limit)
        rows = _walk_back(fetch, newest, before_ns, limit, window, _bucket_ns)
        kept, has_more = _kept(rows, limit, ranges, _bucket_ns)
        previous = rows[-limit - 1]["oi"] if len(rows) > limit else None
        if kept and previous is None:  # walk back to the newest known bucket before the page
            start_ns = _bucket_ns(kept[0])
            fetch_before = partial(_oi_rows, ticks, bar_seconds, start_ns)
            before = _walk_back(fetch_before, newest, start_ns, 1, window, _bucket_ns)
            previous = before[-1]["oi"] if before else None
        return with_gap_markers(_with_oi_change(kept, previous), bar_seconds), has_more

    return _guarded(instrument_id, read)


# -- mark, index and basis ------------------------------------------------------------------------


def _mark_index_rows(
    catalog_path: str,
    instrument_id: str,
    bar_seconds: int,
    before_ns: int,
    start_ns: int,
    end_ns: int,
) -> list[dict]:
    end_ns = min(end_ns, before_ns)
    window = (instrument_id, bar_seconds, start_ns, end_ns)
    marks = _last_prices(catalog_path, catalog_files.MARK_PRICE_DIRNAME, *window)
    indexes = _last_prices(catalog_path, catalog_files.INDEX_PRICE_DIRNAME, *window)
    return [
        {"t": t, "mark": marks.get(t), "index": indexes.get(t)}
        for t in sorted(marks.keys() | indexes.keys())
    ]


def _store_closes(
    candles_dir: str, instrument_id: str, bar_seconds: int, first_t: int, last_t: int
) -> dict[int, Decimal]:
    """
    Return the candle store's traded closes of the buckets `first_t..last_t` (ms), exact: the stored `c`
    is the decoded float of an integer-unit price, so `Decimal(str(c))` quantized at the row's own
    `price_precision` recovers the stored price exactly. A width the store does not fold is read
    at `stored_bar`'s width, a bucket's close being its newest traded constituent's (the last
    trade of the bucket). A width no stored one tiles (1..59 s, 90 s) has no close: `{}`, so its
    `basis_ml_bps` is None (a missing input), while mark, index and `basis_mi_bps` are served.

    Known limit: a bucket the store does not hold (older than its coverage, pruned, untraded) or a
    pre-33.3 row without `price_precision` has no close here, so its `basis_ml_bps` is None, not
    folded from the archive; upgrade path: the operator's history rebuild (DEPLOY_CHECKLIST 33-3)
    stamps the precisions, and an archive close read would serve the store's gaps.
    """
    source = stored_bar(bar_seconds)
    if source is None:
        return {}
    end_t = last_t + bar_seconds * 1000
    count = (end_t - first_t) // (source * 1000)
    with queries.open_store(candles_dir, venue_of(instrument_id)) as db:
        bars = [] if db is None else queries.window(db, instrument_id, source, end_t, count)
    closes: dict[int, Decimal] = {}
    for bar in bars:  # oldest first: a bucket keeps its newest constituent's close
        if bar["t"] >= first_t and bar["c"] is not None and bar["price_precision"] is not None:
            close = Decimal(str(bar["c"])).quantize(Decimal(1).scaleb(-bar["price_precision"]))
            closes[bucket_start_ms(bar["t"], bar_seconds)] = close
    return closes


def _basis_float(mark: Decimal | None, ref: Decimal | None) -> float | None:
    if mark is None or ref is None:
        return None
    basis = basis_bps(mark, ref)
    return None if basis is None else float(basis)


def _mark_index_item(row: dict, close: Decimal | None) -> dict:
    return {
        "t": row["t"],
        "mark": _text(row["mark"]),
        "index": _text(row["index"]),
        "basis_mi_bps": _basis_float(row["mark"], row["index"]),
        "basis_ml_bps": _basis_float(row["mark"], close),
    }


def mark_index_page(
    instrument_id: str,
    before_ns: int,
    limit: int,
    bar_seconds: int,
    *,
    catalog_path: str,
    candles_dir: str,
) -> tuple[list[dict], bool]:
    """
    Return the newest `limit` buckets of mark and index price before `before_ns`, oldest first,
    gap-marked: `{t (ms), mark, index (each the bucket's last, exact text, None when the bucket has
    none), basis_mi_bps (mark against index), basis_ml_bps (mark against the store's traded close
    of the bucket)}`, each basis `kernel.indicators.basis_bps` as a float, None without an input.
    Every width is served; one no candle-store width tiles has a null `basis_ml_bps` (no close).
    """
    if is_spot(instrument_id):
        return [], False

    def read() -> tuple[list[dict], bool]:
        dirnames = (catalog_files.MARK_PRICE_DIRNAME, catalog_files.INDEX_PRICE_DIRNAME)
        fetch = partial(_mark_index_rows, catalog_path, instrument_id, bar_seconds, before_ns)
        newest = _newest_before(catalog_path, instrument_id, *dirnames)
        ranges = sorted(
            span
            for dirname in dirnames
            for span in catalog_files.data_file_ranges(
                catalog_path, instrument_id, dirname, on_foreign=error_ledger.record
            )
        )
        rows = _walk_back(
            fetch, newest, before_ns, limit, _bucket_window(bar_seconds, limit), _bucket_ns
        )
        kept, has_more = _kept(rows, limit, ranges, _bucket_ns)
        closes = (
            _store_closes(candles_dir, instrument_id, bar_seconds, kept[0]["t"], kept[-1]["t"])
            if kept
            else {}
        )
        items = [_mark_index_item(row, closes.get(row["t"])) for row in kept]
        return with_gap_markers(items, bar_seconds), has_more

    return _guarded(instrument_id, read)


# -- liquidations --------------------------------------------------------------------------------


def _liquidation_item(row: Liquidation) -> dict:
    item = Liquidation.to_dict(row)
    del item["instrument_id"]
    return {
        **item,
        "price_kind": PRICE_KIND,
        "notional_units": row.notional_units(),
        "notional_precision": row.price_precision + row.size_precision,
    }


def liquidations_page(
    instrument_id: str,
    before_ns: int,
    limit: int,
    *,
    catalog_path: str,
    recent_liquidations: RecentLiquidations,
) -> tuple[list[dict], bool]:
    """
    Return the newest `limit` liquidations with `ts_event < before_ns`, oldest first, the archive plus the
    live tail not flushed yet, each venue event once (`liquidations_plus_recent`): the stored row
    (`Liquidation.to_dict`, integer units at its own precisions) without its `instrument_id`, plus
    `price_kind: "bankruptcy"` and the row's notional (`notional_units()`, size x bankruptcy price,
    at `notional_precision = price_precision + size_precision`; Story 33.5: the browser does no
    notional arithmetic). An id without the feed (`has_liquidation_feed`) reads nothing.
    """
    if not has_liquidation_feed(instrument_id):
        return [], False

    def fetch(start_ns: int, end_ns: int) -> list[Liquidation]:
        rows = liquidations_plus_recent(
            catalog_path, recent_liquidations, instrument_id, start_ns, end_ns - 1
        )
        return sorted(rows, key=lambda r: (r.ts_event, r.venue_event_id))

    def read() -> tuple[list[dict], bool]:
        dirname = catalog_files.LIQUIDATION_DIRNAME
        newest = _newest_before(catalog_path, instrument_id, dirname)
        ranges = catalog_files.data_file_ranges(
            catalog_path, instrument_id, dirname, on_foreign=error_ledger.record
        )
        rows = _walk_back(
            fetch, newest, before_ns, limit, (NS_PER_DAY, _same), lambda r: r.ts_event
        )
        kept, has_more = _kept(rows, limit, ranges, lambda r: r.ts_event)
        return [_liquidation_item(row) for row in kept], has_more

    return _guarded(instrument_id, read)


def _require_stored_bar(bar_seconds: int) -> int:
    source = stored_bar(bar_seconds)
    if source is None:
        raise UnsupportedBarSeconds(
            f"bar_seconds={bar_seconds} is not composable from the candle store's widths "
            f"{BAR_SECONDS}: it must be a multiple of one of them aligned to its buckets"
        )
    return source


def _store_liquidation_rows(
    candles_dir: str, instrument_id: str, before_ns: int, limit: int, bar_seconds: int
) -> tuple[list[dict], bool]:
    """
    Return the newest `limit` buckets before `before_ns` (start `t < before_ns`'s ms, as the
    candles' store page), traded or not, from one `MAX_QUERY_SPAN_SECONDS`-bounded window of whole
    buckets; when that window is empty it jumps once to the newest older row. Has more: an older
    stored row exists. A width the store does not fold is composed from `stored_bar`'s rows
    (`_composed_window`); a bucket at the cursor is served whole, as a stored one is.
    """
    source = _require_stored_bar(bar_seconds)
    bar_ms = bar_seconds * 1000
    span_ms = max(1, min(limit * bar_seconds, MAX_QUERY_SPAN_SECONDS) // bar_seconds) * bar_ms
    with queries.open_store(candles_dir, venue_of(instrument_id)) as db:
        if db is None:
            return [], False
        read = partial(_composed_window, db, instrument_id, bar_seconds, source)
        end_ms = bucket_start_ms(before_ns // _MS - 1, bar_seconds) + bar_ms
        rows = read(end_ms - span_ms, end_ms)
        if not rows:
            newest = queries.newest_row_t(db, instrument_id, source, end_ms - span_ms)
            if newest is None:
                return [], False
            end_ms = bucket_start_ms(newest, bar_seconds) + bar_ms
            rows = read(end_ms - span_ms, end_ms)
        kept = rows[-limit:]
        older = queries.newest_row_t(db, instrument_id, source, kept[0]["t"])
        return kept, older is not None


def _require_whole_group(row: dict) -> dict:
    """
    Return the stored row if its three `liq_*` are all set or all null; a row with some of them
    null is corrupt (the store writes the three together, D-160) and raises `ValueError`, which
    `_guarded` ledgers, never a `TypeError` 500 nobody counted.
    """
    nulls = sum(row[key] is None for key in _LIQUIDATION_KEYS)
    if 0 < nulls < len(_LIQUIDATION_KEYS):
        values = {key: row[key] for key in _LIQUIDATION_KEYS}
        raise ValueError(
            f"candle row t={row['t']} holds a partially null liquidation group {values}"
        )
    return row


def _composed_window(
    db: sqlite3.Connection,
    instrument_id: str,
    bar_seconds: int,
    source: int,
    start_ms: int,
    end_ms: int,
) -> list[dict]:
    """
    `queries.liquidation_window` at `bar_seconds` over `[start_ms, end_ms)` (bucket-aligned): the
    stored rows themselves at a stored width, else each bucket composed from its stored `source`
    rows (`_composed_bucket`), present when any of them is. Reads `(end - start) / source` rows.
    Every stored row's liquidation group is whole or refused (`_require_whole_group`).

    A composed bucket is known only when it is *complete*: every `source` bucket it spans, up to the
    instrument's newest stored `source` row (the store's live edge: a later one has not happened
    yet), is stored. The store keeps a row for every observed bucket, traded or not, so a missing
    one is a span nobody observed (a collector outage), and summing the rest would read that span
    as 0. A constituent pruned from the store (`fold.RETAIN_DAYS`: 1m after 30 days, 5m after 90)
    is missing the same way, so a composed bucket straddling the retention edge is null, and one
    wholly past it is no bucket at all (a gap). Every width the chart offers
    (`frontend/src/timeframes.ts`) is stored or composes from 1D, which is never pruned.

    Known limit: an outage running up to the store's live edge is not a missing row yet (nothing
    newer was stored), so the forming composed bucket sums what was observed, as the forming stored
    bucket does, until the next row lands. Upgrade path: judge completeness against the capture
    coverage record (§1.16) instead of the store's rows.
    """
    rows = [
        _require_whole_group(row)
        for row in queries.liquidation_window(db, instrument_id, source, start_ms, end_ms)
    ]
    if source == bar_seconds:
        return rows
    since_ns = queries.liquidation_feed_since(db, instrument_id)
    known_from = None if since_ns is None else first_bucket_at_or_after(since_ns, bar_seconds)
    live_edge = queries.newest_row_t(db, instrument_id, source, _NO_BOUND_MS)
    groups: dict[int, list[dict]] = {}
    for row in rows:
        groups.setdefault(bucket_start_ms(row["t"], bar_seconds), []).append(row)
    return [
        _composed_bucket(t, group, known_from, _complete(t, group, bar_seconds, source, live_edge))
        for t, group in groups.items()
    ]


def _complete(
    t: int, group: list[dict], bar_seconds: int, source: int, live_edge: int | None
) -> bool:
    """Whether `group` holds every `source` bucket of the bucket `t`, up to the store's live edge."""
    end = t + bar_seconds * 1000
    if live_edge is not None:
        end = min(end, live_edge + source * 1000)
    present = {row["t"] for row in group}
    return all(s in present for s in range(t, end, source * 1000))


def _composed_bucket(t: int, group: list[dict], known_from: int | None, complete: bool) -> dict:
    """
    One wide bucket from its stored rows, D-160's rule at the wide width: `liq_*` null when the
    bucket is not `complete` (a constituent missing), when any row's is null (unknown stays
    unknown) or when the bucket starts before the first bucket at or after the store's feed start
    (straddling it), else the exact sums with sizes rescaled to the finest `size_precision`; the
    precisions are the rows' finest.
    """
    price_p = max(
        (r["price_precision"] for r in group if r["price_precision"] is not None), default=None
    )
    size_p = max(
        (r["size_precision"] for r in group if r["size_precision"] is not None), default=None
    )
    out = {"t": t, "liq_long_v": None, "liq_short_v": None, "liq_n": None}
    straddles = known_from is not None and t < known_from
    if complete and not straddles and all(r["liq_n"] is not None for r in group):
        scales = [_size_scale(r, size_p) for r in group]
        out["liq_long_v"] = sum(r["liq_long_v"] * k for r, k in zip(group, scales, strict=True))
        out["liq_short_v"] = sum(r["liq_short_v"] * k for r, k in zip(group, scales, strict=True))
        out["liq_n"] = sum(r["liq_n"] for r in group)
    return {**out, "price_precision": price_p, "size_precision": size_p}


def _size_scale(row: dict, size_p: int | None) -> int:
    """`10^(size_p - row's size_precision)`; a row holding a size without a precision is corrupt."""
    if row["size_precision"] is None or size_p is None:
        if row["liq_long_v"] or row["liq_short_v"]:
            raise ValueError(f"candle row t={row['t']} holds liquidation sizes without a precision")
        return 1
    return 10 ** (size_p - row["size_precision"])


def _by_bucket(rows: list[Liquidation], bar_seconds: int) -> dict[int, list[Liquidation]]:
    buckets: dict[int, list[Liquidation]] = {}
    for row in rows:
        buckets.setdefault(bucket_start_ms(row.ts_event // _MS, bar_seconds), []).append(row)
    return buckets


def _side_notionals(
    bar: dict, archived: list[Liquidation]
) -> tuple[int | None, int | None, int | None]:
    """
    `(long_notional_units, short_notional_units, notional_precision)` of one stored bucket: None
    while its `liq_n` is null (unknown), else each side's `Σ notional_units()` of the bucket's
    archived rows rescaled to their finest `price_precision + size_precision` (0 for a side with
    no rows, and both 0 at the bar's own `pp + sp` when there are none), served only when the
    archive holds exactly `liq_n` of them (Story 33.5 adds the per-side split, so the chart's
    mirrored notional bars need no browser arithmetic).

    Exact by capture's order: a flush writes the rows to the archive first and only then applies
    them to the candle store (`CaptureService._flush_once` -> `_apply_to_candle_store`), and the
    catch-up and rebuild read the archive, so every liquidation the store counted is archived. The
    archive holding exactly `liq_n` rows of the bucket is therefore the counted set itself. The live
    bus's unflushed tail is never counted by the store, so it is not summed here.

    Known limit (live-edge lag): between a flush's archive write and its store apply, and after a
    store apply that failed (ledgered `collector.candle_store`, repaired by the next start's
    catch-up or the rebuild), the archive holds more rows than `liq_n`; the store's count then
    cannot be matched to a set of rows, so every notional is None for that bucket, never a partial
    sum (a known 0 beside archived rows included). Upgrade path: the store keeps the per-bucket
    notional sums as folded columns.
    """
    n = bar["liq_n"]
    if n is None or len(archived) != n:
        return None, None, None
    if not archived:
        pp, sp = bar["price_precision"], bar["size_precision"]
        return 0, 0, None if pp is None or sp is None else pp + sp
    finest = max(row.price_precision + row.size_precision for row in archived)

    def side_sum(side: LiquidatedSide) -> int:
        return sum(
            row.notional_units() * 10 ** (finest - row.price_precision - row.size_precision)
            for row in archived
            if row.side == side
        )

    return side_sum(LiquidatedSide.LONG), side_sum(LiquidatedSide.SHORT), finest


def _liquidation_bar_item(bar: dict, archived: list[Liquidation]) -> dict:
    long_units, short_units, precision = _side_notionals(bar, archived)
    notional = None if long_units is None or short_units is None else long_units + short_units
    return {
        "t": bar["t"],
        "long_v": bar["liq_long_v"],
        "short_v": bar["liq_short_v"],
        "n": bar["liq_n"],
        "size_precision": bar["size_precision"],
        "notional_units": notional,
        "notional_precision": precision,
        "long_notional_units": long_units,
        "short_notional_units": short_units,
    }


def liquidation_bars(
    instrument_id: str,
    before_ns: int,
    limit: int,
    bar_seconds: int,
    *,
    catalog_path: str,
    candles_dir: str,
) -> tuple[list[dict], bool]:
    """
    Return the newest `limit` stored buckets' liquidation sums before `before_ns`, oldest first,
    gap-marked, *including untraded buckets* (`queries.liquidation_window`, audit D-162's upgrade
    path), a width the store does not fold composed from `stored_bar`'s rows (1W from 1D) and one
    no stored width tiles refused (`UnsupportedBarSeconds`): `{t (ms), long_v, short_v (units of
    10^-size_precision), n, size_precision,
    notional_units, notional_precision}`. `long_v/short_v/n` and their null-ness are the store's
    own (D-160's feed-start rule: null before or straddling the feed start and for an id without
    the feed, 0 in a known bucket none landed in); the notional is summed from the archived rows
    (`_side_notionals`, with `long_notional_units`/`short_notional_units` its per-side split at
    the same precision and null rule), so it agrees with the screener's `liq_notional_1h`.
    """
    if is_spot(instrument_id):
        return [], False
    _require_stored_bar(bar_seconds)

    def read() -> tuple[list[dict], bool]:
        bars, has_more = _store_liquidation_rows(
            candles_dir, instrument_id, before_ns, limit, bar_seconds
        )
        archived: dict[int, list[Liquidation]] = {}
        if bars and has_liquidation_feed(instrument_id):
            # One UTC day per read, `query_liquidations`' bound (MEM-01): a 1D or 1W page spans
            # up to `MAX_QUERY_SPAN_SECONDS` of cascades. The days are disjoint in `ts_event`, so
            # each venue event is read once.
            end_ns = (bars[-1]["t"] + bar_seconds * 1000) * _MS
            rows = [
                row
                for lo, hi in day_slices(bars[0]["t"] * _MS, end_ns)
                for row in catalog_files.query_liquidations(
                    catalog_path, instrument_id, lo, hi - 1, on_foreign=error_ledger.record
                )
            ]
            archived = _by_bucket(rows, bar_seconds)
        items = [_liquidation_bar_item(bar, archived.get(bar["t"], [])) for bar in bars]
        return with_gap_markers(items, bar_seconds), has_more

    return _guarded(instrument_id, read)
