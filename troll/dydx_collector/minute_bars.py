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
Enriched 1-minute bar computed from raw trades + order book + mark price.

DydxMinuteBar packs OHLCV plus per-minute microstructure (OFI, microprice, spread,
last mark price) into a single catalog-ready type. The collector builds these in
real-time from its data buffers so the WS bars channel is never needed.
"""

from dataclasses import dataclass

import pyarrow as pa

from nautilus_trader.core.data import Data
from nautilus_trader.model.book import OrderBook
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDeltas
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.serialization.arrow.serializer import make_dict_deserializer
from nautilus_trader.serialization.arrow.serializer import make_dict_serializer
from nautilus_trader.serialization.arrow.serializer import register_arrow


_MINUTE_NS = 60_000_000_000


class DydxMinuteBar(Data):
    """
    Enriched 1-minute OHLCV bar built from raw trade ticks + book deltas + mark price.

    Queried via `catalog.query(DydxMinuteBar, identifiers=[iid], start=..., end=...)`.
    Float64 prices (not fixed-point) — suitable for analysis, not for BacktestEngine
    bar feeds which require the standard nautilus_trader Bar type.
    """

    def __init__(
        self,
        instrument_id: InstrumentId,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        buy_volume: float,
        sell_volume: float,
        buy_absorption: float,
        sell_absorption: float,
        trade_count: int,
        mark_price: float | None,
        ofi: float | None,
        microprice: float | None,
        spread: float | None,
        ts_event: int,
        ts_init: int,
    ) -> None:
        self.instrument_id = instrument_id
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.buy_volume = buy_volume
        self.sell_volume = sell_volume
        self.buy_absorption = buy_absorption
        self.sell_absorption = sell_absorption
        self.trade_count = trade_count
        self.mark_price = mark_price
        self.ofi = ofi
        self.microprice = microprice
        self.spread = spread
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
                "open": pa.float64(),
                "high": pa.float64(),
                "low": pa.float64(),
                "close": pa.float64(),
                "volume": pa.float64(),
                "buy_volume": pa.float64(),
                "sell_volume": pa.float64(),
                "buy_absorption": pa.float64(),
                "sell_absorption": pa.float64(),
                "trade_count": pa.int32(),
                "mark_price": pa.float64(),
                "ofi": pa.float64(),
                "microprice": pa.float64(),
                "spread": pa.float64(),
                "ts_event": pa.uint64(),
                "ts_init": pa.uint64(),
            },
            metadata={"type": "DydxMinuteBar"},
        )

    @staticmethod
    def to_dict(obj: "DydxMinuteBar") -> dict:
        return {
            "instrument_id": obj.instrument_id.value,
            "open": obj.open,
            "high": obj.high,
            "low": obj.low,
            "close": obj.close,
            "volume": obj.volume,
            "buy_volume": obj.buy_volume,
            "sell_volume": obj.sell_volume,
            "buy_absorption": obj.buy_absorption,
            "sell_absorption": obj.sell_absorption,
            "trade_count": obj.trade_count,
            "mark_price": obj.mark_price,
            "ofi": obj.ofi,
            "microprice": obj.microprice,
            "spread": obj.spread,
            "ts_event": obj.ts_event,
            "ts_init": obj.ts_init,
        }

    @classmethod
    def from_dict(cls, values: dict) -> "DydxMinuteBar":
        return cls(
            instrument_id=InstrumentId.from_str(str(values["instrument_id"])),
            open=float(values["open"]),
            high=float(values["high"]),
            low=float(values["low"]),
            close=float(values["close"]),
            volume=float(values["volume"]),
            buy_volume=float(values["buy_volume"]),
            sell_volume=float(values["sell_volume"]),
            buy_absorption=float(values["buy_absorption"]),
            sell_absorption=float(values["sell_absorption"]),
            trade_count=int(values["trade_count"]),
            mark_price=values.get("mark_price"),
            ofi=values.get("ofi"),
            microprice=values.get("microprice"),
            spread=values.get("spread"),
            ts_event=int(values["ts_event"]),
            ts_init=int(values["ts_init"]),
        )

    def __repr__(self) -> str:
        return (
            f"DydxMinuteBar({self.instrument_id} "
            f"O={self.open} H={self.high} L={self.low} C={self.close} V={self.volume})"
        )


register_arrow(
    data_cls=DydxMinuteBar,
    schema=DydxMinuteBar.schema(),
    encoder=make_dict_serializer(schema=DydxMinuteBar.schema()),
    decoder=make_dict_deserializer(DydxMinuteBar),
)


@dataclass
class _BarAccum:
    """Per-instrument accumulator for the current in-progress minute."""

    minute_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    trade_count: int
    last_mark: float | None = None
    ofi_sum: float = 0.0
    has_ofi: bool = False
    last_microprice: float | None = None
    last_spread: float | None = None
    buy_absorption: float = 0.0
    sell_absorption: float = 0.0


class MinuteBarBuilder:
    """
    Stateful builder: call `update()` each flush with one instrument's buffered data,
    get back completed DydxMinuteBar objects.

    OrderBook and prev-bid/ask state persist across flushes for OFI continuity.
    Microprice/spread and OFI from deltas that arrive before the first trade of a
    minute are carried forward and applied to the accumulator when the trade arrives.
    """

    def __init__(self) -> None:
        self._books: dict[str, OrderBook] = {}
        self._prev_bid_price: dict[str, float] = {}
        self._prev_bid_size: dict[str, float] = {}
        self._prev_ask_price: dict[str, float] = {}
        self._prev_ask_size: dict[str, float] = {}
        self._accums: dict[str, _BarAccum] = {}
        # Enrichment from deltas before the first trade of a minute
        self._last_microprice: dict[str, float | None] = {}
        self._last_spread: dict[str, float | None] = {}
        self._pending_ofi_sum: dict[str, float] = {}
        self._pending_ofi_minute: dict[str, int] = {}
        self._pending_has_ofi: dict[str, bool] = {}
        # Absorption: buy/sell volume since last delta, resolved when next delta arrives
        self._abs_pending_buy: dict[str, float] = {}
        self._abs_pending_sell: dict[str, float] = {}

    def update(
        self,
        instrument_id: str,
        trades: list[TradeTick],
        delta_batches: list[OrderBookDeltas],
        marks: list[MarkPriceUpdate],
        now_ns: int,
    ) -> list["DydxMinuteBar"]:
        """Return completed 1-minute bars for this instrument from the buffered data."""
        current_minute = (now_ns // _MINUTE_NS) * _MINUTE_NS
        iid_obj = InstrumentId.from_str(instrument_id)

        if instrument_id not in self._books:
            self._books[instrument_id] = OrderBook(iid_obj, BookType.L2_MBP)
        book = self._books[instrument_id]

        flat_deltas = [d for batch in delta_batches for d in batch.deltas]

        events: list[tuple[int, str, object]] = (
            [(t.ts_event, "trade", t) for t in trades]
            + [(d.ts_event, "delta", d) for d in flat_deltas]
            + [(m.ts_event, "mark", m) for m in marks]
        )
        events.sort(key=lambda x: x[0])

        completed: list[DydxMinuteBar] = []

        for ts, kind, obj in events:
            minute_ts = (ts // _MINUTE_NS) * _MINUTE_NS

            if kind == "trade":
                price = obj.price.as_double()  # type: ignore[union-attr]
                size = obj.size.as_double()  # type: ignore[union-attr]
                is_buy = obj.aggressor_side == AggressorSide.BUYER  # type: ignore[union-attr]
                accum = self._accums.get(instrument_id)

                if accum is None:
                    accum = _BarAccum(
                        minute_ts=minute_ts,
                        open=price, high=price, low=price, close=price,
                        volume=size,
                        buy_volume=size if is_buy else 0.0,
                        sell_volume=0.0 if is_buy else size,
                        trade_count=1,
                        # Seed enrichment from deltas that arrived before this first trade
                        last_microprice=self._last_microprice.get(instrument_id),
                        last_spread=self._last_spread.get(instrument_id),
                    )
                    if self._pending_ofi_minute.get(instrument_id) == minute_ts:
                        accum.ofi_sum = self._pending_ofi_sum.get(instrument_id, 0.0)
                        accum.has_ofi = self._pending_has_ofi.get(instrument_id, False)
                    self._accums[instrument_id] = accum
                elif minute_ts > accum.minute_ts:
                    bar = self._emit(instrument_id, accum, now_ns)
                    if bar is not None:
                        completed.append(bar)
                    accum = _BarAccum(
                        minute_ts=minute_ts,
                        open=price, high=price, low=price, close=price,
                        volume=size,
                        buy_volume=size if is_buy else 0.0,
                        sell_volume=0.0 if is_buy else size,
                        trade_count=1,
                        last_microprice=self._last_microprice.get(instrument_id),
                        last_spread=self._last_spread.get(instrument_id),
                    )
                    if self._pending_ofi_minute.get(instrument_id) == minute_ts:
                        accum.ofi_sum = self._pending_ofi_sum.get(instrument_id, 0.0)
                        accum.has_ofi = self._pending_has_ofi.get(instrument_id, False)
                    self._accums[instrument_id] = accum
                else:
                    accum.high = max(accum.high, price)
                    accum.low = min(accum.low, price)
                    accum.close = price
                    accum.volume += size
                    if is_buy:
                        accum.buy_volume += size
                    else:
                        accum.sell_volume += size
                    accum.trade_count += 1

                # Accumulate pending absorption volume for resolution on next delta
                if is_buy:
                    self._abs_pending_buy[instrument_id] = self._abs_pending_buy.get(instrument_id, 0.0) + size
                else:
                    self._abs_pending_sell[instrument_id] = self._abs_pending_sell.get(instrument_id, 0.0) + size

            elif kind == "delta":
                # Capture best prices before delta to resolve absorption
                ask_before_obj = book.best_ask_price()
                bid_before_obj = book.best_bid_price()
                ask_before = ask_before_obj.as_double() if ask_before_obj is not None else None
                bid_before = bid_before_obj.as_double() if bid_before_obj is not None else None

                book.apply_delta(obj)  # type: ignore[arg-type]
                bid_p_obj = book.best_bid_price()
                ask_p_obj = book.best_ask_price()
                if bid_p_obj is None or ask_p_obj is None:
                    continue

                bp = bid_p_obj.as_double()
                bs = book.best_bid_size().as_double()
                ap = ask_p_obj.as_double()
                as_ = book.best_ask_size().as_double()

                # Resolve absorption: if best price held across this delta, pending
                # aggressive volume was absorbed by the resting side
                accum = self._accums.get(instrument_id)
                if accum is not None:
                    if ask_before is not None and ap == ask_before:
                        accum.buy_absorption += self._abs_pending_buy.pop(instrument_id, 0.0)
                    else:
                        self._abs_pending_buy.pop(instrument_id, None)
                    if bid_before is not None and bp == bid_before:
                        accum.sell_absorption += self._abs_pending_sell.pop(instrument_id, 0.0)
                    else:
                        self._abs_pending_sell.pop(instrument_id, None)

                ofi_c = self._ofi_contribution(instrument_id, bp, bs, ap, as_)
                total = bs + as_
                micro = (bp * as_ + ap * bs) / total if total > 0 else None
                spread = ap - bp

                # Always keep last microprice/spread for seeding new accumulators
                self._last_microprice[instrument_id] = micro
                self._last_spread[instrument_id] = spread

                # Accumulate pending OFI per minute for pre-first-trade deltas
                if self._pending_ofi_minute.get(instrument_id) != minute_ts:
                    self._pending_ofi_sum[instrument_id] = 0.0
                    self._pending_has_ofi[instrument_id] = False
                    self._pending_ofi_minute[instrument_id] = minute_ts
                if ofi_c is not None:
                    self._pending_ofi_sum[instrument_id] = (
                        self._pending_ofi_sum.get(instrument_id, 0.0) + ofi_c
                    )
                    self._pending_has_ofi[instrument_id] = True

                accum = self._accums.get(instrument_id)
                if accum is not None and minute_ts == accum.minute_ts:
                    if ofi_c is not None:
                        accum.ofi_sum += ofi_c
                        accum.has_ofi = True
                    accum.last_microprice = micro
                    accum.last_spread = spread

            elif kind == "mark":
                accum = self._accums.get(instrument_id)
                if accum is not None and minute_ts == accum.minute_ts:
                    accum.last_mark = obj.value.as_double()  # type: ignore[union-attr]

        # Emit any accumulator whose minute completed before now
        accum = self._accums.get(instrument_id)
        if accum is not None and accum.minute_ts < current_minute:
            bar = self._emit(instrument_id, accum, now_ns)
            if bar is not None:
                completed.append(bar)
            del self._accums[instrument_id]

        return completed

    def _ofi_contribution(
        self,
        iid: str,
        bp: float,
        bs: float,
        ap: float,
        as_: float,
    ) -> float | None:
        prev_bp = self._prev_bid_price.get(iid)
        if prev_bp is None:
            self._prev_bid_price[iid] = bp
            self._prev_bid_size[iid] = bs
            self._prev_ask_price[iid] = ap
            self._prev_ask_size[iid] = as_
            return None

        prev_bs = self._prev_bid_size[iid]
        prev_ap = self._prev_ask_price[iid]
        prev_as = self._prev_ask_size[iid]

        if bp > prev_bp:
            bid_term = bs
        elif bp == prev_bp:
            bid_term = bs - prev_bs
        else:
            bid_term = -prev_bs

        if ap < prev_ap:
            ask_term = as_
        elif ap == prev_ap:
            ask_term = as_ - prev_as
        else:
            ask_term = -prev_as

        self._prev_bid_price[iid] = bp
        self._prev_bid_size[iid] = bs
        self._prev_ask_price[iid] = ap
        self._prev_ask_size[iid] = as_
        return bid_term - ask_term

    def _emit(self, iid: str, accum: _BarAccum, now_ns: int) -> "DydxMinuteBar | None":
        if accum.trade_count == 0:
            return None
        return DydxMinuteBar(
            instrument_id=InstrumentId.from_str(iid),
            open=accum.open,
            high=accum.high,
            low=accum.low,
            close=accum.close,
            volume=accum.volume,
            buy_volume=accum.buy_volume,
            sell_volume=accum.sell_volume,
            buy_absorption=accum.buy_absorption,
            sell_absorption=accum.sell_absorption,
            trade_count=accum.trade_count,
            mark_price=accum.last_mark,
            ofi=accum.ofi_sum if accum.has_ofi else None,
            microprice=accum.last_microprice,
            spread=accum.last_spread,
            ts_event=accum.minute_ts,
            ts_init=now_ns,
        )
