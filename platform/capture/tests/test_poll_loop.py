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
`CaptureService.poll_loop` (Story 26.2): the generic REST poll that replaced the dYdX and Bybit
`_open_interest_loop` bodies, with their behaviour -- rows straight into the flush buffer (never
WS feed liveness), the plan's ids only or every row, and a failed round ledgered and survived.
"""

import asyncio
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest
from kernel.open_interest import OpenInterest
from observability import error_ledger

from capture.application import sites
from capture.application.capture_service import CaptureService
from capture.application.ports import PolledRows
from capture.tests.test_collector import _BYBIT
from capture.tests.test_collector import _collector
from nautilus_trader.model.identifiers import InstrumentId


_OTHER = "ETHUSDT-LINEAR.BYBIT"


def _oi(iid: str) -> OpenInterest:
    return OpenInterest(
        instrument_id=InstrumentId.from_str(iid),
        open_interest=Decimal("12.5"),
        ts_event=1,
        ts_init=1,
    )


def _one_round(
    c: CaptureService,
    fetch: Callable[[], list[OpenInterest]],
    plan_only: bool,
    malformed: list[tuple[str | None, str]] | None = None,
) -> None:
    """Run the loop for one poll: the fetch stops the service, so the loop exits after it."""

    async def fetch_once() -> PolledRows:
        c.stop()
        return PolledRows(fetch(), malformed or [])

    asyncio.run(
        c.poll_loop(
            fetch_once, 0, site=sites.OPEN_INTEREST_POLL, failure="poll failed", plan_only=plan_only
        )
    )


def _buffered(c: CaptureService) -> list[str]:
    return sorted(iid for (kind, iid), rows in c._buffer.items() if kind is OpenInterest and rows)


def test_plan_only_keeps_the_plans_ids(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    _one_round(c, lambda: [_oi(_BYBIT), _oi(_OTHER)], plan_only=True)
    assert _buffered(c) == [_BYBIT]


def test_a_venue_wide_poll_keeps_every_row(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    _one_round(c, lambda: [_oi(_BYBIT), _oi(_OTHER)], plan_only=False)
    assert _buffered(c) == sorted([_BYBIT, _OTHER])


def test_polled_rows_are_never_ws_feed_liveness(tmp_path: Path) -> None:
    c = _collector(tmp_path)
    _one_round(c, lambda: [_oi(_BYBIT)], plan_only=False)
    assert c._feeds.last_ns == {}
    assert c._ingest_backlog() == 0  # no row went through the queue (the stop sentinel is not one)


def test_a_failed_round_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)

    def failing() -> list[OpenInterest]:
        raise ConnectionError("indexer down")

    _one_round(c, failing, plan_only=True)
    assert error_ledger.counts() == {"collector.open_interest_poll": 1}
    assert error_ledger.last_details()["collector.open_interest_poll"] == "poll failed"
    assert _buffered(c) == []


def test_the_loop_polls_again_after_a_failed_round(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    rounds = 0

    async def fail_then_succeed() -> PolledRows:
        nonlocal rounds
        rounds += 1
        if rounds == 1:
            raise ConnectionError("indexer down")
        c.stop()
        return PolledRows([_oi(_BYBIT)], [])

    asyncio.run(
        c.poll_loop(
            fail_then_succeed,
            0,
            site=sites.OPEN_INTEREST_POLL,
            failure="poll failed",
            plan_only=True,
        )
    )
    assert rounds == 2
    assert error_ledger.counts() == {"collector.open_interest_poll": 1}
    assert _buffered(c) == [_BYBIT]


def test_malformed_rows_of_planned_or_unknown_ids_are_ledgered_in_one_line(tmp_path: Path) -> None:
    """Story 31.2: a row the parser could not read is ledgered at the poll's site, not skipped."""
    error_ledger.reset()
    c = _collector(tmp_path)
    malformed = [(_BYBIT, "no openInterest"), (_OTHER, "no openInterest"), (None, "no symbol")]
    _one_round(c, lambda: [_oi(_BYBIT)], plan_only=True, malformed=malformed)
    assert _buffered(c) == [_BYBIT]  # the other rows are kept
    assert error_ledger.counts() == {"collector.open_interest_poll": 1}
    detail = error_ledger.last_details()["collector.open_interest_poll"]
    assert "2 polled rows could not be parsed" in detail
    assert f"{_BYBIT}: no openInterest" in detail
    assert "<no id>: no symbol" in detail
    assert _OTHER not in detail  # not planned: the venue-wide poll's other markets


def test_a_venue_wide_poll_ledgers_every_malformed_row(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    _one_round(c, list, plan_only=False, malformed=[(_OTHER, "bad value")])
    assert f"{_OTHER}: bad value" in error_ledger.last_details()["collector.open_interest_poll"]


def test_the_malformed_line_names_ten_rows_and_counts_them_all(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    malformed: list[tuple[str | None, str]] = [(f"X{n}-LINEAR.BYBIT", "bad") for n in range(15)]
    _one_round(c, list, plan_only=False, malformed=malformed)
    detail = error_ledger.last_details()["collector.open_interest_poll"]
    assert detail.startswith("15 polled rows could not be parsed")
    assert "X9-LINEAR.BYBIT" in detail
    assert "X10-LINEAR.BYBIT" not in detail
    assert detail.endswith("(+5 more)")


def test_a_clean_round_ledgers_nothing(tmp_path: Path) -> None:
    error_ledger.reset()
    c = _collector(tmp_path)
    _one_round(c, lambda: [_oi(_BYBIT)], plan_only=True)
    assert error_ledger.counts() == {}


def test_loops_are_added_only_before_run(tmp_path: Path) -> None:
    c = _collector(tmp_path)

    async def loop() -> None:
        return None

    c.add_loops(loop)
    assert c._extra_loops[-1] is loop
    c._started = True  # as `run()` leaves it
    with pytest.raises(RuntimeError, match="would never run"):
        c.add_loops(loop)


def test_the_first_round_runs_before_the_first_sleep(tmp_path: Path) -> None:
    """DW-237: a process restarting faster than the period still records a poll."""
    c = _collector(tmp_path)
    rounds = 0

    async def fetch() -> PolledRows:
        nonlocal rounds
        rounds += 1
        return PolledRows([_oi(_BYBIT)], [])

    async def scenario() -> None:
        task = asyncio.ensure_future(
            c.poll_loop(
                fetch, 3600, site=sites.OPEN_INTEREST_POLL, failure="poll failed", plan_only=True
            )
        )
        with pytest.raises(TimeoutError):  # asleep for the hour after its first round
            await asyncio.wait_for(task, 0.2)

    asyncio.run(scenario())
    assert rounds == 1
    assert _buffered(c) == [_BYBIT]
