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
The liquidations self-check (Story 33.1): the pure match (same size, forced side, within 2 s,
each trade once), the unrecoverable seconds, and the tool end to end over a real catalog written
by `ParquetDataCatalog.write_data` (real `Liquidation`s and `TradeTick`s) and a coverage record.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from kernel.liquidation import LiquidatedSide
from kernel.liquidation import Liquidation
from observability import error_ledger

from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog
from verification import liquidations as tool
from verification.application import sites
from verification.application.conservation import day_start_ns
from verification.domain.liquidation_check import BUYER
from verification.domain.liquidation_check import SELLER
from verification.domain.liquidation_check import Fill
from verification.domain.liquidation_check import StoredLiquidation
from verification.domain.liquidation_check import covered_seconds
from verification.domain.liquidation_check import match_liquidations


_S = 1_000_000_000
_RAW_0_010 = 10 * 10**13  # 0.010 at 16 decimals
_DAY = date(2026, 10, 4)
_START = day_start_ns(_DAY)
_BTC = "BTCUSDT-LINEAR.BYBIT"
_CLOCK = _START + 3 * 86_400 * _S  # long after the day closed


def _liq(key: str, side: str, ts: int, size_units: int = 10) -> StoredLiquidation:
    return StoredLiquidation(key, side, size_units, 3, ts)


# -- the pure match ---------------------------------------------------------------------------


def test_a_liquidated_long_matches_a_same_size_seller_trade_within_two_seconds() -> None:
    matched, unmatched = match_liquidations(
        [_liq("a", "long", 10 * _S)], [Fill(_RAW_0_010, SELLER, 11 * _S)]
    )
    assert (matched, unmatched) == (1, ())


def test_the_forced_side_size_and_window_all_decide() -> None:
    row = _liq("a", "long", 10 * _S)
    for fill in (
        Fill(_RAW_0_010, BUYER, 10 * _S),  # a liquidated long is a forced sell, not a buy
        Fill(_RAW_0_010 + 1, SELLER, 10 * _S),
        Fill(_RAW_0_010, SELLER, 12 * _S + 1),  # just past 2 s
    ):
        assert match_liquidations([row], [fill]) == (0, ("a",))
    assert (
        match_liquidations([_liq("b", "short", 10 * _S)], [Fill(_RAW_0_010, BUYER, 8 * _S)])[0] == 1
    )


def test_each_trade_matches_one_liquidation_the_earliest_first() -> None:
    rows = [_liq("a", "long", 10 * _S), _liq("b", "long", 10 * _S + 1)]
    fills = [Fill(_RAW_0_010, SELLER, 10 * _S + 2)]
    assert match_liquidations(rows, fills) == (1, ("b",))


def test_the_earliest_eligible_fill_leaves_a_later_liquidation_its_match() -> None:
    # The review's case: two same-size longs 1.5 s apart, fills at +1.0 s and +1.9 s of the first.
    rows = [_liq("a", "long", 10 * _S), _liq("b", "long", 10 * _S + 1_500_000_000)]
    fills = [
        Fill(_RAW_0_010, SELLER, 11 * _S),
        Fill(_RAW_0_010, SELLER, 11 * _S + 900_000_000),
    ]
    assert match_liquidations(rows, fills) == (2, ())


def test_greedy_nearest_would_lose_a_match_the_earliest_rule_keeps() -> None:
    # `a` at 10 s: the nearest fill is 10.5 s, but `b` at 12.4 s can only reach 10.4..14.4 s.
    rows = [_liq("a", "long", 10 * _S), _liq("b", "long", 12 * _S + 400_000_000)]
    fills = [
        Fill(_RAW_0_010, SELLER, 8 * _S + 500_000_000),
        Fill(_RAW_0_010, SELLER, 10 * _S + 500_000_000),
    ]
    assert match_liquidations(rows, fills) == (2, ())


def test_an_unknown_stored_side_is_refused() -> None:
    with pytest.raises(ValueError, match="not long/short"):
        match_liquidations([_liq("a", "sideways", 0)], [])


def test_unrecoverable_seconds_count_overlaps_once_and_clip_to_the_day() -> None:
    windows = [(5 * _S, 7 * _S - 1), (6 * _S, 8 * _S), (-10 * _S, 1)]
    assert covered_seconds(windows, 0, 100 * _S) == 1 + 4  # second 0, then seconds 5..8


# -- the tool end to end ----------------------------------------------------------------------


