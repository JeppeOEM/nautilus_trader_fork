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
"""
One fixture day of every data type the catalog holds (Story 30.1), written by Nautilus's own
`ParquetDataCatalog.write_data` from real Nautilus objects -- the files capture produces, which
the archive then merges or rewrites. One `write_data` call per type, so each leaf holds one file.

Prices and sizes enter `Price`/`Quantity` from exact decimals (never a float); the snapshot is in
the integer layout (Story 30.2): units at the instrument definition's precisions (`PERP`: price 0.1,
size 0.001), exactly as capture encodes them. A seeded walk gives the values the variety of a real
day.
"""

import random
from decimal import Decimal
from pathlib import Path

from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot

from nautilus_trader.model.data import BookOrder
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import InstrumentStatus
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import OrderBookDelta
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.enums import BookAction
from nautilus_trader.model.enums import MarketStatusAction
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from nautilus_trader.test_kit.providers import TestInstrumentProvider


SEC = 1_000_000_000
DAY_NS = 86_400 * SEC
DAY0 = 20_000  # 2024-10-04, a UTC day long closed
PERP = TestInstrumentProvider.btcusdt_perp_binance()  # CryptoPerpetual, price 0.1, size 0.001
SPOT = TestInstrumentProvider.ethusdt_binance()  # CurrencyPair
IID: InstrumentId = PERP.id
# Every data directory the catalog holds (and so every one a rewrite must keep value-identical).
DATA_TYPES = (
    "custom_dydx_second_snapshot",
    "trade_tick",
    "mark_price_update",
    "index_price_update",
    "funding_rate_update",
    "custom_open_interest",
    "instrument_status",
    "order_book_deltas",
    "crypto_perpetual",
    "currency_pair",
)
_LEVELS = 20


def _price(ticks: int) -> Price:
    return Price(Decimal(ticks).scaleb(-1), 1)


def _size(units: int) -> Quantity:
    return Quantity(Decimal(units).scaleb(-3), 3)


def _walk(rng: random.Random, n: int, start: int = 600_000) -> list[int]:
    """Return a mid price in ticks of 0.1 per second: a random walk around 60,000."""
    ticks, out = start, []
    for _ in range(n):
        ticks += rng.randint(-3, 3)
        out.append(ticks)
    return out


def _book_side(rng: random.Random, best: int, step: int) -> tuple[list[int], list[int]]:
    """One side's level prices (ticks of 0.1) and sizes (units of 0.001), best first."""
    prices, sizes, level = [], [], best
    for _ in range(_LEVELS):
        prices.append(level)
        sizes.append(rng.randint(1, 5_000))
        level += step * rng.randint(1, 3)
    return prices, sizes


def snapshots(t0: int, mids: list[int], rng: random.Random) -> list[DydxSecondSnapshot]:
    rows = []
    for i, mid in enumerate(mids):
        bids, bid_sizes = _book_side(rng, mid, -1)
        asks, ask_sizes = _book_side(rng, mid + 1, 1)
        traded = rng.random() < 0.8  # a second without a trade has no OHLC (nulls)
        close = mid if traded else None
        ts = t0 + i * SEC + rng.randint(0, 999) * 1_000_000
        rows.append(
            DydxSecondSnapshot(
                instrument_id=IID,
                price_precision=PERP.price_precision,
                size_precision=PERP.size_precision,
                bid_price_units=bids,
                bid_size_units=bid_sizes,
                ask_price_units=asks,
                ask_size_units=ask_sizes,
                buy_volume_units=rng.randint(0, 3_000) if traded else 0,
                sell_volume_units=rng.randint(0, 3_000) if traded else 0,
                buy_count=rng.randint(0, 40) if traded else 0,
                sell_count=rng.randint(0, 40) if traded else 0,
                open_price_units=close,
                high_price_units=close,
                low_price_units=close,
                close_price_units=close,
                ts_event=ts,
                ts_init=t0 + (i + 1) * SEC + rng.randint(0, 999) * 1_000_000,  # the close
            )
        )
    return rows


