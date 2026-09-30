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
import dataclasses
import tomllib
from decimal import Decimal
from pathlib import Path

import pytest

from bots.domain.config import BotConfig
from bots.domain.config import ExecBot
from bots.domain.config import PaperFleet
from bots.infrastructure.config import _DUMMY_ONLY_KEYS
from bots.infrastructure.config import load_paper_config
from bots.infrastructure.config import load_real_money_config
from bots.infrastructure.config import resolve_config
from bots.strategies.dummy import DummyStrategyConfig


_ONE_BOT = '\n[[bots]]\nbot_id = "bot-01"\n'


def _write(tmp_path, name: str, content: str):
    path = tmp_path / name
    path.write_text(content)
    return path


def test_default_paper_config_loads_paper_mode(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", f'log_level = "INFO"\n{_ONE_BOT}')
    config = load_paper_config(path)
    assert isinstance(config, PaperFleet)
    assert config.venue_config("DYDX").environment == "mainnet"


def test_paper_config_requires_at_least_one_bot(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'log_level = "INFO"\n')
    with pytest.raises(ValueError, match="at least one \\[\\[bots\\]\\] entry"):
        load_paper_config(path)


def test_paper_config_rejects_duplicate_bot_ids(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'log_level = "INFO"\n[[bots]]\nbot_id = "bot-01"\n[[bots]]\nbot_id = "bot-01"\n',
    )
    with pytest.raises(ValueError, match="distinct bot_id"):
        load_paper_config(path)


def test_resolve_config_with_no_real_money_path_returns_paper(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", f'log_level = "INFO"\n{_ONE_BOT}')
    config, is_real_money = resolve_config(path, real_money_path=None)
    assert is_real_money is False
    assert isinstance(config, PaperFleet)


def test_paper_config_rejects_a_mode_field(tmp_path) -> None:
    path = _write(
        tmp_path, "config.toml", f'environment = "mainnet"\nmode = "real_money"\n{_ONE_BOT}'
    )
    with pytest.raises(ValueError, match="must not contain a 'mode' field"):
        load_paper_config(path)


@pytest.mark.parametrize("body", ['environment = "mainnet"\n', 'mode = "paper"\n'])
def test_exec_config_requires_one_of_the_two_explicit_modes(tmp_path, body) -> None:
    path = _write(tmp_path, "exec.toml", body)
    with pytest.raises(ValueError, match='mode = "real_money" or mode = "exchange_demo"'):
        load_real_money_config(path)


def test_real_money_config_loads_when_mode_is_explicit(tmp_path) -> None:
    path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nenvironment = "mainnet"\nsubaccount = 2\n',
    )
    config = load_real_money_config(path)
    assert isinstance(config, ExecBot)
    assert (config.config.mode, config.config.environment, config.config.subaccount) == (
        "real_money",
        "mainnet",
        2,
    )


@pytest.mark.parametrize(
    ("environment", "instrument_id"),
    [
        ("demo", "BTCUSDT-LINEAR.BYBIT"),
        ("demo", "BTCUSDT-SPOT.BYBIT"),
        ("testnet", "BTC-USD-PERP.HYPERLIQUID"),
        ("testnet", "BTC-USD-PERP.DYDX"),
    ],
)
def test_exchange_demo_loads_for_each_venue_play_money_environment(
    tmp_path, environment, instrument_id
) -> None:
    path = _write(
        tmp_path,
        "demo.toml",
        f'mode = "exchange_demo"\nenvironment = "{environment}"\n'
        f'instrument_id = "{instrument_id}"\n',
    )
    config = load_real_money_config(path)
    assert (config.config.mode, config.config.environment) == ("exchange_demo", environment)


@pytest.mark.parametrize(
    ("mode", "environment"),
    [("real_money", "testnet"), ("real_money", "demo"), ("exchange_demo", "mainnet")],
)
def test_mode_and_environment_must_agree(tmp_path, mode, environment) -> None:
    # The whole point of two keys: promoting a demo file to real money must take editing
    # both, so neither a stray `mode` nor a stray `environment` line can do it alone.
    path = _write(
        tmp_path,
        "exec.toml",
        f'mode = "{mode}"\nenvironment = "{environment}"\ninstrument_id = "BTCUSDT-LINEAR.BYBIT"\n',
    )
    with pytest.raises(ValueError, match=f"mode '{mode}'.*{environment}"):
        load_real_money_config(path)


@pytest.mark.parametrize("instrument_id", ["BTC-USD-PERP.DYDX", "BTC-USD-PERP.HYPERLIQUID"])
def test_demo_environment_is_rejected_on_a_venue_that_has_none(tmp_path, instrument_id) -> None:
    path = _write(
        tmp_path,
        "demo.toml",
        f'mode = "exchange_demo"\nenvironment = "demo"\ninstrument_id = "{instrument_id}"\n',
    )
    with pytest.raises(ValueError, match="environment 'demo' not in"):
        load_real_money_config(path)


@pytest.mark.parametrize("instrument_id", ["BTCUSDT-LINEAR.BYBIT", "BTC-USD-PERP.HYPERLIQUID"])
def test_subaccount_is_rejected_on_a_non_dydx_venue(tmp_path, instrument_id) -> None:
    path = _write(
        tmp_path,
        "demo.toml",
        'mode = "real_money"\nenvironment = "mainnet"\nsubaccount = 1\n'
        f'instrument_id = "{instrument_id}"\n',
    )
    with pytest.raises(ValueError, match="subaccount is dYdX-only"):
        load_real_money_config(path)


@pytest.mark.parametrize("instrument_id", ["BTCUSDT.BYBIT", "BTCUSDT-FUTURE.BYBIT"])
def test_bybit_exec_config_requires_a_product_type_suffix(tmp_path, instrument_id) -> None:
    # The suffix selects the exec client's product_types; without one, build_node would
    # only fail later with an AttributeError, so the loader rejects it with the file named.
    path = _write(
        tmp_path,
        "demo.toml",
        f'mode = "exchange_demo"\nenvironment = "demo"\ninstrument_id = "{instrument_id}"\n',
    )
    with pytest.raises(ValueError, match="must carry its product type as the symbol suffix"):
        load_real_money_config(path)


def test_resolve_config_with_real_money_path_returns_real_money(tmp_path) -> None:
    paper_path = _write(tmp_path, "config.toml", f'log_level = "INFO"\n{_ONE_BOT}')
    real_money_path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nenvironment = "mainnet"\n',
    )
    config, is_real_money = resolve_config(paper_path, real_money_path=str(real_money_path))
    assert is_real_money is True
    assert isinstance(config, ExecBot)


