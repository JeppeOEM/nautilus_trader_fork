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
`python3 -m verification.catalog` end to end (Story 31.7): a real catalog written by
`ParquetDataCatalog.write_data` (a `CryptoPerpetual`, `TradeTick`s, `MarkPriceUpdate`s and
`DydxSecondSnapshot`s, one file per minute), a real candle store built by writing SQLite rows
directly from the test's own fold of its rows, and the session's Nautilus log guard. The clean day
passes exactly; every planted defect (written with raw pyarrow where Nautilus would refuse it)
makes a failing count non-zero and the exit status 1. Then the pure pieces: the name parser, the
digest, overlaps, the schema signature, the candle classes and the windows.
"""

import argparse
import json
import math
import os
import sqlite3
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import UTC
from datetime import date
from datetime import datetime
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from observability import error_ledger

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import FIXED_PRECISION
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification import catalog as catalog_tool
from verification.application import sites
from verification.application.catalog import BacktestReader
from verification.application.catalog import NautilusReader
from verification.application.catalog import check_day
from verification.application.catalog import report_json
from verification.application.conservation import day_start_ns
from verification.conservation import plan_of
from verification.domain.catalog_check import BOTH_UNDEFINED
from verification.domain.catalog_check import DIFFERENT
from verification.domain.catalog_check import EXACT
from verification.domain.catalog_check import FLOAT_NOISE
from verification.domain.catalog_check import IDENTICAL
from verification.domain.catalog_check import NOT_EXERCISED
from verification.domain.catalog_check import READ_MARGIN_NS
from verification.domain.catalog_check import TRADE_TYPE
from verification.domain.catalog_check import UNDEFINED_MISMATCH
from verification.domain.catalog_check import Digest
from verification.domain.catalog_check import FileOpen
from verification.domain.catalog_check import FileScan
from verification.domain.catalog_check import LeafFile
from verification.domain.catalog_check import Leg
from verification.domain.catalog_check import Received
from verification.domain.catalog_check import Span
from verification.domain.catalog_check import StoredBar
from verification.domain.catalog_check import backtest_windows
from verification.domain.catalog_check import count_overlaps
from verification.domain.catalog_check import duplicate_count
from verification.domain.catalog_check import hour_windows
from verification.domain.catalog_check import judge_bar
from verification.domain.catalog_check import judge_leaf
from verification.domain.catalog_check import parse_file_name
from verification.domain.catalog_check import row_digest
from verification.domain.catalog_check import schema_signature
from verification.domain.reference_signals import RefCandle
from verification.infrastructure.catalog_scan import CatalogScan
from verification.subject.consolidation import maintenance_writer
from verification.subject.nautilus_reads import digest_encoded


_DAY = date(2026, 9, 29)
_D0 = day_start_ns(_DAY)
_NS = 1_000_000_000
_MS = 1_000_000
_NOW = _D0 + 2 * 86_400 * _NS  # the day and the next are closed
_BTC = "BTCUSDT-LINEAR.BYBIT"
_SOL = "SOL-USD-PERP.HYPERLIQUID"
_START_S = _D0 // _NS + 36_000  # 10:00:00
_MINUTES = 3
_UNTRADED_MINUTE = 2  # 10:02 has rows but no trade: its 60 s bucket is both_undefined
_WIDTHS = (60, 300, 900, 3600, 14400, 86400)
_SCHEMA = """
CREATE TABLE candles (
    instrument_id TEXT NOT NULL, bar_seconds INTEGER NOT NULL, t INTEGER NOT NULL,
    o REAL, h REAL, l REAL, c REAL, v REAL NOT NULL, seconds_observed INTEGER NOT NULL,
    PRIMARY KEY (instrument_id, bar_seconds, t)
) WITHOUT ROWID;
"""


# --- the day's rows -------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Second:
    """One snapshot second: its trade price in units at precision 2 (None: nothing traded)."""

    second: int
    price: int | None
    ts_init_lag: int = _NS  # the venue-timed close's hold-back (DATA-01)

    @property
    def ts_event(self) -> int:
        return self.second * _NS + _NS // 2


def _seconds() -> list[_Second]:
    found = []
    for k in range(_MINUTES * 60):
        traded = k // 60 != _UNTRADED_MINUTE
        found.append(_Second(_START_S + k, 8_400_000 + (k % 7) * 10 if traded else None))
    return found


def _snapshot(row: _Second, iid: str = _BTC) -> DydxSecondSnapshot:
    p = row.price
    reference = p if p is not None else 8_400_000
    traded = p is not None
    return DydxSecondSnapshot(
        InstrumentId.from_str(iid),
        2,
        3,
        [reference - 100],
        [1_000],
        [reference + 100],
        [2_000],
        1_000 + row.second % 3 if traded else 0,
        500 if traded else 0,
        1 if traded else 0,
        1 if traded else 0,
        row.ts_event,
        row.ts_event + row.ts_init_lag,
        p,
        None if p is None else p + 5,
        None if p is None else p - 5,
        None if p is None else p + 1,
    )


def _tick(row: _Second, iid: str = _BTC) -> TradeTick:
    ts_event = row.second * _NS + 200 * _MS
    return TradeTick(
        InstrumentId.from_str(iid),
        Price.from_raw(int(row.price or 0) * 10 ** (FIXED_PRECISION - 2), 2),
        Quantity.from_str("0.500"),
        AggressorSide.BUYER,
        TradeId(f"t{row.second}"),
        ts_event,
        ts_event + 100 * _MS,
    )


def _mark(row: _Second, iid: str = _BTC) -> MarkPriceUpdate:
    return MarkPriceUpdate(
        InstrumentId.from_str(iid),
        Price.from_str("84000.10"),
        row.second * _NS,
        row.second * _NS + 50 * _MS,
    )


def _perpetual(iid: str = _BTC) -> CryptoPerpetual:
    quote = USDT if iid == _BTC else USDC
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(iid.split("-")[0]),
        base_currency=BTC,
        quote_currency=quote,
        settlement_currency=quote,
        is_inverse=False,
        price_precision=2,
        price_increment=Price.from_str("0.10"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        lot_size=Quantity.from_str("0.001"),
        max_quantity=None,
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.1"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=_START_S * _NS - 60 * _NS,
        ts_init=_START_S * _NS - 60 * _NS,
    )


# --- the test's own fold, for the candle store ----------------------------------------------------


def _units(units: int, places: int) -> float:
    return float(Decimal(units).scaleb(-places))


def _bar(width: int, t: int, rows: list[_Second], iid: str) -> tuple[Any, ...]:
    traded = [r for r in rows if r.price is not None]
    if not traded:
        return (iid, width, t, None, None, None, None, 0.0, len(rows))
    volume = sum(1_000 + r.second % 3 + 500 for r in traded)
    return (
        iid,
        width,
        t,
        _units(traded[0].price or 0, 2),
        _units(max(r.price or 0 for r in traded) + 5, 2),
        _units(min(r.price or 0 for r in traded) - 5, 2),
        _units((traded[-1].price or 0) + 1, 2),
        _units(volume, 3),
        len(rows),
    )


def _bars(rows: list[_Second], iid: str = _BTC) -> list[tuple[Any, ...]]:
    found = []
    for width in _WIDTHS:
        buckets: dict[int, list[_Second]] = {}
        for row in rows:
            ms = row.ts_event // _MS
            buckets.setdefault(ms // (width * 1000) * width * 1000, []).append(row)
        found += [_bar(width, t, members, iid) for t, members in sorted(buckets.items())]
    return found


def _write_store(path: Path, bars: list[tuple[Any, ...]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    try:
        db.executescript(_SCHEMA)
        db.executemany("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", bars)
        db.commit()
    finally:
        db.close()


# --- a day on disk --------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Day:
    """What to write: the rows, per-minute files or one file per type, and the extra rows."""

    rows: tuple[_Second, ...] = field(default_factory=lambda: tuple(_seconds()))
    per_minute: bool = True
    extra_snapshots: tuple[_Second, ...] = ()
    iid: str = _BTC

    @property
    def venue(self) -> str:
        return self.iid.rsplit(".", 1)[1]


def _minutes(rows: tuple[_Second, ...], per_minute: bool) -> list[list[_Second]]:
    if not per_minute:
        return [list(rows)]
    groups: dict[int, list[_Second]] = {}
    for row in rows:
        groups.setdefault(row.second // 60, []).append(row)
    return list(groups.values())


def _write_catalog(root: Path, day: _Day) -> None:
    writer = ParquetDataCatalog(str(root))
    writer.write_data([_perpetual(day.iid)])
    for group in _minutes(day.rows, day.per_minute):
        batches = (
            [_snapshot(r, day.iid) for r in group],
            [_tick(r, day.iid) for r in group if r.price is not None],
            [_mark(r, day.iid) for r in group if r.second % 30 == 0],
        )
        for batch in batches:
            if batch:
                writer.write_data(batch)
    for row in day.extra_snapshots:
        writer.write_data([_snapshot(row, day.iid)])


def _env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, venue: str = "BYBIT", iid: str = _BTC
) -> None:
    plan = tmp_path / f"{venue.lower()}.toml"
    plan.write_text(f'instruments = ["{iid}"]\n')
    monkeypatch.setenv("VERIFY_DATA_DIR", str(tmp_path / "verify"))
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "catalog"))
    monkeypatch.setenv("CANDLES_DIR", str(tmp_path / "candles"))
    monkeypatch.setenv(f"{venue}_COLLECTOR_CONFIG", str(plan))
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)


def _write_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, day: _Day = _Day()) -> Path:
    """Write the day's catalog and a store consistent with it; return the catalog root."""
    catalog = tmp_path / "catalog"
    _write_catalog(catalog, day)
    store = tmp_path / "candles" / f"candles_{day.venue.lower()}.db"
    _write_store(store, _bars([*day.rows, *day.extra_snapshots], day.iid))
    _env(monkeypatch, tmp_path, day.venue, day.iid)
    return catalog


