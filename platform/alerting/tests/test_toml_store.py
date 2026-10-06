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
"""`AlertStore` over `alerts.toml` (moved from `data_api/tests/test_alerts.py`, Story 24.3)."""

from pathlib import Path

import pytest
from observability import error_ledger

from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.infrastructure import toml_store
from alerting.infrastructure.toml_store import AlertStore


_S = 1_000_000_000
_T0 = 1_800_000_000 * _S


def _alert(**kw: object) -> Alert:
    fields: dict[str, object] = {
        "instrument_id": "BTC-USD-PERP.DYDX",
        "level": 100.0,
        "frequency": "only_once",
        "bar_seconds": 60,
        "template": "{{ticker}} {{close}}",
        "webhook_url": "http://localhost:1/hook",
    }
    fields.update(kw)
    return new_alert(**fields)


def test_store_round_trip_keeps_optional_fields(tmp_path: Path) -> None:
    path = tmp_path / "alerts.toml"
    store = AlertStore(path)
    a, b = _alert(expires_at_ns=_T0 + _S), _alert()
    store.add(a)
    store.add(b)
    store.record_fire(a, _T0)
    reloaded = AlertStore(path).list()
    assert [x.id for x in reloaded] == [a.id, b.id]
    assert reloaded[0].expires_at_ns == _T0 + _S
    assert reloaded[0].triggered is True
    assert reloaded[1].expires_at_ns is None
    assert store.delete(a.id) is True
    assert store.delete(a.id) is False
    assert [x.id for x in AlertStore(path).list()] == [b.id]


def test_file_text_is_frozen(tmp_path: Path) -> None:
    # The `alerts.toml` key set and text (AD-D12): the bind-mounted file a deployed API reads back.
    path = tmp_path / "alerts.toml"
    store = AlertStore(path)
    alert = Alert(
        id="a1",
        instrument_id="BTC-USD-PERP.DYDX",
        level=65000.5,
        frequency="only_once",
        bar_seconds=60,
        template="{{ticker}}",
        webhook_url="",
        created_ns=_T0,
    )
    store.add(alert)
    store.record_fire(alert, _T0 + _S)
    assert path.read_text() == (
        "[[alerts]]\n"
        'id = "a1"\n'
        'instrument_id = "BTC-USD-PERP.DYDX"\n'
        "level = 65000.5\n"
        'frequency = "only_once"\n'
        "bar_seconds = 60\n"
        'template = "{{ticker}}"\n'
        'webhook_url = ""\n'
        "created_ns = 1800000000000000000\n"
        "triggered = true\n"
        "last_fired_ns = 1800000001000000000\n"
    )


def test_failed_persist_keeps_the_fire_in_memory_and_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    path = tmp_path / "a.toml"
    store = AlertStore(path)
    alert = _alert()
    store.add(alert)
    path.unlink()
    path.mkdir()  # makes every later save raise IsADirectoryError

    store.record_fire(alert, _T0)

    assert alert.triggered is True
    assert alert.last_fired_ns == _T0
    assert error_ledger.counts() == {"alerting.store.persist": 1}
    assert f"alert {alert.id}" in error_ledger.last_details()["alerting.store.persist"]
    error_ledger.reset()


def _failing_fsync(fd: int) -> None:
    raise OSError(28, "No space left on device")


def _saved_store(tmp_path: Path) -> tuple[AlertStore, Path, Alert]:
    path = tmp_path / "alerts.toml"
    store = AlertStore(path)
    alert = _alert()
    store.add(alert)
    return store, path, alert


def test_failed_add_keeps_file_bytes_and_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, path, kept = _saved_store(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(toml_store.os, "fsync", _failing_fsync)

    with pytest.raises(OSError, match="No space left"):
        store.add(_alert())

    assert path.read_bytes() == before
    assert not (tmp_path / ".alerts.toml.tmp").exists()
    assert [a.id for a in store.list()] == [kept.id]


def test_failed_delete_keeps_the_alert_in_memory_and_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, path, alert = _saved_store(tmp_path)
    before = path.read_bytes()
    monkeypatch.setattr(toml_store.os, "fsync", _failing_fsync)

    with pytest.raises(OSError, match="No space left"):
        store.delete(alert.id)

    assert path.read_bytes() == before
    assert not (tmp_path / ".alerts.toml.tmp").exists()
    assert [a.id for a in store.list()] == [alert.id]
    monkeypatch.undo()
    assert [a.id for a in AlertStore(path).list()] == [alert.id]


def test_failed_rename_leaves_no_temp_and_restores_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, path, kept = _saved_store(tmp_path)
    before = path.read_bytes()

    def _failing_replace(src: object, dst: object) -> None:
        raise OSError(16, "Device or resource busy")  # a single-file bind mount's answer

    monkeypatch.setattr(toml_store.os, "replace", _failing_replace)

    with pytest.raises(OSError, match="busy"):
        store.add(_alert())

    assert path.read_bytes() == before
    assert not (tmp_path / ".alerts.toml.tmp").exists()
    assert [a.id for a in store.list()] == [kept.id]


def test_successful_saves_leave_no_temp_file(tmp_path: Path) -> None:
    store, path, alert = _saved_store(tmp_path)
    store.record_fire(alert, _T0)
    store.delete(alert.id)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["alerts.toml"]
    assert AlertStore(path).list() == []


def test_failed_directory_fsync_after_rename_is_ledgered_not_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The rename already published the file, so memory must follow it, not the old list.
    error_ledger.reset()
    store, path, first = _saved_store(tmp_path)

    def _failing_fsync_dir(directory: Path) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(toml_store, "_fsync_dir", _failing_fsync_dir)
    second = _alert()
    store.add(second)

    assert [a.id for a in store.list()] == [first.id, second.id]
    assert [a.id for a in AlertStore(path).list()] == [first.id, second.id]
    assert error_ledger.counts() == {"alerting.store.fsync_dir": 1}
    error_ledger.reset()


def test_an_interrupt_after_the_rename_keeps_memory_with_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, path, _ = _saved_store(tmp_path)
    added = _alert()

    def _interrupted(directory: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(toml_store, "_fsync_dir", _interrupted)
    with pytest.raises(KeyboardInterrupt):
        store.add(added)

    monkeypatch.undo()
    assert added.id in [a.id for a in store.list()]
    assert [a.id for a in store.list()] == [a.id for a in AlertStore(path).list()]
