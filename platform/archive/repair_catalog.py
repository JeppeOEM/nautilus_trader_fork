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
Find and repair second snapshots whose trade OHLC is impossible given their own book.

Usage:
    python -m archive.repair_catalog --catalog /app/catalog [--instrument X ...]      # report only
    python -m archive.repair_catalog --catalog /app/catalog --apply \\
        --candles-dir /app/candles_dir                                                # rewrite

Detection and the repair itself are `archive.application.repair`'s docstring. --apply, per flagged
second: replace the snapshot with a copy whose trade fields are cleared, and (with --candles-db)
rebuild the candle-store days it touched from the corrected raw 1s. Without --candles-db the store
still holds the spike: run `python -m candles.rebuild` for those days. The book fields are
untouched. Manually run, like prune_catalog; reads raw 1s in day chunks (MEM-01).

--apply needs --candles-dir, an existing candle-store directory holding at least one
`candles_<venue>.db` (a store-less directory would make every clear a silent no-op), whose
`verified_days` verdict each repaired instrument-day loses before its first row is deleted
(DW-203); a verdict that cannot be cleared leaves that instrument unrepaired (`repair.error`,
exit 2). Repaired files are written zstd (DW-260). A --candles-db given with --apply must be a
file in that same directory, so the candles rebuilt and the verdict cleared are one store's.

--apply holds the catalog maintenance flock and, per venue, the venue's capture lock exclusively
(`<catalog>/.capture-<VENUE>.lock`): while that venue's collector runs, its instruments are not
repaired (`repair.capture_running`, exit 1); an instrument id with no venue, or with a venue
`kernel.venues` does not know, has no lock to take and is refused (`repair.error`, exit 1). A
missing catalog is `archive.catalog_missing`, exit 1. A row of the current UTC day, or one held by a file whose span
reaches it, is never repaired (`repair.open_day`, exit 2); nor is a flagged row no longer stored
when its group is read back (`repair.error`, exit 2).

Never run it on a day `rebuild_seconds` has rebuilt (story 22.13): a rebuilt second holds the
trades whose *exchange* time falls in it, while its book is still sampled at mid-second on
arrival time, so a fast market can put a real trade outside that book range -- this tool would
then clear real trades. The rebuild itself is the repair for rows written after the trade
archive existed; this tool is for pre-archive rows only.
"""

import argparse
import logging
from itertools import groupby
from pathlib import Path

from candles.application.rebuild import all_instruments
from candles.application.rebuild import data_range_ns
from candles.application.verified_days import VerifiedDays
from candles.infrastructure.verified_days import VerifiedDaysDir
from candles.infrastructure.verified_days import candle_store_dir_problem
from kernel.venues import VENUE_KINDS
from kernel.venues import MalformedInstrumentId
from kernel.venues import venue_of
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.ports import CatalogWriter
from archive.application.repair import closed_rows
from archive.application.repair import find_impossible_snapshots
from archive.application.repair import repair_instrument
from archive.infrastructure.maintenance_lock import MAINTENANCE_LOCK_NAME
from archive.infrastructure.maintenance_lock import capture_exclusive
from archive.infrastructure.maintenance_lock import maintenance
from nautilus_trader.persistence.catalog import ParquetDataCatalog


logger = logging.getLogger(__name__)

_CAPTURE_RUNNING = 1
_OPEN_DAY_SKIPPED = 2


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--instrument", action="append", help="repeatable; default: all")
    parser.add_argument(
        "--apply", action="store_true", help="rewrite the catalog (default: report only)"
    )
    parser.add_argument(
        "--candles-db", help="candles.db to rebuild the repaired days in (closed days only)"
    )
    parser.add_argument(
        "--candles-dir",
        help="candle-store directory whose verdicts a repaired day loses (required with --apply)",
    )
    return parser


def _venue(iid: str) -> str:
    try:
        return venue_of(iid)
    except MalformedInstrumentId:
        return ""


def _same_store_dir(candles_db: str, candles_dir: str) -> bool:
    return Path(candles_db).resolve().parent == Path(candles_dir).resolve()


def _instrument(
    args: argparse.Namespace,
    catalog: ParquetDataCatalog,
    iid: str,
    writer: CatalogWriter | None,
    verified: VerifiedDays | None,
) -> bool:
    """
    Report (and with --apply, a `writer` and `verified`, repair) one instrument; False when a
    flagged row was skipped.
    """
    span = data_range_ns(args.catalog, iid)
    if span is None:
        return True
    flagged = find_impossible_snapshots(args.catalog, iid, *span)
    if not flagged:
        return True
    logger.info("%s: %d impossible snapshot(s)", iid, len(flagged))
    for snap in flagged:
        logger.info(
            "  ts=%d high=%s low=%s vol=%.4f",
            snap.ts_event,
            snap.high_price,
            snap.low_price,
            snap.buy_volume + snap.sell_volume,
        )
    if writer is None or verified is None:  # a report writes nothing
        return True
    closed = closed_rows(writer, args.catalog, iid, flagged)
    repaired = not closed or repair_instrument(
        catalog, args.catalog, iid, closed, verified, args.candles_db
    )
    if closed and repaired:
        logger.info("  repaired")
    return repaired and len(closed) == len(flagged)


def _all_instruments(
    args: argparse.Namespace,
    catalog: ParquetDataCatalog,
    iids: list[str],
    writer: CatalogWriter | None,
    verified: VerifiedDays | None,
) -> bool:
    """Every instrument, none skipped by an earlier one's result; False when any row was skipped."""
    results = [_instrument(args, catalog, iid, writer, verified) for iid in iids]
    return all(results)


