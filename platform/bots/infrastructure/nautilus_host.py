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

Each bot's strategy is named by its `strategy` key and resolved through `STRATEGIES`, then attached
with `node.trader.add_strategy(...)`. `dummy` (the default, and the only strategy of an `ExecBot`)
is `DummyStrategy`, built directly from the bot's config as the live reference examples do
(`examples/sandbox/dydx_sandbox.py`). Every other strategy is built by string path with Nautilus's
own resolver, `StrategyFactory.create(ImportableStrategyConfig(...))` -- the mechanism
`BacktestNode` uses (AD-6), so the class a backtest ran is the class the paper bot runs, from the
same `research.strategies.<module>:<Class>` path. The string is deliberate: a direct import would
be a bots -> research edge the context map does not have (`platform/tests/test_boundaries.py`
states why there is none); the image still ships `research` (`bots.dockerfile`), and
`platform/tests/test_images.py`'s `_STRING_PATH_IMPORTS` names each path because its `ast` walk
cannot see a string.

Known limit: an `ExecBot` (`real_money`/`exchange_demo`) always runs `DummyStrategy` --
`ExecConfig` has no `strategy`/`params` keys, so a strategy reaches real signing only after it has
run as a paper bot. Upgrade path: the same two keys on `ExecConfig` and its loader, checked by
`check_strategy`, once a paper-proven strategy is promoted.

`BOT_SIGNAL_LOG_DIR` (Story 31.9): when set, every `DummyStrategy` this host builds writes its
per-cycle signal log to `<dir>/<bot_id>.jsonl` (`bots.strategies.signal_log`), and so does every
paper `liquidation_cascade` bot (Story 33.14, its own record kinds, `docs/DATA_DICTIONARY.md`
§1.22); other strategies have no such log. Unset, nothing is written.

**Liquidations (Story 33.14).** A strategy of `LIQUIDATION_STRATEGIES` subscribes `Liquidation` rows
through the client id `LIQUIDATIONS` (`kernel.liquidation.LIQUIDATION_CLIENT_ID`). For a paper fleet
holding one, `build_node` adds one more data client under that name, the Redis bridge of the Bybit
collector's `liquidations:raw` (`bots.infrastructure.liquidation_data_client`), and its factory;
a fleet without one gets no such client. The bridge's `LiquidationFeedStatus` is shared with those
bots' cache readers (`cache_reader_for`), so a dropped feed reads as stale data. Such a bot is
refused at config load on an instrument without the feed (`kernel.liquidation.has_liquidation_feed`:
Bybit LINEAR only), and it is paper only: an `ExecBot` always runs `DummyStrategy` (the Known limit
above), so the cascade strategy can never reach real signing.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from types import MappingProxyType
from urllib.parse import parse_qs
from urllib.parse import unquote
from urllib.parse import urlparse

from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import has_liquidation_feed
from kernel.venues import market_suffix
from kernel.venues import venue_of

from bots.domain.config import BotConfig
from bots.domain.config import ExecBot
from bots.domain.config import ExecConfig
from bots.domain.config import PaperFleet
from bots.domain.config import plain_params
from bots.infrastructure.cache_reader import StrategyCacheReader
from bots.infrastructure.liquidation_data_client import LiquidationDataClientConfig
from bots.infrastructure.liquidation_data_client import LiquidationFeedStatus
from bots.infrastructure.liquidation_data_client import liquidation_client_factory
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
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import StrategyConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.core.nautilus_pyo3 import BybitProductType
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.trading.config import StrategyFactory
from nautilus_trader.trading.strategy import Strategy


# One fixed id for the whole node (AD-11): per-bot addressing lives entirely in bot_id /
# StrategyId (order_id_tag), never in trader_id.
TRADER_ID = "LIVE-PAPER-001"
# The env var naming the directory of the opt-in DummyStrategy signal logs (see the docstring).
SIGNAL_LOG_DIR_ENV = "BOT_SIGNAL_LOG_DIR"

