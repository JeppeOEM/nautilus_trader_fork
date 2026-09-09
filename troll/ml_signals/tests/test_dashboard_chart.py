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
Unit tests for _coin_chart_json() — mid / microprice / effective-price formulas,
crossed-book guard, and flat-line (stale feed) behaviour.

Why these bugs occur
--------------------
* Flat lines: the collector's _second_loop samples _live_books every second. If the
  WebSocket feed stalls the book freezes, and identical snapshots are emitted forever.
* Crossed book (bid >= ask): during a WebSocket reconnect the dYdX adapter sends a
  CLEAR delta then replays the new snapshot level-by-level. Sampling mid-replay can
  produce a partially-rebuilt book where bid >= ask. The fix lives in two places:
  collector._second_loop (skips the snapshot) and _coin_chart_json (skips the entry
  if it already reached _second_rolling before the guard was added).
"""

import json
import tempfile
from collections import deque
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer
from dydx_collector.second_snapshot import DydxSecondSnapshot

import ml_signals.dashboard
from ml_signals.chart_indicators import INDICATOR_CATALOG
from ml_signals.custom_indicators import ReplayWindow
from ml_signals.dashboard import _coerce_indicator_params
from ml_signals.dashboard import _coin_chart_json
from ml_signals.dashboard import _historical_candles_json
from ml_signals.dashboard import _historical_lines_json
from ml_signals.dashboard import _historical_ticks_json
from ml_signals.dashboard import _indicator_id
from ml_signals.dashboard import _indicators_json
from ml_signals.dashboard import _indicator_replay_window
from ml_signals.dashboard import _merged_indicator_catalog
from ml_signals.dashboard import _render_chart_page
from ml_signals.dashboard import _live_candles_json
from ml_signals.dashboard import _live_lines_json
from ml_signals.dashboard import _parse_indicator_spec
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_TS_NS = 1_700_000_000_000_000_000  # arbitrary fixed nanosecond timestamp


def _reset() -> None:
    ml_signals.dashboard._second_rolling.clear()
    ml_signals.dashboard._ind_rolling.clear()


def _snap(
    bid_p: float,
    ask_p: float,
    bid_s: float = 1.0,
    ask_s: float = 1.0,
    buy_vol: float = 0.0,
    sell_vol: float = 0.0,
    ts_ns: int = _TS_NS,
) -> dict:
    return {
        "instrument_id": _IID,
        "bid_prices": [bid_p],
        "bid_sizes": [bid_s],
        "ask_prices": [ask_p],
        "ask_sizes": [ask_s],
        "buy_volume": buy_vol,
        "sell_volume": sell_vol,
        "buy_count": 0,
        "sell_count": 0,
        "ts_event": ts_ns,
        "ts_init": ts_ns,
    }


def _chart() -> dict:
    return json.loads(_coin_chart_json(_IID))


# ---------------------------------------------------------------------------
# Output keys
# ---------------------------------------------------------------------------

def test_chart_json_output_keys() -> None:
    _reset()
    result = json.loads(_coin_chart_json("UNKNOWN.DYDX"))
    assert set(result) == {"ts", "bid", "ask", "mid", "micro", "price", "sig_ts", "ofi_10_z", "obi_10"}


# ---------------------------------------------------------------------------
# Mid formula
# ---------------------------------------------------------------------------

def test_mid_is_arithmetic_mean_of_bid_and_ask() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0)])
    result = _chart()
    assert result["mid"] == [pytest.approx(101.0)]


def test_mid_with_asymmetric_spread() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(49999.5, 50001.5)])
    result = _chart()
    assert result["mid"] == [pytest.approx(50000.5)]


# ---------------------------------------------------------------------------
# Microprice formula
# ---------------------------------------------------------------------------

def test_microprice_balanced_sizes_equals_mid() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, bid_s=5.0, ask_s=5.0)])
    result = _chart()
    assert result["micro"] == [pytest.approx(101.0)]


def test_microprice_bid_heavy_closer_to_ask() -> None:
    # Large bid size → buyers dominate → fair price closer to ask
    # micro = (100*1 + 102*10) / 11 = 1120/11 ≈ 101.818
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, bid_s=10.0, ask_s=1.0)])
    result = _chart()
    mid = 101.0
    assert result["micro"][0] > mid
    assert result["micro"][0] == pytest.approx((100.0 * 1.0 + 102.0 * 10.0) / 11.0)


def test_microprice_ask_heavy_closer_to_bid() -> None:
    # Large ask size → sellers dominate → fair price closer to bid
    # micro = (100*10 + 102*1) / 11 = 1102/11 ≈ 100.182
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, bid_s=1.0, ask_s=10.0)])
    result = _chart()
    mid = 101.0
    assert result["micro"][0] < mid
    assert result["micro"][0] == pytest.approx((100.0 * 10.0 + 102.0 * 1.0) / 11.0)


def test_microprice_always_between_bid_and_ask() -> None:
    _reset()
    cases = [
        (100.0, 101.0, 1.0, 1.0),
        (100.0, 101.0, 10.0, 1.0),
        (100.0, 101.0, 1.0, 10.0),
        (50000.0, 50001.0, 3.5, 7.2),
    ]
    for bid_p, ask_p, bid_s, ask_s in cases:
        ml_signals.dashboard._second_rolling[_IID] = deque([_snap(bid_p, ask_p, bid_s, ask_s)])
        res = _chart()
        assert bid_p <= res["micro"][0] <= ask_p, (
            f"microprice {res['micro'][0]} outside [{bid_p}, {ask_p}] "
            f"with bid_s={bid_s} ask_s={ask_s}"
        )


def test_microprice_falls_back_to_mid_when_total_size_zero() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, bid_s=0.0, ask_s=0.0)])
    result = _chart()
    assert result["micro"] == [pytest.approx(101.0)]


# ---------------------------------------------------------------------------
# Effective price formula
# ---------------------------------------------------------------------------

def test_effective_price_all_buy_volume_equals_ask() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, buy_vol=5.0, sell_vol=0.0)])
    result = _chart()
    assert result["price"] == [pytest.approx(102.0)]


def test_effective_price_all_sell_volume_equals_bid() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, buy_vol=0.0, sell_vol=5.0)])
    result = _chart()
    assert result["price"] == [pytest.approx(100.0)]


def test_effective_price_balanced_volume_equals_mid() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, buy_vol=5.0, sell_vol=5.0)])
    result = _chart()
    assert result["price"] == [pytest.approx(101.0)]


def test_effective_price_zero_volume_falls_back_to_mid() -> None:
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, buy_vol=0.0, sell_vol=0.0)])
    result = _chart()
    assert result["price"] == [pytest.approx(101.0)]


def test_effective_price_always_bounded_within_bid_ask() -> None:
    _reset()
    cases = [
        (100.0, 102.0, 0.0, 0.0),
        (100.0, 102.0, 10.0, 0.0),
        (100.0, 102.0, 0.0, 10.0),
        (100.0, 102.0, 7.0, 3.0),
        (100.0, 102.0, 3.0, 7.0),
        (50000.0, 50000.5, 100.0, 1.0),
    ]
    for bid_p, ask_p, buy_vol, sell_vol in cases:
        ml_signals.dashboard._second_rolling[_IID] = deque([
            _snap(bid_p, ask_p, buy_vol=buy_vol, sell_vol=sell_vol)
        ])
        res = _chart()
        p = res["price"][0]
        assert bid_p - 1e-9 <= p <= ask_p + 1e-9, (
            f"effective price {p} outside [{bid_p}, {ask_p}] "
            f"with buy_vol={buy_vol} sell_vol={sell_vol}"
        )


# ---------------------------------------------------------------------------
# Crossed-book guard
# ---------------------------------------------------------------------------

def test_crossed_book_snap_is_skipped() -> None:
    # bid > ask — produced during reconnect snapshot replay; must not appear in output
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(102.0, 100.0)])
    result = _chart()
    assert result["ts"] == [], "crossed-book snapshot must be dropped"


def test_touched_book_snap_is_skipped() -> None:
    # bid == ask (zero spread) — also invalid; skip it
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 100.0)])
    result = _chart()
    assert result["ts"] == []


def test_valid_snaps_before_and_after_crossed_snap() -> None:
    # A crossed entry in the middle must not corrupt surrounding valid entries
    _reset()
    ts1, ts2, ts3 = _TS_NS, _TS_NS + 1_000_000_000, _TS_NS + 2_000_000_000
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(100.0, 102.0, ts_ns=ts1),
        _snap(103.0, 101.0, ts_ns=ts2),   # crossed — must be dropped
        _snap(100.0, 102.0, ts_ns=ts3),
    ])
    result = _chart()
    assert result["ts"] == [ts1 // 1_000_000, ts3 // 1_000_000]
    assert len(result["bid"]) == 2
    assert result["bid"] == [pytest.approx(100.0), pytest.approx(100.0)]


# ---------------------------------------------------------------------------
# Empty / missing levels
# ---------------------------------------------------------------------------

def test_empty_bid_prices_snap_is_skipped() -> None:
    _reset()
    snap = _snap(100.0, 102.0)
    snap["bid_prices"] = []
    snap["bid_sizes"] = []
    ml_signals.dashboard._second_rolling[_IID] = deque([snap])
    result = _chart()
    assert result["ts"] == []


def test_empty_ask_prices_snap_is_skipped() -> None:
    _reset()
    snap = _snap(100.0, 102.0)
    snap["ask_prices"] = []
    snap["ask_sizes"] = []
    ml_signals.dashboard._second_rolling[_IID] = deque([snap])
    result = _chart()
    assert result["ts"] == []


# ---------------------------------------------------------------------------
# Flat-line (stale feed) behaviour — documented, not a formula bug
# ---------------------------------------------------------------------------

def test_stale_feed_produces_identical_bid_ask_across_ticks() -> None:
    """
    When no new OrderBookDeltas arrive the collector's _live_books[iid] stays frozen.
    _second_loop now has a staleness guard (_STALE_BOOK_NS=5s) that skips emission, so
    in practice this path is not reached for instruments with a live feed.

    If stale snapshots DO reach _second_rolling (e.g. gap threshold not yet exceeded,
    or during the first few seconds after reconnect), consecutive 1-second snapshots
    with the same bid/ask produce a flat chart line.  No gap-null is inserted here
    because the timestamps are only 1s apart (below _CHART_GAP_THRESHOLD_MS=2500ms).
    """
    _reset()
    frozen_bid, frozen_ask = 50000.0, 50001.0
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(frozen_bid, frozen_ask, ts_ns=_TS_NS + i * 1_000_000_000)
        for i in range(5)
    ])
    result = _chart()
    assert all(b == frozen_bid for b in result["bid"]), "all bids identical — stale feed"
    assert all(a == frozen_ask for a in result["ask"]), "all asks identical — stale feed"
    assert len(result["ts"]) == 5


# ---------------------------------------------------------------------------
# Timestamp conversion
# ---------------------------------------------------------------------------

def test_ts_event_converted_from_ns_to_ms() -> None:
    _reset()
    ts_ns = 1_700_000_000_123_456_789
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0, ts_ns=ts_ns)])
    result = _chart()
    assert result["ts"] == [ts_ns // 1_000_000]


# ---------------------------------------------------------------------------
# Gap detection — null insertion for stale-book gaps (_CHART_GAP_THRESHOLD_MS)
# ---------------------------------------------------------------------------

def test_gap_below_threshold_does_not_insert_null() -> None:
    """Consecutive snapshots within 2.5s do NOT get a null break inserted."""
    _reset()
    ts1 = _TS_NS
    ts2 = ts1 + 2_000_000_000  # 2s gap — below 2.5s threshold
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(100.0, 102.0, ts_ns=ts1),
        _snap(101.0, 103.0, ts_ns=ts2),
    ])
    result = _chart()
    assert result["ts"] == [ts1 // 1_000_000, ts2 // 1_000_000]
    assert len(result["bid"]) == 2
    assert None not in result["bid"]


def test_gap_above_threshold_inserts_null_break() -> None:
    """
    Consecutive snapshots with a gap > 2.5s get a null data point inserted just
    before the second snapshot.  This breaks the Plotly line, preventing a
    misleading horizontal flatline from being drawn across the missing seconds.
    The null is inserted at (ts2_ms - 1) so Plotly stops the line before the gap.
    """
    _reset()
    ts1 = _TS_NS
    ts2 = ts1 + 5_000_000_000  # 5s gap — exceeds 2.5s threshold
    ts1_ms = ts1 // 1_000_000
    ts2_ms = ts2 // 1_000_000
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(100.0, 102.0, ts_ns=ts1),
        _snap(101.0, 103.0, ts_ns=ts2),
    ])
    result = _chart()
    # Expected: [ts1_ms, ts2_ms-1 (null), ts2_ms (valid)]
    assert len(result["ts"]) == 3
    assert result["ts"] == [ts1_ms, ts2_ms - 1, ts2_ms]
    # Middle entry is the null break
    assert result["bid"][1] is None
    assert result["ask"][1] is None
    assert result["mid"][1] is None
    assert result["micro"][1] is None
    assert result["price"][1] is None
    # Flanking entries are valid
    assert result["bid"][0] == 100.0
    assert result["ask"][0] == 102.0
    assert result["bid"][2] == 101.0
    assert result["ask"][2] == 103.0


def test_multiple_gaps_each_inserts_one_null() -> None:
    """Multiple gaps each get their own null break."""
    _reset()
    ts1 = _TS_NS
    ts2 = ts1 + 6_000_000_000   # 6s gap
    ts3 = ts2 + 8_000_000_000   # 8s gap
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(100.0, 102.0, ts_ns=ts1),
        _snap(101.0, 103.0, ts_ns=ts2),
        _snap(102.0, 104.0, ts_ns=ts3),
    ])
    result = _chart()
    # 3 valid points + 2 null breaks = 5 total entries
    assert len(result["ts"]) == 5
    assert result["bid"][1] is None   # null after first gap
    assert result["bid"][3] is None   # null after second gap
    assert result["bid"][0] == 100.0
    assert result["bid"][2] == 101.0
    assert result["bid"][4] == 102.0


def test_first_snapshot_never_gets_null_prefix() -> None:
    """No null is inserted before the very first snapshot — gaps only tracked after first point."""
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(100.0, 102.0, ts_ns=_TS_NS),
    ])
    result = _chart()
    assert len(result["ts"]) == 1
    assert result["bid"] == [100.0]


# ---------------------------------------------------------------------------
# Live candles — leading-bucket churn
# ---------------------------------------------------------------------------

def test_live_candles_drops_partial_leading_bucket() -> None:
    """The oldest bucket loses members every second as the deque evicts old snapshots
    (maxlen), so its open/high/low would otherwise change on every poll even though
    it isn't the currently-forming candle. Only fully-aged buckets should be returned.
    """
    _reset()
    bar = 60
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(100.0, 102.0, ts_ns=_TS_NS),  # oldest bucket -- must be dropped
        _snap(110.0, 112.0, ts_ns=_TS_NS + bar * 1_000_000_000),
    ])
    candles = json.loads(_live_candles_json(_IID, bar))["candles"]
    assert len(candles) == 1
    assert candles[0]["o"] == 111.0


def test_live_candles_keeps_only_bucket_when_alone() -> None:
    """With just one bucket there's nothing to drop -- it's the only data available."""
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([_snap(100.0, 102.0)])
    candles = json.loads(_live_candles_json(_IID, 60))["candles"]
    assert len(candles) == 1
    assert candles[0]["o"] == 101.0


# ---------------------------------------------------------------------------
# Catalog-backed candles/ticks (_historical_candles_json / _historical_ticks_json)
# ---------------------------------------------------------------------------

def _write_trades_to_catalog(tmp_path: str, n: int = 10) -> tuple[int, int]:
    """Write n TradeTicks 60s apart, prices 100,101,...,100+n-1, into a temp catalog.

    Mirrors the temp-catalog-with-real-TradeTicks pattern from
    ml_signals/tests/test_timeframe_backtest.py's _catalog_with_trades. Returns
    (first_ts_ns, last_ts_ns) for the caller to build a bounding [start_ms, end_ms].
    """
    base_ns = _TS_NS
    step_ns = 60 * 1_000_000_000
    trades = [
        TradeTick(
            instrument_id=InstrumentId.from_str(_IID), price=Price(100.0 + i, 1), size=Quantity(1.0 + i, 1),
            aggressor_side=AggressorSide.BUYER if i % 2 == 0 else AggressorSide.SELLER,
            trade_id=TradeId(str(i)), ts_event=base_ns + i * step_ns, ts_init=base_ns + i * step_ns,
        )
        for i in range(n)
    ]
    catalog = ParquetDataCatalog(tmp_path)
    catalog.write_data(trades)
    return trades[0].ts_event, trades[-1].ts_event


def test_historical_candles_json_builds_from_catalog_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-trips real TradeTicks through the catalog into OHLC candles."""
    with tempfile.TemporaryDirectory() as tmp:
        first_ns, last_ns = _write_trades_to_catalog(tmp, n=10)
        monkeypatch.setattr(ml_signals.dashboard, "CATALOG_PATH", tmp)
        start_ms = first_ns // 1_000_000 - 1
        end_ms = last_ns // 1_000_000 + 1
        # bar_seconds wide enough that all 10 trades (9 minutes apart) land in one candle
        candles = json.loads(_historical_candles_json(_IID, start_ms, end_ms, 3600))["candles"]
        assert len(candles) == 1
        assert candles[0]["o"] == 100.0
        assert candles[0]["h"] == 109.0
        assert candles[0]["l"] == 100.0
        assert candles[0]["c"] == 109.0


