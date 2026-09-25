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

from observability import error_ledger

from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
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
