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
Tests for Story 6.1's instrument control: config.toml's [[instruments]] list is the sole
subscribe source, and start/unpin/stop/pin_top_liquid are the only ways it changes.
Every instrument in `instruments` is pinned by definition -- there is no more
"collected but not pinned" middle state (see collector.py's module docstring).
"""

import asyncio
import contextlib
import dataclasses
import json
from pathlib import Path

import pytest

import dydx_collector.collector as collector_module
from dydx_collector.collector import _MAX_COLLECTED_INSTRUMENTS
from dydx_collector.collector import _STATUS_CHANNEL
from dydx_collector.collector import Collector
from dydx_collector.collector import _prune_candidates
from dydx_collector.config import CollectorConfig
from dydx_collector.config import InstrumentEntry
from dydx_collector.config import load_config
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


def _make_config(
    catalog_path: Path, instruments: tuple[InstrumentEntry, ...] = ()
) -> CollectorConfig:
    return CollectorConfig(
        network=DydxNetwork.TESTNET,
        catalog_path=str(catalog_path),
        flush_interval_seconds=60,
        snapshot_interval_seconds=1.0,
        config_reload_seconds=60,
        open_interest_poll_seconds=60,
        non_config_retain_hours=24.0,
        liquidity_min_oi_usd=100_000.0,
        liquidity_check_seconds=60,
        instruments=instruments,
        exclude=frozenset(),
    )


class _FakeClient:
    """Records subscribe/unsubscribe calls without touching the network."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def subscribe_trades(self, iid: str) -> None:
        self.calls.append(f"subscribe_trades:{iid}")

    async def subscribe_orderbook(self, iid: str) -> None:
        self.calls.append(f"subscribe_orderbook:{iid}")

    async def unsubscribe_trades(self, iid: str) -> None:
        self.calls.append(f"unsubscribe_trades:{iid}")

    async def unsubscribe_orderbook(self, iid: str) -> None:
        self.calls.append(f"unsubscribe_orderbook:{iid}")


def _collector(tmp_path: Path, instruments: tuple[InstrumentEntry, ...] = ()) -> Collector:
    collector = Collector(_make_config(tmp_path / "catalog", instruments))
    collector._client = _FakeClient()  # type: ignore[assignment]
    return collector


class _FakeRedis:
    """Records publish() calls without a real Redis connection."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> None:
        self.published.append((channel, message))


def _use_tmp_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(collector_module, "CONFIG_PATH", config_path)
    return config_path


@pytest.mark.asyncio
async def test_start_appends_pinned_and_subscribes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path)

    await collector._handle_control_message("start", "SOL-USD-PERP.DYDX")

    ids = {e.id for e in collector._config.instruments}
    assert ids == {"SOL-USD-PERP.DYDX"}
    assert collector._config.instruments[0].pinned is True
    assert load_config(config_path).instruments[0].pinned is True
    assert "subscribe_trades:SOL-USD-PERP.DYDX" in collector._client.calls
    assert "subscribe_orderbook:SOL-USD-PERP.DYDX" in collector._client.calls


@pytest.mark.asyncio
async def test_start_clears_id_from_exclude(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-:start-ing a previously-unpinned (excluded) id un-excludes it."""
    _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path)
    collector._config = dataclasses.replace(
        collector._config, exclude=frozenset({"SOL-USD-PERP.DYDX"})
    )

    await collector._handle_control_message("start", "SOL-USD-PERP.DYDX")

    assert "SOL-USD-PERP.DYDX" not in collector._config.exclude


@pytest.mark.asyncio
async def test_unpin_removes_entry_and_excludes_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path, (InstrumentEntry(id="BTC-USD-PERP.DYDX", pinned=True),))

    await collector._handle_control_message("unpin", "BTC-USD-PERP.DYDX")

    assert collector._config.instruments == ()
    assert collector._config.exclude == frozenset({"BTC-USD-PERP.DYDX"})
    assert load_config(config_path).exclude == frozenset({"BTC-USD-PERP.DYDX"})
    assert "unsubscribe_trades:BTC-USD-PERP.DYDX" in collector._client.calls


