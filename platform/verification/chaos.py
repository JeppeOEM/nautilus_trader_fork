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
The fault injection tool's composition root (Story 31.10):

    python3 -m verification.chaos --scenario S --venue BYBIT|HYPERLIQUID [--container NAME]
        [--log PATH] [--json] [--raw-dir DIR] [--catalog DIR]
    python3 -m verification.chaos --evaluate --venue V [--json] [--log PATH] [--raw-dir DIR]
        [--catalog DIR] [--errors-dir DIR]

Run mode injects one scenario on the verify stack and undoes it (`docs/DATA_DICTIONARY.md` §1.23):
`sigkill_flush` (`docker kill -s KILL` at :02.25 of a minute, then `docker start`),
`graceful_restart` (`docker restart`), `pause_15s`/`pause_45s` (`docker pause`, sleep, `docker
unpause`), `network_cut` (60 s of `sudo -n iptables`/`ip6tables` OUTPUT DROP rules for uid 1000 to
the venue hosts' addresses, comment `verify-chaos`), `catalog_readonly` (`chmod a-w` on the venue's
catalog leaves from :55 to :10 of the next minute, their exact modes restored) and `redis_stop`
(`docker stop verify-redis`, 60 s, `docker start`; logged for both venues). `deploy` rebuilds and
recreates both verify collectors (`docker compose -p verify ... up -d --no-deps --build`), logged
so its window is excluded, never judged. Each run appends a `start` line before its fault and an
`end` line after its undo (every command with its return code) to
`<VERIFY_DATA_DIR>/chaos/scenarios.jsonl` (`--log`), fsync'd. Exit 0 when every command returned 0;
1 when one did not (a failed fault command is ledgered at `verification.chaos.fault_failed`, a
failed undo at `verification.chaos.undo_failed`) or the run is refused.

