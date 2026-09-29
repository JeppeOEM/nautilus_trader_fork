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
The conservation tool's composition root (Story 31.2):

    python3 -m verification.conservation --venue BYBIT|HYPERLIQUID --day YYYY-MM-DD [--json]
        [--raw-dir DIR] [--catalog DIR]

For every instrument of the venue's plan, over one closed UTC day, reconciles:
- trades: every reference trade id (the recorder's verbatim `publicTrade`/`recent-trade` or
  `trades` lines, venue time in the day) is archived (`<catalog>/data/trade_tick/`) or explained
  by a durable record -- capture's coverage record (`trades_dropped`, `trades_unrecoverable`) or
  an archive-gap marker; the rest is `unexplained`, which must be 0; ids archived twice too;
- seconds: every second of the day has a snapshot row (`custom_dydx_second_snapshot`) or a
  coverage `seconds` run naming why not -- never both, never two rows.

Prints a table (or `--json`) and exits 0 when the day passes, 1 when it does not, 2 on a usage
error. The day passes when every instrument does, the coverage record exists and no raw reference
hour of the day is missing. A refusal -- a day not yet closed, no raw root, no catalog, an
unreadable plan, a malformed coverage or marker line, a truncated raw file of an hour of the
day -- is ledgered at
`verification.conservation.refused` and exits with its message (status 1).

The reference side never imports the code it checks: the reference trades are parsed from the
venue's own frames, the catalog is read as raw Parquet with pyarrow and the durable files from
their published line formats -- nothing of `capture`, `kernel.second_snapshot`,
`kernel.catalog_files` or `nautilus_trader` (`tests/test_boundaries.py`).

Environment:
- `VERIFY_DATA_DIR` (required unless `--raw-dir`): the recording root holding `raw/<venue>/`.
- `CATALOG_PATH` (required unless `--catalog`): the catalog root; the coverage record is
  `<catalog>/../coverage/<venue>.jsonl`.
- `BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG`: the venue's `config.toml`, the same
  variables and defaults as the collectors and the recorder.
- `ERROR_LEDGER_DIR` / `ERROR_LEDGER_SERVICE`: the durable error ledger (DATA-07).
"""

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from collections.abc import Mapping
from datetime import date
from pathlib import Path

from observability import error_ledger

from verification.application import sites
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import Inputs
from verification.application.conservation import conserve
from verification.application.conservation import day_hours
from verification.application.conservation import is_closed
from verification.application.conservation import render_text
from verification.application.conservation import report_json
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import DayReport
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import VENUES
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.catalog_reader import ParquetArchive
from verification.infrastructure.raw_store import RawReader
from verification.infrastructure.raw_store import TruncatedTail
from verification.infrastructure.raw_store import venue_dir
from verification.recorder import config_path
from verification.recorder import read_plan_file


class Refused(Exception):
    """A run that cannot give a verdict: its message is ledgered and becomes the exit message."""


def _directory(value: str | None, name: str) -> Path:
    if not value:
        raise Refused(f"{name} is required")
    path = Path(value)
    if not path.is_dir():
        raise Refused(f"{name} {path} is not a directory")
    return path


def _inputs(args: argparse.Namespace, environ: Mapping[str, str]) -> Inputs:
    raw_root = _directory(args.raw_dir or environ.get("VERIFY_DATA_DIR"), "VERIFY_DATA_DIR")
    _directory(str(venue_dir(raw_root, args.venue)), "the raw venue directory")
    catalog = _directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    return Inputs(
        reference=RawReader(raw_root, args.venue, day_hours(args.day)),
        archive=ParquetArchive(catalog),
        coverage=CoverageFiles(catalog, args.venue),
    )


def _plan(venue: str, environ: Mapping[str, str]) -> RecordingPlan:
    path = config_path(venue, environ)
    try:
        return read_plan_file(path, venue)
    except (OSError, ValueError) as exc:
        raise Refused(f"plan {path}: {exc}") from exc


def run(args: argparse.Namespace, environ: Mapping[str, str], now_ns: int) -> DayReport:
    """Build the inputs and reconcile the day; any input that cannot be trusted is `Refused`."""
    if not is_closed(args.day, now_ns):
        raise Refused(
            f"day not closed: {args.day} has not ended in UTC and settled "
            f"({DAY_SETTLE_NS // NS_PER_HOUR} h after midnight)"
        )
    inputs = _inputs(args, environ)
    plan = _plan(args.venue, environ)
    try:
        return conserve(plan, args.day, inputs)
    except (TruncatedTail, ValueError, OSError) as exc:  # MalformedLine is a ValueError
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.conservation")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, type=date.fromisoformat, help="UTC YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--raw-dir", help="the recording root (default: VERIFY_DATA_DIR)")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    return parser


def main(argv: list[str] | None = None, clock: Callable[[], int] = time.time_ns) -> int:
    """
    Run the tool; return the exit status (0 pass, 1 fail). A refusal raises `SystemExit`.
    `clock` (epoch ns) decides whether the day is closed; tests pass a fixed one.
    """
    args = _parser().parse_args(argv)
    error_ledger.start()
    try:
        report = run(args, os.environ, clock())
    except Refused as exc:
        error_ledger.record(sites.CONSERVATION_REFUSED, f"{args.venue} {args.day}: {exc}", exc)
        raise SystemExit(f"conservation refused: {exc}") from exc
    text = json.dumps(report_json(report), indent=2) if args.json else render_text(report)
    print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
