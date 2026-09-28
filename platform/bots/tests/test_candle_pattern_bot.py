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
A `candle_pattern` paper bot (Story 27.8): the host resolves it by string path through Nautilus's
`StrategyFactory`, next to a dummy bot on the one Sandbox venue, with `order_id_tag` pinned to its
`bot_id` (AD-11). Builds a real `TradingNode`, so it needs Redis at `REDIS_URL` (as
`test_node.py`). The strategy is recognised by its type name: bots never imports research.
"""

import os
from decimal import Decimal
from typing import Any

import pytest

from bots.domain.config import BotConfig
from bots.domain.config import PaperConfig
from bots.domain.config import PaperFleet
from bots.infrastructure.cache_reader import StrategyCacheReader
from bots.infrastructure.nautilus_host import STRATEGIES
from bots.infrastructure.nautilus_host import _strategy_for
from bots.infrastructure.nautilus_host import build_node
from bots.tests.test_node import _dispose
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig


_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")


def _fleet(params: dict[str, Any]) -> PaperFleet:
    return PaperFleet(
        PaperConfig(
            log_level="ERROR",
            bots=(
                BotConfig(bot_id="bot-01"),
                BotConfig(
                    bot_id="candle-01",
                    instrument_id="ETH-USD-PERP.DYDX",
                    trade_size=Decimal("0.02"),
                    strategy="candle_pattern",
                    params=params,
                ),
            ),
        )
    )


def test_a_candle_pattern_bot_builds_beside_a_dummy_bot_on_the_sandbox_venue() -> None:
    params = {"long_patterns": ["HAMMER"], "trend_condition": "any", "exit_bars": 4}
    node, hosted = build_node(_fleet(params), _REDIS_URL)
    try:
        assert isinstance(node._config.exec_clients["DYDX"], SandboxExecutionClientConfig)
        (_, dummy), (bot, candle) = hosted
        assert type(dummy).__name__ == "DummyStrategy"
        assert type(candle).__name__ == "CandlePatternStrategy"
        assert bot.bot_id == "candle-01"
        config = candle.config
        assert config.order_id_tag == "candle-01"
        assert str(candle.id).endswith("-candle-01")
        assert str(config.instrument_id) == "ETH-USD-PERP.DYDX"
        assert config.trade_size == Decimal("0.02")
        assert (config.long_patterns, config.trend_condition, config.exit_bars) == (
            ("HAMMER",),
            "any",
            4,
        )
        reader = StrategyCacheReader(candle)
        assert reader.strategy_name == "CandlePatternStrategy"
        assert reader.symbol == "ETH-USD-PERP.DYDX"
        assert reader.last_data_ns == 0
    finally:
        _dispose(node)


def test_an_unknown_params_key_fails_the_build_naming_the_bot() -> None:
    with pytest.raises(ValueError, match=r"\[\[bots\]\] candle-01: .*unknown field `exit_bar`"):
        build_node(_fleet({"exit_bar": 5}), _REDIS_URL)


def test_a_bad_strategy_config_fails_the_build_naming_the_bot() -> None:
    with pytest.raises(ValueError, match=r"candle-01: .*never fires bearish"):
        build_node(_fleet({"short_patterns": ["HAMMER"]}), _REDIS_URL)


@pytest.mark.parametrize("name", [name for name, paths in STRATEGIES.items() if paths is not None])
def test_every_string_path_strategy_builds_from_the_keys_the_host_passes(name: str) -> None:
    """Redis-free, through the host's own build: its keys suffice and the heartbeat field exists."""
    bot = BotConfig(
        bot_id="bot-x",
        instrument_id="BTC-USD-PERP.DYDX",
        trade_size=Decimal("0.001"),
        strategy=name,
    )
    strategy = _strategy_for(bot)
    assert strategy.config.order_id_tag == "bot-x"
    assert type(strategy.config).__struct_config__.forbid_unknown_fields
    assert strategy.last_data_ns == 0


def test_a_directly_built_bot_with_an_unknown_strategy_fails_naming_it() -> None:
    fleet = PaperFleet(
        PaperConfig(log_level="ERROR", bots=(BotConfig(bot_id="x-01", strategy="nope"),))
    )
    with pytest.raises(ValueError, match=r"\[\[bots\]\] x-01: unknown strategy 'nope'"):
        build_node(fleet, _REDIS_URL)
