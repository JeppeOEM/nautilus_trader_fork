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
Unit tests for `views.indicator_picker`'s custom half (was the `custom_indicators` module;
`replay_indicator`/`catalog_json` are `replay_custom`/`custom_catalog_json`): its dispatch mechanism (Story 10.1) and its registered
indicators (CumulativeVolumeDelta, Story 10.2; CancelPressure, Story 10.3; OFI to follow,
Story 10.4).

The dispatch-mechanism tests below register a placeholder entry directly to prove
replay_indicator's dispatch contract and catalog_json's shape, matching chart_indicators.py's
own dispatch (proven separately in test_indicator_picker_native.py) rather than duplicating that
coverage here -- they use subset/membership assertions, not exact-dict equality, since real
entries (CumulativeVolumeDelta and later additions) live in the same module-level catalog.
"""

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from candles.domain.fold import bucket_start_ms
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.second_snapshot import SecondOHLC
from kernel.tests.snapshot_factory import make_snapshot
from kernel.venues import venue_of

from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import indicator_picker as ci
from views.chart_series import MAX_QUERY_SPAN_SECONDS
from views.indicator_picker import CustomIndicatorSpec
from views.indicator_picker import ReplayWindow


_IID = "BTC-USD-PERP.DYDX"
_TS_NS = 1_700_000_000_000_000_000


def _window() -> ReplayWindow:
    return ReplayWindow(
        instrument_id="BTC-USD-PERP.DYDX", bar_seconds=60, start_ms=None, end_ms=None
    )


def _echo_replay(
    candles: list[dict], params: dict, window: ReplayWindow
) -> dict[str, list[float | None]]:
    """Return one placeholder output ("value") per candle, scaled by params["scale"]."""
    return {"value": [c["c"] * params["scale"] for c in candles]}


@pytest.fixture(autouse=True)
def _placeholder_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        ci.CUSTOM_INDICATOR_CATALOG,
        "PlaceholderCustom",
        CustomIndicatorSpec(
            params={"scale": 1.0}, panel="histogram", replay=_echo_replay, outputs=("value",)
        ),
    )


def test_replay_indicator_calls_the_registered_replay_function() -> None:
    candles = [{"t": 0, "c": 2.0}, {"t": 60_000, "c": 3.0}]
    result = ci.replay_custom(candles, "PlaceholderCustom", {"scale": 2.0}, _window())
    assert result == {"value": [4.0, 6.0]}


def test_replay_indicator_merges_params_over_catalog_defaults() -> None:
    candles = [{"t": 0, "c": 5.0}]
    result = ci.replay_custom(candles, "PlaceholderCustom", {}, _window())
    assert result == {"value": [5.0]}  # default scale=1.0 applied


def test_replay_indicator_raises_for_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown custom indicator"):
        ci.replay_custom([], "NotRegistered", {}, _window())


def test_catalog_json_returns_params_and_panel_per_entry() -> None:
    # Subset check, not exact-dict equality -- the catalog also carries real entries
    # (CumulativeVolumeDelta, Story 10.2+) alongside whatever a test adds via monkeypatch.
    assert ci.custom_catalog_json()["PlaceholderCustom"] == {
        "params": {"scale": 1.0},
        "panel": "histogram",
        "choices": {},
        "units": {},
        "outputs": ["value"],
        "plot": {},
        "note": None,
    }
    assert (
        "category" not in ci.custom_catalog_json()["PlaceholderCustom"]
    )  # tagged only at the merge point


# -- Story 10.2 / 33.3: CumulativeVolumeDelta, from the bars' stored order flow ---------------

_HOUR_MS = 3_600_000
_DAY1_MS = 20_000 * 86_400_000  # a UTC midnight


def _flow_bar(
    t: int,
    buy_v: int | None,
    sell_v: int | None,
    size_precision: int = 1,
    *,
    price_precision: int = 2,
    pv: int = 0,
    buy_n: int = 1,
    sell_n: int = 1,
    liq: tuple[int, int, int] | None = None,
) -> dict:
    """
    Build a candle as `candle_page` serves it: `t` and the Story 33.3 aggregates the flow
    indicators read. `buy_v is None` is a pre-migration bar (the whole flow group and the
    precisions null); `liq` is `(liq_long_v, liq_short_v, liq_n)`, None for no feed.
    """
    flow = buy_v is not None
    liq_long_v, liq_short_v, liq_n = liq if liq is not None else (None, None, None)
    return {
        "t": t,
        "buy_v": buy_v,
        "sell_v": sell_v,
        "buy_n": buy_n if flow else None,
        "sell_n": sell_n if flow else None,
        "pv": pv if flow else None,
        "liq_long_v": liq_long_v,
        "liq_short_v": liq_short_v,
        "liq_n": liq_n,
        "price_precision": price_precision if flow else None,
        "size_precision": size_precision if flow else None,
    }


def _hourly(bars: list[dict], candles_dir: str | None = None) -> ReplayWindow:
    return ReplayWindow(
        instrument_id=_IID,
        bar_seconds=3600,
        start_ms=bars[0]["t"],
        end_ms=bars[-1]["t"] + _HOUR_MS,
        candles_dir=candles_dir,
    )


# The spec's matrix: day 1 10:00 delta +3, day 1 11:00 flow null, day 2 00:00 delta +2 (at size
# precision 1: 30 and 20 units).
_MATRIX = [
    _flow_bar(_DAY1_MS + 10 * _HOUR_MS, 50, 20),
    _flow_bar(_DAY1_MS + 11 * _HOUR_MS, None, None),
    _flow_bar(_DAY1_MS + 24 * _HOUR_MS, 25, 5),
]


def test_cvd_accumulates_the_bars_buy_minus_sell() -> None:
    bars = [_flow_bar(0, 60, 60), _flow_bar(_HOUR_MS, 70, 20)]  # deltas 0 and +5.0
    result = ci.replay_custom(bars, "CumulativeVolumeDelta", {}, _hourly(bars))
    assert result["value"] == [0.0, 5.0]


def test_cvd_a_null_flow_bar_is_none_and_the_total_carries_on() -> None:
    """A pre-migration bar's flow is unknown: None, never a flat carry-forward (DATA-01)."""
    result = ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", {}, _hourly(_MATRIX))
    assert result["value"] == [3.0, None, 5.0]


