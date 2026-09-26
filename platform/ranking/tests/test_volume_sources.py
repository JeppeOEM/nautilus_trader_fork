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
Tests for the per-venue USD 24 h volume sources (`ranking.infrastructure.volume_*`, Story 22.10;
moved in Story 25.2): the pure parsers, the requests they send through `kernel.venue_http`, and
`__main__`'s environment validation.
"""

import asyncio
import io
import json
import urllib.request

import pytest
from observability import error_ledger

from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from ranking.__main__ import Settings
from ranking.__main__ import volume_sources
from ranking.infrastructure.volume_bybit import BybitVolumeSource
from ranking.infrastructure.volume_bybit import parse_bybit_volume_24h
from ranking.infrastructure.volume_dydx import DydxVolumeSource
from ranking.infrastructure.volume_dydx import parse_volume_24h
from ranking.infrastructure.volume_hyperliquid import HyperliquidVolumeSource
from ranking.infrastructure.volume_hyperliquid import parse_hyperliquid_volume_24h


def test_parse_volume_24h_extracts_usd_volume_per_market() -> None:
    markets_json = {
        "markets": {
            "BTC": {"ticker": "BTC-USD", "volume24H": "50000000"},
            "ETH": {"ticker": "ETH-USD", "volume24H": "10000000"},
        }
    }
    result = parse_volume_24h(markets_json)
    assert result == {"BTC-USD-PERP.DYDX": 50000000.0, "ETH-USD-PERP.DYDX": 10000000.0}


@pytest.mark.parametrize(
    "market", [{}, {"volume24H": None}, {"volume24H": ""}, {"volume24H": "nan"}]
)
def test_parse_volume_24h_missing_or_unparseable_volume_is_skipped_and_ledgered(
    market: dict,
) -> None:
    """No volume is not zero volume (DATA-01): the coin is left out, never ranked on a 0."""
    error_ledger.reset()
    markets_json = {
        "markets": {
            "X": {"ticker": "X-USD", **market},
            "BTC": {"ticker": "BTC-USD", "volume24H": "5"},
        }
    }
    assert parse_volume_24h(markets_json) == {"BTC-USD-PERP.DYDX": 5.0}
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}


def test_parse_volume_24h_skips_market_missing_ticker() -> None:
    assert parse_volume_24h({"markets": {"X": {"volume24H": "100"}}}) == {}


# --- Story 22.10: per-venue USD 24h volume -------------------------------------------------
# Fixtures below follow the shape of real responses captured 2026-09-21 (Bybit v5
# /market/tickers linear + spot, Hyperliquid /info metaAndAssetCtxs) -- same field names,
# string-typed numbers, and `[meta, ctxs]` pairing as the live APIs; trimmed to a few rows,
# and the numbers are illustrative, not the captured values.

_BYBIT_LINEAR_TICKERS = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "category": "linear",
        "list": [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "81850.10",
                "openInterest": "52034.1",
                "turnover24h": "3499130184.7322",
                "volume24h": "42880.8150",
                "fundingRate": "0.00005",
            },
            {
                "symbol": "0GUSDT",
                "lastPrice": "0.2266",
                "openInterest": "9529956.3",
                "turnover24h": "2114464.7013",
                "volume24h": "9653419.2000",
                "fundingRate": "0.00005",
            },
        ],
    },
}

_BYBIT_SPOT_TICKERS = {
    "retCode": 0,
    "retMsg": "OK",
    "result": {
        "category": "spot",
        "list": [
            {
                "symbol": "BTCUSDT",
                "lastPrice": "81852.3",
                "turnover24h": "366657848.7248449",
                "volume24h": "4480.1",
                "usdIndexPrice": "81860.1",
            },
            {
                "symbol": "WLDUSDC",
                "lastPrice": "0.4459",
                "turnover24h": "224189.487161",
                "volume24h": "515315.85",
                "usdIndexPrice": "0.446893",
            },
            {
                "symbol": "ETHBTC",
                "lastPrice": "0.02651",
                "turnover24h": "12.3456",
                "volume24h": "465.7",
                "usdIndexPrice": "2170.4",
            },
        ],
    },
}

_HL_META_AND_CTXS = [
    {
        "universe": [
            {"szDecimals": 5, "name": "BTC", "maxLeverage": 40, "marginTableId": 56},
            {"szDecimals": 4, "name": "ETH", "maxLeverage": 25, "marginTableId": 55},
        ],
        "marginTables": [],
        "collateralToken": 0,
    },
    [
        {
            "funding": "0.0000125",
            "openInterest": "43265.9884999999",
            "prevDayPx": "80534.0",
            "dayNtlVlm": "1787695641.5723600388",
            "markPx": "81858.0",
            "dayBaseVlm": "22035.29",
        },
        {
            "funding": "0.0000100",
            "openInterest": "812345.1",
            "prevDayPx": "2150.0",
            "dayNtlVlm": "954321000.25",
            "markPx": "2170.4",
            "dayBaseVlm": "440000.1",
        },
    ],
]


def test_parse_bybit_volume_24h_linear_uses_turnover24h_usd() -> None:
    error_ledger.reset()

    result = parse_bybit_volume_24h(_BYBIT_LINEAR_TICKERS, "linear")

    assert result == {"BTCUSDT-LINEAR.BYBIT": 3499130184.7322, "0GUSDT-LINEAR.BYBIT": 2114464.7013}
    assert error_ledger.counts() == {}


def test_parse_bybit_volume_24h_spot_keeps_only_usd_stablecoin_quotes() -> None:
    """
    ETHBTC's turnover24h is in BTC, not USD (OBS-03): left out, and not a ledger
    event at parse time -- only a *collected* one is (see the missing-volume ledger).
    """
    error_ledger.reset()

    result = parse_bybit_volume_24h(_BYBIT_SPOT_TICKERS, "spot")

    assert result == {"BTCUSDT-SPOT.BYBIT": 366657848.7248449, "WLDUSDC-SPOT.BYBIT": 224189.487161}
    assert error_ledger.counts() == {}


def test_parse_bybit_volume_24h_linear_and_spot_ids_never_collide() -> None:
    linear = parse_bybit_volume_24h(_BYBIT_LINEAR_TICKERS, "linear")
    spot = parse_bybit_volume_24h(_BYBIT_SPOT_TICKERS, "spot")

    assert not set(linear) & set(spot)


@pytest.mark.parametrize("raw", ["", None, "abc", "-5", "nan", "inf", True])
def test_parse_bybit_volume_24h_unparseable_value_is_skipped_and_ledgered(raw: object) -> None:
    error_ledger.reset()
    tickers = {
        "retCode": 0,
        "result": {
            "list": [
                {"symbol": "BTCUSDT", "turnover24h": raw},
                {"symbol": "ETHUSDT", "turnover24h": "10.5"},
            ]
        },
    }

    result = parse_bybit_volume_24h(tickers, "linear")

    assert result == {"ETHUSDT-LINEAR.BYBIT": 10.5}  # never BTCUSDT at 0 (DATA-01)
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}


def test_parse_hyperliquid_volume_24h_pairs_universe_with_ctxs_by_index() -> None:
    error_ledger.reset()

    result = parse_hyperliquid_volume_24h(_HL_META_AND_CTXS)

    assert result == {
        "BTC-USD-PERP.HYPERLIQUID": 1787695641.5723600388,
        "ETH-USD-PERP.HYPERLIQUID": 954321000.25,
    }
    assert error_ledger.counts() == {}


def test_parse_hyperliquid_volume_24h_null_value_is_skipped_and_ledgered() -> None:
    error_ledger.reset()
    payload = [_HL_META_AND_CTXS[0], [{"dayNtlVlm": None}, {"dayNtlVlm": "5.0"}]]

    result = parse_hyperliquid_volume_24h(payload)

    assert result == {"ETH-USD-PERP.HYPERLIQUID": 5.0}
    assert error_ledger.counts() == {"ranking_engine.volume24h": 1}


@pytest.mark.parametrize(
    "payload",
    [
        {"universe": []},
        [],
        [{"universe": [{"name": "BTC"}]}, []],
        [{"universe": [{"name": "BTC"}]}, [{}], [{}]],
        [["not", "a", "dict"], [{}]],
    ],
)
def test_parse_hyperliquid_volume_24h_bad_shape_raises(payload: object) -> None:
    with pytest.raises(ValueError, match="metaAndAssetCtxs"):
        parse_hyperliquid_volume_24h(payload)


@pytest.mark.parametrize(
    "tickers",
    [
        {"retCode": 10006, "retMsg": "Too many visits!", "result": {}},
        {"retCode": 0, "result": {}},
        {},
    ],
)
def test_parse_bybit_volume_24h_error_response_raises_instead_of_parsing_empty(
    tickers: dict,
) -> None:
    with pytest.raises(ValueError, match="retCode"):
        parse_bybit_volume_24h(tickers, "linear")


def _json_response(payload: object) -> io.BytesIO:
    """Stand-in for urlopen's response: a context manager json.load can read."""
    return io.BytesIO(json.dumps(payload).encode())


