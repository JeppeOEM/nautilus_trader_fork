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
`python3 -m verification.bot_parity` end to end (Story 31.9): signal logs in
`bots.strategies.signal_log`'s record format, a catalog of snapshot rows in the stored integer
layout (written raw with pyarrow, the layout `ParquetDataCatalog.write_data` produces: gap-encoded
level lists, precisions per row) and a coverage record. Every row of the spec's I/O matrix, planted
defects that must fail the run, and the CLI's exit statuses; then the pure pieces.
"""

import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from observability import error_ledger

from verification import bot_parity as tool
from verification.application import sites
from verification.domain.bot_parity import MalformedRecord
from verification.domain.bot_parity import grid_units
from verification.domain.bot_parity import latest_segment
from verification.domain.bot_parity import same
from verification.domain.bot_parity import top_equals
from verification.domain.reference_book import BookUnits
from verification.domain.reference_book import StoredBook
from verification.infrastructure.signal_logs import SignalLogDir
from verification.infrastructure.snapshot_book import stored_book


IID = "BTCUSDT-LINEAR.BYBIT"
BOT = "verify-bybit-btcusdt-linear"
NS = 1_000_000_000
# A start off the second (as the live clock's is), so cycles sit at S + 0.754043 s.
START_NS = 1_790_000_000 * NS + 754_043_000
CYCLES = 30
PP, SP = 1, 3
_BASE_UNITS = 100_000  # 10000.0 at precision 1

Record = dict[str, Any]


def _second(ts_ns: int) -> int:
    return ts_ns // NS


def _cycle_ts(k: int) -> int:
    return START_NS + k * NS


def _units_book(second: int) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Return one second's stored book in integer units: two levels a side, varying by second."""
    best = _BASE_UNITS + second % 5
    bids = [(best, 1000 + second % 3), (best - 1, 2000)]
    asks = [(best + 1, 1500 + second % 4), (best + 3, 500)]
    return bids, asks


def _floats(levels: list[tuple[int, int]]) -> list[list[float]]:
    return [[float(Decimal(p).scaleb(-PP)), float(Decimal(s).scaleb(-SP))] for p, s in levels]


def _levels_of(second: int) -> dict[str, list[list[float]]]:
    """Return one second's stored book as a signal log's `bids`/`asks` floats."""
    bids, asks = _units_book(second)
    return {"bids": _floats(bids), "asks": _floats(asks)}


def _microprice_of(second: int) -> float:
    """Return the microprice of one second's stored top of book (the replay's quote), exactly."""
    (bid, bid_size), (ask, ask_size) = (side[0] for side in _units_book(second))
    p, a = Decimal(bid).scaleb(-PP), Decimal(ask).scaleb(-PP)
    bs, as_ = Decimal(bid_size).scaleb(-SP), Decimal(ask_size).scaleb(-SP)
    return float((p * as_ + a * bs) / (bs + as_))


def _fed(ts_ns: int, second: int) -> Record:
    """Return a book cycle's inputs fed from one stored second: its levels and its microprice."""
    return {**_levels_of(second), "microprice": _microprice_of(second)}


def _active(ts_ns: int) -> int:
    """
    Return the second of the stored row active at a cycle by `ts_init` (the replay's book): a row
    is sampled 1.5 s after its second opens, so at S + 0.754 s the latest known row is S - 1's.
    """
    return _second(ts_ns) - 1


def _start() -> Record:
    return {
        "kind": "start",
        "bot_id": BOT,
        "instrument_id": IID,
        "ts_ns": START_NS,
        "trade_size": "0.001",
        "bar_spec": "1-MINUTE-LAST-INTERNAL",
        "trend_lookback": 5,
        "trend_buy_threshold": 0.6,
        "trend_sell_threshold": 0.4,
        "ofi_levels": 2,
        "ofi_window": 3,
        "obi_levels": 2,
        "ofi_confirm_threshold": 0.0,
    }


def _values(k: int) -> Record:
    return {
        "microprice": _microprice_of(_active(_cycle_ts(k))),
        "ofi": float(k),
        "obi": 0.25 + k / 64,
        "mlofi": None if k < 3 else float(k - 3),
        "trend": None,
        "signal": "not_ready",
        "action": None,
    }


