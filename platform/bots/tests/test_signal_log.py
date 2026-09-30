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
The opt-in `DummyStrategy` signal log (Story 31.9): its records per kind, a skipped second's
reason, the pure `signal_of` rule, the host's env wiring, and that nothing is written -- and
nothing trades differently -- when the log is off. Runs the real strategy in a `BacktestEngine`
over synthetic data, the pattern of `test_strategy.py`.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from bots.domain.config import BotConfig
from bots.domain.config import ExecConfig
from bots.infrastructure import nautilus_host
from bots.strategies.dummy import _BOOK_SNAPSHOT_TIMER
from bots.strategies.dummy import DummyStrategy
from bots.strategies.signal_log import SignalLog
from bots.strategies.signal_log import signal_of
from bots.tests.test_strategy import _IID
from bots.tests.test_strategy import _STEP_NS
from bots.tests.test_strategy import _TS_START
from bots.tests.test_strategy import _config
from bots.tests.test_strategy import _delta
from bots.tests.test_strategy import _engine
from bots.tests.test_strategy import _quotes_and_deltas
from nautilus_trader.common.events import TimeEvent
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import OrderSide


_ENTERING = {
    "trend_buy_threshold": 0.49,
    "trend_sell_threshold": 0.1,
    "ofi_confirm_threshold": -999_999.0,
    "ofi_levels": 2,
    "obi_levels": 3,
}


