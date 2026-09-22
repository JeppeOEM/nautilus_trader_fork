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
from collector_core.second_snapshot import DydxSecondSnapshot
from fastapi.testclient import TestClient
from ml_signals import candle_store
from ml_signals.tests.test_candle_store import _DAY0_MS
from ml_signals.tests.test_candle_store import _second

import data_api.app as app_module
import data_api.routes.candles as candles_routes
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
    # No candle store unless a test builds one: these tests exercise the Parquet path.
    monkeypatch.setattr(candles_routes, "CANDLES_DB_DIR", f"{catalog_path}-no-candle-store-dir")
    return TestClient(app_module.app)


def _write_snapshots(catalog_path: str, entries: list[tuple[int, float]]) -> None:
    """
    `entries` is a list of (ts_ns, price) -- each becomes a one-second snapshot with
    open=high=low=close=price, written in one `write_data()` call for speed.
    """
    entries = sorted(entries, key=lambda e: e[0])
    ParquetDataCatalog(catalog_path).write_data(
        [
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
        ]
    )


def test_pagination_two_sequential_pages_are_strictly_older_and_disjoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    catalog_path = str(tmp_path / "catalog")
    # 5 one-minute-apart snapshots -> 5 distinct 60s candles.
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(5)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    first = client.get(
        f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=3&bar_seconds=60"
    )
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


