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
`python3 -m verification.chaos` (Story 31.10): every scenario's commands through an injected
runner, clock and sleeper (no test executes docker, sudo or chmod on a real target), the undo on
every path, the refusals, and the evaluation of a scenario window over the conservation tool's
synthetic Bybit day (`test_conservation`'s builders: a real catalog written by
`ParquetDataCatalog.write_data`, raw lines in the recorder's format, a coverage record). Every
comparator gets a planted defect that makes it report a mismatch.
"""

import json
import os
import signal
from collections.abc import Iterable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from observability import error_ledger

from verification import chaos
from verification.application.chaos import ChaosHost
from verification.application.chaos import ChaosRefused
from verification.application.chaos import EndLineLost
from verification.application.chaos import RunRequest
from verification.application.chaos import rule_argv
from verification.application.chaos import run_scenario
from verification.domain.chaos import CATALOG_READONLY
from verification.domain.chaos import END
from verification.domain.chaos import GRACEFUL_RESTART
from verification.domain.chaos import NETWORK_CUT
from verification.domain.chaos import PAUSE_15S
from verification.domain.chaos import PAUSE_45S
from verification.domain.chaos import REDIS_STOP
from verification.domain.chaos import SCENARIO_SPACING_NS
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
from verification.domain.conservation import MalformedLine
from verification.domain.plan_file import RecordingPlan
from verification.infrastructure import chaos_io
from verification.tests.test_conservation import _BTC
from verification.tests.test_conservation import _NOW
from verification.tests.test_conservation import _ROW_SECONDS
from verification.tests.test_conservation import _S0
from verification.tests.test_conservation import _bybit_day
from verification.tests.test_conservation import _clean_runs
from verification.tests.test_conservation import _seconds_line
from verification.tests.test_conservation import _write_jsonl


_S = 1_000_000_000
_MINUTE = 1_789_999_980 * _S  # a whole UTC minute
_T0 = _MINUTE + 17 * _S
_PLAN = RecordingPlan("BYBIT", "mainnet", (_BTC,))
_COLLECTOR = "verify-bybit-collector"


# -- the injected effects ----------------------------------------------------------------------


class _Host:
    """A clock the sleeper advances, a runner and a log that record into one event list."""

    def __init__(self, now_ns: int = _T0, failing: Iterable[str] = (), project: str = "verify"):
        self.now_ns = now_ns
        self.events: list[tuple[str, ...]] = []
        self.lines: list[dict[str, Any]] = []
        self.failing = set(failing)  # a docker/iptables verb (argv[1] or argv[4]) that returns 1
        self.project = project
        self.ps_output = ""  # `docker ps`: no container of another compose project running
        self.modes = {"/c/data/trade_tick/BTCUSDT-LINEAR.BYBIT": 0o775}

    def clock(self) -> int:
        return self.now_ns

    def sleep(self, seconds: float) -> None:
        self.events.append(("sleep", f"{seconds:g}"))
        self.now_ns += round(seconds * _S)

    def runner(self, argv: Sequence[str]) -> CommandRecord:
        command = tuple(argv)
        self.events.append(command)
        if command[1] == "inspect":
            return CommandRecord(command, 0, self.project + "\n")
        if command[1] == "ps":
            return CommandRecord(command, 0, self.ps_output)
        verb = command[4] if command[0] == "sudo" and len(command) > 4 else command[1]
        return CommandRecord(command, 1 if verb in self.failing else 0, "")

    def lines_of(self) -> list[tuple[str, str]]:
        return [(f"log:{n}", json.dumps(line)) for n, line in enumerate(self.lines, start=1)]

    def host(self) -> ChaosHost:
        return ChaosHost(self.runner, self.clock, self.sleep, _Log(self), _Leaves(self), _resolve)


class _Log:
    def __init__(self, fake: _Host) -> None:
        self._fake = fake

    def lines(self) -> list[tuple[str, str]]:
        return self._fake.lines_of()

    def append(self, line: Mapping[str, Any]) -> None:
        self._fake.events.append(("log", str(line["event"])))
        self._fake.lines.append(dict(line))


class _Leaves:
    def __init__(self, fake: _Host) -> None:
        self._fake = fake

    def leaves(self, venue: str) -> tuple[str, ...]:
        return tuple(p for p in sorted(self._fake.modes) if p.endswith(f".{venue}"))

    def mode(self, path: str) -> int:
        return self._fake.modes[path]

    def set_mode(self, path: str, mode: int) -> None:
        self._fake.events.append(("chmod", f"{mode:o}", path))
        self._fake.modes[path] = mode


def _resolve(host: str) -> tuple[str, ...]:
    return {"api.bybit.com": ("1.2.3.4",), "stream.bybit.com": ("5.6.7.8", "2001:db8::1")}[host]


def _request(scenario: str, container: str = _COLLECTOR) -> RunRequest:
    return RunRequest(scenario, "BYBIT", container, _PLAN, ("env", "docker", "compose", "up"))


def _commands(fake: _Host) -> list[tuple[str, ...]]:
    """Return the events without the container-label checks: what the run did, in order."""
    return [event for event in fake.events if event[:2] != ("docker", "inspect")]


# -- run mode: the commands of each scenario ----------------------------------------------------


def test_sigkill_flush_kills_at_second_two_and_a_quarter_then_starts() -> None:
    fake = _Host()
    run_scenario(_request(SIGKILL_FLUSH), fake.host())
    assert _commands(fake) == [
        ("sleep", "45.25"),  # from :17 to :02.25 of the next minute
        ("log", START),
        ("docker", "kill", "-s", "KILL", _COLLECTOR),
        ("sleep", "0"),
        ("docker", "start", _COLLECTOR),
        ("log", END),
    ]
    assert fake.lines[0]["fault_start_ns"] % (60 * _S) == 2_250_000_000


@pytest.mark.parametrize(
    ("scenario", "fault", "hold", "undo"),
    [
        (GRACEFUL_RESTART, ("docker", "restart", _COLLECTOR), "0", ("docker", "start", _COLLECTOR)),
        (PAUSE_15S, ("docker", "pause", _COLLECTOR), "15", ("docker", "unpause", _COLLECTOR)),
        (PAUSE_45S, ("docker", "pause", _COLLECTOR), "45", ("docker", "unpause", _COLLECTOR)),
    ],
)
def test_a_container_scenario_applies_holds_and_undoes(
    scenario: str, fault: tuple[str, ...], hold: str, undo: tuple[str, ...]
) -> None:
    fake = _Host()
    outcome = run_scenario(_request(scenario), fake.host())
    assert _commands(fake) == [("log", START), fault, ("sleep", hold), undo, ("log", END)]
    assert not outcome.failed
    assert fake.lines[1]["commands"] == [
        {"argv": list(fault), "returncode": 0, "output": ""},
        {"argv": list(undo), "returncode": 0, "output": ""},
    ]


def test_redis_stop_stops_verify_redis_for_both_venues() -> None:
    fake = _Host()
    run_scenario(_request(REDIS_STOP), fake.host())
    assert _commands(fake)[1:4] == [
        ("docker", "stop", "verify-redis"),
        ("sleep", "60"),
        ("docker", "start", "verify-redis"),
    ]
    assert fake.lines[1]["venues"] == ["BYBIT", "HYPERLIQUID"]
    assert ("docker", "inspect", "-f", _label(), "verify-redis") in fake.events


def _label() -> str:
    return '{{index .Config.Labels "com.docker.compose.project"}}'


def test_network_cut_drops_uid_1000_to_every_venue_address_then_removes_the_rules() -> None:
    fake = _Host()
    run_scenario(_request(NETWORK_CUT), fake.host())
    ips = ("1.2.3.4", "2001:db8::1", "5.6.7.8")
    assert _commands(fake) == [
        ("docker", "ps", "--format", _PS),
        ("sudo", "-n", "ip6tables", "-w", "-S", "OUTPUT"),
        ("sudo", "-n", "iptables", "-w", "-S", "OUTPUT"),
        ("log", START),
        *(rule_argv("-I", ip) for ip in ips),
        ("sleep", "60"),
        *(rule_argv("-D", ip) for ip in reversed(ips)),
        ("log", END),
    ]
    assert rule_argv("-I", "1.2.3.4") == (
        *("sudo", "-n", "iptables", "-w", "-I", "OUTPUT", "-m", "owner", "--uid-owner", "1000"),
        *("-d", "1.2.3.4", "-m", "comment", "--comment", "verify-chaos", "-j", "DROP"),
    )


def test_catalog_readonly_freezes_the_venue_leaves_across_one_flush_and_restores_modes() -> None:
    fake = _Host()
    fake.modes["/c/data/trade_tick/BTC.HYPERLIQUID"] = 0o755  # another venue's leaf: untouched
    leaf = "/c/data/trade_tick/BTCUSDT-LINEAR.BYBIT"
    run_scenario(_request(CATALOG_READONLY), fake.host())
    assert _commands(fake) == [
        ("sleep", "38"),  # from :17 to :55
        ("log", START),
        ("chmod", "555", leaf),
        ("sleep", "15"),  # to :10 of the next minute
        ("chmod", "775", leaf),
        ("log", END),
    ]
    assert fake.modes == {leaf: 0o775, "/c/data/trade_tick/BTC.HYPERLIQUID": 0o755}


def test_deploy_rebuilds_both_verify_collectors() -> None:
    fake = _Host()
    run_scenario(_request("deploy"), fake.host())
    command = ("env", "docker", "compose", "up", "bybit_collector", "hyperliquid_collector")
    assert _commands(fake) == [("log", START), command, ("sleep", "0"), ("log", END)]
    assert fake.lines[0]["venues"] == ["BYBIT", "HYPERLIQUID"]


def test_the_compose_command_is_the_verify_projects_no_deps_rebuild() -> None:
    command = chaos.compose_command({"VERIFY_REDIS_PORT": "1"})
    assert command[:4] == ("env", "REDIS_PORT=1", "DATA_API_PORT=29100", "DOZZLE_PORT=28080")
    assert command[4:8] == ("docker", "compose", "-p", "verify")
    assert command[-4:] == ("up", "-d", "--no-deps", "--build")


# -- run mode: undo on every path ---------------------------------------------------------------


def test_a_failed_undo_is_in_the_end_line_and_fails_the_run() -> None:
    fake = _Host(failing={"unpause"})
    outcome = run_scenario(_request(PAUSE_15S), fake.host())
    assert outcome.undo_failures == (f"docker unpause {_COLLECTOR} -> 1",)
    assert outcome.fault_failures == ()
    assert fake.lines[1]["commands"][1]["returncode"] == 1


def test_an_interrupted_hold_still_undoes_and_writes_the_end_line() -> None:
    fake = _Host()

    def interrupted(seconds: float) -> None:
        raise KeyboardInterrupt("SIGTERM")

    outcome = run_scenario(_request(PAUSE_45S), replace(fake.host(), sleep=interrupted))
    assert outcome.interrupted == "SIGTERM"
    assert outcome.undo_failures == ()
    assert _commands(fake)[-2:] == [("docker", "unpause", _COLLECTOR), ("log", END)]


def test_an_interrupt_mid_undo_never_skips_a_later_undo() -> None:
    fake = _Host()

    def second_signal(argv: Sequence[str]) -> CommandRecord:
        if "-D" in argv and argv[-7] == "5.6.7.8":  # the first of three deletes (newest first)
            raise KeyboardInterrupt("SIGTERM")
        return fake.runner(argv)

    outcome = run_scenario(_request(NETWORK_CUT), replace(fake.host(), runner=second_signal))
    deleted = [event[-7] for event in fake.events if "-D" in event]
    assert deleted == ["2001:db8::1", "1.2.3.4"]  # newest first; the interrupted one skipped
    assert outcome.interrupted == "SIGTERM"
    assert outcome.undo_failures == (" ".join(rule_argv("-D", "5.6.7.8")) + " -> -1",)
    assert fake.lines[-1]["event"] == END


def test_a_leaf_interrupted_before_its_mode_was_read_is_left_unchanged() -> None:
    fake = _Host()
    leaves = _Leaves(fake)

    def interrupted_read(path: str) -> int:
        raise KeyboardInterrupt("SIGINT")

    leaves.mode = interrupted_read  # type: ignore[method-assign]
    host = replace(fake.host(), permissions=leaves)
    outcome = run_scenario(_request(CATALOG_READONLY), host)
    assert outcome.interrupted == "SIGINT"
    assert outcome.undo_failures == ()
    assert fake.modes == {"/c/data/trade_tick/BTCUSDT-LINEAR.BYBIT": 0o775}


def test_an_end_line_that_cannot_be_written_carries_what_the_undo_did() -> None:
    fake = _Host()
    log = _Log(fake)
    append = log.append

    def disk_full(line: Mapping[str, Any]) -> None:
        if line["event"] == END:
            raise OSError(28, "No space left on device")
        append(line)

    log.append = disk_full  # type: ignore[method-assign]
    with pytest.raises(EndLineLost, match="No space left") as lost:
        run_scenario(_request(PAUSE_15S), replace(fake.host(), log=log))
    assert ("docker", "unpause", _COLLECTOR) in fake.events
    assert lost.value.outcome.undo_failures == ()


def test_a_command_that_raises_is_recorded_and_still_undone() -> None:
    fake = _Host()

    def broken(argv: Sequence[str]) -> CommandRecord:
        if argv[1] == "pause":
            raise OSError("docker: not found")
        return fake.runner(argv)

    outcome = run_scenario(_request(PAUSE_15S), replace(fake.host(), runner=broken))
    assert outcome.fault_failures == (f"docker pause {_COLLECTOR} -> -1",)
    assert _commands(fake)[-2:] == [("docker", "unpause", _COLLECTOR), ("log", END)]


def test_a_rule_that_failed_to_insert_is_not_deleted() -> None:
    fake = _Host(failing={"-I"})
    outcome = run_scenario(_request(NETWORK_CUT), fake.host())
    assert len(outcome.fault_failures) == 3
    assert not [event for event in fake.events if "-D" in event]


# -- run mode: refusals -------------------------------------------------------------------------


def _open_log(fake: _Host, end_ns: int | None) -> None:
    run = ScenarioRun(PAUSE_15S, ("BYBIT",), _COLLECTOR, _T0 - 400 * _S, None, ())
    fake.lines.append(run.log_line(START))
    if end_ns is not None:
        fake.lines.append(replace(run, fault_end_ns=end_ns).log_line(END))


@pytest.mark.parametrize(
    ("end_ns", "why"),
    [(None, "still open"), (_T0 - SCENARIO_SPACING_NS + _S, "spaced")],
)
def test_an_open_or_too_recent_scenario_refuses_before_any_fault(
    end_ns: int | None, why: str
) -> None:
    fake = _Host()
    _open_log(fake, end_ns)
    with pytest.raises(ChaosRefused, match=why):
        run_scenario(_request(PAUSE_15S), fake.host())
    assert fake.events == []


def test_a_scenario_spaced_long_enough_runs() -> None:
    fake = _Host()
    _open_log(fake, _T0 - SCENARIO_SPACING_NS)
    run_scenario(_request(PAUSE_15S), fake.host())
    assert ("docker", "pause", _COLLECTOR) in fake.events


_PS = '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Label "com.docker.compose.service"}}'


def test_network_cut_is_refused_while_a_collector_of_another_project_runs() -> None:
    fake = _Host()
    fake.ps_output = (
        "verify-bybit-collector\tverify\tbybit_collector\n"
        "platform-redis-1\tplatform\tredis\n"
        "bybit-collector\tplatform\tbybit_collector\n"
    )
    with pytest.raises(
        ChaosRefused, match=r"would be cut too \(stop them first\): bybit-collector$"
    ):
        run_scenario(_request(NETWORK_CUT), fake.host())
    assert [event for event in fake.events if event[0] == "sudo"] == []
    assert fake.lines == []


def test_network_cut_is_refused_while_a_paper_bot_runs_even_in_the_verify_stack() -> None:
    fake = _Host()
    fake.ps_output = (
        "verify-bybit-collector\tverify\tbybit_collector\nverify-live-paper\tverify\tlive-paper\n"
    )
    with pytest.raises(ChaosRefused, match=r"would be cut too .*: verify-live-paper$"):
        run_scenario(_request(NETWORK_CUT), fake.host())
    assert fake.lines == []


def test_a_container_fault_on_another_venues_collector_is_refused() -> None:
    fake = _Host()
    with pytest.raises(ChaosRefused, match="not BYBIT's collector"):
        run_scenario(_request(PAUSE_15S, "verify-hyperliquid-collector"), fake.host())
    assert fake.lines == []


def test_network_cut_without_sudo_is_refused_and_inserts_no_rule() -> None:
    fake = _Host(failing={"-S"})
    with pytest.raises(ChaosRefused, match="sudo -n"):
        run_scenario(_request(NETWORK_CUT), fake.host())
    assert not [event for event in fake.events if "-I" in event]
    assert fake.lines == []


@pytest.mark.parametrize(
    ("container", "project", "why"),
    [("bybit-collector", "verify", "not the verify stack's"), (_COLLECTOR, "live", "project")],
)
def test_a_container_outside_the_verify_stack_is_refused(
    container: str, project: str, why: str
) -> None:
    fake = _Host(project=project)
    with pytest.raises(ChaosRefused, match=why):
        run_scenario(_request(PAUSE_15S, container), fake.host())
    assert _commands(fake) == []


# -- the domain rules ---------------------------------------------------------------------------


def test_next_at_finds_the_next_second_of_a_minute() -> None:
    minute = 1_790_000_040 * _S  # a whole minute
    assert next_at(minute + 1 * _S, 2, 250_000_000) == minute + 2_250_000_000
    assert next_at(minute + 3 * _S, 2, 250_000_000) == minute + 62_250_000_000
    assert next_at(minute + 55 * _S, 55) == minute + 55 * _S


def test_the_evaluation_window_is_whole_seconds_around_the_fault() -> None:
    start, end = evaluation_window(100 * _S + 700, 200 * _S + 1)
    assert (start, end) == (10 * _S, 381 * _S)


def test_the_log_pairs_start_and_end_and_keeps_the_open_one() -> None:
    fake = _Host()
    _open_log(fake, _T0)
    _open_log(fake, None)
    runs = parse_log(fake.lines_of())
    assert [run.fault_end_ns for run in runs] == [_T0, None]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda lines: lines[:1] + lines[:1],  # a start while one is open
        lambda lines: lines[1:],  # an end without its start
        lambda lines: [{**lines[0], "extra": 1}],
        lambda lines: [{**lines[0], "scenario": "unplug"}],
        lambda lines: [lines[0], {**lines[1], "fault_start_ns": 1}],  # an end of another run
    ],
)
def test_a_malformed_scenario_log_is_refused(mutate: Any) -> None:
    fake = _Host()
    _open_log(fake, _T0)
    fake.lines = mutate(fake.lines)
    with pytest.raises(MalformedLine):
        parse_log(fake.lines_of())


def test_refusal_names_nothing_for_an_empty_log() -> None:
    assert refusal([], _T0) is None


def _observed(**changes: Any) -> Observed:
    clean = Observed(
        unexplained_trades=0,
        unexplained_seconds=0,
        archived_twice=0,
        duplicate_rows=0,
        row_and_reason=0,
        inputs_whole=True,
        reasons={"restart": 12, "stale": 3},
        sites=frozenset({"collector.restart_gap", "collector.trade_backfill"}),
        backfilled=4,
        unrecoverable=0,
    )
    return replace(clean, **changes)


def test_a_window_matching_its_row_has_no_mismatch() -> None:
    assert evaluate(_observed(), expected(SIGKILL_FLUSH, "BYBIT")) == ()


@pytest.mark.parametrize(
    ("scenario", "venue", "changes", "mismatch"),
    [
        (SIGKILL_FLUSH, "BYBIT", {"reasons": {"stale": 3}}, "required reason restart absent"),
        (
            REDIS_STOP,
            "BYBIT",
            {"reasons": {"write_failed": 1}, "sites": frozenset({"collector.snapshot_publish"})},
            "forbidden reason write_failed present",
        ),
        (
            CATALOG_READONLY,
            "BYBIT",
            {"reasons": {"write_failed": 60}},
            "required ledger site collector.flush_write absent",
        ),
        (SIGKILL_FLUSH, "BYBIT", {"unexplained_trades": 2}, "unexplained_trades = 2"),
        (SIGKILL_FLUSH, "BYBIT", {"unexplained_seconds": 1}, "unexplained_seconds = 1"),
        (SIGKILL_FLUSH, "BYBIT", {"archived_twice": 1}, "archived_twice = 1"),
        (SIGKILL_FLUSH, "BYBIT", {"duplicate_rows": 1}, "duplicate_rows = 1"),
        (SIGKILL_FLUSH, "BYBIT", {"row_and_reason": 1}, "row_and_reason = 1"),
        (SIGKILL_FLUSH, "BYBIT", {"inputs_whole": False}, "inputs incomplete"),
        (SIGKILL_FLUSH, "BYBIT", {"failed_commands": ("docker start x -> 1",)}, "command failed"),
        (NETWORK_CUT, "BYBIT", {"backfilled": 0}, "backfilled > 0"),
        (NETWORK_CUT, "HYPERLIQUID", {"unrecoverable": 0}, "unrecoverable"),
    ],
)
def test_each_planted_defect_is_a_mismatch(
    scenario: str, venue: str, changes: dict[str, Any], mismatch: str
) -> None:
    observed = _observed(**{"sites": frozenset({"collector.trade_backfill"}), **changes})
    found = evaluate(observed, expected(scenario, venue))
    assert any(mismatch in text for text in found), found


def test_network_cut_expects_backfill_on_bybit_and_unrecoverable_on_hyperliquid() -> None:
    bybit, hyperliquid = expected(NETWORK_CUT, "BYBIT"), expected(NETWORK_CUT, "HYPERLIQUID")
    assert (bybit.backfilled, bybit.unrecoverable) == (True, False)
    assert (hyperliquid.backfilled, hyperliquid.unrecoverable) == (False, True)


# -- evaluate mode, end to end over the conservation tool's synthetic day -----------------------

# The fault of every evaluated scenario below: 10:01:30 .. 10:02:30 of the synthetic day, judged
# over [10:00:00, 10:05:30) -- trade A (10:00:00.5) and both snapshot rows lie inside it.
_FAULT = ((_S0 + 36_090) * _S, (_S0 + 36_150) * _S)


def _ledger_line(site: str, ts_ns: int) -> dict[str, object]:
    return {
        "ts_ns": ts_ns,
        "service": "bybit_collector",
        "pid": 1,
        "site": site,
        "detail": "",
        "exc_type": None,
        "suppressed": 0,
    }


def _stack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str = REDIS_STOP,
    ledger: Sequence[tuple[str, int]] = (("collector.snapshot_publish", _FAULT[0] + 10 * _S),),
    coverage: Sequence[Mapping[str, object]] | None = None,
) -> list[str]:
    """Write the synthetic day, one logged scenario and the collector ledger; return the argv."""
    _bybit_day(tmp_path, monkeypatch, coverage=coverage)
    venues = ["BYBIT", "HYPERLIQUID"] if scenario == REDIS_STOP else ["BYBIT"]
    run = ScenarioRun(scenario, tuple(venues), "t", _FAULT[0], _FAULT[1], ())
    log = tmp_path / "verify" / "chaos" / "scenarios.jsonl"
    _write_jsonl(log, [run.log_line(START), run.log_line(END)])
    lines = [_ledger_line(site, ts) for site, ts in ledger]
    _write_jsonl(tmp_path / "errors" / "bybit_collector.jsonl", lines)
    return ["--evaluate", "--venue", "BYBIT", "--errors-dir", str(tmp_path / "errors"), "--json"]


def _evaluate(
    argv: list[str], capsys: pytest.CaptureFixture[str], now_ns: int = _NOW
) -> tuple[int, dict[str, Any]]:
    status = chaos.main(argv, clock=lambda: now_ns)
    return status, json.loads(capsys.readouterr().out)


def test_a_clean_scenario_window_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _evaluate(_stack(tmp_path, monkeypatch), capsys)
    (scenario,) = report["scenarios"]
    assert (status, report["passed"], report["evaluated"]) == (0, True, 1)
    assert scenario["window"] == ["2026-09-29T10:00:00Z", "2026-09-29T10:05:30Z"]
    assert scenario["observed"]["reasons"] == {"not_collected": 328}
    assert scenario["mismatches"] == []


def test_a_required_reason_missing_fails_the_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = (("collector.restart_gap", _FAULT[1]), ("collector.trade_backfill", _FAULT[1]))
    argv = _stack(tmp_path, monkeypatch, scenario=SIGKILL_FLUSH, ledger=ledger)
    status, report = _evaluate(argv, capsys)
    assert status == 1
    assert report["scenarios"][0]["mismatches"] == ["required reason restart absent"]


def test_a_forbidden_reason_in_the_window_fails_the_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    second = _S0 + 36_100
    runs = [
        *_clean_runs(_BTC, (*_ROW_SECONDS, second)),
        _seconds_line(_BTC, "write_failed", second, second),
    ]
    status, report = _evaluate(_stack(tmp_path, monkeypatch, coverage=runs), capsys)
    assert status == 1
    assert report["scenarios"][0]["mismatches"] == ["forbidden reason write_failed present"]


def test_a_ledger_line_outside_the_window_does_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (_S0 + 35_999) * _S  # 09:59:59, a second before the window
    argv = _stack(tmp_path, monkeypatch, ledger=(("collector.snapshot_publish", before),))
    status, report = _evaluate(argv, capsys)
    assert status == 1
    assert report["scenarios"][0]["mismatches"] == [
        "required ledger site collector.snapshot_publish absent"
    ]


def test_an_unexplained_second_in_the_window_fails_the_scenario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = _clean_runs(_BTC, (*_ROW_SECONDS, _S0 + 36_200))  # 10:03:20 neither row nor run
    status, report = _evaluate(_stack(tmp_path, monkeypatch, coverage=runs), capsys)
    assert status == 1
    assert report["scenarios"][0]["mismatches"] == ["unexplained_seconds = 1"]


def test_an_unsettled_window_is_pending_and_never_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    during_its_hour = (_S0 + 36_900) * _S  # 10:15, the window's hour still recording
    status, report = _evaluate(_stack(tmp_path, monkeypatch), capsys, now_ns=during_its_hour)
    assert (status, report["evaluated"]) == (1, 0)
    assert report["scenarios"][0]["status"] == "pending"


def test_evaluate_refuses_without_the_collector_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = _stack(tmp_path, monkeypatch)
    (tmp_path / "errors" / "bybit_collector.jsonl").unlink()
    with pytest.raises(SystemExit, match="no bybit_collector ledger"):
        chaos.main(argv, clock=lambda: _NOW)


# -- the composition root -----------------------------------------------------------------------


def _root_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, marked: bool = True) -> None:
    for name in ("verify", "catalog"):
        (tmp_path / name).mkdir()
    if marked:
        (tmp_path / ".verify-stack").touch()
    monkeypatch.setenv("VERIFY_DATA_DIR", str(tmp_path / "verify"))
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "catalog"))
    plan = tmp_path / "bybit.toml"
    plan.write_text(f'instruments = ["{_BTC}"]\n')
    monkeypatch.setenv("BYBIT_COLLECTOR_CONFIG", str(plan))
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)


def _main(argv: list[str], fake: _Host) -> int:
    return chaos.main(argv, fake.clock, fake.sleep, fake.runner, _resolve)


def test_a_run_appends_its_start_and_end_to_the_scenario_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _root_env(tmp_path, monkeypatch)
    status = _main(["--scenario", PAUSE_15S, "--venue", "BYBIT"], _Host())
    log = tmp_path / "verify" / "chaos" / "scenarios.jsonl"
    events = [json.loads(line)["event"] for line in log.read_text().splitlines()]
    assert (status, events) == (0, [START, END])
    assert "chaos pause_15s BYBIT on verify-bybit-collector" in capsys.readouterr().out


def test_a_failed_undo_is_ledgered_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    error_ledger.reset()
    _root_env(tmp_path, monkeypatch)
    status = _main(["--scenario", PAUSE_15S, "--venue", "BYBIT"], _Host(failing={"unpause"}))
    assert status == 1
    assert error_ledger.counts() == {"verification.chaos.undo_failed": 1}
    error_ledger.reset()


def test_a_data_dir_without_the_verify_marker_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root_env(tmp_path, monkeypatch, marked=False)
    fake = _Host()
    with pytest.raises(SystemExit, match="not a verify stack"):
        _main(["--scenario", PAUSE_15S, "--venue", "BYBIT"], fake)
    assert fake.events == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--venue", "BYBIT"],
        ["--scenario", "unplug", "--venue", "BYBIT"],
        ["--scenario", PAUSE_15S, "--evaluate", "--venue", "BYBIT"],
        ["--evaluate", "--venue", "BYBIT", "--container", _COLLECTOR],
    ],
)
def test_a_bad_command_line_is_a_usage_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        chaos.main(argv)
    assert raised.value.code == 2


@pytest.mark.parametrize(
    ("scenario", "open_run", "failing", "why"),
    [
        (PAUSE_15S, True, (), "still open"),
        (NETWORK_CUT, False, ("-S",), "sudo -n"),
    ],
)
def test_a_refusal_is_ledgered_exits_1_and_applies_no_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    open_run: bool,
    failing: tuple[str, ...],
    why: str,
) -> None:
    error_ledger.reset()
    _root_env(tmp_path, monkeypatch)
    log = tmp_path / "verify" / "chaos" / "scenarios.jsonl"
    if open_run:
        run = ScenarioRun(PAUSE_15S, ("BYBIT",), _COLLECTOR, _T0 - 400 * _S, None, ())
        _write_jsonl(log, [run.log_line(START)])
    before = log.read_text() if log.exists() else ""
    fake = _Host(failing=failing)
    with pytest.raises(SystemExit, match=why) as refused:
        _main(["--scenario", scenario, "--venue", "BYBIT"], fake)
    assert isinstance(refused.value.code, str)  # a message: the interpreter exits 1
    assert error_ledger.counts() == {"verification.chaos.refused": 1}
    probes = {("docker", "inspect"), ("docker", "ps")}
    faults = [e for e in fake.events if e[:2] not in probes and "-S" not in e]
    assert faults == []
    assert (log.read_text() if log.exists() else "") == before  # no start line either
    error_ledger.reset()


def test_a_sigterm_mid_fault_undoes_it_writes_the_end_line_and_is_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    _root_env(tmp_path, monkeypatch)
    fake = _Host()
    held = fake.sleep

    def sleep_until_terminated(seconds: float) -> None:
        held(seconds)
        if seconds == 15:  # the pause's hold: the operator's terminal closes
            os.kill(os.getpid(), signal.SIGTERM)

    fake.sleep = sleep_until_terminated  # type: ignore[method-assign]
    held_runner = fake.runner

    def terminated_again(argv: Sequence[str]) -> CommandRecord:
        if argv[1] == "unpause":  # a second signal mid-undo is ignored, never cuts it short
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)
        return held_runner(argv)

    fake.runner = terminated_again  # type: ignore[method-assign]
    handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    with pytest.raises(SystemExit, match="interrupted: SIGTERM"):
        _main(["--scenario", PAUSE_15S, "--venue", "BYBIT"], fake)
    assert ("docker", "unpause", _COLLECTOR) in fake.events
    log = tmp_path / "verify" / "chaos" / "scenarios.jsonl"
    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert [line["event"] for line in lines] == [START, END]
    assert [c["returncode"] for c in lines[1]["commands"]] == [0, 0]
    assert error_ledger.counts() == {"verification.chaos.fault_failed": 1}
    assert {s: signal.getsignal(s) for s in handlers} == handlers  # restored
    error_ledger.reset()


def test_an_end_line_lost_after_the_undo_is_ledgered_not_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    _root_env(tmp_path, monkeypatch)
    append = chaos_io.ScenarioLogFile.append

    def disk_full(self: chaos_io.ScenarioLogFile, line: Mapping[str, Any]) -> None:
        if line["event"] == END:
            raise OSError(28, "No space left on device")
        append(self, line)

    monkeypatch.setattr(chaos_io.ScenarioLogFile, "append", disk_full)
    fake = _Host()
    with pytest.raises(SystemExit, match="chaos failed: the end line was not written"):
        _main(["--scenario", PAUSE_15S, "--venue", "BYBIT"], fake)
    assert ("docker", "unpause", _COLLECTOR) in fake.events
    assert error_ledger.counts() == {"verification.chaos.fault_failed": 1}
    error_ledger.reset()


def test_an_evaluation_keeps_the_default_signal_handling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _root_env(tmp_path, monkeypatch)
    seen: list[object] = []
    monkeypatch.setattr(chaos, "evaluate", lambda *_: seen.append(signal.getsignal(signal.SIGTERM)))
    with pytest.raises(AttributeError):  # the stub returns no report: only the handler matters
        _main(["--evaluate", "--venue", "BYBIT"], _Host())
    assert seen == [signal.getsignal(signal.SIGTERM)]
    error_ledger.reset()


# -- the adapters -------------------------------------------------------------------------------


def test_the_catalog_leaves_are_only_the_venues_and_their_modes_round_trip(tmp_path: Path) -> None:
    data = tmp_path / "catalog" / "data"
    ours = data / "trade_tick" / "BTCUSDT-LINEAR.BYBIT"
    for path in (ours, data / "trade_tick" / "BTC.HYPERLIQUID", data / "bar" / "x.BYBIT-1-MIN"):
        path.mkdir(parents=True)
    (data / "trade_tick" / "stray.BYBIT").write_text("")  # a file, not a leaf directory
    leaves = chaos_io.CatalogLeaves(tmp_path / "catalog")
    assert leaves.leaves("BYBIT") == (str(ours),)
    prior = leaves.mode(str(ours))
    leaves.set_mode(str(ours), prior & ~0o222)
    assert leaves.mode(str(ours)) == prior & ~0o222
    leaves.set_mode(str(ours), prior)
    assert leaves.mode(str(ours)) == prior


def test_the_scenario_log_file_appends_whole_lines_with_their_place(tmp_path: Path) -> None:
    log = chaos_io.ScenarioLogFile(tmp_path / "chaos" / "scenarios.jsonl")
    assert list(log.lines()) == []  # no file yet: an empty log
    run = ScenarioRun(PAUSE_15S, ("BYBIT",), _COLLECTOR, _T0, None, ())
    log.append(run.log_line(START))
    log.append(replace(run, fault_end_ns=_T0 + 15 * _S).log_line(END))
    lines = list(log.lines())
    assert [where for where, _ in lines] == [f"{log.path}:1", f"{log.path}:2"]
    assert [run.fault_end_ns for run in parse_log(lines)] == [_T0 + 15 * _S]


def test_the_ledger_reader_keeps_only_the_sites_inside_the_window(tmp_path: Path) -> None:
    lines = [
        _ledger_line("collector.before", 99),
        _ledger_line("collector.first", 100),
        _ledger_line("collector.last", 199),
        _ledger_line("collector.at_end", 200),  # the window's end is exclusive
    ]
    _write_jsonl(tmp_path / "bybit_collector.jsonl", lines)
    ledger = chaos_io.LedgerFiles(tmp_path)
    assert ledger.has("BYBIT")
    assert not ledger.has("HYPERLIQUID")
    assert ledger.sites("BYBIT", 100, 200) == frozenset({"collector.first", "collector.last"})
