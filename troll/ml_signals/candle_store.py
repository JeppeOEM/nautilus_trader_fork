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
SQLite store of finished candles (1m..1D): the read model for charts, indicator-values and the
Technicals tab, so none of them re-read thousands of tiny Parquet files per request.

The Parquet catalog's raw 1s snapshots stay the archive and source of truth; this store is derived
and can be deleted and rebuilt (`dydx_collector.build_candles`). One writer: the collector folds every
flushed snapshot in (`apply_seconds`); everything else opens the file read-only. The live writer and
the rebuild share one aggregation (`_fold`), so they cannot disagree.

Every second a snapshot exists counts toward `seconds_observed` (the `partial` flag's input), traded or
not; a bucket row exists for every bucket a snapshot touched. Reads only return buckets with a trade
(`o IS NOT NULL`), matching `candle_dicts_from_snapshots`.
"""

import contextlib
import sqlite3
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ml_signals.candles import PARTIAL_OBSERVED_FRACTION


BAR_SECONDS = (60, 300, 900, 3600, 14400, 86400)
# Older 1m/5m bars are pruned (the wide ones are tiny and kept); the Parquet archive keeps everything.
RETAIN_DAYS = {60: 30, 300: 90}
_DAY_MS = 86_400_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    instrument_id    TEXT    NOT NULL,
    bar_seconds      INTEGER NOT NULL,
    t                INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL,
    v                REAL    NOT NULL,
    seconds_observed INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, bar_seconds, t)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS built_through (
    instrument_id TEXT PRIMARY KEY,
    through_ns    INTEGER NOT NULL
);
"""

# NULL-safe merge: a bucket with no trade carries NULL o/h/l/c and must not erase or poison them.
_UPSERT = """
INSERT INTO candles(instrument_id, bar_seconds, t, o, h, l, c, v, seconds_observed)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(instrument_id, bar_seconds, t) DO UPDATE SET
    o = COALESCE(o, excluded.o),
    h = CASE WHEN excluded.h IS NULL THEN h WHEN h IS NULL THEN excluded.h ELSE max(h, excluded.h) END,
    l = CASE WHEN excluded.l IS NULL THEN l WHEN l IS NULL THEN excluded.l ELSE min(l, excluded.l) END,
    c = COALESCE(excluded.c, c),
    v = v + excluded.v,
    seconds_observed = seconds_observed + excluded.seconds_observed
"""


def connect_rw(path: str) -> sqlite3.Connection:
    """The writer's connection (collector, rebuild CLI): creates the file and schema."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")  # WAL + NORMAL: no fsync per commit; a crash loses at most the last commits, rebuildable
    db.executescript(_SCHEMA)
    return db


@contextlib.contextmanager
def connect_ro(path: str) -> Iterator[sqlite3.Connection | None]:
    """A reader's connection; None when the store does not exist yet (callers fall back)."""
    if not Path(path).exists():
        yield None
        return
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    try:
        yield db
    finally:
        db.close()


def _fold(rows: Iterable[Any]) -> dict[tuple[int, int], list]:
    """(bar_seconds, bucket_start_ms) -> [o, h, l, c, volume, seconds_observed], for rows sorted by
    ts_event that carry ts_event (ns), open/high/low/close_price (None = no trade that second) and
    buy_volume/sell_volume: a `DydxSecondSnapshot` or a `SecondOHLC`."""
    acc: dict[tuple[int, int], list] = {}
    for r in rows:
        ms = r.ts_event // 1_000_000
        traded = r.close_price is not None
        for bar in BAR_SECONDS:
            a = acc.setdefault((bar, ms // (bar * 1000) * (bar * 1000)), [None, None, None, None, 0.0, 0])
            a[5] += 1
            if not traded:
                continue
            if a[0] is None:
                a[0] = r.open_price
            a[1] = r.high_price if a[1] is None else max(a[1], r.high_price)
            a[2] = r.low_price if a[2] is None else min(a[2], r.low_price)
            a[3] = r.close_price
            a[4] += r.buy_volume + r.sell_volume
    return acc


def _write(db: sqlite3.Connection, iid: str, acc: dict[tuple[int, int], list]) -> None:
    for (bar, t), (o, h, low, c, v, secs) in acc.items():
        db.execute(_UPSERT, (iid, bar, t, o, h, low, c, v, secs))


def apply_seconds(db: sqlite3.Connection, iid: str, rows: Iterable[Any]) -> int:
    """Fold seconds into every bar size, each second exactly once: rows at or before the
    instrument's watermark were already applied (a replayed flush) and are skipped. A hole filled
    into the archive later is left to `rebuild`. Returns the number of seconds applied."""
    with db:
        row = db.execute("SELECT through_ns FROM built_through WHERE instrument_id = ?", (iid,)).fetchone()
        mark = row[0] if row is not None else -1
        fresh = sorted((r for r in rows if r.ts_event > mark), key=lambda r: r.ts_event)
        if not fresh:
            return 0
        _write(db, iid, _fold(fresh))
        db.execute("INSERT OR REPLACE INTO built_through VALUES(?, ?)", (iid, fresh[-1].ts_event))
    return len(fresh)


def rebuild(
    db: sqlite3.Connection, iid: str, rows: Iterable[Any], start_ms: int, end_ms: int,
    allow_open_day: bool = False,
) -> int:
    """Recompute every bucket in whole UTC days covering [start_ms, end_ms) from `rows` (all of that
    instrument's seconds in those days, any order). Idempotent. Whole days, so no bucket is
    half-rebuilt. Today is excluded unless `allow_open_day` (only safe with the collector stopped):
    seconds the collector applied but the archive has not flushed yet would be deleted and lost.
    Returns the number of seconds applied."""
    lo = start_ms // _DAY_MS * _DAY_MS
    hi = -(-end_ms // _DAY_MS) * _DAY_MS
    if not allow_open_day:
        hi = min(hi, int(time.time() * 1000) // _DAY_MS * _DAY_MS)
    seconds = sorted((r for r in rows if lo <= r.ts_event // 1_000_000 < hi), key=lambda r: r.ts_event)
    with db:
        db.execute("DELETE FROM candles WHERE instrument_id = ? AND t >= ? AND t < ?", (iid, lo, hi))
        _write(db, iid, _fold(seconds))
        if seconds:
            db.execute(
                "INSERT INTO built_through VALUES(?, ?) ON CONFLICT(instrument_id) DO UPDATE "
                "SET through_ns = max(through_ns, excluded.through_ns)",
                (iid, seconds[-1].ts_event),
            )
    return len(seconds)


def _candle(row: tuple) -> dict:
    t, o, h, low, c, v, seconds_observed, bar = row
    return {
        "t": t, "o": o, "h": h, "l": low, "c": c, "v": v,
        "seconds_observed": seconds_observed,
        "partial": seconds_observed < PARTIAL_OBSERVED_FRACTION * bar,
        "source": "candle_store",
    }


_COLUMNS = "t, o, h, l, c, v, seconds_observed, bar_seconds"


def window(db: sqlite3.Connection, iid: str, bar_seconds: int, before_ms: int, limit: int) -> list[dict]:
    """Up to `limit` traded candles with t < before_ms, oldest first."""
    rows = db.execute(
        f"SELECT {_COLUMNS} FROM candles WHERE instrument_id = ? AND bar_seconds = ? "
        "AND o IS NOT NULL AND t < ? ORDER BY t DESC LIMIT ?",
        (iid, bar_seconds, before_ms, limit),
    ).fetchall()
    return [_candle(r) for r in reversed(rows)]


def oldest_t(db: sqlite3.Connection, iid: str, bar_seconds: int, traded_only: bool = True) -> int | None:
    """Start of the oldest bucket (ms). `traded_only=False` includes buckets with no trade: the
    earliest one is where the store's coverage of the archive begins."""
    row = db.execute(
        "SELECT MIN(t) FROM candles WHERE instrument_id = ? AND bar_seconds = ?"
        + (" AND o IS NOT NULL" if traded_only else ""),
        (iid, bar_seconds),
    ).fetchone()
    return row[0]


def latest(db: sqlite3.Connection, iids: list[str], bar_seconds: int, n: int) -> dict[str, list[dict]]:
    """The newest `n` traded candles per instrument (oldest first); one query per coin, each an
    index range scan -- no file I/O."""
    return {iid: window(db, iid, bar_seconds, 1 << 62, n) for iid in iids}


def prune(db: sqlite3.Connection, now_ms: int | None = None) -> None:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    with db:
        for bar, days in RETAIN_DAYS.items():
            db.execute("DELETE FROM candles WHERE bar_seconds = ? AND t < ?", (bar, now_ms - days * _DAY_MS))
