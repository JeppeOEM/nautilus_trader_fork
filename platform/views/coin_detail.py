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
The single-coin detail read model's data reads (SSOT-05): the inputs the web coin page reads for
one coin.

- `metrics_history`/`metrics_nearest` -- the ranking context's `metrics.db` history reads
  (its query service `ranking.application.queries`), and `catalog_snapshot_rows` -- the archived seconds of one
  coin as plain dicts.

Moved out of `data_api/routes/metrics.py` and `data_api/app.py` (Story 24.2). Story 25.1a deleted
the metric groups (`COIN_DETAIL_GROUPS`), the rank-row lookup and the `snapshots:raw` decode that
came from bot_tui's Coin-detail view: that view was their only reader, and it went web-only.
"""

from ranking.application.queries import history
from ranking.application.queries import nearest

from views.catalog_reads import query_second_snapshots


def metrics_history(symbol: str, db_path: str, days: int = 31) -> list[dict]:
    """One coin's `metrics.db` rows over the last `days` days, oldest first (ranking's own read)."""
    return history(symbol, db_path, days)


def metrics_nearest(symbol: str, ts_ns: int, db_path: str) -> dict | None:
    """Return the `metrics.db` row of one coin closest to `ts_ns`; None if never stored."""
    return nearest(symbol, ts_ns, db_path)


def catalog_snapshot_rows(
    catalog_path: str, instrument_id: str, start_ns: int, end_ns: int
) -> list[dict]:
    """Return the archived seconds of one coin in `[start_ns, end_ns]` as book + OHLC dicts."""
    return [
        {
            "bid_prices": s.bid_prices,
            "bid_sizes": s.bid_sizes,
            "ask_prices": s.ask_prices,
            "ask_sizes": s.ask_sizes,
            "buy_volume": s.buy_volume,
            "sell_volume": s.sell_volume,
            "ts_event": s.ts_event,
            "open_price": s.open_price,
            "high_price": s.high_price,
            "low_price": s.low_price,
            "close_price": s.close_price,
        }
        for s in query_second_snapshots(catalog_path, instrument_id, start_ns, end_ns)
    ]
