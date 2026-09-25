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
SQLite store of finished candles (1m..1D): the read model for charts, indicator values and the
Technicals tab, so none of them re-read thousands of tiny Parquet files per request.

The Parquet catalog's raw 1s snapshots stay the archive and source of truth; this store is derived
and can be deleted and rebuilt (`python -m candles.rebuild`). One writer per file: that venue's
collector folds every flushed snapshot in through `CandleSink`; everything else opens it read-only.
The live writer and the rebuild share one aggregation (`candles.domain.fold`), so they cannot
disagree.

Every second a snapshot exists counts toward `seconds_observed` (the `partial` flag's input), traded
or not; a bucket row exists for every bucket a snapshot touched. Reads only return buckets with a
trade (`o IS NOT NULL`).

`_SCHEMA` and `_UPSERT` are frozen text (spine AD-D12): they are the on-disk contract of a file the
collectors keep across deploys, and `candles/tests/test_schema_is_frozen.py` asserts them against a
recorded copy.
"""

import contextlib
import os
import sqlite3
import time
from collections.abc import Iterable
from collections.abc import Iterator
from collections.abc import Mapping
from pathlib import Path

import numpy as np
from kernel.second_snapshot import SecondRow

from candles.domain.candle_series import CandleSeries
from candles.domain.fold import DAY_MS
from candles.domain.fold import RETAIN_DAYS
from candles.domain.fold import fold_arrays
from candles.domain.fold import fold_rows


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
CREATE TABLE IF NOT EXISTS verified_days (
    instrument_id TEXT    NOT NULL,
    day           TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    checked_at    INTEGER NOT NULL,
    mismatches    INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, day)
) WITHOUT ROWID;
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
    """Open the writer's connection (collector, rebuild CLI), creating the file and schema."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path, check_same_thread=False, timeout=60.0
    )  # rebuild workers share the file
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "PRAGMA synchronous=NORMAL"
    )  # WAL + NORMAL: no fsync per commit; a crash loses at most the last commits, rebuildable
    db.executescript(_SCHEMA)
    return db


@contextlib.contextmanager
def connect_ro(path: str) -> Iterator[sqlite3.Connection | None]:
    """Open a read-only connection; yields None when the store does not exist yet (callers fall back)."""
    if not Path(path).exists():
        yield None
        return
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    try:
        yield db
    finally:
        db.close()


def db_path_for_venue(candles_dir: str | Path, venue: str) -> str:
    """
    One store file per venue: `<candles_dir>/candles_<venue lowercased>.db`.

    The formula is frozen (AD-D12): compose sets each collector's `CANDLES_DB_PATH` to it, and every
    reader derives the same path from `kernel.venues.venue_of(instrument_id)`.
    """
    return str(Path(candles_dir) / f"candles_{venue.lower()}.db")


def store_from_env(catalog_path: str) -> "CandleStore":
    """
    Open this process's candle store from `CANDLES_DB_PATH`, defaulting beside the catalog.

    The variable and its default are the frozen deployment contract (AD-D12): compose sets it per
    collector service to `/app/candles_dir/candles_<venue>.db`, and the default
    `<catalog>/../candles/candles.db` is what an unset variable falls back to -- a container-local
    file nothing mounts, which is why leaving it unset shows up as empty charts.
    """
    catalog = Path(catalog_path).resolve()
    return CandleStore(
        os.environ.get("CANDLES_DB_PATH", str(catalog.parent / "candles" / "candles.db"))
    )


def write_buckets(db: sqlite3.Connection, iid: str, acc: Mapping[tuple[int, int], list]) -> None:
    """Merge one fold's buckets into `candles` with the NULL-safe, accumulating `_UPSERT`."""
    for (bar, t), (o, h, low, c, v, secs) in acc.items():
        db.execute(_UPSERT, (iid, bar, t, o, h, low, c, v, secs))


def _read_watermark(db: sqlite3.Connection, iid: str) -> int:
    row = db.execute(
        "SELECT through_ns FROM built_through WHERE instrument_id = ?", (iid,)
    ).fetchone()
    return row[0] if row is not None else -1


