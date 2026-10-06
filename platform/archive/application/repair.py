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
Find and repair second snapshots whose trade OHLC is impossible given their own book.

Detection is `kernel.second_snapshot.ohlc_outside_book` -- see there for why it is sound. It
finds the rows written by the pre-fix collector, which counted dYdX's subscribed-reply trade
history as live trades (before the `stale_trade_seconds` filter, now in `capture/application/config.py`).

Repair, per flagged second: its stored row keeps every column but the eight trade columns, which
are set to the kernel's no-trade units at the row's own `price_precision`/`size_precision`
(`SecondTradeFields().snapshot_units`: OHLC None, volumes and counts 0 -- the real trades in that
second cannot be told apart from the replayed ones, so "no trade recorded" is the honest value,
DATA-01). The row is found by its `(ts_init, ts_event)` in the snapshot files whose catalog name
span holds its `ts_init`, and cleared in that same file (a verified temp renamed over it, under the
same name): every other row of the file comes out unchanged, in order, and no file is split or
renamed to another span -- only a file a duplicate drop empties is removed (below). A flagged
row of the current UTC day, or in a file reaching it, is skipped and ledgered (`closed_rows`,
`repair.open_day`): capture is that day's writer, and the rewrite replaces whole files.

Only pre-archive rows are repaired (`uncovered_rows`, DW-206): a row at or after the instrument's
trade-archive coverage start (`coverage_start`: the earlier of the earliest stored trade,
`archive.application.rebuild_day.covered_from`, and the earliest archive-gap marker, so a prune
cannot move it later) is refused and ledgered once per instrument (`repair.covered`).
`rebuild_seconds` is the repair of such a row: a rebuilt second holds the trades whose exchange
time falls in it while its book is sampled on arrival, so a fast market can put a real trade
outside that book, which this detector would clear.
An instrument with no archive at all has every row pre-archive.

Write path (DW-204): every changed file of the instrument is built in memory, then staged through
the `CatalogWriter` (`stage_rewrite`, `RewriteMode.WHOLE_FILE`: a verified temp, nothing renamed),
then the touched days' verdicts are cleared, then every temp is renamed over its file
(`commit_rewrites`) -- the same stage -> clear -> commit order as `rebuild_day`. The temps are
renamed in reverse path order, after any file a duplicate drop empties is removed: a dropped copy
always sits in a later path than the copy kept for its key (below), so the kept copy is renamed
last and, until it is, the key's original row is still stored and still flagged. No row is absent
from the catalog at any instant, and the files are written with the archive's compact zstd
settings (`CatalogFiles`). A stage that fails (an unverifiable temp, `RewriteVerifyError`; a file
reaching today, `OpenDayWriteError`; an I/O error) discards every temp: files untouched,
`repair.error`.

Duplicates: an earlier repair (delete then write, before DW-204) could leave a cleared copy of a
second beside its original. In sorted path order, then row order, the first stored copy of a
flagged `(ts_init, ts_event)` is cleared and kept; a later copy is dropped only when its cleared
form equals the kept one in every column (`repair.duplicate`, with the count) -- no information is
lost. A key whose copies differ beyond the trade columns is refused (`repair.error`), nothing of it
changed. A file left with no row by such a drop is removed before the first rename
(`CatalogWriter.remove_merged_sources`: the kept file holds a copy of its one row); a removal that
fails discards every temp (`repair.error`, nothing renamed). A flagged key found in no file is
skipped (`repair.error`). A snapshot file whose name the catalog did not write is skipped and
ledgered (`catalog.foreign_file`, DW-181) while `ParquetDataCatalog.query` -- and so the detector
-- still reads it: a copy of a flagged row in it cannot be repaired, so its instrument never
reports a complete repair (False, exit 2) until the file is moved out of the leaf. A float-layout (pre-30.2) file holding a flagged row
refuses the instrument (`repair.error`: run `archive.tools.migrate_snapshot_ints` first).

Verdict (DW-203): after every changed file is staged and verified, and before the first rename,
every UTC day (of `ts_event`) holding a repaired row loses its stored `verified_days` verdict
(`VerifiedDays.clear_verified`): the verdict judged trade values the repair replaces, so the day
stays unverified -- its trades kept by `prune_catalog` -- until a rerun of the nightly saga for
that day (`make nightly VENUE=<v> DAY=<day>`) rebuilds and reconciles it again: nothing re-runs a
day the scheduler's watermark has passed. A verdict that cannot be cleared discards the temps and
stops the instrument (`repair.error`, files untouched).