def _book(k: int) -> Record:
    ts = _cycle_ts(k)
    return {
        "kind": "book",
        "bot_id": BOT,
        "instrument_id": IID,
        "ts_ns": ts,
        "book_ts_ns": ts - 100_000_000,
        **_values(k),
        **_fed(ts, _active(ts)),
    }


def _bar(minute_ts: int, close: float, trend: float | None) -> Record:
    return {
        "kind": "bar",
        "bot_id": BOT,
        "instrument_id": IID,
        "ts_ns": minute_ts,
        "bar": {"ts_event": minute_ts, "close": close},
        **_values(0),
        "mlofi": 1.0,
        "trend": trend,
    }


def _log() -> list[Record]:
    return [_start(), *(_book(k) for k in range(1, CYCLES + 1))]


def _at(log: list[Record], k: int) -> Record:
    """Return the book-group record of cycle k (book or book_skipped)."""
    ts = _cycle_ts(k)
    return next(r for r in log if r["ts_ns"] == ts and r["kind"] != "bar")


def _stored_row(second: int) -> Record:
    bids, asks = _units_book(second)
    return {
        "price_precision": PP,
        "size_precision": SP,
        "bid_prices": [bids[0][0]] + [bids[i - 1][0] - bids[i][0] for i in range(1, len(bids))],
        "bid_sizes": [s for _, s in bids],
        "ask_prices": [asks[0][0]] + [asks[i][0] - asks[i - 1][0] for i in range(1, len(asks))],
        "ask_sizes": [s for _, s in asks],
        "buy_volume": 0,
        "sell_volume": 0,
        "buy_count": 0,
        "sell_count": 0,
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "close_price": None,
        "ts_event": second * NS + NS // 2,
        "ts_init": second * NS + 3 * NS // 2,
    }


_SCHEMA = pa.schema(
    [
        ("price_precision", pa.uint8()),
        ("size_precision", pa.uint8()),
        *((name, pa.list_(pa.int64())) for name in ("bid_prices", "bid_sizes")),
        *((name, pa.list_(pa.int64())) for name in ("ask_prices", "ask_sizes")),
        ("buy_volume", pa.int64()),
        ("sell_volume", pa.int64()),
        ("buy_count", pa.uint32()),
        ("sell_count", pa.uint32()),
        *((name, pa.int64()) for name in ("open_price", "high_price", "low_price", "close_price")),
        ("ts_event", pa.uint64()),
        ("ts_init", pa.uint64()),
    ]
)


def _all_seconds() -> list[int]:
    """Every second the window can look at, plus the minute after it (the flush probe)."""
    return list(range(_second(START_NS) - 10, _second(_cycle_ts(CYCLES)) + 70))


def _write_catalog(root: Path, seconds: list[int], coverage: list[Record]) -> Path:
    catalog = root / "catalog"
    part = catalog / "data" / "custom_dydx_second_snapshot" / IID
    part.mkdir(parents=True)
    rows = [_stored_row(s) for s in seconds]
    pq.write_table(pa.Table.from_pylist(rows, schema=_SCHEMA), part / "rows.parquet")
    (root / "coverage").mkdir()
    (root / "coverage" / "bybit.jsonl").write_text("".join(json.dumps(c) + "\n" for c in coverage))
    return catalog


def _write_log(directory: Path, records: list[Record]) -> None:
    directory.mkdir(exist_ok=True)
    text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)
    (directory / f"{BOT}.jsonl").write_text(text)


def _coverage(second: int, reason: str) -> Record:
    return {
        "kind": "seconds",
        "instrument_id": IID,
        "reason": reason,
        "first_s": second,
        "last_s": second,
        "count": 1,
    }


