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
import typing
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import alerting.domain.alert as alert_module
import pytest
from alerting.application.engine import QUEUE_MAX as ALERTING_QUEUE_MAX
from alerting.application.engine import AlertEngine
from alerting.application.ports import Deliverer
from alerting.application.service import AlertService
from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.domain.conditions import CONDITION_FIELDS
from alerting.domain.conditions import CONDITION_KINDS
from alerting.domain.policy import RunState
from alerting.infrastructure import toml_store
from alerting.infrastructure.deliverer import NotifyDeliverer
from alerting.infrastructure.toml_store import AlertStore
from fastapi.testclient import TestClient
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger
from views import preferences
from views.live_candles import LiveCandleBus
from views.live_derivs import LiveDerivsBus
from views.rankings_bus import QUEUE_MAX as VIEWS_QUEUE_MAX

import data_api.app as app_module
from data_api import alert_wiring
from data_api import buses
from data_api import settings
from data_api.alert_inputs import ChartIndicatorReader
from data_api.alert_inputs import DrawingFileReader
from data_api.routes import alerts as alert_routes
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


# The drawings file of a test that names no trendline: never created, so every drawing is missing.
_NO_DRAWINGS = Path("no-drawings-in-this-test.toml")


def _inline(job: Callable[[], Any], done: Callable[[Any], None]) -> None:
    done(job())


def _engine_inputs(drawings_path: Path) -> dict[str, Any]:
    """Return the real input adapters an engine takes: the chart's indicator page, the drawings."""
    indicators = ChartIndicatorReader(
        catalog_path=lambda: _NO_CATALOG,
        candles_dir=lambda: _NO_CATALOG,
        recent_rows=lambda *_: [],
        recent_liquidations=lambda *_: [],
    )
    return {
        "indicators": indicators,
        "drawings": DrawingFileReader(lambda: drawings_path),
        "submit": _inline,
    }


def _new_engine(store: AlertStore, deliverer: Deliverer, drawings_path: Path) -> AlertEngine:
    """Return an engine on the real input adapters: the chart's indicator page, the drawings."""
    return AlertEngine(store, deliverer, **_engine_inputs(drawings_path))


def _engine(store: AlertStore, deliverer: Deliverer | None = None) -> AlertEngine:
    return _new_engine(store, deliverer or _RecordingDeliverer(), _NO_DRAWINGS)


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
    engine = _engine(store, deliverer)
    _drive(engine, [(_T0 + i * _S, price) for i, price in enumerate([99, 101, 99, 101])])
    _join_post_threads()
    assert deliverer.posted == [f"{_IID} 101.0"]
    assert store.list()[0].triggered is True


def test_failed_persist_still_fires_the_alert(tmp_path: Path) -> None:
    error_ledger.reset()
    store = AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1))
    deliverer = _RecordingDeliverer()
    engine = _engine(store, deliverer)
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
    engine = _engine(store, deliverer)
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
    drawings = tmp_path / "drawings.toml"
    engine = _new_engine(store, deliverer, drawings)
    monkeypatch.setattr(settings, "CHART_DRAWINGS_PATH", str(drawings))
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


class _ForgetRecordingEngine(AlertEngine):
    def __init__(self, store: AlertStore) -> None:
        super().__init__(store, _RecordingDeliverer(), **_engine_inputs(_NO_DRAWINGS))
        self.forgotten: list[str] = []

    def forget(self, alert_id: str) -> None:
        self.forgotten.append(alert_id)
        super().forget(alert_id)


def test_failed_delete_keeps_the_alert_and_its_run_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DW-199: a delete whose save raises keeps the alert listed and watched, so the engine's run
    # state for it must not be forgotten either.
    store = AlertStore(tmp_path / "alerts.toml")
    engine = _ForgetRecordingEngine(store)
    service = AlertService(store, _RecordingDeliverer(), engine)
    alert = _alert()
    store.add(alert)

    def _failing_fsync(fd: int) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(toml_store.os, "fsync", _failing_fsync)
    with pytest.raises(OSError, match="No space left"):
        service.delete(alert.id)

    assert engine.forgotten == []
    assert [a.id for a in store.list()] == [alert.id]


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
    return make_snapshot(
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
        '"status":"active","created_ns":1800000000123456789,"last_fired_ns":null,'
        # Story 33.8's keys, appended after the frozen ones (AD-D12).
        '"condition":{"kind":"price_cross","level":65000.0},'
        '"condition_text":"close crosses 65000 on 60s bars","invalid_reason":null}'
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
        "alert": {
            "id": _PINNED_ID,
            "message": "BTC-USD-PERP.DYDX crossed 101.0",
            "condition": "close crosses 100 on 1s bars",  # Story 33.8's appended key
        },
    }
    assert queue.empty()


