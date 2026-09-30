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
    python3 -m verification.conservation --venue V --start ISO --end ISO [--json] ...

`--start`/`--end` (Story 31.10, UTC whole seconds, e.g. `2026-09-30T10:00:00Z`; exclusive with
`--day`) judge one window `[start, end)` by the same rules: trades with venue time in it, its
seconds, the raw hours it touches plus their neighbours. A window is judged only once its last
touched hour has ended and `WINDOW_SETTLE_NS` (10 min) has passed since its end; the JSON adds
`start` and `end`.

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
from datetime import UTC
from datetime import date
from datetime import datetime
from pathlib import Path

from observability import error_ledger

from verification.application import sites
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import WINDOW_SETTLE_NS
from verification.application.conservation import Inputs
from verification.application.conservation import conserve
from verification.application.conservation import conserve_window
from verification.application.conservation import day_hours
from verification.application.conservation import is_closed
from verification.application.conservation import render_text
from verification.application.conservation import report_json
from verification.application.conservation import window_hours
from verification.application.conservation import window_settled
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.conservation import NS_PER_S
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


def directory(value: str | None, name: str) -> Path:
    """Return `value` as an existing directory; `Refused` naming `name` otherwise."""
    if not value:
        raise Refused(f"{name} is required")
    path = Path(value)
    if not path.is_dir():
        raise Refused(f"{name} {path} is not a directory")
    return path


def candle_store(args: argparse.Namespace, environ: Mapping[str, str], catalog: Path) -> Path:
    """Return the venue's candle store file; `Refused` when its directory or the file is absent."""
    candles = directory(
        args.candles or environ.get("CANDLES_DIR") or str(catalog.parent / "candles"),
        "the candles directory (CANDLES_DIR)",
    )
    store = candles / f"candles_{args.venue.lower()}.db"
    if not store.is_file():
        raise Refused(f"candle store {store} does not exist")
    return store


def _hours(args: argparse.Namespace) -> range:
    """Return the UTC hours the run judges: the day's, or the ones the window touches."""
    if args.day is not None:
        return day_hours(args.day)
    return window_hours(args.start, args.end)


def _inputs(args: argparse.Namespace, environ: Mapping[str, str]) -> Inputs:
    raw_root = directory(args.raw_dir or environ.get("VERIFY_DATA_DIR"), "VERIFY_DATA_DIR")
    directory(str(venue_dir(raw_root, args.venue)), "the raw venue directory")
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    return Inputs(
        reference=RawReader(raw_root, args.venue, _hours(args)),
        archive=ParquetArchive(catalog),
        coverage=CoverageFiles(catalog, args.venue),
    )


def plan_of(venue: str, environ: Mapping[str, str]) -> RecordingPlan:
    """Read the venue's plan (its collector `config.toml`); `Refused` when it cannot be read."""
    path = config_path(venue, environ)
    try:
        return read_plan_file(path, venue)
    except (OSError, ValueError) as exc:
        raise Refused(f"plan {path}: {exc}") from exc


def _refuse_unsettled(args: argparse.Namespace, now_ns: int) -> None:
    if args.day is not None and not is_closed(args.day, now_ns):
        raise Refused(
            f"day not closed: {args.day} has not ended in UTC and settled "
            f"({DAY_SETTLE_NS // NS_PER_HOUR} h after midnight)"
        )
    if args.day is None and not window_settled(args.start, args.end, now_ns):
        raise Refused(
            f"window not settled: {subject(args)} needs its last hour ended and "
            f"{WINDOW_SETTLE_NS // NS_PER_S // 60} min after its end"
        )


def run(args: argparse.Namespace, environ: Mapping[str, str], now_ns: int) -> DayReport:
    """
    Build the inputs and reconcile the day or the window; any input that cannot be trusted, or a
    span not yet settled, is `Refused`.
    """
    _refuse_unsettled(args, now_ns)
    inputs = _inputs(args, environ)
    plan = plan_of(args.venue, environ)
    try:
        if args.day is not None:
            return conserve(plan, args.day, inputs)
        return conserve_window(plan, args.start, args.end, inputs)
    except (TruncatedTail, ValueError, OSError) as exc:  # MalformedLine is a ValueError
        raise Refused(str(exc)) from exc


def instant_ns(text: str) -> int:
    """
    Parse a UTC whole-second ISO instant (`2026-09-30T10:00:00Z`; no offset means UTC) into epoch
    ns; `ValueError` for another offset or a fraction of a second.
    """
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    if moment.utcoffset() or moment.microsecond:
        raise ValueError(f"{text!r} is not a whole UTC second")
    return int(moment.timestamp()) * NS_PER_S


def subject(args: argparse.Namespace) -> str:
    """Return what the run judges, as its messages name it: the day, or `start/end`."""
    if args.day is not None:
        return str(args.day)
    return f"{args.start_text}/{args.end_text}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.conservation")
    parser.add_argument("--venue", required=True, choices=VENUES)
    span = parser.add_mutually_exclusive_group(required=True)
    span.add_argument("--day", type=date.fromisoformat, help="UTC YYYY-MM-DD")
    span.add_argument("--start", dest="start_text", help="window start, UTC ISO whole second")
    parser.add_argument("--end", dest="end_text", help="window end (exclusive), with --start")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--raw-dir", help="the recording root (default: VERIFY_DATA_DIR)")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    return parser


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """
    Parse the command line; a usage error (a bad day or instant, `--end` without `--start` or
    the reverse, an empty or inverted window) exits 2.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    args.start = args.end = None
    if args.day is not None:
        if args.end_text is not None:
            parser.error("--end goes with --start, not --day")
        return args
    if args.end_text is None:
        parser.error("--start needs --end")
    try:
        args.start, args.end = instant_ns(args.start_text), instant_ns(args.end_text)
    except ValueError as exc:
        parser.error(str(exc))
    if args.end <= args.start:
        parser.error(f"--end {args.end_text} is not after --start {args.start_text}")
    return args


def main(argv: list[str] | None = None, clock: Callable[[], int] = time.time_ns) -> int:
    """
    Run the tool; return the exit status (0 pass, 1 fail). A refusal raises `SystemExit`.
    `clock` (epoch ns) decides whether the day is closed; tests pass a fixed one.
    """
    args = parse_args(argv)
    error_ledger.start(
        service=error_ledger.job_service("verify_conservation", "verification", args.venue)
    )
    try:
        report = run(args, os.environ, clock())
    except Refused as exc:
        error_ledger.record(sites.CONSERVATION_REFUSED, f"{args.venue} {subject(args)}: {exc}", exc)
        raise SystemExit(f"conservation refused: {exc}") from exc
    text = json.dumps(report_json(report), indent=2) if args.json else render_text(report)
    print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
