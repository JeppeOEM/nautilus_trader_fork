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
Fault injection's ports and orchestration (Story 31.10): inject one scenario on the verify stack
and undo it (`run_scenario`), and judge every settled scenario window of a venue
(`evaluate_venue`, over the windowed conservation and the collector's ledger).
`verification.domain.chaos` holds the rules.

Every effect is injected -- the command runner, the clock, the sleeper, the scenario log, the
catalog leaf permissions, the name resolver and the ledger reader -- so the tests drive every
scenario's commands without executing docker, sudo or chmod.

Invariants:
- a fault is undone whatever happens after its first command (`execute`'s `finally`), and its
  `end` line is written after the undo, with every command's return code -- an undo that failed is
  in that line and makes the run fail (`RunOutcome.undo_failures`); an interrupt mid-fault ends the
  hold and is returned (`RunOutcome.interrupted`), and one mid-undo never skips a later undo;
- no fault starts on a target outside the verify stack: a container must be named `verify-*` and
  carry the compose project label `verify` (`_verify_container`); the scenario log must have no
  open run and its last run must have ended `SCENARIO_SPACING_NS` ago (`refusal`); a network cut
  is refused before any rule is inserted when `sudo -n` may not run iptables.
"""

from collections.abc import Callable
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import replace
from types import MappingProxyType
from typing import Any
from typing import Protocol
from urllib.parse import urlsplit

from kernel.venue_http import bybit_url
from kernel.venue_http import bybit_ws_url
from kernel.venue_http import hyperliquid_info_url
from kernel.venue_http import hyperliquid_ws_url

from verification.application.conservation import Inputs
from verification.application.conservation import conserve_window
from verification.application.conservation import iso_second
from verification.application.conservation import window_settled
from verification.domain.chaos import CATALOG_READONLY
from verification.domain.chaos import DEPLOY
from verification.domain.chaos import END
from verification.domain.chaos import GRACEFUL_RESTART
from verification.domain.chaos import KILL_OFFSET_NS
from verification.domain.chaos import KILL_SECOND
from verification.domain.chaos import NETWORK_CUT
from verification.domain.chaos import OUTAGE_NS
from verification.domain.chaos import PAUSE_NS
from verification.domain.chaos import READONLY_HOLD_NS
from verification.domain.chaos import READONLY_SECOND
from verification.domain.chaos import REDIS_STOP
from verification.domain.chaos import SIGKILL_FLUSH
from verification.domain.chaos import START
from verification.domain.chaos import CommandRecord
from verification.domain.chaos import Observed
from verification.domain.chaos import ScenarioRun
from verification.domain.chaos import evaluate
from verification.domain.chaos import evaluation_window
from verification.domain.chaos import expected
from verification.domain.chaos import next_at
from verification.domain.chaos import parse_log
from verification.domain.chaos import refusal
from verification.domain.conservation import NS_PER_S
from verification.domain.conservation import DayReport
from verification.domain.plan_file import RecordingPlan
from verification.domain.subscriptions import BYBIT
from verification.domain.subscriptions import HYPERLIQUID
from verification.domain.subscriptions import VENUES


# The verify stack's containers (`docker-compose.verify.yml`) and the compose label every one of
# them carries (`docker compose -p verify`).
COLLECTOR_CONTAINERS = MappingProxyType(
    {BYBIT: "verify-bybit-collector", HYPERLIQUID: "verify-hyperliquid-collector"}
)
COLLECTOR_SERVICES = MappingProxyType(
    {BYBIT: "bybit_collector", HYPERLIQUID: "hyperliquid_collector"}
)
REDIS_CONTAINER = "verify-redis"
CONTAINER_PREFIX = "verify-"
COMPOSE_PROJECT = "verify"
_PROJECT_LABEL = '{{index .Config.Labels "com.docker.compose.project"}}'
# The collectors run as uid 1000 (`docker-compose.yml`'s `user: "1000:1000"`), the reference
# recorders as 1001 (`docker-compose.verify.yml`): an OUTPUT rule matching uid 1000 cuts only the
# collectors (and any host process of that uid) and leaves the reference recording.
COLLECTOR_UID = "1000"
RULE_COMMENT = "verify-chaos"
# Every collector service of `docker-compose.yml` (dYdX's is `collector`): a running one outside
# the verify project would match the uid-1000 rule too, so `network_cut` refuses while one runs.
_COLLECTOR_SERVICE_NAMES = frozenset({"collector", *COLLECTOR_SERVICES.values()})
# The paper bots (`live-paper`, uid 1000, host networking) stream the same venues: a cut would
# silently gap their signal logs, the verify fleet's being Story 31.9's parity input, so
# `network_cut` refuses while one runs in any project.
_BOT_SERVICE_NAMES = frozenset({"live-paper"})
_PS_FORMAT = (
    '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}'
)
_WRITE_BITS = 0o222
_OUTPUT_CHARS = 400  # the tail of a command's output kept on its log line

Runner = Callable[[Sequence[str]], CommandRecord]
Clock = Callable[[], int]
Sleep = Callable[[float], None]
Resolve = Callable[[str], tuple[str, ...]]


class ChaosRefused(Exception):
    """A run or evaluation that must not proceed: its message is ledgered and becomes the exit."""


class EndLineLost(Exception):
    """
    The `end` line could not be written after the undo ran: the run stays open in the log, so
    every later run is refused until the operator has checked the target and appended it by hand.
    `outcome` holds what the undo did.
    """

    def __init__(self, outcome: "RunOutcome", cause: OSError) -> None:
        super().__init__(f"the end line was not written after the undo: {cause}")
        self.outcome = outcome


class ScenarioLog(Protocol):
    """
    `<VERIFY_DATA_DIR>/chaos/scenarios.jsonl`. Invariant: append-only, and `append` returns only
    once the line is durable (fsync'd), so a `start` is on disk before its fault is applied and
    the log can never claim less than happened; `lines` yields every line with its `file:line`.
    """

    def lines(self) -> Iterable[tuple[str, str]]: ...

    def append(self, line: Mapping[str, Any]) -> None: ...


class LeafPermissions(Protocol):
    """
    The catalog's leaf directories of one venue (`<catalog>/data/*/<iid>`, iid ending `.<VENUE>`)
    and their permission bits. Invariant: `leaves` names only directories of that venue's
    instruments, so `catalog_readonly` never touches another venue's or a shared table's.
    """

    def leaves(self, venue: str) -> tuple[str, ...]: ...

    def mode(self, path: str) -> int: ...

    def set_mode(self, path: str, mode: int) -> None: ...


class CollectorLedger(Protocol):
    """A venue collector's durable error ledger: the sites it recorded in `[start_ns, end_ns)`."""

    def sites(self, venue: str, start_ns: int, end_ns: int) -> frozenset[str]: ...


@dataclass(frozen=True)
class ChaosHost:
    """The injected effects a run executes through (the composition root wires the real ones)."""

    runner: Runner
    clock: Clock
    sleep: Sleep
    log: ScenarioLog
    permissions: LeafPermissions
    resolve: Resolve


@dataclass(frozen=True)
class RunRequest:
    """
    One run: the scenario, the venue (both, for `redis_stop` and `deploy`), the collector
    container, the plan (its environment names the venue hosts a network cut blocks) and the
    compose command a `deploy` runs (`docker compose -p verify -f ... up -d --no-deps --build`,
    without the services).
    """

    scenario: str
    venue: str
    container: str
    plan: RecordingPlan
    compose: tuple[str, ...] = ()


@dataclass(frozen=True)
class Step:
    """One fault command and its undo, each labelled with the argv its log entry shows."""

    label: tuple[str, ...]
    apply: Callable[[], CommandRecord]
    undo_label: tuple[str, ...] | None = None
    undo: Callable[[], CommandRecord] | None = None
    # Undo even when the command failed: true where the undo is harmless on an unchanged target
    # (`docker start` of a running container), false where it would fail (deleting a rule never
    # inserted, restoring a mode never read).
    undo_after_failure: bool = True


@dataclass(frozen=True)
class Fault:
    """A scenario ready to inject: its target, its steps, when it starts and how long it holds."""

    scenario: str
    venues: tuple[str, ...]
    target: str
    steps: tuple[Step, ...]
    at: tuple[int, int] | None = None  # (second of the minute, offset ns) the fault starts at
    hold_ns: int = 0


@dataclass(frozen=True)
class RunOutcome:
    """A finished run: its log record, and the fault and undo commands that did not return 0."""

    run: ScenarioRun
    fault_failures: tuple[str, ...]
    undo_failures: tuple[str, ...]
    # The signal that ended the fault early ("" when it ran its course): the undo still ran.
    interrupted: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.fault_failures or self.undo_failures)


def _tail(text: str) -> str:
    return text[-_OUTPUT_CHARS:]


def _docker(runner: Runner, *argv: str) -> Step:
    command = ("docker", *argv)
    return Step(command, lambda: runner(command))


def _undone(step: Step, runner: Runner, *argv: str) -> Step:
    command = ("docker", *argv)
    return replace(step, undo_label=command, undo=lambda: runner(command))


def _verify_container(name: str, runner: Runner) -> None:
    """Refuse a container outside the verify stack: by name, then by its compose project label."""
    if not name.startswith(CONTAINER_PREFIX):
        raise ChaosRefused(f"container {name!r} is not the verify stack's (`{CONTAINER_PREFIX}*`)")
    found = runner(("docker", "inspect", "-f", _PROJECT_LABEL, name))
    project = found.output.strip()
    if found.returncode != 0 or project != COMPOSE_PROJECT:
        raise ChaosRefused(
            f"container {name} is not in compose project {COMPOSE_PROJECT!r} "
            f"(docker inspect -> {found.returncode}: {project[:200]!r})"
        )


def _container_fault(request: RunRequest, host: ChaosHost) -> Fault:
    """`sigkill_flush`, `graceful_restart`, `pause_15s`/`pause_45s`: the venue's collector."""
    name, runner = request.container, host.runner
    _verify_container(name, runner)
    if request.venue.lower() not in name or "collector" not in name:
        # The log records the venue: a fault on another container would be judged as this one's.
        raise ChaosRefused(f"container {name} is not {request.venue}'s collector")
    venues = (request.venue,)
    if request.scenario == SIGKILL_FLUSH:
        kill = _undone(_docker(runner, "kill", "-s", "KILL", name), runner, "start", name)
        return Fault(request.scenario, venues, name, (kill,), at=(KILL_SECOND, KILL_OFFSET_NS))
    if request.scenario == GRACEFUL_RESTART:
        restart = _undone(_docker(runner, "restart", name), runner, "start", name)
        return Fault(request.scenario, venues, name, (restart,))
    pause = _undone(_docker(runner, "pause", name), runner, "unpause", name)
    return Fault(request.scenario, venues, name, (pause,), hold_ns=PAUSE_NS[request.scenario])


def _redis_fault(host: ChaosHost) -> Fault:
    _verify_container(REDIS_CONTAINER, host.runner)
    stop = _undone(
        _docker(host.runner, "stop", REDIS_CONTAINER), host.runner, "start", REDIS_CONTAINER
    )
    return Fault(REDIS_STOP, VENUES, REDIS_CONTAINER, (stop,), hold_ns=OUTAGE_NS)


def _deploy_fault(request: RunRequest, host: ChaosHost) -> Fault:
    """Rebuild and recreate both verify collectors (`--no-deps`), logged to exclude its window."""
    if not request.compose:
        raise ChaosRefused("deploy needs the verify stack's compose command")
    for venue in VENUES:
        _verify_container(COLLECTOR_CONTAINERS[venue], host.runner)
    command = (*request.compose, *(COLLECTOR_SERVICES[v] for v in VENUES))
    step = Step(command, lambda: host.runner(command))
    target = f"compose project {COMPOSE_PROJECT}: {', '.join(COLLECTOR_SERVICES.values())}"
    return Fault(DEPLOY, VENUES, target, (step,))


def venue_hosts(plan: RecordingPlan) -> tuple[str, ...]:
    """Return the host names of the venue's REST and WebSocket URLs (`kernel.venue_http`)."""
    if plan.venue == BYBIT:
        urls = (bybit_url(plan.environment, "/"), bybit_ws_url(plan.environment, "linear"))
    else:
        urls = (hyperliquid_info_url(plan.environment), hyperliquid_ws_url(plan.environment))
    return tuple(sorted({str(urlsplit(url).hostname) for url in urls}))


def _iptables(ip: str) -> str:
    return "ip6tables" if ":" in ip else "iptables"


def rule_argv(action: str, ip: str) -> tuple[str, ...]:
    """Return the `sudo -n` argv inserting (`-I`) or deleting (`-D`) the collectors' DROP rule."""
    return (
        *("sudo", "-n", _iptables(ip), "-w", action, "OUTPUT"),
        *("-m", "owner", "--uid-owner", COLLECTOR_UID, "-d", ip),
        *("-m", "comment", "--comment", RULE_COMMENT, "-j", "DROP"),
    )


def _rule_step(ip: str, runner: Runner) -> Step:
    insert, delete = rule_argv("-I", ip), rule_argv("-D", ip)
    return Step(insert, lambda: runner(insert), delete, lambda: runner(delete), False)


def _refuse_other_collectors(runner: Runner) -> None:
    """
    Refuse while a collector of another compose project, or a paper bot of any project, runs on
    this host: it runs as the same uid, so the rule would cut it too (the production stack on the
    dev box, the verify stack's `verify-live-paper`).

    Known limit: any other process of uid 1000 is cut from the venue's addresses for the 60 s as
    well -- the operator's own session, and the verify archive's kline fetches, whose failures it
    ledgers and retries itself. Upgrade path: run the verify collectors under a uid of their own.
    """
    listed = runner(("docker", "ps", "--format", _PS_FORMAT))
    if listed.returncode != 0:
        raise ChaosRefused(f"docker ps -> {listed.returncode}: {listed.output.strip()[:200]!r}")
    rows = (f"{line}\t\t".split("\t")[:3] for line in listed.output.splitlines() if line)
    others = sorted(
        name
        for name, project, service in rows
        if (project != COMPOSE_PROJECT and service in _COLLECTOR_SERVICE_NAMES)
        or service in _BOT_SERVICE_NAMES
    )
    if others:
        raise ChaosRefused(
            f"containers other than the verify collectors run as uid {COLLECTOR_UID} and "
            f"would be cut too (stop them first): {', '.join(others)}"
        )


def _network_fault(request: RunRequest, host: ChaosHost) -> Fault:
    """Drop the collectors' packets to the venue's hosts; refused unless `sudo -n` may do it."""
    _refuse_other_collectors(host.runner)
    ips = sorted({ip for name in venue_hosts(request.plan) for ip in host.resolve(name)})
    if not ips:
        raise ChaosRefused(f"no address resolved for {venue_hosts(request.plan)}")
    for tool in sorted({_iptables(ip) for ip in ips}):
        probe = host.runner(("sudo", "-n", tool, "-w", "-S", "OUTPUT"))
        if probe.returncode != 0:
            raise ChaosRefused(
                f"`sudo -n {tool}` is not permitted ({probe.returncode}: "
                f"{probe.output.strip()[:200]!r}): no rule inserted -- network_cut is an "
                "operator action"
            )
    steps = tuple(_rule_step(ip, host.runner) for ip in ips)
    target = f"uid {COLLECTOR_UID} -> {', '.join(ips)}"
    return Fault(NETWORK_CUT, (request.venue,), target, steps, hold_ns=OUTAGE_NS)


def _leaf_step(path: str, permissions: LeafPermissions) -> Step:
    prior: dict[str, int] = {}

    def freeze() -> CommandRecord:
        prior["mode"] = permissions.mode(path)
        permissions.set_mode(path, prior["mode"] & ~_WRITE_BITS)
        return CommandRecord(("chmod", "a-w", path), 0)

    def restore() -> CommandRecord:
        if "mode" not in prior:  # interrupted before `freeze` read it: nothing was changed
            return CommandRecord(("chmod", "<unchanged>", path), 0)
        permissions.set_mode(path, prior["mode"])
        return CommandRecord(("chmod", f"{prior['mode']:o}", path), 0)

    return Step(("chmod", "a-w", path), freeze, ("chmod", "<prior mode>", path), restore, False)


def _readonly_fault(request: RunRequest, host: ChaosHost) -> Fault:
    """Take write permission off the venue's catalog leaves across exactly one :02 flush."""
    leaves = host.permissions.leaves(request.venue)
    if not leaves:
        raise ChaosRefused(f"no catalog leaf directory of {request.venue} to make read-only")
    steps = tuple(_leaf_step(path, host.permissions) for path in leaves)
    target = f"{len(leaves)} catalog leaves of {request.venue}"
    return Fault(
        CATALOG_READONLY,
        (request.venue,),
        target,
        steps,
        at=(READONLY_SECOND, 0),
        hold_ns=READONLY_HOLD_NS,
    )


def build_fault(request: RunRequest, host: ChaosHost) -> Fault:
    """Return the scenario's fault after its target checks (`ChaosRefused` on any)."""
    if request.scenario == REDIS_STOP:
        return _redis_fault(host)
    if request.scenario == DEPLOY:
        return _deploy_fault(request, host)
    if request.scenario == NETWORK_CUT:
        return _network_fault(request, host)
    if request.scenario == CATALOG_READONLY:
        return _readonly_fault(request, host)
    return _container_fault(request, host)


def _attempt(label: tuple[str, ...], action: Callable[[], CommandRecord]) -> CommandRecord:
    """Run one command; an exception becomes its record (returncode -1), never an unlogged loss."""
    try:
        record = action()
    except Exception as exc:
        return CommandRecord(label, -1, _tail(repr(exc)))
    return replace(record, output=_tail(record.output))


class _Progress:
    """
    What a run did so far. Invariant: a step is recorded as applied (record None) before its
    command runs, so a step interrupted mid-command is still undone -- its state is unknown.
    """

    def __init__(self) -> None:
        self.applied: list[tuple[Step, CommandRecord | None]] = []
        self.undone: list[CommandRecord] = []
        self.interrupted = ""  # the signal that ended the fault early, if one did


def _apply(fault: Fault, progress: _Progress, sleep: Sleep) -> None:
    for step in fault.steps:
        progress.applied.append((step, None))  # attempted, before it can be interrupted
        progress.applied[-1] = (step, _attempt(step.label, step.apply))
    sleep(fault.hold_ns / NS_PER_S)


def _undo(progress: _Progress) -> None:
    """
    Undo every attempted step, newest first; an interrupted one is undone (its state unknown). An
    interrupt during an undo command records that undo as failed (-1, its state unknown) and the
    remaining undos still run: a second Ctrl-C or SIGTERM never leaves a fault in place.
    """
    for step, record in reversed(progress.applied):
        if step.undo is None or step.undo_label is None:
            continue
        failed = record is not None and record.returncode != 0
        if failed and not step.undo_after_failure:
            continue
        try:
            progress.undone.append(_attempt(step.undo_label, step.undo))
        except KeyboardInterrupt as exc:
            progress.interrupted = progress.interrupted or str(exc) or "SIGINT"
            progress.undone.append(CommandRecord(step.undo_label, -1, "interrupted mid-undo"))


def execute(fault: Fault, host: ChaosHost) -> RunOutcome:
    """
    Wait for the fault's start, write its `start` line, apply every step, hold, then -- whatever
    happened -- undo and write its `end` line with every command's return code. An interrupt
    (`KeyboardInterrupt`) during the apply or the hold ends the fault there and is returned in
    `RunOutcome.interrupted`; one before the `start` line propagates (no fault was applied).
    `EndLineLost` when the `end` line cannot be written.
    """
    if fault.at is not None:
        now_ns = host.clock()
        host.sleep((next_at(now_ns, *fault.at) - now_ns) / NS_PER_S)
    run = ScenarioRun(fault.scenario, fault.venues, fault.target, host.clock(), None, ())
    host.log.append(run.log_line(START))
    progress = _Progress()
    try:
        _apply(fault, progress, host.sleep)
    except KeyboardInterrupt as exc:
        progress.interrupted = str(exc) or "SIGINT"
    finally:
        _undo(progress)
    applied = tuple(record for _, record in progress.applied if record is not None)
    run = replace(run, fault_end_ns=host.clock(), commands=(*applied, *progress.undone))
    outcome = RunOutcome(
        run,
        replace(run, commands=applied).failed_commands(),
        replace(run, commands=tuple(progress.undone)).failed_commands(),
        progress.interrupted,
    )
    try:
        host.log.append(run.log_line(END))
    except OSError as exc:
        raise EndLineLost(outcome, exc) from exc
    return outcome


def run_scenario(request: RunRequest, host: ChaosHost) -> RunOutcome:
    """Refuse per the log's state and the target checks, then inject the fault and undo it."""
    reason = refusal(parse_log(host.log.lines()), host.clock())
    if reason is not None:
        raise ChaosRefused(reason)
    return execute(build_fault(request, host), host)


# -- evaluation ----------------------------------------------------------------------------------

MATCHED = "pass"
MISMATCHED = "fail"
PENDING = "pending"
EXCLUDED = "excluded"


@dataclass(frozen=True)
class ScenarioVerdict:
    """One scenario on one venue: its window, its status, what differed and what it showed."""

    scenario: str
    venue: str
    fault_start_ns: int
    fault_end_ns: int | None
    window: tuple[str, str] | None
    status: str
    mismatches: tuple[str, ...] = ()
    observed: Observed | None = None
    note: str = ""


@dataclass(frozen=True)
class EvaluationReport:
    """
    A venue's scenarios. It passes only when at least one was evaluated and every evaluated one
    passed; a pending (unsettled or open) scenario is never a pass and never a failure.
    """

    venue: str
    verdicts: tuple[ScenarioVerdict, ...]

    @property
    def evaluated(self) -> int:
        return sum(verdict.status in (MATCHED, MISMATCHED) for verdict in self.verdicts)

    @property
    def passed(self) -> bool:
        judged = [v for v in self.verdicts if v.status in (MATCHED, MISMATCHED)]
        return bool(judged) and all(v.status == MATCHED for v in judged)


def observed_of(report: DayReport, sites: frozenset[str], failed: tuple[str, ...]) -> Observed:
    """Sum a windowed conservation report over its instruments into what the table judges."""
    reasons: dict[str, int] = {}
    for instrument in report.instruments:
        for reason, count in instrument.seconds.explained_by_reason.items():
            reasons[reason] = reasons.get(reason, 0) + count
    trades = [instrument.trades for instrument in report.instruments]
    seconds = [instrument.seconds for instrument in report.instruments]
    return Observed(
        unexplained_trades=sum(t.unexplained for t in trades),
        unexplained_seconds=sum(s.unexplained for s in seconds),
        archived_twice=sum(t.archived_twice for t in trades),
        duplicate_rows=sum(s.duplicate_rows for s in seconds),
        row_and_reason=sum(s.row_and_reason for s in seconds),
        inputs_whole=report.coverage_present and not report.missing_raw_files,
        reasons=dict(sorted(reasons.items())),
        sites=sites,
        backfilled=sum(t.backfilled for t in trades),
        unrecoverable=sum(t.ledgered_unrecoverable for t in trades),
        failed_commands=failed,
    )


@dataclass(frozen=True)
class EvaluationInputs:
    """What an evaluation reads: the plan, the conservation inputs of a window, the ledger."""

    plan: RecordingPlan
    conservation: Callable[[int, int], Inputs]
    ledger: CollectorLedger


def _judge(run: ScenarioRun, inputs: EvaluationInputs, now_ns: int) -> ScenarioVerdict:
    venue = inputs.plan.venue
    base = ScenarioVerdict(run.scenario, venue, run.fault_start_ns, run.fault_end_ns, None, PENDING)
    if run.scenario == DEPLOY:
        return replace(base, status=EXCLUDED, note="a redeploy: excluded, never judged")
    if run.fault_end_ns is None:
        return replace(base, note="still open: a start without an end")
    start, end = evaluation_window(run.fault_start_ns, run.fault_end_ns)
    window = (iso_second(start), iso_second(end))
    if not window_settled(start, end, now_ns):
        return replace(base, window=window, note="window not settled yet")
    report = conserve_window(inputs.plan, start, end, inputs.conservation(start, end))
    sites = inputs.ledger.sites(venue, start, end)
    observed = observed_of(report, sites, run.failed_commands())
    mismatches = evaluate(observed, expected(run.scenario, venue))
    status = MISMATCHED if mismatches else MATCHED
    return replace(base, window=window, status=status, mismatches=mismatches, observed=observed)


def evaluate_venue(
    runs: Sequence[ScenarioRun], inputs: EvaluationInputs, now_ns: int
) -> EvaluationReport:
    """Judge every logged scenario of the plan's venue, oldest first."""
    venue = inputs.plan.venue
    ours = [run for run in runs if venue in run.venues]
    return EvaluationReport(venue, tuple(_judge(run, inputs, now_ns) for run in ours))


def report_json(report: EvaluationReport) -> dict[str, Any]:
    """Return the evaluation as JSON-ready data, with its verdict and evaluated count."""
    return {
        "venue": report.venue,
        "passed": report.passed,
        "evaluated": report.evaluated,
        "scenarios": [_verdict_json(verdict) for verdict in report.verdicts],
    }


def _verdict_json(verdict: ScenarioVerdict) -> dict[str, Any]:
    body = asdict(verdict)
    if body["observed"] is not None:
        body["observed"]["sites"] = sorted(body["observed"]["sites"])
    return body


def _verdict_lines(verdict: ScenarioVerdict) -> list[str]:
    window = f" [{verdict.window[0]}, {verdict.window[1]})" if verdict.window else ""
    head = f"{verdict.scenario:<17} {verdict.status.upper():<8}{window}"
    lines = [head + (f"  ({verdict.note})" if verdict.note else "")]
    observed = verdict.observed
    if observed is not None:
        lines.append(
            f"    unexplained trades {observed.unexplained_trades}, seconds "
            f"{observed.unexplained_seconds}; backfilled {observed.backfilled}, unrecoverable "
            f"{observed.unrecoverable}; reasons {dict(observed.reasons) or 'none'}"
        )
        lines.append(f"    ledger sites: {', '.join(sorted(observed.sites)) or 'none'}")
    lines += [f"    MISMATCH: {mismatch}" for mismatch in verdict.mismatches]
    return lines


def render_text(report: EvaluationReport) -> str:
    """Render the evaluation: the verdict, then each scenario with its counts and mismatches."""
    verdict = "PASS" if report.passed else "FAIL"
    lines = [f"chaos {report.venue}: {verdict} ({report.evaluated} evaluated)"]
    for scenario in report.verdicts:
        lines += _verdict_lines(scenario)
    return "\n".join(lines)
