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
"""SQLite adapters for the `VerifiedDays` port: one store file, or a directory of them."""

import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self

from kernel.venues import venue_of

from candles.infrastructure.sqlite_store import clear_verified
from candles.infrastructure.sqlite_store import connect_rw
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.sqlite_store import mark_verified
from candles.infrastructure.sqlite_store import verified_status


def candle_store_dir_problem(candles_dir: str | Path) -> str | None:
    """
    Return why `candles_dir` cannot be a writer's verdict-clearing store directory, or None.

    A clear on a venue whose `candles_<venue>.db` is absent is a no-op ("no store, no verdict"), so
    a typo'd or wrong directory -- the catalog, a host path instead of the container's -- would
    make every clear silently find nothing while the real store's stale `pass` stands. A directory
    holding no store file at all is therefore refused: on a running deployment every collector
    writes its venue's store (`CANDLES_DB_PATH`), so the right directory always holds one.
    """
    path = Path(candles_dir)
    if not path.is_dir():
        return f"{candles_dir} is not an existing directory"
    if not any(path.glob("candles_*.db")):
        return f"{candles_dir} holds no candle store (candles_<venue>.db)"
    return None


class VerifiedDaysStore:
    """
    `VerifiedDays` over one named `candles_<venue>.db` (`compare_klines --db`).

    A verdict write opens the file read-write for exactly that write and closes it again: the
    collector is the store's long-lived writer, and holding a second read-write connection open for
    a whole reconciliation run would keep a WAL writer lock against it for minutes. A read opens it
    read-only, and a store that does not exist yet reads as "never verified" (None) rather than
    failing -- an unverified day keeps its files.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path

    @property
    def path(self) -> str:
        return self._path

    def mark_verified(
        self, instrument_id: str, day: str, status: str, mismatches: int, checked_at_ms: int
    ) -> None:
        db = connect_rw(self._path)
        try:
            mark_verified(db, instrument_id, day, status, mismatches, checked_at_ms)
        finally:
            db.close()

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        if not Path(self._path).exists():
            return None
        db = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, check_same_thread=False)
        try:
            return verified_status(db, instrument_id, day)
        finally:
            db.close()

    def clear_verified(self, instrument_id: str, day: str) -> None:
        if not Path(self._path).exists():
            return  # no store, no verdict: never create one just to delete from it
        # `mode=rw` (not `connect_rw`): an existing file only, and no schema written into it.
        db = sqlite3.connect(
            f"file:{self._path}?mode=rw", uri=True, check_same_thread=False, timeout=60.0
        )
        try:
            clear_verified(db, instrument_id, day)
        finally:
            db.close()


class VerifiedDaysDir:
    """
    `VerifiedDays` over a directory of per-venue stores, resolving the file from the instrument id.

    `prune_catalog` walks every venue's trade leaves in one run and asks for a status per
    instrument-day, so each venue's file is opened read-only once and the connection is reused --
    per-call opens would be thousands of them per nightly run. Use it as a context manager (or call
    `close`) so those connections do not outlive the run.

    Only an open connection is cached. A venue whose file is absent is re-probed on every call,
    because `mark_verified` on this same object creates that file (`connect_rw` writes the schema),
    so remembering "absent" would make a verdict this adapter just wrote read back as unverified.
    `clear_verified` never creates a missing file: an absent store has no verdict to clear.
    """

    def __init__(self, candles_dir: str | Path) -> None:
        self._dir = Path(candles_dir)
        self._connections: dict[str, sqlite3.Connection] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _reader(self, venue: str) -> sqlite3.Connection | None:
        cached = self._connections.get(venue)
        if cached is not None:
            return cached
        path = db_path_for_venue(self._dir, venue)
        if not Path(path).exists():
            return None
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self._connections[venue] = db
        return db

    def mark_verified(
        self, instrument_id: str, day: str, status: str, mismatches: int, checked_at_ms: int
    ) -> None:
        store = VerifiedDaysStore(db_path_for_venue(self._dir, venue_of(instrument_id)))
        store.mark_verified(instrument_id, day, status, mismatches, checked_at_ms)

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        db = self._reader(venue_of(instrument_id))
        return None if db is None else verified_status(db, instrument_id, day)

    def clear_verified(self, instrument_id: str, day: str) -> None:
        # Through its own read-write connection, closed again: the cached read-only one starts a
        # fresh read transaction per statement (autocommit), so its next read sees the delete.
        store = VerifiedDaysStore(db_path_for_venue(self._dir, venue_of(instrument_id)))
        store.clear_verified(instrument_id, day)

    def close(self) -> None:
        for db in self._connections.values():
            db.close()
        self._connections.clear()
