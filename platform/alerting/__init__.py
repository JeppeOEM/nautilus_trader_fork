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
The alerting context (DDD spine AD-D2/AD-D16, Story 24.3): saved price alerts, the frequency/
expiration rules that decide when one fires, and their delivery.

Three invariants. *Observes the forming bar*: `application.engine.AlertEngine` is evaluated on the
same `{t, o, h, l, c, v}` bar, for the same `(instrument_id, bar_seconds)` and at the same tick,
that the chart draws -- it satisfies `views.live_candles.BarObserver` structurally and is attached to the
one `LiveCandleBus` by `data_api`'s composition root, so an alert can never fire on a bar the chart
never drew, there is no second `snapshots:raw` subscription, and no third seconds -> bars fold.
*Names channels, not transports*: an alert selects channels (its own webhook, Telegram when
configured) and `infrastructure.deliverer.NotifyDeliverer` hands them to `observability.notify`,
which picks the transport from the environment and ledgers a failed send. *No module state*: every
store, deliverer, engine and service is an instance built in `data_api.alert_wiring`.

`domain/` (`Alert`, `FiringPolicy`, `RunState`, `evaluate`, `render`) is pure stdlib. `application/`
declares the `AlertRepository` and `Deliverer` ports and holds `AlertEngine` and `AlertService`.
`infrastructure/` implements the ports -- `AlertStore` over the frozen `alerts.toml` and
`NotifyDeliverer` over `observability.notify` -- and is imported only by the composition root.

Dependency direction: `alerting` imports stdlib, `tomli_w`, `kernel` and `observability` only, never
`views`, `candles` or `data_api` (which depend on it); `platform/tests/test_boundaries.py` enforces
the edges and that `alerting.infrastructure` has no importer but `data_api.alert_wiring` (the
deprecated `data_api.alerts` re-export shim was deleted in Story 25.1).
"""