def test_has_more_false_at_true_history_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = str(tmp_path / "catalog")
    entries = [(_BASE_NS - i * 60_000_000_000, 100.0) for i in range(3)]
    _write_snapshots(catalog_path, entries)
    client = _client(catalog_path, monkeypatch)

    response = client.get(
        f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=10&bar_seconds=60"
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 3
    assert body["has_more"] is False


def test_short_page_with_has_more_true_is_legal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    in_probe_window_only_ns = (
        before_ns - 390_000_000_000
    )  # outside main window, inside probe window
    _write_snapshots(catalog_path, [(in_main_window_ns, 100.0), (in_probe_window_only_ns, 90.0)])
    client = _client(catalog_path, monkeypatch)

    response = client.get(f"/api/candles/{_IID}?before_ns={before_ns}&limit=2&bar_seconds=60")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1  # short page: only 1 candle in the main window, limit was 2
    assert body["has_more"] is True


def test_gap_marker_inserted_between_candles_separated_by_more_than_one_bar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Python's `list[-0:]` equals `list[0:]` (the whole list) -- `limit=0` must not fall
    through to that footgun and return more than one candle.
    """
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
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    `bar_seconds=0` would otherwise zero-divide inside `candle_dicts_from_snapshots`'
    bucketing; clamped to 1 (a legal, if unusual, 1-second bar) instead of erroring.
    """
    catalog_path = str(tmp_path / "catalog")
    _write_snapshots(catalog_path, [(_BASE_NS - 5_000_000_000, 100.0)])
    client = _client(catalog_path, monkeypatch)

    for bar_seconds in (0, -60):
        response = client.get(
            f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=10&bar_seconds={bar_seconds}",
        )
        assert response.status_code == 200


def test_large_limit_and_bar_seconds_combination_does_not_blow_the_query_span(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    `limit=500` * `bar_seconds=86400` * `_QUERY_WINDOW_MULTIPLIER=3` would otherwise
    span ~4 years of raw snapshots in one query (MEM-01) -- `_MAX_QUERY_SPAN_SECONDS`
    caps it regardless of the individually-clamped `limit`/`bar_seconds` product. A
    snapshot placed just past the cap must be excluded from the main query window.
    """
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


def test_weekly_bar_seconds_is_not_clamped_down_to_a_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The timeframe selector offers 1W; a 1D-only clamp silently returned daily bars."""
    catalog_path = str(tmp_path / "catalog")
    day_ns = 86_400_000_000_000
    week_ns = 7 * day_ns
    week_start = _BASE_NS // week_ns * week_ns
    # one snapshot on each of 3 consecutive days of the same week -> exactly one weekly bar
    _write_snapshots(catalog_path, [(week_start + i * day_ns, 100.0 + i) for i in range(3)])
    client = _client(catalog_path, monkeypatch)

    resp = client.get(
        f"/api/candles/{_IID}?before_ns={week_start + week_ns}&limit=10&bar_seconds=604800"
    )

    bars = [i for i in resp.json()["items"] if i["o"] is not None]
    assert [(b["t"], b["o"], b["c"]) for b in bars] == [(week_start // 1_000_000, 100.0, 102.0)]


def test_archive_fallback_window_is_capped_for_every_bar_size() -> None:
    week_ns = 7 * 86_400 * 1_000_000_000
    assert candles_routes._window_start_ns(0, 120, 3600) == -week_ns
    assert (
        candles_routes._window_start_ns(0, 120, 86_400) == -week_ns
    )  # no wider tier without rollups


def test_one_second_bars_return_one_candle_per_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog_path = str(tmp_path / "catalog")
    _write_snapshots(catalog_path, [(_BASE_NS + i * 1_000_000_000, 100.0 + i) for i in range(5)])
    client = _client(catalog_path, monkeypatch)

    resp = client.get(
        f"/api/candles/{_IID}?before_ns={_BASE_NS + 10_000_000_000}&limit=10&bar_seconds=1"
    )

    assert [i["c"] for i in resp.json()["items"]] == [100.0, 101.0, 102.0, 103.0, 104.0]


def test_sub_minute_bars_look_back_at_least_an_hour() -> None:
    assert candles_routes._window_start_ns(0, 120, 1) == -3600 * 1_000_000_000


def test_venue_field_and_malformed_id_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    ok = client.get(f"/api/candles/{_IID}?before_ns={_BASE_NS}&limit=3&bar_seconds=60")
    assert ok.json()["venue"] == "DYDX"
    assert client.get(f"/api/candles/BTC?before_ns={_BASE_NS}").status_code == 400


def test_invalid_candle_fails_the_request_loudly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from observability import error_ledger

    catalog_path = str(tmp_path / "catalog")
    _write_snapshots(catalog_path, [(_BASE_NS - i * 60_000_000_000, 100.0 + i) for i in range(3)])
    real = candles_routes.candle_dicts_for_window

    def corrupt(*args, **kwargs):
        out = real(*args, **kwargs)
        out[0] = {**out[0], "h": out[0]["l"] - 1.0}  # high below low
        return out

    monkeypatch.setattr(candles_routes, "candle_dicts_for_window", corrupt)
    error_ledger.reset()
    resp = _client(catalog_path, monkeypatch).get(
        f"/api/candles/{_IID}?before_ns={_BASE_NS + 60_000_000_000}&limit=10&bar_seconds=60",
    )
    assert resp.status_code == 500  # never served, never silently dropped
    assert "impossible candle" in resp.json()["detail"]
    assert error_ledger.counts() == {"candles.invalid_candle": 1}


def _store_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, minutes: range) -> TestClient:
    catalog_path = str(tmp_path / "catalog")
    client = _client(catalog_path, monkeypatch)
    db_path = str(tmp_path / "candles" / "candles_dydx.db")
    monkeypatch.setattr(candles_routes, "CANDLES_DB_DIR", str(tmp_path / "candles"))
    db = candle_store.connect_rw(db_path)
    candle_store.apply_seconds(
        db, _IID, [_second(m * 60, 100.0 + m) for m in minutes]
    )  # one trade per minute
    return client


def test_a_page_inside_the_candle_store_needs_no_parquet_at_all(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _store_client(tmp_path, monkeypatch, range(30))  # no Parquet catalog exists
    before_ns = (_DAY0_MS + 60 * 60_000) * 1_000_000

    body = client.get(f"/api/candles/{_IID}", params={"before_ns": before_ns, "limit": 10}).json()

    assert [i["t"] for i in body["items"]] == [_DAY0_MS + m * 60_000 for m in range(20, 30)]
    assert body["items"][-1]["c"] == 129.1  # minute 29: price 129 + the fixture's close offset
    assert body["has_more"] is True  # minutes 0-19 are older, still in the store


def test_history_older_than_the_store_comes_from_parquet_and_joins_seamlessly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _store_client(tmp_path, monkeypatch, range(30))
    day0_ns = _DAY0_MS * 1_000_000
    # 20 older one-minute candles that only the Parquet archive has (the store starts at minute 0).
    _write_snapshots(
        str(tmp_path / "catalog"), [(day0_ns - i * 60_000_000_000, 50.0 + i) for i in range(1, 21)]
    )
    before_ns = day0_ns + 60 * 60_000_000_000

    body = client.get(f"/api/candles/{_IID}", params={"before_ns": before_ns, "limit": 50}).json()

    ts = [i["t"] for i in body["items"]]
    assert ts == [
        _DAY0_MS + m * 60_000 for m in range(-20, 30)
    ]  # 20 archive + 30 store, no gap, no overlap
    assert body["has_more"] is False


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
            f"/api/candles/{iid}?before_ns={_BASE_NS}&limit=3&bar_seconds=60",
        )
        .json()
    )
    assert (body["venue"], body["market"]) == ("BYBIT", market)
