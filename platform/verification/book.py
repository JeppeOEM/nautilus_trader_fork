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
The book tool's composition root (Story 31.5):

    python3 -m verification.book --venue BYBIT|HYPERLIQUID --day YYYY-MM-DD [--json]
        [--raw-dir DIR] [--catalog DIR]

For every instrument of the venue's plan, over one closed UTC day:
- REST agreement (reported first): the recorder's own REST book polls, placed among the reference
  book's messages by the venue's key (Bybit `seq`, Hyperliquid `time`), validate the rebuilt
  reference itself -- a reference that disagrees at an equal key, or keeps a contradicted level
  across two polls, or never agrees at all, fails the instrument whatever the rows say;
- seconds: every second's stored top-20 book (`custom_dydx_second_snapshot`, integer units) is
  compared, exactly, with the reference's top 20 after every message with venue time before the
  second's end (DATA-01), and each difference is classified (`docs/DATA_DICTIONARY.md` section
  1.18).

Prints a report (or `--json`) and exits 0 when the day passes, 1 when it does not, 2 on a usage
error. A refusal -- a day not yet closed, no raw root, no catalog, an unreadable plan, a malformed
line, a truncated raw file of an hour of the day, a float-layout snapshot file, a stored gap that
is not strictly positive, a value the exact decode cannot hold -- is ledgered at
`verification.book.refused` and exits with its message (status 1); any other exception is a
crash, ledgered at the same site and re-raised.

The reference side never imports the code it checks (`tests/test_boundaries.py`). Environment:
the conservation tool's (`VERIFY_DATA_DIR`, `CATALOG_PATH`, `BYBIT_COLLECTOR_CONFIG` /
`HYPERLIQUID_COLLECTOR_CONFIG`, `ERROR_LEDGER_DIR` / `ERROR_LEDGER_SERVICE`).
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from collections.abc import Mapping
from datetime import date

from observability import error_ledger

from verification.application import sites
from verification.application.book import BookInputs
from verification.application.book import check_day
from verification.application.book import render_text
from verification.application.book import report_json
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import day_hours
from verification.application.conservation import is_closed
from verification.conservation import Refused
from verification.conservation import directory
from verification.conservation import plan_of
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.reference_book import BookDayReport
from verification.domain.subscriptions import VENUES
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.raw_store import RawReader
from verification.infrastructure.raw_store import TruncatedTail
from verification.infrastructure.raw_store import venue_dir
from verification.infrastructure.snapshot_book import SnapshotBooks


def inputs_of(args: argparse.Namespace, environ: Mapping[str, str]) -> BookInputs:
    """Build the three read-only sources from the arguments and environment (`Refused`: absent)."""
    raw_root = directory(args.raw_dir or environ.get("VERIFY_DATA_DIR"), "VERIFY_DATA_DIR")
    directory(str(venue_dir(raw_root, args.venue)), "the raw venue directory")
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    return BookInputs(
        reference=RawReader(raw_root, args.venue, day_hours(args.day)),
        snapshots=SnapshotBooks(catalog),
        coverage=CoverageFiles(catalog, args.venue),
    )


def run(args: argparse.Namespace, environ: Mapping[str, str], now_ns: int) -> BookDayReport:
    """Build the inputs and check the day; any input that cannot be trusted is `Refused`."""
    if not is_closed(args.day, now_ns):
        raise Refused(
            f"day not closed: {args.day} has not ended in UTC and settled "
            f"({DAY_SETTLE_NS // NS_PER_HOUR} h after midnight)"
        )
    inputs = inputs_of(args, environ)
    plan = plan_of(args.venue, environ)
    try:
        return check_day(plan, args.day, inputs)
    # MalformedLine, FloatLayout and a non-positive stored gap are ValueErrors; an ArithmeticError
    # is a value the exact decode cannot hold (`decimal.Inexact`), never a number rounded to fit.
    except (TruncatedTail, ValueError, ArithmeticError, OSError) as exc:
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.book")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, type=date.fromisoformat, help="UTC YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--raw-dir", help="the recording root (default: VERIFY_DATA_DIR)")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    return parser


def main(argv: list[str] | None = None, clock: Callable[[], int] = time.time_ns) -> int:
    """
    Run the tool; return the exit status (0 pass, 1 fail). A refusal raises `SystemExit`; any
    other exception is ledgered at the same site as a crash, then re-raised (DATA-07: never an
    unledgered traceback). `clock` (epoch ns) decides whether the day is closed; tests pass a
    fixed one.
    """
    args = _parser().parse_args(argv)
    error_ledger.start(service=error_ledger.job_service("verify_book", "verification", args.venue))
    subject = f"{args.venue} {args.day}"
    try:
        report = run(args, os.environ, clock())
    except Refused as exc:
        error_ledger.record(sites.BOOK_REFUSED, f"{subject}: {exc}", exc)
        raise SystemExit(f"book refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.BOOK_REFUSED, f"{subject}: crashed: {exc!r}", exc)
        raise
    text = json.dumps(report_json(report), indent=2) if args.json else render_text(report)
    print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
