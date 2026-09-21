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
Prune old Parquet files from the catalog: plain age retention for chosen data types, and the
verification-gated retention of the raw trade archive (story 22.13).

Parquet filenames in the catalog encode their time range: `<start_ts>_<end_ts>.parquet`.

Usage (report only unless --apply; --dry-run is the explicit spelling of the default):
    python -m collector_core.prune_catalog --catalog /app/catalog \\
        [--types order_book_deltas --days 14] \\
        [--candles-dir /app/candles_dir --trade-retention-days 7] [--venue BYBIT] [--apply]

`--types T --days N` deletes files of those data types whose end timestamp is older than N days.
`trade_tick` is refused there: raw trades exist to correct and prove the aggregates, so they are
released only by the trade policy.

Trade policy (with `--candles-dir`): a `data/trade_tick/<iid>/` file is deleted only when every
UTC day its name spans is older than `--trade-retention-days` (default 7) **and** that
instrument-day's `verified_days` row in `candles_<venue>.db` is `pass` (`compare_klines`).
Otherwise it is kept, and each old kept (instrument, day) is reported with its reason:
`unverified` (no row) or `failed` (a kline mismatch). A day younger than the window is kept
silently. A file starting within the arrival margin (5 min) after midnight also needs the
previous day proven: its trades' `ts_event` can belong to it. Every deleted trade file is first
recorded as a `pruned` archive gap (`collector_core.archive_gaps`) so a later rebuild keeps those
rows' live values. A file whose name does not parse is skipped and reported, never deleted.
`--venue V` limits both policies to instruments whose id ends in `.V`. Takes the catalog
maintenance flock (`consolidate_catalog.maintenance_lock`).
"""

import argparse
import datetime
import logging
import re
import time
from pathlib import Path

from ml_signals import candle_store
from ml_signals.catalog_stats import _stamp_to_ns

from collector_core.archive_gaps import ARRIVAL_MARGIN_NS
from collector_core.archive_gaps import record_gap
from collector_core.consolidate_catalog import MAINTENANCE_LOCK_NAME
from collector_core.consolidate_catalog import maintenance_lock


logger = logging.getLogger(__name__)

TRADE_TICK = "trade_tick"
_DAY_NS = 86_400 * 1_000_000_000

# Timestamp is nanoseconds since epoch encoded in the filename.
_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d\-]+Z)_(\d{4}-\d{2}-\d{2}T[\d\-]+Z)\.parquet$")


def _filename_end_ns(path: Path) -> str | None:
    """
    Return the end stamp of a catalog Parquet filename, as the filename spells it.

    Catalog filenames sort lexicographically = chronologically, so comparing the string directly
    to a cutoff formatted the same way (`_cutoff_str`) is sufficient.
    """
    m = _TS_RE.search(path.name)
    return m.group(2) if m else None


def _cutoff_str(cutoff_ns: int) -> str:
    """Format the cutoff like a catalog filename, for the lexicographic comparison above."""
    moment = datetime.datetime.fromtimestamp(cutoff_ns / 1e9, tz=datetime.UTC)
    return moment.strftime("%Y-%m-%dT%H-%M-%S")


def prune(
    catalog_path: str,
    data_types: list[str],
    retain_days: int,
    dry_run: bool,
    venue: str | None = None,
) -> None:
    cutoff_str = _cutoff_str(time.time_ns() - retain_days * _DAY_NS)
    root = Path(catalog_path) / "data"
    total_freed = 0

    for data_type in data_types:
        type_dir = root / data_type
        if not type_dir.exists():
            logger.info("  %s: directory not found, skipping", data_type)
            continue

        for parquet_file in sorted(type_dir.rglob("*.parquet")):
            if venue is not None and not parquet_file.parent.name.endswith(f".{venue}"):
                continue
            end_str = _filename_end_ns(parquet_file)
            if end_str is None:
                continue
            if end_str < cutoff_str:
                size = parquet_file.stat().st_size
                total_freed += size
                if dry_run:
                    logger.info(
                        "  [report only] would delete %s (%d KB)", parquet_file, size // 1024
                    )
                else:
                    parquet_file.unlink()
                    logger.info("  deleted %s (%d KB)", parquet_file, size // 1024)

    action = "would free" if dry_run else "freed"
    logger.info("%s %.1f MB", action, total_freed / 1024 / 1024)


def prune_instrument(
    catalog_path: str,
    instrument_id: str,
    retain_hours: float,
    data_types: list[str] | None = None,
) -> int:
    """
    Delete all Parquet files for `instrument_id` whose end timestamp is older than
    `retain_hours` hours ago.

    Scoped to every data type directory in the catalog by default -- except `trade_tick`, whose
    retention is the verification-gated trade policy of this module's CLI (a raw trade is
    released only after its day reconciled `pass`); pass `data_types` to prune only specific ones
    (e.g. `["order_book_deltas"]` for per-coin raw-delta retention, leaving that instrument's
    other data types untouched).

    Returns the total bytes freed.
    """
    cutoff_str = _cutoff_str(time.time_ns() - int(retain_hours * 3_600 * 1_000_000_000))
    freed = 0
    data_root = Path(catalog_path) / "data"
    if not data_root.exists():
        return 0

    if data_types is not None:
        type_dirs = []
        for name in data_types:
            type_dir = data_root / name
            if not type_dir.is_dir():
                logger.warning(
                    f"Prune data_type {name!r} does not exist under {data_root} — skipping"
                )
                continue
            type_dirs.append(type_dir)
    else:
        type_dirs = [d for d in data_root.iterdir() if d.name != TRADE_TICK]

    for type_dir in type_dirs:
        iid_dir = type_dir / instrument_id
        if not iid_dir.is_dir():
            continue
        for f in sorted(iid_dir.glob("*.parquet")):
            end_str = _filename_end_ns(f)
            if end_str and end_str < cutoff_str:
                freed += f.stat().st_size
                f.unlink()
    return freed


# -- trade policy ---------------------------------------------------------------------------------


def _day_text(day: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(day * 86_400))


def _file_span(path: Path) -> tuple[int, int]:
    """Return the file name's `ts_init` span (ns); `ValueError` if the catalog did not write it."""
    first, _, last = path.stem.partition("_")
    return _stamp_to_ns(first), _stamp_to_ns(last)


