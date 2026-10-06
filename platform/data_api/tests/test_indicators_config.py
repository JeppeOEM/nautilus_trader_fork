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
Story 15.6: `GET /api/indicators/catalog`, `GET`/`PUT /api/coin/{instrument_id}/indicators`,
`GET /api/coin/{instrument_id}/indicator-values` -- real `IndicatorEntry`/`save_config`/
`load_config` objects against a temp TOML file (no mocking of persisted-resource internals,
platform/CLAUDE.md TEST-03), real `ParquetDataCatalog`/`DydxSecondSnapshot` for the values route,
mirrors `test_candles.py`'s fixture pattern.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from kernel.tests.snapshot_factory import make_snapshot
from views.preferences import IndicatorEntry
from views.preferences import load_chart_indicators as load_config
from views.preferences import save_chart_indicators as save_config

import data_api.app as app_module
import data_api.routes.candles as candles_routes
import data_api.routes.indicators as indicators_routes
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"

# Same base timestamp convention as test_candles.py -- a multiple of 60s in ns.
_BASE_NS = 1_800_000_000_000_000_000
assert _BASE_NS % 60_000_000_000 == 0


def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    catalog_path: str | None = None,
) -> TestClient:
    monkeypatch.setattr(
        indicators_routes,
        "CHART_INDICATOR_CONFIG_PATH",
        str(tmp_path / "chart_indicators.toml"),
    )
    catalog = catalog_path or str(tmp_path / "cat")
    monkeypatch.setattr(
        candles_routes, "CATALOG_PATH", catalog
    )  # indicator-values reads candles via this route
    monkeypatch.setattr(candles_routes, "CANDLES_DB_DIR", f"{catalog}-no-candle-store-dir")
    return TestClient(app_module.app)


def _define_instrument(catalog_path: str) -> None:
    ParquetDataCatalog(catalog_path).write_data(
        [
            CryptoPerpetual(
                instrument_id=InstrumentId.from_str(_IID),
                raw_symbol=Symbol("BTC-USD"),
                base_currency=BTC,
                quote_currency=USDT,
                settlement_currency=USDT,
                is_inverse=False,
                price_precision=2,
                price_increment=Price(0.01, 2),
                size_precision=3,
                size_increment=Quantity(0.001, 3),
                ts_event=0,
                ts_init=0,
            )
        ]
    )


def _write_snapshots(catalog_path: str, entries: list[tuple[int, float]]) -> None:
    entries = sorted(entries, key=lambda e: e[0])
    ParquetDataCatalog(catalog_path).write_data(
        [
            make_snapshot(
                instrument_id=InstrumentId.from_str(_IID),
                bid_prices=[price],
                bid_sizes=[1.0],
                ask_prices=[price + 1.0],
                ask_sizes=[1.0],
                buy_volume=1.0,
                sell_volume=0.5,
                buy_count=1,
                sell_count=1,
                open_price=price,
                high_price=price,
                low_price=price,
                close_price=price,
                ts_event=ts,
                ts_init=ts,
            )
            for ts, price in entries
        ]
    )


def test_catalog_non_empty_with_both_categories_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/indicators/catalog")
    assert response.status_code == 200
    body = response.json()
    assert body
    categories = {entry["category"] for entry in body.values()}
    assert categories == {"native", "custom"}


