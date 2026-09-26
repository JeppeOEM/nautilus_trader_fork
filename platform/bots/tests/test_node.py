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
Tests for bots.infrastructure.nautilus_host -- the one module that builds a `TradingNode`
(AD-8/AD-11). Only it imports `TradingNode` (platform/tests/test_boundaries.py), so these tests
recognise the node by its type name rather than importing the class.
"""

import asyncio
import os
from typing import Any

import pytest

from bots.domain.config import BotConfig
from bots.domain.config import ExecBot
from bots.domain.config import ExecConfig
from bots.domain.config import PaperConfig
from bots.domain.config import PaperFleet
from bots.infrastructure.nautilus_host import _cache_config
from bots.infrastructure.nautilus_host import build_node
from nautilus_trader.adapters.bybit.config import BybitExecClientConfig
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.core.nautilus_pyo3 import BybitProductType
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment


# The same REDIS_URL the entrypoint reads: the node's Redis-backed Cache connects at build.
_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")


def _paper_config(*bots: BotConfig) -> PaperFleet:
    return PaperFleet(
        PaperConfig(
            log_level="ERROR",
            bots=bots or (BotConfig(bot_id="bot-01"),),
        )
    )


def _build(fleet: PaperFleet | ExecBot, redis_url: str = _REDIS_URL) -> Any:
    node, _hosted = build_node(fleet, redis_url)
    return node


def _dispose(node: Any) -> None:
    """
    Any task scheduled on the node's loop (bots/__main__.py schedules one Supervisor.run()/
    HistoryPublisher.run() task per bot) is never driven by these synchronous tests, so it
    never gets its first `.send()`.
    node.dispose() (nautilus_trader/live/node.py) closes the loop itself, so
    cancelling has to happen here, first -- doing it after dispose() is too late
    (the closed loop can no longer run anything). Un-run + cancelled-without-ever-
    stepping is exactly what triggers Python's "coroutine was never awaited"
    RuntimeWarning on GC; running the cancellation once via gather() gives each
    coroutine the one step it needs to receive CancelledError instead
    (platform/CLAUDE.md TEST-04: fixed at the source, not filtered away).
    """
    loop = node.get_event_loop()
    if loop is not None and not loop.is_closed():
        pending = asyncio.all_tasks(loop=loop)
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    node.dispose()


def test_paper_config_builds_node_with_sandbox_exec_client_and_one_strategy_per_bot() -> None:
    node = _build(_paper_config(BotConfig(bot_id="bot-01"), BotConfig(bot_id="bot-02")))
    try:
        assert type(node).__name__ == "TradingNode"
        # `_config` is TradingNode's own stored constructor argument (no public accessor
        # exists) -- reading it back here is state inspection, not mocking (platform/CLAUDE.md
        # TEST-03 bars mocking Nautilus internals, not reading real, already-built state).
        exec_client_config = node._config.exec_clients["DYDX"]
        assert isinstance(exec_client_config, SandboxExecutionClientConfig)
        assert not isinstance(exec_client_config, DydxExecClientConfig)
        # AD-11: one TradingNode/exec client, but one Strategy per configured bot.
        assert len(node.trader.strategy_states()) == 2
    finally:
        _dispose(node)


def test_paper_config_pins_strategy_id_to_bot_id_not_insertion_order() -> None:
    # AD-11: order_id_tag must come from bot_id, never Trader.add_strategy()'s
    # insertion-order auto-increment default -- otherwise reordering [[bots]] entries
    # between deploys would silently reassign a bot's Cache/Redis history.
    node = _build(_paper_config(BotConfig(bot_id="bot-02"), BotConfig(bot_id="bot-01")))
    try:
        strategy_ids = {str(strategy_id) for strategy_id in node.trader.strategy_states()}
        assert any(sid.endswith("-bot-02") for sid in strategy_ids)
        assert any(sid.endswith("-bot-01") for sid in strategy_ids)
    finally:
        _dispose(node)


def _exec_config(**overrides: str | int) -> ExecBot:
    kwargs: dict = {
        "mode": "real_money",
        "environment": "mainnet",
        "subaccount": 0,
        "log_level": "ERROR",
    }
    return ExecBot(ExecConfig(**{**kwargs, **overrides}))


def test_real_money_config_builds_node_with_dydx_exec_client() -> None:
    node = _build(_exec_config(subaccount=3))
    try:
        exec_client_config = node._config.exec_clients["DYDX"]
        assert isinstance(exec_client_config, DydxExecClientConfig)
        assert exec_client_config.environment == DydxNetwork.MAINNET
        assert exec_client_config.subaccount == 3
    finally:
        _dispose(node)


@pytest.mark.parametrize(
    ("instrument_id", "product_type"),
    [
        ("BTCUSDT-LINEAR.BYBIT", BybitProductType.LINEAR),
        ("BTCUSDT-SPOT.BYBIT", BybitProductType.SPOT),
    ],
)
def test_exchange_demo_builds_a_bybit_demo_exec_client(instrument_id, product_type) -> None:
    node = _build(
        _exec_config(mode="exchange_demo", environment="demo", instrument_id=instrument_id)
    )
    try:
        exec_client_config = node._config.exec_clients["BYBIT"]
        assert isinstance(exec_client_config, BybitExecClientConfig)
        assert exec_client_config.environment == BybitEnvironment.DEMO
        assert exec_client_config.product_types == (product_type,)
        # Credentials must reach the client from the environment only (the Rust client
        # picks BYBIT_DEMO_API_KEY/SECRET off `environment`), never from the config file.
        assert exec_client_config.api_key is None
        assert exec_client_config.api_secret is None
        # Bybit Demo has no WS Trade API -- orders must go over HTTP.
        assert exec_client_config.use_ws_execution_fast is False
    finally:
        _dispose(node)


def test_exchange_demo_builds_a_hyperliquid_testnet_exec_client() -> None:
    node = _build(
        _exec_config(
            mode="exchange_demo",
            environment="testnet",
            instrument_id="BTC-USD-PERP.HYPERLIQUID",
        )
    )
    try:
        exec_client_config = node._config.exec_clients["HYPERLIQUID"]
        assert isinstance(exec_client_config, HyperliquidExecClientConfig)
        assert exec_client_config.environment == HyperliquidEnvironment.TESTNET
        assert exec_client_config.private_key is None
    finally:
        _dispose(node)


def test_explicit_path_builds_only_that_bots_venue_clients() -> None:
    node = _build(
        _exec_config(mode="exchange_demo", environment="demo", instrument_id="BTCUSDT-LINEAR.BYBIT")
    )
    try:
        assert set(node._config.data_clients) == {"BYBIT"}
        assert set(node._config.exec_clients) == {"BYBIT"}
    finally:
        _dispose(node)


def test_build_node_configures_redis_backed_cache() -> None:
    # Story 4.6, AC1: Cache must be Redis-backed (durable), host/port derived from the
    # same REDIS_URL the bots' bus connects to (parsed via urlparse) -- not a second,
    # independently hardcoded literal. Host-dependent: asserts the default port 6379.
    node = _build(_paper_config())
    try:
        cache_config = node._config.cache
        assert cache_config is not None
        assert cache_config.database is not None
        assert cache_config.database.type == "redis"
        assert cache_config.database.host == "127.0.0.1"
        assert cache_config.database.port == 6379
    finally:
        _dispose(node)


def test_build_node_passes_redis_credentials_and_ssl_from_url() -> None:
    # A hardened deployment's REDIS_URL carries auth/TLS; DatabaseConfig(host, port) alone
    # (the pre-fix behavior) silently dropped them, so the Cache's own Redis connection
    # would fail auth even though the bus's separate aioredis.from_url(REDIS_URL)
    # connection -- which does use the full URL -- kept working, masking the problem.
    # Host-dependent: building the node connects to that (unreachable) Redis.
    node = _build(_paper_config(), "rediss://user:secret@myhost:6380")
    try:
        db = node._config.cache.database
        assert db.host == "myhost"
        assert db.port == 6380
        assert db.username == "user"
        assert db.password == "secret"
        assert db.ssl is True
    finally:
        _dispose(node)


def test_cache_config_carries_the_redis_url_credentials_and_tls() -> None:
    # The pure half of the two deselected tests above: runs on every host, no node, no Redis.
    db = _cache_config("rediss://user:secret@myhost:6380").database
    assert db is not None
    assert (db.type, db.host, db.port) == ("redis", "myhost", 6380)
    assert (db.username, db.password, db.ssl) == ("user", "secret", True)
    plain = _cache_config("redis://127.0.0.1:6379/0").database
    assert plain is not None
    assert (plain.host, plain.port, plain.username, plain.ssl) == ("127.0.0.1", 6379, None, False)


def test_cache_config_refuses_a_redis_database_number() -> None:
    # DatabaseConfig has no database field: the Cache would silently use 0 while the bus used 2.
    with pytest.raises(ValueError, match="database '2'"):
        _cache_config("redis://127.0.0.1:6379/2")


def test_cache_config_refuses_a_redis_database_number_in_the_query() -> None:
    # redis-py honours `?db=` (over the path), so the query is the same split by another spelling.
    with pytest.raises(ValueError, match="database '2'"):
        _cache_config("redis://127.0.0.1:6379?db=2")
    with pytest.raises(ValueError, match="database '3'"):
        _cache_config("redis://127.0.0.1:6379/0?db=3")
    assert _cache_config("redis://127.0.0.1:6379?db=0").database is not None


def test_paper_config_builds_one_data_and_one_sandbox_client_per_venue_in_use() -> None:
    node = _build(
        _paper_config(
            BotConfig(bot_id="dydx", instrument_id="BTC-USD-PERP.DYDX"),
            BotConfig(bot_id="bybit-lin", instrument_id="BTCUSDT-LINEAR.BYBIT"),
            BotConfig(bot_id="bybit-spot", instrument_id="BTCUSDT-SPOT.BYBIT"),
            BotConfig(bot_id="hl", instrument_id="BTC-USD-PERP.HYPERLIQUID"),
        )
    )
    try:
        venues = {"DYDX", "BYBIT", "HYPERLIQUID"}
        assert set(node._config.data_clients) == venues
        assert set(node._config.exec_clients) == venues
        assert all(
            isinstance(c, SandboxExecutionClientConfig) for c in node._config.exec_clients.values()
        )
        assert node._config.exec_clients["BYBIT"].starting_balances == ["10_000 USDT"]
        assert node._config.exec_clients["HYPERLIQUID"].starting_balances == ["10_000 USDC"]
        assert len(node.trader.strategy_states()) == 4
    finally:
        _dispose(node)


def test_only_venues_with_a_bot_get_clients() -> None:
    node = _build(_paper_config(BotConfig(bot_id="b", instrument_id="BTCUSDT-SPOT.BYBIT")))
    try:
        assert set(node._config.data_clients) == {"BYBIT"}
    finally:
        _dispose(node)


def test_every_bot_is_hosted_with_its_strategy_tagged_by_its_bot_id() -> None:
    fleet = _paper_config(BotConfig(bot_id="bot-02"), BotConfig(bot_id="bot-01"))
    node, hosted = build_node(fleet, _REDIS_URL)
    try:
        assert [bot.bot_id for bot, _strategy in hosted] == ["bot-02", "bot-01"]
        assert all(str(strategy.id).endswith(f"-{bot.bot_id}") for bot, strategy in hosted)
    finally:
        _dispose(node)


def test_the_exec_path_hosts_exactly_its_one_bot() -> None:
    node, hosted = build_node(_exec_config(bot_id="bot-live"), _REDIS_URL)
    try:
        assert [bot.bot_id for bot, _strategy in hosted] == ["bot-live"]
        assert len(node.trader.strategy_states()) == 1
    finally:
        _dispose(node)