def trades(t0: int, mids: list[int], rng: random.Random) -> list[TradeTick]:
    rows: list[TradeTick] = []
    for i, mid in enumerate(mids):
        for k in range(rng.randint(0, 3)):
            ts = t0 + i * SEC + k * 1_000_000
            side = AggressorSide.BUYER if rng.random() < 0.5 else AggressorSide.SELLER
            rows.append(
                TradeTick(
                    IID,
                    _price(mid + rng.randint(-1, 1)),
                    _size(rng.randint(1, 5_000)),
                    side,
                    TradeId(str(len(rows) + 1)),
                    ts,
                    ts + 20_000_000,
                )
            )
    return rows


def deltas(t0: int, mids: list[int], rng: random.Random) -> list[OrderBookDelta]:
    rows = []
    for i, mid in enumerate(mids):
        side = OrderSide.BUY if i % 2 else OrderSide.SELL
        order = BookOrder(side, _price(mid - rng.randint(0, 20)), _size(rng.randint(1, 900)), 0)
        ts = t0 + i * SEC
        rows.append(OrderBookDelta(IID, BookAction.UPDATE, order, 0, i + 1, ts, ts + 1_000))
    return rows


def _per_second(t0: int, mids: list[int]) -> list[object]:
    """Mark, index, funding and open interest: one row per second."""
    rows: list[object] = []
    next_funding = t0 + 8 * 3_600 * SEC
    for i, mid in enumerate(mids):
        ts = t0 + i * SEC
        rows.append(MarkPriceUpdate(IID, _price(mid), ts, ts + 1_000))
        rows.append(IndexPriceUpdate(IID, _price(mid - 2), ts, ts + 1_000))
        rate = Decimal(mid % 97).scaleb(-6)
        rows.append(FundingRateUpdate(IID, rate, ts, ts, next_funding_ns=next_funding))
        rows.append(OpenInterest(IID, Decimal(1_000_000 + mid % 5_000).scaleb(-3), ts, ts))
    return rows


def write_day(root: Path, day: int = DAY0, seconds: int = 3_600, seed: int = 30) -> None:
    """
    Write one fixture stretch of `seconds` seconds starting at 01:00 UTC of `day`, every type in
    `DATA_TYPES`, into the catalog at `root`: one file per leaf.
    """
    rng = random.Random(seed)  # noqa: S311 -- a deterministic fixture, not cryptography
    t0 = day * DAY_NS + 3_600 * SEC
    mids = _walk(rng, seconds)
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([PERP, SPOT])
    catalog.write_data(snapshots(t0, mids, rng))
    catalog.write_data(trades(t0, mids, rng))
    catalog.write_data(deltas(t0, mids, rng))
    per_second = _per_second(t0, mids)
    for cls in (MarkPriceUpdate, IndexPriceUpdate, FundingRateUpdate, OpenInterest):
        catalog.write_data([row for row in per_second if isinstance(row, cls)])
    status = [MarketStatusAction.PRE_OPEN, MarketStatusAction.TRADING, MarketStatusAction.PAUSE]
    catalog.write_data(
        [
            InstrumentStatus(IID, status[i % 3], t0 + i * 600 * SEC, t0 + i * 600 * SEC)
            for i in range(max(1, seconds // 600))
        ]
    )


def data_files(root: Path) -> dict[str, Path]:
    """Map each data type directory to the fixture's one file in it (instruments included)."""
    files = {}
    for data_type in DATA_TYPES:
        owner = {"currency_pair": SPOT.id}.get(data_type, IID)  # PERP.id is IID
        (path,) = (root / "data" / data_type / str(owner)).glob("*.parquet")
        files[data_type] = path
    return files


def instruments() -> list[Instrument]:
    return [PERP, SPOT]
