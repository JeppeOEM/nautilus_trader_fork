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
"""The exact trades -> second fold, on real `TradeTick`s (TEST-01/03)."""

import random
from decimal import Decimal

from collector_core.fold import SecondTradeFields
from collector_core.fold import fold_trades
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


_IID = InstrumentId.from_str("BTCUSDT-LINEAR.BYBIT")
_BUY, _SELL = AggressorSide.BUYER, AggressorSide.SELLER


def _trade(price: str, size: str, side: AggressorSide, n: int, ts: int = 0) -> TradeTick:
    return TradeTick(
        _IID, Price.from_str(price), Quantity.from_str(size), side, TradeId(str(n)), ts, ts
    )


def test_empty_second_is_the_no_trade_contract() -> None:
    fields = fold_trades([])
    assert fields == SecondTradeFields()
    assert fields.snapshot_values()._asdict() == {
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "close_price": None,
        "buy_volume": 0.0,
        "sell_volume": 0.0,
        "buy_count": 0,
        "sell_count": 0,
    }


def test_one_trade_is_its_own_ohlc() -> None:
    fields = fold_trades([_trade("100.5", "0.25", _BUY, 1)])
    assert (fields.open_price, fields.high_price, fields.low_price, fields.close_price) == (
        Price.from_str("100.5"),
        Price.from_str("100.5"),
        Price.from_str("100.5"),
        Price.from_str("100.5"),
    )
    assert (fields.buy_volume, fields.sell_volume) == (Quantity.from_str("0.25"), None)
    assert (fields.buy_count, fields.sell_count) == (1, 0)


def test_buy_and_sell_volume_split_by_aggressor() -> None:
    fields = fold_trades(
        [
            _trade("100.0", "2.0", _BUY, 1),
            _trade("101.0", "3.0", _SELL, 2),
            _trade("99.0", "0.5", _BUY, 3),
            _trade("100.0", "1.0", AggressorSide.NO_AGGRESSOR, 4),  # the live contract: a sell
        ]
    )
    assert fields.buy_volume == Quantity.from_str("2.5")
    assert fields.sell_volume == Quantity.from_str("4.0")
    assert (fields.buy_count, fields.sell_count) == (2, 2)
    assert (fields.high_price, fields.low_price) == (
        Price.from_str("101.0"),
        Price.from_str("99.0"),
    )


def test_ts_event_orders_open_close_and_ties_keep_input_order() -> None:
    fields = fold_trades(
        [
            _trade("102.0", "1", _BUY, 1, ts=20),
            _trade("100.0", "1", _BUY, 2, ts=10),
            _trade("103.0", "1", _BUY, 3, ts=20),  # same ts_event as #1: arrives after it
        ]
    )
    assert fields.open_price == Price.from_str("100.0")
    assert fields.close_price == Price.from_str("103.0")


def test_tie_order_is_arrival_order_not_price() -> None:
    fields = fold_trades([_trade("105.0", "1", _BUY, 1, ts=5), _trade("95.0", "1", _BUY, 2, ts=5)])
    assert (fields.open_price, fields.close_price) == (
        Price.from_str("105.0"),
        Price.from_str("95.0"),
    )


def test_eight_decimal_sizes_sum_exactly_where_float_does_not() -> None:
    fields = fold_trades(
        [_trade("1.0", "0.10000000", _BUY, 1), _trade("1.0", "0.20000000", _BUY, 2)]
    )
    assert 0.1 + 0.2 != 0.3  # the float fold's error this test exists for
    assert fields.buy_volume is not None
    assert fields.buy_volume.as_decimal() == Decimal("0.3")
    assert fields.buy_volume.precision == 8


def test_mixed_size_precisions_sum_exactly_at_the_highest() -> None:
    fields = fold_trades([_trade("1.0", "0.1", _SELL, 1), _trade("1.0", "0.25", _SELL, 2)])
    assert fields.sell_volume is not None
    assert fields.sell_volume.as_decimal() == Decimal("0.35")
    assert fields.sell_volume.precision == 2


def test_mixed_price_precisions_compare_by_value() -> None:
    fields = fold_trades([_trade("100.5", "1", _BUY, 1), _trade("100.49", "1", _BUY, 2)])
    assert fields.high_price == Price.from_str("100.5")
    assert fields.low_price == Price.from_str("100.49")


def _random_trades(n: int, seed: int) -> list[TradeTick]:
    rng = random.Random(seed)  # noqa: S311 -- a deterministic fixture, not cryptography
    trades = []
    for i in range(n):
        price = f"{rng.randint(90_000, 110_000) / 10:.1f}"
        size = f"{rng.randint(1, 5_000_000) / 10**8:.8f}"
        side = _BUY if rng.random() < 0.5 else _SELL
        trades.append(_trade(price, size, side, i, ts=rng.randint(0, 999)))
    return trades


def test_ten_thousand_trades_equal_decimal_exactly_and_the_old_float_fold_within_a_unit() -> None:
    trades = _random_trades(10_000, seed=22_13)
    fields = fold_trades(trades)
    for side, total in ((_BUY, fields.buy_volume), (_SELL, fields.sell_volume)):
        mine = [t for t in trades if t.aggressor_side == side]
        exact = sum((Decimal(str(t.size)) for t in mine), Decimal(0))
        old_float = 0.0
        for t in mine:  # the retired live accumulator: `+= size.as_double()`
            old_float += t.size.as_double()
        assert total is not None
        assert total.as_decimal() == exact
        assert abs(total.as_double() - old_float) < 10**-total.precision
    ordered = sorted(trades, key=lambda t: t.ts_event)
    assert fields.high_price == max((t.price for t in trades), key=lambda p: p.as_decimal())
    assert fields.low_price == min((t.price for t in trades), key=lambda p: p.as_decimal())
    assert (fields.open_price, fields.close_price) == (ordered[0].price, ordered[-1].price)
