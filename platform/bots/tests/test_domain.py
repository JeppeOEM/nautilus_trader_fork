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
Invariant tests of the bots aggregates, one per command (DDD spine AD-D15, DESIGN-01):
`PaperFleet`/`ExecBot` construction, `Bot.start`/`Bot.observe`, `FillLedger.attribute`.
"""

from decimal import Decimal

import pytest

from bots.domain.bot import DATA_STALE_NS
from bots.domain.bot import MAX_INCIDENTS
from bots.domain.bot import Bot
from bots.domain.config import VENUE_RULES
from bots.domain.config import BotConfig
from bots.domain.config import ExecBot
from bots.domain.config import ExecConfig
from bots.domain.config import PaperConfig
from bots.domain.config import PaperFleet
from bots.domain.config import VenuePaperConfig
from bots.infrastructure.nautilus_host import VENUES


def _exec(**overrides: object) -> ExecConfig:
    kwargs: dict = {
        "mode": "real_money",
        "environment": "mainnet",
        "subaccount": 0,
        "log_level": "INFO",
    }
    return ExecConfig(**{**kwargs, **overrides})


# --- PaperFleet ------------------------------------------------------------------------------


def test_a_paper_fleet_is_always_paper_and_has_no_mode_to_change() -> None:
    fleet = PaperFleet(PaperConfig(log_level="INFO", bots=(BotConfig(bot_id="a"),)))
    assert fleet.mode_label == "paper"
    assert not hasattr(fleet.config, "mode")


def test_a_paper_fleet_needs_a_bot_and_distinct_bot_ids() -> None:
    with pytest.raises(ValueError, match="at least one"):
        PaperFleet(PaperConfig(log_level="INFO", bots=()))
    with pytest.raises(ValueError, match="distinct bot_id"):
        PaperFleet(
            PaperConfig(log_level="INFO", bots=(BotConfig(bot_id="a"), BotConfig(bot_id="a")))
        )


def test_a_paper_pool_must_be_a_supported_venue_in_an_allowed_environment() -> None:
    pool = VenuePaperConfig(
        environment="demo", starting_balances=("1 USDC",), account_type="MARGIN"
    )
    bots = (BotConfig(bot_id="a"),)
    with pytest.raises(ValueError, match=r"\[venues.DYDX\] environment 'demo'"):
        PaperFleet(PaperConfig(log_level="INFO", bots=bots, venues={"DYDX": pool}))
    with pytest.raises(ValueError, match="unsupported venue 'KRAKEN'"):
        PaperFleet(PaperConfig(log_level="INFO", bots=bots, venues={"KRAKEN": pool}))


def test_a_paper_fleet_uses_one_pool_per_venue_in_use_and_anchors_each_bot() -> None:
    fleet = PaperFleet(
        PaperConfig(
            log_level="INFO",
            bots=(
                BotConfig(bot_id="a", instrument_id="BTCUSDT-LINEAR.BYBIT"),
                BotConfig(
                    bot_id="b", instrument_id="BTCUSDT-SPOT.BYBIT", starting_balance="500 USDT"
                ),
                BotConfig(bot_id="c"),
            ),
        )
    )
    assert fleet.venues_in_use() == ["BYBIT", "DYDX"]
    assert fleet.venue_config("BYBIT").starting_balances == ("10_000 USDT",)
    assert [fleet.starting_balance_anchor(bot.bot_id) for bot in fleet.bots] == [
        10_000.0,
        500.0,
        10_000.0,
    ]


# --- ExecBot ---------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["paper", None, "", "REAL_MONEY"])
def test_an_exec_bot_can_never_be_paper_or_modeless(mode: object) -> None:
    with pytest.raises(ValueError, match='mode = "real_money" or mode = "exchange_demo"'):
        ExecBot(_exec(mode=mode))


@pytest.mark.parametrize(
    ("mode", "environment"),
    [("real_money", "testnet"), ("real_money", "demo"), ("exchange_demo", "mainnet")],
)
def test_an_exec_bots_mode_and_environment_must_agree(mode: str, environment: str) -> None:
    with pytest.raises(ValueError, match="must agree"):
        ExecBot(_exec(mode=mode, environment=environment))


def test_an_exec_bot_environment_must_exist_on_its_venue_and_subaccount_is_dydx_only() -> None:
    with pytest.raises(ValueError, match="environment 'demo' not in"):
        ExecBot(_exec(mode="exchange_demo", environment="demo"))
    with pytest.raises(ValueError, match="subaccount is dYdX-only"):
        ExecBot(_exec(subaccount=1, instrument_id="BTCUSDT-LINEAR.BYBIT"))


def test_an_exec_bot_is_one_bot_labelled_by_its_mode_with_no_anchor() -> None:
    live = ExecBot(_exec(subaccount=2, bot_id="x"))
    demo = ExecBot(_exec(mode="exchange_demo", environment="testnet"))
    assert (live.mode_label, demo.mode_label) == ("live", "demo")
    assert [bot.bot_id for bot in live.bots] == ["x"]
    assert live.starting_balance_anchor(live.config.bot_id) is None


def test_the_venue_rules_and_the_client_table_name_the_same_venues() -> None:
    assert set(VENUE_RULES) == set(VENUES)


def test_bot_config_defaults_its_anchor_to_its_venues_paper_currency() -> None:
    assert (
        BotConfig(bot_id="a", instrument_id="BTCUSDT-SPOT.BYBIT").starting_balance == "10_000 USDT"
    )
    assert BotConfig(bot_id="a", trade_size=Decimal(1)).starting_balance == "10_000 USDC"


# --- Bot -------------------------------------------------------------------------------------


def test_start_closes_the_orphan_marks_the_start_and_trims() -> None:
    prior = [
        {"type": "data_stale", "started_at": float(i), "ended_at": float(i)} for i in range(60)
    ]
    prior.append({"type": "data_stale", "started_at": 90.0, "ended_at": None})
    bot = Bot("a", "paper", started_at=100.0)

    incidents = bot.start(prior, now=100.0)

    assert len(incidents) == MAX_INCIDENTS
    assert incidents[-2] == {
        "type": "data_stale",
        "started_at": 90.0,
        "ended_at": 100.0,
        "note": "closed by restart",
    }
    assert incidents[-1] == {"type": "process_start", "started_at": 100.0, "ended_at": 100.0}


def test_observe_keeps_at_most_one_open_span_and_it_is_the_last() -> None:
    bot = Bot("a", "paper", started_at=0.0)
    stale_ns = DATA_STALE_NS + 1
    assert bot.observe(now=31.0, now_ns=stale_ns, last_data_ns=0, running=True) is True
    assert bot.observe(now=32.0, now_ns=stale_ns + 1, last_data_ns=0, running=True) is False
    assert [i["ended_at"] for i in bot.incidents] == [None]
    assert bot.observe(now=33.0, now_ns=stale_ns + 2, last_data_ns=stale_ns + 1, running=True)
    assert [i["ended_at"] for i in bot.incidents] == [33.0]


def test_observe_never_opens_a_span_for_a_stopped_bot() -> None:
    bot = Bot("a", "paper", started_at=0.0)
    assert bot.observe(now=99.0, now_ns=99 * DATA_STALE_NS, last_data_ns=0, running=False) is False
    assert bot.incidents == []


def test_observe_keeps_the_log_bounded() -> None:
    bot = Bot("a", "paper", started_at=0.0)
    ns = 0
    for step in range(2 * MAX_INCIDENTS + 5):
        ns += DATA_STALE_NS + 1
        # alternate stale (no data) and recovered (data just now)
        bot.observe(float(step), ns, last_data_ns=0 if step % 2 == 0 else ns, running=True)
    assert len(bot.incidents) == MAX_INCIDENTS


def test_incidents_is_a_copy_the_caller_cannot_mutate() -> None:
    bot = Bot("a", "paper", started_at=0.0)
    bot.start([], now=1.0)
    bot.incidents.clear()
    assert len(bot.incidents) == 1


def test_start_is_recorded_once_per_process_life() -> None:
    bot = Bot("a", "paper", started_at=0.0)
    bot.start([], now=1.0)
    with pytest.raises(RuntimeError, match="already recorded"):
        bot.start([], now=2.0)
    assert bot.incidents == [{"type": "process_start", "started_at": 1.0, "ended_at": 1.0}]


@pytest.mark.parametrize("prior", [{"not": "a list"}, [1, 2], [{"type": "data_stale"}]])
def test_a_malformed_prior_log_leaves_the_bot_unstarted(prior: object) -> None:
    # The flag is set only once the log is built, so the caller's fallback can still mark this life.
    bot = Bot("a", "paper", started_at=0.0)
    with pytest.raises((TypeError, KeyError, AttributeError)):
        bot.start(prior, now=1.0)  # type: ignore[arg-type]
    assert not bot.started
    assert bot.start([], now=1.0) == [{"type": "process_start", "started_at": 1.0, "ended_at": 1.0}]


def test_fleet_rejects_a_starting_balance_without_a_currency() -> None:
    bots = (BotConfig(bot_id="a", starting_balance="10000"),)
    with pytest.raises(ValueError, match=r"\[\[bots\]\] a: starting_balance '10000'"):
        PaperFleet(PaperConfig(log_level="INFO", bots=bots))


def test_the_validated_venue_pools_cannot_be_edited_afterwards() -> None:
    pool = VenuePaperConfig(
        environment="testnet", starting_balances=("1 USDC",), account_type="CASH"
    )
    fleet = PaperFleet(
        PaperConfig(log_level="INFO", bots=(BotConfig(bot_id="a"),), venues={"DYDX": pool})
    )
    with pytest.raises(TypeError):
        fleet.config.venues["DYDX"] = pool  # type: ignore[index]
    assert fleet.venue_config("DYDX") == pool
