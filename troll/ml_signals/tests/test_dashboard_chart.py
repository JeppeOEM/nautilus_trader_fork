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
from collections import deque

import pytest

import ml_signals.dashboard
from ml_signals.dashboard import _coin_chart_json

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
    Root cause of flat lines: when no new OrderBookDeltas arrive, the collector's
    _live_books[iid] stays frozen. _second_loop samples the same book every second
    and emits identical bid/ask prices. The dashboard faithfully plots them as a
    flat horizontal line. This is correct behaviour — the flatness signals a stale feed.
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
