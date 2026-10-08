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
The `verify_day` step (Story 31.11) with a fake tool runner -- the tools have their own tests and
are never run here: the reference-data gate, every tool's command line, the reduction to a day
verdict, the ledger, the timeouts and crashes that must never exit 1, and the result file -- and
the liquidations report (Story 33.1), kept beside the verdict on both paths, never in it.
"""

import datetime as dt
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from observability import error_ledger

from archive.verify_day import SITE
from archive.verify_day import TIMED_OUT
from archive.verify_day import TOOL_TIMEOUT_S
from archive.verify_day import TOOLS
from archive.verify_day import main
from archive.verify_day import subprocess_runner


_DAY = "2026-09-29"
_BTC = "BTCUSDT-LINEAR.BYBIT"
_ETH = "ETHUSDT-LINEAR.BYBIT"
_NOW = dt.datetime(2026, 9, 30, 3, 10, 5, tzinfo=dt.UTC)
_API = "http://127.0.0.1:29100"


def _instrument(iid: str, failing: int = 0) -> dict[str, Any]:
    return {"instrument_id": iid, "passed": failing == 0, "failing": failing}


def _liquidations_body() -> dict[str, Any]:
    """Return the liquidations tool's `--json` shape: a share, never a verdict (Story 33.1)."""
    btc = {"instrument_id": _BTC, "total": 4, "matched": 3, "share": 0.75, "unmatched_count": 1}
    btc |= {"unmatched_ids": ["1:Buy:0.010:60000.10"], "unrecoverable_seconds": 12}
    head = {"venue": "BYBIT", "day": _DAY, "applicable": True, "coverage_present": True}
    totals = {"total": 4, "matched": 3, "share": 0.75, "unrecoverable_seconds": 12}
    return {**head, **totals, "instruments": [btc]}


_LIQUIDATIONS = {
    "report": "reported",
    "applicable": True,
    "coverage_present": True,
    "total": 4,
    "matched": 3,
    "share": 0.75,
    "unrecoverable_seconds": 12,
    "instruments": {_BTC: {"total": 4, "matched": 3, "unmatched": 1, "unrecoverable_seconds": 12}},
}
_CHILDREN = [*TOOLS, "liquidations"]


def _body(tool: str, eth_failing: int = 0) -> dict[str, Any]:
    """Build a report in `tool`'s own `--json` shape (restated: archive imports no tool)."""
    if tool == "liquidations":
        return _liquidations_body()
    passed = eth_failing == 0
    if tool == "catalog":
        parity = [{"instrument_id": _BTC, "failing": 0}, {"instrument_id": _ETH, "failing": 0}]
        parity[1]["failing"] = eth_failing
        candles = [{"instrument_id": _BTC, "failing": 0}, {"instrument_id": _ETH, "failing": 0}]
        return {"passed": passed, "failing": eth_failing, "parity": parity, "candles": candles}
    instruments = [_instrument(_BTC), _instrument(_ETH, eth_failing)]
    body = {"passed": passed, "missing_raw_files": [], "instruments": instruments}
    if tool == "candles":
        for entry in instruments:
            del entry["passed"]
        return {**body, "failing": eth_failing, "served": "checked"}
    return {**body, "coverage_present": True}


class _Runner:
    """Answers each tool by its module name; records every argv and timeout it was handed."""

    def __init__(self, answers: dict[str, tuple[int, str]] | None = None) -> None:
        self.answers = answers or {}
        self.argv: dict[str, list[str]] = {}
        self.timeouts: list[float] = []

    def __call__(self, argv: list[str], timeout_s: float) -> tuple[int, str]:
        tool = argv[2].removeprefix("verification.")
        self.argv[tool] = argv
        self.timeouts.append(timeout_s)
        return self.answers.get(tool, (0, json.dumps(_body(tool))))


def _raw_root(tmp_path: Path, day: str = _DAY) -> Path:
    root = tmp_path / "verify_data"
    hour = root / "raw" / "bybit" / "publicTrade" / f"{day}T13.jsonl.zst"
    hour.parent.mkdir(parents=True)
    hour.write_bytes(b"")
    return root


def _env(tmp_path: Path, api: bool = True) -> dict[str, str]:
    env = {"VERIFY_DATA_DIR": str(_raw_root(tmp_path))}
    return {**env, "VERIFY_DATA_API_URL": _API} if api else env


