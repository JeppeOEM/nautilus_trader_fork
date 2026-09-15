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
"""Story 15.4: `GET /api/indicator-series/{instrument_id}` -- real ParquetDataCatalog, real
DydxSecondSnapshot, mirrors test_candles.py's fixture pattern."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.routes.indicator_series as indicator_series_routes
from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI
from ml_signals.indicators import microprice as _microprice
from ml_signals.indicators import spread as _spread
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"

# Same base timestamp convention as test_candles.py -- a multiple of 60s in ns.
_BASE_NS = 1_800_000_000_000_000_000
assert _BASE_NS % 60_000_000_000 == 0


def _client(catalog_path: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(indicator_series_routes, "CATALOG_PATH", catalog_path)
    return TestClient(app_module.app)


def _url(before_ns: int, limit: int, bar_seconds: int) -> str:
    query = f"before_ns={before_ns}&limit={limit}&bar_seconds={bar_seconds}"
    return f"/api/indicator-series/{_IID}?{query}"


def _snapshot(
    ts: int,
    bid_prices: list[float],
    bid_sizes: list[float],
    ask_prices: list[float],
    ask_sizes: list[float],
) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=bid_prices,
        bid_sizes=bid_sizes,
        ask_prices=ask_prices,
        ask_sizes=ask_sizes,
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        open_price=bid_prices[0] if bid_prices else None,
        high_price=bid_prices[0] if bid_prices else None,
        low_price=bid_prices[0] if bid_prices else None,
        close_price=bid_prices[0] if bid_prices else None,
        ts_event=ts,
        ts_init=ts,
    )


def _write_book_snapshots(catalog_path: str, entries: list[tuple[int, float]]) -> None:
    """`entries` is a list of (ts_ns, bid_price) -- each becomes a one-second snapshot
    with a simple synthetic two-level book, one second apart, so OFI/OBI have real
    consecutive-tick deltas to replay."""
    entries = sorted(entries, key=lambda e: e[0])
    snapshots = [
        _snapshot(
            ts,
            bid_prices=[bid, bid - 1.0],
            bid_sizes=[10.0 + i, 5.0],
            ask_prices=[bid + 1.0, bid + 2.0],
            ask_sizes=[8.0, 4.0 + i],
        )
        for i, (ts, bid) in enumerate(entries)
    ]
    ParquetDataCatalog(catalog_path).write_data(list(snapshots))


def test_values_match_calling_indicators_directly_on_the_same_input_in_the_same_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing test for AC #8: replays the exact same snapshots, in the exact
    same chronological order, through freshly-constructed `MultiLevelOFI`/`MultiLevelOBI`
    instances and the stateless `microprice`/`spread` functions -- if the route ever
    reimplements the math instead of calling these functions, this diverges."""
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 1_000_000_000, 100.0 + i) for i in range(10)]
    _write_book_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(_url(_BASE_NS + 1_000_000_000, limit=20, bar_seconds=1))
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 10

    ofi = MultiLevelOFI(levels=10, window=50)
    obi = MultiLevelOBI(levels=10)
    for i, (ts, expected) in enumerate(sorted(entries, key=lambda e: e[0])):
        snapshot = _snapshot(
            ts,
            bid_prices=[expected, expected - 1.0],
            bid_sizes=[10.0 + i, 5.0],
            ask_prices=[expected + 1.0, expected + 2.0],
            ask_sizes=[8.0, 4.0 + i],
        )
        ofi.update_raw(
            snapshot.bid_prices, snapshot.bid_sizes, snapshot.ask_prices, snapshot.ask_sizes,
        )
        obi.update_raw(snapshot.bid_sizes, snapshot.ask_sizes)
        snapshot_dict = {
            "bid_prices": snapshot.bid_prices,
            "bid_sizes": snapshot.bid_sizes,
            "ask_prices": snapshot.ask_prices,
            "ask_sizes": snapshot.ask_sizes,
        }
        item = items[i]
        assert item["obi"] == pytest.approx(obi.value)
        assert item["microprice"] == pytest.approx(_microprice(snapshot_dict))
        assert item["spread"] == pytest.approx(_spread(snapshot_dict))
        if ofi.initialized:
            assert item["ofi"] == pytest.approx(ofi.value)
        else:
            assert item["ofi"] is None


