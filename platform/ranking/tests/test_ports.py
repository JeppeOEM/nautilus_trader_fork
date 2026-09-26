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
Port contract tests (Story 25.2): every adapter `ranking/__main__.py` wires satisfies the port it is
wired into -- statically (the annotated assignments below are checked by mypy) and by behaviour.
"""

import asyncio
import inspect
from pathlib import Path

import pytest

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from ranking.application.ports import LivePublisher
from ranking.application.ports import PriceHistory
from ranking.application.ports import RankingHistory
from ranking.application.ports import VolumeSource
from ranking.infrastructure.catalog_prices import CatalogPriceHistory
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from ranking.infrastructure.redis import RedisLivePublisher
from ranking.infrastructure.volume_bybit import BybitVolumeSource
from ranking.infrastructure.volume_dydx import DydxVolumeSource
from ranking.infrastructure.volume_hyperliquid import HyperliquidVolumeSource


_NOW = 1_800_000_000_000_000_000


def test_the_metrics_store_honours_the_ranking_history_contract(tmp_path: Path) -> None:
    store = SqliteMetricsStore(str(tmp_path / "metrics.db"))
    history: RankingHistory = store
    row = {"ts": _NOW, "instrument_id": "BTC-USD-PERP.DYDX", "price": 1.0, "rank": 1}
    try:
        history.write([row])
        assert [r["price"] for r in history.latest()] == [1.0]
        assert (
            history.nearest("BTC-USD-PERP.DYDX", _NOW) == history.history("BTC-USD-PERP.DYDX", 1)[0]
        )
        assert history.price_near_days_ago(7) == {}  # nothing that old: absent, never 0
    finally:
        store.close()


def test_the_catalog_adapter_honours_the_price_history_contract(tmp_path: Path) -> None:
    prices: PriceHistory = CatalogPriceHistory(str(tmp_path))
    assert prices.series("BTC-USD-PERP.DYDX", 0) == []  # an empty catalog: no series, no error


@pytest.mark.parametrize(
    "source",
    [
        DydxVolumeSource(DydxNetwork.MAINNET),
        BybitVolumeSource("mainnet", "linear"),
        BybitVolumeSource("mainnet", "spot"),
        HyperliquidVolumeSource("mainnet"),
    ],
)
def test_every_volume_source_has_a_name_and_an_async_fetch(source: VolumeSource) -> None:
    assert isinstance(source.name, str)
    assert source.name
    assert inspect.iscoroutinefunction(source.fetch)


def test_the_redis_publisher_sends_the_message_verbatim_on_rankings_live() -> None:
    sent: list[tuple[str, str]] = []

    class _Client:
        async def publish(self, channel: str, message: str) -> None:
            sent.append((channel, message))

    live: LivePublisher = RedisLivePublisher(_Client())  # type: ignore[arg-type]
    asyncio.run(live.publish('{"mode": "volume"}'))

    assert sent == [("rankings:live", '{"mode": "volume"}')]
