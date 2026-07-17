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
Live/paper-trading configuration -- a structurally isolated paper vs real-money split.

PaperConfig and RealMoneyConfig are deliberately two separate dataclasses with two separate
loaders, not one schema with an optional `mode` field. Story 3.1 AC4 requires real-money
execution be unreachable by any default or accidental config state -- a single toggleable
field would let a stray `mode = "real_money"` line copy-pasted into the default, committed
config.toml silently promote to real money. Two independent signals must agree instead:

  1. which file is loaded -- the default entrypoint (node.py) only ever reads config.toml
     unless an operator explicitly sets LIVE_PAPER_REAL_MONEY_CONFIG to a different path
     (no default value for that env var), and
  2. that file's own loader understanding the concept of "real money" at all --
     load_paper_config() has no notion of `mode`, so an unexpected `mode` key in the
     default file is a hard error, never silently ignored or acted on.

This means neither signal alone can reach real money: a typo'd env var still loads
config.toml via load_paper_config (fails closed if that file happens to contain a stray
`mode` key). A real-money file loaded via load_paper_config (wrong loader) also fails
closed, since load_paper_config rejects any `mode` key outright.
"""

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


@dataclass(frozen=True)
class PaperConfig:
    network: DydxNetwork
    starting_balances: tuple[str, ...]
    account_type: str
    log_level: str
    instrument_id: str = "BTC-USD-PERP.DYDX"
    trade_size: Decimal = Decimal("0.001")
    trend_buy_threshold: float = 0.6
    trend_sell_threshold: float = 0.4
    ofi_confirm_threshold: float = 0.0


@dataclass(frozen=True)
class RealMoneyConfig:
    mode: str
    network: DydxNetwork
    subaccount: int
    log_level: str
    instrument_id: str = "BTC-USD-PERP.DYDX"
    trade_size: Decimal = Decimal("0.001")
    trend_buy_threshold: float = 0.6
    trend_sell_threshold: float = 0.4
    ofi_confirm_threshold: float = 0.0


def _parse_trade_size(raw: dict, path: Path) -> Decimal:
    trade_size = raw.get("trade_size", "0.001")
    if not isinstance(trade_size, str):
        raise ValueError(
            f'{path}: trade_size must be a quoted TOML string (e.g. "0.001"), got '
            f"{trade_size!r} -- an unquoted TOML float would round-trip through float64 "
            "before becoming a Decimal, risking precision drift (AD-5)."
        )
    return Decimal(trade_size)


def load_paper_config(path: Path) -> PaperConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    if "mode" in raw:
        raise ValueError(
            f"{path}: paper config must not contain a 'mode' field (found {raw['mode']!r}) -- "
            "real-money execution is a separate file with its own loader, never a field "
            "toggle inside the default config (see this module's docstring)."
        )

    starting_balances = raw.get("starting_balances", ["10_000 USDC"])
    if isinstance(starting_balances, str):
        raise ValueError(
            f"{path}: starting_balances must be a TOML array (e.g. "
            f'["10_000 USDC"]), got a bare string {starting_balances!r} -- '
            "a missing pair of brackets here would otherwise silently split it into "
            "individual characters.",
        )

    return PaperConfig(
        network=DydxNetwork.from_str(raw.get("network", "mainnet").lower()),  # type: ignore[attr-defined]
        starting_balances=tuple(starting_balances),
        account_type=raw.get("account_type", "MARGIN"),
        log_level=raw.get("log_level", "INFO"),
        instrument_id=raw.get("instrument_id", "BTC-USD-PERP.DYDX"),
        trade_size=_parse_trade_size(raw, path),
        trend_buy_threshold=raw.get("trend_buy_threshold", 0.6),
        trend_sell_threshold=raw.get("trend_sell_threshold", 0.4),
        ofi_confirm_threshold=raw.get("ofi_confirm_threshold", 0.0),
    )


def load_real_money_config(path: Path) -> RealMoneyConfig:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    mode = raw.get("mode")
    if mode != "real_money":
        raise ValueError(
            f'{path}: real-money config must set mode = "real_money" explicitly '
            f"(found {mode!r}) -- refusing to start rather than guessing operator intent.",
        )

    return RealMoneyConfig(
        mode=mode,
        network=DydxNetwork.from_str(raw.get("network", "mainnet").lower()),  # type: ignore[attr-defined]
        subaccount=raw.get("subaccount", 0),
        log_level=raw.get("log_level", "INFO"),
        instrument_id=raw.get("instrument_id", "BTC-USD-PERP.DYDX"),
        trade_size=_parse_trade_size(raw, path),
        trend_buy_threshold=raw.get("trend_buy_threshold", 0.6),
        trend_sell_threshold=raw.get("trend_sell_threshold", 0.4),
        ofi_confirm_threshold=raw.get("ofi_confirm_threshold", 0.0),
    )


def resolve_config(
    paper_path: Path,
    real_money_path: str | None,
) -> tuple[PaperConfig | RealMoneyConfig, bool]:
    """
    Resolve which config to load and construct it.

    `real_money_path` must come from an explicit, no-default source (an env var with no
    fallback value) at the caller -- never defaulted here. Returns `(config, is_real_money)`.
    An empty string (e.g. an env var set but left blank) is treated the same as unset --
    otherwise `Path("")` resolves to the current directory, which fails on open() with a
    confusing `IsADirectoryError` rather than a clear "not enabled" outcome.
    """
    if not real_money_path:
        return load_paper_config(paper_path), False

    return load_real_money_config(Path(real_money_path)), True
