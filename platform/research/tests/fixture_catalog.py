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
The notebooks' fixture archive (Story 27.2): one small catalog, candle stores and error ledger that
every numbered notebook runs against in `test_notebooks.py`, built once per session.

Six instruments, two per venue (`INSTRUMENTS`), over the ten minutes `2026-09-01 23:55:00` ->
`2026-09-02 00:05:00` UTC (so two UTC days): per second one `DydxSecondSnapshot` with 20 levels a
side, stamped `S + 0.5 s` (the venue-time convention), a `MarkPriceUpdate` and an
`IndexPriceUpdate` at `S + 0.25 s`, and `TradeTick`s in two of every three seconds; a
`FundingRateUpdate` (with the venue's real `interval`: 60 minutes on dYdX and Hyperliquid, 480 on
Bybit) and an `OpenInterest` every minute. Every mid moves by one shared, seeded random wiggle
(`random.Random(27)`, integer ticks in [-11, 11]), so the same asset's legs co-move across venues
(`FixturePaths.same_asset`) and a lead-lag peak is unique -- a periodic wiggle would tie peaks one
period apart. Everything is written through
`ParquetDataCatalog.write_data()` as real Nautilus objects; prices and sizes are built from integer
ticks with `Price.from_raw`/`Quantity.from_raw` (never a float before the `Price`), and each
snapshot's eight trade columns are `kernel.fold.fold_trades` over that second's own ticks, so the
trade/second agreement is exact by construction. The candle stores are built by
`candles.application.rebuild.rebuild_instrument`; day 1 is marked `pass`, day 2 never is.

Deliberate defects (`FixturePaths.defects`), each written straight in, as the live gate never
would:

- **outage** -- `BTC-USD-PERP.DYDX` has no snapshot, trade, mark, index, funding or OI row for 90
  seconds (`HOLE_SECONDS`): a `likely outage`;
- **quiet market** -- `ETH-USD-PERP.HYPERLIQUID` has no trade for 50 seconds (`QUIET_SECONDS`)
  while its book and marks keep flowing;
- **crossed second** -- one `ETHUSDT-LINEAR.BYBIT` snapshot has best bid > best ask;
- **duplicate trade** -- one `BTCUSDT-LINEAR.BYBIT` trade is archived twice (a replay, same
  `trade_id`); its snapshot folds it once;
- **provisional day** -- `2026-09-02` is never reconciled (`verified_day` is `2026-09-01`);
- **leading venue** -- `BTCUSDT-LINEAR.BYBIT` reads the shared wiggle `LEAD_SECONDS` (2 s) ahead of
  every other instrument, so its 1 s returns lead dYdX's and Hyperliquid's BTC by exactly 2 s
  (Story 27.4);
- **liquidation cascade** -- `BTCUSDT-LINEAR.BYBIT` (the one fixture id with a liquidation feed)
  archives a `Liquidation` background (one LONG every 5 s, `LIQUIDATION_BACKGROUND`), then a 30 s
  LONG burst (one a second, `LIQUIDATION_BURST`), then nothing: the decay; each at the second's mid
  as its bankruptcy price, received 50 ms after its venue time (Story 33.14);