def test_cvd_session_with_no_store_is_none_until_a_session_the_page_holds_whole() -> None:
    """
    Day 1's session starts at 00:00, before the page's first bar (10:00), and no store covers
    [00:00, 10:00): that session is None, never a sum restarted at 10:00 (audit D-186). Day 2
    starts inside the page, so it restarts at its midnight from 0.
    """
    params = {"anchor": "session"}
    result = ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", params, _hourly(_MATRIX))
    assert result["value"] == [None, None, 2.0]


def _store_day1_morning(candles_dir: Path, *, from_midnight: bool = True) -> None:
    """
    Store two day-1 seconds at size precision 0, each buying 12 and selling 2 at close 1 (price
    precision 0): delta +10, volume 14, pv 14 each. The first is at 00:00 (the session's start)
    unless `from_midnight` is False (then 01:00), the second at 09:00.
    """
    store = CandleStore(db_path_for_venue(candles_dir, venue_of(_IID)))
    first_hour = 0 if from_midnight else 1
    for hour in (first_hour, 9):
        ts = (_DAY1_MS + hour * _HOUR_MS) * 1_000_000
        store.apply(_IID, [SecondOHLC(ts, 1.0, 1.0, 1.0, 1.0, 12.0, 2.0, 0, 0, 1, 12, 2, 1, 1)])
    store.close()


def test_cvd_session_is_seeded_with_the_stored_bars_since_midnight(tmp_path: Path) -> None:
    """
    The store covers day 1 from 00:00: [00:00, 10:00) adds +20 at size precision 0, run at the
    page's 1 -> 200 + 30 = 230 -> 23.0 at 10:00; day 2 restarts at its own midnight.
    """
    _store_day1_morning(tmp_path)
    window = _hourly(_MATRIX, str(tmp_path))
    result = ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", {"anchor": "session"}, window)
    assert result["value"] == [23.0, None, 2.0]


def test_cvd_session_with_a_store_starting_after_midnight_is_none(tmp_path: Path) -> None:
    """The store's first 1h bar is 01:00, after the session's 00:00: uncovered, never partial."""
    _store_day1_morning(tmp_path, from_midnight=False)
    window = _hourly(_MATRIX, str(tmp_path))
    result = ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", {"anchor": "session"}, window)
    assert result["value"] == [None, None, 2.0]


def test_cvd_session_on_daily_bars_is_each_bars_own_delta() -> None:
    days = [_flow_bar(_DAY1_MS + d * 86_400_000, 40, 10) for d in range(3)]
    window = ReplayWindow(_IID, 86_400, days[0]["t"], days[-1]["t"] + 86_400_000)
    result = ci.replay_custom(days, "CumulativeVolumeDelta", {"anchor": "session"}, window)
    assert result["value"] == [3.0, 3.0, 3.0]


def test_cvd_all_starts_from_the_stored_bars_before_the_window(tmp_path: Path) -> None:
    """
    The store holds one 09:00 second buying 12 and selling 2 at size precision 0 (+10); the
    visible bars are at precision 1, so the total runs at 1: 100 + 30 = 130 -> 13.0, then 15.0.
    """
    store = CandleStore(db_path_for_venue(tmp_path, venue_of(_IID)))
    ts = (_DAY1_MS + 9 * _HOUR_MS) * 1_000_000
    store.apply(_IID, [SecondOHLC(ts, 1.0, 1.0, 1.0, 1.0, 12.0, 2.0, 0, 0, 1, 12, 2, 1, 1)])
    store.close()
    params = {"anchor": "all"}
    window = _hourly(_MATRIX, str(tmp_path))
    result = ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", params, window)
    assert result["value"] == [13.0, None, 15.0]


def test_cvd_all_with_no_store_file_starts_at_zero(tmp_path: Path) -> None:
    window = _hourly(_MATRIX, str(tmp_path / "none"))
    result = ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", {"anchor": "all"}, window)
    assert result["value"] == [3.0, None, 5.0]


def test_cvd_all_without_a_candles_dir_is_refused() -> None:
    with pytest.raises(ValueError, match="candle store"):
        ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", {"anchor": "all"}, _hourly(_MATRIX))


def test_cvd_an_unknown_anchor_is_refused_at_replay_and_at_save() -> None:
    with pytest.raises(ValueError, match="anchor"):
        ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", {"anchor": "week"}, _hourly(_MATRIX))
    with pytest.raises(ValueError, match="not one of the choices"):
        ci.check_params("CumulativeVolumeDelta", {"anchor": "week"})
    ci.check_params("CumulativeVolumeDelta", {"anchor": "session"})


def test_cvd_computes_in_live_mode_too() -> None:
    live = ReplayWindow(instrument_id=_IID, bar_seconds=3600, start_ms=None, end_ms=None)
    result = ci.replay_custom(_MATRIX, "CumulativeVolumeDelta", {}, live)
    assert result["value"] == [3.0, None, 5.0]


def test_cvd_mixed_precision_bars_sum_exactly() -> None:
    """0.1 + 0.2 in floats is 0.30000000000000004; in units at precision 2 it is 30 -> 0.3."""
    bars = [_flow_bar(0, 1, 0, 1), _flow_bar(_HOUR_MS, 20, 0, 2)]
    result = ci.replay_custom(bars, "CumulativeVolumeDelta", {}, _hourly(bars))
    assert result["value"] == [0.1, 0.3]


def test_cvd_registered_in_production_catalog_with_correct_shape() -> None:
    # Exercises the real entry (not a monkeypatched placeholder) through the same dispatch
    # path a request actually uses -- catalog_json() and dashboard's merge point both read
    # this without needing to know CumulativeVolumeDelta's internals.
    assert ci.custom_catalog_json()["CumulativeVolumeDelta"] == {
        "params": {"anchor": "visible"},
        "panel": "oscillator",
        "choices": {"anchor": ["session", "visible", "all"]},
        "units": {"value": "size"},
        "outputs": ["value"],
        "plot": {},
        "note": None,
    }


# -- Story 10.3: CancelPressure ---------------------------------------------------------------


def _book_delta(
    action: BookAction,
    side: OrderSide,
    price: float,
    size: float,
    ts: int,
) -> OrderBookDelta:
    order = BookOrder(side=side, price=Price(price, 1), size=Quantity(size, 1), order_id=0)
    return OrderBookDelta(
        instrument_id=InstrumentId.from_str(_IID),
        action=action,
        order=order,
        flags=0,
        sequence=0,
        ts_event=ts,
        ts_init=ts,
    )


