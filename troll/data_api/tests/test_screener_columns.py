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
"""Story 17.5: screener-wide Technicals column persistence + bulk latest-indicator-values route.

Real TOML file, real `ParquetDataCatalog`/`DydxSecondSnapshot`, real indicator replay (no mocking,
troll/CLAUDE.md TEST-03). The values test cross-checks against the chart's own per-coin
indicator-values route for the same instrument/params: same computation, different destination.
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.routes.candles as candles_routes
import data_api.routes.indicators as indicators_routes
import data_api.routes.rankings as rankings_routes
from data_api import redis_bus
from data_api.redis_bus import RankingsBus
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"
_MINUTE_NS = 60_000_000_000


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    catalog = str(tmp_path / "cat")
    monkeypatch.setattr(rankings_routes, "SCREENER_COLUMNS_CONFIG_PATH", str(tmp_path / "cols.toml"))
    monkeypatch.setattr(rankings_routes, "CATALOG_PATH", catalog)
    rankings_routes._technicals_cache.clear()
    monkeypatch.setattr(candles_routes, "CATALOG_PATH", catalog)  # indicator-values reads candles via this route
    monkeypatch.setattr(candles_routes, "CANDLES_DB_PATH", f"{catalog}-no-candle-store.db")
    monkeypatch.setattr(rankings_routes, "CANDLES_DB_PATH", f"{catalog}-no-candle-store.db")
    return TestClient(app_module.app)


def _seed_recent_minutes(tmp_path: Path, count: int = 45, ended_minutes_ago: int = 0) -> None:
    """One trade-carrying snapshot per minute ending just before now -> `count` 1m candles."""
    end = time.time_ns() // _MINUTE_NS * _MINUTE_NS - ended_minutes_ago * _MINUTE_NS
    ParquetDataCatalog(str(tmp_path / "cat")).write_data([
        DydxSecondSnapshot(
            instrument_id=InstrumentId.from_str(_IID),
            bid_prices=[100.0], bid_sizes=[1.0], ask_prices=[101.0], ask_sizes=[1.0],
            buy_volume=1.0, sell_volume=0.5, buy_count=1, sell_count=1,
            open_price=price, high_price=price + 0.5, low_price=price - 0.5, close_price=price,
            ts_event=ts, ts_init=ts,
        )
        for i in range(count)
        for ts, price in [(end - (count - i) * _MINUTE_NS, 100.0 + (i * 7) % 13 + i * 0.1)]
    ])


def test_columns_default_to_empty_and_roundtrip_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/rankings/technicals-columns").json() == []

    cols = [
        {"name": "RelativeStrengthIndex", "params": {"period": 7}, "category": "native", "bar_seconds": 60},
        {"name": "MovingAverageConvergenceDivergence", "params": {}, "category": "native", "bar_seconds": 3600},
    ]
    assert client.put("/api/rankings/technicals-columns", json=cols).json() == {"ok": True}
    assert client.get("/api/rankings/technicals-columns").json() == cols

    assert client.put("/api/rankings/technicals-columns", json=cols[::-1]).status_code == 200
    assert [c["name"] for c in client.get("/api/rankings/technicals-columns").json()] == [
        "MovingAverageConvergenceDivergence", "RelativeStrengthIndex",
    ]


def test_put_rejects_malformed_columns_with_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.put("/api/rankings/technicals-columns", json=[{"params": {}}])
    assert response.status_code == 400


def test_columns_are_independent_of_per_coin_indicator_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(indicators_routes, "CHART_INDICATOR_CONFIG_PATH", str(tmp_path / "chart.toml"))
    client = _client(tmp_path, monkeypatch)
    client.put(f"/api/coin/{_IID}/indicators", json=[{"name": "AverageTrueRange", "category": "native"}])
    assert client.get("/api/rankings/technicals-columns").json() == []


def test_values_503_before_first_rankings_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(redis_bus, "bus", RankingsBus())
    client = _client(tmp_path, monkeypatch)
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60}])
    assert client.get("/api/rankings/technicals-values", params={"entries": entries}).status_code == 503


def test_values_match_the_chart_route_for_single_and_multi_value_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_recent_minutes(tmp_path)
    bus = RankingsBus()
    bus.latest = {"mode": "volume", "updated_at": 1, "ranks": [{"instrument_id": _IID}, {"instrument_id": "NEW-USD-PERP.DYDX"}]}
    monkeypatch.setattr(redis_bus, "bus", bus)
    client = _client(tmp_path, monkeypatch)
    entries = json.dumps([
        {"name": "RelativeStrengthIndex", "params": {"period": 14}, "bar_seconds": 60},
        {"name": "MovingAverageConvergenceDivergence", "params": {}, "bar_seconds": 60},
    ])

    got = client.get("/api/rankings/technicals-values", params={"entries": entries}).json()["values"]

    chart = client.get(
        f"/api/coin/{_IID}/indicator-values",
        params={"before_ns": time.time_ns() + 3_600 * 1_000_000_000, "entries": entries, "limit": 120},
    ).json()["items"]
    # Chart keys are "{indicator_id}.{attr}"; this route keys by entry position instead.
    expected = {}
    for key, value in chart[-1]["values"].items():
        attr = key.rsplit(".", 1)[1]
        index = 0 if key.startswith("RelativeStrengthIndex") else 1
        expected[f"{index}.{attr}"] = value
    assert got[_IID] == expected
    assert len(got[_IID]) > 1  # MACD's multiple outputs fan out beside RSI's single one
    assert got["NEW-USD-PERP.DYDX"] == {}  # no catalog data: honest empty, never a fabricated value


def test_values_reject_bad_entries_with_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bus = RankingsBus()
    bus.latest = {"mode": "volume", "updated_at": 1, "ranks": []}
    monkeypatch.setattr(redis_bus, "bus", bus)
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/rankings/technicals-values", params={"entries": "not json"}).status_code == 400


def _ranked(monkeypatch: pytest.MonkeyPatch, *iids: str) -> None:
    bus = RankingsBus()
    bus.latest = {"mode": "volume", "updated_at": 1, "ranks": [{"instrument_id": i} for i in iids]}
    monkeypatch.setattr(redis_bus, "bus", bus)


def test_values_omit_a_coin_whose_newest_candle_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_recent_minutes(tmp_path, ended_minutes_ago=30)  # data stopped half an hour ago
    _ranked(monkeypatch, _IID)
    client = _client(tmp_path, monkeypatch)
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60}])

    values = client.get("/api/rankings/technicals-values", params={"entries": entries}).json()["values"]

    assert values[_IID] == {}  # honest gap, not the 30-minute-old value shown as live


def test_put_rejects_an_unknown_indicator_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    response = client.put(
        "/api/rankings/technicals-columns", json=[{"name": "NoSuchIndicator", "params": {}, "category": "native"}],
    )
    assert response.status_code == 400
    assert client.get("/api/rankings/technicals-columns").json() == []


def test_one_coins_catalog_failure_does_not_blank_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_recent_minutes(tmp_path)
    _ranked(monkeypatch, _IID, "BAD-USD-PERP.DYDX")
    client = _client(tmp_path, monkeypatch)
    real = rankings_routes._latest_values

    def flaky(iid: str, entries: list, now_ns: int) -> dict:
        if iid.startswith("BAD"):
            raise OSError("corrupt partition")
        return real(iid, entries, now_ns)

    monkeypatch.setattr(rankings_routes, "_latest_values", flaky)
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60}])

    body = client.get("/api/rankings/technicals-values", params={"entries": entries}).json()

    assert "BAD-USD-PERP.DYDX" not in body["values"]  # no fabricated empty row...
    assert "corrupt partition" in body["errors"]["BAD-USD-PERP.DYDX"]  # ...the failure is reported
    assert body["values"][_IID]  # the healthy coin still has its value


def test_values_are_served_from_the_ttl_cache_on_a_repeat_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_recent_minutes(tmp_path)
    _ranked(monkeypatch, _IID)
    client = _client(tmp_path, monkeypatch)
    calls: list[str] = []
    real = rankings_routes._latest_values
    monkeypatch.setattr(
        rankings_routes, "_latest_values", lambda i, e, n: calls.append(i) or real(i, e, n),
    )
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60}])

    first = client.get("/api/rankings/technicals-values", params={"entries": entries}).json()
    second = client.get("/api/rankings/technicals-values", params={"entries": entries}).json()

    assert first == second
    assert calls == [_IID]  # second poll never touched the catalog


def test_a_valueerror_from_one_coins_catalog_read_does_not_become_a_whole_request_400(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pyarrow's ArrowInvalid subclasses ValueError -- a corrupt partition is not bad client input."""
    _seed_recent_minutes(tmp_path)
    _ranked(monkeypatch, _IID, "BAD-USD-PERP.DYDX")
    client = _client(tmp_path, monkeypatch)
    real = rankings_routes._catalog_stats.query_second_ohlc

    def read(path: str, iid: str, a: int, b: int) -> list:
        if iid.startswith("BAD"):
            raise ValueError("Arrow schema mismatch")
        return real(path, iid, a, b)

    monkeypatch.setattr(rankings_routes._catalog_stats, "query_second_ohlc", read)
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60}])

    response = client.get("/api/rankings/technicals-values", params={"entries": entries})

    assert response.status_code == 200
    assert "BAD-USD-PERP.DYDX" in response.json()["errors"]  # reported, not shown as "no data"
    assert response.json()["values"][_IID]


