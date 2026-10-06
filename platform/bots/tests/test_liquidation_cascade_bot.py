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
A `liquidation_cascade` paper bot (Story 33.14): the host's `STRATEGIES` row, the `LIQUIDATIONS`
bridge client added only for a fleet that has such a bot (and refused without the feed status its
readers share), the refusal of an id without the liquidation feed, the signal log path, the cache
reader's cap while the feed is down, the commented example of `bots/config.toml`, and its catalog
replay (`bots.signal_replay`). The `build_node` tests that build a real `TradingNode` need Redis
at `REDIS_URL` (as `test_node.py`); the rest are Redis-free. The strategy is recognised by its
type name: bots never imports research.
"""

import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from kernel.clocks import NS_PER_S
from kernel.liquidation import LIQUIDATION_CLIENT_ID
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from bots.domain.config import BotConfig
from bots.domain.config import PaperConfig
from bots.domain.config import PaperFleet
from bots.infrastructure.config import load_paper_config
from bots.infrastructure.liquidation_data_client import LiquidationDataClientConfig
from bots.infrastructure.liquidation_data_client import LiquidationFeedStatus
from bots.infrastructure.nautilus_host import LIQUIDATION_STRATEGIES
from bots.infrastructure.nautilus_host import SIGNAL_LOG_DIR_ENV
from bots.infrastructure.nautilus_host import STRATEGIES
from bots.infrastructure.nautilus_host import _strategy_for
from bots.infrastructure.nautilus_host import build_node
from bots.infrastructure.nautilus_host import cache_reader_for
from bots.infrastructure.nautilus_host import check_strategy
from bots.signal_replay import REFUSED_SITE
from bots.signal_replay import ReplayRefused
from bots.signal_replay import _same_value
from bots.signal_replay import check_same_config
from bots.signal_replay import main as replay_main
from bots.tests.test_node import _dispose
from bots.tests.test_signal_replay import _BOT
from bots.tests.test_signal_replay import _IID
from bots.tests.test_signal_replay import _LIVE_START
from bots.tests.test_signal_replay import _T0
from bots.tests.test_signal_replay import _catalog
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_REDIS_URL = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")
_CONFIG_TOML = Path(__file__).parent.parent / "config.toml"
_PARAMS = {"stop_pct": 0.004, "min_episode_notional": "50000", "sides": ["short", "long"]}


def _cascade(
    bot_id: str = "cascade-01",
    instrument_id: str = "BTCUSDT-LINEAR.BYBIT",
    params: dict[str, Any] | None = None,
) -> BotConfig:
    return BotConfig(
        bot_id=bot_id,
        instrument_id=instrument_id,
        trade_size=Decimal("0.001"),
        strategy="liquidation_cascade",
        params=_PARAMS if params is None else params,
    )


def _fleet(*bots: BotConfig) -> PaperFleet:
    return PaperFleet(PaperConfig(log_level="ERROR", bots=bots))


def test_the_strategies_row_names_the_research_strategy_by_string_path() -> None:
    # Joined here, not written whole: a literal string path in a bots module is the image
    # closure's business (`tests/test_images.py`'s `_STRING_PATH_IMPORTS`), and a test loads none.
    module = "research.strategies.liquidation_cascade_strategy"
    classes = ("LiquidationCascadeStrategy", "LiquidationCascadeStrategyConfig")
    assert STRATEGIES["liquidation_cascade"] == tuple(f"{module}:{name}" for name in classes)
    assert {"liquidation_cascade"} == LIQUIDATION_STRATEGIES


def test_the_host_builds_the_cascade_strategy_from_the_bots_keys_and_params() -> None:
    strategy = _strategy_for(_cascade())
    assert type(strategy).__name__ == "LiquidationCascadeStrategy"
    config = strategy.config
    assert config.order_id_tag == "cascade-01"
    assert str(config.instrument_id) == "BTCUSDT-LINEAR.BYBIT"
    assert config.trade_size == Decimal("0.001")
    assert (config.stop_pct, config.min_episode_notional, config.sides) == (
        0.004,
        Decimal(50_000),
        ("short", "long"),
    )
    assert strategy.last_data_ns == 0


def test_the_cascade_bot_writes_its_signal_log_under_the_log_dir_and_others_do_not(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(SIGNAL_LOG_DIR_ENV, str(tmp_path))
    assert _strategy_for(_cascade()).config.signal_log_path == str(tmp_path / "cascade-01.jsonl")
    monkeypatch.delenv(SIGNAL_LOG_DIR_ENV)
    assert _strategy_for(_cascade()).config.signal_log_path is None


@pytest.mark.parametrize(
    "instrument_id",
    ["BTCUSDT-SPOT.BYBIT", "BTC-USD-PERP.HYPERLIQUID", "BTC-USD-PERP.DYDX"],
)
def test_a_cascade_bot_on_an_id_without_the_feed_is_refused_naming_the_bot(
    instrument_id: str,
) -> None:
    with pytest.raises(ValueError, match=r"\[\[bots\]\] cascade-01: .*needs the liquidation feed"):
        check_strategy(_cascade(instrument_id=instrument_id))


def test_a_cascade_bot_on_a_spot_id_is_refused_at_config_load(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        'log_level = "INFO"\n[[bots]]\nbot_id = "cascade-spot"\n'
        'instrument_id = "BTCUSDT-SPOT.BYBIT"\nstrategy = "liquidation_cascade"\n'
        "[bots.params]\nstop_pct = 0.004\n"
    )
    with pytest.raises(ValueError, match=r"cascade-spot.*needs the liquidation feed"):
        load_paper_config(path)


def test_the_signal_log_path_is_the_hosts_never_a_param() -> None:
    with pytest.raises(ValueError, match=r"may not set \['signal_log_path'\]"):
        check_strategy(_cascade(params={**_PARAMS, "signal_log_path": "x.jsonl"}))


def test_the_config_example_parses_and_builds_once_uncommented(tmp_path: Path) -> None:
    lines = _CONFIG_TOML.read_text().splitlines()
    heading = next(i for i, line in enumerate(lines) if "A liquidation cascade paper bot" in line)
    first = lines.index("# [[bots]]", heading)
    block = [line.removeprefix("# ") for line in lines[first:]]
    path = tmp_path / "config.toml"
    path.write_text('log_level = "INFO"\n' + "\n".join(block) + "\n")
    (bot,) = load_paper_config(path).bots
    assert (bot.bot_id, bot.strategy, bot.instrument_id) == (
        "cascade-btc-01",
        "liquidation_cascade",
        "BTCUSDT-LINEAR.BYBIT",
    )
    assert type(_strategy_for(bot)).__name__ == "LiquidationCascadeStrategy"


# --- the cache reader's cap ----------------------------------------------------------------------


def test_the_cascade_bots_reader_is_capped_at_the_disconnect_while_the_feed_is_down() -> None:
    bot = _cascade()
    strategy = _strategy_for(bot)
    status = LiquidationFeedStatus(connected=True)
    reader = cache_reader_for(bot, strategy, status)
    strategy.last_data_ns = 5_000
    assert reader.last_data_ns == 5_000
    status.connected, status.disconnected_since_ns = False, 3_000
    assert reader.last_data_ns == 3_000
    strategy.last_data_ns = 2_000  # data older than the drop is reported as it is
    assert reader.last_data_ns == 2_000
    status.connected, status.disconnected_since_ns = True, None
    strategy.last_data_ns = 9_000
    assert reader.last_data_ns == 9_000


def test_a_dummy_bots_reader_ignores_the_liquidation_feed() -> None:
    bot = BotConfig(bot_id="bot-01")
    strategy = _strategy_for(bot)
    reader = cache_reader_for(bot, strategy, LiquidationFeedStatus(disconnected_since_ns=1))
    strategy.last_data_ns = 5_000
    assert reader.last_data_ns == 5_000


# --- the node (needs Redis) ----------------------------------------------------------------------


def test_a_dummy_only_fleet_gets_no_liquidations_client() -> None:
    node, _hosted = build_node(_fleet(BotConfig(bot_id="bot-01")), _REDIS_URL)
    try:
        assert LIQUIDATION_CLIENT_ID not in node._config.data_clients
        assert ClientId(LIQUIDATION_CLIENT_ID) not in node.kernel.data_engine.registered_clients
    finally:
        _dispose(node)


def test_a_cascade_fleet_without_the_feed_status_its_readers_share_is_refused() -> None:
    # A private status nobody reads would leave the cascade bot's heartbeat blind to a drop.
    fleet = _fleet(BotConfig(bot_id="bot-01"), _cascade())
    with pytest.raises(ValueError, match="needs the LiquidationFeedStatus"):
        build_node(fleet, _REDIS_URL)


def test_a_cascade_fleet_gets_the_liquidations_bridge_on_the_bots_redis() -> None:
    fleet = _fleet(BotConfig(bot_id="bot-01"), _cascade())
    node, hosted = build_node(fleet, _REDIS_URL, LiquidationFeedStatus())
    try:
        bridge = node._config.data_clients[LIQUIDATION_CLIENT_ID]
        assert isinstance(bridge, LiquidationDataClientConfig)
        assert bridge.redis_url == _REDIS_URL
        assert ClientId(LIQUIDATION_CLIENT_ID) in node.kernel.data_engine.registered_clients
        assert set(node._config.data_clients) == {"DYDX", "BYBIT", LIQUIDATION_CLIENT_ID}
        assert [type(strategy).__name__ for _bot, strategy in hosted] == [
            "DummyStrategy",
            "LiquidationCascadeStrategy",
        ]
    finally:
        _dispose(node)


# --- the catalog replay (`bots.signal_replay --strategy liquidation_cascade`) -------------------

_REPLAY_PARAMS = {
    "window_s": 5,
    "baseline_s": 30,
    "intensity_threshold": 3.0,
    "decay_ratio": 0.5,
    "stop_pct": 0.05,
    "cooldown_s": 0,
}
# A quiet background (one LONG every 10 s from second 0) and a burst (one LONG every second over
# seconds 60..79): a long-liquidation cascade the detector, warm after `baseline_s`, sees.
_BURST = range(60, 80)


def _liquidation(second: int) -> Liquidation:
    ts = _T0 + second * NS_PER_S + 400_000_000
    return Liquidation(
        instrument_id=InstrumentId.from_str(_IID),
        side=LiquidatedSide.LONG,
        size_units=500,  # 0.5 BTC at size precision 3
        price_units=830_600,  # 83060.0 at price precision 1
        price_precision=1,
        size_precision=3,
        venue_event_id=f"liq-{second}",
        ts_event=ts - 100_000_000,
        ts_init=ts,
    )


def _cascade_paper_file(path: Path) -> Path:
    params = "".join(f"{key} = {json.dumps(value)}\n" for key, value in _REPLAY_PARAMS.items())
    path.write_text(
        '[venues.BYBIT]\nenvironment = "mainnet"\nstarting_balances = ["100_000 USDT"]\n'
        f'account_type = "MARGIN"\n\n[[bots]]\nbot_id = "{_BOT}"\ninstrument_id = "{_IID}"\n'
        'trade_size = "0.001"\nstrategy = "liquidation_cascade"\n'
        f"[bots.params]\n{params}"
        # a dummy bot beside it, left out by `--strategy liquidation_cascade`
        '\n[[bots]]\nbot_id = "dummy-01"\ninstrument_id = "BTCUSDT-LINEAR.BYBIT"\n'
    )
    return path


def _cascade_live_log(directory: Path, end_ns: int) -> None:
    start = {
        "kind": "start",
        "strategy": "liquidation_cascade",
        "bot_id": _BOT,
        "instrument_id": _IID,
        "ts_ns": _LIVE_START,
        "trade_size": "0.001",
        **_REPLAY_PARAMS,
    }
    directory.mkdir()
    records = [start, {"kind": "tick", "ts_ns": end_ns}]
    (directory / f"{_BOT}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))


def test_a_cascade_bot_replays_its_ticks_and_the_archived_liquidations(tmp_path: Path) -> None:
    _catalog(tmp_path / "catalog")
    background = [s for s in range(0, 150, 10) if s not in _BURST]
    rows = [_liquidation(s) for s in sorted({*background, *_BURST})]
    ParquetDataCatalog(str(tmp_path / "catalog")).write_data(rows)
    end_ns = _LIVE_START + 120 * NS_PER_S
    _cascade_live_log(tmp_path / "live", end_ns)
    argv = [
        *("--config", str(_cascade_paper_file(tmp_path / "config.toml"))),
        *("--catalog", str(tmp_path / "catalog")),
        *("--live-log", str(tmp_path / "live")),
        *("--out", str(tmp_path / "replay")),
        *("--strategy", "liquidation_cascade"),
    ]
    assert replay_main(argv) == 0
    assert not (tmp_path / "replay" / "dummy-01.jsonl").exists()
    lines = (tmp_path / "replay" / f"{_BOT}.jsonl").read_text().splitlines()
    start, *cycles = [json.loads(line) for line in lines]
    assert (start["strategy"], start["ts_ns"], start["window_s"]) == (
        "liquidation_cascade",
        _LIVE_START,
        5,
    )
    ticks = [c["ts_ns"] for c in cycles if c["kind"] == "tick"]
    first = (_LIVE_START // NS_PER_S + 1) * NS_PER_S  # the first whole second after the start
    assert ticks == list(range(first, end_ns + 1, NS_PER_S))
    in_window = [r for r in rows if _LIVE_START <= r.ts_init <= end_ns]
    liquidations = [c for c in cycles if c["kind"] == "liquidation"]
    assert [c["venue_event_id"] for c in liquidations] == [r.venue_event_id for r in in_window]
    assert [c["ts_ns"] for c in liquidations] == [r.ts_init for r in in_window]
    entries = [c for c in cycles if c["decision"].startswith("enter")]
    assert {c["decision"] for c in entries} == {"enter_short"}  # long liquidations only
    burst = (_T0 + _BURST.start * NS_PER_S, _T0 + _BURST.stop * NS_PER_S)
    assert any(burst[0] <= c["ts_ns"] < burst[1] for c in entries)
    assert {c["reason"] for c in cycles if c["decision"] == "exit"} == {"spent"}


def test_a_cascade_live_run_of_other_params_is_refused(tmp_path: Path) -> None:
    _catalog(tmp_path / "catalog")
    _cascade_live_log(tmp_path / "live", _LIVE_START + 10 * NS_PER_S)
    paper = _cascade_paper_file(tmp_path / "config.toml")
    paper.write_text(paper.read_text().replace("window_s = 5", "window_s = 6"))
    bot = next(b for b in load_paper_config(paper).bots if b.strategy == "liquidation_cascade")
    record = json.loads((tmp_path / "live" / f"{_BOT}.jsonl").read_text().splitlines()[0])
    with pytest.raises(ReplayRefused, match=r"window_s.*\(5, 6\)") as refused:
        check_same_config(bot, record)
    assert "strategy" not in str(refused.value)  # equal on both sides: not listed
    record["window_s"] = 6.0
    check_same_config(bot, record)  # equal again: numbers compared as decimals, `6.0` == `6`
    record["strategy"] = "dummy"
    with pytest.raises(ReplayRefused, match=r"'strategy': \('dummy', 'liquidation_cascade'\)"):
        check_same_config(bot, record)


@pytest.mark.parametrize(
    ("live", "configured", "same"),
    [
        (3, 3, True),
        (3, 3.0, True),
        (3.0, 3, True),
        ("0.004", 0.004, True),  # a Decimal config string against the TOML float
        ("50000", 50_000, True),
        ("0.0040", 0.004, True),
        ("0.005", 0.004, False),
        (["short", "long"], ("short", "long"), True),
        (["short", "long"], ["long", "short"], False),
        (["short"], ("short", "long"), False),
        ([1, "2.0"], (1.0, 2), True),
        ("follow", "fade", False),
        ("follow", "follow", True),
        (True, 1, False),
        (None, 0, False),
    ],
)
def test_a_start_record_value_is_judged_as_the_configured_one(
    live: object, configured: object, same: bool
) -> None:
    assert _same_value(live, configured) is same


def test_a_start_override_is_refused_up_front_for_a_cascade_bot(tmp_path: Path) -> None:
    # Cascade parity needs the replay to start at the live start: refused before any replay.
    _catalog(tmp_path / "catalog")
    _cascade_live_log(tmp_path / "live", _LIVE_START + 10 * NS_PER_S)
    argv = [
        *("--config", str(_cascade_paper_file(tmp_path / "config.toml"))),
        *("--catalog", str(tmp_path / "catalog")),
        *("--live-log", str(tmp_path / "live")),
        *("--out", str(tmp_path / "replay")),
        *("--start", "2026-01-01T00:00:00Z"),
    ]
    before = error_ledger.counts().get(REFUSED_SITE, 0)
    assert replay_main(argv) == 1
    assert error_ledger.counts().get(REFUSED_SITE, 0) == before + 1
    assert "--start cannot be set" in error_ledger.last_details()[REFUSED_SITE]
    assert not (tmp_path / "replay").exists()
