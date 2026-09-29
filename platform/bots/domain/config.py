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
The paper/non-paper split as types (DDD spine AD-D15, architecture AD-10/AD-11).

`PaperFleet` and `ExecBot` are distinct aggregates built by distinct loaders
(`bots.infrastructure.config`) from distinct files, never one schema with an optional mode field:

- `PaperFleet` -- many paper bots (`BotConfig`) on one `TradingNode`, one Sandbox balance pool per
  venue in use (`VenuePaperConfig`). `PaperConfig` has no mode field and its loader rejects a
  `mode` key outright, so a stray `mode = "real_money"` line in the committed default file fails
  closed instead of promoting anything.
- `ExecBot` -- exactly one bot on one venue account (`ExecConfig`). Its `mode` selects only between
  the two *non-Sandbox* modes -- `real_money` (mainnet) and `exchange_demo` (the venue's play-money
  account: Bybit `demo`, dYdX/Hyperliquid `testnet`) -- and must agree with `environment`, so
  promoting a demo file to real money takes editing two keys in a file that is itself only
  reachable via the explicit `LIVE_PAPER_REAL_MONEY_CONFIG` path. It can never select paper.

Known limit: within `ExecConfig` the demo/real distinction is a validated value, not a type.
Upgrade path: split `ExecBot` into `ExchangeDemoBot`/`RealMoneyBot`, with `mode` used only as the
loader's discriminator (revisit when the real-money path is first exercised).

The per-venue facts the invariants need live in `VENUE_RULES` (pure); the Nautilus client
factories for the same venues are `bots.infrastructure.nautilus_host.VENUES` (a test pins that both
tables name the same venues).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from decimal import Decimal
from types import MappingProxyType

from kernel.venues import venue_of

from nautilus_trader.model.objects import Money


@dataclass(frozen=True)
class VenueRules:
    """What a venue accepts: its environment names and the currency its paper pool is held in."""

    allowed_environments: tuple[str, ...]
    paper_quote_currency: str
    has_subaccounts: bool = False


# Keyed by the Nautilus venue token (`kernel.venues.venue_of`).
VENUE_RULES: MappingProxyType[str, VenueRules] = MappingProxyType(
    {
        "DYDX": VenueRules(("mainnet", "testnet"), "USDC", has_subaccounts=True),
        "BYBIT": VenueRules(("mainnet", "demo", "testnet"), "USDT"),
        "HYPERLIQUID": VenueRules(("mainnet", "testnet"), "USDC"),
    }
)

# Which environments each explicit (non-Sandbox) mode accepts. `real_money` is mainnet-only by
# definition; `exchange_demo` is every play-money environment the venues offer.
EXEC_MODE_ENVIRONMENTS: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
    {
        "real_money": ("mainnet",),
        "exchange_demo": ("demo", "testnet"),
    }
)

# The `bots:status` `mode` label per exec mode; kept short (bot_tui renders a 5-char column).
_EXEC_MODE_LABELS: MappingProxyType[str, str] = MappingProxyType(
    {"real_money": "live", "exchange_demo": "demo"}
)
PAPER_MODE_LABEL = "paper"


def venue_rules(venue: str) -> VenueRules:
    if venue not in VENUE_RULES:
        raise ValueError(f"unsupported venue {venue!r} -- supported: {sorted(VENUE_RULES)}")
    return VENUE_RULES[venue]


# An exit 10_000 basis points (100%) or more below the entry -- a long's stop-loss, a short's
# take-profit -- sits at or below zero, so neither key may reach it.
MAX_EXIT_BPS = 9_999


def _check_bps(name: str, value: object, maximum: int) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer (basis points), got {value!r}")
    if value > maximum:
        raise ValueError(f"{name} must be at most {maximum}, got {value!r}")


