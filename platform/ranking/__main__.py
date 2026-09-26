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
The ranking process's composition root: `python3 -m ranking` (compose service `ranking_engine`).

Reads the environment (names and defaults frozen, AD-D12), builds the adapters, the board and the
engine, and runs the four loops. The only module of the context that reads the environment or
imports `ranking.infrastructure` for wiring.
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import redis.asyncio as aioredis
from kernel.venue_http import BYBIT_URLS
from kernel.venue_http import DYDX_NETWORKS
from kernel.venue_http import HYPERLIQUID_URLS
from observability import error_ledger

from ranking.application.engine import RankingConfig
from ranking.application.engine import RankingEngine
from ranking.application.ports import VolumeSource
from ranking.domain.board import RankingBoard
from ranking.domain.board import RankingsPublisher
from ranking.infrastructure.catalog_prices import CatalogPriceHistory
from ranking.infrastructure.metrics_store import SqliteMetricsStore
from ranking.infrastructure.redis import RedisLivePublisher
from ranking.infrastructure.redis import listen
from ranking.infrastructure.volume_bybit import BybitVolumeSource
from ranking.infrastructure.volume_dydx import DydxVolumeSource
from ranking.infrastructure.volume_hyperliquid import HyperliquidVolumeSource


@dataclass(frozen=True)
class Settings:
    """The process's environment, read once at start."""

    redis_url: str
    catalog_path: str
    metrics_db_path: str
    # Each must match that venue's collector: testnet and mainnet list different markets with
    # identical-looking symbols, so a mismatch would rank the wrong network's volumes silently.
    dydx_network: str
    bybit_environment: str
    hyperliquid_environment: str
    volatility_lookback_seconds: int
    heartbeat_seconds: int


def settings_from_env() -> Settings:
    catalog_path = os.environ.get("CATALOG_PATH", "platform/data/catalog")
    return Settings(
        redis_url=os.environ.get("REDIS_URL", "redis://127.0.0.1:6379"),
        catalog_path=catalog_path,
        # A directory mount in compose, not the single file: SQLite WAL's -wal/-shm sidecars
        # must sit next to metrics.db on a path data_api's read-only mount shares.
        metrics_db_path=os.environ.get(
            "METRICS_DB_PATH", str(Path(catalog_path).parent / "metrics" / "metrics.db")
        ),
        dydx_network=os.environ.get("DYDX_NETWORK", "mainnet").lower(),
        bybit_environment=os.environ.get("BYBIT_ENVIRONMENT", "mainnet").lower(),
        hyperliquid_environment=os.environ.get("HYPERLIQUID_ENVIRONMENT", "mainnet").lower(),
        volatility_lookback_seconds=_positive_int("RANKING_VOLATILITY_LOOKBACK_SECONDS", "3600"),
        heartbeat_seconds=_positive_int("RANKING_HEARTBEAT_SECONDS", "5"),
    )


def _positive_int(name: str, default: str) -> int:
    """
    Read a positive integer env var; fail fast otherwise. A 0 or negative value would not error
    anywhere later: it would publish on every tick or empty every volatility window.
    """
    value = int(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f"{name}={value} must be a positive number of seconds")
    return value


def volume_sources(settings: Settings) -> list[VolumeSource]:
    """
    One source per venue market. Fails fast on an unknown environment name rather than ledgering
    the same KeyError every poll forever.
    """
    for name, env, known in (
        ("DYDX_NETWORK", settings.dydx_network, DYDX_NETWORKS),
        ("BYBIT_ENVIRONMENT", settings.bybit_environment, BYBIT_URLS),
        ("HYPERLIQUID_ENVIRONMENT", settings.hyperliquid_environment, HYPERLIQUID_URLS),
    ):
        if env not in known:
            raise ValueError(f"{name}={env!r} is not one of {sorted(known)}")
    return [
        DydxVolumeSource(DYDX_NETWORKS[settings.dydx_network]),
        BybitVolumeSource(settings.bybit_environment, "linear"),
        BybitVolumeSource(settings.bybit_environment, "spot"),
        HyperliquidVolumeSource(settings.hyperliquid_environment),
    ]


def build_board(settings: Settings, config: RankingConfig) -> RankingBoard:
    return RankingBoard(
        RankingsPublisher(settings.heartbeat_seconds, now_fn=time.time),
        volatility_lookback_seconds=settings.volatility_lookback_seconds,
        volume_max_age_ns=config.volume_max_age_ns,
    )


async def run(settings: Settings) -> None:
    config = RankingConfig()
    sources = volume_sources(settings)
    history = SqliteMetricsStore(settings.metrics_db_path)
    try:
        async with aioredis.Redis.from_url(settings.redis_url, decode_responses=True) as client:
            engine = RankingEngine(
                build_board(settings, config),
                volume_sources=sources,
                prices=CatalogPriceHistory(settings.catalog_path),
                history=history,
                live=RedisLivePublisher(client),
                config=config,
            )
            await asyncio.gather(
                listen(settings.redis_url, engine.handle),
                engine.heartbeat(),
                engine.volume_loop(),
                engine.slow_loop(),
            )
    finally:
        history.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    error_ledger.start()  # durable error ledger (story 23.3); no-op without ERROR_LEDGER_DIR
    asyncio.run(run(settings_from_env()))


if __name__ == "__main__":
    main()
