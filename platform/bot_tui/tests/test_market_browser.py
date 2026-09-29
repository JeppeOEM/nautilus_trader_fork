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
Tests for bot_tui.market_browser (Story 29.5): the search, every rule of the row marker's
precedence, every add refusal (dYdX's cap refuses; Bybit has none even at 100 rows) and the group
header.
"""

import pytest

from bot_tui.collector_pane import VenueSection
from bot_tui.market_browser import ADD_ANSWER_TIMEOUT_SECONDS
from bot_tui.market_browser import AddContext
from bot_tui.market_browser import BrowserGroup
from bot_tui.market_browser import BrowserRow
from bot_tui.market_browser import add_refusal
from bot_tui.market_browser import format_group_header
from bot_tui.market_browser import format_result_line
from bot_tui.market_browser import result_marker
from bot_tui.market_browser import search


_NOW = 1_800_000_000.0
_SOL = "SOLUSDT-LINEAR.BYBIT"
_MARKETS = {
    "HYPERLIQUID": [("SOL-USD-PERP.HYPERLIQUID", "SOL"), ("BTC-USD-PERP.HYPERLIQUID", "BTC")],
    "BYBIT": [
        ("SOLUSDT-SPOT.BYBIT", "SOL"),
        ("BTCUSDT-LINEAR.BYBIT", "BTC"),
        (_SOL, "SOL"),
        ("SOLAYERUSDT-LINEAR.BYBIT", "SOLAYER"),
    ],
    "DYDX": [("BTC-USD-PERP.DYDX", "BTC")],
}


def _ctx(
    status_row: dict | None = None,
    plan: dict | None = None,
    refused: str | None = None,
    sent_at: float | None = None,
    instrument_id: str = _SOL,
) -> AddContext:
    return AddContext(instrument_id, "BYBIT", status_row, plan, sent_at, refused)


def _pending_plan_with_failed_apply() -> dict:
    return {"last_apply": {"ts": 1_800_000_000_000_000_000, "failed": [_SOL]}}


# -- search ----------------------------------------------------------------------------------------


def test_search_trims_and_casefolds_and_matches_symbol_or_id_across_venues() -> None:
    groups = search(_MARKETS, " sol ")
    assert [g.venue for g in groups] == ["BYBIT", "DYDX", "HYPERLIQUID"]
    assert [r.instrument_id for r in groups[0].rows] == [
        "SOLAYERUSDT-LINEAR.BYBIT",
        _SOL,
        "SOLUSDT-SPOT.BYBIT",
    ]
    assert groups[1].rows == []  # searched, no match: the group stays, empty
    assert groups[2].rows == [BrowserRow("SOL-USD-PERP.HYPERLIQUID", "SOL")]


def test_search_matches_the_id_even_where_the_symbol_does_not() -> None:
    groups = search(_MARKETS, "LINEAR")
    assert [r.instrument_id for r in groups[0].rows] == [
        "BTCUSDT-LINEAR.BYBIT",
        "SOLAYERUSDT-LINEAR.BYBIT",
        _SOL,
    ]


def test_an_empty_query_lists_everything_sorted() -> None:
    groups = search(_MARKETS, "  ")
    assert sum(len(g.rows) for g in groups) == 7
    assert [r.instrument_id for r in groups[2].rows] == [
        "BTC-USD-PERP.HYPERLIQUID",
        "SOL-USD-PERP.HYPERLIQUID",
    ]


# -- result_marker, one test per precedence rule -------------------------------------------------


def test_a_status_row_not_pending_is_collected_over_every_later_rule() -> None:
    ctx = _ctx({"id": _SOL}, {"unpinned_ids": [_SOL]}, "Cannot start: taken", _NOW - 1)
    assert result_marker(ctx, _NOW) == "collected"


def test_a_pending_status_row_is_pending() -> None:
    assert result_marker(_ctx({"id": _SOL, "pending": True}), _NOW) == "pending"


def test_a_pending_row_the_last_apply_failed_to_subscribe_says_so() -> None:
    ctx = _ctx({"id": _SOL, "pending": True}, _pending_plan_with_failed_apply())
    assert result_marker(ctx, _NOW) == (
        "pending · failed: subscribe failed in last apply 2027-01-15 08:00:00Z, retrying"
    )


def test_a_refusal_of_this_tui_s_add_is_failed_with_its_reason() -> None:
    ctx = _ctx(refused="Cannot start: taken", sent_at=_NOW - 5)
    assert result_marker(ctx, _NOW) == "failed: Cannot start: taken"


def test_a_refused_reason_without_an_outstanding_add_is_not_shown() -> None:
    assert result_marker(_ctx(refused="Cannot start: taken"), _NOW) == ""


def test_an_unpinned_id_is_excluded_even_with_an_add_awaiting() -> None:
    assert result_marker(_ctx(plan={"unpinned_ids": [_SOL]}, sent_at=_NOW), _NOW) == "excluded"


def test_an_add_within_the_answer_timeout_is_pending() -> None:
    ctx = _ctx(sent_at=_NOW - ADD_ANSWER_TIMEOUT_SECONDS)
    assert result_marker(ctx, _NOW) == "pending"


def test_an_add_past_the_answer_timeout_has_no_answer() -> None:
    ctx = _ctx(sent_at=_NOW - ADD_ANSWER_TIMEOUT_SECONDS - 1)
    assert result_marker(ctx, _NOW) == "no answer from BYBIT collector"


def test_an_id_nothing_knows_about_has_no_marker() -> None:
    assert result_marker(_ctx(plan={"unpinned_ids": []}), _NOW) == ""


# -- add_refusal ---------------------------------------------------------------------------------


def _refused(ctx: AddContext, cap: int | None = None, count: int = 0) -> str | None:
    return add_refusal(ctx, _NOW, venue_refusal=None, cap=cap, count=count)


def test_the_venue_s_own_refusal_comes_first() -> None:
    reason = "waiting for BYBIT plan on collector:status"
    refused = add_refusal(_ctx({"id": _SOL}), _NOW, venue_refusal=reason, cap=None, count=0)
    assert refused == reason


@pytest.mark.parametrize(
    ("ctx", "reason"),
    [
        (_ctx({"id": _SOL}), "already collected"),
        (_ctx({"id": _SOL, "pending": True}), "already in the plan (pending)"),
        (_ctx(plan={"unpinned_ids": [_SOL]}), "excluded (unpinned): re-add with :start <ID>"),
        (_ctx(sent_at=_NOW - 1), "add already sent, waiting for collector:status"),
    ],
)
def test_each_row_state_refuses_the_add_with_its_reason(ctx: AddContext, reason: str) -> None:
    assert _refused(ctx) == reason


def test_dydx_s_full_cap_refuses_the_add() -> None:
    ctx = AddContext("NEW-USD-PERP.DYDX", "DYDX", None, {"cap": 30}, None, None)  # nothing sent
    assert _refused(ctx, cap=30, count=30) == "cap reached (30)"
    assert _refused(ctx, cap=30, count=29) is None


def test_bybit_has_no_cap_even_at_100_rows() -> None:
    assert _refused(_ctx(plan={"cap": None}), cap=None, count=100) is None


def test_an_add_past_its_timeout_may_be_sent_again() -> None:
    assert _refused(_ctx(sent_at=_NOW - ADD_ANSWER_TIMEOUT_SECONDS - 1)) is None


def test_an_add_the_collector_refused_may_be_sent_again() -> None:
    assert _refused(_ctx(refused="Cannot start: taken", sent_at=_NOW - 1)) is None


# -- formatting ----------------------------------------------------------------------------------


def _section(*rows: dict, cap: object = None) -> VenueSection:
    return VenueSection("BYBIT", list(rows), {"unpinned_ids": [], "cap": cap})


def test_the_group_header_is_the_collector_header_plus_the_match_count() -> None:
    group = BrowserGroup(
        "BYBIT", [BrowserRow(_SOL, "SOL"), BrowserRow("SOLUSDT-SPOT.BYBIT", "SOL")]
    )
    section = _section({"id": _SOL}, {"id": "BTCUSDT-LINEAR.BYBIT", "pending": True})
    assert format_group_header(group, section, plan_stale=False, markets_stale=False) == (
        "BYBIT: 1 collected +1 pending · no cap · 2 matches"
    )


def test_a_stale_markets_list_marks_the_header_once() -> None:
    group = BrowserGroup("DYDX", [])
    section = VenueSection("DYDX", [], {"unpinned_ids": [], "cap": 30})
    assert format_group_header(group, section, plan_stale=False, markets_stale=True) == (
        "~ DYDX: 0 collected +0 pending · cap 30 · 0 matches"
    )
    assert format_group_header(group, section, plan_stale=True, markets_stale=True) == (
        "~ DYDX: 0 collected +0 pending · cap 30 · 0 matches"
    )


def test_a_result_line_fits_its_id_and_prefixes_a_stale_venue() -> None:
    assert format_result_line(BrowserRow(_SOL, "SOL"), "collected", stale=False) == (
        f"  {_SOL:<28} collected"
    )
    long_id = "1000000BABYDOGEUSDT-LINEAR.BYBIT"
    assert format_result_line(BrowserRow(long_id, "X"), "", stale=True) == f"~ {long_id[:27]}…"