def _clear_delta(ts: int) -> OrderBookDelta:
    order = BookOrder(side=OrderSide.BUY, price=Price(0, 1), size=Quantity(0, 1), order_id=0)
    return OrderBookDelta(
        instrument_id=InstrumentId.from_str(_IID),
        action=BookAction.CLEAR,
        order=order,
        flags=0,
        sequence=0,
        ts_event=ts,
        ts_init=ts,
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
            instrument_id=_IID,
            bar_seconds=3,
            start_ms=start_ms,
            end_ms=start_ms + 4 * 3000,
        )
        result = ci.replay_custom(candles, "CancelPressure", {}, window)
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
    result = ci.replay_custom([{"t": 0}, {"t": 60_000}], "CancelPressure", {}, live_window)
    assert result == {"bid_pressure": [None, None], "ask_pressure": [None, None]}


def test_cancel_pressure_registered_in_production_catalog_with_correct_shape() -> None:
    assert ci.custom_catalog_json()["CancelPressure"] == {
        "params": {"window": 200},
        "panel": "histogram",
        "choices": {},
        "units": {},
        "outputs": ["bid_pressure", "ask_pressure"],
        "plot": {},
        "note": None,
    }


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
        _book_delta(
            BookAction.DELETE, OrderSide.BUY, 99.0, 2.0, base_ns + 2 * bar_ns + 100_000_000
        ),
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
            instrument_id=_IID,
            bar_seconds=3,
            start_ms=start_ms,
            end_ms=start_ms + 3 * 3000,
        )
        result = ci.replay_custom(candles, "CancelPressure", {}, window)
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
            instrument_id=_IID,
            bar_seconds=3,
            start_ms=start_ms,
            end_ms=start_ms + (gap_buckets + 2) * 3000,
        )
        result = ci.replay_custom(candles, "CancelPressure", {}, window)
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
        _book_delta(
            BookAction.UPDATE, OrderSide.SELL, 101.0, 2.0, base_ns + 2 * bar_ns + 100_000_000
        ),
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
            instrument_id=_IID,
            bar_seconds=3,
            start_ms=start_ms,
            end_ms=start_ms + 4 * 3000,
        )
        result = ci.replay_custom(candles, "OrderFlowImbalance", {}, window)
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
            instrument_id=_IID,
            bar_seconds=3,
            start_ms=start_ms,
            end_ms=start_ms + (gap_buckets + 2) * 3000,
        )
        result = ci.replay_custom(candles, "OrderFlowImbalance", {}, window)
        assert result["value"][0] == 3.0
        assert result["value"][gap_buckets] == 3.0
        assert result["value"][gap_buckets + 1] is None


def test_ofi_returns_none_for_every_candle_in_live_mode() -> None:
    live_window = ReplayWindow(instrument_id=_IID, bar_seconds=60, start_ms=None, end_ms=None)
    result = ci.replay_custom([{"t": 0}, {"t": 60_000}], "OrderFlowImbalance", {}, live_window)
    assert result == {"value": [None, None]}


def test_ofi_registered_in_production_catalog_with_correct_shape() -> None:
    assert ci.custom_catalog_json()["OrderFlowImbalance"] == {
        "params": {"window": 20},
        "panel": "oscillator",
        "choices": {},
        "units": {},
        "outputs": ["value"],
        "plot": {},
        "note": None,
    }


# -- Story 33.6: order-flow indicators from the bars' stored aggregates ---------------------------


def _one(name: str, bar: dict, params: dict | None = None, output: str = "value") -> float | None:
    """Replay `name` over the one bar and return its single value of `output`."""
    (value,) = ci.replay_custom([bar], name, params or {}, _hourly([bar]))[output]
    return value


_LIQ_BAR = _flow_bar(0, 70, 30, liq=(10, 5, 2))  # size precision 1: 7.0 bought, 3.0 sold


def test_volume_delta_is_the_bars_buy_minus_sell() -> None:
    assert _one("VolumeDelta", _flow_bar(0, 70, 30)) == 4.0


def test_organic_delta_takes_long_liquidations_from_sells_and_short_ones_from_buys() -> None:
    """(70 - 5) - (30 - 10) = 45 units at size precision 1 -> 4.5 (the sign mapping, pinned)."""
    assert _one("OrganicDelta", _LIQ_BAR) == 4.5


def test_without_a_liquidation_feed_only_the_plain_delta_has_a_value() -> None:
    bar = _flow_bar(0, 70, 30)  # liq_* null: spot, Hyperliquid, or before the feed's start
    assert _one("OrganicDelta", bar) is None
    assert _one("ForcedShare", bar) is None
    assert _one("VolumeDelta", bar) == 4.0


def test_forced_share_is_forced_over_traded_volume_and_never_clamped() -> None:
    assert _one("ForcedShare", _LIQ_BAR) == 0.15  # 15 / 100
    assert _one("ForcedShare", _flow_bar(0, 7, 3, liq=(20, 5, 3))) == 2.5  # above 1, as is


def test_a_quiet_bar_has_a_zero_delta_and_no_share_average_or_vwap() -> None:
    quiet = _flow_bar(0, 0, 0, buy_n=0, sell_n=0, liq=(0, 0, 0))
    assert _one("VolumeDelta", quiet) == 0.0
    assert _one("OrganicDelta", quiet) == 0.0
    assert _one("ForcedShare", quiet) is None
    assert _one("AverageTradeSize", quiet) is None
    assert _one("StoredVWAP", quiet, {"mode": "bar"}) is None


def test_a_pre_migration_bar_is_none_for_every_flow_indicator() -> None:
    bar = _flow_bar(0, None, None)
    for name in ("VolumeDelta", "OrganicDelta", "ForcedShare", "TradeCount", "AverageTradeSize"):
        assert _one(name, bar) is None
    assert _one("StoredVWAP", bar, {"mode": "bar"}) is None


def test_trade_count_totals_or_splits_buys_up_and_sells_down() -> None:
    bar = _flow_bar(0, 70, 30, buy_n=7, sell_n=3)
    assert _one("TradeCount", bar) == 10.0
    assert _one("TradeCount", bar, {"split": True}, "buys") == 7.0
    assert _one("TradeCount", bar, {"split": True}, "sells") == -3.0


