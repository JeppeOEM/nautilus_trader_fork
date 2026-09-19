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
Bybit linear open interest. The Rust adapter drops it on the linear ticker WS path (see
client.py), so poll the public REST tickers endpoint -- one GET returns every linear symbol.
Stdlib only, same as `dydx_collector.open_interest`.
"""

import asyncio
import json
import time
import urllib.request
from decimal import Decimal

import pyarrow as pa

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


_URLS = {
    "mainnet": "https://api.bybit.com",
    "testnet": "https://api-testnet.bybit.com",
}


class BybitOpenInterest(Data):
    """Open interest (base-coin contracts) for one Bybit linear perpetual."""

    def __init__(self, instrument_id: InstrumentId, open_interest: Decimal, ts_event: int, ts_init: int) -> None:
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
            metadata={"type": "BybitOpenInterest"},
        )

    @staticmethod
    def to_dict(obj: "BybitOpenInterest") -> dict[str, object]:
        return {
            "instrument_id": obj.instrument_id.value,
            "open_interest": str(obj.open_interest),
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict[str, object]) -> "BybitOpenInterest":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            open_interest=Decimal(str(values["open_interest"])),
            ts_event=int(values["ts_event"]),  # type: ignore[call-overload]
            ts_init=int(values["ts_init"]),  # type: ignore[call-overload]
        )

    def __repr__(self) -> str:
        return f"BybitOpenInterest({self.instrument_id}, {self.open_interest})"


register_arrow(
    data_cls=BybitOpenInterest,
    schema=BybitOpenInterest.schema(),
    encoder=make_dict_serializer(schema=BybitOpenInterest.schema()),
    decoder=make_dict_deserializer(BybitOpenInterest),
)


def _fetch_tickers_json(environment: str) -> dict:
    request = urllib.request.Request(  # noqa: S310 (fixed https URL)
        f"{_URLS[environment]}/v5/market/tickers?category=linear",
        headers={"User-Agent": "nautilus-bybit-collector/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


async def fetch_open_interest(environment: str) -> list[BybitOpenInterest]:
    tickers_json = await asyncio.to_thread(_fetch_tickers_json, environment)
    return parse_open_interest(tickers_json, ts=time.time_ns())


def parse_open_interest(tickers_json: dict, ts: int) -> list[BybitOpenInterest]:
    # The Nautilus Bybit adapter's linear ids are "{symbol}-LINEAR.BYBIT".
    return [
        BybitOpenInterest(
            instrument_id=InstrumentId.from_str(f"{row['symbol']}-LINEAR.BYBIT"),
            open_interest=Decimal(row["openInterest"]),
            ts_event=ts,
            ts_init=ts,
        )
        for row in tickers_json.get("result", {}).get("list", [])
        if row.get("symbol") and row.get("openInterest")
    ]
