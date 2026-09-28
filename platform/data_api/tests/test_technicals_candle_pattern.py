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
Story 27.7: the screener as a pattern scanner. A `CandlePattern` Technicals column goes through
`/api/rankings/technicals-values` with no special case -- the chart's own native replay -- so each
ranked coin's value is the pattern's +100/-100/0 at its latest closed candle, equal to what the
chart route shows there. Real `ParquetDataCatalog`/`DydxSecondSnapshot` rows, real replay (TEST-03).
"""

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kernel.candle_patterns import PatternName
from kernel.second_snapshot import DydxSecondSnapshot
from views.rankings_bus import RankingsBus

from data_api import buses
from data_api.tests.test_screener_columns import _client
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_MINUTE_NS = 60_000_000_000
_BULLISH_IID = "BTC-USD-PERP.DYDX"
_BEARISH_IID = "ETH-USD-PERP.DYDX"
_NO_DATA_IID = "NEW-USD-PERP.DYDX"
_FILLER = [(100.0 + (i % 3) * 0.1, 100.6 + (i % 3) * 0.1, 99.4, 100.1) for i in range(38)]
# The last two closed minutes: a bullish engulfing (black then a covering white) ...
_BULLISH_END = [(100.0, 100.2, 98.9, 99.0), (98.9, 100.6, 98.8, 100.5)]
# ... and a bearish one (white then a covering black).
_BEARISH_END = [(99.0, 100.2, 98.9, 100.0), (100.1, 100.2, 98.5, 98.8)]
_COLUMN = {"name": "CandlePattern", "params": {"pattern": "ENGULFING"}, "bar_seconds": 60}


def _snapshot(iid: str, ts: int, ohlc: tuple[float, float, float, float]) -> DydxSecondSnapshot:
    o, h, l, c = ohlc
    return DydxSecondSnapshot(
        instrument_id=InstrumentId.from_str(iid),
        bid_prices=[l],
        bid_sizes=[1.0],
        ask_prices=[h],
        ask_sizes=[1.0],
        buy_volume=1.0,
        sell_volume=0.5,
        buy_count=1,
        sell_count=1,
        open_price=o,
        high_price=h,
        low_price=l,
        close_price=c,
        ts_event=ts,
        ts_init=ts,
    )


def _seed(tmp_path: Path) -> None:
    """One snapshot per closed minute ending just before now; each coin's last two draw its bars."""
    end = time.time_ns() // _MINUTE_NS * _MINUTE_NS
    rows = []
    for iid, tail in ((_BULLISH_IID, _BULLISH_END), (_BEARISH_IID, _BEARISH_END)):
        bars = [*_FILLER, *tail]
        rows += [_snapshot(iid, end - (len(bars) - i) * _MINUTE_NS, b) for i, b in enumerate(bars)]
    ParquetDataCatalog(str(tmp_path / "cat")).write_data(rows)


def _rank(monkeypatch: pytest.MonkeyPatch, *iids: str) -> None:
    bus = RankingsBus()
    bus.latest = {"mode": "volume", "updated_at": 1, "ranks": [{"instrument_id": i} for i in iids]}
    monkeypatch.setattr(buses, "bus", bus)


def _chart_latest(client: TestClient, iid: str) -> dict:
    items = client.get(
        f"/api/coin/{iid}/indicator-values",
        params={
            "before_ns": time.time_ns() + 3_600 * 1_000_000_000,
            "entries": json.dumps([_COLUMN]),
            "limit": 120,
        },
    ).json()["items"]
    return items[-1]["values"]


def test_a_pattern_column_scans_every_ranked_coin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    _rank(monkeypatch, _BULLISH_IID, _BEARISH_IID, _NO_DATA_IID)
    client = _client(tmp_path, monkeypatch)

    body = client.get(
        "/api/rankings/technicals-values", params={"entries": json.dumps([_COLUMN])}
    ).json()

    assert body["errors"] == {}
    assert body["values"][_BULLISH_IID] == {"0.value": 100.0}
    assert body["values"][_BEARISH_IID] == {"0.value": -100.0}
    assert body["values"][_NO_DATA_IID] == {}  # no candles: an honest gap, never a fabricated 0


def test_the_column_equals_the_chart_at_the_last_closed_candle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    _rank(monkeypatch, _BULLISH_IID, _BEARISH_IID)
    client = _client(tmp_path, monkeypatch)

    values = client.get(
        "/api/rankings/technicals-values", params={"entries": json.dumps([_COLUMN])}
    ).json()["values"]

    for iid in (_BULLISH_IID, _BEARISH_IID):
        (chart_value,) = _chart_latest(client, iid).values()
        assert values[iid] == {"0.value": chart_value}, iid


def test_the_catalog_offers_every_pattern_as_a_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)

    entry = client.get("/api/indicators/catalog").json()["CandlePattern"]

    assert entry["choices"]["pattern"] == [p.name for p in PatternName]
    assert len(entry["choices"]["pattern"]) == 22
    assert (entry["panel"], entry["category"]) == ("histogram", "native")
    assert entry["params"]["pattern"] == "ENGULFING"


def test_a_pattern_column_round_trips_its_params(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    column = {
        "name": "CandlePattern",
        "params": {"pattern": "HAMMER", "trend_bars": 2, "star_gap": False, "body_ratio": 0.25},
        "category": "native",
        "bar_seconds": 900,
    }

    assert client.put("/api/rankings/technicals-columns", json=[column]).status_code == 200

    assert client.get("/api/rankings/technicals-columns").json() == [column]


def test_an_unknown_pattern_is_a_bad_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    _rank(monkeypatch, _BULLISH_IID)
    client = _client(tmp_path, monkeypatch)
    bad = {**_COLUMN, "params": {"pattern": "THREE_LINE_STRIKE"}}

    response = client.get("/api/rankings/technicals-values", params={"entries": json.dumps([bad])})

    assert response.status_code == 400
