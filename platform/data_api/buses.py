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
The process-wide bus instances of the API (Story 24.2): `bus`, the one `rankings:live` subscriber
behind `GET /api/rankings`, the Technicals tab and every `/ws/live` connection, and
`live_candle_bus`, the one `snapshots:raw` subscriber behind every live-candle subscription, the
candle pages' unflushed tail and the alert engine.

Invariant: one subscriber per channel per process -- every reader shares these two objects, so no
request or WebSocket ever opens a Redis subscription of its own. They are constructed here, in the
interface's composition module, because the `views` context that defines both classes holds no
module state and reads no interface settings (`CATALOG_PATH` is passed in). `app.py`'s lifespan
starts them; routes and `ws/live.py` reference them as `buses.bus`/`buses.live_candle_bus` through
this module, so a test can swap either with `monkeypatch.setattr(buses, ...)`.
"""

from views.live_candles import LiveCandleBus
from views.rankings_bus import RankingsBus

from data_api import settings


bus = RankingsBus()
live_candle_bus = LiveCandleBus(settings.CATALOG_PATH)