def test_first_bucket_of_a_page_has_null_ofi_but_populated_obi_microprice_spread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 1_000_000_000, 100.0 + i) for i in range(3)]
    _write_book_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(_url(_BASE_NS + 1_000_000_000, limit=10, bar_seconds=1))
    assert response.status_code == 200
    items = response.json()["items"]
    assert items[0]["ofi"] is None
    assert items[0]["obi"] is not None
    assert items[0]["microprice"] is not None
    assert items[0]["spread"] is not None


def test_pagination_two_sequential_pages_are_strictly_older_and_disjoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(5)]
    _write_book_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    first = client.get(_url(_BASE_NS + 60_000_000_000, limit=3, bar_seconds=60))
    assert first.status_code == 200
    first_items = first.json()["items"]
    assert len(first_items) == 3
    assert all(item["t"] < (_BASE_NS + 60_000_000_000) // 1_000_000 for item in first_items)

    earliest_first_ns = first_items[0]["t"] * 1_000_000
    second = client.get(_url(earliest_first_ns, limit=3, bar_seconds=60))
    assert second.status_code == 200
    second_items = second.json()["items"]
    assert len(second_items) == 2
    assert {i["t"] for i in second_items}.isdisjoint({i["t"] for i in first_items})


def test_has_more_false_at_true_history_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0) for i in range(3)]
    _write_book_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(_url(_BASE_NS + 60_000_000_000, limit=10, bar_seconds=60))
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["has_more"] is False


def test_short_page_with_has_more_true_is_legal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    before_ns = _BASE_NS
    in_main_window_ns = before_ns - 5_000_000_000
    # `_has_more`'s probe window is `_window_start_ns(earliest_kept_ns - 1, limit=2,
    # bar_seconds=60)` = `earliest_kept_ns - 1 - min(2*60*3, 604_800)s` = ~360s back from
    # `in_main_window_ns` -- 390s puts this snapshot just inside that probe window but
    # outside the main query window (`min(2*60*3, 604_800)s` = 360s back from `before_ns`
    # itself), so it's probe-visible-only by construction, not an arbitrary offset.
    in_probe_window_only_ns = before_ns - 390_000_000_000
    _write_book_snapshots(
        catalog_path, [(in_main_window_ns, 100.0), (in_probe_window_only_ns, 90.0)],
    )
    client = _client(catalog_path, monkeypatch)

    response = client.get(_url(before_ns, limit=2, bar_seconds=60))
    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["has_more"] is True


def test_gap_marker_inserted_between_points_separated_by_more_than_one_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    earlier_ns = _BASE_NS - 300_000_000_000
    later_ns = _BASE_NS - 100_000_000_000
    _write_book_snapshots(catalog_path, [(earlier_ns, 100.0), (later_ns, 105.0)])
    client = _client(catalog_path, monkeypatch)

    response = client.get(
        f"/api/indicator-series/{_IID}?before_ns={_BASE_NS}&limit=10&bar_seconds=60",
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3
    real_first, gap, real_second = items
    assert real_first["obi"] is not None
    assert real_second["obi"] is not None
    assert gap["t"] == real_first["t"] + 60_000
    assert gap["ofi"] is None
    assert gap["obi"] is None
    assert gap["microprice"] is None
    assert gap["spread"] is None


def test_limit_far_above_max_never_returns_more_than_max_indicator_series_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    max_limit = indicator_series_routes._MAX_INDICATOR_SERIES_LIMIT
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0) for i in range(max_limit + 1)]
    _write_book_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(_url(_BASE_NS + 60_000_000_000, limit=100_000, bar_seconds=60))
    assert response.status_code == 200
    assert len(response.json()["items"]) == max_limit


def test_thin_book_snapshot_yields_null_microprice_and_spread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    snapshot = DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(_IID),
        bid_prices=[],
        bid_sizes=[],
        ask_prices=[],
        ask_sizes=[],
        buy_volume=0.0,
        sell_volume=0.0,
        buy_count=0,
        sell_count=0,
        ts_event=_BASE_NS - 5_000_000_000,
        ts_init=_BASE_NS - 5_000_000_000,
    )
    ParquetDataCatalog(catalog_path).write_data([snapshot])
    client = _client(catalog_path, monkeypatch)

    response = client.get(_url(_BASE_NS + 60_000_000_000, limit=10, bar_seconds=60))
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["microprice"] is None
    assert items[0]["spread"] is None
