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
"""The nightly chain's control flow, with a fake step runner (the steps have their own tests)."""

import sys

import pytest
from observability import error_ledger

from collector_core.nightly import main
from collector_core.nightly import steps


_ARGS = ["--catalog", "/c", "--candles-dir", "/cd", "--venue", "BYBIT", "--day", "2026-09-20"]


class _FakeRunner:
    """Exit codes by step module; records which steps ran."""

    def __init__(self, codes: dict[str, int] | None = None) -> None:
        self.codes = codes or {}
        self.ran: list[str] = []

    def __call__(self, argv: list[str]) -> int:
        name = argv[2].removeprefix("collector_core.")
        self.ran.append(name)
        return self.codes.get(name, 0)


_ORDER = [
    "rebuild_seconds",
    "consolidate_catalog",
    "build_candles",
    "compare_klines",
    "prune_catalog",
]


def test_steps_are_the_documented_chain_as_subprocess_modules() -> None:
    chain = steps("/c", "/cd", "BYBIT", "2026-09-20")
    assert [s.name for s in chain] == _ORDER
    assert all(s.argv[:2] == [sys.executable, "-m"] for s in chain)
    assert chain[0].argv[3:] == [
        "--catalog",
        "/c",
        "--day",
        "2026-09-20",
        "--venue",
        "BYBIT",
        "--apply",
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


def test_all_steps_run_in_order_and_exit_zero() -> None:
    runner = _FakeRunner()
    assert main(_ARGS, runner) == 0
    assert runner.ran == _ORDER


def test_stops_at_the_first_failure_with_its_exit_code(caplog: pytest.LogCaptureFixture) -> None:
    error_ledger.reset()
    runner = _FakeRunner({"consolidate_catalog": 1})
    with caplog.at_level("INFO", logger="collector_core.nightly"):
        assert main(_ARGS, runner) == 1
    assert runner.ran == ["rebuild_seconds", "consolidate_catalog"]
    assert error_ledger.counts() == {"nightly.consolidate_catalog": 1}
    (summary,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("nightly ")]
    assert summary.startswith("nightly BYBIT 2026-09-20: rebuild_seconds ok ")
    assert "consolidate_catalog FAILED" in summary
    assert "peak child RSS" in summary
    assert summary.endswith("outcome FAILED")


def test_compare_findings_continue_to_prune_and_exit_two() -> None:
    error_ledger.reset()
    runner = _FakeRunner({"compare_klines": 2})
    assert main(_ARGS, runner) == 2
    assert runner.ran == _ORDER
    assert error_ledger.counts() == {"nightly.compare_klines": 1}


def test_rebuild_findings_do_not_halt_the_venue_chain() -> None:
    error_ledger.reset()
    runner = _FakeRunner({"rebuild_seconds": 2})
    assert main(_ARGS, runner) == 2
    assert runner.ran == _ORDER
    assert error_ledger.counts() == {"nightly.rebuild_seconds": 1}


def test_consolidate_is_limited_to_recent_days_and_candles_to_one_worker() -> None:
    chain = steps("/c", "/cd", "BYBIT", "2026-09-20")
    assert chain[1].argv[-2:] == ["--days", "2"]
    assert chain[2].argv[-2:] == ["--workers", "1"]


def test_compare_error_stops_before_prune() -> None:
    runner = _FakeRunner({"compare_klines": 1})
    assert main(_ARGS, runner) == 1
    assert runner.ran == _ORDER[:4]
