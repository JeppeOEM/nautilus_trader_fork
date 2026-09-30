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
`bots.signal_replay` (Story 31.9): a real `BacktestNode` replay of `DummyStrategy` over a
`write_data` catalog of snapshot rows and trades, aligned on a fake live log's `start`.

The load-bearing claims: the replay's clock at `on_start` is the live `start` exactly, so every
book cycle lands on `start + k s`; each cycle's levels are the latest stored row known by then
(`ts_init` before the cycle), exactly as stored; and the trend bars aggregate from the stored trades.
"""

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from kernel.clocks import NS_PER_S
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.tests.snapshot_factory import make_snapshot
from observability import error_ledger

from bots.signal_replay import REFUSED_SITE
from bots.signal_replay import ReplayRefused
from bots.signal_replay import _check_replay_log
from bots.signal_replay import aligned_start
from bots.signal_replay import catalog_instrument
from bots.signal_replay import latest_segment
from bots.signal_replay import main
from nautilus_trader.model.currencies import BTC
from nautilus_trader.model.currencies import USDT
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_IID = "BTCUSDT-LINEAR.BYBIT"
_BOT = "replay-btc"
_T0 = 1_790_000_040 * NS_PER_S  # a whole minute (test_the_window_start_is_a_whole_minute)
_LIVE_START = _T0 + 250_123_000  # whole microseconds, as the live strategy truncates it
_SECONDS = 150
_LEVELS = 12  # more than the strategy's 10, so the logged top-N is a real cut


def _instrument() -> CryptoPerpetual:
    return CryptoPerpetual(
        instrument_id=InstrumentId.from_str(_IID),
        raw_symbol=Symbol("BTCUSDT"),
        base_currency=BTC,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=1,
        price_increment=Price.from_str("0.1"),
        size_precision=3,
        size_increment=Quantity.from_str("0.001"),
        max_quantity=Quantity.from_str("1000.000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(10.00, USDT),
        max_price=Price.from_str("809484.0"),
        min_price=Price.from_str("261.1"),
        margin_init=Decimal("0.0500"),
        margin_maint=Decimal("0.0250"),
        maker_fee=Decimal("0.000200"),
        taker_fee=Decimal("0.000180"),
        ts_event=_T0 - 60 * NS_PER_S,
        ts_init=_T0 - 60 * NS_PER_S,
    )


def _row(second: int) -> DydxSecondSnapshot:
    """Second `second`'s row: a book moving by 0.1 each second, sampled 1.3 s after the second."""
    best_bid = 830_625 + second  # units of 0.1
    bids = [f"{(best_bid - i) / 10:.1f}" for i in range(_LEVELS)]
    asks = [f"{(best_bid + 1 + i) / 10:.1f}" for i in range(_LEVELS)]
    sizes = [f"{(second % 7 + i + 1) / 1000:.3f}" for i in range(_LEVELS)]
    start = _T0 + second * NS_PER_S
    return make_snapshot(
        instrument_id=_IID,
        bid_prices=bids,
        bid_sizes=sizes,
        ask_prices=asks,
        ask_sizes=list(reversed(sizes)),
        ts_event=start + NS_PER_S // 2,
        ts_init=start + 1_300_000_000,
        price_precision=1,
        size_precision=3,
    )


def _trades() -> list[TradeTick]:
    """One trade every 5 s over the window, its price stepping by 0.1."""
    return [
        TradeTick(
            instrument_id=InstrumentId.from_str(_IID),
            price=Price.from_raw((830_600 + second) * 10**15, 1),
            size=Quantity.from_str("0.010"),
            aggressor_side=AggressorSide.BUYER,
            trade_id=TradeId(str(second)),
            ts_event=_T0 + second * NS_PER_S + 100_000_000,
            ts_init=_T0 + second * NS_PER_S + 200_000_000,
        )
        for second in range(0, _SECONDS + 5, 5)
    ]


def _catalog(path: Path) -> list[DydxSecondSnapshot]:
    rows = [_row(second) for second in range(-2, _SECONDS + 3)]
    catalog = ParquetDataCatalog(str(path))
    catalog.write_data([_instrument()])
    catalog.write_data(rows)
    catalog.write_data(_trades())
    return rows


def _start_record(ts_ns: int) -> dict[str, Any]:
    return {
        "kind": "start",
        "bot_id": _BOT,
        "instrument_id": _IID,
        "ts_ns": ts_ns,
        "trade_size": "0.001",
        "trend_buy_threshold": 0.6,
        "trend_sell_threshold": 0.4,
        "ofi_confirm_threshold": 0.0,
    }


