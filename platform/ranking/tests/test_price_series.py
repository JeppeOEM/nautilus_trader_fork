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
"""Unit tests for ranking.domain.price_series (Story 13.2; moved in Story 25.2)."""

import tempfile

import pytest
from kernel.tests.snapshot_factory import make_snapshot

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from ranking.domain.metrics import price_stats_from_series
from ranking.domain.price_series import PriceSeriesStore
from ranking.domain.price_series import _RingBuffer
from ranking.infrastructure.catalog_prices import CatalogPriceHistory


_IID = "BTC-USD-PERP.DYDX"
_1H_NS = 3_600 * 1_000_000_000


def _write_snapshot(catalog_path: str, close_price: float, ts: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            make_snapshot(
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
    expected = price_stats_from_series(retained)
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
    assert store.ingest(_IID, 10_000_000_000, 200.0) is None
    dropped = [
        store.ingest(_IID, 9_000_000_000, 999.0),  # out of order -- must be dropped
        store.ingest(_IID, 10_000_000_000, 999.0),  # duplicate ts -- must be dropped
    ]
    assert store.ingest(_IID, 11_000_000_000, 201.0) is None  # back in order -- must be kept

    assert all(d is not None and "out-of-order" in d for d in dropped)  # returned to be ledgered

    result = store.stats(_IID, now_ns=11_000_000_000)
    expected = price_stats_from_series([(10_000_000_000, 200.0), (11_000_000_000, 201.0)])
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
    assert result == price_stats_from_series(expected_series)


def test_backfill_on_empty_buffer_seeds_directly_from_series() -> None:
    store = PriceSeriesStore()
    series = [(1_000_000_000, 10.0), (2_000_000_000, 11.0)]

    details = store.backfill(_IID, series)

    assert details == []  # a clean series: nothing dropped, nothing to ledger
    result = store.stats(_IID, now_ns=2_000_000_000)
    assert result == price_stats_from_series(series)


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


def test_backfilled_in_memory_stats_match_the_formula_over_the_catalog_series() -> None:
    """
    Writes real DydxSecondSnapshot rows spanning >24h to a temp ParquetDataCatalog, then proves
    the formula over the catalog's own series and the PriceSeriesStore.backfill()+.stats() path
    produce numerically identical output -- SSOT-02/DATA-02 compliance for Story 13.2.

    The span exceeds 24h (so pct_change_24h computes) but stays under the default 25h lookback, so
    the ring buffer's cutoff retains exactly what the catalog read returned.
    """
    t0 = 0
    t1 = 1 * _1H_NS
    t2 = 24 * _1H_NS + _1H_NS // 2  # 24.5h
    with tempfile.TemporaryDirectory() as catalog_dir:
        _write_snapshot(catalog_dir, close_price=100.0, ts=t0)
        _write_snapshot(catalog_dir, close_price=110.0, ts=t1)
        _write_snapshot(catalog_dir, close_price=130.0, ts=t2)

        series = CatalogPriceHistory(catalog_dir).series(_IID, start_ns=0)
        store = PriceSeriesStore()
        store.backfill(_IID, series)
        new_path = store.stats(_IID, now_ns=t2)

    assert series == [(t0, 100.0), (t1, 110.0), (t2, 130.0)]
    assert new_path == price_stats_from_series(series)


def test_backfill_returns_a_parquet_live_disagreement_at_the_same_ts() -> None:
    store = PriceSeriesStore()
    store.ingest("X", 2_000_000_000, 10.0)

    details = store.backfill("X", [(1_000_000_000, 5.0), (2_000_000_000, 11.0)])

    assert len(details) == 1
    assert "1 live/Parquet price mismatches" in details[0]
    assert store.backfill("Y", [(1_000_000_000, 5.0)]) == []


# ---- backfill validation (DW-218): sorted, bad prices and duplicate ts dropped and reported ----


def test_backfill_sorts_an_unsorted_series_without_reporting_anything() -> None:
    store = PriceSeriesStore()
    series = [(3_000_000_000, 12.0), (1_000_000_000, 10.0), (2_000_000_000, 11.0)]

    details = store.backfill(_IID, series)

    assert details == []  # an order the adapter did not guarantee is not a drop
    assert store.stats(_IID, now_ns=3_000_000_000) == price_stats_from_series(sorted(series))


@pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0])
def test_backfill_drops_a_nonfinite_or_nonpositive_price_and_reports_it(bad_price: float) -> None:
    store = PriceSeriesStore()
    good = [(1_000_000_000, 10.0), (2_000_000_000, 11.0), (4_000_000_000, 12.0)]

    details = store.backfill(_IID, [*good[:2], (3_000_000_000, bad_price), good[2]])

    assert len(details) == 1
    assert "1 non-finite/non-positive catalog prices DROPPED" in details[0]
    assert "first ts=3000000000" in details[0]
    assert store.stats(_IID, now_ns=4_000_000_000) == price_stats_from_series(good)


