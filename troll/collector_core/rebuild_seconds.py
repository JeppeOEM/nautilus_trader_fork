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
r"""
Rebuild a closed UTC day's second-snapshot trade columns from the raw trade archive (story 22.13).

Usage:
    python -m collector_core.rebuild_seconds --catalog /app/catalog --day 2026-09-20 \\
        [--instrument BTC-USD-PERP.DYDX ...] [--venue DYDX] [--apply] [--include-open-day]

Live, a snapshot row holds the trades that *arrived* since the previous sample, folded once. This
job re-derives every row of the day from `data/trade_tick/<iid>/` on exchange time: a trade whose
`ts_event` lies in `[S, S+1 s)` belongs to the row whose `ts_event // 1 s == S`, whatever second it
arrived in (late trades, boundary misattribution: audit D-31/D-44). The fold is the live one,
`collector_core.fold.fold_trades`. Only the eight trade columns (`buy_volume`, `sell_volume`,
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

Archive gaps: rows whose `ts_event` falls in a span of `collector_core.archive_gaps` (a trade
write that failed while the snapshots landed, a quarantined trade file, a pruned trade file) are
`not covered` too: the archive is known to miss trades their live values hold, so replacing them
would zero correct values. Archived trades mapping to such a row are counted as orphans.

Known limit: the floor-second mapping assumes one snapshot row per second
(`snapshot_interval_seconds = 1.0`, every venue's config today). At another cadence a second may
hold no row or two; the collector ledgers `collector.cadence` at start, and a day with two rows in
one second is refused. Upgrade path: map trades to the row whose sampling interval contains them.

Refusals are per instrument-day (error ledger, that instrument-day untouched, the run continues and
exits 2): two rows in one floor second (`rebuild.duplicate_second`: the mapping would be
ambiguous), day files whose full schema differs or lacks the trade columns (`rebuild.mixed_schema`,
D-24), an unreadable file or gap marker (`rebuild.error`). Run-level failures exit 1: today (or
later) without `--include-open-day` (only safe with that venue's collector stopped), a missing
catalog, the maintenance lock held elsewhere.

Files are rewritten temp-then-rename (`<file>.rebuild.tmp`): full schema (Arrow metadata
included) and row count are checked before `os.replace`; only files with a changed row are
written; `--apply` absent = report only. One instrument-day in memory at a time, trades one hour at
a time (MEM-01). Holds the catalog maintenance flock (`consolidate_catalog.maintenance_lock`).
Run `build_candles --day D` afterwards: the candle store is folded from these columns.
"""

import argparse
import glob
import logging
import os
import resource
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from ml_signals import error_ledger
from ml_signals.catalog_stats import _stamp_to_ns

