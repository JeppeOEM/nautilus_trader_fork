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
Tests for bots.application.ownership and the supervisor's lease refresh (DW-78): one live process
per `bot_id`. Time is a fake clock whose `sleep` advances it, so a 20 s wait runs instantly; the
leases are `FakeBus`'s, expiring against that clock. The Redis scripts themselves are tested
against a real Redis in `test_ports.py`.
"""

import asyncio
import json
import os
import signal
from collections.abc import Awaitable
from collections.abc import Callable

import pytest
from observability import error_ledger

from bots import __main__ as bots_main
from bots.application.ownership import OWNER_RETRY_SECONDS
from bots.application.ownership import OWNER_TTL_SECONDS
from bots.application.ownership import BotIdCollision
from bots.application.ownership import claim_all
from bots.application.ownership import describe_owner
from bots.application.ownership import owner_value
from bots.application.ownership import reconfirm
from bots.application.ownership import release_all
from bots.application.ports import PositionSnapshot
from bots.application.supervise import STATUS_HEARTBEAT_SECONDS
from bots.application.supervise import Supervisor
from bots.infrastructure.fills_store import SqliteFillsStore
from bots.tests.support import FakeBus
from bots.tests.support import connect_to


_OURS = owner_value("paper", "/app/bots/config.toml", "box-a", "a" * 32, 0.0)
_THEIRS = owner_value("real-money", "/app/exec/live.toml", "box-b", "b" * 32, 0.0)
_TTL_MS = int(OWNER_TTL_SECONDS * 1000)


class _Time:
    """A clock in seconds; `sleep` advances it and then runs every registered tick."""

    def __init__(self) -> None:
        self.now = 0.0
        self.ticks: list[Callable[[], Awaitable[object]]] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        for tick in self.ticks:
            await tick()


class _Runtime:
    """A `BotRuntime` stand-in (the application's own port -- no Nautilus internal is faked)."""

    strategy_name = "DummyStrategy"
    symbol = "BTCUSDT-LINEAR.BYBIT"
    is_running = True
    last_data_ns = 0

    def positions(self) -> PositionSnapshot:
        return PositionSnapshot("flat", 0.0, 0.0)

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def on_fill(self, handler: object) -> None: ...

    def on_position_closed(self, handler: object) -> None: ...

    def on_order_event(self, handler: Callable[[], None]) -> None: ...


@pytest.fixture(autouse=True)
def _fresh_ledger() -> None:
    error_ledger.reset()


def _bus(time: _Time) -> FakeBus:
    bus = FakeBus()
    bus.now = time.clock
    return bus


def _claim(bus: FakeBus, time: _Time, bot_ids: list[str]) -> None:
    connect = connect_to(bus)
    asyncio.run(claim_all(connect, bot_ids, _OURS, clock=time.clock, sleep=time.sleep))


def _held_by_a_live_process(bus: FakeBus, time: _Time, bot_id: str) -> None:
    """Another process holds `bot_id` and renews it on every one of our sleeps, as it would."""
    asyncio.run(bus.hold(f"bots:owner:{bot_id}", _THEIRS, _TTL_MS))
    time.ticks.append(lambda: bus.hold(f"bots:owner:{bot_id}", _THEIRS, _TTL_MS))


def _refusal(bus: FakeBus, time: _Time, bot_ids: list[str]) -> str:
    with pytest.raises(BotIdCollision) as refused:
        _claim(bus, time, bot_ids)
    return str(refused.value)


def _supervisor(bus: FakeBus, time: _Time, store: SqliteFillsStore) -> Supervisor:
    return Supervisor(
        "bot-01",
        "paper",
        _Runtime(),
        store,
        connect_to(bus),
        owner=_OURS,
        clock=time.clock,
        clock_ns=lambda: int(time.now * 1e9),
    )


def test_a_fresh_start_claims_every_lease() -> None:
    time = _Time()
    bus = _bus(time)

    _claim(bus, time, ["bot-01", "bot-02"])

    assert [bus.lease("bots:owner:bot-01"), bus.lease("bots:owner:bot-02")] == [_OURS, _OURS]


def test_a_fresh_start_does_not_wait() -> None:
    time = _Time()

    _claim(_bus(time), time, ["bot-01"])

    assert time.now == 0.0


def test_a_live_holder_refuses_the_start_naming_the_bot_both_holders() -> None:
    time = _Time()
    bus = _bus(time)
    _held_by_a_live_process(bus, time, "bot-02")

    message = _refusal(bus, time, ["bot-01", "bot-02"])

    assert "'bot-02'" in message
    assert "mode=real-money config=/app/exec/live.toml host=box-b" in message
    assert "This process: mode=paper config=/app/bots/config.toml host=box-a" in message
    assert "'bot-01'" not in message


def test_a_refused_start_releases_the_leases_it_already_claimed() -> None:
    time = _Time()
    bus = _bus(time)
    _held_by_a_live_process(bus, time, "bot-02")

    _refusal(bus, time, ["bot-01", "bot-02"])

    assert bus.lease("bots:owner:bot-01") is None
    assert bus.lease("bots:owner:bot-02") == _THEIRS


def test_a_live_holder_is_refused_only_after_a_full_ttl_and_one_heartbeat() -> None:
    # Any shorter and a crashed life's lease, renewed up to the moment it died, could still be
    # standing: the restart would be refused for a process that no longer exists.
    time = _Time()
    bus = _bus(time)
    _held_by_a_live_process(bus, time, "bot-01")

    _refusal(bus, time, ["bot-01"])

    assert time.now >= OWNER_TTL_SECONDS + STATUS_HEARTBEAT_SECONDS
    # And promptly then: the first retry past the window refuses, never a later one.
    assert time.now <= OWNER_TTL_SECONDS + STATUS_HEARTBEAT_SECONDS + OWNER_RETRY_SECONDS


def test_a_crashed_lives_lease_of_the_same_config_expires_and_is_claimed() -> None:
    # The same mode, config and host, but the prior life's token: it stopped renewing at death.
    time = _Time()
    bus = _bus(time)
    prior_life = owner_value("paper", "/app/bots/config.toml", "box-a", "c" * 32, -60.0)
    asyncio.run(bus.hold("bots:owner:bot-01", prior_life, _TTL_MS))

    _claim(bus, time, ["bot-01"])

    assert bus.lease("bots:owner:bot-01") == _OURS


def test_a_crash_restart_waits_no_longer_than_a_ttl_and_one_heartbeat() -> None:
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-01", _THEIRS, _TTL_MS))

    _claim(bus, time, ["bot-01"])

    assert OWNER_TTL_SECONDS <= time.now <= OWNER_TTL_SECONDS + STATUS_HEARTBEAT_SECONDS


def test_waiting_on_one_bot_keeps_the_leases_already_claimed_renewed() -> None:
    # bot-02's stale lease takes a whole TTL to expire: bot-01's must not expire meanwhile.
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-02", _THEIRS, _TTL_MS))

    _claim(bus, time, ["bot-01", "bot-02"])

    assert bus.lease_ttl("bots:owner:bot-01") == pytest.approx(OWNER_TTL_SECONDS, abs=1.0)


def test_an_unreachable_redis_is_retried_and_ledgered_until_the_claim_succeeds() -> None:
    time = _Time()
    bus = _bus(time)
    bus.fail_hold = 3

    _claim(bus, time, ["bot-01"])

    assert bus.lease("bots:owner:bot-01") == _OURS
    assert error_ledger.counts() == {"bots.redis": 3}


def test_a_long_redis_outage_never_counts_against_the_wait() -> None:
    # Ownership is unverified while Redis is down: the start waits for it, it is never refused.
    time = _Time()
    bus = _bus(time)
    bus.fail_hold = 100

    _claim(bus, time, ["bot-01"])

    assert bus.lease("bots:owner:bot-01") == _OURS


def test_a_redis_outage_mid_contest_restarts_the_wait() -> None:
    # Contested at 0 s, then Redis down 1-30 s: the holder is watched a full window again after
    # the outage, never refused on the first round back.
    time = _Time()
    bus = _bus(time)
    key = "bots:owner:bot-01"
    dropped: list[float] = []

    async def holder_renews_and_redis_drops_once() -> None:
        bus.leases[key] = (_THEIRS, time.now + OWNER_TTL_SECONDS)
        if time.now > 0 and not dropped:
            dropped.append(time.now)
            bus.fail_hold = 30

    asyncio.run(holder_renews_and_redis_drops_once())
    time.ticks.append(holder_renews_and_redis_drops_once)

    _refusal(bus, time, ["bot-01"])

    assert dropped == [1.0]
    assert time.now >= 31.0 + OWNER_TTL_SECONDS + STATUS_HEARTBEAT_SECONDS


class _HangsOnceBus(FakeBus):
    """A Redis that does not answer the first call of `method`, then recovers."""

    def __init__(self, method: str) -> None:
        super().__init__()
        self.hang = {method}

    async def hold(self, key: str, value: str, ttl_ms: int) -> bool:
        await self._maybe_hang("hold")
        return await super().hold(key, value, ttl_ms)

    async def get(self, key: str) -> str | None:
        await self._maybe_hang("get")
        return await super().get(key)

    async def _maybe_hang(self, method: str) -> None:
        if method in self.hang:
            self.hang.discard(method)
            await asyncio.Event().wait()


# Real seconds: `asyncio.wait_for` bounds a round on the loop's own clock, so the round bound
# (one heartbeat, TTL / 3) is kept short enough for a test.
_SHORT_TTL = 0.3


def test_a_hung_round_is_ledgered_within_one_heartbeat_and_retried() -> None:
    time = _Time()
    bus = _HangsOnceBus("hold")
    bus.now = time.clock

    asyncio.run(
        claim_all(
            connect_to(bus),
            ["bot-01"],
            _OURS,
            ttl_seconds=_SHORT_TTL,
            clock=time.clock,
            sleep=time.sleep,
        )
    )

    assert bus.lease("bots:owner:bot-01") == _OURS
    assert error_ledger.counts() == {"bots.redis": 1}


def test_a_hung_holder_read_at_the_refusal_is_ledgered_and_the_wait_restarts() -> None:
    time = _Time()
    bus = _HangsOnceBus("get")
    bus.now = time.clock
    _held_by_a_live_process(bus, time, "bot-01")

    with pytest.raises(BotIdCollision):
        asyncio.run(
            claim_all(
                connect_to(bus),
                ["bot-01"],
                _OURS,
                ttl_seconds=_SHORT_TTL,
                clock=time.clock,
                sleep=time.sleep,
            )
        )

    assert error_ledger.counts() == {"bots.redis": 1}
    # Contested twice for a full window: the timed-out read restarted it.
    assert time.now >= 2 * (_SHORT_TTL + STATUS_HEARTBEAT_SECONDS)


def test_the_renewal_before_the_node_runs_renews_every_lease() -> None:
    time = _Time()
    bus = _bus(time)
    _claim(bus, time, ["bot-01", "bot-02"])
    time.now += 10.0

    asyncio.run(reconfirm(connect_to(bus), ["bot-01", "bot-02"], _OURS))

    assert bus.lease_ttl("bots:owner:bot-01") == pytest.approx(OWNER_TTL_SECONDS)
    assert bus.lease_ttl("bots:owner:bot-02") == pytest.approx(OWNER_TTL_SECONDS)


def test_the_renewal_reclaims_a_lease_that_lapsed_free_while_the_node_was_built() -> None:
    time = _Time()
    bus = _bus(time)
    _claim(bus, time, ["bot-01"])
    time.now += OWNER_TTL_SECONDS + 1.0

    asyncio.run(reconfirm(connect_to(bus), ["bot-01"], _OURS))

    assert bus.lease("bots:owner:bot-01") == _OURS
    assert error_ledger.counts() == {}


def test_a_lease_taken_while_the_node_was_built_refuses_at_once_and_releases_ours() -> None:
    time = _Time()
    bus = _bus(time)
    _claim(bus, time, ["bot-01", "bot-02"])
    time.now += OWNER_TTL_SECONDS + 1.0
    asyncio.run(bus.hold("bots:owner:bot-02", _THEIRS, _TTL_MS))

    with pytest.raises(BotIdCollision, match="bot-02"):
        asyncio.run(reconfirm(connect_to(bus), ["bot-01", "bot-02"], _OURS))

    assert bus.lease("bots:owner:bot-01") is None
    assert bus.lease("bots:owner:bot-02") == _THEIRS


def test_a_failed_renewal_before_the_node_runs_is_ledgered_and_the_start_goes_on() -> None:
    time = _Time()
    bus = _bus(time)
    _claim(bus, time, ["bot-01"])
    bus.fail_hold = 1

    asyncio.run(reconfirm(connect_to(bus), ["bot-01"], _OURS))

    assert error_ledger.counts() == {"bots.redis": 1}


class _FreedAtTheDeadlineBus(FakeBus):
    """The holder lets go between the last contested round and the refusal's read of it."""

    def __init__(self) -> None:
        super().__init__()
        self.holder_gone = False

    async def get(self, key: str) -> str | None:
        if key.startswith("bots:owner:"):
            self.leases.pop(key, None)
            self.holder_gone = True
        return await super().get(key)


def test_a_holder_gone_by_the_refusal_is_claimed_not_refused() -> None:
    time = _Time()
    bus = _FreedAtTheDeadlineBus()
    bus.now = time.clock

    async def holder_renews_until_gone() -> None:
        if not bus.holder_gone:
            await bus.hold("bots:owner:bot-01", _THEIRS, _TTL_MS)

    asyncio.run(holder_renews_until_gone())
    time.ticks.append(holder_renews_until_gone)

    _claim(bus, time, ["bot-01"])

    assert bus.lease("bots:owner:bot-01") == _OURS


def test_release_deletes_only_this_processes_lease() -> None:
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-01", _OURS, _TTL_MS))
    asyncio.run(bus.hold("bots:owner:bot-02", _THEIRS, _TTL_MS))

    asyncio.run(release_all(connect_to(bus), ["bot-01", "bot-02"], _OURS))

    assert [bus.lease("bots:owner:bot-01"), bus.lease("bots:owner:bot-02")] == [None, _THEIRS]


def test_a_failed_release_is_ledgered_not_raised() -> None:
    def broken_connect() -> object:
        raise ConnectionError("redis down")

    asyncio.run(release_all(broken_connect, ["bot-01"], _OURS))  # type: ignore[arg-type]

    assert error_ledger.counts() == {"bots.ownership": 1}


def test_an_unrecognised_holder_is_shown_raw() -> None:
    assert describe_owner("legacy-holder") == "unrecognised holder 'legacy-holder'"


def test_the_owner_value_is_unique_per_process_life() -> None:
    first = owner_value("paper", "/app/bots/config.toml", "box-a", "a" * 32, 0.0)
    second = owner_value("paper", "/app/bots/config.toml", "box-a", "d" * 32, 0.0)

    assert json.loads(first)["token"] != json.loads(second)["token"]


def test_the_heartbeat_tick_renews_the_lease(store: SqliteFillsStore) -> None:
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-01", _OURS, _TTL_MS))
    supervisor = _supervisor(bus, time, store)
    time.now = 10.0

    asyncio.run(supervisor.heartbeat_tick(bus))

    assert bus.lease_ttl("bots:owner:bot-01") == pytest.approx(OWNER_TTL_SECONDS)


def test_the_heartbeat_tick_reclaims_a_lease_that_expired_free(store: SqliteFillsStore) -> None:
    # A Redis blip longer than the TTL, nobody else claimed: taken back silently, not a takeover.
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-01", _OURS, _TTL_MS))
    supervisor = _supervisor(bus, time, store)
    time.now = 60.0

    asyncio.run(supervisor.heartbeat_tick(bus))

    assert bus.lease("bots:owner:bot-01") == _OURS
    assert error_ledger.counts() == {}


def test_a_runtime_takeover_is_ledgered_once_and_never_overwritten(
    store: SqliteFillsStore,
) -> None:
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-01", _THEIRS, _TTL_MS))
    supervisor = _supervisor(bus, time, store)

    for _ in range(3):
        asyncio.run(supervisor.heartbeat_tick(bus))

    assert bus.lease("bots:owner:bot-01") == _THEIRS
    assert error_ledger.counts() == {"bots.ownership": 1}
    assert _THEIRS in error_ledger.last_details()["bots.ownership"]


def test_a_runtime_takeover_still_publishes_the_status(store: SqliteFillsStore) -> None:
    # Known limit: a lost lease is reported, never acted on -- the bot keeps running and visible.
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-01", _THEIRS, _TTL_MS))
    supervisor = _supervisor(bus, time, store)

    asyncio.run(supervisor.heartbeat_tick(bus))

    assert [channel for channel, _ in bus.published] == ["bots:status"]


def test_a_lease_regained_after_a_takeover_is_ledgered(store: SqliteFillsStore) -> None:
    time = _Time()
    bus = _bus(time)
    asyncio.run(bus.hold("bots:owner:bot-01", _THEIRS, _TTL_MS))
    supervisor = _supervisor(bus, time, store)
    asyncio.run(supervisor.heartbeat_tick(bus))
    time.now = 60.0  # the other process stopped renewing

    asyncio.run(supervisor.heartbeat_tick(bus))

    assert bus.lease("bots:owner:bot-01") == _OURS
    assert error_ledger.counts() == {"bots.ownership": 2}
    assert "regained" in error_ledger.last_details()["bots.ownership"]


def test_a_failed_hold_raises_into_the_reconnect(store: SqliteFillsStore) -> None:
    time = _Time()
    bus = _bus(time)
    bus.fail_hold = 1
    supervisor = _supervisor(bus, time, store)

    with pytest.raises(ConnectionError, match="hold failed"):
        asyncio.run(supervisor.heartbeat_tick(bus))


class _ExpiresBetweenHoldAndGetBus(FakeBus):
    """`hold` sees another holder, whose lease has expired by the following `get`."""

    async def hold(self, key: str, value: str, ttl_ms: int) -> bool:
        return False

    async def get(self, key: str) -> str | None:
        return None


def test_a_lease_freed_between_hold_and_get_is_no_takeover(store: SqliteFillsStore) -> None:
    time = _Time()
    bus = _ExpiresBetweenHoldAndGetBus()
    supervisor = _supervisor(bus, time, store)

    asyncio.run(supervisor.heartbeat_tick(bus))

    assert error_ledger.counts() == {}


def test_a_sigterm_during_the_claim_wait_releases_our_leases_and_exits() -> None:
    # Python is PID 1 in the container: the default SIGTERM action never reaches it, so the
    # claim must handle the signal itself or `docker stop` waits for SIGKILL.
    bus = FakeBus()
    asyncio.run(bus.hold("bots:owner:bot-01", _THEIRS, _TTL_MS))
    loop = asyncio.new_event_loop()
    try:
        loop.call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
        with pytest.raises(SystemExit) as stopped:
            bots_main._claim(loop, connect_to(bus), ["bot-01", "bot-02"], _OURS)
        # The handler is gone again, so the node can install its own.
        assert not loop.remove_signal_handler(signal.SIGTERM)
    finally:
        loop.close()

    assert stopped.value.code == 128 + signal.SIGTERM
    assert bus.lease("bots:owner:bot-02") is None
    assert bus.lease("bots:owner:bot-01") == _THEIRS
