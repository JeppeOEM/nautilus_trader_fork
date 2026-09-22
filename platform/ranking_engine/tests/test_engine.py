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
"""Unit tests for ranking_engine.engine -- the ranking core (Story 1.8)."""

import asyncio
import io
import json
import tempfile
import time

import pytest
from collector_core.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

import ranking_engine.engine as engine
import ranking_engine.metrics_store as metrics_store
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


def _reset_state() -> None:
    engine._LAST_SEEN.clear()
    engine._VOLUME_24H.clear()
    engine._VENUE_VOLUMES.clear()
    lookback = engine.RANKING_VOLATILITY_LOOKBACK_SECONDS
    engine._VOLATILITY = engine.VolatilityTracker(lookback_seconds=lookback)
    engine._ACTIVE_MODE = "volume"
    engine._OFI_INDS.clear()
    engine._OFI_RAW_INDS.clear()
    engine._OBI_INDS.clear()
    engine._LAST_FED.clear()
    engine._SECOND_ROLLING.clear()
    engine._SLOW_METRICS.clear()
    engine._PRICE_SERIES = engine.PriceSeriesStore()
    engine._BACKFILLED.clear()


def test_parse_volume_24h_extracts_usd_volume_per_market() -> None:
    markets_json = {
        "markets": {
            "BTC": {"ticker": "BTC-USD", "volume24H": "50000000"},
            "ETH": {"ticker": "ETH-USD", "volume24H": "10000000"},
        }
    }
    result = engine.parse_volume_24h(markets_json)
    assert result == {"BTC-USD-PERP.DYDX": 50000000.0, "ETH-USD-PERP.DYDX": 10000000.0}


def test_parse_volume_24h_missing_field_defaults_to_zero() -> None:
    markets_json = {"markets": {"X": {"ticker": "X-USD"}}}
    result = engine.parse_volume_24h(markets_json)
    assert result == {"X-USD-PERP.DYDX": 0.0}


def test_parse_volume_24h_skips_market_missing_ticker() -> None:
    markets_json = {"markets": {"X": {"volume24H": "100"}}}
    assert engine.parse_volume_24h(markets_json) == {}


def _mark_fresh(iid: str, now_ns: int) -> None:
    engine._LAST_SEEN[iid] = now_ns


def _feed_volatility(iid: str, prices: list[float]) -> None:
    for i, price in enumerate(prices):
        engine._VOLATILITY.update(iid, i * 1_000_000_000, price)


def _snap(
    iid: str,
    bid: float,
    ask: float,
    ts_event: int = 0,
    bid_size: float = 1.0,
    ask_size: float = 1.0,
    buy_volume: float = 0.0,
    sell_volume: float = 0.0,
    buy_count: int = 0,
    sell_count: int = 0,
    close_price: float | None = None,
) -> dict:
    return {
        "instrument_id": iid,
        "ts_event": ts_event,
        "bid_prices": [bid],
        "ask_prices": [ask],
        "bid_sizes": [bid_size],
        "ask_sizes": [ask_size],
        "buy_volume": buy_volume,
        "sell_volume": sell_volume,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "close_price": close_price,
    }


def test_ingest_snapshot_batch_skips_zero_mid_price_without_raising() -> None:
    """
    A zero mid-price (bid=ask=0.0) must not raise ZeroDivisionError inside
    VolatilityTracker.score() on a later call -- guarded at the ingest source (AD-2
    fail-closed: a zero/negative mid is not a legitimate market state).
    """
    _reset_state()
    iid = "ZERO-USD-PERP.DYDX"

    engine._ingest_snapshot_batch([_snap(iid, 0.0, 0.0)])  # must not raise

    assert engine._VOLATILITY.score(iid) is None  # nothing fed to the tracker


def test_ingest_snapshot_batch_skips_negative_mid_price_without_raising() -> None:
    _reset_state()
    iid = "NEG-USD-PERP.DYDX"

    engine._ingest_snapshot_batch([_snap(iid, -5.0, -1.0)])  # mid = -3.0, must not raise

    assert engine._VOLATILITY.score(iid) is None


def test_ingest_snapshot_batch_one_malformed_snap_does_not_drop_the_rest() -> None:
    """
    A single malformed snap (missing key) must not abort processing of every
    remaining snap in the same batch -- each snap is wrapped in its own try/except.
    """
    _reset_state()
    good_iid = "GOOD-USD-PERP.DYDX"
    batch = [
        {"instrument_id": "BAD-USD-PERP.DYDX"},  # missing bid_prices/ask_prices
        _snap(good_iid, 99.0, 101.0),
    ]

    engine._ingest_snapshot_batch(batch)  # must not raise

    assert good_iid in engine._LAST_SEEN


def test_ingest_snapshot_batch_feeds_close_price_into_price_series() -> None:
    """
    Two snaps with distinct close_price/ts_event must both land in _PRICE_SERIES,
    with stats() reflecting the latest one (Story 13.2 wiring).
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    engine._ingest_snapshot_batch(
        [_snap(iid, 99.0, 101.0, ts_event=1_000_000_000, close_price=100.0)],
    )
    engine._ingest_snapshot_batch(
        [_snap(iid, 100.0, 102.0, ts_event=2_000_000_000, close_price=101.0)],
    )

    stats = engine._PRICE_SERIES.stats(iid, now_ns=3_000_000_000)

    assert stats["price"] == 101.0


def test_ingest_snapshot_batch_drops_nonfinite_or_nonpositive_close_price() -> None:
    """
    A malformed close_price (NaN/inf/<=0) must not be recorded in _PRICE_SERIES --
    it would otherwise silently poison every downstream pct_change/volatility read
    with no error raised (DATA-02), unlike the mid<=0 guard for book state.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    for bad_price in (float("nan"), float("inf"), -1.0, 0.0):
        engine._ingest_snapshot_batch(
            [_snap(iid, 99.0, 101.0, ts_event=1_000_000_000, close_price=bad_price)],
        )

    stats = engine._PRICE_SERIES.stats(iid, now_ns=2_000_000_000)
    assert stats["price"] is None


