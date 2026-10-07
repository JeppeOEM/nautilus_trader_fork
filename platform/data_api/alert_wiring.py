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
The process-wide alerting instances of the API (Story 24.3): `store`, the one `AlertStore` over
`alerts.toml`; `deliverer`, the `observability.notify` adapter; `engine`, the `AlertEngine` the
lifespan attaches to `buses.live_candle_bus` as a `BarObserver`; and `service`, the CRUD use cases
behind `/api/alerts`.

Invariant: one store and one engine per process -- the route's writes and the engine's fires go
through the same in-memory list, so a created alert is evaluated on the next batch and a deleted one
stops at once. They are constructed here, in the interface's composition module, because the
`alerting` context holds no module state and reads no interface wiring; this is the only module
outside `alerting` that imports `alerting.infrastructure` (`platform/tests/test_boundaries.py`; the
deprecated `data_api.alerts` re-export shim was deleted in Story 25.1).
`app.py`'s lifespan attaches the engine; routes and `ws/live.py` reference `alert_wiring.engine`/
`alert_wiring.service` through this module, so a test can swap either with
`monkeypatch.setattr(alert_wiring, ...)`.

Story 33.8: the engine also reads the two inputs it cannot compute itself through the adapters of
`data_api.alert_inputs` -- the chart's own indicator replay (over `buses.live_candle_bus`'s
unflushed rows, exactly as the indicator values route reads them) and the saved drawings file --
with its blocking reads run in the default executor (`executor_submit`). The lifespan attaches it
to `buses.live_derivs_bus` too.
"""

from pathlib import Path

from alerting.application.engine import AlertEngine
from alerting.application.service import AlertService
from alerting.infrastructure.deliverer import NotifyDeliverer
from alerting.infrastructure.toml_store import ALERTS_PATH
from alerting.infrastructure.toml_store import AlertStore

from data_api import buses
from data_api import settings
from data_api.alert_inputs import ChartIndicatorReader
from data_api.alert_inputs import DrawingFileReader
from data_api.alert_inputs import executor_submit


store = AlertStore(Path(ALERTS_PATH))
deliverer = NotifyDeliverer()
indicators = ChartIndicatorReader(
    catalog_path=lambda: settings.CATALOG_PATH,
    candles_dir=lambda: settings.CANDLES_DB_DIR,
    recent_rows=buses.live_candle_bus.recent_rows,
    recent_liquidations=buses.live_candle_bus.recent_liquidations,
)
drawings = DrawingFileReader(lambda: Path(settings.CHART_DRAWINGS_PATH))
engine = AlertEngine(
    store, deliverer, indicators=indicators, drawings=drawings, submit=executor_submit
)
service = AlertService(store, deliverer, engine)
