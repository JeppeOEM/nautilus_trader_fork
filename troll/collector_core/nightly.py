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
One venue's nightly maintenance for one closed day (story 22.13; `make nightly VENUE=... DAY=...`).

Usage:
    python -m collector_core.nightly --catalog /app/catalog --candles-dir /app/candles_dir \\
        --venue BYBIT --day 2026-09-20

Runs, in order, each as its own subprocess (so each step's memory is returned to the OS before the
next one starts -- MEM-01; the job never runs inside a collector):

    rebuild_seconds --apply -> consolidate_catalog --apply --days 2 -> build_candles --day
        --workers 1 -> compare_klines -> prune_catalog --apply --trade-retention-days 7

A step's exit 2 means "findings": it finished, but some instruments were refused (rebuild) or
mismatched / could not be compared (compare) -- all ledgered, and a mismatch or error keeps that
day's trades from being pruned. The chain continues past findings, since one instrument's standing
problem must not halt the venue's maintenance every night. Any other non-zero exit is a run-level
failure and stops the chain. Findings and failures are recorded as `nightly.<step>` in the error
ledger. Consolidation is limited to the last 2 closed days (an old refused day is the standalone
`make consolidate`'s to report) and `build_candles` runs one worker (MEM-01 on the 2 vCPU host).
Ends with one summary line (per-step outcome and wall seconds, peak child RSS). Exit code: the
failing step's, else 2 when any step had findings, else 0.
"""

import argparse
import logging
import resource
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

from ml_signals import error_ledger

from collector_core.compare_klines import VENUES


logger = logging.getLogger(__name__)

_FINDINGS = 2
_TRADE_RETENTION_DAYS = 7


@dataclass(frozen=True)
class Step:
    """One nightly step: its name (the ledger suffix) and its command line."""

    name: str
    argv: list[str]


@dataclass
class StepResult:
    """How one step ended."""

    name: str
    code: int
    seconds: float

    def outcome(self) -> str:
        if self.code == 0:
            return "ok"
        return "findings" if self.code == _FINDINGS else "FAILED"


StepRunner = Callable[[list[str]], int]


def steps(catalog: str, candles_dir: str, venue: str, day: str) -> list[Step]:
    """Build the nightly chain for one venue-day, in order."""
    db = f"{candles_dir}/candles_{venue.lower()}.db"

    def module(name: str, *args: str) -> list[str]:
        return [sys.executable, "-m", f"collector_core.{name}", *args]

    return [
        Step(
            "rebuild_seconds",
            module(
                "rebuild_seconds", "--catalog", catalog, "--day", day, "--venue", venue, "--apply"
            ),
        ),
        Step(
            "consolidate_catalog",
            module(
                "consolidate_catalog",
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
                "build_candles",
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
                "compare_klines", "--catalog", catalog, "--db", db, "--venue", venue, "--day", day
            ),
        ),
        Step(
            "prune_catalog",
            module(
                "prune_catalog",
                "--catalog",
                catalog,
                "--apply",
                "--trade-retention-days",
                str(_TRADE_RETENTION_DAYS),
                "--candles-dir",
                candles_dir,
                "--venue",
                venue,
            ),
        ),
    ]


def subprocess_runner(argv: list[str]) -> int:
    return subprocess.run(argv, check=False).returncode  # noqa: S603 (our own module argv)


def peak_child_rss_mb() -> float:
    # ru_maxrss is KiB on Linux (the collector image): the largest single child so far.
    return resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024


def run_steps(chain: list[Step], runner: StepRunner) -> list[StepResult]:
    """Run the chain, stopping after the first failed step (a step's findings, exit 2, continue)."""
    results = []
    for step in chain:
        started = time.monotonic()
        code = runner(step.argv)
        result = StepResult(step.name, code, time.monotonic() - started)
        results.append(result)
        if result.outcome() == "FAILED":
            error_ledger.record(f"nightly.{step.name}", f"exit {code}; later steps not run")
            break
        if result.outcome() == "findings":
            error_ledger.record(
                f"nightly.{step.name}", "findings: per-instrument refusals/mismatches (see above)"
            )
    return results


def exit_code(results: list[StepResult]) -> int:
    failed = [r for r in results if r.outcome() == "FAILED"]
    if failed:
        return failed[0].code
    return _FINDINGS if any(r.outcome() == "findings" for r in results) else 0


def summary_line(venue: str, day: str, results: list[StepResult], peak_mb: float) -> str:
    parts = ", ".join(f"{r.name} {r.outcome()} {r.seconds:.1f}s" for r in results)
    code = exit_code(results)
    overall = "ok" if code == 0 else ("findings" if code == _FINDINGS else "FAILED")
    return f"nightly {venue} {day}: {parts}; peak child RSS {peak_mb:.0f} MB; outcome {overall}"


def main(argv: list[str] | None = None, runner: StepRunner = subprocess_runner) -> int:
    """CLI entry point; returns the exit code (see the module docstring)."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--candles-dir", required=True)
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, help="YYYY-MM-DD (UTC), a closed day")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    results = run_steps(steps(args.catalog, args.candles_dir, args.venue, args.day), runner)
    logger.info("%s", summary_line(args.venue, args.day, results, peak_child_rss_mb()))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
