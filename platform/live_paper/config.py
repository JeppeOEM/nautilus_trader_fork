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

Architecture AD-11: one `live_paper` process runs ONE `TradingNode` (one data client
and one shared simulated-balance pool per venue in use) hosting every configured paper bot --
`PaperConfig.bots` is a tuple of `BotConfig`, one per bot, each getting its own
`Strategy` instance and its own `bots:status`/`bots:history:*` identity. This is
paper-only: `ExecConfig` deliberately stays single-bot (its own account/subaccount,
never sharing a pool with other bots), so the multi-bot shape below is not mirrored
there.

`ExecConfig` covers BOTH non-Sandbox modes -- `real_money` (a mainnet account, real
funds) and `exchange_demo` (the venue's own play-money account: Bybit Demo, Hyperliquid
or dYdX testnet). They share one dataclass, one loader and one env-var gate because they
share the risk that matters here: both build a real venue exec client that signs and
submits orders with real credentials. `mode` and `environment` must agree, so promoting
a demo file to real money takes editing two keys in a file that is itself only reachable
via LIVE_PAPER_REAL_MONEY_CONFIG -- never one stray line.

PaperConfig and ExecConfig are deliberately two separate dataclasses with two separate
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
from dataclasses import field
from dataclasses import fields
from decimal import Decimal
from pathlib import Path

from kernel.venues import venue_of

from live_paper.venues import VENUES
from live_paper.venues import VenueSpec
from nautilus_trader.adapters.dydx.constants import DYDX


# Which environments each explicit mode accepts. `real_money` is mainnet-only by
# definition; `exchange_demo` is every play-money environment the venues offer (Bybit
# calls its one `demo`, dYdX and Hyperliquid call theirs `testnet`).
_MODE_ENVIRONMENTS: dict[str, tuple[str, ...]] = {
    "real_money": ("mainnet",),
    "exchange_demo": ("demo", "testnet"),
}


@dataclass(frozen=True)
class BotConfig:
    """
    One paper bot within a shared `PaperConfig` node (AD-11) -- its own `Strategy`
    instance, own instrument/sizing/thresholds, own `bots:status`/`bots:history:*`
    identity via `bot_id`. `starting_balance` is a bookkeeping anchor only (feeds
    `performance_metrics.equity_returns()`'s Sharpe/Sortino calc for this bot's own
    fill history) -- it is NOT this bot's real simulated balance, since every bot in
    one venue draw from that venue's single shared pool (AD-11: Nautilus's
    ExecutionEngine allows only one exec client per venue per node, so per-bot balance
    isolation isn't available once bots share a venue). Left empty it defaults to
    10_000 of the bot's venue's paper quote currency.
    """

    bot_id: str
    instrument_id: str = "BTC-USD-PERP.DYDX"
    trade_size: Decimal = Decimal("0.001")
    trend_buy_threshold: float = 0.6
    trend_sell_threshold: float = 0.4
    ofi_confirm_threshold: float = 0.0
    starting_balance: str = ""

    def __post_init__(self) -> None:
        spec = _venue_spec(venue_of(self.instrument_id))
        if not self.starting_balance:
            object.__setattr__(self, "starting_balance", f"10_000 {spec.paper_quote_currency}")


@dataclass(frozen=True)
class VenuePaperConfig:
    environment: str
    starting_balances: tuple[str, ...]
    account_type: str


@dataclass(frozen=True)
class PaperConfig:
    log_level: str
    bots: tuple[BotConfig, ...]
    venues: dict[str, VenuePaperConfig] = field(default_factory=dict)

    def venue_config(self, venue: str) -> VenuePaperConfig:
        """A venue used by a bot but absent from `[venues]` gets the defaults."""
        return self.venues.get(venue) or _parse_venue(venue, {}, Path("<defaults>"))


@dataclass(frozen=True)
class ExecConfig:
    """
    The one-bot, explicitly-pathed config behind LIVE_PAPER_REAL_MONEY_CONFIG: `mode` is
    `real_money` (mainnet) or `exchange_demo` (the venue's demo/testnet account), and
    `environment` must match it. Credentials are never fields here -- the venue's Rust
    client reads the env-var pair its environment selects (see node.py).
    """

    mode: str
    environment: str
    subaccount: int
    log_level: str
    instrument_id: str = "BTC-USD-PERP.DYDX"
    trade_size: Decimal = Decimal("0.001")
    trend_buy_threshold: float = 0.6
    trend_sell_threshold: float = 0.4
    ofi_confirm_threshold: float = 0.0
    bot_id: str = "bot-01"


def _venue_spec(venue: str) -> VenueSpec:
    if venue not in VENUES:
        raise ValueError(f"unsupported venue {venue!r} -- supported: {sorted(VENUES)}")
    return VENUES[venue]


