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
`RetentionPolicy`, pure: every rule and its reason, the open-day exclusion, unlimited retention,
the midnight margin, unparsable names -- and the cases of the deleted `DydxCollector._prune_loop`
(`_prune_candidates`, `_prune_delta_retention`) as policy cases.
"""

import math

import pytest
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S

from archive.domain.archive_day import ArchiveDay
from archive.domain.archive_day import DayStatus
from archive.domain.retention import CatalogFile
from archive.domain.retention import PlanRetention
from archive.domain.retention import RetentionPolicy
from archive.domain.retention import day_text
from archive.domain.retention import file_days


_HOUR = 3_600 * NS_PER_S
_TODAY = 20_400  # a UTC day index; "now" is noon of it
_NOW = _TODAY * NS_PER_DAY + 12 * _HOUR
_BYBIT = "BTCUSDT-LINEAR.BYBIT"


def _file(data_type: str, iid: str, start: int, end: int) -> CatalogFile:
    return CatalogFile(data_type, iid, f"{data_type}/{iid}/{start}_{end}.parquet", (start, end))


def _day_file(data_type: str, iid: str, day: int) -> CatalogFile:
    """Build a file inside one UTC day, past the midnight margin."""
    start = day * NS_PER_DAY + MAX_TS_INIT_SKEW_NS + NS_PER_S
    return _file(data_type, iid, start, day * NS_PER_DAY + 23 * _HOUR)


def _verified(iid: str, *days: int, status: DayStatus = DayStatus.VERIFIED) -> dict:
    return {(iid, d): ArchiveDay("BYBIT", iid, day_text(d), status) for d in days}


def test_trade_rule_releases_only_old_verified_days() -> None:
    policy = RetentionPolicy(_NOW, trade_retention_days=7)
    unverified = _day_file("trade_tick", _BYBIT, _TODAY - 10)
    failed = _day_file("trade_tick", _BYBIT, _TODAY - 9)
    passed = _day_file("trade_tick", _BYBIT, _TODAY - 8)
    young = _day_file("trade_tick", _BYBIT, _TODAY - 3)
    days = {
        **_verified(_BYBIT, _TODAY - 9, status=DayStatus.MISMATCHED),
        **_verified(_BYBIT, _TODAY - 8, _TODAY - 3),
    }
    files = [unverified, failed, passed, young]
    assert policy.status_days(files) == {(_BYBIT, _TODAY - d) for d in (10, 9, 8)}
    decision = policy.decide(files, days)
    assert [(d.file, d.rule) for d in decision.delete] == [(passed, "trade")]
    assert decision.kept == [
        (_BYBIT, day_text(_TODAY - 10), "unverified"),
        (_BYBIT, day_text(_TODAY - 9), "failed"),
    ]


def test_a_file_starting_inside_the_midnight_margin_also_needs_the_previous_day() -> None:
    start = (_TODAY - 9) * NS_PER_DAY + MAX_TS_INIT_SKEW_NS - NS_PER_S
    early = _file("trade_tick", _BYBIT, start, start + _HOUR)
    assert list(file_days((start, start + _HOUR))) == [_TODAY - 10, _TODAY - 9]
    policy = RetentionPolicy(_NOW, trade_retention_days=7)
    kept = policy.decide([early], _verified(_BYBIT, _TODAY - 9))
    assert (kept.delete, kept.kept) == ([], [(_BYBIT, day_text(_TODAY - 10), "unverified")])
    both = policy.decide([early], _verified(_BYBIT, _TODAY - 10, _TODAY - 9))
    assert [d.file for d in both.delete] == [early]


def test_a_file_ending_inside_the_midnight_margin_also_needs_the_next_day() -> None:
    """A venue clock ahead of ours stamps a trade's `ts_event` past its `ts_init`."""
    midnight = (_TODAY - 9) * NS_PER_DAY
    end = midnight - 2 * 60 * NS_PER_S  # 23:58 of the day before: within the bound of midnight
    late = _file("trade_tick", _BYBIT, end - _HOUR, end)
    assert list(file_days((end - _HOUR, end))) == [_TODAY - 10, _TODAY - 9]
    policy = RetentionPolicy(_NOW, trade_retention_days=7)
    kept = policy.decide([late], _verified(_BYBIT, _TODAY - 10))
    assert (kept.delete, kept.kept) == ([], [(_BYBIT, day_text(_TODAY - 9), "unverified")])
    both = policy.decide([late], _verified(_BYBIT, _TODAY - 10, _TODAY - 9))
    assert [d.file for d in both.delete] == [late]


def test_a_file_spanning_two_days_needs_both_verified() -> None:
    spanning = _file(
        "trade_tick", _BYBIT, (_TODAY - 10) * NS_PER_DAY - NS_PER_S, (_TODAY - 10) * NS_PER_DAY + 1
    )
    policy = RetentionPolicy(_NOW, trade_retention_days=7)
    decision = policy.decide([spanning], _verified(_BYBIT, _TODAY - 11))
    assert decision.kept == [(_BYBIT, day_text(_TODAY - 10), "unverified")]


