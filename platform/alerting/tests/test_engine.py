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

Story 33.8: every condition kind through the engine, with fake indicator and drawing readers and an
inline `submit` -- one indicator read per closed bar batching every alert of a series, invalidation
ledgered once, restart, the windows' MEM trimming and `forget` on an edit.
"""

import asyncio
import dataclasses
import threading
from collections.abc import Callable
from collections.abc import Sequence
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from kernel.derivs_wire import FUNDING
from kernel.derivs_wire import MARK
from kernel.derivs_wire import OI
from kernel.derivs_wire import DerivsTick
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from alerting.application.engine import INPUT_SITE
from alerting.application.engine import INVALID_SITE
from alerting.application.engine import AlertEngine
from alerting.application.ports import Anchors
from alerting.application.ports import Failed
from alerting.application.ports import IndicatorReading
from alerting.application.ports import IndicatorRef
from alerting.application.ports import IndicatorResult
from alerting.application.ports import Missing
from alerting.application.service import AlertService
from alerting.application.service import NoDeliveryChannel
from alerting.domain.alert import Alert
from alerting.domain.alert import new_alert
from alerting.domain.alert import status_of
from alerting.domain.conditions import ConditionError
from alerting.infrastructure.toml_store import AlertStore
from nautilus_trader.model.identifiers import InstrumentId


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


class _FakeIndicators:
    """An `IndicatorReader` by shape: answers per series name and records every read."""

    def __init__(self, results: dict[str, IndicatorResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, int, int, tuple[IndicatorRef, ...]]] = []
        self.error: Exception | None = None

    def read(
        self, instrument_id: str, bar_seconds: int, closed_t_ms: int, refs: Sequence[IndicatorRef]
    ) -> dict[IndicatorRef, IndicatorResult]:
        self.calls.append((instrument_id, bar_seconds, closed_t_ms, tuple(refs)))
        if self.error is not None:
            raise self.error
        return {ref: self.results.get(ref.name, Failed("no result")) for ref in refs}


class _FakeDrawings:
    """A `DrawingReader` by shape over an in-memory dict; `error` makes every read raise."""

    def __init__(self, items: dict[str, Anchors] | None = None) -> None:
        self.items = items or {}
        self.error: Exception | None = None

    def trendline(self, instrument_id: str, drawing_id: str) -> Anchors | Missing:
        if self.error is not None:
            raise self.error
        anchors = self.items.get(drawing_id)
        return Missing(f"drawing {drawing_id} no longer exists") if anchors is None else anchors


def _inline(job: Callable[[], Any], done: Callable[[Any], None]) -> None:
    done(job())


def _new_engine(
    store: AlertStore,
    deliverer: _RecordingDeliverer,
    indicators: _FakeIndicators | None = None,
    drawings: _FakeDrawings | None = None,
) -> AlertEngine:
    return AlertEngine(
        store,
        deliverer,
        indicators=indicators or _FakeIndicators(),
        drawings=drawings or _FakeDrawings(),
        submit=_inline,
    )


def _engine(tmp_path: Path, *alerts: Alert) -> tuple[AlertEngine, AlertStore, _RecordingDeliverer]:
    store = AlertStore(tmp_path / "a.toml")
    for alert in alerts:
        store.add(alert)
    deliverer = _RecordingDeliverer()
    return _new_engine(store, deliverer), store, deliverer


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
        "alert": {
            "id": alert.id,
            "message": "hit 101.0",
            "condition": "close crosses 100 on 1s bars",
        },
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
    service = AlertService(store, deliverer, _new_engine(store, deliverer))
    fields = {
        k: v
        for k, v in vars(_alert(webhook_url="")).items()
        if k not in ("id", "created_ns", "condition", "invalid_reason")
    }
    with pytest.raises(NoDeliveryChannel, match="no delivery channel"):
        service.create(**fields)
    assert service.list() == []


def test_service_create_list_delete_forgets_run_state(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer()
    engine = _new_engine(store, deliverer)
    service = AlertService(store, deliverer, engine)
    fields = {
        k: v
        for k, v in vars(_alert()).items()
        if k not in ("id", "created_ns", "condition", "invalid_reason")
    }
    alert = service.create(**fields)
    engine.on_bar(_IID, 1, _bar(_T0, 99), _T0)  # leaves a previous price behind
    assert service.list() == [alert]
    assert service.delete(alert.id) is True
    assert service.delete(alert.id) is False
    store.add(alert)  # the same id back: a stale previous price would make this tick fire
    engine.on_bar(_IID, 1, _bar(_T0 + _S, 101), _T0 + _S)
    _join_deliveries()
    assert deliverer.sent == []


# --- Story 33.8: every condition kind through the engine ------------------------------------------


_BYBIT = "BTCUSDT-LINEAR.BYBIT"
_RSI: dict[str, Any] = {
    "kind": "indicator",
    "name": "RelativeStrengthIndex",
    "params": {"period": 14},
    "output": "value",
    "op": "crosses_up",
    "value": 70.0,
}


def _kind_alert(condition: dict[str, Any], **kw: Any) -> Alert:
    fields: dict[str, Any] = {
        "instrument_id": _IID,
        "level": condition.get("level"),
        "condition": condition,
        "frequency": "once_per_bar",
        "bar_seconds": 60,
        "template": "{{ticker}} {{value}}",
        "webhook_url": "http://localhost:1/hook",
    }
    fields.update(kw)
    return new_alert(**fields)


def _flow_bar(ts_ns: int, close: float, buy_v: int, sell_v: int, bar_seconds: int = 60) -> dict:
    """Return a forming bar with Story 33.3's flow keys: volumes in units at size precision 3."""
    return {
        **_bar(ts_ns, close, bar_seconds),
        "buy_v": buy_v,
        "sell_v": sell_v,
        "size_precision": 3,
    }