def _run(
    capsys: pytest.CaptureFixture[str], writer: Any = maintenance_writer, venue: str = "BYBIT"
) -> tuple[int, Any]:
    status = catalog_tool.main(
        ["--venue", venue, "--day", _DAY.isoformat(), "--json"], lambda: _NOW, writer
    )
    return status, json.loads(capsys.readouterr().out)


def _structure(report: Mapping[str, Any], data_type: str) -> dict[str, Any]:
    (found,) = [t for t in report["structure"] if t["data_type"] == data_type]
    return found


def _leaf_counts(report: Mapping[str, Any], data_type: str) -> dict[str, int]:
    (leaf,) = _structure(report, data_type)["leaves"]
    return {name: count for name, count in leaf["counts"].items() if count}


def _rehearsal(report: Mapping[str, Any]) -> dict[str, str]:
    return {t["data_type"]: t["verdict"] for t in report["rehearsal"]["types"]}


def _parity(report: Mapping[str, Any], data_type: str) -> dict[str, Any]:
    (found,) = [p for p in report["parity"] if p["data_type"] == data_type]
    return found


def _candle_counts(report: Mapping[str, Any]) -> dict[str, int]:
    (candles,) = report["candles"]
    total: dict[str, int] = {}
    for width in candles["widths"].values():
        for name, count in width["counts"].items():
            total[name] = total.get(name, 0) + count
    return {name: count for name, count in total.items() if count}


