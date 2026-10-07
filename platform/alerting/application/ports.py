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
The ports alerting's application layer needs: where alerts are kept, how they are sent, and (Story
33.8) the two inputs it cannot compute itself -- an indicator's replayed value at a closed bar and a
saved chart trendline. The readers are implemented in `data_api.alert_inputs` (they need `views`,
which alerting may not import) and wired by `data_api.alert_wiring`.
"""

import json
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Protocol

from alerting.domain.alert import Alert


class AlertRepository(Protocol):
    """
    The saved alerts, shared by the CRUD service and the engine.

    Invariant (durable before acknowledged, frozen key set): `add`/`update`/`delete` persist before
    they return (and raise when they cannot), so an alert the API answered for survives a restart;
    `record_fire` and `mark_invalid` change the alert in memory *first* and never raise on a failed
    persist (ledgered instead), so an `only_once` alert cannot repeat this run, an invalid one is
    not evaluated again, and a full disk can never swallow the fire or the invalidation itself.
    The stored shape is the frozen `alerts.toml` key set (AD-D12; Story 33.8 appended keys only).
    """

    def list(self) -> list[Alert]:
        """Return a snapshot copy of every saved alert, in creation order."""
        ...

    def add(self, alert: Alert) -> None:
        """Save `alert`; raises when it cannot be persisted."""
        ...

    def update(self, alert_id: str, edit: Callable[[Alert], Alert]) -> Alert | None:
        """
        Replace the saved alert with `alert_id` by `edit(stored)` and return it; None when there is
        none. The read, the edit and the write are one step under the store's lock, so a fire or an
        invalidation recorded meanwhile is never overwritten by an edit built from an older copy.
        An exception from `edit` or from the persist propagates and keeps the stored alert in
        memory. Known limit: `AlertStore` rewrites `alerts.toml` in place (a single-file bind
        mount, which cannot be renamed over), so a write that fails partway (a full disk) leaves
        the file truncated and the next start refuses to load it. Upgrade path: mount the file's
        directory and write by temp-file rename, as `views.preferences` does since Story 32.5.
        """
        ...

    def delete(self, alert_id: str) -> bool:
        """Remove the alert with `alert_id`; False when there is none."""
        ...

    def record_fire(self, alert: Alert, ts_ns: int) -> None:
        """
        Stamp `last_fired_ns` (and `triggered` for `only_once`) on `alert`; never raises. When an
        edit replaced `alert` while it fired, the stored edit gets `last_fired_ns` only: the
        condition that fired is no longer the alert's, so it does not trigger the edited one.
        """
        ...

    def mark_invalid(self, alert: Alert, reason: str) -> bool:
        """
        Set `invalid_reason` on `alert`; never raises (a failed persist is ledgered). Return
        whether `alert` is still the stored alert: one an edit or a delete replaced meanwhile is not
        persisted invalid (the edit re-validated its condition), so the caller ledgers nothing.
        """
        ...


class Deliverer(Protocol):
    """
    How a fired alert reaches its owner.

    Invariant (an alert names channels, never transports, AD-D16): `channels` derives the channel
    names from the alert and the environment and `deliver` sends on each of them through the one
    outbound notifier, which owns the transports and ledgers a failed send. `deliver` is blocking
    network I/O and never raises for a failed send -- the next fire opportunity is the retry.
    An alert with no channel cannot be delivered at all, which is why creation refuses it.
    """

    def channels(self, alert: Alert) -> tuple[str, ...]:
        """Return the notification channels `alert` delivers on (empty: undeliverable)."""
        ...

    def deliver(self, alert: Alert, body: str) -> None:
        """Send `body` on every channel of `alert`."""
        ...


@dataclass(frozen=True)
class IndicatorRef:
    """
    One indicator series to read: a catalog `name`, its params and its price `source`. Hashable,
    so the engine batches every alert on one series into one entry (the params are kept as their
    canonical JSON text); it satisfies `views.indicator_picker.IndicatorRequest` structurally.
    """

    name: str
    params_json: str
    source: str

    @classmethod
    def of(cls, condition: Mapping[str, Any]) -> "IndicatorRef":
        """Return the series an `indicator` condition reads."""
        params = json.dumps(condition["params"], sort_keys=True, separators=(",", ":"))
        return cls(condition["name"], params, condition["source"])

    @property
    def params(self) -> dict[str, Any]:
        return dict(json.loads(self.params_json))


@dataclass(frozen=True)
class IndicatorReading:
    """
    One series at a closed bar: every output's value at that bar (`cur`) and at the bar before it
    in the same replay (`prev`, empty when the page holds no earlier bar), and the outputs the
    replay produced (`outputs`), so a condition naming another output is known to be invalid.
    """

    prev: Mapping[str, float | None]
    cur: Mapping[str, float | None]
    outputs: frozenset[str]


@dataclass(frozen=True)
class Missing:
    """The input is gone for good (an indicator left the catalog, a drawing was deleted)."""

    reason: str


@dataclass(frozen=True)
class Failed:
    """One read failed (a replay or read error): the sample is skipped, never invalidating."""

    message: str


IndicatorResult = IndicatorReading | Missing | Failed
Anchors = Sequence[Mapping[str, float]]
# `submit(job, done)`: run the blocking `job` off the event loop and hand its result to `done` on
# the loop (production: the default executor plus `call_soon_threadsafe`; tests: inline).
Submit = Callable[[Callable[[], Any], Callable[[Any], None]], None]


class IndicatorReader(Protocol):
    """
    An indicator's replayed values at one closed bar, read through the chart's own replay.

    Invariant (one indicator computation, SSOT-02): the values are the chart's -- the same
    `candle_page` and the same `replay_entry` the indicator pane draws -- never a second
    implementation inside alerting. `read` is blocking (catalog and store reads), so the engine
    calls it only through its `Submit` seam, never on the event loop.
    """

    def read(
        self,
        instrument_id: str,
        bar_seconds: int,
        closed_t_ms: int,
        refs: Sequence[IndicatorRef],
    ) -> dict[IndicatorRef, IndicatorResult]:
        """
        Return one result per ref for the bar starting at `closed_t_ms`: an `IndicatorReading`, a
        `Missing` (the indicator is no longer in the catalog) or a `Failed` (this read failed).
        """
        ...


class DrawingReader(Protocol):
    """
    A saved chart trendline, read from the chart's own drawings store.

    Invariant: the anchors returned are exactly the stored drawing's (`{time, price}`, UTC
    seconds), so the line an alert crosses is the line the chart draws; a missing drawing or one of
    another kind is `Missing`, and an unreadable store raises (a read failure, never an
    invalidation).
    """

    def trendline(self, instrument_id: str, drawing_id: str) -> Anchors | Missing:
        """Return the trendline's two anchors, or `Missing` naming why it is not one."""
        ...