def test_toast_queue_bound_matches_the_views_queue_bound() -> None:
    """
    `alerting` copies `views.rankings_bus.QUEUE_MAX` by value (it may not import views); this is the
    one place both are importable, so it pins them equal -- every `/ws/live` feed drops alike.
    """
    assert ALERTING_QUEUE_MAX == VIEWS_QUEUE_MAX


# --- Story 33.8: condition kinds, PUT, invalid status, the derivatives observer -------------------

_RSI = {
    "kind": "indicator",
    "name": "RelativeStrengthIndex",
    "params": {"period": 14},
    "output": "value",
    "op": ">",
    "value": 70,
}
_TRENDLINE = {
    "id": "t1",
    "kind": "trendline",
    "color": "#fff",
    "anchors": [{"time": 1000, "price": 100.0}, {"time": 2000, "price": 200.0}],
}
_CONDITIONS = [
    {"kind": "price_cross", "level": 100},
    {"kind": "price_cross_up", "level": 100},
    {"kind": "price_cross_down", "level": 100},
    {"kind": "price_above", "level": 100},
    {"kind": "price_below", "level": 100},
    {"kind": "pct_move", "pct": -2.5, "bars": 3},
    {"kind": "channel_exit", "upper": 110, "lower": 90},
    _RSI,
    {"kind": "trendline_cross", "drawing_id": "t1"},
    {"kind": "funding_above", "rate": 0.0003},
    {"kind": "funding_below", "rate": -0.0001},
    {"kind": "oi_change", "pct": 5, "window_s": 3600},
    {"kind": "liquidation_notional", "notional": 100000, "window_s": 300, "side": "long"},
    {"kind": "forced_share", "share": 0.15, "window_s": 300},
]


def _save_drawings(tmp_path: Path, items: list[dict]) -> None:
    preferences.save_chart_drawings({_IID: items}, tmp_path / "drawings.toml")


def _create(client: TestClient, condition: dict, **patch: Any) -> Any:
    body = {k: v for k, v in _BODY.items() if k != "level"}
    return client.post("/api/alerts", json={**body, "condition": condition, **patch})


def test_the_route_models_mirror_the_domain_kinds_and_fields() -> None:
    fields: dict[str, set[str]] = {}
    for model in alert_routes.CONDITION_MODELS:
        kinds = typing.get_args(model.model_fields["kind"].annotation)
        for kind in kinds:
            fields[kind] = set(model.model_fields) - {"kind"}
    assert set(fields) == set(CONDITION_KINDS)
    assert fields == {kind: set(names) for kind, names in CONDITION_FIELDS.items()}


@pytest.mark.parametrize("condition", _CONDITIONS, ids=lambda c: c["kind"])
def test_post_creates_each_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: dict
) -> None:
    client = _client(tmp_path, monkeypatch)
    _save_drawings(tmp_path, [_TRENDLINE])
    created = _create(client, condition)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["condition"]["kind"] == condition["kind"]
    assert body["level"] == (100.0 if condition["kind"].startswith("price_") else None)
    assert body["condition_text"].endswith("on 60s bars")
    assert (body["status"], body["invalid_reason"]) == ("active", None)
    assert AlertStore(tmp_path / "alerts.toml").list()[0].condition == body["condition"]


def test_level_with_an_agreeing_level_condition_is_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    created = client.post(
        "/api/alerts", json={**_BODY, "level": 6, "condition": {"kind": "price_above", "level": 6}}
    )
    assert created.status_code == 201
    assert created.json()["level"] == 6.0


@pytest.mark.parametrize(
    ("patch", "detail"),
    [
        (  # the matrix: level 5 with price_above 6
            {"level": 5, "condition": {"kind": "price_above", "level": 6}},
            "level must be omitted, or equal the condition's level for a price-level kind",
        ),
        (
            {"level": 5, "condition": {"kind": "funding_above", "rate": 0.1}},
            "level must be omitted, or equal the condition's level for a price-level kind",
        ),
        (
            {"level": None, "condition": {"kind": "channel_exit", "upper": 90, "lower": 110}},
            "condition.upper must be greater than lower",
        ),
        ({"level": None}, "level or condition is required"),
        (
            {"level": None, "condition": {**_RSI, "name": "Nope"}},
            "condition.name 'Nope' is not an indicator of the catalog",
        ),
        (
            {"level": None, "condition": {**_RSI, "output": "signal"}},
            "condition.output must be one of ['value']",
        ),
        (
            {"level": None, "condition": {"kind": "trendline_cross", "drawing_id": "h1"}},
            f"condition.drawing_id 'h1' is not a trendline of {_IID}",
        ),
    ],
)
def test_post_refuses_a_bad_condition_naming_the_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch: dict, detail: str
) -> None:
    client = _client(tmp_path, monkeypatch)
    _save_drawings(tmp_path, [{"id": "h1", "kind": "hline", "price": 100.0}])
    refused = client.post("/api/alerts", json={**_BODY, **patch})
    assert refused.status_code == 422
    assert refused.json() == {"detail": detail}
    assert client.get("/api/alerts").json() == []