Known limit: the renames of one instrument's files are individually atomic, not as a set. A rename
failing part-way leaves the instrument partially repaired -- ledgered `repair.error` naming the
files committed and the files kept. Every file is still whole and every second is still stored
(cleared or not), and the repair is idempotent: by the reverse rename order, every key not fully
repaired still has its flagged original stored, so a rerun finds it and completes it, duplicate
drops included. The candle-store days are rebuilt whenever a commit was attempted, even after a
failed one; an exception during the commit or that rebuild is ledgered (`repair.error`, naming
the days whose candles must be rebuilt with `python -m candles.rebuild`, since a renamed file's
cleared rows are no longer flagged by a rerun) and the instrument reported unrepaired, and an
interrupt is ledgered alike before it propagates.
Upgrade path: a per-leaf intent log replayed at the next run's start.

Known limit: a flagged row after the coverage start that lies inside an archive-gap span is
repaired by neither tool: `rebuild_seconds` keeps a gap row's live values, and this tool refuses
every covered row (operator decision: pre-archive rows only). It stays flagged and ledgered
(`repair.covered`) at every run. Upgrade path: let the repair accept rows `Coverage.in_gap` holds,
once an operator decision allows it.

Known limit: when the archive's first trade file has been pruned, its `pruned` marker -- widened
by the skew bound, `MAX_TS_INIT_SKEW_NS` (300 s) -- can place the coverage start up to that bound
before the first archived trade, so a flagged pre-archive row in that window is refused and
repaired by neither tool. It stays flagged and ledgered (`repair.covered`) at every run, never
silently kept. Upgrade path: the same recorded archive start as the next limit.