def test_historical_ticks_json_returns_raw_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    """Returns individual trade prints, not aggregated candles."""
    with tempfile.TemporaryDirectory() as tmp:
        first_ns, last_ns = _write_trades_to_catalog(tmp, n=5)
        monkeypatch.setattr(ml_signals.dashboard, "CATALOG_PATH", tmp)
        start_ms = first_ns // 1_000_000 - 1
        end_ms = last_ns // 1_000_000 + 1
        ticks = json.loads(_historical_ticks_json(_IID, start_ms, end_ms))["ticks"]
        assert len(ticks) == 5
        assert [t["price"] for t in ticks] == [100.0, 101.0, 102.0, 103.0, 104.0]
        assert ticks[0]["side"] == "BUYER"
        assert ticks[1]["side"] == "SELLER"


def test_historical_ticks_json_respects_row_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """max_rows caps the response even when more trades exist in the requested window."""
    with tempfile.TemporaryDirectory() as tmp:
        first_ns, last_ns = _write_trades_to_catalog(tmp, n=10)
        monkeypatch.setattr(ml_signals.dashboard, "CATALOG_PATH", tmp)
        start_ms = first_ns // 1_000_000 - 1
        end_ms = last_ns // 1_000_000 + 1
        body = json.loads(_historical_ticks_json(_IID, start_ms, end_ms, max_rows=3))
        assert len(body["ticks"]) == 3
        assert body["truncated"] is True


