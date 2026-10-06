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
"""Unit tests for capture's Redis output: `publish_snapshot_batch` and the hot-path record."""

import asyncio
import json
from typing import cast
from unittest.mock import AsyncMock

import pytest
import redis.asyncio as aioredis
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot

from capture.infrastructure.redis_stream import DERIVS_CHANNEL
from capture.infrastructure.redis_stream import HOTPATH_CHANNEL
from capture.infrastructure.redis_stream import RedisLiveStream
from capture.infrastructure.redis_stream import hotpath_key
from capture.infrastructure.redis_stream import hotpath_payload
from capture.infrastructure.redis_stream import publish_derivs_batch
from capture.infrastructure.redis_stream import publish_snapshot_batch
from nautilus_trader.model.identifiers import InstrumentId


_IID = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")


def _snap(ts_event: int = 1_000_000_000) -> DydxSecondSnapshot:
    return make_snapshot(
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
    asyncio.run(publish_snapshot_batch(redis_client, []))
    redis_client.publish.assert_not_called()


def test_single_snapshot_calls_publish() -> None:
    redis_client = AsyncMock()
    snap = _snap()
    asyncio.run(publish_snapshot_batch(redis_client, [snap]))
    redis_client.publish.assert_called_once()
    call_args = redis_client.publish.call_args
    assert call_args[0][0] == "snapshots:raw"


def test_payload_is_valid_json() -> None:
    redis_client = AsyncMock()
    snap = _snap()
    asyncio.run(publish_snapshot_batch(redis_client, [snap]))
    call_args = redis_client.publish.call_args
    payload = call_args[0][1]
    parsed = json.loads(payload)
    assert isinstance(parsed, list)


def test_payload_has_one_element_with_correct_instrument_id() -> None:
    redis_client = AsyncMock()
    snap = _snap()
    asyncio.run(publish_snapshot_batch(redis_client, [snap]))
    call_args = redis_client.publish.call_args
    payload = call_args[0][1]
    parsed = json.loads(payload)
    assert len(parsed) == 1
    assert parsed[0]["instrument_id"] == _IID.value


def test_a_connection_error_reaches_the_caller_to_ledger() -> None:
    """Story 31.2: the failure is the service's to ledger (`collector.snapshot_publish`)."""
    redis_client = AsyncMock()
    redis_client.publish.side_effect = ConnectionError("Redis down")
    with pytest.raises(ConnectionError, match="Redis down"):
        asyncio.run(publish_snapshot_batch(redis_client, [_snap()]))


# -- the per-flush hot-path record (Story 28.1) ---------------------------------------------------

_REPORT = {
    "queue_depth_max": 37,
    "messages_processed": 500,
    "wakes": 60,
    "lag_max_ms": 3200.0,
    "lag_p99_ms": 1.25,
    "write_data_ms": 4.5,
}


class _FakePipeline:
    def __init__(self, commands: list[tuple], failing: bool) -> None:
        self._commands = commands
        self._failing = failing

    def publish(self, channel: str, payload: str) -> None:
        self._commands.append(("PUBLISH", channel, payload))

    def set(self, key: str, payload: str) -> None:
        self._commands.append(("SET", key, payload))

    async def execute(self) -> None:
        if self._failing:
            raise ConnectionError("Redis down")
        self._commands.append(("EXECUTE",))


class _FakeRedis:
    """Records the one pipeline `publish_hotpath` sends, and whether it asked for a MULTI."""

    def __init__(self, failing: bool = False) -> None:
        self.commands: list[tuple] = []
        self.transactions: list[bool] = []
        self._failing = failing

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        self.transactions.append(transaction)
        return _FakePipeline(self.commands, self._failing)


def _stream(fake: _FakeRedis) -> RedisLiveStream:
    stream = RedisLiveStream("redis://unused")
    stream._client = cast(aioredis.Redis, fake)  # the lazily created client, already there
    return stream


def test_the_hotpath_key_is_per_venue_in_lower_case() -> None:
    assert hotpath_key("BYBIT") == "capture:hotpath:bybit"


def test_the_hotpath_payload_carries_the_venue_the_time_and_the_figures() -> None:
    parsed = json.loads(hotpath_payload("BYBIT", _REPORT, 1_790_000_000_000_000_000))
    assert parsed == {"venue": "BYBIT", "ts": 1_790_000_000_000_000_000, **_REPORT}


def test_publish_hotpath_publishes_and_sets_the_same_record_in_one_pipeline() -> None:
    fake = _FakeRedis()
    asyncio.run(_stream(fake).publish_hotpath("HYPERLIQUID", _REPORT))
    (publish, set_, execute) = fake.commands
    assert (publish[0], publish[1]) == ("PUBLISH", HOTPATH_CHANNEL)
    assert (set_[0], set_[1]) == ("SET", "capture:hotpath:hyperliquid")
    assert publish[2] == set_[2]
    assert json.loads(publish[2])["venue"] == "HYPERLIQUID"
    assert execute == ("EXECUTE",)
    assert fake.transactions == [False]


def test_a_failed_hotpath_round_trip_reaches_the_caller_to_ledger() -> None:
    with pytest.raises(ConnectionError, match="Redis down"):
        asyncio.run(_stream(_FakeRedis(failing=True)).publish_hotpath("BYBIT", _REPORT))


# -- the derivatives rows (Story 33.4) ------------------------------------------------------------


def test_derivs_rows_are_published_as_one_json_array_on_derivs_raw() -> None:
    redis_client = AsyncMock()
    rows = [{"instrument_id": _IID.value, "kind": "mark", "t": 1, "ts_init": 2, "value": "100.5"}]
    asyncio.run(publish_derivs_batch(redis_client, rows))
    redis_client.publish.assert_called_once_with(DERIVS_CHANNEL, json.dumps(rows))
    assert DERIVS_CHANNEL == "derivs:raw"


def test_an_empty_derivs_batch_publishes_nothing() -> None:
    redis_client = AsyncMock()
    asyncio.run(publish_derivs_batch(redis_client, []))
    redis_client.publish.assert_not_called()
