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
1-minute rollup of DydxSecondSnapshot -- a regenerable performance cache, never a
replacement for the raw 1-second archive.

Built incrementally (O(1) per second) by MinuteRollupBuilder so wide-window candle
requests need not rescan raw 1s data. Top-of-book fields are the *raw* values at the
minute's last observed second; microprice/spread are derived on read (SIGNAL-01).
"""

from dataclasses import dataclass
from dataclasses import field

import pyarrow as pa

from dydx_collector.second_snapshot import DydxSecondSnapshot
from ml_signals.indicators import MultiLevelOBI
from ml_signals.indicators import MultiLevelOFI
from nautilus_trader.core.data import Data
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow

_MINUTE_NS = 60_000_000_000
_LEVELS = (5, 10)
# Snapshots arrive every 1s; a longer silence means the collector skipped seconds (stale/crossed
# book), so the next book must not be diffed against the pre-gap one.
_MAX_GAP_NS = 2_000_000_000
_OPTIONAL_FLOATS = ("open", "high", "low", "close")
_FLOATS = (
    "buy_volume",
    "sell_volume",
    "close_bid_price",
    "close_bid_size",
    "close_ask_price",
    "close_ask_size",
    "ofi_5",
    "ofi_10",
    "obi_5",
    "obi_10",
)
_INTS = ("buy_count", "sell_count", "seconds_observed")


class DydxMinuteRollup(Data):
    """
    One closed minute of DydxSecondSnapshots for one instrument.

    `ts_event` is the minute's start. `open`..`close` are None when no trade occurred;
    `ofi_N` is the sum of the minute's per-second OFI contributions, `obi_N` their mean.
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        ts_event: int,
        ts_init: int,
        open: float | None,  # noqa: A002
        high: float | None,
        low: float | None,
        close: float | None,
        buy_volume: float,
        sell_volume: float,
        buy_count: int,
        sell_count: int,
        seconds_observed: int,
        close_bid_price: float,
        close_bid_size: float,
        close_ask_price: float,
        close_ask_size: float,
        ofi_5: float,
        ofi_10: float,
        obi_5: float,
        obi_10: float,
    ) -> None:
        self.instrument_id = instrument_id
        self._ts_event = ts_event
        self._ts_init = ts_init
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.buy_volume = buy_volume
        self.sell_volume = sell_volume
        self.buy_count = buy_count
        self.sell_count = sell_count
        self.seconds_observed = seconds_observed
        self.close_bid_price = close_bid_price
        self.close_bid_size = close_bid_size
        self.close_ask_price = close_ask_price
        self.close_ask_size = close_ask_size
        self.ofi_5 = ofi_5
        self.ofi_10 = ofi_10
        self.obi_5 = obi_5
        self.obi_10 = obi_10

    @property
    def ts_event(self) -> int:
        return self._ts_event

    @property
    def ts_init(self) -> int:
        return self._ts_init

    @classmethod
    def schema(cls) -> pa.Schema:
        fields: dict[str, pa.DataType] = {"instrument_id": pa.dictionary(pa.int8(), pa.string())}
        fields.update(dict.fromkeys(_OPTIONAL_FLOATS + _FLOATS, pa.float64()))
        fields.update(dict.fromkeys(_INTS, pa.uint32()))
        fields["ts_event"] = pa.uint64()
        fields["ts_init"] = pa.uint64()
        return pa.schema(fields, metadata={"type": "DydxMinuteRollup"})

    @staticmethod
    def to_dict(obj: "DydxMinuteRollup") -> dict:
        d = {"instrument_id": obj.instrument_id.value, "ts_event": obj.ts_event, "ts_init": obj.ts_init}
        d.update({k: getattr(obj, k) for k in _OPTIONAL_FLOATS + _FLOATS + _INTS})
        return d

    @classmethod
    def from_dict(cls, values: dict) -> "DydxMinuteRollup":
        kwargs: dict = {
            "instrument_id": InstrumentId.from_str(str(values["instrument_id"])),
            "ts_event": int(values["ts_event"]),
            "ts_init": int(values["ts_init"]),
        }
        kwargs.update({k: None if values.get(k) is None else float(values[k]) for k in _OPTIONAL_FLOATS})
        kwargs.update({k: float(values[k]) for k in _FLOATS})
        kwargs.update({k: int(values[k]) for k in _INTS})
        return cls(**kwargs)

    def __repr__(self) -> str:
        return f"DydxMinuteRollup({self.instrument_id} ts={self.ts_event} close={self.close} secs={self.seconds_observed})"


@dataclass
class _Minute:
    bucket: int
    last: DydxSecondSnapshot
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    buy_volume: float = 0.0
    sell_volume: float = 0.0
    buy_count: int = 0
    sell_count: int = 0
    seconds: int = 0
    ofi: dict[int, float] = field(default_factory=lambda: dict.fromkeys(_LEVELS, 0.0))
    obi: dict[int, float] = field(default_factory=lambda: dict.fromkeys(_LEVELS, 0.0))