def test_historical_ticks_json_not_truncated_under_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """No truncation flagged when the window/row count are both within bounds."""
    with tempfile.TemporaryDirectory() as tmp:
        first_ns, last_ns = _write_trades_to_catalog(tmp, n=5)
        monkeypatch.setattr(ml_signals.dashboard, "CATALOG_PATH", tmp)
        start_ms = first_ns // 1_000_000 - 1
        end_ms = last_ns // 1_000_000 + 1
        body = json.loads(_historical_ticks_json(_IID, start_ms, end_ms))
        assert len(body["ticks"]) == 5
        assert body["truncated"] is False


def test_historical_ticks_json_clamps_wide_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request window wider than _MAX_TICK_WINDOW_NS is clamped before hitting the
    catalog (MEM-01) -- trades older than the clamped start are excluded and the
    response is flagged truncated so the client's pagination cursor doesn't skip them."""
    with tempfile.TemporaryDirectory() as tmp:
        # 5 trades 2.5h apart (0,2.5,5,7.5,10h) -- a 10h span, wider than the 6h clamp,
        # with the clamp boundary (4h before the last trade) landing well clear of any
        # trade timestamp so this isn't sensitive to ms-rounding at the edges.
        base_ns = _TS_NS
        step_ns = int(2.5 * 3600 * 1_000_000_000)
        trades = [
            TradeTick(
                instrument_id=InstrumentId.from_str(_IID), price=Price(100.0 + i, 1),
                size=Quantity(1.0, 1), aggressor_side=AggressorSide.BUYER,
                trade_id=TradeId(str(i)), ts_event=base_ns + i * step_ns,
                ts_init=base_ns + i * step_ns,
            )
            for i in range(5)
        ]
        ParquetDataCatalog(tmp).write_data(trades)
        monkeypatch.setattr(ml_signals.dashboard, "CATALOG_PATH", tmp)
        start_ms = trades[0].ts_event // 1_000_000 - 1
        end_ms = trades[-1].ts_event // 1_000_000 + 1
        body = json.loads(_historical_ticks_json(_IID, start_ms, end_ms))
        assert body["truncated"] is True
        # Clamped to the last 6h of the requested window: only the 6h/9h/12h trades survive.
        assert len(body["ticks"]) == 3


# ---------------------------------------------------------------------------
# Lines mode (Story 8.1) -- catalog-backed historical rows, and a regression guard
# proving the extracted price-series computation didn't change _coin_chart_json's output.
# ---------------------------------------------------------------------------

def _write_snapshots_to_catalog(tmp_path: str, n: int = 5) -> tuple[int, int]:
    """Write n DydxSecondSnapshots 1s apart (realistic cadence -- wider spacing would
    trip the gap-detection None-insertion in _price_series_rows), bid/ask prices
    climbing by 1 each step, into a temp catalog. Mirrors _write_trades_to_catalog's pattern."""
    base_ns = _TS_NS
    step_ns = 1_000_000_000
    snapshots = [
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(_IID),
            bid_prices=[100.0 + i], bid_sizes=[1.0],
            ask_prices=[102.0 + i], ask_sizes=[1.0],
            buy_volume=0.0, sell_volume=0.0, buy_count=0, sell_count=0,
            ts_event=base_ns + i * step_ns, ts_init=base_ns + i * step_ns,
        )
        for i in range(n)
    ]
    catalog = ParquetDataCatalog(tmp_path)
    catalog.write_data(snapshots)
    return snapshots[0].ts_event, snapshots[-1].ts_event