def test_backfill_drops_every_bad_price_in_one_detail_naming_the_first() -> None:
    store = PriceSeriesStore()
    series = [
        (1_000_000_000, 10.0),
        (5_000_000_000, -2.0),
        (2_000_000_000, float("nan")),
        (3_000_000_000, 11.0),
    ]

    details = store.backfill(_IID, series)

    assert len(details) == 1  # one detail per drop kind, not per point
    assert "2 non-finite/non-positive catalog prices DROPPED" in details[0]
    assert "first ts=2000000000" in details[0]  # first in time, after the sort


def test_backfill_drops_a_duplicate_ts_keeping_the_first_and_reports_it() -> None:
    store = PriceSeriesStore()
    series = [
        (1_000_000_000, 10.0),
        (2_000_000_000, 11.0),
        (2_000_000_000, 11.0),
        (3_000_000_000, 12.0),
        (3_000_000_000, 99.0),  # disagrees: the first in catalog order is kept
    ]

    details = store.backfill(_IID, series)

    assert len(details) == 1
    assert "2 duplicate-ts catalog prices DROPPED" in details[0]
    assert "(1 with a different price)" in details[0]
    assert "first ts=2000000000" in details[0]
    expected = [(1_000_000_000, 10.0), (2_000_000_000, 11.0), (3_000_000_000, 12.0)]
    assert store.stats(_IID, now_ns=3_000_000_000) == price_stats_from_series(expected)


def test_backfill_drops_a_bad_price_before_deduplicating_so_it_never_shadows_a_good_one() -> None:
    store = PriceSeriesStore()
    series = [(1_000_000_000, 10.0), (2_000_000_000, float("nan")), (2_000_000_000, 11.0)]

    details = store.backfill(_IID, series)

    assert len(details) == 1  # the NaN only: the good point at its ts is no duplicate
    assert "non-finite/non-positive" in details[0]
    expected = [(1_000_000_000, 10.0), (2_000_000_000, 11.0)]
    assert store.stats(_IID, now_ns=2_000_000_000) == price_stats_from_series(expected)


def test_backfill_reports_each_drop_kind_and_still_merges_with_live_points() -> None:
    store = PriceSeriesStore()
    store.ingest(_IID, 10_000_000_000, 200.0)
    series = [
        (8_000_000_000, 175.0),
        (5_000_000_000, 150.0),
        (6_000_000_000, 0.0),
        (8_000_000_000, 175.0),
    ]

    details = store.backfill(_IID, series)

    assert len(details) == 2
    assert "1 non-finite/non-positive catalog prices DROPPED" in details[0]
    assert "1 duplicate-ts catalog prices DROPPED" in details[1]
    assert "(0 with a different price)" in details[1]
    expected = [(5_000_000_000, 150.0), (8_000_000_000, 175.0), (10_000_000_000, 200.0)]
    assert store.stats(_IID, now_ns=10_000_000_000) == price_stats_from_series(expected)


def test_backfill_of_only_bad_points_seeds_nothing_and_reports_them() -> None:
    store = PriceSeriesStore()

    details = store.backfill(_IID, [(1_000_000_000, float("nan")), (2_000_000_000, -1.0)])

    assert len(details) == 1
    assert store.stats(_IID, now_ns=2_000_000_000) == price_stats_from_series([])


def test_drop_releases_an_instrument_so_a_return_starts_empty() -> None:
    store = PriceSeriesStore()
    store.ingest(_IID, 1_000_000_000, 10.0)

    store.drop(_IID)
    store.drop("NEVER-SEEN")  # dropping an unknown instrument is a no-op

    assert store.stats(_IID, now_ns=2_000_000_000)["price"] is None
