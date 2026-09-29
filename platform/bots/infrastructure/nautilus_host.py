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
The Nautilus host: the only module in `platform/` that imports `TradingNode` (architecture AD-8's
sanctioned exception, DDD spine AD-D2 -- `platform/tests/test_boundaries.py` fails any other).

One `TradingNode` per process (AD-11), hosting every bot of a `PaperFleet` or the one `ExecBot`:
one data client per venue in use and, for paper, one `SandboxExecutionClientConfig` per venue --
Nautilus's ExecutionEngine allows one exec client per venue per node, so paper bots on one venue
share that venue's balance pool. No wallet address or private key exists anywhere on the paper
path: real funds are structurally unreachable, not merely gated by a flag. The `ExecBot` path uses
the venue's own exec client from `VENUES`, which really signs and submits orders.

`VENUES` is a plain table, not a class hierarchy: how to build a venue's public data client and its
non-Sandbox exec client. The pure per-venue facts (allowed environments, paper quote currency) are
`bots.domain.config.VENUE_RULES`, keyed by the same venue tokens (a test pins it). Adding a venue is
one row in each, never an `if venue == ...` branch here.

Strategies are attached directly with `node.trader.add_strategy(...)` -- the live reference
examples' pattern (`examples/sandbox/dydx_sandbox.py`), not `ImportableStrategyConfig`, which is a
BacktestNode sweep convenience (AD-6).
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from urllib.parse import parse_qs
from urllib.parse import urlparse

from kernel.venues import market_suffix
from kernel.venues import venue_of

from bots.domain.config import BotConfig
from bots.domain.config import ExecBot
from bots.domain.config import ExecConfig
from bots.domain.config import PaperFleet
from bots.strategies.dummy import DummyStrategy
from bots.strategies.dummy import DummyStrategyConfig
from nautilus_trader.adapters.bybit.config import BybitDataClientConfig
from nautilus_trader.adapters.bybit.config import BybitExecClientConfig
from nautilus_trader.adapters.bybit.constants import BYBIT
from nautilus_trader.adapters.bybit.factories import BybitLiveDataClientFactory
from nautilus_trader.adapters.bybit.factories import BybitLiveExecClientFactory
from nautilus_trader.adapters.dydx.config import DydxDataClientConfig
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.dydx.constants import DYDX
from nautilus_trader.adapters.dydx.factories import DydxLiveDataClientFactory
from nautilus_trader.adapters.dydx.factories import DydxLiveExecClientFactory
from nautilus_trader.adapters.hyperliquid.config import HyperliquidDataClientConfig
from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.adapters.hyperliquid.constants import HYPERLIQUID
from nautilus_trader.adapters.hyperliquid.factories import HyperliquidLiveDataClientFactory
from nautilus_trader.adapters.hyperliquid.factories import HyperliquidLiveExecClientFactory
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory
from nautilus_trader.config import CacheConfig
from nautilus_trader.config import DatabaseConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.core.nautilus_pyo3 import BybitProductType
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId


# One fixed id for the whole node (AD-11): per-bot addressing lives entirely in bot_id /
# StrategyId (order_id_tag), never in trader_id.
TRADER_ID = "LIVE-PAPER-001"

type HostedBot = tuple[BotConfig | ExecConfig, DummyStrategy]


@dataclass(frozen=True)
class VenueSpec:
    make_data_config: Callable[..., object]
    data_factory: type
    environment_cls: type
    exec_config_cls: type
    exec_factory: type
    exec_kwargs: Callable[[ExecConfig], dict] = lambda config: {}

    def parse_environment(self, name: str) -> object:
        # PyO3 enums have no string constructor that agrees across venues; the upper-cased
        # member name does (config spells them lower-case).
        return getattr(self.environment_cls, name.upper())


def _bybit_exec_kwargs(config: ExecConfig) -> dict:
    """
    Bybit's exec client is scoped to product types, and the instrument id's own suffix is that
    product type (`BTCUSDT-LINEAR.BYBIT` -> LINEAR), the suffix `kernel.venues.market_kind()`
    reads. So the bot's one instrument decides it.
    """
    suffix = market_suffix(config.instrument_id)
    product_type = getattr(BybitProductType, suffix.upper(), None) if suffix else None
    if product_type is None:
        raise ValueError(
            f"Bybit instrument id {config.instrument_id!r} must carry its product type as the "
            "symbol suffix (LINEAR, INVERSE, SPOT or OPTION), e.g. BTCUSDT-LINEAR.BYBIT"
        )
    return {"product_types": (product_type,)}


VENUES: MappingProxyType[str, VenueSpec] = MappingProxyType(
    {
        DYDX: VenueSpec(
            make_data_config=DydxDataClientConfig,
            data_factory=DydxLiveDataClientFactory,
            environment_cls=DydxNetwork,
            exec_config_cls=DydxExecClientConfig,
            exec_factory=DydxLiveExecClientFactory,
            exec_kwargs=lambda config: {"subaccount": config.subaccount},
        ),
        BYBIT: VenueSpec(
            # Both product types: spot bots and linear bots share the one BYBIT data client.
            make_data_config=partial(
                BybitDataClientConfig,
                product_types=(BybitProductType.LINEAR, BybitProductType.SPOT),
            ),
            data_factory=BybitLiveDataClientFactory,
            environment_cls=BybitEnvironment,
            exec_config_cls=BybitExecClientConfig,
            exec_factory=BybitLiveExecClientFactory,
            exec_kwargs=_bybit_exec_kwargs,
        ),
        HYPERLIQUID: VenueSpec(
            make_data_config=HyperliquidDataClientConfig,
            data_factory=HyperliquidLiveDataClientFactory,
            environment_cls=HyperliquidEnvironment,
            exec_config_cls=HyperliquidExecClientConfig,
            exec_factory=HyperliquidLiveExecClientFactory,
        ),
    }
)