from collector_core.archive_gaps import ARRIVAL_MARGIN_NS
from collector_core.archive_gaps import in_gap
from collector_core.archive_gaps import load_gaps
from collector_core.build_candles import _files_by_day
from collector_core.build_candles import _parse_date_ns
from collector_core.build_candles import all_instruments
from collector_core.build_candles import venue_instruments
from collector_core.consolidate_catalog import MAINTENANCE_LOCK_NAME
from collector_core.consolidate_catalog import maintenance_lock
from collector_core.fold import SecondTradeFields
from collector_core.fold import fold_trades
from nautilus_trader.model.data import TradeTick
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_S_NS = 1_000_000_000
_HOUR_NS = 3_600 * _S_NS
_DAY_NS = 86_400 * _S_NS
# Trades are queried on `ts_init` (what the catalog filters on) and kept on `ts_event`; the live age
# filter (`stale_trade_seconds`, 10 s) bounds `ts_init - ts_event`, so a 5 minute margin is ample.
_TS_INIT_MARGIN_NS = ARRIVAL_MARGIN_NS
_TMP_SUFFIX = ".rebuild.tmp"
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
_REFUSED_EXIT = 2  # finished, but some instrument-days were refused (ledgered, untouched)


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
    orphan_trades: int = 0
    orphan_seconds: int = 0
    duplicates: int = 0
    files_rewritten: int = 0

    def line(self, day: str) -> str:
        return (
            f"{self.iid} {day}: rebuilt {self.rebuilt}, changed {self.changed}, "
            f"without trades {self.without_trades}, not covered {self.not_covered}, "
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
    starts = {p: _stamp_to_ns(Path(p).stem.partition("_")[0]) for p in paths}
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


@dataclass(frozen=True)
class Coverage:
    """Which rows the archive can rebuild: from `start` on, outside every archive-gap span."""

    start: int | None
    gaps: list[tuple[int, int]]

    def covers(self, ts_ns: int) -> bool:
        return self.start is not None and ts_ns >= self.start and not in_gap(ts_ns, self.gaps)


def coverage(catalog_path: str, iid: str) -> Coverage:
    return Coverage(covered_from(catalog_path, iid), load_gaps(catalog_path, iid))


def _check_schema(files: list[str], label: str) -> None:
    first = pq.read_schema(files[0])
    missing = [c for c in _TRADE_COLUMNS if c not in first.names]
    if missing or any(not pq.read_schema(f).equals(first, check_metadata=True) for f in files[1:]):
        raise RefusedError(
            "rebuild.mixed_schema",
            f"{label}: day files differ in full schema or lack {missing or 'nothing'} (D-24)",
        )


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
    spans = [Path(p).stem.partition("_") for p in paths]
    return sorted(
        (path, _stamp_to_ns(first), _stamp_to_ns(last))
        for path, (first, _, last) in zip(paths, spans, strict=True)
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
            report.not_covered += 1
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


def _replace_file(path: str, table: pa.Table, cols: dict[str, list], label: str) -> None:
    """Write `table` with `cols` swapped in, temp-then-rename, schema and row count verified."""
    new = table
    for name in _TRADE_COLUMNS:
        index = new.schema.get_field_index(name)
        field = new.schema.field(index)
        new = new.set_column(index, field, pa.array(cols[name], type=field.type))
    if not new.schema.equals(table.schema, check_metadata=True):
        raise RefusedError("rebuild.schema_changed", f"{label}: {path}: schema would change")
    tmp = path + _TMP_SUFFIX
    pq.write_table(new, tmp, compression="zstd")
    same_schema = pq.read_schema(tmp).equals(pq.read_schema(path), check_metadata=True)
    if pq.read_metadata(tmp).num_rows != table.num_rows or not same_schema:
        os.unlink(tmp)
        raise RefusedError("rebuild.verify", f"{label}: {path}: rewritten file failed verification")
    os.replace(tmp, path)


def _remove_stale_tmp(files: list[str]) -> None:
    """Delete `*.rebuild.tmp` a crash left next to these files; the rerun recomputes them."""
    for directory in {os.path.dirname(f) for f in files}:
        for tmp in glob.glob(os.path.join(directory, "*" + _TMP_SUFFIX)):
            logger.warning("removing leftover %s from an interrupted rebuild", tmp)
            os.unlink(tmp)


def rebuild_day(catalog_path: str, iid: str, day_start_ns: int, apply: bool) -> DayReport:
    """Rebuild (or, without `apply`, report) one instrument-day; raises `RefusedError`."""
    label = f"{iid} {_day_text(day_start_ns)}"
    report = DayReport(iid)
    files = sorted(
        _files_by_day(catalog_path, iid, day_start_ns, day_start_ns + _DAY_NS - 1).get(
            day_start_ns // _DAY_NS, []
        )
    )
    covered = coverage(catalog_path, iid)
    if not files:
        # No snapshot rows at all: every archived trade of the day is an orphan (reported).
        if covered.start is not None:
            fold_day(catalog_path, iid, day_start_ns, {}, covered, report)
        return report
    _check_schema(files, label)
    rows = _row_seconds(files, day_start_ns, day_start_ns + _DAY_NS, label)
    rebuilt = (
        {}  # no archive at all: every row is `not covered`, there is nothing to fold
        if covered.start is None
        else fold_day(catalog_path, iid, day_start_ns, rows, covered, report)
    )
    if apply:
        _remove_stale_tmp(files)
    for path in files:
        table = pq.read_table(path)
        cols, changed = _updated_columns(table, day_start_ns, covered, rebuilt, report)
        report.changed += changed
        if changed and apply:
            _replace_file(path, table, cols, label)
            report.files_rewritten += 1
    return report


def _day_text(day_start_ns: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day_start_ns // _S_NS))


def run(catalog_path: str, iids: list[str], day_start_ns: int, apply: bool) -> int:
    """Rebuild every instrument's day; returns the number of refused instrument-days."""
    day, refused = _day_text(day_start_ns), 0
    totals = DayReport("all")
    for iid in iids:
        try:
            report = rebuild_day(catalog_path, iid, day_start_ns, apply)
        except RefusedError as e:
            error_ledger.record(e.site, str(e))
            refused += 1
            continue
        except (OSError, pa.ArrowException, ValueError, RuntimeError) as e:
            # e.g. an unreadable file, or (RuntimeError from the Rust query session) trade files
            # whose `price_precision`/`size_precision` metadata differ. Known limit: such a leaf
            # cannot be queried as one table, so every day touching it is refused (loudly) until
            # an operator re-stamps the odd files exactly (`Decimal.scaleb` + `from_raw`); no
            # venue has changed an instrument's precision mid-archive yet. Upgrade path: query
            # the leaf file by file and fold across them.
            error_ledger.record("rebuild.error", f"{iid} {day}: {e!r}; refused", exc=e)
            refused += 1
            continue
        logger.info("%s", report.line(day))
        if report.orphan_trades:
            logger.warning(
                "%s %s: %d archived trades have no sampled second to go to (collector not "
                "sampling then); they stay in the archive, the snapshot cannot hold them",
                iid,
                day,
                report.orphan_trades,
            )
        for name in ("rebuilt", "changed", "orphan_trades", "duplicates", "files_rewritten"):
            setattr(totals, name, getattr(totals, name) + getattr(report, name))
    logger.info(
        "rebuild %s: %d instrument(s), %d refused; changed %d seconds, %d files rewritten%s, "
        "orphan trades %d, duplicates %d",
        day,
        len(iids) - refused,
        refused,
        totals.changed,
        totals.files_rewritten,
        "" if apply else " (report only, nothing changed)",
        totals.orphan_trades,
        totals.duplicates,
    )
    return refused


def _instruments(catalog_path: str, explicit: list[str] | None, venue: str | None) -> list[str]:
    """`--instrument`, else every id with snapshot *or* trade files (trade-only ids: orphans)."""
    if explicit:
        return venue_instruments(explicit, venue)
    trade_root = Path(catalog_path) / "data" / "trade_tick"
    traded = [p.name for p in trade_root.iterdir() if p.is_dir()] if trade_root.exists() else []
    return venue_instruments(sorted(set(all_instruments(catalog_path)) | set(traded)), venue)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns 0, 2 when some instrument-days were refused, 1 on a run failure."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--day", required=True, help="YYYY-MM-DD (UTC)")
    parser.add_argument("--instrument", action="append", help="repeatable; default: all")
    parser.add_argument("--venue", help="only ids of this venue, e.g. DYDX")
    parser.add_argument("--apply", action="store_true", help="rewrite files (default: report)")
    parser.add_argument(
        "--include-open-day", action="store_true", help="allow today; collector must be stopped"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    started = time.monotonic()
    day_start_ns = _parse_date_ns(args.day)
    if day_start_ns >= time.time_ns() // _DAY_NS * _DAY_NS and not args.include_open_day:
        error_ledger.record(
            "rebuild.open_day", f"{args.day} is not a closed UTC day; --include-open-day to force"
        )
        return 1
    catalog = Path(args.catalog)
    if not catalog.is_dir():
        logger.error("rebuild: catalog %s does not exist (wrong mount?)", catalog)
        return 1
    iids = _instruments(args.catalog, args.instrument, args.venue)
    with maintenance_lock(catalog) as locked:
        if not locked:
            logger.error("rebuild: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return 1
        refused = run(args.catalog, iids, day_start_ns, args.apply)
    # ru_maxrss is KiB on Linux (the collector image).
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    logger.info("rebuild: %.1f s wall; peak RSS %.0f MB", time.monotonic() - started, peak_mb)
    return _REFUSED_EXIT if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