def _reject_unknown_keys(raw: dict, config_cls: type, path: Path, where: str = "") -> None:
    """
    A typo'd key (`subaccont`) would otherwise silently fall back to its default -- for a
    real-money config that could route trades through the wrong subaccount.
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
    spec = _venue_spec(venue)
    _reject_unknown_keys(raw, VenuePaperConfig, path, f" in [venues.{venue}]")
    environment = raw.get("environment", "mainnet")
    if not isinstance(environment, str):
        raise ValueError(f"{path}: [venues.{venue}] environment must be a string")
    environment = environment.lower()
    if environment not in spec.allowed_environments:
        raise ValueError(
            f"{path}: [venues.{venue}] environment {environment!r} not in "
            f"{list(spec.allowed_environments)}"
        )
    starting_balances = raw.get("starting_balances", [f"10_000 {spec.paper_quote_currency}"])
    if isinstance(starting_balances, str):
        raise ValueError(
            f"{path}: [venues.{venue}] starting_balances must be a TOML array (e.g. "
            f'["10_000 {spec.paper_quote_currency}"]), got a bare string '
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
        environment=environment,
        starting_balances=tuple(starting_balances),
        account_type=account_type,
    )


def _parse_bot(raw_bot: dict, path: Path) -> BotConfig:
    if "bot_id" not in raw_bot:
        raise ValueError(f"{path}: every [[bots]] entry must set bot_id")
    _reject_unknown_keys(raw_bot, BotConfig, path, " in [[bots]] entry")

    return BotConfig(
        bot_id=raw_bot["bot_id"],
        instrument_id=raw_bot.get("instrument_id", "BTC-USD-PERP.DYDX"),
        trade_size=_parse_trade_size(raw_bot, "trade_size", path),
        trend_buy_threshold=raw_bot.get("trend_buy_threshold", 0.6),
        trend_sell_threshold=raw_bot.get("trend_sell_threshold", 0.4),
        ofi_confirm_threshold=raw_bot.get("ofi_confirm_threshold", 0.0),
        starting_balance=raw_bot.get("starting_balance", ""),
    )


def load_paper_config(path: Path) -> PaperConfig:
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

    raw_bots = raw.get("bots")
    if not raw_bots:
        raise ValueError(f"{path}: must define at least one [[bots]] entry")
    bots = tuple(_parse_bot(raw_bot, path) for raw_bot in raw_bots)
    bot_ids = [bot.bot_id for bot in bots]
    if len(bot_ids) != len(set(bot_ids)):
        raise ValueError(
            f"{path}: [[bots]] entries must have distinct bot_id values, got {bot_ids}"
        )

    return PaperConfig(log_level=raw.get("log_level", "INFO"), bots=bots, venues=venues)


def load_real_money_config(path: Path) -> ExecConfig:
    """
    Load the explicitly-pathed, non-Sandbox config -- `real_money` or `exchange_demo`
    (see this module's docstring for why both live behind the one env var). Every check
    below fails closed and names the offending value(s); none of them ever reads a
    credential.
    """
    with path.open("rb") as f:
        raw = tomllib.load(f)

    mode = raw.get("mode")
    if mode not in _MODE_ENVIRONMENTS:
        raise ValueError(
            f'{path}: config must set mode = "real_money" or mode = "exchange_demo" '
            f"explicitly (found {mode!r}) -- refusing to start rather than guessing "
            "operator intent.",
        )

    environment = str(raw.get("environment", "mainnet")).lower()
    if environment not in _MODE_ENVIRONMENTS[mode]:
        raise ValueError(
            f"{path}: mode {mode!r} requires environment in "
            f"{list(_MODE_ENVIRONMENTS[mode])}, got {environment!r} -- the two must agree, "
            "so no single edited key can promote a demo config to mainnet.",
        )

    venue = venue_of(raw.get("instrument_id", "BTC-USD-PERP.DYDX"))
    spec = _venue_spec(venue)
    if environment not in spec.allowed_environments:
        raise ValueError(
            f"{path}: environment {environment!r} not in {list(spec.allowed_environments)} "
            f"for venue {venue}",
        )
    if "subaccount" in raw and venue != DYDX:
        raise ValueError(f"{path}: subaccount is dYdX-only, not valid for venue {venue}")

    _reject_unknown_keys(raw, ExecConfig, path)
    config = ExecConfig(
        mode=mode,
        environment=environment,
        subaccount=raw.get("subaccount", 0),
        log_level=raw.get("log_level", "INFO"),
        instrument_id=raw.get("instrument_id", "BTC-USD-PERP.DYDX"),
        trade_size=_parse_trade_size(raw, "trade_size", path),
        trend_buy_threshold=raw.get("trend_buy_threshold", 0.6),
        trend_sell_threshold=raw.get("trend_sell_threshold", 0.4),
        ofi_confirm_threshold=raw.get("ofi_confirm_threshold", 0.0),
        bot_id=raw.get("bot_id", "bot-01"),
    )
    # Venue-specific derivations (Bybit's product type from the id suffix) fail closed
    # here, with the file named, rather than inside build_node.
    try:
        spec.exec_kwargs(config)
    except ValueError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    return config


def resolve_config(
    paper_path: Path,
    real_money_path: str | None,
) -> tuple[PaperConfig | ExecConfig, bool]:
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
