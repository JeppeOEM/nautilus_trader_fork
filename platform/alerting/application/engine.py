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
`AlertEngine`: evaluates every active alert on the live forming bar and fires it.

A structural `views.live_candles.BarObserver` (alerting imports nothing from views): the composition
root attaches it to the one `LiveCandleBus`, which asks `watched_bars()` once per batch and folds a
forming bar for each pair it names, chart listener or not -- so alerts fire with no browser tab
open, on exactly the bar the chart shows.
"""

import asyncio
import threading
import time

from alerting.application.ports import AlertRepository
from alerting.application.ports import Deliverer
from alerting.domain.alert import Alert
from alerting.domain.alert import status_of
from alerting.domain.policy import RunState
from alerting.domain.policy import evaluate
from alerting.domain.policy import render


# The same bound and drop-oldest policy as `views.rankings_bus.QUEUE_MAX`/`put_drop_oldest`, kept
# equal by value because alerting may not import views (AD-D2): a stalled `/ws/live` client loses
# its oldest toasts rather than growing the queue without bound (MEM-02).
QUEUE_MAX = 1000


def _put_drop_oldest(queue: "asyncio.Queue[dict]", item: dict) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


class AlertEngine:
    """
    Fires saved alerts from the forming bar and toasts each fire to every `/ws/live` subscriber.

    Invariant (one evaluation input): an alert is evaluated only on the `bar` the bus publishes for
    its own `(instrument_id, bar_seconds)` -- its close is the price, the producing second's
    `ts_event` the time -- never on a raw snapshot or a bar folded here. Per-alert `RunState` is
    in-memory, so a restart forgets the previous price and the first bar after it cannot fire.
    """

    def __init__(self, store: AlertRepository, deliverer: Deliverer) -> None:
        self._store = store
        self._deliverer = deliverer
        self._state: dict[str, RunState] = {}
        self._listeners: set[asyncio.Queue[dict]] = set()

    def subscribe(self) -> "asyncio.Queue[dict]":
        """Toast feed for one `/ws/live` connection."""
        queue: asyncio.Queue[dict] = asyncio.Queue(QUEUE_MAX)
        self._listeners.add(queue)
        return queue

    def unsubscribe(self, queue: "asyncio.Queue[dict]") -> None:
        self._listeners.discard(queue)

    def forget(self, alert_id: str) -> None:
        self._state.pop(alert_id, None)

    def watched_bars(self) -> frozenset[tuple[str, int]]:
        """
        Return the `(instrument_id, bar_seconds)` pairs of every alert that can still fire.

        Known limit: expiry is judged here on the wall clock, but `evaluate` judges it on the bar's
        `ts_event`, so an alert stops being watched up to the capture->Redis lag (about a second)
        before its last in-window bar is evaluated. Upgrade path: keep a pair watched until an
        event-time `evaluate` has seen the alert expire, once expiries that tight matter.
        """
        now_ns = time.time_ns()
        return frozenset(
            (a.instrument_id, a.bar_seconds)
            for a in self._store.list()
            if status_of(a, now_ns) == "active"
        )

    def on_bar(self, instrument_id: str, bar_seconds: int, bar: dict, ts_ns: int) -> None:
        """Evaluate the pair's alerts on `bar`'s close at `ts_ns`, firing any that cross."""
        price = bar["c"]
        for alert in self._store.list():
            if alert.instrument_id != instrument_id or alert.bar_seconds != bar_seconds:
                continue
            state = self._state.setdefault(alert.id, RunState())
            fire_price = evaluate(alert, state, price, ts_ns)
            if fire_price is not None:
                self._fire(alert, fire_price, ts_ns)

    def _fire(self, alert: Alert, price: float, ts_ns: int) -> None:
        message = render(alert.template, alert.instrument_id, price, ts_ns, alert.bar_seconds)
        self._store.record_fire(alert, ts_ns)
        # Delivery is blocking network I/O and the bus runs on the event loop: a daemon thread
        # keeps a slow webhook from stalling every chart's live candle.
        threading.Thread(target=self._deliverer.deliver, args=(alert, message), daemon=True).start()
        toast = {"channel": "alerts", "alert": {"id": alert.id, "message": message}}
        for queue in self._listeners:
            _put_drop_oldest(queue, toast)
