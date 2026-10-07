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
`AlertEngine`: evaluates every active alert on its input and fires it.

Two structural observers in one (alerting imports nothing from views): a
`views.live_candles.BarObserver` -- the composition root attaches it to the one `LiveCandleBus`,
which asks `watched_bars()` once per batch and folds a forming bar for each pair it names, chart
listener or not, so alerts fire with no browser tab open, on exactly the bar the chart shows -- and
(Story 33.8) a `views.live_derivs.DerivsObserver` attached to the one `LiveDerivsBus`, which hands
it every decoded `derivs:raw` tick and every `liquidations:raw` batch, listener or not.

What feeds each condition kind (`domain.conditions`):
- `on_bar`: the price kinds, `pct_move` (a per-pair history of closed-bar closes),
  `trendline_cross` (the saved drawing through the `DrawingReader` port), `forced_share`'s volume
  (the per-tick increase of the forming bar's `buy_v + sell_v`, a new bucket counting its whole
  value) and, once per closed bar, every `indicator` alert of the pair -- one read per
  `(instrument_id, bar_seconds, closed bar t)` batching every distinct series, run off the event
  loop through the injected `submit` seam;
- `on_deriv`: `funding_*` (each funding tick) and `oi_change` (a per-instrument open-interest
  series);
- `on_liquidation`: `liquidation_notional` and `forced_share` (a per-instrument liquidation window).

Every bar tick, derivatives tick and liquidation of an instrument is also its event clock: it
closes the `once_per_bar_close` bucket of that instrument's event-fed alerts (funding, open
interest, liquidations) once its time has passed it (`policy.advance`), so a sparse series is
decided when its bucket ends, not when its next sample arrives.
Known limit: an instrument with no input at all -- no watched bar, no mark, index, funding or
open-interest tick, no liquidation -- decides its last bucket only when its next input arrives.
Upgrade path: a wall-clock timer on the loop advancing every such alert once per second.

Known limit (restart): run state and every window are in memory only. After a restart a cross
needs two samples, `pct_move` needs N + 1 closed bars, and `oi_change`, `liquidation_notional` and
`forced_share` are understated until a full window has been observed: the first forming bar seen
after a start (or after a `forced_share` alert starts watching an instrument mid-bucket) only seeds
the volume baseline, so the volume that bucket traded before it is never counted. Upgrade path:
backfill each window from the catalog (open interest, liquidations) and the candle store (volume,
closes) at attach.
"""

import asyncio
import contextlib
import threading
import time
from collections import deque
from decimal import Decimal
from typing import Any

from kernel.derivs_wire import FUNDING
from kernel.derivs_wire import OI
from kernel.derivs_wire import DerivsTick
from kernel.indicators import pct_change
from kernel.indicators import units_ratio
from kernel.liquidation import Liquidation
from observability import error_ledger

from alerting.application.ports import AlertRepository
from alerting.application.ports import Deliverer
from alerting.application.ports import DrawingReader
from alerting.application.ports import Failed
from alerting.application.ports import IndicatorReader
from alerting.application.ports import IndicatorRef
from alerting.application.ports import IndicatorResult
from alerting.application.ports import Missing
from alerting.application.ports import Submit
from alerting.application.windows import Amount
from alerting.application.windows import AsOfSeries
from alerting.application.windows import SumWindow
from alerting.domain.alert import Alert
from alerting.domain.alert import status_of
from alerting.domain.conditions import BAR_KINDS
from alerting.domain.conditions import DERIVS_KINDS
from alerting.domain.conditions import LIQUIDATION_KINDS
from alerting.domain.conditions import describe
from alerting.domain.geometry import trendline_price_at
from alerting.domain.policy import Fire
from alerting.domain.policy import RunState
from alerting.domain.policy import Sample
from alerting.domain.policy import advance
from alerting.domain.policy import render
from alerting.domain.policy import step


# The same bound and drop-oldest policy as `views.rankings_bus.QUEUE_MAX`/`put_drop_oldest`, kept
# equal by value because alerting may not import views (AD-D2): a stalled `/ws/live` client loses
# its oldest toasts rather than growing the queue without bound (MEM-02).
QUEUE_MAX = 1000

INVALID_SITE = "alerting.engine.invalid"
INPUT_SITE = "alerting.engine.input"

_NS_PER_S = 1_000_000_000
_LEVEL_LIKE = frozenset(
    {"price_cross", "price_cross_up", "price_cross_down", "price_above", "price_below"}
)
_FUNDING_KINDS = frozenset({"funding_above", "funding_below"})
_VOLUME_KINDS = frozenset({"forced_share"})
# The event-fed kinds an instrument's other inputs advance (`_clock`).
_CLOCKED_KINDS = DERIVS_KINDS | LIQUIDATION_KINDS
# A liquidation window's components: liquidated size, long notional, short notional.
_SIZE, _LONG, _SHORT = 0, 1, 2

_Pair = tuple[str, int]
_Closed = tuple[int, float]  # a closed bar's `(t, close)`


def _put_drop_oldest(queue: "asyncio.Queue[dict]", item: dict) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


def _dec(value: float) -> Decimal:
    """Return a bar's float price as the decimal it prints as (its exact text never reached it)."""
    return Decimal(repr(float(value)))


