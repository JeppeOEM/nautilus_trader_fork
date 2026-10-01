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
The day verdict's reduction (Story 31.11), verified by planted defects: for every tool's report
shape, a report with a failing instrument, a `passed: false`, a provisional candles run or no
report at all never summarises to `pass`, and a day is `verified` only with all six tools passing.
"""

import json
from typing import Any

import pytest

from verification.application.conservation import report_json as conservation_json
from verification.domain.conservation import DayReport
from verification.domain.conservation import InstrumentReport
from verification.domain.conservation import SecondCounts
from verification.domain.conservation import TradeCounts
from verification.domain.verdict import FAILED
from verification.domain.verdict import FINDINGS
from verification.domain.verdict import PASSED
from verification.domain.verdict import REFUSED
from verification.domain.verdict import TOOLS
from verification.domain.verdict import VERIFIED
from verification.domain.verdict import InstrumentVerdict
from verification.domain.verdict import TypeVerdict
from verification.domain.verdict import day_verdict
from verification.domain.verdict import parse_report
from verification.domain.verdict import summarise


_BTC = "BTCUSDT-LINEAR.BYBIT"
_ETH = "ETHUSDT-LINEAR.BYBIT"
_PER_INSTRUMENT_TOOLS = ("conservation", "trades", "book", "derivs")
_CHECKED_AT = "2026-09-30T03:10:00Z"


def _instrument(iid: str, failing: int = 0) -> dict[str, Any]:
    return {"instrument_id": iid, "passed": failing == 0, "failing": failing}


def _per_instrument(eth_failing: int = 0, **extra: Any) -> dict[str, Any]:
    """Build the conservation / trades / book / derivs shape: a verdict per instrument."""
    return {
        "passed": eth_failing == 0,
        "coverage_present": True,
        "missing_raw_files": [],
        "instruments": [_instrument(_BTC), _instrument(_ETH, eth_failing)],
        **extra,
    }


def _catalog(eth_failing: int = 0) -> dict[str, Any]:
    """Build the catalog shape: a top-level count; instruments only in `parity` and `candles`."""
    return {
        "passed": eth_failing == 0,
        "failing": eth_failing,
        "instruments": [_BTC, _ETH],
        "parity": [
            {"instrument_id": _BTC, "data_type": "trade_tick", "failing": 0},
            {"instrument_id": _ETH, "data_type": "trade_tick", "failing": eth_failing},
            {"instrument_id": _ETH, "data_type": "custom_dydx_second_snapshot", "failing": 0},
        ],
        "candles": [
            {"instrument_id": _BTC, "failing": 0},
            {"instrument_id": _ETH, "failing": 0},
        ],
    }


def _candles(eth_failing: int = 0, served: str = "checked") -> dict[str, Any]:
    """Build the candles shape: a top-level count, `served`; per instrument a count, no `passed`."""
    return {
        "verdict": "PASS",
        "passed": eth_failing == 0 and served == "checked",
        "provisional": served != "checked",
        "failing": eth_failing,
        "served": served,
        "missing_raw_files": [],
        "instruments": [
            {"instrument_id": _BTC, "failing": 0},
            {"instrument_id": _ETH, "failing": eth_failing},
        ],
    }


def _clean(tool: str) -> dict[str, Any]:
    if tool == "catalog":
        return _catalog()
    if tool == "candles":
        return _candles()
    return _per_instrument()


def _failing(tool: str, count: int) -> dict[str, Any]:
    if tool == "catalog":
        return _catalog(count)
    if tool == "candles":
        return _candles(count)
    return _per_instrument(count)


@pytest.mark.parametrize("tool", TOOLS)
def test_a_clean_report_on_exit_zero_passes(tool: str) -> None:
    verdict = summarise(tool, 0, _clean(tool))
    assert (verdict.verdict, verdict.failing, verdict.inputs_missing) == (PASSED, 0, 0)
    assert verdict.instruments == {
        _BTC: InstrumentVerdict(True, 0),
        _ETH: InstrumentVerdict(True, 0),
    }


@pytest.mark.parametrize("tool", TOOLS)
def test_a_planted_failing_instrument_never_summarises_to_pass(tool: str) -> None:
    verdict = summarise(tool, 1, _failing(tool, 3))
    assert (verdict.verdict, verdict.failing) == (FAILED, 3)
    assert verdict.instruments[_ETH] == InstrumentVerdict(False, 3)
    assert verdict.failing_instruments == (_ETH,)


@pytest.mark.parametrize("tool", TOOLS)
def test_exit_zero_with_passed_false_is_a_fail(tool: str) -> None:
    body = {**_clean(tool), "passed": False}
    assert summarise(tool, 0, body).verdict == FAILED


@pytest.mark.parametrize("tool", TOOLS)
def test_a_passing_report_with_a_failing_exit_is_a_fail(tool: str) -> None:
    assert summarise(tool, 1, _clean(tool)).verdict == FAILED


@pytest.mark.parametrize("tool", _PER_INSTRUMENT_TOOLS)
def test_a_report_contradicting_itself_is_never_a_pass(tool: str) -> None:
    body = {**_failing(tool, 2), "passed": True}
    assert summarise(tool, 0, body).verdict == FAILED


@pytest.mark.parametrize("tool", TOOLS)
def test_no_report_is_refused_with_its_reason(tool: str) -> None:
    refused = summarise(tool, 1, None)
    assert (refused.verdict, refused.reason) == (REFUSED, "no JSON report (exit 1)")
    killed = summarise(tool, 124, None, reason="killed after 3000 s")
    assert (killed.verdict, killed.reason) == (REFUSED, "killed after 3000 s")


@pytest.mark.parametrize(
    "body",
    [
        {"instruments": []},  # no verdict
        {"passed": "yes", "instruments": []},
        {"passed": True, "instruments": [{"instrument_id": _BTC, "passed": True}]},  # no count
        {"passed": True, "instruments": [{"passed": True, "failing": 0}]},  # no id
        {"passed": True, "instruments": [_instrument(_BTC, 0) | {"failing": -1}]},
        {"passed": True, "instruments": [_instrument(_BTC, 0) | {"failing": True}]},
        {"passed": True, "instruments": [_instrument(_BTC, 1), _instrument(_BTC, 0)]},  # twice
        {"passed": True, "instruments": [], "missing_raw_files": "none"},
        {"passed": True, "instruments": [], "coverage_present": None},
    ],
)
def test_a_report_missing_what_the_reduction_needs_is_refused(body: dict[str, Any]) -> None:
    verdict = summarise("conservation", 0, body)
    assert verdict.verdict == REFUSED
    assert verdict.reason.startswith("malformed report (exit 0): ")


def test_a_provisional_candles_run_is_never_a_pass() -> None:
    body = _candles(served="not checked")
    body["passed"] = True  # even a report claiming it: served bars unchecked is not a pass
    assert summarise("candles", 0, body).verdict == FAILED


def test_a_candles_instrument_passes_by_its_own_count() -> None:
    verdict = summarise("candles", 1, _candles(eth_failing=4))
    assert verdict.instruments == {
        _BTC: InstrumentVerdict(True, 0),
        _ETH: InstrumentVerdict(False, 4),
    }


def test_catalog_instruments_sum_their_parity_and_candle_entries() -> None:
    body = _catalog(eth_failing=2)
    body["candles"][1]["failing"] = 1
    body["failing"] = 3
    verdict = summarise("catalog", 1, body)
    assert verdict.instruments[_ETH] == InstrumentVerdict(False, 3)
    assert verdict.instruments[_BTC] == InstrumentVerdict(True, 0)
    assert verdict.failing == 3  # the top-level count, which also covers structure and rehearsal


def test_a_catalog_failing_outside_every_instrument_still_fails() -> None:
    body = {**_catalog(), "passed": False, "failing": 1}  # e.g. a structure or rehearsal count
    verdict = summarise("catalog", 1, body)
    assert (verdict.verdict, verdict.failing, verdict.failing_instruments) == (FAILED, 1, ())


@pytest.mark.parametrize("tool", ["catalog", "candles"])
def test_a_report_claiming_a_pass_with_a_failing_count_is_never_a_pass(tool: str) -> None:
    body = {**_clean(tool), "failing": 4}  # a count no instrument carries, `passed` left true
    verdict = summarise(tool, 0, body)
    assert (verdict.verdict, verdict.failing, verdict.failing_instruments) == (FAILED, 4, ())


@pytest.mark.parametrize("tool", TOOLS)
def test_a_report_judging_no_instrument_is_never_a_pass(tool: str) -> None:
    body = _clean(tool)
    for key in ("instruments", "parity", "candles"):
        if isinstance(body.get(key), list):
            body[key] = []
    verdict = summarise(tool, 0, body)
    assert (verdict.verdict, verdict.instruments) == (FAILED, {})


def test_missing_inputs_are_counted_and_named() -> None:
    body = _per_instrument(missing_raw_files=["raw/bybit/trades/2026-09-29T03"])
    body.update(coverage_present=False, passed=False)
    verdict = summarise("book", 1, body)
    assert (verdict.verdict, verdict.inputs_missing) == (FAILED, 2)
    assert verdict.missing_raw_files == ("raw/bybit/trades/2026-09-29T03",)


def test_an_unknown_tool_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown verification tool"):
        summarise("chaos", 0, {})


def test_parse_report_takes_only_a_json_object() -> None:
    assert parse_report('{"passed": true}') == {"passed": True}
    assert parse_report("") is None
    assert parse_report("conservation refused: day not closed") is None
    assert parse_report("[1, 2]") is None


def _passing() -> list[TypeVerdict]:
    return [summarise(tool, 0, _clean(tool)) for tool in TOOLS]


def test_a_day_is_verified_only_when_all_six_types_pass() -> None:
    day = day_verdict(_passing(), _CHECKED_AT)
    assert (day["verification"], day["checked_at"]) == (VERIFIED, _CHECKED_AT)
    assert list(day["types"]) == list(TOOLS)


@pytest.mark.parametrize("tool", TOOLS)
def test_one_failing_or_refused_type_makes_the_day_findings(tool: str) -> None:
    for planted in (summarise(tool, 1, _failing(tool, 1)), summarise(tool, 1, None)):
        types = [planted if v.tool == tool else v for v in _passing()]
        assert day_verdict(types, _CHECKED_AT)["verification"] == FINDINGS


def test_a_type_never_run_is_not_a_pass() -> None:
    assert day_verdict(_passing()[:-1], _CHECKED_AT)["verification"] == FINDINGS
    assert day_verdict([], _CHECKED_AT)["verification"] == FINDINGS


def test_the_json_form_is_compact_and_round_trips_through_json() -> None:
    body = _per_instrument(3, missing_raw_files=["raw/bybit/trades/2026-09-29T03"])
    verdict = summarise("trades", 1, body)
    assert json.loads(json.dumps(verdict.to_json())) == {
        "verdict": FAILED,
        "failing": 3,
        "inputs_missing": 1,
        "reason": "",
        "instruments": {
            _BTC: {"passed": True, "failing": 0},
            _ETH: {"passed": False, "failing": 3},
        },
    }


def _conservation_report(unexplained: int) -> DayReport:
    seconds = SecondCounts(86_400, 86_400, {}, 0, 0, 0, 0, ())
    return DayReport(
        venue="BYBIT",
        day="2026-09-29",
        coverage_file="coverage/bybit.jsonl",
        coverage_present=True,
        missing_raw_files=(),
        truncated_neighbour_files=(),
        instruments=(
            InstrumentReport(_BTC, TradeCounts(seen=5, archived=5), seconds),
            InstrumentReport(_ETH, TradeCounts(seen=5, unexplained=unexplained), seconds),
        ),
    )


def _stdout(report: DayReport) -> str:
    return json.dumps(conservation_json(report), indent=2)  # as the tool prints it


def test_the_conservation_tools_own_report_reduces_by_its_own_counts() -> None:
    # The real `report_json` through JSON, not a hand-written shape: the tool's own keys.
    clean = parse_report(_stdout(_conservation_report(0)))
    assert summarise("conservation", 0, clean).verdict == PASSED
    planted = parse_report(_stdout(_conservation_report(2)))
    verdict = summarise("conservation", 1, planted)
    assert (verdict.verdict, verdict.failing, verdict.failing_instruments) == (FAILED, 2, (_ETH,))
