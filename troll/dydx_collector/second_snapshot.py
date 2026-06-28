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
1-second L2 book snapshot with OFI, microprice, and per-side volume.

Stores the top 20 price levels on each side so OBI can be computed at any N
during analysis: obi_N = sum(bid_sizes[:N]) / (sum(bid_sizes[:N]) + sum(ask_sizes[:N])).

OFI is the Cont-Kukanov-Stoikov delta between consecutive 1s snapshots (top of book).
Spread is omitted — derive as ask_prices[0] - bid_prices[0].
"""

import pyarrow as pa

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow

BOOK_DEPTH = 20


class DydxSecondSnapshot(Data):
    """
    1-second sampled L2 book snapshot with microstructure signals.

    `bid_prices[0]` / `ask_prices[0]` are best bid/ask.
    Lists are variable-length (up to BOOK_DEPTH=20); shorter for illiquid coins.
    `ofi` is None for the first snapshot per instrument.
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        bid_prices: list[float],
        bid_sizes: list[float],
        ask_prices: list[float],
        ask_sizes: list[float],
        buy_volume: float,
        sell_volume: float,
        ofi: float | None,
        microprice: float | None,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.bid_prices = bid_prices
        self.bid_sizes = bid_sizes
        self.ask_prices = ask_prices
        self.ask_sizes = ask_sizes
        self.buy_volume = buy_volume
        self.sell_volume = sell_volume
        self.ofi = ofi
        self.microprice = microprice
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
                "bid_prices": pa.list_(pa.float64()),
                "bid_sizes": pa.list_(pa.float64()),
                "ask_prices": pa.list_(pa.float64()),
                "ask_sizes": pa.list_(pa.float64()),
                "buy_volume": pa.float64(),
                "sell_volume": pa.float64(),
                "ofi": pa.float64(),
                "microprice": pa.float64(),
                "ts_event": pa.uint64(),
                "ts_init": pa.uint64(),
            },
            metadata={"type": "DydxSecondSnapshot"},
        )

    @staticmethod
    def to_dict(obj: "DydxSecondSnapshot") -> dict:
        return {
            "instrument_id": obj.instrument_id.value,
            "bid_prices": obj.bid_prices,
            "bid_sizes": obj.bid_sizes,
            "ask_prices": obj.ask_prices,
            "ask_sizes": obj.ask_sizes,
            "buy_volume": obj.buy_volume,
            "sell_volume": obj.sell_volume,
            "ofi": obj.ofi,
            "microprice": obj.microprice,
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict) -> "DydxSecondSnapshot":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            bid_prices=list(values["bid_prices"]),
            bid_sizes=list(values["bid_sizes"]),
            ask_prices=list(values["ask_prices"]),
            ask_sizes=list(values["ask_sizes"]),
            buy_volume=float(values.get("buy_volume") or 0.0),
            sell_volume=float(values.get("sell_volume") or 0.0),
            ofi=values.get("ofi"),
            microprice=values.get("microprice"),
            ts_event=int(values["ts_event"]),
            ts_init=int(values["ts_init"]),
        )

    def __repr__(self) -> str:
        bp = self.bid_prices[0] if self.bid_prices else None
        ap = self.ask_prices[0] if self.ask_prices else None
        return f"DydxSecondSnapshot({self.instrument_id} bid={bp} ask={ap} levels={len(self.bid_prices)})"


register_arrow(
    data_cls=DydxSecondSnapshot,
    schema=DydxSecondSnapshot.schema(),
    encoder=make_dict_serializer(schema=DydxSecondSnapshot.schema()),
    decoder=make_dict_deserializer(DydxSecondSnapshot),
)
