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
`python3 -m verification.derivs` end to end (Story 31.6): ticker/context frames and REST polls in
the recorder's own raw format, a real catalog written by `ParquetDataCatalog.write_data` (real
`MarkPriceUpdate`, `IndexPriceUpdate`, `FundingRateUpdate`, `OpenInterest` and instruments, whose
values are the test's own reading of the frames) and a coverage record. The clean day passes
exactly; every planted defect makes a failing count non-zero and the exit status 1; every
explained case passes. Then the pure pieces: the matchers, the funding cache emulation, the REST
classes, the poll gaps, the labels, the `ts` rules and every definition field's mapping.
"""

import json
from collections.abc import Iterable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from kernel.catalog_files import DEFINITION_DIRNAMES
from kernel.open_interest import OpenInterest
from observability import error_ledger

from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.currencies import USDC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import FundingRateUpdate
from nautilus_trader.model.data import IndexPriceUpdate
from nautilus_trader.model.data import MarkPriceUpdate
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import CryptoFuture
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification import derivs as derivs_tool
from verification.application import sites
from verification.application.conservation import day_start_ns
from verification.domain.conservation import Intervals
from verification.domain.conservation import MalformedLine
from verification.domain.derivs_check import AGREE
from verification.domain.derivs_check import AGREE_BRACKET
from verification.domain.derivs_check import AGREE_KEY
from verification.domain.derivs_check import AGREE_STATE
from verification.domain.derivs_check import BETWEEN_PUSHES
from verification.domain.derivs_check import DUPLICATE
from verification.domain.derivs_check import EXACT
from verification.domain.derivs_check import EXPECTED
from verification.domain.derivs_check import NEXT_TIME_ONLY
from verification.domain.derivs_check import OFF_GRID
from verification.domain.derivs_check import REFERENCE_UNAVAILABLE
from verification.domain.derivs_check import TS_RULE
from verification.domain.derivs_check import UNALIGNED
from verification.domain.derivs_check import UNCHANGED
from verification.domain.derivs_check import UNMATCHED
from verification.domain.derivs_check import VALUE_MISMATCH
from verification.domain.derivs_check import FileLabel
from verification.domain.derivs_check import Frame
from verification.domain.derivs_check import Poll
from verification.domain.derivs_check import Recording
from verification.domain.derivs_check import RowJudge
from verification.domain.derivs_check import StoredValue
from verification.domain.derivs_check import bybit_funding_updates
from verification.domain.derivs_check import bybit_references
from verification.domain.derivs_check import bybit_ticker
from verification.domain.derivs_check import bybit_ticker_poll
from verification.domain.derivs_check import compare_state
from verification.domain.derivs_check import decimal_text
from verification.domain.derivs_check import hyperliquid_ctx
from verification.domain.derivs_check import hyperliquid_ctx_poll
from verification.domain.derivs_check import hyperliquid_references
from verification.domain.derivs_check import label_check
from verification.domain.derivs_check import match_by_receive
from verification.domain.derivs_check import match_keyed
from verification.domain.derivs_check import match_poll_window
from verification.domain.derivs_check import poll_coverage
from verification.domain.derivs_check import rest_by_receive
from verification.domain.derivs_check import rest_keyed
from verification.domain.derivs_check import stored_decimal
from verification.domain.derivs_check import ts_is_init
from verification.domain.derivs_check import ts_whole_ms
from verification.domain.instrument_check import BEFORE_FIRST_DEFINITION
from verification.domain.instrument_check import DIFFERS
from verification.domain.instrument_check import OddVenueDefinition
from verification.domain.instrument_check import StoredDefinition
from verification.domain.instrument_check import bybit_linear_definition
from verification.domain.instrument_check import bybit_spot_definition
from verification.domain.instrument_check import hyperliquid_definition
from verification.domain.instrument_check import judge_polls
from verification.infrastructure.derivs_reader import DEFINITION_DIRS
from verification.infrastructure.derivs_reader import DerivsCatalog
from verification.infrastructure.derivs_reader import funding_row
from verification.tests.test_trades import _write_raw


_DAY = date(2026, 9, 29)
_D0 = day_start_ns(_DAY)
_NS = 1_000_000_000
_MS = 1_000_000
_S = _D0 // _NS + 36_000  # 10:00:00
_NOW = _D0 + 2 * 86_400 * _NS  # the day is closed
_BTC = "BTCUSDT-LINEAR.BYBIT"
_SPOT = "BTCUSDT-SPOT.BYBIT"
_SOL = "SOL-USD-PERP.HYPERLIQUID"
_NEXT_1 = str((_D0 + 16 * 3600 * _NS) // _MS)
_NEXT_2 = str((_D0 + 24 * 3600 * _NS) // _MS)
_ROW_LAG_NS = 50 * _MS  # capture's `ts_init` after the venue time / the recorder's receipt


def _at(at_ms: int) -> int:
    return (_S * 1000 + at_ms) * _MS


# --- Bybit ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Tick:
    """One Bybit ticker frame: venue time in ms from `_S` and the wire fields it carries."""

    at_ms: int
    fields: Mapping[str, str]
    snapshot: bool = False
    recv_lag_ms: int = 100

    @property
    def key(self) -> int:
        return _at(self.at_ms)


_FULL = {
    "markPrice": "84000.10",
    "indexPrice": "84001.00",
    "fundingRate": "0.0001",
    "nextFundingTime": _NEXT_1,
    "fundingIntervalHour": "8",
    "openInterest": "55000.100",
}
_TICKS = (
    _Tick(-2000, _FULL, snapshot=True),
    _Tick(-1000, {"markPrice": "84000.20", "indexPrice": "84001.10"}),
    _Tick(0, {"indexPrice": "84001.20"}),
    _Tick(300, {"openInterest": "55000.200"}),
    _Tick(1000, {"markPrice": "84000.30", "indexPrice": "84001.30", "fundingRate": "0.00011"}),
    _Tick(2000, {"markPrice": "84000.40", "fundingRate": "0.00011"}),  # an equal repeat
    _Tick(3000, {"indexPrice": "84001.40", "nextFundingTime": _NEXT_2}),  # next time only
    _Tick(4000, {"markPrice": "84000.50", "indexPrice": "84001.50", "openInterest": "55000.300"}),
    _Tick(5000, {"markPrice": "84000.60", "indexPrice": "84001.60"}),
    _Tick(
        15000,
        _FULL
        | {"markPrice": "84000.60", "indexPrice": "84001.60", "fundingRate": "0.00011"}
        | {"nextFundingTime": _NEXT_2, "openInterest": "55000.300"},
        snapshot=True,  # a re-sent snapshot: funding strings unchanged
    ),
)
# The adapter's funding rows: the snapshot, then the rate change at +1 s (the equal repeat, the
# next-time-only frame and the unchanged re-sent snapshot store nothing).
_FUNDING_ROWS = ((-2000, "0.0001", 480, _NEXT_1), (1000, "0.00011", None, None))
_OI_VALUE = "55000.300"  # the state from +4 s on


def _ticker(tick: _Tick, symbol: str = "BTCUSDT") -> dict[str, object]:
    raw = {
        "topic": f"tickers.{symbol}",
        "type": "snapshot" if tick.snapshot else "delta",
        "data": {"symbol": symbol, **tick.fields},
        "cs": 1,
        "ts": tick.key // _MS,
    }
    recv = tick.key + tick.recv_lag_ms * _MS
    return {"kind": "frame", "recv_ns": recv, "endpoint": "linear", "raw": json.dumps(raw)}


def _connection(ts_ns: int, event: str = "open", reason: str = "reconnect") -> dict[str, object]:
    return {"kind": "connection", "event": event, "ts_ns": ts_ns, "endpoint": "linear"} | {
        "reason": reason
    }


def _hourly_connections() -> list[dict[str, object]]:
    """Return a REST channel's filler: one connection line per hour (REST readers skip them)."""
    return [_connection(_D0 + hour * 3600 * _NS + _NS) for hour in range(24)]


