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
"""Unit tests for _coin_chart_json() after the aiohttp/Redis refactor.

_metrics_from_rolling() has been removed (replaced by _ingest_batch() which works on
module-level state). Tests for financial calculations are in test_dashboard_ingest.py.
"""

import json
from collections import deque

import ml_signals.dashboard
from ml_signals.dashboard import _coin_chart_json


def test_chart_json_no_rolling() -> None:
    ml_signals.dashboard._second_rolling.clear()
    result = json.loads(_coin_chart_json("ETH-USD-PERP.DYDX"))
    assert result == {"ts": [], "mid": [], "bid": [], "ask": [], "micro": []}


def test_chart_json_ts_conversion() -> None:
    ts_ns = 1_700_000_000_000_000_000
    ml_signals.dashboard._second_rolling.clear()
    ml_signals.dashboard._second_rolling["ETH-USD-PERP.DYDX"] = deque([{
        "instrument_id": "ETH-USD-PERP.DYDX",
        "bid_prices": [100.0],
        "bid_sizes": [1.0],
        "ask_prices": [101.0],
        "ask_sizes": [1.0],
        "buy_volume": 1.0,
        "sell_volume": 1.0,
        "buy_count": 1,
        "sell_count": 1,
        "ts_event": ts_ns,
        "ts_init": ts_ns,
    }])
    result = json.loads(_coin_chart_json("ETH-USD-PERP.DYDX"))
    assert result["ts"][0] == ts_ns // 1_000_000
    ml_signals.dashboard._second_rolling.clear()