def test_resolve_config_with_empty_string_real_money_path_returns_paper(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", f'log_level = "INFO"\n{_ONE_BOT}')
    config, is_real_money = resolve_config(path, real_money_path="")
    assert is_real_money is False
    assert isinstance(config, PaperFleet)


def test_paper_config_rejects_a_bare_string_starting_balances(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        f'log_level = "INFO"\n[venues.DYDX]\nstarting_balances = "10_000 USDC"\n{_ONE_BOT}',
    )
    with pytest.raises(ValueError, match="must be a TOML array"):
        load_paper_config(path)


def test_default_bot_has_instrument_id_and_trade_size(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", f'log_level = "INFO"\n{_ONE_BOT}')
    config = load_paper_config(path)
    assert config.bots[0].instrument_id == "BTC-USD-PERP.DYDX"
    assert config.bots[0].trade_size == Decimal("0.001")


def test_bot_reads_explicit_instrument_id_and_trade_size(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'log_level = "INFO"\n'
        '[[bots]]\nbot_id = "bot-01"\ninstrument_id = "ETH-USD-PERP.DYDX"\ntrade_size = "0.05"\n',
    )
    config = load_paper_config(path)
    assert config.bots[0].instrument_id == "ETH-USD-PERP.DYDX"
    assert config.bots[0].trade_size == Decimal("0.05")


def test_bot_rejects_an_unquoted_trade_size(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'log_level = "INFO"\n[[bots]]\nbot_id = "bot-01"\ntrade_size = 0.05\n',
    )
    with pytest.raises(ValueError, match="trade_size must be a quoted TOML string"):
        load_paper_config(path)


def test_bot_reads_explicit_thresholds(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'log_level = "INFO"\n[[bots]]\nbot_id = "bot-01"\n'
        "trend_buy_threshold = 0.7\ntrend_sell_threshold = 0.3\nofi_confirm_threshold = 1.5\n",
    )
    config = load_paper_config(path)
    bot = config.bots[0]
    assert bot.trend_buy_threshold == 0.7
    assert bot.trend_sell_threshold == 0.3
    assert bot.ofi_confirm_threshold == 1.5


def test_multiple_bots_each_get_their_own_settings(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'log_level = "INFO"\n'
        '[[bots]]\nbot_id = "bot-01"\ntrend_buy_threshold = 0.6\n'
        '[[bots]]\nbot_id = "bot-02"\ntrend_buy_threshold = 0.51\n',
    )
    config = load_paper_config(path)
    assert [bot.bot_id for bot in config.bots] == ["bot-01", "bot-02"]
    assert config.bots[0].trend_buy_threshold == 0.6
    assert config.bots[1].trend_buy_threshold == 0.51


def test_real_money_config_rejects_an_unquoted_trade_size(tmp_path) -> None:
    path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nenvironment = "mainnet"\ntrade_size = 0.05\n',
    )
    with pytest.raises(ValueError, match="trade_size must be a quoted TOML string"):
        load_real_money_config(path)


def test_bot_requires_a_bot_id(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'log_level = "INFO"\n[[bots]]\ninstrument_id = "x"\n')
    with pytest.raises(ValueError, match="must set bot_id"):
        load_paper_config(path)


def test_bot_reads_explicit_bot_id(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'log_level = "INFO"\n[[bots]]\nbot_id = "bot-btc"\n')
    config = load_paper_config(path)
    assert config.bots[0].bot_id == "bot-btc"


def test_bot_default_starting_balance_anchor(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", f'log_level = "INFO"\n{_ONE_BOT}')
    config = load_paper_config(path)
    assert config.bots[0].starting_balance == "10_000 USDC"


def test_real_money_config_reads_explicit_bot_id(tmp_path) -> None:
    path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nenvironment = "mainnet"\nbot_id = "bot-btc-live"\n',
    )
    config = load_real_money_config(path)
    assert config.config.bot_id == "bot-btc-live"


def test_unknown_keys_are_rejected_in_every_loader(tmp_path: Path) -> None:
    paper = tmp_path / "paper.toml"
    paper.write_text('[[bots]]\nbot_id = "a"\ntrade_sizee = "1"\n')
    with pytest.raises(ValueError, match="trade_sizee"):
        load_paper_config(paper)

    real = tmp_path / "real.toml"
    real.write_text('mode = "real_money"\nsubaccont = 1\n')
    with pytest.raises(ValueError, match="subaccont"):
        load_real_money_config(real)


def test_default_starting_balance_uses_the_bots_venue_quote_currency(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        '[[bots]]\nbot_id = "a"\ninstrument_id = "BTCUSDT-LINEAR.BYBIT"\n'
        '[[bots]]\nbot_id = "b"\ninstrument_id = "BTC-USD-PERP.HYPERLIQUID"\n',
    )
    config = load_paper_config(path)
    assert [b.starting_balance for b in config.bots] == ["10_000 USDT", "10_000 USDC"]
    assert config.venue_config("BYBIT").starting_balances == ("10_000 USDT",)
    assert config.venue_config("HYPERLIQUID").starting_balances == ("10_000 USDC",)


def test_venue_table_is_parsed_per_venue(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        '[venues.BYBIT]\nenvironment = "DEMO"\nstarting_balances = ["5_000 USDT"]\n' + _ONE_BOT,
    )
    venue = load_paper_config(path).venue_config("BYBIT")
    assert (venue.environment, venue.starting_balances) == ("demo", ("5_000 USDT",))


def test_venue_environment_must_be_allowed_for_that_venue(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", '[venues.BYBIT]\nenvironment = "prod"\n' + _ONE_BOT)
    with pytest.raises(ValueError, match="environment 'prod'"):
        load_paper_config(path)


def test_demo_environment_is_bybit_only(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", '[venues.DYDX]\nenvironment = "demo"\n' + _ONE_BOT)
    with pytest.raises(ValueError, match="environment 'demo'"):
        load_paper_config(path)


def test_unknown_venue_table_and_unknown_venue_key_are_rejected(tmp_path) -> None:
    unknown_venue = _write(tmp_path, "a.toml", "[venues.KRAKEN]\n" + _ONE_BOT)
    with pytest.raises(ValueError, match="unsupported venue 'KRAKEN'"):
        load_paper_config(unknown_venue)
    unknown_key = _write(tmp_path, "b.toml", "[venues.DYDX]\nnetwork = 'x'\n" + _ONE_BOT)
    with pytest.raises(ValueError, match="unknown key"):
        load_paper_config(unknown_key)


@pytest.mark.parametrize(
    "body",
    [
        "environment = 1",
        "starting_balances = []",
        "starting_balances = [1]",
        "account_type = 'FOO'",
    ],
)
def test_malformed_venue_values_are_rejected_at_load(tmp_path, body) -> None:
    path = _write(tmp_path, "c.toml", f"[venues.DYDX]\n{body}\n" + _ONE_BOT)
    with pytest.raises(ValueError, match=r"\[venues.DYDX\]"):
        load_paper_config(path)


def test_bot_on_an_unsupported_venue_fails_at_load_naming_the_venue(tmp_path) -> None:
    path = _write(
        tmp_path, "config.toml", '[[bots]]\nbot_id = "a"\ninstrument_id = "BTC-USD.KRAKEN"\n'
    )
    with pytest.raises(ValueError, match="unsupported venue 'KRAKEN'"):
        load_paper_config(path)


# --- strategy / params (Story 27.8) ------------------------------------------------------------

_CHECKED_IN = Path(__file__).resolve().parents[1] / "config.toml"
_CANDLE_BOT = """
[[bots]]
bot_id = "candle-01"
instrument_id = "BTC-USD-PERP.DYDX"
trade_size = "0.002"
strategy = "candle_pattern"

[bots.params]
long_patterns = ["HAMMER", "ENGULFING"]
trend_condition = "above"
exit_bars = 5
"""


def test_the_checked_in_config_parses_with_every_bot_dummy() -> None:
    raw_bots = tomllib.loads(_CHECKED_IN.read_text())["bots"]
    parsed = {bot.bot_id: bot for bot in load_paper_config(_CHECKED_IN).bots}
    without_key = [raw["bot_id"] for raw in raw_bots if "strategy" not in raw]
    assert without_key, "the checked-in file sets no strategy key: every bot defaults"
    for bot_id in without_key:
        assert (parsed[bot_id].strategy, dict(parsed[bot_id].params)) == ("dummy", {}), bot_id


def test_a_candle_pattern_bot_with_a_params_table_parses(tmp_path: Path) -> None:
    config = load_paper_config(_write(tmp_path, "c.toml", _ONE_BOT + _CANDLE_BOT))
    dummy, candle = config.bots
    assert (dummy.strategy, dict(dummy.params)) == ("dummy", {})
    assert candle.strategy == "candle_pattern"
    assert dict(candle.params) == {
        "long_patterns": ("HAMMER", "ENGULFING"),
        "trend_condition": "above",
        "exit_bars": 5,
    }


def test_bot_params_are_read_only_all_the_way_down_and_the_bot_stays_hashable() -> None:
    bot = BotConfig(
        bot_id="candle-01",
        strategy="candle_pattern",
        params={"long_patterns": ["HAMMER"], "nested": {"levels": [1, 2]}},
    )
    with pytest.raises(TypeError):
        bot.params["exit_bars"] = 1  # type: ignore[index]
    with pytest.raises(AttributeError):
        bot.params["long_patterns"].append("ENGULFING")
    with pytest.raises(TypeError):
        bot.params["nested"]["levels"] = []
    assert bot.params["nested"]["levels"] == (1, 2)
    assert hash(bot) == hash(BotConfig(bot_id="candle-01", strategy="candle_pattern"))


@pytest.mark.parametrize(
    ("bot", "message"),
    [
        ('strategy = "candel_pattern"', r"candle-01: unknown strategy 'candel_pattern'"),
        ("strategy = 1", "strategy must be a string"),
        ('strategy = "dummy"\nparams = { exit_bars = 5 }', "takes no \\[bots.params\\]"),
        ('strategy = "candle_pattern"\nparams = "exit_bars=5"', "params must be a TOML table"),
        (
            'strategy = "candle_pattern"\ntrend_buy_threshold = 0.7',
            r"\['trend_buy_threshold'\] tune only the dummy strategy",
        ),
        (
            'strategy = "candle_pattern"\nparams = { order_id_tag = "x" }',
            r"may not set \['order_id_tag'\]",
        ),
        (
            'strategy = "candle_pattern"\nparams = { trade_size = "1", strategy_id = "S-1" }',
            r"may not set \['strategy_id', 'trade_size'\]",
        ),
        ('strategy = "candle_pattern"\nparams = { instrument_id = "X" }', "may not set"),
    ],
)
def test_a_bad_strategy_or_params_fails_at_load_naming_file_and_bot(
    tmp_path: Path, bot: str, message: str
) -> None:
    path = _write(tmp_path, "c.toml", f'[[bots]]\nbot_id = "candle-01"\n{bot}\n')
    with pytest.raises(ValueError, match=message) as raised:
        load_paper_config(path)
    assert str(path) in str(raised.value)
    assert "candle-01" in str(raised.value)


def test_the_dummy_only_keys_are_every_dummy_tunable_on_botconfig() -> None:
    # A new `DummyStrategy` tunable on `BotConfig` missing from `_DUMMY_ONLY_KEYS` would be
    # silently ignored on a bot running another strategy (DATA-07); the tuple follows the fields.
    shared = {"instrument_id", "trade_size"}  # every strategy gets these from `BotConfig`
    tunables = {f.name for f in dataclasses.fields(BotConfig)} & set(
        DummyStrategyConfig.__struct_fields__
    )
    assert set(_DUMMY_ONLY_KEYS) == tunables - shared


# --- Story 29.6: bracket-exit keys ---------------------------------------------------------------


def test_bot_reads_the_bracket_exit_keys(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        f"{_ONE_BOT}take_profit_bps = 25\nstop_loss_bps = 10\n",
    )
    bot = load_paper_config(path).bots[0]
    assert (bot.take_profit_bps, bot.stop_loss_bps) == (25, 10)


def test_bracket_exit_keys_default_to_none(tmp_path: Path) -> None:
    bot = load_paper_config(_write(tmp_path, "config.toml", _ONE_BOT)).bots[0]
    assert (bot.take_profit_bps, bot.stop_loss_bps) == (None, None)


@pytest.mark.parametrize(
    "line",
    [
        "take_profit_bps = 0",
        "take_profit_bps = -5",
        "stop_loss_bps = true",
        "stop_loss_bps = 1.5",
        'take_profit_bps = "5"',
        "stop_loss_bps = 10000",
        "take_profit_bps = 10000",
    ],
)
def test_invalid_bracket_exit_keys_fail_at_load_naming_the_file(tmp_path: Path, line: str) -> None:
    path = _write(tmp_path, "config.toml", f"{_ONE_BOT}{line}\n")
    with pytest.raises(ValueError, match=str(path)):
        load_paper_config(path)


def test_the_exec_config_has_no_bracket_exit_keys(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "demo.toml",
        'mode = "exchange_demo"\nenvironment = "testnet"\ntake_profit_bps = 5\n',
    )
    with pytest.raises(ValueError, match="unknown key"):
        load_real_money_config(path)


def test_the_committed_configs_parse() -> None:
    bots_dir = Path(__file__).resolve().parent.parent
    default = load_paper_config(bots_dir / "config.toml")
    assert all(bot.take_profit_bps is None for bot in default.bots)
    churn = load_paper_config(bots_dir / "tests" / "fixtures" / "config.churn.toml")
    (bot,) = churn.bots
    assert (bot.bot_id, bot.instrument_id) == ("churn-01", "BTC-USD-PERP.DYDX")
    assert (bot.take_profit_bps, bot.stop_loss_bps) == (5, 5)
    assert (bot.trend_buy_threshold, bot.trend_sell_threshold) == (0.0, -1.0)
    assert bot.ofi_confirm_threshold == -1e9
    assert churn.venue_config("DYDX").environment == "mainnet"


def test_the_verify_parity_fleet_is_one_paper_dummy_bot_per_verify_instrument() -> None:
    """
    Story 31.9: `bots/config.verify.toml` loads with the real loader and names exactly the
    instruments the verify collectors record, one `dummy` bot each (spot included).
    """
    platform = Path(__file__).resolve().parents[2]
    fleet = load_paper_config(platform / "bots" / "config.verify.toml")
    collected: set[str] = set()
    for venue in ("bybit", "hyperliquid"):
        with (platform / "capture" / "venues" / venue / "config.toml").open("rb") as f:
            collected |= set(tomllib.load(f)["instruments"])
    instruments = [bot.instrument_id for bot in fleet.config.bots]
    assert sorted(instruments) == sorted(collected)
    assert {bot.strategy for bot in fleet.config.bots} == {"dummy"}
    assert {name: venue.environment for name, venue in fleet.config.venues.items()} == {
        "BYBIT": "mainnet",
        "HYPERLIQUID": "mainnet",
    }
