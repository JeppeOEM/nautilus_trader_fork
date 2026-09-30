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
The catalog tool's composition root (Story 31.7):

    python3 -m verification.catalog --venue BYBIT|HYPERLIQUID --day YYYY-MM-DD [--json]
        [--catalog DIR] [--raw-dir DIR] [--candles DIR] [--scratch-dir DIR]

Over one closed UTC day, for the venue's plan instruments (`docs/DATA_DICTIONARY.md` section 1.20):
1. structure -- schema classes, file intervals, sort order, snapshot duplicates, and every day file
   opening through `ParquetDataCatalog`;
2. the consolidation rehearsal -- the archive's real intraday and nightly consolidation on a
   linked scratch copy, an order-independent row digest per type identical before and after;
3. backtest-read parity -- per instrument, the `TradeTick` and snapshot rows a `BacktestNode` actor
   receives equal the stored rows and 24 hourly bounded `catalog.query` reads, by count and digest;
4. candle parity -- the candle store's bars equal the reference fold of the received rows.

Prints a report (or `--json`) and exits 0 when every failing count is 0, 1 when one is not, 2 on
a usage error. A refusal -- a day not closed, a missing catalog, candles directory or store file,
an unreadable plan, a plan instrument without a stored definition, a day file that vanished or
appeared mid-run, an uncreatable scratch directory -- is ledgered at `verification.catalog.refused`
and exits with its message (status 1); any other exception is a crash, ledgered at the same site
and re-raised.

This root is the one module that wires `verification.subject` (Nautilus, the archive's
consolidation and the snapshot codec, driven as the code under test) beside the oracle's raw reads
(`tests/test_boundaries.py`). It is read-only outside its scratch directory: the catalog and the
candle store are never written.

Environment: `CATALOG_PATH` (unless `--catalog`); `CANDLES_DIR` (unless `--candles`; default
`<catalog>/../candles`, the store `candles_<venue lowercased>.db`); `VERIFY_DATA_DIR` (unless
`--raw-dir`: the scratch default `<VERIFY_DATA_DIR>/scratch`, unless `--scratch-dir`);
`BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG` (the plan); `ERROR_LEDGER_DIR` /
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
from pathlib import Path

from observability import error_ledger

from verification.application import sites
from verification.application.catalog import CatalogInputs
from verification.application.catalog import Unjudgeable
from verification.application.catalog import check_day
from verification.application.catalog import render_text
from verification.application.catalog import report_json
from verification.application.conservation import DAY_SETTLE_NS
from verification.application.conservation import is_closed
from verification.conservation import Refused
from verification.conservation import candle_store
from verification.conservation import directory
from verification.conservation import plan_of
from verification.domain.catalog_check import CatalogDayReport
from verification.domain.conservation import NS_PER_HOUR
from verification.domain.subscriptions import VENUES
from verification.infrastructure.catalog_scan import CandleStoreFile
from verification.infrastructure.catalog_scan import CatalogScan
from verification.subject.backtest_probe import BacktestReads
from verification.subject.consolidation import Rehearsals
from verification.subject.consolidation import WriterFactory
from verification.subject.consolidation import maintenance_writer
from verification.subject.nautilus_reads import NautilusReads


def scratch_dir(args: argparse.Namespace, environ: Mapping[str, str], catalog: Path) -> Path:
    """
    Return the rehearsal's scratch directory, created when absent; `Refused` if it cannot be, or
    if it lies inside the catalog root: the rehearsal writes there, and the live catalog is never
    written.
    """
    if args.scratch_dir:
        path = Path(args.scratch_dir)
    else:
        raw = args.raw_dir or environ.get("VERIFY_DATA_DIR")
        if not raw:
            raise Refused("VERIFY_DATA_DIR is required for the scratch default (or --scratch-dir)")
        path = Path(raw) / "scratch"
    if path.resolve().is_relative_to(catalog.resolve()):
        raise Refused(f"scratch directory {path} lies inside the catalog {catalog}")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise Refused(f"scratch directory {path} cannot be created: {exc}") from exc
    return path


def inputs_of(
    args: argparse.Namespace, environ: Mapping[str, str], writer: WriterFactory
) -> CatalogInputs:
    """Wire the oracle's reads and the subject over the arguments (`Refused`: an input absent)."""
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    store = candle_store(args, environ, catalog)
    return CatalogInputs(
        catalog=catalog,
        raw=CatalogScan(catalog),
        raw_of=CatalogScan,
        nautilus=NautilusReads(catalog),
        backtest=BacktestReads(catalog),
        rehearsal=Rehearsals(scratch_dir(args, environ, catalog), writer),
        candles=CandleStoreFile(store),
    )


def run(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    now_ns: int,
    writer: WriterFactory = maintenance_writer,
) -> CatalogDayReport:
    """Build the inputs and judge the day; any input that cannot be trusted is `Refused`."""
    if not is_closed(args.day, now_ns):
        raise Refused(
            f"day not closed: {args.day} has not ended in UTC and settled "
            f"({DAY_SETTLE_NS // NS_PER_HOUR} h after midnight)"
        )
    inputs = inputs_of(args, environ, writer)
    plan = plan_of(args.venue, environ)
    try:
        return check_day(plan, args.day, inputs, now_ns)
    except Unjudgeable as exc:
        raise Refused(str(exc)) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.catalog")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, type=date.fromisoformat, help="UTC YYYY-MM-DD")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    parser.add_argument("--raw-dir", help="the recording root (default: VERIFY_DATA_DIR)")
    parser.add_argument("--candles", help="the candle store directory (default: CANDLES_DIR)")
    parser.add_argument("--scratch-dir", help="the rehearsal's scratch (default: <raw>/scratch)")
    return parser


def main(
    argv: list[str] | None = None,
    clock: Callable[[], int] = time.time_ns,
    writer: WriterFactory = maintenance_writer,
) -> int:
    """
    Run the tool; return the exit status (0 pass, 1 fail). A refusal raises `SystemExit`; any
    other exception is ledgered at the same site as a crash, then re-raised (DATA-07). `clock`
    (epoch ns) decides whether the day is closed and clocks the nightly stage; `writer` is the
    rehearsal's catalog writer (the archive's own, or a planted one in a test).
    """
    args = _parser().parse_args(argv)
    error_ledger.start(
        service=error_ledger.job_service("verify_catalog", "verification", args.venue)
    )
    subject = f"{args.venue} {args.day}"
    try:
        report = run(args, os.environ, clock(), writer)
    except Refused as exc:
        error_ledger.record(sites.CATALOG_REFUSED, f"{subject}: {exc}", exc)
        raise SystemExit(f"catalog refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.CATALOG_REFUSED, f"{subject}: crashed: {exc!r}", exc)
        raise
    text = json.dumps(report_json(report), indent=2) if args.json else render_text(report)
    print(text)
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
