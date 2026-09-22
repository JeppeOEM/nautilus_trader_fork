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
"""`kernel.venue_http`: the venue URLs and the request builders every venue REST call uses."""

import json

import pytest

from kernel import venue_http
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork


def test_get_request_carries_the_user_agent() -> None:
    request = venue_http.get_request(venue_http.bybit_url("mainnet", "/v5/market/kline?x=1"))
    assert request.full_url == "https://api.bybit.com/v5/market/kline?x=1"
    assert request.get_method() == "GET"
    assert request.get_header("User-agent") == venue_http.USER_AGENT
    custom = venue_http.get_request("https://api-testnet.bybit.com/v5", "ua/1.0")
    assert custom.get_header("User-agent") == "ua/1.0"


def test_post_json_request_body_and_headers() -> None:
    body = {"type": "l2Book", "coin": "BTC"}
    request = venue_http.post_json_request(venue_http.hyperliquid_info_url("testnet"), body, "hl")
    assert request.full_url == "https://api.hyperliquid-testnet.xyz/info"
    assert request.get_method() == "POST"
    assert request.data == json.dumps(body).encode()
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("User-agent") == "hl"


def test_dydx_indexer_url_uses_the_network_base() -> None:
    url = venue_http.dydx_indexer_url(DydxNetwork.MAINNET, "/v4/perpetualMarkets")
    assert url.startswith("https://")
    assert url.endswith("/v4/perpetualMarkets")
    assert venue_http.DYDX_NETWORKS["testnet"] == DydxNetwork.TESTNET


@pytest.mark.parametrize("path", ["v5/market/kline", "", "api.bybit.com/v5"])
def test_a_path_without_a_leading_slash_is_refused(path: str) -> None:
    """Joined bare, `v5/...` would name another host (`api.bybit.comv5/...`)."""
    with pytest.raises(ValueError, match="must start with '/'"):
        venue_http.bybit_url("mainnet", path)
    with pytest.raises(ValueError, match="must start with '/'"):
        venue_http.dydx_indexer_url(DydxNetwork.MAINNET, path)


@pytest.mark.parametrize("url", ["http://api.bybit.com/v5", "file:///etc/passwd", "/v5/market"])
def test_a_non_https_url_is_refused(url: str) -> None:
    """The builders take a `str`, so the scheme their `# noqa: S310` asserts is checked here."""
    with pytest.raises(ValueError, match="must be https"):
        venue_http.get_request(url)
    with pytest.raises(ValueError, match="must be https"):
        venue_http.post_json_request(url, {"type": "l2Book"})


def test_url_tables_are_read_only() -> None:
    with pytest.raises(TypeError):
        venue_http.BYBIT_URLS["mainnet"] = "https://elsewhere"
    assert venue_http.BYBIT_URLS["testnet"] == "https://api-testnet.bybit.com"
    assert venue_http.HYPERLIQUID_URLS["mainnet"] == "https://api.hyperliquid.xyz/info"
