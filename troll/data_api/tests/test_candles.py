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
"""Story 15.3: `GET /api/candles/{instrument_id}` -- real ParquetDataCatalog, real DydxSecondSnapshot."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.routes.candles as candles_routes
from dydx_collector.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"

# Large, arbitrary, far-from-epoch base timestamp -- a multiple of 60s in ns, so bucket
# arithmetic in the tests below lines up cleanly with 60s candle boundaries. Keeps every
# test from accidentally tripping a timestamp-smallness edge case rather than a real one.
_BASE_NS = 1_800_000_000_000_000_000
assert _BASE_NS % 60_000_000_000 == 0


def _client(catalog_path: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(candles_routes, "CATALOG_PATH", catalog_path)
    return TestClient(app_module.app)


def _write_snapshots(catalog_path: str, entries: list[tuple[int, float]]) -> None:
    """`entries` is a list of (ts_ns, price) -- each becomes a one-second snapshot with
    open=high=low=close=price, written in one `write_data()` call for speed."""
    entries = sorted(entries, key=lambda e: e[0])
    ParquetDataCatalog(catalog_path).write_data([
        DydxSecondSnapshot(
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
    ])


def test_pagination_two_sequential_pages_are_strictly_older_and_disjoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    # 5 one-minute-apart snapshots -> 5 distinct 60s candles.
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    first = client.get(f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=3&bar_seconds=60")
    assert first.status_code == 200
    first_items = first.json()["items"]
    assert len(first_items) == 3
    assert all(item["t"] < (_BASE_NS + 60_000_000_000) // 1_000_000 for item in first_items)

    earliest_first_ns = first_items[0]["t"] * 1_000_000
    second = client.get(f"/api/candles/{_IID}?before_ns={earliest_first_ns}&limit=3&bar_seconds=60")
    assert second.status_code == 200
    second_items = second.json()["items"]
    assert len(second_items) == 2  # only 2 candles remain strictly older
    assert all(item["t"] < earliest_first_ns // 1_000_000 for item in second_items)
    assert {i["t"] for i in second_items}.isdisjoint({i["t"] for i in first_items})


def test_has_more_false_at_true_history_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0) for i in range(3)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=10&bar_seconds=60")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["has_more"] is False


def test_short_page_with_has_more_true_is_legal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    A page shorter than `limit` must not be misread as history exhaustion (AD-F3) --
    constructed so the probe query genuinely extends coverage into a second snapshot
    that lies outside the main query window, never by re-finding the main window's own
    in-page candle (the off-by-one this route's probe bound specifically guards against).
    """
    catalog_path = str(tmp_path / "catalog")
    before_ns = _BASE_NS
    # limit=2, bar_seconds=60 -> main window span = 2*60*3 = 360s; probe window is the
    # same 360s span immediately preceding the main window.
    in_main_window_ns = before_ns - 5_000_000_000  # 5s before `before_ns`
    in_probe_window_only_ns = before_ns - 390_000_000_000  # outside main window, inside probe window
    _write_snapshots(catalog_path, [(in_main_window_ns, 100.0), (in_probe_window_only_ns, 90.0)])
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/candles/{_IID}?before_ns={before_ns}&limit=2&bar_seconds=60")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1  # short page: only 1 candle in the main window, limit was 2
    assert body["has_more"] is True


def test_gap_marker_inserted_between_candles_separated_by_more_than_one_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    earlier_ns = _BASE_NS - 300_000_000_000  # 5 minutes before base
    later_ns = _BASE_NS - 100_000_000_000  # 200s later -- exceeds the 60s bar interval
    _write_snapshots(catalog_path, [(earlier_ns, 100.0), (later_ns, 105.0)])
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/candles/{_IID}?before_ns={_BASE_NS}&limit=10&bar_seconds=60")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3  # real candle, gap marker, real candle
    real_first, gap, real_second = items
    assert real_first["c"] is not None
    assert real_second["c"] is not None
    assert gap["t"] == real_first["t"] + 60_000
    assert gap["o"] is None
    assert gap["h"] is None
    assert gap["l"] is None
    assert gap["c"] is None
    assert gap["v"] is None


def test_limit_far_above_max_never_returns_more_than_max_candles_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    max_limit = candles_routes._MAX_CANDLES_LIMIT
    # One more real candle than the server-enforced max, all one minute apart, well
    # within the clamped query window (25h span for limit=500/bar_seconds=60).
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0) for i in range(max_limit + 1)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(
        f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=100000&bar_seconds=60",
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == max_limit


def test_limit_zero_or_negative_is_clamped_up_to_one_not_treated_as_unbounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python's `list[-0:]` equals `list[0:]` (the whole list) -- `limit=0` must not fall
    through to that footgun and return more than one candle."""
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    for limit in (0, -5):
        response = client.get(
            f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit={limit}&bar_seconds=60",
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1


def test_bar_seconds_zero_or_negative_is_clamped_up_to_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`bar_seconds=0` would otherwise zero-divide inside `candle_dicts_from_snapshots`'
    bucketing; clamped to 1 (a legal, if unusual, 1-second bar) instead of erroring."""
    catalog_path = str(tmp_path / "catalog")
    _write_snapshots(catalog_path, [(_BASE_NS - 5_000_000_000, 100.0)])
    client = _client(catalog_path, monkeypatch)

    for bar_seconds in (0, -60):
        response = client.get(
            f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=10&bar_seconds={bar_seconds}",
        )
        assert response.status_code == 200


def test_large_limit_and_bar_seconds_combination_does_not_blow_the_query_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`limit=500` * `bar_seconds=86400` * `_QUERY_WINDOW_MULTIPLIER=3` would otherwise
    span ~4 years of raw snapshots in one query (MEM-01) -- `_MAX_QUERY_SPAN_SECONDS`
    caps it regardless of the individually-clamped `limit`/`bar_seconds` product. A
    snapshot placed just past the cap must be excluded from the main query window."""
    catalog_path = str(tmp_path / "catalog")
    cap_seconds = candles_routes._MAX_QUERY_SPAN_SECONDS
    within_cap_ns = _BASE_NS - (cap_seconds - 60) * 1_000_000_000
    past_cap_ns = _BASE_NS - (cap_seconds + 60) * 1_000_000_000
    _write_snapshots(catalog_path, [(within_cap_ns, 100.0), (past_cap_ns, 90.0)])
    client = _client(catalog_path, monkeypatch)

    response = client.get(
        f"/api/candles/{_IID}?before_ns={_BASE_NS}&limit={candles_routes._MAX_CANDLES_LIMIT}"
        f"&bar_seconds={candles_routes._MAX_BAR_SECONDS}",
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1  # only the within-cap snapshot's candle is queried at all
