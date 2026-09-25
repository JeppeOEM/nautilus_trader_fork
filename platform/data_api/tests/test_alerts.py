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
Stories 20.1/20.2/24.3: the `/api/alerts` routes, the frozen `/api/alerts` JSON and `/ws/live`
toast payloads (pinned against the pre-24.3 code), and the engine driven the way the running app
drives it -- attached to a real `LiveCandleBus` fed real `DydxSecondSnapshot` batches, so an alert
is evaluated on the forming bar the chart shows. The pure frequency rules, the store and the
deliverer are tested in `alerting/tests`.
"""

import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

import alerting.domain.alert as alert_module
import pytest
from alerting.application.engine import QUEUE_MAX as ALERTING_QUEUE_MAX
from alerting.application.engine import AlertEngine
from alerting.application.service import AlertService
from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.infrastructure.deliverer import NotifyDeliverer
from alerting.infrastructure.toml_store import AlertStore
from fastapi.testclient import TestClient
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger
from views.live_candles import LiveCandleBus
from views.rankings_bus import QUEUE_MAX as VIEWS_QUEUE_MAX

import data_api.app as app_module
from data_api import alert_wiring
from data_api import buses
from nautilus_trader.model.identifiers import InstrumentId


_IID = "BTC-USD-PERP.DYDX"
_S = 1_000_000_000
_T0 = 1_800_000_000 * _S  # multiple of 60s
# The archive a bus's seed would read; these tests never subscribe a chart, so never seed.
_NO_CATALOG = "no-catalog-read-in-this-test"


def _alert(frequency: str = "only_once", **kw: object) -> Alert:
    fields: dict[str, object] = {
        "instrument_id": _IID,
        "level": 100.0,
        "frequency": frequency,
        "bar_seconds": 60,
        "template": "{{ticker}} {{close}}",
        "webhook_url": "http://localhost:1/hook",
    }
    fields.update(kw)
    return new_alert(**fields)


class _RecordingDeliverer:
    """A `Deliverer` by shape: records the bodies a fire would send."""

    def __init__(self) -> None:
        self.posted: list[str] = []

    def channels(self, alert: Alert) -> tuple[str, ...]:
        return (f"webhook:{alert.webhook_url}",)

    def deliver(self, alert: Alert, body: str) -> None:
        self.posted.append(body)


def _engine(store: AlertStore) -> AlertEngine:
    return AlertEngine(store, _RecordingDeliverer())


def _drive(engine: AlertEngine, ticks: list[tuple[int, float | None]]) -> None:
    """Feed (ts_ns, close) seconds to `engine` the way the running app does: one bus batch each."""
    bus = LiveCandleBus(_NO_CATALOG)
    bus.attach(engine)
    for ts_ns, close in ticks:
        bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(ts_ns, close))])


def test_only_once_engine_fires_a_single_time(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1))
    deliverer = _RecordingDeliverer()
    engine = AlertEngine(store, deliverer)
    _drive(engine, [(_T0 + i * _S, price) for i, price in enumerate([99, 101, 99, 101])])
    _join_post_threads()
    assert deliverer.posted == [f"{_IID} 101.0"]
    assert store.list()[0].triggered is True


def test_failed_persist_still_fires_the_alert(tmp_path: Path) -> None:
    error_ledger.reset()
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1))
    deliverer = _RecordingDeliverer()
    engine = AlertEngine(store, deliverer)
    (tmp_path / "a.toml").unlink()
    (tmp_path / "a.toml").mkdir()  # makes every later save raise IsADirectoryError
    _drive(engine, [(_T0, 99), (_T0 + _S, 101)])
    _join_post_threads()
    assert len(deliverer.posted) == 1
    assert error_ledger.counts() == {"alerting.store.persist": 1}
    error_ledger.reset()


def test_toast_is_pushed_to_subscribers(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1, template="hit {{close}}"))
    engine = _engine(store)
    queue = engine.subscribe()
    _drive(engine, [(_T0, 99), (_T0 + _S, 101)])
    assert queue.get_nowait()["alert"]["message"] == "hit 101.0"


def test_second_without_a_trade_is_ignored(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1))
    engine = _engine(store)
    _drive(engine, [(_T0, 99), (_T0 + _S, None)])
    assert store.list()[0].triggered is False


def test_alert_on_an_uncharted_pair_fires_on_the_forming_bar_close(tmp_path: Path) -> None:
    # No `/ws/live` listener at all: the bus folds the pair because the engine watches it, and the
    # 60 s bar's close (the latest traded second) is what crosses -- an untraded second between
    # republishes the same close and cannot fire.
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("once_per_bar", bar_seconds=60))
    deliverer = _RecordingDeliverer()
    engine = AlertEngine(store, deliverer)
    _drive(engine, [(_T0, 99), (_T0 + _S, None), (_T0 + 2 * _S, 101), (_T0 + 3 * _S, None)])
    _join_post_threads()
    assert deliverer.posted == [f"{_IID} 101.0"]


async def _idle(_redis_url: str) -> None:
    await asyncio.Event().wait()  # stands in for a bus's Redis loop until the lifespan cancels it


@pytest.mark.asyncio
async def test_lifespan_attaches_the_engine_to_the_bus_and_detaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    bus = LiveCandleBus(_NO_CATALOG)
    monkeypatch.setattr(bus, "run", _idle)
    monkeypatch.setattr(buses, "live_candle_bus", bus)
    monkeypatch.setattr(buses.bus, "run", _idle)
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("once_per_bar", bar_seconds=1, template="hit {{close}}"))
    engine = _engine(store)
    monkeypatch.setattr(alert_wiring, "engine", engine)
    queue = engine.subscribe()

    def feed(ts_ns: int, close: float) -> None:
        bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(ts_ns, close))])

    async with app_module.lifespan(app_module.app):
        feed(_T0, 99)
        feed(_T0 + _S, 101)
        assert queue.get_nowait()["alert"]["message"] == "hit 101.0"
    feed(_T0 + 2 * _S, 99)  # a cross the detached engine never sees
    _join_post_threads()
    assert queue.empty()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    store = AlertStore(tmp_path / "alerts.toml")
    deliverer = NotifyDeliverer()
    engine = AlertEngine(store, deliverer)
    monkeypatch.setattr(alert_wiring, "store", store)
    monkeypatch.setattr(alert_wiring, "engine", engine)
    monkeypatch.setattr(alert_wiring, "service", AlertService(store, deliverer, engine))
    return TestClient(app_module.app)


_BODY = {
    "instrument_id": _IID,
    "level": 65000,
    "frequency": "once_per_bar",
    "bar_seconds": 60,
    "template": "{{ticker}}",
    "webhook_url": "https://example.com/h",
}


def test_routes_create_list_delete(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    created = client.post("/api/alerts", json=_BODY)
    assert created.status_code == 201
    assert created.json()["status"] == "active"
    listed = client.get("/api/alerts").json()
    assert [a["id"] for a in listed] == [created.json()["id"]]
    assert client.delete(f"/api/alerts/{listed[0]['id']}").status_code == 204
    assert client.get("/api/alerts").json() == []
    assert client.delete("/api/alerts/nope").status_code == 404


@pytest.mark.parametrize(
    "patch",
    [
        {"webhook_url": "ftp://x/y"},
        {"webhook_url": "not a url"},
        {"frequency": "always"},
        {"bar_seconds": 0},
        {"level": "nan"},
        {"instrument_id": " "},
        {"template": "x" * 1001},
    ],
)
def test_routes_reject_invalid_alerts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch: dict,
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/alerts", json={**_BODY, **patch}).status_code == 422


def _snapshot(ts_ns: int, close: float | None) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[1.0],
        bid_sizes=[1.0],
        ask_prices=[2.0],
        ask_sizes=[1.0],
        buy_volume=0.0,
        sell_volume=0.0,
        buy_count=0,
        sell_count=0,
        ts_event=ts_ns,
        ts_init=ts_ns,
        close_price=None if close is None else float(close),
    )


def _join_post_threads() -> None:
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)


def test_route_requires_a_delivery_channel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert client.post("/api/alerts", json={**_BODY, "webhook_url": ""}).status_code == 422
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert client.post("/api/alerts", json={**_BODY, "webhook_url": ""}).status_code == 201


# --- Frozen payloads (AD-D12), recorded against the pre-24.3 `data_api/alerts.py` -----------------

_PINNED_ID = "0123456789abcdef0123456789abcdef"
_PINNED_CREATED_NS = 1_800_000_000_123_456_789


def _pin_id_and_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only the id/clock `new_alert` reads are fixed; the route's own `time` (status) stays real.
    monkeypatch.setattr(
        alert_module, "uuid", SimpleNamespace(uuid4=lambda: SimpleNamespace(hex=_PINNED_ID))
    )
    monkeypatch.setattr(alert_module, "time", SimpleNamespace(time_ns=lambda: _PINNED_CREATED_NS))


def test_api_alerts_json_is_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    _pin_id_and_clock(monkeypatch)
    expected = (
        '{"instrument_id":"BTC-USD-PERP.DYDX","level":65000.0,"frequency":"once_per_bar",'
        '"bar_seconds":60,"expires_at_ns":null,"template":"{{ticker}}",'
        '"webhook_url":"https://example.com/h","id":"0123456789abcdef0123456789abcdef",'
        '"status":"active","created_ns":1800000000123456789,"last_fired_ns":null}'
    )
    created = client.post("/api/alerts", json=_BODY)
    assert created.status_code == 201
    assert created.content == expected.encode()
    listed = client.get("/api/alerts")
    assert listed.content == f"[{expected}]".encode()
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    rejected = client.post("/api/alerts", json={**_BODY, "webhook_url": ""})
    assert rejected.status_code == 422
    assert rejected.content == (
        b'{"detail":"no delivery channel: set a webhook URL or configure '
        b'TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID"}'
    )
    missing = client.delete("/api/alerts/nope")
    assert missing.content == b'{"detail":"alert not found"}'


def test_toast_payload_is_pinned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _pin_id_and_clock(monkeypatch)
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1, template="{{ticker}} crossed {{close}}"))
    engine = _engine(store)
    queue = engine.subscribe()
    _drive(engine, [(_T0, 99), (_T0 + _S, 101)])
    _join_post_threads()
    assert queue.get_nowait() == {
        "channel": "alerts",
        "alert": {"id": _PINNED_ID, "message": "BTC-USD-PERP.DYDX crossed 101.0"},
    }
    assert queue.empty()


def test_toast_queue_bound_matches_the_views_queue_bound() -> None:
    """
    `alerting` copies `views.rankings_bus.QUEUE_MAX` by value (it may not import views); this is the
    one place both are importable, so it pins them equal -- every `/ws/live` feed drops alike.
    """
    assert ALERTING_QUEUE_MAX == VIEWS_QUEUE_MAX
