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
The nightly saga's control flow, with a fake step runner (the steps have their own tests): the
rebuild proof carried from the rebuild's result file to the reconcile's argv, and every stop.
"""

import json
import sys
from pathlib import Path

import pytest
from observability import error_ledger

from archive.application.nightly import Step
from archive.application.nightly import StepResult
from archive.application.nightly import run_steps
from archive.nightly import main
from archive.nightly import steps


def _args(tmp_path: Path) -> list[str]:
    """Build the saga's command line over an existing (empty) catalog directory."""
    catalog = tmp_path / "catalog"
    catalog.mkdir(exist_ok=True)
    return [
        "--catalog",
        str(catalog),
        "--candles-dir",
        "/cd",
        "--venue",
        "BYBIT",
        "--day",
        "2026-09-20",
    ]


def _dydx_args(tmp_path: Path) -> list[str]:
    args = _args(tmp_path)
    return [*args[:5], "DYDX", *args[6:], "--dydx-plan", "/plan.toml"]


# A step's module is a full dotted path and no longer matches its name (`build_candles` runs
# `candles.rebuild` since Story 24.1), so the fake runner maps the argv it is handed back to the
# step that produced it. An argv naming no step is a KeyError: loud, never a silently skipped step.
_MODULE_TO_STEP = {step.argv[2]: step.name for step in steps("/c", "/cd", "BYBIT", "d", "/r")}


def _option(argv: list[str], flag: str) -> str | None:
    return argv[argv.index(flag) + 1] if flag in argv else None


_VERDICT = {"verification": "no reference data", "reason": "VERIFY_DATA_DIR is not set"}


class _FakeRunner:
    """
    Exit codes by step name; records each step's argv. The rebuild step writes its result file as
    `archive.rebuild_seconds` does (refusing `refused`), unless `result` overrides its content;
    the verify_day step writes its verdict as `archive.verify_day` does, unless `verdict`
    overrides its content.
    """

    def __init__(
        self,
        codes: dict[str, int] | None = None,
        refused: tuple[str, ...] = (),
        result: str | None = None,
        verdict: str | None = None,
    ) -> None:
        self.codes = codes or {}
        self.refused = list(refused)
        self.result = result
        self.verdict = verdict
        self.argv: dict[str, list[str]] = {}

    @property
    def ran(self) -> list[str]:
        return list(self.argv)

    def __call__(self, argv: list[str]) -> int:
        name = _MODULE_TO_STEP[argv[2]]
        self.argv[name] = argv
        result_file = _option(argv, "--result-file")
        venue, day = _option(argv, "--venue"), _option(argv, "--day")
        if name == "verify_day" and result_file is not None and self.verdict != "missing":
            verdict = {"venue": venue, "day": day, **_VERDICT}
            Path(result_file).write_text(self.verdict or json.dumps(verdict))
        elif result_file is not None and self.result != "missing":
            body = {"venue": venue, "day": day, "rebuilt": ["A"], "refused": self.refused}
            Path(result_file).write_text(self.result or json.dumps(body))
        return self.codes.get(name, 0)


_ORDER = [
    "rebuild_seconds",
    "consolidate_catalog",
    "build_candles",
    "compare_klines",
    "prune_catalog",
    "verify_day",
]


