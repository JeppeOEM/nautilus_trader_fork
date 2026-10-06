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
Invariant tests for `RankingBoard` (AD-9, Story 22.10; one per command, AC #2 of Story 25.2) and
the live-tick fields it publishes.
"""

import math

import pytest

from ranking.domain import board as board_module
from ranking.domain.board import MAX_FUTURE_SKEW_NS
from ranking.domain.board import RECENTLY_STALE_WINDOW_NS
from ranking.domain.board import STALE_NS
from ranking.domain.board import RankingsPublisher
from ranking.domain.values import RankingMode
from ranking.tests.support import NOW_NS
from ranking.tests.support import SEC_NS
from ranking.tests.support import FakeClock
from ranking.tests.support import board
from ranking.tests.support import feed_prices
from ranking.tests.support import mark_fresh
from ranking.tests.support import set_volumes
from ranking.tests.support import snap


BTC = "BTC-USD-PERP.DYDX"


# --- ingest ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("bid", "ask"), [(0.0, 0.0), (-5.0, -1.0)])
def test_ingest_skips_a_non_positive_mid_without_raising(bid: float, ask: float) -> None:
    b = board()
    dropped = b.ingest(snap("ZERO-USD-PERP.DYDX", bid, ask), NOW_NS)
    assert len(dropped) == 1  # returned to be ledgered
    assert "non-finite/non-positive mid" in dropped[0]
    assert b.current_ranks(NOW_NS) == []  # volume mode, no volume
    b.switch_mode(RankingMode.VOLATILITY)
    assert b.current_ranks(NOW_NS)[0]["volatility_score"] is None  # nothing fed


def test_ingest_feeds_close_price_into_the_price_series() -> None:
    b = board()
    b.ingest(snap(BTC, ts_event=NOW_NS - 2 * SEC_NS, close_price=100.0), NOW_NS)
    b.ingest(snap(BTC, 100.0, 102.0, ts_event=NOW_NS - SEC_NS, close_price=101.0), NOW_NS)

    assert b.slow_rows(NOW_NS, {}, {})[0]["price"] == 101.0


# NaN/inf can no longer reach the board: the integer layout cannot carry them (Story 30.2).
@pytest.mark.parametrize("bad_price", [-1.0, 0.0])
def test_ingest_drops_a_nonfinite_or_nonpositive_close_price(bad_price: float) -> None:
    b = board()
    dropped = b.ingest(snap(BTC, ts_event=1 * SEC_NS, close_price=bad_price), NOW_NS)
    assert len(dropped) == 1  # returned to be ledgered
    assert "close_price" in dropped[0]
    assert b.slow_rows(NOW_NS, {}, {})[0]["price"] is None


def test_ingest_with_no_trade_records_no_price_but_still_feeds_the_book_trackers() -> None:
    b = board()
    b.ingest(snap(BTC, ts_event=0), NOW_NS)
    b.ingest(snap(BTC, ts_event=SEC_NS), NOW_NS)
    row = b.slow_rows(NOW_NS, {}, {})[0]
    assert row["price"] is None
    assert row["spread"] == 2.0  # the book trackers were fed


@pytest.mark.parametrize("ts_offset", [0, -SEC_NS])  # a duplicate, then an older snapshot
def test_ingest_drops_a_duplicate_or_older_snapshot_whole(ts_offset: int) -> None:
    """DW-218: a redelivered or replayed snapshot feeds nothing -- no book, no flow, no price."""
    b = board()
    first_ts = NOW_NS - 10 * SEC_NS
    first = snap(BTC, 100.0, 101.0, first_ts, buy_volume=3.0, buy_count=1, close_price=100.25)
    b.ingest(first, NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)
    replay_ts = first_ts + ts_offset
    replay = snap(BTC, 90.0, 95.0, replay_ts, buy_volume=3.0, buy_count=1, close_price=92.0)

    dropped = b.ingest(replay, NOW_NS)

    assert len(dropped) == 1  # one detail for the whole snapshot, returned to be ledgered
    assert "duplicate/out-of-order snapshot DROPPED" in dropped[0]
    assert f"last ts_event={first_ts}" in dropped[0]
    row = b.current_ranks(NOW_NS)[0]
    assert (row["spread"], row["price"]) == (1.0, 100.5)  # the first book, not the replay's
    assert (row["cvd"], row["buy_count"]) == (3.0, 1)  # its trades are not counted twice
    assert row["volatility_score"] is None  # one mid only: the replay's mid was not fed
    assert b.slow_rows(NOW_NS, {}, {})[0]["price"] == 100.25  # no price point either


def test_a_far_future_snapshot_is_dropped_and_never_freezes_the_instrument() -> None:
    """A broken clock's one far-future ts_event must not make every real snapshot after it "old"."""
    b = board()
    b.ingest(snap(BTC, 100.0, 101.0, NOW_NS - 2 * SEC_NS), NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    dropped = b.ingest(snap(BTC, 90.0, 95.0, NOW_NS + MAX_FUTURE_SKEW_NS + 1), NOW_NS)

    assert len(dropped) == 1
    assert "ahead of its arrival" in dropped[0]
    assert b.ingest(snap(BTC, 100.0, 103.0, NOW_NS - SEC_NS), NOW_NS) == []  # still taken
    assert b.current_ranks(NOW_NS)[0]["spread"] == 3.0


def test_a_snapshot_within_the_future_skew_is_taken() -> None:
    b = board()

    assert b.ingest(snap(BTC, ts_event=NOW_NS + MAX_FUTURE_SKEW_NS), NOW_NS) == []


def test_an_accepted_lead_never_takes_a_live_instrument_out_of_the_ranks() -> None:
    """
    A lead inside the skew bound makes the real snapshots behind it read as out of order, so they
    stamp no freshness. The bound sits under STALE_NS, so the instrument stays ranked until the
    real ts_event passes the lead and the gate takes it again.
    """
    assert MAX_FUTURE_SKEW_NS < STALE_NS
    b = board()
    b.ingest(snap(BTC, 100.0, 101.0, NOW_NS - SEC_NS), NOW_NS - SEC_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)
    b.ingest(snap(BTC, 100.0, 101.0, NOW_NS + MAX_FUTURE_SKEW_NS), NOW_NS)

    refused = 0
    for second in range(1, MAX_FUTURE_SKEW_NS // SEC_NS + 2):
        now = NOW_NS + second * SEC_NS
        refused += len(b.ingest(snap(BTC, 100.0, 102.0, now), now))
        assert [r["instrument_id"] for r in b.current_ranks(now)] == [BTC]

    assert refused == MAX_FUTURE_SKEW_NS // SEC_NS
    assert b.current_ranks(now)[0]["spread"] == 2.0  # the gate took the real snapshot again


def test_a_refused_first_snapshot_leaves_no_instrument_behind() -> None:
    """A far-future first snapshot must not list the instrument as fresh with no data."""
    b = board()

    b.ingest(snap(BTC, ts_event=NOW_NS + MAX_FUTURE_SKEW_NS + 1), NOW_NS)

    assert b.instrument_ids() == []


def test_a_dropped_snapshot_does_not_stamp_freshness() -> None:
    """DW-218, DATA-01: a redelivered old snapshot is no evidence that the market is live."""
    b = board()
    b.ingest(snap(BTC, ts_event=SEC_NS), NOW_NS - STALE_NS - 1)
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    assert len(b.ingest(snap(BTC, ts_event=SEC_NS), NOW_NS)) == 1

    assert b.current_ranks(NOW_NS) == []
    assert b.stale_ids(NOW_NS) == [BTC]


def test_an_in_order_snapshot_after_a_drop_is_taken_whole() -> None:
    b = board()
    b.ingest(snap(BTC, 100.0, 101.0, NOW_NS - 3 * SEC_NS), NOW_NS - STALE_NS - 1)
    b.ingest(snap(BTC, 100.0, 101.0, NOW_NS - 4 * SEC_NS), NOW_NS)  # dropped
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    taken = b.ingest(snap(BTC, 100.0, 103.0, NOW_NS - 2 * SEC_NS, close_price=101.0), NOW_NS)

    assert taken == []

    row = b.current_ranks(NOW_NS)[0]  # freshness stamped by the in-order snapshot
    assert (row["spread"], row["price"]) == (3.0, 101.5)
    assert row["volatility_score"] is None  # two mids, one return: fed, but not yet a score
    assert b.slow_rows(NOW_NS, {}, {})[0]["price"] == 101.0


def test_the_gate_covers_one_sided_snapshots_too() -> None:
    """An older two-sided snapshot after a newer one-sided one is dropped: the gate is per snapshot."""
    b = board()
    b.ingest(snap(BTC, bid=None, ask=None, ts_event=5 * SEC_NS), NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    dropped = b.ingest(snap(BTC, 100.0, 101.0, 4 * SEC_NS), NOW_NS)

    assert len(dropped) == 1
    assert "ts_event=4000000000 <= last ts_event=5000000000" in dropped[0]
    assert b.current_ranks(NOW_NS)[0]["spread"] is None  # no book tracker was fed


@pytest.mark.parametrize("bad_mid", [math.nan, math.inf, -math.inf])
def test_ingest_skips_a_non_finite_mid(bad_mid: float, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    The integer layout cannot carry a non-finite price (Story 30.2), so the mid is substituted at
    the board's own `calc_mid_price` seam: the guard is proven, not the encoder.
    """
    monkeypatch.setattr(board_module, "calc_mid_price", lambda _row: bad_mid)
    b = board()
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    dropped = b.ingest(snap(BTC, 100.0, 101.0), NOW_NS)

    assert len(dropped) == 1
    assert "non-finite/non-positive mid" in dropped[0]
    monkeypatch.undo()
    row = b.current_ranks(NOW_NS)[0]
    assert (row["spread"], row["ofi_10"], row["volatility_score"]) == (None, None, None)


def test_freshness_is_stamped_on_receipt_not_on_the_snapshots_exchange_time() -> None:
    """Story 22.12: a venue-timed row's ts_event lag must not age the instrument."""
    b = board()
    b.ingest(snap(BTC, ts_event=NOW_NS - STALE_NS - 5 * SEC_NS), NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)
    assert [r["instrument_id"] for r in b.current_ranks(NOW_NS)] == [BTC]


# --- ranks: both scores, volume mode, volatility mode ------------------------------------------


def test_volume_mode_sorts_by_descending_volume24h() -> None:
    b = board()
    for iid in (BTC, "SHIB-USD-PERP.DYDX"):
        mark_fresh(b, iid, NOW_NS)
    set_volumes(b, {BTC: 50_000_000.0, "SHIB-USD-PERP.DYDX": 100.0}, NOW_NS)

    ranks = b.current_ranks(NOW_NS)

    assert [r["instrument_id"] for r in ranks] == [BTC, "SHIB-USD-PERP.DYDX"]
    assert [r["rank"] for r in ranks] == [1, 2]
    assert {(r["venue"], r["venue_kind"], r["market"]) for r in ranks} == {("DYDX", "dex", "perp")}
    assert [r["symbol"] for r in ranks] == ["BTC", "SHIB"]


def test_market_distinguishes_spot_from_linear() -> None:
    b = board()
    for iid in ("BTCUSDT-SPOT.BYBIT", "BTCUSDT-LINEAR.BYBIT"):
        mark_fresh(b, iid, NOW_NS)
    set_volumes(b, {"BTCUSDT-SPOT.BYBIT": 1.0, "BTCUSDT-LINEAR.BYBIT": 1.0}, NOW_NS)
    assert {r["instrument_id"]: r["market"] for r in b.current_ranks(NOW_NS)} == {
        "BTCUSDT-SPOT.BYBIT": "spot",
        "BTCUSDT-LINEAR.BYBIT": "perp",
    }


def test_volatility_mode_sorts_by_descending_volatility_score() -> None:
    b = board()
    b.switch_mode(RankingMode.VOLATILITY)
    feed_prices(b, "CALM-USD-PERP.DYDX", [100.0, 100.1, 99.9, 100.05], NOW_NS)
    feed_prices(b, "WILD-USD-PERP.DYDX", [100.0, 120.0, 80.0, 130.0], NOW_NS)

    ranks = b.current_ranks(NOW_NS)

    assert [r["instrument_id"] for r in ranks] == ["WILD-USD-PERP.DYDX", "CALM-USD-PERP.DYDX"]


@pytest.mark.parametrize("mode", list(RankingMode))
def test_both_scores_are_present_on_every_row_in_either_mode(mode: RankingMode) -> None:
    b = board()
    feed_prices(b, BTC, [100.0, 101.0, 99.0], NOW_NS)
    set_volumes(b, {BTC: 12.0}, NOW_NS)
    b.switch_mode(mode)

    row = b.current_ranks(NOW_NS)[0]

    assert row["volume24h"] == 12.0
    assert row["volatility_score"] is not None


def test_a_row_without_usd_volume_leaves_volume_mode_and_stays_none_in_volatility_mode() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    mark_fresh(b, "BTC-USD-PERP.HYPERLIQUID", NOW_NS)
    set_volumes(b, {BTC: 3_000_000.0}, NOW_NS)

    assert [r["instrument_id"] for r in b.current_ranks(NOW_NS)] == [BTC]
    assert b.missing_volume_ids(NOW_NS) == ["BTC-USD-PERP.HYPERLIQUID"]

    b.switch_mode(RankingMode.VOLATILITY)
    rows = {r["instrument_id"]: r for r in b.current_ranks(NOW_NS)}
    assert set(rows) == {BTC, "BTC-USD-PERP.HYPERLIQUID"}
    assert rows["BTC-USD-PERP.HYPERLIQUID"]["volume24h"] is None  # never a fabricated 0


# --- volumes -----------------------------------------------------------------------------------


def test_a_source_past_max_age_is_expired_and_reported() -> None:
    b = board()
    max_age = b._volume_max_age_ns
    b.record_volume_poll("dydx", {BTC: 1.0}, NOW_NS)
    b.record_volume_poll("bybit-spot", {"BTCUSDT-SPOT.BYBIT": 2.0}, NOW_NS - max_age - 1)

    expired = b.refresh_volumes(NOW_NS)

    assert expired == [("bybit-spot", max_age + 1)]
    for iid in (BTC, "BTCUSDT-SPOT.BYBIT"):
        mark_fresh(b, iid, NOW_NS)
    assert [r["instrument_id"] for r in b.current_ranks(NOW_NS)] == [BTC]


def test_a_poll_replaces_its_source_whole_so_a_delisted_coin_drops_out() -> None:
    b = board()
    b.record_volume_poll("hyperliquid", {"A-USD-PERP.HYPERLIQUID": 1.0}, NOW_NS)
    b.record_volume_poll("hyperliquid", {"B-USD-PERP.HYPERLIQUID": 2.0}, NOW_NS)
    b.refresh_volumes(NOW_NS)
    for iid in ("A-USD-PERP.HYPERLIQUID", "B-USD-PERP.HYPERLIQUID"):
        mark_fresh(b, iid, NOW_NS)

    assert [r["instrument_id"] for r in b.current_ranks(NOW_NS)] == ["B-USD-PERP.HYPERLIQUID"]


def test_volume_max_age_is_three_poll_intervals_in_ns() -> None:
    assert board()._volume_max_age_ns == 3 * 60 * SEC_NS


# --- staleness and age-out ---------------------------------------------------------------------


def test_a_stale_instrument_leaves_the_ranks_and_is_listed_as_stale() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    mark_fresh(b, "SOL-USD-PERP.DYDX", NOW_NS - STALE_NS - 1)
    set_volumes(b, {BTC: 1.0, "SOL-USD-PERP.DYDX": 1.0}, NOW_NS)

    message = b.build_message(NOW_NS)

    assert [r["instrument_id"] for r in message["ranks"]] == [BTC]
    assert message["stale_instrument_ids"] == ["SOL-USD-PERP.DYDX"]


def test_stale_ids_exclude_long_dead_and_fresh_instruments() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    mark_fresh(b, "DEAD-USD-PERP.DYDX", NOW_NS - RECENTLY_STALE_WINDOW_NS - 1)
    assert b.stale_ids(NOW_NS) == []


def test_age_out_drops_an_instrument_silent_past_the_window_and_it_returns_fresh() -> None:
    b = board()
    feed_prices(
        b, "DEAD-USD-PERP.DYDX", [100.0, 101.0, 102.0], NOW_NS - RECENTLY_STALE_WINDOW_NS - 1
    )
    b.mark_backfilled("DEAD-USD-PERP.DYDX")
    mark_fresh(b, BTC, NOW_NS)

    assert b.age_out(NOW_NS) == ["DEAD-USD-PERP.DYDX"]
    assert b.instrument_ids() == [BTC]
    assert [row["instrument_id"] for row in b.slow_rows(NOW_NS, {}, {})] == [BTC]

    mark_fresh(b, "DEAD-USD-PERP.DYDX", NOW_NS)  # it comes back: fresh state, backfilled again
    assert "DEAD-USD-PERP.DYDX" in b.unbackfilled_ids()
    b.switch_mode(RankingMode.VOLATILITY)
    rows = {r["instrument_id"]: r for r in b.current_ranks(NOW_NS)}
    assert rows["DEAD-USD-PERP.DYDX"]["volatility_score"] is None  # old returns forgotten


def test_slow_rows_skip_a_stale_instrument_so_metrics_db_gets_a_gap() -> None:
    """DW-217, DATA-01: a stale instrument's last values are not current, so no row stamps them."""
    b = board()
    b.ingest(snap(BTC, ts_event=NOW_NS - SEC_NS, close_price=100.0), NOW_NS)
    sol_ts = NOW_NS - 60 * SEC_NS
    b.ingest(snap("SOL-USD-PERP.DYDX", ts_event=sol_ts, close_price=150.0), NOW_NS - STALE_NS - 1)

    rows = b.slow_rows(NOW_NS, {}, {})

    assert [row["instrument_id"] for row in rows] == [BTC]
    assert b.stale_ids(NOW_NS) == ["SOL-USD-PERP.DYDX"]  # still visible as stale, not ledgered


def test_a_stale_instrument_that_returns_gets_slow_rows_again() -> None:
    b = board()
    sol = "SOL-USD-PERP.DYDX"
    b.ingest(snap(sol, ts_event=NOW_NS - 60 * SEC_NS, close_price=150.0), NOW_NS - STALE_NS - 1)
    assert b.slow_rows(NOW_NS, {}, {}) == []

    b.ingest(snap(sol, ts_event=NOW_NS - SEC_NS, close_price=151.0), NOW_NS)

    rows = b.slow_rows(NOW_NS, {}, {})
    assert [(r["instrument_id"], r["price"]) for r in rows] == [(sol, 151.0)]


def test_age_out_keeps_a_recently_stale_instrument() -> None:
    b = board()
    mark_fresh(b, "SOL-USD-PERP.DYDX", NOW_NS - STALE_NS - 1)
    assert b.age_out(NOW_NS) == []


# --- mode --------------------------------------------------------------------------------------


def test_mode_is_global_and_last_write_wins() -> None:
    b = board()
    assert b.mode == RankingMode.VOLUME
    b.switch_mode(RankingMode.VOLATILITY)
    b.switch_mode(RankingMode.VOLUME)
    b.switch_mode(RankingMode.VOLATILITY)
    assert b.build_message(NOW_NS)["mode"] == "volatility"


def test_mode_parse_accepts_only_the_two_modes() -> None:
    assert RankingMode.parse("volume") is RankingMode.VOLUME
    assert RankingMode.parse("volatility") is RankingMode.VOLATILITY
    for bad in ("nonsense", "", None, 1, ["volume"]):
        assert RankingMode.parse(bad) is None


# --- publisher: on change and on heartbeat -----------------------------------------------------


def _rank_row(rank: int, iid: str = BTC) -> dict:
    return {"instrument_id": iid, "rank": rank, "volume24h": 1.0, "volatility_score": None}


def test_publisher_republishes_on_heartbeat_even_with_no_rank_change() -> None:
    clock = FakeClock(0)
    publisher = RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time)
    ranks = [_rank_row(1)]

    assert publisher.should_publish(ranks, "volume") is True  # first call always publishes
    publisher.record_published(ranks, "volume")
    clock.ns = 2 * SEC_NS  # within the heartbeat window, nothing changed
    assert publisher.should_publish(ranks, "volume") is False
    clock.ns = 5 * SEC_NS  # heartbeat interval elapsed, still nothing changed
    assert publisher.should_publish(ranks, "volume") is True


def test_publisher_republishes_immediately_on_rank_change() -> None:
    clock = FakeClock(0)
    publisher = RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time)
    publisher.record_published([_rank_row(1)], "volume")
    clock.ns = SEC_NS
    assert publisher.should_publish([_rank_row(2)], "volume") is True


def test_publisher_republishes_on_mode_change_even_with_identical_ranks() -> None:
    clock = FakeClock(0)
    publisher = RankingsPublisher(heartbeat_seconds=5, now_fn=clock.time)
    publisher.record_published([_rank_row(1)], "volume")
    clock.ns = SEC_NS
    assert publisher.should_publish([_rank_row(1)], "volatility") is True


# --- wire fields -------------------------------------------------------------------------------


def test_build_message_matches_the_wire_schema() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    set_volumes(b, {BTC: 5.0}, NOW_NS)

    message = b.build_message(NOW_NS)

    assert list(message) == ["mode", "updated_at", "ranks", "stale_instrument_ids"]
    assert message["updated_at"] == NOW_NS
    row = message["ranks"][0]
    assert (row["instrument_id"], row["rank"], row["volume24h"]) == (BTC, 1, 5.0)
    assert row["volatility_score"] is None


def test_rank_rows_carry_the_live_tick_fields_from_ingested_snapshots() -> None:
    b = board()
    b.ingest(
        snap(BTC, 100.0, 101.0, 0, buy_volume=3.0, sell_volume=1.0, buy_count=2, sell_count=1),
        NOW_NS,
    )
    b.ingest(snap(BTC, 101.0, 102.0, SEC_NS, buy_volume=2.0, buy_count=1), NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    row = b.current_ranks(NOW_NS)[0]

    assert row["ofi_10"] is not None  # second update, tracker initialized
    assert row["obi_10"] == 0.5  # bid_size == ask_size == 1.0 on every snapshot
    assert row["microprice"] is not None
    assert row["spread"] == 1.0  # 102.0 - 101.0
    assert row["price"] == 101.5  # (101.0 + 102.0) / 2
    assert row["cvd"] == 4.0  # (3+2) buy - (1+0) sell
    assert row["volume_delta"] == 4.0  # both snapshots inside the 60-snapshot window
    assert (row["buy_count"], row["sell_count"]) == (3, 1)
    assert row["avg_trade_size"] == 1.5  # (5.0 + 1.0) / (3 + 1)


def test_rank_rows_take_pct_and_volatility_from_the_slow_metrics_but_never_the_price() -> None:
    """Story 31.3: `price` is the live mid or None -- a slow-loop trade close never stands in."""
    b = board()
    b.ingest(snap(BTC, bid=None, ask=None, ts_event=NOW_NS - SEC_NS, close_price=99.0), NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)
    b.slow_rows(NOW_NS, {BTC: 90.0}, {})

    row = b.current_ranks(NOW_NS)[0]

    assert row["price"] is None  # no live book yet: no mid, and no substitute for it
    assert row["pct_1w"] == pytest.approx(10.0)
    assert row["pct_1m"] is None  # not enough history -- never 0
    assert row["pct_1h"] is None


def test_an_empty_rolling_window_publishes_no_cvd_rather_than_zero() -> None:
    """Story 31.3: with no snapshot held there is no flow to report -- None, never a 0.0 CVD."""
    b = board()
    b.ingest(snap(BTC, bid=None, ask=None, ts_event=NOW_NS - SEC_NS), NOW_NS)  # feeds no window
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    row = b.current_ranks(NOW_NS)[0]

    assert (row["cvd"], row["volume_delta"], row["avg_trade_size"]) == (None, None, None)
    assert (row["buy_count"], row["sell_count"]) == (0, 0)  # the empty sums


def test_volatility_mode_sorts_an_unscored_row_as_zero_known_limit() -> None:
    """
    Pinned Known limit (`RankingBoard`'s docstring, DATA_DICTIONARY §3.3): a row whose
    `volatility_score` is None sorts as 0 -- after every positive score, before a negative one
    (none exists: a standard deviation is never negative) -- and still publishes None.
    """
    b = board()
    eth = "ETH-USD-PERP.DYDX"
    for k, bid in enumerate((100.0, 101.0, 100.5, 102.0)):
        b.ingest(snap(eth, bid, bid + 2.0, ts_event=NOW_NS - (4 - k) * SEC_NS), NOW_NS)
    b.ingest(snap(BTC, ts_event=NOW_NS - SEC_NS), NOW_NS)  # one mid: no score
    b.switch_mode(RankingMode.VOLATILITY)

    ranks = b.current_ranks(NOW_NS)

    assert [r["instrument_id"] for r in ranks] == [eth, BTC]
    assert ranks[1]["volatility_score"] is None


def test_rank_rows_drop_stale_slow_metrics() -> None:
    b = board()
    b.ingest(snap(BTC, bid=None, ask=None, ts_event=NOW_NS - SEC_NS, close_price=99.0), NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)
    b.slow_rows(NOW_NS - 10 * 60 * SEC_NS, {}, {})

    row = b.current_ranks(NOW_NS)[0]

    assert row["price"] is None  # a stalled slow loop reads as a gap, not as live


def test_with_ranks_attaches_rank_and_volume_and_none_for_unranked() -> None:
    b = board()
    mark_fresh(b, BTC, NOW_NS)
    set_volumes(b, {BTC: 50_000_000.0}, NOW_NS)
    rows: list[dict] = [
        {"instrument_id": BTC, "price": 1.0},
        {"instrument_id": "DEAD-USD-PERP.DYDX"},
    ]

    merged = b.with_ranks(rows, NOW_NS)

    assert merged[0] == {"instrument_id": BTC, "price": 1.0, "rank": 1, "volume24h": 50_000_000.0}
    assert (merged[1]["rank"], merged[1]["volume24h"]) == (None, None)


def test_ranks_by_iid_is_one_indexed() -> None:
    b = board()
    for iid in (BTC, "SHIB-USD-PERP.DYDX"):
        mark_fresh(b, iid, NOW_NS)
    set_volumes(b, {BTC: 50_000_000.0, "SHIB-USD-PERP.DYDX": 100.0}, NOW_NS)
    assert b.ranks_by_iid(NOW_NS) == {BTC: 1, "SHIB-USD-PERP.DYDX": 2}


def test_metrics_db_book_columns_read_the_same_live_trackers_as_the_rank_entry() -> None:
    """SSOT-02: ofi/microprice/spread in metrics.db are the rank entry's ofi_5/microprice/spread."""
    b = board()
    b.ingest(snap(BTC, 100.0, 101.0, 0), NOW_NS)
    b.ingest(snap(BTC, 100.0, 101.0, SEC_NS), NOW_NS)
    set_volumes(b, {BTC: 1.0}, NOW_NS)

    rank = b.current_ranks(NOW_NS)[0]
    row = b.slow_rows(NOW_NS, {}, {})[0]

    assert (row["ofi"], row["microprice"], row["spread"]) == (
        rank["ofi_5"],
        rank["microprice"],
        rank["spread"],
    )
    assert row["spread"] == 1.0
