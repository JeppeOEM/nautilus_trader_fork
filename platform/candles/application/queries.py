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

from kernel.second_snapshot import SecondRow

from candles.domain.candle import is_partial
from candles.domain.fold import fold_rows
from candles.infrastructure.sqlite_store import connect_ro
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.sqlite_store import verified_status as _store_verified_status


_COLUMNS = "t, o, h, l, c, v, seconds_observed, bar_seconds"


def _candle(row: tuple) -> dict:
    t, o, h, low, c, v, seconds_observed, bar = row
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


def window(
    db: sqlite3.Connection, iid: str, bar_seconds: int, before_ms: int, limit: int
) -> list[dict]:
    """Up to `limit` traded candles with t < before_ms, oldest first."""
    rows = db.execute(
        f"SELECT {_COLUMNS} FROM candles WHERE instrument_id = ? AND bar_seconds = ? "  # noqa: S608 -- constant column list, values are bound
        "AND o IS NOT NULL AND t < ? ORDER BY t DESC LIMIT ?",
        (iid, bar_seconds, before_ms, limit),
    ).fetchall()
    return [_candle(r) for r in reversed(rows)]


def oldest_t(
    db: sqlite3.Connection, iid: str, bar_seconds: int, traded_only: bool = True
) -> int | None:
    """
    Start of the oldest bucket (ms). `traded_only=False` includes buckets with no trade: the
    earliest one is where the store's coverage of the archive begins.
    """
    row = db.execute(
        "SELECT MIN(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ?"  # noqa: S608 -- constant clause, values are bound
        + (" AND o IS NOT NULL" if traded_only else ""),
        (iid, bar_seconds),
    ).fetchone()
    return row[0]


def newest_t(db: sqlite3.Connection, iid: str, bar_seconds: int) -> int | None:
    """
    Start of the newest bucket (ms), traded or not: the store's coverage of the archive ends at
    this bucket's close (the bucket itself may still be forming, flagged `partial`).
    """
    row = db.execute(
        "SELECT MAX(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ?",
        (iid, bar_seconds),
    ).fetchone()
    return row[0]


def bucket_starts(
    db: sqlite3.Connection, iid: str, bar_seconds: int, from_ms: int, before_ms: int
) -> list[int]:
    """
    Start (ms) of every stored bucket, traded or not, with `from_ms <= t < before_ms`, oldest first.
    A bucket is stored once any second of it was observed, so a start missing from a contiguous
    grid is a span with no observation at all (a collector outage), not a span with no trade.
    """
    rows = db.execute(
        "SELECT t FROM candles WHERE instrument_id = ? AND bar_seconds = ? AND t >= ? AND t < ? "
        "ORDER BY t",
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
) -> list[dict]:
    """
    Candles for [start_ns, end_ns] folded from the raw 1s rows -- the slow, archive-side path.

    Charts read the store (`window`); this serves only history the store does not hold. Same fold
    (`fold_rows`, the one `bars_from_rows` wraps), so the two sources agree bar for bar, and each
    bar carries `partial` from the same counted `seconds_observed` rule as a stored bar (Story
    31.8: a read-time 10m/30m/45m/1W bar, or history older than the store, used to carry none, so
    an understated bucket looked whole). It reads the fold directly rather than
    `bars_from_rows`, whose `{t,o,h,l,c,v}` shape is the frozen `/ws/live` forming-bar payload.
    Every dict carries `source`.
    """
    folded = fold_rows(snapshot_rows_fn(iid, start_ns, end_ns), bars=(bar_seconds,))
    return [
        {
            "t": t,
            "o": o,
            "h": h,
            "l": low,
            "c": c,
            "v": v,
            "partial": is_partial(observed, bar_seconds),
            "source": "raw_1s",
        }
        for (_bar, t), (o, h, low, c, v, observed) in sorted(folded.items())
        if o is not None
    ]