def _files(catalog: Path, data_type: str) -> list[Path]:
    return sorted((catalog / "data" / data_type / _BTC).glob("*.parquet"))


def _stamp(ns: int) -> str:
    moment = datetime.fromtimestamp(ns // _NS, UTC)
    return f"{moment:%Y-%m-%dT%H-%M-%S}-{ns % _NS:09d}Z"


def _name(start: int, end: int) -> str:
    return f"{_stamp(start)}_{_stamp(end)}.parquet"


def _plant(path: Path, table: pa.Table) -> Path:
    """Write `table` raw into `path`'s leaf under the name its own `ts_init` span gives."""
    ts = table.column("ts_init").to_pylist()
    target = path.parent / _name(min(ts), max(ts))
    pq.write_table(table, target)
    return target


# --- end to end -----------------------------------------------------------------------------------


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_clean_day_passes_with_every_count_zero_and_every_reader_agreeing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch)
    status, report = _run(capsys)
    assert (status, report["passed"], report["failing"]) == (0, True, 0)
    assert [t["data_type"] for t in report["structure"]] == [
        "crypto_perpetual",
        "custom_dydx_second_snapshot",
        "mark_price_update",
        "trade_tick",
    ]
    assert all(t["failing"] == 0 for t in report["structure"])
    assert (
        _structure(report, "trade_tick")["leaves"][0]["files"] == _MINUTES - 1
    )  # 10:02 traded nothing
    assert _rehearsal(report) == {
        "crypto_perpetual": NOT_EXERCISED,
        "custom_dydx_second_snapshot": IDENTICAL,
        "mark_price_update": IDENTICAL,
        "trade_tick": IDENTICAL,
    }
    snapshots = _parity(report, "custom_dydx_second_snapshot")
    assert {leg["rows"] for leg in snapshots["legs"].values()} == {_MINUTES * 60}
    assert len({leg["digest"] for leg in snapshots["legs"].values()}) == 1
    trades = _parity(report, "trade_tick")
    assert {leg["rows"] for leg in trades["legs"].values()} == {(_MINUTES - 1) * 60}
    entries = [*report["parity"], *report["candles"]]
    assert {entry["failing"] for entry in entries} == {0}  # Story 31.11: the per-entry counts
    assert _candle_counts(report) == {EXACT: 7, BOTH_UNDEFINED: 1}  # 3 minutes + 5 wider buckets


@pytest.mark.usefixtures("nautilus_log_guard")
def test_the_text_report_names_every_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch)
    status = catalog_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    text = capsys.readouterr().out
    assert status == 0
    assert text.startswith("catalog BYBIT 2026-09-29: PASS")
    for section in ("structure:", "rehearsal (", "parity (", "candles ("):
        assert section in text


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_second_snapshot_file_repeating_a_ts_event_is_a_duplicate_and_an_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    first = _files(catalog, "custom_dydx_second_snapshot")[0]
    table = pq.read_table(first).slice(10, 1)
    shifted = table.set_column(
        table.schema.get_field_index("ts_init"),
        "ts_init",
        pa.array([table.column("ts_init")[0].as_py() + 1], pa.uint64()),
    )
    _plant(first, shifted)
    status, report = _run(capsys)
    assert status == 1
    assert _structure(report, "custom_dydx_second_snapshot")["duplicate_ts_event"] == 1
    assert _leaf_counts(report, "custom_dydx_second_snapshot")["overlap"] >= 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_two_trade_files_with_intersecting_spans_are_an_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    first = _files(catalog, "trade_tick")[0]
    table = pq.read_table(first).slice(5, 1)
    renamed = table.set_column(
        table.schema.get_field_index("trade_id"), "trade_id", pa.array(["planted"], pa.string())
    )
    _plant(first, renamed)
    status, report = _run(capsys)
    assert status == 1
    assert _leaf_counts(report, "trade_tick") == {"overlap": 1}


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_trade_file_with_an_extra_column_splits_the_schema_and_is_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    last = _files(catalog, "trade_tick")[-1]
    table = pq.read_table(last)
    pq.write_table(table.append_column("extra", pa.array([7] * table.num_rows, pa.int64())), last)
    status, report = _run(capsys)
    assert status == 1
    schemas = _structure(report, "trade_tick")["schemas"]
    assert sorted(c["files"] for c in schemas) == [1, 1]
    assert any(c["example"].endswith(last.name) for c in schemas)


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_file_named_after_its_first_ts_init_is_a_name_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    first = _files(catalog, "trade_tick")[0]
    span = parse_file_name(first.name)
    assert span is not None
    first.rename(first.parent / _name(span.start + _NS, span.end))
    status, report = _run(capsys)
    assert status == 1
    assert _leaf_counts(report, "trade_tick")["name_span"] == 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_rows_out_of_ts_init_order_are_unsorted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    first = _files(catalog, "trade_tick")[0]
    table = pq.read_table(first)
    pq.write_table(table.take(list(reversed(range(table.num_rows)))), first)
    status, report = _run(capsys)
    assert status == 1
    assert _leaf_counts(report, "trade_tick") == {"unsorted": 1}


