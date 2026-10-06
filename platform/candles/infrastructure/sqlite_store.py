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
or not; a bucket row exists for every bucket a snapshot touched, and for every bucket a liquidation
landed in (a liquidation-only row has `seconds_observed = 0`, never counted as coverage). Reads only
return buckets with a trade (`o IS NOT NULL`).

Story 33.3 adds ten nullable INTEGER columns (`domain.fold.AGGREGATE_KEYS`: exact order-flow and
liquidation sums with the bar's precisions, `docs/DATA_DICTIONARY.md` §2.15) and the
`liquidations_applied` table, the exactly-once guard of a liquidation (keyed by its
`venue_event_id`, audit D-150). A file created before the story is migrated in place by
`connect_rw` (`_migrate`: `ALTER TABLE ... ADD COLUMN`), so its old rows read null in the new
columns -- unknown, never 0 -- until `python -m candles.rebuild` refolds their days.

`liquidation_feed_since` persists each feed id's liquidation feed start for the live sink: the one
feed-start rule (`domain.fold.LiquidationArrays.since_ns`) knows a bucket's liquidations only if it
starts at or after it, so the live sink and a later rebuild of the same day store the same `liq_*`
bucket for bucket (Story 33.3 review loop 2, audit D-160). `apply_liquidations` lowers it to the
earliest liquidation it is given, the rebuild to the archive's first one; no row means no start is
known and every bucket's `liq_*` stay null.

The merge is Python (`domain.fold.merge_buckets`), not SQL: a fragment's rows are read, merged and
written back with `INSERT OR REPLACE` (`write_buckets`). SQL could not rescale two parts of different
precision, and SQLite turns an int64 overflow in `+` into a REAL silently; `v` is added the same
IEEE way in both, so it is byte for byte what the old `_UPSERT` stored. Every write path runs its
read and its replace in one `BEGIN IMMEDIATE` transaction (`_immediate`), so no other connection
can write between them (Story 33.3 review loop 1).

`_SCHEMA` and `_REPLACE` are frozen text (spine AD-D12): they are the on-disk contract of a file the
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
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

import numpy as np
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.second_snapshot import SecondRow

from candles.domain.candle_series import CandleSeries
from candles.domain.fold import AGGREGATE_KEYS
from candles.domain.fold import BUCKET_FIELDS
from candles.domain.fold import DAY_MS
from candles.domain.fold import RETAIN_DAYS
from candles.domain.fold import FlowArrays
from candles.domain.fold import FoldedBucket
from candles.domain.fold import LiquidationArrays
from candles.domain.fold import check_storable
from candles.domain.fold import feed_since_ns
from candles.domain.fold import fold_arrays
from candles.domain.fold import fold_rows
from candles.domain.fold import merge_buckets


_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    instrument_id    TEXT    NOT NULL,
    bar_seconds      INTEGER NOT NULL,
    t                INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL,
    v                REAL    NOT NULL,
    seconds_observed INTEGER NOT NULL,
    buy_v INTEGER, sell_v INTEGER, buy_n INTEGER, sell_n INTEGER, pv INTEGER,
    liq_long_v INTEGER, liq_short_v INTEGER, liq_n INTEGER,
    price_precision INTEGER, size_precision INTEGER,
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
CREATE TABLE IF NOT EXISTS liquidations_applied (
    instrument_id  TEXT    NOT NULL,
    venue_event_id TEXT    NOT NULL,
    ts_event       INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, venue_event_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS liquidation_feed_since (
    instrument_id TEXT    PRIMARY KEY,
    since_ns      INTEGER NOT NULL
);
"""

# The bucket's whole row, already merged in Python (`merge_buckets`): the write replaces it.
_REPLACE = """
INSERT OR REPLACE INTO candles(instrument_id, bar_seconds, t, o, h, l, c, v, seconds_observed,
    buy_v, sell_v, buy_n, sell_n, pv, liq_long_v, liq_short_v, liq_n,
    price_precision, size_precision)
VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# The columns `_migrate` adds to a file created before Story 33.3, in `_SCHEMA`'s order, so a
# migrated file and a new one have the same columns in the same order.
AGGREGATE_COLUMNS = AGGREGATE_KEYS
_BUCKET_COLUMNS = ", ".join(("t", *BUCKET_FIELDS))
# How long an applied liquidation's id is kept: the startup catch-up replays one day
# (`capture.application.capture_service._CATCH_UP_MAX_NS`), so two days covers it with a margin.
LIQUIDATIONS_APPLIED_RETAIN_DAYS = 2


class CandleConnection(sqlite3.Connection):
    """
    A candle store connection that remembers its `candles` table's columns once read
    (`table_columns`): a read-only reader of a file the collector has not migrated yet selects
    NULL for the Story 33.3 columns it lacks, without a `PRAGMA` per query.
    """

    candle_columns: frozenset[str] | None = None


def table_columns(db: sqlite3.Connection) -> frozenset[str]:
    """Return the `candles` table's column names (cached on a `CandleConnection`)."""
    cached = db.candle_columns if isinstance(db, CandleConnection) else None
    if cached is not None:
        return cached
    columns = frozenset(row[1] for row in db.execute("PRAGMA table_info(candles)").fetchall())
    if isinstance(db, CandleConnection):
        db.candle_columns = columns
    return columns


def connect_rw(path: str) -> sqlite3.Connection:
    """Open the writer's connection (collector, rebuild CLI), creating the file and schema."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(
        path, check_same_thread=False, timeout=60.0, factory=CandleConnection
    )  # rebuild workers share the file
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        "PRAGMA synchronous=NORMAL"
    )  # WAL + NORMAL: no fsync per commit; a crash loses at most the last commits, rebuildable
    db.executescript(_SCHEMA)
    _migrate(db)
    return db


def _migrate(db: sqlite3.Connection) -> None:
    """
    Add the Story 33.3 columns a file created before it lacks. `ALTER TABLE ... ADD COLUMN` gives
    every existing row NULL there: unknown, never 0 (the rebuild fills them). Idempotent.

    Invariant (one migration): the columns present are read *inside* a `BEGIN IMMEDIATE` write
    transaction, so a collector and a rebuild opening the same unmigrated file together serialise
    on the lock, and the second sees the first's columns and adds none -- without it both would read
    the old columns and the second `ADD COLUMN` would fail with "duplicate column name".
    """
    if isinstance(db, CandleConnection):
        db.candle_columns = None  # never trust a read from outside the lock
    with _immediate(db):
        present = frozenset(row[1] for row in db.execute("PRAGMA table_info(candles)"))
        for column in AGGREGATE_COLUMNS:
            if column not in present:
                db.execute(f"ALTER TABLE candles ADD COLUMN {column} INTEGER")


@contextlib.contextmanager
def connect_ro(path: str) -> Iterator[sqlite3.Connection | None]:
    """Open a read-only connection; yields None when the store does not exist yet (callers fall back)."""
    if not Path(path).exists():
        yield None
        return
    db = sqlite3.connect(
        f"file:{path}?mode=ro", uri=True, check_same_thread=False, factory=CandleConnection
    )
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


@contextlib.contextmanager
def _immediate(db: sqlite3.Connection) -> Iterator[None]:
    """
    One write transaction taken before its first read: `BEGIN IMMEDIATE` holds the file's write
    lock from the start, so a read-merge-write (`write_buckets`) cannot be interleaved by another
    connection's write between its SELECT and its REPLACE. Commits on success, rolls back on any
    exception, including a failed COMMIT (`SQLITE_BUSY`, I/O): SQLite leaves the transaction open
    then, and every later `BEGIN IMMEDIATE` on this connection would fail until a restart. (The
    `sqlite3` module's own implicit BEGIN comes only before the first DML statement, after the
    read: the gap this closes.)
    """
    db.execute("BEGIN IMMEDIATE")
    try:
        yield
        db.commit()
    except BaseException:
        if db.in_transaction:
            db.rollback()
        raise


def write_buckets(
    db: sqlite3.Connection, iid: str, acc: Mapping[tuple[int, int], FoldedBucket]
) -> None:
    """
    Merge one fold's buckets into `candles`: read the stored rows of each width over the fragment's
    `t` range (one SELECT per width), `merge_buckets` each, write them back (`_REPLACE`).

    Invariant (atomic merge): the read and the replace are one transaction the caller opened with
    `_immediate`, so no other connection can change a row between them; called outside a
    transaction it raises `RuntimeError` rather than merge over a read another writer may overtake.
    Raises `CandleOverflowError` before anything is written for a merged sum outside int64 (the
    store's INTEGER range; the only place that bound applies, `domain.fold.check_storable`), and the
    caller's transaction rolls the whole fragment back.
    """
    if not db.in_transaction:
        raise RuntimeError("write_buckets needs the caller's BEGIN IMMEDIATE transaction")
    by_bar: dict[int, dict[int, FoldedBucket]] = {}
    for (bar, t), bucket in acc.items():
        by_bar.setdefault(bar, {})[t] = bucket
    rows = []
    for bar, fragment in sorted(by_bar.items()):
        stored = _stored_buckets(db, iid, bar, min(fragment), max(fragment))
        for t, bucket in sorted(fragment.items()):
            merged = bucket if t not in stored else merge_buckets(stored[t], bucket)
            # The one int64 refusal: the fold and the merge are exact Python ints at any size,
            # and only a value bound to this INTEGER column must fit (sqlite3 would raise a bare
            # OverflowError mid-batch instead of naming the column).
            check_storable(merged)
            rows.append(_row(iid, bar, t, merged))
    db.executemany(_REPLACE, rows)


def _stored_buckets(
    db: sqlite3.Connection, iid: str, bar: int, lo: int, hi: int
) -> dict[int, FoldedBucket]:
    rows = db.execute(
        f"SELECT {_BUCKET_COLUMNS} FROM candles "  # noqa: S608 -- constant column list, values are bound
        "WHERE instrument_id = ? AND bar_seconds = ? AND t >= ? AND t <= ?",
        (iid, bar, lo, hi),
    ).fetchall()
    return {row[0]: FoldedBucket(*row[1:]) for row in rows}


def _row(iid: str, bar: int, t: int, bucket: FoldedBucket) -> tuple:
    return (iid, bar, t, *(getattr(bucket, name) for name in BUCKET_FIELDS))


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
    with _immediate(db):
        series = CandleSeries(iid, _read_watermark(db, iid))
        fresh, through_ns = series.accept(rows)
        if not fresh:
            return 0
        # Seconds carry no liquidation, but a feed instrument's bucket reads 0, not null, from the
        # feed's persisted start on: the empty input says "the feed, and none here"
        # (`apply_liquidations` adds the rows themselves). A bucket before or straddling the start,
        # or every bucket while no start is recorded, is null -- what the rebuild stores too.
        since_ns = _feed_since(db, iid) if has_liquidation_feed(iid) else None
        none_yet: list[Liquidation] | None = None if since_ns is None else []
        write_buckets(
            db, iid, series.buckets(fresh, liquidations=none_yet, liquidations_since_ns=since_ns)
        )
        db.execute("INSERT OR REPLACE INTO built_through VALUES(?, ?)", (iid, through_ns))
        return len(fresh)


def apply_liquidations(
    db: sqlite3.Connection, iid: str, liquidations: Iterable[Liquidation]
) -> int:
    """
    Fold liquidations into every bar size, each venue event exactly once: an id already in
    `liquidations_applied` (a catch-up overlapping the live apply, a replayed batch, a rebuilt
    day) is skipped. Returns the number newly applied. A liquidation may land before its bucket's
    first second; it then creates the row with `seconds_observed = 0`, and the seconds merge in.

    The rows also lower the id's persisted feed start (`liquidation_feed_since`) to their earliest
    `ts_event`, so the seconds applied after them read 0 from the first bucket starting at or after
    it; a liquidation in a bucket straddling the start is recorded as applied but leaves that
    bucket's `liq_*` null, as the rebuild does (`domain.fold.first_bucket_at_or_after`).

    Known limit: the first liquidation an id ever records may arrive in a later flush than seconds
    after it (the liquidation socket is separate); those seconds were applied with no start known,
    so their buckets stay null -- unknown, never a false 0 -- until the nightly rebuild of the day,
    which knows the start from the archive. Upgrade path: a durable "feed confirmed since" marker
    written by capture when the liquidation socket subscribes (D-160's).

    Known limit (null group, claimed id): a liquidation merged into a bucket whose stored `liq_*`
    group is null -- a pre-migration row, or a bucket before or straddling the feed start -- is
    counted in no bar of that width (null + n stays null, `domain.fold.merge_buckets`) until the
    nightly rebuild of its day recounts it from the archive; its `venue_event_id` is nonetheless
    claimed in `liquidations_applied`, because a claim is per liquidation, not per width (the same
    row may be counted at 1m and null at 1D), so a catch-up replay does not re-offer it either.
    Upgrade path: the operator's history rebuild (DEPLOY_CHECKLIST 33-3) refolds every
    pre-migration day, after which only the straddling bucket -- null by the feed-start rule -- is
    left, where null is the correct value.

    An instrument without a liquidation feed (`has_liquidation_feed`) is refused with `ValueError`:
    its `liq_*` columns are null by definition, and a row here would contradict that. So is a row
    whose `instrument_id` is not `iid`: it would land in another instrument's bars.
    """
    if not has_liquidation_feed(iid):
        raise ValueError(f"{iid} has no liquidation feed: its liquidation columns stay null")
    rows = list(liquidations)
    foreign = sorted({row.instrument_id.value for row in rows} - {iid})
    if foreign:
        raise ValueError(f"liquidations of {foreign} applied under {iid}: refused")
    with _immediate(db):
        since_ns = feed_since_ns(_feed_since(db, iid), rows)
        if since_ns is not None:
            _record_feed_since(db, iid, since_ns)
        fresh = [row for row in rows if _claim(db, iid, row)]
        if fresh:
            nothing = np.empty(0, dtype=np.float64)
            folded = fold_arrays(
                np.empty(0, dtype=np.int64),
                *(nothing for _ in range(5)),
                flow=FlowArrays.empty(),
                liquidations=LiquidationArrays.of(fresh, since_ns),
            )
            write_buckets(db, iid, folded)
    return len(fresh)


class RebuildRaceError(RuntimeError):
    """A rebuild's archive read missed a liquidation the live sink applied meanwhile (rerun it)."""


def _feed_since(db: sqlite3.Connection, iid: str) -> int | None:
    """Return the id's persisted liquidation feed start (ns), None when none is recorded."""
    row = db.execute(
        "SELECT since_ns FROM liquidation_feed_since WHERE instrument_id = ?", (iid,)
    ).fetchone()
    return None if row is None else int(row[0])


def _record_feed_since(db: sqlite3.Connection, iid: str, since_ns: int) -> None:
    """Lower (never raise) the id's persisted feed start to `since_ns`."""
    db.execute(
        "INSERT INTO liquidation_feed_since VALUES(?, ?) ON CONFLICT(instrument_id) DO UPDATE "
        "SET since_ns = min(since_ns, excluded.since_ns)",
        (iid, since_ns),
    )


def feed_since(db: sqlite3.Connection, iid: str) -> int | None:
    """Return the id's persisted liquidation feed start (ns), None when none is recorded."""
    return _feed_since(db, iid)


def _claim(db: sqlite3.Connection, iid: str, row: Liquidation) -> bool:
    """Record one venue event as applied; False when it already was (the dedup, D-150)."""
    cursor = db.execute(
        "INSERT OR IGNORE INTO liquidations_applied(instrument_id, venue_event_id, ts_event) "
        "VALUES(?, ?, ?)",
        (iid, row.venue_event_id, row.ts_event),
    )
    return cursor.rowcount == 1


class _RebuildLiquidations(NamedTuple):
    """
    A rebuilt span's liquidation input: `applied` the rows whose ids the span's
    `liquidations_applied` becomes (None: no feed, the table is left alone), `since_ns` the feed
    start the fold is bounded by (None: no start known, `liq_*` null for the whole span).
    """

    applied: list[Liquidation] | None
    since_ns: int | None

    def fold_input(self) -> list[Liquidation] | None:
        """Return the fold's `liquidations`: None (every bucket null) unless a start is known."""
        return None if self.since_ns is None else self.applied


def _feed_input(
    db: sqlite3.Connection,
    iid: str,
    liquidations: Sequence[Liquidation] | None,
    archive_since_ns: int | None,
    span: tuple[int, int],
) -> _RebuildLiquidations:
    """
    Refuse a rebuild input that contradicts the feed predicate, and bound the rest in time.

    A feed instrument rebuilt without its liquidations would store 0 where the archive holds some,
    and a no-feed one given rows (or a bound) would invent a feed: both raise `ValueError`. A feed
    instrument's start is the earliest of the store's persisted one (`liquidation_feed_since`, what
    the live sink folded with), `archive_since_ns` (its first archived liquidation,
    `kernel.catalog_files.liquidation_feed_since_ns`) and the rows given, and is persisted back, so
    the rebuild and the live sink bound the same buckets (`domain.fold.LiquidationArrays.since_ns`,
    audit D-160). None of the three knowing one means every bucket reads null, never 0. Runs inside
    the rebuild's transaction (it reads and lowers the persisted start).
    """
    if not has_liquidation_feed(iid):
        if liquidations is not None or archive_since_ns is not None:
            raise ValueError(f"{iid} has no liquidation feed: rebuild it with liquidations=None")
        return _RebuildLiquidations(None, None)
    if liquidations is None:
        raise ValueError(f"{iid} has a liquidation feed: rebuild it with that day's liquidations")
    taken = [row for row in liquidations if span[0] <= row.ts_event // 1_000_000 < span[1]]
    bound = _feed_since(db, iid)
    if archive_since_ns is not None:
        bound = archive_since_ns if bound is None else min(bound, archive_since_ns)
    since_ns = feed_since_ns(bound, taken)
    if since_ns is not None:
        _record_feed_since(db, iid, since_ns)
    return _RebuildLiquidations(taken, since_ns)


def _replace_applied(
    db: sqlite3.Connection, iid: str, lo_ms: int, hi_ms: int, rows: Sequence[Liquidation]
) -> None:
    """
    Make a rebuilt span's applied ids exactly the liquidations its fold just took.

    Refuses (`RebuildRaceError`, the whole rebuild rolls back) when the live sink already applied an
    id of the span that the rebuild's input lacks: the rebuild read the archive before taking its
    lock, and the collector flushed and applied that liquidation in between (a late flush of a just
    closed day). Replacing the ids would delete the claim and refold without it, so the bar would
    be short of a liquidation no later apply restores. Refused, the live rows stay (they hold it)
    and a rerun reads it from the archive.
    """
    taken = {row.venue_event_id for row in rows}
    claimed = db.execute(
        "SELECT venue_event_id FROM liquidations_applied "
        "WHERE instrument_id = ? AND ts_event >= ? AND ts_event < ?",
        (iid, lo_ms * 1_000_000, hi_ms * 1_000_000),
    ).fetchall()
    missing = sorted(key for (key,) in claimed if key not in taken)
    if missing:
        raise RebuildRaceError(
            f"{iid}: {len(missing)} liquidation(s) applied live but not in the rebuild's input "
            f"(first {missing[0]!r}): the archive changed under the rebuild, rerun it"
        )
    db.execute(
        "DELETE FROM liquidations_applied WHERE instrument_id = ? AND ts_event >= ? AND ts_event < ?",
        (iid, lo_ms * 1_000_000, hi_ms * 1_000_000),
    )
    db.executemany(
        "INSERT OR IGNORE INTO liquidations_applied(instrument_id, venue_event_id, ts_event) "
        "VALUES(?, ?, ?)",
        [(iid, row.venue_event_id, row.ts_event) for row in rows],
    )


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
    liquidations: Sequence[Liquidation] | None = None,
    liquidations_since_ns: int | None = None,
) -> int:
    """
    Recompute every bucket in whole UTC days covering [start_ms, end_ms) from `rows` (all of that
    instrument's seconds in those days, any order) and, for a feed instrument, `liquidations` (all
    of its liquidations in those days; None for an instrument without the feed) with
    `liquidations_since_ns`, its first archived liquidation (lowered to the store's persisted feed
    start and the rows' own; none known: `liq_*` null throughout; `_feed_input`). Idempotent. Whole
    days, so no bucket is half-rebuilt. Today is excluded unless `allow_open_day` (only safe with
    the collector stopped): seconds the collector applied but the archive has not flushed yet would
    be deleted and lost. The span's `liquidations_applied` ids are replaced by the ones folded here,
    so a later live or catch-up apply of them is a no-op. Returns the number of seconds applied.
    """
    lo, hi = _day_bounds(start_ms, end_ms, allow_open_day)
    seconds = sorted(
        (r for r in rows if lo <= r.ts_event // 1_000_000 < hi), key=lambda r: r.ts_event
    )
    with _immediate(db):
        taken = _feed_input(db, iid, liquidations, liquidations_since_ns, (lo, hi))
        folded = fold_rows(
            seconds, liquidations=taken.fold_input(), liquidations_since_ns=taken.since_ns
        )
        db.execute(
            "DELETE FROM candles WHERE instrument_id = ? AND t >= ? AND t < ?", (iid, lo, hi)
        )
        write_buckets(db, iid, folded)
        if taken.applied is not None:
            _replace_applied(db, iid, lo, hi, taken.applied)
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
    liquidations: Sequence[Liquidation] | None = None,
    liquidations_since_ns: int | None = None,
) -> int:
    """
    `rebuild` over column arrays (`ts_ms`, `o`, `h`, `l`, `c`, `v`; NaN = no trade; and the integer
    order-flow inputs `kernel.catalog_files.second_ohlc_arrays` returns), as the rebuild CLI reads
    them straight out of Parquet without per-row Python objects.
    """
    lo, hi = _day_bounds(start_ms, end_ms, allow_open_day)
    keep = (cols["ts_ms"] >= lo) & (cols["ts_ms"] < hi)
    ts_ms = cols["ts_ms"][keep]
    with _immediate(db):
        taken = _feed_input(db, iid, liquidations, liquidations_since_ns, (lo, hi))
        rows = taken.fold_input()
        folded = fold_arrays(
            ts_ms,
            *(cols[k][keep] for k in ("o", "h", "l", "c", "v")),
            flow=_flow_columns(cols, keep),
            liquidations=None if rows is None else LiquidationArrays.of(rows, taken.since_ns),
        )
        db.execute(
            "DELETE FROM candles WHERE instrument_id = ? AND t >= ? AND t < ?", (iid, lo, hi)
        )
        if folded:
            write_buckets(db, iid, folded)
        if taken.applied is not None:
            _replace_applied(db, iid, lo, hi, taken.applied)
        if len(ts_ms):
            _advance_watermark(db, iid, int(ts_ms.max()) * 1_000_000)
    return len(ts_ms)


def _flow_columns(cols: Mapping[str, np.ndarray], keep: np.ndarray) -> FlowArrays:
    """Return the order-flow inputs of a `second_ohlc_arrays` result, masked."""
    return FlowArrays(
        *(
            cols[key][keep]
            for key in (
                "close_units",
                "buy_units",
                "sell_units",
                "buy_n",
                "sell_n",
                "price_precision",
                "size_precision",
            )
        )
    )


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
        # An id older than any catch-up can replay is no longer needed to dedup (Story 33.3).
        horizon_ns = (now_ms - LIQUIDATIONS_APPLIED_RETAIN_DAYS * DAY_MS) * 1_000_000
        db.execute("DELETE FROM liquidations_applied WHERE ts_event < ?", (horizon_ns,))


class CandleStore:
    """
    One venue's `candles_<venue>.db`, opened read-write.

    Invariant (one writer of a given instrument-day): this class is the only code in `platform/`
    that opens a candle store read-write, and no two writers ever touch the same instrument-day --
    that venue's collector owns the open UTC day, and `candles.rebuild` (the nightly saga's step,
    or by hand) recomputes CLOSED days, which it may do while the collector keeps writing the open
    day: their rows are disjoint, and every write path (`apply_seconds`, `apply_liquidations`,
    `rebuild*`) runs its read-merge-replace, and its update of the per-instrument
    `liquidation_feed_since` / `liquidations_applied` rows, inside one `BEGIN IMMEDIATE`
    transaction (`_immediate`), so the two processes' writes are serialised, never interleaved. The
    rebuild reads the archive before that lock, so a closed day's liquidation the collector flushes
    and applies late, between the two, would be refolded out of its bar; the rebuild refuses then
    (`RebuildRaceError`, `_replace_applied`) and a rerun reads it.
    Only `--include-open-day` (`allow_open_day`) rewrites the collector's day and so needs that
    venue's collector stopped. The commands that would violate it are a second writer's `apply` over the
    same instrument (two watermarks over one accumulating merge, so volumes double, and a merge
    whose read another writer's replace overtakes) and any
    reader that "just" opened it read-write to mark a day verified; both go through this class or
    through the `VerifiedDays` port instead.

    Known limit: the *file* does take concurrent writers. `candles.rebuild --workers` defaults to
    `os.cpu_count()` and each worker opens its own `CandleStore` on the same file, partitioned by
    instrument so the invariant above still holds; they serialise on SQLite's WAL lock with
    `connect_rw`'s 60 s busy timeout, and a day wide enough to exhaust it fails the rebuild with
    `sqlite3.OperationalError: database is locked` (`--workers 1`, which `nightly` uses, avoids it).
    Upgrade path: one writer process fed by a queue, or per-instrument store files.

    Satisfies `capture.application.ports.SecondSink` through `CandleSink`, and
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

    def apply_liquidations(self, instrument_id: str, rows: Iterable[Liquidation]) -> int:
        """Fold flushed liquidations in, each venue event once; returns how many were new."""
        return apply_liquidations(self._db, instrument_id, rows)

    def watermarks(self) -> Mapping[str, int]:
        """instrument_id -> `ts_event` (ns) of the last second applied."""
        return watermarks(self._db)

    def rebuild_day(
        self,
        instrument_id: str,
        cols: dict[str, np.ndarray],
        day_ms: int,
        allow_open_day: bool = False,
        liquidations: Sequence[Liquidation] | None = None,
        liquidations_since_ns: int | None = None,
    ) -> int:
        """
        Recompute one whole UTC day from raw 1s columns and, for a feed instrument, that day's
        liquidations (None without the feed) with the id's first archived liquidation
        (`liquidations_since_ns`, `rebuild`); idempotent.
        """
        return rebuild_from_arrays(
            self._db,
            instrument_id,
            cols,
            day_ms,
            day_ms + DAY_MS,
            allow_open_day,
            liquidations,
            liquidations_since_ns,
        )

    def rebuild(
        self,
        instrument_id: str,
        rows: Iterable[SecondRow],
        start_ms: int,
        end_ms: int,
        allow_open_day: bool = False,
        liquidations: Sequence[Liquidation] | None = None,
        liquidations_since_ns: int | None = None,
    ) -> int:
        """Recompute the whole UTC days covering [start_ms, end_ms) from `rows`; idempotent."""
        return rebuild(
            self._db,
            instrument_id,
            rows,
            start_ms,
            end_ms,
            allow_open_day,
            liquidations,
            liquidations_since_ns,
        )

    def prune(self, now_ms: int | None = None) -> None:
        """
        Drop 1m/5m bars past their retention (`RETAIN_DAYS`); wide bars are kept. Applied
        liquidation ids older than `LIQUIDATIONS_APPLIED_RETAIN_DAYS` go too.
        """
        prune(self._db, now_ms)

    def mark_verified(
        self, instrument_id: str, day: str, status: str, mismatches: int, checked_at_ms: int
    ) -> None:
        mark_verified(self._db, instrument_id, day, status, mismatches, checked_at_ms)

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        return verified_status(self._db, instrument_id, day)

    def close(self) -> None:
        self._db.close()