@dataclass(frozen=True)
class BotConfig:
    """
    One paper bot within a `PaperFleet` (AD-11): its own `Strategy` instance, instrument, sizing
    and thresholds, and its own `bots:status`/`bots:history:*` identity via `bot_id`.
    `starting_balance` is a bookkeeping anchor only (the equity curve of this bot's own fills,
    `kernel.performance_metrics.equity_returns`) -- NOT this bot's real simulated balance, since
    every bot on one venue draws from that venue's single shared pool (Nautilus allows one exec
    client per venue per node). Left empty it defaults to 10_000 of the venue's paper currency.

    `take_profit_bps`/`stop_loss_bps` (Story 29.6) give the bot's entries bracket exits; each is
    None (no such leg) or a positive int (a `bool` is refused although Python counts it as one),
    and at most `MAX_EXIT_BPS`: a long's stop or a short's take-profit 10_000 bps or more away
    would sit at or below zero.
    """

    bot_id: str
    instrument_id: str = "BTC-USD-PERP.DYDX"
    trade_size: Decimal = Decimal("0.001")
    trend_buy_threshold: float = 0.6
    trend_sell_threshold: float = 0.4
    ofi_confirm_threshold: float = 0.0
    starting_balance: str = ""
    take_profit_bps: int | None = None
    stop_loss_bps: int | None = None

    def __post_init__(self) -> None:
        _check_bps("take_profit_bps", self.take_profit_bps, MAX_EXIT_BPS)
        _check_bps("stop_loss_bps", self.stop_loss_bps, MAX_EXIT_BPS)
        rules = venue_rules(venue_of(self.instrument_id))
        if not self.starting_balance:
            object.__setattr__(self, "starting_balance", f"10_000 {rules.paper_quote_currency}")


@dataclass(frozen=True)
class VenuePaperConfig:
    environment: str
    starting_balances: tuple[str, ...]
    account_type: str


def default_venue_paper_config(venue: str) -> VenuePaperConfig:
    """Return the pool a venue gets when a bot uses it but `[venues.<VENUE>]` omits it."""
    rules = venue_rules(venue)
    return VenuePaperConfig(
        environment="mainnet",
        starting_balances=(f"10_000 {rules.paper_quote_currency}",),
        account_type="MARGIN",
    )