def _run(
    tmp_path: Path, environ: dict[str, str], runner: Any, venue: str = "BYBIT"
) -> tuple[int, dict[str, Any]]:
    error_ledger.reset()
    result = tmp_path / "verify_result.json"
    argv = ["--catalog", "/c", "--candles-dir", "/cd", "--venue", venue, "--day", _DAY]
    code = main([*argv, "--result-file", str(result)], runner, lambda: _NOW, environ)
    return code, json.loads(result.read_text())


@pytest.fixture(autouse=True)
def _no_durable_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)


# --- the reference-data gate ---------------------------------------------------------------------


@pytest.mark.parametrize("case", ["unset", "no_file_of_the_day", "dydx"])
def test_without_reference_data_nothing_runs_and_nothing_is_ledgered(
    tmp_path: Path, case: str
) -> None:
    if case == "unset":
        environ: dict[str, str] = {}
    elif case == "no_file_of_the_day":
        environ = {"VERIFY_DATA_DIR": str(_raw_root(tmp_path, "2026-09-28"))}
    else:
        environ = _env(tmp_path)  # DYDX: recorders run, but none for this venue
    runner = _Runner()
    venue = "DYDX" if case == "dydx" else "BYBIT"
    code, result = _run(tmp_path, environ, runner, venue)
    assert code == 0
    assert result["verification"] == "no reference data"
    assert (result["venue"], result["day"]) == (venue, _DAY)
    assert result["reason"]
    # Only the liquidations report, which needs no recorder, and only for a venue with the feed.
    assert list(runner.argv) == (["liquidations"] if venue == "BYBIT" else [])
    assert result.get("liquidations") == (_LIQUIDATIONS if venue == "BYBIT" else None)
    assert error_ledger.counts() == {}


# --- the tools' command lines --------------------------------------------------------------------


def test_every_tool_runs_in_order_with_its_own_arguments(tmp_path: Path) -> None:
    runner = _Runner()
    root = str(tmp_path / "verify_data")
    _run(tmp_path, _env(tmp_path), runner)
    assert list(runner.argv) == _CHILDREN
    liquidations = runner.argv.pop("liquidations")
    assert liquidations[3:] == ["--venue", "BYBIT", "--day", _DAY, "--json", "--catalog", "/c"]
    common = ["--venue", "BYBIT", "--day", _DAY, "--json", "--catalog", "/c", "--raw-dir", root]
    for tool, argv in runner.argv.items():
        assert argv[:3] == [sys.executable, "-m", f"verification.{tool}"]
        assert argv[3:12] == common
    tail = {tool: argv[12:] for tool, argv in runner.argv.items()}
    assert tail == {
        "conservation": [],
        "trades": ["--stage", "rebuilt"],
        "book": [],
        "derivs": [],
        "catalog": ["--candles", "/cd", "--scratch-dir", f"{root}/scratch/verify_day"],
        "candles": ["--candles", "/cd", "--data-api", _API],
    }
    assert runner.timeouts == [TOOL_TIMEOUT_S] * len(_CHILDREN)
    shipped = tomllib.loads((Path(__file__).parents[1] / "config.toml").read_text())
    # The step's budget holds every child at its own timeout, the liquidations one included.
    assert len(_CHILDREN) * TOOL_TIMEOUT_S < shipped["step_timeout_minutes"] * 60


def test_without_a_data_api_the_candles_run_is_provisional_and_never_verified(
    tmp_path: Path,
) -> None:
    runner = _Runner()
    served = {**_body("candles"), "served": "not checked"}
    runner.answers["candles"] = (0, json.dumps(served))
    code, result = _run(tmp_path, _env(tmp_path, api=False), runner)
    assert runner.argv["candles"][12:] == ["--candles", "/cd", "--no-served"]
    assert (code, result["verification"]) == (2, "findings")
    assert result["types"]["candles"]["verdict"] == "fail"


# --- verdicts ------------------------------------------------------------------------------------


def test_all_passing_is_verified_with_one_verdict_per_type(tmp_path: Path) -> None:
    code, result = _run(tmp_path, _env(tmp_path), _Runner())
    assert code == 0
    assert set(result) == {"venue", "day", "verification", "checked_at", "types", "liquidations"}
    assert (result["verification"], result["checked_at"]) == ("verified", "2026-09-30T03:10:05Z")
    assert set(result["types"]) == set(TOOLS)
    assert result["liquidations"] == _LIQUIDATIONS
    assert {t["verdict"] for t in result["types"].values()} == {"pass"}
    assert result["types"]["book"]["instruments"][_ETH] == {"passed": True, "failing": 0}
    assert error_ledger.counts() == {}


