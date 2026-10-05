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
One venue's nightly maintenance for one closed day (story 22.13; `make nightly VENUE=... DAY=...`
by hand; the `archive` service runs the same chain, `steps`, every night -- Story 25.1b).

Usage:
    python -m archive.nightly --catalog /app/catalog --candles-dir /app/candles_dir \\
        --venue BYBIT --day 2026-09-20 [--dydx-plan /app/dydx_collector/config.toml]

Runs, in order, each as its own subprocess (so each step's memory is returned to the OS before the
next one starts -- MEM-01; the job never runs inside a collector):

    rebuild_seconds --apply --candles-dir --result-file -> consolidate_catalog --apply --days 2
        -> build_candles (`python -m candles.rebuild`) --day --workers 1
        -> compare_klines --rebuilt-by RUN_ID
        -> prune_catalog --apply --trade-retention-days 7 [--dydx-plan]
        -> verify_day --result-file <saga scratch>/verify_result.json

`verify_day` (Story 31.11, `archive.verify_day`) runs the independent verifiers over the day and
writes its verdict for the scheduler's `verification_days`. It is last so it can never gate the
pruning or any later step, and it exits 0 or 2, never 1, so it can never fail the saga or hold the
watermark; without reference recorders (`VERIFY_DATA_DIR` unset, as in production) it is
`"no reference data"`, exit 0. Known limit: an earlier FAILED step stops the saga before it, so
that venue-day gets no verdict that night (its absence from `verification_days` shows it) until
the retry after the failure is fixed. Upgrade path: run it after a failure too, as a step of its
own in the scheduler's chains.

