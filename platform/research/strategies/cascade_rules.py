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
The liquidation cascade strategy's decisions (Story 33.14), as two pure functions of one frozen
state: `should_enter` and `should_exit`. `LiquidationCascadeStrategy` only gathers the state from
`kernel.indicators.LiquidationCascade`, its quotes and its own bookkeeping, and acts on what these
return, so a backtest, the paper bot and a test decide identically. Imports only the standard
library and `nautilus_trader.model.enums`.

**Side mapping (audit D-147).** The cascade's `direction` -1 means longs are being liquidated
(forced sells, the price is falling), +1 shorts. `follow` trades with the forced flow (-1 is a
SELL, opening a short; +1 a BUY), `fade` against it once the episode is spent (-1 is a BUY, +1 a
SELL). `sides` names the *position* sides allowed, `"short"` and/or `"long"`.

**Entry** (`should_enter`), refused (None) unless the detector is initialized, nothing is open or
working, the episode's entries are under `max_entries_per_episode`, `cooldown_s` has passed since
the last exit and the UTC day's realised PnL is above `-max_daily_loss`; then:

- in both modes the episode's notional is at least `min_episode_notional` (below it the episode
  is ignored);
- `follow`: the detector is `active` and `rising`, the episode is not `spent` and at most
  `entry_timeout_s` old, and the current `direction` is the episode's (a brief flip inside a
  long-liquidation episode never enters long); the side follows that direction;
- `fade`: the episode is `spent` and the rate is not `rising` again (never buy into a resurging
  wave); the side opposes `episode_direction`;

and the position side must be in `sides`, and with `ofi_confirm` the top-of-book OFI must be known
and agree (negative for a SELL, positive for a BUY).

**Exit** (`should_exit`), in priority order, the exit price being the bid for a long and the ask
for a short: the stop (`entry -/+ stop_distance` reached), the take-profit (`entry +/- take_profit_r
* stop_distance` reached, when set), `spent` (with `exit_on_spent`, `follow` only: a fade position
is entered on the spent state itself) and `max_hold_s` held. A missing quote skips the two price
exits, never guesses a price.

**The cascade gate of another strategy** (Story 33.13, `OFIStrategy`'s `liquidation_cascade_mode`):
`next_phase` folds the detector's updates into one `CascadePhase` -- `quiet`, `building` (an
episode is open), `unwinding` (it ended, the total rate still at or above the baseline) and
`fade_window` (the first `window_s` seconds after the rate fell under the baseline) -- and
`cascade_allows` says whether an entry side passes: `follow` lets only the forced-flow side through
while the cascade builds and rises, `fade` only the opposite side inside the fade window, and both
suppress every other entry while a cascade is detected. The gate only filters: the other strategy
still decides its entries.

Known limit: the fade window lasts the detector's own `window_s` (no separate parameter), an
approximation of "once the rate has decayed under the baseline"; upgrade path: a dedicated
`fade_window_s` in the config, passed to `next_phase` instead.
"""

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.model.enums import OrderSide


ENTER_FOLLOW = "follow"
ENTER_FADE = "fade"
EXIT_STOP = "stop"
EXIT_TAKE_PROFIT = "take_profit"
EXIT_SPENT = "spent"
EXIT_MAX_HOLD = "max_hold"

MODES = (ENTER_FOLLOW, ENTER_FADE)
# The cascade gate's mode that never filters (`cascade_allows`), beside `MODES`.
MODE_OFF = "off"
PHASE_QUIET = "quiet"
PHASE_BUILDING = "building"
PHASE_UNWINDING = "unwinding"
PHASE_FADE_WINDOW = "fade_window"
SIDE_LONG = "long"
SIDE_SHORT = "short"
SIDES = (SIDE_SHORT, SIDE_LONG)

# Local on purpose: this module imports only the standard library and `nautilus_trader.model.enums`
# (the spec's purity rule), so it does not import `kernel.clocks.NS_PER_S`; the value is the same.
_NS_PER_S = 1_000_000_000


@dataclass(frozen=True)
class CascadeState:
    """
    Everything one decision reads, at one update of the detector.

    Invariant: the indicator fields are `LiquidationCascade`'s outputs at `now_ns`; `bid`/`ask` are
    the latest quote's (None before the first); `ofi` is None while the OFI is not initialized;
    `episode_notional` is the episode's notional in quote currency (exact); `position_open` is True
    while a position is open or an order of the strategy is working.
    """

    initialized: bool
    active: bool
    rising: bool
    spent: bool
    direction: int
    episode_direction: int
    episode_start_ns: int | None
    now_ns: int
    bid: Decimal | None
    ask: Decimal | None
    ofi: float | None
    episode_notional: Decimal
    entries_this_episode: int
    last_exit_ns: int | None
    daily_realized: Decimal
    position_open: bool


@dataclass(frozen=True)
class PositionView:
    """
    The open position as the exits see it: `side` (`"long"`/`"short"`), exact prices, open time.
    """

    side: str
    entry_price: Decimal
    stop_distance: Decimal
    opened_ns: int


@dataclass(frozen=True)
class RulesConfig:
    """The strategy config's rule fields (validated by the strategy's config check)."""

    sides: tuple[str, ...]
    mode: str
    min_episode_notional: Decimal
    entry_timeout_s: int
    max_entries_per_episode: int
    cooldown_s: int
    take_profit_r: Decimal | None
    exit_on_spent: bool
    max_hold_s: int
    ofi_confirm: bool
    max_daily_loss: Decimal | None


def daily_loss_breached(state: CascadeState, config: RulesConfig) -> bool:
    """Return whether the UTC day's realised PnL is at or below `-max_daily_loss`."""
    limit = config.max_daily_loss
    return limit is not None and state.daily_realized <= -limit


