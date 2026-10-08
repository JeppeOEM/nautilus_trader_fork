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
`python3 -m verification.candles` end to end (Story 31.8): a real catalog written by
`ParquetDataCatalog.write_data` (`DydxSecondSnapshot`s whose trade columns are the reference's own
trades, and the `TradeTick`s), a candle store built by the real `candles.application.rebuild`,
the served bars read from the real `data_api` route through `fastapi.testclient` as the tool's
`fetch`, reference lines in the recorder's raw format and a coverage record. The clean day passes;
every planted defect of the story's I/O matrix makes its failing count non-zero and the exit
status 1; every explained case passes. Then the pure classes, and the urllib pager against a stdlib
`http.server` stub.

The code under test (`candles`, `data_api`) is driven from this test module only
(`tests/test_boundaries.py`'s `COMPOSITION_ROOTS`); the tool itself never imports it.
"""

import json
import socket
import sqlite3
import threading
from collections.abc import Callable
from collections.abc import Iterator
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from datetime import timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from urllib.parse import urlsplit

import data_api.app as app_module
import data_api.routes.candles as candles_routes
import pyarrow as pa
import pytest
from candles.application.rebuild import rebuild_instrument
from fastapi.testclient import TestClient
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

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
from verification import candles
from verification.application import sites
from verification.application.conservation import day_start_ns
from verification.domain.candle_check import BOTH_UNDEFINED
from verification.domain.candle_check import EXACT
from verification.domain.candle_check import FLOAT_NOISE
from verification.domain.candle_check import NO_DATA
from verification.domain.candle_check import NOT_FETCHED
from verification.domain.candle_check import OMITTED
from verification.domain.candle_check import PARTIAL_MISMATCH
from verification.domain.candle_check import PARTIAL_OK
from verification.domain.candle_check import REF_DIFFERENT
from verification.domain.candle_check import REF_EXPLAINED
from verification.domain.candle_check import REF_RECORDER_GAP
from verification.domain.candle_check import SERVED_DIFFERS
from verification.domain.candle_check import SERVED_EXTRA
from verification.domain.candle_check import SERVED_MISSING
from verification.domain.candle_check import TRADED
from verification.domain.candle_check import UNTRADED
from verification.domain.candle_check import WEEK_CLASSES
from verification.domain.candle_check import WEEK_JUDGED
from verification.domain.candle_check import WEEK_OPEN
from verification.domain.candle_check import Causes
from verification.domain.candle_check import PreparedDay
from verification.domain.candle_check import ServedBar
from verification.domain.candle_check import WeekFacts
from verification.domain.candle_check import bucket_kind
from verification.domain.candle_check import fold_reference
from verification.domain.candle_check import is_partial
from verification.domain.candle_check import judge_reference
from verification.domain.candle_check import judge_served_bucket
from verification.domain.candle_check import judge_week
from verification.domain.candle_check import merge_candles
from verification.domain.candle_check import prepare_day
from verification.domain.candle_check import week_start_ms
from verification.domain.catalog_check import DIFFERENT
from verification.domain.catalog_check import TradeRow
from verification.domain.conservation import BUYER
from verification.domain.conservation import SELLER
from verification.domain.conservation import Intervals
from verification.domain.conservation import ReferenceTrade
from verification.domain.reference_signals import RefCandle
from verification.domain.reference_signals import fold_candles
from verification.domain.trade_check import TradeColumns
from verification.infrastructure.liquidation_reader import LiquidationCatalog
from verification.infrastructure.raw_store import channel_file
from verification.infrastructure.raw_store import encode_line
from verification.infrastructure.raw_store import hour_of
from verification.infrastructure.served_candles import ServedCandles
from verification.infrastructure.served_candles import Unservable
from verification.infrastructure.served_candles import parse_page
from verification.infrastructure.served_candles import urllib_fetch


_NS = 1_000_000_000
_MS = 1_000_000
_DAY_S = 86_400
_BTC = "BTCUSDT-LINEAR.BYBIT"
_DAY = date(2026, 9, 29)  # a Tuesday: its week (from Monday 09-28) is still open two days later
_WEEK_DAY = date(2026, 9, 22)  # a Tuesday of a week closed by 09-30
_OTHER_DAY = date(2026, 9, 25)  # the Friday of that week, with trades of its own
_TRADED = 120  # seconds 10:00:00-10:01:59 trade once each; 10:02:00-10:02:59 have rows, no trade
_ROWS = 180
_UNTRADED_SECOND = 130  # a row without trades: where a reference-only trade is planted
_UNOBSERVED_SECOND = 200  # no row: where a reference trade of an unobserved second is planted
_HOUR_OFFSET_S = 36_000  # 10:00:00


def _d0(day: date) -> int:
    return day_start_ns(day)


def _s0(day: date) -> int:
    return _d0(day) // _NS + _HOUR_OFFSET_S


# --- the day's trades and rows --------------------------------------------------------------------


@dataclass(frozen=True)
class _Trade:
    """One venue trade (`k`: its second after 10:00:00)."""

    trade_id: str
    ts_ns: int
    price: Decimal
    size: Decimal
    buy: bool


def _trade(day: date, k: int, base: str = "84000.00", tag: str = "t") -> _Trade:
    price = Decimal(base) + Decimal(k % 7) * Decimal("0.10")
    ts_ns = (_s0(day) + k) * _NS + 300 * _MS
    return _Trade(f"{tag}{day.day}-{k}", ts_ns, price, Decimal("0.010"), k % 3 != 0)


def _day_trades(day: date, base: str = "84000.00") -> list[_Trade]:
    return [_trade(day, k, base) for k in range(_TRADED)]


def _snapshot(second: int, trade: _Trade | None) -> Any:
    ts_event = second * _NS + _NS // 2
    price = None if trade is None else trade.price
    buy = trade is not None and trade.buy
    sell = trade is not None and not trade.buy
    return make_snapshot(
        instrument_id=_BTC,
        bid_prices=[Decimal("83999.00")],
        bid_sizes=[Decimal("1.000")],
        ask_prices=[Decimal("84001.00")],
        ask_sizes=[Decimal("1.000")],
        buy_volume=trade.size if buy and trade else 0,
        sell_volume=trade.size if sell and trade else 0,
        buy_count=int(buy),
        sell_count=int(sell),
        ts_event=ts_event,
        ts_init=ts_event + _NS,
        open_price=price,
        high_price=price,
        low_price=price,
        close_price=price,
        price_precision=2,
        size_precision=3,
    )


def _tick(trade: _Trade) -> TradeTick:
    return TradeTick(
        InstrumentId.from_str(_BTC),
        Price.from_str(str(trade.price)),
        Quantity.from_str(str(trade.size)),
        AggressorSide.BUYER if trade.buy else AggressorSide.SELLER,
        TradeId(trade.trade_id),
        trade.ts_ns,
        trade.ts_ns + 51 * _MS,
    )


def _definition() -> CryptoPerpetual:
    """
    Return the definition the served route labels its prices with (Story 32.5: no definition, a
    404 naming the id): precisions 2 and 3, as the rows are written.
    """
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(_BTC),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        ts_event=0,
        ts_init=0,
    )


def _write_catalog(catalog: Path, day: date, trades: list[_Trade]) -> None:
    """Rows for seconds 0..179 after 10:00 of `day`, each traded one carrying its trade's fold."""
    by_second = {t.ts_ns // _NS: t for t in trades}
    s0 = _s0(day)
    writer = ParquetDataCatalog(str(catalog))
    writer.write_data([_tick(t) for t in trades])
    writer.write_data([_snapshot(s0 + k, by_second.get(s0 + k)) for k in range(_ROWS)])


def _liquidation(day: date, k: int, key: str, side: LiquidatedSide, size: str) -> Liquidation:
    """Return an archived liquidation `k` s after 10:00 of `day` (bankruptcy price 84000.00)."""
    ts_ns = (_s0(day) + k) * _NS + 400 * _MS
    iid = InstrumentId.from_str(_BTC)
    return Liquidation.from_wire_text(iid, side, size, "84000.00", (2, 3), key, ts_ns, ts_ns)


# --- the recorder's raw lines ---------------------------------------------------------------------


def _frame(trades: list[_Trade]) -> dict[str, object]:
    items = [
        {"T": t.ts_ns // _MS, "s": "BTCUSDT", "S": "Buy" if t.buy else "Sell", "v": str(t.size)}
        | {"p": str(t.price), "L": "PlusTick", "i": t.trade_id, "BT": False, "RPI": False}
        for t in trades
    ]
    raw = {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "ts": trades[0].ts_ns // _MS}
    recv_ns = trades[0].ts_ns + 50 * _MS
    return {
        "kind": "frame",
        "recv_ns": recv_ns,
        "endpoint": "linear",
        "raw": json.dumps(raw | {"data": items}),
    }


def _poll(trades: list[_Trade], recv_ns: int) -> dict[str, object]:
    rows = [
        {"execId": t.trade_id, "symbol": "BTCUSDT", "price": str(t.price), "size": str(t.size)}
        | {"side": "Buy" if t.buy else "Sell", "time": str(t.ts_ns // _MS), "seq": "1"}
        for t in reversed(trades)
    ]
    raw = {"retCode": 0, "retMsg": "OK", "result": {"category": "linear", "list": rows}}
    request = "/v5/market/recent-trade?category=linear&symbol=BTCUSDT&limit=1000"
    line = {"kind": "rest", "sent_ns": recv_ns - _MS, "endpoint": "linear", "request": request}
    return line | {"recv_ns": recv_ns, "status": 200, "raw": json.dumps(raw)}


def _connection(ts_ns: int, event: str = "open", reason: str = "reconnect") -> dict[str, object]:
    return {"kind": "connection", "event": event, "ts_ns": ts_ns, "endpoint": "linear"} | {
        "reason": reason
    }


def _every_hour(day: date, ws: bool) -> list[dict[str, object]]:
    """Return a line in each hour of the day (WS: a startup open, then empty frames; no gap)."""
    d0 = _d0(day)
    start = _connection(d0 + _NS, reason="startup")
    if not ws:
        return [start, *(_connection(d0 + h * 3600 * _NS + _NS) for h in range(1, 24))]
    empty = json.dumps({"topic": "publicTrade.BTCUSDT", "data": []})
    frames = [
        {"kind": "frame", "recv_ns": d0 + h * 3600 * _NS + _NS, "endpoint": "ws", "raw": empty}
        for h in range(1, 24)
    ]
    return [start, *frames]


def _stamp(line: Mapping[str, object]) -> int:
    stamp = line.get("recv_ns", line.get("ts_ns"))
    assert isinstance(stamp, int)
    return stamp


def _write_raw(root: Path, channel: str, lines: list[dict[str, object]]) -> None:
    """File lines by their own hour, in time order, as the recorder's store does."""
    by_hour: dict[int, list[dict[str, object]]] = {}
    for line in sorted(lines, key=_stamp):
        by_hour.setdefault(hour_of(_stamp(line)), []).append(line)
    for hour, hour_lines in by_hour.items():
        path = channel_file(root, "BYBIT", channel, hour)
        path.parent.mkdir(parents=True, exist_ok=True)
        with pa.CompressedOutputStream(str(path), "zstd") as stream:
            for line in hour_lines:
                stream.write(encode_line(line))


# --- the coverage record --------------------------------------------------------------------------


def _coverage_lines(
    day: date, reasons: Mapping[int, str], uncovered: frozenset[int]
) -> list[dict[str, object]]:
    """Return runs naming every second of the day without a row (`not_collected` unless overridden)."""
    s0, d0_s = _s0(day), _d0(day) // _NS
    rows = set(range(s0, s0 + _ROWS))
    runs: list[list[Any]] = []
    for second in range(d0_s, d0_s + _DAY_S):
        if second in rows or second - s0 in uncovered:
            continue
        reason = reasons.get(second - s0, "not_collected")
        if runs and runs[-1][2] == second - 1 and runs[-1][0] == reason:
            runs[-1][2] = second
        else:
            runs.append([reason, second, second])
    return [
        {"kind": "seconds", "instrument_id": _BTC, "reason": r, "first_s": a, "last_s": b}
        | {"count": b - a + 1}
        for r, a, b in runs
    ]


# --- the scenario ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scenario:
    """One Bybit day: what the wire said beyond the archived trades, the coverage, the week."""

    day: date = _DAY
    ws_extra: tuple[_Trade, ...] = ()
    poll_extra: tuple[_Trade, ...] = ()
    connections: tuple[dict[str, object], ...] = ()
    reasons: Mapping[int, str] = field(default_factory=dict)
    uncovered: frozenset[int] = frozenset()
    other_day: date | None = None
    duplicated: tuple[int, ...] = ()  # offsets from 10:00 whose row the catalog holds twice
    liquidations: tuple[Liquidation, ...] = ()  # archived before the store is rebuilt


def _write_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: _Scenario) -> Path:
    """Write the scenario, build the store with the real rebuild; return the catalog root."""
    raw, catalog = tmp_path / "verify", tmp_path / "catalog"
    trades = _day_trades(scenario.day)
    ParquetDataCatalog(str(catalog)).write_data([_definition()])  # once: the route needs it
    _write_catalog(catalog, scenario.day, trades)
    by_second = {t.ts_ns // _NS: t for t in trades}
    copies = [_s0(scenario.day) + k for k in scenario.duplicated]
    for second in copies:
        ParquetDataCatalog(str(catalog)).write_data(
            [_snapshot(second, by_second.get(second))], skip_disjoint_check=True
        )
    if scenario.liquidations:
        ParquetDataCatalog(str(catalog)).write_data(list(scenario.liquidations))
    first, last = _d0(scenario.day), _d0(scenario.day) + _DAY_S * _NS - 1
    if scenario.other_day is not None:
        _write_catalog(catalog, scenario.other_day, _day_trades(scenario.other_day, "85000.00"))
        last = _d0(scenario.other_day) + _DAY_S * _NS - 1
    frames = [_frame([t]) for t in (*trades, *scenario.ws_extra)]
    ws = [*_every_hour(scenario.day, ws=True), *frames, *scenario.connections]
    _write_raw(raw, "linear.publicTrade", ws)
    polls = [_poll([t], t.ts_ns + 20 * _NS) for t in scenario.poll_extra]
    _write_raw(raw, "linear.rest.recent-trade", [*_every_hour(scenario.day, ws=False), *polls])
    coverage = _coverage_lines(scenario.day, scenario.reasons, scenario.uncovered)
    path = tmp_path / "coverage" / "bybit.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in coverage))
    store = tmp_path / "candles" / "candles_bybit.db"
    store.parent.mkdir(parents=True, exist_ok=True)
    rebuild_instrument(str(store), str(catalog), _BTC, first, last)
    plan = tmp_path / "bybit.toml"
    plan.write_text(f'instruments = ["{_BTC}"]\n')
    monkeypatch.setenv("VERIFY_DATA_DIR", str(raw))
    monkeypatch.setenv("CATALOG_PATH", str(catalog))
    monkeypatch.setenv("CANDLES_DIR", str(tmp_path / "candles"))
    monkeypatch.setenv("BYBIT_COLLECTOR_CONFIG", str(plan))
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    monkeypatch.delenv("VERIFY_DATA_API_URL", raising=False)
    return catalog


# --- the served route -----------------------------------------------------------------------------

Rewrite = Callable[[Mapping[str, int], dict[str, Any]], dict[str, Any]]


def _route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rewrite: Rewrite | None = None) -> Any:
    """Drive the real `GET /api/candles` over the test's catalog and store, as the tool's `fetch`."""
    monkeypatch.setattr(candles_routes, "CATALOG_PATH", str(tmp_path / "catalog"))
    monkeypatch.setattr(candles_routes, "CANDLES_DB_DIR", str(tmp_path / "candles"))
    client = TestClient(app_module.app)

    def fetch(url: str) -> tuple[int, bytes]:
        response = client.get(url)
        if rewrite is None or response.status_code != 200:
            return response.status_code, response.content
        query = {k: int(v[0]) for k, v in parse_qs(urlsplit(url).query).items()}
        return 200, json.dumps(rewrite(query, response.json())).encode()

    return fetch


def _main(
    argv: list[str], now_ns: int, fetch: Any, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict[str, Any]]:
    status = candles.main([*argv, "--json"], clock=lambda: now_ns, fetch=fetch)
    return status, json.loads(capsys.readouterr().out)


def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scenario: _Scenario = _Scenario(),
    rewrite: Rewrite | None = None,
    now_ns: int | None = None,
) -> tuple[int, dict[str, Any]]:
    _write_day(tmp_path, monkeypatch, scenario)
    fetch = _route(tmp_path, monkeypatch, rewrite)
    now = _d0(scenario.day) + 2 * _DAY_S * _NS if now_ns is None else now_ns
    argv = [
        "--venue",
        "BYBIT",
        "--day",
        scenario.day.isoformat(),
        "--data-api",
        "http://testserver",
    ]
    return _main(argv, now, fetch, capsys)


def _instrument(report: Mapping[str, Any]) -> dict[str, Any]:
    (instrument,) = report["instruments"]
    return instrument


def _width(report: Mapping[str, Any], bar_seconds: int) -> dict[str, Any]:
    return _instrument(report)["widths"][str(bar_seconds)]


def _minute_ms(day: date, minute: int) -> int:
    return (_s0(day) + minute * 60) * 1000


# --- end to end -----------------------------------------------------------------------------------


def test_a_clean_day_passes_on_every_width(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _run(tmp_path, monkeypatch, capsys)

    assert (status, report["verdict"], report["failing"]) == (0, "PASS", 0)
    instrument = _instrument(report)
    assert instrument["seconds"]["unexplained"] == 0
    assert instrument["trades_unobserved"] == {}
    minute = _width(report, 60)
    assert minute["buckets"] == {TRADED: 2, UNTRADED: 1, NO_DATA: 1437}
    assert minute["catalog"][EXACT] + minute["catalog"][FLOAT_NOISE] == 2  # v: 0.6000000000000001
    assert minute["catalog"][BOTH_UNDEFINED] == 1
    assert minute["served"][EXACT] + minute["served"][FLOAT_NOISE] == 2
    assert minute["served"][PARTIAL_OK] == 2
    assert minute["reference"] == {
        EXACT: 2,
        BOTH_UNDEFINED: 1,
        REF_RECORDER_GAP: 0,
        REF_EXPLAINED: 0,
        REF_DIFFERENT: 0,
    }
    for width in (600, 1800, 2700):  # the read-time widths: served by the route's Parquet fold
        served = _width(report, width)["served"]
        assert (served[EXACT] + served[FLOAT_NOISE], served[PARTIAL_OK]) == (1, 1), width
    assert instrument["week"]["status"] == WEEK_OPEN  # the day's week has not closed


def test_the_text_report_names_every_width_and_the_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", "http://testserver"]
    now = _d0(_DAY) + 2 * _DAY_S * _NS

    status = candles.main(argv, clock=lambda: now, fetch=_route(tmp_path, monkeypatch))

    text = capsys.readouterr().out
    assert status == 0
    assert text.startswith(f"candles BYBIT {_DAY.isoformat()}: PASS")
    assert all(f"{w:>6} s:" in text for w in (60, 300, 600, 900, 1800, 2700, 3600, 14400, 86400))
    assert "1W t=" in text
    assert WEEK_OPEN in text


def test_an_altered_stored_bar_fails_the_catalog_fold_and_the_served_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    db = sqlite3.connect(tmp_path / "candles" / "candles_bybit.db")
    db.execute("UPDATE candles SET c = l WHERE bar_seconds = 60 AND t = ?", (_minute_ms(_DAY, 0),))
    db.commit()
    db.close()
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", "http://testserver"]

    status, report = _main(
        argv, _d0(_DAY) + 2 * _DAY_S * _NS, _route(tmp_path, monkeypatch), capsys
    )

    assert status == 1
    assert _width(report, 60)["catalog"][DIFFERENT] == 1
    assert _width(report, 60)["served"][SERVED_DIFFERS] == 1
    assert _width(report, 300)["failing"] == 0  # only the altered width fails


def test_a_day_with_liquidations_proves_the_order_flow_and_liquidation_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Story 33.3: the rebuilt store and the served bars carry section 2.15's columns, and both equal
    the oracle's own fold -- a long and a short liquidation in minute 0 and one in the untraded
    minute 2 (stored, and never served: a bar with no trade is not a bar). The feed starts at
    09:59:00.400 ("prior"): that minute, the 09:00 hour and the day straddle it and are null on
    both sides (review loop 2), its liquidation counted nowhere; 10:00 onward is known.
    """
    scenario = _Scenario(
        liquidations=(
            _liquidation(_DAY, -60, "prior", LiquidatedSide.LONG, "0.001"),
            _liquidation(_DAY, 5, "a", LiquidatedSide.LONG, "0.041"),
            _liquidation(_DAY, 6, "b", LiquidatedSide.SHORT, "0.500"),
            _liquidation(_DAY, 125, "c", LiquidatedSide.LONG, "0.007"),
        )
    )
    status, report = _run(tmp_path, monkeypatch, capsys, scenario)
    assert (status, report["passed"]) == (0, True)
    db = sqlite3.connect(tmp_path / "candles" / "candles_bybit.db")
    stored = db.execute(
        "SELECT liq_long_v, liq_short_v, liq_n FROM candles WHERE bar_seconds = 60 ORDER BY t"
    ).fetchall()
    db.close()
    assert stored == [(41, 500, 2), (0, 0, 0), (7, 0, 1)]


def test_a_day_archived_before_the_liquidation_feed_is_null_and_the_oracle_agrees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    The feed's first archived liquidation is the day after the judged one: the full-history
    rebuild stores the judged day's `liq_*` null (unknown, never 0), the served bars carry null,
    and the oracle -- deriving the same bound from its own raw read -- agrees, every count 0.
    """
    later = _DAY + timedelta(days=1)
    scenario = _Scenario(
        liquidations=(_liquidation(later, 5, "first", LiquidatedSide.LONG, "0.041"),)
    )
    status, report = _run(tmp_path, monkeypatch, capsys, scenario)
    assert (status, report["passed"]) == (0, True)
    db = sqlite3.connect(tmp_path / "candles" / "candles_bybit.db")
    stored = db.execute(
        "SELECT DISTINCT liq_long_v, liq_short_v, liq_n FROM candles WHERE t < ?",
        (_d0(later) // 1_000_000,),
    ).fetchall()
    db.close()
    assert stored == [(None, None, None)]


def _tamper_buy_v(query: Mapping[str, int], page: dict[str, Any]) -> dict[str, Any]:
    if query["bar_seconds"] == 300 and page["items"]:
        page["items"][-1]["buy_v"] += 1
    return page


def test_a_served_buy_v_off_by_one_unit_is_served_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    status, report = _run(tmp_path, monkeypatch, capsys, rewrite=_tamper_buy_v)
    assert status == 1
    assert _width(report, 300)["served"][SERVED_DIFFERS] == 1
    assert _width(report, 60)["failing"] == 0


def test_a_stored_pv_off_by_one_unit_fails_the_catalog_fold_and_the_served_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    db = sqlite3.connect(tmp_path / "candles" / "candles_bybit.db")
    db.execute(
        "UPDATE candles SET pv = pv + 1 WHERE bar_seconds = 60 AND t = ?", (_minute_ms(_DAY, 0),)
    )
    db.commit()
    db.close()
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", "http://testserver"]
    status, report = _main(
        argv, _d0(_DAY) + 2 * _DAY_S * _NS, _route(tmp_path, monkeypatch), capsys
    )
    assert status == 1
    assert _width(report, 60)["catalog"][DIFFERENT] == 1
    assert _width(report, 60)["served"][SERVED_DIFFERS] == 1


def _drop_partial(query: Mapping[str, int], page: dict[str, Any]) -> dict[str, Any]:
    if query["bar_seconds"] == 60 and page["items"]:
        page["items"][0].pop("partial", None)
    return page


def _wrong_partial(query: Mapping[str, int], page: dict[str, Any]) -> dict[str, Any]:
    if query["bar_seconds"] == 600 and page["items"]:
        page["items"][-1]["partial"] = not page["items"][-1]["partial"]
    return page


@pytest.mark.parametrize(("rewrite", "width"), [(_drop_partial, 60), (_wrong_partial, 600)])
def test_a_served_partial_absent_or_wrong_is_a_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rewrite: Rewrite,
    width: int,
) -> None:
    status, report = _run(tmp_path, monkeypatch, capsys, rewrite=rewrite)

    assert status == 1
    assert _width(report, width)["served"][PARTIAL_MISMATCH] == 1


def _drop_first(query: Mapping[str, int], page: dict[str, Any]) -> dict[str, Any]:
    if query["bar_seconds"] == 300:
        page["items"] = [i for i in page["items"] if i["o"] is not None][1:]
    return page


def _serve_untraded(query: Mapping[str, int], page: dict[str, Any]) -> dict[str, Any]:
    if query["bar_seconds"] == 60 and page["items"]:
        bar = dict(page["items"][-1], t=_minute_ms(_DAY, 2))  # 10:02 has rows, no trade
        page["items"] = [i for i in page["items"] if i["o"] is not None] + [bar]
    return page


@pytest.mark.parametrize(
    ("rewrite", "width", "verdict"),
    [(_drop_first, 300, SERVED_MISSING), (_serve_untraded, 60, SERVED_EXTRA)],
)
def test_a_traded_bucket_not_served_or_an_untraded_one_served_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    rewrite: Rewrite,
    width: int,
    verdict: str,
) -> None:
    status, report = _run(tmp_path, monkeypatch, capsys, rewrite=rewrite)

    assert status == 1
    assert _width(report, width)["served"][verdict] == 1


def test_a_reference_trade_the_catalog_lacks_outside_every_gap_is_ref_different(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = _trade(_DAY, _UNTRADED_SECOND, tag="x")
    status, report = _run(tmp_path, monkeypatch, capsys, _Scenario(ws_extra=(extra,)))

    assert status == 1
    assert _width(report, 60)["reference"][REF_DIFFERENT] == 1
    assert _width(report, 2700)["reference"][REF_DIFFERENT] == 1
    assert _width(report, 60)["served"][SERVED_MISSING] == 0  # the archive's own bars agree


def test_a_duplicated_traded_second_fails_the_reference_and_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Both copies of a traded second reach every fold alike (stored, served, catalog); only the
    # reference, folded once from the venue's trades, sees the doubled volume.
    status, report = _run(tmp_path, monkeypatch, capsys, _Scenario(duplicated=(0,)))

    assert status == 1
    assert _width(report, 60)["reference"][REF_DIFFERENT] == 1
    assert _width(report, 2700)["reference"][REF_DIFFERENT] == 1
    assert report["failing"] == len(_instrument(report)["widths"])  # one per width, nothing else


def test_the_same_trade_inside_a_recorder_gap_is_ref_recorder_gap_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = _trade(_DAY, _UNTRADED_SECOND, tag="x")
    gap = (_connection(extra.ts_ns - 2 * _NS, "close"), _connection(extra.ts_ns + 2 * _NS))
    scenario = _Scenario(poll_extra=(extra,), connections=gap)

    status, report = _run(tmp_path, monkeypatch, capsys, scenario)

    assert (status, report["failing"]) == (0, 0)
    assert _width(report, 60)["reference"][REF_RECORDER_GAP] == 1


def test_a_differing_second_inside_a_coverage_trade_window_is_ref_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = _trade(_DAY, _UNTRADED_SECOND, tag="x")
    catalog = _write_day(tmp_path, monkeypatch, _Scenario(ws_extra=(extra,)))
    window: dict[str, object] = {
        "kind": "trades_unrecoverable",
        "instrument_id": _BTC,
        "reason": "rest_depth",
    }
    window |= {"from_ns": extra.ts_ns - _NS, "to_ns": extra.ts_ns + _NS}
    with (catalog.parent / "coverage" / "bybit.jsonl").open("a") as handle:
        handle.write(json.dumps(window) + "\n")
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", "http://testserver"]

    status, report = _main(
        argv, _d0(_DAY) + 2 * _DAY_S * _NS, _route(tmp_path, monkeypatch), capsys
    )

    assert status == 0
    assert _width(report, 60)["reference"][REF_EXPLAINED] == 1


def test_an_unobserved_second_is_counted_under_its_coverage_reason_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    extra = _trade(_DAY, _UNOBSERVED_SECOND, tag="x")
    scenario = _Scenario(ws_extra=(extra,), reasons={_UNOBSERVED_SECOND: "crossed"})

    status, report = _run(tmp_path, monkeypatch, capsys, scenario)

    assert (status, report["failing"]) == (0, 0)
    assert _instrument(report)["trades_unobserved"] == {"crossed": 1}
    assert _instrument(report)["seconds"]["explained_by_reason"]["crossed"] == 1


def test_a_second_without_a_row_or_a_reason_is_unexplained_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(uncovered=frozenset({_UNOBSERVED_SECOND}))

    status, report = _run(tmp_path, monkeypatch, capsys, scenario)

    assert status == 1
    assert _instrument(report)["seconds"]["unexplained"] == 1
    assert report["failing"] == 1


# --- 1W -------------------------------------------------------------------------------------------


def _week_now() -> int:
    return _d0(_WEEK_DAY) + 8 * _DAY_S * _NS  # 09-30 00:00: the week and the day after it closed


def test_a_closed_week_is_served_whole_on_the_aligned_and_the_chart_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(day=_WEEK_DAY, other_day=_OTHER_DAY)

    status, report = _run(tmp_path, monkeypatch, capsys, scenario, now_ns=_week_now())

    week = _instrument(report)["week"]
    assert (status, week["status"], week["kind"]) == (0, WEEK_JUDGED, TRADED)
    assert week["served"][EXACT] + week["served"][FLOAT_NOISE] == 2  # aligned + chart-like
    assert week["served"][PARTIAL_OK] == 2
    assert week["week_start"] == week_start_ms(_d0(_WEEK_DAY))


def _truncated_week(day_bar: Mapping[str, Any]) -> Rewrite:
    """Serve, on the chart-like 1W page only, the week folded from its first day alone."""
    week_end_ns = (week_start_ms(_d0(_WEEK_DAY)) + 7 * _DAY_S * 1000) * _MS

    def rewrite(query: Mapping[str, int], page: dict[str, Any]) -> dict[str, Any]:
        if query["bar_seconds"] != 604_800 or query["before_ns"] != week_end_ns + _DAY_S * _NS:
            return page
        start = week_start_ms(_d0(_WEEK_DAY))
        page["items"] = [dict(day_bar, t=start) if i["t"] == start else i for i in page["items"]]
        return page

    return rewrite


def test_a_truncated_served_week_is_served_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(day=_WEEK_DAY, other_day=_OTHER_DAY)
    _write_day(tmp_path, monkeypatch, scenario)
    probe = _route(tmp_path, monkeypatch)
    before = (_d0(_WEEK_DAY) + _DAY_S * _NS) // _MS * _MS
    _, body = probe(f"http://testserver/api/candles/{_BTC}?before_ns={before}&bar_seconds=86400")
    day_bar = json.loads(body)["items"][-1]  # the week's first traded day, alone
    fetch = _route(tmp_path, monkeypatch, _truncated_week(day_bar))
    argv = ["--venue", "BYBIT", "--day", _WEEK_DAY.isoformat(), "--data-api", "http://testserver"]

    status, report = _main(argv, _week_now(), fetch, capsys)

    assert status == 1
    assert _instrument(report)["week"]["served"][SERVED_DIFFERS] == 1


_NEXT_MONDAY = date(2026, 9, 28)  # the Monday after `_WEEK_DAY`'s week, before the chart cursor


def _with_next_week(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: _Scenario
) -> dict[str, Any]:
    """Write the scenario plus trades on the next week's Monday; return the week's first day bar."""
    catalog = _write_day(tmp_path, monkeypatch, scenario)
    _write_catalog(catalog, _NEXT_MONDAY, _day_trades(_NEXT_MONDAY, "86000.00"))
    probe = _route(tmp_path, monkeypatch)
    before = (_d0(_WEEK_DAY) + _DAY_S * _NS) // _MS * _MS
    _, body = probe(f"http://testserver/api/candles/{_BTC}?before_ns={before}&bar_seconds=86400")
    return json.loads(body)["items"][-1]


def test_a_chart_page_that_serves_only_the_next_week_is_omitted_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The chart-like cursor (the week's end + 1 day) lies in a week that has data, so the aligned
    # one-week window reads only that week: the judged week is left out, never truncated (D-118).
    _with_next_week(tmp_path, monkeypatch, _Scenario(day=_WEEK_DAY, other_day=_OTHER_DAY))
    fetch = _route(tmp_path, monkeypatch)
    argv = ["--venue", "BYBIT", "--day", _WEEK_DAY.isoformat(), "--data-api", "http://testserver"]

    status, report = _main(argv, _week_now(), fetch, capsys)

    week = _instrument(report)["week"]
    assert (status, week["status"]) == (0, WEEK_JUDGED)
    assert week["served"][OMITTED] == 1
    assert week["served"][EXACT] + week["served"][FLOAT_NOISE] == 1  # the aligned page
    assert week["failing"] == 0


def _planted_week(day_bar: Mapping[str, Any]) -> Rewrite:
    """On the chart-like 1W page only, add the week folded from its first day alone."""
    week_start = week_start_ms(_d0(_WEEK_DAY))
    chart_before = (week_start + 8 * _DAY_S * 1000) * _MS

    def rewrite(query: Mapping[str, int], page: dict[str, Any]) -> dict[str, Any]:
        if query["bar_seconds"] != 604_800 or query["before_ns"] != chart_before:
            return page
        others = [i for i in page["items"] if i["t"] != week_start]
        page["items"] = [dict(day_bar, t=week_start), *others]
        return page

    return rewrite


def test_a_truncated_week_planted_beside_the_next_week_is_served_differs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    scenario = _Scenario(day=_WEEK_DAY, other_day=_OTHER_DAY)
    day_bar = _with_next_week(tmp_path, monkeypatch, scenario)
    fetch = _route(tmp_path, monkeypatch, _planted_week(day_bar))
    argv = ["--venue", "BYBIT", "--day", _WEEK_DAY.isoformat(), "--data-api", "http://testserver"]

    status, report = _main(argv, _week_now(), fetch, capsys)

    assert status == 1
    assert _instrument(report)["week"]["served"][SERVED_DIFFERS] == 1


def test_judge_week_classes() -> None:
    ref = RefCandle(
        0, Decimal("1.00"), Decimal("2.00"), Decimal("1.00"), Decimal("2.00"), Decimal("3.000"), 5
    )
    whole = ServedBar(0, 1.0, 2.0, 1.0, 2.0, 3.0, True)
    facts = WeekFacts(0, True, ref, (2, 3), whole, None, True, True)

    assert judge_week(facts).served == {**_zero_week(), EXACT: 1, PARTIAL_OK: 1, OMITTED: 1}
    unfetched = judge_week(WeekFacts(0, True, ref, (2, 3), whole, None, False, True))
    assert unfetched.served is not None
    assert unfetched.served[NOT_FETCHED] == 1
    open_week = judge_week(WeekFacts(0, False, None, (0, 0), None, None, False, True))
    assert (open_week.status, open_week.failing) == (WEEK_OPEN, 0)
    untraded = RefCandle(0, None, None, None, None, Decimal(0), 5)
    extra = judge_week(WeekFacts(0, True, untraded, (2, 3), whole, None, False, True))
    assert extra.served is not None
    assert extra.served[SERVED_EXTRA] == 1
    assert extra.failing == 1


def _zero_week() -> dict[str, int]:
    return dict.fromkeys(WEEK_CLASSES, 0)


def test_merge_candles_folds_days_into_the_week() -> None:
    monday = RefCandle(0, Decimal(5), Decimal(7), Decimal(4), Decimal(6), Decimal(1), 10)
    tuesday = RefCandle(1, None, None, None, None, Decimal(0), 3)
    friday = RefCandle(4, Decimal(6), Decimal(9), Decimal(3), Decimal(8), Decimal(2), 7)

    week = merge_candles(0, [monday, tuesday, friday])

    assert week == RefCandle(0, Decimal(5), Decimal(9), Decimal(3), Decimal(8), Decimal(3), 20)
    assert merge_candles(0, [tuesday]) == RefCandle(0, None, None, None, None, Decimal(0), 3)


def test_the_week_is_monday_anchored() -> None:
    tuesday = _d0(date(2026, 9, 29))
    assert week_start_ms(tuesday) == _d0(date(2026, 9, 28)) // _MS
    assert week_start_ms(_d0(date(2026, 9, 28))) == _d0(date(2026, 9, 28)) // _MS
    assert week_start_ms(_d0(date(2026, 10, 4))) == _d0(date(2026, 9, 28)) // _MS  # Sunday


# --- refusals and --no-served ---------------------------------------------------------------------


def test_no_served_is_provisional_never_a_full_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--no-served"]

    status, report = _main(argv, _d0(_DAY) + 2 * _DAY_S * _NS, _unreachable, capsys)

    assert (status, report["verdict"], report["passed"]) == (0, "PROVISIONAL", False)
    assert report["served"] == "not checked"
    assert report["provisional"] is True
    assert _width(report, 60)["served"] is None


def _unreachable(url: str) -> tuple[int, bytes]:
    raise AssertionError(f"--no-served fetched {url}")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _refused(argv: list[str], now_ns: int, fetch: Any, match: str) -> None:
    before = error_ledger.counts().get(sites.CANDLES_REFUSED, 0)
    with pytest.raises(SystemExit, match=match):
        candles.main(argv, clock=lambda: now_ns, fetch=fetch)
    assert error_ledger.counts().get(sites.CANDLES_REFUSED, 0) == before + 1


def test_a_data_api_that_is_down_refuses_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    url = f"http://127.0.0.1:{_free_port()}"
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", url]

    _refused(argv, _d0(_DAY) + 2 * _DAY_S * _NS, urllib_fetch, "unreachable")


def test_a_non_200_or_malformed_page_refuses_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", "http://x"]
    now = _d0(_DAY) + 2 * _DAY_S * _NS

    _refused(argv, now, lambda url: (503, b"busy"), "HTTP 503")
    _refused(argv, now, lambda url: (200, b'{"items": 1}'), "no `items` list")


@pytest.mark.parametrize(
    ("setup", "match"),
    [
        (lambda tmp: None, "day not closed"),
        (lambda tmp: (tmp / "coverage" / "bybit.jsonl").unlink(), "coverage record"),
        (lambda tmp: (tmp / "candles" / "candles_bybit.db").unlink(), "candle store"),
    ],
)
def test_an_open_day_or_a_missing_input_refuses_the_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    setup: Callable[[Path], None],
    match: str,
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    setup(tmp_path)
    open_day = match == "day not closed"
    now = _d0(_DAY) + (_DAY_S * _NS if open_day else 2 * _DAY_S * _NS)
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", "http://testserver"]

    _refused(argv, now, _route(tmp_path, monkeypatch), match)


def test_a_missing_raw_directory_refuses_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())
    monkeypatch.setenv("VERIFY_DATA_DIR", str(tmp_path / "nowhere"))
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--no-served"]

    _refused(argv, _d0(_DAY) + 2 * _DAY_S * _NS, _unreachable, "VERIFY_DATA_DIR")


def test_a_crash_is_ledgered_and_re_raised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_day(tmp_path, monkeypatch, _Scenario())

    def boom(url: str) -> tuple[int, bytes]:
        raise RuntimeError("boom")

    before = error_ledger.counts().get(sites.CANDLES_REFUSED, 0)
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--data-api", "http://x"]
    with pytest.raises(RuntimeError, match="boom"):
        candles.main(argv, clock=lambda: _d0(_DAY) + 2 * _DAY_S * _NS, fetch=boom)
    assert error_ledger.counts().get(sites.CANDLES_REFUSED, 0) == before + 1
    assert "crashed: RuntimeError('boom')" in error_ledger.last_details()[sites.CANDLES_REFUSED]


def test_a_usage_error_exits_2() -> None:
    with pytest.raises(SystemExit) as exc:
        candles.main(["--venue", "BYBIT"])
    assert exc.value.code == 2


# --- the pure classes -----------------------------------------------------------------------------


_REF = RefCandle(
    0,
    Decimal("84000.10"),
    Decimal("84000.50"),
    Decimal("83999.90"),
    Decimal("84000.20"),
    Decimal("0.030"),
    60,
)


def test_judge_served_bucket_classes() -> None:
    exact = ServedBar(0, 84000.1, 84000.5, 83999.9, 84000.2, 0.03, False)
    noise = ServedBar(0, 84000.1, 84000.5, 83999.9, 84000.2, 0.030000000000000002, False)
    off = ServedBar(0, 84000.1, 84000.5, 83999.9, 84000.3, 0.03, False)
    untraded = RefCandle(0, None, None, None, None, Decimal(0), 60)

    assert judge_served_bucket(exact, _REF, (2, 3), 60) == (EXACT, PARTIAL_OK)
    assert judge_served_bucket(noise, _REF, (2, 3), 60) == (FLOAT_NOISE, PARTIAL_OK)
    assert judge_served_bucket(off, _REF, (2, 3), 60) == (SERVED_DIFFERS, PARTIAL_OK)
    assert judge_served_bucket(None, _REF, (2, 3), 60) == (SERVED_MISSING,)
    assert judge_served_bucket(exact, untraded, (2, 3), 60) == (SERVED_EXTRA,)
    assert judge_served_bucket(exact, None, (2, 3), 60) == (SERVED_EXTRA,)
    assert judge_served_bucket(None, untraded, (2, 3), 60) == ()
    no_flag = ServedBar(0, 84000.1, 84000.5, 83999.9, 84000.2, 0.03, None)
    assert judge_served_bucket(no_flag, _REF, (2, 3), 60)[1] == PARTIAL_MISMATCH
    no_high = ServedBar(0, 84000.1, None, 83999.9, 84000.2, 0.03, False)
    assert judge_served_bucket(no_high, _REF, (2, 3), 60)[0] == SERVED_DIFFERS


def test_partial_is_the_exact_ninety_percent_rule() -> None:
    assert not is_partial(54, 60)
    assert is_partial(53, 60)
    assert not is_partial(2430, 2700)
    assert is_partial(2429, 2700)
    assert is_partial(544_319, 604_800)
    assert not is_partial(544_320, 604_800)


def _row(second: int, columns: TradeColumns) -> TradeRow:
    return TradeRow(
        ts_event=second * _NS + _NS // 2,
        price_precision=2,
        size_precision=3,
        open_price=columns.open_price,
        high_price=columns.high_price,
        low_price=columns.low_price,
        close_price=columns.close_price,
        buy_volume=columns.buy_volume,
        sell_volume=columns.sell_volume,
        buy_count=columns.buy_count,
        sell_count=columns.sell_count,
    )


_TRADED_COLUMNS = TradeColumns(8400010, 8400010, 8400010, 8400010, 10, 0, 1, 0)


def _ref_trade(second: int, price: str, side: int = BUYER, tid: str = "r") -> ReferenceTrade:
    ts = second * _NS + 100 * _MS
    return ReferenceTrade(
        _BTC, tid, ts, False, Decimal(price), Decimal("0.010"), side, "Buy", (ts, ts, 0)
    )


def _prepared(
    reference: Mapping[int, TradeColumns | None], causes: Causes | None = None
) -> PreparedDay:
    rows = [_row(1000, _TRADED_COLUMNS), _row(1001, TradeColumns())]
    empty = Intervals.of([])
    return prepare_day(0, rows, reference, causes or Causes(empty, empty))


def _judge(day: PreparedDay) -> str:
    catalog = fold_candles(day.books, 60)
    masked = fold_candles(day.masked, 60)
    (t,) = catalog
    return judge_reference(catalog[t], masked[t], day.differing, day)


def test_judge_reference_classes() -> None:
    agrees = {1000: _TRADED_COLUMNS}
    extra = TradeColumns(8400010, 8400020, 8400010, 8400020, 20, 0, 2, 0)
    gap = Intervals.of([(1000 * _NS, 1000 * _NS + 10)])
    none = Intervals.of([])

    assert _judge(_prepared(agrees)) == EXACT
    assert _judge(_prepared({1000: extra})) == REF_DIFFERENT
    assert _judge(_prepared({1000: extra}, Causes(gap, none))) == REF_RECORDER_GAP
    assert _judge(_prepared({1000: extra}, Causes(none, gap))) == REF_EXPLAINED
    assert _judge(_prepared({1000: None})) == REF_DIFFERENT  # off grid: never a pass
    counts_only = TradeColumns(8400010, 8400010, 8400010, 8400010, 10, 0, 2, 0)
    # Story 33.3: a bar carries the trade counts (`buy_n`), so a count difference is one too.
    assert _judge(_prepared({1000: counts_only})) == REF_DIFFERENT


def test_a_duplicated_traded_row_is_ref_different_not_a_doubled_reference() -> None:
    rows = [_row(1000, _TRADED_COLUMNS), _row(1000, _TRADED_COLUMNS), _row(1001, TradeColumns())]
    empty = Intervals.of([])

    day = prepare_day(0, rows, {1000: _TRADED_COLUMNS}, Causes(empty, empty))

    assert day.masked[1] == _row(1000, TradeColumns()).book()  # the duplicate carries no trades
    assert day.differing == (1000,)
    assert _judge(day) == REF_DIFFERENT


def test_an_untraded_bucket_agreeing_is_both_undefined() -> None:
    rows = [_row(1001, TradeColumns())]
    empty = Intervals.of([])
    day = prepare_day(0, rows, {}, Causes(empty, empty))

    assert _judge(day) == BOTH_UNDEFINED


def test_fold_reference_masks_unobserved_seconds_and_counts_them_by_reason() -> None:
    rows = {1000: _row(1000, _TRADED_COLUMNS)}
    by_second = {
        1000: [_ref_trade(1000, "84000.10")],
        1002: [_ref_trade(1002, "1.00", SELLER, "a"), _ref_trade(1002, "1.00", SELLER, "b")],
        1003: [_ref_trade(1003, "1.00")],
    }

    folded, unobserved = fold_reference(by_second, rows, {1002: "crossed"})

    assert folded == {1000: _TRADED_COLUMNS}
    assert unobserved == {"crossed": 2, "unexplained": 1}
    off_grid, _ = fold_reference({1000: [_ref_trade(1000, "84000.105")]}, rows, {})
    assert off_grid == {1000: None}


def test_causes_meet_a_second_by_any_of_its_nanoseconds() -> None:
    edge = Intervals.of([(1000 * _NS + _NS - 1, 1000 * _NS + _NS - 1)])
    none = Intervals.of([])

    assert Causes(edge, none).of(1000) == REF_RECORDER_GAP
    assert Causes(none, edge).of(1000) == REF_EXPLAINED
    assert Causes(edge, none).of(1001) == REF_DIFFERENT


def test_bucket_kinds() -> None:
    assert bucket_kind(None) == NO_DATA
    assert bucket_kind(RefCandle(0, None, None, None, None, Decimal(0), 1)) == UNTRADED
    assert bucket_kind(_REF) == TRADED


# --- the pager ------------------------------------------------------------------------------------


_PAGE_T = 86_400_000 * 20_000  # a UTC midnight, ms


def _item(t: int) -> dict[str, Any]:
    return {"t": t, "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 1.0, "partial": False}


class _StubServer(ThreadingHTTPServer):
    """A loopback server on an ephemeral port that logs the paths it was asked for."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Stub)
        self.paths: list[str] = []


class _Stub(BaseHTTPRequestHandler):
    """
    Two bars before any cursor (and a gap row between them). By `bar_seconds`: 5 answers 500, 6
    cuts its body short, 7 sends a malformed status line, 8 an empty page claiming more.
    """

    server: _StubServer

    def do_GET(self) -> None:
        self.server.paths.append(self.path)
        query = {k: int(v[0]) for k, v in parse_qs(urlsplit(self.path).query).items()}
        if self._broken(query["bar_seconds"]):
            return
        before_ms = query["before_ns"] // _MS
        items = [_item(before_ms - n * 60_000) for n in (2, 1)]
        gap = {"t": before_ms - 90_000, "o": None, "h": None, "l": None, "c": None, "v": None}
        body = {"items": [items[0], gap, items[1]], "has_more": True, "venue": "bybit"}
        self._send(200, json.dumps(body | {"market": "perp"}).encode())

    def _broken(self, bar_seconds: int) -> bool:
        self.close_connection = True
        if bar_seconds == 5:
            self._send(500, b"boom")
        elif bar_seconds == 6:
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b'{"items": [')
        elif bar_seconds == 7:
            self.wfile.write(b"HTTP/1.1 OK-ish\r\n\r\n")
        elif bar_seconds == 8:
            self._send(200, b'{"items": [], "has_more": true, "venue": "bybit", "market": "perp"}')
        return bar_seconds in (5, 6, 7, 8)

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def stub_server() -> Iterator[_StubServer]:
    server = _StubServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def _base(server: _StubServer) -> str:
    return f"http://127.0.0.1:{server.server_address[1]}"


def test_the_pager_walks_back_until_the_window_start(stub_server: _StubServer) -> None:
    served = ServedCandles(_base(stub_server))
    start_ns = _PAGE_T * _MS
    end_ns = start_ns + 240 * _NS  # four minutes: two pages of two bars

    found = served.day(_BTC, 60, start_ns, end_ns)

    assert sorted(found) == [_PAGE_T + m * 60_000 for m in range(4)]
    assert len(stub_server.paths) == 2
    assert "limit=4" in stub_server.paths[0]
    assert f"before_ns={end_ns}" in stub_server.paths[0]


def test_the_pager_refuses_a_non_200_status(stub_server: _StubServer) -> None:
    with pytest.raises(Unservable, match="HTTP 500"):
        ServedCandles(_base(stub_server)).page(_BTC, _PAGE_T * _MS, 10, 5)


@pytest.mark.parametrize("bar_seconds", [6, 7], ids=["incomplete_read", "bad_status_line"])
def test_the_pager_refuses_a_broken_http_response(
    stub_server: _StubServer, bar_seconds: int
) -> None:
    with pytest.raises(Unservable, match="unreachable"):
        ServedCandles(_base(stub_server)).page(_BTC, _PAGE_T * _MS, 10, bar_seconds)


def test_the_pager_refuses_an_empty_page_that_claims_more(stub_server: _StubServer) -> None:
    start_ns = _PAGE_T * _MS

    with pytest.raises(Unservable, match="empty page"):
        ServedCandles(_base(stub_server)).day(_BTC, 8, start_ns, start_ns + 240 * _NS)


def test_the_pager_refuses_an_unreachable_server() -> None:
    with pytest.raises(Unservable, match="unreachable"):
        ServedCandles(f"http://127.0.0.1:{_free_port()}").page(_BTC, _PAGE_T * _MS, 10, 60)


def test_the_pager_refuses_a_url_that_is_not_http(tmp_path: Path) -> None:
    # `urlopen` answers a `file:` URL with no HTTP status: a crash (`int(None)`), never a refusal.
    with pytest.raises(Unservable, match="not an HTTP data_api"):
        ServedCandles(f"file://{tmp_path}").page(_BTC, _PAGE_T * _MS, 10, 60)


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (b"not json", "not JSON"),
        (b'{"items": [], "has_more": 1}', "has_more"),
        (b'{"items": [{"t": "x", "o": 1}], "has_more": false}', "not an integer"),
        (b'{"items": [{"t": 1, "o": true}], "has_more": false}', "not a number"),
        (b'{"items": [{"t": 1, "o": 1, "partial": 1}], "has_more": false}', "not a boolean"),
        (b'{"items": [{"t": 1, "o": 1, "q": 1}], "has_more": false}', "not a candle object"),
        (b'{"items": [{"t": 2, "o": 1}, {"t": 1, "o": 1}], "has_more": false}', "ascending"),
        (b'{"items": [{"t": 9, "o": 1}], "has_more": false}', "ascending"),
    ],
)
def test_parse_page_refuses_a_malformed_body(body: bytes, match: str) -> None:
    with pytest.raises(Unservable, match=match):
        parse_page(body, 9, "page")


def test_parse_page_ignores_gap_rows_and_keeps_an_absent_partial_as_none() -> None:
    body = b'{"items": [{"t": 1, "o": null}, {"t": 2, "o": 1, "h": 2, "l": 1, "c": 2, "v": 3}], "has_more": false}'

    page = parse_page(body, 9, "page")

    assert page.bars == (ServedBar(2, 1.0, 2.0, 1.0, 2.0, 3.0, None),)
    assert page.has_more is False


def test_the_oracle_reads_an_instruments_liquidations_once_per_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    A closed week (judged ten days later): the day fold and the seven week-day folds all slice one
    read of the week [Monday 09-28, Monday 10-05), never eight reads of the archive.
    """
    scenario = _Scenario(liquidations=(_liquidation(_DAY, 5, "a", LiquidatedSide.LONG, "0.041"),))
    calls: list[tuple[str, int, int]] = []
    real = LiquidationCatalog.liquidations

    def counted(self: LiquidationCatalog, iid: str, start_ns: int, end_ns: int) -> list:
        calls.append((iid, start_ns, end_ns))
        return real(self, iid, start_ns, end_ns)

    monkeypatch.setattr(LiquidationCatalog, "liquidations", counted)
    _run(tmp_path, monkeypatch, capsys, scenario, now_ns=_d0(_DAY) + 10 * _DAY_S * _NS)
    monday = _d0(_DAY) - _DAY_S * _NS
    assert calls == [(_BTC, monday, monday + 7 * _DAY_S * _NS)]
