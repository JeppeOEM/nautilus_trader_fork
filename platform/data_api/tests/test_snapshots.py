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
"""Story 15.7: `GET /api/snapshots/{instrument_id}` -- real ParquetDataCatalog, real DydxSecondSnapshot."""

from pathlib import Path

import pytest
from collector_core.second_snapshot import DydxSecondSnapshot
from fastapi.testclient import TestClient

import data_api.app as app_module
import data_api.routes.snapshots as snapshots_routes
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTC-USD-PERP.DYDX"

# Large, arbitrary, far-from-epoch base timestamp -- a multiple of 1s in ns (trivially true
# for any integer ns timestamp), keeps every test from accidentally tripping a
# timestamp-smallness edge case rather than a real one.
_BASE_NS = 1_800_000_000_000_000_000


def _client(catalog_path: str, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(snapshots_routes, "CATALOG_PATH", catalog_path)
    return TestClient(app_module.app)


def _write_snapshots(
    catalog_path: str,
    entries: list[tuple[int, float, float]],
    buy_volume: float = 1.0,
    sell_volume: float = 0.5,
) -> None:
    """
    `entries` is a list of (ts_ns, bid_price, ask_price) -- each becomes a one-second
    snapshot, written in one `write_data()` call for speed.
    """
    entries = sorted(entries, key=lambda e: e[0])
    ParquetDataCatalog(catalog_path).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(_IID),
                bid_prices=[bid],
                bid_sizes=[1.0],
                ask_prices=[ask],
                ask_sizes=[1.0],
                buy_volume=buy_volume,
                sell_volume=sell_volume,
                buy_count=1,
                sell_count=1,
                open_price=bid,
                high_price=ask,
                low_price=bid,
                close_price=bid,
                ts_event=ts,
                ts_init=ts,
            )
            for ts, bid, ask in entries
        ]
    )