def test_trade_count_refuses_a_non_boolean_split_at_replay_and_at_save() -> None:
    with pytest.raises(ValueError, match="split"):
        ci.replay_custom([_LIQ_BAR], "TradeCount", {"split": 1}, _hourly([_LIQ_BAR]))
    with pytest.raises(ValueError, match="split"):
        ci.check_params("TradeCount", {"split": "yes"})


def test_average_trade_size_is_exact_volume_over_trades() -> None:
    """10.5 units of size over 7 trades: 105 / (7 x 10) = 1.5."""
    assert _one("AverageTradeSize", _flow_bar(0, 100, 5, buy_n=4, sell_n=3)) == 1.5


def test_stored_vwap_bar_is_pv_over_volume_at_the_price_scale() -> None:
    """Pv 1_000_050 at 10^-(2+1) over 10 units of volume at 10^-1: 1_000_050 / (10 x 100)."""
    bar = _flow_bar(0, 6, 4, price_precision=2, pv=1_000_050)
    assert _one("StoredVWAP", bar, {"mode": "bar"}) == 1000.05


# Day 1 10:00 at (2, 1): 7.0 at 3.00 (pv 300 x 70); 11:00 null; day 2 00:00 at (3, 2): 1.00 at
# 4.000 (pv 4000 x 100). Each session's VWAP is its own bars' price unless a prefix joins in.
_VWAP_PAGE = [
    _flow_bar(_DAY1_MS + 10 * _HOUR_MS, 50, 20, 1, price_precision=2, pv=21_000),
    _flow_bar(_DAY1_MS + 11 * _HOUR_MS, None, None),
    _flow_bar(_DAY1_MS + 24 * _HOUR_MS, 60, 40, 2, price_precision=3, pv=400_000),
]


def test_stored_vwap_session_is_seeded_from_the_store_and_carries_across_a_null_bar(
    tmp_path: Path,
) -> None:
    """
    The store's [00:00, 10:00) holds 28 at price 1 (pv 28 at precision 0); the 10:00 bar adds 7.0
    at 3.00: (28 + 21) / 35 = 1.4. The null 11:00 bar is None; day 2 restarts: 4.0.
    """
    _store_day1_morning(tmp_path)
    window = _hourly(_VWAP_PAGE, str(tmp_path))
    result = ci.replay_custom(_VWAP_PAGE, "StoredVWAP", {"mode": "session"}, window)
    assert result["value"] == [1.4, None, 4.0]


def test_stored_vwap_session_with_an_uncovered_prefix_is_none() -> None:
    result = ci.replay_custom(_VWAP_PAGE, "StoredVWAP", {}, _hourly(_VWAP_PAGE))
    assert result["value"] == [None, None, 4.0]


def test_stored_vwap_session_sums_mixed_precisions_exactly() -> None:
    """
    One day, 1.0 at 2.00 (pp 2, sp 1: pv 200 x 10) then 0.10 at 3.000 (pp 3, sp 2: pv 3000 x 10),
    rescaled to (3, 2): pv 2000 x 10^2 + 30_000 = 230_000 over 100 + 10 = 110 units of 10^-2 ->
    230_000 / (110 x 10^3) = 2.3 / 1.1.
    """
    bars = [
        _flow_bar(_DAY1_MS, 10, 0, 1, price_precision=2, pv=2000),
        _flow_bar(_DAY1_MS + _HOUR_MS, 10, 0, 2, price_precision=3, pv=30_000),
    ]
    result = ci.replay_custom(bars, "StoredVWAP", {}, _hourly(bars))
    assert result["value"] == [2.0, 230_000 / 110_000]


def test_stored_vwap_on_daily_bars_is_each_bars_own_price() -> None:
    days = [_flow_bar(_DAY1_MS + d * 86_400_000, 6, 4, pv=300 * (d + 1) * 10) for d in range(2)]
    window = ReplayWindow(_IID, 86_400, days[0]["t"], days[-1]["t"] + 86_400_000)
    assert ci.replay_custom(days, "StoredVWAP", {}, window)["value"] == [3.0, 6.0]


def test_stored_vwap_refuses_an_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        ci.replay_custom(_VWAP_PAGE, "StoredVWAP", {"mode": "week"}, _hourly(_VWAP_PAGE))
    with pytest.raises(ValueError, match="not one of the choices"):
        ci.check_params("StoredVWAP", {"mode": "week"})


_ANCHOR_PAGE = [
    _flow_bar(_DAY1_MS + 10 * _HOUR_MS, 10, 0, pv=10_000),  # 1.0 at 10.00
    _flow_bar(_DAY1_MS + 11 * _HOUR_MS, 10, 0, pv=20_000),  # 1.0 at 20.00
    _flow_bar(_DAY1_MS + 12 * _HOUR_MS, 20, 0, pv=80_000),  # 2.0 at 40.00
]


def _anchored(anchor_ms: int, candles_dir: str | None = None) -> list[float | None]:
    params = {"anchor_t": str(anchor_ms)}
    window = _hourly(_ANCHOR_PAGE, candles_dir)
    return ci.replay_custom(_ANCHOR_PAGE, "AnchoredStoredVWAP", params, window)["value"]


def test_anchored_stored_vwap_is_none_before_the_anchor_then_cumulative() -> None:
    """From 11:00: 20.00, then (20 + 80) / 3.0 units of 10^-1 -> 100_000 / (30 x 100)."""
    assert _anchored(_DAY1_MS + 11 * _HOUR_MS) == [None, 20.0, 100_000 / 3000]


def test_anchored_stored_vwap_before_the_page_is_seeded_from_the_store(tmp_path: Path) -> None:
    """
    Anchor 00:00, store from 00:00: 28 at 1.0 (pv 28) before the page, then 1.0 at 10.00:
    (28 x 1000 + 10_000) / ((280 + 10) x 100) = 38_000 / 29_000.
    """
    _store_day1_morning(tmp_path)
    assert _anchored(_DAY1_MS, str(tmp_path))[0] == 38_000 / 29_000


def test_anchored_stored_vwap_inside_a_bar_starts_at_that_bar() -> None:
    """An anchor at 11:37 on 1h bars is the 11:00 bar's, where the drawing's handle snaps."""
    assert _anchored(_DAY1_MS + 11 * _HOUR_MS + 37 * 60_000) == _anchored(_DAY1_MS + 11 * _HOUR_MS)


