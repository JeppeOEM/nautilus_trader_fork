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
Unit tests for custom_indicators.py's dispatch mechanism (Story 10.1).

No real custom indicator exists yet (CVD/Cancel Pressure/OFI are Stories 10.2-10.4) --
these tests register a placeholder entry directly to prove replay_indicator's dispatch
contract and catalog_json's shape, matching chart_indicators.py's own dispatch (proven
separately in test_chart_indicators.py) rather than duplicating that coverage here.
"""

import pytest

from ml_signals import custom_indicators as ci
from ml_signals.custom_indicators import CustomIndicatorSpec
from ml_signals.custom_indicators import ReplayWindow


def _window() -> ReplayWindow:
    return ReplayWindow(instrument_id="BTC-USD-PERP.DYDX", bar_seconds=60, start_ms=None, end_ms=None)


def _echo_replay(candles: list[dict], params: dict, window: ReplayWindow) -> dict[str, list[float | None]]:
    """A placeholder replay: one output ("value") per candle, scaled by params["scale"]."""
    return {"value": [c["c"] * params["scale"] for c in candles]}


@pytest.fixture(autouse=True)
def _placeholder_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        ci.CUSTOM_INDICATOR_CATALOG,
        "PlaceholderCustom",
        CustomIndicatorSpec(params={"scale": 1.0}, panel="histogram", replay=_echo_replay),
    )


def test_replay_indicator_calls_the_registered_replay_function() -> None:
    candles = [{"t": 0, "c": 2.0}, {"t": 60_000, "c": 3.0}]
    result = ci.replay_indicator(candles, "PlaceholderCustom", {"scale": 2.0}, _window())
    assert result == {"value": [4.0, 6.0]}


def test_replay_indicator_merges_params_over_catalog_defaults() -> None:
    candles = [{"t": 0, "c": 5.0}]
    result = ci.replay_indicator(candles, "PlaceholderCustom", {}, _window())
    assert result == {"value": [5.0]}  # default scale=1.0 applied


def test_replay_indicator_raises_for_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown custom indicator"):
        ci.replay_indicator([], "NotRegistered", {}, _window())


def test_catalog_json_returns_params_and_panel_per_entry() -> None:
    assert ci.catalog_json() == {"PlaceholderCustom": {"params": {"scale": 1.0}, "panel": "histogram"}}
    assert "category" not in ci.catalog_json()["PlaceholderCustom"]  # tagged only at the merge point