def _ticker_fillers() -> list[dict[str, object]]:
    """Return the recorder's startup, then an ETHUSDT frame every hour (no raw hour missing)."""
    other = _Tick(0, {"markPrice": "2700.00"})
    frames = [
        _ticker(other, "ETHUSDT") | {"recv_ns": _D0 + hour * 3600 * _NS + 7 * _NS}
        for hour in range(24)
    ]
    return [_connection(_D0 + _NS, reason="startup"), *frames]


def _rest(request: str, body: object, recv_ns: int, endpoint: str = "linear") -> dict[str, object]:
    line = {"kind": "rest", "sent_ns": recv_ns - 200 * _MS, "endpoint": endpoint}
    return line | {"request": request, "recv_ns": recv_ns, "status": 200, "raw": json.dumps(body)}


def _state_at(at_ms: int) -> dict[str, str]:
    state: dict[str, str] = {}
    for tick in _TICKS:
        if tick.at_ms <= at_ms:
            state |= dict(tick.fields)
    return state


def _ticker_poll(at_ms: int) -> dict[str, object]:
    """Return a tickers poll answered at venue time `at_ms`: the WS state then."""
    item = {"symbol": "BTCUSDT", **_state_at(at_ms)}
    body = {"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": [item]}}
    request = "/v5/market/tickers?category=linear&symbol=BTCUSDT"
    return _rest(request, body | {"time": _at(at_ms) // _MS}, _at(at_ms) + 150 * _MS)


def _instrument_item(tick_size: str = "0.10") -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "priceScale": "2",
        "priceFilter": {"minPrice": "0.10", "maxPrice": "1999999.80", "tickSize": tick_size},
        "lotSizeFilter": {"maxOrderQty": "1190.000", "minOrderQty": "0.001", "qtyStep": "0.001"},
    }


def _instruments_poll(at_ms: int, item: Mapping[str, object], category: str = "linear") -> Any:
    body = {"retCode": 0, "retMsg": "OK", "result": {"category": category, "list": [item]}}
    request = f"/v5/market/instruments-info?category={category}&symbol={item['symbol']}"
    return _rest(request, body | {"time": _at(at_ms) // _MS}, _at(at_ms), endpoint=category)


def _spot_item() -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "priceFilter": {"tickSize": "0.1"},
        "lotSizeFilter": {"basePrecision": "0.000001", "minOrderQty": "0.000001"},
    }


def _perpetual(tick: str = "0.10", ts_init: int = _D0 + 2 * _NS) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(_BTC),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        price_increment=Price.from_str(tick),
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
        ts_event=ts_init,
        ts_init=ts_init,
    )


def _pair() -> CurrencyPair:
    return CurrencyPair(
        instrument_id=InstrumentId.from_str(_SPOT),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        price_precision=1,
        size_precision=6,
        price_increment=Price.from_str("0.1"),
        size_increment=Quantity.from_str("0.000001"),
        lot_size=Quantity.from_str("0.000001"),
        max_quantity=None,
        min_quantity=Quantity.from_str("0.000001"),
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal("0.1"),
        margin_maint=Decimal("0.1"),
        maker_fee=Decimal("0.001"),
        taker_fee=Decimal("0.001"),
        ts_event=_D0 + 2 * _NS,
        ts_init=_D0 + 2 * _NS,
    )


def _price_rows(field_name: str) -> dict[int, str]:
    """Every frame carrying the field is one stored row (key -> wire value)."""
    return {t.key: t.fields[field_name] for t in _TICKS if field_name in t.fields}


def _oi_rows(values: Mapping[int, str] | None = None) -> dict[int, str]:
    """Bybit's poll every 300 s over the whole day, at local time `ts_event == ts_init`."""
    stamps = [_D0 + (k * 300 + 150) * _NS for k in range(288)]
    return dict.fromkeys(stamps, _OI_VALUE) | dict(values or {})


@dataclass(frozen=True)
class _Bybit:
    """One Bybit day: the frames, the stored rows (key -> value; None: removed) and extras."""

    ticks: tuple[_Tick, ...] = _TICKS
    mark: Mapping[int, str | None] = field(default_factory=dict)
    index: Mapping[int, str | None] = field(default_factory=dict)
    oi: Mapping[int, str] | None = None
    instrument_polls: tuple[Mapping[str, object], ...] = (_instrument_item(),)
    connections: tuple[dict[str, object], ...] = ()
    coverage: tuple[Mapping[str, object], ...] = ()
    period_s: int = 300
    spot: bool = False
    funding: tuple[tuple[int, str, int | None, str | None], ...] = _FUNDING_ROWS


def _write_jsonl(path: Path, lines: Iterable[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{json.dumps(line)}\n" for line in lines))


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, venue: str, text: str) -> None:
    plan = tmp_path / f"{venue.lower()}.toml"
    plan.write_text(text)
    monkeypatch.setenv("VERIFY_DATA_DIR", str(tmp_path / "verify"))
    monkeypatch.setenv("CATALOG_PATH", str(tmp_path / "catalog"))
    monkeypatch.setenv(f"{venue}_COLLECTOR_CONFIG", str(plan))
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)


def _iid(instrument_id: str) -> InstrumentId:
    return InstrumentId.from_str(instrument_id)


def _stored(rows: Mapping[int, str | None], base: Mapping[int, str]) -> list[tuple[int, str]]:
    merged = dict(base) | dict(rows)
    return [(key, value) for key, value in sorted(merged.items()) if value is not None]


def _write_bybit_catalog(catalog: Path, scenario: _Bybit) -> None:
    writer = ParquetDataCatalog(str(catalog))
    iid = _iid(_BTC)
    marks = _stored(scenario.mark, _price_rows("markPrice"))
    writer.write_data(
        [MarkPriceUpdate(iid, Price.from_str(v), k, k + _ROW_LAG_NS) for k, v in marks]
    )
    indexes = _stored(scenario.index, _price_rows("indexPrice"))
    writer.write_data(
        [IndexPriceUpdate(iid, Price.from_str(v), k, k + _ROW_LAG_NS) for k, v in indexes]
    )
    funding = [
        FundingRateUpdate(
            iid, Decimal(rate), _at(ms), _at(ms) + _ROW_LAG_NS, interval, _next_ns(nt)
        )
        for ms, rate, interval, nt in scenario.funding
    ]
    writer.write_data(funding)
    oi = _oi_rows() if scenario.oi is None else scenario.oi
    writer.write_data(
        [
            OpenInterest(instrument_id=iid, open_interest=Decimal(v), ts_event=k, ts_init=k)
            for k, v in sorted(oi.items())
        ]
    )
    writer.write_data([_perpetual()])
    if scenario.spot:
        writer.write_data([_pair()])


def _next_ns(text: str | None) -> int | None:
    return None if text is None else int(text) * _MS


def _write_bybit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: _Bybit) -> Path:
    """Write the scenario and point the environment at it; return the catalog root."""
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    frames = [_ticker(t) for t in scenario.ticks]
    _write_raw(raw, "BYBIT", "linear.tickers", [*_ticker_fillers(), *frames, *scenario.connections])
    polls = [_ticker_poll(4500), _ticker_poll(16000)]
    _write_raw(raw, "BYBIT", "linear.rest.tickers", [*_hourly_connections(), *polls])
    at = (4500, 16000)
    info = [
        _instruments_poll(ms, item) for ms, item in zip(at, scenario.instrument_polls, strict=False)
    ]
    _write_raw(raw, "BYBIT", "linear.rest.instruments-info", [*_hourly_connections(), *info])
    ids = [_BTC]
    if scenario.spot:
        spot = [_instruments_poll(4500, _spot_item(), "spot")]
        _write_raw(raw, "BYBIT", "spot.rest.instruments-info", [*_hourly_connections(), *spot])
        ids.append(_SPOT)
    catalog.mkdir(parents=True, exist_ok=True)
    _write_bybit_catalog(catalog, scenario)
    _write_jsonl(tmp_path / "coverage" / "bybit.jsonl", scenario.coverage)
    listed = ", ".join(f'"{i}"' for i in ids)
    _env(
        monkeypatch,
        tmp_path,
        "BYBIT",
        f"instruments = [{listed}]\nopen_interest_poll_seconds = {scenario.period_s}\n",
    )
    return catalog