def test_the_catalog_serves_the_cvd_anchor_choices(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Story 33.3: a custom entry carries its string params' choices, the picker's dropdown."""
    body = _client(tmp_path, monkeypatch).get("/api/indicators/catalog").json()
    cvd = body["CumulativeVolumeDelta"]
    assert (cvd["params"], cvd["panel"], cvd["category"]) == (
        {"anchor": "visible"},
        "oscillator",
        "custom",
    )
    assert cvd["choices"] == {"anchor": ["session", "visible", "all"]}


def test_a_cvd_anchor_outside_its_choices_is_refused_at_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = [
        {"name": "CumulativeVolumeDelta", "params": {"anchor": "week"}, "category": "custom"}
    ]
    response = client.put(f"/api/coin/{_IID}/indicators", json=payload)
    assert response.status_code == 400
    assert "anchor" in response.text


def test_coin_never_configured_returns_empty_list(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.get(f"/api/coin/{_IID}/indicators")
    assert response.status_code == 200
    assert response.json() == []


def test_put_then_get_reflects_exactly_what_was_put(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = [
        {"name": "RelativeStrengthIndex", "params": {"period": 21}, "category": "native"},
        {"name": "CumulativeVolumeDelta", "params": {}, "category": "custom"},
    ]

    put_response = client.put(f"/api/coin/{_IID}/indicators", json=payload)
    assert put_response.status_code == 200
    assert put_response.json() == {"ok": True}

    get_response = client.get(f"/api/coin/{_IID}/indicators")
    assert get_response.status_code == 200
    assert get_response.json() == [
        {**entry, "source": "close", "hidden": False, "style": {}} for entry in payload
    ]

    # Real load_config() against the same temp file the route just wrote, not a mock --
    # proves the write actually landed as real TOML, not just an in-process fake.
    config = load_config(Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH))
    assert config[_IID] == [
        IndicatorEntry(name="RelativeStrengthIndex", params={"period": 21}, category="native"),
        IndicatorEntry(name="CumulativeVolumeDelta", params={}, category="custom"),
    ]


def test_put_preserves_other_coins_existing_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Real save_config()/load_config() round trip seeding a second instrument's entry
    before the route's own PUT for `_IID` -- proves the route's read-modify-write doesn't
    clobber sibling instruments' persisted config.
    """
    client = _client(tmp_path, monkeypatch)
    path = Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH)
    other_iid = "ETH-USD-PERP.DYDX"
    save_config({other_iid: [IndicatorEntry(name="EMA", params={}, category="native")]}, path)

    put_response = client.put(
        f"/api/coin/{_IID}/indicators",
        json=[{"name": "SimpleMovingAverage", "params": {}, "category": "native"}],
    )
    assert put_response.status_code == 200

    config = load_config(path)
    assert config[other_iid] == [IndicatorEntry(name="EMA", params={}, category="native")]
    assert config[_IID] == [
        IndicatorEntry(name="SimpleMovingAverage", params={}, category="native")
    ]


@pytest.mark.parametrize(
    "payload",
    [
        [{"params": {}, "category": "native"}],  # missing "name"
        [{"name": "RSI", "params": {}}],  # missing "category"
    ],
)
def test_malformed_put_payload_returns_400_not_500(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: list[dict],
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.put(f"/api/coin/{_IID}/indicators", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"]  # a real, non-empty error message, not a bare 400


def test_malformed_put_body_not_a_json_array_returns_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.put(
        f"/api/coin/{_IID}/indicators",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 400


def test_corrupt_toml_fails_loud_with_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH).write_text("not [ valid toml")
    response = client.get(f"/api/coin/{_IID}/indicators")
    assert response.status_code == 500
    assert "corrupt" in response.json()["detail"]


def test_indicator_values_reload_reproduces_same_series(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Chart-page-reload edge case (FR42): the same entry, re-fetched via the values route
    twice against the exact same window, reproduces the identical series both times -- not
    merely round-tripped in storage.
    """
    catalog_path = str(tmp_path / "cat")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)

    spec = json.dumps([{"name": "RelativeStrengthIndex", "params": {"period": 2}}])
    url = f"/api/coin/{_IID}/indicator-values"
    query = {
        "before_ns": _BASE_NS + 60_000_000_000,
        "limit": 5,
        "bar_seconds": 60,
        "entries": spec,
    }

    first = client.get(url, params=query)
    second = client.get(url, params=query)
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["items"]
    keys = {k for item in first.json()["items"] for k in item["values"]}
    assert keys == {"RelativeStrengthIndex_period=2.value"}
    # Not just key-presence/reproducibility -- prove the dispatch actually ran and
    # produced real numbers, not five None points that would "reproduce" identically too.
    values = [
        item["values"]["RelativeStrengthIndex_period=2.value"] for item in first.json()["items"]
    ]
    assert any(v is not None for v in values)


def test_indicator_values_unknown_indicator_is_a_per_entry_error_not_a_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "cat")
    _write_snapshots(catalog_path, [(_BASE_NS, 100.0)])
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)
    spec = json.dumps(
        [
            {"name": "NotARealIndicator", "params": {}},
            {"name": "SimpleMovingAverage", "params": {"period": 2}},
        ]
    )
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000,
            "limit": 5,
            "bar_seconds": 60,
            "entries": spec,
        },
    )
    body = response.json()
    assert response.status_code == 200
    assert "NotARealIndicator" in body["errors"]
    assert any(k.startswith("SimpleMovingAverage") for k in body["items"][0]["values"])


def test_put_rejects_unknown_indicator_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = [{"name": "NotARealIndicator", "params": {}, "category": "native"}]

    assert client.put(f"/api/coin/{_IID}/indicators", json=payload).status_code == 400


def test_indicator_values_dispatches_custom_indicator_via_replay_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    `CumulativeVolumeDelta` is only in `indicator_picker.CUSTOM_INDICATOR_CATALOG`, so a
    successful response here proves `indicator_picker.replay_entry`'s custom-catalog branch (the `ReplayWindow`
    path, untested by every other case in this file) actually dispatches, not just the native
    branch every other test exercises. `views.indicator_picker` reads its own module-level
    `_CATALOG_PATH` (a separate constant from this route's `CATALOG_PATH`), so both are
    monkeypatched to the same temp catalog.
    """
    import views.indicator_picker as custom_indicators_module

    catalog_path = str(tmp_path / "cat")
    monkeypatch.setattr(custom_indicators_module, "_CATALOG_PATH", catalog_path)
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)

    spec = json.dumps([{"name": "CumulativeVolumeDelta", "params": {}}])
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000,
            "limit": 5,
            "bar_seconds": 60,
            "entries": spec,
        },
    )
    assert response.status_code == 200
    keys = {k for item in response.json()["items"] for k in item["values"]}
    assert keys == {"CumulativeVolumeDelta.value"}


