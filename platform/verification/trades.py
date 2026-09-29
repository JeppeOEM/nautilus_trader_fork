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
The trades tool's composition root (Story 31.4):

    python3 -m verification.trades --venue BYBIT|HYPERLIQUID --day YYYY-MM-DD
        --stage live|rebuilt [--json] [--raw-dir DIR] [--catalog DIR]

For every instrument of the venue's plan, over one closed UTC day, one hour at a time:
- ids: every reference trade (the recorder's verbatim `publicTrade`/`trades` frames and Bybit
  `recent-trade` polls) is matched with the archived `trade_tick` row of its id and compared
  field by field -- price, size (numeric `Decimal` equality), aggressor side, `ts_event` (the
  venue's ms * 10^6, exactly); the rest is missing (explained by capture's coverage record or an
  archive-gap marker, or not), archive-only ids are extra (explained only when backfilled inside
  a recorder connection gap), and ids stored twice are duplicated;
- seconds: every exchange second with a snapshot row, reference or archived trades is judged by folding
  the reference's trades itself (`Decimal`, its own fold) and comparing the row's eight stored
  trade columns with that fold and with the same fold over the archived trades.

`--stage` says what the rows are: `live` (before the night's `archive.rebuild_seconds`, which
rewrites rows in place and leaves no marker) reports a row the rebuild will correct as
`live_provisional` and labels the verdict provisional; `rebuilt` fails it (`rebuild_mismatch`).
A wrong `rebuilt` can only false-fail.

Prints a report (or `--json`) and exits 0 when the checked window passes, 1 when it does not, 2
on a usage error. A refusal -- an unknown stage, a day not yet closed, no raw root, no catalog,
an unreadable plan, a malformed line, a truncated raw file of an hour of the day, a trade file
without its precision metadata, a value the exact decode cannot hold -- is ledgered at `verification.trades.refused` and exits with its
message (status 1); any other exception is a crash, ledgered at the same site and re-raised.

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
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import day_hours
from verification.application.conservation import is_closed
from verification.application.trades import TradeInputs
from verification.application.trades import check_day
from verification.application.trades import render_text
from verification.application.trades import report_json
from verification.conservation import Refused
from verification.conservation import directory
from verification.conservation import plan_of
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.subscriptions import VENUES
from verification.domain.trade_check import STAGES
from verification.domain.trade_check import TradesDayReport
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.catalog_reader import ParquetArchive
from verification.infrastructure.raw_store import RawReader
from verification.infrastructure.raw_store import TruncatedTail
from verification.infrastructure.raw_store import venue_dir


def inputs_of(args: argparse.Namespace, environ: Mapping[str, str]) -> TradeInputs:
    """Build the three read-only sources from the arguments and environment (`Refused` if absent)."""
    raw_root = directory(args.raw_dir or environ.get("VERIFY_DATA_DIR"), "VERIFY_DATA_DIR")
    directory(str(venue_dir(raw_root, args.venue)), "the raw venue directory")
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    return TradeInputs(
        reference=RawReader(raw_root, args.venue, day_hours(args.day)),
        archive=ParquetArchive(catalog),
        coverage=CoverageFiles(catalog, args.venue),
    )


def run(args: argparse.Namespace, environ: Mapping[str, str], now_ns: int) -> TradesDayReport:
    """Build the inputs and check the day; any input that cannot be trusted is `Refused`."""
    if args.stage not in STAGES:
        raise Refused(f"unknown --stage {args.stage!r}: one of {', '.join(STAGES)}")
    if not is_closed(args.day, now_ns):
        raise Refused(
            f"day not closed: {args.day} has not ended in UTC and settled "
            f"({DAY_SETTLE_NS // NS_PER_HOUR} h after midnight)"
        )
    inputs = inputs_of(args, environ)
    plan = plan_of(args.venue, environ)
    try:
        return check_day(plan, args.day, inputs, args.stage)
    # MalformedLine is a ValueError; an ArithmeticError is a wire or stored value the exact decode
    # cannot hold (`decimal.Inexact` from an over-long decimal), never a number rounded to fit.
    except (TruncatedTail, ValueError, ArithmeticError, OSError) as exc:
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.trades")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, type=date.fromisoformat, help="UTC YYYY-MM-DD")
    # Validated by `run`, not `choices`: an unknown stage is a ledgered refusal, not a usage error.
    parser.add_argument("--stage", required=True, help="live | rebuilt: what the rows are")
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
    error_ledger.start()
    subject = f"{args.venue} {args.day}"
    try:
        report = run(args, os.environ, clock())
    except Refused as exc:
        error_ledger.record(sites.TRADES_REFUSED, f"{subject}: {exc}", exc)
        raise SystemExit(f"trades refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.TRADES_REFUSED, f"{subject}: crashed: {exc!r}", exc)
        raise
    text = json.dumps(report_json(report), indent=2) if args.json else render_text(report)
    print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