class _Lossy:
    """The archive's writer with one row fewer in every merged file (and a count to match)."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def write_merged(self, directory: Path, table: pa.Table, expected: int, **kw: Any) -> Path:
        return self._inner.write_merged(directory, table.slice(1), expected - 1, **kw)


@contextmanager
def _lossy_writer(root: Path, now_ns: Any) -> Iterator[Any]:
    with maintenance_writer(root, now_ns) as writer:
        yield _Lossy(writer)


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_writer_dropping_a_row_in_the_rehearsal_is_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch)
    status, report = _run(capsys, _lossy_writer)
    assert status == 1
    assert _rehearsal(report)["trade_tick"] == DIFFERENT
    assert report["rehearsal"]["failing"] >= 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_an_already_merged_day_is_not_exercised_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Day(per_minute=False))
    status, report = _run(capsys)
    assert status == 0
    assert set(_rehearsal(report).values()) == {NOT_EXERCISED}


def _alter_store(path: Path) -> None:
    db = sqlite3.connect(path)
    try:
        first, second = _START_S * 1000, (_START_S + 60) * 1000
        db.execute("UPDATE candles SET c = c + 0.1 WHERE bar_seconds = 60 AND t = ?", (first,))
        db.execute("DELETE FROM candles WHERE bar_seconds = 60 AND t = ?", (second,))
        db.commit()
    finally:
        db.close()


@pytest.mark.usefixtures("nautilus_log_guard")
def test_an_altered_and_a_deleted_bar_are_different_and_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch)
    _alter_store(tmp_path / "candles" / "candles_bybit.db")
    status, report = _run(capsys)
    assert status == 1
    widths = report["candles"][0]["widths"]["60"]["counts"]
    assert (widths[DIFFERENT], widths["missing"]) == (1, 1)


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_close_one_ulp_off_is_float_noise_reported_apart_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch)
    db = sqlite3.connect(tmp_path / "candles" / "candles_bybit.db")
    try:
        (close,) = db.execute(
            "SELECT c FROM candles WHERE bar_seconds = 60 AND t = ?", (_START_S * 1000,)
        ).fetchone()
        db.execute(
            "UPDATE candles SET c = ? WHERE bar_seconds = 60 AND t = ?",
            (math.nextafter(close, math.inf), _START_S * 1000),
        )
        db.commit()
    finally:
        db.close()
    status, report = _run(capsys)
    assert status == 0
    assert report["float_noise"] == 1
    assert report["candles"][0]["widths"]["60"]["counts"][FLOAT_NOISE] == 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_the_midnight_second_is_in_the_fold_not_in_the_parity_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    last = _Second(_D0 // _NS + 86_399, 8_400_000, ts_init_lag=2 * _NS)  # 23:59:59.5 -> 00:00:02.5
    _write_day(tmp_path, monkeypatch, _Day(extra_snapshots=(last,)))
    status, report = _run(capsys)
    assert status == 0
    snapshots = _parity(report, "custom_dydx_second_snapshot")
    assert {leg["rows"] for leg in snapshots["legs"].values()} == {_MINUTES * 60}
    assert report["candles"][0]["rows"] == _MINUTES * 60 + 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_row_further_than_the_margin_from_its_ts_event_is_beyond_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    late = TradeTick(
        InstrumentId.from_str(_BTC),
        Price.from_str("84000.00"),
        Quantity.from_str("0.500"),
        AggressorSide.SELLER,
        TradeId("late"),
        (_START_S + 200) * _NS,
        (_START_S + 200) * _NS + READ_MARGIN_NS + _NS,
    )
    ParquetDataCatalog(str(catalog)).write_data([late])
    status, report = _run(capsys)
    assert status == 1
    assert _parity(report, "trade_tick")["beyond_margin"] == 1
    assert _parity(report, "trade_tick")["failing"] == 1  # Story 31.11: the per-entry count


@pytest.mark.usefixtures("nautilus_log_guard")
def test_an_index_price_file_no_catalog_path_decodes_is_open_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit D-113 (OPEN): the pinned Nautilus decodes `IndexPriceUpdate` on neither read path."""
    catalog = _write_day(tmp_path, monkeypatch)
    rows = [
        IndexPriceUpdate(InstrumentId.from_str(_BTC), Price.from_str("84000.20"), ts, ts + _MS)
        for ts in ((_START_S + 5) * _NS, (_START_S + 65) * _NS)
    ]
    writer = ParquetDataCatalog(str(catalog))
    for row in rows:
        writer.write_data([row])
    status, report = _run(capsys)
    assert status == 1
    assert _leaf_counts(report, "index_price_update") == {"open_failed": 2}
    (leaf,) = _structure(report, "index_price_update")["leaves"]
    assert "NotImplementedError" in leaf["examples"][0]
    assert _leaf_counts(report, "mark_price_update") == {}  # the Rust path decodes marks


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_type_directory_nautilus_has_no_class_for_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    source = _files(catalog, "trade_tick")[0]
    target = catalog / "data" / "mystery_type" / _BTC / source.name
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    status, report = _run(capsys)
    assert status == 1
    assert _structure(report, "mystery_type")["known"] is False
    assert _leaf_counts(report, "mystery_type") == {}  # unknown once, never also open_failed