def _rig(
    tmp_path: Path,
    *alerts: Alert,
    indicators: _FakeIndicators | None = None,
    drawings: _FakeDrawings | None = None,
) -> tuple[AlertEngine, AlertStore, _RecordingDeliverer]:
    store = AlertStore(tmp_path / "a.toml")
    for alert in alerts:
        store.add(alert)
    deliverer = _RecordingDeliverer()
    return _new_engine(store, deliverer, indicators, drawings), store, deliverer


def _fired(deliverer: _RecordingDeliverer) -> list[str]:
    _join_deliveries()
    return [body for _id, body in deliverer.sent]


def _closes(engine: AlertEngine, iid: str, bar_seconds: int, closes: Sequence[float]) -> int:
    """Feed one tick per bucket at each close; return the ts_ns of the next bucket's start."""
    ts = _T0
    for close in closes:
        engine.on_bar(iid, bar_seconds, _bar(ts, close, bar_seconds), ts)
        ts += bar_seconds * _S
    return ts


def _liq(
    ts_ns: int, size_units: int, price_units: int, side: str = "long", n: int = 0
) -> Liquidation:
    return Liquidation(
        instrument_id=InstrumentId.from_str(_BYBIT),
        side=LiquidatedSide(side),
        size_units=size_units,
        price_units=price_units,
        price_precision=1,
        size_precision=3,
        venue_event_id=f"{ts_ns}:{side}:{n}",
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


@pytest.fixture(autouse=True)
def _clean_ledger() -> Any:
    error_ledger.reset()
    yield
    error_ledger.reset()


def test_cross_up_fires_on_the_way_up_only(tmp_path: Path) -> None:
    up = _kind_alert({"kind": "price_cross_up", "level": 100.0}, bar_seconds=1)
    engine, _store, deliverer = _rig(tmp_path, up)
    _closes(engine, _IID, 1, [99, 100, 99])  # 99 -> 100 fires, 100 -> 99 does not
    assert _fired(deliverer) == [f"{_IID} 100.0"]


def test_above_once_per_bar_fires_once_per_bucket(tmp_path: Path) -> None:
    engine, _store, deliverer = _rig(tmp_path, _kind_alert({"kind": "price_above", "level": 100.0}))
    for offset, close in [(0, 101), (1, 102), (60, 103)]:
        ts = _T0 + offset * _S
        engine.on_bar(_IID, 60, _bar(ts, close, 60), ts)
    assert _fired(deliverer) == [f"{_IID} 101.0", f"{_IID} 103.0"]


def test_pct_move_compares_the_close_n_closed_bars_back(tmp_path: Path) -> None:
    alert = _kind_alert({"kind": "pct_move", "pct": 10, "bars": 2})
    engine, _store, deliverer = _rig(tmp_path, alert)
    ts = _closes(engine, _IID, 60, [100, 105])
    engine.on_bar(_IID, 60, _bar(ts, 110, 60), ts)  # only two closed bars: no sample yet
    assert _fired(deliverer) == []
    ts += 60 * _S
    engine.on_bar(_IID, 60, _bar(ts, 110, 60), ts)  # closed 100, 105, 110; 110 vs 100 is +10 %
    assert _fired(deliverer) == [f"{_IID} 10.0"]


def test_pct_move_history_is_bounded_to_bars_plus_one(tmp_path: Path) -> None:
    engine, _store, _deliverer = _rig(
        tmp_path, _kind_alert({"kind": "pct_move", "pct": 50, "bars": 2})
    )
    _closes(engine, _IID, 60, [100, 101, 102, 103, 104, 105, 106])
    assert list(engine._closes[(_IID, 60)]) == [103, 104, 105]


def test_channel_exit_fires_on_leaving_the_channel(tmp_path: Path) -> None:
    alert = _kind_alert({"kind": "channel_exit", "upper": 110, "lower": 90}, bar_seconds=1)
    engine, _store, deliverer = _rig(tmp_path, alert)
    _closes(engine, _IID, 1, [105, 111])
    assert _fired(deliverer) == [f"{_IID} 111.0"]


def _trendline_alert() -> Alert:
    return _kind_alert(
        {"kind": "trendline_cross", "drawing_id": "d1"},
        bar_seconds=1,
        template="{{close}} {{value}}",
    )


def test_trendline_cross_fires_on_the_extrapolated_line(tmp_path: Path) -> None:
    # Anchors (1000 s, 100) and (2000 s, 200): line(3000) = 300; closes 290 -> 310 cross it.
    drawings = _FakeDrawings({"d1": [{"time": 1000, "price": 100}, {"time": 2000, "price": 200}]})
    engine, _store, deliverer = _rig(tmp_path, _trendline_alert(), drawings=drawings)
    at = 3000 * _S
    engine.on_bar(_IID, 1, {**_bar(at, 290), "t": 3_000_000}, at)
    engine.on_bar(_IID, 1, {**_bar(at, 310), "t": 3_000_000}, at + _S // 2)
    assert _fired(deliverer) == ["310.0 310.0"]


def test_a_deleted_trendline_invalidates_once_and_never_fires(tmp_path: Path) -> None:
    engine, store, deliverer = _rig(tmp_path, _trendline_alert(), drawings=_FakeDrawings())
    _closes(engine, _IID, 1, [290, 310, 290])
    alert = store.list()[0]
    assert status_of(alert, _T0) == "invalid"
    assert alert.invalid_reason == "drawing d1 no longer exists"
    assert error_ledger.counts() == {INVALID_SITE: 1}
    assert _fired(deliverer) == []
    assert engine.watched_bars() == frozenset()  # no longer watched
    assert AlertStore(tmp_path / "a.toml").list()[0].invalid_reason == alert.invalid_reason


def test_a_vertical_trendline_invalidates(tmp_path: Path) -> None:
    drawings = _FakeDrawings({"d1": [{"time": 1000, "price": 100}, {"time": 1000, "price": 200}]})
    engine, store, _deliverer = _rig(tmp_path, _trendline_alert(), drawings=drawings)
    _closes(engine, _IID, 1, [290])
    assert "vertical" in (store.list()[0].invalid_reason or "")


def test_an_unreadable_drawings_file_skips_the_sample_and_never_invalidates(
    tmp_path: Path,
) -> None:
    drawings = _FakeDrawings()
    drawings.error = ValueError("corrupt chart_drawings.toml")
    engine, store, _deliverer = _rig(tmp_path, _trendline_alert(), drawings=drawings)
    _closes(engine, _IID, 1, [290, 310])
    assert store.list()[0].invalid_reason is None
    assert error_ledger.counts() == {INPUT_SITE: 2}


def _reading(prev: float, cur: float, outputs: frozenset[str] = frozenset({"value"})) -> Any:
    return IndicatorReading(prev={"value": prev}, cur={"value": cur}, outputs=outputs)


def test_indicator_cross_fires_once_for_the_closed_bar(tmp_path: Path) -> None:
    indicators = _FakeIndicators({"RelativeStrengthIndex": _reading(69, 71)})
    engine, _store, deliverer = _rig(tmp_path, _kind_alert(_RSI), indicators=indicators)
    ts = _closes(engine, _IID, 60, [100, 101])  # one bucket rolled over: one read
    engine.on_bar(_IID, 60, _bar(ts - 30 * _S, 102, 60), ts - 30 * _S)  # mid-bucket: no read
    assert [call[2] for call in indicators.calls] == [_T0 // 1_000_000]
    assert _fired(deliverer) == [f"{_IID} 71.0"]


def test_indicator_reads_are_batched_per_pair_and_closed_bar(tmp_path: Path) -> None:
    indicators = _FakeIndicators({"RelativeStrengthIndex": _reading(69, 71)})
    alerts = [_kind_alert(_RSI) for _ in range(3)]
    other = _kind_alert({**_RSI, "params": {"period": 7}})
    engine, _store, deliverer = _rig(tmp_path, *alerts, other, indicators=indicators)
    _closes(engine, _IID, 60, [100, 101])
    assert len(indicators.calls) == 1
    assert len(indicators.calls[0][3]) == 2  # two distinct series, RSI(14) once
    assert len(_fired(deliverer)) == 4


def test_an_unknown_indicator_output_invalidates(tmp_path: Path) -> None:
    indicators = _FakeIndicators({"RelativeStrengthIndex": _reading(69, 71, frozenset({"signal"}))})
    engine, store, deliverer = _rig(tmp_path, _kind_alert(_RSI), indicators=indicators)
    _closes(engine, _IID, 60, [100, 101, 102])
    assert "output 'value'" in (store.list()[0].invalid_reason or "")
    assert error_ledger.counts() == {INVALID_SITE: 1}
    assert _fired(deliverer) == []


def test_an_indicator_gone_from_the_catalog_invalidates(tmp_path: Path) -> None:
    indicators = _FakeIndicators({"RelativeStrengthIndex": Missing("not in the catalog")})
    engine, store, _deliverer = _rig(tmp_path, _kind_alert(_RSI), indicators=indicators)
    _closes(engine, _IID, 60, [100, 101])
    assert store.list()[0].invalid_reason == "not in the catalog"


def test_a_failed_indicator_read_is_ledgered_and_never_invalidates(tmp_path: Path) -> None:
    indicators = _FakeIndicators()
    indicators.error = RuntimeError("replay blew up")
    engine, store, deliverer = _rig(tmp_path, _kind_alert(_RSI), indicators=indicators)
    ts = _closes(engine, _IID, 60, [100, 101])  # the whole read raises
    indicators.error = None  # the next read answers `Failed` for the series
    engine.on_bar(_IID, 60, _bar(ts, 102, 60), ts)
    assert store.list()[0].invalid_reason is None
    assert error_ledger.counts() == {INPUT_SITE: 2}
    assert _fired(deliverer) == []


def _tick(kind: str, t: int, value: str) -> DerivsTick:
    return DerivsTick(_BYBIT, kind, t, t, Decimal(value))


def test_funding_above_fires_on_the_exact_tick_and_reports_it(tmp_path: Path) -> None:
    alert = _kind_alert({"kind": "funding_above", "rate": 0.0003}, instrument_id=_BYBIT)
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_deriv(_tick(FUNDING, _T0, "0.0002"))
    engine.on_deriv(_tick(FUNDING, _T0 + 3600 * _S, "0.0004"))
    assert _fired(deliverer) == [f"{_BYBIT} 0.0004"]


def test_funding_ignores_open_interest_and_other_instruments(tmp_path: Path) -> None:
    alert = _kind_alert({"kind": "funding_above", "rate": 0.0003}, instrument_id=_BYBIT)
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_deriv(_tick(OI, _T0, "1"))
    engine.on_deriv(DerivsTick("ETHUSDT-LINEAR.BYBIT", FUNDING, _T0, _T0, Decimal("0.01")))
    assert _fired(deliverer) == []


def test_oi_change_fires_over_its_window_and_waits_for_a_full_one(tmp_path: Path) -> None:
    alert = _kind_alert({"kind": "oi_change", "pct": 5, "window_s": 3600}, instrument_id=_BYBIT)
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_deriv(_tick(OI, _T0, "200"))
    engine.on_deriv(_tick(OI, _T0 + 1800 * _S, "230"))  # the series is younger than the window
    assert _fired(deliverer) == []
    engine.on_deriv(_tick(OI, _T0 + 3600 * _S, "210"))  # 200 -> 210 is +5 %
    assert _fired(deliverer) == [f"{_BYBIT} 5.0"]


def test_the_oi_series_is_trimmed_to_the_widest_window(tmp_path: Path) -> None:
    alert = _kind_alert({"kind": "oi_change", "pct": 50, "window_s": 60}, instrument_id=_BYBIT)
    engine, _store, _deliverer = _rig(tmp_path, alert)
    for i in range(10):
        engine.on_deriv(_tick(OI, _T0 + i * 30 * _S, str(100 + i)))
    # The newest tick at or before t - 60 s is the base; everything older is gone.
    assert [v for _t, v in engine._oi[_BYBIT]] == [Decimal(107), Decimal(108), Decimal(109)]


def test_liquidation_notional_sums_one_side_over_its_window(tmp_path: Path) -> None:
    condition = {
        "kind": "liquidation_notional",
        "notional": 100000,
        "window_s": 300,
        "side": "long",
    }
    engine, _store, deliverer = _rig(tmp_path, _kind_alert(condition, instrument_id=_BYBIT))
    # 1 BTC at 60000.0 is 60k, 0.5 BTC at 100000.0 is 50k (units at precisions 1 and 3).
    engine.on_liquidation([_liq(_T0, 1000, 600000), _liq(_T0 + _S, 900, 1000000, "short")])
    assert _fired(deliverer) == []  # the short row is not summed under side long
    engine.on_liquidation([_liq(_T0 + 2 * _S, 500, 1000000)])
    assert _fired(deliverer) == [f"{_BYBIT} 110000.0"]


def test_liquidations_leave_the_window_and_a_redelivery_counts_once(tmp_path: Path) -> None:
    condition = {"kind": "liquidation_notional", "notional": 100000, "window_s": 300}
    engine, _store, deliverer = _rig(tmp_path, _kind_alert(condition, instrument_id=_BYBIT))
    first = _liq(_T0, 1000, 600000)
    engine.on_liquidation([first, first])
    engine.on_liquidation([_liq(_T0 + 300 * _S, 500, 1000000)])  # the first is out of the window
    assert _fired(deliverer) == []
    assert len(engine._liquidations[_BYBIT]) == 1


def _seed_volume(engine: AlertEngine) -> None:
    """Observe the volume source once in the bucket before `_T0`: the first observation seeds."""
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0 - 60 * _S, 100, 0, 0), _T0 - 60 * _S)


def test_forced_share_is_liquidated_over_traded_size(tmp_path: Path) -> None:
    alert = _kind_alert(
        {"kind": "forced_share", "share": 0.15, "window_s": 300}, instrument_id=_BYBIT
    )
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_liquidation([_liq(_T0, 2000, 600000)])  # no volume yet: no share, no fire
    assert _fired(deliverer) == []
    _seed_volume(engine)
    # 4 BTC in the first tick of a new bucket, then the forming bar grows to 10 BTC: 10 BTC traded in the window.
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0 + _S, 100, 3000, 1000), _T0 + _S)
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0 + 2 * _S, 100, 7000, 3000), _T0 + 2 * _S)
    assert _fired(deliverer) == [f"{_BYBIT} 0.5"]  # 2 / 4 at the first bar tick