def _traded(bar: dict) -> tuple[int, int] | None:
    """Return the forming bar's traded size `buy_v + sell_v` in units and its precision."""
    buy_v, sell_v, precision = bar.get("buy_v"), bar.get("sell_v"), bar.get("size_precision")
    if buy_v is None or sell_v is None or precision is None:
        return None  # the bar's flow is unknown (DATA-01)
    return int(buy_v) + int(sell_v), int(precision)


def _increase(previous: tuple[int, int, int], t: int, traded: tuple[int, int]) -> Amount:
    """Return the volume traded since `previous` (`(t, units, precision)`); a new bucket: whole."""
    units, precision = traded
    prev_t, prev_units, prev_precision = previous
    if prev_t != t:
        return units, precision
    at = max(precision, prev_precision)
    return units * 10 ** (at - precision) - prev_units * 10 ** (at - prev_precision), at


def _running_on(loop: asyncio.AbstractEventLoop) -> bool:
    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False


class AlertEngine:
    """
    Fires saved alerts from their inputs and toasts each fire to every `/ws/live` subscriber.

    Invariant (one evaluation input per kind): an alert is evaluated only on the input its kind
    names (module docstring) -- the bar the bus publishes for its own `(instrument_id,
    bar_seconds)`, its close the price and the producing second's `ts_event` the time; the chart's
    own indicator replay; the decoded derivatives tick or liquidation -- never on a raw snapshot,
    a bar folded here or a second indicator implementation.

    Invariant (bounded state, MEM-01/02): per instrument the open-interest series, the liquidation
    window and the volume window exist only while an active alert of that kind names it, and are
    trimmed to that kind's largest `window_s` on every append; a pair's close history holds at most
    its largest `pct_move.bars + 1` closes; at most one indicator read per pair is in flight;
    `forget` (delete, edit) drops an alert's run state and every window no active alert needs any
    more.

    Invariant (one thread mutates the tables): the observers run on the event loop; `forget`, called
    by the CRUD routes from the threadpool, is marshalled onto that loop, and an alert object an
    edit replaced never reuses the run state of the one it replaced (`_run_state`), so an edit can
    neither corrupt a table mid-iteration nor let a stale previous value cross.

    Invariant (an invalid alert is ledgered once and never evaluated): a gone input (`Missing`, an
    absent output, a vertical trendline) marks the alert invalid through `mark_invalid` and is
    ledgered at `alerting.engine.invalid`; a failed read is ledgered at `alerting.engine.input` and
    skips only that sample.
    """

    def __init__(
        self,
        store: AlertRepository,
        deliverer: Deliverer,
        *,
        indicators: IndicatorReader,
        drawings: DrawingReader,
        submit: Submit,
    ) -> None:
        self._store = store
        self._deliverer = deliverer
        self._indicators = indicators
        self._drawings = drawings
        self._submit = submit
        self._loop: asyncio.AbstractEventLoop | None = None  # the loop the observers run on
        # Per alert id: the alert object the state was built for, and its run state.
        self._state: dict[str, tuple[Alert, RunState]] = {}
        self._listeners: set[asyncio.Queue[dict]] = set()
        # `{{close}}` of a fire whose sample is not a bar's (a derivatives tick, a liquidation):
        # the newest close seen per instrument.
        self._last_close: dict[str, float] = {}
        # Per watched pair: the forming bar's `t` and close, the volume source's last
        # `(t, buy_v + sell_v, size precision)`, the closes of its closed bars (pct_move) and
        # whether an indicator read of it is in flight.
        self._bar_t: dict[_Pair, int] = {}
        self._bar_close: dict[_Pair, float] = {}
        self._bar_volume: dict[_Pair, tuple[int, int, int]] = {}
        self._closes: dict[_Pair, deque[float]] = {}
        self._reads_in_flight: set[_Pair] = set()
        # Per instrument windows (MEM rules above).
        self._oi: dict[str, AsOfSeries] = {}
        self._liquidations: dict[str, SumWindow] = {}
        self._volume: dict[str, SumWindow] = {}

    # --- toasts and lifecycle ------------------------------------------------------------------

    def subscribe(self) -> "asyncio.Queue[dict]":
        """Toast feed for one `/ws/live` connection."""
        queue: asyncio.Queue[dict] = asyncio.Queue(QUEUE_MAX)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[dict]") -> None:
        self._listeners.discard(queue)

    def forget(self, alert_id: str) -> None:
        """
        Drop the alert's run state and every window no remaining active alert needs. Callable from
        any thread: off the observers' running loop it is queued onto it (`call_soon_threadsafe`)
        and runs between two observer calls, never inside one.
        """
        loop = self._loop
        if loop is None or not loop.is_running() or _running_on(loop):
            self._forget_now(alert_id)  # no observer can be running concurrently
            return
        try:
            loop.call_soon_threadsafe(self._forget_now, alert_id)
        except RuntimeError:
            self._forget_now(alert_id)  # the loop closed meanwhile: nothing observes any more

    def _forget_now(self, alert_id: str) -> None:
        self._state.pop(alert_id, None)
        self._prune()

    def bind_loop(self) -> None:
        """
        Remember the loop the observers run on (`forget` marshals onto it). The composition root
        calls it when it attaches the engine, so a `forget` before the first observer call is
        already marshalled; each observer call binds it again, for an engine driven without one.
        """
        # Driven synchronously (tests, tools) there is no loop: `forget` then runs inline.
        with contextlib.suppress(RuntimeError):
            self._loop = asyncio.get_running_loop()

    def _active(self, now_ns: int | None = None) -> list[Alert]:
        """Every alert that can still fire, its expiry judged on the wall clock."""
        now = time.time_ns() if now_ns is None else now_ns
        return [a for a in self._store.list() if status_of(a, now) == "active"]

    def _alerts_for(self, instrument_id: str, kinds: frozenset[str]) -> list[Alert]:
        """
        Return the instrument's alerts of `kinds` that are not invalid or triggered (`step` judges
        expiry on the sample's event time).
        """
        return [
            a
            for a in self._store.list()
            if a.instrument_id == instrument_id
            and a.kind in kinds
            and a.invalid_reason is None
            and not a.triggered
        ]

    def watched_bars(self) -> frozenset[tuple[str, int]]:
        """
        Return the `(instrument_id, bar_seconds)` pairs of every bar-fed alert that can still fire
        (and drop the state of every pair and window no active alert needs, MEM-02).

        Known limit: expiry is judged here on the wall clock, but `step` judges it on the sample's
        `ts_event`, so an alert stops being watched up to the capture->Redis lag (about a second)
        before its last in-window bar is evaluated. Upgrade path: keep a pair watched until an
        event-time `step` has seen the alert expire, once expiries that tight matter.
        """
        self.bind_loop()
        self._prune()
        return frozenset(
            (a.instrument_id, a.bar_seconds) for a in self._active() if a.kind in BAR_KINDS
        )

    def _prune(self) -> None:
        active = self._active()
        pairs = {(a.instrument_id, a.bar_seconds) for a in active if a.kind in BAR_KINDS}
        for table in (self._bar_t, self._bar_close, self._bar_volume, self._closes):
            for pair in [p for p in table if p not in pairs]:
                del table[pair]
        self._keep(self._last_close, {a.instrument_id for a in active})
        self._keep(self._oi, {a.instrument_id for a in active if a.kind == "oi_change"})
        self._keep(
            self._liquidations, {a.instrument_id for a in active if a.kind in LIQUIDATION_KINDS}
        )
        self._keep(self._volume, {a.instrument_id for a in active if a.kind in _VOLUME_KINDS})

    @staticmethod
    def _keep(table: dict[str, Any], ids: set[str]) -> None:
        for key in [k for k in table if k not in ids]:
            del table[key]

    # --- the bar observer ---------------------------------------------------------------------

    def on_bar(self, instrument_id: str, bar_seconds: int, bar: dict, ts_ns: int) -> None:
        """Evaluate the pair's bar-fed alerts on `bar` at `ts_ns`, firing any that hold."""
        pair = (instrument_id, bar_seconds)
        self._last_close[instrument_id] = bar["c"]
        closed = self._roll(pair, bar)
        self._track_volume(pair, bar, ts_ns)
        self._clock(instrument_id, ts_ns)
        if closed is not None:
            self._read_indicators(pair, closed, ts_ns)
        for alert in self._alerts_for(instrument_id, BAR_KINDS):
            if alert.bar_seconds != bar_seconds:
                continue
            sample = self._bar_sample(alert, bar, ts_ns)
            if sample is not None:
                self._step(alert, sample)

    def _roll(self, pair: _Pair, bar: dict) -> _Closed | None:
        """Track the pair's forming bar; return the `(t, close)` of the bar a new bucket closed."""
        previous_t = self._bar_t.get(pair)
        closed = None
        if previous_t is not None and bar["t"] != previous_t:
            closed = (previous_t, self._bar_close[pair])
            self._remember_close(pair, closed[1])
        self._bar_t[pair], self._bar_close[pair] = bar["t"], bar["c"]
        return closed

    def _remember_close(self, pair: _Pair, close: float) -> None:
        depth = max(
            (
                int(a.rule["bars"])
                for a in self._alerts_for(pair[0], frozenset({"pct_move"}))
                if a.bar_seconds == pair[1]
            ),
            default=0,
        )
        if depth == 0:
            self._closes.pop(pair, None)
            return
        closes = self._closes.setdefault(pair, deque())
        closes.append(close)
        while len(closes) > depth + 1:
            closes.popleft()

    def _bar_sample(self, alert: Alert, bar: dict, ts_ns: int) -> Sample | None:
        kind, close = alert.kind, bar["c"]
        if kind in _LEVEL_LIKE or kind == "channel_exit":
            return Sample(close, ts_ns, close=close)
        if kind == "pct_move":
            return self._pct_move_sample(alert, close, ts_ns)
        if kind == "trendline_cross":
            return self._trendline_sample(alert, bar, ts_ns)
        if kind == "forced_share":
            return self._forced_share_sample(alert, self._now(alert.instrument_id, ts_ns), close)
        return None  # indicator: sampled once per closed bar, `_read_indicators`

    def _pct_move_sample(self, alert: Alert, close: float, ts_ns: int) -> Sample | None:
        # The base is the close N closed bars before the newest closed one (the story's matrix:
        # closed closes 100, 105, 110 with N = 2 compare a tick against 100), so the history holds
        # N + 1 closes and there is no sample until it does.
        bars = int(alert.rule["bars"])
        closes = self._closes.get((alert.instrument_id, alert.bar_seconds), deque())
        if len(closes) < bars + 1:
            return None
        change = pct_change(_dec(closes[-(bars + 1)]), _dec(close))
        return None if change is None else Sample(change, ts_ns, close=close)

    def _trendline_sample(self, alert: Alert, bar: dict, ts_ns: int) -> Sample | None:
        drawing_id = alert.rule["drawing_id"]
        try:
            anchors = self._drawings.trendline(alert.instrument_id, drawing_id)
        except Exception as exc:
            # A corrupt or unreadable drawings file skips this sample; it never invalidates.
            error_ledger.record(
                INPUT_SITE, f"alert {alert.id}: drawing {drawing_id} could not be read", exc
            )
            return None
        if isinstance(anchors, Missing):
            self._invalidate(alert, anchors.reason)
            return None
        line = trendline_price_at(anchors, bar["t"] / 1000)
        if line is None:
            self._invalidate(alert, f"trendline {drawing_id} is vertical (equal anchor times)")
            return None
        return Sample(bar["c"] - line, ts_ns, report=bar["c"], close=bar["c"])

    # --- indicators, once per closed bar --------------------------------------------------------

    def _read_indicators(self, pair: _Pair, closed: _Closed, ts_ns: int) -> None:
        """
        Read every indicator series the pair's alerts name at the bar `closed`, off the loop.

        Known limit: at most one read per pair is in flight. A bar that closes while the previous
        bar's read still runs (a read slower than the bar width: a cold catalog under 1 s bars) is
        skipped and ledgered at `alerting.engine.input`, so that bar's indicator crosses are not
        evaluated. Upgrade path: hold the newest skipped bar and read it when the in-flight read
        finishes (still one in flight per pair), or keep an incremental indicator state per pair.
        """
        alerts = [
            a
            for a in self._alerts_for(pair[0], frozenset({"indicator"}))
            if a.bar_seconds == pair[1]
        ]
        refs = list(dict.fromkeys(IndicatorRef.of(a.rule) for a in alerts))
        if not refs:
            return
        instrument_id, bar_seconds = pair
        if pair in self._reads_in_flight:
            error_ledger.record(
                INPUT_SITE,
                f"indicator read of {instrument_id}/{bar_seconds}s at bar t={closed[0]} skipped: "
                "the previous bar's read is still running",
            )
            return

        def job() -> dict[IndicatorRef, IndicatorResult] | Exception:
            try:
                return self._indicators.read(instrument_id, bar_seconds, closed[0], refs)
            except Exception as exc:  # handed to the loop and ledgered there
                return exc

        def done(result: dict[IndicatorRef, IndicatorResult] | Exception) -> None:
            self._reads_in_flight.discard(pair)
            self._on_indicators(pair, closed, ts_ns, result)

        self._reads_in_flight.add(pair)
        try:
            self._submit(job, done)
        except BaseException:
            self._reads_in_flight.discard(pair)  # never submitted: nothing is in flight
            raise

    def _on_indicators(
        self,
        pair: _Pair,
        closed: _Closed,
        ts_ns: int,
        result: dict[IndicatorRef, IndicatorResult] | Exception,
    ) -> None:
        where = f"{pair[0]}/{pair[1]}s at bar t={closed[0]}"
        if isinstance(result, Exception):
            error_ledger.record(INPUT_SITE, f"indicator read of {where} failed", result)
            return
        for ref, reading in result.items():
            if isinstance(reading, Failed):
                error_ledger.record(
                    INPUT_SITE, f"indicator {ref.name} of {where} failed: {reading.message}"
                )
        for alert in self._alerts_for(pair[0], frozenset({"indicator"})):
            if alert.bar_seconds == pair[1]:
                own = result.get(IndicatorRef.of(alert.rule))
                self._indicator_step(alert, own, closed[1], ts_ns)

    def _indicator_step(
        self, alert: Alert, reading: IndicatorResult | None, close: float, ts_ns: int
    ) -> None:
        if reading is None or isinstance(reading, Failed):
            return  # created after the read was submitted, or this read failed (ledgered)
        if isinstance(reading, Missing):
            self._invalidate(alert, reading.reason)
            return
        output = alert.rule["output"]
        if output not in reading.outputs:
            self._invalidate(
                alert, f"output {output!r} is not one of the replay's {sorted(reading.outputs)}"
            )
            return
        cur = reading.cur.get(output)
        if cur is not None:
            prev = reading.prev.get(output)
            self._step(alert, Sample(cur, ts_ns, closed=True, prev=prev, close=close))

    # --- the derivatives observer ----------------------------------------------------------------

    def on_deriv(self, tick: DerivsTick) -> None:
        """Evaluate the instrument's funding or open-interest alerts on one decoded tick."""
        self.bind_loop()
        self._clock(tick.instrument_id, tick.t)
        if tick.kind == FUNDING:
            for alert in self._alerts_for(tick.instrument_id, _FUNDING_KINDS):
                self._step(alert, Sample(tick.value, tick.t))
        elif tick.kind == OI:
            self._on_open_interest(tick)

    def _on_open_interest(self, tick: DerivsTick) -> None:
        alerts = self._alerts_for(tick.instrument_id, frozenset({"oi_change"}))
        if not alerts:
            self._oi.pop(tick.instrument_id, None)
            return
        series = self._oi.setdefault(tick.instrument_id, AsOfSeries())
        if not series.append(tick.t, tick.value):
            return  # a repeat or an older tick (a Redis redelivery): the series stays ordered
        # Keep the newest tick at or before the widest window's start: it is that window's base.
        series.trim(tick.t - max(int(a.rule["window_s"]) for a in alerts) * _NS_PER_S)
        for alert in alerts:
            base = series.at_or_before(tick.t - int(alert.rule["window_s"]) * _NS_PER_S)
            change = None if base is None else pct_change(base, tick.value)
            if change is not None:  # None: the series is younger than the window
                self._step(alert, Sample(change, tick.t))

    def on_liquidation(self, rows: list[Liquidation]) -> None:
        """
        Evaluate the liquidation-fed alerts once per new row, in batch order. A row is also its
        instrument's event clock (`_clock`), as a bar or derivatives tick is.
        """
        self.bind_loop()
        for row in rows:
            instrument_id = row.instrument_id.value
            self._clock(instrument_id, row.ts_event)
            alerts = self._alerts_for(instrument_id, LIQUIDATION_KINDS)
            if not alerts:
                self._liquidations.pop(instrument_id, None)
                continue
            if not self._append_liquidation(instrument_id, row, alerts):
                continue  # a redelivery: counted, and stepped, once
            now = self._now(instrument_id, row.ts_event)
            for alert in alerts:
                sample = self._liquidation_sample(alert, now)
                if sample is not None:
                    self._step(alert, sample)

    def _append_liquidation(
        self, instrument_id: str, row: Liquidation, alerts: list[Alert]
    ) -> bool:
        """Add `row` to the instrument's window; False when it is already there (a redelivery)."""
        window = self._liquidations.setdefault(instrument_id, SumWindow(3))
        notional_precision = row.price_precision + row.size_precision
        notional = (row.notional_units(), notional_precision)
        none = (0, notional_precision)
        is_long = row.side.value == "long"
        amounts = [
            (row.size_units, row.size_precision),
            notional if is_long else none,
            none if is_long else notional,
        ]
        if not window.insert(row.ts_event, amounts, key=row.venue_event_id):
            return False
        newest = window.newest
        if newest is not None:
            widest = max(int(a.rule["window_s"]) for a in alerts)
            window.trim(newest - widest * _NS_PER_S)
        return True

    def _now(self, instrument_id: str, t: int) -> int:
        """
        Return the time a windowed sample (`liquidation_notional`, `forced_share`) is taken at: `t`,
        or the newest event the instrument's liquidation or volume window already holds when that is
        later. A late row (a liquidation whose `ts_event` lags rows already counted, a redelivery, a
        bar tick behind a liquidation) is counted at its own place in the window, but the sample is
        the window as it stands now: a total ending at the late row's time would leave the newer rows
        out, and `step` would keep it as the alert's newest value (the bucket a once_per_bar_close
        alert decides on, the previous value of the next comparison).
        """
        windows = (self._liquidations.get(instrument_id), self._volume.get(instrument_id))
        return max([t, *(w.newest for w in windows if w is not None and w.newest is not None)])

    def _liquidation_sample(self, alert: Alert, t: int) -> Sample | None:
        if alert.kind == "forced_share":
            return self._forced_share_sample(alert, t, None)
        window = self._liquidations.get(alert.instrument_id)
        if window is None:
            return Sample(Decimal(0), t)
        totals = window.total(t - int(alert.rule["window_s"]) * _NS_PER_S, t)
        side = alert.rule.get("side")
        sides = (_LONG, _SHORT) if side is None else ((_LONG,) if side == "long" else (_SHORT,))
        notional = sum((Decimal(totals[c][0]).scaleb(-totals[c][1]) for c in sides), Decimal(0))
        return Sample(notional, t)

    # --- forced share: liquidated size over traded volume -----------------------------------------

    def _track_volume(self, pair: _Pair, bar: dict, ts_ns: int) -> None:
        """
        Append the second's traded volume (the forming bar's increase) to the instrument's volume
        window. One width feeds it -- the narrowest of the instrument's active `forced_share`
        alerts -- so two watched widths of one instrument (a 3600 s price alert beside a 60 s
        `forced_share`) can never both count, or race to count, one second. The first observation
        of the source (a start, a new or edited alert, a source width change) only seeds the
        baseline: an increase needs two observations, and the bucket's earlier volume is not this
        second's (the restart `Known limit:` of the module docstring).

        Known limit: a chart opened on the source pair mid-bucket seeds the bus's buffer with the
        bucket's archived seconds older than the observer's first (`LiveCandleBus.seed`), so the
        next tick's increase also carries their volume, stamped at that tick: it is volume the
        bucket really traded, which the first observation's seed had left out, but counted up to
        one bar width late, so a `window_s` shorter than the bar width can read a share too low.
        Upgrade path: the bus hands observers each second's own traded size, not the forming bar.
        """
        instrument_id, width = pair
        sources = [
            a.bar_seconds
            for a in self._alerts_for(instrument_id, _VOLUME_KINDS)
            if status_of(a, ts_ns) == "active"
        ]
        traded = _traded(bar)
        if traded is None or not sources or width != min(sources):
            self._bar_volume.pop(pair, None)  # not the source: a later switch back re-seeds
            return
        previous = self._bar_volume.get(pair)
        self._bar_volume[pair] = (bar["t"], *traded)
        window = self._volume.setdefault(instrument_id, SumWindow(1))
        newest = window.newest
        if previous is None or (newest is not None and ts_ns <= newest):
            return  # seeding the baseline, or a repeated second
        window.insert(ts_ns, [_increase(previous, bar["t"], traded)])
        widest = max(
            int(a.rule["window_s"]) for a in self._alerts_for(instrument_id, _VOLUME_KINDS)
        )
        window.trim(ts_ns - widest * _NS_PER_S)

    def _forced_share_sample(self, alert: Alert, t: int, close: float | None) -> Sample | None:
        start = t - int(alert.rule["window_s"]) * _NS_PER_S
        volume = self._volume.get(alert.instrument_id)
        liquidations = self._liquidations.get(alert.instrument_id)
        volume_units, volume_p = volume.total(start, t)[0] if volume is not None else (0, 0)
        forced, forced_p = liquidations.total(start, t)[_SIZE] if liquidations else (0, 0)
        share = units_ratio(forced, forced_p, volume_units, volume_p)
        return None if share is None else Sample(share, t, close=close)  # None: no volume

    # --- firing --------------------------------------------------------------------------------

    def _run_state(self, alert: Alert) -> RunState:
        """
        Return the alert's run state, fresh for an alert object it was not built for: an edit
        replaces the stored object before its `forget` reaches the loop, so the edited alert can
        never evaluate against the replaced one's previous value.
        """
        held = self._state.get(alert.id)
        if held is None or held[0] is not alert:
            held = (alert, RunState())
            self._state[alert.id] = held
        return held[1]

    def _step(self, alert: Alert, sample: Sample) -> None:
        fire = step(alert, self._run_state(alert), sample)
        if fire is not None:
            self._fire(alert, fire)

    def _clock(self, instrument_id: str, ts_ns: int) -> None:
        """Advance the instrument's event-fed `once_per_bar_close` alerts to `ts_ns`."""
        if not self._state:
            return
        for alert in self._alerts_for(instrument_id, _CLOCKED_KINDS):
            held = self._state.get(alert.id)
            if held is None or held[0] is not alert:
                continue  # no sample yet: nothing to decide
            fire = advance(alert, held[1], ts_ns)
            if fire is not None:
                self._fire(alert, fire)

    def _invalidate(self, alert: Alert, reason: str) -> None:
        if alert.invalid_reason is not None:
            return  # already invalid: ledgered once
        stored = self._store.mark_invalid(alert, reason)
        self._state.pop(alert.id, None)
        if not stored:
            return  # an edit or a delete replaced it meanwhile: the stored alert is not invalid
        error_ledger.record(
            INVALID_SITE,
            f"alert {alert.id} ({alert.instrument_id}, "
            f"{describe(alert.rule, alert.bar_seconds)}) is invalid: {reason}",
        )

    def _fire(self, alert: Alert, fire: Fire) -> None:
        condition = describe(alert.rule, alert.bar_seconds)
        # `{{close}}` is the close of the bar sample that fired (the closed bucket's last sample
        # under once_per_bar_close); a sample that is not a bar's reports the newest close seen.
        close = fire.close if fire.close is not None else self._last_close.get(alert.instrument_id)
        message = render(
            alert.template,
            alert.instrument_id,
            close,
            fire.ts_ns,
            alert.bar_seconds,
            value=fire.value,
            condition=condition,
        )
        self._store.record_fire(alert, fire.ts_ns)
        # Delivery is blocking network I/O and the buses run on the event loop: a daemon thread
        # keeps a slow webhook from stalling every chart's live candle.
        threading.Thread(target=self._deliverer.deliver, args=(alert, message), daemon=True).start()
        toast = {
            "channel": "alerts",
            "alert": {"id": alert.id, "message": message, "condition": condition},
        }
        for queue in self._listeners:
            _put_drop_oldest(queue, toast)
