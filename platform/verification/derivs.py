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
The derivs tool's composition root (Story 31.6):

    python3 -m verification.derivs --venue BYBIT|HYPERLIQUID --day YYYY-MM-DD [--json]
        [--raw-dir DIR] [--catalog DIR]

For every instrument of the venue's plan, over one closed UTC day, per type (mark, index,
funding, open interest): the recorder's REST polls validate the reference (reported first); every
stored row is matched to the recorder's frames exactly and classified; every reference update of
the day is classified as stored or not, beside the type's documented sampling; the `ts_event`
rule and the precision labels are checked. Then every instrument definition field against every
venue poll of the day, and -- on Bybit -- that no spot id holds a derivative row
(`docs/DATA_DICTIONARY.md` section 1.19).

Prints a report (or `--json`) and exits 0 when the day passes, 1 when it does not, 2 on a usage
error. A refusal -- the book tool's (a day not closed, no raw root or catalog, an unreadable plan,
a malformed line, a truncated raw file of an hour of the day), a mark/index file without its
`price_precision` label, a stored value that is not decimal text, a null value or clock, an
unreadable `open_interest_poll_seconds` -- is ledgered at `verification.derivs.refused` and exits
with its message (status 1); any other exception is a crash, ledgered at the same site and
re-raised.

The reference side never imports the code it checks (`tests/test_boundaries.py`). Environment:
the conservation tool's (`VERIFY_DATA_DIR`, `CATALOG_PATH`, `BYBIT_COLLECTOR_CONFIG` /
`HYPERLIQUID_COLLECTOR_CONFIG`, `ERROR_LEDGER_DIR` / `ERROR_LEDGER_SERVICE`).
"""

import argparse
import json
import os
import sys
import time
import tomllib
from collections.abc import Callable
from collections.abc import Mapping
from datetime import date

from observability import error_ledger

from verification.application import sites
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import day_hours
from verification.application.conservation import is_closed
from verification.application.derivs import DerivsInputs
from verification.application.derivs import check_day
from verification.application.derivs import render_text
from verification.application.derivs import report_json
from verification.conservation import Refused
from verification.conservation import directory
from verification.conservation import plan_of
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.derivs_check import DerivsDayReport
from verification.domain.subscriptions import BYBIT
from verification.domain.subscriptions import VENUES
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.derivs_reader import DerivsCatalog
from verification.infrastructure.raw_store import RawReader
from verification.infrastructure.raw_store import TruncatedTail
from verification.infrastructure.raw_store import venue_dir
from verification.recorder import config_path


# The Bybit collector's own default when its config omits the key: `BybitConfig
# .open_interest_poll_seconds = 300` (`capture/venues/bybit/config.py:36`, applied by
# `capture/infrastructure/config.py`'s `_bybit`). Restated, not imported: capture's config is code
# the reference side checks. The committed `capture/venues/bybit/config.toml` sets 300 too.
# Known limit: the period is read from *today's* config file, not the one in force on the checked
# day, and `DEFAULT_OI_POLL_SECONDS` restates capture's default rather than reading it -- a period
# changed since the day, or a changed capture default, judges the day's poll gaps against the wrong
# period (a loud false fail or a missed gap). Upgrade path: capture writes its poll period to a
# durable per-day record (the coverage record), and the tool reads the day's own value.
DEFAULT_OI_POLL_SECONDS = 300
_OI_KEY = "open_interest_poll_seconds"


def oi_period_of(venue: str, environ: Mapping[str, str]) -> int:
    """
    Read Bybit's open-interest poll period from the venue config the collector reads (0 for a
    venue without the poll); `Refused` when the file cannot be read or the value is not a
    positive integer -- the collector refuses to start on it too.
    """
    if venue != BYBIT:
        return 0
    path = config_path(venue, environ)
    try:
        value = tomllib.loads(path.read_text()).get(_OI_KEY, DEFAULT_OI_POLL_SECONDS)
    except (OSError, ValueError) as exc:
        raise Refused(f"plan {path}: {exc}") from exc
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise Refused(f"plan {path}: `{_OI_KEY}` {value!r} is not a positive integer")
    return value


def inputs_of(args: argparse.Namespace, environ: Mapping[str, str]) -> DerivsInputs:
    """Build the read-only sources from the arguments and environment (`Refused`: absent)."""
    raw_root = directory(args.raw_dir or environ.get("VERIFY_DATA_DIR"), "VERIFY_DATA_DIR")
    directory(str(venue_dir(raw_root, args.venue)), "the raw venue directory")
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    return DerivsInputs(
        reference=RawReader(raw_root, args.venue, day_hours(args.day)),
        catalog=DerivsCatalog(catalog),
        coverage=CoverageFiles(catalog, args.venue),
        oi_period_s=oi_period_of(args.venue, environ),
    )


def run(args: argparse.Namespace, environ: Mapping[str, str], now_ns: int) -> DerivsDayReport:
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
    # MalformedLine, a missing label, a non-decimal stored value and a null clock are ValueErrors;
    # an ArithmeticError is a value the exact decode cannot hold (`decimal.Inexact`).
    except (TruncatedTail, ValueError, ArithmeticError, OSError) as exc:
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.derivs")
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
    error_ledger.start(
        service=error_ledger.job_service("verify_derivs", "verification", args.venue)
    )
    subject = f"{args.venue} {args.day}"
    try:
        report = run(args, os.environ, clock())
    except Refused as exc:
        error_ledger.record(sites.DERIVS_REFUSED, f"{subject}: {exc}", exc)
        raise SystemExit(f"derivs refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.DERIVS_REFUSED, f"{subject}: crashed: {exc!r}", exc)
        raise
    text = json.dumps(report_json(report), indent=2) if args.json else render_text(report)
    print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
