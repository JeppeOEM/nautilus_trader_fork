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
The capture service's seams for a venue's liquidation feed (Story 33.1): `ingest_rows` (the plan
filter, never WS liveness), a `ChannelRetry` subscribe (applied, book kept, retried), the
`not_running` coverage of a restart, `note_coverage`, and the status field.
"""

import asyncio
from pathlib import Path

from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from capture.application import sites
from capture.application.capture_service import CaptureService
from capture.application.config import CoreConfig
from capture.application.feed import ChannelRetry
from capture.application.ports import PlanChange
from capture.domain.coverage import LiquidationsUnrecoverable
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from capture.tests.definition_kit import definitions
from nautilus_trader.model.identifiers import InstrumentId


_A = "AAAUSDT-LINEAR.BYBIT"
_B = "BBBUSDT-LINEAR.BYBIT"
_S_NS = 1_000_000_000


class _LiquidationClient:
    """A client with the liquidation capabilities; `retry` ids raise `ChannelRetry` once."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.retry: set[str] = set()
        self.held: set[str] = set()
        self.restarts: list[tuple[str, int, int]] = []
        self.state = "connected"

    async def subscribe(self, iid: str) -> None:
        self.calls.append(iid)
        if iid in self.retry:
            self.retry.discard(iid)
            raise ChannelRetry(f"{iid}: liquidation topic")
        self.held.add(iid)

    async def unsubscribe(self, iid: str) -> None:
        self.held.discard(iid)

    def liquidation_state(self) -> str:
        return self.state

    def note_liquidation_restart(self, iid: str, from_ns: int, to_ns: int) -> None:
        self.restarts.append((iid, from_ns, to_ns))


def _service(tmp_path: Path, client: object, plan: tuple[str, ...] = (_A,)) -> CaptureService:
    config = CoreConfig(environment="mainnet", catalog_path=str(tmp_path))
    capture = CaptureService(
        config,
        lambda _on_data, _ledger: client,
        venue="BYBIT",
        plan=plan,
        archive=ParquetArchiveWriter(config.catalog_path),
        live_stream=None,
    )
    capture._instruments = definitions(_A, _B)
    return capture


def _liquidation(iid: str) -> Liquidation:
    return Liquidation.from_wire_text(
        InstrumentId.from_str(iid), LiquidatedSide.LONG, "0.010", "60000.10", (2, 3), "k", 1, 2
    )


def test_ingest_rows_keeps_the_plans_rows_and_returns_them(tmp_path: Path) -> None:
    capture = _service(tmp_path, _LiquidationClient())
    planned, other = _liquidation(_A), _liquidation(_B)
    kept = capture.ingest_rows([planned, other], sites.LIQUIDATION_FEED)
    assert kept == [planned]
    assert capture._buffer[(Liquidation, _A)] == [planned]
    assert capture._buffer.get((Liquidation, _B), []) == []


def test_ingested_rows_bypass_the_queue_and_feed_liveness(tmp_path: Path) -> None:
    capture = _service(tmp_path, _LiquidationClient())
    capture.ingest_rows([_liquidation(_A)], sites.LIQUIDATION_FEED)
    assert capture._ingest_backlog() == 0
    assert capture._feeds.last_ns == {}


def test_ingest_rows_ledgers_malformed_rows_at_the_site(tmp_path: Path) -> None:
    error_ledger.reset()
    capture = _service(tmp_path, _LiquidationClient())
    capture.ingest_rows([], sites.LIQUIDATION_FEED, malformed=[(_A, "bad")])
    assert error_ledger.counts() == {sites.LIQUIDATION_FEED: 1}


def test_a_channel_retry_keeps_the_id_applied_and_queues_it(tmp_path: Path) -> None:
    error_ledger.reset()
    client = _LiquidationClient()
    client.retry.add(_A)
    capture = _service(tmp_path, client)
    capture._book(_A)  # a book booked during the subscribe must survive it
    applied = asyncio.run(capture.apply(PlanChange(added=frozenset({_A}))))
    assert applied.subscribed == {_A}
    assert capture.capture_status().applied == {_A}
    assert _A in capture._books
    assert capture._retry_subscribe == {_A}
    assert error_ledger.counts() == {}  # the client ledgered it, capture does not again
    asyncio.run(capture._retry_subscriptions())
    assert client.calls == [_A, _A]
    assert capture._retry_subscribe == set()


def test_every_restart_gap_is_handed_to_the_client_held_or_not(tmp_path: Path) -> None:
    error_ledger.reset()
    client = _LiquidationClient()
    client.held.add(_A)  # B's subscribe failed at start: its window is the client's to run on
    capture = _service(tmp_path, client, plan=(_A, _B))
    capture._last_archived.update({_A: 100, _B: 100})
    capture._note_restart_gaps([_A, _B], 110)
    assert client.restarts == [
        (_A, 101 * _S_NS, 110 * _S_NS - 1),
        (_B, 101 * _S_NS, 110 * _S_NS - 1),
    ]


def test_an_in_process_re_add_is_not_noted_not_running(tmp_path: Path) -> None:
    client = _LiquidationClient()
    client.held.add(_A)
    capture = _service(tmp_path, client)
    capture._note_restart_gaps([_A], 110)  # the process's first verdict: no archived row yet
    capture._forget_verdicts(_A)  # removed from the plan, then re-added
    capture._last_archived[_A] = 120
    capture._note_restart_gaps([_A], 200)
    assert client.restarts == []


def test_noted_coverage_is_appended_with_the_next_flush(tmp_path: Path) -> None:
    capture = _service(tmp_path, _LiquidationClient())
    line = LiquidationsUnrecoverable(_A, "feed_down", 5, 9)
    capture.note_coverage([line])
    assert capture._take_coverage_lines() == [line.to_json_line()]


def test_the_status_carries_the_feed_state_or_none_without_it(tmp_path: Path) -> None:
    client = _LiquidationClient()
    client.state = "down"
    assert _service(tmp_path, client).capture_status().liquidations == "down"
    assert _service(tmp_path, object()).capture_status().liquidations is None


class _FailingLiquidationWrites:
    """Wraps a real archive writer; every liquidation batch write raises."""

    def __init__(self, inner: object) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    def write(self, items: list) -> None:
        if isinstance(items[0], Liquidation):
            raise OSError("disk full")
        self._inner.write(items)  # type: ignore[attr-defined]


def test_a_failed_liquidation_write_is_a_write_failed_window(tmp_path: Path) -> None:
    error_ledger.reset()
    capture = _service(tmp_path, _LiquidationClient())
    capture._archive = _FailingLiquidationWrites(capture._archive)  # type: ignore[assignment]
    rows = [
        Liquidation.from_wire_text(
            InstrumentId.from_str(_A), LiquidatedSide.LONG, "0.010", "60000.10", (2, 3), k, t, t
        )
        for k, t in (("k1", 9 * _S_NS), ("k2", 5 * _S_NS))
    ]
    capture.ingest_rows(rows, sites.LIQUIDATION_FEED)

    async def no_append(final: bool = False) -> None:
        return None

    capture._write_coverage = no_append  # type: ignore[method-assign]
    asyncio.run(capture._flush_once(final=True))
    assert capture._coverage_trades == [
        LiquidationsUnrecoverable(_A, "write_failed", 5 * _S_NS, 9 * _S_NS)
    ]
    assert "LOST" in error_ledger.last_details()[sites.FLUSH_WRITE]
