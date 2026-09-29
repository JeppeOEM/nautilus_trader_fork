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
"""
Story 29.6: the `make bots-churn-check` sequence checker (`bots/tests/churn_check.py`), fed
`bots:status` payloads exactly as `supervise.build_status` shapes them. The live run itself needs
Docker, dYdX market data and ~10 minutes, so it is a make target, not a unit test.
"""

import json
from pathlib import Path

import pytest

from bots.tests.churn_check import CHECKS
from bots.tests.churn_check import ChurnChecker
from bots.tests.churn_check import _status as _message_status
from bots.tests.churn_check import main


def _status(**overrides: object) -> dict:
    base: dict[str, object] = {
        "bot_id": "churn-01",
        "strategy": "DummyStrategy",
        "symbol": "BTC-USD-PERP.DYDX",
        "mode": "paper",
        "running": True,
        "position_side": "long",
        "net_exposure": 100.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "win_rate": None,
        "closed_trades": 0,
        "started_at": 1.0,
        "updated_at": 2.0,
        "stop_loss": "99950",
        "take_profit": "100051",
        "entry_price": "100001",
        "mark_price": "100000.5",
        "position_qty": "0.001",
        "stop_loss_orders": 1,
        "take_profit_orders": 1,
        "open_orders": 2,
        "last_fill_at": 1,
    }
    base.update(overrides)
    return base


def _flat(**overrides: object) -> dict:
    flat: dict[str, object] = {
        "position_side": "flat",
        "stop_loss": None,
        "take_profit": None,
        "entry_price": None,
        "mark_price": None,
        "position_qty": None,
        "stop_loss_orders": 0,
        "take_profit_orders": 0,
        "open_orders": 0,
        "closed_trades": 1,
    }
    flat.update(overrides)
    return _status(**flat)


_SECOND_LONG = {
    "closed_trades": 1,
    "stop_loss": "100010",
    "take_profit": "100111",
    "entry_price": "100061",
}


def _fed(*statuses: dict) -> ChurnChecker:
    checker = ChurnChecker("churn-01")
    for status in statuses:
        checker.feed(status)
    return checker


def test_the_full_sequence_passes_every_check() -> None:
    checker = _fed(_status(), _flat(), _status(**_SECOND_LONG))
    assert checker.done
    assert checker.report() == "churn-01: all 3 checks passed"
    assert list(checker.captured()["checks"]) == list(CHECKS)


def test_a_price_that_is_not_a_number_is_a_named_check_failure() -> None:
    checker = _fed(_status(stop_loss="garbage"))
    assert not checker.passed
    assert "a price is not a number" in checker.report()


def test_waiting_messages_in_between_do_not_break_the_order() -> None:
    warming_up = _flat(closed_trades=0)
    entry_filled_legs_pending = _status(stop_loss=None, stop_loss_orders=0, open_orders=1)
    sibling_not_cancelled_yet = _flat(open_orders=1)
    checker = _fed(
        warming_up,
        entry_filled_legs_pending,
        _status(),
        _status(),
        sibling_not_cancelled_yet,
        _flat(),
        _status(**_SECOND_LONG),
    )
    assert checker.done


def test_another_bots_messages_are_ignored() -> None:
    checker = _fed(_status(bot_id="bot-01"), _flat(bot_id="bot-01"))
    assert checker.passed == []
    assert "no bots:status message received" in checker.report()


@pytest.mark.parametrize(
    ("status", "problem"),
    [
        (_status(stop_loss_orders=2), "stop_loss_orders is 2, not 1"),
        (_status(take_profit=None), "take_profit is null"),
        (_status(mark_price=None), "mark_price is null"),
        (_status(stop_loss="100002"), "not stop_loss < entry_price < take_profit"),
        (_status(take_profit="100000"), "not stop_loss < entry_price < take_profit"),
        (_status(position_side="short"), "position_side is not long"),
    ],
)
def test_a_long_that_is_not_protected_names_the_failed_check(status: dict, problem: str) -> None:
    checker = _fed(status)
    assert checker.passed == []
    report = checker.report()
    assert "check 1 (protected long) not reached" in report
    assert problem in report


@pytest.mark.parametrize(
    ("flat", "problem"),
    [
        (_flat(open_orders=1), "open_orders is 1, not 0 (orphaned order)"),
        (_flat(closed_trades=0), "closed_trades 0 is not above 0"),
        (_flat(stop_loss="99950"), "stop_loss is not null"),
    ],
)
def test_a_flat_that_leaves_an_orphan_or_no_trade_fails_check_two(flat: dict, problem: str) -> None:
    checker = _fed(_status(), flat)
    assert len(checker.passed) == 1
    report = checker.report()
    assert "check 2 (flat with no orphaned order) not reached" in report
    assert problem in report


def test_a_second_long_with_the_first_ones_exits_is_not_fresh() -> None:
    checker = _fed(_status(), _flat(), _status(closed_trades=1))
    assert len(checker.passed) == 2
    assert "not fresh" in checker.report()


def test_a_second_long_before_any_flat_does_not_count() -> None:
    checker = _fed(_status(), _status(**_SECOND_LONG))
    assert len(checker.passed) == 1


def test_the_cli_reports_an_unreachable_redis(tmp_path: Path) -> None:
    out = tmp_path / "payloads.json"
    code = main(["--redis-url", "redis://127.0.0.1:1", "--timeout", "1", "--out", str(out)])
    assert code == 2
    assert json.loads(out.read_text())["passed"] is False


@pytest.mark.parametrize("data", [b"not json", b"[1, 2]", b"null"])
def test_a_malformed_status_message_is_skipped_not_fatal(data: bytes) -> None:
    assert _message_status(data) is None


def test_a_status_message_is_read_as_a_dict() -> None:
    assert _message_status(json.dumps(_status()).encode()) == _status()


def test_captured_payloads_are_json() -> None:
    checker = _fed(_status(), _flat())
    text = json.dumps(checker.captured())
    assert json.loads(text)["checks"]["protected long"]["stop_loss"] == "99950"