def _run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    replay_edit: Callable[[list[Record]], None] = lambda log: None,
    live_edit: Callable[[list[Record]], None] = lambda log: None,
    missing: tuple[int, ...] = (),
    coverage: tuple[Record, ...] = (),
) -> tuple[int, dict[str, Any]]:
    """Write both logs (edited), the catalog without `missing` seconds; run with `--json`."""
    live, replay = _log(), _log()
    live_edit(live)
    replay_edit(replay)
    _write_log(tmp_path / "live", live)
    _write_log(tmp_path / "replay", replay)
    seconds = [s for s in _all_seconds() if s not in missing]
    catalog = _write_catalog(tmp_path, seconds, list(coverage))
    status = tool.main([*_args(tmp_path, catalog), "--json"])
    report = json.loads(capsys.readouterr().out)
    return status, report["bots"][0]


def _args(tmp_path: Path, catalog: Path) -> list[str]:
    return [
        "--venue",
        "BYBIT",
        "--live-dir",
        str(tmp_path / "live"),
        "--replay-dir",
        str(tmp_path / "replay"),
        "--catalog",
        str(catalog),
    ]


@pytest.fixture(autouse=True)
def _no_ledger_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    monkeypatch.delenv("CATALOG_PATH", raising=False)


# --- the I/O matrix ------------------------------------------------------------------------------


def test_identical_logs_are_all_equal_and_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys)
    assert status == 0
    assert bot["paired"] == {"book": CYCLES, "bar": 0}
    assert all(s["equal_share"] == 1.0 for s in bot["signals"].values())
    assert all(s["paired"] == CYCLES for s in bot["signals"].values())
    assert bot["cycle_classes"] == {}
    assert bot["unexplained"] == 0


def test_a_planted_output_with_equal_inputs_throughout_memory_is_unexplained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def plant(log: list[Record]) -> None:
        _at(log, 15)["mlofi"] += 1e-9

    status, bot = _run(tmp_path, capsys, replay_edit=plant)
    assert status == 1
    assert bot["signals"]["mlofi"]["classes"] == {"unexplained": 1}
    assert bot["signals"]["mlofi"]["max_abs_diff"] == pytest.approx(1e-9)
    assert bot["unexplained"] == 1


def test_a_book_the_archive_holds_two_seconds_back_is_book_timing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def live_saw_a_later_book(log: list[Record]) -> None:
        record = _at(log, 10)
        record.update(_levels_of(_second(record["ts_ns"])))  # stored, one second after the replay's
        record["obi"] += 0.5

    status, bot = _run(tmp_path, capsys, live_edit=live_saw_a_later_book)
    assert status == 0
    assert bot["signals"]["obi"]["classes"] == {"book_timing": 1}
    assert bot["cycle_classes"] == {"book_timing": 1}


def test_a_live_book_the_archive_never_held_is_book_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def live_saw_another_book(log: list[Record]) -> None:
        record = _at(log, 10)
        record["asks"] = [[10001.2, 0.5], [10001.3, 0.5]]
        record["obi"] += 0.5

    status, bot = _run(tmp_path, capsys, live_edit=live_saw_another_book)
    assert status == 0
    assert bot["signals"]["obi"]["classes"] == {"book_source": 1}


def test_a_missing_row_with_a_coverage_reason_is_a_gap_carrying_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = _second(_cycle_ts(12)) - 1

    def replay_went_stale(log: list[Record]) -> None:
        record = _at(log, 12)
        record.update(_fed(record["ts_ns"], missing - 1))  # the row before the missing one
        record["obi"] += 0.5

    status, bot = _run(
        tmp_path,
        capsys,
        replay_edit=replay_went_stale,
        missing=(missing,),
        coverage=(_coverage(missing, "crossed"),),
    )
    assert status == 0
    assert bot["signals"]["obi"]["classes"] == {"gap": 1}
    assert bot["gap_reasons"] == {"crossed": 1}


def test_a_missing_row_without_a_reason_is_unexplained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = (_second(_cycle_ts(12)) - 1,)

    def replay_went_stale(log: list[Record]) -> None:
        record = _at(log, 12)
        record.update(_fed(record["ts_ns"], missing[0] - 1))
        record["obi"] += 0.5

    status, bot = _run(tmp_path, capsys, replay_edit=replay_went_stale, missing=missing)
    assert status == 1
    assert bot["signals"]["obi"]["classes"] == {"unexplained": 1}