def test_steps_are_the_documented_chain_as_subprocess_modules() -> None:
    chain = steps("/c", "/cd", "BYBIT", "2026-09-20", "/scratch/r.json")
    assert [s.name for s in chain] == _ORDER
    assert all(s.argv[:2] == [sys.executable, "-m"] for s in chain)
    # A step's name is its ledger suffix and its place in the frozen summary line, not its module:
    # `build_candles` runs the candles context's rebuild CLI (Story 24.1).
    assert [s.argv[2] for s in chain] == [
        "archive.rebuild_seconds",
        "archive.consolidate_catalog",
        "candles.rebuild",
        "archive.compare_klines",
        "archive.prune_catalog",
        "archive.verify_day",
    ]
    assert chain[0].argv[3:] == [
        "--catalog",
        "/c",
        "--day",
        "2026-09-20",
        "--venue",
        "BYBIT",
        "--apply",
        "--candles-dir",
        "/cd",
        "--result-file",
        "/scratch/r.json",
    ]
    assert "/cd/candles_bybit.db" in chain[2].argv
    assert "/cd/candles_bybit.db" in chain[3].argv
    assert chain[4].argv[3:] == [
        "--catalog",
        "/c",
        "--apply",
        "--trade-retention-days",
        "7",
        "--candles-dir",
        "/cd",
        "--venue",
        "BYBIT",
    ]
    # Last, after the prune it must never gate, its verdict beside the rebuild's result.
    assert chain[5].argv[3:] == [
        "--catalog",
        "/c",
        "--candles-dir",
        "/cd",
        "--venue",
        "BYBIT",
        "--day",
        "2026-09-20",
        "--result-file",
        "/scratch/verify_result.json",
    ]
    assert chain[5].verdict_file == "/scratch/verify_result.json"
    assert [s.name for s in chain if s.verdict_file] == ["verify_day"]


def test_all_steps_run_in_order_and_exit_zero(tmp_path: Path) -> None:
    runner = _FakeRunner()
    assert main(_args(tmp_path), runner) == 0
    assert runner.ran == _ORDER


