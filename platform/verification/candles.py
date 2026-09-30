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
The candles tool's composition root (Story 31.8):

    python3 -m verification.candles --venue BYBIT|HYPERLIQUID --day YYYY-MM-DD [--json]
        [--catalog DIR] [--raw-dir DIR] [--candles DIR] [--data-api URL] [--no-served]

For every plan instrument, over one closed UTC day, on every chart width (the six stored -- 1m, 5m,
15m, 1h, 4h, 1d -- and the four folded at read time -- 10m, 30m, 45m, 1W), each bucket is judged
against the fold of the catalog's rows (exact), the reference fold of the recorder's trades over
the seconds the catalog observed (every difference explained per second or failing) and the
coverage record (every unobserved second explained): the stored bars read raw from the SQLite store,
the served bars over HTTP from the data_api, as the chart pages them (`docs/DATA_DICTIONARY.md`
section 1.21).

Prints a report (or `--json`) and exits 0 when every failing count is 0, 1 when one is not (or the
inputs are refused), 2 on a usage error. With `--no-served` the served bars are not checked: the
report says `served: not checked` and its verdict is PROVISIONAL, never PASS (the exit status is
still 0 when nothing failed, so a provisional run stays scriptable; 31.11 runs with served checks).
A refusal -- a day not closed, a missing catalog, candles directory, store file, raw directory or
coverage record, an unreadable plan, a malformed line, a truncated raw file of the day, the data_api
unreachable or answering non-200 or with a malformed page, a file vanishing mid-run -- is ledgered at
`verification.candles.refused` and exits with its message (status 1); any other exception is a
crash, ledgered at the same site and re-raised.

The reference side never imports the code it checks (`tests/test_boundaries.py`): no `candles`,
`views`, `data_api`, `capture`, `kernel.fold`, `kernel.second_snapshot` or `nautilus_trader`; the
production candles are read only as data at rest or as the served HTTP response.

Environment: `CATALOG_PATH` (unless `--catalog`); `CANDLES_DIR` (unless `--candles`; default
`<catalog>/../candles`, the store `candles_<venue lowercased>.db`); `VERIFY_DATA_DIR` (unless
`--raw-dir`); `VERIFY_DATA_API_URL` (unless `--data-api`; default `http://127.0.0.1:29100`, the
verify stack's data_api, SEC-01); `BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG` (the
plan); `ERROR_LEDGER_DIR` / `ERROR_LEDGER_SERVICE`.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from collections.abc import Mapping
from datetime import date

from observability import error_ledger

from verification.application import sites
from verification.application.candles import CandleInputs
from verification.application.candles import check_day
from verification.application.candles import render_text
from verification.application.candles import report_json
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import day_hours
from verification.application.conservation import is_closed
from verification.conservation import Refused
from verification.conservation import candle_store
from verification.conservation import directory
from verification.conservation import plan_of
from verification.domain.candle_check import CandlesDayReport
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.subscriptions import VENUES
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.catalog_reader import ParquetArchive
from verification.infrastructure.catalog_scan import CandleStoreFile
from verification.infrastructure.raw_store import RawReader
from verification.infrastructure.raw_store import TruncatedTail
from verification.infrastructure.raw_store import venue_dir
from verification.infrastructure.served_candles import Fetch
from verification.infrastructure.served_candles import ServedCandles
from verification.infrastructure.served_candles import Unservable
from verification.infrastructure.served_candles import urllib_fetch


# The verify stack's data_api, bound to loopback (`docker-compose.verify.yml`, SEC-01).
DEFAULT_DATA_API = "http://127.0.0.1:29100"


def data_api_url(args: argparse.Namespace, environ: Mapping[str, str]) -> str:
    """`--data-api`, else `VERIFY_DATA_API_URL`, else the verify stack's loopback data_api."""
    return args.data_api or environ.get("VERIFY_DATA_API_URL") or DEFAULT_DATA_API


def inputs_of(args: argparse.Namespace, environ: Mapping[str, str], fetch: Fetch) -> CandleInputs:
    """Build the read-only sources from the arguments and environment (`Refused` if absent)."""
    raw_root = directory(args.raw_dir or environ.get("VERIFY_DATA_DIR"), "VERIFY_DATA_DIR")
    directory(str(venue_dir(raw_root, args.venue)), "the raw venue directory")
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    coverage = CoverageFiles(catalog, args.venue)
    if not coverage.present():
        raise Refused(f"coverage record {coverage.path} does not exist")
    served = None if args.no_served else ServedCandles(data_api_url(args, environ), fetch)
    return CandleInputs(
        reference=RawReader(raw_root, args.venue, day_hours(args.day)),
        catalog=ParquetArchive(catalog),
        store=CandleStoreFile(candle_store(args, environ, catalog)),
        coverage=coverage,
        served=served,
    )


def run(
    args: argparse.Namespace, environ: Mapping[str, str], now_ns: int, fetch: Fetch
) -> CandlesDayReport:
    """Build the inputs and judge the day; any input that cannot be trusted is `Refused`."""
    if not is_closed(args.day, now_ns):
        raise Refused(
            f"day not closed: {args.day} has not ended in UTC and settled "
            f"({DAY_SETTLE_NS // NS_PER_HOUR} h after midnight)"
        )
    inputs = inputs_of(args, environ, fetch)
    plan = plan_of(args.venue, environ)
    try:
        return check_day(plan, args.day, inputs, now_ns)
    # MalformedLine is a ValueError; a vanished file an OSError (or, for the store, an sqlite3
    # error); an ArithmeticError a stored value the exact decode cannot hold.
    except (Unservable, TruncatedTail, ValueError, ArithmeticError, OSError, sqlite3.Error) as exc:
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.candles")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, type=date.fromisoformat, help="UTC YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    parser.add_argument("--raw-dir", help="the recording root (default: VERIFY_DATA_DIR)")
    parser.add_argument("--candles", help="the candle store directory (default: CANDLES_DIR)")
    parser.add_argument(
        "--data-api", help=f"the data_api (default: VERIFY_DATA_API_URL, else {DEFAULT_DATA_API})"
    )
    parser.add_argument(
        "--no-served", action="store_true", help="skip the served bars: a provisional verdict"
    )
    return parser


def main(
    argv: list[str] | None = None,
    clock: Callable[[], int] = time.time_ns,
    fetch: Fetch = urllib_fetch,
) -> int:
    """
    Run the tool; return the exit status (0: nothing failing, 1: something failing). A refusal
    raises `SystemExit`; any other exception is ledgered at the same site as a crash, then
    re-raised (DATA-07). `clock` (epoch ns) decides whether the day and its week are closed;
    `fetch` reads the served pages (urllib; a test passes the real route's test client).
    """
    args = _parser().parse_args(argv)
    error_ledger.start(
        service=error_ledger.job_service("verify_candles", "verification", args.venue)
    )
    subject = f"{args.venue} {args.day}"
    try:
        report = run(args, os.environ, clock(), fetch)
    except Refused as exc:
        error_ledger.record(sites.CANDLES_REFUSED, f"{subject}: {exc}", exc)
        raise SystemExit(f"candles refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.CANDLES_REFUSED, f"{subject}: crashed: {exc!r}", exc)
        raise
    text = json.dumps(report_json(report), indent=2) if args.json else render_text(report)
    print(text)
    return 0 if report.failing == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