def test_historical_lines_json_builds_from_catalog_snapshots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-trips real DydxSecondSnapshots through the catalog into bid/ask/mid/micro/price rows."""
    with tempfile.TemporaryDirectory() as tmp:
        first_ns, last_ns = _write_snapshots_to_catalog(tmp, n=5)
        monkeypatch.setattr(ml_signals.dashboard, "CATALOG_PATH", tmp)
        start_ms = first_ns // 1_000_000 - 1
        end_ms = last_ns // 1_000_000 + 1
        rows = json.loads(_historical_lines_json(_IID, start_ms, end_ms))["rows"]
        assert len(rows) == 5
        assert rows[0]["bid"] == pytest.approx(100.0)
        assert rows[0]["ask"] == pytest.approx(102.0)
        assert rows[0]["mid"] == pytest.approx(101.0)
        assert rows[-1]["bid"] == pytest.approx(104.0)


def test_live_lines_json_matches_coin_chart_json_price_series() -> None:
    """The extracted price-series computation (Story 8.1) must produce identical
    bid/ask/mid/micro/price values to _coin_chart_json's own fields for the same
    buffer -- a pure extraction, not a behavior change."""
    _reset()
    ml_signals.dashboard._second_rolling[_IID] = deque([
        _snap(100.0, 102.0, ts_ns=_TS_NS),
        _snap(101.0, 103.0, ts_ns=_TS_NS + 1_000_000_000),
    ])
    chart = _chart()
    rows = json.loads(_live_lines_json(_IID))["rows"]
    assert [r["t"] for r in rows] == chart["ts"]
    assert [r["bid"] for r in rows] == chart["bid"]
    assert [r["ask"] for r in rows] == chart["ask"]
    assert [r["mid"] for r in rows] == chart["mid"]
    assert [r["micro"] for r in rows] == chart["micro"]
    assert [r["price"] for r in rows] == chart["price"]


# ---------------------------------------------------------------------------
# Indicator endpoint glue (_parse_indicator_spec / _coerce_indicator_params /
# _indicator_id / _indicators_json) -- Story 8.2
# ---------------------------------------------------------------------------

def test_parse_indicator_spec_splits_entries_on_pipe_and_params_on_comma() -> None:
    specs = _parse_indicator_spec("SimpleMovingAverage:period=20|Stochastics:period_k=14,period_d=3")
    assert specs == [
        ("SimpleMovingAverage", {"period": "20"}),
        ("Stochastics", {"period_k": "14", "period_d": "3"}),
    ]


def test_parse_indicator_spec_handles_entry_with_no_params() -> None:
    assert _parse_indicator_spec("VolumeWeightedAveragePrice") == [("VolumeWeightedAveragePrice", {})]


def test_coerce_indicator_params_casts_to_default_types() -> None:
    spec = INDICATOR_CATALOG["KeltnerChannel"]
    coerced = _coerce_indicator_params(spec, {"period": "20", "k_multiplier": "1.5", "use_previous": "false"})
    assert coerced == {"period": 20, "k_multiplier": 1.5, "use_previous": False}


def test_coerce_indicator_params_drops_unknown_keys() -> None:
    spec = INDICATOR_CATALOG["SimpleMovingAverage"]
    assert _coerce_indicator_params(spec, {"not_a_real_param": "1"}) == {}


def test_merged_indicator_catalog_tags_native_entries() -> None:
    merged = _merged_indicator_catalog()
    assert merged["SimpleMovingAverage"]["category"] == "native"
    assert merged["SimpleMovingAverage"]["panel"] == "overlay"


def test_merged_indicator_catalog_tags_custom_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    from ml_signals import custom_indicators as _ci

    monkeypatch.setitem(
        _ci.CUSTOM_INDICATOR_CATALOG,
        "PlaceholderCustom",
        _ci.CustomIndicatorSpec(params={}, panel="histogram", replay=lambda c, p, w: {}),
    )
    merged = _merged_indicator_catalog()
    assert merged["PlaceholderCustom"] == {"params": {}, "panel": "histogram", "category": "custom"}
    assert merged["SimpleMovingAverage"]["category"] == "native"  # native entries unaffected


def test_merged_indicator_catalog_raises_on_name_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    from ml_signals import custom_indicators as _ci

    # A name registered in both catalogs would otherwise silently resolve differently
    # depending which code path you ask (the merged catalog lists it last-write-wins;
    # _indicators_json's dispatch checks native first) -- must fail loud, not diverge.
    monkeypatch.setitem(
        _ci.CUSTOM_INDICATOR_CATALOG,
        "SimpleMovingAverage",
        _ci.CustomIndicatorSpec(params={}, panel="oscillator", replay=lambda c, p, w: {}),
    )
    with pytest.raises(ValueError, match="registered in both catalogs"):
        _merged_indicator_catalog()


def test_indicator_replay_window_both_bounds_set_is_historical() -> None:
    window = _indicator_replay_window("BTC-USD-PERP.DYDX", 60, 1000, 2000)
    assert (window.start_ms, window.end_ms) == (1000, 2000)


def test_indicator_replay_window_only_one_bound_falls_back_to_live() -> None:
    # A query string with only "start" (or only "end") parseable must not leave the
    # window half-set -- coin_indicators_handler takes the live candle path in this
    # case, and the window's bounds must agree (both None), not silently carry the one
    # real value through.
    assert _indicator_replay_window("BTC-USD-PERP.DYDX", 60, 1000, None).start_ms is None
    assert _indicator_replay_window("BTC-USD-PERP.DYDX", 60, None, 2000).end_ms is None


def test_indicator_replay_window_neither_bound_set_is_live() -> None:
    window = _indicator_replay_window("BTC-USD-PERP.DYDX", 60, None, None)
    assert (window.start_ms, window.end_ms) == (None, None)


def test_render_chart_page_figure_row_counts_stay_in_lockstep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plotly's make_subplots raises if rows/row_heights/subplot_titles lengths disagree --
    nothing else in this suite exercises _render_chart_page's figure construction at all, so a
    future row add/remove (each Epic 10 custom-indicator story has retired one fixed row) could
    silently break page load with no test catching it until someone opens the page by hand."""
    monkeypatch.setattr(
        ml_signals.dashboard._chart_data, "compute_chart_series",
        lambda *a, **k: {
            "microprice": [], "spread": [],
            "imbalance": [], "mid_imbalance": [], "bid_depth": [], "ask_depth": [],
        },
    )
    html_out = _render_chart_page("BTC-USD-PERP.DYDX", 0, 1000)
    assert "Book imbalance" in html_out