@pytest.mark.parametrize(
    ("condition", "field"),
    [
        ({**_RSI, "params": {"period": 0}}, "condition.params"),
        ({**_RSI, "params": {"nope": 1}}, "condition.params"),
        ({**_RSI, "source": "nope"}, "condition.source"),
    ],
)
def test_post_checks_an_indicator_against_the_picker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, condition: dict, field: str
) -> None:
    refused = _create(_client(tmp_path, monkeypatch), condition)
    assert refused.status_code == 422
    assert refused.json()["detail"].startswith(field)


def test_post_refuses_an_unknown_condition_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = _create(_client(tmp_path, monkeypatch), {"kind": "price_above", "level": 1, "x": 2})
    assert refused.status_code == 422


def _put(client: TestClient, alert_id: str, **patch: Any) -> Any:
    body = {
        "condition": {"kind": "price_cross_down", "level": 90},
        "frequency": "only_once",
        "expires_at_ns": None,
        "template": "{{ticker}} {{condition}}",
        "webhook_url": "https://example.com/h",
        "rearm": False,
    }
    return client.put(f"/api/alerts/{alert_id}", json={**body, **patch})


def test_put_edits_a_triggered_alert_and_rearms_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    alert_id = client.post("/api/alerts", json={**_BODY, "frequency": "only_once"}).json()["id"]
    stored = alert_wiring.store.list()[0]
    alert_wiring.engine._state[alert_id] = (alert_wiring.store.list()[0], RunState())
    stored.triggered = True
    assert client.get("/api/alerts").json()[0]["status"] == "triggered"
    edited = _put(client, alert_id, rearm=True)
    assert edited.status_code == 200
    body = edited.json()
    assert (body["status"], body["level"]) == ("active", 90.0)
    assert body["condition_text"] == "close crosses down 90 on 60s bars"
    assert alert_id not in alert_wiring.engine._state  # run state reset
    reloaded = AlertStore(tmp_path / "alerts.toml").list()[0]
    assert (reloaded.condition, reloaded.triggered) == (
        {"kind": "price_cross_down", "level": 90.0},
        False,
    )


def test_put_without_rearm_keeps_a_triggered_alert_triggered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    alert_id = client.post("/api/alerts", json=_BODY).json()["id"]
    alert_wiring.store.list()[0].triggered = True
    assert _put(client, alert_id).json()["status"] == "triggered"


def test_put_refusals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    alert_id = client.post("/api/alerts", json=_BODY).json()["id"]
    missing = _put(client, "nope")
    assert (missing.status_code, missing.json()) == (404, {"detail": "alert not found"})
    bad = _put(client, alert_id, condition={"kind": "pct_move", "pct": 0, "bars": 2})
    assert bad.status_code == 422
    assert bad.json()["detail"].startswith("condition.pct ")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    no_channel = _put(client, alert_id, webhook_url="")
    assert no_channel.status_code == 422
    assert no_channel.json()["detail"].startswith("no delivery channel")
    assert AlertStore(tmp_path / "alerts.toml").list()[0].condition == {
        "kind": "price_cross",
        "level": 65000.0,
    }


def test_an_invalid_alert_lists_as_invalid_and_a_put_revalidates_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    alert_id = client.post("/api/alerts", json=_BODY).json()["id"]
    alert_wiring.store.mark_invalid(alert_wiring.store.list()[0], "drawing t1 no longer exists")
    listed = client.get("/api/alerts").json()[0]
    assert (listed["status"], listed["invalid_reason"]) == (
        "invalid",
        "drawing t1 no longer exists",
    )
    revalidated = _put(client, alert_id).json()
    assert (revalidated["status"], revalidated["invalid_reason"]) == ("active", None)


