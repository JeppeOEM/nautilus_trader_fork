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
`views.coin_detail`: the single-coin read model's metrics.db and catalog reads. (Its rank-row
lookup, `snapshots:raw` decode and metric groups were read only by bot_tui's Coin-detail view and
were deleted with it in Story 25.1a.)
"""

from pathlib import Path

from kernel.second_snapshot import DydxSecondSnapshot
from ranking_engine import metrics_store

from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from views import coin_detail
from views.coin_detail import catalog_snapshot_rows


_IID = "BTC-USD-PERP.DYDX"


def _snapshot(iid: str, ts_ns: int = 1_000_000_000) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(iid),
        bid_prices=[100.0, 99.5],
        bid_sizes=[1.0, 2.0],
        ask_prices=[100.5, 101.0],
        ask_sizes=[1.5, 3.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=2,
        sell_count=1,
        open_price=100.0,
        high_price=100.4,
        low_price=99.9,
        close_price=100.2,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


# --- the metrics.db and catalog reads ------------------------------------------------------------


def test_metrics_reads_return_ranking_contexts_own_rows(tmp_path: Path) -> None:
    db_path = str(tmp_path / "metrics.db")
    now_ns = 1_900_000_000_000_000_000
    metrics_store.write([{"instrument_id": _IID, "ts": now_ns, "price": 100.0}], db_path)

    history = coin_detail.metrics_history(_IID, db_path)
    assert [row["price"] for row in history] == [100.0]
    assert history == metrics_store.history(_IID, db_path, 31)
    assert coin_detail.metrics_nearest(_IID, now_ns, db_path) == metrics_store.nearest(
        _IID, now_ns, db_path
    )


def test_catalog_snapshot_rows_project_book_and_ohlc_fields(tmp_path: Path) -> None:
    catalog_path = str(tmp_path / "catalog")
    ParquetDataCatalog(catalog_path).write_data([_snapshot(_IID, 2_000_000_000)])

    (row,) = catalog_snapshot_rows(catalog_path, _IID, 0, 3_000_000_000)

    assert row == {
        "bid_prices": [100.0, 99.5],
        "bid_sizes": [1.0, 2.0],
        "ask_prices": [100.5, 101.0],
        "ask_sizes": [1.5, 3.0],
        "buy_volume": 1.0,
        "sell_volume": 0.5,
        "ts_event": 2_000_000_000,
        "open_price": 100.0,
        "high_price": 100.4,
        "low_price": 99.9,
        "close_price": 100.2,
    }
