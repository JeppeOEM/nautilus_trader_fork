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
import tempfile
import time

import ranking_engine.engine as engine
import ranking_engine.metrics_store as metrics_store


def _reset_state() -> None:
    engine._LAST_SEEN.clear()
    engine._VOLUME_24H.clear()
    lookback = engine.RANKING_VOLATILITY_LOOKBACK_SECONDS
    engine._VOLATILITY = engine.VolatilityTracker(lookback_seconds=lookback)
    engine._ACTIVE_MODE = "volume"
    engine._OFI_INDS.clear()
    engine._OFI_RAW_INDS.clear()
    engine._OBI_INDS.clear()
    engine._LAST_FED.clear()
    engine._SECOND_ROLLING.clear()
    engine._SLOW_METRICS.clear()


def test_parse_volume_24h_extracts_usd_volume_per_market() -> None:
    markets_json = {"markets": {
        "BTC": {"ticker": "BTC-USD", "volume24H": "50000000"},
        "ETH": {"ticker": "ETH-USD", "volume24H": "10000000"},
    }}
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
) -> dict:
    return {
        "instrument_id": iid, "ts_event": ts_event,
        "bid_prices": [bid], "ask_prices": [ask],
        "bid_sizes": [bid_size], "ask_sizes": [ask_size],
        "buy_volume": buy_volume, "sell_volume": sell_volume,
        "buy_count": buy_count, "sell_count": sell_count,
    }


def test_ingest_snapshot_batch_skips_zero_mid_price_without_raising() -> None:
    """A zero mid-price (bid=ask=0.0) must not raise ZeroDivisionError inside
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
    """A single malformed snap (missing key) must not abort processing of every
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
    """A synthetic sequence with no change between two calls still publishes once the
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
    """Records publish() calls; yields control at the await point like a real client,
    so two concurrent _maybe_publish() calls actually interleave under asyncio.
    """

    def __init__(self) -> None:
        self.publish_calls = 0

    async def publish(self, channel: str, message: str) -> None:
        await asyncio.sleep(0)  # yield control -- the same interleaving window a real
        self.publish_calls += 1  # redis-asyncio await would create


def test_maybe_publish_lock_prevents_duplicate_publish_from_concurrent_callers() -> None:
    """_redis_listener (per-message) and _heartbeat_loop (every 1s) share one
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
    """SSOT-02 migration: OFI/OBI/microprice/spread/cvd/price now come from
    ranking_engine's own snapshots:raw ingest (relocated from dashboard.py), not left
    None/absent -- two ingested ticks are enough for the z-scored/gap-aware trackers to
    report a real (non-None) value.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    engine._ingest_snapshot_batch(
        [_snap(iid, 100.0, 101.0, ts_event=0, buy_volume=3.0, sell_volume=1.0, buy_count=2, sell_count=1)],
    )
    engine._ingest_snapshot_batch(
        [_snap(iid, 101.0, 102.0, ts_event=1_000_000_000, buy_volume=2.0, sell_volume=0.0, buy_count=1, sell_count=0)],
    )
    _mark_fresh(iid, now_ns)

    row = engine._current_ranks()[0]

    assert row["ofi_10"] is not None  # second update_raw call, tracker is initialized
    assert row["obi_10"] == 0.5  # bid_size == ask_size == 1.0 on every ingested snap
    assert row["microprice"] is not None
    assert row["spread"] == 1.0  # last ingested snap: 102.0 - 101.0
    assert row["price"] == 101.5  # last ingested snap mid: (101.0 + 102.0) / 2
    assert row["cvd"] == 4.0  # (3+2) buy - (1+0) sell across both snapshots
    assert row["volume_delta"] == 2.0  # last snap only: 2.0 - 0.0
    assert row["buy_count"] == 3  # summed across both snapshots: 2 + 1
    assert row["sell_count"] == 1  # summed across both snapshots: 1 + 0
    assert row["avg_trade_size"] == 1.5  # (5.0 buy + 1.0 sell) / (3 buy_cnt + 1 sell_cnt)


def test_current_ranks_falls_back_to_slow_metrics_price_pct_and_volatility() -> None:
    """pct_1h/pct_24h/volatility(catalog) have no live-tick equivalent -- always come
    from the cached _SLOW_METRICS snapshot _slow_loop_task populates; "price" falls
    back to it too, but only when no live snapshot has arrived yet for that instrument.
    """
    _reset_state()
    iid = "BTC-USD-PERP.DYDX"
    now_ns = time.time_ns()
    _mark_fresh(iid, now_ns)
    engine._SLOW_METRICS[iid] = {
        "instrument_id": iid, "price": 99.0, "pct_1h": 0.01, "pct_24h": 0.05, "volatility": 0.002,
    }

    row = engine._current_ranks()[0]

    assert row["pct_1h"] == 0.01
    assert row["pct_24h"] == 0.05
    assert row["volatility"] == 0.002
    assert row["price"] == 99.0  # no live snapshot ingested -- falls back to slow cache


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
    """Task 7: _current_ranks() now returns list[dict], not Story 1.4's dict[str, int] --
    the persist loop must build its own {iid: rank} dict from the new shape.
    """
    _reset_state()
    now_ns = time.time_ns()
    for iid, vol in (("BTC-USD-PERP.DYDX", 50_000_000.0), ("SHIB-USD-PERP.DYDX", 100.0)):
        _mark_fresh(iid, now_ns)
        engine._VOLUME_24H[iid] = vol

    assert engine._ranks_by_iid() == {"BTC-USD-PERP.DYDX": 1, "SHIB-USD-PERP.DYDX": 2}


def test_persist_snapshots_writes_rank_and_volume_from_current_ranks() -> None:
    """The relocated merge+write step persists a row carrying rank/volume24h sourced
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

    assert legacy == {"ofi": fast["ofi_5"], "microprice": fast["microprice"], "spread": fast["spread"]}
    assert legacy["microprice"] is not None
    assert legacy["spread"] == 1.0


def test_legacy_book_metrics_for_unknown_instrument_is_all_none() -> None:
    _reset_state()
    assert engine._legacy_book_metrics_for("NEVER-SEEN-USD-PERP.DYDX") == {
        "ofi": None,
        "microprice": None,
        "spread": None,
    }
