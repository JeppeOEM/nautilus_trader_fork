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
`AlertEngine` and `AlertService` on hand-built forming bars (Story 24.3): real `AlertStore` over a
temp file, a recording `Deliverer`. The same scenarios driven through a real `LiveCandleBus` live
in `data_api/tests/test_alerts.py` (alerting may not import views).
"""

import threading
from pathlib import Path

import pytest

from alerting.application.engine import AlertEngine
from alerting.application.service import AlertService
from alerting.application.service import NoDeliveryChannel
from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.infrastructure.toml_store import AlertStore


_IID = "BTC-USD-PERP.DYDX"
_S = 1_000_000_000
_T0 = 1_800_000_000 * _S  # multiple of 60s


class _RecordingDeliverer:
    """A `Deliverer` by shape: records bodies instead of sending them."""

    def __init__(self, channels: tuple[str, ...] = ("webhook:http://h/x",)) -> None:
        self.named = channels
        self.sent: list[tuple[str, str]] = []

    def channels(self, alert: Alert) -> tuple[str, ...]:
        return self.named

    def deliver(self, alert: Alert, body: str) -> None:
        self.sent.append((alert.id, body))


def _alert(frequency: str = "only_once", **kw: object) -> Alert:
    fields: dict[str, object] = {
        "instrument_id": _IID,
        "level": 100.0,
        "frequency": frequency,
        "bar_seconds": 1,
        "template": "{{ticker}} {{close}}",
        "webhook_url": "http://localhost:1/hook",
    }
    fields.update(kw)
    return new_alert(**fields)


def _bar(ts_ns: int, close: float, bar_seconds: int = 1) -> dict:
    """Build the `{t, o, h, l, c, v}` shape `forming_bar` returns; its prices are always floats."""
    bucket_ms = ts_ns // (bar_seconds * _S) * bar_seconds * 1000
    price = float(close)
    return {"t": bucket_ms, "o": price, "h": price, "l": price, "c": price, "v": 1.0}


def _join_deliveries() -> None:
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)


def _engine(tmp_path: Path, *alerts: Alert) -> tuple[AlertEngine, AlertStore, _RecordingDeliverer]:
    store = AlertStore(tmp_path / "a.toml")
    for alert in alerts:
        store.add(alert)
    deliverer = _RecordingDeliverer()
    return AlertEngine(store, deliverer), store, deliverer


def test_on_bar_fires_on_the_bars_close_and_delivers_and_toasts(tmp_path: Path) -> None:
    engine, store, deliverer = _engine(tmp_path, _alert(template="hit {{close}}"))
    queue = engine.subscribe()
    engine.on_bar(_IID, 1, _bar(_T0, 99), _T0)
    engine.on_bar(_IID, 1, _bar(_T0 + _S, 101), _T0 + _S)
    _join_deliveries()
    alert = store.list()[0]
    assert deliverer.sent == [(alert.id, "hit 101.0")]
    assert queue.get_nowait() == {
        "channel": "alerts",
        "alert": {"id": alert.id, "message": "hit 101.0"},
    }
    assert alert.triggered is True
    assert alert.last_fired_ns == _T0 + _S


def test_on_bar_ignores_other_instruments_and_widths(tmp_path: Path) -> None:
    engine, store, deliverer = _engine(tmp_path, _alert())
    engine.on_bar("ETH-USD-PERP.DYDX", 1, _bar(_T0, 99), _T0)
    engine.on_bar("ETH-USD-PERP.DYDX", 1, _bar(_T0 + _S, 101), _T0 + _S)
    engine.on_bar(_IID, 60, _bar(_T0, 99, 60), _T0)
    engine.on_bar(_IID, 60, _bar(_T0 + _S, 101, 60), _T0 + _S)
    _join_deliveries()
    assert deliverer.sent == []
    assert store.list()[0].triggered is False


def test_republished_unchanged_close_never_fires(tmp_path: Path) -> None:
    # An untraded second mid-bucket republishes the forming bar with the same close.
    engine, _store, deliverer = _engine(tmp_path, _alert("once_per_bar", bar_seconds=60))
    for i in range(3):
        engine.on_bar(_IID, 60, _bar(_T0 + i * _S, 101, 60), _T0 + i * _S)
    _join_deliveries()
    assert deliverer.sent == []


def test_watched_bars_names_only_active_alerts(tmp_path: Path) -> None:
    triggered = _alert(bar_seconds=300)
    triggered.triggered = True
    expired = _alert(bar_seconds=900, expires_at_ns=1)
    engine, _store, _deliverer = _engine(tmp_path, _alert(bar_seconds=60), triggered, expired)
    assert engine.watched_bars() == frozenset({(_IID, 60)})


def test_unsubscribed_queue_gets_no_toast(tmp_path: Path) -> None:
    engine, _store, _deliverer = _engine(tmp_path, _alert())
    queue = engine.subscribe()
    engine.unsubscribe(queue)
    engine.on_bar(_IID, 1, _bar(_T0, 99), _T0)
    engine.on_bar(_IID, 1, _bar(_T0 + _S, 101), _T0 + _S)
    _join_deliveries()
    assert queue.empty()


def test_service_refuses_an_alert_with_no_channel(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer(channels=())
    service = AlertService(store, deliverer, AlertEngine(store, deliverer))
    fields = {
        k: v for k, v in vars(_alert(webhook_url="")).items() if k not in ("id", "created_ns")
    }
    with pytest.raises(NoDeliveryChannel, match="no delivery channel"):
        service.create(**fields)
    assert service.list() == []


def test_service_create_list_delete_forgets_run_state(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer()
    engine = AlertEngine(store, deliverer)
    service = AlertService(store, deliverer, engine)
    fields = {k: v for k, v in vars(_alert()).items() if k not in ("id", "created_ns")}
    alert = service.create(**fields)
    engine.on_bar(_IID, 1, _bar(_T0, 99), _T0)  # leaves a previous price behind
    assert service.list() == [alert]
    assert service.delete(alert.id) is True
    assert service.delete(alert.id) is False
    store.add(alert)  # the same id back: a stale previous price would make this tick fire
    engine.on_bar(_IID, 1, _bar(_T0 + _S, 101), _T0 + _S)
    _join_deliveries()
    assert deliverer.sent == []