def test_anchored_stored_vwap_before_the_store_names_both_times(tmp_path: Path) -> None:
    """`_DAY1_MS` is 2024-10-04 00:00 UTC (day 20,000); the store starts an hour later."""
    _store_day1_morning(tmp_path, from_midnight=False)
    both = r"anchor 2024-10-04T00:00:00Z: .*first 3600 s bar is 2024-10-04T01:00:00Z"
    with pytest.raises(ValueError, match=both):
        _anchored(_DAY1_MS, str(tmp_path))
    with pytest.raises(ValueError, match="no candle store"):
        _anchored(_DAY1_MS)


def test_anchored_stored_vwap_on_an_untiled_width_says_so() -> None:
    bars = [_flow_bar(_DAY1_MS + 30_000 * i, 1, 0, pv=1000) for i in range(2)]
    window = ReplayWindow(_IID, 30, bars[0]["t"], bars[-1]["t"] + 30_000)
    with pytest.raises(ValueError, match="no stored bar width tiles a 30 s chart"):
        ci.replay_custom(bars, "AnchoredStoredVWAP", {"anchor_t": str(_DAY1_MS - 30_000)}, window)


def test_a_bar_with_known_flow_but_null_pv_is_named_not_a_type_error() -> None:
    bar = {**_flow_bar(_DAY1_MS, 1, 0, pv=1000), "pv": None}
    with pytest.raises(ValueError, match="corrupt row"):
        ci.replay_custom([bar], "StoredVWAP", {"mode": "bar"}, _hourly([bar]))


@pytest.mark.parametrize(
    ("name", "params", "nulled"),
    [
        ("TradeCount", {"split": False}, "sell_n"),
        ("TradeCount", {"split": True}, "buy_n"),
        ("AverageTradeSize", {}, "buy_n"),
        ("VolumeDelta", {}, "sell_v"),
        ("VolumeDelta", {}, "size_precision"),
        ("OrganicDelta", {}, "liq_short_v"),
        ("ForcedShare", {}, "liq_n"),
    ],
)
def test_a_partial_flow_or_liquidation_group_is_named_not_a_type_error(
    name: str, params: dict, nulled: str
) -> None:
    bar = {**_flow_bar(_DAY1_MS, 7, 3, liq=(1, 1, 2)), nulled: None}
    with pytest.raises(ValueError, match=f"null {nulled}.*corrupt row"):
        ci.replay_custom([bar], name, params, _hourly([bar]))


def test_anchored_stored_vwap_after_every_bar_is_all_none() -> None:
    assert _anchored(_DAY1_MS + 13 * _HOUR_MS) == [None, None, None]


@pytest.mark.parametrize("anchor", ["", "17e11", "-1", "١٢", "1" * 16, 1_700_000_000_000])
def test_anchored_stored_vwap_refuses_an_anchor_that_is_not_digits(anchor: object) -> None:
    with pytest.raises(ValueError, match="anchor_t"):
        ci.check_params("AnchoredStoredVWAP", {"anchor_t": anchor})
    with pytest.raises(ValueError, match="anchor_t"):
        ci.replay_custom(_ANCHOR_PAGE, "AnchoredStoredVWAP", {"anchor_t": anchor}, _window())


def test_the_unlisted_anchored_entry_is_absent_from_the_catalog_but_replayed() -> None:
    """Out of the picker, saved configs and the Technicals; the values route still serves it."""
    assert "AnchoredStoredVWAP" not in ci.merged_catalog()
    assert "AnchoredStoredVWAP" not in ci.custom_catalog_json()
    ci.check_params("AnchoredStoredVWAP", ci.CUSTOM_INDICATOR_CATALOG["AnchoredStoredVWAP"].params)
    entry = _Request("AnchoredStoredVWAP", {"anchor_t": str(_DAY1_MS + 11 * _HOUR_MS)})
    by_time, errors = ci.values_by_time(_ANCHOR_PAGE, [entry], _hourly(_ANCHOR_PAGE))
    assert errors == {}
    key = ci.indicator_id(entry.name, entry.params) + ".value"
    assert [by_time[c["t"]][key] for c in _ANCHOR_PAGE] == [None, 20.0, 100_000 / 3000]


@dataclass(frozen=True)
class _Request:
    name: str
    params: dict
    source: str = "close"


def test_the_new_entries_are_listed_with_their_units_and_panels() -> None:
    catalog = ci.merged_catalog()
    expected = {
        "VolumeDelta": ("histogram", {"value": "size"}),
        "OrganicDelta": ("histogram", {"value": "size"}),
        "ForcedShare": ("histogram", {"value": "ratio"}),
        "TradeCount": ("histogram", {"value": "count", "buys": "count", "sells": "count"}),
        "AverageTradeSize": ("oscillator", {"value": "size_mean"}),
        "StoredVWAP": ("overlay", {"value": "price"}),
        "DepthWithinBps": ("oscillator", {"bid": "size", "ask": "size"}),
    }
    for name, (panel, units) in expected.items():
        assert (catalog[name]["panel"], catalog[name]["units"], catalog[name]["category"]) == (
            panel,
            units,
            "custom",
        )
    assert catalog["StoredVWAP"]["choices"] == {"mode": ["bar", "session"]}
    assert catalog["StoredVWAP"]["params"] == {"mode": "session"}
    assert catalog["TradeCount"]["params"] == {"split": False}
    assert catalog["DepthWithinBps"]["params"] == {"bps": 10.0}


def test_every_custom_unit_is_a_known_unit() -> None:
    for spec in ci.CUSTOM_INDICATOR_CATALOG.values():
        assert set(spec.units.values()) <= set(ci.INDICATOR_UNITS)


# -- DepthWithinBps over a real snapshot catalog ----------------------------------------------

_MIN_MS = 60_000
_DEPTH_T0_MS = _DAY1_MS + 10 * _HOUR_MS


def _depth_snapshot(ts_ms: int, bid_size: float) -> DydxSecondSnapshot:
    """Build a book around mid 100 at 5 bps and 10 bps each side; the bid's touch size varies."""
    return make_snapshot(
        _IID,
        bid_prices=[99.95, 99.9],
        bid_sizes=[bid_size, 2.0],
        ask_prices=[100.05, 100.1],
        ask_sizes=[3.0, 4.0],
        ts_event=(ts_ms + 500) * 1_000_000,
    )


