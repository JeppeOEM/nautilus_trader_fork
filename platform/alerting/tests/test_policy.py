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
24.3 with only their imports changed: the pure `evaluate` state machine, `status_of` and `render`.
"""

from collections.abc import Sequence

from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.domain.alert import status_of
from alerting.domain.policy import FREQUENCIES
from alerting.domain.policy import FiringPolicy
from alerting.domain.policy import RunState
from alerting.domain.policy import evaluate
from alerting.domain.policy import render


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


def _run(alert: Alert, ticks: Sequence[tuple[int, float]]) -> list[int]:
    """Feed (seconds-after-T0, price) ticks through evaluate(); return indexes that fired."""
    state = RunState()
    return [
        i for i, (s, p) in enumerate(ticks) if evaluate(alert, state, p, _T0 + s * _S) is not None
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
    reported = [evaluate(alert, state, p, _T0 + s * _S) for s, p in ticks]
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