def test_ingest_snapshot_batch_none_close_price_records_no_price_but_still_updates_ofi() -> None:
    """
    A snap with close_price=None (no trade that second) must not be recorded in
    _PRICE_SERIES, even though the rest of the snap (OFI/OBI) is ingested normally.
    """
    _reset_state()
    iid = "ETH-USD-PERP.DYDX"
    engine._ingest_snapshot_batch(
        [_snap(iid, 99.0, 101.0, ts_event=1_000_000_000, close_price=None)],
    )

    stats = engine._PRICE_SERIES.stats(iid, now_ns=2_000_000_000)

    assert stats["price"] is None
    assert engine._OFI_INDS.get(iid) is not None  # OFI tracker still fed


def test_current_ranks_volume_mode_sorts_by_descending_volume24h() -> None:
    _reset_state()
    now_ns = time.time_ns()
    engine._ACTIVE_MODE = "volume"
    for iid, vol in (("BTC-USD-PERP.DYDX", 50_000_000.0), ("SHIB-USD-PERP.DYDX", 100.0)):
        _mark_fresh(iid, now_ns)
        engine._VOLUME_24H[iid] = vol

    ranks = engine._current_ranks()

    assert [r["instrument_id"] for r in ranks] == ["BTC-USD-PERP.DYDX", "SHIB-USD-PERP.DYDX"]
    assert [r["rank"] for r in ranks] == [1, 2]
    assert {r["venue"] for r in ranks} == {"DYDX"}
    assert {r["venue_kind"] for r in ranks} == {"dex"}
    assert {r["market"] for r in ranks} == {"perp"}


def test_current_ranks_market_distinguishes_spot_from_linear() -> None:
    _reset_state()
    now_ns = time.time_ns()
    for iid in ("BTCUSDT-SPOT.BYBIT", "BTCUSDT-LINEAR.BYBIT"):
        _mark_fresh(iid, now_ns)
        engine._VOLUME_24H[iid] = 1.0  # volume mode ranks only rows with a known volume
    ranks = engine._current_ranks()
    assert {r["instrument_id"]: r["market"] for r in ranks} == {
        "BTCUSDT-SPOT.BYBIT": "spot",
        "BTCUSDT-LINEAR.BYBIT": "perp",
    }


def test_current_ranks_volatility_mode_sorts_by_descending_volatility_score() -> None:
    _reset_state()
    now_ns = time.time_ns()
    engine._ACTIVE_MODE = "volatility"
    for iid in ("CALM-USD-PERP.DYDX", "WILD-USD-PERP.DYDX"):
        _mark_fresh(iid, now_ns)
    _feed_volatility("CALM-USD-PERP.DYDX", [100.0, 100.1, 99.9, 100.05])
    _feed_volatility("WILD-USD-PERP.DYDX", [100.0, 120.0, 80.0, 130.0])

    ranks = engine._current_ranks()

    assert [r["instrument_id"] for r in ranks] == ["WILD-USD-PERP.DYDX", "CALM-USD-PERP.DYDX"]


def test_current_ranks_both_score_fields_present_in_both_modes() -> None:
    """AC1: volume24h and volatility_score must both be present regardless of active mode."""
    _reset_state()
    now_ns = time.time_ns()
    iid = "BTC-USD-PERP.DYDX"
    _mark_fresh(iid, now_ns)
    engine._VOLUME_24H[iid] = 12.0
    _feed_volatility(iid, [100.0, 101.0, 99.0])

    for mode in ("volume", "volatility"):
        engine._ACTIVE_MODE = mode
        row = engine._current_ranks()[0]
        assert "volume24h" in row
        assert "volatility_score" in row
        assert row["volume24h"] == 12.0
        assert row["volatility_score"] is not None


def test_current_ranks_excludes_stale_instrument() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _mark_fresh("BTC-USD-PERP.DYDX", now_ns)
    _mark_fresh("DEAD-USD-PERP.DYDX", now_ns - engine._WATCHLIST_STALE_NS - 1)
    engine._VOLUME_24H.update({"BTC-USD-PERP.DYDX": 1.0, "DEAD-USD-PERP.DYDX": 1.0})

    ranks = engine._current_ranks()

    assert [r["instrument_id"] for r in ranks] == ["BTC-USD-PERP.DYDX"]


def test_recently_stale_iids_includes_recently_gone_stale_instrument() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _mark_fresh("BTC-USD-PERP.DYDX", now_ns)
    _mark_fresh("SOL-USD-PERP.DYDX", now_ns - engine._WATCHLIST_STALE_NS - 1)

    assert engine._recently_stale_iids(now_ns) == ["SOL-USD-PERP.DYDX"]


def test_recently_stale_iids_excludes_long_dead_instrument() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _mark_fresh("DEAD-USD-PERP.DYDX", now_ns - engine._RECENTLY_STALE_WINDOW_NS - 1)

    assert engine._recently_stale_iids(now_ns) == []


def test_recently_stale_iids_excludes_currently_fresh_instrument() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _mark_fresh("BTC-USD-PERP.DYDX", now_ns)

    assert engine._recently_stale_iids(now_ns) == []


