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
Story 33.4: the five `GET /api/coin/{iid}/...` derivatives routes' contract on a real
`ParquetDataCatalog` and candle store: the response shape, spot's empty `200`, the silent
`limit`/`bar_seconds` clamps, a malformed id (400), a missing definition (404) and a failed read
(500). The read model's values are `views/tests/test_derivatives.py`'s.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from views import derivatives

import data_api.app as app_module
import data_api.routes.derivatives as derivatives_routes
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"
_SPOT = "BTCUSDT-SPOT.BYBIT"
_S = 1_000_000_000
_MIN = 60 * _S
_TEN = 20_000 * 86_400 * _S + 10 * 3600 * _S  # 10:00 UTC of a long closed day
_TEN_MS = _TEN // 1_000_000
_BEFORE = _TEN + 10 * _MIN
_ROUTES = ("funding", "open-interest", "mark-index", "liquidations", "liquidation-bars")


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(derivatives_routes, "CATALOG_PATH", str(tmp_path))
    monkeypatch.setattr(derivatives_routes, "CANDLES_DB_DIR", str(tmp_path / "candles"))
    return TestClient(app_module.app)


def _define(root: Path) -> None:
    ParquetDataCatalog(str(root)).write_data(
        [
            CryptoPerpetual(
                instrument_id=InstrumentId.from_str(_IID),
                raw_symbol=Symbol("BTCUSDT"),
                base_currency=BTC,
                quote_currency=USDT,
                settlement_currency=USDT,
                is_inverse=False,
                price_precision=1,
                price_increment=Price.from_str("0.1"),
                size_precision=3,
                size_increment=Quantity.from_str("0.001"),
                ts_event=0,
                ts_init=0,
            )
        ]
    )


def _get(client: TestClient, route: str, iid: str = _IID, **params: object) -> dict:
    query = "&".join(f"{k}={v}" for k, v in {"before_ns": _BEFORE, **params}.items())
    response = client.get(f"/api/coin/{iid}/{route}?{query}")
    assert response.status_code == 200, response.text
    return response.json()


