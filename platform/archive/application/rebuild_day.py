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
Rebuild a closed UTC day's second-snapshot trade columns from the raw trade archive (story 22.13).

Live, a snapshot row holds the trades that *arrived* since the previous sample, folded once. This
re-derives every row of the day from `data/trade_tick/<iid>/` on exchange time: a trade whose
`ts_event` lies in `[S, S+1 s)` belongs to the row whose `ts_event // 1 s == S`, whatever second it
arrived in (late trades, boundary misattribution: audit D-31/D-44). The fold is the live one,
`kernel.fold.fold_trades`. Only the eight trade columns (`buy_volume`, `sell_volume`,
`buy_count`, `sell_count`, `open/high/low/close_price`) of rows inside the day change; book
columns, `ts_event` and `ts_init` never do.

Coverage: the live loop and the archive are written by the same flush, so every live-folded trade
is archived -- unless the archive did not exist yet. Rows before the floor second of the
instrument's earliest archived trade (`covered_from`) keep their live values and are counted
`not covered`. (The literal rule
"a second with no archived trade keeps its live values" would double-count every late trade: its
arrival second has no exchange-time trade.) Trades whose second has no row, or only a not-covered
row, are counted as `orphan trades` (and their distinct seconds) -- the collector was not sampling
then (stale/crossed book, restart) -- never silently dropped. An archived `trade_id` seen twice (a
restart replay) is folded once and counted as a duplicate. An instrument with trade files but no
snapshot files that day has every archived trade of the day counted as an orphan.

Archive gaps: rows whose `ts_event` falls in an `ArchiveGap` span (a trade write that failed while
the snapshots landed, a quarantined trade file, a pruned trade file) keep their live values too:
the archive is known to miss trades their live values hold, so replacing them would zero correct
values. They are counted `in gap`, apart from `not covered`. Archived trades mapping to such a row
are counted as orphans.

Known limit: the floor-second mapping assumes one snapshot row per second
(`snapshot_interval_seconds = 1.0`, every venue's config today). At another cadence a second may
hold no row or two; the collector ledgers `collector.cadence` at start, and a day with two rows in
one second is refused. Upgrade path: map trades to the row whose sampling interval contains them.

Files: every snapshot file whose `ts_init` span overlaps `[D start, D end + _TS_INIT_MARGIN_NS]`,
not only the files named inside D -- venue-time capture closes second S at `S + 1 +
hold_back_seconds`, so D's last rows can land in a file that starts after midnight (Hyperliquid's
23:59:59 row always does). Rows are chosen by `ts_event` in D. Every write is a
`RewriteMode.KEEP_OPEN_DAY_ROWS` rewrite: a file that also holds rows of the current UTC day (the
midnight-crossing file B, the after-midnight file C at the nightly after it) is rewritten with those
rows identical, which `CatalogFiles` verifies against the original before any rename. So the normal
nightly never refuses `rebuild.open_day`; only a rebuild that would change a row of the open day
(i.e. of today itself) does.

Refusals are per instrument-day (error ledger, that instrument-day untouched, the run continues):
two rows in one floor second (`rebuild.duplicate_second`: the mapping would be ambiguous), day
files whose full schema differs or lacks the trade columns (`rebuild.mixed_schema`, D-24), a change
to a row of the current UTC day (`rebuild.open_day`), a temp that failed its read-back
(`rebuild.verify`), an unreadable file or gap marker (`rebuild.error`). All of an instrument-day's
changed files are staged and verified before the first one is renamed, so a refusal leaves every
file as it was. Only files with a changed row are written; `apply` False = report only (no writer
needed). One instrument-day in memory at a time, trades one hour at a time (MEM-01).

