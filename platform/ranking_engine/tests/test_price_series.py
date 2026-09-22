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
"""Unit tests for ranking_engine.price_series (Story 13.2)."""

import tempfile

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from ml_signals import catalog_stats

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from ranking_engine.price_series import PriceSeriesStore
from ranking_engine.price_series import _RingBuffer


_IID = "BTC-USD-PERP.DYDX"
_1H_NS = 3_600 * 1_000_000_000


def _write_snapshot(catalog_path: str, close_price: float, ts: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(_IID),
                bid_prices=[close_price - 1],
                bid_sizes=[1.0],
                ask_prices=[close_price + 1],
                ask_sizes=[1.0],
                buy_volume=1.0,
                sell_volume=0.0,
                buy_count=1,
                sell_count=0,
                open_price=close_price,
                high_price=close_price,
                low_price=close_price,
                close_price=close_price,
                ts_event=ts,
                ts_init=ts,
            )
        ]
    )


# ---- ring buffer append/evict/wrap ----


def test_ring_buffer_wraparound_evicts_oldest_points_but_stats_stay_correct() -> None:
    """
    Capacity 3: ingesting 5 points must overwrite the 2 oldest, leaving only the
    last 3 in stats() -- proven by comparing against the formula run on the expected
    retained series directly, through the public PriceSeriesStore surface.
    """
    store = PriceSeriesStore(lookback_hours=3 / 3600)  # capacity = int(3/3600 * 3600) = 3
    prices = [100.0, 101.0, 102.0, 103.0, 104.0]
    for i, price in enumerate(prices):
        store.ingest(_IID, (i + 1) * 1_000_000_000, price)

    now_ns = 5_000_000_000
    result = store.stats(_IID, now_ns)

    retained = [(3_000_000_000, 102.0), (4_000_000_000, 103.0), (5_000_000_000, 104.0)]
    expected = catalog_stats.price_stats_from_series(retained)
    assert result == expected
    assert result["price"] == 104.0  # latest of the retained window, not the evicted 100.0


def test_ring_buffer_rejects_nonpositive_capacity() -> None:
    """
    Capacity is derived from lookback_hours (int(lookback_hours * 3600)) --
    a misconfigured/typo'd value collapsing to <= 0 must fail loudly at construction,
    not divide-by-zero deep inside append()'s cursor wraparound.
    """
    with pytest.raises(ValueError, match="positive"):
        _RingBuffer(0)


def test_ingest_drops_out_of_order_point_without_corrupting_buffer() -> None:
    """
    A point at or before the buffer's last-appended ts (Redis redelivery, a race)
    must be dropped, not appended -- ascending()'s sorted-order assumption (and every
    stat derived via searchsorted/consecutive-diff over it) would otherwise silently
    go wrong with no error raised (DATA-02).
    """
    store = PriceSeriesStore()
    store.ingest(_IID, 10_000_000_000, 200.0)
    store.ingest(_IID, 9_000_000_000, 999.0)  # out of order -- must be dropped
    store.ingest(_IID, 10_000_000_000, 999.0)  # duplicate ts -- must be dropped
    store.ingest(_IID, 11_000_000_000, 201.0)  # back in order -- must be kept

    result = store.stats(_IID, now_ns=11_000_000_000)
    expected = catalog_stats.price_stats_from_series(
        [(10_000_000_000, 200.0), (11_000_000_000, 201.0)]
    )
    assert result == expected


# ---- backfill/live-ingest merge ----


def test_backfill_merges_older_history_without_duplicating_or_clobbering_live_points() -> None:
    """
    Live points already ingested must survive verbatim; only strictly-older
    historical points are prepended -- a historical point at or after the earliest
    live timestamp is dropped (would-be duplicate/overlap), per the Design Notes.
    """
    store = PriceSeriesStore()
    store.ingest(_IID, 10_000_000_000, 200.0)
    store.ingest(_IID, 11_000_000_000, 201.0)

    historical = [
        (5_000_000_000, 150.0),
        (8_000_000_000, 175.0),
        (10_000_000_000, 199.0),  # same ts as earliest live point -- must be dropped
    ]
    store.backfill(_IID, historical)

    result = store.stats(_IID, now_ns=12_000_000_000)
    expected_series = [
        (5_000_000_000, 150.0),
        (8_000_000_000, 175.0),
        (10_000_000_000, 200.0),
        (11_000_000_000, 201.0),
    ]
    assert result == catalog_stats.price_stats_from_series(expected_series)


def test_backfill_on_empty_buffer_seeds_directly_from_series() -> None:
    store = PriceSeriesStore()
    series = [(1_000_000_000, 10.0), (2_000_000_000, 11.0)]

    store.backfill(_IID, series)

    result = store.stats(_IID, now_ns=2_000_000_000)
    assert result == catalog_stats.price_stats_from_series(series)


# ---- fresh instrument, no data at all ----


def test_stats_for_unseeded_instrument_returns_all_none() -> None:
    store = PriceSeriesStore()

    result = store.stats("NEVER-SEEN-USD-PERP.DYDX", now_ns=1_000_000_000)

    assert result == {
        "price": None,
        "pct_change_1h": None,
        "pct_change_24h": None,
        "volatility": None,
    }


# ---- catalog-vs-in-memory parity (DATA-02 standard of proof) ----


def test_backfilled_in_memory_stats_match_catalog_backed_price_stats() -> None:
    """
    The most important test in this file: writes real DydxSecondSnapshot rows
    spanning >24h to a temp ParquetDataCatalog, then proves the old catalog-backed
    price_stats() path and the new PriceSeriesStore.backfill()+.stats() path produce
    numerically identical output -- SSOT-02/DATA-02 compliance for Story 13.2.
    """
    # Span must exceed 24h (so pct_change_24h computes) but stay under
    # PriceSeriesStore's default 25h lookback window (so its ring-buffer cutoff
    # doesn't truncate history the catalog-backed, unbounded price_stats() call
    # still sees) -- otherwise the two paths would legitimately disagree on the
    # retained window, not on the shared formula this test is meant to verify.
    t0 = 0
    t1 = 1 * _1H_NS
    t2 = 24 * _1H_NS + _1H_NS // 2  # 24.5h
    with tempfile.TemporaryDirectory() as catalog_dir:
        _write_snapshot(catalog_dir, close_price=100.0, ts=t0)
        _write_snapshot(catalog_dir, close_price=110.0, ts=t1)
        _write_snapshot(catalog_dir, close_price=130.0, ts=t2)

        catalog = ParquetDataCatalog(catalog_dir)
        old_path = catalog_stats.price_stats(catalog, _IID)

        series = catalog_stats.price_series(catalog, _IID)
        store = PriceSeriesStore()
        store.backfill(_IID, series)
        new_path = store.stats(_IID, now_ns=t2)

    assert old_path == new_path


def test_backfill_warns_when_parquet_disagrees_with_live_at_same_ts(caplog) -> None:  # type: ignore[no-untyped-def]
    store = PriceSeriesStore()
    store.ingest("X", 2_000_000_000, 10.0)
    store.backfill("X", [(1_000_000_000, 5.0), (2_000_000_000, 11.0)])

    assert "mismatches" in caplog.text