- **ledger** -- hand-written 23.3-format lines (`observability.error_ledger`'s field set): a
  restart and two `collector.crossed_book` lines (one carrying `suppressed: 3`) for `collector`, a
  `collector.book_sequence` line for `bybit_collector`, and one line before the window.

No `metrics.db` is written (spec Design Notes: research tests may not import `ranking`, and no
research code reads it); `metrics_db_path` names where one would be.
"""

import json
import random
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from candles.application.rebuild import rebuild_instrument
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from kernel.clocks import NS_PER_MS
from kernel.clocks import NS_PER_S
from kernel.fold import fold_trades
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.open_interest import OpenInterest
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.venues import venue_of

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import ETH
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


START = "2026-09-01"
END = "2026-09-03"
DATA_START_NS = int(datetime(2026, 9, 1, 23, 55, tzinfo=UTC).timestamp()) * NS_PER_S
SECONDS = 600
DATA_END_NS = DATA_START_NS + SECONDS * NS_PER_S
LEVELS = 20

OUTAGE_INSTRUMENT = "BTC-USD-PERP.DYDX"
HOLE_SECONDS = range(200, 290)
QUIET_INSTRUMENT = "ETH-USD-PERP.HYPERLIQUID"
QUIET_SECONDS = range(400, 450)
CROSSED_INSTRUMENT = "ETHUSDT-LINEAR.BYBIT"
CROSSED_SECOND = 300  # 00:00:00, the first second of day 2
DUPLICATE_INSTRUMENT = "BTCUSDT-LINEAR.BYBIT"
DUPLICATE_SECOND = 100
LEAD_INSTRUMENT = "BTCUSDT-LINEAR.BYBIT"
LEAD_SECONDS = 2
CASCADE_INSTRUMENT = "BTCUSDT-LINEAR.BYBIT"
LIQUIDATION_BACKGROUND = range(0, 400, 5)
LIQUIDATION_BURST = range(400, 430)
FUNDING_INTERVALS = {"DYDX": 60, "BYBIT": 480, "HYPERLIQUID": 60}  # minutes, as the venues publish
# The shared mid wiggle (price ticks), one entry per second plus the lead's look-ahead; a fixed
# seed, so every session builds the same archive.
_WIGGLE_RNG = random.Random(27)  # noqa: S311 -- a reproducible fixture, not a secret
_WIGGLE = tuple(_WIGGLE_RNG.randint(-11, 11) for _ in range(SECONDS + LEAD_SECONDS))


@dataclass(frozen=True)
class _Spec:
    """One fixture instrument: currencies, precisions and its mid in price ticks."""

    iid: str
    base: Currency
    quote: Currency
    settlement: Currency
    price_precision: int
    size_precision: int
    mid_ticks: int


_SPECS = (
    _Spec("BTC-USD-PERP.DYDX", BTC, USD, USDC, 1, 4, 650_000),
    _Spec("ETH-USD-PERP.DYDX", ETH, USD, USDC, 2, 3, 250_000),
    _Spec("BTCUSDT-LINEAR.BYBIT", BTC, USDT, USDT, 2, 3, 6_501_000),
    _Spec("ETHUSDT-LINEAR.BYBIT", ETH, USDT, USDT, 2, 2, 250_100),
    _Spec("BTC-USD-PERP.HYPERLIQUID", BTC, USDC, USDC, 1, 5, 650_020),
    _Spec("ETH-USD-PERP.HYPERLIQUID", ETH, USDC, USDC, 2, 4, 250_030),
)
INSTRUMENTS = tuple(spec.iid for spec in _SPECS)
# The same-asset groups, sorted by venue then id (`MarketFrames.same_symbol`'s order).
_BTC = ("BTCUSDT-LINEAR.BYBIT", "BTC-USD-PERP.DYDX", "BTC-USD-PERP.HYPERLIQUID")
_ETH = ("ETHUSDT-LINEAR.BYBIT", "ETH-USD-PERP.DYDX", "ETH-USD-PERP.HYPERLIQUID")


@dataclass(frozen=True)
class FixtureDefects:
    """What the fixture planted, in the terms `research.application.inspection` reports them."""

    outage_instrument: str
    outage: tuple[int, int]  # the `likely outage` row: the whole snapshot-seconds gap
    quiet_instrument: str
    quiet: tuple[int, int]  # the `quiet market` trade gap
    crossed_instrument: str
    crossed_ts: int
    duplicate_instrument: str
    duplicate_trade_id: str
    verified_day: str
    provisional_day: str
    lead_instrument: str  # whose 1 s returns lead its asset's other legs
    lead_seconds: int  # by how many seconds
    ledger_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    ledger_restarts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class FixturePaths:
    """Where the fixture lives, the window a notebook reads and the defects it must show."""

    catalog_path: str
    candles_dir: str
    errors_dir: str
    metrics_db_path: str
    instruments: tuple[str, ...]
    start: str
    end: str
    defects: FixtureDefects
    # Each instrument -> every fixture id of the same asset (`kernel.venues.asset_key`), itself
    # included, sorted by venue then id: what `MarketFrames.same_symbol` must return.
    same_asset: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def env(self) -> dict[str, str]:
        """Return the notebooks' environment (`research/notebooks/_params.py`) for this fixture."""
        return {
            "CATALOG_PATH": self.catalog_path,
            "CANDLES_DIR": self.candles_dir,
            "METRICS_DB_PATH": self.metrics_db_path,
            "ERRORS_DIR": self.errors_dir,
            "INSTRUMENTS": ",".join(self.instruments),
            "START": self.start,
            "END": self.end,
        }


def _second_ns(second: int) -> int:
    return DATA_START_NS + second * NS_PER_S


def _price(ticks: int, precision: int) -> Price:
    return Price.from_raw(ticks * 10 ** (FIXED_PRECISION - precision), precision)


def _size(ticks: int, precision: int) -> Quantity:
    return Quantity.from_raw(ticks * 10 ** (FIXED_PRECISION - precision), precision)


def _mid(spec: _Spec, second: int) -> int:
    """
    Return the spec's mid plus the shared seeded wiggle, in price ticks; the lead instrument reads
    the wiggle `LEAD_SECONDS` ahead.
    """
    ahead = LEAD_SECONDS if spec.iid == LEAD_INSTRUMENT else 0
    return spec.mid_ticks + _WIGGLE[second + ahead]


def _book(spec: _Spec, second: int) -> tuple[list[int], list[int]]:
    """Bid and ask price ticks, best first; the crossed defect's bids sit above its asks."""
    mid = _mid(spec, second)
    asks = [mid + 1 + i for i in range(LEVELS)]
    if spec.iid == CROSSED_INSTRUMENT and second == CROSSED_SECOND:
        return [mid + 2 - i for i in range(LEVELS)], asks
    return [mid - 1 - i for i in range(LEVELS)], asks


def _collected(spec: _Spec, second: int) -> bool:
    return not (spec.iid == OUTAGE_INSTRUMENT and second in HOLE_SECONDS)


def _trades(spec: _Spec, second: int) -> list[TradeTick]:
    """Trades in two of every three seconds (two when `second % 5 == 0`), at the touch."""
    if second % 3 == 0 or (spec.iid == QUIET_INSTRUMENT and second in QUIET_SECONDS):
        return []
    iid = InstrumentId.from_str(spec.iid)
    bids, asks = _book(spec, second)
    trades = []
    for k in range(2 if second % 5 == 0 else 1):
        buyer = (second + k) % 2 == 1
        ts_event = _second_ns(second) + (100 + 300 * k) * NS_PER_MS
        trades.append(
            TradeTick(
                iid,
                _price(asks[0] if buyer else bids[0], spec.price_precision),
                _size(
                    (1 + (second + k) % 7) * 10 ** (spec.size_precision - 2), spec.size_precision
                ),
                AggressorSide.BUYER if buyer else AggressorSide.SELLER,
                TradeId(f"{spec.iid.split('.')[0]}-{second}-{k}"),
                ts_event,
                ts_event + 40 * NS_PER_MS,
            )
        )
    return trades


def _replayed(trade: TradeTick) -> TradeTick:
    """Return the same trade archived again, received 5 ms later (a reconnect replay)."""
    return TradeTick(
        trade.instrument_id,
        trade.price,
        trade.size,
        trade.aggressor_side,
        trade.trade_id,
        trade.ts_event,
        trade.ts_init + 5 * NS_PER_MS,
    )


def _snapshot(spec: _Spec, second: int, trades: list[TradeTick]) -> DydxSecondSnapshot:
    """Build the row capture would encode: exact levels and the fold, at the definition's precisions."""
    bids, asks = _book(spec, second)
    ts_event = _second_ns(second) + 500 * NS_PER_MS
    sizes = [
        _size((5 + (i + second) % 11) * 10 ** (spec.size_precision - 1), spec.size_precision)
        for i in range(LEVELS)
    ]
    pp, sp = spec.price_precision, spec.size_precision
    return DydxSecondSnapshot.from_levels(
        InstrumentId.from_str(spec.iid),
        pp,
        sp,
        [(_price(t, pp), q) for t, q in zip(bids, sizes, strict=True)],
        [(_price(t, pp), q) for t, q in zip(asks, reversed(sizes), strict=True)],
        fold_trades(trades).snapshot_units(pp, sp),
        ts_event=ts_event,
        ts_init=ts_event + (500 + (second % 5) * 100) * NS_PER_MS,
    )


def _instrument(spec: _Spec) -> CryptoPerpetual:
    symbol = spec.iid.split(".")[0]
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(spec.iid),
        raw_symbol=Symbol(symbol),
        base_currency=spec.base,
        quote_currency=spec.quote,
        settlement_currency=spec.settlement,
        is_inverse=False,
        price_precision=spec.price_precision,
        size_precision=spec.size_precision,
        price_increment=_price(1, spec.price_precision),
        size_increment=_size(1, spec.size_precision),
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.05"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0005"),
        ts_event=0,
        ts_init=0,
    )


