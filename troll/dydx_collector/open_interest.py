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
dYdX open interest: the one field dropped by the Rust/PyO3 adapter bindings on
both the REST and WebSocket markets-channel paths (confirmed by reading
crates/adapters/dydx/src/python/{http,websocket}.rs -- `open_interest` is parsed
Rust-side but never forwarded to Python). Fetched here with a plain stdlib REST
poll against the same public indexer endpoint, since stdlib already covers a
single infrequent GET (no need for a dependency that may not even be in the
production image -- aiohttp is a `test`-only extra of nautilus_trader, not a
runtime dependency).

"""

import asyncio
import json
import time
import urllib.request
from decimal import Decimal

import pyarrow as pa

from nautilus_trader.core.data import Data
from nautilus_trader.core.nautilus_pyo3 import DydxNetwork
from nautilus_trader.core.nautilus_pyo3 import get_dydx_http_url  # type: ignore[attr-defined]
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


class DydxOpenInterest(Data):
    """Open interest snapshot for a single dYdX perpetual market."""

    def __init__(
        self,
        instrument_id: InstrumentId,
        open_interest: Decimal,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.open_interest = open_interest
        self._ts_event = ts_event
        self._ts_init = ts_init

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init

    @classmethod
    def schema(cls) -> pa.Schema:
        return pa.schema(
            {
                "instrument_id": pa.dictionary(pa.int8(), pa.string()),
                "open_interest": pa.string(),
                "ts_event": pa.uint64(),
                "ts_init": pa.uint64(),
            },
            metadata={"type": "DydxOpenInterest"},
        )

    @staticmethod
    def to_dict(obj: "DydxOpenInterest") -> dict[str, object]:
        return {
            "instrument_id": obj.instrument_id.value,
            "open_interest": str(obj.open_interest),
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict[str, object]) -> "DydxOpenInterest":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            open_interest=Decimal(str(values["open_interest"])),
            ts_event=int(values["ts_event"]),  # type: ignore[call-overload]
            ts_init=int(values["ts_init"]),  # type: ignore[call-overload]
        )

    def __repr__(self) -> str:
        return f"DydxOpenInterest(instrument_id={self.instrument_id}, open_interest={self.open_interest})"


register_arrow(
    data_cls=DydxOpenInterest,
    schema=DydxOpenInterest.schema(),
    encoder=make_dict_serializer(schema=DydxOpenInterest.schema()),
    decoder=make_dict_deserializer(DydxOpenInterest),
)


def _fetch_markets_json(network: DydxNetwork) -> dict:
    url = f"{get_dydx_http_url(network)}/v4/perpetualMarkets"
    # dYdX's indexer rejects urllib's default User-Agent (403); needs a real one.
    request = urllib.request.Request(  # noqa: S310 (fixed https indexer URL)
        url,
        headers={"User-Agent": "nautilus-dydx-collector/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


async def fetch_open_interest(network: DydxNetwork) -> list[DydxOpenInterest]:
    """Poll dYdX's REST indexer for current open interest across all markets."""
    markets_json = await asyncio.to_thread(_fetch_markets_json, network)
    return parse_open_interest(markets_json, ts=time.time_ns())


def parse_open_interest(markets_json: dict, ts: int) -> list[DydxOpenInterest]:
    items = []
    for market in markets_json.get("markets", {}).values():
        ticker = market.get("ticker")
        open_interest = market.get("openInterest")
        if ticker is None or open_interest is None:
            continue
        items.append(
            DydxOpenInterest(
                instrument_id=InstrumentId.from_str(f"{ticker}-PERP.DYDX"),
                open_interest=Decimal(open_interest),
                ts_event=ts,
                ts_init=ts,
            ),
        )
    return items
