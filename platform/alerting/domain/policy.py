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
The firing policy: when a saved alert fires, as a pure state-machine step, and what it says.

Split from delivery so the frequency/expiration rules are testable without any network: `step()`
is a pure state-machine step, `render()` a pure string substitution.

One sample stream, three policies (Story 33.8): the engine turns every input into a `Sample` for
each alert -- the value of the series its condition reads, at its event time -- and `step` decides
which samples reach `conditions.evaluate`:

- `once_per_bar_close` evaluates only the last sample of a bucket, at rollover (the first sample
  of the next bucket), comparing it with the last sample of the bucket before;
- `once_per_bar` evaluates every sample and fires at most once per bucket;
- `only_once` evaluates every sample and fires once.

For `price_cross` this is the pre-33.8 `_crossed` machine exactly. A `closed` sample (an indicator
read at a closed bar) carries its own previous value from the same replay and is evaluated at once
under every frequency: it already is a bar close, so the three policies coincide for it.

Time only moves forward per alert: a sample older than the newest bucket seen belongs to that bucket
(`_bucket`). `advance` is a time-only step: another input of the instrument closes a
`once_per_bar_close` bucket its own series has not left yet (a sparse funding or liquidation
series).
"""

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from enum import StrEnum

from alerting.domain.alert import Alert
from alerting.domain.alert import status_of
from alerting.domain.conditions import Observation
from alerting.domain.conditions import Value
from alerting.domain.conditions import evaluate


_NS_PER_S = 1_000_000_000


class FiringPolicy(StrEnum):
    """
    How often a condition fires. Values are the stored `Alert.frequency` text and the
    `/api/alerts` `frequency` literal (frozen, AD-D12), so a `StrEnum` member compares equal to the
    stored string.
    """

    ONCE_PER_BAR_CLOSE = "once_per_bar_close"
    ONCE_PER_BAR = "once_per_bar"
    ONLY_ONCE = "only_once"


FREQUENCIES: tuple[str, ...] = tuple(policy.value for policy in FiringPolicy)


@dataclass(frozen=True)
class Sample:
    """
    One observation of the series an alert's condition reads.

    `value` is what the condition compares (the close, `close - line(t)`, a percent change, a
    funding rate, a window sum); `report` is the triggering value `{{value}}` shows, the value
    itself unless the kind reports another (a trendline cross reports the close). `ts_ns` is the
    event time the bucket is taken from. `closed` marks a closed-bar sample carrying `prev`.
    `close` is the bar close the sample was taken on (`{{close}}`), None for a sample that is not
    a bar's (a funding or open-interest tick, a liquidation).
    """

    value: Value
    ts_ns: int
    report: Value | None = None
    closed: bool = False
    prev: Value | None = None
    close: float | None = None

    @property
    def reported(self) -> float:
        return float(self.value if self.report is None else self.report)


@dataclass(frozen=True)
class Fire:
    """
    One fire: the triggering value (`{{value}}`), the close of the sample that fired (`{{close}}`,
    None when it was not a bar's) and the event time it is stamped with.
    """

    value: float
    close: float | None
    ts_ns: int


@dataclass
class RunState:
    """
    Per-alert, in-memory only: a restart forgets the previous value, so the first sample after a
    restart can never fire a cross (a cross needs two observations).

    `bucket` is the newest bucket seen; `pending` (once_per_bar_close) is whether that bucket has a
    sample not yet decided, so a bucket closed by `advance` is never decided a second time.
    """

    last: Sample | None = None
    prev_close: Value | None = None  # once_per_bar_close: last value of the bucket before the last
    bucket: int | None = None
    fired_bucket: int | None = None
    pending: bool = False

    @property
    def last_value(self) -> Value | None:
        return None if self.last is None else self.last.value


def _fire_of(sample: Sample, ts_ns: int | None = None) -> Fire:
    return Fire(sample.reported, sample.close, sample.ts_ns if ts_ns is None else ts_ns)


def _bucket(alert: Alert, state: RunState, ts_ns: int) -> int:
    """
    Return the sample's bucket, never older than the newest bucket seen: a late sample (a
    liquidation row whose `ts_event` lags the bar ticks that also step `forced_share`, a Redis
    redelivery) belongs to the current bucket. Treating it as current, rather than dropping it,
    keeps every in-order stream (every price kind) exactly as before and still evaluates the late
    row's value (a late liquidation can complete a notional); rolling the state back instead would
    let `once_per_bar` fire twice in one bucket and `once_per_bar_close` decide spurious rollovers.
    """
    bucket = ts_ns // (alert.bar_seconds * _NS_PER_S)
    return bucket if state.bucket is None else max(bucket, state.bucket)


def _close_bucket(alert: Alert, state: RunState, ts_ns: int | None) -> Fire | None:
    """
    Decide the bucket that just ended on its last sample (once: `pending` is cleared), comparing it
    with the bucket before; the fire is stamped `ts_ns` (None: the decided sample's own time).
    """
    last = state.last
    if not state.pending or last is None:
        return None
    state.pending = False
    met = evaluate(alert.rule, Observation(state.prev_close, last.value))
    state.prev_close = last.value
    return _fire_of(last, ts_ns) if met else None


def _bar_close_step(alert: Alert, state: RunState, sample: Sample, bucket: int) -> Fire | None:
    # Only bucket closes are compared: a bucket's close is the last sample seen before it rolls
    # over, so the condition is decided once, on the first sample of the next bucket (or by
    # `advance`, when another input's clock passes the bucket first).
    fired = None
    if state.bucket is not None and bucket != state.bucket:
        fired = _close_bucket(alert, state, sample.ts_ns)
    state.bucket, state.last, state.pending = bucket, sample, True
    return fired


def _gate(alert: Alert, state: RunState, bucket: int) -> bool:
    """Whether a met condition may fire now: `once_per_bar` fires once per bucket."""
    if alert.frequency == FiringPolicy.ONCE_PER_BAR:
        if state.fired_bucket == bucket:
            return False
        state.fired_bucket = bucket
    return True


def step(alert: Alert, state: RunState, sample: Sample) -> Fire | None:
    """
    One sample of the frequency/expiration state machine. Returns the fire when `alert` fires
    (for `once_per_bar_close` the closed bucket's last sample, not this one), else None. Only an
    `active` alert (not invalid, triggered or expired at `ts_ns`) can fire.
    """
    if status_of(alert, sample.ts_ns) != "active":
        return None
    bucket = _bucket(alert, state, sample.ts_ns)
    if alert.frequency == FiringPolicy.ONCE_PER_BAR_CLOSE and not sample.closed:
        return _bar_close_step(alert, state, sample, bucket)
    state.bucket = bucket
    if sample.closed:
        met = evaluate(alert.rule, Observation(sample.prev, sample.value))
    else:
        met = evaluate(alert.rule, Observation(state.last_value, sample.value))
        state.last = sample
    return _fire_of(sample) if met and _gate(alert, state, bucket) else None


def advance(alert: Alert, state: RunState, ts_ns: int) -> Fire | None:
    """
    Take a time-only step (`once_per_bar_close` only): when `ts_ns` -- the event time of any other
    input of the alert's instrument -- has passed the bucket of the alert's last sample, decide that
    bucket now on its last sample, stamped with that sample's time. A sparse series (a funding rate
    that changes hourly, a quiet liquidation stream) is then decided when its bucket ends, not when
    its next sample arrives hours later. Every other frequency evaluates each sample as it arrives,
    so there is nothing to advance.
    """
    if alert.frequency != FiringPolicy.ONCE_PER_BAR_CLOSE or state.bucket is None:
        return None
    if not state.pending or status_of(alert, ts_ns) != "active":
        return None
    bucket = ts_ns // (alert.bar_seconds * _NS_PER_S)
    if bucket <= state.bucket:
        return None
    state.bucket = bucket
    return _close_bucket(alert, state, None)


def render(
    template: str,
    ticker: str,
    close: float | None,
    ts_ns: int,
    bar_seconds: int,
    *,
    value: float | None = None,
    condition: str = "",
) -> str:
    """
    Plain replace over the six documented placeholders: `{{ticker}}`, `{{close}}` (the newest close
    the engine saw for the pair, `n/a` when none), `{{time}}`, `{{interval}}` (bar seconds),
    `{{value}}` (the triggering value, `n/a` when none) and `{{condition}}` (`conditions.describe`).
    """
    time_iso = datetime.fromtimestamp(ts_ns / _NS_PER_S, UTC).isoformat()
    values = {
        "ticker": ticker,
        "close": "n/a" if close is None else str(close),
        "time": time_iso,
        "interval": str(bar_seconds),
        "value": "n/a" if value is None else str(value),
        "condition": condition,
    }
    for name, text in values.items():
        template = template.replace("{{" + name + "}}", text)
    return template
