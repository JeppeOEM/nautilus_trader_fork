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
The liquidation cascade strategy (Story 33.14): trades `kernel.indicators.LiquidationCascade`
episodes of one Bybit LINEAR instrument, in a backtest (`backtest_liquidation_cascade.py`,
`RunSpec(data="liquidations")`, the `08_strategy_gallery` notebook) and as a paper bot
(`strategy = "liquidation_cascade"` in `bots/config.toml`, resolved by string path, so bots never
imports this module). It imports only `kernel`, `nautilus_trader` and its `research.strategies`
siblings.

**Data.** `Liquidation` rows through `subscribe_data(DataType(Liquidation),
client_id=ClientId(LIQUIDATION_CLIENT_ID), instrument_id=...)` -- live from the bots' Redis bridge
of `liquidations:raw`, in a backtest the archive's `custom_liquidation` (the same client id is the
data config's label) -- and the instrument's quotes (the exit prices, the optional OFI and the
heartbeat). With `stop_atr_multiple`, also `<iid>-1-MINUTE-MID-INTERNAL` bars (or `bar_type`)
built from the quotes, feeding `AverageTrueRange(atr_period)`.

**Feeding.** Each liquidation feeds the detector at its `ts_init` (the receive time, what a live
bot knows; the order a backtest replays) with its notional at the instrument definition's
precisions (`Liquidation.notional_units_at`): a row stored at other precisions is rescaled exactly,
and one that cannot be is recorded in the error ledger (`UNSCALABLE_ROW_SITE`, ERROR), counted in
`unscalable_rows` and not fed (DATA-04, DATA-07). A row received at or before the detector's clock
minus `window_s` cannot belong to the window any more (placing it at the clock, as a late event
is, would count it for a whole window it never was in): it is logged at WARNING, counted in
`stale_rows` and not fed. A 1 s timer on whole UTC seconds advances the detector without an
event, because a cascade is spent when liquidations *stop*. Each update builds one
`cascade_rules.CascadeState`, asks `should_exit` (a position is open) or `should_enter` (flat,
nothing working), acts with a market order, and writes one signal-log record. It never adds to a
position, and one order works at a time.