@dataclass
class _Rows:
    """One instrument's rows, by type, in `ts_init` order."""

    snapshots: list[DydxSecondSnapshot] = field(default_factory=list)
    trades: list[TradeTick] = field(default_factory=list)
    marks: list[MarkPriceUpdate] = field(default_factory=list)
    indexes: list[IndexPriceUpdate] = field(default_factory=list)
    funding: list[FundingRateUpdate] = field(default_factory=list)
    open_interest: list[OpenInterest] = field(default_factory=list)
    liquidations: list[Liquidation] = field(default_factory=list)


def _liquidation(spec: _Spec, second: int) -> Liquidation:
    """One LONG liquidation of 0.001 to 0.003 base at the second's mid, received 50 ms later."""
    ts_event = _second_ns(second) + 300 * NS_PER_MS
    return Liquidation(
        instrument_id=InstrumentId.from_str(spec.iid),
        side=LiquidatedSide.LONG,
        size_units=(1 + second % 3) * 10 ** (spec.size_precision - 3),
        price_units=_mid(spec, second),
        price_precision=spec.price_precision,
        size_precision=spec.size_precision,
        venue_event_id=f"fixture-{second}",
        ts_event=ts_event,
        ts_init=ts_event + 50 * NS_PER_MS,
    )


def _liquidations(spec: _Spec) -> list[Liquidation]:
    if spec.iid != CASCADE_INSTRUMENT:
        return []
    seconds = (*LIQUIDATION_BACKGROUND, *LIQUIDATION_BURST)
    return [_liquidation(spec, second) for second in seconds]