def test_indicator_id_sorts_params_for_a_stable_key() -> None:
    assert _indicator_id("BollingerBands", {"k": 2.0, "period": 5}) == "BollingerBands_k=2.0,period=5"
    assert _indicator_id("VolumeWeightedAveragePrice", {}) == "VolumeWeightedAveragePrice"


def _window() -> ReplayWindow:
    return ReplayWindow(instrument_id="BTC-USD-PERP.DYDX", bar_seconds=60, start_ms=None, end_ms=None)


def test_indicators_json_returns_points_per_output_attribute() -> None:
    candles = [{"t": i * 60_000, "o": c, "h": c, "l": c, "c": c, "v": 1.0}
               for i, c in enumerate([float(x) for x in range(1, 11)])]
    body, status = _indicators_json(
        candles, "SimpleMovingAverage:period=3|BollingerBands:period=5,k=2", _window(),
    )
    assert status == 200
    payload = json.loads(body)
    assert set(payload.keys()) == {"SimpleMovingAverage_period=3", "BollingerBands_k=2.0,period=5"}
    sma_points = payload["SimpleMovingAverage_period=3"]["value"]
    assert [p["t"] for p in sma_points] == [c["t"] for c in candles]
    assert set(payload["BollingerBands_k=2.0,period=5"].keys()) == {"upper", "middle", "lower"}