Known limit: the renames of one instrument-day's files are individually atomic, not as a set. A
rename failing part-way (an I/O error between two of its renames) leaves the day partially
rebuilt -- ledgered `rebuild.error` naming how many files were replaced, and that instrument
refused, so it is never reconciled. Every file is still whole, and the rebuild is idempotent: a
rerun re-derives the same values and completes the day. Upgrade path: a per-leaf intent log
replayed at the next run's start.
"""

import glob
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from candles.application.rebuild import all_instruments
from candles.application.rebuild import venue_instruments
from kernel.catalog_files import snapshot_files
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import CatalogFileSpan
from kernel.fold import SecondTradeFields
from kernel.fold import fold_trades
from observability import error_ledger

from archive.application.ports import CatalogWriter
from archive.application.ports import GapMarkers
from archive.application.ports import OpenDayWriteError
from archive.application.ports import PartialCommitError
from archive.application.ports import RewriteMode
from archive.application.ports import RewriteVerifyError
from archive.application.ports import StagedRewrite
from archive.domain.gaps import Coverage
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_S_NS = 1_000_000_000
_HOUR_NS = 3_600 * _S_NS
_DAY_NS = 86_400 * _S_NS
# Trades are queried on `ts_init` (what the catalog filters on) and kept on `ts_event`; every writer
# keeps `ts_init - ts_event` within the one skew bound, so the window is exactly that bound.
_TS_INIT_MARGIN_NS = MAX_TS_INIT_SKEW_NS
_TRADE_COLUMNS = (
    "buy_volume",
    "sell_volume",
    "buy_count",
    "sell_count",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
)
_NO_TRADES = SecondTradeFields().snapshot_values()._asdict()


class RefusedError(Exception):
    """An instrument-day that must not be rebuilt; `site` is its error-ledger site."""

    def __init__(self, site: str, detail: str) -> None:
        super().__init__(detail)
        self.site = site


@dataclass
class DayReport:
    """What the rebuild did (or would do) to one instrument-day."""

    iid: str
    rebuilt: int = 0
    changed: int = 0
    without_trades: int = 0
    not_covered: int = 0
    in_gap: int = 0
    orphan_trades: int = 0
    orphan_seconds: int = 0
    duplicates: int = 0
    files_rewritten: int = 0

    @property
    def rows(self) -> int:
        """The instrument's snapshot rows in the day, whatever happened to them."""
        return self.rebuilt + self.not_covered + self.in_gap

    def line(self, day: str) -> str:
        return (
            f"{self.iid} {day}: rebuilt {self.rebuilt}, changed {self.changed}, "
            f"without trades {self.without_trades}, not covered {self.not_covered}, "
            f"in gap {self.in_gap}, "
            f"orphan trades {self.orphan_trades} / seconds {self.orphan_seconds}, "
            f"duplicates {self.duplicates}, files rewritten {self.files_rewritten}"
        )


def covered_from(catalog_path: str, iid: str) -> int | None:
    """
    Start (ns) of the floor second of the instrument's earliest archived trade (`ts_event`);
    None without any archive.

    Not the earliest file's name stamp: that is a `ts_init`, up to `stale_trade_seconds` after the
    first trade's `ts_event`, and would leave that trade's own row "not covered" -- found live on
    2026-09-21, a dYdX trade 1.3 s older than its arrival dropped out of the rebuilt minute. Only
    files starting within `_TS_INIT_MARGIN_NS` of the earliest one can hold that trade.

    Known limit: a row written by the *previous* collector process (before the archive existed)
    inside `[first ts_event, first ts_init)` would be treated as covered, and its unarchived live
    trades replaced; that needs a restart shorter than the trade's arrival lag (under
    `stale_trade_seconds`, 10 s). Upgrade path: record the archive's start in the catalog.
    """
    paths = glob.glob(os.path.join(catalog_path, "data", "trade_tick", iid, "*.parquet"))
    starts = {p: CatalogFileSpan.from_path(p).start_ns for p in paths}
    if not starts:
        return None
    earliest = min(starts.values())
    firsts = [
        pc.min(pq.read_table(p, columns=["ts_event"]).column("ts_event")).as_py()
        for p, start in starts.items()
        if start <= earliest + _TS_INIT_MARGIN_NS
    ]
    # A zero-row file has no minimum (None): it holds no trade, so it proves no coverage.
    events = [t for t in firsts if t is not None]
    return min(events) // _S_NS * _S_NS if events else None


def coverage(catalog_path: str, iid: str, gaps: GapMarkers) -> Coverage:
    return Coverage(covered_from(catalog_path, iid), tuple(gaps.load(iid)))


def _check_schema(files: list[str], label: str) -> None:
    first = pq.read_schema(files[0])
    missing = [c for c in _TRADE_COLUMNS if c not in first.names]
    if missing or any(not pq.read_schema(f).equals(first, check_metadata=True) for f in files[1:]):
        raise RefusedError(
            "rebuild.mixed_schema",
            f"{label}: day files differ in full schema or lack {missing or 'nothing'} (D-24)",
        )


def _prepare_writes(files: list[str], writer: CatalogWriter) -> None:
    """
    Clear the temp files an interrupted run left next to these files (the rerun recomputes them).
    No open-day check here: a file reaching today is rewritten too, its open-day rows kept
    (`RewriteMode.KEEP_OPEN_DAY_ROWS`, verified by the writer).
    """
    for leaf in sorted({Path(f).parent for f in files}):
        writer.remove_stale_tmp(leaf)