def _rows(spec: _Spec) -> _Rows:
    iid = InstrumentId.from_str(spec.iid)
    rows = _Rows()
    for second in (s for s in range(SECONDS) if _collected(spec, s)):
        trades = _trades(spec, second)
        rows.snapshots.append(_snapshot(spec, second, trades))
        if spec.iid == DUPLICATE_INSTRUMENT and second == DUPLICATE_SECOND:
            trades = [trades[0], _replayed(trades[0]), *trades[1:]]
        rows.trades += trades
        stamp = _second_ns(second) + 250 * NS_PER_MS
        mid = _mid(spec, second)
        rows.marks.append(MarkPriceUpdate(iid, _price(mid, spec.price_precision), stamp, stamp))
        rows.indexes.append(
            IndexPriceUpdate(iid, _price(mid + 1, spec.price_precision), stamp, stamp)
        )
        if second % 60 == 0:
            at = _second_ns(second)
            interval = FUNDING_INTERVALS[venue_of(spec.iid)]
            rows.funding.append(
                FundingRateUpdate(iid, Decimal("0.0001"), at, at, interval=interval)
            )
            rows.open_interest.append(OpenInterest(iid, Decimal(f"{1_000 + second}.5"), at, at))
    rows.trades.sort(key=lambda t: t.ts_init)
    rows.liquidations = _liquidations(spec)
    return rows