def _run(capsys: pytest.CaptureFixture[str], venue: str) -> tuple[int, dict[str, Any]]:
    status = derivs_tool.main(["--venue", venue, "--day", _DAY.isoformat(), "--json"], lambda: _NOW)
    return status, json.loads(capsys.readouterr().out)


def _types(report: dict[str, Any], instrument_id: str) -> dict[str, dict[str, Any]]:
    (instrument,) = [i for i in report["instruments"] if i["instrument_id"] == instrument_id]
    return {t["kind"]: t for t in instrument["types"]}


def _nonzero(counts: Mapping[str, int]) -> dict[str, int]:
    return {name: value for name, value in counts.items() if value}


def _bybit_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: _Bybit,
) -> tuple[int, dict[str, Any]]:
    _write_bybit(tmp_path, monkeypatch, scenario)
    return _run(capsys, "BYBIT")


def test_a_clean_bybit_day_passes_with_every_row_and_update_accounted_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit())
    assert (status, report["passed"]) == (0, True)
    assert all(i["failing"] == 0 and i["passed"] for i in report["instruments"])
    types = _types(report, _BTC)
    # The recorder's startup snapshot lies outside its gap, so every frame is a judged update.
    assert _nonzero(types["mark"]["row_classes"]) == {EXACT: 7}
    assert _nonzero(types["mark"]["updates"]) == {"stored": 7}
    assert _nonzero(types["index"]["row_classes"]) == {EXACT: 8}
    assert _nonzero(types["funding"]["row_classes"]) == {EXACT: 2}
    assert _nonzero(types["funding"]["updates"]) == {"stored": 2, UNCHANGED: 2, NEXT_TIME_ONLY: 1}
    # 120 polls before the snapshot find no state; the 168 after it equal the WS state.
    assert _nonzero(types["open_interest"]["row_classes"]) == {
        AGREE_STATE: 168,
        REFERENCE_UNAVAILABLE: 120,
    }
    assert types["open_interest"]["coverage"]["poll_gaps"] == 0
    assert all(_nonzero(t["rest"]) == {AGREE_KEY: 2} for t in types.values())
    assert types["mark"]["labels"] == [2]
    assert types["funding"]["labels"] == "none: stored as decimal text"
    definitions = report["instruments"][0]["definitions"]
    assert _nonzero(definitions["polls"]) == {"agree": 1}


def test_a_planted_mark_one_tick_off_is_a_value_mismatch_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Bybit(mark={_at(1000): "84000.31"})
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, scenario)
    mark = _types(report, _BTC)["mark"]
    assert status == 1
    assert _nonzero(mark["row_classes"]) == {EXACT: 6, VALUE_MISMATCH: 1}
    # Story 31.11: the instrument's count is its types' (and definitions') own failing counts.
    (btc,) = [i for i in report["instruments"] if i["instrument_id"] == _BTC]
    counts = [*(t["failing"] for t in btc["types"]), btc["definitions"]["failing"]]
    assert (btc["passed"], btc["failing"]) == (False, sum(counts))
    assert btc["failing"] >= 1
    assert mark["updates"]["not_stored"] == 1  # the frame's own value was never stored
    assert mark["examples"][0].startswith(f"value_mismatch@{_at(1000)}: stored 84000.31")


def test_a_removed_index_row_leaves_its_update_not_stored_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit(index={_at(0): None}))
    index = _types(report, _BTC)["index"]
    assert status == 1
    assert _nonzero(index["updates"]) == {"stored": 7, "not_stored": 1}


def test_a_removed_row_inside_a_stale_run_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run: dict[str, object] = {"kind": "seconds", "instrument_id": _BTC, "reason": "stale"}
    run |= {"first_s": _S - 1, "last_s": _S + 1, "count": 3}
    scenario = _Bybit(index={_at(0): None}, coverage=(run,))
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, scenario)
    assert status == 0
    assert _nonzero(_types(report, _BTC)["index"]["updates"]) == {
        "stored": 7,
        "not_stored_explained": 1,
    }


def test_a_collector_snapshot_row_absent_from_the_reference_agrees_with_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Capture's own reconnect snapshot at +2.5 s: no recorder frame has its `ts`."""
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit(mark={_at(2500): "84000.40"}))
    assert status == 0
    assert _nonzero(_types(report, _BTC)["mark"]["row_classes"]) == {EXACT: 7, AGREE_STATE: 1}


def test_a_row_equal_to_no_state_is_unmatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit(mark={_at(2500): "84000.99"}))
    assert status == 1
    assert _types(report, _BTC)["mark"]["row_classes"][UNMATCHED] == 1


def test_index_files_at_two_precisions_fail_on_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_bybit(tmp_path, monkeypatch, _Bybit(index={_at(5000): None}))
    # Another file, labelled 3; its `ts_init` after the first file's (the catalog keeps them disjoint).
    late = IndexPriceUpdate(_iid(_BTC), Price.from_str("84001.600"), _at(5000), _at(20000))
    ParquetDataCatalog(str(catalog)).write_data([late])
    status, report = _run(capsys, "BYBIT")
    index = _types(report, _BTC)["index"]
    assert status == 1
    assert (index["labels"], index["label_vs_definition"]) == ([2, 3], 1)
    assert _nonzero(index["row_classes"]) == {EXACT: 8}  # every value itself is right


def test_a_neighbour_day_file_read_for_matching_brings_no_label_into_the_day(
    tmp_path: Path,
) -> None:
    writer = ParquetDataCatalog(str(tmp_path))
    before = _D0 - _NS // 2  # read by Hyperliquid's window, but yesterday's row
    writer.write_data([MarkPriceUpdate(_iid(_BTC), Price.from_str("1.000"), before, before)])
    inside = _D0 + 10 * _NS
    writer.write_data([MarkPriceUpdate(_iid(_BTC), Price.from_str("1.00"), inside, inside)])
    day = range(_D0, _D0 + 86_400 * _NS)
    window = DerivsCatalog(tmp_path).prices("mark", _BTC, day.start - _NS, day.stop + _NS, day)
    assert len(window.rows) == 2  # both still consume their updates
    assert [(f.label, f.first_ts_init) for f in window.files] == [(2, inside)]


def test_a_bybit_category_the_recorder_does_not_record_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_bybit(tmp_path, monkeypatch, _Bybit())
    (tmp_path / "bybit.toml").write_text(f'instruments = ["{_BTC}", "BTCUSD-INVERSE.BYBIT"]\n')
    with pytest.raises(SystemExit, match=r"BTCUSD-INVERSE.BYBIT: Bybit category 'inverse'"):
        derivs_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)


def test_a_mark_file_without_its_label_is_refused_and_ledgered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _write_bybit(tmp_path, monkeypatch, _Bybit())
    raw = (84000_10 * 10**14).to_bytes(16, "little", signed=True)
    table = pa.table(
        {
            "value": pa.array([raw], pa.binary(16)),
            "ts_event": pa.array([_at(7000)], pa.uint64()),
            "ts_init": pa.array([_at(7000)], pa.uint64()),
        }
    )
    pq.write_table(table, catalog / "data" / "mark_price_update" / _BTC / "unlabelled.parquet")
    before = error_ledger.counts().get(sites.DERIVS_REFUSED, 0)
    with pytest.raises(SystemExit, match=r"unlabelled.parquet: a mark/index file without"):
        derivs_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    assert error_ledger.counts().get(sites.DERIVS_REFUSED, 0) == before + 1