def test_the_rebuild_proof_reaches_the_reconcile(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runner = _FakeRunner()
    with caplog.at_level("INFO", logger="archive.nightly"):
        assert main(_args(tmp_path), runner) == 0
    run_id = _option(runner.argv["compare_klines"], "--rebuilt-by")
    assert run_id is not None
    assert "--not-rebuilt" not in runner.argv["compare_klines"]
    (summary,) = [r.getMessage() for r in caplog.records if ": rebuild_seconds " in r.getMessage()]
    assert summary.endswith(f"run id {run_id}")


def test_a_refused_instrument_is_forwarded_as_not_rebuilt(tmp_path: Path) -> None:
    error_ledger.reset()
    runner = _FakeRunner({"rebuild_seconds": 2}, refused=("X.BYBIT", "B.BYBIT"))
    assert main(_args(tmp_path), runner) == 2
    compare = runner.argv["compare_klines"]
    assert compare[compare.index("--rebuilt-by") + 2 :] == [
        "--rebuilt",
        "A",
        "--not-rebuilt",
        "B.BYBIT",
        "--not-rebuilt",
        "X.BYBIT",
    ]
    assert runner.ran == _ORDER
    assert error_ledger.counts() == {"nightly.rebuild_seconds": 1}


@pytest.mark.parametrize("result", ["missing", "{not json", '{"venue": "BYBIT"}', "[]"])
def test_an_unusable_rebuild_result_stops_before_consolidate(tmp_path: Path, result: str) -> None:
    error_ledger.reset()
    runner = _FakeRunner(result=result)
    assert main(_args(tmp_path), runner) == 1
    assert runner.ran == ["rebuild_seconds"]
    assert error_ledger.counts() == {"nightly.rebuild_seconds": 1}


def test_a_result_for_another_venue_day_stops_the_saga(tmp_path: Path) -> None:
    wrong = json.dumps({"venue": "DYDX", "day": "2026-09-20", "rebuilt": [], "refused": []})
    runner = _FakeRunner(result=wrong)
    assert main(_args(tmp_path), runner) == 1
    assert runner.ran == ["rebuild_seconds"]


def test_the_dydx_plan_is_forwarded_to_the_prune_for_dydx_only(tmp_path: Path) -> None:
    runner = _FakeRunner()
    assert main(_dydx_args(tmp_path), runner) == 0
    assert _option(runner.argv["prune_catalog"], "--dydx-plan") == "/plan.toml"
    assert all(
        "--dydx-plan" not in argv for name, argv in runner.argv.items() if name != "prune_catalog"
    )
    bybit = _FakeRunner()
    assert main([*_args(tmp_path), "--dydx-plan", "/plan.toml"], bybit) == 0
    assert "--dydx-plan" not in bybit.argv["prune_catalog"]


def test_dydx_without_its_plan_runs_the_chain_as_findings(tmp_path: Path) -> None:
    """A pre-25.1 `--venue DYDX` line (no `--dydx-plan`) keeps working -- loudly."""
    error_ledger.reset()
    runner = _FakeRunner()
    assert main(_dydx_args(tmp_path)[:-2], runner) == 2
    assert runner.ran == _ORDER
    assert "--dydx-plan" not in runner.argv["prune_catalog"]
    assert error_ledger.counts() == {"nightly.dydx_plan_missing": 1}
    failing = _FakeRunner({"consolidate_catalog": 1})
    assert main(_dydx_args(tmp_path)[:-2], failing) == 1  # a failed step still wins


def test_the_saga_normalizes_the_day(tmp_path: Path) -> None:
    runner = _FakeRunner()
    assert main([*_args(tmp_path)[:7], "2026-9-20"], runner) == 0
    assert all(_option(argv, "--day") in (None, "2026-09-20") for argv in runner.argv.values())
    assert _option(runner.argv["rebuild_seconds"], "--day") == "2026-09-20"


def test_stops_at_the_first_failure_with_its_exit_code(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    error_ledger.reset()
    runner = _FakeRunner({"consolidate_catalog": 1})
    with caplog.at_level("INFO", logger="archive.nightly"):
        assert main(_args(tmp_path), runner) == 1
    assert runner.ran == ["rebuild_seconds", "consolidate_catalog"]
    assert error_ledger.counts() == {"nightly.consolidate_catalog": 1}
    (summary,) = [r.getMessage() for r in caplog.records if ": rebuild_seconds " in r.getMessage()]
    assert summary.startswith("nightly BYBIT 2026-09-20: rebuild_seconds ok ")
    assert "consolidate_catalog FAILED" in summary
    assert "peak child RSS" in summary
    assert "outcome FAILED; run id " in summary


def test_compare_findings_continue_to_prune_and_exit_two(tmp_path: Path) -> None:
    error_ledger.reset()
    runner = _FakeRunner({"compare_klines": 2})
    assert main(_args(tmp_path), runner) == 2
    assert runner.ran == _ORDER
    assert error_ledger.counts() == {"nightly.compare_klines": 1}


def test_consolidate_findings_from_a_mixed_schema_day_run_every_later_step_and_exit_two(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    `consolidate_catalog` exits 2 when its only problems were mixed-schema refusals (DW-213): one
    standing D-24 day must not stop the venue's candles, reconcile, retention and verdict nightly.
    """
    error_ledger.reset()
    runner = _FakeRunner({"consolidate_catalog": 2})
    with caplog.at_level("INFO", logger="archive.nightly"):
        assert main(_args(tmp_path), runner) == 2
    assert runner.ran == _ORDER
    assert error_ledger.counts() == {"nightly.consolidate_catalog": 1}
    (summary,) = [r.getMessage() for r in caplog.records if ": rebuild_seconds " in r.getMessage()]
    assert "consolidate_catalog findings" in summary
    assert "outcome findings; run id " in summary


def test_consolidate_is_limited_to_recent_days_and_candles_to_one_worker() -> None:
    chain = steps("/c", "/cd", "BYBIT", "2026-09-20", "/r")
    assert chain[1].argv[-2:] == ["--days", "2"]
    assert chain[2].argv[-2:] == ["--workers", "1"]


def test_compare_error_stops_before_prune(tmp_path: Path) -> None:
    runner = _FakeRunner({"compare_klines": 1})
    assert main(_args(tmp_path), runner) == 1
    assert runner.ran == _ORDER[:4]


def _chain(tmp_path: Path) -> list[Step]:
    return steps("/c", "/cd", "BYBIT", "2026-09-20", str(tmp_path / "rebuild_result.json"))


def test_the_verify_days_verdict_reaches_its_step_result(tmp_path: Path) -> None:
    error_ledger.reset()
    results = run_steps(_chain(tmp_path), _FakeRunner(), "run", "BYBIT", "2026-09-20")
    assert [r.verification for r in results] == [None] * 5 + [_VERDICT]
    assert error_ledger.counts() == {}


@pytest.mark.parametrize(
    "verdict",
    [
        "missing",
        "{not json",
        "[]",
        '{"venue": "BYBIT", "day": "2026-09-20"}',  # no verdict
        '{"venue": "DYDX", "day": "2026-09-20", "verification": "verified"}',
    ],
)
def test_an_unreadable_verdict_is_ledgered_and_changes_no_exit_code(
    tmp_path: Path, verdict: str
) -> None:
    error_ledger.reset()
    results = run_steps(_chain(tmp_path), _FakeRunner(verdict=verdict), "r", "BYBIT", "2026-09-20")
    assert (results[-1].name, results[-1].code) == ("verify_day", 0)
    assert results[-1].verification == {"verification": "result unreadable"}
    assert error_ledger.counts() == {"nightly.verify_day": 1}


def test_verify_day_findings_end_the_saga_as_findings_after_everything_ran(tmp_path: Path) -> None:
    error_ledger.reset()
    runner = _FakeRunner({"verify_day": 2})
    assert main(_args(tmp_path), runner) == 2
    assert runner.ran == _ORDER
    assert error_ledger.counts() == {"nightly.verify_day": 1}


def test_a_failed_step_stops_the_saga_before_verify_day(tmp_path: Path) -> None:
    runner = _FakeRunner({"prune_catalog": 1})
    assert main(_args(tmp_path), runner) == 1
    assert "verify_day" not in runner.ran


def test_the_rebuilt_list_reaches_the_reconcile_as_rebuilt(tmp_path: Path) -> None:
    runner = _FakeRunner()  # its result file names "A" rebuilt
    assert main(_args(tmp_path), runner) == 0
    compare = runner.argv["compare_klines"]
    assert compare[compare.index("--rebuilt-by") + 2 :] == ["--rebuilt", "A"]


def test_a_missing_catalog_stops_the_saga_before_any_step(tmp_path: Path) -> None:
    error_ledger.reset()
    runner = _FakeRunner()
    args = _args(tmp_path)
    args[1] = str(tmp_path / "nope")
    assert main(args, runner) == 1
    assert runner.ran == []
    assert error_ledger.counts() == {"archive.catalog_missing": 1}


def test_on_step_sees_each_final_result_before_the_next_step_runs(tmp_path: Path) -> None:
    runner = _FakeRunner(result="missing")  # the rebuild's proof is unusable: it fails late
    seen: list[tuple[str, int, list[str]]] = []

    def on_step(result: StepResult) -> None:
        seen.append((result.name, result.code, runner.ran))

    chain = steps("/c", "/cd", "BYBIT", "2026-09-20", str(tmp_path / "r.json"))
    results = run_steps(chain, runner, "run", "BYBIT", "2026-09-20", on_step)
    assert seen == [("rebuild_seconds", 1, ["rebuild_seconds"])]
    assert [r.name for r in results] == ["rebuild_seconds"]


def test_on_step_is_called_once_per_step_in_order(tmp_path: Path) -> None:
    runner = _FakeRunner(codes={"compare_klines": 2})
    seen: list[str] = []
    chain = steps("/c", "/cd", "BYBIT", "2026-09-20", str(tmp_path / "r.json"))
    run_steps(chain, runner, "run", "BYBIT", "2026-09-20", lambda r: seen.append(r.outcome()))
    assert seen == ["ok", "ok", "ok", "findings", "ok", "ok"]
