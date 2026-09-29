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
The two bot config loaders -- two files, two loaders, two aggregate types (DDD spine AD-D15; see
`bots.domain.config` for the invariants each aggregate holds).

Story 3.1 AC4 requires real-money execution be unreachable by any default or accidental config
state, so two independent signals must agree before anything signs an order:

  1. which file is loaded -- `bots.__main__` only ever reads the paper `config.toml` unless the
     operator explicitly sets `LIVE_PAPER_REAL_MONEY_CONFIG` to a different path (no default), and
  2. that file's loader understanding "real money" at all -- `load_paper_config` has no notion of
     `mode`, so a `mode` key in the paper file is a hard error, never silently ignored.

Neither alone reaches real money: a typo'd env var still loads the paper file, and a real-money
file handed to the paper loader fails closed on its `mode` key. Every check fails closed and names
the file and the offending value; no loader ever reads a credential.
"""

import tomllib
from collections.abc import Callable
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

from kernel.venues import venue_of

from bots.domain.config import VENUE_RULES
from bots.domain.config import BotConfig
from bots.domain.config import ExecBot
from bots.domain.config import ExecConfig
from bots.domain.config import PaperConfig
from bots.domain.config import PaperFleet
from bots.domain.config import VenuePaperConfig
from bots.domain.config import venue_rules
from bots.infrastructure.nautilus_host import VENUES
from bots.infrastructure.nautilus_host import check_strategy


def _reject_unknown_keys(raw: dict, config_cls: type, path: Path, where: str = "") -> None:
    """
    Reject keys the dataclass does not have: a typo'd key (`subaccont`) would otherwise silently
    fall back to its default -- for a real-money config that could route trades through the wrong
    subaccount.
    """
    unknown = sorted(set(raw) - {f.name for f in fields(config_cls)})
    if unknown:
        raise ValueError(f"{path}: unknown key(s) {unknown}{where}")


def _parse_trade_size(raw: dict, key: str, path: Path) -> Decimal:
    trade_size = raw.get(key, "0.001")
    if not isinstance(trade_size, str):
        raise ValueError(
            f'{path}: {key} must be a quoted TOML string (e.g. "0.001"), got '
            f"{trade_size!r} -- an unquoted TOML float would round-trip through float64 "
            "before becoming a Decimal, risking precision drift (AD-5)."
        )
    return Decimal(trade_size)


def _parse_venue(venue: str, raw: dict, path: Path) -> VenuePaperConfig:
    quote = _named(path, venue_rules, venue).paper_quote_currency
    _reject_unknown_keys(raw, VenuePaperConfig, path, f" in [venues.{venue}]")
    environment = raw.get("environment", "mainnet")
    if not isinstance(environment, str):
        raise ValueError(f"{path}: [venues.{venue}] environment must be a string")
    starting_balances = raw.get("starting_balances", [f"10_000 {quote}"])
    if isinstance(starting_balances, str):
        raise ValueError(
            f"{path}: [venues.{venue}] starting_balances must be a TOML array (e.g. "
            f'["10_000 {quote}"]), got a bare string '
            f"{starting_balances!r} -- a missing pair of brackets here would otherwise "
            "silently split it into individual characters.",
        )
    if (
        not isinstance(starting_balances, list)
        or not starting_balances
        or not all(isinstance(b, str) for b in starting_balances)
    ):
        raise ValueError(
            f"{path}: [venues.{venue}] starting_balances must be a non-empty array of strings"
        )
    account_type = raw.get("account_type", "MARGIN")
    if account_type not in ("MARGIN", "CASH"):
        raise ValueError(f"{path}: [venues.{venue}] account_type must be MARGIN or CASH")
    return VenuePaperConfig(
        environment=environment.lower(),
        starting_balances=tuple(starting_balances),
        account_type=account_type,
    )


# `DummyStrategy`'s tunables, which live on `BotConfig` itself: set on a bot running another
# strategy they would be silently ignored, so they are refused instead (DATA-07).
_DUMMY_ONLY_KEYS = (
    "trend_buy_threshold",
    "trend_sell_threshold",
    "ofi_confirm_threshold",
    "take_profit_bps",  # Story 29.6: bracket exits are a DummyStrategy feature
    "stop_loss_bps",
)


def _parse_strategy(raw_bot: dict, path: Path) -> tuple[str, dict]:
    """Return the bot's `strategy` name and `params` table, each checked for its TOML type."""
    where = f"[[bots]] {raw_bot['bot_id']}"
    strategy = raw_bot.get("strategy", "dummy")
    if not isinstance(strategy, str):
        raise ValueError(f"{path}: {where}: strategy must be a string, got {strategy!r}")
    params = raw_bot.get("params", {})
    if not isinstance(params, dict):
        raise ValueError(
            f"{path}: {where}: params must be a TOML table ([bots.params]), got {params!r}"
        )
    stray = [key for key in _DUMMY_ONLY_KEYS if key in raw_bot]
    if strategy != "dummy" and stray:
        raise ValueError(
            f"{path}: {where}: {stray} tune only the dummy strategy, not {strategy!r} -- "
            "set that strategy's parameters in [bots.params]"
        )
    return strategy, params