def test_forced_share_counts_each_tick_increase_and_a_new_bucket_whole(tmp_path: Path) -> None:
    alert = _kind_alert(
        {"kind": "forced_share", "share": 0.15, "window_s": 300},
        instrument_id=_BYBIT,
        frequency="only_once",
    )
    engine, _store, deliverer = _rig(tmp_path, alert)
    _seed_volume(engine)
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0, 100, 3000, 1000), _T0)  # 4 BTC, a new bucket
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0 + _S, 100, 5000, 1000), _T0 + _S)  # +2 BTC
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0 + 60 * _S, 100, 2000, 2000), _T0 + 60 * _S)  # +4 BTC
    assert [a[0][0] for _ts, a in engine._volume[_BYBIT].amounts()] == [4000, 2000, 4000]
    engine.on_liquidation([_liq(_T0 + 61 * _S, 2000, 600000)])  # 2 / 10 = 0.2 >= 0.15
    assert _fired(deliverer) == [f"{_BYBIT} 0.2"]


def test_a_restart_needs_two_samples_for_a_cross(tmp_path: Path) -> None:
    engine, store, deliverer = _rig(tmp_path, _alert())
    engine.on_bar(_IID, 1, _bar(_T0, 99), _T0)
    restarted = _new_engine(AlertStore(tmp_path / "a.toml"), deliverer)
    restarted.on_bar(_IID, 1, _bar(_T0 + _S, 101), _T0 + _S)  # no previous value: no cross
    assert _fired(deliverer) == []
    restarted.on_bar(_IID, 1, _bar(_T0 + 2 * _S, 99), _T0 + 2 * _S)
    assert len(_fired(deliverer)) == 1