Known limit: a trade file pruned before `pruned` markers existed leaves no trace, so on such a
catalog the coverage start can sit after days that were rebuilt. Every prune since retention moved
to the archive (Story 25.1) writes the marker first. Upgrade path: record the archive's start in
the catalog (`covered_from`'s own Known limit).
"""

import logging
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from candles.application.rebuild import day_chunks
from candles.application.rebuild import rebuild_instrument
from candles.application.verified_days import VerifiedDays
from kernel.catalog_files import named_spans
from kernel.catalog_files import snapshot_files
from kernel.clocks import READ_SPAN_MARGIN_NS
from kernel.clocks import CatalogFileSpan
from kernel.fold import SecondTradeFields
from kernel.second_snapshot import MIGRATION_TOOL
from kernel.second_snapshot import PRECISION_COLUMNS
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SnapshotTradeUnits
from kernel.second_snapshot import ohlc_outside_book
from observability import error_ledger

from archive.application.ports import CatalogWriter
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteMode
from archive.application.ports import StagedRewrite
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_S_NS = 1_000_000_000
_TRADE_COLUMNS = SnapshotTradeUnits._fields
_KEY_COLUMNS = ("ts_init", "ts_event")
_NO_TRADE = SecondTradeFields()

# A flagged second's identity in the catalog, and where one stored copy of it sits.
_Key = tuple[int, int]  # (ts_init, ts_event)
_Spot = tuple[str, int]  # (file path, row index)


class _RefusedError(Exception):
    """The instrument must not be repaired; the message is its `repair.error` detail."""


@dataclass
class _FileEdit:
    """Which rows of one file the repair clears (kept) and drops (an identical extra copy)."""

    clear: set[int] = field(default_factory=set)
    drop: set[int] = field(default_factory=set)


@dataclass
class _Plan:
    edits: dict[str, _FileEdit]
    repaired: list[_Key]
    foreign: list[str] = field(default_factory=list)  # skipped files a copy could sit in


def second_snapshots(
    catalog_path: str, iid: str, start_ns: int, end_ns: int
) -> list[DydxSecondSnapshot]:
    """
    `DydxSecondSnapshot` rows of `iid` with `ts_event` in [start_ns, end_ns], CustomData-unwrapped.

    The same query as `views.catalog_reads.query_second_snapshots` (archive imports no views):
    `query` bounds on `ts_init` and the window is `ts_event`, and a row's skew runs either way
    (`kernel.clocks.READ_SPAN_MARGIN_NS`), so both ends are widened by the read margin (the start
    clamped at 0) and the exact `ts_event` filter decides.
    """
    results = ParquetDataCatalog(catalog_path).query(
        data_cls=DydxSecondSnapshot,
        identifiers=[iid],
        start=max(0, start_ns - READ_SPAN_MARGIN_NS),
        end=end_ns + READ_SPAN_MARGIN_NS,
    )
    snapshots = [r.data if hasattr(r, "data") else r for r in results]
    return [s for s in snapshots if start_ns <= s.ts_event <= end_ns]


def find_impossible_snapshots(
    catalog_path: str, iid: str, start_ns: int, end_ns: int
) -> list[DydxSecondSnapshot]:
    return [
        snap
        for a, b in day_chunks(start_ns, end_ns)
        for snap in second_snapshots(catalog_path, iid, a, min(b, end_ns))
        if ohlc_outside_book(snap)
    ]


def coverage_start(first_trade_ns: int | None, gaps: list[tuple[int, int]]) -> int | None:
    """
    Return when the instrument's trade archive began: the earlier of its earliest stored trade's
    second (`rebuild_day.covered_from`) and its earliest archive-gap marker; None when neither
    exists. The markers are what keep this from moving later: `prune_catalog` records a `pruned`
    marker over a trade file before it deletes it, so a pruned (verified, rebuilt) day stays
    covered after its trades are gone -- the earliest *stored* trade alone would then move past
    it and hand that day's rebuilt rows to this repair. A `write_failed`/`quarantined` marker is
    written only once the archive exists, so no marker reaches before the real start (a `pruned`
    span is widened by the skew bound, which can refuse up to that bound of pre-archive rows: the
    module's Known limit).
    """
    starts = [start for start, _ in gaps]
    if first_trade_ns is not None:
        starts.append(first_trade_ns)
    return min(starts, default=None)


def uncovered_rows(
    iid: str, flagged: list[DydxSecondSnapshot], archive_start: int | None
) -> list[DydxSecondSnapshot]:
    """
    Keep the flagged rows before the instrument's trade-archive coverage start (all of them when
    there is no archive, `archive_start` None); the rest are refused, ledgered once
    (`repair.covered`): the archive covers them, so `rebuild_seconds` is their repair.
    """
    if archive_start is None:
        return list(flagged)
    covered = sorted(s.ts_event for s in flagged if s.ts_event >= archive_start)
    if covered:
        error_ledger.record(
            "repair.covered",
            f"{iid}: {len(covered)} flagged row(s), ts_event {covered[0]}..{covered[-1]}, lie at "
            f"or after the trade archive's coverage start {archive_start}: the archive covers "
            f"them, so `rebuild_seconds` is their repair; not repaired",
        )
    return [s for s in flagged if s.ts_event < archive_start]


def _containing_spans(catalog_path: str, iid: str, ts_init: int) -> list[tuple[int, int]]:
    """Return the name spans of every snapshot file of `iid` that can hold a row with this `ts_init`."""
    spans = []
    for path in snapshot_files(catalog_path, iid):
        try:
            span = CatalogFileSpan.from_path(path)
        except ValueError:
            continue  # not a catalog-written name: the catalog never reads it as data
        if span.start_ns <= ts_init <= span.end_ns:
            spans.append((span.start_ns, span.end_ns))
    return spans


def closed_rows(
    writer: CatalogWriter, catalog_path: str, iid: str, flagged: list[DydxSecondSnapshot]
) -> list[DydxSecondSnapshot]:
    """
    Keep the flagged rows archive may rewrite; ledger and skip each one that touches the current
    UTC day. The repair replaces whole files (`RewriteMode.WHOLE_FILE`), so every file of the leaf
    whose name span contains the row's `ts_init` must be closed too -- one crossing midnight into
    today is capture's.
    """
    closed = []
    for snap in flagged:
        spans = [(snap.ts_init, snap.ts_init), *_containing_spans(catalog_path, iid, snap.ts_init)]
        try:
            for start, end in spans:
                writer.assert_span_closed(start, end)
        except OpenDayWriteError as e:
            error_ledger.record("repair.open_day", f"{iid} ts={snap.ts_event}: {e}; not repaired")
            continue
        closed.append(snap)
    return closed


def _candidate_files(
    catalog_path: str, iid: str, ts_inits: set[int], foreign: list[str]
) -> list[str]:
    """
    Every snapshot file whose name span holds one of `ts_inits`, in sorted path order. A name the
    catalog did not write is skipped and ledgered (`catalog.foreign_file`), as `rebuild_day` does,
    and its detail appended to `foreign`: the catalog's reads (the detector's too) still see it.
    """

    def _skipped(site: str, detail: str) -> None:
        error_ledger.record(site, detail)
        foreign.append(detail)

    return sorted(
        path
        for path, span in named_spans(snapshot_files(catalog_path, iid), _skipped)
        if any(span.start_ns <= t <= span.end_ns for t in ts_inits)
    )


def _require_layout(path: str, schema: pa.Schema) -> None:
    """Refuse a file the no-trade units cannot be written into (float layout, no trade column)."""
    missing = [c for c in (*PRECISION_COLUMNS, *_TRADE_COLUMNS) if c not in schema.names]
    if missing:
        raise _RefusedError(
            f"{path}: lacks {missing} (a float-layout file holds no precision column); run "
            f"`python -m {MIGRATION_TOOL} --apply` first; not repaired"
        )


def _locate(files: list[str], keys: set[_Key]) -> dict[_Key, list[_Spot]]:
    """Every stored copy of each key, in sorted path order then row order."""
    located: dict[_Key, list[_Spot]] = {key: [] for key in keys}
    for path in files:
        table = pq.read_table(path, columns=list(_KEY_COLUMNS))
        rows = zip(*(table.column(c).to_pylist() for c in _KEY_COLUMNS), strict=True)
        spots = [(i, key) for i, key in enumerate(rows) if key in keys]
        if spots:
            _require_layout(path, pq.read_schema(path))
        for i, key in spots:
            located[key].append((path, i))
    return located


def _no_trade_units(price_precision: int, size_precision: int) -> dict[str, Any]:
    return _NO_TRADE.snapshot_units(price_precision, size_precision)._asdict()


def _cleared_row(spot: _Spot) -> dict[str, Any]:
    """Return the stored row at `spot`, every column, its trade columns set to no-trade units."""
    path, index = spot
    row = pq.read_table(path).slice(index, 1).to_pylist()[0]
    row.update(_no_trade_units(row["price_precision"], row["size_precision"]))
    return row


def _resolve_key(iid: str, key: _Key, spots: list[_Spot], foreign: list[str]) -> list[_Spot] | None:
    """
    Return the copies of `key` to drop (empty for a single copy); None (ledgered) when the key is
    refused: in no catalog-named file (perhaps only in a skipped `foreign` one), or a later copy
    that differs from the kept one beyond the trade columns.
    """
    what = f"{iid} ts_init={key[0]} ts_event={key[1]}"
    if not spots:
        where = (
            f"in no catalog-named file; {len(foreign)} foreign-named file(s) skipped may hold it"
            if foreign
            else "not stored"
        )
        error_ledger.record("repair.error", f"{what}: flagged row {where}; not repaired")
        return None
    extra = spots[1:]
    if not extra:
        return []
    kept = _cleared_row(spots[0])
    if any(_cleared_row(spot) != kept for spot in extra):
        error_ledger.record(
            "repair.error",
            f"{what}: {len(spots)} stored copies differ beyond the trade columns "
            f"({[path for path, _ in spots]}); refused, not repaired",
        )
        return None
    error_ledger.record(
        "repair.duplicate",
        f"{what}: {len(extra)} extra stored copy(ies) dropped: each is, trade columns cleared, "
        f"identical to the kept cleared row",
    )
    return extra


def _plan(catalog_path: str, iid: str, flagged: list[DydxSecondSnapshot]) -> _Plan:
    """Decide, per file, which rows are cleared and which dropped; nothing is written."""
    keys = {(s.ts_init, s.ts_event) for s in flagged}
    plan = _Plan({}, [])
    files = _candidate_files(catalog_path, iid, {ts_init for ts_init, _ in keys}, plan.foreign)
    for key, spots in sorted(_locate(files, keys).items()):
        dropped = _resolve_key(iid, key, spots, plan.foreign)
        if dropped is None:
            continue
        plan.repaired.append(key)
        path, index = spots[0]
        plan.edits.setdefault(path, _FileEdit()).clear.add(index)
        for path, index in dropped:
            plan.edits.setdefault(path, _FileEdit()).drop.add(index)
    return plan


def _edited_table(path: str, edit: _FileEdit) -> pa.Table | None:
    """
    Return the file's table with `edit` applied, or None when that changes nothing (the kept copy
    was already cleared); refuses a change of the full schema.
    """
    table = pq.read_table(path)
    precisions = list(zip(*(table.column(c).to_pylist() for c in PRECISION_COLUMNS), strict=True))
    columns = {c: table.column(c).to_pylist() for c in _TRADE_COLUMNS}
    for index in edit.clear:
        for name, value in _no_trade_units(*precisions[index]).items():
            columns[name][index] = value
    new = table
    for name in _TRADE_COLUMNS:
        position = new.schema.get_field_index(name)
        column_field = new.schema.field(position)
        new = new.set_column(
            position, column_field, pa.array(columns[name], type=column_field.type)
        )
    if edit.drop:
        keep = [i not in edit.drop for i in range(new.num_rows)]
        new = new.filter(pa.array(keep))
    if not new.schema.equals(table.schema, check_metadata=True):
        raise _RefusedError(f"{path}: the repaired table's schema would change; not repaired")
    return None if new.equals(table) else new


def _stage_edits(
    writer: CatalogWriter, iid: str, edits: dict[str, _FileEdit]
) -> tuple[list[StagedRewrite], list[Path]] | None:
    """
    Stage every changed file (verified temps, nothing renamed) and list the files a drop empties;
    None (ledgered, every temp discarded) when one cannot be staged. An interrupt discards them
    too, then propagates. Staged in reverse path order, the order `commit_rewrites` renames them:
    each key's kept copy (its first path) is renamed after every file dropping a copy of it.
    """
    staged: list[StagedRewrite] = []
    emptied: list[Path] = []
    try:
        for leaf in sorted({Path(path).parent for path in edits}):
            writer.remove_stale_tmp(leaf)  # an interrupted run's temps; this run recomputes them
        for path, edit in sorted(edits.items(), reverse=True):
            table = _edited_table(path, edit)
            if table is not None and table.num_rows == 0:
                emptied.append(Path(path))
            elif table is not None:
                staged.append(writer.stage_rewrite(Path(path), table, RewriteMode.WHOLE_FILE))
    except Exception as e:
        writer.discard_rewrites(staged)
        error_ledger.record("repair.error", f"{iid}: staging failed ({e!r}); not repaired", exc=e)
        return None
    except BaseException:
        writer.discard_rewrites(staged)
        raise
    return staged, emptied


def _changed_days(keys: Iterable[_Key]) -> list[str]:
    """Return the UTC days (YYYY-MM-DD of `ts_event`) of the repaired rows."""
    seconds = {ts_event // _S_NS for _, ts_event in keys}
    return sorted({time.strftime("%Y-%m-%d", time.gmtime(second)) for second in seconds})


def _clear_verdicts(verified: VerifiedDays, iid: str, days: list[str]) -> bool:
    """Clear each day's stored verdict; False (ledgered) when one could not be cleared."""
    cleared: list[str] = []
    try:
        for day in days:
            verified.clear_verified(iid, day)
            cleared.append(day)
    except (sqlite3.Error, OSError) as e:
        # Name the days already cleared: they are unverified now and need a saga rerun.
        what = (
            f"{iid} {days}: the stored verdict could not be cleared ({e!r}); not repaired; "
            f"already cleared: {cleared}"
        )
        error_ledger.record("repair.error", what, exc=e)
        return False
    return True