def _parse_bot(raw_bot: dict, path: Path) -> BotConfig:
    if "bot_id" not in raw_bot:
        raise ValueError(f"{path}: every [[bots]] entry must set bot_id")
    _reject_unknown_keys(raw_bot, BotConfig, path, " in [[bots]] entry")
    strategy, params = _parse_strategy(raw_bot, path)
    bot = _named(
        path,
        BotConfig,
        bot_id=raw_bot["bot_id"],
        instrument_id=raw_bot.get("instrument_id", "BTC-USD-PERP.DYDX"),
        trade_size=_parse_trade_size(raw_bot, "trade_size", path),
        trend_buy_threshold=raw_bot.get("trend_buy_threshold", 0.6),
        trend_sell_threshold=raw_bot.get("trend_sell_threshold", 0.4),
        ofi_confirm_threshold=raw_bot.get("ofi_confirm_threshold", 0.0),
        starting_balance=raw_bot.get("starting_balance", ""),
        strategy=strategy,
        params=params,
        take_profit_bps=raw_bot.get("take_profit_bps"),
        stop_loss_bps=raw_bot.get("stop_loss_bps"),
    )
    _named(path, check_strategy, bot)
    return bot


def _named[T](path: Path, build: Callable[..., T], *args: object, **kwargs: object) -> T:
    """Run a domain constructor/validator, prefixing its `ValueError` with the file's path."""
    try:
        return build(*args, **kwargs)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def load_paper_config(path: Path) -> PaperFleet:
    with path.open("rb") as f:
        raw = tomllib.load(f)

    if "mode" in raw:
        raise ValueError(
            f"{path}: paper config must not contain a 'mode' field (found {raw['mode']!r}) -- "
            "real-money execution is a separate file with its own loader, never a field "
            "toggle inside the default config (see this module's docstring)."
        )

    _reject_unknown_keys(raw, PaperConfig, path)
    raw_venues = raw.get("venues", {})
    if not isinstance(raw_venues, dict) or not all(
        isinstance(v, dict) for v in raw_venues.values()
    ):
        raise ValueError(f"{path}: [venues] must be a table of [venues.<VENUE>] tables")
    venues = {name: _parse_venue(name, v, path) for name, v in raw_venues.items()}
    bots = tuple(_parse_bot(raw_bot, path) for raw_bot in raw.get("bots") or ())
    config = PaperConfig(log_level=raw.get("log_level", "INFO"), bots=bots, venues=venues)
    return _named(path, PaperFleet, config)


def load_real_money_config(path: Path) -> ExecBot:
    """
    Load the explicitly-pathed, non-Sandbox file -- `real_money` or `exchange_demo` (both live
    behind the one env var because both build a venue exec client that really signs orders).
    """
    with path.open("rb") as f:
        raw = tomllib.load(f)

    instrument_id = raw.get("instrument_id", "BTC-USD-PERP.DYDX")
    venue = venue_of(instrument_id)
    # The key itself, not only a non-zero value: `subaccount = 0` on Bybit is still a file
    # written for another venue.
    if "subaccount" in raw and venue in VENUE_RULES and not VENUE_RULES[venue].has_subaccounts:
        raise ValueError(f"{path}: subaccount is dYdX-only, not valid for venue {venue}")
    _reject_unknown_keys(raw, ExecConfig, path)
    config = ExecConfig(
        mode=raw.get("mode"),  # type: ignore[arg-type]  # validated by ExecBot
        environment=str(raw.get("environment", "mainnet")).lower(),
        subaccount=raw.get("subaccount", 0),
        log_level=raw.get("log_level", "INFO"),
        instrument_id=instrument_id,
        trade_size=_parse_trade_size(raw, "trade_size", path),
        trend_buy_threshold=raw.get("trend_buy_threshold", 0.6),
        trend_sell_threshold=raw.get("trend_sell_threshold", 0.4),
        ofi_confirm_threshold=raw.get("ofi_confirm_threshold", 0.0),
        bot_id=raw.get("bot_id", "bot-01"),
    )
    bot = _named(path, ExecBot, config)
    # Venue-specific derivations (Bybit's product type from the id suffix) fail closed here, with
    # the file named, rather than inside build_node.
    _named(path, VENUES[venue].exec_kwargs, config)
    return bot


def resolve_config(
    paper_path: Path, real_money_path: str | None
) -> tuple[PaperFleet | ExecBot, bool]:
    """
    Load the paper fleet, or the exec bot when `real_money_path` is set. `real_money_path` must
    come from an explicit, no-default source (an env var with no fallback) at the caller -- never
    defaulted here. An empty string (an env var set but blank) counts as unset: `Path("")` is the
    current directory, which would fail on open() with a confusing `IsADirectoryError`.
    """
    if not real_money_path:
        return load_paper_config(paper_path), False
    return load_real_money_config(Path(real_money_path)), True
