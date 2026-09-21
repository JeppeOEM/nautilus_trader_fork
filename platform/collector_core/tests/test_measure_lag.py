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
"""measure_lag's pure parts on real TradeTicks (the live run is manual, see its docstring)."""

from collector_core.measure_lag import LagRecorder
from collector_core.measure_lag import percentile
from collector_core.measure_lag import report
from collector_core.measure_lag import suggest_hold_back
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_MS = 1_000_000


def _trade(n: int, lag_ms: int) -> TradeTick:
    ts_event = 1_789_000_000_000_000_000
    return TradeTick(
        InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT"),
        Price.from_str("100.0"),
        Quantity.from_str("1.0"),
        AggressorSide.BUYER,
        TradeId(str(n)),
        ts_event,
        ts_event + lag_ms * _MS,
    )


def _recording(stale_ms: int = 10_000) -> LagRecorder:
    recorder = LagRecorder(stale_ms * _MS)
    recorder.recording = True
    return recorder


def test_percentile_is_nearest_rank() -> None:
    values = list(range(1, 1001))
    assert (percentile(values, 0.5), percentile(values, 0.999), percentile(values, 1.0)) == (
        500,
        999,
        1000,
    )


def test_hold_back_is_trade_p999_rounded_up_to_half_a_second() -> None:
    assert [suggest_hold_back(ms * _MS) for ms in (0, 1, 500, 501, 1700)] == [
        0.0,
        0.5,
        0.5,
        1.0,
        2.0,
    ]


def test_recorder_keeps_lag_per_kind_and_counts_replayed_trades() -> None:
    recorder = _recording()
    for n, lag in enumerate((120, 80, 20_000)):
        recorder(_trade(n, lag))
    assert (recorder.lags["TradeTick"], recorder.replayed_trades) == ([120 * _MS, 80 * _MS], 1)


def test_recorder_ignores_the_warm_up_and_objects_without_both_clocks() -> None:
    recorder = LagRecorder(10_000 * _MS)
    recorder(_trade(1, 100))  # still warming up
    recorder.recording = True
    recorder(object())
    assert dict(recorder.lags) == {}


def test_report_suggests_the_hold_back_from_trades() -> None:
    recorder = _recording()
    for n in range(1000):
        recorder(_trade(n, 100 if n < 998 else 1_200))
    assert report(recorder)[-1].endswith(": 1.5")