def test_cache_key_ignores_rank_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_recent_minutes(tmp_path)
    client = _client(tmp_path, monkeypatch)
    calls: list[str] = []
    real = rankings_routes._latest_values
    monkeypatch.setattr(rankings_routes, "_latest_values", lambda i, e, n: calls.append(i) or real(i, e, n))
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60}])

    _ranked(monkeypatch, _IID, "ZZZ-USD-PERP.DYDX")
    client.get("/api/rankings/technicals-values", params={"entries": entries})
    _ranked(monkeypatch, "ZZZ-USD-PERP.DYDX", _IID)  # rank order swapped
    client.get("/api/rankings/technicals-values", params={"entries": entries})

    assert len(calls) == 2  # only the first request hit the catalog


def test_put_rejects_non_object_params_and_too_many_columns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    bad = client.put(
        "/api/rankings/technicals-columns",
        json=[{"name": "AverageTrueRange", "params": "x", "category": "native"}],
    )
    assert bad.status_code == 400
    many = [{"name": "AverageTrueRange", "params": {}, "category": "native"}] * 51
    assert client.put("/api/rankings/technicals-columns", json=many).status_code == 400


def test_columns_default_to_hourly_and_reject_unknown_bar_sizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client(tmp_path, monkeypatch)
    bare = [{"name": "RelativeStrengthIndex", "params": {}, "category": "native"}]
    assert client.put("/api/rankings/technicals-columns", json=bare).status_code == 200
    assert client.get("/api/rankings/technicals-columns").json()[0]["bar_seconds"] == 3600
    bad = [{**bare[0], "bar_seconds": 7}]
    assert client.put("/api/rankings/technicals-columns", json=bad).status_code == 400


