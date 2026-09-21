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
import asyncio

import pytest

import live_paper.node
from live_paper.config import BotConfig
from live_paper.config import ExecConfig
from live_paper.config import PaperConfig
from live_paper.node import build_node
from nautilus_trader.adapters.bybit.config import BybitExecClientConfig
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.hyperliquid.config import HyperliquidExecClientConfig
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.core.nautilus_pyo3 import BybitEnvironment
from nautilus_trader.core.nautilus_pyo3 import BybitProductType
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import HyperliquidEnvironment
from nautilus_trader.live.node import TradingNode


def _paper_config(*bots: BotConfig) -> PaperConfig:
    return PaperConfig(
        log_level="ERROR",
        bots=bots or (BotConfig(bot_id="bot-01"),),
    )


def _dispose(node: TradingNode) -> None:
    """
    build_node() schedules one bot_status.run()/trade_history.run() task per
    configured bot via loop.create_task() (live_paper/node.py) -- these synchronous
    tests never drive the loop, so those tasks never get their first `.send()`.
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
    node = build_node(_paper_config(BotConfig(bot_id="bot-01"), BotConfig(bot_id="bot-02")))
    try:
        assert isinstance(node, TradingNode)
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
    node = build_node(_paper_config(BotConfig(bot_id="bot-02"), BotConfig(bot_id="bot-01")))
    try:
        strategy_ids = {str(strategy_id) for strategy_id in node.trader.strategy_states()}
        assert any(sid.endswith("-bot-02") for sid in strategy_ids)
        assert any(sid.endswith("-bot-01") for sid in strategy_ids)
    finally:
        _dispose(node)


def _exec_config(**overrides: str | int) -> ExecConfig:
    kwargs: dict = {
        "mode": "real_money",
        "environment": "mainnet",
        "subaccount": 0,
        "log_level": "ERROR",
    }
    return ExecConfig(**{**kwargs, **overrides})


def test_real_money_config_builds_node_with_dydx_exec_client() -> None:
    node = build_node(_exec_config(subaccount=3))
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
    node = build_node(
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
    node = build_node(
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
    node = build_node(
        _exec_config(mode="exchange_demo", environment="demo", instrument_id="BTCUSDT-LINEAR.BYBIT")
    )
    try:
        assert set(node._config.data_clients) == {"BYBIT"}
        assert set(node._config.exec_clients) == {"BYBIT"}
    finally:
        _dispose(node)


def test_build_node_configures_redis_backed_cache() -> None:
    # Story 4.6, AC1: Cache must be Redis-backed (durable), host/port derived from the
    # same REDIS_URL bot_status.py already connects to (node.py's module-level
    # _REDIS_URL, parsed via urlparse) -- not a second, independently hardcoded literal.
    node = build_node(_paper_config())
    try:
        cache_config = node._config.cache
        assert cache_config is not None
        assert cache_config.database is not None
        assert cache_config.database.type == "redis"
        assert cache_config.database.host == "127.0.0.1"
        assert cache_config.database.port == 6379
    finally:
        _dispose(node)


def test_build_node_passes_redis_credentials_and_ssl_from_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hardened deployment's REDIS_URL carries auth/TLS; DatabaseConfig(host, port) alone
    # (the pre-fix behavior) silently dropped them, so the Cache's own Redis connection
    # would fail auth even though bot_status.py's separate aioredis.from_url(REDIS_URL)
    # connection -- which does use the full URL -- kept working, masking the problem.
    monkeypatch.setattr(live_paper.node, "_REDIS_URL", "rediss://user:secret@myhost:6380")
    node = build_node(_paper_config())
    try:
        db = node._config.cache.database
        assert db.host == "myhost"
        assert db.port == 6380
        assert db.username == "user"
        assert db.password == "secret"
        assert db.ssl is True
    finally:
        _dispose(node)


def test_paper_config_builds_one_data_and_one_sandbox_client_per_venue_in_use() -> None:
    node = build_node(
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
    node = build_node(_paper_config(BotConfig(bot_id="b", instrument_id="BTCUSDT-SPOT.BYBIT")))
    try:
        assert set(node._config.data_clients) == {"BYBIT"}
    finally:
        _dispose(node)
