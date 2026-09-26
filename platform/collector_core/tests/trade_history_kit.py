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
Shared test kit for the venue `trade_history` tests (story 22.14, split in Story 26.1): real
Nautilus instruments at each venue's live precisions (verified 2026-09-21 through the pyo3 HTTP
clients: dYdX BTC-USD 0/4, Bybit BTCUSDT linear 2/3, spot 1/6, Hyperliquid BTC 1/5) and a fake
transport that replays recorded payloads. No network.
"""

import json
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


S = 1_000_000_000
MS = 1_000_000
TS_INIT = 1_789_990_700 * S


def instrument(iid: str, raw: str, price_p: int, size_p: int) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(raw),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_p,
        price_increment=Price.from_str(str(Decimal(1).scaleb(-price_p))),
        size_precision=size_p,
        size_increment=Quantity.from_str(str(Decimal(1).scaleb(-size_p))),
        ts_event=0,
        ts_init=0,
    )


def fixture(tests_dir: Path, name: str) -> Any:
    return json.loads((tests_dir / "fixtures" / name).read_text())


class Recorder:
    """Fake transport: returns recorded payloads in order and keeps the requests it saw."""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request) -> Any:
        self.requests.append(request)
        return self.payloads.pop(0)