def test_a_derivative_row_under_a_spot_id_is_fabricated_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_bybit(tmp_path, monkeypatch, _Bybit(spot=True))
    row = MarkPriceUpdate(_iid(_SPOT), Price.from_str("84000.1"), _at(0), _at(0) + _NS)
    ParquetDataCatalog(str(catalog)).write_data([row])
    status, report = _run(capsys, "BYBIT")
    assert status == 1
    assert report["spot"]["fabricated"] == 1
    assert report["spot"]["rows"][f"mark_price_update/{_SPOT}"] == 1
    assert report["spot"]["rows"][f"custom_open_interest/{_SPOT}"] == 0
    (spot,) = [i for i in report["instruments"] if i["instrument_id"] == _SPOT]
    assert (spot["types"], _nonzero(spot["definitions"]["polls"])) == ([], {"agree": 1})


def test_a_clean_spot_id_prints_zero_rows_per_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_bybit(tmp_path, monkeypatch, _Bybit(spot=True))
    status = derivs_tool.main(["--venue", "BYBIT", "--day", _DAY.isoformat()], lambda: _NOW)
    text = capsys.readouterr().out
    assert status == 0
    assert f"funding_rate_update/{_SPOT}: 0 rows" in text
    assert "fabricated=0" in text


def test_an_open_interest_poll_gap_without_a_restart_run_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = _oi_rows()
    for k in (60, 61):  # 05:00:150 and 05:05:150 lost: 900 s between the rows around them
        rows.pop(_D0 + (k * 300 + 150) * _NS)
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit(oi=rows))
    coverage = _types(report, _BTC)["open_interest"]["coverage"]
    assert status == 1
    assert (coverage["poll_gaps"], coverage["poll_gaps_explained"]) == (1, 0)
    assert (coverage["expected"], coverage["rows"]) == (288, 286)


def test_an_open_interest_poll_gap_overlapped_by_a_restart_run_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = _oi_rows()
    for k in (60, 61):
        rows.pop(_D0 + (k * 300 + 150) * _NS)
    run: dict[str, object] = {"kind": "seconds", "instrument_id": _BTC, "reason": "restart"}
    run |= {"first_s": _D0 // _NS + 18_300, "last_s": _D0 // _NS + 18_400, "count": 101}
    scenario = _Bybit(oi=rows, coverage=(run,))
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, scenario)
    assert status == 0
    assert _types(report, _BTC)["open_interest"]["coverage"]["poll_gaps_explained"] == 1


def test_an_open_interest_row_stamped_apart_from_its_ts_init_breaks_the_ts_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_bybit(tmp_path, monkeypatch, _Bybit())
    at = _D0 + (86_400 - 120) * _NS  # 23:58, after the last poll's file
    odd = OpenInterest(
        instrument_id=_iid(_BTC), open_interest=Decimal(_OI_VALUE), ts_event=at, ts_init=at + 1
    )
    ParquetDataCatalog(str(catalog)).write_data([odd])
    status, report = _run(capsys, "BYBIT")
    assert status == 1
    assert _types(report, _BTC)["open_interest"]["row_classes"][TS_RULE] == 1


def test_a_venue_tick_change_is_reported_and_later_polls_differ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    polls = (_instrument_item(), _instrument_item("0.20"))
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit(instrument_polls=polls))
    definitions = report["instruments"][0]["definitions"]
    assert status == 1
    assert _nonzero(definitions["polls"]) == {"agree": 1, DIFFERS: 1}
    assert definitions["venue_changes"] == [f"{_at(16000)} price_increment: 0.10 -> 0.20"]
    assert definitions["definitions_ts_init"] == [_D0 + 2 * _NS]
    assert definitions["differs"] == [f"{_at(16000)} price_increment: stored 0.10, venue 0.20"]


def test_a_recorded_frame_inside_a_gap_margin_is_still_matched_and_the_rest_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    The recorder is disconnected 2.5-2.6 s, widened 5 s each way (-2.5 s .. +7.6 s). Every frame
    it did record there still judges its row exactly; a row at +2.55 s, whose frame the recorder
    missed, has nothing to be compared with: `reference_unavailable`, never `unmatched`.
    """
    gap = (_connection(_at(2500), "close", "server_closed"), _connection(_at(2600)))
    scenario = _Bybit(connections=gap, mark={_at(2550): "84000.99"})
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, scenario)
    mark = _types(report, _BTC)["mark"]
    assert status == 0
    assert _nonzero(mark["row_classes"]) == {EXACT: 7, REFERENCE_UNAVAILABLE: 1}
    assert _nonzero(mark["updates"]) == {"stored": 7}
    assert _nonzero(mark["rest"]) == {AGREE_KEY: 1, UNALIGNED: 1}


def test_an_off_grid_row_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_bybit(tmp_path, monkeypatch, _Bybit())
    raw = (840_001_050 * 10**12).to_bytes(16, "little", signed=True)  # 84000.105 at label 2
    table = pa.table(
        {
            "value": pa.array([raw], pa.binary(16)),
            "ts_event": pa.array([_at(2500)], pa.uint64()),
            "ts_init": pa.array([_at(20000)], pa.uint64()),
        }
    ).replace_schema_metadata({"instrument_id": _BTC, "price_precision": "2"})
    pq.write_table(table, catalog / "data" / "mark_price_update" / _BTC / "odd.parquet")
    status, report = _run(capsys, "BYBIT")
    assert status == 1
    assert _types(report, _BTC)["mark"]["row_classes"][OFF_GRID] == 1


def test_a_duplicate_row_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _write_bybit(tmp_path, monkeypatch, _Bybit())
    again = MarkPriceUpdate(_iid(_BTC), Price.from_str("84000.30"), _at(1000), _at(20000))
    ParquetDataCatalog(str(catalog)).write_data([again])  # another flush's file
    status, report = _run(capsys, "BYBIT")
    assert status == 1
    assert _nonzero(_types(report, _BTC)["mark"]["row_classes"]) == {EXACT: 7, DUPLICATE: 1}


def test_a_stored_null_funding_component_against_a_frame_that_carried_it_is_a_value_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The snapshot frame carried `fundingIntervalHour` and `nextFundingTime`; the row drops them."""
    rows = ((-2000, "0.0001", None, None), _FUNDING_ROWS[1])
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit(funding=rows))
    funding = _types(report, _BTC)["funding"]
    assert status == 1
    assert _nonzero(funding["row_classes"]) == {EXACT: 1, VALUE_MISMATCH: 1}


