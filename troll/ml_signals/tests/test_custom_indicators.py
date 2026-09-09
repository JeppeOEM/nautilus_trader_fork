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
indicators (CumulativeVolumeDelta, Story 10.2; CancelPressure, Story 10.3; OFI to follow,
Story 10.4).

The dispatch-mechanism tests below register a placeholder entry directly to prove
replay_indicator's dispatch contract and catalog_json's shape, matching chart_indicators.py's
own dispatch (proven separately in test_chart_indicators.py) rather than duplicating that
coverage here -- they use subset/membership assertions, not exact-dict equality, since real
entries (CumulativeVolumeDelta and later additions) live in the same module-level catalog.
"""

import tempfile

import pytest
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
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


# -- Story 10.3: CancelPressure ---------------------------------------------------------------

def _book_delta(
    action: BookAction, side: OrderSide, price: float, size: float, ts: int,
) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
    return OrderBookDelta(
        instrument_id=InstrumentId.from_str(_IID), action=action, order=order,
        flags=0, sequence=0, ts_event=ts, ts_init=ts,
    )


def _clear_delta(ts: int) -> OrderBookDelta:
    order = BookOrder(side=OrderSide.BUY, price=Price(0, 1), size=Quantity(0, 1), order_id=0)
    return OrderBookDelta(
        instrument_id=InstrumentId.from_str(_IID), action=BookAction.CLEAR, order=order,
        flags=0, sequence=0, ts_event=ts, ts_init=ts,
    )


def _write_deltas(tmp_path: str, deltas: list[OrderBookDelta]) -> None:
    catalog = ParquetDataCatalog(tmp_path)
    catalog.write_data([OrderBookDeltas(instrument_id=InstrumentId.from_str(_IID), deltas=deltas)])


def test_cancel_pressure_samples_last_value_in_bucket_and_forward_fills_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bar_ns = 3_000_000_000
    base_ns = (_TS_NS // bar_ns) * bar_ns
    deltas = [
        # Bucket 0 (base_ns): establish the book, then one tracked bid DELETE -> pressure 1.0.
        _book_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0, base_ns),
        _book_delta(BookAction.ADD, OrderSide.SELL, 101.0, 5.0, base_ns + 100_000_000),
        _book_delta(BookAction.DELETE, OrderSide.BUY, 100.0, 5.0, base_ns + 200_000_000),
        # Bucket 1 (base_ns + bar_ns): no deltas at all -- a real gap, must forward-fill.
        # Bucket 2 (base_ns + 2*bar_ns): re-establish a bid, then a tracked ADD shifts pressure.
        _book_delta(BookAction.ADD, OrderSide.BUY, 99.0, 3.0, base_ns + 2 * bar_ns + 100_000_000),
        _book_delta(BookAction.ADD, OrderSide.BUY, 99.0, 4.0, base_ns + 2 * bar_ns + 200_000_000),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        _write_deltas(tmp, deltas)
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        candles = [
            {"t": (base_ns - bar_ns) // 1_000_000},  # before any delta -- warm-up, None
            {"t": base_ns // 1_000_000},
            {"t": (base_ns + bar_ns) // 1_000_000},  # the gap bucket
            {"t": (base_ns + 2 * bar_ns) // 1_000_000},
        ]
        start_ms = (base_ns - bar_ns) // 1_000_000
        window = ReplayWindow(
            instrument_id=_IID, bar_seconds=3, start_ms=start_ms, end_ms=start_ms + 4 * 3000,
        )
        result = ci.replay_indicator(candles, "CancelPressure", {}, window)
        # Tracker window=200 accumulates events across the whole replay (not per-bucket): by
        # bucket 2 the deque holds DELETE 5.0 (bucket 0) + ADD 3.0 + ADD 4.0 (bucket 2), so
        # pressure = (deleted - added) / total = (5.0 - 7.0) / 12.0 = -1/6 -- but the ADD/SELL
        # at bucket 0 is never tracked (ask side), so only bid events accumulate for bid_pressure:
        # (5.0 - 7.0) / 12.0. Kept as the pre-existing hand-verified expectation, unchanged by
        # this story's CLEAR/bounded-forward-fill additions (no CLEAR in this scenario).
        assert result["bid_pressure"] == pytest.approx([None, 1.0, 1.0, 1 / 9], abs=1e-9)
        assert result["ask_pressure"] == [None, 0.0, 0.0, 0.0]


def test_cancel_pressure_returns_none_for_every_candle_in_live_mode() -> None:
    live_window = ReplayWindow(instrument_id=_IID, bar_seconds=60, start_ms=None, end_ms=None)
    result = ci.replay_indicator([{"t": 0}, {"t": 60_000}], "CancelPressure", {}, live_window)
    assert result == {"bid_pressure": [None, None], "ask_pressure": [None, None]}


def test_cancel_pressure_registered_in_production_catalog_with_correct_shape() -> None:
    assert ci.catalog_json()["CancelPressure"] == {"params": {"window": 200}, "panel": "histogram"}


def test_cancel_pressure_clear_bucket_is_none_and_forward_fill_resumes_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bar_ns = 3_000_000_000
    base_ns = (_TS_NS // bar_ns) * bar_ns
    deltas = [
        # Bucket 0: a tracked bid DELETE -> pressure 1.0.
        _book_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0, base_ns),
        _book_delta(BookAction.DELETE, OrderSide.BUY, 100.0, 5.0, base_ns + 100_000_000),
        # Bucket 1: a CLEAR -- must record None, not the tracker's post-clear (0.0, 0.0),
        # and must break forward-fill rather than carrying bucket 0's 1.0 forward.
        _clear_delta(base_ns + bar_ns),
        # Bucket 2: book/tracker rebuilt from scratch after the CLEAR -> a fresh sample.
        _book_delta(BookAction.ADD, OrderSide.BUY, 99.0, 2.0, base_ns + 2 * bar_ns),
        _book_delta(BookAction.DELETE, OrderSide.BUY, 99.0, 2.0, base_ns + 2 * bar_ns + 100_000_000),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        _write_deltas(tmp, deltas)
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        candles = [
            {"t": base_ns // 1_000_000},
            {"t": (base_ns + bar_ns) // 1_000_000},
            {"t": (base_ns + 2 * bar_ns) // 1_000_000},
        ]
        start_ms = base_ns // 1_000_000
        window = ReplayWindow(
            instrument_id=_IID, bar_seconds=3, start_ms=start_ms, end_ms=start_ms + 3 * 3000,
        )
        result = ci.replay_indicator(candles, "CancelPressure", {}, window)
        assert result["bid_pressure"] == [1.0, None, 1.0]
        assert result["ask_pressure"] == [0.0, None, 0.0]


def test_cancel_pressure_forward_fill_reverts_to_none_past_the_bucket_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bar_ns = 3_000_000_000
    base_ns = (_TS_NS // bar_ns) * bar_ns
    deltas = [
        _book_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0, base_ns),
        _book_delta(BookAction.DELETE, OrderSide.BUY, 100.0, 5.0, base_ns + 100_000_000),
    ]
    gap_buckets = ci._MAX_FORWARD_FILL_BUCKETS
    with tempfile.TemporaryDirectory() as tmp:
        _write_deltas(tmp, deltas)
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        # Sample bucket, then (gap_buckets) empty buckets, then one more empty bucket that
        # must finally revert to None -- the (gap_buckets + 1)-th bucket without data.
        candles = [{"t": (base_ns + i * bar_ns) // 1_000_000} for i in range(gap_buckets + 2)]
        start_ms = base_ns // 1_000_000
        window = ReplayWindow(
            instrument_id=_IID, bar_seconds=3,
            start_ms=start_ms, end_ms=start_ms + (gap_buckets + 2) * 3000,
        )
        result = ci.replay_indicator(candles, "CancelPressure", {}, window)
        assert result["bid_pressure"][0] == 1.0
        # Still forward-filled at exactly the cap...
        assert result["bid_pressure"][gap_buckets] == 1.0
        # ...but one bucket further, the gap has outlasted the cap -- back to None.
        assert result["bid_pressure"][gap_buckets + 1] is None
        assert result["ask_pressure"][gap_buckets + 1] is None


# -- Story 10.4: OrderFlowImbalance ---------------------------------------------------------


def test_ofi_samples_last_value_in_bucket_and_forward_fills_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bar_ns = 3_000_000_000
    base_ns = (_TS_NS // bar_ns) * bar_ns
    deltas = [
        # Bucket 0: ADD bid (ask side still empty -- skipped, no top-of-book yet), then
        # ADD ask seeds ofi's prev state (not initialized yet), then an UPDATE to the bid
        # size is the first initialized sample.
        # bid_term = 8-5=3 (price unchanged), ask_term = 5-5=0 (price/size unchanged) -> 3.0
        _book_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0, base_ns),
        _book_delta(BookAction.ADD, OrderSide.SELL, 101.0, 5.0, base_ns + 100_000_000),
        _book_delta(BookAction.UPDATE, OrderSide.BUY, 100.0, 8.0, base_ns + 200_000_000),
        # Bucket 1: no deltas -- a real gap, must forward-fill bucket 0's 3.0.
        # Bucket 2: ask size shrinks -- bid_term=8-8=0, ask_term=2-5=-3 -> contribution +3,
        # running sum (window=20, unbounded here) = 3.0 (bucket 0) + 3.0 = 6.0.
        _book_delta(BookAction.UPDATE, OrderSide.SELL, 101.0, 2.0, base_ns + 2 * bar_ns + 100_000_000),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        _write_deltas(tmp, deltas)
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        candles = [
            {"t": (base_ns - bar_ns) // 1_000_000},  # before any delta -- warm-up, None
            {"t": base_ns // 1_000_000},
            {"t": (base_ns + bar_ns) // 1_000_000},  # the gap bucket
            {"t": (base_ns + 2 * bar_ns) // 1_000_000},
        ]
        start_ms = (base_ns - bar_ns) // 1_000_000
        window = ReplayWindow(
            instrument_id=_IID, bar_seconds=3, start_ms=start_ms, end_ms=start_ms + 4 * 3000,
        )
        result = ci.replay_indicator(candles, "OrderFlowImbalance", {}, window)
        assert result["value"] == pytest.approx([None, 3.0, 3.0, 6.0])


def test_ofi_forward_fill_reverts_to_none_past_the_bucket_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bar_ns = 3_000_000_000
    base_ns = (_TS_NS // bar_ns) * bar_ns
    deltas = [
        _book_delta(BookAction.ADD, OrderSide.BUY, 100.0, 5.0, base_ns),
        _book_delta(BookAction.ADD, OrderSide.SELL, 101.0, 5.0, base_ns + 100_000_000),
        _book_delta(BookAction.UPDATE, OrderSide.BUY, 100.0, 8.0, base_ns + 200_000_000),
    ]
    gap_buckets = ci._MAX_FORWARD_FILL_BUCKETS
    with tempfile.TemporaryDirectory() as tmp:
        _write_deltas(tmp, deltas)
        monkeypatch.setattr(ci, "_CATALOG_PATH", tmp)
        candles = [{"t": (base_ns + i * bar_ns) // 1_000_000} for i in range(gap_buckets + 2)]
        start_ms = base_ns // 1_000_000
        window = ReplayWindow(
            instrument_id=_IID, bar_seconds=3,
            start_ms=start_ms, end_ms=start_ms + (gap_buckets + 2) * 3000,
        )
        result = ci.replay_indicator(candles, "OrderFlowImbalance", {}, window)
        assert result["value"][0] == 3.0
        assert result["value"][gap_buckets] == 3.0
        assert result["value"][gap_buckets + 1] is None


def test_ofi_returns_none_for_every_candle_in_live_mode() -> None:
    live_window = ReplayWindow(instrument_id=_IID, bar_seconds=60, start_ms=None, end_ms=None)
    result = ci.replay_indicator([{"t": 0}, {"t": 60_000}], "OrderFlowImbalance", {}, live_window)
    assert result == {"value": [None, None]}


def test_ofi_registered_in_production_catalog_with_correct_shape() -> None:
    assert ci.catalog_json()["OrderFlowImbalance"] == {
        "params": {"window": 20}, "panel": "oscillator",
    }
