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
"""Stories 20.1/20.2: alert store round-trip, `/api/alerts` routes, the pure frequency/
expiration state machine, template rendering, and engine fire path (real objects; the network
POST is injected as a recording callable, not a mock of any library)."""

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
from data_api import alerts
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId


_IID = "BTC-USD-PERP.DYDX"
_S = 1_000_000_000
_T0 = 1_800_000_000 * _S  # multiple of 60s


def _alert(frequency: str = "only_once", **kw) -> alerts.Alert:
    fields = dict(
        instrument_id=_IID, level=100.0, frequency=frequency, bar_seconds=60,
        template="{{ticker}} {{close}}", webhook_url="http://localhost:1/hook",
    )
    fields.update(kw)
    return alerts.new_alert(**fields)


def _run(alert: alerts.Alert, ticks: list[tuple[int, float]]) -> list[int]:
    """Feed (seconds-after-T0, price) ticks through evaluate(); return indexes that fired."""
    state = alerts.RunState()
    return [i for i, (s, p) in enumerate(ticks) if alerts.evaluate(alert, state, p, _T0 + s * _S)]


def test_store_round_trip_keeps_optional_fields(tmp_path: Path) -> None:
    path = tmp_path / "alerts.toml"
    store = alerts.AlertStore(path)
    a, b = _alert(expires_at_ns=_T0 + _S), _alert()
    store.add(a)
    store.add(b)
    store.record_fire(a, _T0)
    reloaded = alerts.AlertStore(path).list()
    assert [x.id for x in reloaded] == [a.id, b.id]
    assert reloaded[0].expires_at_ns == _T0 + _S
    assert reloaded[0].triggered is True
    assert reloaded[1].expires_at_ns is None
    assert store.delete(a.id) is True
    assert store.delete(a.id) is False
    assert [x.id for x in alerts.AlertStore(path).list()] == [b.id]


def test_cross_up_and_down_fire_but_touching_without_crossing_does_not() -> None:
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 99), (1, 101)]) == [1]
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 101), (1, 99)]) == [1]
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 99), (1, 99.5)]) == []
    assert _run(_alert("once_per_bar", bar_seconds=1), [(0, 100), (1, 100)]) == []


def test_only_once_engine_fires_a_single_time(tmp_path: Path) -> None:
    posted: list[str] = []
    store = alerts.AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1))
    engine = alerts.AlertEngine(store, post=lambda a, body: posted.append(body))
    for i, price in enumerate([99, 101, 99, 101]):
        engine.on_snapshot(_snapshot(_T0 + i * _S, price))
    _join_post_threads()
    assert posted == [f"{_IID} 101.0"]
    assert store.list()[0].triggered is True


def test_once_per_bar_fires_at_most_once_per_bar() -> None:
    # bar 0 (0-59s): crosses at idx 1, 2, 3 -> only idx 1 fires. Bar 1 (60s+): crosses at idx 4
    # (101->99 across the bar boundary) and 5 -> only idx 4 fires.
    ticks = [(0, 99), (1, 101), (2, 99), (3, 101), (60, 99), (61, 101)]
    assert _run(_alert("once_per_bar"), ticks) == [1, 4]


def test_once_per_bar_close_decides_on_the_bar_close_not_intrabar_wicks() -> None:
    # bar 0 closes 99, bar 1 wicks to 101 but closes 99 -> no fire; bar 2 closes 101 -> fires
    # on the first tick of bar 3 (the bar's close is only known once the bucket rolls over).
    ticks = [(0, 99), (30, 99), (60, 101), (90, 99), (120, 101), (150, 101), (180, 101)]
    assert _run(_alert("once_per_bar_close"), ticks) == [6]


def test_expired_alert_never_fires_and_reports_expired() -> None:
    alert = _alert("once_per_bar", bar_seconds=1, expires_at_ns=_T0 + _S)
    assert _run(alert, [(0, 99), (1, 101), (2, 99), (3, 101)]) == []
    assert alerts.status_of(alert, _T0 + _S) == "expired"
    assert alerts.status_of(alert, _T0) == "active"


def test_render_substitutes_all_four_placeholders() -> None:
    out = alerts.render("{{ticker}}|{{close}}|{{time}}|{{interval}}|{{ticker}}", _IID, 65000.5, _T0, 60)
    assert out == f"{_IID}|65000.5|2027-01-15T08:00:00+00:00|60|{_IID}"


def test_failed_webhook_is_logged_with_alert_id(caplog: pytest.LogCaptureFixture) -> None:
    alert = _alert(webhook_url="http://127.0.0.1:1/hook")  # nothing listens on port 1
    alerts.post_webhook(alert, "x")
    assert f"alert {alert.id} webhook POST failed" in caplog.text


def test_toast_is_pushed_to_subscribers(tmp_path: Path) -> None:
    store = alerts.AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1, template="hit {{close}}"))
    engine = alerts.AlertEngine(store, post=lambda a, body: None)
    queue = engine.subscribe()
    engine.on_snapshot(_snapshot(_T0, 99))
    engine.on_snapshot(_snapshot(_T0 + _S, 101))
    assert queue.get_nowait()["alert"]["message"] == "hit 101.0"


def test_second_without_a_trade_is_ignored(tmp_path: Path) -> None:
    store = alerts.AlertStore(tmp_path / "a.toml")
    store.add(_alert("only_once", bar_seconds=1))
    engine = alerts.AlertEngine(store, post=lambda a, body: None)
    engine.on_snapshot(_snapshot(_T0, 99))
    engine.on_snapshot(_snapshot(_T0 + _S, None))
    assert store.list()[0].triggered is False


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(alerts, "store", alerts.AlertStore(tmp_path / "alerts.toml"))
    return TestClient(app_module.app)


_BODY = {
    "instrument_id": _IID, "level": 65000, "frequency": "once_per_bar",
    "bar_seconds": 60, "template": "{{ticker}}", "webhook_url": "https://example.com/h",
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
        {"webhook_url": "ftp://x/y"}, {"webhook_url": "not a url"}, {"frequency": "always"},
        {"bar_seconds": 0}, {"level": "nan"}, {"instrument_id": " "}, {"template": "x" * 1001},
    ],
)
def test_routes_reject_invalid_alerts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch: dict,
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/alerts", json={**_BODY, **patch}).status_code == 422


def _snapshot(ts_ns: int, close: float | None) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID), bid_prices=[1.0], bid_sizes=[1.0],
        ask_prices=[2.0], ask_sizes=[1.0], buy_volume=0.0, sell_volume=0.0, buy_count=0, sell_count=0,
        ts_event=ts_ns, ts_init=ts_ns, close_price=None if close is None else float(close),
    )


def _join_post_threads() -> None:
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=2)
