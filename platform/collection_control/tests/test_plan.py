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
            InstrumentEntry(id="A", store_order_book_deltas=True, retain_hours=48.0),
            InstrumentEntry(id="B", store_order_book_deltas=True),
            InstrumentEntry(id="C"),
        ),
    )
    assert plan.delta_store_ids == {"A", "B"}
    assert plan.delta_retain_hours == {"A": 48.0, "B": None}
    assert plan.pins == plan.collected == ("A", "B", "C")
    assert plan.free_slots == 27


# -- add -----------------------------------------------------------------------------------------


def test_add_appends_and_lifts_the_id_out_of_exclude() -> None:
    diff = _plan("A", excluded=frozenset({"B"})).add("B")
    assert diff.plan.collected == ("A", "B")
    assert diff.plan.excluded == frozenset()
    assert (diff.added, diff.removed) == ({"B"}, frozenset())


def test_add_of_a_collected_id_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="already collected"):
        _plan("A").add("A")


def test_add_at_the_cap_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="at 2-instrument cap"):
        _plan("A", "B", cap=2).add("C")


# -- remove --------------------------------------------------------------------------------------


def test_remove_drops_the_entry_without_excluding_it() -> None:
    diff = _plan("A", "B").remove("A")
    assert diff.plan.collected == ("B",)
    assert diff.plan.excluded == frozenset()
    assert diff.removed == {"A"}


def test_remove_of_an_uncollected_id_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="not currently collected"):
        _plan("A").remove("B")


# -- exclude / unpin -----------------------------------------------------------------------------


def test_exclude_of_a_collected_id_also_stops_collecting_it() -> None:
    diff = _plan("A").exclude("A")
    assert (diff.plan.collected, diff.plan.excluded, diff.removed) == ((), {"A"}, {"A"})


def test_exclude_of_an_uncollected_id_only_denylists_it() -> None:
    diff = _plan("A").exclude("B")
    assert (diff.plan.collected, diff.plan.excluded, diff.changed) == (("A",), {"B"}, False)


def test_unpin_stops_collecting_and_excludes() -> None:
    diff = _plan("A", "B").unpin("A")
    assert (diff.plan.collected, diff.plan.excluded, diff.removed) == (("B",), {"A"}, {"A"})


def test_unpin_of_an_uncollected_id_is_rejected() -> None:
    with pytest.raises(PlanRejected, match="Cannot unpin"):
        _plan("A").unpin("B")


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
    plan = _plan("P0", "P1", cap=4)
    volumes = {f"C{i}": float(1_000_000 - i) for i in range(10)}
    diff = plan.pin(classify_liquidity(_markets(volumes), _MIN_USD))
    assert diff.plan.collected == ("P0", "P1", "C0-PERP.DYDX", "C1-PERP.DYDX")


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
    diff = _plan("A", "B").reload(_plan("B", "C"))
    assert (diff.added, diff.removed, diff.plan.collected) == ({"C"}, {"A"}, ("B", "C"))


def test_reload_of_another_venues_plan_is_rejected() -> None:
    other = dataclasses.replace(_plan(), venue="BYBIT")
    with pytest.raises(PlanRejected, match="BYBIT"):
        _plan().reload(other)