def _refused(match: str) -> None:
    before = error_ledger.counts().get(sites.CATALOG_REFUSED, 0)
    with pytest.raises(SystemExit, match=match):
        catalog_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    assert error_ledger.counts().get(sites.CATALOG_REFUSED, 0) == before + 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_day_file_vanishing_mid_run_is_refused_and_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    victim = _files(catalog, "trade_tick")[1]
    original = CatalogScan.scan

    def scan_then_delete(self: CatalogScan, path: Path) -> Any:
        if victim.exists():
            victim.unlink()
        return original(self, path)

    monkeypatch.setattr(CatalogScan, "scan", scan_then_delete)
    _refused(r"catalog changed during the check \(maintenance ran\?\)")


def test_a_day_not_closed_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit, match="day not closed"):
        catalog_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _D0)


def test_a_missing_candle_store_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "catalog").mkdir()
    (tmp_path / "candles").mkdir()
    _env(monkeypatch, tmp_path)
    _refused("candle store .*candles_bybit.db does not exist")


def test_a_plan_instrument_without_a_definition_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "catalog").mkdir()
    _write_store(tmp_path / "candles" / "candles_bybit.db", [])
    _env(monkeypatch, tmp_path)
    _refused("plan instruments without a stored definition")


def test_an_uncreatable_scratch_directory_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "catalog").mkdir()
    _write_store(tmp_path / "candles" / "candles_bybit.db", [])
    _env(monkeypatch, tmp_path)
    (tmp_path / "verify").write_text("a file where the scratch parent should be")
    _refused("scratch directory .* cannot be created")


def test_a_scratch_directory_inside_the_catalog_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "catalog").mkdir()
    _write_store(tmp_path / "candles" / "candles_bybit.db", [])
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("VERIFY_DATA_DIR", str(tmp_path / "catalog" / "verify"))
    _refused("scratch directory .* lies inside the catalog")
    assert not (tmp_path / "catalog" / "verify").exists()


# --- the change guard and the window ---------------------------------------------------------------


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_new_file_of_a_later_day_appearing_mid_run_is_not_a_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A live collector's flush into today (a non-day file) never refuses the checked day."""
    catalog = _write_day(tmp_path, monkeypatch)
    source = _files(catalog, "trade_tick")[0]
    later = (_D0 + 2 * 86_400 * _NS) + 12 * 3_600 * _NS
    table = pq.read_table(source).slice(0, 1)
    shifted = table.set_column(
        table.schema.get_field_index("ts_init"), "ts_init", pa.array([later], pa.uint64())
    )
    shifted = shifted.set_column(
        shifted.schema.get_field_index("ts_event"), "ts_event", pa.array([later], pa.uint64())
    )
    original = CatalogScan.scan

    def scan_then_flush(self: CatalogScan, path: Path) -> Any:
        target = source.parent / _name(later, later)
        if not target.exists():
            pq.write_table(shifted, target)
        return original(self, path)

    monkeypatch.setattr(CatalogScan, "scan", scan_then_flush)
    status, report = _run(capsys)
    assert (status, report["passed"]) == (0, True)


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_late_row_named_past_the_margin_is_found_by_its_ts_event_and_beyond_margin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    late = TradeTick(
        InstrumentId.from_str(_BTC),
        Price.from_str("84000.00"),
        Quantity.from_str("0.500"),
        AggressorSide.SELLER,
        TradeId("late"),
        (_START_S + 200) * _NS,  # ts_event in the day ...
        _D0 + 86_400 * _NS + READ_MARGIN_NS + 10 * _NS,  # ... arriving after D + 1 + M
    )
    ParquetDataCatalog(str(catalog)).write_data([late])
    status, report = _run(capsys)
    assert status == 1
    assert _parity(report, "trade_tick")["beyond_margin"] == 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_file_named_days_away_is_found_by_its_rows_and_overlaps_that_days_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    first = _files(catalog, "trade_tick")[0]
    away = _D0 - 3 * 86_400 * _NS + 36_000 * _NS  # 10:00 three days before
    first.rename(first.parent / _name(away, away + 59 * _NS))
    neighbour = pq.read_table(first.parent / _name(away, away + 59 * _NS)).slice(0, 1)
    moved = neighbour.set_column(
        neighbour.schema.get_field_index("ts_init"), "ts_init", pa.array([away + _NS], pa.uint64())
    )
    moved = moved.set_column(
        moved.schema.get_field_index("ts_event"), "ts_event", pa.array([away], pa.uint64())
    )
    pq.write_table(moved, first.parent / _name(away + _NS, away + _NS))
    status, report = _run(capsys)
    assert status == 1
    counts = _leaf_counts(report, "trade_tick")
    assert counts["name_span"] == 1
    assert counts["overlap"] == 1  # the lying day file against the other day's own file


