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
Every one-shot verification tool ledgers under its own job service (Story 31.8 review).

`error_ledger.start()` with no service inherits `ERROR_LEDGER_SERVICE`: run by the `archive`
nightly, or by `docker exec` inside a collector or recorder, a tool would write its `process_start`
into that service's file and reset its since-restart window. Each root names its own job instead,
`<parent>.verify_<tool>`. The long-running `verification.recorder` is a service and keeps its own.
"""

import json
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path

import pytest
from observability import error_ledger

from verification import book
from verification import candles
from verification import catalog
from verification import conservation
from verification import derivs
from verification import trades


_ROOTS: dict[str, Callable[[list[str]], int]] = {
    "book": book.main,
    "candles": candles.main,
    "catalog": catalog.main,
    "conservation": conservation.main,
    "derivs": derivs.main,
    "trades": trades.main,
}
_FUTURE_DAY = ["--venue", "BYBIT", "--day", "2099-01-05"]  # never closed: the cheapest refusal


@pytest.fixture
def errors_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    error_ledger.reset()
    monkeypatch.setenv("ERROR_LEDGER_DIR", str(tmp_path / "errors"))
    monkeypatch.setenv("ERROR_LEDGER_SERVICE", "bybit_collector")  # a docker exec's inheritance
    yield tmp_path / "errors"
    error_ledger.reset()


@pytest.mark.parametrize("tool", sorted(_ROOTS))
def test_each_tool_ledgers_under_its_own_job_never_the_inherited_service(
    tool: str, errors_dir: Path
) -> None:
    argv = [*_FUTURE_DAY, "--stage", "live"] if tool == "trades" else _FUTURE_DAY
    with pytest.raises(SystemExit) as refused:
        _ROOTS[tool](argv)

    assert refused.value.code != 2  # a refusal after start, not a usage error before it

    ledger = errors_dir / f"bybit_collector.verify_{tool}_bybit.jsonl"
    first = json.loads(ledger.read_text().splitlines()[0])
    assert first["site"] == "process_start"
    assert error_ledger.is_job_service(first["service"])
    assert not (errors_dir / "bybit_collector.jsonl").exists()  # the collector's window untouched


def test_a_tool_run_by_hand_defaults_its_parent_to_verification(
    errors_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ERROR_LEDGER_SERVICE")

    with pytest.raises(SystemExit):
        candles.main(_FUTURE_DAY)

    assert (errors_dir / "verification.verify_candles_bybit.jsonl").exists()