def test_an_empty_funding_rate_writes_nothing_but_updates_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    A frame with `fundingRate: ""` (Bybit's dated futures): the adapter's parse fails, so no row,
    but its cache now holds "", so the re-sent snapshot's rate at +15 s is a change and stored.
    """
    ticks = (*_TICKS, _Tick(7000, {"fundingRate": ""}))
    rows = (*_FUNDING_ROWS, (15000, "0.00011", 480, _NEXT_2))
    status, report = _bybit_day(tmp_path, monkeypatch, capsys, _Bybit(ticks=ticks, funding=rows))
    funding = _types(report, _BTC)["funding"]
    assert status == 0
    assert _nonzero(funding["updates"]) == {"stored": 3, UNCHANGED: 1, NEXT_TIME_ONLY: 1}


def test_a_missing_coverage_record_or_raw_hour_fails_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_bybit(tmp_path, monkeypatch, _Bybit())
    (tmp_path / "coverage" / "bybit.jsonl").unlink()
    status, report = _run(capsys, "BYBIT")
    assert (status, report["coverage_present"], report["missing_raw_files"]) == (1, False, [])


def test_a_missing_raw_hour_fails_the_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_bybit(tmp_path, monkeypatch, _Bybit())
    hour = tmp_path / "verify" / "raw" / "bybit" / "linear.tickers" / "2026-09-29T03.jsonl.zst"
    hour.unlink()
    status, report = _run(capsys, "BYBIT")
    assert (status, report["missing_raw_files"]) == (1, ["linear.tickers/2026-09-29T03"])
    assert _types(report, _BTC)["mark"]["passed"]  # the instrument itself is still clean


def test_a_reference_no_rest_poll_agrees_with_is_unvalidated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_bybit(tmp_path, monkeypatch, _Bybit())
    polls = []
    for at_ms in (4500, 16000):
        poll = _ticker_poll(at_ms)
        body = json.loads(str(poll["raw"]))
        body["result"]["list"][0]["markPrice"] = "84999.99"  # never a WS state
        polls.append(poll | {"raw": json.dumps(body)})
    channel = tmp_path / "verify" / "raw" / "bybit" / "linear.rest.tickers"
    for path in channel.iterdir():
        path.unlink()
    _write_raw(
        tmp_path / "verify", "BYBIT", "linear.rest.tickers", [*_hourly_connections(), *polls]
    )
    status, report = _run(capsys, "BYBIT")
    mark = _types(report, _BTC)["mark"]
    assert status == 1
    assert (mark["reference"], _nonzero(mark["rest"])) == ("unvalidated", {BETWEEN_PUSHES: 2})
    assert _nonzero(mark["row_classes"]) == {EXACT: 7}  # the rows are fine; the oracle is not


# --- Hyperliquid ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Ctx:
    """One `activeAssetCtx` frame: receipt in ms from `_S` and its four fields."""

    at_ms: int
    mark: str
    oracle: str
    funding: str
    oi: str

    @property
    def recv_ns(self) -> int:
        return _at(self.at_ms)


_CTXS = (
    _Ctx(-3000, "150.1", "150.2", "0.0000125", "1000.5"),
    _Ctx(-2000, "150.1", "150.2", "0.0000125", "1000.5"),  # no change
    _Ctx(-1000, "150.11", "150.2", "0.0000125", "1000.5"),
    _Ctx(0, "150.11", "150.21", "0.0000125", "1000.6"),
    _Ctx(1000, "150.11", "150.21", "-0.0000013", "1000.6"),
    _Ctx(2000, "150.12", "150.21", "-0.0000013", "1000.7"),
    _Ctx(3000, "150.12", "150.21", "-0.0000013", "1000.7"),
)
_HL_FIELDS = (
    ("mark", "markPx"),
    ("oracle", "oraclePx"),
    ("funding", "funding"),
    ("oi", "openInterest"),
)


def _context(ctx: _Ctx, coin: str = "SOL") -> dict[str, object]:
    fields = {wire: getattr(ctx, name) for name, wire in _HL_FIELDS}
    raw = {"channel": "activeAssetCtx", "data": {"coin": coin, "ctx": fields | {"premium": "0"}}}
    return {"kind": "frame", "recv_ns": ctx.recv_ns, "endpoint": "ws", "raw": json.dumps(raw)}


def _changes(name: str, ctxs: Iterable[_Ctx] = _CTXS) -> list[tuple[int, str]]:
    """Return the adapter's rows: each frame whose field string changed (receipt, value)."""
    out, last = [], None
    for ctx in ctxs:
        value = getattr(ctx, name)
        if value != last:
            out.append((ctx.recv_ns, value))
        last = value
    return out


def _hl_price(text: str) -> Price:
    return Price.from_raw(int(Decimal(text).scaleb(16)), 4)


def _sol() -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=_iid(_SOL),
        raw_symbol=Symbol("SOL"),
        base_currency=Currency.from_str("SOL"),
        quote_currency=USD,
        settlement_currency=USDC,
        is_inverse=False,
        price_precision=4,
        price_increment=Price.from_str("0.0001"),
        size_precision=2,
        size_increment=Quantity.from_str("0.01"),
        max_quantity=None,
        min_quantity=None,
        max_notional=None,
        min_notional=None,
        max_price=None,
        min_price=None,
        margin_init=Decimal(0),
        margin_maint=Decimal(0),
        maker_fee=Decimal(0),
        taker_fee=Decimal(0),
        ts_event=_D0 + 2 * _NS,
        ts_init=_D0 + 2 * _NS,
    )


@dataclass(frozen=True)
class _Hl:
    """One Hyperliquid day: the frames, planted OI values, the rows' lag and extra raw lines."""

    ctxs: tuple[_Ctx, ...] = _CTXS
    oi: Mapping[int, str] = field(default_factory=dict)
    lag_ns: int = _ROW_LAG_NS  # capture's `ts_init` minus the recorder's receipt (may be < 0)
    startup_ns: int = _D0 + _NS
    extra_lines: tuple[dict[str, object], ...] = ()


def _write_hyperliquid_catalog(catalog: Path, day: _Hl) -> None:
    writer = ParquetDataCatalog(str(catalog))
    iid, lag = _iid(_SOL), day.lag_ns
    marks = _changes("mark", day.ctxs)
    writer.write_data([MarkPriceUpdate(iid, _hl_price(v), t + lag, t + lag) for t, v in marks])
    indexes = _changes("oracle", day.ctxs)
    writer.write_data([IndexPriceUpdate(iid, _hl_price(v), t + lag, t + lag) for t, v in indexes])
    writer.write_data(
        [
            FundingRateUpdate(iid, Decimal(v), t + lag, t + lag, 60, None)
            for t, v in _changes("funding", day.ctxs)
        ]
    )
    rows = dict(_changes("oi", day.ctxs)) | dict(day.oi)
    writer.write_data(
        [
            OpenInterest(
                instrument_id=iid, open_interest=Decimal(v), ts_event=t + lag, ts_init=t + lag
            )
            for t, v in sorted(rows.items())
        ]
    )
    writer.write_data([_sol()])


def _meta_poll(at_ms: int, ctx: _Ctx) -> dict[str, object]:
    fields = {wire: getattr(ctx, name) for name, wire in _HL_FIELDS}
    universe = [{"name": "BTC", "szDecimals": 5}, {"name": "SOL", "szDecimals": 2}]
    body = [{"universe": universe}, [{"markPx": "1"}, fields]]
    return _rest('{"type": "metaAndAssetCtxs"}', body, _at(at_ms), endpoint="info")


def _hyperliquid_day(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    day: _Hl | None = None,
) -> tuple[int, dict[str, Any]]:
    day = day or _Hl()
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    fillers = [
        _context(_Ctx(0, "1", "1", "0", "1"), "BTC") | {"recv_ns": _D0 + h * 3600 * _NS + 7 * _NS}
        for h in range(24)
    ]
    startup = _connection(day.startup_ns, reason="startup") | {"endpoint": "ws"}
    frames = [startup, *fillers, *map(_context, day.ctxs), *day.extra_lines]
    _write_raw(raw, "HYPERLIQUID", "activeAssetCtx", frames)
    poll = _meta_poll(2700, _CTXS[5])
    _write_raw(raw, "HYPERLIQUID", "rest.metaAndAssetCtxs", [*_hourly_connections(), poll])
    catalog.mkdir(parents=True, exist_ok=True)
    _write_hyperliquid_catalog(catalog, day)
    _write_jsonl(tmp_path / "coverage" / "hyperliquid.jsonl", [])
    _env(monkeypatch, tmp_path, "HYPERLIQUID", f'instruments = ["{_SOL}"]\n')
    return _run(capsys, "HYPERLIQUID")


