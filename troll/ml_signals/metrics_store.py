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
SQLite store for rolling 31-day metric snapshots.

One row per (ts, instrument_id) snapshot. Pruning happens on every write.
Add columns to COLS and the INSERT below to extend the schema with new metrics.
"""

import sqlite3
import threading
import time

# RLock: write() holds _lock while calling _conn(), which also takes _lock internally
# on first-open -- must be reentrant for the same thread to avoid deadlocking itself.
_lock = threading.RLock()
_connections: dict[str, sqlite3.Connection] = {}

# Metric columns stored per snapshot. Extend here to track new metrics.
COLS = (
    "price", "pct_1h", "pct_24h", "volatility", "ofi", "microprice", "spread", "rank", "volume24h",
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


def _migrate(db: sqlite3.Connection) -> None:
    """Add any COLS missing from an already-existing table (CREATE TABLE IF NOT EXISTS is a no-op
    against a pre-existing db, so extending COLS alone would otherwise break on deployed data).
    """
    existing = {row[1] for row in db.execute("PRAGMA table_info(snapshots)")}
    for col in COLS:
        if col not in existing:
            db.execute(f"ALTER TABLE snapshots ADD COLUMN {col} REAL")


def _conn(db_path: str) -> sqlite3.Connection:
    if db_path not in _connections:
        # First-open (schema create + migration) is serialized so two concurrent callers
        # can't both run _migrate()'s ALTER TABLE against the same fresh db and collide.
        with _lock:
            if db_path not in _connections:
                db = sqlite3.connect(db_path, check_same_thread=False)
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(_SCHEMA)
                _migrate(db)
                db.commit()
                _connections[db_path] = db
    return _connections[db_path]


def write(rows: list[dict], db_path: str, retain_days: int = 31) -> None:
    """Upsert snapshots and prune rows older than retain_days."""
    cutoff = time.time_ns() - retain_days * 86_400 * 1_000_000_000
    placeholders = ", ".join("?" * (2 + len(COLS)))
    sql = f"INSERT OR REPLACE INTO snapshots(ts, instrument_id, {', '.join(COLS)}) VALUES({placeholders})"
    with _lock:
        db = _conn(db_path)
        db.execute("DELETE FROM snapshots WHERE ts < ?", (cutoff,))
        db.executemany(sql, [
            (r["ts"], r["instrument_id"], *[r.get(c) for c in COLS])
            for r in rows
        ])
        db.commit()


def latest(db_path: str) -> list[dict]:
    """Most recent snapshot per instrument, for the rankings table."""
    db = _conn(db_path)
    rows = db.execute(f"""
        SELECT instrument_id, {", ".join(COLS)}
        FROM snapshots
        WHERE (instrument_id, ts) IN (
            SELECT instrument_id, MAX(ts) FROM snapshots GROUP BY instrument_id
        )
        ORDER BY instrument_id
    """).fetchall()
    keys = ("instrument_id", *COLS)
    return [dict(zip(keys, r)) for r in rows]


def history(instrument_id: str, db_path: str, days: int = 31) -> list[dict]:
    """All snapshots for one instrument over the last `days` days, ordered by ts."""
    cutoff = time.time_ns() - days * 86_400 * 1_000_000_000
    db = _conn(db_path)
    rows = db.execute(
        f"SELECT ts, {', '.join(COLS)} FROM snapshots WHERE instrument_id=? AND ts>=? ORDER BY ts",
        (instrument_id, cutoff),
    ).fetchall()
    keys = ("ts", *COLS)
    return [dict(zip(keys, r)) for r in rows]


def nearest(instrument_id: str, ts: int, db_path: str) -> dict | None:
    """Snapshot for instrument_id with ts closest to the given ts (ns). None if never stored."""
    db = _conn(db_path)
    row = db.execute(
        f"SELECT ts, {', '.join(COLS)} FROM snapshots WHERE instrument_id=? "
        "ORDER BY ABS(ts - ?) LIMIT 1",
        (instrument_id, ts),
    ).fetchone()
    if row is None:
        return None
    keys = ("ts", *COLS)
    return dict(zip(keys, row))