def test_a_skipped_live_second_explains_the_replay_only_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def live_skipped(log: list[Record]) -> None:
        record = _at(log, 10)
        for key in ("bids", "asks"):
            del record[key]
        record["kind"], record["reason"] = "book_skipped", "one_sided"

    status, bot = _run(tmp_path, capsys, live_edit=live_skipped)
    assert status == 0
    assert bot["replay_only"] == {"book_skipped:one_sided": 1}
    assert bot["paired"]["book"] == CYCLES - 1


def test_a_missing_live_dir_is_refused_and_ledgered(tmp_path: Path) -> None:
    _write_log(tmp_path / "replay", _log())
    catalog = _write_catalog(tmp_path, _all_seconds(), [])
    before = error_ledger.counts().get(sites.BOT_PARITY_REFUSED, 0)
    with pytest.raises(SystemExit) as exc:
        tool.main(_args(tmp_path, catalog))
    assert isinstance(exc.value.code, str)  # a message: exit status 1
    assert "--live-dir" in exc.value.code
    assert error_ledger.counts().get(sites.BOT_PARITY_REFUSED, 0) == before + 1


# --- planted defects and the other classes -------------------------------------------------------


def test_a_cycle_missing_from_the_replay_without_a_reason_is_unexplained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys, replay_edit=lambda log: log.remove(_at(log, 7)))
    assert status == 1
    assert bot["live_only"] == {"unexplained": 1}


def test_a_decision_disagreement_with_equal_inputs_is_unexplained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys, replay_edit=lambda log: _at(log, 9).update(signal="none"))
    assert status == 1
    assert bot["decisions"] == {"disagreements": 1, "unexplained": 1}


def test_a_carried_mlofi_difference_attributes_the_decision_to_carried_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def live_book_differed_then_carried(log: list[Record]) -> None:
        _at(log, 10).update(_levels_of(_second(_cycle_ts(10))))
        for k in (10, 11, 12):
            _at(log, k)["mlofi"] += 3.0
        _at(log, 12)["signal"] = "none"

    status, bot = _run(tmp_path, capsys, live_edit=live_book_differed_then_carried)
    assert status == 0
    assert bot["signals"]["mlofi"]["classes"] == {"book_timing": 1, "carried_state": 2}
    assert bot["decisions"] == {"disagreements": 1, "carried_state": 1}


def test_mlofi_beyond_its_memory_is_unexplained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def live_differs_past_the_window(log: list[Record]) -> None:
        _at(log, 10).update(_levels_of(_second(_cycle_ts(10))))
        _at(log, 14)["mlofi"] += 3.0  # ofi_window 3: cycle 10 is four updates back

    status, bot = _run(tmp_path, capsys, live_edit=live_differs_past_the_window)
    assert status == 1
    assert bot["signals"]["mlofi"]["classes"] == {"unexplained": 1}


def test_quote_fed_differences_are_quote_cadence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def live_quotes_between_seconds(log: list[Record]) -> None:
        for k in range(1, CYCLES + 1):
            _at(log, k)["microprice"] += 0.05
            _at(log, k)["ofi"] -= 1.0

    status, bot = _run(tmp_path, capsys, live_edit=live_quotes_between_seconds)
    assert status == 0
    assert bot["signals"]["microprice"]["classes"] == {"quote_cadence": CYCLES}
    assert bot["signals"]["ofi"]["max_abs_diff"] == 1.0


def test_a_quote_cadence_difference_never_hides_an_unexplained_signal_of_its_cycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def both(log: list[Record]) -> None:
        _at(log, 20)["microprice"] += 0.05
        _at(log, 20)["obi"] += 0.05

    status, bot = _run(tmp_path, capsys, live_edit=both)
    assert status == 1
    assert bot["cycle_classes"] == {"unexplained": 1}


