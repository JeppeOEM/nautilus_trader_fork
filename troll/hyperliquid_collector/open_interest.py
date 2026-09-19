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
Hyperliquid open interest. Unlike dYdX/Bybit the Rust adapter forwards it over the WebSocket
(`subscribe_open_interest`), so this module is only the catalog-serializable Data type.
"""

from decimal import Decimal

import pyarrow as pa

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


class HyperliquidOpenInterest(Data):
    """Open interest for one Hyperliquid perpetual (streamed; no REST poll needed)."""

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
            metadata={"type": "HyperliquidOpenInterest"},
        )

    @staticmethod
    def to_dict(obj: "HyperliquidOpenInterest") -> dict[str, object]:
        return {
            "instrument_id": obj.instrument_id.value,
            "open_interest": str(obj.open_interest),
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict[str, object]) -> "HyperliquidOpenInterest":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            open_interest=Decimal(str(values["open_interest"])),
            ts_event=int(values["ts_event"]),  # type: ignore[call-overload]
            ts_init=int(values["ts_init"]),  # type: ignore[call-overload]
        )

    @staticmethod
    def from_pyo3(pyo3_open_interest: object) -> "HyperliquidOpenInterest":
        return HyperliquidOpenInterest(
            instrument_id=InstrumentId.from_str(str(pyo3_open_interest.instrument_id)),  # type: ignore[attr-defined]
            open_interest=Decimal(str(pyo3_open_interest.open_interest)),  # type: ignore[attr-defined]
            ts_event=pyo3_open_interest.ts_event,  # type: ignore[attr-defined]
            ts_init=pyo3_open_interest.ts_init,  # type: ignore[attr-defined]
        )

    def __repr__(self) -> str:
        return f"HyperliquidOpenInterest({self.instrument_id}, {self.open_interest})"


register_arrow(
    data_cls=HyperliquidOpenInterest,
    schema=HyperliquidOpenInterest.schema(),
    encoder=make_dict_serializer(schema=HyperliquidOpenInterest.schema()),
    decoder=make_dict_deserializer(HyperliquidOpenInterest),
)
