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
Story 31.2's proof, on recorded frames: an ingest backlog must not drop live trades.

The committed Bybit `linear.publicTrade` fixture (recorded live on 2026-09-29) is rebuilt into real
`TradeTick`s exactly as the Rust client stamps them -- `ts_event` the venue's `T` (ms), `ts_init`
the socket receipt (`recv_ns`) -- and replayed through `CaptureService._process_data` with the
service's processing clock 15 s behind receipt (an ingest-queue stall). The old stale rule judged
age at processing time (`now - ts_event > stale_trade_seconds`) and would drop every one of them as
subscribe-time history; the fixed rule judges age on arrival (`ts_init - ts_event`), so every
reference id is archived and none is neither archived nor ledgered.

It lives in `platform/tests` (the cross-cutting guards, the one place allowed to import two
contexts, `test_boundaries.py`'s exemption): it reads the verification context's recorded frames
and drives capture's service, and no context's own tests may reach across like that.
"""

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import capture.application.capture_service as capture_mod
import pytest
from capture.application.capture_service import CaptureService
from capture.application.config import CoreConfig
from capture.application.feed import Feed
from capture.infrastructure.parquet_writer import ParquetArchiveWriter
from capture.tests.definition_kit import definitions
from verification.infrastructure.raw_store import iter_records

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_FIXTURE = (
    Path(__file__).parent.parent
    / "verification"
    / "tests"
    / "fixtures"
    / "raw"
    / "bybit"
    / "linear.publicTrade"
    / "2026-09-29T10.jsonl.zst"
)
_IIDS = ("BTCUSDT-LINEAR.BYBIT", "ETHUSDT-LINEAR.BYBIT")
_STALL_NS = 15_000_000_000
_S = 1_000_000_000
_SIDES = {"Buy": AggressorSide.BUYER, "Sell": AggressorSide.SELLER}


def _reference_trades() -> Iterator[TradeTick]:
    """Every recorded public trade as the Rust client hands it to capture (both clocks)."""
    for record in iter_records(_FIXTURE):
        if record["kind"] != "frame":
            continue
        frame = json.loads(str(record["raw"]))
        for trade in frame["data"]:
            yield TradeTick(
                InstrumentId.from_str(f"{trade['s']}-LINEAR.BYBIT"),
                Price.from_str(trade["p"]),
                Quantity.from_str(trade["v"]),
                _SIDES[trade["S"]],
                TradeId(trade["i"]),
                int(trade["T"]) * 1_000_000,
                int(str(record["recv_ns"])),
            )


def _bybit_like_service(catalog: Path) -> CaptureService:
    """Bybit's venue-mode thresholds (its committed `config.toml`), no network."""
    config = CoreConfig(
        environment="mainnet",
        catalog_path=str(catalog),
        book_time_source="venue",
        hold_back_seconds=0.5,
    )
    service = CaptureService(
        config,
        lambda _on_data, _ledger: SimpleNamespace(),
        venue="BYBIT",
        plan=_IIDS,
        archive=ParquetArchiveWriter(config.catalog_path),
        live_stream=None,
    )
    service._applied.update(_IIDS)  # as `run()`'s initial apply leaves it
    service._instruments = definitions(*_IIDS)
    return service


def _replay_stalled(
    service: CaptureService, trades: list[TradeTick], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Process each trade with the service's clock `_STALL_NS` past the trade's receipt."""
    clock = {"now": 0}
    fake_time = SimpleNamespace(time_ns=lambda: clock["now"], time=lambda: clock["now"] / _S)
    monkeypatch.setattr(capture_mod, "time", fake_time)
    for trade in trades:
        clock["now"] = trade.ts_init + _STALL_NS
        service._process_data(trade, Feed("linear", "linear"))
    clock["now"] = trades[-1].ts_init + _STALL_NS + 60 * _S
    asyncio.run(service._flush_once(final=True))


def test_a_recorded_burst_processed_after_a_15s_stall_is_archived_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trades = list(_reference_trades())
    reference = {str(t.trade_id) for t in trades}
    assert len(reference) == len(trades) > 0, "the fixture must replay into distinct trades"
    stale_s = CoreConfig(environment="mainnet", catalog_path="x").stale_trade_seconds

    # The evidence: the processing-time rule would have called these subscribe-time history.
    old_rule_dropped = [t for t in trades if t.ts_init + _STALL_NS - t.ts_event > stale_s * _S]
    assert len(old_rule_dropped) > 0

    service = _bybit_like_service(tmp_path / "catalog")
    _replay_stalled(service, trades, monkeypatch)

    catalog = service._archive.catalog  # type: ignore[attr-defined]
    archived = {str(t.trade_id) for t in catalog.trade_ticks(instrument_ids=list(_IIDS))}
    ledgered = sum(intake.take_counts().stale for intake in service._intakes.values())
    assert ledgered == 0
    assert sorted(reference - archived) == [], "neither archived nor ledgered"
    assert archived == reference
