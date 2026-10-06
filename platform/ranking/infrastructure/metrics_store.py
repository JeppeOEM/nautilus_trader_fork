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
`metrics.db`: rolling 31-day metric snapshots, one row per (ts, instrument_id) -- the `RankingHistory`
adapter (moved into the ranking context in Story 25.2; schema frozen, AD-D12).

`SqliteMetricsStore` is the ranking process's one writer. `read_history`/`read_nearest` are the
readers other processes use (through `ranking.application.queries`): each opens its own read-only
connection per call and closes it, so no reader ever creates, migrates or writes the file and no
process-wide connection cache exists.
"""

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path


# Metric columns stored per snapshot (AD-D12: append-only -- a column is never renamed, moved or
# removed). Extend here and in the writer's rows; `_migrate` adds a new column, nullable, to a
# deployed table. Story 33.4 appended the numeric derivatives, liquidation and flow fields of the
# slow row (`ranking.domain.derivs.DERIVS_FIELDS` minus `next_funding_ns`: an epoch-ns timestamp
# that a REAL column would round, and a schedule rather than a metric).
COLS = (
    "price",
    "pct_1h",
    "pct_24h",
    "pct_1w",
    "pct_1m",
    "volatility",
    "ofi",
    "microprice",
    "spread",
    "rank",
    "volume24h",
    "funding_rate",
    "funding_annualised",
    "open_interest",
    "oi_change_1h",
    "oi_change_24h",
    "basis_mi_bps",
    "basis_ml_bps",
    "liq_long_1h",
    "liq_short_1h",
    "liq_notional_1h",
    "liq_ratio_1h",
    "forced_share_1h",
    "relative_volume",
    "high_24h",
    "low_24h",
    "range_position_24h",
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS snapshots (
    ts             INTEGER NOT NULL,
    instrument_id  TEXT    NOT NULL,
    {", ".join(f"{c} REAL" for c in COLS)},
    PRIMARY KEY (ts, instrument_id)
);
CREATE INDEX IF NOT EXISTS idx_iid_ts ON snapshots(instrument_id, ts);
"""

_NS_PER_DAY = 86_400 * 1_000_000_000

# How many days of snapshots metrics.db keeps: `write` prunes everything older, so a history read
# reaching further back can only return what this retention left. Readers bound their `days` by
# it (`ranking.application.queries.HISTORY_MAX_DAYS`) instead of repeating the number.
RETAIN_DAYS = 31


def _migrate(db: sqlite3.Connection) -> None:
    """
    Add any COLS missing from an already-existing table (CREATE TABLE IF NOT EXISTS is a no-op
    against a pre-existing db, so extending COLS alone would otherwise break on deployed data).
    Each is added in place as a nullable REAL, so every row written before it reads None for it --
    a gap, never a fabricated 0 (Story 33.4's columns on a pre-33.4 metrics.db).
    """
    existing = {row[1] for row in db.execute("PRAGMA table_info(snapshots)")}
    for col in COLS:
        if col not in existing:
            db.execute(f"ALTER TABLE snapshots ADD COLUMN {col} REAL")


def _history_rows(db: sqlite3.Connection, instrument_id: str, days: int) -> list[dict]:
    cutoff = time.time_ns() - days * _NS_PER_DAY
    rows = db.execute(
        f"SELECT ts, {', '.join(COLS)} FROM snapshots "  # noqa: S608 -- constant column list
        "WHERE instrument_id=? AND ts>=? ORDER BY ts",
        (instrument_id, cutoff),
    ).fetchall()
    keys = ("ts", *COLS)
    return [dict(zip(keys, r, strict=True)) for r in rows]


# How far from the asked time a stored row may lie and still be "the nearest" (Story 31.3): two
# write intervals (`db_write_interval_seconds`, 60 s). A farther row describes another time, so
# the answer is None, exactly as for an instrument with no rows -- never a stale row passed off as
# the value at `ts`.
NEAREST_TOLERANCE_S = 120
_INT64_MIN, _INT64_MAX = -(2**63), 2**63 - 1


def _nearest_row(db: sqlite3.Connection, instrument_id: str, ts: int) -> dict | None:
    tolerance_ns = NEAREST_TOLERANCE_S * 1_000_000_000
    # SQLite binds int64 only: a `ts` near or past either end (a client's `ts_ns`) must not overflow.
    ts = max(min(ts, _INT64_MAX), _INT64_MIN)
    low, high = max(ts - tolerance_ns, _INT64_MIN), min(ts + tolerance_ns, _INT64_MAX)
    row = db.execute(
        f"SELECT ts, {', '.join(COLS)} FROM snapshots WHERE instrument_id=? "  # noqa: S608
        "AND ts BETWEEN ? AND ? ORDER BY ABS(ts - ?) LIMIT 1",
        (instrument_id, low, high, ts),
    ).fetchone()
    if row is None:
        return None
    return dict(zip(("ts", *COLS), row, strict=True))