# --- verify the verifiers: a lossy reader under test ------------------------------------------------


def _resized(tick: TradeTick) -> TradeTick:
    return TradeTick(
        tick.instrument_id,
        tick.price,
        Quantity.from_str("0.501"),
        tick.aggressor_side,
        tick.trade_id,
        tick.ts_event,
        tick.ts_init,
    )


_MUTATIONS: Mapping[str, Callable[[list[TradeTick]], list[TradeTick]]] = MappingProxyType(
    {
        "identity": lambda ticks: ticks,
        "drop": lambda ticks: ticks[1:],
        "duplicate": lambda ticks: [*ticks, ticks[0]],
        "change": lambda ticks: [_resized(ticks[0]), *ticks[1:]],
    }
)


def _day_trades(catalog: Path) -> list[TradeTick]:
    reader = ParquetDataCatalog(str(catalog))
    end = _D0 + 86_400 * _NS - 1
    return list(reader.query(TradeTick, identifiers=[_BTC], start=_D0, end=end))


class _LossyQuery:
    """The query leg of a reader that hands back `trades` for the day (a fake subject)."""

    def __init__(self, inner: NautilusReader, trades: list[TradeTick]) -> None:
        self._inner = inner
        self._trades = trades

    def known(self, data_type: str) -> bool:
        return self._inner.known(data_type)

    def open_file(
        self, data_type: str, iid: str, path: Path, windows: Sequence[tuple[int, int]]
    ) -> FileOpen:
        return self._inner.open_file(data_type, iid, path, windows)

    def hourly(self, data_type: str, iid: str, day_start_ns: int, columns: Sequence[str]) -> Leg:
        if data_type != TRADE_TYPE:
            return self._inner.hourly(data_type, iid, day_start_ns, columns)
        return digest_encoded(self._trades, TradeTick, columns)

    def settlement(self, iid: str) -> str | None:
        return self._inner.settlement(iid)


class _LossyBacktest:
    """A backtest whose actor received `trades` as the day's trade ticks (a fake subject)."""

    def __init__(self, inner: BacktestReader, trades: list[TradeTick]) -> None:
        self._inner = inner
        self._trades = trades

    def receive(
        self, currencies: Mapping[str, str], day_start_ns: int, columns: Mapping[str, Sequence[str]]
    ) -> Mapping[str, Received]:
        got = dict(self._inner.receive(currencies, day_start_ns, columns))
        leg = digest_encoded(self._trades, TradeTick, columns[TRADE_TYPE])
        real = got[_BTC]
        got[_BTC] = Received(MappingProxyType({**real.legs, TRADE_TYPE: leg}), real.trade_rows)
        return got


def _lossy_report(catalog: Path, leg: str, mutation: str) -> Mapping[str, Any]:
    args = argparse.Namespace(
        venue="BYBIT", day=_DAY, catalog=None, raw_dir=None, candles=None, scratch_dir=None
    )
    inputs = catalog_tool.inputs_of(args, os.environ, maintenance_writer)
    trades = _MUTATIONS[mutation](_day_trades(catalog))
    if leg == "query":
        inputs = replace(inputs, nautilus=_LossyQuery(inputs.nautilus, trades))
    else:
        inputs = replace(inputs, backtest=_LossyBacktest(inputs.backtest, trades))
    report = check_day(plan_of("BYBIT", os.environ), _DAY, inputs, _NOW)
    return report_json(report)


@pytest.mark.usefixtures("nautilus_log_guard")
@pytest.mark.parametrize("leg", ["query", "received"])
@pytest.mark.parametrize("mutation", ["identity", "drop", "duplicate", "change"])
def test_a_reader_dropping_changing_or_duplicating_a_row_is_a_read_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leg: str, mutation: str
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    report = _lossy_report(catalog, leg, mutation)
    parity = _parity(report, TRADE_TYPE)
    if mutation == "identity":  # the fake itself reads exactly: the comparator stays quiet
        assert parity["read_mismatch"] == 0
        return
    assert parity["read_mismatch"] > 0
    assert f"stored/{leg}" in parity["mismatches"]
    assert report["passed"] is False


# --- more classes, end to end ----------------------------------------------------------------------