def test_a_clean_hyperliquid_day_passes_with_every_change_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _hyperliquid_day(tmp_path, monkeypatch, capsys)
    assert (status, report["passed"], report["spot"]) == (0, True, None)
    types = _types(report, _SOL)
    changes = {"mark": 3, "index": 2, "funding": 2, "open_interest": 3}
    for kind, count in changes.items():
        assert _nonzero(types[kind]["row_classes"]) == {EXACT: count}
        assert _nonzero(types[kind]["updates"]) == {"stored": count}
        assert _nonzero(types[kind]["rest"]) == {AGREE: 1}
    assert types["mark"]["labels"] == [4]
    definitions = report["instruments"][0]["definitions"]
    assert _nonzero(definitions["polls"]) == {"agree": 1}
    assert definitions["not_declared"] == [
        "lot_size: not_venue_declared (stored 1: Nautilus default)"
    ]


def test_a_planted_hyperliquid_open_interest_value_is_unmatched_and_its_update_not_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _hyperliquid_day(tmp_path, monkeypatch, capsys, _Hl(oi={_at(2000): "1000.8"}))
    oi = _types(report, _SOL)["open_interest"]
    assert status == 1
    assert _nonzero(oi["row_classes"]) == {EXACT: 2, UNMATCHED: 1}
    assert _nonzero(oi["updates"]) == {"stored": 2, "not_stored": 1}