**Signal log** (`signal_log_path`, opt-in; `bots.strategies.signal_log`'s line format: compact
`json.dumps`, one flushed line per record, an error after close). A `start` record with `strategy:
"liquidation_cascade"`, `bot_id` (the `order_id_tag`), `instrument_id`, `ts_ns` (the clock at
start rounded down to whole microseconds, as `DummyStrategy`'s) and every field of the config
except `signal_log_path`; then one record per update: `kind` (`liquidation` | `tick`), `bot_id`,
`instrument_id`, `ts_ns` (a `liquidation`: the detector's clock, the row's `ts_init` or the clock
if it arrived late; a `tick`: the timer's whole second, even when a row received after that second
was fed before the timer's callback ran, the indicator fields then holding it), `venue_event_id` (`liquidation` only), `rate_long`, `rate_short`,
`baseline`, `intensity`, `direction`, `active`, `spent`, `decision` (`enter_short` |
`enter_long` | `exit` | `none` | `not_ready`) and `reason` (the entry's mode, the exit's reason,
`no_stop` for an entry refused for want of a stop distance, `stale_row` for a row too old for
the window, written but not fed, else None).

**Daily loss.** Realised PnL is summed per UTC day of the position's close; at or below
`-max_daily_loss` no entry is taken for the rest of that day (one INFO line per day). A close
stamped on an earlier UTC day than the current one never moves the day back nor touches today's
sum.

**Entries.** An entry counts against `max_entries_per_episode` when it is submitted; an entry the
risk engine denies or the venue rejects gives its count back (and its ATR distance), so a refused
order never uses up the episode's entry.

Known limit: exits are judged at the detector's updates (at least once a second) and sent as
market orders, never as resting stops, so a stop fills up to a second after its price is crossed,
at the market then. Upgrade path: a reduce-only `STOP_MARKET` kept with the position, as
`CandlePatternStrategy._ensure_stop` keeps one.

Known limit: the notional is size x the **bankruptcy** price (audit D-148), not the fill notional.

Known limit: the signal log grows unrotated for the whole run (`DummyStrategy`'s limit). Upgrade
path: hourly rotation.
"""

import json
import math
from decimal import Decimal
from pathlib import Path
from typing import IO
from typing import Any

import pandas as pd
from kernel.clocks import NS_PER_DAY
from kernel.clocks import NS_PER_S
from kernel.indicators import LiquidationCascade
from kernel.indicators import OrderFlowImbalance
from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import Liquidation
from kernel.liquidation import has_liquidation_feed
from kernel.second_snapshot import SnapshotEncodingError
from observability import error_ledger

from nautilus_trader.common.events import TimeEvent
from nautilus_trader.config import StrategyConfig
from nautilus_trader.core.data import Data
from nautilus_trader.indicators import AverageTrueRange
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.data import DataType
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AggregationSource
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import PositionSide
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.events import OrderDenied
from nautilus_trader.model.events import OrderRejected
from nautilus_trader.model.events import PositionClosed
from nautilus_trader.model.events import PositionOpened
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.position import Position
from nautilus_trader.trading.strategy import Strategy
from research.strategies._bars import check_count
from research.strategies._bars import check_trade_size
from research.strategies._bars import resolve_bar_type
from research.strategies._bars import size_problem
from research.strategies.cascade_rules import MODES
from research.strategies.cascade_rules import SIDE_LONG
from research.strategies.cascade_rules import SIDE_SHORT
from research.strategies.cascade_rules import SIDES
from research.strategies.cascade_rules import CascadeState
from research.strategies.cascade_rules import PositionView
from research.strategies.cascade_rules import RulesConfig
from research.strategies.cascade_rules import daily_loss_breached
from research.strategies.cascade_rules import entry_reason
from research.strategies.cascade_rules import should_enter
from research.strategies.cascade_rules import should_exit


STRATEGY_NAME = "liquidation_cascade"
KIND_LIQUIDATION = "liquidation"
KIND_TICK = "tick"
DECISION_ENTER_SHORT = "enter_short"
DECISION_ENTER_LONG = "enter_long"
DECISION_EXIT = "exit"
DECISION_NONE = "none"
DECISION_NOT_READY = "not_ready"
REASON_NO_STOP = "no_stop"
# A row too old for the window: recorded (so a parity replay that fed it pairs it as a late
# arrival, never a one-sided record) but not fed to the detector.
REASON_STALE_ROW = "stale_row"
# The stop every runner uses when its params name neither stop field (the config requires one):
# `backtest_liquidation_cascade.run` and `research.application.gallery.cascade_specs` both import
# it, so the CLI and the notebook default to the same 1 % stop.
DEFAULT_STOP_PCT = 0.01
# The error-ledger site of a liquidation whose notional the definition's precisions cannot hold.
UNSCALABLE_ROW_SITE = "research.liquidation_cascade.unscalable_row"

_TIMER = "liquidation_cascade_tick"
# Config fields the start record leaves out: the log's own location is host-specific, so a live
# run and its replay would never agree on it.
_UNLOGGED_FIELDS = frozenset({"signal_log_path"})
# Config fields that must be ints >= 1 (the detector's two windows included).
_COUNT_FIELDS = (
    "window_s",
    "baseline_s",
    "entry_timeout_s",
    "max_entries_per_episode",
    "max_hold_s",
    "atr_period",
    "ofi_window",
)


class LiquidationCascadeStrategyConfig(StrategyConfig, frozen=True, forbid_unknown_fields=True):
    """
    Parameters
    ----------
    instrument_id : InstrumentId
        A Bybit LINEAR id (`kernel.liquidation.has_liquidation_feed`).
    window_s / baseline_s / intensity_threshold / decay_ratio
        The `LiquidationCascade` detector's parameters.
    sides : tuple[str, ...]
        The position sides allowed: `"short"` and/or `"long"`.
    mode : str
        `follow` (with the forced flow, while rising) or `fade` (against it, once spent).
    min_episode_notional : Decimal
        The episode notional (quote currency) below which an episode is ignored (both modes).
    entry_timeout_s : int
        The latest a follow entry may come after the episode started, in seconds.
    max_entries_per_episode : int
    cooldown_s : int
        Seconds after an exit before the next entry.
    trade_size : Decimal
        Base-asset quantity per entry.
    stop_pct / stop_atr_multiple : float | None
        Exactly one: the stop distance as a fraction of the entry price, or in ATRs of
        `atr_period` one-minute mid bars.
    take_profit_r : float | None
        The take-profit distance in stop distances; None for none.
    exit_on_spent : bool
        Close a follow position when the episode is spent.
    max_hold_s : int
    ofi_confirm : bool
        Require the top-of-book OFI over `ofi_window` quotes to agree with the entry side.
    max_daily_loss : Decimal | None
        No entry for the rest of a UTC day whose realised PnL is at or below its negative.
    signal_log_path : str | None
        The JSON-lines signal log; None for none.
    bar_type : str | None
        The ATR's bar type; None means `<instrument_id>-1-MINUTE-MID-INTERNAL`. Only a time bar
        of the instrument built from its quotes (`-MID-INTERNAL`) is accepted: the backtest and
        the replay feed quotes, so any other bar would never arrive.

    Unknown fields are rejected (`forbid_unknown_fields`), so a typo'd parameter fails the build.
    """

    instrument_id: InstrumentId
    window_s: int = 30
    baseline_s: int = 3_600
    intensity_threshold: float = 3.0
    decay_ratio: float = 0.5
    sides: tuple[str, ...] = (SIDE_SHORT,)
    mode: str = "follow"
    min_episode_notional: Decimal = Decimal(0)
    entry_timeout_s: int = 60
    max_entries_per_episode: int = 1
    cooldown_s: int = 300
    trade_size: Decimal = Decimal("0.01")
    stop_pct: float | None = None
    stop_atr_multiple: float | None = None
    atr_period: int = 14
    take_profit_r: float | None = None
    exit_on_spent: bool = True
    max_hold_s: int = 1_800
    ofi_confirm: bool = False
    ofi_window: int = 50
    max_daily_loss: Decimal | None = None
    signal_log_path: str | None = None
    bar_type: str | None = None


def _atr_bar_type(config: LiquidationCascadeStrategyConfig) -> BarType | None:
    """
    Return the ATR's bar type (None without `stop_atr_multiple`); `ValueError` for a `bar_type`
    that is not a time bar of the instrument built internally from its quotes' mid.
    """
    if config.bar_type is None and config.stop_atr_multiple is None:
        return None
    text = config.bar_type or f"{config.instrument_id}-1-MINUTE-MID-INTERNAL"
    resolved = resolve_bar_type(config.instrument_id, text)
    if resolved.spec.price_type != PriceType.MID:
        raise ValueError(f"bar_type {text!r} must be built from quotes' mid (-MID-)")
    if resolved.aggregation_source != AggregationSource.INTERNAL:
        raise ValueError(f"bar_type {text!r} must be aggregated internally (-INTERNAL)")
    return resolved if config.stop_atr_multiple is not None else None


def _positive(name: str, value: float | None) -> None:
    if value is not None and not (math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be finite and > 0, was {value}")


def _non_negative_money(name: str, value: Decimal | None) -> None:
    if value is not None and not (Decimal(value).is_finite() and Decimal(value) >= 0):
        raise ValueError(f"{name} must be a finite decimal >= 0, was {value}")


def _check_rules(config: LiquidationCascadeStrategyConfig) -> None:
    if isinstance(config.sides, str) or not config.sides:
        raise ValueError(f"sides must be a non-empty sequence of {SIDES}, was {config.sides!r}")
    unknown = sorted(set(config.sides) - set(SIDES))
    if unknown:
        raise ValueError(f"sides: unknown {unknown}; known: {SIDES}")
    if config.mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {config.mode!r}")
    for name in _COUNT_FIELDS:
        check_count(name, getattr(config, name))
    cooldown = config.cooldown_s
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown < 0:
        raise ValueError(f"cooldown_s must be an int >= 0, was {cooldown!r}")


def _check_money_and_stop(config: LiquidationCascadeStrategyConfig) -> None:
    check_trade_size(config.trade_size)
    _non_negative_money("min_episode_notional", config.min_episode_notional)
    _non_negative_money("max_daily_loss", config.max_daily_loss)
    if (config.stop_pct is None) == (config.stop_atr_multiple is None):
        raise ValueError("set exactly one of stop_pct and stop_atr_multiple")
    _positive("stop_pct", config.stop_pct)
    if config.stop_pct is not None and not config.stop_pct < 1:
        # A stop at or beyond the whole entry price would never trigger on a long.
        raise ValueError(f"stop_pct must be in (0, 1), was {config.stop_pct}")
    _positive("stop_atr_multiple", config.stop_atr_multiple)
    _positive("take_profit_r", config.take_profit_r)


def _decimal(value: float | None) -> Decimal | None:
    """Return a float config value as the decimal it was written as (its shortest repr), exactly."""
    return None if value is None else Decimal(repr(value))


def rules_of(config: LiquidationCascadeStrategyConfig) -> RulesConfig:
    """Return the rule fields of `config` as `cascade_rules.RulesConfig`."""
    loss = config.max_daily_loss
    return RulesConfig(
        sides=tuple(config.sides),
        mode=config.mode,
        min_episode_notional=Decimal(config.min_episode_notional),
        entry_timeout_s=config.entry_timeout_s,
        max_entries_per_episode=config.max_entries_per_episode,
        cooldown_s=config.cooldown_s,
        take_profit_r=_decimal(config.take_profit_r),
        exit_on_spent=config.exit_on_spent,
        max_hold_s=config.max_hold_s,
        ofi_confirm=config.ofi_confirm,
        max_daily_loss=None if loss is None else Decimal(loss),
    )


def _json_value(value: object) -> object:
    if isinstance(value, Decimal | InstrumentId):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def start_fields(config: LiquidationCascadeStrategyConfig) -> dict[str, object]:
    """Return every own config field but `signal_log_path`, JSON-ready, for the `start` record."""
    own = [
        name
        for name in LiquidationCascadeStrategyConfig.__struct_fields__
        if name not in StrategyConfig.__struct_fields__ and name not in _UNLOGGED_FIELDS
    ]
    return {name: _json_value(getattr(config, name)) for name in own}


def _avg_open(position: Position) -> Decimal:
    """
    Return the position's average open price as the decimal of Nautilus's own float (its shortest
    repr): a reader's computation for judging exits, never a stored value.
    """
    return Decimal(repr(position.avg_px_open))


def definition_units(row: Liquidation, instrument: Instrument, skipped_before: int) -> int | None:
    """
    Return the row's notional (`Liquidation.notional_units_at`) at `instrument`'s definition
    precisions, the one feeding rule of `LiquidationCascade` in every strategy (this one's and
    `OFIStrategy`'s cascade mode, Story 33.13). None, recorded in the error ledger at
    `UNSCALABLE_ROW_SITE`, when the definition's precisions cannot hold it exactly (DATA-04): the
    caller counts it (`skipped_before` is its count before this row, for the ledger line) and does
    not feed it, never a rounded value.
    """
    precisions = (instrument.price_precision, instrument.size_precision)
    try:
        return row.notional_units_at(*precisions)
    except SnapshotEncodingError as exc:
        error_ledger.record(
            UNSCALABLE_ROW_SITE,
            f"{row.instrument_id} liquidation {row.venue_event_id} not fed "
            f"({skipped_before + 1} so far): {exc}",
            exc,
        )
        return None


class _SignalLog:
    """
    An append-only JSON-lines file of one bot's decision cycles, in `bots.strategies.signal_log`'s
    line format (research may not import `bots`, `tests/test_boundaries.py`).

    Invariant: every record is one complete flushed line, and nothing is written after `close`.
    Violated by: `write` after `close` (raises).
    """

    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] | None = open(path, "a", encoding="utf-8")  # noqa: SIM115

    def write(self, record: dict[str, Any]) -> None:
        if self._file is None:
            raise RuntimeError("signal log written after close")
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None


class LiquidationCascadeStrategy(Strategy):
    """
    Follows or fades liquidation cascades (the rules are `cascade_rules`, the feeding and the
    limits the module docstring's).

    Invariant: at most one position and one working order; every decision is `should_enter` /
    `should_exit` of the state built at that detector update, and every update writes exactly one
    signal-log record when the log is on. A bad config raises `ValueError` here, before any node
    runs it.
    """

    def __init__(self, config: LiquidationCascadeStrategyConfig) -> None:
        super().__init__(config)
        _check_rules(config)
        _check_money_and_stop(config)
        self._rules = rules_of(config)
        self._cascade = LiquidationCascade(
            config.window_s, config.baseline_s, config.intensity_threshold, config.decay_ratio
        )
        self._ofi = OrderFlowImbalance(config.ofi_window)
        self._atr = AverageTrueRange(config.atr_period)
        self._bar_type = _atr_bar_type(config)
        self.instrument: Instrument | None = None
        # ts of the last market data seen (quotes' `ts_event`, liquidations' `ts_init`), read by
        # the bots' heartbeat (`StrategyCacheReader`).
        self.last_data_ns: int = 0
        # Rows not fed because their notional is not exact at the definition's precisions.
        self.unscalable_rows: int = 0
        # Rows not fed because they were received at or before the clock minus `window_s`.
        self.stale_rows: int = 0
        self._signal_log: _SignalLog | None = None
        self._clear_state()

    def _clear_state(self) -> None:
        self._bid: Decimal | None = None
        self._ask: Decimal | None = None
        self._episode_key: int | None = None
        self._entries = 0
        # The working entry order and the episode it counted against (a denial gives it back).
        self._entry_order_id: ClientOrderId | None = None
        self._entry_episode: int | None = None
        self._last_exit_ns: int | None = None
        self._day: int | None = None
        self._day_realized = Decimal(0)
        self._loss_logged_day: int | None = None
        self._entry_distance: Decimal | None = None
        self._stop_distance: Decimal | None = None

    # --- lifecycle -----------------------------------------------------------------------------

    def on_start(self) -> None:
        iid = self.config.instrument_id
        instrument = self.cache.instrument(iid)
        if instrument is None:
            self.log.error(f"Instrument not found: {iid}")
            self.stop()
            return
        if not has_liquidation_feed(str(iid)):
            self.log.error(f"{iid} has no liquidation feed (Bybit LINEAR only)")
            self.stop()
            return
        if not self._tradeable(instrument):
            self.stop()
            return
        self.instrument = instrument
        self.subscribe_data(
            DataType(Liquidation), client_id=ClientId(LIQUIDATION_CLIENT_ID), instrument_id=iid
        )
        self.subscribe_quote_ticks(iid)
        if self._bar_type is not None:
            self.subscribe_bars(self._bar_type)
        self._start_timer()

    def _tradeable(self, instrument: Instrument) -> bool:
        problem = size_problem(Decimal(self.config.trade_size), instrument)
        if problem is not None:
            self.log.error(f"trade_size {self.config.trade_size} {problem}")
            return False
        return True

    def _start_timer(self) -> None:
        """Open the signal log and start the 1 s timer on whole UTC seconds."""
        now_ns = self.clock.timestamp_ns()
        if self.config.signal_log_path is not None:
            self._signal_log = _SignalLog(self.config.signal_log_path)
            self._signal_log.write(
                {
                    "kind": "start",
                    "strategy": STRATEGY_NAME,
                    "bot_id": self.config.order_id_tag,
                    "instrument_id": str(self.config.instrument_id),
                    # Whole microseconds, as `DummyStrategy`'s start record.
                    "ts_ns": now_ns // 1_000 * 1_000,
                    **start_fields(self.config),
                }
            )
        # The timer's first event is one interval after its start: start on the whole second at or
        # before now, so every tick lands on a whole UTC second, live and in a replay alike.
        self.clock.set_timer(
            name=_TIMER,
            interval=pd.Timedelta(seconds=1),
            start_time=pd.Timestamp(now_ns // NS_PER_S * NS_PER_S, tz="UTC"),
            callback=self.on_timer,
        )

    def on_stop(self) -> None:
        if _TIMER in self.clock.timer_names:
            self.clock.cancel_timer(_TIMER)
        if self._signal_log is not None:
            self._signal_log.close()
            self._signal_log = None

    def on_reset(self) -> None:
        self._cascade.reset()
        self._ofi.reset()
        self._atr.reset()
        self.instrument = None
        self.last_data_ns = 0
        self.unscalable_rows = 0
        self.stale_rows = 0
        self._clear_state()

    # --- data ----------------------------------------------------------------------------------

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.last_data_ns = max(self.last_data_ns, tick.ts_event)
        self._bid = tick.bid_price.as_decimal()
        self._ask = tick.ask_price.as_decimal()
        self._ofi.handle_quote_tick(tick)

    def on_bar(self, bar: Bar) -> None:
        self._atr.handle_bar(bar)

    def on_data(self, data: Data) -> None:
        if not isinstance(data, Liquidation) or self.instrument is None:
            return
        if data.instrument_id != self.config.instrument_id:
            return
        self.last_data_ns = max(self.last_data_ns, data.ts_init)
        if self._stale(data):
            self._write_stale(data)
            return
        units = self.units_of(data)
        if units is None:
            return
        self._cascade.update_liquidation(data.side, units, data.ts_init)
        now_ns = self._cascade.clock_ns
        assert now_ns is not None  # just updated
        self._decide(KIND_LIQUIDATION, {"venue_event_id": data.venue_event_id}, now_ns)

    def on_timer(self, event: TimeEvent) -> None:
        if event.name != _TIMER or self.instrument is None:
            return
        self._cascade.advance(event.ts_event)
        # Recorded on the timer's whole second, never the clock: a row received after that second
        # can be fed before this callback runs (live only), and the tick must still pair by second.
        self._decide(KIND_TICK, {}, event.ts_event)

    def _stale(self, row: Liquidation) -> bool:
        """
        Return True (logged at WARNING, counted in `stale_rows`) for a row received at or before
        the clock minus `window_s`: placed at the clock it would count for a window it never was
        in (an entry placed exactly `window_s` before the clock has just expired).
        """
        clock = self._cascade.clock_ns
        if clock is None or row.ts_init > clock - self.config.window_s * NS_PER_S:
            return False
        self.stale_rows += 1
        self.log.warning(
            f"liquidation {row.venue_event_id} received at {row.ts_init}, at or before the "
            f"window's start ({clock} - {self.config.window_s} s): not fed "
            f"({self.stale_rows} so far)"
        )
        return True

    def _write_stale(self, row: Liquidation) -> None:
        clock = self._cascade.clock_ns
        assert clock is not None  # `_stale` is True only with a clock
        fields: dict[str, object] = {"venue_event_id": row.venue_event_id}
        self._write(KIND_LIQUIDATION, clock, fields, DECISION_NONE, REASON_STALE_ROW)

    def units_of(self, row: Liquidation) -> int | None:
        """
        Return the row's notional at the instrument definition's precisions; None (recorded in
        the error ledger at `UNSCALABLE_ROW_SITE` and counted in `unscalable_rows`) when it is not
        exact there (`definition_units`).
        """
        assert self.instrument is not None  # on_start stopped the strategy otherwise
        units = definition_units(row, self.instrument, self.unscalable_rows)
        if units is None:
            self.unscalable_rows += 1
        return units

    # --- decisions -----------------------------------------------------------------------------

    def _decide(self, kind: str, fields: dict[str, object], record_ns: int) -> None:
        now_ns = self._cascade.clock_ns
        assert now_ns is not None  # just updated
        self._roll_day(now_ns)
        if self._cascade.episode_start_ns != self._episode_key:
            self._episode_key = self._cascade.episode_start_ns
            self._entries = 0
        state = self._state(now_ns)
        position = self._open_position()
        if position is not None and not self._working():
            # Judged even before the detector is warm: a position a restart found open keeps its
            # stop, take-profit and hold exits.
            decision, reason = self._maybe_exit(state, position)
        else:
            decision, reason = self._maybe_enter(state)
        if decision == DECISION_NONE and not self._cascade.initialized:
            decision = DECISION_NOT_READY
        self._write(kind, record_ns, fields, decision, reason)

    def _maybe_exit(self, state: CascadeState, position: Position) -> tuple[str, str | None]:
        view = self._view(position)
        reason = should_exit(state, view, self._rules)
        if reason is None:
            return DECISION_NONE, None
        self.close_position(position)
        return DECISION_EXIT, reason

    def _maybe_enter(self, state: CascadeState) -> tuple[str, str | None]:
        side = should_enter(state, self._rules)
        if side is None:
            if daily_loss_breached(state, self._rules):
                self._log_loss_once(state)
            return DECISION_NONE, None
        distance = self._atr_distance()
        if self.config.stop_atr_multiple is not None and distance is None:
            return DECISION_NONE, REASON_NO_STOP
        self._entry_distance = distance
        self._entry_order_id = self._submit(side)
        self._entry_episode = self._episode_key
        self._entries += 1
        decision = DECISION_ENTER_SHORT if side == OrderSide.SELL else DECISION_ENTER_LONG
        return decision, entry_reason(self._rules)

    def _state(self, now_ns: int) -> CascadeState:
        cascade = self._cascade
        assert self.instrument is not None
        digits = self.instrument.price_precision + self.instrument.size_precision
        return CascadeState(
            initialized=cascade.initialized,
            active=cascade.active,
            rising=cascade.rising,
            spent=cascade.spent,
            direction=cascade.direction,
            episode_direction=cascade.episode_direction,
            episode_start_ns=cascade.episode_start_ns,
            now_ns=now_ns,
            bid=self._bid,
            ask=self._ask,
            ofi=self._ofi.value if self._ofi.initialized else None,
            episode_notional=Decimal(cascade.episode_notional_units).scaleb(-digits),
            entries_this_episode=self._entries,
            last_exit_ns=self._last_exit_ns,
            daily_realized=self._day_realized,
            position_open=self._open_position() is not None or self._working(),
        )

    def _roll_day(self, now_ns: int) -> bool:
        """
        Move to `now_ns`'s UTC day (a new day starts at 0); return False, changing nothing, for a
        day earlier than the current one -- the day never moves back.
        """
        day = now_ns // NS_PER_DAY
        if self._day is not None and day < self._day:
            return False
        if day != self._day:
            self._day, self._day_realized = day, Decimal(0)
        return True

    def _log_loss_once(self, state: CascadeState) -> None:
        if self._loss_logged_day != self._day:
            self._loss_logged_day = self._day
            self.log.info(
                f"daily loss limit reached ({state.daily_realized} <= "
                f"-{self._rules.max_daily_loss}): no entry until the next UTC day"
            )

    # --- orders and positions ------------------------------------------------------------------

    def _submit(self, side: OrderSide) -> ClientOrderId:
        assert self.instrument is not None
        order = self.order_factory.market(
            instrument_id=self.config.instrument_id,
            order_side=side,
            quantity=self.instrument.make_qty(self.config.trade_size),
        )
        self.submit_order(order)
        return order.client_order_id

    def _open_position(self) -> Position | None:
        positions = self.cache.positions_open(
            instrument_id=self.config.instrument_id, strategy_id=self.id
        )
        return positions[0] if positions else None

    def _working(self) -> bool:
        """Return True while an order of this strategy is open or in flight."""
        iid = self.config.instrument_id
        return bool(
            self.cache.orders_open(instrument_id=iid, strategy_id=self.id)
            or self.cache.orders_inflight(instrument_id=iid, strategy_id=self.id)
        )

    def _atr_distance(self) -> Decimal | None:
        """
        Return the ATR stop distance now; None while the ATR is not initialized (or no ATR stop).
        """
        multiple = self.config.stop_atr_multiple
        if multiple is None or not self._atr.initialized or self._atr.value <= 0:
            return None
        return Decimal(repr(multiple * self._atr.value))

    def _view(self, position: Position) -> PositionView:
        if self._stop_distance is None:
            self._stop_distance = self._distance_for(position)
        side = SIDE_LONG if position.side == PositionSide.LONG else SIDE_SHORT
        return PositionView(
            side=side,
            entry_price=_avg_open(position),
            stop_distance=self._stop_distance,
            opened_ns=position.ts_opened,
        )

    def _distance_for(self, position: Position) -> Decimal:
        """`stop_pct` of the entry price, or the ATR distance taken when the entry was sent."""
        entry = _avg_open(position)
        pct = _decimal(self.config.stop_pct)
        if pct is not None:
            return entry * pct
        if self._entry_distance is not None:
            return self._entry_distance
        # After a restart the entry's ATR is gone: the ATR now, else the entry price itself
        # (a stop that never triggers), logged so the position is visibly unprotected.
        # Known limit: in that last case the position is protected only by `max_hold_s` (and the
        # take-profit/spent exits), for up to `max_hold_s` after the restart. Upgrade path: persist
        # the entry's stop distance with the position (or keep a resting reduce-only STOP_MARKET,
        # the stop Known limit's upgrade), so a restart finds the stop the entry was sent with.
        now = self._atr_distance()
        if now is None:
            self.log.error(f"{position.id}: no ATR for its stop, judged without one")
            return entry
        return now

    def on_position_opened(self, event: PositionOpened) -> None:
        self._stop_distance = None
        self._entry_order_id = None  # filled: it keeps its count

    def on_order_denied(self, event: OrderDenied) -> None:
        self._entry_refused(event.client_order_id, f"denied: {event.reason}")

    def on_order_rejected(self, event: OrderRejected) -> None:
        self._entry_refused(event.client_order_id, f"rejected: {event.reason}")

    def _entry_refused(self, order_id: ClientOrderId, why: str) -> None:
        """Give a refused entry's count back to its episode (if still current) and drop its ATR."""
        if order_id != self._entry_order_id:
            return  # an exit order, or an entry already settled
        self._entry_order_id = None
        self._entry_distance = None
        if self._entry_episode == self._episode_key and self._entries > 0:
            self._entries -= 1
        self.log.warning(f"entry {order_id} {why}: it does not count against the episode")

    def on_position_closed(self, event: PositionClosed) -> None:
        self._last_exit_ns = event.ts_event
        # A close stamped on an earlier UTC day than the current one (an event delivered after
        # the clock crossed midnight) belongs to a day already over: it neither moves the day back
        # nor wipes or adds to today's sum, so it never blocks or unblocks today's entries.
        if self._roll_day(event.ts_event):
            self._day_realized += event.realized_pnl.as_decimal()
        self._stop_distance = None
        self._entry_distance = None

    # --- the signal log ------------------------------------------------------------------------

    def _write(
        self,
        kind: str,
        ts_ns: int,
        fields: dict[str, object],
        decision: str,
        reason: str | None,
    ) -> None:
        if self._signal_log is None:
            return
        cascade = self._cascade
        self._signal_log.write(
            {
                "kind": kind,
                "bot_id": self.config.order_id_tag,
                "instrument_id": str(self.config.instrument_id),
                "ts_ns": ts_ns,
                **fields,
                "rate_long": cascade.rate_long,
                "rate_short": cascade.rate_short,
                "baseline": cascade.baseline,
                "intensity": cascade.intensity,
                "direction": cascade.direction,
                "active": cascade.active,
                "spent": cascade.spent,
                "decision": decision,
                "reason": reason,
            }
        )