def test_indicators_json_dispatches_to_custom_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    from ml_signals import custom_indicators as _ci

    monkeypatch.setitem(
        _ci.CUSTOM_INDICATOR_CATALOG,
        "PlaceholderCustom",
        _ci.CustomIndicatorSpec(
            params={}, panel="histogram",
            replay=lambda candles, params, window: {"value": [c["c"] for c in candles]},
        ),
    )
    candles = [{"t": 0, "c": 1.0}, {"t": 60_000, "c": 2.0}]
    body, status = _indicators_json(candles, "PlaceholderCustom", _window())
    assert status == 200
    assert json.loads(body)["PlaceholderCustom"]["value"] == [
        {"t": 0, "value": 1.0}, {"t": 60_000, "value": 2.0},
    ]


def test_indicators_json_returns_400_for_unknown_indicator() -> None:
    body, status = _indicators_json([], "NotARealIndicator:period=3", _window())
    assert status == 400
    assert "Unknown indicator" in json.loads(body)["error"]


def test_indicators_json_returns_400_instead_of_raising_for_malformed_spec() -> None:
    # Regression: a param entry missing "=" used to raise ValueError out of
    # _parse_indicator_spec's dict() construction, uncaught -- an aiohttp 500, not a
    # clean 4xx, for what is just bad/untrusted query-string input.
    candles = [{"t": 0, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}]
    body, status = _indicators_json(candles, "SimpleMovingAverage:period", _window())
    assert status == 400
    assert "error" in json.loads(body)


