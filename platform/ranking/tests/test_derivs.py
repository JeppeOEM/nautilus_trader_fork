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
Story 33.4's ranking fields (`ranking.domain.derivs` and their board wiring): every field
hand-computed, and every None case -- a missing input, a zero denominator, a series that does not
reach its target, an id with no liquidation feed. Real kernel and Nautilus types throughout.
"""

from decimal import Decimal

import pytest
from kernel.derivs_wire import DerivsTick
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest

from nautilus_trader.model.identifiers import InstrumentId
from ranking.domain.derivs import DERIVS_FIELDS
from ranking.domain.derivs import HOUR_NS
from ranking.domain.derivs import LAST_CLOSE_MAX_AGE_NS
from ranking.domain.derivs import MINUTE_NS
from ranking.domain.derivs import OI_MAX_AGE_NS
from ranking.domain.derivs import DerivsState
from ranking.domain.derivs import OpenInterestSeries
from ranking.domain.derivs import TradedVolume
from ranking.domain.derivs import has_open_interest
from ranking.domain.price_series import PricePoint
from ranking.domain.price_series import PriceRange
from ranking.tests.support import NOW_NS
from ranking.tests.support import SEC_NS
from ranking.tests.support import board
from ranking.tests.support import set_volumes
from ranking.tests.support import snap


LINEAR = "BTCUSDT-LINEAR.BYBIT"  # the one liquidation feed
HL = "SOL-USD-PERP.HYPERLIQUID"  # open interest, no liquidation feed
SPOT = "BTCUSDT-SPOT.BYBIT"


def _tick(kind: str, value: str, t: int = NOW_NS, **funding: int | None) -> DerivsTick:
    return DerivsTick(LINEAR, kind, t, t, Decimal(value), **funding)


def _liq(
    event: str, side: LiquidatedSide, size_units: int, price_units: int, ts: int, iid: str = LINEAR
) -> Liquidation:
    """Build a liquidation at price precision 1 and size precision 3 (units of 0.1 and 0.001)."""
    return Liquidation(
        instrument_id=InstrumentId.from_str(iid),
        side=side,
        size_units=size_units,
        price_units=price_units,
        price_precision=1,
        size_precision=3,
        venue_event_id=event,
        ts_event=ts,
        ts_init=ts,
    )


def _fields(state: DerivsState, feed: bool = True, rng: PriceRange | None = None) -> dict:
    return state.fields(NOW_NS, feed, rng)


# --- funding and basis --------------------------------------------------------------------------


def test_funding_rate_its_annualised_rate_and_next_funding_time() -> None:
    state = DerivsState(NOW_NS)
    state.ingest(_tick("funding", "0.0001", interval=28_800, next_funding_ns=NOW_NS + 7))

    row = _fields(state)

    assert row["funding_rate"] == 0.0001
    assert row["funding_annualised"] == pytest.approx(0.1095)  # 0.0001 * 31_536_000 / 28_800
    assert row["next_funding_ns"] == NOW_NS + 7


def test_funding_without_an_interval_has_no_annualised_rate() -> None:
    state = DerivsState(NOW_NS)
    state.ingest(_tick("funding", "0.0001", interval=None, next_funding_ns=None))

    row = _fields(state)

    assert (row["funding_rate"], row["funding_annualised"], row["next_funding_ns"]) == (
        0.0001,
        None,
        None,
    )


def test_mark_index_and_mark_last_basis_in_bps() -> None:
    state = DerivsState(NOW_NS)
    state.ingest(_tick("mark", "100.5"))
    state.ingest(_tick("index", "100.0"))
    state.note_close(NOW_NS, Decimal("100.4"))

    row = _fields(state)

    assert row["basis_mi_bps"] == 50.0  # (100.5 - 100) / 100 * 1e4
    assert row["basis_ml_bps"] == pytest.approx(9.9601593625)  # 0.1 / 100.4 * 1e4


def test_mark_last_basis_holds_until_the_close_is_older_than_its_bound() -> None:
    """A close exactly `LAST_CLOSE_MAX_AGE_NS` old is still current; 1 ns older it is not."""
    state = DerivsState(NOW_NS)
    state.ingest(_tick("mark", "100.5"))
    state.note_close(NOW_NS - LAST_CLOSE_MAX_AGE_NS, Decimal("100.4"))
    assert _fields(state)["basis_ml_bps"] == pytest.approx(9.9601593625)
    state.note_close(NOW_NS - LAST_CLOSE_MAX_AGE_NS - 1, Decimal("100.4"))
    assert _fields(state)["basis_ml_bps"] is None


def test_basis_is_none_without_its_reference() -> None:
    state = DerivsState(NOW_NS)
    state.ingest(_tick("mark", "100.5"))

    row = _fields(state)

    assert (row["basis_mi_bps"], row["basis_ml_bps"]) == (None, None)


def test_an_older_mark_tick_is_refused_and_reported() -> None:
    state = DerivsState(NOW_NS)
    state.ingest(_tick("mark", "100.5", t=NOW_NS))

    refused = state.ingest(_tick("mark", "99.0", t=NOW_NS - SEC_NS))

    assert refused is not None
    assert "out-of-order mark" in refused
    assert state.mark is not None
    assert state.mark.value == Decimal("100.5")


def test_every_field_is_none_with_no_input_at_all() -> None:
    row = DerivsState(NOW_NS).fields(NOW_NS, False, None)

    assert list(row) == list(DERIVS_FIELDS)
    assert set(row.values()) == {None}


# --- open interest ------------------------------------------------------------------------------


def _oi(*points: tuple[int, str]) -> OpenInterestSeries:
    series = OpenInterestSeries()
    for ts, value in points:
        series.add(ts, Decimal(value))
    return series


def test_open_interest_and_its_1h_and_24h_changes() -> None:
    series = _oi(
        (NOW_NS - 24 * HOUR_NS - 60 * SEC_NS, "1000"),
        (NOW_NS - HOUR_NS - 120 * SEC_NS, "1100"),
        (NOW_NS - 30 * SEC_NS, "1200"),
    )

    assert series.fields(NOW_NS) == {
        "open_interest": 1200.0,
        "oi_change_1h": 100.0,  # 1200 - the 1100 standing at now - 1 h
        "oi_change_24h": 200.0,  # 1200 - the 1000 standing at now - 24 h
    }


def test_oi_changes_are_none_when_the_series_reaches_back_only_30_minutes() -> None:
    series = _oi((NOW_NS - 30 * 60 * SEC_NS, "1000"), (NOW_NS - 30 * SEC_NS, "1200"))

    assert series.fields(NOW_NS) == {
        "open_interest": 1200.0,
        "oi_change_1h": None,
        "oi_change_24h": None,
    }


def test_an_oi_base_further_than_the_max_age_before_its_target_is_none() -> None:
    too_old = NOW_NS - HOUR_NS - OI_MAX_AGE_NS - SEC_NS  # a gap across the 1 h target
    series = _oi((too_old, "1100"), (NOW_NS - 30 * SEC_NS, "1200"))

    assert series.fields(NOW_NS)["oi_change_1h"] is None


def test_a_stalled_open_interest_reads_none_not_its_last_value() -> None:
    series = _oi((NOW_NS - OI_MAX_AGE_NS - SEC_NS, "1200"))

    assert series.fields(NOW_NS) == {
        "open_interest": None,
        "oi_change_1h": None,
        "oi_change_24h": None,
    }


def test_one_oi_point_delivered_twice_with_different_values_is_refused() -> None:
    series = _oi((NOW_NS - SEC_NS, "1200"))

    assert series.add(NOW_NS - SEC_NS, Decimal(1200)) is None  # an agreeing repeat
    refused = series.add(NOW_NS - SEC_NS, Decimal(1300))

    assert refused is not None
    assert "1200 and 1300" in refused
    assert series.fields(NOW_NS)["open_interest"] == 1200.0


def test_the_oi_series_keeps_the_newest_point_per_minute_and_25_hours_only() -> None:
    series = _oi(
        (NOW_NS - 26 * HOUR_NS, "1"),  # pruned: older than 25 h before the newest
        (NOW_NS - 50 * SEC_NS, "2"),
        (NOW_NS - 20 * SEC_NS, "3"),  # same minute, newer: replaces "2"
    )

    assert len(series._by_minute) == 1
    assert series.fields(NOW_NS)["open_interest"] == 3.0


def test_only_perpetuals_have_open_interest() -> None:
    assert (has_open_interest(LINEAR), has_open_interest(HL), has_open_interest(SPOT)) == (
        True,
        True,
        False,
    )


# --- liquidations -------------------------------------------------------------------------------


def _liquidated_state() -> DerivsState:
    state = DerivsState(NOW_NS)
    state.liquidations.add(_liq("a", LiquidatedSide.LONG, 500, 1000, NOW_NS - SEC_NS))  # 0.5 @ 100
    state.liquidations.add(_liq("b", LiquidatedSide.SHORT, 1500, 1010, NOW_NS - SEC_NS))  # 1.5@101
    state.liquidations.add(_liq("b", LiquidatedSide.SHORT, 1500, 1010, NOW_NS - SEC_NS))  # repeat
    state.liquidations.add(_liq("old", LiquidatedSide.LONG, 9000, 1000, NOW_NS - HOUR_NS))
    state.volume.add_live(NOW_NS - 10 * SEC_NS, Decimal(8))
    return state


def test_liquidation_sums_ratio_notional_and_forced_share_over_the_hour() -> None:
    row = _fields(_liquidated_state())

    assert row["liq_long_1h"] == 0.5
    assert row["liq_short_1h"] == 1.5  # the repeated event "b" counted once
    assert row["liq_notional_1h"] == 201.5  # 0.5 x 100 + 1.5 x 101, at the bankruptcy price
    assert row["liq_ratio_1h"] == 0.25  # 0.5 / (0.5 + 1.5)
    assert row["forced_share_1h"] == 0.25  # 2.0 liquidated / 8.0 traded


def test_liquidation_fields_are_none_for_an_id_without_the_feed() -> None:
    row = _fields(_liquidated_state(), feed=False)

    names = ("liq_long_1h", "liq_short_1h", "liq_notional_1h", "liq_ratio_1h", "forced_share_1h")
    assert {row[name] for name in names} == {None}


def test_a_feed_with_no_liquidation_is_zero_and_its_ratios_none() -> None:
    state = DerivsState(NOW_NS)

    row = _fields(state)

    assert (row["liq_long_1h"], row["liq_short_1h"], row["liq_notional_1h"]) == (0.0, 0.0, 0.0)
    assert (row["liq_ratio_1h"], row["forced_share_1h"]) == (None, None)


def test_forced_share_is_none_with_no_traded_volume_in_the_hour() -> None:
    state = DerivsState(NOW_NS)
    state.liquidations.add(_liq("a", LiquidatedSide.LONG, 500, 1000, NOW_NS - SEC_NS))

    row = _fields(state)

    assert (row["liq_ratio_1h"], row["forced_share_1h"]) == (1.0, None)


def test_expire_drops_events_older_than_the_window() -> None:
    state = _liquidated_state()

    state.liquidations.expire(NOW_NS)

    assert set(state.liquidations._events) == {"a", "b"}


# --- traded volume ------------------------------------------------------------------------------


def test_relative_volume_is_the_last_hour_over_the_mean_hourly_volume() -> None:
    volume = TradedVolume()
    volume.add_live(NOW_NS - 4 * HOUR_NS, Decimal(2))
    volume.add_live(NOW_NS - 2 * HOUR_NS, Decimal(2))
    volume.add_live(NOW_NS - 30 * MINUTE_NS, Decimal(4))

    # 8 over 4 h is a mean of 2 per hour; the last hour traded 4.
    assert volume.relative(NOW_NS) == Decimal(2)


def test_relative_volume_means_over_24_hours_once_the_series_is_longer() -> None:
    volume = TradedVolume()
    volume.add_live(NOW_NS - 25 * HOUR_NS + MINUTE_NS, Decimal(100))  # outside the 24 h mean
    volume.add_live(NOW_NS - 23 * HOUR_NS, Decimal(21))
    volume.add_live(NOW_NS - 30 * MINUTE_NS, Decimal(3))

    assert volume.relative(NOW_NS) == Decimal(3)  # 3 / (24 / 24)


@pytest.mark.parametrize("first_ago_ns", [HOUR_NS, 2 * HOUR_NS - SEC_NS])
def test_relative_volume_is_none_under_two_hours_of_data(first_ago_ns: int) -> None:
    volume = TradedVolume()
    volume.add_live(NOW_NS - first_ago_ns, Decimal(5))

    assert volume.relative(NOW_NS) is None


def test_relative_volume_is_none_with_no_traded_volume() -> None:
    volume = TradedVolume()
    volume.add_live(NOW_NS - 3 * HOUR_NS, Decimal(0))

    assert volume.relative(NOW_NS) is None


def test_a_repeated_live_second_is_refused_and_counted_once() -> None:
    volume = TradedVolume()
    volume.add_live(NOW_NS - SEC_NS, Decimal(1))

    refused = volume.add_live(NOW_NS - SEC_NS, Decimal(1))

    assert refused is not None
    assert volume.within(NOW_NS, HOUR_NS) == Decimal(1)


def test_the_backfill_adds_only_seconds_before_the_first_live_one() -> None:
    volume = TradedVolume()
    volume.add_live(NOW_NS - 10 * SEC_NS, Decimal(1))

    volume.backfill([(NOW_NS - 3 * HOUR_NS, Decimal(6)), (NOW_NS - 10 * SEC_NS, Decimal(1))])

    assert volume.within(NOW_NS, 4 * HOUR_NS) == Decimal(7)  # the shared second once
    assert volume.relative(NOW_NS) == Decimal(3) / Decimal(7)  # 1 / (7 / 3)


# --- 24 h range ---------------------------------------------------------------------------------


def test_range_position_is_where_the_last_close_sits_in_the_range() -> None:
    row = _fields(DerivsState(NOW_NS), rng=PriceRange(high=110.0, low=100.0, last=102.5))

    assert (row["high_24h"], row["low_24h"], row["range_position_24h"]) == (110.0, 100.0, 0.25)


def test_range_position_is_none_on_a_flat_range() -> None:
    row = _fields(DerivsState(NOW_NS), rng=PriceRange(high=100.0, low=100.0, last=100.0))

    assert (row["high_24h"], row["low_24h"], row["range_position_24h"]) == (100.0, 100.0, None)


# --- the board ----------------------------------------------------------------------------------


def test_the_rank_row_carries_the_new_fields_after_volatility_and_rank_stays_last() -> None:
    b = board()
    b.ingest(snap(LINEAR, 99.0, 101.0, NOW_NS - 2 * SEC_NS, close_price=100.0), NOW_NS)
    b.ingest(snap(LINEAR, 104.0, 106.0, NOW_NS - SEC_NS, close_price=105.0), NOW_NS)
    b.ingest_derivs(_tick("mark", "105.21"), NOW_NS)
    set_volumes(b, {LINEAR: 1.0}, NOW_NS)
    b.slow_rows(NOW_NS, {}, {})

    row = b.current_ranks(NOW_NS)[0]

    keys = list(row)
    start = keys.index("volatility") + 1
    assert tuple(keys[start : start + len(DERIVS_FIELDS)]) == DERIVS_FIELDS
    assert keys[-1] == "rank"
    assert (row["high_24h"], row["low_24h"], row["range_position_24h"]) == (105.0, 100.0, 1.0)
    assert row["basis_ml_bps"] == pytest.approx(20.0)  # (105.21 - 105) / 105 * 1e4
    assert row["liq_long_1h"] == 0.0  # the feed, and no liquidation yet


def test_the_slow_row_persists_the_fields_and_none_for_a_spot_id() -> None:
    b = board()
    b.ingest(snap(SPOT, 99.0, 101.0, NOW_NS - SEC_NS, close_price=100.0), NOW_NS)

    (row,) = b.slow_rows(NOW_NS, {}, {})

    assert row["open_interest"] is None
    assert row["liq_long_1h"] is None  # spot: no feed, never 0
    assert row["high_24h"] == 100.0


def test_a_liquidation_for_an_id_without_the_feed_is_reported_not_counted() -> None:
    b = board()

    refused = b.ingest_liquidation(_liq("x", LiquidatedSide.LONG, 1, 1, NOW_NS, iid=HL), NOW_NS)

    assert len(refused) == 1
    assert "no liquidation feed" in refused[0]


def test_a_repeated_snapshot_second_is_one_ledger_detail() -> None:
    b = board()
    b.ingest(snap(LINEAR, 99.0, 101.0, NOW_NS - SEC_NS, close_price=100.0), NOW_NS)

    dropped = b.ingest(snap(LINEAR, 99.0, 101.0, NOW_NS - SEC_NS, close_price=100.0), NOW_NS)

    assert len(dropped) == 1  # the price series' detail; the volume's is not a second one


def test_the_backfill_seeds_volume_close_open_interest_and_liquidations() -> None:
    b = board()
    b.ingest(snap(LINEAR, 99.0, 101.0, NOW_NS - SEC_NS, buy_volume=1.0, buy_count=1), NOW_NS)
    point = PricePoint(NOW_NS - 3 * HOUR_NS, 100.0, Decimal("100.0"), Decimal(6))
    b.backfill(LINEAR, [point])
    oi = OpenInterest(InstrumentId.from_str(LINEAR), Decimal(1200), NOW_NS - SEC_NS, NOW_NS)
    assert b.backfill_open_interest(LINEAR, [oi]) == []
    liq = _liq("a", LiquidatedSide.SHORT, 1000, 1000, NOW_NS - SEC_NS)
    assert b.backfill_liquidations(LINEAR, [liq]) == []

    (row,) = b.slow_rows(NOW_NS, {}, {})

    assert row["open_interest"] == 1200.0
    assert (row["liq_short_1h"], row["liq_notional_1h"]) == (1.0, 100.0)
    assert row["relative_volume"] == pytest.approx(3 / 7)  # 1 / (7 / 3)
    assert row["high_24h"] == 100.0


def test_derivs_state_of_an_id_never_ranked_ages_out() -> None:
    b = board()
    b.ingest_derivs(_tick("mark", "1"), NOW_NS)

    b.age_out(NOW_NS + 2 * HOUR_NS)

    assert b._derivs == {}


def test_backfill_needs_name_open_interest_and_the_liquidation_feed() -> None:
    b = board()

    assert b.derivs_backfill_needs(LINEAR) == (True, True)
    assert b.derivs_backfill_needs(HL) == (True, False)
    assert b.derivs_backfill_needs(SPOT) == (False, False)


def test_a_backfill_never_moves_the_derivs_state_last_seen_backwards() -> None:
    """
    The snapshot arrived at NOW - 10 s, a mark tick at NOW: the backfill (which passes the
    snapshot's arrival) leaves the state's last arrival at NOW, the newer of the two.
    """
    b = board()
    b.ingest(snap(LINEAR, 99.0, 101.0, NOW_NS - 11 * SEC_NS), NOW_NS - 10 * SEC_NS)
    b.ingest_derivs(_tick("mark", "100.5"), NOW_NS)

    b.backfill(LINEAR, [PricePoint(NOW_NS - HOUR_NS, 100.0, Decimal("100.0"), Decimal(1))])

    assert b._derivs[LINEAR].last_seen_ns == NOW_NS
