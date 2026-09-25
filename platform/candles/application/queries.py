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

Every reader takes a connection the caller owns (`infrastructure.sqlite_store.connect_ro`), so the
store stays single-writer: nothing here opens a file read-write.
"""

import sqlite3
from collections.abc import Callable
from collections.abc import Sequence

from kernel.second_snapshot import SecondRow

from candles.application.forming import bars_from_rows
from candles.domain.candle import is_partial


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

    Charts read the store (`window`); this serves only history the store does not hold. Same fold,
    so the two sources agree bar for bar. Every dict carries `source`.
    """
    return [
        {**c, "source": "raw_1s"}
        for c in bars_from_rows(snapshot_rows_fn(iid, start_ns, end_ns), bar_seconds)
    ]
