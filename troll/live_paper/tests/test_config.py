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
from decimal import Decimal

import pytest

from live_paper.config import PaperConfig
from live_paper.config import RealMoneyConfig
from live_paper.config import load_paper_config
from live_paper.config import load_real_money_config
from live_paper.config import resolve_config
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


def _write(tmp_path, name: str, content: str):
    path = tmp_path / name
    path.write_text(content)
    return path


def test_default_paper_config_loads_paper_mode(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\n')
    config = load_paper_config(path)
    assert isinstance(config, PaperConfig)
    assert config.network == DydxNetwork.MAINNET


def test_resolve_config_with_no_real_money_path_returns_paper(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\n')
    config, is_real_money = resolve_config(path, real_money_path=None)
    assert is_real_money is False
    assert isinstance(config, PaperConfig)


def test_paper_config_rejects_a_mode_field(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\nmode = "real_money"\n')
    with pytest.raises(ValueError, match="must not contain a 'mode' field"):
        load_paper_config(path)


def test_real_money_config_requires_explicit_mode_field(tmp_path) -> None:
    path = _write(tmp_path, "real_money.toml", 'network = "mainnet"\n')
    with pytest.raises(ValueError, match='mode = "real_money"'):
        load_real_money_config(path)


def test_real_money_config_rejects_wrong_mode_value(tmp_path) -> None:
    path = _write(tmp_path, "real_money.toml", 'mode = "paper"\nnetwork = "mainnet"\n')
    with pytest.raises(ValueError, match='mode = "real_money"'):
        load_real_money_config(path)


def test_real_money_config_loads_when_mode_is_explicit(tmp_path) -> None:
    path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nnetwork = "mainnet"\nsubaccount = 0\n',
    )
    config = load_real_money_config(path)
    assert isinstance(config, RealMoneyConfig)
    assert config.mode == "real_money"


def test_resolve_config_with_real_money_path_returns_real_money(tmp_path) -> None:
    paper_path = _write(tmp_path, "config.toml", 'network = "mainnet"\n')
    real_money_path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nnetwork = "mainnet"\n',
    )
    config, is_real_money = resolve_config(paper_path, real_money_path=str(real_money_path))
    assert is_real_money is True
    assert isinstance(config, RealMoneyConfig)


def test_resolve_config_with_empty_string_real_money_path_returns_paper(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\n')
    config, is_real_money = resolve_config(path, real_money_path="")
    assert is_real_money is False
    assert isinstance(config, PaperConfig)


def test_paper_config_rejects_a_bare_string_starting_balances(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'network = "mainnet"\nstarting_balances = "10_000 USDC"\n',
    )
    with pytest.raises(ValueError, match="must be a TOML array"):
        load_paper_config(path)


def test_default_paper_config_has_instrument_id_and_trade_size(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\n')
    config = load_paper_config(path)
    assert config.instrument_id == "BTC-USD-PERP.DYDX"
    assert config.trade_size == Decimal("0.001")


def test_paper_config_reads_explicit_instrument_id_and_trade_size(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'network = "mainnet"\ninstrument_id = "ETH-USD-PERP.DYDX"\ntrade_size = "0.05"\n',
    )
    config = load_paper_config(path)
    assert config.instrument_id == "ETH-USD-PERP.DYDX"
    assert config.trade_size == Decimal("0.05")


def test_paper_config_rejects_an_unquoted_trade_size(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\ntrade_size = 0.05\n')
    with pytest.raises(ValueError, match="trade_size must be a quoted TOML string"):
        load_paper_config(path)


def test_paper_config_reads_explicit_thresholds(tmp_path) -> None:
    path = _write(
        tmp_path,
        "config.toml",
        'network = "mainnet"\n'
        "trend_buy_threshold = 0.7\ntrend_sell_threshold = 0.3\nofi_confirm_threshold = 1.5\n",
    )
    config = load_paper_config(path)
    assert config.trend_buy_threshold == 0.7
    assert config.trend_sell_threshold == 0.3
    assert config.ofi_confirm_threshold == 1.5


def test_real_money_config_rejects_an_unquoted_trade_size(tmp_path) -> None:
    path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nnetwork = "mainnet"\ntrade_size = 0.05\n',
    )
    with pytest.raises(ValueError, match="trade_size must be a quoted TOML string"):
        load_real_money_config(path)


def test_default_paper_config_has_bot_id(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\n')
    config = load_paper_config(path)
    assert config.bot_id == "bot-01"


def test_paper_config_reads_explicit_bot_id(tmp_path) -> None:
    path = _write(tmp_path, "config.toml", 'network = "mainnet"\nbot_id = "bot-btc"\n')
    config = load_paper_config(path)
    assert config.bot_id == "bot-btc"


def test_real_money_config_reads_explicit_bot_id(tmp_path) -> None:
    path = _write(
        tmp_path,
        "real_money.toml",
        'mode = "real_money"\nnetwork = "mainnet"\nbot_id = "bot-btc-live"\n',
    )
    config = load_real_money_config(path)
    assert config.bot_id == "bot-btc-live"
