# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software distributed under the
#  License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND,
#  either express or implied.  See the License for the specific language governing permissions
#  and limitations under the License.
# -------------------------------------------------------------------------------------------------
"""The DW-266 memory canary: the cgroup read, the once-per-crossing policy, the wiring."""

import asyncio
from pathlib import Path

import pytest
from observability import error_ledger
from observability import notify

import capture.application.cgroup as cgroup
from capture.application import sites
from capture.application.capture_service import CaptureService
from capture.application.config import CoreConfig
from capture.infrastructure.parquet_writer import ParquetArchiveWriter


class _Notifier:
    """The injected Notifier: records every push (channel, title, body)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def notify(self, channel: str, title: str, body: str) -> None:
        self.sent.append((channel, title, body))


class _Client:
    """The never-connected duck type: the canary needs no feed (TEST-03: ours, not Nautilus)."""


def _cgroup(tmp_path: Path, current: str, limit: str) -> Path:
    """Write a cgroup v2 pair (kernel files end with a newline) and return its base."""
    (tmp_path / "memory.current").write_text(current)
    (tmp_path / "memory.max").write_text(limit)
    return tmp_path


def _service(tmp_path: Path, notifier: _Notifier) -> CaptureService:
    cfg = CoreConfig(environment="mainnet", catalog_path=str(tmp_path))
    return CaptureService(
        cfg,
        lambda _on_data, _ledger: _Client(),  # type: ignore[arg-type, return-value]
        venue="BYBIT",
        plan=(),
        archive=ParquetArchiveWriter(cfg.catalog_path),
        live_stream=None,
        notifier=notifier,
    )


def test_memory_usage_reads_the_container_pair(tmp_path: Path) -> None:
    base = _cgroup(tmp_path, "241251328\n", "379541504\n")
    assert cgroup.memory_usage(base) == (241251328, 379541504)


def test_memory_usage_is_none_without_a_limit_a_cgroup_or_numbers(tmp_path: Path) -> None:
    assert cgroup.memory_usage(_cgroup(tmp_path, "241251328\n", "max\n")) is None
    assert cgroup.memory_usage(tmp_path) is None
    assert cgroup.memory_usage(_cgroup(tmp_path, "not a number\n", "100\n")) is None
    assert cgroup.memory_usage(_cgroup(tmp_path, "100\n", "not a number\n")) is None
    assert cgroup.memory_usage(_cgroup(tmp_path, "100\n", "0\n")) is None


def test_the_canary_fires_once_per_crossing_and_rearms_below_the_rearm_level() -> None:
    canary = cgroup.MemoryCanary()
    assert canary.check((899, 1000)) is False  # below the fire level
    assert canary.check((900, 1000)) is True  # the crossing: 0.9 exactly
    assert canary.check((999, 1000)) is False  # still elevated: once per crossing
    assert canary.check((799, 1000)) is False  # below the re-arm level: re-arms
    assert canary.check((900, 1000)) is True  # a second crossing fires again


def test_the_canary_stays_armed_through_a_missing_cgroup() -> None:
    canary = cgroup.MemoryCanary()
    assert canary.check(None) is False
    assert canary.check((900, 1000)) is True
    assert canary.check(None) is False  # a missing canary does not re-arm
    assert canary.check((950, 1000)) is False  # still once per crossing
    assert canary.check((799, 1000)) is False
    assert canary.check((950, 1000)) is True


def test_the_service_ledgers_and_pushes_once_per_crossing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.setattr(cgroup, "CGROUP_BASE", _cgroup(tmp_path, "350000000\n", "379584512\n"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    notifier = _Notifier()
    c = _service(tmp_path, notifier)
    asyncio.run(c._check_memory_pressure())
    (tmp_path / "memory.current").write_text("375000000\n")
    asyncio.run(c._check_memory_pressure())  # still elevated: once per crossing
    (tmp_path / "memory.current").write_text("300000000\n")
    asyncio.run(c._check_memory_pressure())  # below the re-arm level
    (tmp_path / "memory.current").write_text("375000000\n")
    asyncio.run(c._check_memory_pressure())  # a second crossing fires again
    assert error_ledger.counts() == {sites.MEMORY_PRESSURE: 2}
    assert [(channel, title) for channel, title, _ in notifier.sent] == [
        (notify.OPERATOR, "collector memory pressure"),
        (notify.OPERATOR, "collector memory pressure"),
    ]
    assert "333 MiB of its 362 MiB mem_limit (92%)" in notifier.sent[0][2]


def test_the_service_does_nothing_without_a_cgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.setattr(cgroup, "CGROUP_BASE", tmp_path)  # no files at all
    notifier = _Notifier()
    c = _service(tmp_path, notifier)
    asyncio.run(c._check_memory_pressure())
    assert error_ledger.counts() == {}
    assert notifier.sent == []


def test_the_push_goes_to_telegram_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.setattr(cgroup, "CGROUP_BASE", _cgroup(tmp_path, "350000000\n", "379584512\n"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")
    notifier = _Notifier()
    c = _service(tmp_path, notifier)
    asyncio.run(c._check_memory_pressure())
    assert error_ledger.counts() == {sites.MEMORY_PRESSURE: 1}
    assert notifier.sent[0][0] == notify.TELEGRAM
    assert "BYBIT collector" in notifier.sent[0][2]