def test_a_missing_status_is_unverified_never_assumed_proven() -> None:
    policy = RetentionPolicy(_NOW, trade_retention_days=7)
    decision = policy.decide([_day_file("trade_tick", _BYBIT, _TODAY - 20)], {})
    assert decision.delete == []


def test_age_rule_deletes_old_files_of_the_named_types_only() -> None:
    policy = RetentionPolicy(_NOW, age_types=frozenset({"order_book_deltas"}), age_days=14)
    old = _day_file("order_book_deltas", _BYBIT, _TODAY - 15)
    young = _day_file("order_book_deltas", _BYBIT, _TODAY - 13)
    other = _day_file("custom_dydx_second_snapshot", _BYBIT, _TODAY - 30)
    trades = _day_file("trade_tick", _BYBIT, _TODAY - 30)
    decision = policy.decide([old, young, other, trades], {})
    assert [(d.file, d.rule) for d in decision.delete] == [(old, "age")]


_PLAN = PlanRetention(
    collected=frozenset({"BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"}),
    non_config_retain_hours=4.0,
    delta_retain_hours={"BTC-USD-PERP.DYDX": None, "ETH-USD-PERP.DYDX": 48.0},
)


def test_a_dropped_dydx_instrument_loses_every_type_but_trades() -> None:
    """`_prune_candidates`: a known market no longer collected is a prune candidate."""
    policy = RetentionPolicy(_NOW, plan=_PLAN)
    stopped = "STOPPED-USD-PERP.DYDX"
    snapshots = _day_file("custom_dydx_second_snapshot", stopped, _TODAY - 1)
    deltas = _day_file("order_book_deltas", stopped, _TODAY - 1)
    trades = _day_file("trade_tick", stopped, _TODAY - 1)
    collected = _day_file("custom_dydx_second_snapshot", "BTC-USD-PERP.DYDX", _TODAY - 1)
    other_venue = _day_file("custom_dydx_second_snapshot", _BYBIT, _TODAY - 1)
    decision = policy.decide([snapshots, deltas, trades, collected, other_venue], {})
    assert {(d.file, d.rule) for d in decision.delete} == {
        (snapshots, "dropped_instrument"),
        (deltas, "dropped_instrument"),
    }


def test_a_dropped_instrument_file_younger_than_the_window_is_kept() -> None:
    policy = RetentionPolicy(_NOW + 30 * _HOUR, plan=_PLAN)  # tomorrow 18:00: file is 21 h old
    recent = _file(
        "custom_dydx_second_snapshot",
        "STOPPED-USD-PERP.DYDX",
        _TODAY * NS_PER_DAY + 20 * _HOUR,
        _TODAY * NS_PER_DAY + 21 * _HOUR,
    )
    assert [d.rule for d in policy.decide([recent], {}).delete] == ["dropped_instrument"]
    assert RetentionPolicy(_NOW + 13 * _HOUR, plan=_PLAN).decide([recent], {}).delete == []


def test_delta_retention_prunes_a_finite_window_and_never_an_unlimited_one() -> None:
    """`_prune_delta_retention`: `retain_hours=None` means unlimited, never pruned."""
    policy = RetentionPolicy(_NOW, plan=_PLAN)
    unlimited = _day_file("order_book_deltas", "BTC-USD-PERP.DYDX", _TODAY - 30)
    finite_old = _day_file("order_book_deltas", "ETH-USD-PERP.DYDX", _TODAY - 3)
    finite_young = _day_file("order_book_deltas", "ETH-USD-PERP.DYDX", _TODAY - 1)
    snapshots = _day_file("custom_dydx_second_snapshot", "ETH-USD-PERP.DYDX", _TODAY - 30)
    decision = policy.decide([unlimited, finite_old, finite_young, snapshots], {})
    assert [(d.file, d.rule) for d in decision.delete] == [(finite_old, "delta_retention")]


def test_nothing_reaching_the_current_utc_day_is_ever_chosen() -> None:
    policy = RetentionPolicy(
        _NOW,
        trade_retention_days=0,
        age_types=frozenset({"order_book_deltas"}),
        age_days=0,
        plan=PlanRetention(frozenset(), 0.0, {}),
    )
    morning = (_TODAY * NS_PER_DAY + _HOUR, _TODAY * NS_PER_DAY + 2 * _HOUR)
    crossing = (_TODAY * NS_PER_DAY - _HOUR, _TODAY * NS_PER_DAY + _HOUR)
    files = [
        _file(data_type, "X-USD-PERP.DYDX", *span)
        for data_type in ("order_book_deltas", "custom_dydx_second_snapshot", "trade_tick")
        for span in (morning, crossing)
    ]
    assert policy.decide(files, {}).delete == []