def _may_enter(state: CascadeState, config: RulesConfig) -> bool:
    """Return False on a refusal common to both modes."""
    if not state.initialized or state.position_open:
        return False
    if state.entries_this_episode >= config.max_entries_per_episode:
        return False
    cooling = state.last_exit_ns is not None and (
        state.now_ns - state.last_exit_ns < config.cooldown_s * _NS_PER_S
    )
    if cooling:
        return False
    return not daily_loss_breached(state, config)


def _follow_side(state: CascadeState, config: RulesConfig) -> OrderSide | None:
    if not (state.active and state.rising) or state.spent or state.episode_start_ns is None:
        return None
    if state.now_ns - state.episode_start_ns > config.entry_timeout_s * _NS_PER_S:
        return None
    if state.direction != state.episode_direction:
        return None  # a flip inside the episode: never follow the minority side
    return _side_of(state.direction)


def _fade_side(state: CascadeState) -> OrderSide | None:
    if not state.spent or state.rising:
        return None  # not spent yet, or a resurging wave: never fade into it
    return _side_of(-state.episode_direction)


def _side_of(direction: int) -> OrderSide | None:
    if direction < 0:
        return OrderSide.SELL
    return OrderSide.BUY if direction > 0 else None


def _ofi_agrees(side: OrderSide, ofi: float | None) -> bool:
    if ofi is None:
        return False
    return ofi < 0 if side == OrderSide.SELL else ofi > 0


def should_enter(state: CascadeState, config: RulesConfig) -> OrderSide | None:
    """Return the entry order side the state calls for, or None (the module docstring's rules)."""
    if not _may_enter(state, config):
        return None
    if state.episode_notional < config.min_episode_notional:
        return None
    if config.mode == ENTER_FOLLOW:
        side = _follow_side(state, config)
    else:
        side = _fade_side(state)
    if side is None:
        return None
    if (SIDE_SHORT if side == OrderSide.SELL else SIDE_LONG) not in config.sides:
        return None
    if config.ofi_confirm and not _ofi_agrees(side, state.ofi):
        return None
    return side


def entry_reason(config: RulesConfig) -> str:
    """Return the reason an entry of this config is logged with: its mode."""
    return ENTER_FOLLOW if config.mode == ENTER_FOLLOW else ENTER_FADE


def _price_exit(state: CascadeState, position: PositionView, config: RulesConfig) -> str | None:
    """Return the stop, then the take-profit, judged at the bid (long) or the ask (short)."""
    long = position.side == SIDE_LONG
    price = state.bid if long else state.ask
    if price is None:
        return None
    sign = 1 if long else -1
    adverse = (position.entry_price - price) * sign
    if adverse >= position.stop_distance:
        return EXIT_STOP
    target = config.take_profit_r
    if target is not None and -adverse >= target * position.stop_distance:
        return EXIT_TAKE_PROFIT
    return None


def should_exit(state: CascadeState, position: PositionView, config: RulesConfig) -> str | None:
    """Return the reason the open position must close now, or None (in the docstring's priority)."""
    reason = _price_exit(state, position, config)
    if reason is not None:
        return reason
    if config.exit_on_spent and config.mode == ENTER_FOLLOW and state.spent:
        return EXIT_SPENT
    if state.now_ns - position.opened_ns >= config.max_hold_s * _NS_PER_S:
        return EXIT_MAX_HOLD
    return None