def test_pagination_two_sequential_pages_are_strictly_older_and_disjoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    # 5 one-second-apart snapshots -> 5 rows.
    entries = [(_BASE_NS - i * 1_000_000_000, 100.0 + i, 101.0 + i) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    first = client.get(f"/api/snapshots/{_IID}?before_ns={_BASE_NS + 1_000_000_000}&limit=3")
    assert first.status_code == 200
    first_items = first.json()["items"]
    assert len(first_items) == 3
    assert all(item["t"] < (_BASE_NS + 1_000_000_000) // 1_000_000 for item in first_items)

    earliest_first_ns = first_items[0]["t"] * 1_000_000
    second = client.get(f"/api/snapshots/{_IID}?before_ns={earliest_first_ns}&limit=3")
    assert second.status_code == 200
    second_items = second.json()["items"]
    assert len(second_items) == 2  # only 2 rows remain strictly older
    assert all(item["t"] < earliest_first_ns // 1_000_000 for item in second_items)
    assert {i["t"] for i in second_items}.isdisjoint({i["t"] for i in first_items})


def test_has_more_false_at_true_history_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 1_000_000_000, 100.0, 101.0) for i in range(3)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/snapshots/{_IID}?before_ns={_BASE_NS + 1_000_000_000}&limit=10")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["has_more"] is False


def test_short_page_with_has_more_true_is_legal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    A page shorter than `limit` must not be misread as history exhaustion (AD-F3) --
    constructed so the probe query genuinely extends coverage into a second snapshot that
    lies outside the main query window, never by re-finding the main window's own in-page
    row (the off-by-one this route's probe bound specifically guards against).
    """
    catalog_path = str(tmp_path / "catalog")
    before_ns = _BASE_NS
    # limit=2 -> main window span = 2 * _QUERY_WINDOW_MULTIPLIER(2) = 4s; probe window is
    # the same 4s span immediately preceding the main window.
    in_main_window_ns = before_ns - 1_000_000_000  # 1s before `before_ns`
    # Probe window = the same 4s span immediately preceding the main query's earliest
    # actual row (not the window's own edge -- the probe is anchored on `earliest_kept_ns`,
    # see `_has_more`'s docstring), i.e. roughly [before_ns-5s, before_ns-1s). 4.5s before
    # `before_ns` lands inside that range while sitting outside the main window
    # ([before_ns-4s, before_ns]).
    in_probe_window_only_ns = before_ns - 4_500_000_000
    _write_snapshots(
        catalog_path, [(in_main_window_ns, 100.0, 101.0), (in_probe_window_only_ns, 90.0, 91.0)]
    )
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/snapshots/{_IID}?before_ns={before_ns}&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1  # short page: only 1 row in the main window, limit was 2
    assert body["has_more"] is True


def test_gap_marker_inserted_between_rows_separated_by_more_than_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    # limit=10 -> main query window span = 10 * _QUERY_WINDOW_MULTIPLIER(2) = 20s -- both
    # rows below must sit inside that window to be queried at all.
    earlier_ns = _BASE_NS - 10_000_000_000  # 10s before base
    later_ns = earlier_ns + 3_000_000_000  # 3s later -- exceeds the 2.5s gap threshold
    _write_snapshots(catalog_path, [(earlier_ns, 100.0, 101.0), (later_ns, 105.0, 106.0)])
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/snapshots/{_IID}?before_ns={_BASE_NS}&limit=10")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 3  # real row, gap marker, real row
    real_first, gap, real_second = items
    assert real_first["bid"] is not None
    assert real_second["bid"] is not None
    assert gap["t"] == real_second["t"] - 1
    assert gap["bid"] is None
    assert gap["ask"] is None
    assert gap["mid"] is None
    assert gap["micro"] is None
    assert gap["price"] is None


def test_crossed_book_row_is_skipped_and_not_counted_toward_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A crossed/touched snapshot (`bid_prices[0] >= ask_prices[0]`, stale reconnect data,
    DATA-04) is silently dropped -- it must not consume any of the requested `limit` budget,
    same discipline as `_price_series_rows`' original (`ml_signals/dashboard.py`).
    """
    catalog_path = str(tmp_path / "catalog")
    crossed_ns = _BASE_NS - 3_000_000_000
    healthy_ns = _BASE_NS - 2_000_000_000
    _write_snapshots(catalog_path, [(healthy_ns, 100.0, 101.0)])
    # Write the crossed row directly (helper always writes bid < ask).
    ParquetDataCatalog(catalog_path).write_data(
        [
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(_IID),
                bid_prices=[105.0],
                bid_sizes=[1.0],
                ask_prices=[100.0],  # crossed: bid >= ask
                ask_sizes=[1.0],
                buy_volume=1.0,
                sell_volume=0.5,
                buy_count=1,
                sell_count=1,
                ts_event=crossed_ns,
                ts_init=crossed_ns,
            ),
        ]
    )
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/snapshots/{_IID}?before_ns={_BASE_NS}&limit=10")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1  # only the healthy row -- the crossed row never appears
    assert items[0]["bid"] == 100.0


def test_limit_far_above_max_never_returns_more_than_max_snapshots_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    max_limit = snapshots_routes._MAX_SNAPSHOTS_LIMIT
    # A handful more real rows than the server-enforced max, all one second apart -- keep
    # this small enough to run fast (the route's own query window/limit clamp is what's
    # under test, not raw row-count scaling).
    count = max_limit + 5
    entries = [(_BASE_NS - i * 1_000_000_000, 100.0, 101.0) for i in range(count)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(
        f"/api/snapshots/{_IID}?before_ns={_BASE_NS + 1_000_000_000}&limit=100000000"
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == max_limit


def test_limit_zero_or_negative_is_clamped_up_to_one_not_treated_as_unbounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Python's `list[-0:]` equals `list[0:]` (the whole list) -- `limit=0` must not fall
    through to that footgun and return more than one row.
    """
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 1_000_000_000, 100.0, 101.0) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    for limit in (0, -5):
        response = client.get(
            f"/api/snapshots/{_IID}?before_ns={_BASE_NS + 1_000_000_000}&limit={limit}"
        )
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1


def test_paging_reaches_data_beyond_a_gap_wider_than_the_query_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshots(
        catalog_path,
        [
            (_BASE_NS - 1_000_000_000, 100.0, 101.0),
            (_BASE_NS - 3_600_000_000_000, 90.0, 91.0),  # 1h back; the query window is only seconds
        ],
    )
    client = _client(catalog_path, monkeypatch)

    first = client.get(f"/api/snapshots/{_IID}?before_ns={_BASE_NS}&limit=2").json()
    second = client.get(
        f"/api/snapshots/{_IID}?before_ns={first['items'][0]['t'] * 1_000_000}&limit=2"
    ).json()

    assert first["has_more"] is True
    assert [i["t"] for i in second["items"]] == [(_BASE_NS - 3_600_000_000_000) // 1_000_000]
    assert second["has_more"] is False


@pytest.mark.parametrize(
    ("iid", "market"), [("BTCUSDT-SPOT.BYBIT", "spot"), ("BTCUSDT-LINEAR.BYBIT", "perp")]
)
def test_market_field_next_to_venue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    iid: str,
    market: str,
) -> None:
    body = (
        _client(str(tmp_path / "cat"), monkeypatch)
        .get(
            f"/api/snapshots/{iid}?before_ns={_BASE_NS}&limit=3",
        )
        .json()
    )
    assert (body["venue"], body["market"]) == ("BYBIT", market)
