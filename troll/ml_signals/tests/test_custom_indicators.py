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
Unit tests for custom_indicators.py's dispatch mechanism (Story 10.1) and its registered
indicators (CumulativeVolumeDelta, Story 10.2; Cancel Pressure/OFI, Stories 10.3-10.4).

The dispatch-mechanism tests below register a placeholder entry directly to prove
replay_indicator's dispatch contract and catalog_json's shape, matching chart_indicators.py's
own dispatch (proven separately in test_chart_indicators.py) rather than duplicating that
coverage here -- they use subset/membership assertions, not exact-dict equality, since real
entries (CumulativeVolumeDelta and later additions) live in the same module-level catalog.
"""

import tempfile

import pytest
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from ml_signals import custom_indicators as ci
from ml_signals.custom_indicators import CustomIndicatorSpec
from ml_signals.custom_indicators import ReplayWindow

_IID = "BTC-USD-PERP.DYDX"
_TS_NS = 1_700_000_000_000_000_000


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
    # Subset check, not exact-dict equality -- the catalog also carries real entries
    # (CumulativeVolumeDelta, Story 10.2+) alongside whatever a test adds via monkeypatch.
    assert ci.catalog_json()["PlaceholderCustom"] == {"params": {"scale": 1.0}, "panel": "histogram"}
    assert "category" not in ci.catalog_json()["PlaceholderCustom"]  # tagged only at the merge point


# -- Story 10.2: CumulativeVolumeDelta -------------------------------------------------------

def _write_snapshots(
    tmp_path: str, buy_sell_pairs: list[tuple[float, float]], base_ns: int = _TS_NS,
) -> tuple[int, int]:
    """Write one DydxSecondSnapshot per (buy_volume, sell_volume) pair, 1s apart."""
    step_ns = 1_000_000_000
    snapshots = [
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(_IID),
            bid_prices=[100.0], bid_sizes=[1.0], ask_prices=[101.0], ask_sizes=[1.0],
            buy_volume=buy_vol, sell_volume=sell_vol, buy_count=1, sell_count=1,
            ts_event=base_ns + i * step_ns, ts_init=base_ns + i * step_ns,
        )
        for i, (buy_vol, sell_vol) in enumerate(buy_sell_pairs)
    ]
    ParquetDataCatalog(tmp_path).write_data(snapshots)
    return snapshots[0].ts_event, snapshots[-1].ts_event


def test_cvd_accumulates_buy_minus_sell_volume_per_candle(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        # 6 one-second snapshots -> two 3-second candle buckets.
        bar_ns = 3_000_000_000
        aligned_base_ns = (_TS_NS // bar_ns) * bar_ns  # deliberately bucket-aligned base
        first_ns, _ = _write_snapshots(
            tmp, [(5.0, 2.0), (1.0, 1.0), (0.0, 3.0), (4.0, 0.0), (2.0, 2.0), (1.0, 0.0)], base_ns=aligned_base_ns,
        )
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        candles = [{"t": aligned_base_ns // 1_000_000}, {"t": (aligned_base_ns + bar_ns) // 1_000_000}]
        start_ms = first_ns // 1_000_000
        window = ReplayWindow(instrument_id=_IID, bar_seconds=3, start_ms=start_ms, end_ms=start_ms + 6000)
        result = ci.replay_indicator(candles, "CumulativeVolumeDelta", {}, window)
        # bucket 1: (5-2)+(1-1)+(0-3) = 0; bucket 2 adds (4-0)+(2-2)+(1-0) = +5 -> running 5
        assert result["value"] == [0.0, 5.0]


def test_cvd_returns_none_for_every_candle_in_live_mode() -> None:
    live_window = ReplayWindow(instrument_id=_IID, bar_seconds=60, start_ms=None, end_ms=None)
    result = ci.replay_indicator([{"t": 0}, {"t": 60_000}], "CumulativeVolumeDelta", {}, live_window)
    assert result == {"value": [None, None]}


def test_cvd_flags_a_snapshot_gap_as_none_instead_of_a_flat_carry_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    """A candle bucket with zero DydxSecondSnapshot rows means the collector's snapshot data
    didn't arrive for that second, not that trading was flat -- DATA-01 requires flagging
    this as unknown, not silently carrying the running total forward as if nothing happened."""
    with tempfile.TemporaryDirectory() as tmp:
        bar_ns = 3_000_000_000
        aligned_base_ns = (_TS_NS // bar_ns) * bar_ns
        # Only the first bucket's 3 seconds have snapshots -- the second bucket is a real gap.
        first_ns, _ = _write_snapshots(tmp, [(5.0, 2.0), (1.0, 1.0), (0.0, 3.0)], base_ns=aligned_base_ns)
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        candles = [{"t": aligned_base_ns // 1_000_000}, {"t": (aligned_base_ns + bar_ns) // 1_000_000}]
        start_ms = first_ns // 1_000_000
        window = ReplayWindow(instrument_id=_IID, bar_seconds=3, start_ms=start_ms, end_ms=start_ms + 6000)
        result = ci.replay_indicator(candles, "CumulativeVolumeDelta", {}, window)
        assert result["value"] == [0.0, None]


def test_cvd_resumes_running_total_after_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bar_ns = 3_000_000_000
        aligned_base_ns = (_TS_NS // bar_ns) * bar_ns
        # Bucket 1 has data, bucket 2 is a gap (no snapshots written for it), bucket 3 has data.
        bucket1 = _write_snapshots(tmp, [(5.0, 2.0)], base_ns=aligned_base_ns)[0]
        _write_snapshots(tmp, [(4.0, 1.0)], base_ns=aligned_base_ns + 2 * bar_ns)
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        candles = [
            {"t": aligned_base_ns // 1_000_000},
            {"t": (aligned_base_ns + bar_ns) // 1_000_000},
            {"t": (aligned_base_ns + 2 * bar_ns) // 1_000_000},
        ]
        start_ms = bucket1 // 1_000_000
        window = ReplayWindow(instrument_id=_IID, bar_seconds=3, start_ms=start_ms, end_ms=start_ms + 9000)
        result = ci.replay_indicator(candles, "CumulativeVolumeDelta", {}, window)
        assert result["value"] == [3.0, None, 6.0]  # 3.0 carries through the gap, then +3.0


def test_cvd_registered_in_production_catalog_with_correct_shape() -> None:
    # Exercises the real entry (not a monkeypatched placeholder) through the same dispatch
    # path a request actually uses -- catalog_json() and dashboard's merge point both read
    # this without needing to know CumulativeVolumeDelta's internals.
    assert ci.catalog_json()["CumulativeVolumeDelta"] == {"params": {}, "panel": "oscillator"}