@dataclass(frozen=True)
class CascadeView:
    """
    What `next_phase` reads of `LiquidationCascade` after one update.

    Invariant: every field is the detector's own output at that update: `episode_start_ns` is set
    while an episode is shown (open, or on its `episode_ended` update), `total_rate` is `rate_long
    + rate_short` and `baseline` the detector's baseline, both in notional units per second.
    """

    episode_start_ns: int | None
    episode_ended: bool
    episode_direction: int
    rising: bool
    spent: bool
    total_rate: float
    baseline: float


@dataclass(frozen=True)
class CascadePhase:
    """
    Where the cascade gate stands: `kind` (`quiet`, `building`, `unwinding`, `fade_window`), the
    episode's `direction` (-1 longs liquidated, +1 shorts; 0 when quiet), its `rising`/`spent` at
    the last update and `since_ns`: the episode's start while `building`, else when the phase
    began (None when quiet).

    Invariant: produced only by `next_phase` (or `QUIET`), so a `building` phase always belongs to
    the detector's open episode. A `fade_window` is a value, not a timer: it ends at the first
    `next_phase` call at or after `since_ns + window_s`, so it is never *read* past its window by a
    caller that runs `next_phase` at the decision's own time before `cascade_allows` --
    `OFIStrategy` advances the detector and calls `next_phase` at every snapshot before it gates
    that snapshot's entry, so after a gap in the feed the window closes at the first snapshot
    after it (`test_ofi_strategy_forced_flow`'s gap test).
    """

    kind: str
    direction: int
    rising: bool
    spent: bool
    since_ns: int | None


QUIET = CascadePhase(PHASE_QUIET, 0, False, False, None)


def _episode_open(view: CascadeView) -> bool:
    return view.episode_start_ns is not None and not view.episode_ended


def _after_episode(previous: CascadePhase, view: CascadeView, now_ns: int) -> CascadePhase:
    """`unwinding` while the total rate holds at or above the baseline, then the fade window."""
    if view.total_rate >= view.baseline:
        since = previous.since_ns if previous.kind == PHASE_UNWINDING else now_ns
        return CascadePhase(PHASE_UNWINDING, previous.direction, view.rising, True, since)
    return CascadePhase(PHASE_FADE_WINDOW, previous.direction, view.rising, True, now_ns)


def next_phase(
    previous: CascadePhase, view: CascadeView, now_ns: int, window_s: int
) -> CascadePhase:
    """
    Return the phase after one detector update at `now_ns` (the module docstring's machine): an
    open episode is always `building` (a new start included, from any phase); after it ends,
    `unwinding` while the total rate is at or above the baseline; at the first update under it the
    `fade_window`, which lasts `window_s` seconds from its start; then `quiet`. An already-ended
    episode seen from `quiet` (its start and end fell between two calls) moves to `unwinding`.
    """
    if _episode_open(view):
        assert view.episode_start_ns is not None  # an open episode has a start
        return CascadePhase(
            PHASE_BUILDING, view.episode_direction, view.rising, view.spent, view.episode_start_ns
        )
    if previous.kind in (PHASE_BUILDING, PHASE_UNWINDING):
        return _after_episode(previous, view, now_ns)
    if view.episode_start_ns is not None and previous.kind == PHASE_QUIET:
        # An episode the gate never saw open (started and ended between two calls): it still
        # happened, so it unwinds rather than reading as quiet.
        return CascadePhase(PHASE_UNWINDING, view.episode_direction, view.rising, True, now_ns)
    if previous.kind == PHASE_FADE_WINDOW:
        assert previous.since_ns is not None  # a fade window has a start
        if now_ns - previous.since_ns < window_s * _NS_PER_S:
            return previous
    return QUIET


def cascade_allows(mode: str, side: OrderSide, phase: CascadePhase) -> bool:
    """
    Return whether an entry on `side` passes the gate of `mode` in `phase`: `off` and a `quiet`
    phase always pass; `follow` passes only the forced-flow side (`_side_of(direction)`) while
    `building`, rising and not spent; `fade` passes only the opposite side
    (`_side_of(-direction)`) in the `fade_window`. Every other case is suppressed -- including a
    phase whose `direction` is 0 (the detector's `episode_direction` is 0 until the first non-zero
    direction after the start, e.g. while both sides' rates are equal): `_side_of(0)` names no side,
    so `follow` and `fade` block every entry until the direction becomes non-zero.
    """
    if mode == MODE_OFF or phase.kind == PHASE_QUIET:
        return True
    if mode == ENTER_FOLLOW:
        building = phase.kind == PHASE_BUILDING and phase.rising and not phase.spent
        return building and side == _side_of(phase.direction)
    return phase.kind == PHASE_FADE_WINDOW and side == _side_of(-phase.direction)