@pytest.mark.asyncio
async def test_unpin_publishes_removed_tombstone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bot_tui must drop the row immediately, not wait out its staleness timeout."""
    _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path, (InstrumentEntry(id="BTC-USD-PERP.DYDX", pinned=True),))
    collector._redis = _FakeRedis()  # type: ignore[assignment]

    await collector._handle_control_message("unpin", "BTC-USD-PERP.DYDX")

    tombstones = [
        json.loads(msg) for channel, msg in collector._redis.published if channel == _STATUS_CHANNEL
    ]
    assert {"id": "BTC-USD-PERP.DYDX", "removed": True} in tombstones


@pytest.mark.asyncio
async def test_unpin_unknown_id_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path)

    await collector._handle_control_message("unpin", "UNKNOWN-PERP.DYDX")

    assert not config_path.exists()


@pytest.mark.asyncio
async def test_start_already_collected_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path, (InstrumentEntry(id="BTC-USD-PERP.DYDX"),))

    await collector._handle_control_message("start", "BTC-USD-PERP.DYDX")

    assert len(collector._config.instruments) == 1
    assert collector._client.calls == []


@pytest.mark.asyncio
async def test_start_rejected_at_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _use_tmp_config_path(tmp_path, monkeypatch)
    at_cap = tuple(
        InstrumentEntry(id=f"COIN{i}-USD-PERP.DYDX") for i in range(_MAX_COLLECTED_INSTRUMENTS)
    )
    collector = _collector(tmp_path, at_cap)

    await collector._handle_control_message("start", "OVERFLOW-USD-PERP.DYDX")

    assert len(collector._config.instruments) == _MAX_COLLECTED_INSTRUMENTS
    assert collector._client.calls == []
    assert not config_path.exists()  # rejected before any persist


@pytest.mark.asyncio
async def test_start_at_exactly_one_below_cap_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_tmp_config_path(tmp_path, monkeypatch)
    below_cap = tuple(
        InstrumentEntry(id=f"COIN{i}-USD-PERP.DYDX") for i in range(_MAX_COLLECTED_INSTRUMENTS - 1)
    )
    collector = _collector(tmp_path, below_cap)

    await collector._handle_control_message("start", "NEW-USD-PERP.DYDX")

    assert len(collector._config.instruments) == _MAX_COLLECTED_INSTRUMENTS


@pytest.mark.asyncio
async def test_stop_removes_pinned_entry_and_unsubscribes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Stop removes an entry regardless of its pinned flag (AC #4) -- and, unlike unpin,
    does not add it to `exclude` (that's the whole distinction between the two).
    """
    _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path, (InstrumentEntry(id="BTC-USD-PERP.DYDX", pinned=True),))

    await collector._handle_control_message("stop", "BTC-USD-PERP.DYDX")

    assert collector._config.instruments == ()
    assert collector._config.exclude == frozenset()
    assert "unsubscribe_trades:BTC-USD-PERP.DYDX" in collector._client.calls


@pytest.mark.asyncio
async def test_stop_unknown_id_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path)

    await collector._handle_control_message("stop", "NOT-COLLECTED-PERP.DYDX")

    assert not config_path.exists()


@pytest.mark.asyncio
async def test_unknown_action_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = _use_tmp_config_path(tmp_path, monkeypatch)
    collector = _collector(tmp_path, (InstrumentEntry(id="BTC-USD-PERP.DYDX"),))

    await collector._handle_control_message("frobnicate", "BTC-USD-PERP.DYDX")

    assert len(collector._config.instruments) == 1
    assert not config_path.exists()


def _markets_json(volumes: dict[str, float]) -> dict:
    return {
        "markets": {
            ticker: {"ticker": ticker, "volume24H": vol} for ticker, vol in volumes.items()
        }
    }


@pytest.mark.asyncio
async def test_pin_top_liquid_fills_empty_slots_pinned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_tmp_config_path(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector_module,
        "_fetch_markets_json",
        lambda _network: _markets_json({"AAA": 500_000.0, "BBB": 400_000.0, "LOW": 50.0}),
    )

    existing = InstrumentEntry(id="PIN-USD-PERP.DYDX", pinned=True)
    collector = _collector(tmp_path, (existing,))

    await collector._handle_control_message("pin_top_liquid", None)

    ids = {e.id for e in collector._config.instruments}
    assert "PIN-USD-PERP.DYDX" in ids  # existing entry untouched
    assert "AAA-PERP.DYDX" in ids
    assert "BBB-PERP.DYDX" in ids
    assert "LOW-PERP.DYDX" not in ids  # below volume threshold
    new_entries = [e for e in collector._config.instruments if e.id != "PIN-USD-PERP.DYDX"]
    assert all(e.pinned for e in new_entries)
    existing_entry = next(e for e in collector._config.instruments if e.id == "PIN-USD-PERP.DYDX")
    assert existing_entry is existing  # byte-identical -- never rebuilt


@pytest.mark.asyncio
async def test_pin_top_liquid_never_re_adds_an_unpinned_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression: an explicitly-unpinned id must stay out even if it's back in the
    top-by-volume set -- otherwise pin_top_liquid silently overrides an unpin, which
    defeats the point of unpin remembering it (re-adding is always :start <ID>).
    """
    _use_tmp_config_path(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector_module,
        "_fetch_markets_json",
        lambda _network: _markets_json({"AAA": 500_000.0}),
    )
    collector = _collector(tmp_path)
    collector._config = dataclasses.replace(
        collector._config, exclude=frozenset({"AAA-PERP.DYDX"})
    )

    await collector._handle_control_message("pin_top_liquid", None)

    assert collector._config.instruments == ()
    assert collector._config.exclude == frozenset({"AAA-PERP.DYDX"})  # left untouched


@pytest.mark.asyncio
async def test_pin_top_liquid_never_removes_or_exceeds_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_tmp_config_path(tmp_path, monkeypatch)
    volumes = {f"COIN{i}": float(1_000_000 - i) for i in range(40)}
    monkeypatch.setattr(
        collector_module, "_fetch_markets_json", lambda _network: _markets_json(volumes)
    )

    existing = tuple(InstrumentEntry(id=f"PIN{i}-PERP.DYDX", pinned=True) for i in range(5))
    collector = _collector(tmp_path, existing)

    await collector._handle_control_message("pin_top_liquid", None)

    ids = {e.id for e in collector._config.instruments}
    assert {e.id for e in existing} <= ids  # nothing removed
    assert len(collector._config.instruments) <= _MAX_COLLECTED_INSTRUMENTS


@pytest.mark.asyncio
async def test_pin_top_liquid_excludes_already_collected_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A currently-collected id must not be duplicated even if it's also top-by-volume."""
    _use_tmp_config_path(tmp_path, monkeypatch)
    monkeypatch.setattr(
        collector_module,
        "_fetch_markets_json",
        lambda _network: _markets_json({"AAA": 500_000.0}),
    )
    collector = _collector(tmp_path, (InstrumentEntry(id="AAA-PERP.DYDX", pinned=True),))

    await collector._handle_control_message("pin_top_liquid", None)

    ids = [e.id for e in collector._config.instruments]
    assert ids.count("AAA-PERP.DYDX") == 1