type HostedBot = tuple[BotConfig | ExecConfig, Strategy]

# `strategy` key -> (strategy path, config path) for `StrategyFactory`, or None for the one class
# built directly (`DummyStrategy`). Adding a strategy is one row here, one in `_STRING_PATH_IMPORTS`
# (`platform/tests/test_images.py`) and one bot_tui source mount (`docker-compose.yml`).
STRATEGIES: MappingProxyType[str, tuple[str, str] | None] = MappingProxyType(
    {
        "dummy": None,
        "candle_pattern": (
            "research.strategies.candle_pattern_strategy:CandlePatternStrategy",
            "research.strategies.candle_pattern_strategy:CandlePatternStrategyConfig",
        ),
        "liquidation_cascade": (
            "research.strategies.liquidation_cascade_strategy:LiquidationCascadeStrategy",
            "research.strategies.liquidation_cascade_strategy:LiquidationCascadeStrategyConfig",
        ),
    }
)
# The strategies fed `Liquidation` rows through the `LIQUIDATIONS` data client (the module
# docstring): each writes its signal log under `BOT_SIGNAL_LOG_DIR` and needs the feed.
LIQUIDATION_STRATEGIES = frozenset({"liquidation_cascade"})
# Keys a bot's `params` may never set: `BotConfig` owns the identity (`order_id_tag` is the bot's
# `bot_id`, AD-11; `strategy_id` would override it) and the sizing, the host the signal log's
# location (`BOT_SIGNAL_LOG_DIR`), and every other `StrategyConfig` base field is the host's (e.g.
# `oms_type`, `manage_stop`).
_RESERVED_PARAMS = frozenset({"instrument_id", "trade_size", "signal_log_path"}) | frozenset(
    StrategyConfig.__struct_fields__
)


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


def _decoded(part: str | None) -> str | None:
    return unquote(part) if part is not None else None


def _cache_config(redis_url: str) -> CacheConfig:
    """
    Build the Redis-backed Cache config (Story 4.6, AD-10): orders/positions persist beyond the
    process. Parsed from the same REDIS_URL the bus uses, so an override can never split the Cache
    and the `bots:*` channels onto different Redis instances -- which is also why a database
    number is refused: `DatabaseConfig` has no field for it, so the Cache would use database 0
    while the bus used the one named. redis-py reads the number from the path or a `?db=` query
    (the query wins), so both are checked. Host, username and password are percent-decoded, as
    redis-py's `parse_url` decodes all three, so the Cache and the bus authenticate with the same
    credentials (an encoded `@` or `:` in a password otherwise reached the Cache still encoded).
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
            host=_decoded(url.hostname),
            port=url.port,
            username=_decoded(url.username),
            password=_decoded(url.password),
            ssl=url.scheme == "rediss",
        ),
    )


def _needs_liquidations(fleet: PaperFleet | ExecBot) -> bool:
    """Whether a paper bot of `fleet` runs a `LIQUIDATION_STRATEGIES` strategy."""
    return isinstance(fleet, PaperFleet) and any(
        bot.strategy in LIQUIDATION_STRATEGIES for bot in fleet.bots
    )


def _liquidation_bridge(
    fleet: PaperFleet | ExecBot, redis_url: str, status: LiquidationFeedStatus | None
) -> tuple[LiquidationDataClientConfig, LiquidationFeedStatus] | None:
    """
    Return the `LIQUIDATIONS` bridge's config and the status it shares for a fleet with a
    liquidation strategy, None for any other; `ValueError` for such a fleet without `status`.
    """
    if not _needs_liquidations(fleet):
        return None
    if status is None:
        raise ValueError(
            "a fleet with a liquidation strategy needs the LiquidationFeedStatus its cache readers "
            "read (build_node(..., liquidation_status=))"
        )
    return LiquidationDataClientConfig(redis_url=redis_url), status


def _venue_clients(fleet: PaperFleet | ExecBot) -> tuple[dict, dict, dict, type]:
    """Return the fleet's data and exec client configs, its data factories and exec factory."""
    instrument_provider = InstrumentProviderConfig(load_all=True)
    if isinstance(fleet, ExecBot):
        clients = _exec_venue_clients(fleet, instrument_provider)
        return (*clients, VENUES[venue_of(fleet.config.instrument_id)].exec_factory)
    return (*_paper_venue_clients(fleet, instrument_provider), SandboxLiveExecClientFactory)


