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
`AlertStore` over `alerts.toml` (moved from `data_api/tests/test_alerts.py`, Story 24.3), and Story
33.8's appended keys: a pre-33.8 file loading as `price_cross` and round-tripping, a condition table
round trip, a bad stored condition refused at load, `update` and `mark_invalid`.
"""

import shutil
from pathlib import Path

import pytest
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
        "\n"
        "[alerts.condition]\n"
        'kind = "price_cross"\n'
        "level = 65000.5\n"
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


_LEGACY = Path(__file__).parent / "fixtures" / "alerts_pre_33_8.toml"


def test_a_pre_33_8_file_loads_every_alert_as_a_price_cross(tmp_path: Path) -> None:
    path = tmp_path / "alerts.toml"
    shutil.copy(_LEGACY, path)
    alerts = AlertStore(path).list()
    assert [a.id for a in alerts] == ["legacy-close", "legacy-bar", "legacy-once"]
    assert [a.frequency for a in alerts] == ["once_per_bar_close", "once_per_bar", "only_once"]
    assert [a.condition for a in alerts] == [
        {"kind": "price_cross", "level": 100.0},
        {"kind": "price_cross", "level": 65000.5},
        {"kind": "price_cross", "level": 150.25},
    ]
    assert [a.level for a in alerts] == [100.0, 65000.5, 150.25]
    assert alerts[1].expires_at_ns == 1_900_000_000_000_000_000
    assert (alerts[2].triggered, alerts[2].last_fired_ns) == (True, 1_800_000_100_000_000_000)
    assert [a.invalid_reason for a in alerts] == [None, None, None]


def test_a_pre_33_8_file_round_trips_with_its_condition_written(tmp_path: Path) -> None:
    path = tmp_path / "alerts.toml"
    shutil.copy(_LEGACY, path)
    store = AlertStore(path)
    before = store.list()
    store.add(_alert())  # any save rewrites the whole file
    assert "[alerts.condition]" in path.read_text()
    after = AlertStore(path).list()[:3]
    assert [vars(a) for a in after] == [vars(a) for a in before]


def test_a_condition_table_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "alerts.toml"
    store = AlertStore(path)
    condition = {
        "kind": "indicator",
        "name": "RelativeStrengthIndex",
        "params": {"period": 14},
        "output": "value",
        "op": ">",
        "value": 70,
    }
    liquidation = {"kind": "liquidation_notional", "notional": 1e5, "window_s": 300}
    store.add(_alert(level=None, condition=condition, bar_seconds=3600))
    store.add(_alert(level=None, condition=liquidation))
    reloaded = AlertStore(path).list()
    assert reloaded[0].condition == {**condition, "source": "close", "value": 70.0}
    assert reloaded[0].level is None
    assert reloaded[1].condition == {**liquidation, "notional": 100000.0}
    assert "level" not in path.read_text().split("[[alerts]]")[1]


@pytest.mark.parametrize(
    "condition_toml",
    [
        'kind = "channel_exit"\nupper = 90.0\nlower = 110.0\n',
        'kind = "teleport"\n',
        'kind = "price_above"\nlevel = 7.0\n',  # disagrees with the alert's level = 5.0
    ],
)
def test_a_bad_stored_condition_raises_at_load(tmp_path: Path, condition_toml: str) -> None:
    path = tmp_path / "alerts.toml"
    path.write_text(
        "[[alerts]]\n"
        'id = "bad"\n'
        'instrument_id = "BTCUSDT-LINEAR.BYBIT"\n'
        "level = 5.0\n"
        'frequency = "only_once"\n'
        "bar_seconds = 60\n"
        'template = ""\n'
        'webhook_url = ""\n'
        "created_ns = 1\n"
        "triggered = false\n"
        "[alerts.condition]\n" + condition_toml
    )
    with pytest.raises(ValueError, match=r"alerts\[0\] is not a valid alert"):
        AlertStore(path)


def test_update_persists_and_keeps_the_previous_alert_when_the_save_fails(tmp_path: Path) -> None:
    path = tmp_path / "a.toml"
    store = AlertStore(path)
    alert = _alert()
    store.add(alert)
    edited = Alert(
        **{**vars(alert), "level": None, "condition": {"kind": "funding_above", "rate": 0.001}}
    )
    assert store.update(alert.id, lambda _stored: edited) is edited
    assert AlertStore(path).list()[0].condition == {"kind": "funding_above", "rate": 0.001}
    assert store.update(_alert().id, lambda stored: stored) is None  # no such id
    path.unlink()
    path.mkdir()  # every later save raises IsADirectoryError
    with pytest.raises(OSError):
        store.update(alert.id, lambda _stored: alert)
    assert store.list()[0] is edited


def test_update_edits_the_stored_alert_and_an_edit_that_raises_changes_nothing(
    tmp_path: Path,
) -> None:
    store = AlertStore(tmp_path / "a.toml")
    alert = _alert()
    store.add(alert)
    store.record_fire(alert, _T0)  # a fire recorded after the caller last read the alert
    seen: list[Alert] = []

    def edit(stored: Alert) -> Alert:
        seen.append(stored)
        raise ValueError("refused")

    with pytest.raises(ValueError, match="refused"):
        store.update(alert.id, edit)
    assert seen == [alert]
    assert seen[0].triggered is True  # the edit is built from the stored, fired alert
    assert store.list() == [alert]


def test_a_fire_of_an_alert_an_edit_replaced_stamps_the_time_but_does_not_trigger_the_edit(
    tmp_path: Path,
) -> None:
    # The engine evaluated the alert object it read before the edit replaced it.
    path = tmp_path / "a.toml"
    store = AlertStore(path)
    replaced = _alert()
    store.add(replaced)
    condition = {"kind": "price_cross", "level": 90.0}
    edited = store.update(
        replaced.id, lambda stored: Alert(**{**vars(stored), "level": 90.0, "condition": condition})
    )
    assert edited is not None
    store.record_fire(replaced, _T0)
    store.mark_invalid(replaced, "the old condition's drawing is gone")
    stored = AlertStore(path).list()[0]
    assert (stored.level, stored.last_fired_ns, stored.triggered) == (90.0, _T0, False)
    assert stored.invalid_reason is None  # the edit re-validated its own condition


def test_failed_mark_invalid_persist_keeps_it_in_memory_and_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    path = tmp_path / "a.toml"
    store = AlertStore(path)
    alert = _alert()
    store.add(alert)
    store.mark_invalid(alert, "drawing d1 no longer exists")
    assert AlertStore(path).list()[0].invalid_reason == "drawing d1 no longer exists"
    path.unlink()
    path.mkdir()

    store.mark_invalid(alert, "again")

    assert alert.invalid_reason == "again"
    assert error_ledger.counts() == {"alerting.store.persist": 1}
    assert "was marked invalid" in error_ledger.last_details()["alerting.store.persist"]
    error_ledger.reset()