@pytest.mark.asyncio
async def test_pin_top_liquid_at_cap_is_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_tmp_config_path(tmp_path, monkeypatch)
    fetch_calls: list[object] = []
    monkeypatch.setattr(
        collector_module,
        "_fetch_markets_json",
        lambda network: fetch_calls.append(network) or _markets_json({}),
    )
    at_cap = tuple(
        InstrumentEntry(id=f"COIN{i}-PERP.DYDX", pinned=True) for i in range(_MAX_COLLECTED_INSTRUMENTS)
    )
    collector = _collector(tmp_path, at_cap)

    await collector._handle_control_message("pin_top_liquid", None)

    assert fetch_calls == []  # no network call needed -- there's nowhere to put a result


def test_prune_candidates_includes_dropped_instrument() -> None:
    """
    Regression guard for AC #8: an instrument removed from `instruments` (via stop or
    unpin) must still be a prune candidate as long as it's a known market -- catalog
    data for abandoned instruments must not be orphaned.
    """
    instruments = (InstrumentEntry(id="PINNED-PERP.DYDX", pinned=True),)
    known_markets = {"PINNED-PERP.DYDX", "STOPPED-PERP.DYDX"}

    candidates = _prune_candidates(instruments, known_markets)

    assert "STOPPED-PERP.DYDX" in candidates
    assert "PINNED-PERP.DYDX" not in candidates


def test_prune_candidates_includes_non_pinned_collected() -> None:
    instruments = (
        InstrumentEntry(id="PINNED-PERP.DYDX", pinned=True),
        InstrumentEntry(id="NON-PINNED-PERP.DYDX", pinned=False),
    )

    candidates = _prune_candidates(instruments, known_markets=set())

    assert candidates == {"NON-PINNED-PERP.DYDX"}


@pytest.mark.asyncio
async def test_publish_status_broadcasts_hand_edited_exclude_entries(
    tmp_path: Path,
) -> None:
    """
    bot_tui's "unpinned" section must show every excluded id, whatever its origin --
    not just ones that went through the TUI's unpin action. A config.exclude entry
    that came from hand-editing config.toml (never touched by any control action)
    must appear in the same unpinned_ids broadcast.
    """
    config = _make_config(tmp_path / "catalog")
    config = dataclasses.replace(config, exclude=frozenset({"HANDEDITED-PERP.DYDX"}))
    collector = Collector(config)
    collector._redis = _FakeRedis()  # type: ignore[assignment]

    await collector._publish_status()

    payloads = [json.loads(msg) for _channel, msg in collector._redis.published]
    assert {"unpinned_ids": ["HANDEDITED-PERP.DYDX"]} in payloads


@pytest.mark.asyncio
async def test_status_loop_publishes_immediately_without_waiting_for_first_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Regression: a sleep-first loop left the bot_tui Collector page showing "waiting for
    collector:status" for up to liquidity_check_seconds (30 min default) after every
    collector restart -- a real production bug hit before this fix. _status_loop must
    publish on its first iteration, before its first sleep, not after.
    """
    monkeypatch.setattr(
        collector_module,
        "_fetch_markets_json",
        lambda _network: _markets_json({"BTC": 500_000.0}),
    )
    config = _make_config(tmp_path / "catalog", (InstrumentEntry(id="BTC-PERP.DYDX"),))
    # Long enough that the test's own timeout below would fail first if the loop were
    # still sleeping before its first publish -- proves the publish isn't just "fast",
    # it happens strictly before this sleep could ever complete.
    config = dataclasses.replace(config, liquidity_check_seconds=999_999)
    collector = Collector(config)
    collector._redis = _FakeRedis()  # type: ignore[assignment]

    loop_task = asyncio.create_task(collector._status_loop())
    await asyncio.sleep(0.05)
    # _stop.set() alone wouldn't end this loop -- it's parked in a 999_999s sleep by
    # then, and that check only runs between iterations. Cancel it directly instead.
    loop_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await loop_task

    assert collector._redis.published
    channel, _message = collector._redis.published[0]
    assert channel == "collector:status"
