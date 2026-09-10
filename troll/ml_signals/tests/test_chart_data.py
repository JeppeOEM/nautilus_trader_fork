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
Integration tests for chart_data.compute_chart_series() -- the /chart/{id} page's
imbalance/mid-imbalance/depth/spread/microprice data source.

Story 11.1: this function previously replayed OrderBookDeltas, which are only persisted
per-instrument when store_order_book_deltas is opted in (default off) -- so it silently
returned empty series for every instrument in the live catalog. It now reads the always-
persisted DydxSecondSnapshot records instead. These tests write real DydxSecondSnapshot
objects into a real ParquetDataCatalog (TEST-03: no mocking Nautilus internals) and assert
on the computed series.
"""

import tempfile

import pytest
from dydx_collector.second_snapshot import DydxSecondSnapshot

from ml_signals.chart_data import compute_chart_series
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_TS_NS = 1_700_000_000_000_000_000
_STEP_NS = 1_000_000_000


def _snapshot(
    ts_event: int,
    bid_prices: list[float],
    bid_sizes: list[float],
    ask_prices: list[float],
    ask_sizes: list[float],
) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=bid_prices,
        bid_sizes=bid_sizes,
        ask_prices=ask_prices,
        ask_sizes=ask_sizes,
        buy_volume=0.0,
        sell_volume=0.0,
        buy_count=0,
        sell_count=0,
        ts_event=ts_event,
        ts_init=ts_event,
    )


def _write(tmp_path: str, snapshots: list[DydxSecondSnapshot]) -> None:
    ParquetDataCatalog(tmp_path).write_data(snapshots)


def test_compute_chart_series_empty_window_returns_all_empty_series() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data = compute_chart_series(tmp, _IID, start_ns=0, end_ns=1)
        assert data == {
            "microprice": [],
            "spread": [],
            "imbalance": [],
            "mid_imbalance": [],
            "bid_depth": [],
            "ask_depth": [],
        }


def test_compute_chart_series_two_levels_no_mid_imbalance() -> None:
    """
    2-level book: imbalance/depth/spread/microprice populate; mid_imbalance (levels 2-3)
    must not, since there's no level index 2 to average.
    """
    snap = _snapshot(_TS_NS, [100.0, 99.0], [2.0, 2.0], [101.0, 102.0], [8.0, 2.0])
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, [snap])
        data = compute_chart_series(tmp, _IID, start_ns=_TS_NS - 1, end_ns=_TS_NS + 1)

    assert data["mid_imbalance"] == []
    assert data["spread"] == [{"time": pytest.approx(_TS_NS / 1e9), "value": pytest.approx(1.0)}]
    assert data["bid_depth"][0]["value"] == pytest.approx(4.0)
    assert data["ask_depth"][0]["value"] == pytest.approx(10.0)
    # aggregate imbalance = total_bid / (total_bid + total_ask) = 4 / 14
    assert data["imbalance"][0]["value"] == pytest.approx(4.0 / 14.0)
    # microprice = (bid*ask_size + ask*bid_size) / (bid_size+ask_size) at level 0
    assert data["microprice"][0]["value"] == pytest.approx((100.0 * 8.0 + 101.0 * 2.0) / 10.0)


def test_compute_chart_series_three_levels_populates_mid_imbalance() -> None:
    snap = _snapshot(
        _TS_NS,
        [100.0, 99.0, 98.0],
        [1.0, 1.0, 1.0],
        [101.0, 102.0, 103.0],
        [1.0, 3.0, 1.0],
    )
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, [snap])
        data = compute_chart_series(tmp, _IID, start_ns=_TS_NS - 1, end_ns=_TS_NS + 1)

    # per_level[1] = 1/(1+3) = 0.25, per_level[2] = 1/(1+1) = 0.5 -> mean 0.375
    assert data["mid_imbalance"] == [
        {"time": pytest.approx(_TS_NS / 1e9), "value": pytest.approx(0.375)}
    ]


def test_compute_chart_series_thin_ask_side_does_not_crash_or_populate_mid_imbalance() -> None:
    """
    Bid has 3 levels, ask only has 2 -- per_level (zip of both sides) is length 2, so the
    levels>=3 mid-imbalance guard must key off per_level's own length, not bid-side count alone,
    or this indexes past the end of a length-2 list.
    """
    snap = _snapshot(
        _TS_NS,
        [100.0, 99.0, 98.0],
        [1.0, 1.0, 1.0],
        [101.0, 102.0],
        [1.0, 1.0],
    )
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, [snap])
        data = compute_chart_series(tmp, _IID, start_ns=_TS_NS - 1, end_ns=_TS_NS + 1)

    assert data["mid_imbalance"] == []
    assert data["imbalance"][0]["value"] == pytest.approx(3.0 / 5.0)


def test_compute_chart_series_skips_crossed_snapshot() -> None:
    """
    A crossed/touched snapshot (bid >= ask) is a normal, expected dYdX v4 condition during
    reconnect replay (DATA-04) -- must be dropped from every series, not fed through as a
    negative spread.
    """
    crossed = _snapshot(_TS_NS, [101.0], [1.0], [100.0], [1.0])
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, [crossed])
        data = compute_chart_series(tmp, _IID, start_ns=_TS_NS - 1, end_ns=_TS_NS + 1)

    assert data == {
        "microprice": [],
        "spread": [],
        "imbalance": [],
        "mid_imbalance": [],
        "bid_depth": [],
        "ask_depth": [],
    }


def test_compute_chart_series_output_is_chronological_across_write_batches() -> None:
    """
    Snapshots written in two separate out-of-order write_data() batches (write_data only
    enforces monotonic ts_init *within* one call, not across calls/files) must still come back
    from compute_chart_series in chronological order -- the chart page assumes each series is
    already time-ordered. compute_chart_series sorts by ts_event itself as a defensive guarantee
    of this, on top of whatever order the catalog read happens to return.
    """
    snap_b = _snapshot(_TS_NS + _STEP_NS, [200.0], [1.0], [201.0], [1.0])
    snap_a = _snapshot(_TS_NS, [100.0], [1.0], [101.0], [1.0])
    with tempfile.TemporaryDirectory() as tmp:
        _write(tmp, [snap_b])
        _write(tmp, [snap_a])
        data = compute_chart_series(tmp, _IID, start_ns=_TS_NS - 1, end_ns=_TS_NS + _STEP_NS + 1)

    assert [p["value"] for p in data["microprice"]] == [pytest.approx(100.5), pytest.approx(200.5)]
