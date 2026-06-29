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
"""Unit tests for _publish_snapshot_batch — Redis pub/sub publish function."""

import asyncio
import json
from unittest.mock import AsyncMock

from dydx_collector.collector import _publish_snapshot_batch
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId

_IID = InstrumentId.from_str("BTC-USD-PERP.DYDX")


def _snap(ts_event: int = 1_000_000_000) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=_IID,
        bid_prices=[50000.0],
        bid_sizes=[1.0],
        ask_prices=[50001.0],
        ask_sizes=[1.0],
        buy_volume=10.0,
        sell_volume=5.0,
        buy_count=2,
        sell_count=1,
        ts_event=ts_event,
        ts_init=ts_event,
    )


def test_empty_batch_does_not_publish() -> None:
    redis_client = AsyncMock()
    asyncio.run(_publish_snapshot_batch(redis_client, []))
    redis_client.publish.assert_not_called()


def test_single_snapshot_calls_publish() -> None:
    redis_client = AsyncMock()
    snap = _snap()
    asyncio.run(_publish_snapshot_batch(redis_client, [snap]))
    redis_client.publish.assert_called_once()
    call_args = redis_client.publish.call_args
    assert call_args[0][0] == "snapshots:1s"


def test_payload_is_valid_json() -> None:
    redis_client = AsyncMock()
    snap = _snap()
    asyncio.run(_publish_snapshot_batch(redis_client, [snap]))
    call_args = redis_client.publish.call_args
    payload = call_args[0][1]
    parsed = json.loads(payload)
    assert isinstance(parsed, list)


def test_payload_has_one_element_with_correct_instrument_id() -> None:
    redis_client = AsyncMock()
    snap = _snap()
    asyncio.run(_publish_snapshot_batch(redis_client, [snap]))
    call_args = redis_client.publish.call_args
    payload = call_args[0][1]
    parsed = json.loads(payload)
    assert len(parsed) == 1
    assert parsed[0]["instrument_id"] == _IID.value


def test_connection_error_is_caught_silently() -> None:
    redis_client = AsyncMock()
    redis_client.publish.side_effect = ConnectionError("Redis down")
    snap = _snap()
    # Must not raise
    asyncio.run(_publish_snapshot_batch(redis_client, [snap]))