def _row_seconds(files: list[str], lo: int, hi: int, label: str) -> dict[int, int]:
    """Floor second -> that row's `ts_event`, for the day's rows; refuses two rows in one second."""
    seconds: dict[int, int] = {}
    for path in files:
        for ts in pq.read_table(path, columns=["ts_event"]).column("ts_event").to_pylist():
            if not lo <= ts < hi:
                continue
            if ts // _S_NS in seconds:
                raise RefusedError(
                    "rebuild.duplicate_second",
                    f"{label}: two snapshot rows in second {ts // _S_NS} "
                    f"({seconds[ts // _S_NS]} and {ts}); the trade mapping would be ambiguous",
                )
            seconds[ts // _S_NS] = ts
    return seconds


def trade_files(catalog_path: str, iid: str) -> list[tuple[str, int, int]]:
    """Return (path, first ts_init, last ts_init) of each of the instrument's trade files."""
    paths = glob.glob(os.path.join(catalog_path, "data", "trade_tick", iid, "*.parquet"))
    spans = [CatalogFileSpan.from_path(p) for p in paths]
    return sorted(
        (path, span.start_ns, span.end_ns) for path, span in zip(paths, spans, strict=True)
    )


def _hour_trades(
    catalog: ParquetDataCatalog, iid: str, hour: int, files: list[tuple[str, int, int]]
) -> tuple[list[TradeTick], int]:
    """
    Return the hour's archived trades by `ts_event`, in arrival (`ts_init`) order, one per
    `trade_id`; plus the number of duplicates dropped. Deduplicating per hour is exact: a
    replayed trade is the same trade, so both copies carry the same `ts_event` and fall in the
    same hour.

    The overlapping files are passed explicitly: without `files=` every `query` lists the whole
    `trade_tick` tree (all instruments) before filtering, 24 times per instrument-day -- and the
    rebuild runs before the nightly consolidation, on up to ~1,440 files per instrument-day.
    """
    lo, hi = hour - _TS_INIT_MARGIN_NS, hour + _HOUR_NS + _TS_INIT_MARGIN_NS
    overlapping = [path for path, first, last in files if first <= hi and last >= lo]
    if not overlapping:
        return [], 0
    queried = catalog.query(TradeTick, identifiers=[iid], start=lo, end=hi, files=overlapping)
    in_hour = sorted(
        (t for t in queried if hour <= t.ts_event < hour + _HOUR_NS), key=lambda t: t.ts_init
    )
    seen: set[str] = set()
    unique: list[TradeTick] = []
    for trade in in_hour:
        trade_id = trade.trade_id.value
        if trade_id not in seen:
            seen.add(trade_id)
            unique.append(trade)
    return unique, len(in_hour) - len(unique)


def fold_day(
    catalog_path: str,
    iid: str,
    lo: int,
    rows: dict[int, int],
    covered: Coverage,
    report: DayReport,
) -> dict[int, dict[str, Any]]:
    """Floor second -> rebuilt trade columns, for every covered row that has archived trades."""
    catalog, files = ParquetDataCatalog(catalog_path), trade_files(catalog_path, iid)
    rebuilt: dict[int, dict[str, Any]] = {}
    for hour in range(lo, lo + _DAY_NS, _HOUR_NS):
        trades, duplicates = _hour_trades(catalog, iid, hour, files)
        report.duplicates += duplicates
        buckets: defaultdict[int, list[TradeTick]] = defaultdict(list)
        for trade in trades:
            buckets[trade.ts_event // _S_NS].append(trade)
        for second, bucket in buckets.items():
            row_ts = rows.get(second)
            if row_ts is None or not covered.covers(row_ts):
                report.orphan_trades += len(bucket)
                report.orphan_seconds += 1
                continue
            rebuilt[second] = fold_trades(bucket).snapshot_values()._asdict()
    return rebuilt


def _count_kept(ts: int, covered: Coverage, report: DayReport) -> None:
    if covered.in_gap(ts):
        report.in_gap += 1
    else:
        report.not_covered += 1


def _updated_columns(
    table: pa.Table,
    lo: int,
    covered: Coverage,
    rebuilt: dict[int, dict[str, Any]],
    report: DayReport,
) -> tuple[dict[str, list], int]:
    """Return the file's trade columns with the day's covered rows replaced, and how many changed."""
    cols = {c: table.column(c).to_pylist() for c in _TRADE_COLUMNS}
    changed = 0
    for i, ts in enumerate(table.column("ts_event").to_pylist()):
        if not lo <= ts < lo + _DAY_NS:
            continue
        if not covered.covers(ts):
            _count_kept(ts, covered, report)
            continue
        report.rebuilt += 1
        values = rebuilt.get(ts // _S_NS)
        if values is None:
            report.without_trades += 1
            values = _NO_TRADES
        if any(cols[c][i] != values[c] for c in _TRADE_COLUMNS):
            changed += 1
            for c in _TRADE_COLUMNS:
                cols[c][i] = values[c]
    return cols, changed


def _replaced(path: str, table: pa.Table, cols: dict[str, list], label: str) -> pa.Table:
    """Return `table` with `cols` swapped in; refuses a change of the full schema."""
    new = table
    for name in _TRADE_COLUMNS:
        index = new.schema.get_field_index(name)
        field = new.schema.field(index)
        new = new.set_column(index, field, pa.array(cols[name], type=field.type))
    if not new.schema.equals(table.schema, check_metadata=True):
        raise RefusedError("rebuild.schema_changed", f"{label}: {path}: schema would change")
    return new


def day_files(catalog_path: str, iid: str, day_start_ns: int) -> list[str]:
    """
    Every snapshot file that can hold a row of the day: its `ts_init` span overlaps
    `[D start, D end + _TS_INIT_MARGIN_NS]` (a row of D is sampled up to that bound after its
    `ts_event`, so it can sit in a file that starts after midnight). An unparsable name raises
    `ValueError` (the instrument-day is refused, `rebuild.error`).
    """
    hi = day_start_ns + _DAY_NS - 1 + _TS_INIT_MARGIN_NS
    return sorted(
        path
        for path in snapshot_files(catalog_path, iid)
        if CatalogFileSpan.from_path(path).overlaps(day_start_ns, hi)
    )


def rebuild_day(
    catalog_path: str,
    iid: str,
    day_start_ns: int,
    writer: CatalogWriter | None,
    gaps: GapMarkers,
    apply: bool,
) -> DayReport:
    """
    Rebuild (or, without `apply`, report) one instrument-day; raises `RefusedError`, having
    changed no file. `writer` may be None only when not applying.
    """
    if apply and writer is None:
        raise ValueError("rebuild_day(apply=True) needs a CatalogWriter")
    label = f"{iid} {day_text(day_start_ns)}"
    report = DayReport(iid)
    files = day_files(catalog_path, iid, day_start_ns)
    covered = coverage(catalog_path, iid, gaps)
    if not files:
        # No snapshot rows at all: every archived trade of the day is an orphan (reported).
        if covered.start is not None:
            fold_day(catalog_path, iid, day_start_ns, {}, covered, report)
        return report
    _check_schema(files, label)
    active = writer if apply else None
    if active is not None:
        _prepare_writes(files, active)
    rows = _row_seconds(files, day_start_ns, day_start_ns + _DAY_NS, label)
    rebuilt = (
        {}  # no archive at all: every row is `not covered`, there is nothing to fold
        if covered.start is None
        else fold_day(catalog_path, iid, day_start_ns, rows, covered, report)
    )
    _rebuild_files(files, day_start_ns, covered, rebuilt, report, active)
    return report


def _rebuild_files(
    files: list[str],
    lo: int,
    covered: Coverage,
    rebuilt: dict[int, dict[str, Any]],
    report: DayReport,
    writer: CatalogWriter | None,
) -> None:
    """
    Count every file's changes into `report`; with a `writer`, stage each changed file (written
    and verified, nothing renamed), then commit them all -- or, on any refusal, none.
    """
    label = f"{report.iid} {day_text(lo)}"
    staged: list[StagedRewrite] = []
    try:
        for path in files:
            table = _rebuild_file(path, lo, covered, rebuilt, report, label)
            if table is not None and writer is not None:
                staged.append(_stage(writer, path, table, label))
    except BaseException:
        if writer is not None:
            writer.discard_rewrites(staged)
        raise
    if writer is not None and staged:
        _commit(writer, staged, label)
        report.files_rewritten += len(staged)


def _rebuild_file(
    path: str,
    lo: int,
    covered: Coverage,
    rebuilt: dict[int, dict[str, Any]],
    report: DayReport,
    label: str,
) -> pa.Table | None:
    """Count one file's changes into `report`; return its rebuilt table when a row changed."""
    table = pq.read_table(path)
    cols, changed = _updated_columns(table, lo, covered, rebuilt, report)
    report.changed += changed
    return _replaced(path, table, cols, label) if changed else None


def _stage(writer: CatalogWriter, path: str, table: pa.Table, label: str) -> StagedRewrite:
    try:
        return writer.stage_rewrite(Path(path), table, RewriteMode.KEEP_OPEN_DAY_ROWS)
    except RewriteVerifyError as e:
        raise RefusedError("rebuild.verify", f"{label}: {e}") from e
    except OpenDayWriteError as e:
        raise RefusedError("rebuild.open_day", f"{label}: {path}: {e}") from e


def _commit(writer: CatalogWriter, staged: list[StagedRewrite], label: str) -> None:
    try:
        writer.commit_rewrites(staged)
    except PartialCommitError as e:
        raise RefusedError(
            "rebuild.error",
            f"{label}: PARTIALLY rebuilt -- {len(e.committed)} of {len(staged)} file(s) "
            f"replaced before a rename failed ({e}); rerun the rebuild to complete the day",
        ) from e


def day_text(day_start_ns: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day_start_ns // _S_NS))


def _log_report(report: DayReport, day: str) -> None:
    logger.info("%s", report.line(day))
    if report.orphan_trades:
        logger.warning(
            "%s %s: %d archived trades have no sampled second to go to (collector not "
            "sampling then); they stay in the archive, the snapshot cannot hold them",
            report.iid,
            day,
            report.orphan_trades,
        )


def _rebuild_or_refuse(
    catalog_path: str,
    iid: str,
    day_start_ns: int,
    writer: CatalogWriter | None,
    gaps: GapMarkers,
    apply: bool,
) -> DayReport | None:
    """
    One instrument-day; a refusal is ledgered at its site and returns None (day untouched, or --
    said so in the ledger line -- partially rebuilt).
    """
    day = day_text(day_start_ns)
    try:
        return rebuild_day(catalog_path, iid, day_start_ns, writer, gaps, apply)
    except RefusedError as e:
        error_ledger.record(e.site, str(e))
    except (OSError, pa.ArrowException, ValueError, RuntimeError) as e:
        # e.g. an unreadable file, or (RuntimeError from the Rust query session) trade files
        # whose `price_precision`/`size_precision` metadata differ. Known limit: such a leaf
        # cannot be queried as one table, so every day touching it is refused (loudly) until
        # an operator re-stamps the odd files exactly (`Decimal.scaleb` + `from_raw`); no
        # venue has changed an instrument's precision mid-archive yet. Upgrade path: query
        # the leaf file by file and fold across them.
        error_ledger.record("rebuild.error", f"{iid} {day}: {e!r}; refused", exc=e)
    return None


@dataclass(frozen=True)
class RunResult:
    """
    Which instruments a rebuild run rebuilt and which it refused (ledgered). An instrument-day
    with no snapshot row is in neither list: there was nothing to rebuild, so nothing is proven.
    """

    rebuilt: list[str]
    refused: list[str]


def run(
    catalog_path: str,
    iids: list[str],
    day_start_ns: int,
    writer: CatalogWriter | None,
    gaps: GapMarkers,
    apply: bool,
) -> RunResult:
    """Rebuild every instrument's day; returns the rebuilt and the refused (ledgered) ids."""
    day, refused, done = day_text(day_start_ns), [], []
    totals = DayReport("all")
    for iid in iids:
        report = _rebuild_or_refuse(catalog_path, iid, day_start_ns, writer, gaps, apply)
        if report is None:
            refused.append(iid)
            continue
        if report.rows:
            done.append(iid)
        _log_report(report, day)
        for name in ("rebuilt", "changed", "in_gap", "orphan_trades", "duplicates"):
            setattr(totals, name, getattr(totals, name) + getattr(report, name))
        totals.files_rewritten += report.files_rewritten
    logger.info(
        "rebuild %s: %d instrument(s), %d refused; changed %d seconds, %d files rewritten%s, "
        "in gap %d, orphan trades %d, duplicates %d",
        day,
        len(iids) - len(refused),
        len(refused),
        totals.changed,
        totals.files_rewritten,
        "" if apply else " (report only, nothing changed)",
        totals.in_gap,
        totals.orphan_trades,
        totals.duplicates,
    )
    return RunResult(sorted(done), sorted(refused))


def instruments(catalog_path: str, explicit: list[str] | None, venue: str | None) -> list[str]:
    """`--instrument`, else every id with snapshot *or* trade files (trade-only ids: orphans)."""
    if explicit:
        return venue_instruments(explicit, venue)
    trade_root = Path(catalog_path) / "data" / "trade_tick"
    traded = [p.name for p in trade_root.iterdir() if p.is_dir()] if trade_root.exists() else []
    return venue_instruments(sorted(set(all_instruments(catalog_path)) | set(traded)), venue)