def test_indicators_json_returns_400_for_non_numeric_param_value() -> None:
    # A non-numeric value for an int/float param used to raise ValueError out of
    # _coerce_indicator_params' int()/float() coercion, uncaught.
    candles = [{"t": 0, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0}]
    body, status = _indicators_json(candles, "SimpleMovingAverage:period=not_a_number", _window())
    assert status == 400
    assert "error" in json.loads(body)


# -- Story 10.5: persisted per-instrument indicator config (GET/POST) -------------------------


async def _config_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setattr(
        ml_signals.dashboard, "CHART_INDICATOR_CONFIG_PATH", str(tmp_path / "chart_indicators.toml"),
    )
    app = ml_signals.dashboard.make_app("redis://127.0.0.1:6379", str(tmp_path / "catalog"))
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_indicator_config_get_on_fresh_file_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    async with await _config_client(monkeypatch, tmp_path) as client:
        resp = await client.get("/data/coin/BTC-USD-PERP.DYDX/indicator-config")
        assert resp.status == 200
        assert await resp.json() == []


@pytest.mark.asyncio
async def test_indicator_config_post_then_get_round_trips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    payload = [
        {"name": "CumulativeVolumeDelta", "params": {}, "category": "custom"},
        {"name": "RelativeStrengthIndex", "params": {"period": 14}, "category": "native"},
    ]
    async with await _config_client(monkeypatch, tmp_path) as client:
        post_resp = await client.post("/data/coin/BTC-USD-PERP.DYDX/indicator-config", json=payload)
        assert post_resp.status == 200

        get_resp = await client.get("/data/coin/BTC-USD-PERP.DYDX/indicator-config")
        assert await get_resp.json() == payload

        # A different instrument's config is untouched -- keyed by instrument_id.
        other_resp = await client.get("/data/coin/ETH-USD-PERP.DYDX/indicator-config")
        assert await other_resp.json() == []


