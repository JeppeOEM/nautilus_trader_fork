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
Read-only FastAPI service exposing catalog/metrics data over the network.

`dashboard.py` reads catalog/metrics data straight off local disk, which only
works when it runs on the same box as the collector's data. This service is a
thin network-reachable wrapper around the same existing functions
`dashboard.py` already calls -- no reimplemented query/aggregation logic here
(NAUT-02). Every route is a plain sync `def`; FastAPI runs sync handlers in
its own threadpool automatically, which offloads the blocking SQLite/Parquet
reads without hand-rolled `asyncio.to_thread`.

Bound to 127.0.0.1 only (SEC-01) -- see docker-compose.yml's `data_api`
service (`network_mode: host` + `uvicorn --host 127.0.0.1`), no `ports:` entry.
"""

import os
from pathlib import Path

from fastapi import FastAPI

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals import catalog_stats as _catalog_stats
from ml_signals import chart_data as _chart_data
from ranking_engine import metrics_store


CATALOG_PATH: str = os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog")

# Default mirrors dashboard.py:85-86 exactly.
METRICS_DB_PATH: str = os.environ.get(
    "METRICS_DB_PATH", str(Path(CATALOG_PATH).parent / "metrics" / "metrics.db"),
)

app = FastAPI()


@app.get("/metrics/history/{symbol}")
def metrics_history(symbol: str, days: int = 31) -> list[dict]:
    return metrics_store.history(symbol, METRICS_DB_PATH, days)


@app.get("/metrics/nearest/{symbol}")
def metrics_nearest(symbol: str, ts_ns: int) -> dict | None:
    return metrics_store.nearest(symbol, ts_ns, METRICS_DB_PATH)


@app.get("/catalog/chart-series/{symbol}")
def catalog_chart_series(symbol: str, start_ns: int, end_ns: int) -> dict[str, list[dict]]:
    return _chart_data.compute_chart_series(CATALOG_PATH, symbol, start_ns, end_ns)


def _snapshot_to_dict(snapshot: DydxSecondSnapshot) -> dict:
    return {
        "bid_prices": snapshot.bid_prices,
        "bid_sizes": snapshot.bid_sizes,
        "ask_prices": snapshot.ask_prices,
        "ask_sizes": snapshot.ask_sizes,
        "buy_volume": snapshot.buy_volume,
        "sell_volume": snapshot.sell_volume,
        "ts_event": snapshot.ts_event,
        "open_price": snapshot.open_price,
        "high_price": snapshot.high_price,
        "low_price": snapshot.low_price,
        "close_price": snapshot.close_price,
    }


@app.get("/catalog/snapshots/{iid}")
def catalog_snapshots(iid: str, start_ns: int, end_ns: int) -> list[dict]:
    snapshots = _catalog_stats.query_second_snapshots(CATALOG_PATH, iid, start_ns, end_ns)
    return [_snapshot_to_dict(s) for s in snapshots]
