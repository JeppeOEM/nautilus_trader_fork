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
`OFIStrategy`'s own z-score series in a real backtest (Story 31.9, AC3): a recording subclass run
by a `BacktestNode` over a `write_data` catalog appends `(ts_event, ts_init, value, initialized)`
of its `MultiLevelOFI` after every `on_data`, and that series is compared with

- `research.application.microstructure.ofi_readings` (the research replay of the same strategy):
  exactly equal on every row where the replay has a reading. A row where it has none (NaN: the
  first row, the baseline after a gap over `OFI_GAP_NS`, a one-sided row) is the pinned class
  `carried`: the strategy's value there is the previous row's (its documented Known limit, which
  `ofi_readings`' docstring names), and 0.0/uninitialised before the first reading;
- the independent reference `rolling_ofi_z(..., usd=True, ...)` within `signal_compare.REL_TOL`,
  an undefined z-score (under 2 readings, or a flat window) published as 0.0 judged `pinned_zero`.

The rows the node delivers, in delivery (`ts_init`) order, must be the rows in `ts_event` order.

Fixture variant (always runs): the committed real rows of Bybit BTCUSDT linear and Hyperliquid SOL
(300 each) at the strategy's defaults (10 levels, window 20, z-window 300) and at a short z-window
(20). Soak variant: `VERIFY_SOAK_CATALOG` + `VERIFY_SOAK_DAY` (YYYY-MM-DD), every instrument with
stored snapshots, that UTC day's rows (bounded reads), the defaults; it only reads the catalog.

A declared composition root (`tests/test_boundaries.py`): it drives research's strategy and replay.
"""

import math
import os
import resource
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import UTC
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import NamedTuple

import pytest
from kernel.indicators import OFI_GAP_NS
from kernel.second_snapshot import DydxSecondSnapshot
from research.application.frames import CatalogFrames
from research.application.microstructure import ofi_readings
from research.strategies.ofi_strategy import OFIStrategy
from research.strategies.ofi_strategy import OFIStrategyConfig

from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.core.data import Data
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification.domain import reference_signals as ref
from verification.domain.reference_signals import RefBook
from verification.domain.signal_compare import FAILING
from verification.domain.signal_compare import Tally
from verification.tests.signal_cases import NS_PER_S
from verification.tests.signal_cases import load_fixture
from verification.tests.signal_cases import pinned_zero


class Reading(NamedTuple):
    """What the strategy held after one `on_data`: its OFI z-score and whether it is initialised."""

    ts_event: int
    ts_init: int
    value: float
    initialized: bool


class RecordingOFI(OFIStrategy):
    """`OFIStrategy` unchanged, plus the record of its indicator after every snapshot it handles."""

    def __init__(self, config: OFIStrategyConfig) -> None:
        super().__init__(config)
        self.readings: list[Reading] = []

    def on_data(self, data: Data) -> None:
        super().on_data(data)
        if isinstance(data, DydxSecondSnapshot):
            self.readings.append(
                Reading(data.ts_event, data.ts_init, self._ofi.value, self._ofi.initialized)
            )


_STRATEGY_PATH = f"{__name__}:RecordingOFI"
_CONFIG_PATH = "research.strategies.ofi_strategy:OFIStrategyConfig"
_SNAPSHOT_PATH = "kernel.second_snapshot:DydxSecondSnapshot"
# Out of every z-score's reach (|z| <= sqrt(n - 1) over n readings): the run never submits an
# order, and nothing it records depends on the threshold.
_NEVER = 1e9
DEFAULTS = OFIStrategyConfig(instrument_id=InstrumentId.from_str("X-USD-PERP.HYPERLIQUID"))
FIXTURE_IDS = ("BTCUSDT-LINEAR.BYBIT", "SOL-USD-PERP.HYPERLIQUID")
SHORT_Z_WINDOW = 20
_DAY_NS = 86_400 * NS_PER_S


# --- the backtest ---------------------------------------------------------------------------------


def _strategy_config(iid: str, z_window: int) -> dict[str, Any]:
    return {
        "instrument_id": iid,
        "ofi_zscore_window": z_window,
        "ofi_threshold": _NEVER,
    }


def _currency(instrument: Instrument) -> Currency:
    settlement: Currency | None = getattr(instrument, "settlement_currency", None)
    return settlement or instrument.quote_currency


# `BacktestDataConfig`'s `start_time`/`end_time` on `ts_init`, inclusive; None leaves one open.
type Bounds = tuple[int | None, int | None]


def _run_config(
    catalog: str, instrument: Instrument, z_window: int, bounds: Bounds
) -> BacktestRunConfig:
    iid, venue, currency = str(instrument.id), str(instrument.id.venue), _currency(instrument)
    strategy = ImportableStrategyConfig(
        strategy_path=_STRATEGY_PATH,
        config_path=_CONFIG_PATH,
        config=_strategy_config(iid, z_window),
    )
    return BacktestRunConfig(
        engine=BacktestEngineConfig(
            logging=LoggingConfig(bypass_logging=True), strategies=[strategy]
        ),
        venues=[
            BacktestVenueConfig(
                name=venue,
                oms_type="NETTING",
                account_type="MARGIN",
                base_currency=str(currency),
                starting_balances=[f"10000 {currency}"],
            )
        ],
        data=[
            # `BacktestNode` adds instruments only for a Nautilus data type's config: this one
            # loads the definition (the strategy stops on start without it) and, the catalog
            # holding no quotes, no data at all.
            BacktestDataConfig(
                catalog_path=catalog,
                data_cls=QuoteTick,
                instrument_id=iid,
                start_time=bounds[0],
                end_time=bounds[1],
            ),
            BacktestDataConfig(
                catalog_path=catalog,
                data_cls=_SNAPSHOT_PATH,
                instrument_id=iid,
                client_id=venue,  # a custom type needs a (bookkeeping) client id
                start_time=bounds[0],
                end_time=bounds[1],
            ),
        ],
        raise_exception=True,
        dispose_on_completion=False,
    )


def _backtest(catalog: str, instrument: Instrument, z_window: int, bounds: Bounds) -> list[Reading]:
    """Run the recording strategy through a real `BacktestNode`; return what it recorded."""
    config = _run_config(catalog, instrument, z_window, bounds)
    node = BacktestNode(configs=[config])
    try:
        node.run()
        engine = node.get_engine(config.id)
        assert engine is not None, f"no engine for run config {config.id}"
        (strategy,) = engine.trader.strategies()
        assert isinstance(strategy, RecordingOFI)
        return list(strategy.readings)
    finally:
        node.dispose()


# --- the comparison -------------------------------------------------------------------------------


@dataclass
class Report:
    """
    One instrument's comparison. Invariant: every recorded row lands in exactly one of `exact`,
    `carried`, `mismatch` against `ofi_readings`, and is judged once against the reference.
    """

    rows: int = 0
    exact: int = 0
    carried: int = 0
    mismatch: int = 0
    max_rel_diff: float = 0.0
    reference: Tally = field(default_factory=Tally)

    def line(self, name: str) -> str:
        judged = self.reference.report()[0] if self.reference.signals() else "none"
        return (
            f"{name}: rows={self.rows} compared={self.exact + self.mismatch} exact={self.exact} "
            f"baseline_carried={self.carried} mismatch={self.mismatch} "
            f"max_rel_diff={self.max_rel_diff:.3e} {judged}"
        )


def _replayed(catalog: str, iid: str, readings: list[Reading], config: OFIStrategyConfig) -> Any:
    """`ofi_readings` over exactly the delivered rows' `ts_event` span, read by research."""
    start, end = readings[0].ts_event, readings[-1].ts_event + 1
    seconds = CatalogFrames(catalog, "").seconds(iid, start=start, end=end)
    return ofi_readings(seconds, config)


def _books(catalog: str, iid: str, readings: list[Reading]) -> list[RefBook]:
    """Return the same rows, decoded by the reference from their stored integers."""
    start, end = readings[0].ts_event, readings[-1].ts_event + 1
    rows = CatalogFrames(catalog, "").objects(DydxSecondSnapshot, iid, start=start, end=end)
    return [RefBook.from_stored(DydxSecondSnapshot.to_dict(r)) for r in rows]


def _judge_replay(report: Report, readings: list[Reading], replay_z: list[float]) -> None:
    previous = Reading(0, 0, 0.0, False)
    for reading, z in zip(readings, replay_z, strict=True):
        if math.isnan(z):  # no reading: the strategy keeps what it held (the pinned class)
            same = (reading.value, reading.initialized) == (previous.value, previous.initialized)
            report.carried += int(same)
            report.mismatch += int(not same)
        elif reading.value == z and reading.initialized:
            report.exact += 1
        else:
            report.mismatch += 1
        previous = reading


def _judge_reference(
    report: Report, readings: list[Reading], books: list[RefBook], c: OFIStrategyConfig
) -> None:
    """`rolling_ofi_z` over the two-sided rows (the strategy handles no other)."""
    two_sided = [(r, b) for r, b in zip(readings, books, strict=True) if b.two_sided]
    expected = ref.rolling_ofi_z(
        [b for _, b in two_sided], c.ofi_levels, c.ofi_window, True, OFI_GAP_NS, c.ofi_zscore_window
    )
    for (reading, _), want in zip(two_sided, expected, strict=True):
        report.reference.record("ofi_z", pinned_zero(reading.value, want))
        if want is not None and want != 0.0:
            gap = abs(Decimal(reading.value) - Decimal(want)) / abs(Decimal(want))
            report.max_rel_diff = max(report.max_rel_diff, float(gap))


def _compare(catalog: str, iid: str, readings: list[Reading], z_window: int) -> Report:
    config = OFIStrategyConfig(instrument_id=InstrumentId.from_str(iid), ofi_zscore_window=z_window)
    replay = _replayed(catalog, iid, readings, config)
    books = _books(catalog, iid, readings)
    assert replay["ts_event"].tolist() == [r.ts_event for r in readings], "not the rows fed"
    assert [b.ts_event for b in books] == [r.ts_event for r in readings], "not the rows fed"
    report = Report(rows=len(readings))
    _judge_replay(report, readings, replay["ofi_z"].tolist())
    _judge_reference(report, readings, books, config)
    return report


def _assert_parity(report: Report, readings: list[Reading]) -> None:
    assert [r.ts_init for r in readings] == sorted(r.ts_init for r in readings)
    stamps = [r.ts_event for r in readings]
    assert stamps == sorted(set(stamps)), "delivery (ts_init) order is not ts_event order"
    assert report.mismatch == 0, "the strategy's z-score differs from ofi_readings"
    assert report.exact > 0, "nothing was compared"
    failing = sum(report.reference.count("ofi_z", v) for v in FAILING)
    assert failing == 0, "the strategy's z-score differs from the reference"


# --- fixture variant ------------------------------------------------------------------------------


def _instrument(iid: str, row: dict[str, Any]) -> CryptoPerpetual:
    """Return a perpetual at the row's own precisions (the definition the rows were cut at)."""
    instrument_id = InstrumentId.from_str(iid)
    p, s = row["price_precision"], row["size_precision"]
    quote = USDT if instrument_id.venue.value == "BYBIT" else USDC
    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(instrument_id.symbol.value),
        base_currency=Currency.from_str(iid.split("-")[0].removesuffix("USDT")),
        quote_currency=quote,
        settlement_currency=quote,
        is_inverse=False,
        price_precision=p,
        size_precision=s,
        price_increment=Price.from_str(str(Decimal(1).scaleb(-p))),
        size_increment=Quantity.from_str(str(Decimal(1).scaleb(-s))),
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


@pytest.fixture(scope="module", params=FIXTURE_IDS)
def fixture_catalog(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[str, Instrument]:
    iid: str = request.param
    rows = load_fixture(iid)
    root = str(tmp_path_factory.mktemp(iid.replace(".", "_")))
    catalog = ParquetDataCatalog(root)
    instrument = _instrument(iid, rows[0])
    catalog.write_data([instrument])
    catalog.write_data([DydxSecondSnapshot.from_dict(r) for r in rows])
    return root, instrument


@pytest.mark.parametrize("z_window", [DEFAULTS.ofi_zscore_window, SHORT_Z_WINDOW])
def test_the_backtest_z_score_equals_the_replay_and_the_reference(
    nautilus_log_guard: None, fixture_catalog: tuple[str, Instrument], z_window: int
) -> None:
    catalog, instrument = fixture_catalog
    readings = _backtest(catalog, instrument, z_window, (None, None))
    report = _compare(catalog, str(instrument.id), readings, z_window)
    print("\n" + report.line(f"{instrument.id} z={z_window}"))
    assert report.rows == 300
    _assert_parity(report, readings)
    assert report.carried == 1  # the first row: a 1 s fixture has no gap and no one-sided row


def test_the_strategy_defaults_are_the_documented_ones() -> None:
    """The fixture variant's "defaults" are the strategy's own (10 levels, window 20, z 300)."""
    assert (DEFAULTS.ofi_levels, DEFAULTS.ofi_window, DEFAULTS.ofi_zscore_window) == (10, 20, 300)


# --- soak variant ---------------------------------------------------------------------------------

SOAK = pytest.mark.skipif(
    not (os.environ.get("VERIFY_SOAK_CATALOG") and os.environ.get("VERIFY_SOAK_DAY")),
    reason="runs a stored day: set VERIFY_SOAK_CATALOG and VERIFY_SOAK_DAY (YYYY-MM-DD)",
)


def _soak_ids(catalog: str) -> list[str]:
    snapshots = Path(catalog) / "data" / "custom_dydx_second_snapshot"
    return sorted(p.name for p in snapshots.iterdir() if p.is_dir())


def _day_bounds(day: str) -> Bounds:
    """Return the day on `ts_init` (the replay clock), inclusive, as `BacktestDataConfig` takes."""
    start = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()) * NS_PER_S
    return start, start + _DAY_NS - 1


def _soak_one(catalog: str, iid: str, bounds: Bounds) -> Report:
    (instrument,) = ParquetDataCatalog(catalog).instruments(instrument_ids=[iid])
    started = time.monotonic()
    readings = _backtest(catalog, instrument, DEFAULTS.ofi_zscore_window, bounds)
    report = _compare(catalog, iid, readings, DEFAULTS.ofi_zscore_window)
    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"\n{report.line(iid)} runtime={time.monotonic() - started:.1f}s peak_rss={rss_mb:.0f}MB")
    _assert_parity(report, readings)
    return report


@SOAK
def test_the_soak_day_backtest_z_score_equals_the_replay_and_the_reference(
    nautilus_log_guard: None,
) -> None:
    catalog, day = os.environ["VERIFY_SOAK_CATALOG"], os.environ["VERIFY_SOAK_DAY"]
    bounds = _day_bounds(day)
    ids = _soak_ids(catalog)
    assert ids, f"no stored snapshots under {catalog}"
    reports = {iid: _soak_one(catalog, iid, bounds) for iid in ids}
    assert all(r.rows > 0 for r in reports.values()), "an instrument has no row that day"