def test_funding_serves_exact_rates_and_the_ids_venue_and_market(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iid = InstrumentId.from_str(_IID)
    ParquetDataCatalog(str(tmp_path)).write_data(
        [FundingRateUpdate(iid, Decimal("0.0001"), _TEN, _TEN, interval=480, next_funding_ns=7)]
    )
    body = _get(_client(tmp_path, monkeypatch), "funding")
    assert body == {
        "items": [
            {
                "t": _TEN,
                "rate": "0.0001",
                "interval": 28_800,
                "next_funding_ns": 7,
                "annualised": pytest.approx(0.1095, abs=1e-15),
            }
        ],
        "has_more": False,
        "venue": "BYBIT",
        "market": "perp",
    }


def test_open_interest_serves_gap_rows_as_nulls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iid = InstrumentId.from_str(_IID)
    ParquetDataCatalog(str(tmp_path)).write_data(
        [
            OpenInterest(iid, Decimal(100), _TEN, _TEN),
            OpenInterest(iid, Decimal("120.5"), _TEN + 2 * _MIN, _TEN + 2 * _MIN),
        ]
    )
    body = _get(_client(tmp_path, monkeypatch), "open-interest", bar_seconds=60)
    assert body["items"] == [
        {"t": _TEN_MS, "oi": "100", "oi_change": None},
        {"t": _TEN_MS + 60_000, "oi": None, "oi_change": None},
        {"t": _TEN_MS + 120_000, "oi": "120.5", "oi_change": "20.5"},
    ]


def test_mark_index_serves_strings_and_float_basis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iid = InstrumentId.from_str(_IID)
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([MarkPriceUpdate(iid, Price.from_str("100.5"), _TEN, _TEN)])
    catalog.write_data([IndexPriceUpdate(iid, Price.from_str("100.0"), _TEN, _TEN)])
    body = _get(_client(tmp_path, monkeypatch), "mark-index")
    assert body["items"] == [
        {
            "t": _TEN_MS,
            "mark": "100.5",
            "index": "100.0",
            "basis_mi_bps": 50.0,  # (100.5 - 100) / 100 x 10^4
            "basis_ml_bps": None,  # no candle store
        }
    ]


def _liq(key: str, ts: int) -> Liquidation:
    iid = InstrumentId.from_str(_IID)
    return Liquidation(iid, LiquidatedSide.SHORT, 4, 1_000_000, 1, 3, key, ts, ts)


def test_liquidations_serve_units_with_the_definitions_precisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _define(tmp_path)
    ParquetDataCatalog(str(tmp_path)).write_data([_liq("a", _TEN)])
    body = _get(_client(tmp_path, monkeypatch), "liquidations")
    assert body == {
        "items": [
            {
                "side": "short",
                "size_units": 4,
                "price_units": 1_000_000,
                "price_precision": 1,
                "size_precision": 3,
                "venue_event_id": "a",
                "ts_event": _TEN,
                "ts_init": _TEN,
                "price_kind": "bankruptcy",
                "notional_units": 4_000_000,  # 4 x 1 000 000 at 10^-(1 + 3)
                "notional_precision": 4,
            }
        ],
        "has_more": False,
        "venue": "BYBIT",
        "market": "perp",
        "price_precision": 1,
        "size_precision": 3,
    }


def test_liquidation_bars_serve_the_read_models_rows_and_gap_rows_as_nulls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    The page's values are `views/tests/test_derivatives.py`'s (the candle store is the candles
    context's, which `data_api` never reaches): here the route's transport of a read-model page,
    an item and a gap row, with the definition's precisions beside them.
    """
    _define(tmp_path)
    bar = {
        "t": _TEN_MS,
        "long_v": 0,
        "short_v": 4,
        "n": 1,
        "size_precision": 3,
        "notional_units": 4_000_000,
        "notional_precision": 4,
        "long_notional_units": 0,
        "short_notional_units": 4_000_000,
    }

    def page(*_args: object, **_kw: object) -> tuple[list[dict], bool]:
        return [{"t": _TEN_MS - 60_000}, bar], True

    monkeypatch.setattr(derivatives, "liquidation_bars", page)
    body = _get(_client(tmp_path, monkeypatch), "liquidation-bars")
    assert body["items"] == [{**dict.fromkeys(bar), "t": _TEN_MS - 60_000}, bar]
    assert (body["has_more"], body["price_precision"], body["size_precision"]) == (True, 1, 3)


@pytest.mark.parametrize("route", _ROUTES)
def test_a_spot_id_gets_an_empty_page_never_a_404(
    route: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    body = _get(_client(tmp_path, monkeypatch), route, iid=_SPOT)
    assert body["items"] == []
    assert (body["has_more"], body["venue"], body["market"]) == (False, "BYBIT", "spot")


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"limit": 0, "bar_seconds": 0}, (1, 1)),
        ({"limit": 10_000, "bar_seconds": 10**9}, (500, 604_800)),
        ({}, (120, 60)),
    ],
)
def test_limit_and_bar_seconds_are_clamped_silently(
    params: dict, expected: tuple[int, int], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, int]] = []

    def page(_iid: str, _before: int, limit: int, bar_seconds: int, **_kw: object) -> tuple:
        seen.append((limit, bar_seconds))
        return [], False

    monkeypatch.setattr(derivatives, "open_interest_page", page)
    _get(_client(tmp_path, monkeypatch), "open-interest", **params)
    assert seen == [expected]


def test_a_malformed_id_is_a_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(tmp_path, monkeypatch).get(f"/api/coin/NOVENUE/funding?before_ns={_BEFORE}")
    assert response.status_code == 400


@pytest.mark.parametrize("route", ("liquidations", "liquidation-bars"))
def test_a_unit_route_without_a_definition_is_a_404(
    route: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _client(tmp_path, monkeypatch).get(f"/api/coin/{_IID}/{route}?before_ns={_BEFORE}")
    assert response.status_code == 404
    assert "no instrument definition" in response.json()["detail"]


def test_a_failed_read_is_a_500_naming_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    iid = InstrumentId.from_str(_IID)
    catalog = ParquetDataCatalog(str(tmp_path))
    catalog.write_data([OpenInterest(iid, Decimal(100), _TEN, _TEN)])
    copy = [
        OpenInterest(iid, Decimal(101), _TEN, _TEN),
        OpenInterest(iid, Decimal(9), _TEN + _S, _TEN + _S),
    ]
    catalog.write_data(copy, skip_disjoint_check=True)
    response = _client(tmp_path, monkeypatch).get(
        f"/api/coin/{_IID}/open-interest?before_ns={_BEFORE}"
    )
    assert response.status_code == 500
    assert "stored twice" in response.json()["detail"]


@pytest.mark.parametrize("bar_seconds", (1, 90))
def test_liquidation_bars_at_a_width_the_candle_store_cannot_tile_is_a_400_naming_it(
    bar_seconds: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """1 s and 90 s are a multiple of no stored width: refused, never an empty or null page."""
    _define(tmp_path)
    response = _client(tmp_path, monkeypatch).get(
        f"/api/coin/{_IID}/liquidation-bars?before_ns={_BEFORE}&bar_seconds={bar_seconds}"
    )
    assert response.status_code == 400
    assert f"bar_seconds={bar_seconds} is not composable" in response.json()["detail"]


def test_mark_index_at_a_width_the_candle_store_cannot_tile_is_served(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At 1 s the mark is served (200) and `basis_ml_bps` is null: no stored close to compare."""
    ParquetDataCatalog(str(tmp_path)).write_data(
        [MarkPriceUpdate(InstrumentId.from_str(_IID), Price.from_str("100.5"), _TEN, _TEN)]
    )
    body = _get(_client(tmp_path, monkeypatch), "mark-index", bar_seconds=1)
    assert [(i["t"], i["mark"], i["basis_ml_bps"]) for i in body["items"]] == [
        (_TEN_MS, "100.5", None)
    ]
