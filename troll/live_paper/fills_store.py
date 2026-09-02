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
Shared SQLite store for live_paper trade fills (Story 4.6).

One row per fill, across every bot (a bot_id column, not a file per bot -- write
volume is low enough on a human timescale that a single shared file adds no real
contention, and it's one fewer file/connection to manage). Append-only: a row is
written once, as its OrderFilled event is observed, and never rewritten -- unlike
Nautilus's own Cache, which overwrites a NETTING position's closed history the
instant it reopens (see trade_history.py's module docstring for why that made
Cache reconstruction unusable here). Mirrors ranking_engine/metrics_store.py's
plain-sqlite3, no-ORM style.
"""

import sqlite3
import threading
from pathlib import Path


# RLock: mirrors metrics_store.py's reentrancy note -- write() takes _lock while
# calling _conn(), which also takes _lock internally on first-open.
_lock = threading.RLock()
_connections: dict[str, sqlite3.Connection] = {}

_NS_PER_DAY = 24 * 3600 * 1_000_000_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fills (
    ts             INTEGER NOT NULL,
    bot_id         TEXT    NOT NULL,
    side           TEXT    NOT NULL,
    price          REAL    NOT NULL,
    qty            REAL    NOT NULL,
    realized_pnl   REAL
);
CREATE INDEX IF NOT EXISTS idx_bot_ts ON fills(bot_id, ts);
"""


def _conn(db_path: str) -> sqlite3.Connection:
    if db_path not in _connections:
        with _lock:
            if db_path not in _connections:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(db_path, check_same_thread=False)
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(_SCHEMA)
                db.commit()
                _connections[db_path] = db
    return _connections[db_path]


def write_fill(
    bot_id: str,
    ts: int,
    side: str,
    price: float,
    qty: float,
    realized_pnl: float | None,
    db_path: str,
) -> None:
    with _lock:
        db = _conn(db_path)
        db.execute(
            "INSERT INTO fills(ts, bot_id, side, price, qty, realized_pnl) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, bot_id, side, price, qty, realized_pnl),
        )
        db.commit()


def recent_trades(bot_id: str, db_path: str, cutoff_ns: int | None, limit: int) -> list[dict]:
    """Most recent `limit` fills at/after cutoff_ns (all time if None), ascending by ts."""
    db = _conn(db_path)
    rows = db.execute(
        """
        SELECT ts, side, price, qty, realized_pnl FROM (
            SELECT ts, side, price, qty, realized_pnl FROM fills
            WHERE bot_id = ? AND (? IS NULL OR ts >= ?)
            ORDER BY ts DESC
            LIMIT ?
        )
        ORDER BY ts ASC
        """,
        (bot_id, cutoff_ns, cutoff_ns, limit),
    ).fetchall()
    return [
        {"ts": ts, "side": side, "price": price, "qty": qty, "realized_pnl": realized_pnl}
        for ts, side, price, qty, realized_pnl in rows
    ]


def win_rate_stats(bot_id: str, db_path: str) -> tuple[int, int]:
    """
    (closed_trades, wins) for a bot, all-time -- closing fills only (realized_pnl
    IS NOT NULL); a win is a closing fill with realized_pnl > 0.
    """
    db = _conn(db_path)
    closed_trades, wins = db.execute(
        """
        SELECT COUNT(*), SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
        FROM fills
        WHERE bot_id = ? AND realized_pnl IS NOT NULL
        """,
        (bot_id,),
    ).fetchone()
    return closed_trades, wins or 0


def pnl_by_day(bot_id: str, db_path: str, cutoff_ns: int | None) -> list[dict]:
    """Net realized PnL per UTC day (only closing fills carry a non-NULL realized_pnl)."""
    db = _conn(db_path)
    rows = db.execute(
        f"""
        SELECT (ts / {_NS_PER_DAY}) AS day_bucket, SUM(realized_pnl) AS pnl
        FROM fills
        WHERE bot_id = ? AND realized_pnl IS NOT NULL AND (? IS NULL OR ts >= ?)
        GROUP BY day_bucket
        ORDER BY day_bucket
        """,
        (bot_id, cutoff_ns, cutoff_ns),
    ).fetchall()
    return [{"period_start": day_bucket * _NS_PER_DAY, "pnl": pnl} for day_bucket, pnl in rows]