@pytest.mark.usefixtures("nautilus_log_guard")
def test_an_extra_bar_an_undefined_bar_and_an_unknown_width_each_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch)
    db = sqlite3.connect(tmp_path / "candles" / "candles_bybit.db")
    try:
        first = _START_S * 1000
        db.execute(
            "UPDATE candles SET o = NULL, h = NULL, l = NULL, c = NULL "
            "WHERE bar_seconds = 60 AND t = ?",
            (first,),
        )
        extra = (_BTC, 60, first + 3_600_000, 1.0, 1.0, 1.0, 1.0, 1.0, 60)
        unknown = (_BTC, 120, first, 1.0, 1.0, 1.0, 1.0, 1.0, 120)
        db.executemany("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [extra, unknown])
        db.commit()
    finally:
        db.close()
    status, report = _run(capsys)
    assert status == 1
    (candles,) = report["candles"]
    minute = candles["widths"]["60"]["counts"]
    assert (minute["extra"], minute[UNDEFINED_MISMATCH]) == (1, 1)
    assert candles["unknown_width"] == 1


@pytest.mark.usefixtures("nautilus_log_guard")
def test_an_empty_file_and_a_null_stamp_are_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    source = _files(catalog, "trade_tick")[0]
    table = pq.read_table(source)
    empty_at = (_START_S + 600) * _NS
    pq.write_table(table.slice(0, 0), source.parent / _name(empty_at, empty_at))
    null_at = (_START_S + 700) * _NS
    row = table.slice(0, 1)
    row = row.set_column(
        row.schema.get_field_index("ts_init"), "ts_init", pa.array([null_at], pa.uint64())
    )
    row = row.set_column(
        row.schema.get_field_index("ts_event"),
        pa.field("ts_event", pa.uint64(), nullable=True),
        pa.array([None], pa.uint64()),
    )
    pq.write_table(row, source.parent / _name(null_at, null_at))
    status, report = _run(capsys)
    assert status == 1
    counts = _leaf_counts(report, "trade_tick")
    assert counts["empty"] == 1
    assert counts["null_ts"] == 1
    assert _parity(report, "trade_tick")["beyond_margin"] == 0  # a null is never a skew


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_file_with_an_extra_metadata_key_splits_the_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_day(tmp_path, monkeypatch)
    last = _files(catalog, "trade_tick")[-1]
    table = pq.read_table(last)
    metadata = {**(table.schema.metadata or {}), b"extra_key": b"1"}
    pq.write_table(table.replace_schema_metadata(metadata), last)
    status, report = _run(capsys)
    assert status == 1
    assert len(_structure(report, "trade_tick")["schemas"]) == 2


@pytest.mark.usefixtures("nautilus_log_guard")
def test_a_clean_hyperliquid_day_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Day(iid=_SOL))
    status, report = _run(capsys, venue="HYPERLIQUID")
    assert (status, report["passed"], report["instruments"]) == (0, True, [_SOL])
    trades = _parity(report, TRADE_TYPE)
    assert {leg["rows"] for leg in trades["legs"].values()} == {(_MINUTES - 1) * 60}
    assert _candle_counts(report) == {EXACT: 7, BOTH_UNDEFINED: 1}


def test_open_count_counts_a_file_the_reader_decoded_short() -> None:
    scan = FileScan(rows=10, low=5, high=9, decreases=0, null_ts=0)
    files = [
        LeafFile("a", Span(5, 9), scan, FileOpen(9)),
        LeafFile("b", Span(20, 20), FileScan(0, None, None, 0, 0), FileOpen(0)),
        LeafFile("c", Span(30, 31), FileScan(2, 30, 31, 0, 0), None),
    ]
    report = judge_leaf("trade_tick", _BTC, files, (), (Span(31, 40),))
    nonzero = {name: n for name, n in report.counts.items() if n}
    assert nonzero == {"open_count": 1, "empty": 1, "overlap": 1}


def test_a_file_of_null_stamps_is_null_ts_alone_never_open_count() -> None:
    files = [LeafFile("a", Span(5, 9), FileScan(2, None, None, 0, 2), FileOpen(0))]
    report = judge_leaf("trade_tick", _BTC, files, (), ())
    assert {name: n for name, n in report.counts.items() if n} == {"null_ts": 1}


