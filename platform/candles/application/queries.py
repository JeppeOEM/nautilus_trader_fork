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
Read models over the candle store: the closed bars charts, indicators and reconciliation read.

Every reader takes a connection the caller owns, so the store stays single-writer: nothing here opens
a file read-write. A reader outside the candles context opens it through `open_store` (read-only), so
it never imports `candles.infrastructure` itself (Story 24.2: the views context calls only these
query services).
"""

import sqlite3
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path

from kernel.liquidation import Liquidation
from kernel.second_snapshot import SecondRow

from candles.domain.candle import is_partial
from candles.domain.fold import AGGREGATE_KEYS
from candles.domain.fold import archive_liquidations
from candles.domain.fold import fold_rows
from candles.infrastructure.sqlite_store import connect_ro
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.sqlite_store import table_columns
from candles.infrastructure.sqlite_store import verified_status as _store_verified_status


_BASE_COLUMNS = ("t", "o", "h", "l", "c", "v", "seconds_observed", "bar_seconds")


def _columns(db: sqlite3.Connection) -> str:
    """
    Return the SELECT list of `_candle`: the base columns, then each Story 33.3 column, or NULL for
    one the file lacks -- a read-only reader may open a file the collector has not migrated yet (it
    never writes, so it cannot migrate), and a missing column is unknown, never 0. Which columns
    exist is read once per `CandleConnection` (`table_columns`).
    """
    present = table_columns(db)
    aggregates = (key if key in present else f"NULL AS {key}" for key in AGGREGATE_KEYS)
    return ", ".join((*_BASE_COLUMNS, *aggregates))


def _candle(row: tuple) -> dict:
    t, o, h, low, c, v, seconds_observed, bar = row[: len(_BASE_COLUMNS)]
    return {
        "t": t,
        "o": o,
        "h": h,
        "l": low,
        "c": c,
        "v": v,
        "seconds_observed": seconds_observed,
        "partial": is_partial(seconds_observed, bar),
        "source": "candle_store",
        **dict(zip(AGGREGATE_KEYS, row[len(_BASE_COLUMNS) :], strict=True)),
    }


@contextmanager
def open_store(candles_dir: str | Path, venue: str) -> Iterator[sqlite3.Connection | None]:
    """
    Open `venue`'s candle store read-only for the readers below: yields the connection, or None when
    the store file does not exist yet (the caller then falls back to the archive).

    The file is the frozen per-venue path (`<candles_dir>/candles_<venue lowercased>.db`, AD-D12);
    it is never opened read-write here, so the collector stays the store's single writer.
    """
    with connect_ro(db_path_for_venue(candles_dir, venue)) as db:
        yield db


def verified_status(db: sqlite3.Connection, iid: str, day: str) -> str | None:
    """
    Return that instrument-day's last kline reconciliation verdict ("pass"/"fail"), or None when
    never verified (a provisional day) -- read-only, over a connection from `open_store`. The
    store's own reader, so a research caller never imports `candles.infrastructure` (Story 27.2).
    """
    return _store_verified_status(db, iid, day)


def liquidation_feed_since(db: sqlite3.Connection, iid: str) -> int | None:
    """
    Return the instrument's persisted liquidation feed start (ns) -- the store's
    `liquidation_feed_since` row, lowered by every live `apply_liquidations` and every rebuild
    (`docs/DATA_DICTIONARY.md` §2.15) -- or None when the store records none (no row, or a file not
    migrated yet, which has no such table). One primary-key SELECT: an archive-side reader takes it
    instead of scanning the archive for the id's first liquidation, and falls back to that scan only
    on None.
    """
    has_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'liquidation_feed_since'"
    ).fetchone()
    if has_table is None:
        return None
    row = db.execute(
        "SELECT since_ns FROM liquidation_feed_since WHERE instrument_id = ?", (iid,)
    ).fetchone()
    return None if row is None else int(row[0])


def window(
    db: sqlite3.Connection, iid: str, bar_seconds: int, before_ms: int, limit: int
) -> list[dict]:
    """Up to `limit` traded candles with t < before_ms, oldest first."""
    rows = db.execute(
        f"SELECT {_columns(db)} FROM candles WHERE instrument_id = ? AND bar_seconds = ? "  # noqa: S608 -- constant column list, values are bound
        "AND o IS NOT NULL AND t < ? ORDER BY t DESC LIMIT ?",
        (iid, bar_seconds, before_ms, limit),
    ).fetchall()
    return [_candle(r) for r in reversed(rows)]


_LIQUIDATION_COLUMNS = ("liq_long_v", "liq_short_v", "liq_n", "price_precision", "size_precision")


def liquidation_window(
    db: sqlite3.Connection, iid: str, bar_seconds: int, start_ms: int, end_ms: int
) -> list[dict]:
    """
    Every stored bucket with `start_ms <= t < end_ms`, oldest first, *traded or not*: `{t,
    liq_long_v, liq_short_v, liq_n, price_precision, size_precision}`. Unlike `window` there is no
    `o IS NOT NULL` filter, so a bucket holding only a liquidation (no trade, or no observed second
    at all) is served -- audit D-162's upgrade path, read by `views.derivatives.liquidation_bars`
    (Story 33.4). The `liq_*` are the store's own: null for an instrument without the feed and for
    a bucket before or straddling its feed start (D-160), 0 for a known bucket none landed in. A
    file the collector has not migrated yet lacks the columns: they read null (unknown), never 0.

    MEM-01: the caller bounds `[start_ms, end_ms)` (`views.chart_series.MAX_QUERY_SPAN_SECONDS`).
    """
    present = table_columns(db)
    columns = ", ".join(c if c in present else f"NULL AS {c}" for c in _LIQUIDATION_COLUMNS)
    rows = db.execute(
        f"SELECT t, {columns} FROM candles WHERE instrument_id = ? AND bar_seconds = ? "  # noqa: S608 -- constant column list, values are bound
        "AND t >= ? AND t < ? ORDER BY t",
        (iid, bar_seconds, start_ms, end_ms),
    ).fetchall()
    return [dict(zip(("t", *_LIQUIDATION_COLUMNS), row, strict=True)) for row in rows]


def newest_row_t(db: sqlite3.Connection, iid: str, bar_seconds: int, before_ms: int) -> int | None:
    """
    Start (ms) of the newest stored bucket with `t < before_ms` of *any* kind (traded, observed only,
    or liquidation-only), None when there is none: where `liquidation_window`'s pages jump a gap to,
    and whether one has older rows (`has_more`). One indexed MAX over the primary key.
    """
    row = db.execute(
        "SELECT MAX(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ? AND t < ?",
        (iid, bar_seconds, before_ms),
    ).fetchone()
    return row[0]


def oldest_t(
    db: sqlite3.Connection, iid: str, bar_seconds: int, traded_only: bool = True
) -> int | None:
    """
    Start of the oldest bucket (ms). `traded_only=False` includes buckets with no trade: the
    earliest observed one is where the store's coverage of the archive begins (a liquidation-only
    row, `seconds_observed = 0`, observed nothing and is never coverage).
    """
    row = db.execute(
        "SELECT MIN(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ?"  # noqa: S608 -- constant clause, values are bound
        + (" AND o IS NOT NULL" if traded_only else " AND seconds_observed > 0"),
        (iid, bar_seconds),
    ).fetchone()
    return row[0]


def newest_t(db: sqlite3.Connection, iid: str, bar_seconds: int) -> int | None:
    """
    Start of the newest observed bucket (ms), traded or not: the store's coverage of the archive
    ends at this bucket's close (the bucket itself may still be forming, flagged `partial`). A
    liquidation-only row (`seconds_observed = 0`) is not coverage.
    """
    row = db.execute(
        "SELECT MAX(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ? "
        "AND seconds_observed > 0",
        (iid, bar_seconds),
    ).fetchone()
    return row[0]


def bucket_starts(
    db: sqlite3.Connection, iid: str, bar_seconds: int, from_ms: int, before_ms: int
) -> list[int]:
    """
    Start (ms) of every stored bucket, traded or not, with `from_ms <= t < before_ms`, oldest first.
    A bucket is stored once any second of it was observed, so a start missing from a contiguous
    grid is a span with no observation at all (a collector outage), not a span with no trade. A
    liquidation-only row (`seconds_observed = 0`) observed nothing and is not listed.
    """
    rows = db.execute(
        "SELECT t FROM candles WHERE instrument_id = ? AND bar_seconds = ? AND t >= ? AND t < ? "
        "AND seconds_observed > 0 ORDER BY t",
        (iid, bar_seconds, from_ms, before_ms),
    ).fetchall()
    return [row[0] for row in rows]


def latest(
    db: sqlite3.Connection, iids: list[str], bar_seconds: int, n: int
) -> dict[str, list[dict]]:
    """
    Return the newest `n` traded candles per instrument (oldest first); one query per coin, each an
    index range scan -- no file I/O.
    """
    return {iid: window(db, iid, bar_seconds, 1 << 62, n) for iid in iids}


def candle_dicts_for_window(
    iid: str,
    start_ns: int,
    end_ns: int,
    bar_seconds: int,
    snapshot_rows_fn: Callable[[str, int, int], Sequence[SecondRow]],
    liquidation_rows_fn: Callable[[str, int, int], Sequence[Liquidation]] | None = None,
    liquidations_since_ns: int | None = None,
) -> list[dict]:
    """
    Candles for [start_ns, end_ns] folded from the raw 1s rows -- the slow, archive-side path.

    Charts read the store (`window`); this serves only history the store does not hold. Same fold
    (`fold_rows`, the one `bars_from_rows` wraps), so the two sources agree bar for bar, and each
    bar carries `partial` from the same counted `seconds_observed` rule as a stored bar (Story
    31.8: a read-time 10m/30m/45m/1W bar, or history older than the store, used to carry none, so
    an understated bucket looked whole). Every dict carries `source`, then the Story 33.3 columns.
    `liquidation_rows_fn` reads the window's liquidations (each venue event once) for an
    instrument with the feed; None means no feed, so the `liq_*` columns are null.
    `liquidations_since_ns` is the caller's feed start -- the store's persisted one
    (`liquidation_feed_since`), else the id's first archived liquidation
    (`kernel.catalog_files.liquidation_feed_since_ns`); None: none known --, lowered to the
    earliest row `liquidation_rows_fn` returns (a live-tail row may precede it;
    `domain.fold.archive_liquidations`): a bucket starting before that feed start, or straddling
    it, reads null `liq_*`, never 0 -- the archive does not know the feed existed then (audit
    D-160) -- and with no start known at all every bucket does.
    """
    liquidations = None
    if liquidation_rows_fn is not None:
        liquidations = liquidation_rows_fn(iid, start_ns, end_ns)
    liquidations, since_ns = archive_liquidations(liquidations, liquidations_since_ns)
    folded = fold_rows(
        snapshot_rows_fn(iid, start_ns, end_ns),
        liquidations=liquidations,
        liquidations_since_ns=since_ns,
        bars=(bar_seconds,),
    )
    return [
        {
            "t": t,
            "o": bucket.o,
            "h": bucket.h,
            "l": bucket.l,
            "c": bucket.c,
            "v": bucket.v,
            "partial": is_partial(bucket.seconds_observed, bar_seconds),
            "source": "raw_1s",
            **bucket.aggregates(),
        }
        for (_bar, t), bucket in sorted(folded.items())
        if bucket.o is not None
    ]


def flow_delta_before(
    db: sqlite3.Connection, iid: str, bar_seconds: int, before_ms: int
) -> tuple[int, int] | None:
    """
    Return the exact sum of `buy_v - sell_v` over the instrument's stored bars of one width with `t < before_ms`
    and known flow, as `(units, size_precision)`: the CVD `all` anchor (Story 33.3). One indexed
    SQLite aggregate per precision present (`GROUP BY size_precision`), rescaled here to the finest
    by `10**k`, exact in Python integers; no row reaches Python (MEM-01). None when the store holds
    no such bar (an unmigrated file included).

    Known limit: the aggregate is O(stored bars of that width before the window), at most 43,200
    rows at 1m (`RETAIN_DAYS`). Upgrade path: a per-day delta table summed by day.

    Known limit: SQLite's `SUM` over INTEGER raises "integer overflow" past int64 rather than wrap;
    that reaches the caller as `sqlite3.OperationalError`, a loud failure, never a wrong total.

    Only observed bars count (`seconds_observed > 0`): a liquidation-only row carries flow 0 at
    its liquidation's size precision, which would add nothing but could raise the returned
    precision. A row with known flow but no `size_precision` cannot exist (`domain.fold`: the
    precisions are null only when both integer groups are) -- its units would be unreadable, so it
    raises `ValueError` naming the instrument and width rather than being skipped (DATA-07).
    """
    if "buy_v" not in table_columns(db):
        return None
    rows = db.execute(
        "SELECT size_precision, SUM(buy_v - sell_v) FROM candles WHERE instrument_id = ? "
        "AND bar_seconds = ? AND t < ? AND buy_v IS NOT NULL AND seconds_observed > 0 "
        "GROUP BY size_precision",
        (iid, bar_seconds, before_ms),
    ).fetchall()
    if not rows:
        return None
    if any(p is None for p, _units in rows):
        raise ValueError(
            f"{iid} {bar_seconds}s: a stored bar has known flow but no size_precision before "
            f"t={before_ms}: its units cannot be read (impossible by the fold; a corrupt row)"
        )
    precision = max(row[0] for row in rows)
    return sum(units * 10 ** (precision - p) for p, units in rows), precision