def _bar_tick(bus: LiveCandleBus, ts_ns: int, close: float) -> None:
    bus.handle_batch([DydxSecondSnapshot.to_dict(_snapshot(ts_ns, close))])


def test_a_deleted_trendline_marks_the_alert_invalid_on_the_next_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    client = _client(tmp_path, monkeypatch)
    _save_drawings(tmp_path, [_TRENDLINE])
    created = _create(client, {"kind": "trendline_cross", "drawing_id": "t1"}, bar_seconds=1)
    assert created.status_code == 201
    assert client.put(f"/api/coin/{_IID}/drawings", json={"items": []}).status_code == 200
    bus = LiveCandleBus(_NO_CATALOG)
    bus.attach(alert_wiring.engine)
    _bar_tick(bus, _T0, 99)
    _bar_tick(bus, _T0 + _S, 101)
    listed = client.get("/api/alerts").json()[0]
    assert (listed["status"], listed["invalid_reason"]) == (
        "invalid",
        "drawing t1 no longer exists",
    )
    assert error_ledger.counts() == {"alerting.engine.invalid": 1}
    error_ledger.reset()


def test_an_indicator_alert_is_created_without_a_chart_and_edited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    created = _create(client, _RSI, bar_seconds=3600)
    assert (
        created.json()["condition_text"]
        == "RelativeStrengthIndex(period=14) value > 70 on 3600s bars"
    )
    edited = _put(client, created.json()["id"], condition={**_RSI, "op": "crosses_up"})
    assert edited.json()["condition_text"] == (
        "RelativeStrengthIndex(period=14) value crosses up 70 on 3600s bars"
    )
    assert client.get("/api/alerts").json()[0]["condition_text"] == edited.json()["condition_text"]
    stored = (tmp_path / "alerts.toml").read_text()
    assert 'op = "crosses_up"' in stored
    assert "[alerts.condition.params]\nperiod = 14" in stored


def test_a_liquidation_alert_fires_through_the_derivs_bus_with_no_listener(
    tmp_path: Path,
) -> None:
    iid = "BTCUSDT-LINEAR.BYBIT"
    store = AlertStore(tmp_path / "a.toml")
    condition = {"kind": "liquidation_notional", "notional": 100000, "window_s": 300}
    store.add(_alert(instrument_id=iid, level=None, condition=condition, template="{{value}}"))
    deliverer = _RecordingDeliverer()
    engine = _engine(store, deliverer)
    candles, derivs = LiveCandleBus(_NO_CATALOG), LiveDerivsBus()
    candles.attach_liquidations(derivs)
    derivs.attach(engine)
    rows = [
        Liquidation.from_wire_text(
            InstrumentId.from_str(iid), LiquidatedSide.LONG, size, "60000.0", (1, 3), key, ts, ts
        )
        for size, key, ts in [("1.000", "a", _T0), ("1.000", "b", _T0 + _S)]
    ]
    candles.handle_liquidations([Liquidation.to_dict(row) for row in rows])
    _join_post_threads()
    assert deliverer.posted == ["120000.0"]  # 60k, then 120k >= 100k


def test_the_indicator_catalog_serves_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entries = _client(tmp_path, monkeypatch).get("/api/indicators/catalog").json()
    assert entries["RelativeStrengthIndex"]["outputs"] == ["value"]
    assert entries["TradeCount"]["outputs"] == ["value", "buys", "sells"]


@pytest.mark.asyncio
async def test_lifespan_attaches_the_engine_to_the_derivs_bus_and_detaches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    derivs = LiveDerivsBus()
    for bus in (buses.bus, buses.live_candle_bus, buses.archive_bus, derivs):
        monkeypatch.setattr(bus, "run", _idle)
    monkeypatch.setattr(buses, "live_derivs_bus", derivs)
    store = AlertStore(tmp_path / "a.toml")
    condition = {"kind": "funding_above", "rate": 0.0003}
    iid = "BTCUSDT-LINEAR.BYBIT"
    store.add(_alert(instrument_id=iid, level=None, condition=condition, template="{{value}}"))
    deliverer = _RecordingDeliverer()
    monkeypatch.setattr(alert_wiring, "engine", _engine(store, deliverer))

    def funding(value: str) -> list[dict]:
        return [{"instrument_id": iid, "kind": "funding", "t": _T0, "ts_init": _T0, "value": value}]

    async with app_module.lifespan(app_module.app):
        derivs.handle_derivs(funding("0.0004"))  # no `/ws/live` listener open
    derivs.handle_derivs(funding("0.0005"))  # detached: never seen
    _join_post_threads()
    assert deliverer.posted == ["0.0004"]