def _write(catalog: ParquetDataCatalog, rows: _Rows) -> None:
    # One snapshot file per minute, as the live flush writes several: the coverage timeline then
    # shows the hole as missing file spans.
    for minute in range(SECONDS // 60):
        lo, hi = _second_ns(minute * 60), _second_ns((minute + 1) * 60)
        chunk = [s for s in rows.snapshots if lo <= s.ts_event < hi]
        if chunk:
            catalog.write_data(chunk)
    for data in (rows.trades, rows.marks, rows.indexes, rows.funding, rows.open_interest):
        catalog.write_data(data)
    if rows.liquidations:
        catalog.write_data(rows.liquidations)


def _gap_around(stamps: list[int], lo: int, hi: int) -> tuple[int, int]:
    """(last stamp before `lo`, first stamp at or after `hi`): how `find_gaps` reports a hole."""
    return max(t for t in stamps if t < lo), min(t for t in stamps if t >= hi)


def _defects(rows: dict[str, _Rows], ledger: tuple[dict, dict]) -> FixtureDefects:
    hole = (_second_ns(HOLE_SECONDS.start), _second_ns(HOLE_SECONDS.stop))
    outage_rows = rows[OUTAGE_INSTRUMENT]
    seconds_gap = _gap_around([s.ts_event for s in outage_rows.snapshots], *hole)
    quiet = _gap_around(
        [t.ts_event for t in rows[QUIET_INSTRUMENT].trades],
        _second_ns(QUIET_SECONDS.start),
        _second_ns(QUIET_SECONDS.stop),
    )
    return FixtureDefects(
        outage_instrument=OUTAGE_INSTRUMENT,
        outage=seconds_gap,
        quiet_instrument=QUIET_INSTRUMENT,
        quiet=quiet,
        crossed_instrument=CROSSED_INSTRUMENT,
        crossed_ts=_second_ns(CROSSED_SECOND) + 500 * NS_PER_MS,
        duplicate_instrument=DUPLICATE_INSTRUMENT,
        duplicate_trade_id=f"{DUPLICATE_INSTRUMENT.split('.')[0]}-{DUPLICATE_SECOND}-0",
        verified_day="2026-09-01",
        provisional_day="2026-09-02",
        lead_instrument=LEAD_INSTRUMENT,
        lead_seconds=LEAD_SECONDS,
        ledger_counts=ledger[0],
        ledger_restarts=ledger[1],
    )


def _ledger_line(ts_ns: int, service: str, site: str, suppressed: int = 0) -> dict:
    line = {
        "ts_ns": ts_ns,
        "service": service,
        "pid": 1,
        "site": site,
        "detail": f"fixture {site}",
        "exc_type": None,
        "suppressed": suppressed,
    }
    return {**line, "revision": None} if site == "process_start" else line


def _write_ledger(errors_dir: Path) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """Write the 23.3 ledger files; return the counts and restarts the window must report."""
    before_window = int(datetime(2026, 8, 31, 12, tzinfo=UTC).timestamp()) * NS_PER_S
    files = {
        "collector": [
            _ledger_line(before_window, "collector", "collector.crossed_book"),
            _ledger_line(_second_ns(10), "collector", "process_start"),
            _ledger_line(_second_ns(30), "collector", "collector.crossed_book"),
            _ledger_line(_second_ns(400), "collector", "collector.crossed_book", suppressed=3),
        ],
        "bybit_collector": [
            _ledger_line(_second_ns(200), "bybit_collector", "collector.book_sequence"),
        ],
    }
    errors_dir.mkdir(parents=True)
    for service, lines in files.items():
        text = "".join(json.dumps(line) + "\n" for line in lines)
        (errors_dir / f"{service}.jsonl").write_text(text)
    counts = {
        ("collector", "collector.crossed_book"): 5,
        ("bybit_collector", "collector.book_sequence"): 1,
    }
    return counts, {"collector": 1}


def _build_candles(catalog_path: str, candles_dir: Path, verified_day: str) -> None:
    start_ns = int(datetime(2026, 9, 1, tzinfo=UTC).timestamp()) * NS_PER_S
    end_ns = int(datetime(2026, 9, 3, tzinfo=UTC).timestamp()) * NS_PER_S
    candles_dir.mkdir(parents=True)
    for iid in INSTRUMENTS:
        db_path = db_path_for_venue(candles_dir, venue_of(iid))
        rebuild_instrument(db_path, catalog_path, iid, start_ns, end_ns)
        store = CandleStore(db_path)
        store.mark_verified(iid, verified_day, "pass", 0, 1_788_000_000_000)
        store.close()


def build(root: Path) -> FixturePaths:
    """Write the fixture archive under `root` and return its paths, window and defects."""
    catalog_path = str(root / "catalog")
    catalog = ParquetDataCatalog(catalog_path)
    catalog.write_data([_instrument(spec) for spec in _SPECS])
    rows = {spec.iid: _rows(spec) for spec in _SPECS}
    for instrument_rows in rows.values():
        _write(catalog, instrument_rows)
    defects = _defects(rows, _write_ledger(root / "errors"))
    _build_candles(catalog_path, root / "candles", defects.verified_day)
    return FixturePaths(
        catalog_path=catalog_path,
        candles_dir=str(root / "candles"),
        errors_dir=str(root / "errors"),
        metrics_db_path=str(root / "metrics" / "metrics.db"),
        instruments=INSTRUMENTS,
        start=START,
        end=END,
        defects=defects,
        same_asset={
            "BTC-USD-PERP.DYDX": _BTC,
            "BTCUSDT-LINEAR.BYBIT": _BTC,
            "BTC-USD-PERP.HYPERLIQUID": _BTC,
            "ETH-USD-PERP.DYDX": _ETH,
            "ETHUSDT-LINEAR.BYBIT": _ETH,
            "ETH-USD-PERP.HYPERLIQUID": _ETH,
        },
    )