def _cleared_before_commit(
    writer: CatalogWriter,
    staged: list[StagedRewrite],
    verified: VerifiedDays,
    iid: str,
    days: list[str],
) -> bool:
    """Clear the verdicts; on failure (or an interrupt) discard every temp: files untouched."""
    try:
        cleared = _clear_verdicts(verified, iid, days)
    except BaseException:
        writer.discard_rewrites(staged)
        raise
    if not cleared:
        writer.discard_rewrites(staged)
    return cleared


def _removed_detail(emptied: list[Path]) -> str:
    if not emptied:
        return ""
    return (
        f"; the emptied duplicate file(s) {[str(p) for p in emptied]} were already removed (each "
        f"held only a copy of a row its kept file still stores)"
    )


def _partial_commit_detail(iid: str, e: PartialCommitError, emptied: list[Path]) -> str:
    removed = _removed_detail(emptied)
    if not e.committed:
        what = "every file kept as it was" if not emptied else "every staged file kept as it was"
        return (
            f"{iid}: not repaired -- the first rename failed ({e}); {what}{removed}; rerun the "
            f"repair to complete it"
        )
    return (
        f"{iid}: PARTIALLY repaired -- committed {[str(p) for p in e.committed]}, kept the "
        f"old content of {[str(p) for p in e.kept]} ({e}){removed}; every file is whole, rerun "
        f"the repair to complete it"
    )