@pytest.mark.asyncio
async def test_indicator_config_post_ignores_client_side_id_field(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # A client-sent "id" (the picker UI's own sequence counter) must not be required
    # or persisted -- confirmed here by sending one and asserting it never comes back.
    payload = [{"id": 7, "name": "OFI", "params": {}, "category": "custom"}]
    async with await _config_client(monkeypatch, tmp_path) as client:
        post_resp = await client.post("/data/coin/BTC-USD-PERP.DYDX/indicator-config", json=payload)
        assert post_resp.status == 200

        get_resp = await client.get("/data/coin/BTC-USD-PERP.DYDX/indicator-config")
        saved = await get_resp.json()
        assert saved == [{"name": "OFI", "params": {}, "category": "custom"}]
        assert "id" not in saved[0]


@pytest.mark.asyncio
async def test_indicator_config_post_malformed_payload_returns_400(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    async with await _config_client(monkeypatch, tmp_path) as client:
        resp = await client.post(
            "/data/coin/BTC-USD-PERP.DYDX/indicator-config", json=[{"name": "OFI"}],  # missing category
        )
        assert resp.status == 400
        assert "error" in await resp.json()


@pytest.mark.asyncio
async def test_indicator_config_post_toml_unrepresentable_param_returns_400_not_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # A JSON null decodes to Python None, which tomli_w cannot serialize -- must be a
    # structured 400 from save_coin_indicator_config_handler's widened try/except, not
    # an unhandled 500 (Review Finding: try/except previously only wrapped JSON parsing).
    payload = [{"name": "OFI", "params": {"threshold": None}, "category": "custom"}]
    async with await _config_client(monkeypatch, tmp_path) as client:
        resp = await client.post("/data/coin/BTC-USD-PERP.DYDX/indicator-config", json=payload)
        assert resp.status == 400
        assert "error" in await resp.json()


@pytest.mark.asyncio
async def test_indicator_config_get_on_corrupt_file_returns_500_not_crash(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # A hand-edited/corrupt chart_indicators.toml (AC #1 makes this file human-editable)
    # must surface as a diagnosable 500, not an unhandled exception (Review Finding).
    path = tmp_path / "chart_indicators.toml"
    path.write_text("this is not valid toml [[[")
    monkeypatch.setattr(ml_signals.dashboard, "CHART_INDICATOR_CONFIG_PATH", str(path))
    app = ml_signals.dashboard.make_app("redis://127.0.0.1:6379", str(tmp_path / "catalog"))
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/data/coin/BTC-USD-PERP.DYDX/indicator-config")
        assert resp.status == 500
        assert "error" in await resp.json()