The saga itself -- findings vs failures, the rebuild proof carried from the rebuild to the
reconcile, the `nightly.<step>` ledger entries -- is `archive.application.nightly`'s docstring.
Consolidation is limited to the last 2 closed days (an old refused day is the standalone
`make consolidate`'s to report) and `build_candles` runs one worker (MEM-01 on the 2 vCPU host).
`--dydx-plan` is forwarded to the prune step for `--venue DYDX` (dropped-instrument and per-coin
delta retention); for another venue it is accepted and not forwarded (`make nightly` passes it for
every venue). Without it a DYDX run still runs the chain, ledgers `nightly.dydx_plan_missing` and
ends as findings (exit 2) unless a step failed -- so a pre-25.1 command line without the flag
keeps working, loudly (the old module path's shim itself was removed in Story 25.3).

A missing catalog stops the saga before any step (`archive.catalog_missing`, exit 1).

Known limit: plan retention runs only as the saga's last step, so an earlier FAILED step (e.g. a
standing consolidate refusal) also postpones dYdX dropped-instrument and delta retention -- and the
trade retention -- until that failure is fixed. Upgrade path: a standalone
`archive.prune_catalog --dydx-plan` job in the `archive` service's chains
(`archive/scheduler.py`), or a saga that runs retention after a failure that does not concern
the files it would delete. Ends with one summary line
(per-step outcome and wall seconds, peak child RSS, the run id). Exit code: the failing step's,
else 2 when any step had findings, else 0.
"""

import argparse
import logging
import resource
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from candles.application.rebuild import parse_date_ns
from candles.infrastructure.sqlite_store import db_path_for_venue
from observability import error_ledger

from archive.application.catalog_check import catalog_missing
from archive.application.nightly import Step
from archive.application.nightly import StepRunner
from archive.application.nightly import exit_code
from archive.application.nightly import run_steps
from archive.application.nightly import summary_line
from archive.application.rebuild_day import day_text
from archive.domain.reconciliation import VENUES


logger = logging.getLogger(__name__)

_TRADE_RETENTION_DAYS = 7
VERDICT_FILE = "verify_result.json"


def steps(
    catalog: str,
    candles_dir: str,
    venue: str,
    day: str,
    result_file: str,
    dydx_plan: str | None = None,
) -> list[Step]:
    """
    Build the nightly chain for one venue-day, in order.

    A step's name is its ledger suffix and its place in the frozen summary line, so it is not the
    module it runs: `build_candles` keeps its name while running `candles.rebuild` (Story 24.1).
    """
    db = db_path_for_venue(candles_dir, venue)
    plan = ["--dydx-plan", dydx_plan] if venue == "DYDX" and dydx_plan else []
    # Beside the rebuild's result, in the saga's own scratch directory: transport, never persisted.
    verdict_file = str(Path(result_file).with_name(VERDICT_FILE))

    def module(dotted: str, *args: str) -> list[str]:
        """Build one step's argv from a full dotted path (the chain spans two contexts)."""
        return [sys.executable, "-m", dotted, *args]

    return [
        Step(
            "rebuild_seconds",
            module(
                "archive.rebuild_seconds",
                "--catalog",
                catalog,
                "--day",
                day,
                "--venue",
                venue,
                "--apply",
                "--candles-dir",
                candles_dir,
                "--result-file",
                result_file,
            ),
            result_file=result_file,
        ),
        Step(
            "consolidate_catalog",
            module(
                "archive.consolidate_catalog",
                "--catalog",
                catalog,
                "--apply",
                "--venue",
                venue,
                "--days",
                "2",
            ),
        ),
        Step(
            "build_candles",
            module(
                "candles.rebuild",
                "--catalog",
                catalog,
                "--db",
                db,
                "--day",
                day,
                "--venue",
                venue,
                "--workers",
                "1",
            ),
        ),
        Step(
            "compare_klines",
            module(
                "archive.compare_klines",
                "--catalog",
                catalog,
                "--db",
                db,
                "--venue",
                venue,
                "--day",
                day,
            ),
            needs_proof=True,
        ),
        Step(
            "prune_catalog",
            module(
                "archive.prune_catalog",
                "--catalog",
                catalog,
                "--apply",
                "--trade-retention-days",
                str(_TRADE_RETENTION_DAYS),
                "--candles-dir",
                candles_dir,
                "--venue",
                venue,
                *plan,
            ),
        ),
        Step(
            "verify_day",
            module(
                "archive.verify_day",
                "--catalog",
                catalog,
                "--candles-dir",
                candles_dir,
                "--venue",
                venue,
                "--day",
                day,
                "--result-file",
                verdict_file,
            ),
            verdict_file=verdict_file,
        ),
    ]


def subprocess_runner(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode  # noqa: S603 (our own module argv)


def peak_child_rss_mb() -> float:
    # ru_maxrss is KiB on Linux (the collector image): the largest single child so far.
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024


def main(argv: list[str] | None = None, runner: StepRunner = subprocess_runner) -> int:
    """CLI entry point; returns the exit code (see the module docstring)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--candles-dir", required=True)
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, help="YYYY-MM-DD (UTC), a closed day")
    parser.add_argument(
        "--dydx-plan", help="the dYdX collection plan (DYDX: enables its plan retention)"
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # A manual `make nightly` is its own process too (the scheduler runs the chain in-process):
    # its own durable file, so its refusals are not stdout-only (Story 31.8).
    error_ledger.start(service=error_ledger.job_service("nightly", "archive", args.venue))
    day = day_text(parse_date_ns(args.day))  # the canonical YYYY-MM-DD every step and proof uses
    if catalog_missing("nightly", args.catalog):
        return 1
    plan_missing = args.venue == "DYDX" and not args.dydx_plan
    if plan_missing:
        error_ledger.record(
            "nightly.dydx_plan_missing",
            f"DYDX {day}: no --dydx-plan; dropped-instrument and delta retention not applied",
        )
    run_id = uuid.uuid4().hex
    logger.info("nightly %s %s: run id %s", args.venue, day, run_id)
    with tempfile.TemporaryDirectory(prefix="nightly-") as scratch:
        result_file = str(Path(scratch) / "rebuild_result.json")
        chain = steps(args.catalog, args.candles_dir, args.venue, day, result_file, args.dydx_plan)
        results = run_steps(chain, runner, run_id, args.venue, day)
    peak = peak_child_rss_mb()
    logger.info("%s", summary_line(args.venue, day, run_id, results, peak, plan_missing))
    return exit_code(results, plan_missing)


if __name__ == "__main__":
    raise SystemExit(main())