Evaluate mode judges every logged scenario of the venue whose window
`[fault_start - 90 s, fault_end + 180 s)` has settled (its last hour ended, 10 min past its end):
the windowed conservation (`verification.conservation --start/--end`'s rules), the second reasons,
the collector ledger sites in the window (`<ERROR_LEDGER_DIR>/<venue>_collector.jsonl`), the
backfilled and ledgered-unrecoverable trades, against the expected-outcome table
(`verification.domain.chaos`). Unsettled or open scenarios are listed `pending`. Exit 0 iff at
least one scenario was evaluated and every evaluated one passed.

A refusal -- no `data/.verify-stack` beside the catalog and the raw root (run mode), a target that
is not the verify stack's, an open last run or one that ended less than 6 min ago, `sudo -n` not
permitted (network_cut; no rule is inserted), a malformed log line, a missing directory or
collector ledger, an unreadable window input -- is ledgered at `verification.chaos.refused` and
exits with its message (status 1); a usage error exits 2; any other exception is a crash, ledgered
at the same site and re-raised.

The reference side never imports the code it checks (`tests/test_boundaries.py`): nothing of
`capture`, and the collectors are only ever touched through docker, iptables and chmod.

Known limit (network_cut): the rules block the addresses `getaddrinfo` gives the venue hosts at
the run; a CDN host (Bybit's) can answer with others, and a collector connected to one of those
is not cut, which the scenario's missing `stale` reason then shows (a loud mismatch, never a false
pass). The rules match uid 1000, which on the verify host is also the operator's own. Upgrade
path: cut the collector container's established connections (`ss -tnp` in its network namespace).
An interrupted tool (Ctrl-C, SIGTERM, SIGHUP) ends the fault there, undoes it and writes the `end`
line, ledgered at `verification.chaos.fault_failed` (a failed undo at `undo_failed` too), and
exits 1; every later signal is ignored until the tool exits, so a second one cannot cut the undo
short. An `end` line that cannot be written after the undo is ledgered at `fault_failed` with
what the undo did, and exits 1. A collector of another compose project, or a paper bot of any,
running on the host refuses `network_cut`.
Known limit (an interrupted tool): a SIGKILL of the tool itself between its `start` and `end`
lines leaves the run open, and every later run is refused until the operator has checked the
target and appended the `end` line by hand. Upgrade path: a `--close` that re-runs the undo.

Environment: `VERIFY_DATA_DIR` (unless `--raw-dir`), `CATALOG_PATH` (unless `--catalog`),
`ERROR_LEDGER_DIR` (unless `--errors-dir`; evaluate reads the collectors' ledgers there, and this
tool's own ledger goes there), `BYBIT_COLLECTOR_CONFIG` / `HYPERLIQUID_COLLECTOR_CONFIG` (the
plan: its instruments and environment), `VERIFY_REDIS_PORT` / `VERIFY_DATA_API_PORT` /
`VERIFY_DOZZLE_PORT` (a deploy's compose ports; defaults 26379/29100/28080, the Makefile's).
"""

import argparse
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from collections.abc import Mapping
from pathlib import Path

from observability import error_ledger

from verification.application import sites
from verification.application.chaos import COLLECTOR_CONTAINERS
from verification.application.chaos import ChaosHost
from verification.application.chaos import ChaosRefused
from verification.application.chaos import EndLineLost
from verification.application.chaos import EvaluationInputs
from verification.application.chaos import EvaluationReport
from verification.application.chaos import Resolve
from verification.application.chaos import Runner
from verification.application.chaos import RunOutcome
from verification.application.chaos import RunRequest
from verification.application.chaos import Sleep
from verification.application.chaos import evaluate_venue
from verification.application.chaos import render_text
from verification.application.chaos import report_json
from verification.application.chaos import run_scenario
from verification.application.conservation import Inputs
from verification.application.conservation import iso_second
from verification.application.conservation import window_hours
from verification.conservation import Refused
from verification.conservation import directory
from verification.conservation import plan_of
from verification.domain.chaos import END
from verification.domain.chaos import RUNNABLE
from verification.domain.chaos import parse_log
from verification.domain.subscriptions import VENUES
from verification.infrastructure.catalog_reader import CoverageFiles
from verification.infrastructure.catalog_reader import ParquetArchive
from verification.infrastructure.chaos_io import CatalogLeaves
from verification.infrastructure.chaos_io import LedgerFiles
from verification.infrastructure.chaos_io import ScenarioLogFile
from verification.infrastructure.chaos_io import resolve_host
from verification.infrastructure.chaos_io import subprocess_runner
from verification.infrastructure.raw_store import RawReader
from verification.infrastructure.raw_store import TruncatedTail


VERIFY_MARKER = ".verify-stack"  # `make verify-up` marks the data/ it runs on (Makefile)
PLATFORM_DIR = Path(__file__).resolve().parent.parent
_COMPOSE_PORTS = (
    ("REDIS_PORT", "VERIFY_REDIS_PORT", "26379"),
    ("DATA_API_PORT", "VERIFY_DATA_API_PORT", "29100"),
    ("DOZZLE_PORT", "VERIFY_DOZZLE_PORT", "28080"),
)


def compose_command(environ: Mapping[str, str]) -> tuple[str, ...]:
    """
    Return the verify stack's `up -d --no-deps --build` command, as the Makefile's `VERIFY_COMPOSE`
    runs it (its ports passed explicitly: the live stack's may be exported), services to append.
    """
    ports = tuple(f"{name}={environ.get(env, default)}" for name, env, default in _COMPOSE_PORTS)
    base, override = PLATFORM_DIR / "docker-compose.yml", PLATFORM_DIR / "docker-compose.verify.yml"
    files = ("-f", str(base), "-f", str(override))
    return (
        "env",
        *ports,
        "docker",
        "compose",
        "-p",
        "verify",
        *files,
        "up",
        "-d",
        "--no-deps",
        "--build",
    )


def _require_marker(*roots: Path) -> None:
    for root in roots:
        marker = root.parent / VERIFY_MARKER
        if not marker.is_file():
            raise Refused(f"{root.parent} is not a verify stack's data/ (no {marker.name})")


def _log_file(args: argparse.Namespace, raw_root: Path) -> ScenarioLogFile:
    return ScenarioLogFile(Path(args.log) if args.log else raw_root / "chaos" / "scenarios.jsonl")


def _roots(args: argparse.Namespace, environ: Mapping[str, str]) -> tuple[Path, Path]:
    raw_root = directory(args.raw_dir or environ.get("VERIFY_DATA_DIR"), "VERIFY_DATA_DIR")
    catalog = directory(args.catalog or environ.get("CATALOG_PATH"), "CATALOG_PATH")
    return raw_root, catalog


def run_fault(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    effects: tuple[Runner, Callable[[], int], Sleep, Resolve],
) -> tuple[RunOutcome, Path]:
    """Build the run from the arguments and environment, then inject and undo the scenario."""
    raw_root, catalog = _roots(args, environ)
    _require_marker(raw_root, catalog)
    runner, clock, sleep, resolve = effects
    log = _log_file(args, raw_root)
    host = ChaosHost(runner, clock, sleep, log, CatalogLeaves(catalog), resolve)
    container = args.container or COLLECTOR_CONTAINERS[args.venue]
    plan = plan_of(args.venue, environ)
    request = RunRequest(args.scenario, args.venue, container, plan, compose_command(environ))
    return run_scenario(request, host), log.path


def evaluate(args: argparse.Namespace, environ: Mapping[str, str], now_ns: int) -> EvaluationReport:
    """Judge every logged scenario of the venue whose window has settled."""
    raw_root, catalog = _roots(args, environ)
    errors = directory(args.errors_dir or environ.get("ERROR_LEDGER_DIR"), "ERROR_LEDGER_DIR")
    ledger = LedgerFiles(errors)
    if not ledger.has(args.venue):
        raise Refused(f"no {args.venue.lower()}_collector ledger in {errors}")
    log = _log_file(args, raw_root)
    if not log.path.is_file():
        raise Refused(f"scenario log {log.path} does not exist")
    plan = plan_of(args.venue, environ)

    def conservation_inputs(start_ns: int, end_ns: int) -> Inputs:
        return Inputs(
            reference=RawReader(raw_root, args.venue, window_hours(start_ns, end_ns)),
            archive=ParquetArchive(catalog),
            coverage=CoverageFiles(catalog, args.venue),
        )

    inputs = EvaluationInputs(plan, conservation_inputs, ledger)
    return evaluate_venue(parse_log(log.lines()), inputs, now_ns)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m verification.chaos")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--scenario", choices=RUNNABLE, help="inject one scenario and undo it")
    mode.add_argument("--evaluate", action="store_true", help="judge the settled scenarios")
    parser.add_argument("--venue", required=True, choices=VENUES)
    parser.add_argument("--container", help="the collector container (default: the venue's)")
    parser.add_argument("--log", help="the scenario log (default: <VERIFY_DATA_DIR>/chaos/...)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--raw-dir", help="the recording root (default: VERIFY_DATA_DIR)")
    parser.add_argument("--catalog", help="the catalog root (default: CATALOG_PATH)")
    parser.add_argument("--errors-dir", help="the ledger directory (default: ERROR_LEDGER_DIR)")
    return parser


def _run_text(outcome: RunOutcome, log: Path) -> str:
    run = outcome.run
    span = f"{iso_second(run.fault_start_ns)}..{iso_second(run.fault_end_ns or 0)}"
    failed = [*outcome.fault_failures, *outcome.undo_failures]
    verdict = "FAILED: " + "; ".join(failed) if failed else "ok"
    return (
        f"chaos {run.scenario} {'+'.join(run.venues)} on {run.target}: fault {span}, "
        f"{len(run.commands)} commands, {verdict}; logged to {log}"
    )


def _ledger_failures(outcome: RunOutcome) -> None:
    subject = f"{outcome.run.scenario} {'+'.join(outcome.run.venues)}"
    if outcome.fault_failures:
        detail = f"{subject}: fault command failed: {'; '.join(outcome.fault_failures)}"
        error_ledger.record(sites.CHAOS_FAULT_FAILED, detail)
    if outcome.undo_failures:
        detail = f"{subject}: UNDO FAILED, the fault may still be in place: "
        error_ledger.record(sites.CHAOS_UNDO_FAILED, detail + "; ".join(outcome.undo_failures))


def _undo_state(outcome: RunOutcome) -> str:
    if outcome.undo_failures:
        return "UNDO FAILED, the fault may still be in place"
    return "the fault was undone"


def _dispatch(
    args: argparse.Namespace,
    clock: Callable[[], int],
    effects: tuple[Runner, Callable[[], int], Sleep, Resolve],
) -> int:
    if args.evaluate:
        report = evaluate(args, os.environ, clock())
        print(json.dumps(report_json(report), indent=2) if args.json else render_text(report))
        return 0 if report.passed else 1
    outcome, log = run_fault(args, os.environ, effects)
    _ledger_failures(outcome)
    print(json.dumps(outcome.run.log_line(END), indent=2) if args.json else _run_text(outcome, log))
    if outcome.interrupted:
        error_ledger.record(
            sites.CHAOS_FAULT_FAILED,
            f"{args.venue} {args.scenario}: interrupted ({outcome.interrupted}) mid-fault; "
            f"{_undo_state(outcome)}; the scenario log's end line records each undo",
        )
        raise SystemExit(f"chaos interrupted: {outcome.interrupted}")
    return 1 if outcome.failed else 0


# A terminal closed or a `kill` mid-run must end the fault and unwind through the undo and the
# `end` line like Ctrl-C does, never leave a collector paused, a leaf read-only or a rule in.
_UNWINDING_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


def _interrupt(signum: int, _frame: object) -> None:
    """
    End the fault (`execute` catches the interrupt and undoes it), then ignore every later signal:
    a second Ctrl-C or SIGTERM must not cut the undo short. Only a SIGKILL can, the known limit.
    """
    for unwinding in _UNWINDING_SIGNALS:
        signal.signal(unwinding, signal.SIG_IGN)
    raise KeyboardInterrupt(signal.Signals(signum).name)


def main(
    argv: list[str] | None = None,
    clock: Callable[[], int] = time.time_ns,
    sleep: Sleep = time.sleep,
    runner: Runner = subprocess_runner,
    resolve: Resolve = resolve_host,
) -> int:
    """
    Run the tool; return the exit status. A refusal raises `SystemExit` (status 1); any other
    exception is ledgered as a crash and re-raised (DATA-07). `clock`, `sleep`, `runner` and
    `resolve` are the effects a test replaces, so no test executes docker, sudo or a DNS query.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    if args.evaluate and args.container:
        parser.error("--container goes with --scenario")
    error_ledger.start(service=error_ledger.job_service("verify_chaos", "verification", args.venue))
    subject = f"{args.venue} {args.scenario or 'evaluate'}"
    # Only a run has a fault to undo; an evaluation keeps the default (terminate) behaviour.
    unwinding = _UNWINDING_SIGNALS if args.scenario else ()
    previous = {signum: signal.signal(signum, _interrupt) for signum in unwinding}
    try:
        return _dispatch(args, clock, (runner, clock, sleep, resolve))
    except EndLineLost as exc:
        _ledger_failures(exc.outcome)
        error_ledger.record(
            sites.CHAOS_FAULT_FAILED,
            f"{subject}: {exc}; {_undo_state(exc.outcome)}; the run stays open in the scenario "
            "log -- check the target, then append its end line by hand",
            exc,
        )
        raise SystemExit(f"chaos failed: {exc}") from exc
    except KeyboardInterrupt as exc:
        # A fault interrupted during its apply or hold returns from `execute` (above): this one
        # came before any fault was applied, during an evaluation, or -- the one narrow seam --
        # between two undo commands, which leaves the run's `start` line without its `end`.
        error_ledger.record(
            sites.CHAOS_FAULT_FAILED,
            f"{subject}: interrupted ({exc or 'SIGINT'}) outside a fault's hold; if the "
            "scenario log's last line is a start, check the target and append its end by hand",
            exc,
        )
        raise SystemExit(f"chaos interrupted: {exc or 'SIGINT'}") from exc
    except (Refused, ChaosRefused, TruncatedTail, ValueError, OSError) as exc:
        # MalformedLine is a ValueError; a vanished file or a failed resolve an OSError.
        error_ledger.record(sites.CHAOS_REFUSED, f"{subject}: {exc}", exc)
        raise SystemExit(f"chaos refused: {exc}") from exc
    except Exception as exc:
        error_ledger.record(sites.CHAOS_REFUSED, f"{subject}: crashed: {exc!r}", exc)
        raise
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    sys.exit(main())