def _file_days(path: Path) -> range:
    """
    UTC day indices whose trades the file can hold. The name spans `ts_init`; a trade's `ts_event`
    precedes it by up to the arrival margin, so a file starting within that margin after midnight
    can hold trades of the previous day too, and that day must also be proven.
    """
    start, end = _file_span(path)
    first_day = start // _DAY_NS
    if start - first_day * _DAY_NS < ARRIVAL_MARGIN_NS:
        first_day -= 1
    return range(first_day, end // _DAY_NS + 1)


def _parsed_files(leaf: Path) -> dict[Path, range]:
    """Each trade file's days; a file whose name does not parse is skipped and reported."""
    files: dict[Path, range] = {}
    for f in sorted(leaf.glob("*.parquet")):
        try:
            files[f] = _file_days(f)
        except ValueError:
            logger.warning("  skipped %s: not a catalog file name, never pruned", f)
    return files


def _leaf_statuses(candles_dir: Path, iid: str, days: set[int]) -> dict[int, str | None]:
    """Each day's `verified_days` status in that venue's candle store (None: unverified)."""
    venue = iid.rpartition(".")[2].lower()
    with candle_store.connect_ro(str(candles_dir / f"candles_{venue}.db")) as db:
        if db is None:
            return dict.fromkeys(days)
        return {d: candle_store.verified_status(db, iid, _day_text(d)) for d in days}


def plan_trade_prune(
    catalog_path: str,
    candles_dir: str,
    retention_days: int,
    now_ns: int,
    venue: str | None = None,
) -> tuple[list[Path], list[tuple[str, str, str]]]:
    """
    (trade files to delete, sorted (instrument, day, reason) kept although old enough). A file
    goes only when every day it spans is older than `retention_days` and verified `pass`.
    """
    old_before = now_ns // _DAY_NS - retention_days  # day indices below this are old enough
    delete: list[Path] = []
    kept: set[tuple[str, str, str]] = set()
    for leaf in sorted((Path(catalog_path) / "data" / TRADE_TICK).glob("*")):
        if not leaf.is_dir() or (venue is not None and not leaf.name.endswith(f".{venue}")):
            continue
        files = _parsed_files(leaf)
        old = {f: days for f, days in files.items() if days[-1] < old_before}
        statuses = _leaf_statuses(
            Path(candles_dir), leaf.name, {d for ds in old.values() for d in ds}
        )
        for f, days in old.items():
            unproven = [d for d in days if statuses[d] != "pass"]
            if not unproven:
                delete.append(f)
            for d in unproven:
                reason = "failed" if statuses[d] == "fail" else "unverified"
                kept.add((leaf.name, _day_text(d), reason))
    return delete, sorted(kept)


def prune_trades(
    catalog_path: str,
    candles_dir: str,
    retention_days: int,
    apply: bool,
    venue: str | None = None,
) -> tuple[int, int]:
    """Apply (or report) the trade policy; returns (files deleted or deletable, bytes)."""
    delete, kept = plan_trade_prune(
        catalog_path, candles_dir, retention_days, time.time_ns(), venue
    )
    freed = 0
    for path in delete:
        freed += path.stat().st_size
        if apply:
            # Marked first: the rebuild must keep the live values of these rows from now on, since
            # an older unverified file can keep `covered_from` reaching back past this one.
            record_gap(catalog_path, path.parent.name, *_file_span(path), "pruned", 0)
            path.unlink()
        logger.info("  %s %s", "deleted" if apply else "[report only] would delete", path)
    for iid, day, reason in kept:
        logger.info("  kept %s %s: %s", iid, day, reason)
    logger.info(
        "trade retention %d days: %d file(s) %s (%.1f MB), %d old instrument-day(s) kept",
        retention_days,
        len(delete),
        "deleted" if apply else "deletable (report only)",
        freed / 1024 / 1024,
        len(kept),
    )
    return len(delete), freed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--types", nargs="+", help="data types for plain age retention")
    parser.add_argument("--days", type=int, default=14, help="age retention for --types")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="delete (default: report only)")
    mode.add_argument("--dry-run", action="store_true", help="report only (the default)")
    parser.add_argument("--candles-dir", help="directory of candles_<venue>.db; enables trades")
    parser.add_argument("--trade-retention-days", type=int, default=7)
    parser.add_argument("--venue", help="only instruments of this venue, e.g. DYDX")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.types and TRADE_TICK in args.types:
        parser.error(
            "trade_tick has its own verification-gated policy (--candles-dir), not --types"
        )
    if not args.types and not args.candles_dir:
        parser.error("nothing to do: give --types and/or --candles-dir")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    catalog = Path(args.catalog)
    if not catalog.is_dir():
        logger.error("prune: catalog %s does not exist (wrong mount?)", catalog)
        return 1
    with maintenance_lock(catalog) as locked:
        if not locked:
            logger.error("prune: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return 1
        if args.types:
            prune(args.catalog, args.types, args.days, not args.apply, args.venue)
        if args.candles_dir:
            prune_trades(
                args.catalog, args.candles_dir, args.trade_retention_days, args.apply, args.venue
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