def build_node(
    fleet: PaperFleet | ExecBot,
    redis_url: str,
    liquidation_status: LiquidationFeedStatus | None = None,
) -> tuple[TradingNode, list[HostedBot]]:
    """
    Build the process's one node and attach each bot's strategy (`STRATEGIES`) in order. A paper
    fleet with a liquidation strategy also gets the `LIQUIDATIONS` bridge on `redis_url`, sharing
    `liquidation_status` with the cascade bots' cache readers (`cache_reader_for`); such a fleet
    without one is refused (`ValueError`): a status nobody reads would leave the bots' heartbeat
    blind to a dropped feed.
    """
    bridge = _liquidation_bridge(fleet, redis_url, liquidation_status)
    # Every strategy first: a bad one fails the build before any node (Cache, Redis, loop) exists.
    hosted: list[HostedBot] = [(bot, _strategy_for(bot)) for bot in fleet.bots]
    data_clients, exec_clients, data_factories, exec_factory = _venue_clients(fleet)
    if bridge is not None:
        data_clients[LIQUIDATION_CLIENT_ID] = bridge[0]

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
    if bridge is not None:
        node.add_data_client_factory(LIQUIDATION_CLIENT_ID, liquidation_client_factory(bridge[1]))

    for _bot, strategy in hosted:
        node.trader.add_strategy(strategy)

    node.build()
    return node, hosted


def cache_reader_for(
    bot: BotConfig | ExecConfig, strategy: Strategy, liquidation_status: LiquidationFeedStatus
) -> StrategyCacheReader:
    """
    Return the bot's `BotRuntime`: a liquidation strategy's reader also reads the feed's status
    (its `last_data_ns` is capped at the disconnect while the feed is down), every other's not.
    """
    if isinstance(bot, BotConfig) and bot.strategy in LIQUIDATION_STRATEGIES:
        return StrategyCacheReader(strategy, liquidation_status)
    return StrategyCacheReader(strategy)


def check_strategy(bot: BotConfig) -> None:
    """
    Refuse a bot whose `strategy`/`params` cannot mean what they say (DATA-07: never a silent
    fallback to `dummy`): an unknown strategy, `params` on `dummy` (its tunables are `BotConfig`
    keys), a params key `BotConfig` or the host owns (`_RESERVED_PARAMS`), or a liquidation
    strategy on an instrument without the liquidation feed. A params key the strategy's config
    does not have fails at build, where its config class rejects it.
    """
    if bot.strategy not in STRATEGIES:
        raise ValueError(
            f"[[bots]] {bot.bot_id}: unknown strategy {bot.strategy!r} -- known: {list(STRATEGIES)}"
        )
    if STRATEGIES[bot.strategy] is None and bot.params:
        raise ValueError(
            f"[[bots]] {bot.bot_id}: strategy {bot.strategy!r} takes no [bots.params] "
            f"(got {sorted(bot.params)}); its tunables are [[bots]] keys"
        )
    reserved = sorted(_RESERVED_PARAMS & set(bot.params))
    if reserved:
        raise ValueError(
            f"[[bots]] {bot.bot_id}: [bots.params] may not set {reserved}: the bot's own keys "
            "and the host set them"
        )
    if bot.strategy in LIQUIDATION_STRATEGIES and not has_liquidation_feed(bot.instrument_id):
        raise ValueError(
            f"[[bots]] {bot.bot_id}: strategy {bot.strategy!r} needs the liquidation feed, which "
            f"{bot.instrument_id!r} has not (Bybit LINEAR ids only)"
        )