def test_build_rankings_message_carries_stale_instrument_ids() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _mark_fresh("BTC-USD-PERP.DYDX", now_ns)
    _mark_fresh("SOL-USD-PERP.DYDX", now_ns - engine._WATCHLIST_STALE_NS - 1)
    engine._VOLUME_24H["BTC-USD-PERP.DYDX"] = 1.0

    message = engine._build_rankings_message()

    assert message["stale_instrument_ids"] == ["SOL-USD-PERP.DYDX"]
    assert [r["instrument_id"] for r in message["ranks"]] == ["BTC-USD-PERP.DYDX"]


def test_handle_control_message_switches_active_mode() -> None:
    _reset_state()
    engine._handle_control_message({"mode": "volatility"})
    assert engine._ACTIVE_MODE == "volatility"


def test_handle_control_message_unrecognized_mode_is_ignored() -> None:
    _reset_state()
    engine._handle_control_message({"mode": "nonsense"})
    assert engine._ACTIVE_MODE == "volume"


class _FakeClock:
    """Injectable clock for the publish-discipline tests -- never a real asyncio.sleep."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now


def _rank_row(rank: int, iid: str = "BTC-USD-PERP.DYDX") -> dict:
    return {"instrument_id": iid, "rank": rank, "volume24h": 1.0, "volatility_score": None}


def test_publisher_republishes_on_heartbeat_even_with_no_rank_change() -> None:
    """
    A synthetic sequence with no change between two calls still publishes once the
    heartbeat interval elapses -- proves the heartbeat is independent of rank-change.
    """
    clock = _FakeClock()
    publisher = engine.RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time)
    ranks = [_rank_row(1)]

    assert publisher.should_publish(ranks, "volume") is True  # first call always publishes
    publisher.record_published(ranks, "volume")

    clock.now = 2.0  # within heartbeat window, nothing changed
    assert publisher.should_publish(ranks, "volume") is False

    clock.now = 5.0  # heartbeat interval elapsed, still nothing changed
    assert publisher.should_publish(ranks, "volume") is True


def test_publisher_republishes_immediately_on_rank_change_independent_of_heartbeat() -> None:
    clock = _FakeClock()
    publisher = engine.RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time)
    ranks_a, ranks_b = [_rank_row(1)], [_rank_row(2)]

    publisher.record_published(ranks_a, "volume")
    clock.now = 1.0  # well within heartbeat window, but the rank order changed

    assert publisher.should_publish(ranks_b, "volume") is True


def test_publisher_republishes_on_mode_change_even_with_identical_ranks() -> None:
    clock = _FakeClock()
    publisher = engine.RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time)
    ranks = [_rank_row(1)]

    publisher.record_published(ranks, "volume")
    clock.now = 1.0

    assert publisher.should_publish(ranks, "volatility") is True


class _FakeRedisClient:
    """
    Records publish() calls; yields control at the await point like a real client,
    so two concurrent _maybe_publish() calls actually interleave under asyncio.
    """

    def __init__(self) -> None:
        self.publish_calls = 0

    async def publish(self, channel: str, message: str) -> None:
        await asyncio.sleep(0)  # yield control -- the same interleaving window a real
        self.publish_calls += 1  # redis-asyncio await would create


def test_maybe_publish_lock_prevents_duplicate_publish_from_concurrent_callers() -> None:
    """
    _redis_listener (per-message) and _heartbeat_loop (every 1s) share one
    _PUBLISHER instance; should_publish/publish/record_published straddles an `await`,
    so without _PUBLISH_LOCK both could observe "should publish" before either records.
    Two _maybe_publish() calls run concurrently via asyncio.gather (deterministic under
    asyncio's single-threaded cooperative scheduling, not a real-thread race) must still
    yield exactly one publish.
    """
    _reset_state()
    engine._PUBLISHER = engine.RankingsPublisher(heartbeat_seconds=engine.RANKING_HEARTBEAT_SECONDS)
    _mark_fresh("BTC-USD-PERP.DYDX", time.time_ns())
    fake_redis = _FakeRedisClient()

    async def _run() -> None:
        await asyncio.gather(engine._maybe_publish(fake_redis), engine._maybe_publish(fake_redis))

    asyncio.run(_run())

    assert fake_redis.publish_calls == 1


def test_build_rankings_message_matches_wire_schema() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _mark_fresh("BTC-USD-PERP.DYDX", now_ns)
    engine._VOLUME_24H["BTC-USD-PERP.DYDX"] = 5.0

    message = engine._build_rankings_message()

    assert message["mode"] == "volume"
    assert isinstance(message["updated_at"], int)
    row = message["ranks"][0]
    # Only the pre-SSOT-migration fields checked for exact match here -- the full
    # live-tick field set (ofi_10_z/spread/cvd/etc, all None with no snapshot fed) is
    # covered by test_current_ranks_includes_live_tick_fields_from_ingested_snapshots
    # and test_current_ranks_falls_back_to_slow_metrics_price_pct_and_volatility below.
    assert row["instrument_id"] == "BTC-USD-PERP.DYDX"
    assert row["rank"] == 1
    assert row["volume24h"] == 5.0
    assert row["volatility_score"] is None


def test_current_ranks_includes_live_tick_fields_from_ingested_snapshots() -> None:
    """
    SSOT-02 migration: OFI/OBI/microprice/spread/cvd/price now come from
    ranking_engine's own snapshots:raw ingest (relocated from dashboard.py), not left
    None/absent -- two ingested ticks are enough for the z-scored/gap-aware trackers to
    report a real (non-None) value.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    engine._ingest_snapshot_batch(
        [
            _snap(
                iid,
                100.0,
                101.0,
                ts_event=0,
                buy_volume=3.0,
                sell_volume=1.0,
                buy_count=2,
                sell_count=1,
            )
        ],
    )
    engine._ingest_snapshot_batch(
        [
            _snap(
                iid,
                101.0,
                102.0,
                ts_event=1_000_000_000,
                buy_volume=2.0,
                sell_volume=0.0,
                buy_count=1,
                sell_count=0,
            )
        ],
    )
    _mark_fresh(iid, now_ns)
    engine._VOLUME_24H[iid] = 1.0

    row = engine._current_ranks()[0]

    assert row["ofi_10"] is not None  # second update_raw call, tracker is initialized
    assert row["obi_10"] == 0.5  # bid_size == ask_size == 1.0 on every ingested snap
    assert row["microprice"] is not None
    assert row["spread"] == 1.0  # last ingested snap: 102.0 - 101.0
    assert row["price"] == 101.5  # last ingested snap mid: (101.0 + 102.0) / 2
    assert row["cvd"] == 4.0  # (3+2) buy - (1+0) sell across both snapshots
    # 60s-windowed, not last-snap-only (both snaps fall inside the window here):
    # (3+2) buy - (1+0) sell across both snapshots, same as cvd.
    assert row["volume_delta"] == 4.0
    assert row["buy_count"] == 3  # summed across both snapshots: 2 + 1
    assert row["sell_count"] == 1  # summed across both snapshots: 1 + 0
    assert row["avg_trade_size"] == 1.5  # (5.0 buy + 1.0 sell) / (3 buy_cnt + 1 sell_cnt)


def test_current_ranks_falls_back_to_slow_metrics_price_pct_and_volatility() -> None:
    """
    pct_1h/pct_24h/volatility(catalog) have no live-tick equivalent -- always come
    from the cached _SLOW_METRICS snapshot _slow_loop_task populates; "price" falls
    back to it too, but only when no live snapshot has arrived yet for that instrument.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    _mark_fresh(iid, now_ns)
    engine._VOLUME_24H[iid] = 1.0
    engine._SLOW_METRICS[iid] = {
        "instrument_id": iid,
        "price": 99.0,
        "pct_1h": 0.01,
        "pct_24h": 0.05,
        "volatility": 0.002,
        "pct_1w": 3.5,
        "pct_1m": None,
        "ts": now_ns,
    }

    row = engine._current_ranks()[0]

    assert row["pct_1w"] == 3.5
    assert row["pct_1m"] is None  # not enough history yet -- never 0

    assert row["pct_1h"] == 0.01
    assert row["pct_24h"] == 0.05
    assert row["volatility"] == 0.002
    assert row["price"] == 99.0  # no live snapshot ingested -- falls back to slow cache


def test_current_ranks_drops_stale_slow_metrics() -> None:
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    _mark_fresh(iid, now_ns)
    engine._VOLUME_24H[iid] = 1.0
    engine._SLOW_METRICS[iid] = {
        "instrument_id": iid,
        "price": 99.0,
        "pct_1h": 0.01,
        "ts": now_ns - 10 * 60 * 1_000_000_000,
    }

    row = engine._current_ranks()[0]

    assert row["pct_1h"] is None
    assert row["price"] is None


def test_merge_rank_into_snapshots_attaches_rank_and_volume() -> None:
    snapshots = [{"instrument_id": "BTC-USD-PERP.DYDX", "price": 1.0}]
    ranks = {"BTC-USD-PERP.DYDX": 1}
    volumes = {"BTC-USD-PERP.DYDX": 50_000_000.0}

    merged = engine._merge_rank_into_snapshots(snapshots, ranks, volumes)

    assert merged[0]["rank"] == 1
    assert merged[0]["volume24h"] == 50_000_000.0
    assert merged[0]["price"] == 1.0  # original fields preserved


def test_merge_rank_into_snapshots_none_for_unranked_instrument() -> None:
    snapshots = [{"instrument_id": "DEAD-USD-PERP.DYDX", "price": 1.0}]

    merged = engine._merge_rank_into_snapshots(snapshots, ranks={}, volumes={})

    assert merged[0]["rank"] is None
    assert merged[0]["volume24h"] is None


def test_ranks_by_iid_reflects_current_ranks_1_indexed_shape() -> None:
    """
    Task 7: _current_ranks() now returns list[dict], not Story 1.4's dict[str, int] --
    the persist loop must build its own {iid: rank} dict from the new shape.
    """
    _reset_state()
    now_ns = time.time_ns()
    for iid, vol in (("BTC-USD-PERP.DYDX", 50_000_000.0), ("SHIB-USD-PERP.DYDX", 100.0)):
        _mark_fresh(iid, now_ns)
        engine._VOLUME_24H[iid] = vol

    assert engine._ranks_by_iid() == {"BTC-USD-PERP.DYDX": 1, "SHIB-USD-PERP.DYDX": 2}


def test_persist_snapshots_writes_rank_and_volume_from_current_ranks() -> None:
    """
    The relocated merge+write step persists a row carrying rank/volume24h sourced
    from _current_ranks()'s output -- reuses Story 1.4's coverage against the relocated
    function (metrics_store.write/nearest), now under ranking_engine.

    Note: the review fix for _slow_loop_task's cross-thread race (rank/merge computed
    on the main thread, only metrics_store.write pushed to asyncio.to_thread) has no
    dedicated regression test here -- reproducing a real cross-OS-thread race
    deterministically would require forcing a specific thread-interleaving timing,
    which is either impractical or flaky. The fix is a structural one (verified by
    reading _slow_loop_task's body: _ranks_by_iid()/_merge_rank_into_snapshots() are no
    longer inside the asyncio.to_thread call), not something a unit test can assert on.
    """
    _reset_state()
    db_path = tempfile.mktemp(suffix=".db")
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    _mark_fresh(iid, now_ns)
    engine._VOLUME_24H[iid] = 50_000_000.0

    engine._persist_snapshots([{"instrument_id": iid, "ts": now_ns, "price": 1.0}], db_path)

    row = metrics_store.nearest(iid, now_ns, db_path)
    assert row["rank"] == 1
    assert row["volume24h"] == 50_000_000.0


def test_legacy_book_metrics_for_reads_the_live_ofi5_microprice_spread() -> None:
    """
    SSOT-02 regression: metrics_store's historical ofi/microprice/spread columns must
    come from the exact same live tracker state rankings:live's ofi_5/microprice/spread
    fields come from (via _fast_metrics_for), never an independent recomputation that
    could diverge from it.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    engine._ingest_snapshot_batch(
        [_snap(iid, 100.0, 101.0, ts_event=0), _snap(iid, 100.0, 101.0, ts_event=1_000_000_000)]
    )

    legacy = engine._legacy_book_metrics_for(iid)
    fast = engine._fast_metrics_for(iid)

    assert legacy == {
        "ofi": fast["ofi_5"],
        "microprice": fast["microprice"],
        "spread": fast["spread"],
    }
    assert legacy["microprice"] is not None
    assert legacy["spread"] == 1.0