def _liquidation(key: str, side: LiquidatedSide, size: str, ts: int) -> Liquidation:
    iid = InstrumentId.from_str(_BTC)
    return Liquidation.from_wire_text(iid, side, size, "60000.10", (2, 3), key, ts, ts + 1)


def _trade(size: str, aggressor: AggressorSide, ts: int, n: int) -> TradeTick:
    iid = InstrumentId.from_str(_BTC)
    return TradeTick(
        iid, Price.from_str("60000.00"), Quantity.from_str(size), aggressor, TradeId(str(n)), ts, ts
    )


def _catalog(tmp_path: Path) -> Path:
    catalog = tmp_path / "catalog"
    writer = ParquetDataCatalog(str(catalog))
    writer.write_data(
        [
            _liquidation("k3", LiquidatedSide.LONG, "0.020", _START - _S),  # the day before
            _liquidation("k1", LiquidatedSide.LONG, "0.010", _START + 100 * _S),
            _liquidation("k2", LiquidatedSide.SHORT, "0.500", _START + 200 * _S),
        ]
    )
    writer.write_data(
        [
            _trade("0.010", AggressorSide.SELLER, _START + 101 * _S, 1),
            _trade("0.500", AggressorSide.SELLER, _START + 200 * _S, 2),  # the wrong side for k2
            _trade("0.300", AggressorSide.BUYER, _START + 200 * _S, 3),
        ]
    )
    coverage = tmp_path / "coverage" / "bybit.jsonl"
    coverage.parent.mkdir()
    window = {"kind": "liquidations_unrecoverable", "instrument_id": _BTC, "reason": "feed_down"}
    coverage.write_text(
        json.dumps({**window, "from_ns": _START + 10 * _S, "to_ns": _START + 19 * _S}) + "\n"
    )
    return catalog


def test_the_report_states_the_matched_share_and_the_unmatched_ids(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path)
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--json", "--catalog", str(catalog)]
    assert tool.main(argv, clock=lambda: _CLOCK) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["applicable"], report["total"], report["matched"], report["share"]) == (
        True,
        2,
        1,
        0.5,
    )
    (btc,) = report["instruments"]
    assert btc["unmatched_ids"] == ["k2"]
    assert btc["unrecoverable_seconds"] == 10
    assert "passed" not in report  # a self-check, never a verdict


def test_a_venue_without_the_feed_is_not_applicable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path)
    argv = [
        "--venue",
        "HYPERLIQUID",
        "--day",
        _DAY.isoformat(),
        "--json",
        "--catalog",
        str(catalog),
    ]
    assert tool.main(argv, clock=lambda: _CLOCK) == 0
    report = json.loads(capsys.readouterr().out)
    assert (report["applicable"], report["instruments"]) == (False, [])


def test_an_open_day_is_refused_and_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--catalog", str(tmp_path)]
    with pytest.raises(SystemExit, match="day not closed"):
        tool.main(argv, clock=lambda: _START + _S)
    assert error_ledger.counts() == {sites.LIQUIDATIONS_REFUSED: 1}


def test_the_text_report_names_the_share(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path)
    tool.main(
        ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--catalog", str(catalog)], lambda: _CLOCK
    )
    text = capsys.readouterr().out
    assert "1/2 matched" in text
    assert "unmatched: k2" in text


def test_a_quiet_instrument_and_an_old_window_are_not_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    catalog = _catalog(tmp_path)
    ParquetDataCatalog(str(catalog)).write_data(
        [
            Liquidation.from_wire_text(
                InstrumentId.from_str("ETHUSDT-LINEAR.BYBIT"),
                LiquidatedSide.LONG,
                "1.00",
                "2000.00",
                (2, 2),
                "e1",
                _START - 2 * 86_400 * _S,
                _START - 2 * 86_400 * _S,
            )
        ]
    )
    old = {"kind": "liquidations_unrecoverable", "instrument_id": "SOLUSDT-LINEAR.BYBIT"}
    old |= {"reason": "feed_down", "from_ns": 1, "to_ns": _START - 1}
    with (tmp_path / "coverage" / "bybit.jsonl").open("a") as handle:
        handle.write(json.dumps(old) + "\n")
    argv = ["--venue", "BYBIT", "--day", _DAY.isoformat(), "--json", "--catalog", str(catalog)]
    tool.main(argv, clock=lambda: _CLOCK)
    report = json.loads(capsys.readouterr().out)
    assert [entry["instrument_id"] for entry in report["instruments"]] == [_BTC]