class MinuteRollupBuilder:
    """
    Feed one DydxSecondSnapshot per call; get back the just-closed minute, else None.

    OFI runs on one long-lived window=1 MultiLevelOFI per (iid, levels), so the first
    second of a minute is compared against the true previous second. An in-progress
    minute is never returned (nothing is emitted at shutdown).
    """

    def __init__(self) -> None:
        self._minutes: dict[str, _Minute] = {}
        self._ofi: dict[tuple[str, int], MultiLevelOFI] = {}
        self._obi: dict[tuple[str, int], MultiLevelOBI] = {}
        self._primed: dict[str, bool] = {}
        self._last_ts: dict[str, int] = {}

    def update(self, iid: str, s: DydxSecondSnapshot) -> DydxMinuteRollup | None:
        bucket = s.ts_event // _MINUTE_NS
        cur = self._minutes.get(iid)
        last_ts = self._last_ts.get(iid)
        if last_ts is not None and s.ts_event <= last_ts:
            return None  # duplicate/out-of-order: would close the wrong minute
        if last_ts is not None and s.ts_event - last_ts > _MAX_GAP_NS:
            self.discard_book_state(iid)
        self._last_ts[iid] = s.ts_event
        closed = None
        if cur is not None and bucket != cur.bucket:
            closed = self._close(iid, cur)
            cur = None
        if cur is None:
            cur = self._minutes[iid] = _Minute(bucket=bucket, last=s)
        self._accumulate(iid, cur, s)
        return closed

    def drop(self, iid: str) -> None:
        """Forget an unsubscribed instrument entirely (MEM-02); its open minute is not emitted."""
        self._minutes.pop(iid, None)
        self._primed.pop(iid, None)
        self._last_ts.pop(iid, None)
        for n in _LEVELS:
            self._ofi.pop((iid, n), None)
            self._obi.pop((iid, n), None)

    def discard_book_state(self, iid: str) -> None:
        """Book rebuilt from scratch: next OFI update is a first observation, not a delta."""
        self._primed[iid] = False
        for n in _LEVELS:
            if (iid, n) in self._ofi:
                self._ofi[(iid, n)].clear_prev_state()

    def _accumulate(self, iid: str, m: _Minute, s: DydxSecondSnapshot) -> None:
        primed = self._primed.get(iid, False)
        for n in _LEVELS:
            ofi = self._ofi.setdefault((iid, n), MultiLevelOFI(levels=n, window=1))
            ofi.update_raw(s.bid_prices, s.bid_sizes, s.ask_prices, s.ask_sizes)
            # A first observation leaves `value` stale from before; it contributes nothing.
            m.ofi[n] += ofi.value if primed else 0.0
            obi = self._obi.setdefault((iid, n), MultiLevelOBI(levels=n))
            obi.update_raw(s.bid_sizes, s.ask_sizes)
            m.obi[n] += obi.value
        self._primed[iid] = True
        if None not in (s.open_price, s.high_price, s.low_price, s.close_price):
            m.open = s.open_price if m.open is None else m.open
            m.high = s.high_price if m.high is None else max(m.high, s.high_price)
            m.low = s.low_price if m.low is None else min(m.low, s.low_price)
            m.close = s.close_price
        m.buy_volume += s.buy_volume
        m.sell_volume += s.sell_volume
        m.buy_count += s.buy_count
        m.sell_count += s.sell_count
        m.seconds += 1
        m.last = s

    @staticmethod
    def _close(iid: str, m: _Minute) -> DydxMinuteRollup:
        s = m.last
        return DydxMinuteRollup(
            instrument_id=InstrumentId.from_str(iid),
            ts_event=m.bucket * _MINUTE_NS,
            # Knowable only once the minute ends; an earlier ts_init would be look-ahead.
            ts_init=max(s.ts_init, (m.bucket + 1) * _MINUTE_NS),
            open=m.open,
            high=m.high,
            low=m.low,
            close=m.close,
            buy_volume=m.buy_volume,
            sell_volume=m.sell_volume,
            buy_count=m.buy_count,
            sell_count=m.sell_count,
            seconds_observed=m.seconds,
            close_bid_price=s.bid_prices[0],
            close_bid_size=s.bid_sizes[0],
            close_ask_price=s.ask_prices[0],
            close_ask_size=s.ask_sizes[0],
            ofi_5=m.ofi[5],
            ofi_10=m.ofi[10],
            obi_5=m.obi[5] / m.seconds,
            obi_10=m.obi[10] / m.seconds,
        )


register_arrow(
    data_cls=DydxMinuteRollup,
    schema=DydxMinuteRollup.schema(),
    encoder=make_dict_serializer(schema=DydxMinuteRollup.schema()),
    decoder=make_dict_deserializer(DydxMinuteRollup),
)