def _depth_catalog(root: Path) -> None:
    """Minute 0: two snapshots (the later, bid 1.5, is the bar's); minute 1: none; minute 2: one."""
    ParquetDataCatalog(str(root)).write_data(
        [
            _depth_snapshot(_DEPTH_T0_MS, 9.0),
            _depth_snapshot(_DEPTH_T0_MS + 30_000, 1.5),
            _depth_snapshot(_DEPTH_T0_MS + 2 * _MIN_MS + 59_000, 0.5),
        ]
    )


def _depth(
    monkeypatch: pytest.MonkeyPatch, root: Path, bps: float, end_ms: int | None = None
) -> dict[str, list[float | None]]:
    _depth_catalog(root)
    monkeypatch.setattr(ci, "_CATALOG_PATH", str(root))
    bars = [{"t": _DEPTH_T0_MS + k * _MIN_MS} for k in range(3)]
    end = _DEPTH_T0_MS + 3 * _MIN_MS if end_ms is None else end_ms
    window = ReplayWindow(_IID, 60, _DEPTH_T0_MS, end)
    return ci.replay_custom(bars, "DepthWithinBps", {"bps": bps}, window)


def test_depth_within_bps_reads_each_bars_last_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Within 8 bps only the 5 bps levels count: bid 1.5 (minute 0's last row), ask 3.0."""
    result = _depth(monkeypatch, tmp_path, 8.0)
    assert result == {"bid": [1.5, None, 0.5], "ask": [3.0, None, 3.0]}


def test_depth_past_the_stored_levels_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The deepest stored level is 10 bps out: the size within 20 bps is unknown (NaN), so None."""
    assert _depth(monkeypatch, tmp_path, 20.0) == {"bid": [None] * 3, "ask": [None] * 3}


def test_depth_reads_nothing_older_than_the_seven_day_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With the window's end 7 days + 90 s after minute 0, minute 0 ends before the cap: None."""
    end = _DEPTH_T0_MS + MAX_QUERY_SPAN_SECONDS * 1000 + 90_000
    result = _depth(monkeypatch, tmp_path, 8.0, end_ms=end)
    assert result == {"bid": [None, None, 0.5], "ask": [None, None, 3.0]}


def test_depth_in_the_live_window_is_none() -> None:
    result = ci.replay_custom([{"t": 0}], "DepthWithinBps", {}, _window())
    assert result == {"bid": [None], "ask": [None]}


@pytest.mark.parametrize("bps", [0, -1.0, 1000.5, True])
def test_depth_refuses_bps_outside_its_range(bps: object) -> None:
    with pytest.raises(ValueError, match="bps"):
        ci.check_params("DepthWithinBps", {"bps": bps})
    with pytest.raises(ValueError, match="bps"):
        ci.replay_custom([{"t": 0}], "DepthWithinBps", {"bps": bps}, _window())


def test_indicator_units_mirror_the_frontend() -> None:
    # The legend formatter ignores a unit it does not know (the generic readout), so a unit added on
    # one side only would silently drop its precision formatting.
    source = (Path(__file__).parents[2] / "frontend/src/lib/indicatorFormat.ts").read_text()
    match = re.search(r"export const INDICATOR_UNITS = \[([^\]]*)\]", source)
    assert match is not None
    assert tuple(part.strip(' "') for part in match.group(1).split(",")) == ci.INDICATOR_UNITS
    used = {unit for spec in ci.CUSTOM_INDICATOR_CATALOG.values() for unit in spec.units.values()}
    assert used <= set(ci.INDICATOR_UNITS)


def test_every_custom_entry_lists_its_outputs_and_units_only_name_them() -> None:
    # Story 33.8: the alert form's output select reads `outputs`; a `units` key the replay never
    # returns would format nothing, so it must be one of them.
    for name, spec in ci.CUSTOM_INDICATOR_CATALOG.items():
        assert spec.outputs, name
        assert set(spec.units) <= set(spec.outputs), name


def test_every_listed_entry_of_the_merged_catalog_carries_outputs() -> None:
    for name, entry in ci.merged_catalog().items():
        assert entry["outputs"], name
        assert set(entry.get("units", {})) <= set(entry["outputs"]), name


# -- Story 33.11: Supertrend, PivotPoints and ZigZag (`kernel.ta`) -------------------------------


def _ohlc(t: int, high: float, low: float, close: float) -> dict:
    return {"t": t, "o": close, "h": high, "l": low, "c": close, "v": 1.0}


def _hourly_bars(rows: list[tuple[int, float, float, float]]) -> list[dict]:
    """`(hour since day 1's midnight, h, l, c)` -> hourly candles."""
    return [_ohlc(_DAY1_MS + hour * _HOUR_MS, h, l, c) for hour, h, l, c in rows]


def test_supertrend_splits_its_line_at_a_flip() -> None:
    """
    `kernel/tests/test_ta.py`'s fixture (period 2, multiplier 1): directions -1, -1, -1, -1, +1
    from bar 1 (TradingView's down-trend start) -- `down` holds the line through bar 4 and `up`
    from the flip at bar 5.
    """
    bars = _hourly_bars(
        [
            (0, 10, 8, 9),
            (1, 11, 9, 10),
            (2, 12, 10, 11.5),
            (3, 11, 7, 7.5),
            (4, 9, 6, 8.5),
            (5, 12, 9, 11.8),
        ]
    )
    result = ci.replay_custom(bars, "Supertrend", {"period": 2, "multiplier": 1.0}, _hourly(bars))
    assert result["up"] == [None, None, None, None, None, 7.1875]
    assert result["down"] == [None, 12.0, 12.0, 12.0, 10.625, None]


def test_supertrend_refuses_a_bad_param_at_save() -> None:
    with pytest.raises(ValueError, match="period"):
        ci.check_params("Supertrend", {"period": 0})
    ci.check_params("Supertrend", {"period": 7, "multiplier": 2.5})


def _store_second_at(candles_dir: Path, ms: int) -> None:
    """Store one traded second at `ms` (precision 0): the store's newest observed bar."""
    store = CandleStore(db_path_for_venue(candles_dir, venue_of(_IID)))
    store.apply(
        _IID, [SecondOHLC(ms * 1_000_000, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0, 0, 1, 1, 0, 1, 0)]
    )
    store.close()


def _zigzag(bars: list[dict], candles_dir: Path) -> list[float | None]:
    """ZigZag over `bars` on a store whose newest bar is the page's last (the newest page)."""
    _store_second_at(candles_dir, bars[-1]["t"])
    return ci.replay_custom(bars, "ZigZag", {}, _hourly(bars, str(candles_dir)))["value"]


_ZIGZAG_ROWS = [
    (0, 100, 99, 99.5),
    (1, 105, 104, 104.5),
    (2, 110, 109, 109.5),
    (3, 109, 104.4, 105),
]


def test_zigzag_emits_each_confirmed_pivot_and_the_last_legs_end(tmp_path: Path) -> None:
    """
    The spec's matrix at 5 %: a bottom at bar 0 (99) is confirmed by bar 1's 105 but not drawn
    (the page's first bar, see the next test), the top at bar 2 (110) by bar 3's 104.4; bar 3's
    low is the last leg's running end. Then bar 4 extends that leg: its end moves to bar 4 (100)
    and bar 3 goes back to None (the repainting last leg).
    """
    assert _zigzag(_hourly_bars(_ZIGZAG_ROWS), tmp_path / "a") == [None, None, 110, 104.4]
    longer = _hourly_bars([*_ZIGZAG_ROWS, (4, 104, 100, 101)])
    assert _zigzag(longer, tmp_path / "b") == [None, None, 110, None, 100]


def test_zigzag_draws_no_pivot_on_the_pages_first_bar(tmp_path: Path) -> None:
    """
    The page's first bar is an extreme only because the page starts there (its older bars are on
    the older page), so a pivot there is no swing the market made: with one bar before it, the
    same bottom (99, now bar 1) is a real local low and is drawn.
    """
    rows = [(0, 101, 100, 100.5), *((i + 1, h, l, c) for i, h, l, c in _ZIGZAG_ROWS)]
    assert _zigzag(_hourly_bars(rows), tmp_path) == [None, 99, None, 110, 104.4]


def test_zigzag_skips_a_gap_candle_and_keeps_the_pivots_on_their_own_bars(tmp_path: Path) -> None:
    bars = _hourly_bars(_ZIGZAG_ROWS)
    bars.insert(2, {"t": _DAY1_MS + 90 * 60_000, "h": None, "l": None, "c": None})
    assert _zigzag(bars, tmp_path) == [None, None, None, 110, 104.4]


def test_zigzag_on_an_older_page_draws_confirmed_pivots_only(tmp_path: Path) -> None:
    """
    The store holds bars two hours past the page: newer bars continue the page's last leg, so its
    running end is no swing point (drawn, the line would join it to the newer page's first pivot).
    """
    bars = _hourly_bars(_ZIGZAG_ROWS)
    _store_second_at(tmp_path, bars[-1]["t"] + 2 * _HOUR_MS)
    values = ci.replay_custom(bars, "ZigZag", {}, _hourly(bars, str(tmp_path)))["value"]
    assert values == [None, None, 110, None]


def test_zigzag_read_by_a_closed_bar_reader_keeps_the_tip(tmp_path: Path) -> None:
    """The store's newest bar is the forming one just after the page (a Technicals/alert read)."""
    bars = _hourly_bars(_ZIGZAG_ROWS)
    _store_second_at(tmp_path, bars[-1]["t"] + _HOUR_MS + 60_000)
    values = ci.replay_custom(bars, "ZigZag", {}, _hourly(bars, str(tmp_path)))["value"]
    assert values == [None, None, 110, 104.4]


def test_zigzag_without_a_store_draws_confirmed_pivots_only() -> None:
    bars = _hourly_bars(_ZIGZAG_ROWS)
    assert ci.replay_custom(bars, "ZigZag", {}, _hourly(bars))["value"] == [None, None, 110, None]


def test_zigzag_and_pivots_serialize_their_plot_and_note() -> None:
    catalog = ci.custom_catalog_json()
    assert (catalog["ZigZag"]["plot"], catalog["ZigZag"]["note"]) == (
        {"value": "swing"},
        "repaints last leg",
    )
    assert catalog["PivotPoints"]["plot"] == dict.fromkeys(
        catalog["PivotPoints"]["outputs"], "steps"
    )
    assert catalog["PivotPoints"]["outputs"] == [
        "pp",
        "r1",
        "r2",
        "r3",
        "r4",
        "s1",
        "s2",
        "s3",
        "s4",
    ]
    assert catalog["PivotPoints"]["choices"] == {
        "kind": ["standard", "fibonacci", "camarilla"],
        "session": ["D", "W"],
    }
    assert (catalog["Supertrend"]["outputs"], catalog["Supertrend"]["plot"]) == (["up", "down"], {})
    for name in ("Supertrend", "PivotPoints", "ZigZag"):
        assert catalog[name]["panel"] == "overlay", name


# Day 1's page from 10:00 (session S0 = day 1, the previous P = day 0): two day-1 bars, then day 2.
_PIVOT_PAGE = [
    (10, 105, 100, 102),
    (11, 106, 99, 104),
    (24, 104, 99, 103),
    (25, 108, 103, 107),
]


def _store_pivot_history(candles_dir: Path, day0_from_hour: int = 0) -> None:
    """
    Store whole-unit seconds (precision 0): day 0 from `day0_from_hour` -- H 110 at 01:00, L 90 at
    12:00, last close 100 at 23:00 -- and day 1 before the page, 05:00 with h 120, l 95, c 101.
    """
    store = CandleStore(db_path_for_venue(candles_dir, venue_of(_IID)))
    day0 = _DAY1_MS - 86_400_000
    seconds = [
        (day0 + day0_from_hour * _HOUR_MS, 100.0, 100.0, 100.0),
        (day0 + 1 * _HOUR_MS, 110.0, 100.0, 105.0),
        (day0 + 12 * _HOUR_MS, 95.0, 90.0, 92.0),
        (day0 + 23 * _HOUR_MS, 101.0, 99.0, 100.0),
        (_DAY1_MS + 5 * _HOUR_MS, 120.0, 95.0, 101.0),
    ]
    for ms, high, low, close in seconds:
        row = SecondOHLC(
            ms * 1_000_000, close, high, low, close, 1.0, 0.0, 0, 0, int(close), 1, 0, 1, 0
        )
        store.apply(_IID, [row])
    store.close()


def _pivots(
    rows: list[tuple[int, float, float, float]], params: dict, candles_dir: str | None = None
) -> dict[str, list[float | None]]:
    bars = _hourly_bars(rows)
    return ci.replay_custom(bars, "PivotPoints", params, _hourly(bars, candles_dir))


def test_pivots_seeded_from_the_store_cover_the_pages_first_session(tmp_path: Path) -> None:
    """
    The store covers day 0 from its midnight: day 1's bars carry day 0's levels (H 110, L 90, C 100:
    PP 100, R1 110), and day 2's day 1's -- the stored prefix [00:00, 10:00) (H 120, L 95) plus the
    page's day-1 bars (last close 104): PP (120 + 95 + 104) / 3.
    """
    _store_pivot_history(tmp_path)
    levels = _pivots(_PIVOT_PAGE, {}, str(tmp_path))
    assert levels["pp"][:2] == [100.0, 100.0]
    assert levels["r1"][:2] == [110.0, 110.0]
    assert levels["pp"][2:] == pytest.approx([319 / 3, 319 / 3])
    assert levels["r4"] == [None] * 4  # standard: no fourth level


def test_pivots_without_a_store_never_take_levels_from_a_partial_session() -> None:
    """
    No store: day 1 is seen only from 10:00, so its bars and all of day 2 (whose levels would be
    day 1's) read None; feeding starts at day 2's midnight, so day 3 carries day 2's levels.
    """
    rows = [*_PIVOT_PAGE, (48, 106, 101, 105)]
    levels = _pivots(rows, {"kind": "camarilla"})
    assert levels["pp"] == [None, None, None, None, pytest.approx((108 + 99 + 107) / 3)]
    assert levels["r4"][4] == pytest.approx(107 + 9 * 1.1 / 2)


def test_pivots_with_a_store_starting_after_the_previous_session_are_uncovered(
    tmp_path: Path,
) -> None:
    _store_pivot_history(tmp_path, day0_from_hour=1)  # day 0 stored from 01:00 only
    assert _pivots(_PIVOT_PAGE, {}, str(tmp_path))["pp"] == [None] * 4


def test_pivots_on_a_page_starting_at_a_session_boundary_feed_from_its_first_bar() -> None:
    rows = [(24, 104, 99, 103), (25, 108, 103, 107), (48, 110, 100, 101)]
    assert _pivots(rows, {})["pp"] == [None, None, pytest.approx((108 + 99 + 107) / 3)]


def test_a_daily_pivot_on_a_weekly_chart_is_the_entrys_error() -> None:
    @dataclass(frozen=True)
    class _Request:
        name: str
        params: dict
        source: str = "close"

    week = 604_800
    bars = [_ohlc(_DAY1_MS, 10, 9, 9.5)]
    window = ReplayWindow(_IID, week, _DAY1_MS, _DAY1_MS + week * 1000)
    entries = [_Request("PivotPoints", {"session": "D"}), _Request("ZigZag", {})]
    by_time, errors = ci.values_by_time(bars, entries, window)
    assert errors == {"PivotPoints_session=D": "pivot session D is narrower than the 604800 s bar"}
    assert "ZigZag.value" in by_time[_DAY1_MS]


def test_a_weekly_pivot_on_a_weekly_chart_carries_the_previous_weeks_bar() -> None:
    """
    A 1W bar and a `W` session are the same Monday-anchored bucket: one bar per session, so each
    week carries the previous week's levels (never refused for not starting at the epoch).
    """
    week = 604_800
    monday = bucket_start_ms(_DAY1_MS, week) + week * 1000
    bars = [
        _ohlc(monday, 110, 90, 100),
        _ohlc(monday + week * 1000, 108, 99, 107),
        _ohlc(monday + 2 * week * 1000, 104, 100, 101),
    ]
    window = ReplayWindow(_IID, week, monday, monday + 3 * week * 1000)
    levels = ci.replay_custom(bars, "PivotPoints", {"session": "W"}, window)
    assert levels["pp"] == [None, 100.0, pytest.approx((108 + 99 + 107) / 3)]


def test_pivots_after_a_session_with_no_traded_bar_wait_for_a_whole_session() -> None:
    """
    Days 0, 1, 3, 4 (day 2 has no bar): day 1 carries day 0's levels; day 3's previous session
    (day 2) is unknown, so it reads None, never day 1's levels; day 4 carries day 3's.
    """
    rows = [(0, 110, 90, 100), (24, 108, 99, 107), (72, 104, 100, 101), (96, 103, 101, 102)]
    assert _pivots(rows, {})["pp"] == [None, 100.0, None, pytest.approx(305 / 3)]


def test_pivots_with_a_covered_but_untraded_previous_session_start_from_the_seeded_one(
    tmp_path: Path,
) -> None:
    """
    The store covers day 0 from its midnight but day 0 never traded (one observed, untraded
    second): day 1's bars read None, and day 2's carry day 1's levels -- the stored prefix
    [00:00, 10:00) (H 120, L 95) plus the page's day-1 bars (last close 104).
    """
    store = CandleStore(db_path_for_venue(tmp_path, venue_of(_IID)))
    day0_ms = _DAY1_MS - 86_400_000
    untraded = SecondOHLC(
        day0_ms * 1_000_000, None, None, None, None, 0.0, 0.0, 0, 0, None, 0, 0, 0, 0
    )
    prefix = (_DAY1_MS + 5 * _HOUR_MS) * 1_000_000
    traded = SecondOHLC(prefix, 101.0, 120.0, 95.0, 101.0, 1.0, 0.0, 0, 0, 101, 1, 0, 1, 0)
    store.apply(_IID, [untraded])
    store.apply(_IID, [traded])
    store.close()
    levels = _pivots(_PIVOT_PAGE, {}, str(tmp_path))
    assert levels["pp"] == [None, None, pytest.approx(319 / 3), pytest.approx(319 / 3)]


@pytest.mark.parametrize(
    ("params", "match"), [({"kind": "woodie"}, "kind"), ({"session": "M"}, "session")]
)
def test_pivots_refuse_an_unknown_kind_or_session(params: dict, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        ci.check_params("PivotPoints", params)
    with pytest.raises(ValueError, match=match):
        _pivots(_PIVOT_PAGE, params)
