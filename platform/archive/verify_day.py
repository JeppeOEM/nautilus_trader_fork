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
The nightly saga's last step (Story 31.11): one venue-day judged by the six independent
verifiers, each its own child process, reduced to one verdict per data type.

Usage:
    python -m archive.verify_day --catalog /app/catalog --candles-dir /app/candles_dir \\
        --venue BYBIT --day 2026-09-29 --result-file /tmp/nightly-x/verify_result.json \\
        [--reports-dir DIR]

Environment: `VERIFY_DATA_DIR`, the reference recorders' root (`<root>/raw/<venue>/...`);
`VERIFY_DATA_API_URL` (optional), the data_api whose served bars the candles tool checks; and
everything the tools read themselves (`BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG`,
`ERROR_LEDGER_DIR` / `ERROR_LEDGER_SERVICE`), inherited by each child.

Reference-data gate: a venue without a reference recorder (not BYBIT or HYPERLIQUID), an unset
`VERIFY_DATA_DIR`, or no raw file of the day (`<root>/raw/<venue>/*/<day>T*.jsonl.zst`) writes
`{"venue", "day", "verification": "no reference data", "reason"}`, logs one INFO line and exits
0. Nothing is ledgered: a stack without recorders (production) is not an error.

Otherwise each tool of `verification.domain.verdict.TOOLS` runs, in order, as
`python -m verification.<tool> --venue V --day D --json --catalog C --raw-dir <VERIFY_DATA_DIR>`
(trades adds `--stage rebuilt`: this step runs after the night's rebuild; catalog `--candles X
--scratch-dir <VERIFY_DATA_DIR>/scratch/verify_day`; candles `--candles X` and `--data-api URL`
when `VERIFY_DATA_API_URL` is set, else `--no-served`, a provisional run the reduction never
passes). Each child is bounded by `TOOL_TIMEOUT_S` (50 min, so the six stay under the
scheduler's 360 min step timeout) and killed past it; one that cannot be started is `refused`
and the next still runs. Its stdout is its JSON report, reduced by
`verification.domain.verdict.summarise`; its stderr (a refusal's message) goes to this process's
own. Why children: MEM-01 -- a tool peaks at ~1.6 GB on a partial Bybit day, and each child
returns its memory to the OS before the next starts; the tools also keep their own refusal and
ledger paths. This module imports only the pure `verification.domain.verdict`; the tools are
reached through their command lines (`tests/test_boundaries.py`).

The result file (`--result-file`, written atomically: `write_json_atomic`) is
`{"venue", "day", "verification": "verified" | "findings", "checked_at", "types": {tool: {...}}}`
(`verification.domain.verdict.day_verdict`); the saga carries it to the scheduler's
`verification_days`, an informational record that never gates pruning or a watermark.

Exit: 0 when every type passed (or there is no reference data), 2 (findings) otherwise -- never
1, so a failing verifier, a crash here or an unwritable result can never fail the saga or hold
its watermark. Each non-passed type is one `archive.verify_day` ledger entry (the tool, its
verdict, failing count, failing instruments and missing raw files); an unexpected exception is
ledgered at the same site and gives `"verification": "error"`. The step's own ledger file is
`error_ledger.job_service("verify_day", "archive", venue)`.

Liquidations (Story 33.1): for a venue of `verification.domain.verdict.LIQUIDATION_VENUES` (Bybit),
`python -m verification.liquidations --venue V --day D --json --catalog C` runs as one more child
on *both* paths -- after the six tools, and alone when there is no reference data, since it reads
only the catalog and the coverage record. Its report is reduced by `summarise_liquidations` and
kept under the result key `liquidations` (`{"report": "reported", "applicable", "total",
"matched", "share", "unrecoverable_seconds", "instruments"}`, or `{"report": "refused",
"reason"}`, ledgered at `archive.verify_day`), never in `types` and never in `verification`: a
matched share is a self-check, and a venue without the feed must still be able to verify. It
never changes the exit code.

Known limit: the step runs after the night's consolidation, so the catalog tool's consolidation
rehearsal finds day D already consolidated and reports `not_exercised` (D-116), which is not
failing: the rehearsal is exercised only by a manual `verification.catalog` run before the
nightly. Upgrade path: a catalog step of its own before `consolidate_catalog`.

Known limit: a `run_now` of a day older than `prune_catalog`'s trade retention judges a catalog
whose raw trades are already pruned, so its trades/conservation/catalog types report findings
that are retention, not bad data. Upgrade path: skip with a distinct status past the retention.

Known limit: the step runs on every venue-day of a catch-up too, up to six `TOOL_TIMEOUT_S` each
(a full Bybit day measured ~27 min), all under the maintenance lock, so a long catch-up on a stack
with recorders runs correspondingly longer. Upgrade path: a per-run verification budget.

Where it runs: with reference data, this is a dev-box tool (the verify stack, `make verify-up`),
never a VPS service -- production has no `VERIFY_DATA_DIR` and stops at `no reference data`.
Known limit: wall time is linear in the plan's instrument count, ~7 min per Bybit instrument
(27 min for 4), because each tool judges one instrument at a time and the tools run in sequence;
memory stays flat (~1.5 GB peak, one instrument's working set) and cores do not help. One tool
passes `TOOL_TIMEOUT_S` at about 7 Bybit instruments, the scheduler's `step_timeout_minutes`
(360) at about 50, so the verify stack's plan stays small (`docs/DATA_DICTIONARY.md` §1.24).
Upgrade path: a per-instrument worker pool in the tools, with memory as workers x one instrument.
"""

import argparse
import datetime as dt
import logging
import os
import subprocess
import sys
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from observability import error_ledger
from verification.domain.verdict import LIQUIDATION_VENUES
from verification.domain.verdict import REFUSED
from verification.domain.verdict import TOOLS
from verification.domain.verdict import VERIFIED
from verification.domain.verdict import TypeVerdict
from verification.domain.verdict import day_verdict
from verification.domain.verdict import parse_report
from verification.domain.verdict import summarise
from verification.domain.verdict import summarise_liquidations

from archive.domain.reconciliation import VENUES
from archive.infrastructure.catalog_files import write_json_atomic


logger = logging.getLogger(__name__)

SITE = "archive.verify_day"
FINDINGS = 2
TOOL_TIMEOUT_S = 50 * 60
# The exit code a child killed at `TOOL_TIMEOUT_S` is given (timeout(1)'s, as the scheduler's).
TIMED_OUT = 124
NO_REFERENCE = "no reference data"
ERROR = "error"
# The venues Epic 31's reference recorders cover (`verification.domain.subscriptions`).
REFERENCE_VENUES = ("BYBIT", "HYPERLIQUID")
# The recorder's published raw layout, `<root>/raw/<venue>/<channel>/<YYYY-MM-DDTHH>.jsonl.zst`
# (`verification/infrastructure/raw_store.py`), restated: this root imports no verification I/O.
_RAW_SUFFIX = ".jsonl.zst"
_MISSING_SHOWN = 10

# A child's argv and its timeout -> its exit code and stdout (`TIMED_OUT`, "" once killed).
ToolRunner = Callable[[list[str], float], tuple[int, str]]
Clock = Callable[[], dt.datetime]


def subprocess_runner(argv: list[str], timeout_s: float) -> tuple[int, str]:
    """Run one tool; stdout captured (its report), stderr inherited; killed past `timeout_s`."""
    try:
        done = subprocess.run(  # noqa: S603 (our own module argv)
            argv, check=False, stdout=subprocess.PIPE, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:  # subprocess.run has already killed and reaped it
        return TIMED_OUT, ""
    return done.returncode, done.stdout


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def no_reference(venue: str, day: str, environ: Mapping[str, str]) -> str | None:
    """Why the venue-day has no reference data to judge against, or None when it has some."""
    if venue not in REFERENCE_VENUES:
        return f"{venue} has no reference recorder (only {', '.join(REFERENCE_VENUES)})"
    root = environ.get("VERIFY_DATA_DIR")
    if not root:
        return "VERIFY_DATA_DIR is not set: no reference recorders on this stack"
    raw = Path(root) / "raw" / venue.lower()
    if not any(raw.glob(f"*/{day}T*{_RAW_SUFFIX}")):
        return f"no raw reference file of {day} under {raw}"
    return None


def tool_argv(
    tool: str, args: argparse.Namespace, raw_root: Path, api_url: str | None
) -> list[str]:
    """One tool's command line; `--catalog` and `--raw-dir` explicit, never the child's env."""
    argv = [sys.executable, "-m", f"verification.{tool}", "--venue", args.venue, "--day"]
    argv += [args.day, "--json", "--catalog", args.catalog, "--raw-dir", str(raw_root)]
    if tool == "trades":
        return [*argv, "--stage", "rebuilt"]
    if tool == "catalog":
        scratch = raw_root / "scratch" / "verify_day"
        return [*argv, "--candles", args.candles_dir, "--scratch-dir", str(scratch)]
    if tool == "candles":
        served = ["--data-api", api_url] if api_url else ["--no-served"]
        return [*argv, "--candles", args.candles_dir, *served]
    return argv


def run_tool(
    tool: str, argv: list[str], runner: ToolRunner, reports_dir: Path | None = None
) -> TypeVerdict:
    """
    Run one tool and reduce its report; a child killed at the timeout, or one that could not be
    started (an `OSError`: fork's ENOMEM/EAGAIN under memory pressure), is `refused`, so the other
    tools still run and keep their verdicts. With `reports_dir` the tool's full report is also kept
    there as `<tool>.json` (the evidence behind a verdict, for a by-hand run; the nightly keeps
    only the reduction).
    """
    code, report, why = _child_report(argv, runner, reports_dir, tool)
    return summarise(tool, code, report, reason=why)


def _child_report(
    argv: list[str], runner: ToolRunner, reports_dir: Path | None, name: str
) -> tuple[int, dict[str, Any] | None, str]:
    """Run one child: its exit code, its report (kept in `reports_dir` too) and why it has none."""
    try:
        code, stdout = runner(argv, TOOL_TIMEOUT_S)
    except OSError as exc:
        return 1, None, f"not started: {exc!r}"
    if code == TIMED_OUT and not stdout:
        return code, None, f"killed after {TOOL_TIMEOUT_S} s"
    report = parse_report(stdout)
    if reports_dir is not None and report is not None:
        write_json_atomic(reports_dir / f"{name}.json", dict(report))
    return code, None if report is None else dict(report), ""


def run_liquidations(
    args: argparse.Namespace, runner: ToolRunner, reports_dir: Path | None
) -> dict[str, Any]:
    """
    Return the day's liquidation summary for the result's `liquidations` key (`{}` for a venue without
    the feed): a report beside the verdict, never in it. A refused one is ledgered.
    """
    if args.venue not in LIQUIDATION_VENUES:
        return {}
    argv = [sys.executable, "-m", "verification.liquidations", "--venue", args.venue]
    argv += ["--day", args.day, "--json", "--catalog", args.catalog]
    code, report, why = _child_report(argv, runner, reports_dir, "liquidations")
    summary = summarise_liquidations(code, report, why)
    logger.info("verify_day %s %s liquidations: %s", args.venue, args.day, summary["report"])
    if summary["report"] == REFUSED:
        error_ledger.record(
            SITE, f"{args.venue} {args.day} liquidations: refused ({summary['reason']})"
        )
    return {"liquidations": summary}


def _names(names: tuple[str, ...]) -> str:
    shown = ", ".join(names[:_MISSING_SHOWN])
    return f"{shown}, ... ({len(names)} in all)" if len(names) > _MISSING_SHOWN else shown


def ledger_type(venue: str, day: str, verdict: TypeVerdict) -> None:
    """One `archive.verify_day` entry for a type that did not pass, naming what failed."""
    detail = f"{venue} {day} {verdict.tool}: {verdict.verdict}"
    if verdict.verdict == REFUSED:
        detail += f" ({verdict.reason})"
    else:
        detail += f"; failing {verdict.failing}; inputs missing {verdict.inputs_missing}"
        detail += f"; failing instruments: {', '.join(verdict.failing_instruments) or 'none'}"
    if verdict.missing_raw_files:
        detail += f"; missing raw files: {_names(verdict.missing_raw_files)}"
    error_ledger.record(SITE, detail)


def verify(
    args: argparse.Namespace, environ: Mapping[str, str], runner: ToolRunner, clock: Clock
) -> tuple[dict[str, Any], int]:
    """Judge the venue-day: its result body and exit code (0 verified or no data, else 2)."""
    head = {"venue": args.venue, "day": args.day}
    reports_dir = Path(args.reports_dir) if args.reports_dir else None
    if reports_dir is not None:  # a by-hand run names a fresh directory (an OSError is a crash)
        reports_dir.mkdir(parents=True, exist_ok=True)
    reason = no_reference(args.venue, args.day, environ)
    if reason is not None:
        logger.info("verify_day %s %s: %s (%s)", args.venue, args.day, NO_REFERENCE, reason)
        liquidations = run_liquidations(args, runner, reports_dir)
        return {**head, "verification": NO_REFERENCE, "reason": reason, **liquidations}, 0
    raw_root = Path(environ["VERIFY_DATA_DIR"])
    api_url = environ.get("VERIFY_DATA_API_URL") or None
    types = []
    for tool in TOOLS:
        argv = tool_argv(tool, args, raw_root, api_url)
        verdict = run_tool(tool, argv, runner, reports_dir)
        logger.info("verify_day %s %s %s: %s", args.venue, args.day, tool, verdict.verdict)
        if not verdict.passed:
            ledger_type(args.venue, args.day, verdict)
        types.append(verdict)
    liquidations = run_liquidations(args, runner, reports_dir)
    checked_at = clock().astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = day_verdict(types, checked_at)
    return {**head, **body, **liquidations}, 0 if body["verification"] == VERIFIED else FINDINGS


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--candles-dir", required=True)
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--day", required=True, type=dt.date.fromisoformat, help="YYYY-MM-DD")
    parser.add_argument("--result-file", required=True, help="where the verdict is written")
    parser.add_argument(
        "--reports-dir", help="also keep each tool's full JSON report there (a by-hand run)"
    )
    return parser


def main(
    argv: list[str] | None = None,
    runner: ToolRunner = subprocess_runner,
    clock: Clock = utc_now,
    environ: Mapping[str, str] = os.environ,
) -> int:
    """CLI entry point; returns 0 or 2, never 1 (see the module docstring)."""
    args = _parser().parse_args(argv)
    args.day = args.day.isoformat()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        error_ledger.start(service=error_ledger.job_service("verify_day", "archive", args.venue))
    # An unopenable sink (e.g. an unwritable `ERROR_LEDGER_DIR`) must not exit 1 either: the
    # ledger stays in-memory and every `record` below still logs at ERROR.
    except Exception:
        logger.exception("verify_day %s %s: error ledger not started", args.venue, args.day)
    try:
        body, code = verify(args, environ, runner, clock)
    # Never exit 1: a crash here must not fail the saga or hold its watermark (DATA-07: ledgered).
    except Exception as exc:
        error_ledger.record(SITE, f"{args.venue} {args.day}: crashed: {exc!r}", exc)
        body = {"venue": args.venue, "day": args.day, "verification": ERROR, "reason": repr(exc)}
        code = FINDINGS
    try:
        write_json_atomic(Path(args.result_file), body)
    except OSError as exc:
        error_ledger.record(SITE, f"{args.venue} {args.day}: result not written: {exc!r}", exc)
        return FINDINGS
    return code


if __name__ == "__main__":
    raise SystemExit(main())
