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
Story 30.2: `archive.tools.migrate_snapshot_ints` rewrites closed float-layout snapshot files in
the exact integer layout -- report first, `--apply` under the lock, refusing (and ledgering) any
value it cannot snap exactly, idempotent, open day untouched.

The legacy catalog is written straight with pyarrow (`legacy_snapshot_fixture`): the kernel has
no float write path any more. Its floats are what capture stored, `Price.as_double()` of exact
prices, noise included (`85891.90000000001`); instrument definitions go through `write_data`.
"""

import random
import time
from decimal import Decimal
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from kernel.catalog_files import query_second_ohlc
from kernel.catalog_files import query_top_of_book
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from archive.infrastructure.catalog_files import encoded_size
from archive.infrastructure.compact_parquet import is_compact
from archive.infrastructure.maintenance_lock import maintenance
from archive.tests.legacy_snapshot_fixture import LegacyRow
from archive.tests.legacy_snapshot_fixture import legacy_table
from archive.tests.legacy_snapshot_fixture import write_legacy
from archive.tools import migrate_snapshot_ints as migrate
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.backtest.node import BacktestDataConfig
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.node import BacktestRunConfig
from nautilus_trader.backtest.node import BacktestVenueConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_S = 1_000_000_000
_DAY_NS = 86_400 * _S
_DAY0 = 20_000 * _DAY_NS  # a closed UTC day
_IID = "BTCUSDT-LINEAR.BYBIT"
_HL_IID = "BTC-USD-PERP.HYPERLIQUID"
_PP, _SP = 1, 3  # the definitions' precisions
_LEVELS = 20
_NOW = time.time_ns()


@pytest.fixture(autouse=True)
def _pinned_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """One "now" for the open-day file's stamp and the tool's clock (a run across midnight)."""
    monkeypatch.setattr(migrate.time, "time_ns", lambda: _NOW)


def _definition(iid: str, pp: int, sp: int, ts_init: int) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(iid.split(".")[0]),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=pp,
        price_increment=Price(Decimal(1).scaleb(-pp), pp),
        size_precision=sp,
        size_increment=Quantity(Decimal(1).scaleb(-sp), sp),
        ts_event=ts_init,
        ts_init=ts_init,
    )


def _as_double(units: int, precision: int) -> float:
    """Return what capture stored: `Price.as_double()` of the exact price (float noise included)."""
    return Price.from_raw(units * 10 ** (16 - precision), precision).as_double()


def _row(rng: random.Random, ts_event: int, mid: int) -> LegacyRow:
    """One float-layout row around `mid` (price units at `_PP`, sizes in units at `_SP`)."""
    bids, asks = [mid], [mid + 1]
    for _ in range(_LEVELS - 1):
        bids.append(bids[-1] - rng.randint(1, 3))
        asks.append(asks[-1] + rng.randint(1, 3))
    traded = rng.random() < 0.8
    close = mid if traded else None
    return LegacyRow(
        ts_event=ts_event,
        bid_prices=[_as_double(u, _PP) for u in bids],
        bid_sizes=[_as_double(rng.randint(1, 5_000), _SP) for _ in bids],
        ask_prices=[_as_double(u, _PP) for u in asks],
        ask_sizes=[_as_double(rng.randint(1, 5_000), _SP) for _ in asks],
        buy_volume=_as_double(rng.randint(0, 3_000), _SP) if traded else 0.0,
        sell_volume=_as_double(rng.randint(0, 3_000), _SP) if traded else 0.0,
        buy_count=rng.randint(0, 40) if traded else 0,
        sell_count=rng.randint(0, 40) if traded else 0,
        ohlc=(None,) * 4 if close is None else (_as_double(close, _PP),) * 4,
        ts_init=ts_event + _S // 2,
    )


def _rows(start_ns: int, seconds: int, seed: int = 302) -> list[LegacyRow]:
    rng = random.Random(seed)  # noqa: S311 -- a deterministic fixture, not cryptography
    mid, rows = 858_919, []  # 85891.9: a float-noisy price
    for i in range(seconds):
        mid += rng.randint(-3, 3)
        rows.append(_row(rng, start_ns + i * _S + 500_000_000, mid))
    return rows


def _catalog(tmp_path: Path, seconds: int = 600) -> tuple[Path, Path]:
    """Write a closed float-layout day of `_IID` (one file) and its definition; return both."""
    root = tmp_path / "catalog"
    ParquetDataCatalog(str(root)).write_data([_definition(_IID, _PP, _SP, 0)])
    return root, write_legacy(root, _IID, _rows(_DAY0 + 3_600 * _S, seconds))


def _snapshots(root: Path, iid: str = _IID) -> list[DydxSecondSnapshot]:
    rows = ParquetDataCatalog(str(root)).query(DydxSecondSnapshot, identifiers=[iid])
    return [r.data if hasattr(r, "data") else r for r in rows]


def test_a_report_changes_nothing_and_projects_the_totals_per_venue(tmp_path: Path) -> None:
    root, path = _catalog(tmp_path)
    before = path.read_bytes()
    stats = migrate.run(None, str(root), None, _NOW)
    assert path.read_bytes() == before
    bybit = stats.by_venue["BYBIT"]
    assert (bybit.files, bybit.rows) == (1, 600)
    assert bybit.snapped > 0  # the float noise the migration removes
    assert bybit.bytes_before == len(before)
    assert 0 < bybit.bytes_after < bybit.bytes_before
    assert migrate.main(["--catalog", str(root)]) == 0
    assert path.read_bytes() == before


def test_apply_rewrites_every_closed_file_to_exact_integers(tmp_path: Path) -> None:
    root, path = _catalog(tmp_path)
    original = pq.read_table(path)
    assert migrate.main(["--catalog", str(root), "--apply"]) == 0
    migrated = pq.read_table(path)
    assert migrated.schema.names == DydxSecondSnapshot.schema().names
    assert set(migrated.column("price_precision").to_pylist()) == {_PP}
    assert set(migrated.column("size_precision").to_pylist()) == {_SP}
    assert is_compact(path)
    decoded = _snapshots(root)
    for old, new in zip(original.to_pylist(), decoded, strict=True):
        # The fixture day decodes to the old layout's floats rounded to the instrument precision.
        assert new.bid_prices == [round(v, _PP) for v in old["bid_prices"]]
        assert new.ask_sizes == [round(v, _SP) for v in old["ask_sizes"]]
        assert new.close_price == (
            None if old["close_price"] is None else round(old["close_price"], _PP)
        )
        assert new.buy_volume == round(old["buy_volume"], _SP)
        assert (new.ts_event, new.ts_init, new.buy_count) == (
            old["ts_event"],
            old["ts_init"],
            old["buy_count"],
        )
    assert decoded[0].exact.bid_prices[0] == Price.from_str(
        str(round(original.column("bid_prices")[0][0].as_py(), 1))
    )


def test_the_migrated_file_is_smaller_than_its_30_1_float_version(tmp_path: Path) -> None:
    root, path = _catalog(tmp_path)
    float_compact = encoded_size(pq.read_table(path))  # the float layout at 30.1's settings
    assert migrate.main(["--catalog", str(root), "--apply"]) == 0
    assert path.stat().st_size < float_compact


def test_a_second_run_does_nothing(tmp_path: Path) -> None:
    root, path = _catalog(tmp_path)
    assert migrate.main(["--catalog", str(root), "--apply"]) == 0
    after_first = path.read_bytes()
    with maintenance(root) as writer:
        assert writer is not None
        stats = migrate.run(writer, str(root), None, _NOW)
    assert (stats.total().files, stats.already_integer) == (0, 1)
    assert path.read_bytes() == after_first


def test_an_off_grid_value_refuses_the_file_and_is_ledgered_once(tmp_path: Path) -> None:
    error_ledger.reset()
    root, good = _catalog(tmp_path)
    rows = _rows(_DAY0 + 7_200 * _S, 5, seed=9)
    rows[2].bid_prices[3] = rows[2].bid_prices[3] + 0.05  # half a price unit: a genuine digit
    bad = write_legacy(root, _IID, rows)
    before = bad.read_bytes()
    assert migrate.main(["--catalog", str(root), "--apply"]) == 2
    assert bad.read_bytes() == before  # never rounded, left as it was
    assert "price_precision" in pq.read_schema(good).names  # the run went on
    assert error_ledger.counts() == {"migrate_snapshot_ints.off_grid": 1}
    detail = error_ledger.last_details()["migrate_snapshot_ints.off_grid"]
    assert bad.name in detail
    assert repr(rows[2].bid_prices[3]) in detail


def test_a_row_older_than_every_definition_refuses_the_file(tmp_path: Path) -> None:
    error_ledger.reset()
    root = tmp_path / "catalog"
    ParquetDataCatalog(str(root)).write_data([_definition(_IID, _PP, _SP, _DAY0 + 3_700 * _S)])
    path = write_legacy(root, _IID, _rows(_DAY0 + 3_600 * _S, 200))
    before = path.read_bytes()
    assert migrate.main(["--catalog", str(root), "--apply"]) == 2
    assert path.read_bytes() == before
    assert error_ledger.counts() == {"migrate_snapshot_ints.error": 1}
    assert (
        "predates every stored instrument definition"
        in (error_ledger.last_details()["migrate_snapshot_ints.error"])
    )


def test_each_row_takes_the_definition_valid_at_its_ts_event(tmp_path: Path) -> None:
    """A precision change mid-file: each row is stored at the definition valid when it happened."""
    root = tmp_path / "catalog"
    switch = _DAY0 + 3_600 * _S + 3 * _S
    catalog = ParquetDataCatalog(str(root))
    catalog.write_data([_definition(_IID, 1, 3, 0)])
    catalog.write_data([_definition(_IID, 2, 3, switch)])
    path = write_legacy(root, _IID, _rows(_DAY0 + 3_600 * _S, 6))
    assert migrate.main(["--catalog", str(root), "--apply"]) == 0
    assert pq.read_table(path).column("price_precision").to_pylist() == [1, 1, 1, 2, 2, 2]


def test_a_pre_ohlc_file_gets_null_ohlc(tmp_path: Path) -> None:
    root = tmp_path / "catalog"
    ParquetDataCatalog(str(root)).write_data([_definition(_IID, _PP, _SP, 0)])
    path = write_legacy(root, _IID, _rows(_DAY0 + 3_600 * _S, 10), with_ohlc=False)
    assert migrate.main(["--catalog", str(root), "--apply"]) == 0
    assert pq.read_table(path).column("open_price").null_count == 10
    assert [s.close_price for s in _snapshots(root)] == [None] * 10


def test_an_open_day_file_is_skipped_and_counted(tmp_path: Path) -> None:
    root, closed = _catalog(tmp_path)
    today = _NOW // _DAY_NS * _DAY_NS
    open_file = write_legacy(root, _HL_IID, _rows(today, 3))
    ParquetDataCatalog(str(root)).write_data([_definition(_HL_IID, _PP, _SP, 0)])
    before = open_file.read_bytes()
    with maintenance(root) as writer:
        assert writer is not None
        stats = migrate.run(writer, str(root), None, _NOW)
    assert stats.open_day == 1
    assert open_file.read_bytes() == before
    assert "price_precision" in pq.read_schema(closed).names


def test_venue_narrows_the_run(tmp_path: Path) -> None:
    root, bybit = _catalog(tmp_path)
    ParquetDataCatalog(str(root)).write_data([_definition(_HL_IID, _PP, _SP, 0)])
    hyperliquid = write_legacy(root, _HL_IID, _rows(_DAY0 + 3_600 * _S, 5))
    assert migrate.main(["--catalog", str(root), "--apply", "--venue", "HYPERLIQUID"]) == 0
    assert "price_precision" in pq.read_schema(hyperliquid).names
    assert "price_precision" not in pq.read_schema(bybit).names


def test_a_held_lock_refuses_to_start(tmp_path: Path) -> None:
    root, path = _catalog(tmp_path)
    before = path.read_bytes()
    with maintenance(root) as holder:
        assert holder is not None
        assert migrate.main(["--catalog", str(root), "--apply"]) == 1
    assert path.read_bytes() == before


def test_a_missing_catalog_is_ledgered_and_exit_one(tmp_path: Path) -> None:
    error_ledger.reset()
    assert migrate.main(["--catalog", str(tmp_path / "nope")]) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}


def test_the_snap_refuses_noise_past_the_bar_and_counts_only_noisy_values() -> None:
    import numpy as np

    precisions = np.array([1, 1, 2], dtype=np.intp)
    snapped = migrate.snap(np.array([85891.90000000001, 100.5, 0.07]), precisions, "x")
    assert snapped.units.tolist() == [858919, 1005, 7]
    assert snapped.noisy == 1  # only 85891.90000000001; 100.5 and 0.07 were already exact
    # 100.00001 is a real digit four places finer than p=1 (1e-4 unit off, under the 0.001-unit
    # cap but ~7e8 ULP): refused, never snapped as noise. 3e11 is 3e12 units, past what a double
    # resolves to 0.001 unit.
    for bad in (100.55, 100.00001, float("nan"), float("inf"), 2.0**53, 3e11):
        with pytest.raises(migrate.OffGridError):
            migrate.snap(np.array([bad]), np.array([1], dtype=np.intp), "x")


def test_the_snap_accepts_a_float_summed_volume_hundreds_of_ulp_off() -> None:
    import numpy as np

    trades = [0.001 * k for k in range(1, 400)]
    volume = 0.0
    for size in trades:  # a pre-22.13 live fold: floats added one trade at a time
        volume += size
    snapped = migrate.snap(np.array([volume]), np.array([3], dtype=np.intp), "volume")
    assert snapped.units.tolist() == [sum(range(1, 400))]


def test_the_legacy_writer_is_the_old_float_layout() -> None:
    """Guard on the fixture itself: a float file, the pre-30.2 column set."""
    table = legacy_table(_IID, _rows(_DAY0, 2))
    assert "price_precision" not in table.column_names
    assert str(table.schema.field("bid_prices").type) == "list<item: double>"


# -- the migrated catalog in Nautilus's own readers --------------------------------------------------


def _trades(start_ns: int, seconds: int) -> list[TradeTick]:
    return [
        TradeTick(
            InstrumentId.from_str(_IID),
            Price.from_str("85891.9"),
            Quantity.from_str("0.010"),
            AggressorSide.BUYER,
            TradeId(str(i)),
            start_ns + i * _S,
            start_ns + i * _S,
        )
        for i in range(0, seconds, 10)
    ]


def _run_config(catalog: str) -> BacktestRunConfig:
    return BacktestRunConfig(
        engine=BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")),
        venues=[
            BacktestVenueConfig(
                name="BYBIT",
                oms_type="NETTING",
                account_type="MARGIN",
                base_currency="USDT",
                starting_balances=["10000 USDT"],
            )
        ],
        data=[
            BacktestDataConfig(catalog_path=catalog, data_cls=TradeTick, instrument_id=_IID),
            BacktestDataConfig(
                catalog_path=catalog,
                data_cls="kernel.second_snapshot:DydxSecondSnapshot",
                instrument_id=_IID,
                client_id="BYBIT",
            ),
        ],
    )


@pytest.mark.usefixtures("nautilus_log_guard")
def test_the_migrated_catalog_loads_in_the_catalog_the_projected_readers_and_a_backtest(
    tmp_path: Path,
) -> None:
    root, _ = _catalog(tmp_path, seconds=300)
    start = _DAY0 + 3_600 * _S
    ParquetDataCatalog(str(root)).write_data(_trades(start, 300))
    assert migrate.main(["--catalog", str(root), "--apply"]) == 0
    snapshots = _snapshots(root)
    assert len(snapshots) == 300
    tops = query_top_of_book(str(root), _IID, start, start + 300 * _S)
    assert [t.bid_price for t in tops] == [s.exact.bid_prices[0] for s in snapshots]
    ohlc = query_second_ohlc(str(root), _IID, start, start + 300 * _S)
    assert [r.close_price for r in ohlc] == [s.close_price for s in snapshots]
    node = BacktestNode(configs=[_run_config(str(root))])
    try:
        (result,) = node.run()
    finally:
        node.dispose()
    assert result.iterations == 300 + 30


def test_unreadable_definitions_refuse_the_file_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A definition query raising outside `_FILE_ERRORS` is that file's refusal, not the run's."""
    error_ledger.reset()
    root, bybit = _catalog(tmp_path)
    ParquetDataCatalog(str(root)).write_data([_definition(_HL_IID, _PP, _SP, 0)])
    hyperliquid = write_legacy(root, _HL_IID, _rows(_DAY0 + 3_600 * _S, 5))
    real = migrate.definition_history

    def failing(catalog_path: str, iid: str) -> migrate.DefinitionHistory:
        if iid == _IID:
            raise RuntimeError("DataFusion error: corrupt instrument file")
        return real(catalog_path, iid)

    monkeypatch.setattr(migrate, "definition_history", failing)
    before = bybit.read_bytes()
    assert migrate.main(["--catalog", str(root), "--apply"]) == 2
    assert bybit.read_bytes() == before
    assert "price_precision" in pq.read_schema(hyperliquid).names
    assert error_ledger.counts() == {"migrate_snapshot_ints.error": 1}
    assert "definitions unreadable" in error_ledger.last_details()["migrate_snapshot_ints.error"]
