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
"""Unit tests for metrics_store: write/latest/history roundtrip and pruning."""

import tempfile
import time

import ml_signals.metrics_store as store


_NOW = time.time_ns()
_DAY_NS = 86_400 * 1_000_000_000


def _path() -> str:
    return tempfile.mktemp(suffix=".db")


def _row(ts: int, instrument_id: str = "BTC-USD-PERP.DYDX", **kwargs) -> dict:
    base = {
        "ts": ts, "instrument_id": instrument_id,
        "price": None, "pct_1h": None, "pct_24h": None, "volatility": None,
        "ofi": None, "microprice": None, "spread": None,
    }
    return {**base, **kwargs}


def test_write_and_latest_roundtrip() -> None:
    path = _path()
    store.write([_row(_NOW, price=42.0)], path)
    rows = store.latest(path)
    assert len(rows) == 1
    assert rows[0]["instrument_id"] == "BTC-USD-PERP.DYDX"
    assert rows[0]["price"] == 42.0


def test_all_metric_columns_persisted() -> None:
    path = _path()
    store.write([_row(_NOW, price=1.0, pct_1h=2.0, pct_24h=3.0, volatility=4.0, ofi=5.0, microprice=6.0, spread=7.0)], path)
    rows = store.latest(path)
    r = rows[0]
    assert r["price"] == 1.0
    assert r["ofi"] == 5.0
    assert r["spread"] == 7.0


def test_latest_returns_most_recent_per_instrument() -> None:
    path = _path()
    store.write([_row(_NOW - 1000, price=1.0), _row(_NOW, price=2.0)], path)
    rows = store.latest(path)
    assert len(rows) == 1
    assert rows[0]["price"] == 2.0


def test_latest_multiple_instruments() -> None:
    path = _path()
    store.write([
        _row(_NOW, "BTC-USD-PERP.DYDX", price=10.0),
        _row(_NOW, "ETH-USD-PERP.DYDX", price=20.0),
    ], path)
    by_iid = {r["instrument_id"]: r for r in store.latest(path)}
    assert by_iid["BTC-USD-PERP.DYDX"]["price"] == 10.0
    assert by_iid["ETH-USD-PERP.DYDX"]["price"] == 20.0


def test_history_filters_by_days() -> None:
    path = _path()
    old = _NOW - 40 * _DAY_NS   # 40 days ago — outside the 31-day window
    store.write([_row(old, price=0.0), _row(_NOW, price=99.0)], path)
    rows = store.history("BTC-USD-PERP.DYDX", path, days=31)
    assert len(rows) == 1
    assert rows[0]["price"] == 99.0


def test_history_ordered_by_ts() -> None:
    path = _path()
    store.write([_row(_NOW, price=2.0), _row(_NOW - 1000, price=1.0)], path)
    rows = store.history("BTC-USD-PERP.DYDX", path, days=1)
    assert rows[0]["ts"] < rows[1]["ts"]


def test_history_includes_ts_column() -> None:
    path = _path()
    store.write([_row(_NOW, price=1.0)], path)
    rows = store.history("BTC-USD-PERP.DYDX", path, days=1)
    assert "ts" in rows[0]
    assert rows[0]["ts"] == _NOW


def test_write_prunes_rows_older_than_retain_days() -> None:
    path = _path()
    ancient = _NOW - 40 * _DAY_NS
    store.write([_row(ancient, price=0.0)], path, retain_days=31)
    store.write([_row(_NOW, price=1.0)], path, retain_days=31)
    rows = store.latest(path)
    assert len(rows) == 1
    assert rows[0]["price"] == 1.0


def test_upsert_replaces_same_ts_and_instrument() -> None:
    path = _path()
    store.write([_row(_NOW, price=1.0)], path)
    store.write([_row(_NOW, price=2.0)], path)   # same primary key → replace
    rows = store.latest(path)
    assert len(rows) == 1
    assert rows[0]["price"] == 2.0


def test_write_empty_list_is_noop() -> None:
    path = _path()
    store.write([], path)
    assert store.latest(path) == []


if __name__ == "__main__":
    test_write_and_latest_roundtrip()
    test_all_metric_columns_persisted()
    test_latest_returns_most_recent_per_instrument()
    test_latest_multiple_instruments()
    test_history_filters_by_days()
    test_history_ordered_by_ts()
    test_history_includes_ts_column()
    test_write_prunes_rows_older_than_retain_days()
    test_upsert_replaces_same_ts_and_instrument()
    test_write_empty_list_is_noop()
    print("ok")