def _paper_venue_clients(
    fleet: PaperFleet, instrument_provider: InstrumentProviderConfig
) -> tuple[dict, dict, dict]:
    """One data client + one Sandbox exec client per venue in use (AD-11)."""
    data_clients, exec_clients, data_factories = {}, {}, {}
    for venue in fleet.venues_in_use():
        spec = VENUES[venue]
        pool = fleet.venue_config(venue)
        data_clients[venue] = spec.make_data_config(
            environment=spec.parse_environment(pool.environment),
            instrument_provider=instrument_provider,
        )
        data_factories[venue] = spec.data_factory
        exec_clients[venue] = SandboxExecutionClientConfig(
            venue=venue,
            starting_balances=list(pool.starting_balances),
            account_type=pool.account_type,
            instrument_provider=instrument_provider,
        )
    return data_clients, exec_clients, data_factories


def _exec_venue_clients(
    bot: ExecBot, instrument_provider: InstrumentProviderConfig
) -> tuple[dict, dict, dict]:
    """
    Build the clients of the one exec bot's one venue. `real_money` and `exchange_demo` differ
    only by the environment enum they resolve to. No credential is ever passed in: each venue's Rust client
    resolves the key pair its environment selects (BYBIT_DEMO_API_KEY/..., HYPERLIQUID_TESTNET_PK,
    ...) straight from the process environment, so no secret reaches a config file or a log
    line. A missing key yields an unauthenticated client, not an error (DEPLOY_CHECKLIST.md's
    pre-flight step).
    """
    config = bot.config
    venue = venue_of(config.instrument_id)
    spec = VENUES[venue]
    environment = spec.parse_environment(config.environment)
    data_clients = {
        venue: spec.make_data_config(
            environment=environment, instrument_provider=instrument_provider
        ),
    }
    exec_clients = {
        venue: spec.exec_config_cls(
            environment=environment,
            instrument_provider=instrument_provider,
            **spec.exec_kwargs(config),
        ),
    }
    return data_clients, exec_clients, {venue: spec.data_factory}


def _cache_config(redis_url: str) -> CacheConfig:
    """
    Build the Redis-backed Cache config (Story 4.6, AD-10): orders/positions persist beyond the
    process. Parsed from the same REDIS_URL the bus uses, so an override can never split the Cache
    and the `bots:*` channels onto different Redis instances -- which is also why a database
    number is refused: `DatabaseConfig` has no field for it, so the Cache would use database 0
    while the bus used the one named. redis-py reads the number from the path or a `?db=` query
    (the query wins), so both are checked.
    """
    url = urlparse(redis_url)
    databases = [url.path.lstrip("/")] if url.path not in ("", "/") else []
    databases += parse_qs(url.query).get("db", [])
    for database in databases:
        if database != "0":
            raise ValueError(
                f"REDIS_URL {redis_url!r} names Redis database {database!r}; the Nautilus Cache "
                "can only use database 0, so the bots' Cache and bus would split"
            )
    return CacheConfig(
        database=DatabaseConfig(
            type="redis",
            host=url.hostname,
            port=url.port,
            username=url.username,
            password=url.password,
            ssl=url.scheme == "rediss",
        ),
    )


def build_node(fleet: PaperFleet | ExecBot, redis_url: str) -> tuple[TradingNode, list[HostedBot]]:
    """Build the process's one node and attach one `DummyStrategy` per bot, in config order."""
    instrument_provider = InstrumentProviderConfig(load_all=True)
    if isinstance(fleet, ExecBot):
        data_clients, exec_clients, data_factories = _exec_venue_clients(fleet, instrument_provider)
        exec_factory = VENUES[venue_of(fleet.config.instrument_id)].exec_factory
    else:
        data_clients, exec_clients, data_factories = _paper_venue_clients(
            fleet, instrument_provider
        )
        exec_factory = SandboxLiveExecClientFactory

    node = TradingNode(
        config=TradingNodeConfig(
            trader_id=TraderId(TRADER_ID),
            logging=LoggingConfig(log_level=fleet.log_level, use_pyo3=True),
            cache=_cache_config(redis_url),
            data_clients=data_clients,
            exec_clients=exec_clients,
        )
    )
    for venue, data_factory in data_factories.items():
        node.add_data_client_factory(venue, data_factory)
        node.add_exec_client_factory(venue, exec_factory)

    hosted: list[HostedBot] = []
    for bot in fleet.bots:
        # Bracket exits are paper-only (see `ExecConfig`'s Known limit): an exec bot has none.
        exits = bot if isinstance(bot, BotConfig) else None
        strategy = DummyStrategy(
            config=DummyStrategyConfig(
                instrument_id=InstrumentId.from_str(bot.instrument_id),
                trade_size=bot.trade_size,
                trend_buy_threshold=bot.trend_buy_threshold,
                trend_sell_threshold=bot.trend_sell_threshold,
                ofi_confirm_threshold=bot.ofi_confirm_threshold,
                take_profit_bps=exits.take_profit_bps if exits is not None else None,
                stop_loss_bps=exits.stop_loss_bps if exits is not None else None,
                # Pinned to bot_id, never Trader.add_strategy()'s insertion-order default
                # (AD-11): an auto-assigned tag would make a bot's Cache/Redis/fills.db identity
                # depend on config list order, silently reassigning history on a reorder.
                order_id_tag=bot.bot_id,
            ),
        )
        node.trader.add_strategy(strategy)
        hosted.append((bot, strategy))

    node.build()
    return node, hosted
