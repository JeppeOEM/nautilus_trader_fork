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
"""Unit tests for watchlist.fetch_watchlist and its BacktestDataConfig compatibility (Story 1.3)."""

import json
import urllib.request
from typing import Self

from ml_signals import watchlist
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.identifiers import InstrumentId


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_fetch_watchlist_parses_instrument_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse(
            {"instrument_ids": ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]},
        ),
    )
    result = watchlist.fetch_watchlist("http://127.0.0.1:8765")
    assert result == ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX"]


def test_fetch_watchlist_empty_list(monkeypatch) -> None:
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse({"instrument_ids": []}),
    )
    assert watchlist.fetch_watchlist() == []


def test_watchlist_ids_are_backtest_data_config_compatible(monkeypatch) -> None:
    """AC3: the returned coin-set must accept-as-is into a BacktestDataConfig list, no per-coin editing."""
    ids = ["BTC-USD-PERP.DYDX", "ETH-USD-PERP.DYDX", "SOL-USD-PERP.DYDX"]
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda request, timeout=None: _FakeResponse({"instrument_ids": ids}),
    )

    fetched = watchlist.fetch_watchlist()
    configs = [
        BacktestDataConfig(
            catalog_path="troll/dydx_collector/catalog",
            data_cls=TradeTick,
            instrument_id=InstrumentId.from_str(iid),
        )
        for iid in fetched
    ]

    assert len(configs) == len(ids)
    assert [c.instrument_id for c in configs] == [InstrumentId.from_str(iid) for iid in ids]
