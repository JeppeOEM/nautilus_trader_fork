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
compare_klines: our candle store (built by `build_candles.rebuild_instrument` from real snapshots
in a tmp catalog) against injected venue klines, plus the venue parsers on recorded real responses
(`fixtures/`, captured 2026-09-21 for 2026-09-20). No network.
"""

import http.client
import json
import sqlite3
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from ml_signals import candle_store
from observability import error_ledger

from collector_core.build_candles import _parse_date_ns
from collector_core.build_candles import rebuild_instrument
from collector_core.compare_klines import Kline
from collector_core.compare_klines import KlineError
from collector_core.compare_klines import fetch_klines
from collector_core.compare_klines import float_units
from collector_core.compare_klines import parse_bybit_klines
from collector_core.compare_klines import parse_dydx_candles
from collector_core.compare_klines import parse_hyperliquid_candles
from collector_core.compare_klines import reconcile_instrument
from collector_core.compare_klines import run
from collector_core.compare_klines import seed_with_previous_close
from collector_core.compare_klines import units
from collector_core.fold import fold_trades
from collector_core.second_snapshot import DydxSecondSnapshot
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_FIXTURES = Path(__file__).parent / "fixtures"
_IID = "BTC-USD-PERP.HYPERLIQUID"  # no venue-specific kline definition
_BYBIT = "BTCUSDT-LINEAR.BYBIT"
_DAY = "2026-09-10"
_D0_MS = _parse_date_ns(_DAY) // 1_000_000
_S = 1_000_000_000


def _instrument(iid: str, raw: str, price_p: int, size_p: int) -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(iid),
        raw_symbol=Symbol(raw),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_p,
        price_increment=Price.from_str(str(Decimal(1).scaleb(-price_p))),
        size_precision=size_p,
        size_increment=Quantity.from_str(str(Decimal(1).scaleb(-size_p))),
        ts_event=0,
        ts_init=0,
    )


def _trade(n: int, second: int, price: str, size: str) -> TradeTick:
    ts = _D0_MS * 1_000_000 + second * _S + 100_000_000  # `second` may be negative: the day before
    return TradeTick(
        InstrumentId.from_str(_IID),
        Price.from_str(price),
        Quantity.from_str(size),
        AggressorSide.BUYER,
        TradeId(str(n)),
        ts,
        ts,
    )


# Seconds of the day -> that second's trades. Minute 0 trades twice, minute 5 once.
_TRADES = {
    10: [_trade(1, 10, "100.1", "0.001"), _trade(2, 10, "100.4", "0.002")],
    20: [_trade(3, 20, "99.9", "0.004")],
    305: [_trade(4, 305, "100.5", "0.010")],
}
_THEIRS = [
    Kline(_D0_MS, 1001, 1004, 999, 999, 7),
    Kline(_D0_MS + 300_000, 1005, 1005, 1005, 1005, 10),
]


def _store(tmp_path: Path, iid: str = _IID, trades: dict | None = None, first: int = 0) -> str:
    """Snapshots for the day in a real catalog, folded into a candle store by `build_candles`."""
    trades = _TRADES if trades is None else trades
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    catalog.write_data([_instrument(iid, "BTCUSDT", 1, 3)])
    snaps = []
    for second in range(first, 400):
        ts = _D0_MS * 1_000_000 + second * _S + _S // 2
        snaps.append(
            DydxSecondSnapshot(
                instrument_id=InstrumentId.from_str(iid),
                bid_prices=[99.0],
                bid_sizes=[1.0],
                ask_prices=[101.0],
                ask_sizes=[1.0],
                **fold_trades(trades.get(second, [])).snapshot_values()._asdict(),
                ts_event=ts,
                ts_init=ts,
            )
        )
    catalog.write_data(snaps)
    db = str(tmp_path / "candles.db")
    day_ns = _D0_MS * 1_000_000
    rebuild_instrument(
        db, str(tmp_path / "catalog"), iid, day_ns + first * _S, day_ns + 86_400 * _S - 1
    )
    return db


def _reconcile(tmp_path: Path, theirs: list[Kline]) -> tuple[Any, str]:
    db = _store(tmp_path)
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    result = reconcile_instrument(db, catalog, _IID, _D0_MS, lambda _inst, _day: theirs)
    return result, db


def _status(db: str) -> str | None:
    with candle_store.connect_ro(db) as ro:
        assert ro is not None
        return candle_store.verified_status(ro, _IID, _DAY)


def test_equal_klines_pass_and_mark_the_day_verified(tmp_path: Path) -> None:
    error_ledger.reset()
    result, db = _reconcile(tmp_path, _THEIRS)
    assert (result.status, result.minutes, result.mismatches) == ("pass", 2, [])
    assert _status(db) == "pass"
    assert error_ledger.counts() == {}


def test_one_altered_kline_is_one_ledger_entry_with_the_exact_message(tmp_path: Path) -> None:
    error_ledger.reset()
    altered = [_THEIRS[0], Kline(_D0_MS + 300_000, 1005, 1005, 1005, 1005, 11)]
    result, db = _reconcile(tmp_path, altered)
    assert result.status == "fail"
    assert error_ledger.counts() == {"reconcile.kline_mismatch": 1}
    assert error_ledger.last_details()["reconcile.kline_mismatch"] == (
        f"{_IID} 2026-09-10T00:05Z vol 0.010/0.011 "
        "ohlc 100.5,100.5,100.5,100.5/100.5,100.5,100.5,100.5"
    )
    assert _status(db) == "fail"


@pytest.mark.parametrize(
    ("theirs", "message_tail"),
    [
        ([_THEIRS[0]], "00:05Z vol 0.010/- ohlc 100.5,100.5,100.5,100.5/-"),
        (
            [*_THEIRS, Kline(_D0_MS + 600_000, 1, 1, 1, 1, 1)],
            "00:10Z vol -/0.001 ohlc -/0.1,0.1,0.1,0.1",
        ),
    ],
)
def test_a_bar_missing_on_either_side_is_a_mismatch(
    tmp_path: Path, theirs: list[Kline], message_tail: str
) -> None:
    error_ledger.reset()
    result, _ = _reconcile(tmp_path, theirs)
    assert result.status == "fail"
    assert result.mismatches == [f"{_IID} 2026-09-10T{message_tail}"]


def test_a_zero_volume_venue_kline_is_no_trade_on_either_side(tmp_path: Path) -> None:
    quiet = Kline(_D0_MS + 120_000, 999, 999, 999, 999, 0)
    result, _ = _reconcile(tmp_path, [*_THEIRS, quiet])
    assert result.status == "pass"


def test_fetch_error_leaves_the_day_unverified(tmp_path: Path) -> None:
    error_ledger.reset()
    db = _store(tmp_path)
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))

    def failing(_inst: Instrument, _day: int) -> list[Kline]:
        raise KlineError("bybit retCode 10001: params error")

    result = reconcile_instrument(db, catalog, _IID, _D0_MS, failing)
    assert result.status == "error"
    assert _status(db) is None
    assert error_ledger.counts() == {"reconcile.error": 1}


def test_missing_instrument_definition_is_an_error(tmp_path: Path) -> None:
    error_ledger.reset()
    db = _store(tmp_path)
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    result = reconcile_instrument(db, catalog, "ETHUSDT-LINEAR.BYBIT", _D0_MS, lambda i, d: [])
    assert result.status == "error"
    assert error_ledger.counts() == {"reconcile.error": 1}


def test_run_exit_codes_pass_findings_and_run_failure(tmp_path: Path) -> None:
    db = _store(tmp_path)
    catalog = str(tmp_path / "catalog")
    venue = "HYPERLIQUID"
    assert run(db, catalog, venue, _D0_MS, [_IID], lambda i, d: _THEIRS) == 0
    assert run(db, catalog, venue, _D0_MS, [_IID], lambda i, d: _THEIRS[:1]) == 2
    # A per-instrument error (here: no definition) is a finding too, not a halt.
    assert (
        run(db, catalog, venue, _D0_MS, [_IID, "X-USD-PERP.HYPERLIQUID"], lambda i, d: _THEIRS) == 2
    )
    # Run-level: an unusable candle store means nothing could be compared.
    assert run(str(tmp_path / "nope.db"), catalog, venue, _D0_MS, [_IID], lambda i, d: []) == 1


def test_seed_with_previous_close_is_bybits_kline_definition() -> None:
    ours = [Kline(0, 1001, 1004, 999, 999, 7), Kline(300_000, 1005, 1005, 1005, 1005, 10)]
    assert seed_with_previous_close(ours, None) == [
        Kline(0, 1001, 1004, 999, 999, 7),  # no previous close known: left as it is
        Kline(300_000, 999, 1005, 999, 1005, 10),
    ]
    assert seed_with_previous_close(ours, 1010)[0] == Kline(0, 1010, 1010, 999, 999, 7)


def test_bybit_is_compared_in_its_seeded_definition_across_the_day_boundary(
    tmp_path: Path,
) -> None:
    trades = {**_TRADES, -30: [_trade(9, -30, "100.0", "0.001")]}  # the day before: close 100.0
    db = _store(tmp_path, _BYBIT, trades, first=-60)
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    theirs = [
        Kline(_D0_MS, 1000, 1004, 999, 999, 7),  # open = 23:59's close, as Bybit serves it
        Kline(_D0_MS + 300_000, 999, 1005, 999, 1005, 10),
    ]
    result = reconcile_instrument(db, catalog, _BYBIT, _D0_MS, lambda _i, _d: theirs)
    assert (result.status, result.mismatches) == ("pass", [])
    unseeded = reconcile_instrument(db, catalog, _BYBIT, _D0_MS, lambda _i, _d: _THEIRS)
    assert len(unseeded.mismatches) == 2


def test_units_are_exact_and_refuse_a_non_integral_value() -> None:
    assert units("81292.0", 1, "open") == 812920
    assert units("0.0004", 4, "volume") == 4
    with pytest.raises(KlineError):
        units("100.05", 1, "open")
    with pytest.raises(KlineError):
        units("abc", 1, "open")


def test_float_units_round_a_representation_error_but_refuse_a_real_residual() -> None:
    assert float_units(0.1 + 0.2, 8, "volume") == 30_000_000
    assert float_units(81204.0, 0, "close") == 81204
    with pytest.raises(KlineError):
        float_units(0.3004, 3, "volume")


# -- parsers and fetchers on recorded real responses -----------------------------------------------


def _fixture(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text())


class _Recorder:
    """Fake transport: returns recorded payloads in order and keeps the requests it saw."""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.requests: list[urllib.request.Request] = []

    def __call__(self, request: urllib.request.Request) -> Any:
        self.requests.append(request)
        return self.payloads.pop(0)


def test_dydx_parser_and_fetch_drop_zero_volume_minutes() -> None:
    payload = _fixture("dydx_candles_btc_usd_20260920.json")
    assert len(parse_dydx_candles(payload, 0, 4)) == 5
    inst = _instrument("BTC-USD-PERP.DYDX", "BTC-USD", 0, 4)
    http = _Recorder(payload)
    day = _parse_date_ns("2026-09-20") // 1_000_000
    assert fetch_klines("DYDX", inst, day, "mainnet", http) == [
        Kline(day + 240_000, 81222, 81222, 81204, 81204, 4)
    ]
    url = http.requests[0].full_url
    assert "/v4/candles/perpetualMarkets/BTC-USD?resolution=1MIN" in url
    assert "fromISO=2026-09-20T00:00:00.000Z&toISO=2026-09-21T00:00:00.000Z&limit=1000" in url
    assert http.requests[0].get_header("User-agent")


def test_bybit_parser_and_fetch() -> None:
    payload = _fixture("bybit_kline_btcusdt_linear_20260920.json")
    inst = _instrument("BTCUSDT-LINEAR.BYBIT", "BTCUSDT", 1, 3)
    day = _parse_date_ns("2026-09-20") // 1_000_000
    http = _Recorder(payload)
    klines = fetch_klines("BYBIT", inst, day, "mainnet", http)
    assert klines[0] == Kline(day, 812349, 812352, 811905, 811994, 17881)
    assert [k.t_ms - day for k in klines] == [0, 60_000, 120_000]
    assert f"category=linear&symbol=BTCUSDT&interval=1&start={day}&end={day + 86_399_999}" in (
        http.requests[0].full_url
    )


def test_bybit_error_code_is_an_error() -> None:
    with pytest.raises(KlineError):
        parse_bybit_klines({"retCode": 10001, "retMsg": "params error"}, 1, 3)


def test_bybit_fetch_pages_backwards_until_the_day_start() -> None:
    inst = _instrument("BTCUSDT-SPOT.BYBIT", "BTCUSDT", 1, 3)
    day = _parse_date_ns("2026-09-20") // 1_000_000

    def page(starts: range) -> dict:
        rows = [[str(day + m * 60_000), "1.0", "1.0", "1.0", "1.0", "0.001", "1"] for m in starts]
        return {"retCode": 0, "result": {"list": rows[::-1]}}

    http = _Recorder(page(range(440, 1440)), page(range(440)))
    klines = fetch_klines("BYBIT", inst, day, "mainnet", http)
    assert len(klines) == 1440
    assert "category=spot" in http.requests[0].full_url
    assert f"end={day + 440 * 60_000 - 1}" in http.requests[1].full_url


def test_hyperliquid_parser_and_fetch() -> None:
    payload = _fixture("hyperliquid_candles_btc_20260920.json")
    inst = _instrument("BTC-USD-PERP.HYPERLIQUID", "BTC", 0, 5)
    day = _parse_date_ns("2026-09-20") // 1_000_000
    http = _Recorder(payload, [])
    klines = fetch_klines("HYPERLIQUID", inst, day, "mainnet", http)
    assert klines[0] == Kline(day, 81292, 81297, 81255, 81263, 772335)
    assert len(klines) == len(parse_hyperliquid_candles(payload, 0, 5)) == 3
    body = json.loads(http.requests[0].data)  # type: ignore[arg-type]
    assert body == {
        "type": "candleSnapshot",
        "req": {"coin": "BTC", "interval": "1m", "startTime": day, "endTime": day + 86_399_999},
    }
    assert json.loads(http.requests[1].data)["req"]["startTime"] == day + 180_000  # type: ignore[arg-type]


def test_catalog_kline_source_never_writes_verified_days(tmp_path: Path) -> None:
    db = _store(tmp_path)
    catalog = str(tmp_path / "catalog")
    assert run(db, catalog, "HYPERLIQUID", _D0_MS, [_IID], lambda i, d: _THEIRS, False) == 0
    assert _status(db) is None  # D-52 f64 bars must never release trades to the prune


def test_venue_without_history_for_the_day_is_an_error_not_a_fail(tmp_path: Path) -> None:
    error_ledger.reset()
    result, db = _reconcile(tmp_path, [])
    assert result.status == "error"
    assert _status(db) is None
    assert "no history" in error_ledger.last_details()["reconcile.error"]


def test_hyperliquid_snapshot_starting_after_our_first_minute_is_outside_retention(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    result, _ = _reconcile(tmp_path, _THEIRS[1:])  # venue history starts at 00:05, ours at 00:00
    assert result.status == "error"
    assert "retention" in error_ledger.last_details()["reconcile.error"]


@pytest.mark.parametrize(
    "exc",
    [
        http.client.IncompleteRead(b""),
        TypeError("null in payload"),
        OSError("reset"),
        sqlite3.OperationalError("database is locked"),
    ],
)
def test_transport_and_payload_failures_are_per_instrument_errors(
    tmp_path: Path, exc: Exception
) -> None:
    db = _store(tmp_path)
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))

    def failing(_inst: Instrument, _day: int) -> list[Kline]:
        raise exc

    assert reconcile_instrument(db, catalog, _IID, _D0_MS, failing).status == "error"


def test_a_seed_consequence_is_named_in_the_second_mismatch(tmp_path: Path) -> None:
    db = _store(tmp_path, _BYBIT)
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    wrong_close = [
        Kline(_D0_MS, 1001, 1004, 999, 998, 7),  # the venue closed minute 0 one unit lower
        Kline(_D0_MS + 300_000, 998, 1005, 998, 1005, 10),  # so it seeds minute 5 with it
    ]
    result = reconcile_instrument(db, catalog, _BYBIT, _D0_MS, lambda _i, _d: wrong_close)
    assert len(result.mismatches) == 2
    assert "seed" not in result.mismatches[0]
    assert result.mismatches[1].endswith("(seed from a mismatched minute)")