def _live_log(directory: Path, end_ns: int) -> None:
    """Write an older run, then the latest one: its `start` and a last record at `end_ns`."""
    directory.mkdir()
    records = [
        _start_record(_LIVE_START - 100 * NS_PER_S),
        {"kind": "book", "ts_ns": _LIVE_START - 99 * NS_PER_S},
        _start_record(_LIVE_START),
        {"kind": "book", "ts_ns": end_ns},
    ]
    text = "".join(json.dumps(record) + "\n" for record in records)
    (directory / f"{_BOT}.jsonl").write_text(text)


def _config(path: Path, trend_buy_threshold: str = "0.6") -> Path:
    path.write_text(
        '[venues.BYBIT]\nenvironment = "mainnet"\nstarting_balances = ["100_000 USDT"]\n'
        f'account_type = "MARGIN"\n\n[[bots]]\nbot_id = "{_BOT}"\ninstrument_id = "{_IID}"\n'
        f'trade_size = "0.001"\ntrend_buy_threshold = {trend_buy_threshold}\n'
        "trend_sell_threshold = 0.4\nofi_confirm_threshold = 0.0\n"
    )
    return path


def _replay(tmp_path: Path, end_ns: int) -> tuple[list[dict[str, Any]], list[DydxSecondSnapshot]]:
    rows = _catalog(tmp_path / "catalog")
    _live_log(tmp_path / "live", end_ns)
    argv = [
        *("--config", str(_config(tmp_path / "config.toml"))),
        *("--catalog", str(tmp_path / "catalog")),
        *("--live-log", str(tmp_path / "live")),
        *("--out", str(tmp_path / "replay")),
    ]
    assert main(argv) == 0
    lines = (tmp_path / "replay" / f"{_BOT}.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines], rows


def _levels(row: DydxSecondSnapshot, side: str) -> list[list[float]]:
    """Return the row's top 10 levels as the strategy reads them off the book (`as_double`)."""
    exact = row.exact
    prices = exact.bid_prices if side == "bid" else exact.ask_prices
    sizes = exact.bid_sizes if side == "bid" else exact.ask_sizes
    return [[p.as_double(), q.as_double()] for p, q in zip(prices, sizes, strict=True)][:10]


def test_the_window_start_is_a_whole_minute() -> None:
    assert _T0 % (60 * NS_PER_S) == 0


def test_the_replay_starts_on_the_live_start_and_cycles_on_start_plus_k_seconds(
    tmp_path: Path,
) -> None:
    end_ns = _LIVE_START + _SECONDS * NS_PER_S
    records, _ = _replay(tmp_path, end_ns)
    cycles = [r["ts_ns"] for r in records if r["kind"] in ("book", "book_skipped")]

    assert [r["ts_ns"] for r in records if r["kind"] == "start"] == [_LIVE_START]
    assert cycles == [_LIVE_START + k * NS_PER_S for k in range(1, _SECONDS + 1)]


def test_each_book_cycle_holds_the_latest_stored_row_known_by_then(tmp_path: Path) -> None:
    records, rows = _replay(tmp_path, _LIVE_START + _SECONDS * NS_PER_S)
    books = [r for r in records if r["kind"] == "book"]

    assert len(books) == _SECONDS
    for record in books:
        known = max((row for row in rows if row.ts_init < record["ts_ns"]), key=lambda r: r.ts_init)
        assert record["book_ts_ns"] == known.ts_init
        assert (record["bids"], record["asks"]) == (_levels(known, "bid"), _levels(known, "ask"))


def test_the_trend_bars_aggregate_from_the_stored_trades(tmp_path: Path) -> None:
    records, _ = _replay(tmp_path, _LIVE_START + _SECONDS * NS_PER_S)
    bars = [r["bar"] for r in records if r["kind"] == "bar"]

    # Minutes 1 and 2 close inside the window; each closes at its last trade (second 55, 115).
    assert bars == [
        {"ts_event": _T0 + 60 * NS_PER_S, "close": 83065.5},
        {"ts_event": _T0 + 120 * NS_PER_S, "close": 83071.5},
    ]


def test_a_start_override_is_snapped_onto_the_live_grid() -> None:
    assert aligned_start(_LIVE_START, _LIVE_START + 10 * NS_PER_S) == _LIVE_START + 10 * NS_PER_S
    assert aligned_start(_LIVE_START, _LIVE_START + 10 * NS_PER_S + 1) == (
        _LIVE_START + 11 * NS_PER_S
    )
    assert aligned_start(_LIVE_START, _LIVE_START - NS_PER_S // 2) == _LIVE_START


def test_a_start_override_before_the_live_run_is_the_live_start() -> None:
    # k >= 0: the live run has no cycle before its start for the replay to pair with.
    assert aligned_start(_LIVE_START, _LIVE_START - 10 * NS_PER_S) == _LIVE_START


def test_the_latest_segment_is_the_last_start_to_the_last_record(tmp_path: Path) -> None:
    _live_log(tmp_path / "live", _LIVE_START + 42 * NS_PER_S)

    segment = latest_segment(tmp_path / "live" / f"{_BOT}.jsonl")

    assert (segment.start_ns, segment.end_ns) == (_LIVE_START, _LIVE_START + 42 * NS_PER_S)


def test_a_missing_live_log_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ReplayRefused, match="no signal log"):
        latest_segment(tmp_path / "absent.jsonl")


def test_a_live_run_of_other_thresholds_is_refused(tmp_path: Path, capsys: Any) -> None:
    _catalog(tmp_path / "catalog")
    _live_log(tmp_path / "live", _LIVE_START + 10 * NS_PER_S)
    argv = [
        *("--config", str(_config(tmp_path / "config.toml", trend_buy_threshold="0.7"))),
        *("--catalog", str(tmp_path / "catalog")),
        *("--live-log", str(tmp_path / "live")),
        *("--out", str(tmp_path / "replay")),
    ]

    before = error_ledger.counts().get(REFUSED_SITE, 0)
    assert main(argv) == 1
    assert "trend_buy_threshold" in capsys.readouterr().err
    assert not (tmp_path / "replay").exists()
    assert error_ledger.counts().get(REFUSED_SITE, 0) == before + 1


def _write_lines(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_an_unterminated_last_line_is_held_back(tmp_path: Path) -> None:
    text = json.dumps(_start_record(_LIVE_START)) + "\n" + '{"kind": "book", "ts_n'
    segment = latest_segment(_write_lines(tmp_path / "log.jsonl", text))
    assert (segment.start_ns, segment.end_ns, segment.cycles) == (_LIVE_START, _LIVE_START, 0)


@pytest.mark.parametrize(
    "line", ["", "{not json", '{"ts_ns": 5}', '{"kind": "book"}', "[1, 2]"], ids=repr
)
def test_a_malformed_complete_line_is_refused(tmp_path: Path, line: str) -> None:
    text = json.dumps(_start_record(_LIVE_START)) + "\n" + line + "\n"
    with pytest.raises(ReplayRefused, match="record"):
        latest_segment(_write_lines(tmp_path / "log.jsonl", text))


def _segment_text(start_ns: int, last_ns: int) -> str:
    records = [_start_record(start_ns), {"kind": "book", "ts_ns": last_ns}]
    return "".join(json.dumps(record) + "\n" for record in records)


def test_an_older_segment_never_passes_for_a_fresh_run(tmp_path: Path) -> None:
    window = (_LIVE_START, _LIVE_START + 10 * NS_PER_S)
    path = _write_lines(tmp_path / "replay.jsonl", _segment_text(*window))
    assert _check_replay_log(path, window, 0) == 1
    with pytest.raises(ReplayRefused, match="no `start` record after byte"):
        _check_replay_log(path, window, path.stat().st_size)  # the run appended nothing


def test_a_replay_stopping_short_of_the_window_end_is_refused(tmp_path: Path) -> None:
    window = (_LIVE_START, _LIVE_START + 10 * NS_PER_S)
    short = _segment_text(_LIVE_START, _LIVE_START + 8 * NS_PER_S)
    with pytest.raises(ReplayRefused, match="stops short"):
        _check_replay_log(_write_lines(tmp_path / "replay.jsonl", short), window, 0)


def test_the_definition_in_force_at_the_start_is_used(tmp_path: Path) -> None:
    catalog = ParquetDataCatalog(str(tmp_path / "catalog"))
    older = _instrument()
    newer = CryptoPerpetual.from_dict(
        {
            **CryptoPerpetual.to_dict(older),
            "ts_init": _LIVE_START + NS_PER_S,
            "price_precision": 2,
            "price_increment": "0.01",
        }
    )
    catalog.write_data([older])
    catalog.write_data([newer])
    assert catalog_instrument(catalog, _IID, _LIVE_START).ts_init == older.ts_init
    with pytest.raises(ReplayRefused, match="definition stored by the window start"):
        catalog_instrument(catalog, _IID, older.ts_init - 1)
