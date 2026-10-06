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
`cascade_rules` (Story 33.14): every branch of `should_enter` and `should_exit` on hand-built
states. The base state is a follow entry that passes every rule: an initialized, active, rising,
unspent episode of longs being liquidated (direction -1), 10 s old, 50 000 USDT of notional, flat,
no entry yet, no exit ever, no loss today.
"""

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from nautilus_trader.model.enums import OrderSide
from research.strategies.cascade_rules import ENTER_FADE
from research.strategies.cascade_rules import ENTER_FOLLOW
from research.strategies.cascade_rules import EXIT_MAX_HOLD
from research.strategies.cascade_rules import EXIT_SPENT
from research.strategies.cascade_rules import EXIT_STOP
from research.strategies.cascade_rules import EXIT_TAKE_PROFIT
from research.strategies.cascade_rules import CascadeState
from research.strategies.cascade_rules import PositionView
from research.strategies.cascade_rules import RulesConfig
from research.strategies.cascade_rules import daily_loss_breached
from research.strategies.cascade_rules import entry_reason
from research.strategies.cascade_rules import should_enter
from research.strategies.cascade_rules import should_exit


_S = 1_000_000_000
_START = 1_000 * _S


def _state(**changes: Any) -> CascadeState:
    base = CascadeState(
        initialized=True,
        active=True,
        rising=True,
        spent=False,
        direction=-1,
        episode_direction=-1,
        episode_start_ns=_START,
        now_ns=_START + 10 * _S,
        bid=Decimal("100.00"),
        ask=Decimal("100.10"),
        ofi=-3.0,
        episode_notional=Decimal(50_000),
        entries_this_episode=0,
        last_exit_ns=None,
        daily_realized=Decimal(0),
        position_open=False,
    )
    return replace(base, **changes)


def _config(**changes: Any) -> RulesConfig:
    base = RulesConfig(
        sides=("short",),
        mode=ENTER_FOLLOW,
        min_episode_notional=Decimal(10_000),
        entry_timeout_s=60,
        max_entries_per_episode=1,
        cooldown_s=300,
        take_profit_r=None,
        exit_on_spent=True,
        max_hold_s=1_800,
        ofi_confirm=False,
        max_daily_loss=None,
    )
    return replace(base, **changes)


# --- entry: follow -----------------------------------------------------------------------------


def test_follow_sells_into_a_long_liquidation_cascade() -> None:
    assert should_enter(_state(), _config()) == OrderSide.SELL


def test_follow_buys_into_a_short_liquidation_cascade() -> None:
    state = _state(direction=1, episode_direction=1)
    assert should_enter(state, _config(sides=("long",))) == OrderSide.BUY


def test_follow_with_both_sides_takes_either() -> None:
    both = _config(sides=("short", "long"))
    assert should_enter(_state(), both) == OrderSide.SELL
    assert should_enter(_state(direction=1, episode_direction=1), both) == OrderSide.BUY


@pytest.mark.parametrize(
    ("state_changes", "config_changes"),
    [
        ({}, {"sides": ("long",)}),  # a short is not allowed
        ({"initialized": False}, {}),
        ({"active": False}, {}),
        ({"rising": False}, {}),
        ({"spent": True}, {}),
        ({"direction": 0}, {}),
        # A brief flip to short liquidations inside a long-liquidation episode: never enter long.
        ({"direction": 1}, {"sides": ("short", "long")}),
        ({"episode_start_ns": None}, {}),
        ({"now_ns": _START + 61 * _S}, {}),  # timed out: 61 s > 60 s
        ({"episode_notional": Decimal("9999.99")}, {}),  # under the minimum
        ({"entries_this_episode": 1}, {}),  # max entries reached
        ({"last_exit_ns": _START + 10 * _S - 299 * _S}, {}),  # cooldown: 299 s < 300 s
        ({"daily_realized": Decimal(-500)}, {"max_daily_loss": Decimal(500)}),  # at the limit
        ({"daily_realized": Decimal(-501)}, {"max_daily_loss": Decimal(500)}),
        ({"ofi": 2.0}, {"ofi_confirm": True}),  # buying pressure against a SELL
        ({"ofi": 0.0}, {"ofi_confirm": True}),
        ({"ofi": None}, {"ofi_confirm": True}),  # OFI not initialized
        ({"position_open": True}, {}),
    ],
)
def test_follow_refuses(state_changes: dict[str, Any], config_changes: dict[str, Any]) -> None:
    assert should_enter(_state(**state_changes), _config(**config_changes)) is None


def test_follow_edges_that_still_enter() -> None:
    assert should_enter(_state(now_ns=_START + 60 * _S), _config()) == OrderSide.SELL  # 60 s
    assert should_enter(_state(episode_notional=Decimal(10_000)), _config()) == OrderSide.SELL
    cooled = _state(last_exit_ns=_START + 10 * _S - 300 * _S)  # exactly 300 s ago
    assert should_enter(cooled, _config()) == OrderSide.SELL
    loss = _config(max_daily_loss=Decimal(500))
    assert should_enter(_state(daily_realized=Decimal("-499.99")), loss) == OrderSide.SELL


def test_ofi_that_agrees_confirms_both_sides() -> None:
    confirm = _config(ofi_confirm=True, sides=("short", "long"))
    assert should_enter(_state(ofi=-0.5), confirm) == OrderSide.SELL
    rising_prices = _state(direction=1, episode_direction=1, ofi=0.5)
    assert should_enter(rising_prices, confirm) == OrderSide.BUY


# --- entry: fade -------------------------------------------------------------------------------


def test_fade_buys_a_spent_long_liquidation_cascade() -> None:
    spent = _state(spent=True, active=False, rising=False, direction=0)
    assert should_enter(spent, _config(mode=ENTER_FADE, sides=("long",))) == OrderSide.BUY


def test_fade_sells_a_spent_short_liquidation_cascade() -> None:
    spent = _state(spent=True, rising=False, direction=1, episode_direction=1)
    assert should_enter(spent, _config(mode=ENTER_FADE)) == OrderSide.SELL


@pytest.mark.parametrize(
    "state_changes",
    [
        {"spent": False, "rising": False},  # not spent yet
        {"spent": True, "rising": True},  # spent, but the wave resurges: never buy into it
        {"spent": True, "rising": False, "episode_direction": 0},  # no episode
        {"spent": True, "rising": False, "position_open": True},
        {"spent": True, "rising": False, "entries_this_episode": 1},
        {"spent": True, "rising": False, "episode_notional": Decimal("9999.99")},  # minimum
    ],
)
def test_fade_refuses(state_changes: dict[str, Any]) -> None:
    assert should_enter(_state(**state_changes), _config(mode=ENTER_FADE, sides=("long",))) is None


def test_fade_refuses_a_side_it_may_not_take() -> None:
    spent = _state(spent=True, rising=False)
    assert should_enter(spent, _config(mode=ENTER_FADE, sides=("short",))) is None


def test_fade_ignores_the_follow_only_gates() -> None:
    # Timed out and falling, at exactly the minimum notional: a fade still enters on the spent
    # state (the timeout gates follow only; the minimum notional gates both modes; `rising` gates
    # both, follow requiring it and fade refusing it).
    late = _state(
        spent=True, rising=False, now_ns=_START + 600 * _S, episode_notional=Decimal(10_000)
    )
    assert should_enter(late, _config(mode=ENTER_FADE, sides=("long",))) == OrderSide.BUY


def test_the_entry_reason_is_the_mode() -> None:
    assert entry_reason(_config()) == ENTER_FOLLOW
    assert entry_reason(_config(mode=ENTER_FADE)) == ENTER_FADE


def test_the_daily_loss_is_breached_at_the_limit_only_when_one_is_set() -> None:
    assert not daily_loss_breached(_state(daily_realized=Decimal(-(10**9))), _config())
    limit = _config(max_daily_loss=Decimal(100))
    assert daily_loss_breached(_state(daily_realized=Decimal(-100)), limit)
    assert not daily_loss_breached(_state(daily_realized=Decimal("-99.99")), limit)


# --- exits -------------------------------------------------------------------------------------

# A short opened at 100.00 with a 2.00 stop distance: the stop is at 102.00, judged at the ask; a
# take-profit of 1.5 R is at 100.00 - 3.00 = 97.00.
_SHORT = PositionView(
    side="short", entry_price=Decimal("100.00"), stop_distance=Decimal(2), opened_ns=_START
)
# A long opened at 100.00 with a 2.00 stop distance: the stop is at 98.00, judged at the bid; a
# take-profit of 1.5 R is at 103.00.
_LONG = PositionView(
    side="long", entry_price=Decimal("100.00"), stop_distance=Decimal(2), opened_ns=_START
)
_HELD = {"position_open": True}


def test_a_short_stops_when_the_ask_reaches_the_stop() -> None:
    assert should_exit(_state(ask=Decimal("102.00"), **_HELD), _SHORT, _config()) == EXIT_STOP
    assert should_exit(_state(ask=Decimal("101.99"), **_HELD), _SHORT, _config()) is None


def test_a_short_takes_profit_when_the_ask_reaches_the_target() -> None:
    target = _config(take_profit_r=Decimal("1.5"))
    assert should_exit(_state(ask=Decimal("97.00"), **_HELD), _SHORT, target) == EXIT_TAKE_PROFIT
    assert should_exit(_state(ask=Decimal("97.01"), **_HELD), _SHORT, target) is None
    # Without a take-profit, no price below the entry exits.
    assert should_exit(_state(ask=Decimal("50.00"), **_HELD), _SHORT, _config()) is None


def test_a_long_stops_and_takes_profit_at_the_bid() -> None:
    target = _config(take_profit_r=Decimal("1.5"), mode=ENTER_FADE)
    assert should_exit(_state(bid=Decimal("98.00"), **_HELD), _LONG, target) == EXIT_STOP
    assert should_exit(_state(bid=Decimal("98.01"), **_HELD), _LONG, target) is None
    assert should_exit(_state(bid=Decimal("103.00"), **_HELD), _LONG, target) == EXIT_TAKE_PROFIT
    # The long is judged at the bid: an ask through the stop is not the long's exit price.
    assert should_exit(_state(bid=Decimal(99), ask=Decimal(97), **_HELD), _LONG, target) is None


def test_a_follow_position_exits_when_spent() -> None:
    assert should_exit(_state(spent=True, **_HELD), _SHORT, _config()) == EXIT_SPENT


def test_spent_does_not_exit_without_exit_on_spent_or_in_fade() -> None:
    spent = _state(spent=True, **_HELD)
    assert should_exit(spent, _SHORT, _config(exit_on_spent=False)) is None
    assert should_exit(spent, _LONG, _config(mode=ENTER_FADE)) is None


def test_a_position_exits_after_its_max_hold() -> None:
    held = _state(now_ns=_START + 1_800 * _S, **_HELD)
    assert should_exit(held, _SHORT, _config()) == EXIT_MAX_HOLD
    assert should_exit(replace(held, now_ns=held.now_ns - 1), _SHORT, _config()) is None


def test_the_exit_priority_is_stop_take_profit_spent_hold() -> None:
    late = _START + 1_800 * _S
    every = _state(ask=Decimal("102.00"), spent=True, now_ns=late, **_HELD)
    assert should_exit(every, _SHORT, _config()) == EXIT_STOP
    profit = replace(every, ask=Decimal("97.00"))
    assert should_exit(profit, _SHORT, _config(take_profit_r=Decimal("1.5"))) == EXIT_TAKE_PROFIT
    assert should_exit(replace(every, ask=Decimal("100.00")), _SHORT, _config()) == EXIT_SPENT
    calm = replace(every, ask=Decimal("100.00"), spent=False)
    assert should_exit(calm, _SHORT, _config()) == EXIT_MAX_HOLD


def test_a_missing_quote_skips_only_the_price_exits() -> None:
    no_quote = _state(bid=None, ask=None, **_HELD)
    assert should_exit(no_quote, _SHORT, _config()) is None
    assert should_exit(replace(no_quote, spent=True), _SHORT, _config()) == EXIT_SPENT
