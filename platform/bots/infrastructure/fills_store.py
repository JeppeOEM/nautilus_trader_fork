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
`fills.db`: the shared, append-only SQLite fill and round-trip log of every bot (Story 4.6).

One row per fill across every bot (a `bot_id` column, not a file per bot: write volume is human
timescale, so one shared file adds no real contention), and one `position_closes` row per
completed round trip (DW-223/225). A row is appended once, as its `OrderFilled` or
`PositionClosed` is observed, and never rewritten -- unlike Nautilus's Cache, which overwrites a
NETTING position's closed history the instant it reopens (`bots.domain.fill_ledger`). Every
realized-PnL figure (per-trip stats, win rate, daily PnL, the blotter's PnL column and
`bots:status.realized_pnl`) reads `position_closes`, so they can never disagree -- except that
an unlinked close (`bots.close_unlinked`) has no fill row to show its PnL on, so the blotter's
column then sums short of the others. Plain `sqlite3`, no ORM, like
`ranking/infrastructure/metrics_store.py`.
"""

import contextlib
import sqlite3
import threading
from pathlib import Path

from bots.domain.fill_ledger import FillRecord
from bots.domain.fill_ledger import PositionCloseRecord


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

# `position_realized_pnl` (the round trip's total on its closing fill) and the per-fill
# `realized_pnl` share are legacy since DW-223: still read for rows written before
# `position_closes` existed, never written again. ALTER TABLE (not only CREATE ... IF NOT EXISTS)
# because an existing fills.db predates the column; the "duplicate column" error on an
# already-migrated db is the expected steady state.
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
# Redis-persisted Cache before the process died. The same holds for the close such an inferred
# fill can cause: re-closed under the synthesised trade id, it is not deduplicated against the
# close this store already holds under the venue's, so that round trip would count twice.
# Upgrade path: key inferred fills (detectable by their reconciliation trade id) on
# `(bot_id, client_order_id, cumulative filled qty)` instead, and link their closes by that key.
_UNIQUE_TRADE_INDEX = "CREATE UNIQUE INDEX IF NOT EXISTS idx_bot_trade ON fills(bot_id, trade_id)"

# One row per `PositionClosed` (DW-223/225), keyed by two partial unique indexes. A linked close
# (non-NULL `trade_id`) is unique on `(bot_id, trade_id)`: one fill closes at most one round trip,
# so its trade id names the close whatever position id it arrives under (a migrated
# `legacy-{rowid}` close re-delivered under its real id is the same close), two round trips
# closing in the same ns on one NETTING id (dYdX fills within one block share a timestamp) stay
# two rows, and the blotter's join on it can never multiply a fill row. Only an unlinked close
# falls back to `(bot_id, position_id, ts_closed)`. The two indexes cannot see each other, so
# `write_position_close` also refuses a close whose `(bot_id, position_id, ts_closed)` is already
# stored when either side is unlinked: a close stored unlinked in one process life (its fill out of
# the ledger's lookback, or its conversion failed) and re-delivered linked in a later one, or the
# reverse, is the same close. Only two linked closes with different trade ids are told apart there.
# So a close re-delivered into a later process life is a no-op whichever way each life linked it.
_POSITION_CLOSES_DDL = (
    """
    CREATE TABLE position_closes (
        bot_id        TEXT    NOT NULL,
        position_id   TEXT    NOT NULL,
        ts_closed     INTEGER NOT NULL,
        realized_pnl  REAL    NOT NULL,
        trade_id      TEXT
    )
    """,
    "CREATE UNIQUE INDEX idx_close_bot_trade ON position_closes(bot_id, trade_id) "
    "WHERE trade_id IS NOT NULL",
    "CREATE UNIQUE INDEX idx_close_unlinked ON position_closes(bot_id, position_id, ts_closed) "
    "WHERE trade_id IS NULL",
    "CREATE INDEX idx_close_bot_ts ON position_closes(bot_id, ts_closed)",
)

# Run once, in the transaction that creates `position_closes`: every legacy round trip (a
# closing fill's `position_realized_pnl`) becomes a close at its fill's time, so the per-trip
# stats read the same values before and after the migration. A `legacy-{rowid}` id is unique per
# legacy row; a legacy close with a trade id is keyed by it like any linked close.
# Known limit: the legacy rows cannot be restated exactly. (1) Legacy daily PnL moves from each
# reducing fill's own UTC day to the day its round trip closed. (2) A legacy flip's closed leg,
# which the old per-fill code dropped, was never stored and cannot be recovered. (3) The per-fill
# shares of a position still open at migration are not carried over: its close arrives later as
# a `PositionClosed` with the whole trip's PnL only if the Cache (Redis-persisted for exec bots)
# survives the restart; otherwise that position's realized PnL is gone. (4) Rows written before
# `position_realized_pnl` existed carry only a per-fill `realized_pnl` share, no round trip, so
# they are not copied: their PnL leaves `pnl_by_day`, the equity curve and the blotter. (5) The
# copy runs only when the table is created, so closes an older image writes into `fills` after a
# rollback are never copied on the next upgrade (`docs/DEPLOY_CHECKLIST.md`'s DW-223 entry).
# Upgrade path: rebuild the affected round trips from the venue's own fill history, the only
# source that still holds them.
_LEGACY_CLOSES_COPY = """
INSERT INTO position_closes(bot_id, position_id, ts_closed, realized_pnl, trade_id)
SELECT bot_id, 'legacy-' || rowid, ts, position_realized_pnl, trade_id
FROM fills
WHERE position_realized_pnl IS NOT NULL
"""


def _has_position_closes(db: sqlite3.Connection) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'position_closes'"
    ).fetchone()
    return row is not None


def _ensure_position_closes(db: sqlite3.Connection) -> None:
    """
    Create `position_closes` and copy the legacy closes into it, atomically and only once. The
    steady-state open finds the table without taking a lock, so it never waits on another
    process's write; only when it is missing does `BEGIN IMMEDIATE` take the write lock and
    re-check, so a crash between the two statements, or two processes opening one fresh file,
    can never leave the table created but its legacy rows uncopied (or copied twice).
    """
    if _has_position_closes(db):
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        if not _has_position_closes(db):
            for statement in _POSITION_CLOSES_DDL:
                db.execute(statement)
            db.execute(_LEGACY_CLOSES_COPY)
        db.commit()
    except Exception:
        db.rollback()
        raise


class SqliteFillsStore:
    """
    The `FillsStore` port over one `fills.db` file.

    Invariant: rows are only ever appended (`write_fill`, `write_position_close`), at most one fill
    per `(bot_id, trade_id)` and one close per closing trade id (an unlinked close per
    `(bot_id, position_id, ts_closed)`); one connection, opened on first use and shared by the
    event loop and the executor thread that writes rows, serialised by one lock; after `close()`
    every call raises, so a late write is a loud `bots.fill_lost`/`bots.close_lost`, never a
    second, leaked connection.
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
                _ensure_position_closes(db)
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

    def _insert(self, sql: str, params: tuple) -> bool:
        with self._lock:
            db = self._conn()
            try:
                cursor = db.execute(sql, params)
                db.commit()
            except Exception:
                db.rollback()
                raise
            return cursor.rowcount == 1

    def write_fill(self, record: FillRecord) -> bool:
        """
        Append one fill; False (nothing written) when this bot already stored its trade id.
        `ON CONFLICT ... DO NOTHING`, never `INSERT OR IGNORE`, which would also swallow a NOT NULL
        violation -- a real fault that must raise. The legacy PnL columns stay NULL.
        """
        return self._insert(
            "INSERT INTO fills(ts, bot_id, side, price, qty, trade_id) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(bot_id, trade_id) DO NOTHING",
            (record.ts, record.bot_id, record.side, record.price, record.qty, record.trade_id),
        )

    def write_position_close(self, record: PositionCloseRecord) -> bool:
        """
        Append one round trip's close; False (nothing written) when this bot already stored a
        close with its trade id, or one at its `(position_id, ts_closed)` where either close is
        unlinked (the cross-key check `_POSITION_CLOSES_DDL` explains). `ON CONFLICT DO NOTHING`
        without a target, so it covers both partial unique indexes, and for the reason
        `write_fill` gives never `INSERT OR IGNORE`.
        """
        return self._insert(
            """
            INSERT INTO position_closes(bot_id, position_id, ts_closed, realized_pnl, trade_id)
            SELECT ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM position_closes
                WHERE bot_id = ? AND position_id = ? AND ts_closed = ?
                    AND (trade_id IS NULL OR ? IS NULL)
            )
            ON CONFLICT DO NOTHING
            """,
            (
                record.bot_id,
                record.position_id,
                record.ts_closed,
                record.realized_pnl,
                record.trade_id,
                record.bot_id,
                record.position_id,
                record.ts_closed,
                record.trade_id,
            ),
        )

    def recent_trades(self, bot_id: str, cutoff_ns: int | None, limit: int) -> list[dict]:
        """
        Return the latest `limit` fills at/after `cutoff_ns` (all time if None), ascending. A
        fill's `realized_pnl` is the round trip it closed (its linked `position_closes` row, or a
        legacy row's `position_realized_pnl` when its NULL trade id cannot link), None otherwise.
        """
        rows = self._query(
            """
            SELECT ts, side, price, qty, realized_pnl FROM (
                SELECT f.ts AS ts, f.side AS side, f.price AS price, f.qty AS qty,
                       COALESCE(c.realized_pnl, f.position_realized_pnl) AS realized_pnl
                FROM fills AS f
                LEFT JOIN position_closes AS c
                    ON c.bot_id = f.bot_id AND c.trade_id = f.trade_id
                WHERE f.bot_id = ? AND (? IS NULL OR f.ts >= ?)
                ORDER BY f.ts DESC
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

    def position_realized_pnls(self, bot_id: str, cutoff_ns: int | None) -> list[float]:
        """
        One realized PnL per completed round trip closed at/after `cutoff_ns`, in close order
        (the per-trade stats' input).
        """
        rows = self._query(
            """
            SELECT realized_pnl FROM position_closes
            WHERE bot_id = ? AND (? IS NULL OR ts_closed >= ?)
            ORDER BY ts_closed ASC, rowid ASC
            """,
            (bot_id, cutoff_ns, cutoff_ns),
        )
        return [pnl for (pnl,) in rows]

    def total_realized_pnl(self, bot_id: str) -> float:
        """
        All-time realized PnL over every closed round trip: exactly the sum of
        `position_realized_pnls(bot_id, None)`, summed here in the same order rather than by
        SQLite's `SUM` (whose summation algorithm differs across SQLite versions), so
        `bots:status` equals `bots:history`'s per-trip total to the last bit.
        """
        return sum(self.position_realized_pnls(bot_id, None), 0.0)

    def win_rate_stats(self, bot_id: str) -> tuple[int, int]:
        """(closed_trades, wins) all-time: one per round trip, a win is a positive total."""
        rows = self._query(
            """
            SELECT COUNT(*), SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END)
            FROM position_closes
            WHERE bot_id = ?
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
        """
        Net realized PnL per UTC day a round trip closed (a partial reduction counts on the day
        its round trip closes, not its own).
        """
        rows = self._query(
            """
            SELECT (ts_closed / ?) AS day_bucket, SUM(realized_pnl) AS pnl
            FROM position_closes
            WHERE bot_id = ? AND (? IS NULL OR ts_closed >= ?)
            GROUP BY day_bucket
            ORDER BY day_bucket
            """,
            (_NS_PER_DAY, bot_id, cutoff_ns, cutoff_ns),
        )
        return [{"period_start": day_bucket * _NS_PER_DAY, "pnl": pnl} for day_bucket, pnl in rows]