def test_an_unparsable_name_is_kept_and_reported() -> None:
    odd = CatalogFile("order_book_deltas", _BYBIT, "order_book_deltas/x/not-a-span.parquet", None)
    policy = RetentionPolicy(_NOW, age_types=frozenset({"order_book_deltas"}), age_days=0)
    decision = policy.decide([odd], {})
    assert (decision.delete, decision.unparsable) == ([], [odd])


def test_every_deletion_names_its_rule_and_reason() -> None:
    policy = RetentionPolicy(_NOW, trade_retention_days=7, plan=_PLAN)
    trades = _day_file("trade_tick", _BYBIT, _TODAY - 8)
    dropped = _day_file("custom_dydx_second_snapshot", "GONE-USD-PERP.DYDX", _TODAY - 2)
    decision = policy.decide([trades, dropped], _verified(_BYBIT, _TODAY - 8))
    assert [(d.rule, d.reason) for d in decision.delete] == [
        ("trade", "verified and older than 7 days"),
        ("dropped_instrument", "not collected, older than 4.0 h"),
    ]


def test_an_instrument_captured_today_is_never_dropped_whatever_the_plan_says() -> None:
    """A torn plan read (in-place rewrite of the bind-mounted file) must not delete history."""
    policy = RetentionPolicy(_NOW, plan=PlanRetention(frozenset(), 4.0, {}))  # reads as "none"
    live = "BTC-USD-PERP.DYDX"
    old = _day_file("custom_dydx_second_snapshot", live, _TODAY - 3)
    todays_trades = _file("trade_tick", live, _TODAY * NS_PER_DAY, _TODAY * NS_PER_DAY + _HOUR)
    gone = _day_file("custom_dydx_second_snapshot", "GONE-USD-PERP.DYDX", _TODAY - 3)
    decision = policy.decide([old, todays_trades, gone], {})
    assert [d.file for d in decision.delete] == [gone]


@pytest.mark.parametrize(
    ("non_config", "retain"),
    [(-1.0, None), (math.inf, None), (math.nan, None), (4.0, -0.5), (4.0, math.inf)],
)
def test_a_retention_window_must_be_finite_non_negative_hours(
    non_config: float, retain: float | None
) -> None:
    with pytest.raises(ValueError, match="finite hours >= 0"):
        PlanRetention(frozenset(), non_config, {"ETH-USD-PERP.DYDX": retain, "X.DYDX": None})


def test_zero_and_unlimited_windows_are_valid() -> None:
    PlanRetention(frozenset(), 0.0, {"ETH-USD-PERP.DYDX": 0.0, "BTC-USD-PERP.DYDX": None})


def test_the_midnight_files_b_and_c_are_never_chosen_on_the_night_they_are_written() -> None:
    """
    The nightly for day D runs at 00:30 of D+1. File B (23:59:02 D -> 00:00:01 D+1, capture's
    00:00:02 flush) and file C (00:00:02.5 -> 00:01:01 D+1, holding D's last row) both reach the
    open day: every rule leaves them, however aggressive, while a closed file of D goes.
    """
    day = _TODAY - 1
    now = _TODAY * NS_PER_DAY + 30 * 60 * NS_PER_S
    midnight = _TODAY * NS_PER_DAY
    spans = {
        "a": (day * NS_PER_DAY + 12 * _HOUR, day * NS_PER_DAY + 13 * _HOUR),
        "b": (midnight - 58 * NS_PER_S, midnight + NS_PER_S),
        "c": (midnight + 2_500_000_000, midnight + 61 * NS_PER_S),
    }
    policy = RetentionPolicy(
        now,
        trade_retention_days=0,
        age_types=frozenset({"custom_dydx_second_snapshot", "order_book_deltas"}),
        age_days=0,
        plan=PlanRetention(frozenset(), 0.0, {}),
    )
    files = {
        (data_type, iid, name): _file(data_type, iid, *span)
        for data_type in ("custom_dydx_second_snapshot", "order_book_deltas", "trade_tick")
        for iid in (_BYBIT, "GONE-USD-PERP.DYDX")
        for name, span in spans.items()
    }
    days = {**_verified(_BYBIT, day, _TODAY), **_verified("GONE-USD-PERP.DYDX", day, _TODAY)}
    chosen = {d.file.path for d in policy.decide(list(files.values()), days).delete}
    assert not chosen & {f.path for (_, _, name), f in files.items() if name in ("b", "c")}
    # The Bybit leaves' closed file of D goes (the dYdX instrument is "captured today" by B/C).
    assert {
        files[(t, _BYBIT, "a")].path for t in ("custom_dydx_second_snapshot", "trade_tick")
    } <= (chosen)
