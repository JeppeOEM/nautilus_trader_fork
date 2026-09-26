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
Port contracts (DDD spine AD-D2): the `FillsStore` behaviour the application relies on, run
against its adapter `SqliteFillsStore`, and the bus adapter's shape against `BusConnection`.
"""

import inspect
from pathlib import Path

from bots.application.ports import BusConnection
from bots.application.ports import FillsStore
from bots.domain.fill_ledger import FillRecord
from bots.infrastructure.fills_store import SqliteFillsStore
from bots.infrastructure.redis import RedisBus


_DAY = 24 * 3600 * 1_000_000_000


def _fills_store_contract(store: FillsStore) -> None:
    store.write_fill(FillRecord("b1", 3 * _DAY, "SELL", 105.0, 1.0, 5.0, 5.0))
    store.write_fill(FillRecord("b1", 1 * _DAY, "BUY", 100.0, 1.0, None, None))
    store.write_fill(FillRecord("b2", 2 * _DAY, "SELL", 1.0, 1.0, -1.0, -1.0))

    # scoped to one bot, ascending by ts, every row kept
    assert [t["ts"] for t in store.recent_trades("b1", None, 10)] == [1 * _DAY, 3 * _DAY]
    # cutoff is inclusive
    assert [t["ts"] for t in store.recent_trades("b1", 3 * _DAY, 10)] == [3 * _DAY]
    assert store.realized_pnls("b1", None) == [5.0]
    assert store.position_realized_pnls("b2", None) == [-1.0]
    assert store.win_rate_stats("b1") == (1, 1)
    assert store.win_rate_stats("nobody") == (0, 0)
    assert store.pnl_by_day("b1", None) == [{"period_start": 3 * _DAY, "pnl": 5.0}]


def test_sqlite_fills_store_satisfies_the_fills_store_contract(store: SqliteFillsStore) -> None:
    _fills_store_contract(store)


def test_a_reopened_store_still_holds_every_row(tmp_path: Path) -> None:
    path = str(tmp_path / "fills.db")
    first = SqliteFillsStore(path)
    first.write_fill(FillRecord("b1", 1, "BUY", 1.0, 1.0, None, None))
    first.close()
    second = SqliteFillsStore(path)
    try:
        assert len(second.recent_trades("b1", None, 10)) == 1
    finally:
        second.close()


def test_redis_bus_implements_every_bus_connection_method() -> None:
    wanted = {
        name
        for name, member in inspect.getmembers(BusConnection)
        if not name.startswith("_") and callable(member)
    }
    assert wanted == {"publish", "get", "set", "control_messages"}
    assert all(callable(getattr(RedisBus, name, None)) for name in wanted)