def test_a_file_is_opened_only_over_the_hours_its_rows_occupy(tmp_path: Path) -> None:
    """One stray stamp decades from the rest is one more hour query, never every hour between."""
    hour = 3_600 * _NS
    stamps = [7, _D0 + 10 * hour + 5, _D0 + 10 * hour + 9, _D0 + 11 * hour + 1]
    path = tmp_path / "f.parquet"
    pq.write_table(
        pa.table(
            {"ts_event": stamps, "ts_init": stamps},
            pa.schema([("ts_event", pa.uint64()), ("ts_init", pa.uint64())]),
        ),
        path,
    )
    scan = CatalogScan(tmp_path).scan(path)
    assert scan.hours == (0, _D0 // hour + 10, _D0 // hour + 11)
    assert scan.decreases == 0
    windows = hour_windows(Span(scan.low or 0, scan.high or 0), scan.hours)
    assert windows == [
        (7, hour - 1),
        (_D0 + 10 * hour, _D0 + 11 * hour - 1),
        (_D0 + 11 * hour, _D0 + 11 * hour + 1),
    ]


def test_an_unreadable_file_of_a_later_day_is_not_a_day_file(tmp_path: Path) -> None:
    """A live writer's file mid-write has no footer: it is listed by its name, never read."""
    leaf = tmp_path / "data" / "trade_tick" / _BTC
    leaf.mkdir(parents=True)
    later = _D0 + 2 * 86_400 * _NS
    (leaf / _name(later, later + _NS)).write_bytes(b"PAR1 half written")
    window = (_D0, _D0 + 86_400 * _NS)
    found = CatalogScan(tmp_path).day_files("trade_tick", _BTC, window)
    assert (found.files, found.bad_names, found.others) == ((), (), (Span(later, later + _NS),))


# --- the pure pieces ------------------------------------------------------------------------------


def test_the_name_parser_reads_an_inclusive_utc_span() -> None:
    span = parse_file_name("2026-09-29T12-59-27-222658920Z_2026-09-29T13-00-01-121288283Z.parquet")
    start = int(datetime(2026, 9, 29, 12, 59, 27, tzinfo=UTC).timestamp()) * _NS + 222658920
    assert span == Span(start, start + 33_898_629_363)
    assert span.intersects(span.end, span.end + 1)
    assert not span.intersects(span.end + 1, span.end + 2)


@pytest.mark.parametrize(
    "name",
    [
        "part-0.parquet",
        "2026-09-29T12-59-27-222658920Z.parquet",
        "2026-02-30T00-00-00-000000000Z_2026-02-30T00-00-00-000000000Z.parquet",
        "2026-09-29T24-00-00-000000000Z_2026-09-29T24-00-00-000000000Z.parquet",
        "2026-09-29T13-00-00-000000000Z_2026-09-29T12-00-00-000000000Z.parquet",
        "2026-09-29T12-59-27-22265892Z_2026-09-29T13-00-01-121288283Z.parquet",
    ],
)
def test_the_name_parser_refuses_anything_else(name: str) -> None:
    assert parse_file_name(name) is None


def test_the_digest_is_order_independent_and_counts_a_duplicate() -> None:
    rows = [{"a": 1, "b": b"x"}, {"a": 2, "b": b"y"}, {"b": b"z", "a": 3}]
    assert Digest.of(rows) == Digest.of(list(reversed(rows)))
    assert Digest.of(rows) == Digest.of(rows[:1]) + Digest.of(rows[1:])
    doubled = Digest.of([*rows, rows[0]])
    assert doubled.count == 4
    assert doubled != Digest.of(rows)
    assert Digest.of([{"a": 1, "b": b"X"}]) != Digest.of([{"a": 1, "b": b"x"}])
    assert row_digest({"a": 1}) != row_digest({"a": "1"})


def test_overlaps_count_every_intersecting_pair_inclusively() -> None:
    spans = [Span(0, 10), Span(10, 20), Span(21, 30), Span(5, 25)]
    assert (
        count_overlaps(spans) == 4
    )  # (0,10)-(10,20), (0,10)-(5,25), (10,20)-(5,25), (21,30)-(5,25)
    assert count_overlaps([Span(0, 1), Span(2, 3)]) == 0


def test_the_schema_signature_ignores_metadata_values_but_not_keys() -> None:
    fields = [("price", "fixed_size_binary[16]"), ("ts_init", "uint64")]
    assert schema_signature(fields, ["price_precision", "instrument_id"]) == schema_signature(
        fields, ["instrument_id", "price_precision"]
    )
    assert schema_signature(fields, ["instrument_id"]) != schema_signature(
        fields, ["instrument_id", "price_precision"]
    )
    assert schema_signature(fields, []) != schema_signature(list(reversed(fields)), [])


def test_duplicates_count_only_rows_of_the_day() -> None:
    day = range(100, 200)
    assert duplicate_count([150, 150, 150, 99, 99, 200, 200, 120], day) == 2


def _ref(close: str | None, seconds: int = 60) -> RefCandle:
    value = None if close is None else Decimal(close)
    volume = Decimal(0) if close is None else Decimal("1.500")
    return RefCandle(0, value, value, value, value, volume, seconds)


def _stored(close: float | None, seconds: int = 60, volume: float = 1.5) -> StoredBar:
    return StoredBar(60, 0, close, close, close, close, 0.0 if close is None else volume, seconds)


def test_each_candle_class() -> None:
    places = (2, 3)
    assert judge_bar(_stored(84000.1), _ref("84000.10"), places) == EXACT
    noisy = math.nextafter(84000.1, math.inf)
    assert judge_bar(_stored(noisy), _ref("84000.10"), places) == FLOAT_NOISE
    assert judge_bar(_stored(None), _ref(None), places) == BOTH_UNDEFINED
    assert judge_bar(_stored(84000.2), _ref("84000.10"), places) == DIFFERENT
    assert judge_bar(_stored(None), _ref("84000.10"), places) == UNDEFINED_MISMATCH
    assert judge_bar(_stored(84000.1, seconds=59), _ref("84000.10"), places) == DIFFERENT
    assert judge_bar(_stored(None, seconds=59), _ref("84000.10"), places) == UNDEFINED_MISMATCH
    assert judge_bar(_stored(84000.1, volume=1.501), _ref("84000.10"), places) == DIFFERENT


def test_the_backtest_windows_cover_the_day_and_both_margins_exactly() -> None:
    windows = backtest_windows(_D0)
    assert windows[0][0] == _D0 - READ_MARGIN_NS
    assert windows[-1][1] == _D0 + 86_400 * _NS + READ_MARGIN_NS - 1
    assert all(b[0] == a[1] + 1 for a, b in pairwise(windows))
    assert len(windows) == 26
