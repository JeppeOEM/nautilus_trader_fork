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
Open interest for one perpetual, shared by the dYdX, Bybit and Hyperliquid collectors.

dYdX and Bybit poll a REST endpoint (the Rust adapters drop the field); Hyperliquid streams it
over the WebSocket (`from_pyo3`). Instrument ids are venue-suffixed, so one catalog directory
(`custom_open_interest/`) holds all three venues without collisions.
"""

from decimal import Decimal

import pyarrow as pa

from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


class OpenInterest(Data):
    """Open interest snapshot for a single perpetual market (dYdX, Bybit or Hyperliquid)."""

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
            metadata={"type": "OpenInterest"},
        )

    @staticmethod
    def to_dict(obj: "OpenInterest") -> dict[str, object]:
        return {
            "instrument_id": obj.instrument_id.value,
            "open_interest": str(obj.open_interest),
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict[str, object]) -> "OpenInterest":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            open_interest=Decimal(str(values["open_interest"])),
            ts_event=int(values["ts_event"]),  # type: ignore[call-overload]
            ts_init=int(values["ts_init"]),  # type: ignore[call-overload]
        )

    @staticmethod
    def from_pyo3(pyo3_open_interest: object) -> "OpenInterest":
        # Hyperliquid streams open interest over the WS; the Rust object arrives inside CustomData.
        return OpenInterest(
            instrument_id=InstrumentId.from_str(str(pyo3_open_interest.instrument_id)),  # type: ignore[attr-defined]
            open_interest=Decimal(str(pyo3_open_interest.open_interest)),  # type: ignore[attr-defined]
            ts_event=pyo3_open_interest.ts_event,  # type: ignore[attr-defined]
            ts_init=pyo3_open_interest.ts_init,  # type: ignore[attr-defined]
        )

    def __repr__(self) -> str:
        return (
            f"OpenInterest(instrument_id={self.instrument_id}, open_interest={self.open_interest})"
        )


register_arrow(
    data_cls=OpenInterest,
    schema=OpenInterest.schema(),
    encoder=make_dict_serializer(schema=OpenInterest.schema()),
    decoder=make_dict_deserializer(OpenInterest),
)