def _remove_emptied(
    writer: CatalogWriter, iid: str, staged: list[StagedRewrite], emptied: list[Path]
) -> bool:
    """
    Remove the files a drop emptied, before the first rename; on a failure discard every temp
    (ledgered, nothing renamed). A file removed before the failure held only a copy the kept file
    -- not yet renamed, so still holding the key's original -- stores too.
    """
    try:
        writer.remove_merged_sources(emptied)
    except (OpenDayWriteError, OSError) as e:
        writer.discard_rewrites(staged)
        error_ledger.record(
            "repair.error",
            f"{iid}: the emptied duplicate file(s) {[str(p) for p in emptied]} could not all be "
            f"removed ({e!r}); nothing renamed, each key's kept copy still stored; not repaired",
            exc=e,
        )
        return False
    return True


def _commit(
    writer: CatalogWriter, iid: str, staged: list[StagedRewrite], emptied: list[Path]
) -> bool:
    """Remove the files a drop emptied, then rename every temp; False (ledgered) on a failure."""
    if emptied and not _remove_emptied(writer, iid, staged, emptied):
        return False
    try:
        writer.commit_rewrites(staged)
    except PartialCommitError as e:
        error_ledger.record("repair.error", _partial_commit_detail(iid, e, emptied), exc=e)
        return False
    except OSError as e:
        # Usually the directory fsync after every rename; but a temp that cannot be discarded
        # after a failed rename surfaces here too, so which renames landed is not claimed.
        what = (
            f"{iid}: the commit failed ({e!r}) -- the directory fsync after the renames, or a "
            f"temp discard after a failed rename{_removed_detail(emptied)}; every file is whole, "
            f"durability unproven: rerun the repair"
        )
        error_ledger.record("repair.error", what, exc=e)
        return False
    return True