def test_one_failing_type_is_findings_and_one_ledger_entry(tmp_path: Path) -> None:
    runner = _Runner({"book": (1, json.dumps(_body("book", eth_failing=3)))})
    code, result = _run(tmp_path, _env(tmp_path), runner)
    assert (code, result["verification"]) == (2, "findings")
    book = result["types"]["book"]
    assert (book["verdict"], book["failing"]) == ("fail", 3)
    assert book["instruments"][_ETH] == {"passed": False, "failing": 3}
    assert error_ledger.counts() == {SITE: 1}
    detail = error_ledger.last_details()[SITE]
    assert detail.startswith(f"BYBIT {_DAY} book: fail; failing 3")
    assert f"failing instruments: {_ETH}" in detail


def test_a_reports_dir_keeps_every_tool_report_unreduced(tmp_path: Path) -> None:
    error_ledger.reset()
    reports = tmp_path / "reports" / "BYBIT"  # not there yet: the step creates it
    failing = _body("book", eth_failing=3)
    argv = ["--catalog", "/c", "--candles-dir", "/cd", "--venue", "BYBIT", "--day", _DAY]
    argv += ["--result-file", str(tmp_path / "r.json"), "--reports-dir", str(reports)]
    runner = _Runner({"book": (1, json.dumps(failing))})
    assert main(argv, runner, lambda: _NOW, _env(tmp_path)) == 2
    assert sorted(p.stem for p in reports.iterdir()) == sorted(_CHILDREN)
    assert json.loads((reports / "book.json").read_text()) == failing


def test_the_ledger_names_the_missing_raw_files(tmp_path: Path) -> None:
    body = {**_body("derivs"), "passed": False, "missing_raw_files": ["bybit/tickers/T03"]}
    code, result = _run(tmp_path, _env(tmp_path), _Runner({"derivs": (1, json.dumps(body))}))
    assert (code, result["types"]["derivs"]["inputs_missing"]) == (2, 1)
    assert "missing raw files: bybit/tickers/T03" in error_ledger.last_details()[SITE]


def test_a_child_without_a_report_is_refused_and_ledgered(tmp_path: Path) -> None:
    runner = _Runner({"trades": (1, "")})  # a refusal: its message went to stderr
    code, result = _run(tmp_path, _env(tmp_path), runner)
    assert (code, result["verification"]) == (2, "findings")
    assert result["types"]["trades"]["verdict"] == "refused"
    assert result["types"]["trades"]["reason"] == "no JSON report (exit 1)"
    assert error_ledger.counts() == {SITE: 1}
    assert list(runner.argv) == _CHILDREN  # one refusal never stops the others


def test_a_child_killed_at_its_timeout_is_refused(tmp_path: Path) -> None:
    code, result = _run(tmp_path, _env(tmp_path), _Runner({"catalog": (TIMED_OUT, "")}))
    assert code == 2
    catalog = result["types"]["catalog"]
    assert (catalog["verdict"], catalog["reason"]) == (
        "refused",
        f"killed after {TOOL_TIMEOUT_S} s",
    )


def test_a_child_that_cannot_be_started_is_refused_and_the_others_still_run(
    tmp_path: Path,
) -> None:
    runner = _Runner()

    def failing_fork(argv: list[str], timeout_s: float) -> tuple[int, str]:
        if argv[2] == "verification.book":
            raise OSError(12, "Cannot allocate memory")
        return runner(argv, timeout_s)

    code, result = _run(tmp_path, _env(tmp_path), failing_fork)
    assert (code, result["verification"]) == (2, "findings")
    book = result["types"]["book"]
    assert book["verdict"] == "refused"
    assert book["reason"].startswith("not started: OSError(12, 'Cannot allocate memory')")
    assert sorted(result["types"]) == sorted(TOOLS)
    assert all(result["types"][t]["verdict"] == "pass" for t in TOOLS if t != "book")
    assert error_ledger.counts() == {SITE: 1}