def test_each_column_is_computed_on_its_own_timeframe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed_recent_minutes(tmp_path)
    bus = RankingsBus()
    bus.latest = {"mode": "volume", "updated_at": 1, "ranks": [{"instrument_id": _IID}]}
    monkeypatch.setattr(redis_bus, "bus", bus)
    client = _client(tmp_path, monkeypatch)
    seen: list[int] = []
    monkeypatch.setattr(
        rankings_routes, "_recent_candles",
        lambda iid, bar_seconds, now_ns: seen.append(bar_seconds) or [],
    )
    entries = json.dumps([
        {"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60},
        {"name": "RelativeStrengthIndex", "params": {"period": 7}, "bar_seconds": 86400},
        {"name": "AverageTrueRange", "params": {}, "bar_seconds": 60},
    ])
    assert client.get("/api/rankings/technicals-values", params={"entries": entries}).status_code == 200
    assert seen == [60, 86400]  # one candle build per distinct bar size, not per entry
    bad = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 7}])
    assert client.get("/api/rankings/technicals-values", params={"entries": bad}).status_code == 400


def test_values_come_from_the_candle_store_without_touching_parquet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ml_signals import candle_store
    from ml_signals.tests.test_candle_store import _second

    _ranked(monkeypatch, _IID)
    client = _client(tmp_path, monkeypatch)  # no Parquet catalog exists at all
    db_path = str(tmp_path / "candles.db")
    monkeypatch.setattr(rankings_routes, "CANDLES_DB_PATH", db_path)
    db = candle_store.connect_rw(db_path)
    now_minute = int(time.time()) // 60 * 60 * 1000
    rows = []
    for i in range(45):  # 45 traded minutes ending now (the helper stamps a fixed day, so restamp)
        row = _second(0, 100.0 + (i * 7) % 13)
        row.ts_event = (now_minute - (45 - i) * 60_000) * 1_000_000
        rows.append(row)
    candle_store.apply_seconds(db, _IID, rows)
    entries = json.dumps([{"name": "RelativeStrengthIndex", "params": {}, "bar_seconds": 60}])

    got = client.get("/api/rankings/technicals-values", params={"entries": entries}).json()

    assert got["errors"] == {}
    assert got["values"][_IID]["0.value"] is not None
