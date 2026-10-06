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
The live `derivs:raw` push (Story 33.4): mark, index, funding and open-interest rows reach
`LiveStream.publish_derivs` once per sample tick, a failure or an overflow is ledgered with its
count, the archive is never affected, and nothing accumulates without a live stream.
"""

import asyncio
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from kernel.open_interest import OpenInterest
from observability import error_ledger

import capture.application.capture_service as capture_mod
from capture.application import sites
from capture.application.capture_service import CaptureService
from capture.tests.test_collector import _BYBIT
from capture.tests.test_collector import _collector
from capture.tests.test_collector import _LifecycleClient
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import InstrumentClose
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.enums import InstrumentCloseType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_T = 1_790_000_000_000_000_000
_IID = InstrumentId.from_str(_BYBIT)


class _DerivsStream:
    """A `LiveStream` recording each `derivs:raw` batch; `failing` raises (Redis down)."""

    def __init__(self, failing: bool = False) -> None:
        self.batches: list[list[dict[str, Any]]] = []
        self._failing = failing

    async def publish(self, snapshots: list) -> None:
        return None

    async def publish_hotpath(self, venue: str, report: dict) -> None:
        return None

    async def publish_derivs(self, rows: list[dict[str, Any]]) -> None:
        if self._failing:
            raise ConnectionError("redis down")
        self.batches.append(rows)

    async def close(self) -> None:
        return None


def _live(tmp_path: Path, stream: _DerivsStream) -> CaptureService:
    c = _collector(tmp_path)
    c._live_stream = stream
    return c


def _funding(ts: int = _T) -> FundingRateUpdate:
    return FundingRateUpdate(_IID, Decimal("0.0001"), ts, ts + 5, 480, ts + 3_600)


def _mark(ts: int) -> MarkPriceUpdate:
    return MarkPriceUpdate(_IID, Price.from_str("100.50"), ts, ts + 5)


def _oi(ts: int = _T) -> OpenInterest:
    return OpenInterest(_IID, Decimal("51234.567"), ts, ts)


def test_a_funding_row_and_a_rest_open_interest_row_reach_one_publish(tmp_path: Path) -> None:
    stream = _DerivsStream()
    c = _live(tmp_path, stream)
    c._process_data(_funding())
    c.ingest_rows([_oi()], sites.OPEN_INTEREST_POLL)
    asyncio.run(c._publish_derivs())
    assert stream.batches == [
        [
            {
                "instrument_id": _BYBIT,
                "kind": "funding",
                "t": _T,
                "ts_init": _T + 5,
                "value": "0.0001",
                "interval": 28_800,
                "next_funding_ns": _T + 3_600,
            },
            {"instrument_id": _BYBIT, "kind": "oi", "t": _T, "ts_init": _T, "value": "51234.567"},
        ]
    ]
    assert c._derivs_pending == []


def test_an_empty_tick_publishes_nothing(tmp_path: Path) -> None:
    stream = _DerivsStream()
    asyncio.run(_live(tmp_path, stream)._publish_derivs())
    assert stream.batches == []


def test_a_failed_publish_is_ledgered_with_its_count_and_the_rows_are_still_archived(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    c = _live(tmp_path, _DerivsStream(failing=True))
    c._process_data(_funding())
    c.ingest_rows([_oi()], sites.OPEN_INTEREST_POLL)
    asyncio.run(c._publish_derivs())
    assert error_ledger.counts() == {sites.DERIVS_PUBLISH: 1}
    asyncio.run(c._flush_once(final=True))
    catalog = ParquetDataCatalog(str(tmp_path))
    (funding,) = catalog.query(FundingRateUpdate, identifiers=[_BYBIT])
    (oi,) = catalog.query(OpenInterest, identifiers=[_BYBIT])
    # The catalog decodes the funding rate as its stored text, not a `Decimal`.
    assert (str(funding.rate), oi.data.open_interest) == ("0.0001", Decimal("51234.567"))


def test_the_failed_publish_line_names_the_row_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[str] = []
    c = _live(tmp_path, _DerivsStream(failing=True))
    monkeypatch.setattr(c, "_ledger", lambda site, detail="", exc=None: lines.append(detail))
    c._process_data(_mark(_T))
    c._process_data(_mark(_T + 1))
    asyncio.run(c._publish_derivs())
    assert lines == ["live publish of 2 derivs rows failed (Parquet unaffected)"]


def test_rows_past_the_pending_cap_are_counted_and_ledgered_and_still_archived(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lines: list[tuple[str, str]] = []
    stream = _DerivsStream()
    c = _live(tmp_path, stream)
    monkeypatch.setattr(capture_mod, "_DERIVS_PENDING_MAX", 2)
    monkeypatch.setattr(
        c, "_ledger", lambda site, detail="", exc=None: lines.append((site, detail))
    )
    for k in range(3):
        c._process_data(_mark(_T + k))
    asyncio.run(c._publish_derivs())
    assert [row["t"] for row in stream.batches[0]] == [_T, _T + 1]
    assert lines == [
        (
            sites.DERIVS_PUBLISH,
            "1 derivs rows past the 2-row pending cap were not pushed live (Parquet unaffected)",
        )
    ]
    assert len(c._buffer[(MarkPriceUpdate, _BYBIT)]) == 3
    assert c._derivs_overflow == 0  # reset by the drain that reported it


def test_nothing_accumulates_without_a_live_stream(tmp_path: Path) -> None:
    c = _collector(tmp_path)  # live_stream=None
    c._process_data(_funding())
    c.ingest_rows([_oi()], sites.OPEN_INTEREST_POLL)
    assert c._derivs_pending == []
    assert [len(c._buffer[(t, _BYBIT)]) for t in (FundingRateUpdate, OpenInterest)] == [1, 1]


def test_one_arrival_mode_tick_publishes_the_pending_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = _DerivsStream()
    c = _live(tmp_path, stream)
    c._process_data(_funding())
    second = 1_790_000_000
    wall_ns = second * 1_000_000_000 + 500_000_000
    monkeypatch.setattr(
        capture_mod, "time", SimpleNamespace(time=lambda: second + 0.2, time_ns=lambda: wall_ns)
    )

    async def one_iteration(_seconds: float) -> None:
        c.stop()

    monkeypatch.setattr(capture_mod.asyncio, "sleep", one_iteration)
    asyncio.run(c._second_loop())
    assert [[row["kind"] for row in batch] for batch in stream.batches] == [["funding"]]


def _close(ts: int) -> InstrumentClose:
    return InstrumentClose(
        _IID, Price.from_str("100.0"), InstrumentCloseType.END_OF_SESSION, ts, ts
    )


def test_only_derivs_rows_take_a_pending_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Two `InstrumentClose`s (archived by the catch-all branch, not a `derivs:raw` type) then a mark,
    under a cap of 1: the closes never queue, so the mark is pushed and nothing overflows.
    """
    stream = _DerivsStream()
    c = _live(tmp_path, stream)
    monkeypatch.setattr(capture_mod, "_DERIVS_PENDING_MAX", 1)
    c._process_data(_close(_T))
    c._process_data(_close(_T + 1))
    c._process_data(_mark(_T + 2))
    assert (len(c._derivs_pending), c._derivs_overflow) == (1, 0)
    asyncio.run(c._publish_derivs())
    assert [[row["t"] for row in batch] for batch in stream.batches] == [[_T + 2]]
    assert len(c._buffer[(InstrumentClose, _BYBIT)]) == 2  # still archived