def _with_bars(closes: list[float], trends: list[float | None]) -> Callable[[list[Record]], None]:
    """Append bar cycles between the timer's cycles (a bar pairs by its own `ts_event`)."""

    def add(log: list[Record]) -> None:
        for i, (close, trend) in enumerate(zip(closes, trends, strict=True)):
            log.append(_bar(_cycle_ts(5 + 5 * i) + NS // 2, close, trend))

    return add


def test_a_trend_difference_on_differing_bars_is_bar_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = _with_bars([10.0, 11.0], [0.5, 0.6])
    replay = _with_bars([10.0, 11.5], [0.5, 0.7])
    status, bot = _run(tmp_path, capsys, live_edit=live, replay_edit=replay)
    assert status == 0
    assert bot["paired"]["bar"] == 2
    assert bot["signals"]["trend"]["classes"] == {"bar_source": 1}


def test_a_trend_difference_after_differing_bars_is_carried_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    live = _with_bars([10.0, 11.0], [0.5, 0.6])
    replay = _with_bars([10.5, 11.0], [0.55, 0.65])
    status, bot = _run(tmp_path, capsys, live_edit=live, replay_edit=replay)
    assert status == 0
    assert bot["signals"]["trend"]["classes"] == {"bar_source": 1, "carried_state": 1}


def test_a_trend_difference_with_equal_bars_is_unexplained(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(
        tmp_path,
        capsys,
        live_edit=_with_bars([10.0, 11.0], [0.5, 0.6]),
        replay_edit=_with_bars([10.0, 11.0], [0.5, 0.61]),
    )
    assert status == 1
    assert bot["signals"]["trend"]["classes"] == {"unexplained": 1}


def test_an_action_disagreement_is_informational(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys, replay_edit=lambda log: _at(log, 5).update(action="BUY"))
    assert status == 0
    assert bot["action_disagreements"] == 1


def test_the_latest_segment_is_compared(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def older_run_first(log: list[Record]) -> None:
        old = {**_start(), "ts_ns": START_NS - 3600 * NS}
        log[0:0] = [old, {**_book(1), "ts_ns": START_NS - 3599 * NS, "obi": 99.0}]

    status, bot = _run(tmp_path, capsys, live_edit=older_run_first)
    assert status == 0
    assert bot["start_ns"] == START_NS


# --- review patches: the verifier's own blind spots, each planted --------------------------------


def test_a_replay_truncated_before_the_live_end_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys, replay_edit=lambda log: log.__delitem__(slice(21, None)))
    assert status == 1
    assert bot["truncated"] == CYCLES - 20
    assert bot["paired"]["book"] == 20


def test_a_live_log_truncated_before_the_replay_end_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys, live_edit=lambda log: log.__delitem__(slice(26, None)))
    assert status == 1
    assert bot["truncated"] == CYCLES - 25


def _swap_sides(record: Record) -> None:
    record["bids"], record["asks"] = record["asks"], record["bids"]


def test_a_replay_fed_swapped_sides_fails_even_when_the_live_log_agrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Both logs carry the same wrong book: every signal pairs equal, only the replay's own input
    # against the catalog can catch a broken snapshot conversion.
    def swapped(log: list[Record]) -> None:
        _swap_sides(_at(log, 10))

    status, bot = _run(tmp_path, capsys, replay_edit=swapped, live_edit=swapped)
    assert status == 1
    assert bot["replay_input"] == {"levels": 1}
    assert bot["cycle_classes"] == {}


def test_a_replay_fed_one_level_short_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def short(log: list[Record]) -> None:
        del _at(log, 11)["asks"][-1]

    status, bot = _run(tmp_path, capsys, replay_edit=short, live_edit=short)
    assert status == 1
    assert bot["replay_input"] == {"levels": 1}


def test_a_replay_microprice_not_of_its_fed_top_is_never_quote_cadence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(
        tmp_path, capsys, replay_edit=lambda log: _at(log, 8).__setitem__("microprice", 1.0)
    )
    assert status == 1
    assert bot["replay_input"] == {"microprice": 1}
    assert bot["signals"]["microprice"]["classes"] == {"unexplained": 1}


def _skip(record: Record, reason: str) -> None:
    for key in ("bids", "asks"):
        del record[key]
    record["kind"], record["reason"] = "book_skipped", reason


def test_a_replay_skip_mid_window_explains_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys, replay_edit=lambda log: _skip(_at(log, 15), "no_book"))
    assert status == 1
    assert bot["live_only"] == {"unexplained": 1}
    assert bot["replay_input"] == {"skipped_with_row": 1}


def test_a_replay_without_a_book_before_its_first_row_is_a_cold_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No row of the start's second or the next: the replay's first row is sampled after cycle 2.
    first = _second(START_NS)
    missing = (first, first + 1)

    def cold(log: list[Record]) -> None:
        for k in (1, 2):
            _skip(_at(log, k), "no_book")

    def live_kept_its_book(log: list[Record]) -> None:
        for k in (1, 2):
            _at(log, k).update(_levels_of(first - 1))

    coverage = tuple(_coverage(s, "not_sampling") for s in missing)
    status, bot = _run(
        tmp_path, capsys, cold, live_kept_its_book, missing=missing, coverage=coverage
    )
    assert status == 0
    assert bot["live_only"] == {"cold_start": 2}
    assert bot["replay_input"] == {}


def _later_start(seconds: int) -> Callable[[list[Record]], None]:
    """Make the replay's run start `seconds` s into the live run (a `--start` override)."""

    def edit(log: list[Record]) -> None:
        log[0]["ts_ns"] = START_NS + seconds * NS
        del log[1 : 1 + int(seconds)]

    return edit


def test_a_replay_started_later_on_the_live_grid_pairs_from_its_start(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    status, bot = _run(tmp_path, capsys, replay_edit=_later_start(5))
    assert status == 0
    assert bot["start_ns"] == START_NS + 5 * NS
    assert bot["paired"]["book"] == CYCLES - 5
    assert bot["live_only"] == {}


def test_a_replay_start_off_the_live_grid_or_before_it_is_refused(tmp_path: Path) -> None:
    off_grid = _log()
    off_grid[0]["ts_ns"] += NS // 2
    assert "live grid" in _refused(tmp_path / "off", _log(), off_grid)
    early = _log()
    early[0]["ts_ns"] -= NS
    assert "live grid" in _refused(tmp_path / "early", _log(), early)


def test_a_bot_logging_deeper_than_the_stored_rows_is_refused(tmp_path: Path) -> None:
    deep = _log()
    deep[0]["ofi_levels"] = 3  # the stored rows hold two levels a side
    assert "levels a side" in _refused(tmp_path, deep, deep)


def test_another_venues_malformed_log_never_refuses_this_venue(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    other = [{**r, "instrument_id": "SOL-USD-PERP.HYPERLIQUID"} for r in _log()]
    (tmp_path / "live").mkdir()
    text = "".join(json.dumps(r) + "\n" for r in other) + "{not json\n"
    (tmp_path / "live" / "verify-hyperliquid-sol.jsonl").write_text(text)
    status, bot = _run(tmp_path, capsys)
    assert status == 0
    assert bot["bot_id"] == BOT


# --- refusals and the CLI ------------------------------------------------------------------------


def _refused(tmp_path: Path, live: list[Record], replay: list[Record] | None) -> str:
    tmp_path.mkdir(exist_ok=True)
    _write_log(tmp_path / "live", live)
    (tmp_path / "replay").mkdir()
    if replay is not None:
        _write_log(tmp_path / "replay", replay)
    catalog = _write_catalog(tmp_path, _all_seconds(), [])
    with pytest.raises(SystemExit) as exc:
        tool.main(_args(tmp_path, catalog))
    assert isinstance(exc.value.code, str)
    return exc.value.code


def test_a_replay_with_another_start_is_refused(tmp_path: Path) -> None:
    replay = _log()
    replay[0]["ts_ns"] += 1
    assert "start" in _refused(tmp_path, _log(), replay)


def test_a_bot_without_its_replay_log_is_refused(tmp_path: Path) -> None:
    assert "no replay log" in _refused(tmp_path, _log(), None)


def test_a_malformed_record_is_refused(tmp_path: Path) -> None:
    live = _log()
    del live[5]["signal"]
    assert "keys" in _refused(tmp_path, live, _log())


def test_no_bot_of_the_venue_is_refused(tmp_path: Path) -> None:
    other = [{**r, "instrument_id": "SOL-USD-PERP.HYPERLIQUID"} for r in _log()]
    assert "no signal log of a BYBIT bot" in _refused(tmp_path, other, other)


def test_a_catalog_not_flushed_past_the_window_is_refused(tmp_path: Path) -> None:
    _write_log(tmp_path / "live", _log())
    _write_log(tmp_path / "replay", _log())
    last = _second(_cycle_ts(CYCLES))
    catalog = _write_catalog(tmp_path, [s for s in _all_seconds() if s <= last], [])
    with pytest.raises(SystemExit) as exc:
        tool.main(_args(tmp_path, catalog))
    assert "not flushed past it" in str(exc.value.code)


def test_a_usage_error_exits_2(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        tool.main(["--venue", "BYBIT"])
    assert exc.value.code == 2


def test_the_text_report_names_every_signal_and_the_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_log(tmp_path / "live", _log())
    _write_log(tmp_path / "replay", _log())
    catalog = _write_catalog(tmp_path, _all_seconds(), [])
    assert tool.main(_args(tmp_path, catalog)) == 0
    out = capsys.readouterr().out
    assert out.startswith("bot_parity BYBIT: PASS (0 unexplained)")
    assert all(name in out for name in ("microprice", "ofi", "obi", "mlofi", "trend"))


# --- the pure pieces -----------------------------------------------------------------------------


def test_grid_units_recovers_the_price_behind_a_one_ulp_as_double() -> None:
    # `Price.as_double()` of 83062.6 logged as 83062.59999999999 (a real live record).
    assert grid_units(83062.59999999999, 1) == 830626
    assert grid_units(83062.59999999999, 2) == 8306260
    assert grid_units(83062.7, 1) != 830626
    assert grid_units(float("nan"), 1) is None


def test_a_stored_book_matches_only_its_own_top_levels() -> None:
    book = stored_book(_stored_row(100))
    bids, asks = _units_book(100)
    cycle = latest_segment(
        [("x:1", json.dumps(_start())), ("x:2", json.dumps({**_book(1), **_bids_asks(bids, asks)}))]
    ).cycles[0]
    assert top_equals(book, cycle, 2)
    assert not top_equals(book, cycle, 1)  # one level logged per side would be the top-1
    shallow = StoredBook(book.ts_init, PP, SP, BookUnits(book.book.bids[:1], book.book.asks))
    assert not top_equals(shallow, cycle, 2)
    one_tick_off = [(price + 1, size) for price, size in asks]
    shifted = latest_segment(
        [
            ("x:1", json.dumps(_start())),
            ("x:2", json.dumps({**_book(1), **_bids_asks(bids, one_tick_off)})),
        ]
    ).cycles[0]
    assert not top_equals(book, shifted, 2)  # same sizes, every ask one tick away


def _bids_asks(bids: list[tuple[int, int]], asks: list[tuple[int, int]]) -> Record:
    return {"bids": _floats(bids), "asks": _floats(asks)}


def test_same_is_exact_with_null_and_nan() -> None:
    assert same(None, None)
    assert same(float("nan"), float("nan"))
    assert same(1.0, 1.0)
    assert not same(None, 0.0)
    assert not same(1.0, 1.0 + 2**-52)
    assert not same(float("nan"), 1.0)


def test_a_cycle_before_any_start_is_refused() -> None:
    with pytest.raises(MalformedRecord, match="before any start"):
        latest_segment([("x:1", json.dumps(_book(1)))])


def test_an_unterminated_last_line_is_held_back(tmp_path: Path) -> None:
    text = json.dumps(_start()) + "\n" + json.dumps(_book(1)) + "\n" + '{"kind":"bo'
    (tmp_path / f"{BOT}.jsonl").write_text(text)
    lines = list(SignalLogDir(tmp_path).lines(BOT))
    assert [where.rsplit(":", 1)[1] for where, _ in lines] == ["1", "2"]
