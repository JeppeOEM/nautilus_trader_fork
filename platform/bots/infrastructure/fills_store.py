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
`fills.db`: the shared, append-only SQLite fill log of every bot (Story 4.6).

One row per fill across every bot (a `bot_id` column, not a file per bot: write volume is human
timescale, so one shared file adds no real contention). A row is appended once per trade id, as
its `OrderFilled` is observed, and never rewritten -- unlike Nautilus's Cache, which overwrites a
NETTING position's closed history the instant it reopens (`bots.domain.fill_ledger`). Plain
`sqlite3`, no ORM, like `ranking/infrastructure/metrics_store.py`.
"""

import contextlib
import sqlite3
import threading
from pathlib import Path

from bots.domain.fill_ledger import FillRecord


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

# Added when one closing order started fragmenting into several reducing fills: `realized_pnl`
# became a per-fill proportional share, which can no longer count round trips.
# `position_realized_pnl` is non-NULL on exactly the closing fill of each round trip. ALTER TABLE
# (not only CREATE ... IF NOT EXISTS) because an existing fills.db predates the column; the
# "duplicate column" error on an already-migrated db is the expected steady state.
# `trade_id` (DW-224) is the venue's trade id: with `bot_id`, the idempotency key.
_MIGRATIONS = (
    "ALTER TABLE fills ADD COLUMN position_realized_pnl REAL",
    "ALTER TABLE fills ADD COLUMN trade_id TEXT",
)

# Created after the migrations, outside their suppression: a failure here is real, never the
# expected "duplicate column". Legacy rows keep a NULL `trade_id`, and a SQLite unique index admits
# any number of NULLs, so they never collide. The key drops no legitimate fill: a venue's trade ids
# are unique per venue (Sandbox's `_generate_trade_id_str`, nautilus_trader/backtest/engine.pyx,
# hashes the venue, the instrument's raw id and ts_init, so it does not repeat across restarts),
# and within one process the ExecutionEngine already refuses a duplicate trade id
# (`is_duplicate_fill_c`) -- so this guards only a re-delivery into a later process life.
# Known limit: a fill that startup reconciliation *infers* (no venue fill report, order status
# ahead of the Cache: `nautilus_trader/live/reconciliation.py` `create_inferred_order_filled_event`)
# carries a synthesised trade id, never the venue's, so it is not deduplicated against a row this
# store already holds -- reachable only by an exec bot whose fill reached `fills.db` but not the
# Redis-persisted Cache before the process died. Upgrade path: key inferred fills (detectable by
# their reconciliation trade id) on `(bot_id, client_order_id, cumulative filled qty)` instead.
_UNIQUE_TRADE_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_trade ON fills(bot_id, trade_id)"


class SqliteFillsStore:
    """
    The `FillsStore` port over one `fills.db` file.

    Invariant: rows are only ever appended (`write_fill`), at most one per `(bot_id, trade_id)`;
    one connection, opened on first use and
    shared by the event loop and the executor thread that writes fills, serialised by one lock;
    after `close()` every call raises, so a late write is a loud `bots.fill_lost`, never a second,
    leaked connection.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None
        self._closed = False

    def _conn(self) -> sqlite3.Connection:
        # Called with self._lock held.
        if self._closed:
            raise sqlite3.ProgrammingError(f"{self.db_path}: the fills store is closed")
        if self._db is None:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.db_path, check_same_thread=False)
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(_SCHEMA)
                for migration in _MIGRATIONS:
                    # "duplicate column": already present from a prior run.
                    with contextlib.suppress(sqlite3.OperationalError):
                        db.execute(migration)
                db.execute(_UNIQUE_TRADE_INDEX)
                db.commit()
            except Exception:
                db.close()
                raise
            self._db = db
        return self._db

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._db is not None:
                self._db.close()
                self._db = None

    def _query(self, sql: str, params: tuple) -> list[tuple]:
        with self._lock:
            return self._conn().execute(sql, params).fetchall()

    def write_fill(self, record: FillRecord) -> bool:
        """
        Append one fill; False (nothing written) when this bot already stored its trade id.
        `ON CONFLICT ... DO NOTHING`, never `INSERT OR IGNORE`, which would also swallow a NOT NULL
        violation -- a real fault that must raise.
        """
        with self._lock:
            db = self._conn()
            try:
                cursor = db.execute(
                    "INSERT INTO fills(ts, bot_id, side, price, qty, realized_pnl, "
                    "position_realized_pnl, trade_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(bot_id, trade_id) DO NOTHING",
                    (
                        record.ts,
                        record.bot_id,
                        record.side,
                        record.price,
                        record.qty,
                        record.realized_pnl,
                        record.position_realized_pnl,
                        record.trade_id,
                    ),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
            return cursor.rowcount == 1

    def recent_trades(self, bot_id: str, cutoff_ns: int | None, limit: int) -> list[dict]:
        """Return the latest `limit` fills at/after `cutoff_ns` (all time if None), ascending."""
        rows = self._query(
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
        )
        return [
            {"ts": ts, "side": side, "price": price, "qty": qty, "realized_pnl": realized_pnl}
            for ts, side, price, qty, realized_pnl in rows
        ]

    def realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]:
        """
        Every reducing fill's `realized_pnl` share, in time order -- NOT one per round trip (a
        multi-fill close contributes several). Count-sensitive stats use
        `position_realized_pnls`.
        """
        rows = self._query(
            """
            SELECT realized_pnl FROM fills
            WHERE bot_id = ? AND realized_pnl IS NOT NULL AND (? IS NULL OR ts >= ?)
            ORDER BY ts ASC
            """,
            (bot_id, cutoff_ns, cutoff_ns),
        )
        return [pnl for (pnl,) in rows]

    def position_realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]:
        """One realized PnL per completed round trip, in time order (the per-trade stats' input)."""
        rows = self._query(
            """
            SELECT position_realized_pnl FROM fills
            WHERE bot_id = ? AND position_realized_pnl IS NOT NULL AND (? IS NULL OR ts >= ?)
            ORDER BY ts ASC
            """,
            (bot_id, cutoff_ns, cutoff_ns),
        )
        return [pnl for (pnl,) in rows]

    def win_rate_stats(self, bot_id: str) -> tuple[int, int]:
        """(closed_trades, wins) all-time: one per round trip, a win is a positive total."""
        rows = self._query(
            """
            SELECT COUNT(*), SUM(CASE WHEN position_realized_pnl > 0 THEN 1 ELSE 0 END)
            FROM fills
            WHERE bot_id = ? AND position_realized_pnl IS NOT NULL
            """,
            (bot_id,),
        )
        closed_trades, wins = rows[0]
        return closed_trades, wins or 0

    def last_fill_ns(self, bot_id: str) -> int | None:
        """UNIX nanoseconds of the bot's latest fill (served by `idx_bot_ts`), None before any."""
        rows = self._query("SELECT MAX(ts) FROM fills WHERE bot_id = ?", (bot_id,))
        return rows[0][0]

    def pnl_by_day(self, bot_id: str, cutoff_ns: int | None) -> list[dict]:
        """Net realized PnL per UTC day (only reducing fills carry a non-NULL `realized_pnl`)."""
        rows = self._query(
            """
            SELECT (ts / ?) AS day_bucket, SUM(realized_pnl) AS pnl
            FROM fills
            WHERE bot_id = ? AND realized_pnl IS NOT NULL AND (? IS NULL OR ts >= ?)
            GROUP BY day_bucket
            ORDER BY day_bucket
            """,
            (_NS_PER_DAY, bot_id, cutoff_ns, cutoff_ns),
        )
        return [{"period_start": day_bucket * _NS_PER_DAY, "pnl": pnl} for day_bucket, pnl in rows]
