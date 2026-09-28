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
Catalog inspection (Story 27.2): what the archive holds per venue and instrument over one bounded
window -- every number `research/notebooks/01_catalog_inspection` prints comes from here.

Each function takes what a bounded read already returned (a `MarketFrames` frame, its `objects`
rows, a window in ns) or reads only metadata and small stores: instrument definitions, snapshot
file names (`kernel.catalog_files.data_file_ranges`), the candle store's verdicts (read-only query
service) and the 23.3 error ledger (`observability.error_ledger`'s readers, bounded by the same
window). Nothing here reads market data unbounded (MEM-01), and nothing fills or drops a defect:
a gap, a crossed second, a fold mismatch or a precision disagreement is counted and returned
(DATA-01/DATA-07). Gap heuristics have one home, `archive.application.diagnostics` (its pure
`find_gaps` only -- never its unbounded catalog readers).
"""

import itertools
import math
from bisect import bisect_left
from bisect import bisect_right
from collections import Counter
from collections import defaultdict
from collections.abc import Collection
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from archive.application.diagnostics import find_gaps
from candles.application.queries import open_store
from candles.application.queries import verified_status
from kernel.catalog_files import data_file_ranges
from kernel.catalog_files import price_precision_labels
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_MS
from kernel.clocks import NS_PER_S
from kernel.clocks import CatalogFileSpan
from kernel.fold import SecondTradeFields
from kernel.fold import SnapshotTradeValues
from kernel.fold import fold_trades
from kernel.venues import market_kind
from kernel.venues import venue_of
from observability import error_ledger
from observability.error_ledger import PROCESS_START_SITE
from observability.error_ledger import iter_records
from observability.error_ledger import services
from observability.error_ledger import site_counts

from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.persistence.funcs import class_to_filename
from research.application.frames import OBI_LEVELS


INVENTORY_COLUMNS = ("instrument_id", "venue", "market_kind", "type")
COVERAGE_COLUMNS = ("instrument_id", "start", "end", "start_ns", "end_ns", "duration_ms")
GAP_COLUMNS = ("kind", "start", "end", "start_ns", "end_ns", "seconds")
LIKELY_OUTAGE = "likely outage"
BOOK_GAP = "book gap"
QUIET_MARKET = "quiet market"
NO_MARK_COVERAGE = "no mark coverage"
GAP_KINDS = (LIKELY_OUTAGE, BOOK_GAP, NO_MARK_COVERAGE, QUIET_MARKET)
DAY_STATUS_COLUMNS = ("instrument_id", "venue", "day", "status")
# The candle store's reconciliation verdict -> the day's status; None = never verified.
DAY_STATUSES: Mapping[str | None, str] = {"pass": "verified", "fail": "failed", None: "provisional"}
STORE_ABSENT = "store absent"
KNOWN_DAY_STATUSES = (*DAY_STATUSES.values(), STORE_ABSENT)
AGREEMENT_COLUMNS = (
    "day",
    "seconds",
    "trade_seconds",
    "orphan_trade_seconds",
    "shared_seconds",
    "mismatched_seconds",
    "duplicate_trades",
    "trade_buy_volume",
    "trade_sell_volume",
    "snapshot_buy_volume",
    "snapshot_sell_volume",
    "agrees",
)
# The snapshot's stored trade columns, in `SnapshotTradeValues` order.
TRADE_COLUMNS = SnapshotTradeValues._fields
PRECISION_COLUMNS = ("stream", "files", "labels", "instrument_precision", "uniform")
LEDGER_COUNT_COLUMNS = ("service", "site", "count")
LEDGER_RESTART_COLUMNS = ("service", "restarts")
LEDGER_ABSENT = "absent"
LEDGER_EMPTY = "empty"
LEDGER_UNREADABLE = "unreadable"
LEDGER_RECORDS = "records"


def instrument_definitions(catalog_path: str) -> dict[str, Instrument]:
    """Every instrument definition in the catalog, by id (metadata, not market data)."""
    return {i.id.value: i for i in ParquetDataCatalog(catalog_path).instruments()}


def instrument_inventory(catalog_path: str) -> pd.DataFrame:
    """
    One row per instrument definition, sorted by id: `INVENTORY_COLUMNS` (venue and market kind
    from `kernel.venues`, never parsed here) followed by the definition's own fields, through the
    class's `to_dict` staticmethod (`CryptoPerpetual.to_dict(i)`, not `i.to_dict()`).
    """
    rows = [
        {
            "instrument_id": iid,
            "venue": venue_of(iid),
            "market_kind": market_kind(iid),
            **type(instrument).to_dict(instrument),
        }
        for iid, instrument in sorted(instrument_definitions(catalog_path).items())
    ]
    if not rows:
        return pd.DataFrame(columns=list(INVENTORY_COLUMNS))
    frame = pd.DataFrame(rows)
    return frame[[*INVENTORY_COLUMNS, *(c for c in frame.columns if c not in INVENTORY_COLUMNS)]]


def utc_days(start_ns: int, end_ns: int) -> list[str]:
    """Every UTC day (`YYYY-MM-DD`) the half-open window `[start_ns, end_ns)` touches."""
    first, last = start_ns // NS_PER_DAY, -(-end_ns // NS_PER_DAY)
    return [
        datetime.fromtimestamp(day * NS_PER_DAY // NS_PER_S, tz=UTC).date().isoformat()
        for day in range(first, last)
    ]


def _utc(ns: int) -> pd.Timestamp:
    return pd.Timestamp(ns, unit="ns", tz="UTC")


def file_coverage(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> pd.DataFrame:
    """
    Return the instrument's second-snapshot files that can hold a row of the `ts_event` window
    `[start_ns, end_ns)`, ascending: where data was written, from the file names alone (no Parquet
    I/O). A span is the file's `ts_init` (receive-clock) range as written, so it is matched widened
    by `MAX_TS_INIT_SKEW_NS`, exactly as the frame readers widen their `ts_init` query: every file
    whose rows a `MarketFrames.seconds` read of the window can return is listed. A stretch no file
    covers is a stretch with no snapshot.
    """
    spans = [
        (lo, hi)
        for lo, hi in data_file_ranges(catalog_path, instrument_id)
        if CatalogFileSpan(lo, hi).overlaps(start_ns, end_ns - 1, MAX_TS_INIT_SKEW_NS)
    ]
    return pd.DataFrame(
        [
            {
                "instrument_id": instrument_id,
                "start": _utc(lo),
                "end": _utc(hi),
                "start_ns": lo,
                "end_ns": hi,
                "duration_ms": (hi - lo) / NS_PER_MS,
            }
            for lo, hi in spans
        ],
        columns=list(COVERAGE_COLUMNS),
    )


def ts_events(rows: Iterable[Any]) -> list[int]:
    """Return the `ts_event` of each row (typed rows from `MarketFrames.objects`), in order."""
    return [row.ts_event for row in rows]


def _sorted_ints(ts: Iterable[int]) -> list[int]:
    # `find_gaps` indexes positionally, so a Series (indexed by time) is turned into a plain list.
    return sorted(int(t) for t in ts)


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _sampled_across(
    gap: tuple[int, int], seconds: list[int], second_gaps: Sequence[tuple[int, int]]
) -> bool:
    """Whether snapshots bracket the gap and none of their own gaps overlaps it."""
    if not seconds or not seconds[0] <= gap[0] or not gap[1] <= seconds[-1]:
        return False
    return not any(_overlaps(gap, second_gap) for second_gap in second_gaps)


def _gap_row(kind: str, gap: tuple[int, int]) -> dict:
    lo, hi = gap
    return {
        "kind": kind,
        "start": _utc(lo),
        "end": _utc(hi),
        "start_ns": lo,
        "end_ns": hi,
        "seconds": (hi - lo) / NS_PER_S,
    }


def _classify_seconds_gap(gap: tuple[int, int], marks: list[int]) -> str:
    """
    Classify a seconds gap by the mark stream (a second feed the collector receives, independent
    of the book's gate) over the same span. Marks not
    bracketing it (none at all, as for Bybit spot, or a feed that ended or started inside it)
    cannot tell. Otherwise the longest mark silence inside the gap decides: silent for at least half
    of it -> nothing reached the collector (`likely outage`); shorter -> the venue kept publishing
    while the gate wrote no snapshot (`book gap`). Half, not all: after a reconnect the marks
    resume before the book is resynced, so a real outage's mark silence ends a little early.
    """
    lo, hi = gap
    first_inside = bisect_right(marks, lo)
    past_inside = bisect_left(marks, hi)
    if first_inside == 0 or past_inside == len(marks):
        return NO_MARK_COVERAGE
    points = [lo, *marks[first_inside:past_inside], hi]
    silence = max(b - a for a, b in itertools.pairwise(points))
    return LIKELY_OUTAGE if 2 * silence >= hi - lo else BOOK_GAP


def gap_report(
    seconds_ts: Iterable[int], trade_ts: Iterable[int], mark_ts: Iterable[int]
) -> pd.DataFrame:
    """
    Classify the window's gaps (each `(last row before, first row after)`, `find_gaps`' heuristic
    and its 30 s floor -- a shorter hole is not flagged, its documented Known limit). Every seconds
    gap is one row, classified whole by the mark stream (a second feed, independent of the book's
    gate) over that span:

    - `likely outage`: the marks were silent for at least half of it too -- nothing reached the
      collector;
    - `book gap`: marks kept flowing -- the gate skipped a stale, crossed or one-sided book;
    - `no mark coverage`: the mark stream does not bracket it (no marks at all, e.g. Bybit spot,
      or a feed that ended/started inside it), so the two cannot be told apart;

    plus `quiet market`: a trade gap the book was sampled across -- inside the seconds' own span
    and overlapping no seconds gap -- so nobody traded. Invariant: every seconds gap is reported,
    sorted by start; `quiet market` is only ever claimed where snapshots prove the collector was
    sampling. Known limit: a trade gap overlapping a seconds gap, or reaching past the first or
    last snapshot of the window (trades can exist there: a reconnect's REST backfill archives
    them), is not reported -- while the collector was not sampling it cannot tell whether trading
    paused too; the seconds gap row and the coverage timeline show that stretch. Upgrade path:
    report the part of such a trade gap that lies inside sampled seconds as its own
    `quiet market` row. A hole at the window's edge has no row on one side, so it is not a gap
    here; the coverage timeline shows it.
    """
    marks = _sorted_ints(mark_ts)
    seconds = _sorted_ints(seconds_ts)
    second_gaps = find_gaps(seconds)
    rows = [_gap_row(_classify_seconds_gap(gap, marks), gap) for gap in second_gaps]
    rows += [
        _gap_row(QUIET_MARKET, gap)
        for gap in find_gaps(_sorted_ints(trade_ts))
        if _sampled_across(gap, seconds, second_gaps)
    ]
    rows.sort(key=lambda row: row["start_ns"])
    return pd.DataFrame(rows, columns=list(GAP_COLUMNS))


def day_status(
    candles_dir: str, instrument_ids: Sequence[str], days: Sequence[str]
) -> pd.DataFrame:
    """
    Each instrument-day's candle-store status: `verified` (its kline reconciliation passed),
    `failed`, `provisional` (never reconciled: a live or not-yet-rebuilt day), or `store absent`
    when the venue has no store file -- never an exception. Read-only (`open_store`).
    """
    by_venue: dict[str, list[str]] = defaultdict(list)
    for iid in instrument_ids:
        by_venue[venue_of(iid)].append(iid)
    rows = []
    for venue, iids in by_venue.items():
        with open_store(candles_dir, venue) as db:
            for iid in iids:
                for day in days:
                    if db is None:
                        status = STORE_ABSENT
                    else:
                        verdict = verified_status(db, iid, day)
                        status = DAY_STATUSES.get(verdict, f"unrecognised verdict {verdict!r}")
                    rows.append(
                        {"instrument_id": iid, "venue": venue, "day": day, "status": status}
                    )
    return pd.DataFrame(rows, columns=list(DAY_STATUS_COLUMNS))


def _day_of_second(second: int) -> str:
    return datetime.fromtimestamp(second, tz=UTC).date().isoformat()


def _dedupe(trades: Iterable[TradeTick]) -> tuple[dict[int, list[TradeTick]], Counter[str]]:
    """Trades by floor second, each `trade_id` once (the first); duplicates counted per day."""
    seen: set[str] = set()
    by_second: dict[int, list[TradeTick]] = defaultdict(list)
    duplicates: Counter[str] = Counter()
    for trade in trades:
        second = trade.ts_event // NS_PER_S
        trade_id = trade.trade_id.value
        if trade_id in seen:
            duplicates[_day_of_second(second)] += 1
            continue
        seen.add(trade_id)
        by_second[second].append(trade)
    return by_second, duplicates


def _same(expected: SnapshotTradeValues, stored: Sequence[float]) -> bool:
    """Exact equality per column; an empty OHLC (None) equals the stored NaN, nothing else does."""
    for want, got in zip(expected, stored, strict=True):
        if want is None:
            if not math.isnan(got):
                return False
        elif want != got:
            return False
    return True


_AGREEMENT_COUNTS = AGREEMENT_COLUMNS[1:7]
# A day agrees only when none of these is non-zero; duplicates are folded once, as live.
_DISAGREEMENTS = ("orphan_trade_seconds", "shared_seconds", "mismatched_seconds")


def _new_day() -> dict[str, Any]:
    return {
        **dict.fromkeys(_AGREEMENT_COUNTS, 0),
        "trade_buy": [],
        "trade_sell": [],
        "snapshot_buy": [],
        "snapshot_sell": [],
    }


def _stored_rows(seconds: pd.DataFrame) -> dict[int, list[tuple]]:
    """Snapshot rows' trade columns by floor second (a list: two rows in one second is a defect)."""
    by_second: dict[int, list[tuple]] = defaultdict(list)
    columns = [seconds[name].tolist() for name in TRADE_COLUMNS]
    for ts_event, *stored in zip(seconds["ts_event"].tolist(), *columns, strict=True):
        by_second[ts_event // NS_PER_S].append(tuple(stored))
    return by_second


def _volume(parts: list[Quantity]) -> float:
    """
    Exact sum of `Quantity`s (raw integers, one fixed scale), as a float for display. Summed as a
    `Decimal`, not a `Quantity`: a busy day's total can exceed `QUANTITY_RAW_MAX`.
    """
    return float(Decimal(sum(q.raw for q in parts)).scaleb(-FIXED_PRECISION))


def fold_agreement(
    trades: Iterable[TradeTick],
    seconds: pd.DataFrame,
    window: tuple[int, int] | None = None,
) -> pd.DataFrame:
    """
    Per UTC day, re-run the one trade fold (`kernel.fold.fold_trades`, Story 22.13) over the raw
    trade archive and compare each snapshot second's eight stored trade columns with it, exactly
    (no tolerance; an empty OHLC must be NaN, an untraded second 0 volumes and counts).

    A trade belongs to the row whose `ts_event // 1 s` equals its own (`rebuild_day`'s mapping); a
    `trade_id` seen twice is folded once and counted in `duplicate_trades`. A trade second with no
    snapshot row is an `orphan_trade_seconds` (the collector was not sampling); `seconds` counts the
    distinct seconds holding a snapshot row, like every other `_seconds` column; a second holding
    two rows is ambiguous (`rebuild_day` refuses it) and counted in `shared_seconds`, not
    compared. Invariant: `agrees` is True only with 0 mismatched, orphan and shared seconds.
    Volume sums are display-only: trades summed exactly (`Quantity.raw`), snapshots as stored.
    Live seconds fold by arrival, so a day not yet rebuilt (`provisional`) may disagree at second
    granularity -- read `agrees` beside the day's status.

    With `window` (`[start_ns, end_ns)` of the reads), a second only partly inside it is left out:
    its trades were read only in part, so comparing it would report a mismatch that is not there.
    """
    if window is not None:
        first, past = -(-window[0] // NS_PER_S), window[1] // NS_PER_S
        trades = [t for t in trades if first <= t.ts_event // NS_PER_S < past]
        keys = seconds["ts_event"].to_numpy(dtype="int64") // NS_PER_S
        seconds = seconds.loc[(keys >= first) & (keys < past)]
    by_second, duplicates = _dedupe(trades)
    folds: dict[int, SecondTradeFields] = {s: fold_trades(ts) for s, ts in by_second.items()}
    stored = _stored_rows(seconds)
    days: dict[str, dict[str, Any]] = defaultdict(_new_day)
    for second, fields in folds.items():
        day = days[_day_of_second(second)]
        day["trade_seconds"] += 1
        day["orphan_trade_seconds"] += second not in stored
        day["trade_buy"] += [fields.buy_volume] if fields.buy_volume is not None else []
        day["trade_sell"] += [fields.sell_volume] if fields.sell_volume is not None else []
    empty = SecondTradeFields().snapshot_values()
    for second, rows in stored.items():
        day = days[_day_of_second(second)]
        day["seconds"] += 1
        day["snapshot_buy"] += [row[TRADE_COLUMNS.index("buy_volume")] for row in rows]
        day["snapshot_sell"] += [row[TRADE_COLUMNS.index("sell_volume")] for row in rows]
        if len(rows) > 1:
            day["shared_seconds"] += 1
            continue
        expected = folds[second].snapshot_values() if second in folds else empty
        day["mismatched_seconds"] += not _same(expected, rows[0])
    for day_name, count in duplicates.items():
        days[day_name]["duplicate_trades"] += count
    return pd.DataFrame(
        [_agreement_row(name, days[name]) for name in sorted(days)],
        columns=list(AGREEMENT_COLUMNS),
    )


def _agreement_row(name: str, day: dict[str, Any]) -> dict[str, Any]:
    return {
        "day": name,
        **{key: day[key] for key in _AGREEMENT_COUNTS},
        "trade_buy_volume": _volume(day["trade_buy"]),
        "trade_sell_volume": _volume(day["trade_sell"]),
        "snapshot_buy_volume": math.fsum(day["snapshot_buy"]),
        "snapshot_sell_volume": math.fsum(day["snapshot_sell"]),
        "agrees": all(day[key] == 0 for key in _DISAGREEMENTS),
    }


def precision_labels(
    catalog_path: str, instrument: Instrument | None, instrument_id: str, start_ns: int, end_ns: int
) -> pd.DataFrame:
    """
    Return the distinct `price_precision` labels per price stream (trade prices, mark values, index
    prices) across the window's files (`kernel.catalog_files.price_precision_labels`: schema
    metadata only, no row read), beside the instrument definition's `price_precision` (None
    without one); a file with no label shows as None.

    Invariant checked: `uniform` -- one label per stream, none missing, because
    `ParquetDataCatalog` refuses to read files whose labels disagree (the dYdX mark/index incident,
    CLAUDE.md): a catalog read of such a window raises, so the labels are read from the files. A
    stream may legitimately carry another uniform label than the instrument's: dYdX's mark/index
    prices are re-stamped at `FIXED_PRECISION` by design. A stream with no file is uniform.
    """
    rows = []
    for data_cls in (TradeTick, MarkPriceUpdate, IndexPriceUpdate):
        files = price_precision_labels(catalog_path, data_cls, instrument_id, start_ns, end_ns)
        found = {f.price_precision for f in files}
        rows.append(
            {
                "stream": class_to_filename(data_cls),
                "files": len(files),
                "labels": tuple(sorted(found, key=lambda label: -1 if label is None else label)),
                "instrument_precision": None if instrument is None else instrument.price_precision,
                "uniform": len(found) <= 1 and None not in found,
            }
        )
    return pd.DataFrame(rows, columns=list(PRECISION_COLUMNS))


def _spread(seconds: pd.DataFrame) -> np.ndarray:
    """
    Each row's `spread` column (`kernel.indicators.spread`: best ask - best bid, NaN for an empty
    side). For finite doubles `ask - bid <= 0` exactly when `ask <= bid`, so its sign is the
    crossed test with no second reading of the book.
    """
    return seconds["spread"].to_numpy(dtype="float64")


def receive_lag_ms(seconds: pd.DataFrame) -> pd.Series:
    """`ts_init - ts_event` per snapshot row in ms (how long after the second it was written)."""
    lag = seconds["ts_init"].to_numpy(dtype="int64") - seconds["ts_event"].to_numpy(dtype="int64")
    return pd.Series(lag / NS_PER_MS, index=seconds.index, name="ts_init - ts_event (ms)")


def snapshot_sanity(seconds: pd.DataFrame) -> dict[str, Any]:
    """
    Snapshot rows the gate should never have written, counted, never hidden: `crossed` (best bid
    >= best ask, with each row's `crossed_ts`), `negative_spread` (best ask < best bid) and
    `empty_side` (a side with no level), plus the `ts_init - ts_event` quantiles (ms; NaN on an
    empty window). Invariant: `rows` counts every row read, none skipped.
    """
    spread = _spread(seconds)
    two_sided = ~np.isnan(spread)
    crossed = two_sided & (spread <= 0)
    lag = receive_lag_ms(seconds).to_numpy()
    quantiles = np.quantile(lag, [0.0, 0.5, 0.99, 1.0]) if len(lag) else [math.nan] * 4
    return {
        "rows": len(seconds),
        "crossed": int(crossed.sum()),
        "crossed_ts": [int(t) for t in seconds["ts_event"].to_numpy()[crossed]],
        "negative_spread": int((two_sided & (spread < 0)).sum()),
        "empty_side": int((~two_sided).sum()),
        "lag_ms_min": float(quantiles[0]),
        "lag_ms_p50": float(quantiles[1]),
        "lag_ms_p99": float(quantiles[2]),
        "lag_ms_max": float(quantiles[3]),
    }


# Columns derived from the book (`CatalogFrames.seconds`): blank on a crossed second.
BOOK_COLUMNS = frozenset({"mid", "spread", "microprice", *(f"obi_{n}" for n in OBI_LEVELS)})


def second_grid(
    seconds: pd.DataFrame,
    start_ns: int,
    end_ns: int,
    columns: Sequence[str],
    book_columns: Collection[str] = BOOK_COLUMNS,
) -> pd.DataFrame:
    """
    Return `columns` of a seconds-shaped frame (`ts_event`, `spread` and the columns) on the 1 s
    grid of every second `[start_ns, end_ns)` touches, for plotting and per-second statistics,
    indexed by the second's UTC start (`ts`). A row belongs to second `ts_event // 1 s`.

    Invariant: gaps stay gaps -- nothing is forward-filled or interpolated (DATA-01). Every column
    is NaN on a second with no row and on a second holding two rows (ambiguous); each
    `book_columns` column is NaN on a crossed second too (best bid >= best ask, from the row's
    `spread`), while the others (trade columns) keep that second's value -- a crossed book says
    nothing about the trades.
    """
    keys = pd.Series(seconds["ts_event"].to_numpy(dtype="int64") // NS_PER_S)
    shared = keys.duplicated(keep=False).to_numpy()
    first = ~keys.duplicated().to_numpy()
    crossed = _spread(seconds) <= 0  # NaN compares False: an empty side is no crossed book
    # Floor, not ceiling: a row of second S can lie inside a window that starts after S (its
    # `ts_event` is S + 0.5 s on the exchange-timed venues), so S is on the grid.
    grid = np.arange(start_ns // NS_PER_S, -(-end_ns // NS_PER_S), dtype="int64")
    data = {}
    for name in columns:
        values = seconds[name].to_numpy(dtype="float64", copy=True)
        if name in book_columns:
            values[crossed] = math.nan
        values[shared] = math.nan
        by_second = pd.Series(values[first], index=keys.to_numpy()[first])
        data[name] = by_second.reindex(grid).to_numpy(dtype="float64")
    index = pd.DatetimeIndex(pd.to_datetime(grid * NS_PER_S, unit="ns", utc=True), name="ts")
    return pd.DataFrame(data, index=index, columns=list(columns))


def mid_series(seconds: pd.DataFrame, start_ns: int, end_ns: int) -> pd.Series:
    """
    Return the mid price on the 1 s grid of every second `[start_ns, end_ns)` touches, for
    plotting (`second_grid`'s `mid`): NaN on every second with no snapshot row, a crossed book
    (best bid >= best ask), an empty side, or two rows (ambiguous). Invariant: gaps stay gaps --
    nothing is forward-filled or interpolated (DATA-01).
    """
    return second_grid(seconds, start_ns, end_ns, ("mid",))["mid"]


def plot_axis(index: pd.Index) -> np.ndarray:
    """
    Return a UTC `DatetimeIndex` as naive-UTC `datetime64` values for a plot axis: plotly copies a
    tz-aware index element by element, ~40x slower on a day of seconds, and draws the same instants.
    """
    return pd.DatetimeIndex(index).tz_convert(None).to_numpy()


@dataclass(frozen=True)
class LedgerWindow:
    """
    The 23.3 error ledger over one window. Invariant: `state` is `absent` exactly when the ledger
    directory does not exist, `unreadable` when the path is no directory or a ledger file could
    not be read (its lines are
    missing from the counts, so they are a floor, not a total), `empty` when it holds no line in
    the window, else `records`; `counts` are per service and site with the suppressed carry
    folded in and `process_start` excluded (`site_counts`), and `restarts` counts each service's
    `process_start` lines, so a quiet window is told apart from a restarted one.
    """

    state: str
    counts: pd.DataFrame
    restarts: pd.DataFrame


def _read_failures() -> int:
    return error_ledger.counts().get(error_ledger.READ_FAILED_SITE, 0)


def ledger_window(errors_dir: str, start_ns: int, end_ns: int) -> LedgerWindow:
    """
    Return the ledger's rejections and restarts in `[start_ns, end_ns)` (`iter_records` bounds inclusively,
    so it gets `end_ns - 1`). A path that exists but is not a directory (a misconfigured
    `ERRORS_DIR`) is `unreadable`, not `absent`. An unreadable file is recorded by the reader itself at
    `READ_FAILED_SITE` (this process's ledger, logged at ERROR) and skipped; that count moving
    during the read turns the state to `unreadable`. Known limit (the ledger's own): a suppressed
    carry counts at the line that reports it, so totals at the window's edge are approximate by up
    to one cap-bucket.
    """
    counts: list[dict] = []
    restarts: list[dict] = []
    seen_any = False
    failures_before = _read_failures()
    for service in services(errors_dir):
        records = list(iter_records(errors_dir, service, since_ns=start_ns, until_ns=end_ns - 1))
        seen_any = seen_any or bool(records)
        starts = sum(1 for rec in records if rec["site"] == PROCESS_START_SITE)
        if starts:
            restarts.append({"service": service, "restarts": starts})
        counts += [
            {"service": service, "site": site, "count": n}
            for site, n in sorted(site_counts(records).items())
        ]
    if not Path(errors_dir).exists():
        state = LEDGER_ABSENT
    elif not Path(errors_dir).is_dir() or _read_failures() != failures_before:
        state = LEDGER_UNREADABLE
    else:
        state = LEDGER_RECORDS if seen_any else LEDGER_EMPTY
    return LedgerWindow(
        state,
        pd.DataFrame(counts, columns=list(LEDGER_COUNT_COLUMNS)),
        pd.DataFrame(restarts, columns=list(LEDGER_RESTART_COLUMNS)),
    )


def summary(
    instrument_ids: Sequence[str],
    gaps: Mapping[str, pd.DataFrame],
    days: pd.DataFrame,
    agreement: Mapping[str, pd.DataFrame],
    sanity: Mapping[str, Mapping[str, Any]],
    precision: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """
    One row per instrument from the sections' own results (nothing re-read): gap counts by kind,
    day statuses (any status outside the known ones counted in `other_day_status`), fold days disagreeing, crossed/negative-spread/empty-side rows and whether every
    price stream's precision label is uniform.
    """
    rows = []
    for iid in instrument_ids:
        kinds = Counter(gaps[iid]["kind"].tolist())
        statuses = Counter(days.loc[days["instrument_id"] == iid, "status"].tolist())
        rows.append(
            {
                "instrument_id": iid,
                "venue": venue_of(iid),
                "rows": sanity[iid]["rows"],
                **{kind: kinds[kind] for kind in GAP_KINDS},
                **{status: statuses.pop(status, 0) for status in KNOWN_DAY_STATUSES},
                # e.g. "unrecognised verdict 'x'": counted, never dropped (DATA-07)
                "other_day_status": sum(statuses.values()),
                "fold_days_disagreeing": int((~agreement[iid]["agrees"].astype(bool)).sum()),
                "crossed": sanity[iid]["crossed"],
                "negative_spread": sanity[iid]["negative_spread"],
                "empty_side": sanity[iid]["empty_side"],
                "precision_uniform": bool(precision[iid]["uniform"].all()),
            }
        )
    return pd.DataFrame(rows)