def _commit_and_rebuild(
    writer: CatalogWriter,
    catalog_path: str,
    iid: str,
    staging: tuple[list[StagedRewrite], list[Path]],
    repaired: list[_Key],
    candles_db_path: str | None,
) -> bool:
    """
    Commit, then (with `candles_db_path`) rebuild the repaired rows' candle-store days -- even
    after a failed commit: a file already renamed holds cleared rows, and a rerun only rebuilds
    the days of rows still flagged (the rebuild is idempotent from the raw 1 s). An exception in
    either is ledgered with the days to rebuild and returns False, so the run goes on to the next
    instrument; an interrupt is ledgered alike, then propagates.
    """
    try:
        committed = _commit(writer, iid, *staging)
        if candles_db_path is not None:
            events = [ts_event for _, ts_event in repaired]
            rebuild_instrument(candles_db_path, catalog_path, iid, min(events), max(events))
    except BaseException as e:
        error_ledger.record(
            "repair.error",
            f"{iid}: the commit or the candle rebuild failed or was interrupted ({e!r}); every "
            f"file is whole, but a renamed one's cleared rows are no longer flagged: rebuild the "
            f"candle-store days {_changed_days(repaired)} (`python -m candles.rebuild --day D`) "
            f"and rerun the repair",
            exc=e,
        )
        if not isinstance(e, Exception):
            raise
        return False
    return committed


def repair_instrument(
    writer: CatalogWriter,
    catalog_path: str,
    iid: str,
    flagged: list[DydxSecondSnapshot],
    verified: VerifiedDays,
    candles_db_path: str | None = None,
) -> bool:
    """
    Clear every flagged row's trade columns in place in its own file (stage -> clear the touched
    days' verdicts -> commit); with `candles_db_path`, rebuild the candle-store days the repair
    touched from the corrected raw 1 s. True only when every flagged row was repaired and no
    foreign-named file could hold a copy of one; a skipped or refused one is ledgered, and a
    failure before the first rename leaves every file untouched.
    """
    try:
        plan = _plan(catalog_path, iid, flagged)
    except (_RefusedError, OSError, pa.ArrowException, ValueError, TypeError) as e:
        error_ledger.record("repair.error", f"{iid}: {e}; not repaired", exc=e)
        return False
    staging = _stage_edits(writer, iid, plan.edits)
    if staging is None:
        return False
    staged, emptied = staging
    changed = bool(staged or emptied)
    days = _changed_days(plan.repaired)
    if changed and not _cleared_before_commit(writer, staged, verified, iid, days):
        return False
    committed = not changed or _commit_and_rebuild(
        writer, catalog_path, iid, staging, plan.repaired, candles_db_path
    )
    every_key = len(plan.repaired) == len({(s.ts_init, s.ts_event) for s in flagged})
    return committed and every_key and not plan.foreign