def test_indicator_values_too_many_entries_returns_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "cat")
    _write_snapshots(catalog_path, [(_BASE_NS, 100.0)])
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)
    spec = json.dumps([{"name": "RSI", "params": {}}] * 51)
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000,
            "limit": 5,
            "bar_seconds": 60,
            "entries": spec,
        },
    )
    assert response.status_code == 400


def test_indicator_values_reports_has_more_when_older_data_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Enough history precedes the requested page that `_has_more`'s probe query must find
    it -- the pagination-continues branch every other case in this file leaves untested.
    """
    catalog_path = str(tmp_path / "cat")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(20)]
    _write_snapshots(catalog_path, entries)
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)

    spec = json.dumps([{"name": "RelativeStrengthIndex", "params": {"period": 2}}])
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 60_000_000_000,
            "limit": 3,
            "bar_seconds": 60,
            "entries": spec,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["has_more"] is True


@pytest.mark.parametrize(
    ("iid", "market"), [("BTCUSDT-SPOT.BYBIT", "spot"), ("BTCUSDT-LINEAR.BYBIT", "perp")]
)
def test_indicator_values_market_field_next_to_venue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    iid: str,
    market: str,
) -> None:
    client = _client(tmp_path, monkeypatch)
    spec = json.dumps([{"name": "RelativeStrengthIndex", "params": {"period": 2}}])
    query = {"before_ns": _BASE_NS, "limit": 3, "bar_seconds": 60, "entries": spec}
    body = client.get(f"/api/coin/{iid}/indicator-values", params=query).json()
    assert (body["venue"], body["market"]) == ("BYBIT", market)


def test_indicator_values_at_1w_are_computed_on_monday_anchored_weekly_candles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Story 31.3: a 1W pane replays over 1W candles (604800 s apart, Monday 00:00 UTC) -- the route
    clamped `bar_seconds` to 86400 before, computing the 1W pane on daily bars. The Sunday and the
    Tuesday rows fall in two weeks, which an epoch (Thursday) anchor would have merged.

    Story 31.8: a 1W page from Parquet holds one whole week per request (the query span is capped
    at one week and never starts inside a bucket), so the previous week is the second page -- the
    first page used to fold it from only the days after Thursday, here its Sunday alone.
    """
    day_ns = 86_400_000_000_000
    week_ns = 7 * day_ns
    monday_ns = (_BASE_NS - 4 * day_ns) // week_ns * week_ns + 4 * day_ns  # epoch + 4 d: Monday
    catalog_path = str(tmp_path / "cat")
    _write_snapshots(catalog_path, [(monday_ns - day_ns, 100.0), (monday_ns + day_ns, 110.0)])
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)
    spec = json.dumps([{"name": "SimpleMovingAverage", "params": {"period": 1}}])

    def page(before_ns: int) -> dict:
        query = {"before_ns": before_ns, "limit": 5, "bar_seconds": 604_800, "entries": spec}
        body = client.get(f"/api/coin/{_IID}/indicator-values", params=query)
        assert body.status_code == 200
        return body.json()

    first = page(monday_ns + 3 * day_ns)
    older = page(first["items"][0]["t"] * 1_000_000)

    assert [item["t"] for item in first["items"]] == [monday_ns // 1_000_000]
    assert first["has_more"]
    assert [item["t"] for item in older["items"]] == [(monday_ns - week_ns) // 1_000_000]


def test_candles_and_indicator_values_break_at_identical_gap_times(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Story 32.1: one collection outage (no snapshots and so no trades for three minutes) is broken
    by the same one-row-per-missing-bar run on every pane sharing the chart's time axis.
    """
    catalog_path = str(tmp_path / "cat")
    minutes = [-10, -9, -8, -7, -3, -2, -1]  # the collector was down across -6, -5 and -4
    _write_snapshots(catalog_path, [(_BASE_NS + m * 60_000_000_000, 100.0 - m) for m in minutes])
    _define_instrument(catalog_path)  # the candles route labels at the definition's precision
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)
    page = {"before_ns": _BASE_NS, "limit": 20, "bar_seconds": 60}
    spec = json.dumps([{"name": "RelativeStrengthIndex", "params": {"period": 2}}])

    candles = client.get(f"/api/candles/{_IID}", params=page).json()["items"]
    values = client.get(
        f"/api/coin/{_IID}/indicator-values", params={**page, "entries": spec}
    ).json()["items"]

    expected = [(_BASE_NS // 1_000_000) + m * 60_000 for m in (-6, -5, -4)]
    assert [c["t"] for c in candles if c["c"] is None] == expected
    assert [v["t"] for v in values if not v["values"]] == expected


def test_catalog_flags_exactly_the_close_fed_native_indicators_source_selectable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = _client(tmp_path, monkeypatch).get("/api/indicators/catalog").json()
    assert body["SimpleMovingAverage"]["source_selectable"] is True
    assert body["AverageTrueRange"]["source_selectable"] is False
    assert body["OrderFlowImbalance"]["source_selectable"] is False


def test_put_then_get_round_trips_source_hidden_and_style(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload: list[dict[str, Any]] = [
        {
            "name": "SimpleMovingAverage",
            "params": {"period": 20},
            "category": "native",
            "source": "hl2",
            "hidden": True,
            "style": {"value": {"color": "#ff0000", "line_width": 3, "line_style": "dashed"}},
        },
        {"name": "SimpleMovingAverage", "params": {"period": 20}, "category": "native"},
    ]

    assert client.put(f"/api/coin/{_IID}/indicators", json=payload).status_code == 200

    got = client.get(f"/api/coin/{_IID}/indicators").json()
    assert got[0] == payload[0]
    assert got[1] == {**payload[1], "source": "close", "hidden": False, "style": {}}


def test_put_with_a_source_the_indicator_cannot_take_is_422_naming_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    for name, category, source in (
        ("AverageTrueRange", "native", "hl2"),
        ("OrderFlowImbalance", "custom", "open"),
        ("SimpleMovingAverage", "native", "vwap"),
    ):
        response = client.put(
            f"/api/coin/{_IID}/indicators",
            json=[{"name": name, "category": category, "source": source}],
        )
        assert response.status_code == 422
        assert "source" in response.json()["detail"]
    assert not Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH).exists()


def test_put_with_wrongly_typed_source_hidden_or_style_is_400(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    base = {"name": "SimpleMovingAverage", "category": "native"}
    bad_fields: tuple[dict[str, Any], ...] = (
        {"source": 3},
        {"hidden": "yes"},
        {"style": []},
        {"style": {"value": "red"}},
        {"style": {"value": {"color": None}}},
        {"style": {"value": {"color": {"r": 1}}}},
    )
    for bad in bad_fields:
        response = client.put(f"/api/coin/{_IID}/indicators", json=[{**base, **bad}])
        assert response.status_code == 400, bad
    # JSON has no NaN, but Python's decoder takes the bare token: saved, the GET would be a 500.
    body = (
        '[{"name": "SimpleMovingAverage", "category": "native",'
        ' "style": {"value": {"line_width": NaN}}}]'
    )
    response = client.put(
        f"/api/coin/{_IID}/indicators",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert not Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH).exists()


def test_get_serves_close_for_a_persisted_source_the_indicator_cannot_take(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # A hand-edit (or an indicator that stopped being close-fed): served as is, the client's values
    # request would be a 422 and blank every pane of the coin, and every later save a 422 too.
    client = _client(tmp_path, monkeypatch)
    save_config(
        {
            _IID: [
                IndicatorEntry("AverageTrueRange", {"period": 14}, "native", source="hl2"),
                IndicatorEntry("SimpleMovingAverage", {"period": 20}, "native", source="vwap"),
                IndicatorEntry("SimpleMovingAverage", {"period": 9}, "native", source="hl2"),
            ]
        },
        Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH),
    )

    with caplog.at_level("WARNING", logger="data_api.routes.indicators"):
        got = client.get(f"/api/coin/{_IID}/indicators").json()

    assert [e["source"] for e in got] == ["close", "close", "hl2"]
    assert len(caplog.records) == 2


def test_values_route_refuses_an_unselectable_source_with_422(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    spec = json.dumps([{"name": "AverageTrueRange", "params": {"period": 2}, "source": "hl2"}])
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={"before_ns": _BASE_NS, "limit": 5, "bar_seconds": 60, "entries": spec},
    )
    assert response.status_code == 422
    assert "source" in response.json()["detail"]


def test_values_route_serves_one_series_per_source_keyed_by_the_source_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "cat")
    _write_snapshots(catalog_path, [(_BASE_NS + i * 60_000_000_000, 100.0 + i) for i in range(4)])
    client = _client(tmp_path, monkeypatch, catalog_path=catalog_path)
    spec = json.dumps(
        [
            {"name": "SimpleMovingAverage", "params": {"period": 2}},
            {"name": "SimpleMovingAverage", "params": {"period": 2}, "source": "hl2"},
        ]
    )
    response = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={
            "before_ns": _BASE_NS + 5 * 60_000_000_000,
            "limit": 10,
            "bar_seconds": 60,
            "entries": spec,
        },
    )
    assert response.status_code == 200
    keys = {k for item in response.json()["items"] for k in item["values"]}
    assert keys == {
        "SimpleMovingAverage_period=2.value",
        "SimpleMovingAverage_period=2:hl2.value",
    }


@pytest.mark.parametrize(
    "entry",
    [
        {"name": "SimpleMovingAverage", "params": {"period": 0}},
        {"name": "CandlePattern", "params": {"pattern": "hammer"}},
        {"name": "BollingerBands", "params": {"ma_type": "ADAPTIVE"}},
        {"name": "SimpleMovingAverage", "params": {"perod": 20}},
        {"name": "HullMovingAverage", "params": {"period": 10**9}},
        {"name": "OrderFlowImbalance", "params": {"window": 0}, "category": "custom"},
        {"name": "SimpleMovingAverage", "params": []},
    ],
)
def test_put_with_params_the_indicator_refuses_is_400_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: dict[str, Any]
) -> None:
    client = _client(tmp_path, monkeypatch)
    saved = [{"name": "RelativeStrengthIndex", "params": {"period": 14}, "category": "native"}]
    assert client.put(f"/api/coin/{_IID}/indicators", json=saved).status_code == 200
    before = Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH).read_bytes()

    response = client.put(f"/api/coin/{_IID}/indicators", json=[{"category": "native", **entry}])

    assert response.status_code == 400
    assert Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH).read_bytes() == before


def test_put_naming_the_indicator_and_param_it_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = [{"name": "SimpleMovingAverage", "params": {"period": 0}, "category": "native"}]

    detail = client.put(f"/api/coin/{_IID}/indicators", json=payload).json()["detail"]

    assert "SimpleMovingAverage" in detail
    assert "period" in detail


def test_put_with_on_balance_volume_default_period_zero_is_saved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    payload = [{"name": "OnBalanceVolume", "params": {"period": 0}, "category": "native"}]

    assert client.put(f"/api/coin/{_IID}/indicators", json=payload).status_code == 200
    assert client.get(f"/api/coin/{_IID}/indicators").json()[0]["params"] == {"period": 0}


def test_put_over_a_corrupt_stored_file_is_500_not_blamed_on_the_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    Path(indicators_routes.CHART_INDICATOR_CONFIG_PATH).write_text("not [ valid toml")
    payload = [{"name": "SimpleMovingAverage", "params": {}, "category": "native"}]

    response = client.put(f"/api/coin/{_IID}/indicators", json=payload)

    assert response.status_code == 500
    assert "chart_indicators.toml is corrupt" in response.json()["detail"]


def test_put_over_an_unreadable_stored_file_is_500(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    def _unreadable(path: Path) -> dict[str, list[IndicatorEntry]]:
        raise PermissionError(f"permission denied: {path}")

    monkeypatch.setattr(indicators_routes.preferences, "load_chart_indicators", _unreadable)
    payload = [{"name": "SimpleMovingAverage", "params": {}, "category": "native"}]

    response = client.put(f"/api/coin/{_IID}/indicators", json=payload)

    assert response.status_code == 500
    assert "failed to read chart_indicators.toml" in response.json()["detail"]