class SqliteMetricsStore:
    """
    The writer of `metrics.db` (the `RankingHistory` port).

    Invariant: the ranking process is the store's only writer, through this one instance, and every
    statement runs under its lock -- `write` and `price_near_days_ago` are called from worker
    threads (`asyncio.to_thread`), and the first open creates and migrates the schema exactly once.

    The `-wal`/`-shm` sidecars outlive `close()` (`SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE`): data_api
    opens the db read-only per call on a read-only mount, and SQLite cannot open a WAL database
    that way once a clean close has checkpointed and deleted them -- the history/nearest routes
    would fail until this writer restarts. Auto-checkpointing while open still bounds the WAL.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _conn(self) -> sqlite3.Connection:
        # Called with self._lock held.
        if self._db is None:
            db = sqlite3.connect(self._db_path, check_same_thread=False)
            db.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)
            _migrate(db)
            db.commit()
            self._db = db
        return self._db

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None

    def write(self, rows: list[dict], retain_days: int = RETAIN_DAYS) -> None:
        """Upsert snapshots and prune rows older than retain_days."""
        cutoff = time.time_ns() - retain_days * _NS_PER_DAY
        placeholders = ", ".join("?" * (2 + len(COLS)))
        sql = (
            f"INSERT OR REPLACE INTO snapshots(ts, instrument_id, {', '.join(COLS)}) "
            f"VALUES({placeholders})"
        )
        params = [(r["ts"], r["instrument_id"], *[r.get(c) for c in COLS]) for r in rows]
        with self._lock:
            db = self._conn()
            try:
                db.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
                db.executemany(sql, params)
                db.commit()
            except BaseException:
                # Never leave the prune half-applied in an open transaction for the next commit.
                db.rollback()
                raise

    def latest(self) -> list[dict]:
        """Most recent snapshot per instrument."""
        with self._lock:
            rows = (
                self._conn()
                .execute(f"""
                SELECT instrument_id, {", ".join(COLS)}
                FROM snapshots
                WHERE (instrument_id, ts) IN (
                    SELECT instrument_id, MAX(ts) FROM snapshots GROUP BY instrument_id
                )
                ORDER BY instrument_id
            """)  # noqa: S608 -- constant column list
                .fetchall()
            )
        keys = ("instrument_id", *COLS)
        return [dict(zip(keys, r, strict=True)) for r in rows]

    def history(self, instrument_id: str, days: int = RETAIN_DAYS) -> list[dict]:
        """All snapshots for one instrument over the last `days` days, ordered by ts."""
        with self._lock:
            return _history_rows(self._conn(), instrument_id, days)

    def nearest(self, instrument_id: str, ts: int) -> dict | None:
        """
        Snapshot for instrument_id with ts closest to `ts` (ns), within `NEAREST_TOLERANCE_S`.
        None if never stored or no row lies that close.
        """
        with self._lock:
            return _nearest_row(self._conn(), instrument_id, ts)

    def price_near_days_ago(self, days: float, tolerance_s: float = 3600.0) -> dict[str, float]:
        """
        Per instrument, the stored price at or just before `now - days`.

        Only a row within `tolerance_s` before that target counts -- an instrument whose store
        doesn't reach back that far (or has a gap there) is absent from the result, never padded
        with a stale or zero price (DATA-01). One bulk query, not one per instrument.
        """
        target = time.time_ns() - int(days * _NS_PER_DAY)
        floor = target - int(tolerance_s * 1_000_000_000)
        with self._lock:
            rows = (
                self._conn()
                .execute(
                    "SELECT instrument_id, price, MAX(ts) FROM snapshots "
                    "WHERE ts <= ? AND ts > ? AND price IS NOT NULL GROUP BY instrument_id",
                    (target, floor),
                )
                .fetchall()
            )
        return {iid: price for iid, price, _ts in rows}


def _read_only(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)


def _has_table(db: sqlite3.Connection) -> bool:
    """Whether the writer has created the table yet (a reader never creates or migrates it)."""
    found = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'snapshots'"
    ).fetchone()
    return found is not None


def read_history(db_path: str, instrument_id: str, days: int = RETAIN_DAYS) -> list[dict]:
    """Return a reader's `history`: [] while the writer has written nothing (no file or table)."""
    if not Path(db_path).exists():
        return []
    with closing(_read_only(db_path)) as db:
        return _history_rows(db, instrument_id, days) if _has_table(db) else []


def read_nearest(db_path: str, instrument_id: str, ts: int) -> dict | None:
    """Return a reader's `nearest`: None while the writer has written nothing (no file or table)."""
    if not Path(db_path).exists():
        return None
    with closing(_read_only(db_path)) as db:
        return _nearest_row(db, instrument_id, ts) if _has_table(db) else None