def _capture(monkeypatch: pytest.MonkeyPatch, payload: object) -> list:
    requests: list = []

    def _urlopen(request: urllib.request.Request, timeout: float) -> io.BytesIO:
        requests.append(request)
        return _json_response(payload)

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)
    return requests


def test_hyperliquid_fetch_posts_meta_and_asset_ctxs_as_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _capture(monkeypatch, _HL_META_AND_CTXS)

    volumes = asyncio.run(HyperliquidVolumeSource("mainnet").fetch())

    request = requests[0]
    assert volumes == parse_hyperliquid_volume_24h(_HL_META_AND_CTXS)
    assert request.full_url == "https://api.hyperliquid.xyz/info"
    assert request.get_method() == "POST"
    assert json.loads(request.data) == {"type": "metaAndAssetCtxs"}
    assert request.get_header("Content-type") == "application/json"


def test_bybit_fetch_requests_the_category_tickers(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _capture(monkeypatch, _BYBIT_SPOT_TICKERS)

    source = BybitVolumeSource("testnet", "spot")
    volumes = asyncio.run(source.fetch())

    assert source.name == "bybit-spot"
    assert volumes == parse_bybit_volume_24h(_BYBIT_SPOT_TICKERS, "spot")
    assert requests[0].full_url == "https://api-testnet.bybit.com/v5/market/tickers?category=spot"


def test_dydx_fetch_requests_perpetual_markets_with_a_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _capture(monkeypatch, {"markets": {"BTC": {"ticker": "BTC-USD", "volume24H": "5"}}})

    volumes = asyncio.run(DydxVolumeSource(DydxNetwork.MAINNET).fetch())

    assert volumes == {"BTC-USD-PERP.DYDX": 5.0}
    assert requests[0].full_url.endswith("/v4/perpetualMarkets")
    assert requests[0].get_header("User-agent")  # the indexer rejects urllib's default (403)


def test_bybit_source_rejects_an_unknown_category() -> None:
    with pytest.raises(ValueError, match="category"):
        BybitVolumeSource("mainnet", "inverse")


def _settings(
    dydx: str = "mainnet", bybit: str = "mainnet", hyperliquid: str = "mainnet"
) -> Settings:
    return Settings(
        redis_url="redis://127.0.0.1:6379",
        catalog_path="/nonexistent",
        metrics_db_path="/nonexistent/metrics.db",
        dydx_network=dydx,
        bybit_environment=bybit,
        hyperliquid_environment=hyperliquid,
        volatility_lookback_seconds=3600,
        heartbeat_seconds=5,
    )


@pytest.mark.parametrize(
    ("settings", "name"),
    [
        (_settings(bybit="demo"), "BYBIT_ENVIRONMENT"),
        (_settings(hyperliquid="devnet"), "HYPERLIQUID_ENVIRONMENT"),
        (_settings(dydx="devnet"), "DYDX_NETWORK"),
    ],
)
def test_volume_sources_reject_an_unknown_environment(settings: Settings, name: str) -> None:
    with pytest.raises(ValueError, match=name):
        volume_sources(settings)


def test_volume_sources_poll_every_venue_and_market() -> None:
    sources = volume_sources(_settings(hyperliquid="testnet"))
    assert [s.name for s in sources] == ["dydx", "bybit-linear", "bybit-spot", "hyperliquid"]