def test_hyperliquid_rows_across_midnight_consume_their_updates_from_the_other_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Capture stamps each row 40 ms before the recorder's receipt: the update received at
    00:00:00.020 has its row at 23:59:59.980 the day before (read, matched, not counted), and the
    row at 23:59:59.980 of the day has its update at 00:00:00.020 the day after.
    """
    first = _Ctx(-36_000_000 + 20, "150.3", "150.2", "0.0000125", "1000.5")
    last = _Ctx(50_400_000 + 20, "150.4", "150.21", "-0.0000013", "1000.7")
    day = _Hl(ctxs=(first, *_CTXS, last), lag_ns=-40 * _MS, startup_ns=_D0 - 3000 * _NS)
    status, report = _hyperliquid_day(tmp_path, monkeypatch, capsys, day)
    mark = _types(report, _SOL)["mark"]
    assert status == 0
    assert (mark["rows"], _nonzero(mark["row_classes"])) == (4, {EXACT: 4})
    assert _nonzero(mark["updates"]) == {"stored": 4}


def test_a_malformed_frame_of_another_coin_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw = {"channel": "activeAssetCtx", "data": {"coin": "BTC", "ctx": {"markPx": "NaN"}}}
    odd = {"kind": "frame", "recv_ns": _at(500), "endpoint": "ws", "raw": json.dumps(raw)}
    status, report = _hyperliquid_day(tmp_path, monkeypatch, capsys, _Hl(extra_lines=(odd,)))
    assert (status, report["passed"]) == (0, True)


# --- the pure pieces -----------------------------------------------------------------------------


_NO_GAPS = Recording(Intervals.of(()), ())


def _frames(ticks: Iterable[_Tick]) -> list[Frame]:
    return [bybit_ticker(_ticker(t), "test")[1] for t in ticks]


def _row(ms: int, value: str, init_lag: int = _ROW_LAG_NS, on_grid: bool = True) -> StoredValue:
    return StoredValue(_at(ms), _at(ms) + init_lag, (Decimal(value),), on_grid)


def test_the_funding_cache_emulation_compares_strings_and_drops_a_next_time_only_frame() -> None:
    ticks = [
        _Tick(0, {"fundingRate": "0.0001", "nextFundingTime": _NEXT_1}),
        _Tick(1, {"fundingRate": "0.00010"}),  # a different string of an equal value: stored
        _Tick(2, {"fundingRate": "0.00010"}),
        _Tick(3, {"nextFundingTime": _NEXT_2}),
        _Tick(4, {"markPrice": "1.00"}),  # carries neither field: no update
    ]
    hints = [u.hint for u in bybit_funding_updates(_frames(ticks))]
    assert hints == [EXPECTED, EXPECTED, UNCHANGED, NEXT_TIME_ONLY]


def test_match_keyed_classifies_each_row_by_its_own_key() -> None:
    reference = bybit_references(_frames(_TICKS), _NO_GAPS)["mark"]
    judge = RowJudge(reference)
    rows = [
        _row(-2000, "84000.10"),  # exact
        _row(-2000, "84000.10"),  # the same (ts_event, value) again
        _row(-1000, "84000.21"),  # a same-key update of another value
        _row(2500, "84000.40"),  # no key, equal to the state
        _row(2600, "84000.41"),  # no key, equal to nothing
        _row(4000, "84000.50", on_grid=False),
        StoredValue(_at(5000) + 1, _at(5000), (Decimal("84000.60"),)),  # not a whole ms
    ]
    match_keyed(rows, judge)
    expected = {EXACT: 1, DUPLICATE: 1, VALUE_MISMATCH: 1, AGREE_STATE: 1, UNMATCHED: 1}
    assert dict(judge.classes) == expected | {OFF_GRID: 1, TS_RULE: 1}


def test_a_keyed_row_before_any_state_is_reference_unavailable() -> None:
    reference = bybit_references(_frames(_TICKS), _NO_GAPS)["mark"]
    judge = RowJudge(reference)
    match_keyed([_row(-5000, "84000.10")], judge)
    assert dict(judge.classes) == {REFERENCE_UNAVAILABLE: 1}


def test_a_state_received_before_a_reset_is_unknown_after_it() -> None:
    """A reset (recorder reconnect) at +4.2 s: the +4 s frame arrived at +4.1 s, before it."""
    recording = Recording(Intervals.of(()), (_at(4200),))
    reference = bybit_references(_frames(_TICKS), recording)["mark"]
    assert reference.state(_at(4150)) == (Decimal("84000.50"),)
    assert reference.state(_at(4300)) is None
    assert reference.state(_at(15200)) == (Decimal("84000.60"),)


def test_match_by_receive_takes_the_earliest_equal_update_inside_the_bound() -> None:
    frames = [hyperliquid_ctx(_context(c), "test")[1] for c in _CTXS]
    reference = hyperliquid_references(frames, _NO_GAPS)["mark"]
    judge = RowJudge(reference)
    rows = [
        StoredValue(_at(-2950), _at(-2950), (Decimal("150.1"),)),  # the first update
        StoredValue(_at(-1950), _at(-1950), (Decimal("150.1"),)),  # a frame, no update left
        StoredValue(_at(2100), _at(2100), (Decimal("150.11"),)),  # 1.1 s after its frame: none
        StoredValue(_at(2100), _at(2101), (Decimal("150.12"),)),  # a local clock pair broken
    ]
    match_by_receive(rows, judge)
    assert dict(judge.classes) == {EXACT: 1, AGREE_STATE: 1, UNMATCHED: 1, TS_RULE: 1}
    assert judge.consumed == {0}


def test_match_poll_window_accepts_a_state_up_to_two_seconds_before_the_row() -> None:
    reference = bybit_references(_frames(_TICKS), _NO_GAPS)["open_interest"]
    judge = RowJudge(reference)
    rows = [
        StoredValue(_at(4500), _at(4500), (Decimal("55000.300"),)),  # an update in the window
        StoredValue(_at(2200), _at(2200), (Decimal("55000.100"),)),  # the state 2 s before
        StoredValue(_at(2400), _at(2400), (Decimal("55000.100"),)),  # replaced at +0.3 s: too old
        StoredValue(_at(2400), _at(2400), (Decimal("55000.300"),)),  # a later state
        StoredValue(_at(-4000), _at(-4000), (Decimal("55000.100"),)),  # before any state
    ]
    match_poll_window(rows, judge)
    assert dict(judge.classes) == {AGREE_STATE: 2, UNMATCHED: 2, REFERENCE_UNAVAILABLE: 1}


def test_rest_keyed_classes() -> None:
    reference = bybit_references(
        _frames(_TICKS), Recording(Intervals.of([(_at(9000), _at(9500))]), ())
    )
    mark = reference["mark"]

    def poll(at_ms: int, value: str) -> Poll:
        return Poll(True, _at(at_ms), 0, 0, {"markPrice": Decimal(value)})

    assert rest_keyed(poll(4500, "84000.50"), "markPrice", mark) == AGREE_KEY
    assert rest_keyed(poll(4500, "84000.60"), "markPrice", mark) == AGREE_BRACKET  # the next
    assert rest_keyed(poll(4500, "84000.40"), "markPrice", mark) == AGREE_BRACKET  # the previous
    assert rest_keyed(poll(4500, "84000.30"), "markPrice", mark) == BETWEEN_PUSHES
    assert rest_keyed(poll(9200, "84000.60"), "markPrice", mark) == UNALIGNED  # in a gap
    assert rest_keyed(poll(-5000, "84000.10"), "markPrice", mark) == UNALIGNED  # no state yet


def test_rest_by_receive_classes() -> None:
    frames = [hyperliquid_ctx(_context(c), "test")[1] for c in _CTXS]
    oi = hyperliquid_references(frames, _NO_GAPS)["open_interest"]

    def poll(sent_ms: int, recv_ms: int, value: str) -> Poll:
        return Poll(
            True, _at(recv_ms), _at(sent_ms), _at(recv_ms), {"openInterest": Decimal(value)}
        )

    assert rest_by_receive(poll(1900, 2000, "1000.7"), "openInterest", oi) == AGREE
    assert rest_by_receive(poll(-1000, -900, "1000.7"), "openInterest", oi) == BETWEEN_PUSHES


def test_poll_coverage_counts_spacings_over_one_and_a_half_periods() -> None:
    day = range(3600 * _NS)
    stamps = [k * 300 * _NS for k in range(1, 12) if k not in (5, 6)]  # 0 -> 300 s ... 3300 s
    coverage = poll_coverage(stamps, day, 300, Intervals.of(()))
    assert (coverage.expected, coverage.rows, coverage.gaps) == (12, 9, 1)  # 1200 s -> 2100 s
    # A restart covering 1400-1800 s leaves 1200-1400 and 1800-2100 s: both within 450 s.
    explained = poll_coverage(stamps, day, 300, Intervals.of([(1400 * _NS, 1800 * _NS)]))
    assert (explained.gaps, explained.gaps_explained, explained.failing) == (1, 1, 0)
    # A restart touching only 1300-1400 s leaves 1400-2100 s = 700 s uncovered: still failing.
    partial = poll_coverage(stamps, day, 300, Intervals.of([(1300 * _NS, 1400 * _NS)]))
    assert (partial.gaps, partial.gaps_explained, partial.failing) == (1, 0, 1)
    assert poll_coverage([], day, 300, Intervals.of(())).gaps == 1  # the whole day


def test_label_check_counts_labels_and_files_against_the_definition_in_force() -> None:
    files = [FileLabel("a", 2, 10, 12), FileLabel("b", 3, 20, 22), FileLabel("c", 3, 30, 40)]

    def precisions(first: int, last: int) -> set[int]:
        # Precision 2 in force until 25, 3 from 25, then 2 again from 35.
        return {
            p
            for start, end, p in ((0, 24, 2), (25, 34, 3), (35, 99, 2))
            if first <= end and last >= start
        }

    labels, wrong, examples = label_check(files, precisions)
    # `c` starts under precision 3 but a definition of precision 2 is in force before its last row.
    assert (labels, wrong) == ((2, 3), 2)
    assert examples == ("b: label 3, definition [2]", "c: label 3, definition [2]")
    assert label_check(files, lambda _first, _last: set())[1] == 0  # no definition: not judged


def test_the_ts_rules() -> None:
    assert ts_whole_ms(StoredValue(5 * _MS, 7, (Decimal(1),)))
    assert not ts_whole_ms(StoredValue(5 * _MS + 1, 7, (Decimal(1),)))
    assert ts_is_init(StoredValue(7, 7, (Decimal(1),)))
    assert not ts_is_init(StoredValue(7, 8, (Decimal(1),)))


@pytest.mark.parametrize(
    ("text", "value"),
    [("0.0001", Decimal("0.0001")), ("-9.368E-7", Decimal("-0.0000009368")), ("12", Decimal(12))],
)
def test_a_stored_decimal_may_be_exact_scientific_notation(text: str, value: Decimal) -> None:
    assert stored_decimal(text, "test") == value


@pytest.mark.parametrize("text", ["NaN", "1e5", "+1", " 1", "1.", "", "Infinity"])
def test_a_stored_decimal_that_is_not_decimal_text_is_refused(text: str) -> None:
    with pytest.raises(MalformedLine, match="not decimal text"):
        stored_decimal(text, "test")


@pytest.mark.parametrize("text", ["-9.368E-7", "NaN", "+1", "1."])
def test_a_wire_decimal_takes_no_exponent(text: str) -> None:
    with pytest.raises(MalformedLine, match="not a decimal string"):
        decimal_text(text, "test")


def test_a_ticker_frame_without_ts_or_symbol_is_refused() -> None:
    line = _ticker(_TICKS[1])
    raw = json.loads(str(line["raw"]))
    without_ts = line | {"raw": json.dumps({k: v for k, v in raw.items() if k != "ts"})}
    with pytest.raises(MalformedLine, match="`ts`"):
        bybit_ticker(without_ts, "test")
    raw["data"].pop("symbol")
    with pytest.raises(MalformedLine, match="symbol None"):
        bybit_ticker(line | {"raw": json.dumps(raw)}, "test")


def test_an_asset_context_without_coin_or_ctx_is_refused() -> None:
    line = _context(_CTXS[0])
    raw = json.loads(str(line["raw"]))
    raw["data"].pop("ctx")
    with pytest.raises(MalformedLine, match=r"data\.ctx"):
        hyperliquid_ctx(line | {"raw": json.dumps(raw)}, "test")
    raw["data"].pop("coin")
    with pytest.raises(MalformedLine, match="coin None"):
        hyperliquid_ctx(line | {"raw": json.dumps(raw)}, "test")


def test_the_bybit_linear_definition_mapping() -> None:
    venue = bybit_linear_definition(_instrument_item(), 1, "test")
    assert dict(venue.fields) == {
        "price_precision": 2,  # `priceScale`, not the decimals of `0.10`
        "size_precision": 3,
        "price_increment": Decimal("0.10"),
        "size_increment": Decimal("0.001"),
        "lot_size": Decimal("0.001"),
        "min_quantity": Decimal("0.001"),
        "multiplier": Decimal(1),
    }


def test_a_step_with_non_ascii_digits_is_an_odd_venue_definition() -> None:
    item = _instrument_item(tick_size="0.\u0661")  # `Decimal` would take ARABIC-INDIC ONE
    with pytest.raises(OddVenueDefinition, match="is not a decimal step"):
        bybit_linear_definition(item, 1, "test")


def test_the_bybit_spot_definition_mapping() -> None:
    venue = bybit_spot_definition(_spot_item(), 1, "test")
    assert dict(venue.fields) == {
        "price_precision": 1,  # the written decimals of `tickSize`
        "size_precision": 6,
        "price_increment": Decimal("0.1"),
        "size_increment": Decimal("0.000001"),
        "lot_size": Decimal("0.000001"),
        "min_quantity": Decimal("0.000001"),
        "multiplier": Decimal(1),
    }


def test_the_hyperliquid_definition_mapping() -> None:
    venue = hyperliquid_definition({"name": "SOL", "szDecimals": 2}, 1, "test")
    assert dict(venue.fields) == {
        "price_precision": 4,
        "size_precision": 2,
        "price_increment": Decimal("0.0001"),
        "size_increment": Decimal("0.01"),
        "min_quantity": None,
        "multiplier": Decimal(1),
    }
    assert venue.not_declared == ("lot_size",)
    wide = hyperliquid_definition({"name": "X", "szDecimals": 7}, 1, "test")
    assert (wide.fields["price_precision"], wide.fields["price_increment"]) == (0, Decimal(1))


def test_judge_polls_uses_the_definition_in_force_and_needs_one() -> None:
    venue = bybit_linear_definition(_instrument_item(), 100, "test")
    early = replace(venue, recv_ns=5)
    stored = StoredDefinition(50, dict(venue.fields) | {"min_quantity": Decimal("0.002")})
    report = judge_polls([venue, early], 2, [stored])
    assert dict(report.polls) == {"agree": 0, DIFFERS: 1, BEFORE_FIRST_DEFINITION: 1, "failed": 2}
    assert report.differs == ("100 min_quantity: stored 0.002, venue 0.001",)
    assert not report.passed
    nothing = judge_polls([venue], 0, [])
    assert (nothing.no_definition, nothing.passed) == (True, False)
    assert judge_polls([venue], 0, [StoredDefinition(50, dict(venue.fields))]).passed


def test_a_keyed_row_inside_a_gap_margin_is_matched_when_its_frame_was_recorded() -> None:
    gap = Recording(Intervals.of([(_at(-3000), _at(6000))]), ())
    judge = RowJudge(bybit_references(_frames(_TICKS), gap)["mark"])
    match_keyed([_row(1000, "84000.30"), _row(1000, "84000.39"), _row(2500, "84000.99")], judge)
    assert dict(judge.classes) == {EXACT: 1, VALUE_MISMATCH: 1, REFERENCE_UNAVAILABLE: 1}


def test_a_polled_row_inside_a_gap_margin_agrees_when_the_state_was_recorded() -> None:
    gap = Recording(Intervals.of([(_at(3000), _at(9000))]), ())
    judge = RowJudge(bybit_references(_frames(_TICKS), gap)["open_interest"])
    rows = [
        StoredValue(_at(4500), _at(4500), (Decimal("55000.300"),)),
        StoredValue(_at(4600), _at(4600), (Decimal("55000.999"),)),
    ]
    match_poll_window(rows, judge)
    assert dict(judge.classes) == {AGREE_STATE: 1, REFERENCE_UNAVAILABLE: 1}


def test_a_restart_snapshot_funding_row_with_an_unknown_component_is_unavailable() -> None:
    """A reset at +0.5 s: the rate is known again from +1 s, the interval and next time are not."""
    recording = Recording(Intervals.of(()), (_at(500),))
    judge = RowJudge(bybit_references(_frames(_TICKS), recording)["funding"])
    rows = [
        StoredValue(_at(2500), _at(2600), (Decimal("0.00011"), 480, int(_NEXT_1) * _MS)),
        StoredValue(_at(2700), _at(2800), (Decimal("0.00011"), None, None)),
        StoredValue(_at(2900), _at(3000), (Decimal("0.00012"), 480, None)),
    ]
    match_keyed(rows, judge)
    assert dict(judge.classes) == {REFERENCE_UNAVAILABLE: 1, AGREE_STATE: 1, UNMATCHED: 1}


def test_compare_state_fails_a_known_component_that_disagrees() -> None:
    state = (Decimal("0.0001"), 480, None)
    assert compare_state((Decimal("0.0001"), 480, None), state) == AGREE_STATE
    assert compare_state((Decimal("0.0001"), 60, None), state) == UNMATCHED
    assert compare_state((Decimal("0.0001"), 480, 5), state) == REFERENCE_UNAVAILABLE
    assert compare_state((Decimal("0.0002"), None, 5), state) == UNMATCHED


def test_an_empty_funding_rate_updates_the_cache_and_yields_no_update() -> None:
    ticks = [
        _Tick(0, {"fundingRate": "0.0001"}),
        _Tick(1, {"fundingRate": ""}),
        _Tick(2, {"fundingRate": "0.0001"}),  # differs from the cached "": stored again
    ]
    updates = bybit_funding_updates(_frames(ticks))
    assert [(u.key, u.hint) for u in updates] == [(_at(0), EXPECTED), (_at(2), EXPECTED)]


def test_a_bracket_neighbour_across_a_reset_is_not_used() -> None:
    """A reset at +4.2 s: the +5 s update was received after it, so it brackets no poll before."""
    mark = bybit_references(_frames(_TICKS), Recording(Intervals.of(()), (_at(4200),)))["mark"]
    poll = Poll(True, _at(4150), 0, 0, {"markPrice": Decimal("84000.60")})
    assert rest_keyed(poll, "markPrice", mark) == BETWEEN_PUSHES
    plain = bybit_references(_frames(_TICKS), _NO_GAPS)["mark"]
    assert rest_keyed(poll, "markPrice", plain) == AGREE_BRACKET


def test_venue_oddities_in_a_poll_are_failed_polls_not_refusals() -> None:
    request = "/v5/market/tickers?category=linear&symbol=BTCUSDT"
    empty = _rest(request, {"retCode": 0, "result": {"list": []}, "time": 1}, _at(0))
    refused = _rest(request, {"retCode": 0}, _at(0)) | {"refusal": "result.list is empty"}
    errored = _rest(request, {"retCode": 0}, _at(0)) | {"error": "timeout"}
    for record in (empty, refused, errored):
        assert not bybit_ticker_poll(record, "test")[1].ok
    no_sol = _rest('{"type": "metaAndAssetCtxs"}', [{"universe": [{"name": "BTC"}]}, [{}]], _at(0))
    uneven = _meta_poll(0, _CTXS[0])
    body = json.loads(str(uneven["raw"]))
    body[1].pop()
    for record in (no_sol, uneven | {"raw": json.dumps(body)}):
        assert not hyperliquid_ctx_poll(record, "SOL", "test").ok


def test_a_dated_future_definition_is_read_from_crypto_future(tmp_path: Path) -> None:
    iid = "BTCUSDT-25SEP26-LINEAR.BYBIT"
    future = CryptoFuture(
        instrument_id=_iid(iid),
        raw_symbol=Symbol("BTCUSDT-25SEP26"),
        underlying=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        activation_ns=0,
        expiration_ns=_D0 + 86_400 * _NS,
        price_precision=1,
        size_precision=3,
        price_increment=Price.from_str("0.5"),
        size_increment=Quantity.from_str("0.001"),
        ts_event=_D0,
        ts_init=_D0,
    )
    ParquetDataCatalog(str(tmp_path)).write_data([future])
    (stored,) = DerivsCatalog(tmp_path).definitions(iid)
    assert (stored.ts_init, stored.fields["price_increment"]) == (_D0, Decimal("0.5"))


def test_a_funding_rate_that_is_not_text_is_a_refusal_not_a_crash() -> None:
    row = {"rate": 12, "interval": None, "next_funding_ns": None, "ts_event": 1, "ts_init": 1}
    with pytest.raises(ValueError, match="is not JSON text"):
        funding_row(row, "test")


def test_hyperliquid_updates_are_sorted_by_receipt_when_the_clock_steps_back() -> None:
    frames = [hyperliquid_ctx(_context(c), "test")[1] for c in (_CTXS[3], _CTXS[0])]
    updates = hyperliquid_references(frames, _NO_GAPS)["mark"].updates
    assert [u.key for u in updates] == sorted(u.key for u in updates)


def test_the_definition_dirs_are_the_kernel_definition_dirnames() -> None:
    """The reference's literal cannot import the kernel's (DATA-02): held equal here instead."""
    assert DEFINITION_DIRS == DEFINITION_DIRNAMES
