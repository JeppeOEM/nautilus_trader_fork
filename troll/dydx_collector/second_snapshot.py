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
1-second top-of-book snapshot with OFI and microprice.

Sampled every second from the live L2 book maintained by the collector.
OFI is the Cont-Kukanov-Stoikov delta between consecutive 1s snapshots —
a uniform-time signal suitable for ML features and HFT backtesting.
Spread is omitted: derive it as ask_price - bid_price where needed.
"""

import pyarrow as pa

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


class DydxSecondSnapshot(Data):
    """
    1-second sampled top-of-book snapshot with derived microstructure signals.

    Queried via `catalog.query(DydxSecondSnapshot, identifiers=[iid], start=..., end=...)`.
    `ofi` is None for the first snapshot per instrument (no previous state to diff against).
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        bid_price: float,
        bid_size: float,
        ask_price: float,
        ask_size: float,
        buy_volume: float,
        sell_volume: float,
        ofi: float | None,
        microprice: float | None,
        obi: float | None,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.bid_price = bid_price
        self.bid_size = bid_size
        self.ask_price = ask_price
        self.ask_size = ask_size
        self.buy_volume = buy_volume
        self.sell_volume = sell_volume
        self.ofi = ofi
        self.microprice = microprice
        self.obi = obi
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
                "bid_price": pa.float64(),
                "bid_size": pa.float64(),
                "ask_price": pa.float64(),
                "ask_size": pa.float64(),
                "buy_volume": pa.float64(),
                "sell_volume": pa.float64(),
                "ofi": pa.float64(),
                "microprice": pa.float64(),
                "obi": pa.float64(),
                "ts_event": pa.uint64(),
                "ts_init": pa.uint64(),
            },
            metadata={"type": "DydxSecondSnapshot"},
        )

    @staticmethod
    def to_dict(obj: "DydxSecondSnapshot") -> dict:
        return {
            "instrument_id": obj.instrument_id.value,
            "bid_price": obj.bid_price,
            "bid_size": obj.bid_size,
            "ask_price": obj.ask_price,
            "ask_size": obj.ask_size,
            "buy_volume": obj.buy_volume,
            "sell_volume": obj.sell_volume,
            "ofi": obj.ofi,
            "microprice": obj.microprice,
            "obi": obj.obi,
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict) -> "DydxSecondSnapshot":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            bid_price=float(values["bid_price"]),
            bid_size=float(values["bid_size"]),
            ask_price=float(values["ask_price"]),
            ask_size=float(values["ask_size"]),
            buy_volume=float(values.get("buy_volume") or 0.0),
            sell_volume=float(values.get("sell_volume") or 0.0),
            ofi=values.get("ofi"),
            microprice=values.get("microprice"),
            obi=values.get("obi"),
            ts_event=int(values["ts_event"]),
            ts_init=int(values["ts_init"]),
        )

    def __repr__(self) -> str:
        return (
            f"DydxSecondSnapshot({self.instrument_id} "
            f"bid={self.bid_price}x{self.bid_size} ask={self.ask_price}x{self.ask_size})"
        )


register_arrow(
    data_cls=DydxSecondSnapshot,
    schema=DydxSecondSnapshot.schema(),
    encoder=make_dict_serializer(schema=DydxSecondSnapshot.schema()),
    decoder=make_dict_deserializer(DydxSecondSnapshot),
)
