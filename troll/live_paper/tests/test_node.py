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
import pytest
from nautilus_trader.adapters.dydx.config import DydxExecClientConfig
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.live.node import TradingNode

import live_paper.node
from live_paper.config import PaperConfig
from live_paper.config import RealMoneyConfig
from live_paper.node import build_node


def _paper_config() -> PaperConfig:
    return PaperConfig(
        network=DydxNetwork.MAINNET,
        starting_balances=("10_000 USDC",),
        account_type="MARGIN",
        log_level="ERROR",
    )


def test_paper_config_builds_node_with_sandbox_exec_client_and_zero_strategies() -> None:
    node = build_node(_paper_config())
    try:
        assert isinstance(node, TradingNode)
        # `_config` is TradingNode's own stored constructor argument (no public accessor
        # exists) -- reading it back here is state inspection, not mocking (troll/CLAUDE.md
        # TEST-03 bars mocking Nautilus internals, not reading real, already-built state).
        exec_client_config = node._config.exec_clients["DYDX"]
        assert isinstance(exec_client_config, SandboxExecutionClientConfig)
        assert not isinstance(exec_client_config, DydxExecClientConfig)
        # Story 3.2: the DummyStrategy is now attached by default (Story 3.1 built with
        # zero strategies since it predates the strategy's existence).
        assert len(node.trader.strategy_states()) == 1
    finally:
        node.dispose()


def test_real_money_config_builds_node_with_dydx_exec_client() -> None:
    config = RealMoneyConfig(
        mode="real_money", network=DydxNetwork.MAINNET, subaccount=0, log_level="ERROR"
    )
    node = build_node(config)
    try:
        exec_client_config = node._config.exec_clients["DYDX"]
        assert isinstance(exec_client_config, DydxExecClientConfig)
    finally:
        node.dispose()


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
        node.dispose()


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
        node.dispose()