def test_a_row_that_fails_to_encode_is_counted_and_the_rest_still_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three marks, the middle one's encode raising: one ledger line naming 1 of 3, two pushed."""
    lines: list[tuple[str, str]] = []
    stream = _DerivsStream()
    c = _live(tmp_path, stream)
    monkeypatch.setattr(
        c, "_ledger", lambda site, detail="", exc=None: lines.append((site, detail))
    )
    encode = capture_mod.to_wire

    def failing_middle(data: object) -> dict[str, Any] | None:
        if getattr(data, "ts_event", None) == _T + 1:
            raise ValueError("unencodable")
        return encode(data)

    monkeypatch.setattr(capture_mod, "to_wire", failing_middle)
    for k in range(3):
        c._process_data(_mark(_T + k))
    asyncio.run(c._publish_derivs())
    assert [[row["t"] for row in batch] for batch in stream.batches] == [[_T, _T + 2]]
    assert lines == [
        (
            sites.DERIVS_PUBLISH,
            "1 of 3 derivs rows could not be encoded, not pushed live (Parquet unaffected); "
            "first: ValueError('unencodable')",
        )
    ]


def _run_to_stop(tmp_path: Path, stream: _DerivsStream, monkeypatch: pytest.MonkeyPatch) -> None:
    """Queue one mark, then run `run()` straight into its shutdown (the stop is already set)."""
    monkeypatch.setattr(capture_mod, "instruments_from_pyo3", lambda pyo3: [])
    c = _collector(tmp_path, client=_LifecycleClient([]))
    c._live_stream = stream
    c._process_data(_mark(_T))
    c._applied.clear()  # `run()` applies the plan itself
    c.stop()
    asyncio.run(c.run())


def test_rows_pending_at_shutdown_are_published_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = _DerivsStream()
    _run_to_stop(tmp_path, stream, monkeypatch)
    assert [[row["t"] for row in batch] for batch in stream.batches] == [[_T]]


def test_rows_pending_at_shutdown_that_fail_to_publish_are_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    _run_to_stop(tmp_path, _DerivsStream(failing=True), monkeypatch)
    assert error_ledger.counts().get(sites.DERIVS_PUBLISH) == 1


class _HangingStream(_DerivsStream):
    """A `LiveStream` whose `derivs:raw` publish never returns: a stop cancels it mid-publish."""

    async def publish_derivs(self, rows: list[dict[str, Any]]) -> None:
        await asyncio.Event().wait()


def test_a_stop_cancelling_a_publish_propagates_and_is_not_ledgered(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    A funding and an OI row in flight when the stop cancels the publish: the cancellation
    propagates, nothing is ledgered (a normal stop is no DATA-07 finding), and the 2 rows left
    unpublished are logged at INFO.
    """
    error_ledger.reset()
    c = _live(tmp_path, _HangingStream())
    c._process_data(_funding())
    c.ingest_rows([_oi()], sites.OPEN_INTEREST_POLL)

    async def stop_mid_publish() -> None:
        task = asyncio.create_task(c._publish_derivs())
        await asyncio.sleep(0)  # the publish is now awaiting
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    with caplog.at_level("INFO", logger=capture_mod.__name__):
        asyncio.run(stop_mid_publish())
    assert error_ledger.counts() == {}
    assert any("2 rows left unpublished" in r.getMessage() for r in caplog.records)