def test_legacy_book_metrics_for_unknown_instrument_is_all_none() -> None:
    _reset_state()
    assert engine._legacy_book_metrics_for("NEVER-SEEN-USD-PERP.DYDX") == {
        "ofi": None,
        "microprice": None,
        "spread": None,
    }


def _write_snapshot(catalog_path: str, iid: str, close_price: float, ts: int) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(iid),
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


def test_slow_loop_once_backfills_instrument_exactly_once_across_two_cycles() -> None:
    """
    Story 13.2: _slow_loop_once must backfill a newly-seen instrument's price
    series from Parquet on its first cycle (landing it in _BACKFILLED and populating
    _SLOW_METRICS), and run a second cycle cleanly with no error -- the instrument
    stays backfilled rather than being re-backfilled (engine._BACKFILLED gates the
    Parquet read in _backfill_new_instruments; this is the behavioral proxy for "no
    repeat Parquet read", since TEST-03 forbids mocking Nautilus internals to prove it
    more directly).
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    _mark_fresh(iid, now_ns)
    original_db_path = engine.METRICS_DB_PATH
    with tempfile.TemporaryDirectory() as catalog_dir:
        _write_snapshot(catalog_dir, iid, close_price=100.0, ts=now_ns - 1_000_000_000)
        engine.METRICS_DB_PATH = tempfile.mktemp(suffix=".db")
        try:
            asyncio.run(engine._slow_loop_once(catalog_dir))
            assert iid in engine._BACKFILLED
            assert iid in engine._SLOW_METRICS

            asyncio.run(engine._slow_loop_once(catalog_dir))  # second cycle, must not raise
            assert iid in engine._BACKFILLED
            assert iid in engine._SLOW_METRICS
        finally:
            engine.METRICS_DB_PATH = original_db_path


def test_backfill_new_instruments_reads_parquet_exactly_once_across_two_cycles(
    monkeypatch,
) -> None:
    """
    Direct counter-based proof (not just absence-of-error) that
    _read_price_series_sync is invoked exactly once per instrument, ever -- monkey-
    patching this module's own function is not "mocking Nautilus internals" (TEST-03
    only bans that), so this can assert the call count directly instead of inferring
    it from the previous test's "second cycle doesn't raise" proxy.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    _mark_fresh(iid, now_ns)
    calls = []
    original = engine._read_price_series_sync

    def _counting_read(catalog_path: str, iid: str, start_ns: int) -> list[tuple[int, float]]:
        calls.append(iid)
        return original(catalog_path, iid, start_ns)

    monkeypatch.setattr(engine, "_read_price_series_sync", _counting_read)
    with tempfile.TemporaryDirectory() as catalog_dir:
        _write_snapshot(catalog_dir, iid, close_price=100.0, ts=now_ns - 1_000_000_000)
        asyncio.run(engine._backfill_new_instruments(catalog_dir, now_ns))
        asyncio.run(engine._backfill_new_instruments(catalog_dir, now_ns))
        asyncio.run(engine._backfill_new_instruments(catalog_dir, now_ns))
    assert calls == [iid]


