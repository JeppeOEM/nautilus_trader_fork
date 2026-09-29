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
`CatalogFrames`: the `MarketFrames` port over the Parquet catalog and the candle store (Story 27.1).

Every read is time-bounded (MEM-01): the typed `ParquetDataCatalog.query` always gets `start=` and
`end=` (`research/tests/test_research_reads.py` fails an unbounded one), index prices come through
`kernel.catalog_files.query_index_prices` (the catalog cannot decode them in the pinned Nautilus),
and bars through the candle store's query service (`candles.application.queries.window`). The
window is half-open `[start, end)` on `ts_event` for every frame.
"""

from pathlib import Path

import pandas as pd
from candles.application.queries import bucket_starts
from candles.application.queries import newest_t
from candles.application.queries import oldest_t
from candles.application.queries import open_store
from candles.application.queries import window
from candles.domain.fold import BAR_SECONDS
from kernel.catalog_files import query_index_prices
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_MS
from kernel.indicators import MultiLevelOBI
from kernel.indicators import microprice
from kernel.indicators import mid_price
from kernel.indicators import spread
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.venues import asset_key
from kernel.venues import venue_of

from nautilus_trader.model.data import CustomData
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from research.application.ports import window_ns


# Book levels each `obi_<N>` column sums (`MultiLevelOBI(levels=N)`); 20 is the stored depth.
OBI_LEVELS = (1, 5, 10, 20)
_SNAPSHOT_FIELDS = (
    "price_precision",
    "size_precision",
    "bid_prices",
    "bid_sizes",
    "ask_prices",
    "ask_sizes",
    "buy_volume",
    "sell_volume",
    "buy_count",
    "sell_count",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "ts_init",
)
SECONDS_COLUMNS = (
    "ts_event",
    *_SNAPSHOT_FIELDS,
    "mid",
    "spread",
    "microprice",
    *(f"obi_{n}" for n in OBI_LEVELS),
)
_NULLABLE_SECONDS_COLUMNS = (
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "mid",
    "spread",
    "microprice",
    *(f"obi_{n}" for n in OBI_LEVELS),
)
TRADES_COLUMNS = ("ts_event", "price", "size", "aggressor_side", "trade_id", "ts_init")
BARS_COLUMNS = ("ts_event", "t", "o", "h", "l", "c", "v", "seconds_observed", "partial")
FUNDING_COLUMNS = ("ts_event", "rate", "interval", "next_funding_ns", "ts_init")
OPEN_INTEREST_COLUMNS = ("ts_event", "open_interest", "ts_init")
MARK_INDEX_COLUMNS = ("ts_event", "mark", "index", "ts_init")


def _frame(rows: list[dict], columns: tuple[str, ...]) -> pd.DataFrame:
    """Rows -> a frame indexed by UTC `ts` (from `ts_event`), keeping `ts_event` as a column."""
    frame = pd.DataFrame(rows, columns=list(columns))
    frame["ts_event"] = frame["ts_event"].astype("int64")
    frame.index = pd.DatetimeIndex(
        pd.to_datetime(frame["ts_event"], unit="ns", utc=True), name="ts"
    )
    return frame


def _obi(indicator: MultiLevelOBI, bid_sizes: list[float], ask_sizes: list[float]) -> float | None:
    """
    Read one row statelessly: None (a gap) when a side is empty -- like `mid`/`spread`/
    `microprice` -- or the levels hold no size; never the indicator's last value.
    """
    if not bid_sizes or not ask_sizes:
        return None
    indicator.reset()
    indicator.update_raw(bid_sizes, ask_sizes)
    return indicator.value if indicator.initialized else None


class CatalogFrames:
    """
    `MarketFrames` over one catalog root and one candles directory.

    Invariant: holds no market data between calls -- each read goes to the catalog or the candle
    store for exactly its `[start, end)` window, so two reads can never disagree through a cache.
    """

    def __init__(self, catalog_path: str, candles_dir: str) -> None:
        self._catalog_path = catalog_path
        self._candles_dir = candles_dir
        self._catalog = ParquetDataCatalog(catalog_path)

    def _query(self, data_cls: type, instrument_id: str, start: str | int, end: str | int) -> list:
        start_ns, end_ns = window_ns(start, end)
        # The catalog bounds (and orders) rows by `ts_init`, the receive clock; the frame's window
        # is `ts_event`. A row's `ts_init` trails its `ts_event` by at most `MAX_TS_INIT_SKEW_NS`
        # (and may lead it by a clock step), so read `ts_init` widened by the bound on both sides,
        # then keep exactly `start <= ts_event < end`, ordered by `ts_event`.
        rows = self._catalog.query(
            data_cls,
            identifiers=[instrument_id],
            start=max(0, start_ns - MAX_TS_INIT_SKEW_NS),
            end=end_ns + MAX_TS_INIT_SKEW_NS,
        )
        data = [row.data if isinstance(row, CustomData) else row for row in rows]
        inside = [row for row in data if start_ns <= row.ts_event < end_ns]
        return sorted(inside, key=lambda row: row.ts_event)

    def seconds(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame:
        """
        Return the `DydxSecondSnapshot` rows (the row's stored `price_precision`/`size_precision`,
        then book lists, folded trades and OHLC as the kernel's decoded floats) plus `mid`, `spread`,
        `microprice` (`kernel.indicators`' functions) and `obi_<N>` for `OBI_LEVELS`
        (`MultiLevelOBI`); a derived value is None where its side of the book is empty.

        Known limit: bounded but not capped -- the whole window is materialised at once (every
        snapshot object, then a row holding its four top-20 book lists, several KB per second), so
        a multi-week window of one instrument can exhaust a small host (the 3.7 GB VPS); read a
        long span day by day. Upgrade path: an iterator of per-day frames.
        """
        indicators = {n: MultiLevelOBI(levels=n) for n in OBI_LEVELS}
        rows = []
        for snapshot in self._query(DydxSecondSnapshot, instrument_id, start, end):
            # The kernel's stateless functions take the decoded float view (`as_floats()`); the
            # frame's own columns are read as attributes (only `DydxSecondSnapshot` parses its
            # stored integer layout, AD-D3).
            payload = snapshot.as_floats()
            row = {name: getattr(snapshot, name) for name in ("ts_event", *_SNAPSHOT_FIELDS)}
            row["mid"] = mid_price(payload)
            row["spread"] = spread(payload)
            row["microprice"] = microprice(payload)
            for n, indicator in indicators.items():
                row[f"obi_{n}"] = _obi(indicator, snapshot.bid_sizes, snapshot.ask_sizes)
            rows.append(row)
        frame = _frame(rows, SECONDS_COLUMNS)
        # None (no trade, an empty side) -> NaN, so a gap reads the same in a 1-row or n-row frame.
        return frame.astype(dict.fromkeys(_NULLABLE_SECONDS_COLUMNS, "float64"))

    def trades(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame:
        """Return the raw `TradeTick` archive (Story 22.13; kept `--trade-retention-days`)."""
        return _frame(
            [
                {
                    "ts_event": tick.ts_event,
                    "price": tick.price.as_double(),
                    "size": tick.size.as_double(),
                    "aggressor_side": tick.aggressor_side.name,
                    "trade_id": str(tick.trade_id),
                    "ts_init": tick.ts_init,
                }
                for tick in self._query(TradeTick, instrument_id, start, end)
            ],
            TRADES_COLUMNS,
        )

    def bars(
        self, instrument_id: str, bar_seconds: int, *, start: str | int, end: str | int
    ) -> pd.DataFrame:
        """
        Traded candles from the venue's candle store whose whole span `[t, t + bar_seconds)` lies
        in `[start, end)` -- a bar straddling `end` holds trades after it, so it is left out, never
        leaked into the window (`o..c` None-free, `partial` when under 90% of the span was
        observed); a bucket with no trade is absent. `bar_seconds` must be a size the store keeps
        (`BAR_SECONDS`), and `start` and `end` must lie within the store's coverage (its oldest
        bucket's open to its newest bucket's close, traded or not), with every bucket in between
        stored: a window reaching back past it (history lost to retention), forward past it (a
        store behind the archive, a future `end`) or across a bucket never observed (a collector
        outage) raises `ValueError` naming the first missing bucket, so missing history never reads
        as "no trades".
        """
        if bar_seconds not in BAR_SECONDS:
            raise ValueError(f"the candle store keeps {BAR_SECONDS} second bars, not {bar_seconds}")
        start_ns, end_ns = window_ns(start, end)
        start_ms, end_ms = start_ns // NS_PER_MS, -(-end_ns // NS_PER_MS)
        bar_ms = bar_seconds * 1000
        limit = -(-(end_ms - start_ms) // bar_ms) + 1
        venue = venue_of(instrument_id)
        with open_store(self._candles_dir, venue) as db:
            if db is None:
                raise FileNotFoundError(
                    f"no candle store for {venue} in {self._candles_dir} "
                    f"(expected {Path(self._candles_dir) / f'candles_{venue.lower()}.db'})"
                )
            oldest = oldest_t(db, instrument_id, bar_seconds, traded_only=False)
            if oldest is None or start_ns < oldest * NS_PER_MS:
                raise ValueError(
                    f"{instrument_id} {bar_seconds}s bars: the {venue} candle store covers from "
                    f"{oldest} ms (None = nothing stored), after the window's start {start_ns} ns"
                )
            newest = newest_t(db, instrument_id, bar_seconds)
            if newest is None or end_ns > (newest + bar_ms) * NS_PER_MS:
                raise ValueError(
                    f"{instrument_id} {bar_seconds}s bars: the {venue} candle store covers until "
                    f"{newest} + {bar_ms} ms, before the window's end {end_ns} ns"
                )
            first_ms = -(-start_ns // (bar_ms * NS_PER_MS)) * bar_ms
            last_ms = end_ns // (bar_ms * NS_PER_MS) * bar_ms  # exclusive: the first bar past end
            missing = sorted(
                set(range(first_ms, last_ms, bar_ms))
                - set(bucket_starts(db, instrument_id, bar_seconds, first_ms, last_ms))
            )
            if missing:
                raise ValueError(
                    f"{instrument_id} {bar_seconds}s bars: {len(missing)} bucket(s) in the window "
                    f"were never observed (a collector outage), the first at {missing[0]} ms"
                )
            candles = window(db, instrument_id, bar_seconds, end_ms, limit)
        return _frame(
            [
                {"ts_event": c["t"] * NS_PER_MS, **{k: c[k] for k in BARS_COLUMNS[1:]}}
                for c in candles
                if start_ns <= c["t"] * NS_PER_MS and (c["t"] + bar_ms) * NS_PER_MS <= end_ns
            ],
            BARS_COLUMNS,
        )

    def bar_coverage(
        self, instrument_id: str, bar_seconds: int, *, start: str | int, end: str | int
    ) -> list[tuple[int, int]]:
        """
        Return the ns spans `[first bucket start, last bucket close)` of the maximal runs of stored
        buckets (traded or not) whose whole bar lies in `[start, end)`, oldest first; a venue with
        no candle store raises `FileNotFoundError`, as `bars` does (a wrong `CANDLES_DIR` must not
        read as an outage over the whole window). Invariant: `bars` over any one span never raises (every bucket
        in it is stored, and it lies inside the store's coverage), and the buckets between two
        spans were never observed -- so a caller reading span by span sees an outage, or a
        window reaching past the store, as a hole, never as "no trades" and never as an error.
        Reads only `bucket_starts` (the bucket starts, not the candles).
        """
        if bar_seconds not in BAR_SECONDS:
            raise ValueError(f"the candle store keeps {BAR_SECONDS} second bars, not {bar_seconds}")
        start_ns, end_ns = window_ns(start, end)
        bar_ms = bar_seconds * 1000
        first_ms = -(-start_ns // (bar_ms * NS_PER_MS)) * bar_ms
        last_ms = end_ns // (bar_ms * NS_PER_MS) * bar_ms  # exclusive: the first bar past end
        venue = venue_of(instrument_id)
        with open_store(self._candles_dir, venue) as db:
            if db is None:
                raise FileNotFoundError(
                    f"no candle store for {venue} in {self._candles_dir} "
                    f"(expected {Path(self._candles_dir) / f'candles_{venue.lower()}.db'})"
                )
            starts = bucket_starts(db, instrument_id, bar_seconds, first_ms, last_ms)
        spans: list[tuple[int, int]] = []
        for t in starts:
            if spans and spans[-1][1] == t * NS_PER_MS:
                spans[-1] = (spans[-1][0], (t + bar_ms) * NS_PER_MS)
            else:
                spans.append((t * NS_PER_MS, (t + bar_ms) * NS_PER_MS))
        return spans

    def same_symbol(self, instrument_id: str) -> list[str]:
        """
        Return the catalog's instrument definitions (`ParquetDataCatalog.instruments()`) trading the same
        asset as `instrument_id` (`kernel.venues.asset_key`: base, USD/USDC/USDT quote class, perp
        or spot), the id itself included, sorted by venue then id; `[]` when the id has no asset
        key. A definition says the venue lists it, not that the window holds its data: the window
        read decides "collected".
        """
        key = asset_key(instrument_id)
        if key is None:
            return []
        defined = {instrument.id.value for instrument in self._catalog.instruments()}
        same = {iid for iid in defined if asset_key(iid) == key} | {instrument_id}
        return sorted(same, key=lambda iid: (venue_of(iid), iid))

    def funding(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame:
        """`FundingRateUpdate` rows; `rate` is the venue's decimal rate as a float."""
        return _frame(
            [
                {
                    "ts_event": update.ts_event,
                    "rate": float(update.rate),
                    "interval": update.interval,
                    "next_funding_ns": update.next_funding_ns,
                    "ts_init": update.ts_init,
                }
                for update in self._query(FundingRateUpdate, instrument_id, start, end)
            ],
            FUNDING_COLUMNS,
        )

    def open_interest(
        self, instrument_id: str, *, start: str | int, end: str | int
    ) -> pd.DataFrame:
        """`kernel.open_interest.OpenInterest` rows (base-asset units, as the venue reports)."""
        return _frame(
            [
                {
                    "ts_event": row.ts_event,
                    "open_interest": float(row.open_interest),
                    "ts_init": row.ts_init,
                }
                for row in self._query(OpenInterest, instrument_id, start, end)
            ],
            OPEN_INTEREST_COLUMNS,
        )

    def mark_index(self, instrument_id: str, *, start: str | int, end: str | int) -> pd.DataFrame:
        """
        One row per mark or index update, in `ts_event` order: `mark` is set on a mark row and NaN
        on an index row, and vice versa. The two streams are never joined or filled onto each
        other's timestamps -- a basis at a stamp exists only where both streams have a row there.
        """
        start_ns, end_ns = window_ns(start, end)
        marks = self._query(MarkPriceUpdate, instrument_id, start, end)
        indexes = query_index_prices(self._catalog_path, instrument_id, start_ns, end_ns - 1)
        nan = float("nan")
        rows = [
            {
                "ts_event": u.ts_event,
                "mark": u.value.as_double(),
                "index": nan,
                "ts_init": u.ts_init,
            }
            for u in marks
        ]
        rows += [
            {
                "ts_event": r.ts_event,
                "mark": nan,
                "index": r.price.as_double(),
                "ts_init": r.ts_init,
            }
            for r in indexes
        ]
        rows.sort(key=lambda row: row["ts_event"])
        return _frame(rows, MARK_INDEX_COLUMNS)

    def objects(
        self, data_cls: type, instrument_id: str, *, start: str | int, end: str | int
    ) -> list:
        """
        Return the typed rows (`TradeTick`, `MarkPriceUpdate`, `DydxSecondSnapshot`, ...) of the
        window, `ts_event` ascending, through the same bounded read as the frames. `IndexPriceUpdate`
        yields `kernel.catalog_files.IndexPrice` rows (`ts_event`, `ts_init`, an exact `price`):
        the pinned catalog cannot decode index prices, so they come through the kernel's reader,
        as `mark_index` reads them.

        Known limit: every object of the window is materialised at once (a busy instrument's
        trades over a day is ~10^6 objects); read a long span day by day.
        """
        if data_cls is IndexPriceUpdate:
            start_ns, end_ns = window_ns(start, end)
            return query_index_prices(self._catalog_path, instrument_id, start_ns, end_ns - 1)
        return self._query(data_cls, instrument_id, start, end)
