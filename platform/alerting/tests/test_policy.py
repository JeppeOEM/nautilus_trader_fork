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
Story 20.2's frequency/expiration semantics, moved from `data_api/tests/test_alerts.py` in Story
24.3 with only their imports changed: the pure state machine, `status_of` and `render`. Story 33.8
generalised `evaluate(alert, state, price, ts_ns)` to `step(alert, state, Sample(price, ts_ns))`;
the pre-33.8 `price_cross` scenarios below changed in that call shape only, and the new cases
cover the other kinds under the three frequencies, closed samples and the two new placeholders.
"""

from collections.abc import Sequence

from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.domain.alert import status_of
from alerting.domain.policy import FREQUENCIES
from alerting.domain.policy import Fire
from alerting.domain.policy import FiringPolicy
from alerting.domain.policy import RunState
from alerting.domain.policy import Sample
from alerting.domain.policy import advance
from alerting.domain.policy import render
from alerting.domain.policy import step


_IID = "BTC-USD-PERP.DYDX"
_S = 1_000_000_000
_T0 = 1_800_000_000 * _S  # multiple of 60s


def _alert(frequency: str = "only_once", **kw: object) -> Alert:
    fields: dict[str, object] = {
        "instrument_id": _IID,
        "level": 100.0,
        "frequency": frequency,
        "bar_seconds": 60,
        "template": "{{ticker}} {{close}}",
        "webhook_url": "http://localhost:1/hook",
    }
    fields.update(kw)
    return new_alert(**fields)


def _value(fire: Fire | None) -> float | None:
    return None if fire is None else fire.value


def _run(alert: Alert, ticks: Sequence[tuple[int, float]]) -> list[int]:
    """Feed (seconds-after-T0, price) ticks through step(); return indexes that fired."""
    state = RunState()
    return [
        i
        for i, (s, p) in enumerate(ticks)
        if step(alert, state, Sample(p, _T0 + s * _S)) is not None
    ]


def test_cross_up_and_down_fire_but_touching_without_crossing_does_not() -> None:
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 99), (1, 101)]) == [1]
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 101), (1, 99)]) == [1]
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 99), (1, 99.5)]) == []
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 100), (1, 100)]) == []


def test_once_per_bar_fires_at_most_once_per_bar() -> None:
    # bar 0 (0-59s): crosses at idx 1, 2, 3 -> only idx 1 fires. Bar 1 (60s+): crosses at idx 4
    # (101->99 across the bar boundary) and 5 -> only idx 4 fires.
    ticks = [(0, 99), (1, 101), (2, 99), (3, 101), (60, 99), (61, 101)]
    assert _run(_alert("once_per_bar"), ticks) == [1, 4]


def test_once_per_bar_close_decides_on_the_bar_close_not_intrabar_wicks() -> None:
    # bar 0 closes 99, bar 1 wicks to 101 but closes 99 -> no fire; bar 2 closes 101 -> fires
    # on the first tick of bar 3 (the bar's close is only known once the bucket rolls over).
    ticks = [(0, 99), (30, 99), (60, 101), (90, 99), (120, 101), (150, 101), (180, 101)]
    assert _run(_alert("once_per_bar_close"), ticks) == [6]


def test_once_per_bar_close_reports_the_closed_bars_close_price() -> None:
    alert, state = _alert("once_per_bar_close"), RunState()
    ticks = [(0, 99), (60, 99), (90, 101), (119, 102), (120, 50)]  # bar 1 closes at 102
    reported = [_value(step(alert, state, Sample(p, _T0 + s * _S))) for s, p in ticks]
    assert reported == [None, None, None, None, 102]


def test_expired_alert_never_fires_and_reports_expired() -> None:
    alert = _alert("once_per_bar", bar_seconds=1, expires_at_ns=_T0 + _S)
    assert _run(alert, [(0, 99), (1, 101), (2, 99), (3, 101)]) == []
    assert status_of(alert, _T0 + _S) == "expired"
    assert status_of(alert, _T0) == "active"


def test_render_substitutes_all_four_placeholders() -> None:
    out = render("{{ticker}}|{{close}}|{{time}}|{{interval}}|{{ticker}}", _IID, 65000.5, _T0, 60)
    assert out == f"{_IID}|65000.5|2027-01-15T08:00:00+00:00|60|{_IID}"


def test_firing_policy_values_are_the_frozen_frequency_text() -> None:
    # The stored `alerts.toml` text and the `/api/alerts` literal (AD-D12): a renamed member value
    # would orphan every saved alert.
    assert FREQUENCIES == ("once_per_bar_close", "once_per_bar", "only_once")
    assert [type(f) for f in FREQUENCIES] == [str, str, str]
    assert FiringPolicy("only_once") is FiringPolicy.ONLY_ONCE
    assert FiringPolicy.ONCE_PER_BAR == "once_per_bar"


# --- Story 33.8: every kind through the same three policies -------------------------------------


def _kind(kind: str, frequency: str, bar_seconds: int = 60, **fields: object) -> Alert:
    condition = {"kind": kind, **fields}
    level = fields.get("level")
    return _alert(frequency, bar_seconds=bar_seconds, level=level, condition=condition)


def test_cross_up_fires_on_the_way_up_only() -> None:
    up = _kind("price_cross_up", "once_per_bar", bar_seconds=1, level=100.0)
    assert _run(up, [(0, 99), (1, 100)]) == [1]
    assert _run(up, [(0, 101), (1, 99)]) == []


def test_cross_down_fires_on_the_way_down_only() -> None:
    down = _kind("price_cross_down", "once_per_bar", bar_seconds=1, level=100.0)
    assert _run(down, [(0, 101), (1, 100)]) == [1]
    assert _run(down, [(0, 99), (1, 101)]) == []


def test_above_once_per_bar_fires_once_per_bucket_while_above() -> None:
    # 101 and 102 share bucket 0 (only 101 fires), 103 is in bucket 1 and fires again.
    above = _kind("price_above", "once_per_bar", level=100.0)
    assert _run(above, [(0, 101), (1, 102), (60, 103)]) == [0, 2]


def test_below_only_once_fires_a_single_time() -> None:
    below = _kind("price_below", "only_once", level=100.0)
    alert, state = below, RunState()
    fired = []
    for i, (s, p) in enumerate([(0, 99), (60, 98)]):
        if step(alert, state, Sample(p, _T0 + s * _S)) is not None:
            fired.append(i)
            alert.triggered = True  # what `record_fire` does for only_once
    assert fired == [0]


def test_above_once_per_bar_close_decides_on_the_buckets_last_value() -> None:
    # bucket 0 ends at 99 (a 101 wick inside it), bucket 1 ends at 101: the fire is decided on the
    # first sample of bucket 2 and reports bucket 1's last value.
    alert, state = _kind("price_above", "once_per_bar_close", level=100.0), RunState()
    ticks = [(0, 101), (30, 99), (60, 99), (90, 101), (120, 50)]
    reported = [_value(step(alert, state, Sample(p, _T0 + s * _S))) for s, p in ticks]
    assert reported == [None, None, None, None, 101.0]


def test_a_closed_sample_is_evaluated_at_once_under_every_frequency() -> None:
    # An indicator reading carries its own previous value: crosses_up 70 from 69 to 71.
    for frequency in FREQUENCIES:
        alert = _kind(
            "indicator",
            frequency,
            name="RelativeStrengthIndex",
            params={"period": 14},
            output="value",
            op="crosses_up",
            value=70.0,
        )
        fired = step(alert, RunState(), Sample(71.0, _T0, closed=True, prev=69.0))
        assert _value(fired) == 71.0, frequency


def test_a_closed_sample_without_a_cross_does_not_fire() -> None:
    alert = _kind(
        "indicator",
        "once_per_bar",
        name="RelativeStrengthIndex",
        params={"period": 14},
        output="value",
        op="crosses_up",
        value=70.0,
    )
    assert step(alert, RunState(), Sample(72.0, _T0, closed=True, prev=71.0)) is None


def test_a_sample_reports_its_report_value_not_its_compared_value() -> None:
    # A trendline cross compares close - line(t) but reports the close.
    alert = _kind("trendline_cross", "once_per_bar", drawing_id="d1")
    state = RunState()
    assert step(alert, state, Sample(-10.0, _T0, report=290.0)) is None
    assert _value(step(alert, state, Sample(10.0, _T0 + _S, report=310.0))) == 310.0


def test_an_invalid_alert_never_steps() -> None:
    alert = _kind("price_above", "once_per_bar", level=100.0)
    alert.invalid_reason = "gone"
    assert step(alert, RunState(), Sample(101.0, _T0)) is None
    assert status_of(alert, _T0) == "invalid"


def test_invalid_is_reported_before_triggered_and_expired() -> None:
    alert = _alert("only_once", expires_at_ns=_T0)
    alert.triggered = True
    alert.invalid_reason = "drawing d1 no longer exists"
    assert status_of(alert, _T0 + _S) == "invalid"


def test_render_substitutes_value_and_condition_and_a_missing_close() -> None:
    out = render(
        "{{value}}|{{condition}}|{{close}}", _IID, None, _T0, 60, value=0.0004, condition="x"
    )
    assert out == "0.0004|x|n/a"


# --- time going backwards (Story 33.8 review) -----------------------------------------------------


def test_a_late_sample_never_lets_once_per_bar_fire_twice_in_one_bucket() -> None:
    # 101 fires bucket 1; a 102 stamped back in bucket 0 (a lagging liquidation row) belongs to
    # bucket 1, which has fired; 103 is still bucket 1.
    above = _kind("price_above", "once_per_bar", level=100.0)
    assert _run(above, [(60, 101), (30, 102), (61, 103), (120, 104)]) == [0, 3]


def test_a_late_sample_never_rolls_a_bar_close_bucket_back() -> None:
    # Without the guard the late 99 would close bucket 1 early (a spurious fire at index 2) and the
    # 101 at 90 s would roll over once more.
    alert, state = _kind("price_above", "once_per_bar_close", level=100.0), RunState()
    ticks = [(0, 99), (60, 101), (30, 99), (90, 101), (120, 50)]
    reported = [_value(step(alert, state, Sample(p, _T0 + s * _S))) for s, p in ticks]
    assert reported == [None, None, None, None, 101.0]


def test_advance_decides_a_passed_bucket_once_on_its_last_sample() -> None:
    alert, state = _kind("funding_above", "once_per_bar_close", rate=0.0003), RunState()
    assert step(alert, state, Sample(0.0004, _T0 + 20 * _S, close=None)) is None
    assert advance(alert, state, _T0 + 59 * _S) is None  # the bucket has not ended
    fired = advance(alert, state, _T0 + 61 * _S)
    assert fired == Fire(0.0004, None, _T0 + 20 * _S)
    assert advance(alert, state, _T0 + 200 * _S) is None  # decided once
    assert step(alert, state, Sample(0.0001, _T0 + 300 * _S)) is None  # no second rollover


def test_advance_leaves_every_other_frequency_alone() -> None:
    for frequency in ("once_per_bar", "only_once"):
        alert, state = _kind("funding_above", frequency, rate=0.0003), RunState()
        step(alert, state, Sample(0.0001, _T0))
        assert advance(alert, state, _T0 + 3600 * _S) is None