def test_backfill_new_instruments_marks_backfilled_even_on_failure(monkeypatch) -> None:
    """
    A backfill that raises (e.g. a corrupt catalog partition) must still land the
    instrument in _BACKFILLED -- otherwise it is retried every cycle forever,
    reintroducing the exact recurring-Parquet-read cost Story 13.2 removes.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    _mark_fresh(iid, now_ns)

    def _raising_read(catalog_path: str, iid: str, start_ns: int) -> list[tuple[int, float]]:
        raise RuntimeError("simulated corrupt catalog partition")

    monkeypatch.setattr(engine, "_read_price_series_sync", _raising_read)
    asyncio.run(engine._backfill_new_instruments("/nonexistent", now_ns))
    assert iid in engine._BACKFILLED


def test_pct_change_from_is_signed_percent_and_none_without_history() -> None:
    assert engine._pct_change_from(110.0, 100.0) == pytest.approx(10.0)
    assert engine._pct_change_from(90.0, 100.0) == pytest.approx(-10.0)
    assert engine._pct_change_from(110.0, None) is None
    assert engine._pct_change_from(None, 100.0) is None
    assert engine._pct_change_from(110.0, 0.0) is None


# --- Story 22.10: per-venue USD 24h volume -------------------------------------------------
# Fixtures below follow the shape of real responses captured 2026-09-21 (Bybit v5
# /market/tickers linear + spot, Hyperliquid /info metaAndAssetCtxs) -- same field names,
# string-typed numbers, and `[meta, ctxs]` pairing as the live APIs; trimmed to a few rows,
# and the numbers are illustrative, not the captured values.

_BYBIT_LINEAR_TICKERS = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "category": "linear",
        "list": [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "81850.10",
                "openInterest": "52034.1",
                "turnover24h": "3499130184.7322",
                "volume24h": "42880.8150",
                "fundingRate": "0.00005",
            },
            {
                "symbol": "0GUSDT",
                "lastPrice": "0.2266",
                "openInterest": "9529956.3",
                "turnover24h": "2114464.7013",
                "volume24h": "9653419.2000",
                "fundingRate": "0.00005",
            },
        ],
    },
}

_BYBIT_SPOT_TICKERS = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "category": "spot",
        "list": [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "81852.3",
                "turnover24h": "366657848.7248449",
                "volume24h": "4480.1",
                "usdIndexPrice": "81860.1",
            },
            {
                "symbol": "WLDUSDC",
                "lastPrice": "0.4459",
                "turnover24h": "224189.487161",
                "volume24h": "515315.85",
                "usdIndexPrice": "0.446893",
            },
            {
                "symbol": "ETHBTC",
                "lastPrice": "0.02651",
                "turnover24h": "12.3456",
                "volume24h": "465.7",
                "usdIndexPrice": "2170.4",
            },
        ],
    },
}

_HL_META_AND_CTXS = [
    {
        "universe": [
            {"szDecimals": 5, "name": "BTC", "maxLeverage": 40, "marginTableId": 56},
            {"szDecimals": 4, "name": "ETH", "maxLeverage": 25, "marginTableId": 55},
        ],
        "marginTables": [],
        "collateralToken": 0,
    },
    [
        {
            "funding": "0.0000125",
            "openInterest": "43265.9884999999",
            "prevDayPx": "80534.0",
            "dayNtlVlm": "1787695641.5723600388",
            "markPx": "81858.0",
            "dayBaseVlm": "22035.29",
        },
        {
            "funding": "0.0000100",
            "openInterest": "812345.1",
            "prevDayPx": "2150.0",
            "dayNtlVlm": "954321000.25",
            "markPx": "2170.4",
            "dayBaseVlm": "440000.1",
        },
    ],
]


def test_parse_bybit_volume_24h_linear_uses_turnover24h_usd() -> None:
    error_ledger.reset()

    result = engine.parse_bybit_volume_24h(_BYBIT_LINEAR_TICKERS, "linear")

    assert result == {"BTCUSDT-LINEAR.BYBIT": 3499130184.7322, "0GUSDT-LINEAR.BYBIT": 2114464.7013}
    assert error_ledger.counts() == {}


def test_parse_bybit_volume_24h_spot_keeps_only_usd_stablecoin_quotes() -> None:
    """
    ETHBTC's turnover24h is in BTC, not USD (OBS-03): left out, and not a ledger
    event at parse time -- only a *collected* one is (see the missing-volume ledger).
    """
    error_ledger.reset()

    result = engine.parse_bybit_volume_24h(_BYBIT_SPOT_TICKERS, "spot")

    assert result == {"BTCUSDT-SPOT.BYBIT": 366657848.7248449, "WLDUSDC-SPOT.BYBIT": 224189.487161}
    assert error_ledger.counts() == {}


def test_parse_bybit_volume_24h_linear_and_spot_ids_never_collide() -> None:
    linear = engine.parse_bybit_volume_24h(_BYBIT_LINEAR_TICKERS, "linear")
    spot = engine.parse_bybit_volume_24h(_BYBIT_SPOT_TICKERS, "spot")

    assert not set(linear) & set(spot)


@pytest.mark.parametrize("raw", ["", None, "abc", "-5", "nan", "inf", True])
def test_parse_bybit_volume_24h_unparseable_value_is_skipped_and_ledgered(raw: object) -> None:
    error_ledger.reset()
    tickers = {
        "retCode": 0,
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "turnover24h": raw},
                {"symbol": "ETHUSDT", "turnover24h": "10.5"},
            ]
        },
    }

    result = engine.parse_bybit_volume_24h(tickers, "linear")

    assert result == {"ETHUSDT-LINEAR.BYBIT": 10.5}  # never BTCUSDT at 0 (DATA-01)
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}


def test_parse_hyperliquid_volume_24h_pairs_universe_with_ctxs_by_index() -> None:
    error_ledger.reset()

    result = engine.parse_hyperliquid_volume_24h(_HL_META_AND_CTXS)

    assert result == {
        "BTC-USD-PERP.HYPERLIQUID": 1787695641.5723600388,
        "ETH-USD-PERP.HYPERLIQUID": 954321000.25,
    }
    assert error_ledger.counts() == {}


def test_parse_hyperliquid_volume_24h_null_value_is_skipped_and_ledgered() -> None:
    error_ledger.reset()
    payload = [_HL_META_AND_CTXS[0], [{"dayNtlVlm": None}, {"dayNtlVlm": "5.0"}]]

    result = engine.parse_hyperliquid_volume_24h(payload)

    assert result == {"ETH-USD-PERP.HYPERLIQUID": 5.0}
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {"universe": []},
        [],
        [{"universe": [{"name": "BTC"}]}, []],
        [{"universe": [{"name": "BTC"}]}, [{}], [{}]],
        [["not", "a", "dict"], [{}]],
    ],
)
def test_parse_hyperliquid_volume_24h_bad_shape_raises(payload: object) -> None:
    with pytest.raises(ValueError, match="metaAndAssetCtxs"):
        engine.parse_hyperliquid_volume_24h(payload)


def test_volume_sources_rejects_unknown_environment() -> None:
    with pytest.raises(ValueError, match="BYBIT_ENVIRONMENT"):
        engine._volume_sources(engine.DYDX_NETWORK, "demo", "mainnet")
    with pytest.raises(ValueError, match="HYPERLIQUID_ENVIRONMENT"):
        engine._volume_sources(engine.DYDX_NETWORK, "mainnet", "devnet")


def test_volume_sources_polls_every_venue_and_market() -> None:
    sources = engine._volume_sources(engine.DYDX_NETWORK, "mainnet", "testnet")

    assert set(sources) == {"dydx", "bybit-linear", "bybit-spot", "hyperliquid"}


def _fetcher(volumes: dict[str, float]) -> engine.VolumeFetcher:
    async def _fetch() -> dict[str, float]:
        return dict(volumes)

    return _fetch


def _failing_fetcher() -> engine.VolumeFetcher:
    async def _fetch() -> dict[str, float]:
        raise OSError("simulated venue outage")

    return _fetch


def _three_venue_volumes() -> dict[str, dict[str, float]]:
    return {
        "dydx": {"BTC-USD-PERP.DYDX": 3_000_000.0},
        "bybit-linear": engine.parse_bybit_volume_24h(_BYBIT_LINEAR_TICKERS, "linear"),
        "bybit-spot": engine.parse_bybit_volume_24h(_BYBIT_SPOT_TICKERS, "spot"),
        "hyperliquid": engine.parse_hyperliquid_volume_24h(_HL_META_AND_CTXS),
    }


def test_three_venue_volume_cycle_ranks_every_venue_by_its_own_usd_volume() -> None:
    _reset_state()
    error_ledger.reset()
    now_ns = time.time_ns()
    iids = [
        "BTC-USD-PERP.DYDX",
        "BTCUSDT-LINEAR.BYBIT",
        "BTCUSDT-SPOT.BYBIT",
        "BTC-USD-PERP.HYPERLIQUID",
    ]
    for iid in iids:
        _mark_fresh(iid, now_ns)
    sources = {name: _fetcher(vols) for name, vols in _three_venue_volumes().items()}

    asyncio.run(engine._volume_cycle(sources))
    ranks = engine._current_ranks()

    assert [(r["instrument_id"], r["venue"], r["market"]) for r in ranks] == [
        ("BTCUSDT-LINEAR.BYBIT", "BYBIT", "perp"),
        ("BTC-USD-PERP.HYPERLIQUID", "HYPERLIQUID", "perp"),
        ("BTCUSDT-SPOT.BYBIT", "BYBIT", "spot"),
        ("BTC-USD-PERP.DYDX", "DYDX", "perp"),
    ]
    assert [r["rank"] for r in ranks] == [1, 2, 3, 4]
    assert error_ledger.counts() == {}


def test_current_ranks_missing_volume_left_out_of_volume_mode_kept_as_none_in_volatility() -> None:
    _reset_state()
    now_ns = time.time_ns()
    _mark_fresh("BTC-USD-PERP.DYDX", now_ns)
    _mark_fresh("BTC-USD-PERP.HYPERLIQUID", now_ns)
    engine._VOLUME_24H["BTC-USD-PERP.DYDX"] = 3_000_000.0

    engine._ACTIVE_MODE = "volume"
    assert [r["instrument_id"] for r in engine._current_ranks()] == ["BTC-USD-PERP.DYDX"]

    engine._ACTIVE_MODE = "volatility"
    rows = {r["instrument_id"]: r for r in engine._current_ranks()}
    assert set(rows) == {"BTC-USD-PERP.DYDX", "BTC-USD-PERP.HYPERLIQUID"}
    assert rows["BTC-USD-PERP.HYPERLIQUID"]["volume24h"] is None  # never a fabricated 0


def test_volume_cycle_one_venue_failing_keeps_its_last_good_values_and_updates_the_rest() -> None:
    _reset_state()
    error_ledger.reset()
    asyncio.run(
        engine._volume_cycle(
            {
                "dydx": _fetcher({"BTC-USD-PERP.DYDX": 1.0}),
                "bybit-linear": _fetcher({"BTCUSDT-LINEAR.BYBIT": 2.0}),
            }
        )
    )

    asyncio.run(
        engine._volume_cycle(
            {
                "dydx": _fetcher({"BTC-USD-PERP.DYDX": 10.0}),
                "bybit-linear": _failing_fetcher(),
            }
        )
    )

    assert engine._VOLUME_24H == {"BTC-USD-PERP.DYDX": 10.0, "BTCUSDT-LINEAR.BYBIT": 2.0}
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}
    assert error_ledger.last_details()["ranking_engine.volume24h"].startswith("bybit-linear:")


def test_volume_cycle_successful_poll_replaces_a_source_whole_so_delisted_coins_drop_out() -> None:
    _reset_state()
    asyncio.run(engine._volume_cycle({"hyperliquid": _fetcher({"A-USD-PERP.HYPERLIQUID": 1.0})}))

    asyncio.run(engine._volume_cycle({"hyperliquid": _fetcher({"B-USD-PERP.HYPERLIQUID": 2.0})}))

    assert engine._VOLUME_24H == {"B-USD-PERP.HYPERLIQUID": 2.0}


def test_rebuild_volume_24h_expires_a_source_past_max_age_with_a_ledger_entry() -> None:
    _reset_state()
    error_ledger.reset()
    now_ns = time.time_ns()
    engine._VENUE_VOLUMES["dydx"] = (now_ns, {"BTC-USD-PERP.DYDX": 1.0})
    engine._VENUE_VOLUMES["bybit-spot"] = (
        now_ns - engine._VOLUME_MAX_AGE_NS - 1,
        {"BTCUSDT-SPOT.BYBIT": 2.0},
    )

    engine._rebuild_volume_24h(now_ns)

    assert engine._VOLUME_24H == {"BTC-USD-PERP.DYDX": 1.0}
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}
    assert error_ledger.last_details()["ranking_engine.volume24h"].startswith("bybit-spot:")


def test_volume_max_age_is_three_poll_intervals_in_ns() -> None:
    assert engine._VOLUME_MAX_AGE_NS == 3 * engine.VOLUME_POLL_SECONDS * 1_000_000_000
    assert isinstance(engine._VOLUME_MAX_AGE_NS, int)


def test_volume_cycle_ledgers_each_fresh_iid_without_volume_once_per_cycle() -> None:
    _reset_state()
    error_ledger.reset()
    now_ns = time.time_ns()
    _mark_fresh("BTC-USD-PERP.DYDX", now_ns)
    _mark_fresh("ETHBTC-SPOT.BYBIT", now_ns)  # collected, but no USD volume exists for it
    _mark_fresh("SOL-USD-PERP.HYPERLIQUID", now_ns)  # venue lists no such coin
    _mark_fresh("DEAD-USD-PERP.DYDX", now_ns - engine._WATCHLIST_STALE_NS - 1)  # stale: not counted
    sources = {
        "dydx": _fetcher({"BTC-USD-PERP.DYDX": 1.0}),
        "hyperliquid": _fetcher({"BTC-USD-PERP.HYPERLIQUID": 5.0}),
    }

    asyncio.run(engine._volume_cycle(sources))
    assert error_ledger.counts() == {"ranking_engine.volume24h": 2}

    asyncio.run(engine._volume_cycle(sources))
    assert error_ledger.counts() == {"ranking_engine.volume24h": 4}


@pytest.mark.parametrize(
    "tickers",
    [
        {"retCode": 10006, "retMsg": "Too many visits!", "result": {}},
        {"retCode": 0, "result": {}},
        {},
    ],
)
def test_parse_bybit_volume_24h_error_response_raises_instead_of_parsing_empty(
    tickers: dict,
) -> None:
    with pytest.raises(ValueError, match="retCode"):
        engine.parse_bybit_volume_24h(tickers, "linear")


def test_volume_cycle_empty_poll_is_a_failure_that_keeps_the_last_good_values() -> None:
    _reset_state()
    error_ledger.reset()
    asyncio.run(engine._volume_cycle({"bybit-linear": _fetcher({"BTCUSDT-LINEAR.BYBIT": 2.0})}))

    asyncio.run(engine._volume_cycle({"bybit-linear": _fetcher({})}))

    assert engine._VOLUME_24H == {"BTCUSDT-LINEAR.BYBIT": 2.0}
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}


def test_volume_cycle_a_hung_source_times_out_without_stalling_the_others(monkeypatch) -> None:
    _reset_state()
    error_ledger.reset()
    monkeypatch.setattr(engine, "_VOLUME_FETCH_TIMEOUT_SECONDS", 0.05)

    async def _hung() -> dict[str, float]:
        await asyncio.sleep(10)
        return {}

    asyncio.run(
        engine._volume_cycle({"dydx": _fetcher({"BTC-USD-PERP.DYDX": 1.0}), "hyperliquid": _hung})
    )

    assert engine._VOLUME_24H == {"BTC-USD-PERP.DYDX": 1.0}
    assert error_ledger.last_details()["ranking_engine.volume24h"].startswith("hyperliquid:")


def _json_response(payload: object) -> io.BytesIO:
    """Stand-in for urlopen's response: a context manager json.load can read."""
    return io.BytesIO(json.dumps(payload).encode())


