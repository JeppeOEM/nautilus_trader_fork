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
"""Story 32.8: `GET /api/coin/{instrument_id}/footprint` on a real `ParquetDataCatalog`."""

from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from kernel.fold import fold_trades
from kernel.tests.snapshot_factory import make_snapshot
from views import chart_series

import data_api.app as app_module
import data_api.routes.footprint as footprint_routes
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"
_NS = 1_000_000_000
_DAY0 = 20_000 * 86_400 * _NS  # a UTC midnight
_SETTLED = _DAY0 + (60 + chart_series.FOOTPRINT_SETTLE_SECONDS) * _NS  # minute 0 has settled


def _define(catalog: str, price_precision: int = 1, size_precision: int = 0) -> None:
    ParquetDataCatalog(catalog).write_data(
        [
            CryptoPerpetual(
                instrument_id=InstrumentId.from_str(_IID),
                raw_symbol=Symbol("BTCUSDT"),
                base_currency=BTC,
                quote_currency=USDT,
                settlement_currency=USDT,
                is_inverse=False,
                price_precision=price_precision,
                price_increment=Price.from_str(f"{Decimal(1).scaleb(-price_precision):f}"),
                size_precision=size_precision,
                size_increment=Quantity.from_str(f"{Decimal(1).scaleb(-size_precision):f}"),
                ts_event=0,
                ts_init=0,
            )
        ]
    )


def _trade(sec: int, price: str, size: str, side: AggressorSide, n: int) -> TradeTick:
    ts = _DAY0 + sec * _NS + n
    iid = InstrumentId.from_str(_IID)
    return TradeTick(
        iid, Price.from_str(price), Quantity.from_str(size), side, TradeId(str(n)), ts, ts
    )


def _archive(catalog: str, trades: list[TradeTick]) -> None:
    """One second's snapshot (its trades folded, precision 1/0) plus the trades themselves."""
    writer = ParquetDataCatalog(catalog)
    second = trades[0].ts_event // _NS * _NS
    units = fold_trades(trades).snapshot_units(1, 0)
    writer.write_data(
        [make_snapshot(_IID, ts_event=second, price_precision=1, size_precision=0, trades=units)]
    )
    writer.write_data(trades)


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, define: bool = True) -> TestClient:
    catalog = str(tmp_path / "catalog")
    if define:
        _define(catalog)
    monkeypatch.setattr(footprint_routes, "CATALOG_PATH", catalog)
    monkeypatch.setattr(footprint_routes, "CANDLES_DB_DIR", f"{catalog}-no-candle-store-dir")
    monkeypatch.setattr(footprint_routes.time, "time_ns", lambda: _SETTLED)
    return TestClient(app_module.app)


def _url(**params: object) -> str:
    query = "&".join(f"{k}={v}" for k, v in {"before_ns": _SETTLED, **params}.items())
    return f"/api/coin/{_IID}/footprint?{query}"


def test_happy_path_serves_integer_rows_and_the_definitions_precisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    _archive(
        footprint_routes.CATALOG_PATH,
        [
            _trade(10, "100.0", "3", AggressorSide.BUYER, 1),
            _trade(10, "100.0", "1", AggressorSide.SELLER, 2),
            _trade(10, "100.5", "2", AggressorSide.BUYER, 3),
        ],
    )
    response = client.get(_url(row_ticks=5))
    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "t": _DAY0 // 1_000_000,
                "row_ticks": 5,
                "rows": [{"p": 1000, "b": 3, "s": 1}, {"p": 1005, "b": 2, "s": 0}],
                "delta": 4,
                "total": 6,
                "poc_row": 1000,
                "no_trades": False,
            }
        ],
        "has_more": False,
        "price_precision": 1,
        "size_precision": 0,
    }


@pytest.mark.parametrize("bad", ["0", "-3", "1.5", "abc", "", "1000001", "9" * 5000, "١٢"])
def test_a_bad_row_ticks_is_a_422_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    response = _client(tmp_path, monkeypatch).get(_url(row_ticks=bad))
    assert response.status_code == 422
    assert response.json()["detail"].startswith("row_ticks:")


def test_limit_is_clamped_to_max_footprint_bars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[int] = []

    def page(*args: object, **kwargs: object) -> tuple[list[dict], bool]:
        seen.append(args[2])  # type: ignore[arg-type]
        return [], False

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(footprint_routes.chart_series, "footprint_page", page)
    assert client.get(_url(limit=1000)).status_code == 200
    assert seen == [chart_series.MAX_FOOTPRINT_BARS]


def test_an_id_without_a_definition_is_a_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = _client(tmp_path, monkeypatch, define=False).get(_url())
    assert response.status_code == 404


def test_a_malformed_id_is_a_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(tmp_path, monkeypatch).get(f"/api/coin/nodot/footprint?before_ns={_SETTLED}")
    assert response.status_code == 400


def test_an_undecodable_trade_is_a_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(tmp_path, monkeypatch)
    catalog = footprint_routes.CATALOG_PATH
    writer = ParquetDataCatalog(catalog)  # a trade finer than the definition's 1 decimal
    writer.write_data(
        [
            make_snapshot(
                _IID,
                ts_event=_DAY0,
                price_precision=2,
                size_precision=0,
                trades=fold_trades(
                    [_trade(0, "100.05", "1", AggressorSide.BUYER, 1)]
                ).snapshot_units(2, 0),
            )
        ]
    )
    writer.write_data([_trade(0, "100.05", "1", AggressorSide.BUYER, 1)])
    response = client.get(_url())
    assert response.status_code == 500
    assert "not exact" in response.json()["detail"]


@pytest.mark.parametrize(
    "fault",
    [chart_series.ImpossibleCandle("bad candle"), chart_series.FootprintOverflow("past 2^53")],
)
def test_an_upstream_malfunction_is_a_500_naming_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Exception
) -> None:
    def page(*_args: object, **_kwargs: object) -> tuple[list[dict], bool]:
        raise fault

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(footprint_routes.chart_series, "footprint_page", page)
    response = client.get(_url())
    assert response.status_code == 500
    assert response.json()["detail"] == str(fault)