def test_a_planted_pass_with_passed_false_is_a_fail(tmp_path: Path) -> None:
    body = {**_body("conservation"), "passed": False}
    code, result = _run(tmp_path, _env(tmp_path), _Runner({"conservation": (0, json.dumps(body))}))
    assert (code, result["types"]["conservation"]["verdict"]) == (2, "fail")


def test_a_crash_in_the_root_is_ledgered_and_exits_two(tmp_path: Path) -> None:
    def crashing(argv: list[str], timeout_s: float) -> tuple[int, str]:
        raise RuntimeError("fork failed")

    code, result = _run(tmp_path, _env(tmp_path), crashing)
    assert code == 2
    assert result == {
        "venue": "BYBIT",
        "day": _DAY,
        "verification": "error",
        "reason": "RuntimeError('fork failed')",
    }
    assert error_ledger.counts() == {SITE: 1}


def test_an_unwritable_result_file_is_ledgered_and_exits_two(tmp_path: Path) -> None:
    error_ledger.reset()
    argv = ["--catalog", "/c", "--candles-dir", "/cd", "--venue", "BYBIT", "--day", _DAY]
    result = str(tmp_path / "absent" / "verify_result.json")
    code = main([*argv, "--result-file", result], _Runner(), lambda: _NOW, {})
    assert code == 2
    assert error_ledger.counts() == {SITE: 1}


def test_the_result_file_replaces_an_old_one_and_leaves_no_temporary(tmp_path: Path) -> None:
    (tmp_path / "verify_result.json").write_text("stale")
    _run(tmp_path, _env(tmp_path), _Runner())
    assert [p.name for p in tmp_path.iterdir() if p.is_file()] == ["verify_result.json"]


def test_the_day_is_canonical(tmp_path: Path) -> None:
    runner = _Runner()
    error_ledger.reset()
    result = tmp_path / "verify_result.json"
    argv = ["--catalog", "/c", "--candles-dir", "/cd", "--venue", "BYBIT", "--day", "20260929"]
    main([*argv, "--result-file", str(result)], runner, lambda: _NOW, _env(tmp_path))
    assert json.loads(result.read_text())["day"] == "2026-09-29"
    assert all(argv[argv.index("--day") + 1] == "2026-09-29" for argv in runner.argv.values())


# --- the real runner -----------------------------------------------------------------------------


def test_the_subprocess_runner_captures_stdout_and_kills_at_the_timeout() -> None:
    assert subprocess_runner([sys.executable, "-c", "print('{}')"], 30.0) == (0, "{}\n")
    code, stdout = subprocess_runner([sys.executable, "-c", "import time; time.sleep(30)"], 0.5)
    assert (code, stdout) == (TIMED_OUT, "")


# --- the liquidations report (Story 33.1) ----------------------------------------------------------


def test_a_low_matched_share_never_changes_the_verdict(tmp_path: Path) -> None:
    body = {**_liquidations_body(), "matched": 0, "share": 0.0}
    code, result = _run(tmp_path, _env(tmp_path), _Runner({"liquidations": (0, json.dumps(body))}))
    assert (code, result["verification"]) == (0, "verified")
    assert result["liquidations"]["share"] == 0.0
    assert "liquidations" not in result["types"]


def test_a_refused_liquidations_child_is_ledgered_and_never_changes_the_exit(
    tmp_path: Path,
) -> None:
    code, result = _run(tmp_path, {}, _Runner({"liquidations": (1, "")}))
    assert code == 0  # no reference data: still exit 0
    assert result["liquidations"] == {"report": "refused", "reason": "no JSON report (exit 1)"}
    assert error_ledger.counts() == {SITE: 1}
    assert "liquidations: refused" in error_ledger.last_details()[SITE]


def test_a_malformed_liquidations_report_is_refused(tmp_path: Path) -> None:
    body = {**_liquidations_body(), "share": 1.5}
    _, result = _run(tmp_path, {}, _Runner({"liquidations": (0, json.dumps(body))}))
    assert result["liquidations"]["report"] == "refused"
    assert "share is not a fraction" in result["liquidations"]["reason"]


def test_a_missing_coverage_record_reads_unknown_not_zero_unrecoverable(tmp_path: Path) -> None:
    body = {**_liquidations_body(), "coverage_present": False, "unrecoverable_seconds": 0}
    _, result = _run(tmp_path, {}, _Runner({"liquidations": (0, json.dumps(body))}))
    assert result["liquidations"]["coverage_present"] is False
