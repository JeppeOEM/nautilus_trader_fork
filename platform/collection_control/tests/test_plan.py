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
"""`CollectionPlan`: one invariant test per command, and the invariants asserted at construction."""

import dataclasses

import pytest

from collection_control.domain.liquidity import classify_liquidity
from collection_control.domain.plan import CollectionPlan
from collection_control.domain.plan import InstrumentEntry
from collection_control.domain.plan import PlanRejected


_MIN_USD = 100_000.0


def _plan(*ids: str, excluded: frozenset[str] = frozenset(), cap: int = 30) -> CollectionPlan:
    return CollectionPlan(
        venue="DYDX",
        instruments=tuple(InstrumentEntry(id=iid) for iid in ids),
        cap=cap,
        excluded=excluded,
        min_liquidity_usd=_MIN_USD,
        non_config_retain_hours=4.0,
    )


def _markets(volumes: dict[str, float]) -> dict:
    return {"markets": {t: {"ticker": t, "volume24H": v} for t, v in volumes.items()}}


# -- construction invariants -------------------------------------------------------------------


def test_a_repeated_id_is_refused_naming_it() -> None:
    with pytest.raises(ValueError, match=r"A-PERP\.DYDX"):
        _plan("A-PERP.DYDX", "A-PERP.DYDX")


def test_more_instruments_than_the_cap_is_refused_naming_the_overflow() -> None:
    with pytest.raises(ValueError, match=r"C2-PERP\.DYDX"):
        _plan("C0-PERP.DYDX", "C1-PERP.DYDX", "C2-PERP.DYDX", cap=2)


def test_an_id_both_collected_and_excluded_is_refused_naming_it() -> None:
    with pytest.raises(ValueError, match=r"A-PERP\.DYDX"):
        _plan("A-PERP.DYDX", excluded=frozenset({"A-PERP.DYDX"}))


def test_a_negative_retain_hours_is_refused() -> None:
    with pytest.raises(ValueError, match="retain_hours must be >= 0 for instrument 'A'"):
        InstrumentEntry(id="A", store_order_book_deltas=True, retain_hours=-1.0)


def test_an_empty_id_is_refused() -> None:
    with pytest.raises(ValueError, match="non-empty id"):
        InstrumentEntry(id="")


def test_the_retention_attributes_archive_reads() -> None:
    plan = dataclasses.replace(
        _plan(),
        instruments=(
            InstrumentEntry(id="A.DYDX", store_order_book_deltas=True, retain_hours=48.0),
            InstrumentEntry(id="B.DYDX", store_order_book_deltas=True),
            InstrumentEntry(id="C.DYDX"),
        ),
    )
    assert plan.delta_store_ids == {"A.DYDX", "B.DYDX"}
    assert plan.delta_retain_hours == {"A.DYDX": 48.0, "B.DYDX": None}
    assert plan.pins == plan.collected == ("A.DYDX", "B.DYDX", "C.DYDX")
    assert plan.free_slots == 27


# -- add -----------------------------------------------------------------------------------------


def test_add_appends_and_lifts_the_id_out_of_exclude() -> None:
    diff = _plan("A.DYDX", excluded=frozenset({"B.DYDX"})).add("B.DYDX")
    assert diff.plan.collected == ("A.DYDX", "B.DYDX")
    assert diff.plan.excluded == frozenset()
    assert (diff.added, diff.removed) == ({"B.DYDX"}, frozenset())


def test_add_of_a_collected_id_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="already collected"):
        _plan("A.DYDX").add("A.DYDX")


def test_add_at_the_cap_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="at 2-instrument cap"):
        _plan("A.DYDX", "B.DYDX", cap=2).add("C.DYDX")


# -- remove --------------------------------------------------------------------------------------


def test_remove_drops_the_entry_without_excluding_it() -> None:
    diff = _plan("A.DYDX", "B.DYDX").remove("A.DYDX")
    assert diff.plan.collected == ("B.DYDX",)
    assert diff.plan.excluded == frozenset()
    assert diff.removed == {"A.DYDX"}


def test_remove_of_an_uncollected_id_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="not currently collected"):
        _plan("A.DYDX").remove("B.DYDX")


# -- exclude / unpin -----------------------------------------------------------------------------


def test_exclude_of_a_collected_id_also_stops_collecting_it() -> None:
    diff = _plan("A.DYDX").exclude("A.DYDX")
    assert (diff.plan.collected, diff.plan.excluded, diff.removed) == ((), {"A.DYDX"}, {"A.DYDX"})


def test_exclude_of_an_uncollected_id_only_denylists_it() -> None:
    diff = _plan("A.DYDX").exclude("B.DYDX")
    assert (diff.plan.collected, diff.plan.excluded, diff.changed) == (
        ("A.DYDX",),
        {"B.DYDX"},
        False,
    )


