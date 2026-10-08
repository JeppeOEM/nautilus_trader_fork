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
The liquidation cascade bot's parity (Story 33.14) on hand-written logs in
`LiquidationCascadeStrategy`'s record format: equal logs, a late arrival explained (within its
window, by the baseline's decaying bound past it, and the threshold crossings it causes), a quote
cadence decision, an unexplained difference, repeated event ids, one-sided records, a malformed
record and mismatched starts refused; then the tool end
to end (`python3 -m verification.bot_parity --json`) with a cascade bot beside nothing else, over
`test_bot_parity`'s raw catalog writer (the flushed-catalog probe reads it).
"""

import json
import math
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from verification import bot_parity as tool
from verification.domain.bot_parity import MalformedRecord
from verification.domain.bot_parity import SegmentMismatch
from verification.domain.cascade_parity import LATE_ARRIVAL
from verification.domain.cascade_parity import QUOTE_CADENCE
from verification.domain.cascade_parity import UNEXPLAINED
from verification.domain.cascade_parity import CascadeReport
from verification.domain.cascade_parity import compare_cascade
from verification.domain.cascade_parity import latest_segment
from verification.tests.test_bot_parity import IID
from verification.tests.test_bot_parity import _all_seconds
from verification.tests.test_bot_parity import _args
from verification.tests.test_bot_parity import _write_catalog


NS = 1_000_000_000
BOT = "cascade-btc-01"
# The live start, off the second as the live clock's is; ticks fall on the whole seconds after it.
START_NS = 1_790_000_000 * NS + 754_043_000
TICKS = 30
WINDOW_S = 5

Record = dict[str, Any]


def _tick_ts(k: int) -> int:
    return (START_NS // NS + k) * NS


def _start() -> Record:
    return {
        "kind": "start",
        "strategy": "liquidation_cascade",
        "bot_id": BOT,
        "instrument_id": IID,
        "ts_ns": START_NS,
        "window_s": WINDOW_S,
        "baseline_s": 60,
        "intensity_threshold": 3.0,
        "decay_ratio": 0.5,
        "sides": ["short"],
        "trade_size": "0.001",
    }


def _cycle(kind: str, ts_ns: int, rate: float, **extra: Any) -> Record:
    return {
        "kind": kind,
        "bot_id": BOT,
        "instrument_id": IID,
        "ts_ns": ts_ns,
        **extra,
        "rate_long": rate,
        "rate_short": 0.0,
        "baseline": 10.0 + rate / 100,
        "intensity": rate / (10.0 + rate / 100),
        "direction": -1 if rate > 0 else 0,
        "active": rate > 30,
        "spent": False,
        "decision": "none",
        "reason": None,
    }


# One liquidation, event "e10", stamped 0.9 s into tick 10's second (the replay's time for it).
_EVENT_TS = _tick_ts(10) + 900_000_000


def _log() -> list[Record]:
    """Ticks 1..TICKS, the window rate stepping up from tick 11 by the one liquidation."""
    records: list[Record] = [_start()]
    for k in range(1, TICKS + 1):
        rate = 0.0 if k <= 10 else 100.0
        records.append(_cycle("tick", _tick_ts(k), rate))
        if k == 10:
            records.append(_cycle("liquidation", _EVENT_TS, 100.0, venue_event_id="e10"))
    return records


def _lines(records: list[Record]) -> list[tuple[str, str]]:
    return [(f"x:{n}", json.dumps(r)) for n, r in enumerate(records, start=1)]


def _compare(live: list[Record], replay: list[Record]) -> CascadeReport:
    return compare_cascade(latest_segment(_lines(live)), latest_segment(_lines(replay)))


def _at(log: list[Record], kind: str, ts_ns: int) -> Record:
    return next(r for r in log if r["kind"] == kind and r["ts_ns"] == ts_ns)


def _make_late(log: list[Record]) -> None:
    """
    Rewrite the log as if the live bridge delivered "e10" after tick 11 fired: tick 11 never saw
    it (rate 0), and the strategy fed it at the clock, tick 11's second.
    """
    event = next(r for r in log if r.get("venue_event_id") == "e10")
    log.remove(event)
    tick = _at(log, "tick", _tick_ts(11))
    tick.update(_cycle("tick", _tick_ts(11), 0.0))
    event["ts_ns"] = _tick_ts(11)
    log.insert(log.index(tick) + 1, event)


# --- the classes ---------------------------------------------------------------------------------


def test_equal_logs_pair_every_record_and_pass() -> None:
    report = _compare(_log(), _log())
    assert dict(report.paired) == {"tick": TICKS, "liquidation": 1}
    assert all(stats.equal == stats.paired for stats in report.fields.values())
    assert report.cycle_classes == {}
    assert report.unexplained == 0


def test_a_row_the_live_bridge_delivered_after_the_clock_passed_it_is_a_late_arrival() -> None:
    live = _log()
    _make_late(live)
    report = _compare(live, _log())
    assert report.late_rows == 1
    assert report.fields["ts_ns"].classes == {LATE_ARRIVAL: 1}  # placed at the clock
    assert report.fields["rate_long"].classes == {LATE_ARRIVAL: 1}  # tick 11 without it
    assert report.cycle_classes == {LATE_ARRIVAL: 2}  # tick 11 and the row's own record
    assert report.unexplained == 0


def _row_after_tick_11() -> list[Record]:
    """`_log` with "e10" received 0.2 s after tick 11's second: the replay's order, tick 11 first."""
    log = _log()
    event = next(r for r in log if r.get("venue_event_id") == "e10")
    log.remove(event)
    tick = _at(log, "tick", _tick_ts(11))
    tick.update(_cycle("tick", _tick_ts(11), 0.0))
    event["ts_ns"] = _tick_ts(11) + 200_000_000
    log.insert(log.index(tick) + 1, event)
    return log


def test_a_tick_whose_callback_ran_after_a_later_row_was_fed_is_a_late_arrival() -> None:
    # Live, the row received after tick 11's second was fed before the timer's callback ran: the
    # tick is written after it, on its own second, holding the row the replay's tick 11 precedes.
    live = _row_after_tick_11()
    tick = _at(live, "tick", _tick_ts(11))
    live.remove(tick)
    tick.update(_cycle("tick", _tick_ts(11), 100.0))
    event = next(r for r in live if r.get("venue_event_id") == "e10")
    live.insert(live.index(event) + 1, tick)
    report = _compare(live, _row_after_tick_11())
    assert report.late_rows == 0
    assert report.fields["rate_long"].classes == {LATE_ARRIVAL: 1}
    assert report.cycle_classes == {LATE_ARRIVAL: 1}
    assert report.unexplained == 0
    # The same tick written in its order is no late tick: its difference is unexplained.
    live.remove(tick)
    live.insert(live.index(event), tick)
    assert _compare(live, _row_after_tick_11()).cycle_classes == {UNEXPLAINED: 1}


def _expiring_log() -> list[Record]:
    """`_log` with the row expiring from the window: rate 100 over ticks 11..15 only."""
    log = _log()
    for k in range(16, TICKS + 1):
        _at(log, "tick", _tick_ts(k)).update(_cycle("tick", _tick_ts(k), 0.0))
    return log


def test_a_row_the_live_strategy_refused_as_stale_pairs_as_a_late_arrival() -> None:
    # Delivered after tick 20, past its window: the live strategy writes its record at the clock
    # with reason `stale_row` and never feeds it, so no live tick ever saw it.
    live = _expiring_log()
    event = next(r for r in live if r.get("venue_event_id") == "e10")
    live.remove(event)
    for k in range(11, 16):
        _at(live, "tick", _tick_ts(k)).update(_cycle("tick", _tick_ts(k), 0.0))
    stale = _cycle("liquidation", _tick_ts(20), 0.0, venue_event_id="e10")
    stale["reason"] = "stale_row"
    live.insert(live.index(_at(live, "tick", _tick_ts(20))) + 1, stale)
    report = _compare(live, _expiring_log())
    assert dict(report.paired)["liquidation"] == 1
    assert not report.replay_only
    assert not report.live_only
    assert report.fields["rate_long"].classes == {LATE_ARRIVAL: 6}  # ticks 11..15 and the row
    assert report.unexplained == 0


def test_a_rate_past_the_late_rows_window_is_unexplained() -> None:
    live = _log()
    _make_late(live)
    _at(live, "tick", _tick_ts(25))["rate_long"] += 1.0  # 14 s after the row: past 5 s + 1 s
    report = _compare(live, _log())
    assert report.fields["rate_long"].classes == {LATE_ARRIVAL: 1, UNEXPLAINED: 1}
    assert report.unexplained == 1


# The late row's window [10.9 s, 16.9 s] ends before tick 17, the first paired record after it.
_FIRST_AFTER = 17
_BASELINE_S = 60
_D0 = 0.5


def _decaying_baseline(live: list[Record], scale: float = 1.0) -> None:
    """
    Give the live log the baseline a late row leaves past its window: `_D0` above the replay's at
    tick 17, decaying at the baseline's pace (`exp(-dt / baseline_s)`), times `scale`, and the
    intensity that baseline gives (the rates are equal).
    """
    t0 = _tick_ts(_FIRST_AFTER)
    for k in range(_FIRST_AFTER, TICKS + 1):
        tick = _at(live, "tick", _tick_ts(k))
        diff = _D0 * math.exp(-(_tick_ts(k) - t0) / (_BASELINE_S * NS)) * (1 if k == 17 else scale)
        tick["baseline"] += diff
        tick["intensity"] = tick["rate_long"] / tick["baseline"]


def test_a_late_rows_baseline_decaying_at_its_pace_past_the_window_is_explained() -> None:
    live = _log()
    _make_late(live)
    _decaying_baseline(live)
    report = _compare(live, _log())
    after = TICKS - _FIRST_AFTER + 1
    assert report.fields["baseline"].classes == {LATE_ARRIVAL: 1 + after}  # tick 11, 17..30
    assert report.fields["intensity"].classes == {LATE_ARRIVAL: 1 + after}
    assert report.unexplained == 0


def test_a_baseline_difference_above_the_decaying_bound_is_unexplained() -> None:
    live = _log()
    _make_late(live)
    _decaying_baseline(live, scale=1.01)  # 1 % above the bound from tick 18 on
    report = _compare(live, _log())
    beyond = TICKS - _FIRST_AFTER
    assert report.fields["baseline"].classes == {LATE_ARRIVAL: 2, UNEXPLAINED: beyond}
    assert report.unexplained == beyond


def test_a_later_baseline_difference_with_none_at_the_windows_end_is_unexplained() -> None:
    # The old rule explained every later EMA difference after any late row: a masked defect.
    live = _log()
    _make_late(live)
    _at(live, "tick", _tick_ts(25))["baseline"] += 1e-6
    report = _compare(live, _log())
    assert report.fields["baseline"].classes == {LATE_ARRIVAL: 1, UNEXPLAINED: 1}
    assert report.unexplained == 1


def test_a_threshold_crossing_of_the_late_rows_drift_is_a_late_arrival() -> None:
    live = _log()
    _make_late(live)
    _decaying_baseline(live)
    _at(live, "tick", _tick_ts(20)).update(active=False, direction=0, decision="enter_short")
    report = _compare(live, _log())
    for field in ("active", "direction"):  # tick 11's (in the window) and tick 20's
        assert report.fields[field].classes == {LATE_ARRIVAL: 2}
    assert report.fields["decision"].classes == {LATE_ARRIVAL: 1}
    assert report.unexplained == 0


def test_a_threshold_crossing_beside_an_unexplained_float_is_unexplained() -> None:
    live = _log()
    _make_late(live)
    _at(live, "tick", _tick_ts(20)).update(active=False, rate_long=99.0, decision="enter_short")
    report = _compare(live, _log())
    for field in ("active", "rate_long", "decision"):  # tick 20's; tick 11's are late arrivals
        assert report.fields[field].classes[UNEXPLAINED] == 1
    assert report.unexplained == 1


def test_a_difference_without_a_late_row_is_unexplained() -> None:
    replay = _log()
    _at(replay, "tick", _tick_ts(20))["intensity"] *= 1 + 1e-6
    report = _compare(_log(), replay)
    assert report.fields["intensity"].classes == {UNEXPLAINED: 1}
    assert report.fields["intensity"].max_abs_diff == pytest.approx(
        _cycle("tick", 0, 100.0)["intensity"] * 1e-6
    )
    assert report.unexplained == 1


def test_a_float_within_the_relative_tolerance_is_equal() -> None:
    replay = _log()
    _at(replay, "tick", _tick_ts(20))["rate_long"] *= 1 + 1e-12
    assert _compare(_log(), replay).unexplained == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [("active", False), ("direction", 1), ("spent", True)],
)
def test_an_exact_field_that_differs_without_a_late_row_is_unexplained(
    field: str, value: object
) -> None:
    replay = _log()
    _at(replay, "tick", _tick_ts(20))[field] = value
    report = _compare(_log(), replay)
    assert report.fields[field].classes == {UNEXPLAINED: 1}
    assert report.unexplained == 1


def test_a_decision_over_agreeing_indicators_is_quote_cadence() -> None:
    # The replay's one quote a second against the live bot's every quote: an exit's price.
    live = _log()
    _at(live, "tick", _tick_ts(20)).update(decision="exit", reason="stop")
    report = _compare(live, _log())
    assert report.fields["decision"].classes == {QUOTE_CADENCE: 1}
    assert report.cycle_classes == {QUOTE_CADENCE: 1}
    assert report.unexplained == 0


def test_a_decision_over_a_differing_indicator_is_unexplained() -> None:
    live = _log()
    _at(live, "tick", _tick_ts(20)).update(decision="exit", rate_short=1.0)
    report = _compare(live, _log())
    assert report.fields["decision"].classes == {UNEXPLAINED: 1}
    assert report.unexplained == 1


def test_a_reason_alone_is_informational() -> None:
    live, replay = _log(), _log()
    for log, reason in ((live, "stop"), (replay, "spent")):
        _at(log, "tick", _tick_ts(20)).update(decision="exit", reason=reason)
    report = _compare(live, replay)
    assert (report.reasons, report.unexplained) == (1, 0)


def test_a_liquidation_on_one_side_only_fails() -> None:
    live = [r for r in _log() if r.get("venue_event_id") != "e10"]
    report = _compare(live, _log())
    assert dict(report.replay_only) == {"liquidation": 1}
    assert report.unexplained >= 1
    report = _compare(_log(), live)
    assert dict(report.live_only) == {"liquidation": 1}


def test_a_side_stopping_short_truncates_the_other() -> None:
    replay = _log()[:-5]
    report = _compare(_log(), replay)
    assert report.truncated == 5
    assert report.unexplained == 5


@pytest.mark.parametrize(
    "edit",
    [
        lambda r: r.update(direction=2),
        lambda r: r.update(active="yes"),
        lambda r: r.update(decision="hold"),
        lambda r: r.update(rate_long="1.0"),
        lambda r: r.pop("reason"),
        lambda r: r.update(extra=1),
        lambda r: r.update(kind="book"),
    ],
)
def test_a_malformed_record_is_refused(edit: Callable[[Record], object]) -> None:
    log = _log()
    edit(log[3])
    with pytest.raises(MalformedRecord):
        latest_segment(_lines(log))


def test_a_liquidation_without_its_event_id_is_refused() -> None:
    log = _log()
    next(r for r in log if r["kind"] == "liquidation").pop("venue_event_id")
    with pytest.raises(MalformedRecord, match="liquidation keys"):
        latest_segment(_lines(log))


def _repeat_event(log: list[Record]) -> None:
    """Append a second liquidation record of event "e10" (a repeated synthesized id) at tick 20."""
    tick = _at(log, "tick", _tick_ts(20))
    repeat = _cycle("liquidation", _tick_ts(20) + 500_000_000, 100.0, venue_event_id="e10")
    log.insert(log.index(tick) + 1, repeat)


def test_a_repeated_event_id_pairs_by_its_occurrence() -> None:
    live, replay = _log(), _log()
    _repeat_event(live)
    _repeat_event(replay)
    report = _compare(live, replay)
    assert dict(report.paired) == {"tick": TICKS, "liquidation": 2}
    assert report.unexplained == 0
    _repeat_event(live)  # a third occurrence on the live side only
    assert dict(_compare(live, replay).live_only) == {"liquidation": 1}


def test_two_records_of_one_key_are_refused() -> None:
    log = _log()
    log.append(dict(log[-1]))
    with pytest.raises(MalformedRecord, match="two tick records"):
        _compare(log, _log())


def test_starts_of_other_parameters_or_another_time_are_refused() -> None:
    replay = _log()
    replay[0]["window_s"] = 6
    with pytest.raises(SegmentMismatch, match="live start"):
        _compare(_log(), replay)
    replay = _log()
    replay[0]["ts_ns"] += 1_000
    with pytest.raises(SegmentMismatch, match="baseline history"):
        _compare(_log(), replay)


# --- the tool end to end -------------------------------------------------------------------------


def _write(directory: Path, records: list[Record]) -> None:
    directory.mkdir(exist_ok=True)
    text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    (directory / f"{BOT}.jsonl").write_text(text)


@pytest.fixture(autouse=True)
def _no_ledger_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    monkeypatch.delenv("CATALOG_PATH", raising=False)


def _run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], live: list[Record], replay: list[Record]
) -> tuple[int, dict[str, Any], str]:
    _write(tmp_path / "live", live)
    _write(tmp_path / "replay", replay)
    catalog = _write_catalog(tmp_path, _all_seconds(), [])
    status = tool.main([*_args(tmp_path, catalog), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert tool.main(_args(tmp_path, catalog)) == status
    return status, report["bots"][0], capsys.readouterr().out


def test_the_tool_reports_a_cascade_bot_with_its_label_and_passes_equal_logs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot, text = _run(tmp_path, capsys, _log(), _log())
    assert status == 0
    assert (bot["strategy"], bot["unexplained"]) == ("liquidation_cascade", 0)
    assert bot["paired"] == {"tick": TICKS, "liquidation": 1}
    assert bot["fields"]["intensity"]["equal_share"] == 1.0
    assert f"{BOT} {IID} [liquidation_cascade]: PASS" in text


def test_the_tool_fails_an_unexplained_cascade_difference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    replay = _log()
    _at(replay, "tick", _tick_ts(20))["active"] = False
    status, bot, text = _run(tmp_path, capsys, _log(), replay)
    assert status == 1
    assert bot["cycle_classes"] == {"unexplained": 1}
    assert "[liquidation_cascade]: FAIL (1 unexplained)" in text


def test_the_tool_refuses_a_log_of_a_strategy_without_parity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = _log()
    live[0]["strategy"] = "candle_pattern"
    _write(tmp_path / "live", live)
    _write(tmp_path / "replay", _log())
    catalog = _write_catalog(tmp_path, _all_seconds(), [])
    with pytest.raises(SystemExit, match="no parity for strategy 'candle_pattern'"):
        tool.main(_args(tmp_path, catalog))


def test_the_tool_passes_a_quote_cadence_decision_and_names_the_class(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = _log()
    _at(live, "tick", _tick_ts(20)).update(decision="exit", reason="stop")
    status, bot, text = _run(tmp_path, capsys, live, _log())
    assert status == 0
    assert bot["cycle_classes"] == {QUOTE_CADENCE: 1}
    assert "late_arrival, quote_cadence, unexplained" in text