def _run(data: list, log_path: Path | None, **overrides: Any) -> tuple[DummyStrategy, list]:
    engine = _engine()
    engine.add_data(data)
    config = _config(
        signal_log_path=str(log_path) if log_path is not None else None,
        order_id_tag="bot-7",
        **overrides,
    )
    strategy = DummyStrategy(config)
    engine.add_strategy(strategy)
    engine.run()
    fills = engine.trader.generate_order_fills_report()
    rows = [] if fills.empty else fills[["side", "quantity", "ts_last"]].to_dict("records")
    engine.reset()
    engine.dispose()
    return strategy, rows


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _entering_log(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "bot-7.jsonl"
    _run(_quotes_and_deltas(n_seconds=15, levels_per_side=4), path, **_ENTERING)
    return _records(path)


@pytest.mark.parametrize(
    ("trend", "mlofi", "expected"),
    [
        (None, 1.0, "not_ready"),
        (0.9, None, "not_ready"),
        (0.61, 0.01, "long"),
        (0.61, 0.0, "none"),  # the confirm is strict: equal to the threshold does not confirm
        (0.6, 5.0, "none"),  # the trend threshold is strict too
        (0.39, -0.01, "short"),
        (0.39, 0.01, "none"),
        (0.5, 3.0, "none"),
    ],
)
def test_signal_of_is_the_strategys_threshold_rule(
    trend: float | None, mlofi: float | None, expected: str
) -> None:
    assert signal_of(trend, mlofi, 0.6, 0.4, 0.0) == expected


def test_start_record_carries_the_start_clock_and_the_config(tmp_path: Path) -> None:
    start = _entering_log(tmp_path)[0]
    assert start["kind"] == "start"
    assert start["bot_id"] == "bot-7"
    assert start["instrument_id"] == str(_IID)
    assert start["ts_ns"] % 1_000 == 0  # whole microseconds, the live timers' resolution
    expected = {
        "bar_spec": "1-SECOND-MID-INTERNAL",
        "trend_lookback": 2,
        "trend_buy_threshold": 0.49,
        "trend_sell_threshold": 0.1,
        "ofi_levels": 2,
        "ofi_window": 2,
        "obi_levels": 3,
        "ofi_confirm_threshold": -999_999.0,
        "trade_size": "0.001",
    }
    assert {key: start[key] for key in expected} == expected


def test_book_cycles_land_on_the_start_plus_whole_seconds(tmp_path: Path) -> None:
    records = _entering_log(tmp_path)
    start_ns = records[0]["ts_ns"]
    books = [record for record in records if record["kind"] == "book"]
    assert books, "no book cycle logged"
    assert [(r["ts_ns"] - start_ns) % _STEP_NS for r in books] == [0] * len(books)


def test_book_records_hold_the_top_levels_exactly_as_fed(tmp_path: Path) -> None:
    book = next(r for r in _entering_log(tmp_path) if r["kind"] == "book")
    # max(ofi_levels=2, obi_levels=3) of the 4 levels per side, [price, size] floats
    assert len(book["bids"]) == 3
    assert len(book["asks"]) == 3
    assert [price for price, _ in book["bids"]] == [100.0, 99.0, 98.0]
    assert [price for price, _ in book["asks"]] == [101.0, 102.0, 103.0]
    assert book["book_ts_ns"] <= book["ts_ns"]


def test_every_cycle_records_the_indicators_the_signal_and_the_action(tmp_path: Path) -> None:
    records = _entering_log(tmp_path)
    cycles = [r for r in records if r["kind"] in ("book", "bar")]
    first = cycles[0]
    assert first["mlofi"] is None or first["trend"] is None
    assert first["signal"] == "not_ready"
    acted = [r for r in cycles if r["action"] is not None]
    assert acted, "the entering thresholds must submit an order"
    assert acted[0]["action"] == "BUY"
    assert acted[0]["signal"] == "long"
    for record in cycles:
        expected = signal_of(record["trend"], record["mlofi"], 0.49, 0.1, -999_999.0)
        assert record["signal"] == expected


def test_bar_records_carry_the_bars_ts_event_and_close(tmp_path: Path) -> None:
    bars = [r for r in _entering_log(tmp_path) if r["kind"] == "bar"]
    assert bars, "no bar cycle logged"
    assert all(r["bar"]["ts_event"] == r["ts_ns"] for r in bars)
    assert all(r["bar"]["close"] == 100.5 for r in bars)  # the MID of every synthetic quote


def test_a_one_sided_book_is_logged_as_skipped_never_dropped(tmp_path: Path) -> None:
    data = [_delta(BookAction.ADD, OrderSide.BUY, 100.0, 10.0, _TS_START, 0)]
    data += [
        _delta(BookAction.UPDATE, OrderSide.BUY, 100.0, 10.0 + i, _TS_START + i * _STEP_NS, i)
        for i in range(1, 5)
    ]
    path = tmp_path / "bot-7.jsonl"
    _run(data, path)
    skipped = [r for r in _records(path) if r["kind"] == "book_skipped"]
    assert skipped, "a one-sided second was not logged"
    assert {r["reason"] for r in skipped} == {"one_sided"}
    assert all("bids" not in r and r["action"] is None for r in skipped)


def test_no_book_at_all_is_logged_as_skipped(tmp_path: Path) -> None:
    # A running strategy always has a book once it subscribed, so the no-book cycle is driven
    # directly: a registered, never-started strategy with its log opened.
    engine = _engine()
    path = tmp_path / "bot-7.jsonl"
    strategy = DummyStrategy(_config(signal_log_path=str(path), order_id_tag="bot-7"))
    engine.add_strategy(strategy)
    strategy._open_signal_log()
    ts = _TS_START + _STEP_NS
    strategy.on_timer(TimeEvent(_BOOK_SNAPSHOT_TIMER, UUID4(), ts, ts))
    strategy.on_stop()
    records = _records(path)
    assert [r["kind"] for r in records] == ["start", "book_skipped"]
    assert records[1]["reason"] == "no_book"
    assert records[1]["ts_ns"] == ts
    assert records[1]["signal"] == "not_ready"
    engine.dispose()


def test_a_skipped_cycle_never_logs_the_previous_cycles_action(tmp_path: Path) -> None:
    engine = _engine()
    path = tmp_path / "bot-7.jsonl"
    strategy = DummyStrategy(_config(signal_log_path=str(path), order_id_tag="bot-7"))
    engine.add_strategy(strategy)
    strategy._open_signal_log()
    strategy._cycle_action = OrderSide.BUY  # the side an earlier cycle submitted
    ts = _TS_START + _STEP_NS
    strategy.on_timer(TimeEvent(_BOOK_SNAPSHOT_TIMER, UUID4(), ts, ts))
    strategy.on_stop()
    records = _records(path)
    assert records[1]["kind"] == "book_skipped"
    assert records[1]["action"] is None
    engine.dispose()


def test_log_off_writes_nothing_and_trades_exactly_as_with_it(tmp_path: Path) -> None:
    data = _quotes_and_deltas(n_seconds=15, levels_per_side=4)
    off_dir = tmp_path / "off"
    off_dir.mkdir()
    _, fills_off = _run(data, None, **_ENTERING)
    _, fills_on = _run(data, tmp_path / "on" / "bot-7.jsonl", **_ENTERING)
    assert list(off_dir.iterdir()) == []
    assert fills_off, "the entering thresholds must fill"
    assert fills_off == fills_on


def test_a_record_after_close_is_refused(tmp_path: Path) -> None:
    log = SignalLog(str(tmp_path / "x.jsonl"))
    log.write({"kind": "start", "value": 0.1 + 0.2})
    log.close()
    with pytest.raises(RuntimeError):
        log.write({"kind": "book"})
    assert _records(tmp_path / "x.jsonl") == [{"kind": "start", "value": 0.1 + 0.2}]


def _bot(**overrides: Any) -> BotConfig:
    fields: dict[str, Any] = {
        "bot_id": "bot-9",
        "instrument_id": "BTCUSDT-LINEAR.BYBIT",
        "trade_size": Decimal("0.001"),
    }
    fields.update(overrides)
    return BotConfig(**fields)


def test_the_host_names_the_log_from_the_env_for_a_dummy_bot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOT_SIGNAL_LOG_DIR", "/logs/live")
    strategy = nautilus_host._strategy_for(_bot())
    assert strategy.config.signal_log_path == "/logs/live/bot-9.jsonl"


def test_the_host_sets_no_log_without_the_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_SIGNAL_LOG_DIR", raising=False)
    strategy = nautilus_host._strategy_for(_bot())
    assert strategy.config.signal_log_path is None


def test_the_host_sets_no_log_for_an_exec_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_SIGNAL_LOG_DIR", "/logs/live")
    exec_bot = ExecConfig(mode="live", environment="testnet", subaccount=0, log_level="INFO")
    strategy = nautilus_host._strategy_for(exec_bot)
    assert strategy.config.signal_log_path is None