def test_unpin_stops_collecting_and_excludes() -> None:
    diff = _plan("A.DYDX", "B.DYDX").unpin("A.DYDX")
    assert (diff.plan.collected, diff.plan.excluded, diff.removed) == (
        ("B.DYDX",),
        {"A.DYDX"},
        {"A.DYDX"},
    )


def test_unpin_of_an_uncollected_id_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="Cannot unpin"):
        _plan("A.DYDX").unpin("B.DYDX")


# -- pin -----------------------------------------------------------------------------------------


def test_pin_appends_the_top_liquid_ids_sorted_and_keeps_every_entry() -> None:
    plan = _plan("PIN-USD-PERP.DYDX")
    classification = classify_liquidity(
        _markets({"AAA": 500_000.0, "BBB": 400_000.0, "LOW": 50.0}), _MIN_USD
    )
    diff = plan.pin(classification)
    assert diff.plan.collected == ("PIN-USD-PERP.DYDX", "AAA-PERP.DYDX", "BBB-PERP.DYDX")
    assert diff.plan.instruments[0] is plan.instruments[0]  # never rebuilt
    assert diff.added == {"AAA-PERP.DYDX", "BBB-PERP.DYDX"}


def test_pin_never_re_adds_an_excluded_id_nor_duplicates_a_collected_one() -> None:
    plan = _plan("BBB-PERP.DYDX", excluded=frozenset({"AAA-PERP.DYDX"}))
    diff = plan.pin(classify_liquidity(_markets({"AAA": 9e6, "BBB": 9e6}), _MIN_USD))
    assert diff.plan == plan
    assert diff.added == frozenset()


def test_pin_fills_only_the_free_slots_with_the_highest_volumes() -> None:
    plan = _plan("P0.DYDX", "P1.DYDX", cap=4)
    volumes = {f"C{i}": float(1_000_000 - i) for i in range(10)}
    diff = plan.pin(classify_liquidity(_markets(volumes), _MIN_USD))
    assert diff.plan.collected == ("P0.DYDX", "P1.DYDX", "C0-PERP.DYDX", "C1-PERP.DYDX")


def test_pin_from_a_classification_at_another_threshold_is_rejected() -> None:
    classification = classify_liquidity(_markets({"AAA": 9e6}), _MIN_USD / 2)
    with pytest.raises(PlanRejected, match="classification at the plan's"):
        _plan().pin(classification)


def test_a_plan_without_a_threshold_admits_no_pin() -> None:
    plan = dataclasses.replace(_plan(), min_liquidity_usd=None)
    with pytest.raises(PlanRejected, match="admits no pins"):
        plan.pin(classify_liquidity(_markets({"AAA": 9e6}), _MIN_USD))


# -- reload --------------------------------------------------------------------------------------


def test_reload_diffs_the_collected_sets() -> None:
    diff = _plan("A.DYDX", "B.DYDX").reload(_plan("B.DYDX", "C.DYDX"))
    assert (diff.added, diff.removed, diff.plan.collected) == (
        {"C.DYDX"},
        {"A.DYDX"},
        ("B.DYDX", "C.DYDX"),
    )


def test_reload_of_another_venues_plan_is_rejected() -> None:
    other = dataclasses.replace(_plan(), venue="BYBIT")
    with pytest.raises(PlanRejected, match="BYBIT"):
        _plan().reload(other)


# -- uncapped plans (Bybit, Hyperliquid: Story 29.4) --------------------------------------------


def _uncapped(*ids: str) -> CollectionPlan:
    return CollectionPlan(
        venue="BYBIT", instruments=tuple(InstrumentEntry(id=iid) for iid in ids), cap=None
    )


def test_an_uncapped_plan_adds_past_any_size() -> None:
    plan = _uncapped(*(f"C{i}USDT-LINEAR.BYBIT" for i in range(200)))
    diff = plan.add("NEWUSDT-LINEAR.BYBIT")
    assert (len(diff.plan.collected), diff.plan.cap, diff.plan.free_slots) == (201, None, None)


def test_a_liquidity_threshold_without_a_cap_is_refused() -> None:
    with pytest.raises(ValueError, match="liquidity threshold but no cap"):
        CollectionPlan(venue="BYBIT", instruments=(), cap=None, min_liquidity_usd=_MIN_USD)


def test_an_uncapped_plan_admits_no_pin() -> None:
    classification = classify_liquidity(_markets({"AAA": 500_000.0}), _MIN_USD)
    with pytest.raises(PlanRejected, match="admits no pins"):
        _uncapped().pin(classification)


@pytest.mark.parametrize(
    ("ids", "excluded"),
    [(("SOL-USD-PERP.HYPERLIQUID",), frozenset()), ((), frozenset({"BTCUSDT-SPOT.BYBIT"}))],
)
def test_an_id_of_another_venue_is_refused_at_construction(
    ids: tuple[str, ...], excluded: frozenset[str]
) -> None:
    with pytest.raises(ValueError, match="ids of another venue"):
        _plan(*ids, excluded=excluded)
