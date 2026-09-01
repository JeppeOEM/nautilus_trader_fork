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
Unit tests for dashboard's read-only _LIVE_SLOW refresh loop (Story 1.8, Task 7).

After the metrics-persist loop relocates to ranking_engine, dashboard no longer calls
metrics_computer.compute_all() -- its replacement is a read-only metrics_store.latest()
poll. Tested as a pure function against a stubbed metrics_store.latest(), never via a
real 60s sleep.
"""

import ml_signals.dashboard as dashboard_module
from ml_signals.dashboard import _LIVE_SLOW
from ml_signals.dashboard import _refresh_live_slow


def test_refresh_live_slow_populates_from_metrics_store_latest(monkeypatch) -> None:
    _LIVE_SLOW.clear()
    rows = [{"instrument_id": "BTC-USD-PERP.DYDX", "price": 42.0, "pct_1h": 1.0}]
    monkeypatch.setattr(dashboard_module.metrics_store, "latest", lambda db_path: rows)

    _refresh_live_slow("/nonexistent/metrics.db")

    assert _LIVE_SLOW["BTC-USD-PERP.DYDX"]["price"] == 42.0


def test_refresh_live_slow_is_noop_on_error(monkeypatch) -> None:
    """Mirrors make_app()'s existing preload's error tolerance -- a broken/missing
    metrics.db must not crash the dashboard process, just skip this cycle's refresh.
    """
    _LIVE_SLOW.clear()

    def _raise(db_path: str) -> list[dict]:
        raise RuntimeError("boom")

    monkeypatch.setattr(dashboard_module.metrics_store, "latest", _raise)

    _refresh_live_slow("/nonexistent/metrics.db")  # must not raise

    assert _LIVE_SLOW == {}