def test_hyperliquid_fetch_posts_meta_and_asset_ctxs_as_json(monkeypatch) -> None:
    requests: list = []

    def _urlopen(request, timeout: float) -> io.BytesIO:
        requests.append(request)
        return _json_response(_HL_META_AND_CTXS)

    monkeypatch.setattr(engine.urllib.request, "urlopen", _urlopen)

    payload = engine._fetch_hyperliquid_meta_and_ctxs_json("mainnet")

    request = requests[0]
    assert payload == _HL_META_AND_CTXS
    assert request.full_url == "https://api.hyperliquid.xyz/info"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"type": "metaAndAssetCtxs"}
    assert request.get_header("Content-type") == "application/json"


def test_bybit_fetch_requests_the_category_tickers(monkeypatch) -> None:
    requests: list = []

    def _urlopen(request, timeout: float) -> io.BytesIO:
        requests.append(request)
        return _json_response(_BYBIT_SPOT_TICKERS)

    monkeypatch.setattr(engine.urllib.request, "urlopen", _urlopen)

    engine._fetch_bybit_tickers_json("testnet", "spot")

    assert requests[0].full_url == "https://api-testnet.bybit.com/v5/market/tickers?category=spot"


def test_freshness_is_stamped_on_receipt_not_on_the_snapshots_exchange_time() -> None:
    """
    Story 22.12: a venue-timed row reaches Redis `1 + hold_back_seconds` after its
    `ts_event`; freshness must not age it by that lag. Here `ts_event` is even older than the
    whole stale bound, so only receipt-time stamping keeps the instrument fresh.
    """
    _reset_state()
    iid = "BTC-USD-PERP.HYPERLIQUID"
    now_ns = time.time_ns()
    old_event_ns = now_ns - engine._WATCHLIST_STALE_NS - 5_000_000_000

    engine._ingest_snapshot_batch([_snap(iid, 99.0, 101.0, ts_event=old_event_ns)])

    assert engine._is_fresh(iid, time.time_ns())