def apply_seconds(db: sqlite3.Connection, iid: str, rows: Iterable[SecondRow]) -> int:
    """
    Fold seconds into every bar size, each second exactly once: rows at or before the
    instrument's watermark were already applied (a replayed batch) and are skipped. A hole filled
    into the archive later is left to the rebuild. Returns the number of seconds applied.
    """
    with db:
        series = CandleSeries(iid, _read_watermark(db, iid))
        fresh, through_ns = series.accept(rows)
        if not fresh:
            return 0
        write_buckets(db, iid, series.buckets(fresh))
        db.execute("INSERT OR REPLACE INTO built_through VALUES(?, ?)", (iid, through_ns))
        return len(fresh)


def _day_bounds(start_ms: int, end_ms: int, allow_open_day: bool) -> tuple[int, int]:
    """Whole UTC days covering [start_ms, end_ms); today is excluded unless `allow_open_day`."""
    lo = start_ms // DAY_MS * DAY_MS
    hi = -(-end_ms // DAY_MS) * DAY_MS
    if not allow_open_day:
        hi = min(hi, int(time.time() * 1000) // DAY_MS * DAY_MS)
    return lo, hi


def _advance_watermark(db: sqlite3.Connection, iid: str, through_ns: int) -> None:
    """Raise the watermark to `through_ns`; a rebuild of an old day must never lower it."""
    db.execute(
        "INSERT INTO built_through VALUES(?, ?) ON CONFLICT(instrument_id) DO UPDATE "
        "SET through_ns = max(through_ns, excluded.through_ns)",
        (iid, through_ns),
    )


def rebuild(
    db: sqlite3.Connection,
    iid: str,
    rows: Iterable[SecondRow],
    start_ms: int,
    end_ms: int,
    allow_open_day: bool = False,
) -> int:
    """
    Recompute every bucket in whole UTC days covering [start_ms, end_ms) from `rows` (all of that
    instrument's seconds in those days, any order). Idempotent. Whole days, so no bucket is
    half-rebuilt. Today is excluded unless `allow_open_day` (only safe with the collector stopped):
    seconds the collector applied but the archive has not flushed yet would be deleted and lost.
    Returns the number of seconds applied.
    """
    lo, hi = _day_bounds(start_ms, end_ms, allow_open_day)
    seconds = sorted(
        (r for r in rows if lo <= r.ts_event // 1_000_000 < hi), key=lambda r: r.ts_event
    )
    with db:
        db.execute(
            "DELETE FROM candles WHERE instrument_id = ? AND t >= ? AND t < ?", (iid, lo, hi)
        )
        write_buckets(db, iid, fold_rows(seconds))
        if seconds:
            _advance_watermark(db, iid, seconds[-1].ts_event)
    return len(seconds)


def rebuild_from_arrays(
    db: sqlite3.Connection,
    iid: str,
    cols: dict[str, np.ndarray],
    start_ms: int,
    end_ms: int,
    allow_open_day: bool = False,
) -> int:
    """
    `rebuild` over column arrays (`ts_ms`, `o`, `h`, `l`, `c`, `v`; NaN = no trade), as the
    rebuild CLI reads them straight out of Parquet without per-row Python objects.
    """
    lo, hi = _day_bounds(start_ms, end_ms, allow_open_day)
    keep = (cols["ts_ms"] >= lo) & (cols["ts_ms"] < hi)
    ts_ms = cols["ts_ms"][keep]
    with db:
        db.execute(
            "DELETE FROM candles WHERE instrument_id = ? AND t >= ? AND t < ?", (iid, lo, hi)
        )
        if len(ts_ms):
            write_buckets(
                db, iid, fold_arrays(ts_ms, *(cols[k][keep] for k in ("o", "h", "l", "c", "v")))
            )
            _advance_watermark(db, iid, int(ts_ms.max()) * 1_000_000)
    return len(ts_ms)


def watermarks(db: sqlite3.Connection) -> dict[str, int]:
    """instrument_id -> ts_event (ns) of the last second applied."""
    return dict(db.execute("SELECT instrument_id, through_ns FROM built_through").fetchall())


def mark_verified(
    db: sqlite3.Connection, iid: str, day: str, status: str, mismatches: int, checked_at_ms: int
) -> None:
    """
    Record (upsert) one instrument-day's kline reconciliation verdict (`compare_klines`):
    `status` is "pass" or "fail". The finality marker `prune_catalog` gates trade retention on.
    """
    with db:
        db.execute(
            "INSERT INTO verified_days(instrument_id, day, status, checked_at, mismatches) "
            "VALUES(?, ?, ?, ?, ?) ON CONFLICT(instrument_id, day) DO UPDATE SET "
            "status = excluded.status, checked_at = excluded.checked_at, "
            "mismatches = excluded.mismatches",
            (iid, day, status, checked_at_ms, mismatches),
        )


def verified_status(db: sqlite3.Connection, iid: str, day: str) -> str | None:
    """
    Return that instrument-day's last verdict ("pass"/"fail"), or None when never verified -- also for a
    store opened read-only that predates the table (no reconciliation ever ran against it).
    """
    has_table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'verified_days'"
    ).fetchone()
    if has_table is None:
        return None
    row = db.execute(
        "SELECT status FROM verified_days WHERE instrument_id = ? AND day = ?", (iid, day)
    ).fetchone()
    return None if row is None else str(row[0])


def prune(db: sqlite3.Connection, now_ms: int | None = None) -> None:
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    with db:
        for bar, days in RETAIN_DAYS.items():
            db.execute(
                "DELETE FROM candles WHERE bar_seconds = ? AND t < ?",
                (bar, now_ms - days * DAY_MS),
            )


class CandleStore:
    """
    One venue's `candles_<venue>.db`, opened read-write.

    Invariant (one writer of a given instrument-day): this class is the only code in `platform/`
    that opens a candle store read-write, and no two writers ever touch the same instrument-day --
    that venue's collector owns the live tail, and `candles.rebuild` recomputes whole days while the
    collector is stopped. The commands that would violate it are a second writer's `apply` over the
    same instrument (two watermarks over one accumulating `_UPSERT`, so volumes double) and any
    reader that "just" opened it read-write to mark a day verified; both go through this class or
    through the `VerifiedDays` port instead.

    Known limit: the *file* does take concurrent writers. `candles.rebuild --workers` defaults to
    `os.cpu_count()` and each worker opens its own `CandleStore` on the same file, partitioned by
    instrument so the invariant above still holds; they serialise on SQLite's WAL lock with
    `connect_rw`'s 60 s busy timeout, and a day wide enough to exhaust it fails the rebuild with
    `sqlite3.OperationalError: database is locked` (`--workers 1`, which `nightly` uses, avoids it).
    Upgrade path: one writer process fed by a queue, or per-instrument store files.

    Satisfies `collector_core.ports.SecondSink` through `CandleSink`, and
    `candles.application.verified_days.VerifiedDays` directly.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._db = connect_rw(db_path)

    @property
    def path(self) -> str:
        return self._path

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the open connection, for the owner's own `application.queries` reads."""
        return self._db

    def apply(self, instrument_id: str, rows: Iterable[SecondRow]) -> int:
        """Fold flushed seconds in, each exactly once; returns the number of seconds applied."""
        return apply_seconds(self._db, instrument_id, rows)

    def watermarks(self) -> Mapping[str, int]:
        """instrument_id -> `ts_event` (ns) of the last second applied."""
        return watermarks(self._db)

    def rebuild_day(
        self,
        instrument_id: str,
        cols: dict[str, np.ndarray],
        day_ms: int,
        allow_open_day: bool = False,
    ) -> int:
        """Recompute one whole UTC day from raw 1s columns; idempotent."""
        return rebuild_from_arrays(
            self._db, instrument_id, cols, day_ms, day_ms + DAY_MS, allow_open_day
        )

    def rebuild(
        self,
        instrument_id: str,
        rows: Iterable[SecondRow],
        start_ms: int,
        end_ms: int,
        allow_open_day: bool = False,
    ) -> int:
        """Recompute the whole UTC days covering [start_ms, end_ms) from `rows`; idempotent."""
        return rebuild(self._db, instrument_id, rows, start_ms, end_ms, allow_open_day)

    def prune(self, now_ms: int | None = None) -> None:
        """Drop 1m/5m bars past their retention (`RETAIN_DAYS`); wide bars are kept."""
        prune(self._db, now_ms)

    def mark_verified(
        self, instrument_id: str, day: str, status: str, mismatches: int, checked_at_ms: int
    ) -> None:
        mark_verified(self._db, instrument_id, day, status, mismatches, checked_at_ms)

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        return verified_status(self._db, instrument_id, day)

    def close(self) -> None:
        self._db.close()