def _strategy_for(bot: BotConfig | ExecConfig) -> Strategy:
    """
    Build `bot`'s strategy with its `order_id_tag` pinned to `bot_id` -- never
    `Trader.add_strategy()`'s insertion-order default (AD-11): an auto-assigned tag would make a
    bot's Cache/Redis/fills.db identity depend on config list order, silently reassigning history
    on a reorder.
    """
    paths = None
    exits = None  # bracket exits are paper-only (`ExecConfig`'s Known limit): an exec bot has none
    if isinstance(bot, BotConfig):
        check_strategy(bot)  # a directly built BotConfig is checked too, never a bare KeyError
        paths = STRATEGIES[bot.strategy]
        exits = bot
    if paths is None:
        return DummyStrategy(
            config=DummyStrategyConfig(
                instrument_id=InstrumentId.from_str(bot.instrument_id),
                trade_size=bot.trade_size,
                trend_buy_threshold=bot.trend_buy_threshold,
                trend_sell_threshold=bot.trend_sell_threshold,
                ofi_confirm_threshold=bot.ofi_confirm_threshold,
                take_profit_bps=exits.take_profit_bps if exits is not None else None,
                stop_loss_bps=exits.stop_loss_bps if exits is not None else None,
                # The signal log is the paper fleet's parity instrument (Story 31.9): an exec
                # bot never writes one.
                signal_log_path=_signal_log_path(bot.bot_id) if exits is not None else None,
                order_id_tag=bot.bot_id,
            ),
        )
    assert isinstance(bot, BotConfig)  # an ExecConfig has no paths (see the module's Known limit)
    return _importable_strategy(bot, *paths)


def _signal_log_path(bot_id: str) -> str | None:
    """
    `<BOT_SIGNAL_LOG_DIR>/<bot_id>.jsonl` when the env var is set, else None (no log): the
    `DummyStrategy` and liquidation-strategy signal logs are opt-in per process, never a
    `BotConfig`/TOML key (Stories 31.9, 33.14).
    """
    directory = os.environ.get(SIGNAL_LOG_DIR_ENV)
    if not directory:
        return None
    return os.path.join(directory, f"{bot_id}.jsonl")


def strategy_params(bot: BotConfig, signal_log_path: str | None) -> dict[str, object]:
    """
    Return the config a string-path strategy is built from: the bot's `params`, its instrument,
    sizing and `bot_id` as `order_id_tag`, and `signal_log_path` when not None (a
    `LIQUIDATION_STRATEGIES` strategy's). The one builder of it: the catalog replay
    (`bots.signal_replay`) builds the replayed strategy with exactly these fields.
    """
    config: dict[str, object] = {
        **plain_params(bot.params),
        "instrument_id": bot.instrument_id,
        "trade_size": str(bot.trade_size),
        "order_id_tag": bot.bot_id,
    }
    if signal_log_path is not None:
        config["signal_log_path"] = signal_log_path
    return config


def _importable_strategy(bot: BotConfig, strategy_path: str, config_path: str) -> Strategy:
    log_path = _signal_log_path(bot.bot_id) if bot.strategy in LIQUIDATION_STRATEGIES else None
    config = strategy_params(bot, log_path)
    try:
        strategy = StrategyFactory.create(
            ImportableStrategyConfig(
                strategy_path=strategy_path, config_path=config_path, config=config
            )
        )
    except Exception as exc:  # the resolver's import, msgspec and strategy errors alike
        raise ValueError(f"[[bots]] {bot.bot_id}: strategy {bot.strategy!r}: {exc}") from exc
    if not hasattr(strategy, "last_data_ns"):
        # `StrategyCacheReader.last_data_ns` feeds the heartbeat; without it the bot would crash
        # at its first status publish instead of here.
        raise ValueError(
            f"[[bots]] {bot.bot_id}: strategy {bot.strategy!r} ({strategy_path}) has no "
            "`last_data_ns` for the heartbeat"
        )
    return strategy
