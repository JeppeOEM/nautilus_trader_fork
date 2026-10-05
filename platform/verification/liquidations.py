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
The liquidations tool's composition root (Story 33.1):

    python3 -m verification.liquidations --venue BYBIT --day YYYY-MM-DD [--json] [--catalog DIR]

For every instrument of the venue with archived liquidations or a liquidation coverage window,
over one closed UTC day: the share of its liquidations matched by a same-size trade on the forced
side within 2 s (each trade used once), the unmatched ids (the first 20), and the seconds the
coverage record names as unrecoverable (`docs/DATA_DICTIONARY.md` §1.26). A venue without a
liquidation feed reports `applicable: false`. It reads only the catalog and the coverage record,
so it needs no reference recorder.

Prints a report (or `--json`) and exits 0: it is a self-check, never a verdict, and never asserts
100 % (the domain's `Known limit:`). A refusal -- a day not closed, a missing catalog, a malformed
coverage line, a stored row with a null column or an unknown side, a file that vanished -- is
ledgered at `verification.liquidations.refused` and exits with its message (status 1); any other
exception is a crash, ledgered at the same site and re-raised.

The reference side never imports the code it checks (`tests/test_boundaries.py`): the stored
liquidations and trades are read raw. Environment: `CATALOG_PATH`, `ERROR_LEDGER_DIR` /
`ERROR_LEDGER_SERVICE`.
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
from verification.application.conservation import is_closed
from verification.application.liquidations import LiquidationDayReport
from verification.application.liquidations import LiquidationInputs
from verification.application.liquidations import check_day
from verification.application.liquidations import render_text
from verification.application.liquidations import report_json
from verification.conservation import Refused
from verification.conservation import directory
from verification.domain.conservation import NS_PER_HOUR
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.liquidation_reader import LiquidationCatalog


# Every venue a catalog holds: one without a liquidation feed is answered `applicable: false`.
VENUE_CHOICES = ("BYBIT", "HYPERLIQUID", "DYDX")


def run(args: argparse.Namespace, environ: Mapping[str, str], now_ns: int) -> LiquidationDayReport:
    """Build the read-only sources and report the day; any input that cannot be trusted is `Refused`."""
    if not is_closed(args.day, now_ns):
        raise Refused(
            f"day not closed: {args.day} has not ended in UTC and settled "
            f"({DAY_SETTLE_NS // NS_PER_HOUR} h after midnight)"
        )
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    inputs = LiquidationInputs(LiquidationCatalog(catalog), CoverageFiles(catalog, args.venue))
    try:
        return check_day(args.venue, args.day, inputs)
    # MalformedLine, a null column and an unknown side are ValueErrors; a vanished file an OSError.
    except (ValueError, OSError) as exc:
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.liquidations")
    parser.add_argument("--venue", required=True, choices=VENUE_CHOICES)
    parser.add_argument("--day", required=True, type=date.fromisoformat, help="UTC YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    return parser


def main(argv: list[str] | None = None, clock: Callable[[], int] = time.time_ns) -> int:
    """
    Run the tool; return 0 with a report. A refusal raises `SystemExit`; any other exception is
    ledgered at the same site as a crash, then re-raised (DATA-07). `clock` (epoch ns) decides
    whether the day is closed; tests pass a fixed one.
    """
    args = _parser().parse_args(argv)
    error_ledger.start(
        service=error_ledger.job_service("verify_liquidations", "verification", args.venue)
    )
    subject = f"{args.venue} {args.day}"
    try:
        report = run(args, os.environ, clock())
    except Refused as exc:
        error_ledger.record(sites.LIQUIDATIONS_REFUSED, f"{subject}: {exc}", exc)
        raise SystemExit(f"liquidations refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.LIQUIDATIONS_REFUSED, f"{subject}: crashed: {exc!r}", exc)
        raise
    print(json.dumps(report_json(report), indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
