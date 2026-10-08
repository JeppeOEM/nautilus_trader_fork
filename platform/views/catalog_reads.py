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
The catalog series reads every chart view pages through: whole `DydxSecondSnapshot` rows for a
`ts_event` window (`query_second_snapshots`), and the cursor-paging helpers the scroll-back pages
share (`fetch_page`, `has_older_data`), which walk the catalog's own file ranges
(`kernel.catalog_files.data_file_ranges`) rather than fixed-size probe windows.

Moved verbatim from the catalog-stats module and `data_api/routes/paging.py` (Story 24.2).
"""

from collections.abc import Callable
from dataclasses import dataclass

from candles.application import queries
from kernel.catalog_files import liquidation_feed_since_ns
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.venues import venue_of
from observability import error_ledger

from nautilus_trader.persistence.catalog import ParquetDataCatalog


@dataclass(frozen=True)
class InstrumentPrecision:
    """The decimal places a venue instrument's prices and sizes are quoted at."""

    price_precision: int
    size_precision: int


class NoInstrumentDefinition(LookupError):
    """The catalog holds no instrument definition for the id: no precision may be assumed."""


def instrument_precision(catalog_path: str, instrument_id: str) -> InstrumentPrecision:
    """
    Return the catalog's own `price_precision`/`size_precision` for `instrument_id` (the latest stored
    definition by `ts_init`), so a chart label is never printed at a guessed precision (DATA-01).

    Never derived from a value's digit count and never defaulted: an id without a definition
    raises `NoInstrumentDefinition` naming it.
    """
    found = ParquetDataCatalog(catalog_path).instruments(instrument_ids=[instrument_id])
    if not found:
        raise NoInstrumentDefinition(f"{instrument_id}: no instrument definition in the catalog")
    latest = max(found, key=lambda i: i.ts_init)
    return InstrumentPrecision(latest.price_precision, latest.size_precision)


def liquidation_feed_start(catalog_path: str, candles_dir: str, instrument_id: str) -> int | None:
    """
    Return the liquidation feed start an archive-side fold of `instrument_id` is bounded by (the
    one feed-start rule, `docs/DATA_DICTIONARY.md` §2.15): the candle store's persisted
    `liquidation_feed_since` row (one indexed SELECT, `queries.liquidation_feed_since`), else --
    only when the store has no row or no file -- the archive's first liquidation
    (`kernel.catalog_files.liquidation_feed_since_ns`, a file-name walk plus one column read). The
    caller lowers it to its own rows (`candles.domain.fold.archive_liquidations`). None: no start
    known, every bucket's `liq_*` null.

    Known limit: the store's start is the earliest liquidation the live sink or a rebuild has
    applied; an archive holding older ones no rebuild has reached yet (history archived before the
    store existed) is not consulted while the store has a row, so that span reads null -- unknown,
    never a false 0 -- until `candles.rebuild` of it lowers the stored start. Upgrade path: the
    operator's full-history rebuild (DEPLOY_CHECKLIST 33-3), after which the two agree.
    """
    with queries.open_store(candles_dir, venue_of(instrument_id)) as db:
        stored = None if db is None else queries.liquidation_feed_since(db, instrument_id)
    if stored is not None:
        return stored
    return liquidation_feed_since_ns(catalog_path, instrument_id, on_foreign=error_ledger.record)


def query_second_snapshots(
    catalog_path: str,
    instrument_id: str,
    start_ns: int,
    end_ns: int,
) -> list[DydxSecondSnapshot]:
    """
    DydxSecondSnapshot rows for `instrument_id` with `ts_event` in [start_ns, end_ns],
    CustomData-unwrapped. Files are chosen over the `ts_init` span widened by `READ_SPAN_MARGIN_NS`
    on both sides, so a row whose venue clock ran ahead of ours or behind it is still read.

    Shared by dashboard.py's _historical_lines_json and custom_indicators.py's
    _second_snapshots -- both projected different fields off this same query, so only
    the catalog-query + CustomData-unwrap boilerplate lives here.
    """
    catalog = ParquetDataCatalog(catalog_path)
    # `query` bounds on ts_init, the window is ts_event, and a row's skew runs either way
    # (`kernel.clocks.READ_SPAN_MARGIN_NS`): a venue-timed row (story 22.12) is sampled up to
    # 1 + hold_back s (a catch-up: more) after its ts_event, so the end is widened; a venue clock
    # running ahead of ours stamps `ts_init < ts_event`, so the start is widened too (clamped at
    # 0). The exact ts_event filter below decides -- the same span `query_second_ohlc` reads.
    results = catalog.query(
        data_cls=DydxSecondSnapshot,
        identifiers=[instrument_id],
        start=max(0, start_ns - READ_SPAN_MARGIN_NS),
        end=end_ns + READ_SPAN_MARGIN_NS,
    )
    # query() wraps custom Data subclasses in CustomData -- unwrap via .data to reach the
    # actual DydxSecondSnapshot (confirmed via direct introspection this session).
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    return [s for s in snapshots if start_ns <= s.ts_event <= end_ns]


def has_older_data(ranges: list[tuple[int, int]], ns: int) -> bool:
    return bool(ranges) and ranges[0][0] < ns


def _unaligned(ns: int) -> int:
    return ns


def fetch_page[T](
    fetch: Callable[[int, int], list[T]],
    ranges: list[tuple[int, int]],
    before_ns: int,
    span_ns: int,
    align_end: Callable[[int], int] = _unaligned,
) -> list[T]:
    """
    First non-empty `fetch(start_ns, end_ns)` walking back from `before_ns` in `span_ns`
    windows, jumping over gaps straight to the last data before each empty window. `[]` only
    when nothing older exists at all.

    `align_end` maps every window end -- the first (`before_ns`) and each gap-jump target -- to
    the end the window actually uses; a bar page passes "up to the next bucket boundary" so that,
    with a `span_ns` of whole buckets, no window starts or ends inside a bucket (Story 31.8). It
    must never move an end past a later bucket boundary than the one at or above its input, or a
    gap jump could fail to progress: `align_end(x) <= start_ns` holds for every `x <= start_ns`
    exactly when `start_ns` is itself a fixed point, which a whole-bucket span from an aligned
    end guarantees. The default keeps the end as given (the per-second pages).

    An aligned page's `fetch` reads `[start_ns, end_ns)`: a row stamped on the boundary opens the
    next bucket. `data_file_ranges` ends are the *inclusive* last `ts_event`, so an aligned gap
    jump targets `last + 1`: a file whose last row lies exactly on a boundary `B` would otherwise
    align to `B` itself, and its row at `B` -- bucket `B` as far as that file holds it -- would
    never be read. The default pages' `fetch` is inclusive at both ends, so they jump to `last`
    exactly as before.
    """
    past_last = 0 if align_end is _unaligned else 1
    end_ns = align_end(before_ns)
    while True:
        start_ns = end_ns - span_ns
        result = fetch(start_ns, end_ns)
        if result:
            return result
        older_ends = [end for start, end in ranges if start < start_ns]
        if not older_ends:
            return []
        # min(): always progress, even mid-file
        end_ns = align_end(min(max(older_ends) + past_last, start_ns))