def test_windows_exist_only_while_an_alert_of_that_kind_names_the_instrument(
    tmp_path: Path,
) -> None:
    oi = _kind_alert({"kind": "oi_change", "pct": 5, "window_s": 60}, instrument_id=_BYBIT)
    liq = _kind_alert(
        {"kind": "liquidation_notional", "notional": 1e9, "window_s": 60}, instrument_id=_BYBIT
    )
    engine, store, deliverer = _rig(tmp_path, oi, liq)
    service = AlertService(store, deliverer, engine)
    engine.on_deriv(_tick(OI, _T0, "1"))
    engine.on_liquidation([_liq(_T0, 1, 1)])
    assert (_BYBIT in engine._oi, _BYBIT in engine._liquidations) == (True, True)
    service.delete(oi.id)
    service.delete(liq.id)
    assert (_BYBIT in engine._oi, _BYBIT in engine._liquidations) == (False, False)
    engine.on_deriv(_tick(OI, _T0 + _S, "2"))  # no alert: nothing is kept
    assert engine._oi == {}


def test_service_update_edits_rearms_and_forgets_the_run_state(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer()
    engine = _new_engine(store, deliverer)
    service = AlertService(store, deliverer, engine)
    alert = service.create(**_create_fields())
    _closes(engine, _IID, 1, [99, 101])  # fires once: triggered
    assert store.list()[0].triggered is True
    edited = service.update(
        alert.id,
        condition={"kind": "price_cross_down", "level": 100},
        frequency="only_once",
        expires_at_ns=None,
        template="t",
        webhook_url="http://localhost:1/hook",
        rearm=True,
    )
    assert edited is not None
    assert (edited.triggered, edited.level, status_of(edited, _T0)) == (False, 100.0, "active")
    assert alert.id not in engine._state  # a stale previous value cannot cross on the next tick
    assert AlertStore(tmp_path / "a.toml").list()[0].condition == {
        "kind": "price_cross_down",
        "level": 100.0,
    }


def test_service_update_clears_invalid_and_refuses_a_bad_edit(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer()
    service = AlertService(store, deliverer, _new_engine(store, deliverer))
    alert = service.create(**_create_fields())
    store.mark_invalid(alert, "gone")
    edit: dict[str, Any] = {
        "frequency": "only_once",
        "expires_at_ns": None,
        "template": "t",
        "webhook_url": "x",
        "rearm": False,
    }
    with pytest.raises(ConditionError) as caught:
        service.update(alert.id, condition={"kind": "pct_move", "pct": 0, "bars": 2}, **edit)
    assert caught.value.field == "pct"
    assert service.update("nope", condition={"kind": "price_above", "level": 1}, **edit) is None
    fixed = service.update(alert.id, condition={"kind": "price_above", "level": 1}, **edit)
    assert fixed is not None
    assert fixed.invalid_reason is None


def test_service_update_refuses_an_alert_with_no_channel(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer()
    service = AlertService(store, deliverer, _new_engine(store, deliverer))
    alert = service.create(**_create_fields())
    deliverer.named = ()
    with pytest.raises(NoDeliveryChannel):
        service.update(
            alert.id,
            condition={"kind": "price_above", "level": 1},
            frequency="only_once",
            expires_at_ns=None,
            template="t",
            webhook_url="",
            rearm=False,
        )
    assert store.list()[0].condition == {"kind": "price_cross", "level": 100.0}


def _create_fields() -> dict[str, Any]:
    return {
        "instrument_id": _IID,
        "level": 100.0,
        "frequency": "only_once",
        "bar_seconds": 1,
        "template": "{{ticker}} {{close}}",
        "webhook_url": "http://localhost:1/hook",
    }


def test_close_placeholder_is_the_newest_close_seen(tmp_path: Path) -> None:
    alert = _kind_alert(
        {"kind": "funding_above", "rate": 0.0003},
        instrument_id=_BYBIT,
        template="{{close}} {{condition}}",
    )
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_deriv(_tick(FUNDING, _T0, "0.0004"))
    assert _fired(deliverer) == ["n/a funding rate > 0.0003 on 60s bars"]


# --- Story 33.8 review fixes -----------------------------------------------------------------------


def test_forget_from_another_thread_is_applied_on_the_observers_loop(tmp_path: Path) -> None:
    # The CRUD routes run in the threadpool; the observers mutate the same tables on the loop.
    engine, store, _deliverer = _engine(tmp_path, _alert())
    alert = store.list()[0]

    async def scenario() -> tuple[bool, bool]:
        engine.watched_bars()  # the bus asks it on the loop: the engine learns its loop
        engine.on_bar(_IID, 1, _bar(_T0, 99), _T0)
        worker = threading.Thread(target=engine.forget, args=(alert.id,))
        worker.start()
        worker.join()
        queued = alert.id in engine._state  # not applied off the loop
        await asyncio.sleep(0)  # the loop runs the queued forget
        return queued, alert.id in engine._state

    assert asyncio.run(scenario()) == (True, False)


def test_an_edited_alert_never_reuses_the_replaced_objects_run_state(tmp_path: Path) -> None:
    # The edit replaces the stored object before its `forget` reaches the loop.
    engine, store, deliverer = _engine(tmp_path, _alert())
    alert = store.list()[0]
    engine.on_bar(_IID, 1, _bar(_T0, 99), _T0)  # the replaced object's previous value: 99
    store.update(alert.id, lambda stored: dataclasses.replace(stored, template="edited"))
    engine.on_bar(_IID, 1, _bar(_T0 + _S, 101), _T0 + _S)  # 99 -> 101 must not cross
    assert _fired(deliverer) == []


def test_close_of_a_bar_close_fire_is_the_closed_bars_close_not_the_new_tick(
    tmp_path: Path,
) -> None:
    # The pre-33.8 template: `{{close}}` was the closed bar's close, the price that crossed.
    alert = _alert(
        "once_per_bar_close", bar_seconds=60, template="{{ticker}} price crossed {{close}}"
    )
    engine, _store, deliverer = _engine(tmp_path, alert)
    for offset, close in [(0, 99), (60, 101), (120, 50)]:  # bar 1 closes at 101
        ts = _T0 + offset * _S
        engine.on_bar(_IID, 60, _bar(ts, close, 60), ts)
    assert _fired(deliverer) == [f"{_IID} price crossed 101.0"]


def test_close_of_an_indicator_fire_is_the_closed_bars_close(tmp_path: Path) -> None:
    indicators = _FakeIndicators({"RelativeStrengthIndex": _reading(69, 71)})
    alert = _kind_alert(_RSI, template="{{close}}")
    engine, _store, deliverer = _rig(tmp_path, alert, indicators=indicators)
    _closes(engine, _IID, 60, [100, 101])  # bar 0 closed at 100, read on bar 1's first tick
    assert _fired(deliverer) == ["100.0"]


def test_a_bar_close_funding_alert_is_decided_when_its_instruments_clock_passes_the_bucket(
    tmp_path: Path,
) -> None:
    alert = _kind_alert(
        {"kind": "funding_above", "rate": 0.0003},
        instrument_id=_BYBIT,
        frequency="once_per_bar_close",
        bar_seconds=3600,
        template="{{value}} {{time}}",
    )
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_deriv(_tick(FUNDING, _T0 + 1200 * _S, "0.0004"))  # 10:20, the 10:00 bucket
    engine.on_deriv(_tick(MARK, _T0 + 3599 * _S, "65000"))  # still 10:59:59: undecided
    assert _fired(deliverer) == []
    engine.on_deriv(_tick(MARK, _T0 + 3601 * _S, "65000"))  # 11:00:01 closes the 10:00 bucket
    stamped = datetime.fromtimestamp((_T0 + 1200 * _S) / _S, UTC).isoformat()
    assert _fired(deliverer) == [f"0.0004 {stamped}"]  # stamped with the bucket's own sample
    engine.on_deriv(_tick(MARK, _T0 + 7300 * _S, "65000"))  # the 11:00 bucket had no sample
    engine.on_deriv(_tick(FUNDING, _T0 + 7400 * _S, "0.0001"))  # nor does it roll over twice
    assert len(_fired(deliverer)) == 1


def test_a_bar_tick_closes_a_liquidation_alerts_bucket(tmp_path: Path) -> None:
    condition = {"kind": "liquidation_notional", "notional": 50000, "window_s": 300}
    alert = _kind_alert(condition, instrument_id=_BYBIT, frequency="once_per_bar_close")
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_liquidation([_liq(_T0 + 10 * _S, 1000, 600000)])  # 60k in the bucket at _T0
    assert _fired(deliverer) == []
    engine.on_bar(_BYBIT, 60, _bar(_T0 + 61 * _S, 100, 60), _T0 + 61 * _S)
    assert _fired(deliverer) == [f"{_BYBIT} 60000.0"]


def test_a_redelivered_liquidation_is_neither_counted_nor_stepped_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    condition = {"kind": "liquidation_notional", "notional": 1e9, "window_s": 300}
    engine, _store, _deliverer = _rig(tmp_path, _kind_alert(condition, instrument_id=_BYBIT))
    stepped: list[Any] = []
    monkeypatch.setattr(engine, "_step", lambda _alert, sample: stepped.append(sample))
    row = _liq(_T0, 1000, 600000)
    engine.on_liquidation([row, row])
    engine.on_liquidation([row])
    assert [s.value for s in stepped] == [Decimal(60000)]


def test_at_most_one_indicator_read_is_in_flight_per_pair(tmp_path: Path) -> None:
    indicators = _FakeIndicators({"RelativeStrengthIndex": _reading(69, 71)})
    store = AlertStore(tmp_path / "a.toml")
    store.add(_kind_alert(_RSI))
    pending: list[tuple[Callable[[], Any], Callable[[Any], None]]] = []
    engine = AlertEngine(
        store,
        _RecordingDeliverer(),
        indicators=indicators,
        drawings=_FakeDrawings(),
        submit=lambda job, done: pending.append((job, done)),
    )
    ts = _closes(engine, _IID, 60, [100, 101, 102])  # two bars closed, the first read still runs
    assert len(pending) == 1
    assert error_ledger.counts() == {INPUT_SITE: 1}
    assert "still running" in error_ledger.last_details()[INPUT_SITE]
    job, done = pending.pop()
    done(job())  # the read finishes: the next closed bar is read again
    engine.on_bar(_IID, 60, _bar(ts, 103, 60), ts)
    assert len(pending) == 1


def test_the_first_volume_observation_only_seeds_the_baseline(tmp_path: Path) -> None:
    # Created mid-bucket (or the first tick after a restart): the bucket's 10 BTC so far are not
    # this second's.
    alert = _kind_alert({"kind": "forced_share", "share": 5, "window_s": 300}, instrument_id=_BYBIT)
    engine, _store, _deliverer = _rig(tmp_path, alert)
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0 + 30 * _S, 100, 6000, 4000), _T0 + 30 * _S)
    assert list(engine._volume[_BYBIT].amounts()) == []
    engine.on_bar(_BYBIT, 60, _flow_bar(_T0 + 31 * _S, 100, 6500, 4500), _T0 + 31 * _S)
    assert [a[0][0] for _ts, a in engine._volume[_BYBIT].amounts()] == [1000]


def test_volume_is_tracked_from_one_width_however_the_widths_interleave(tmp_path: Path) -> None:
    # A 3600 s price alert beside a 60 s forced_share on one instrument: the hour bar's 500 BTC so
    # far must never be counted, whichever pair the bus folds first in a second.
    price = _kind_alert(
        {"kind": "price_above", "level": 1e9}, instrument_id=_BYBIT, bar_seconds=3600
    )
    share = _kind_alert({"kind": "forced_share", "share": 5, "window_s": 300}, instrument_id=_BYBIT)
    engine, _store, _deliverer = _rig(tmp_path, price, share)
    seconds = [
        (120, ((3600, 500_000), (60, 2000))),
        (121, ((60, 3000), (3600, 501_000))),
        (122, ((3600, 503_000), (60, 5000))),
    ]
    for offset, folds in seconds:
        ts = _T0 + offset * _S
        for width, traded in folds:
            engine.on_bar(_BYBIT, width, _flow_bar(ts, 100, traded, 0, width), ts)
    assert [a[0][0] for _ts, a in engine._volume[_BYBIT].amounts()] == [1000, 2000]


def test_editing_an_only_once_alert_to_another_frequency_clears_triggered(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer()
    engine = _new_engine(store, deliverer)
    service = AlertService(store, deliverer, engine)
    alert = service.create(**_create_fields())
    _closes(engine, _IID, 1, [99, 101])  # fires once: triggered
    edited = service.update(
        alert.id,
        condition={"kind": "price_cross", "level": 100},
        frequency="once_per_bar",
        expires_at_ns=None,
        template="t",
        webhook_url="http://localhost:1/hook",
        rearm=False,
    )
    assert edited is not None
    assert (edited.triggered, status_of(edited, _T0)) == (False, "active")
    assert edited.last_fired_ns == _T0 + _S  # the fire's time is kept


def test_a_fire_recorded_while_an_edit_runs_is_not_lost(tmp_path: Path) -> None:
    # The fire lands between the edit's read and its write (here: while the channel is checked).
    store = AlertStore(tmp_path / "a.toml")
    deliverer = _RecordingDeliverer()
    service = AlertService(store, deliverer, _new_engine(store, deliverer))
    alert = service.create(**_create_fields())
    fire = threading.Thread(target=lambda: store.record_fire(store.list()[0], _T0))

    class _FiringDeliverer(_RecordingDeliverer):
        def channels(self, alert: Alert) -> tuple[str, ...]:
            fire.start()
            fire.join(timeout=0.2)  # blocked on the store's lock until the edit is written
            return self.named

    service._deliverer = _FiringDeliverer()
    edited = service.update(
        alert.id,
        condition={"kind": "price_cross", "level": 90},
        frequency="only_once",
        expires_at_ns=None,
        template="t",
        webhook_url="http://localhost:1/hook",
        rearm=False,
    )
    fire.join()
    assert edited is not None
    stored = AlertStore(tmp_path / "a.toml").list()[0]
    assert (stored.level, stored.triggered, stored.last_fired_ns) == (90.0, True, _T0)


def test_a_late_liquidation_is_summed_into_the_window_as_it_stands_now(tmp_path: Path) -> None:
    condition = {"kind": "liquidation_notional", "notional": 100000, "window_s": 300}
    engine, _store, deliverer = _rig(tmp_path, _kind_alert(condition, instrument_id=_BYBIT))
    engine.on_liquidation([_liq(_T0 + 10 * _S, 1000, 600000)])  # 60k
    engine.on_liquidation([_liq(_T0 + 12 * _S, 300, 1000000)])  # +30k
    # A row whose ts_event lags the two above: the window now holds 110k, not the 20k up to 5 s.
    engine.on_liquidation([_liq(_T0 + 5 * _S, 200, 1000000)])
    assert _fired(deliverer) == [f"{_BYBIT} 110000.0"]


def test_a_liquidation_closes_its_instruments_bar_close_funding_bucket(tmp_path: Path) -> None:
    alert = _kind_alert(
        {"kind": "funding_above", "rate": 0.0003},
        instrument_id=_BYBIT,
        frequency="once_per_bar_close",
        bar_seconds=3600,
    )
    engine, _store, deliverer = _rig(tmp_path, alert)
    engine.on_deriv(_tick(FUNDING, _T0 + 1200 * _S, "0.0004"))
    assert _fired(deliverer) == []
    engine.on_liquidation([_liq(_T0 + 3601 * _S, 1000, 600000)])  # the 10:00 bucket has ended
    assert _fired(deliverer) == [f"{_BYBIT} 0.0004"]


def test_an_invalidation_of_an_alert_an_edit_replaced_is_not_ledgered(tmp_path: Path) -> None:
    engine, store, _deliverer = _rig(tmp_path, _trendline_alert(), drawings=_FakeDrawings())
    replaced = store.list()[0]
    store.update(replaced.id, lambda a: dataclasses.replace(a, template="edited"))
    engine._invalidate(replaced, "drawing d1 no longer exists")  # a sample of the old object
    assert error_ledger.counts() == {}
    assert store.list()[0].invalid_reason is None