def _venue_group(
    args: argparse.Namespace,
    venue: str,
    iids: list[str],
    writer: CatalogWriter,
    verified: VerifiedDays,
) -> int:
    """One venue's instruments, repaired under its capture lock; returns the exit code."""
    catalog = ParquetDataCatalog(args.catalog)
    if venue not in VENUE_KINDS:  # no capture lock that could prove no collector writes it
        what = f"unknown venue {venue!r}" if venue else "no venue in the instrument id"
        error_ledger.record("repair.error", f"{iids}: {what}; refused, not repaired")
        return _CAPTURE_RUNNING
    with capture_exclusive(args.catalog, venue) as exclusive:
        if not exclusive:
            error_ledger.record(
                "repair.capture_running",
                f"{venue} collector holds its capture lock; {len(iids)} instrument(s) not repaired",
            )
            return _CAPTURE_RUNNING
        ok = _all_instruments(args, catalog, iids, writer, verified)
    return 0 if ok else _OPEN_DAY_SKIPPED


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; 0 done, 1 a venue's collector was running (or no lock), 2 rows skipped."""
    parser = _parser()
    args = parser.parse_args(argv)
    if args.apply and not args.candles_dir:
        parser.error(
            "--apply needs --candles-dir: a repaired day's stored verdict is cleared there"
        )
    problem = candle_store_dir_problem(args.candles_dir) if args.apply else None
    if problem is not None:
        parser.error(f"--candles-dir {problem}")
    if args.apply and args.candles_db and not _same_store_dir(args.candles_db, args.candles_dir):
        # Candles rebuilt in one store while the verdict is cleared in another would leave the
        # rebuilt store's stale `pass` standing.
        parser.error(f"--candles-db {args.candles_db} is not in --candles-dir {args.candles_dir}")
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if catalog_missing("repair", args.catalog):
        return 1
    iids = sorted(args.instrument or all_instruments(args.catalog), key=_venue)
    if not args.apply:  # a report writes nothing, so it takes no lock
        _all_instruments(args, ParquetDataCatalog(args.catalog), iids, None, None)
        return 0
    codes = []
    with maintenance(args.catalog) as writer, VerifiedDaysDir(args.candles_dir) as verified:
        if writer is None:
            logger.error("repair: another run holds %s; not starting", MAINTENANCE_LOCK_NAME)
            return 1
        for venue, group in groupby(iids, key=_venue):
            codes.append(_venue_group(args, venue, list(group), writer, verified))
    return min((c for c in codes if c), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