@dataclass(frozen=True)
class PaperConfig:
    """The paper file's value (`config.toml`): deliberately no `mode` field."""

    log_level: str
    bots: tuple[BotConfig, ...]
    venues: Mapping[str, VenuePaperConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Frozen all the way down: a validated pool table must not be editable afterwards.
        object.__setattr__(self, "venues", MappingProxyType(dict(self.venues)))


@dataclass(frozen=True)
class ExecConfig:
    """
    The one-bot, explicitly-pathed file behind `LIVE_PAPER_REAL_MONEY_CONFIG`. Credentials are
    never fields here -- the venue's Rust client reads the env-var pair its environment selects.

    Known limit: no `take_profit_bps`/`stop_loss_bps` here (Story 29.6 gave them to the paper
    `BotConfig` only): whether each venue's exec client accepts Nautilus's contingent order lists
    (OTO entry, OUO reduce-only legs) is unverified, and a bracket a venue half-rejects would
    leave a real position unprotected. Upgrade path: verify contingent-order support per venue
    on its `exchange_demo` account, then add the two keys here and forward them in `build_node`.
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


@dataclass(frozen=True)
class PaperFleet:
    """
    Every paper bot of one process: many `Bot`s, one shared Sandbox pool per venue in use.

    Invariants: at least one bot; `bot_id`s are distinct (each is pinned as its strategy's
    `order_id_tag`, so a duplicate would merge two bots' Cache, fills and history); every
    configured venue pool is a supported venue in one of its allowed environments; the fleet is
    always `paper` -- it has no mode to change. Violated only at construction (it is immutable).
    """

    config: PaperConfig

    def __post_init__(self) -> None:
        if not self.config.bots:
            raise ValueError("must define at least one [[bots]] entry")
        bot_ids = [bot.bot_id for bot in self.config.bots]
        if len(bot_ids) != len(set(bot_ids)):
            raise ValueError(f"[[bots]] entries must have distinct bot_id values, got {bot_ids}")
        for bot in self.config.bots:
            # Parsed here, not first when the node is already built and its Cache connected.
            try:
                Money.from_str(bot.starting_balance)
            except Exception as exc:
                raise ValueError(
                    f"[[bots]] {bot.bot_id}: starting_balance {bot.starting_balance!r} is not an "
                    f'amount with a currency (e.g. "10_000 USDC"): {exc}'
                ) from exc
        for venue, pool in self.config.venues.items():
            allowed = venue_rules(venue).allowed_environments
            if pool.environment not in allowed:
                raise ValueError(
                    f"[venues.{venue}] environment {pool.environment!r} not in {list(allowed)}"
                )

    @property
    def mode_label(self) -> str:
        return PAPER_MODE_LABEL

    @property
    def log_level(self) -> str:
        return self.config.log_level

    @property
    def bots(self) -> tuple[BotConfig, ...]:
        return self.config.bots

    def venues_in_use(self) -> list[str]:
        return sorted({venue_of(bot.instrument_id) for bot in self.config.bots})

    def venue_config(self, venue: str) -> VenuePaperConfig:
        """Return the venue's pool; a venue a bot uses but `[venues]` omits gets the defaults."""
        return self.config.venues.get(venue) or default_venue_paper_config(venue)

    def starting_balance_anchor(self, bot_id: str) -> float:
        """Return the bot's equity-curve anchor (see `BotConfig`), parsed like the Sandbox pool."""
        bot = next(bot for bot in self.config.bots if bot.bot_id == bot_id)
        return Money.from_str(bot.starting_balance).as_double()


@dataclass(frozen=True)
class ExecBot:
    """
    The one non-Sandbox bot of a process: it really signs and submits orders.

    Invariants: `mode` is `real_money` or `exchange_demo` -- never paper, never absent; the
    `environment` agrees with the mode (mainnet for real money, a play-money network for demo) and
    is one the venue offers; `subaccount` is set only on a venue that has subaccounts (dYdX). One
    bot per file, never the paper `[[bots]]` shape. Violated only at construction (immutable).
    """

    config: ExecConfig

    def __post_init__(self) -> None:
        mode, environment = self.config.mode, self.config.environment
        if mode not in EXEC_MODE_ENVIRONMENTS:
            raise ValueError(
                'config must set mode = "real_money" or mode = "exchange_demo" '
                f"explicitly (found {mode!r}) -- refusing to start rather than guessing "
                "operator intent."
            )
        if environment not in EXEC_MODE_ENVIRONMENTS[mode]:
            raise ValueError(
                f"mode {mode!r} requires environment in {list(EXEC_MODE_ENVIRONMENTS[mode])}, "
                f"got {environment!r} -- the two must agree, so no single edited key can "
                "promote a demo config to mainnet."
            )
        venue = venue_of(self.config.instrument_id)
        rules = venue_rules(venue)
        if environment not in rules.allowed_environments:
            raise ValueError(
                f"environment {environment!r} not in {list(rules.allowed_environments)} "
                f"for venue {venue}"
            )
        if self.config.subaccount and not rules.has_subaccounts:
            raise ValueError(f"subaccount is dYdX-only, not valid for venue {venue}")

    @property
    def mode_label(self) -> str:
        return _EXEC_MODE_LABELS[self.config.mode]

    @property
    def log_level(self) -> str:
        return self.config.log_level

    @property
    def bots(self) -> tuple[ExecConfig, ...]:
        return (self.config,)

    def starting_balance_anchor(self, bot_id: str) -> float | None:
        """
        None: a real account's balance lives at the venue, not in a file, so the return-based
        stats are skipped rather than computed against a fabricated number.
        """
        return None
