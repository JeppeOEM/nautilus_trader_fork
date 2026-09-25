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
import json
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from kernel.second_snapshot import DydxSecondSnapshot
from kernel.venues import VENUE_KINDS

from collector_core import crosscheck_errors
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.persistence.catalog import ParquetDataCatalog


_NS_PER_S = crosscheck_errors.NS_PER_S
_NS_PER_DAY = crosscheck_errors.NS_PER_DAY

_IID = "ETH-USD-PERP.DYDX"
_DAY0 = 20_000 * _NS_PER_DAY
_WINDOW_START = _DAY0
_WINDOW_END = _DAY0 + 60 * _NS_PER_S  # a short 60 s window is plenty for these tests


def _snapshot(ts_event: int, ts_init: int) -> DydxSecondSnapshot:
    return DydxSecondSnapshot(
        InstrumentId.from_str(_IID),
        [1.0],
        [1.0],
        [2.0],
        [1.0],
        1.0,
        0.5,
        1,
        1,
        ts_event,
        ts_init,
        1.0,
        1.0,
        1.0,
        1.0,
    )


def _write_snapshots(catalog_path: Path, seconds: list[int]) -> None:
    writer = ParquetDataCatalog(str(catalog_path))
    writer.write_data([_snapshot(_DAY0 + s * _NS_PER_S, _DAY0 + s * _NS_PER_S) for s in seconds])


def _write_ledger(errors_dir: Path, service: str, records: list[dict[str, Any]]) -> None:
    errors_dir.mkdir(parents=True, exist_ok=True)
    path = errors_dir / f"{service}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for rec in records:
            line = {
                "ts_ns": rec["ts_ns"],
                "service": service,
                "pid": 1,
                "site": rec["site"],
                "detail": rec.get("detail", ""),
                "exc_type": None,
                "suppressed": 0,
            }
            fh.write(json.dumps(line) + "\n")


@pytest.fixture
def catalog_path(tmp_path: Path) -> Path:
    return tmp_path / "catalog"


@pytest.fixture
def errors_dir(tmp_path: Path) -> Path:
    return tmp_path / "errors"


def test_clean_day_passes(catalog_path: Path, errors_dir: Path) -> None:
    _write_snapshots(catalog_path, list(range(61)))  # every second, no gaps
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert report.gaps == []
    assert crosscheck_errors._exit_code(report, crosscheck_errors._DEFAULT_FAIL_ON) == 0


def test_gap_with_matching_ledger_entry_is_explained(catalog_path: Path, errors_dir: Path) -> None:
    _write_snapshots(catalog_path, [0, 1, 2, 10, 11, 12])  # gap between 2 and 10
    _write_ledger(
        errors_dir,
        "collector",
        [{"ts_ns": _DAY0 + 5 * _NS_PER_S, "site": "collector.resync", "detail": "resync"}],
    )
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert len(report.gaps) == 1
    gap = report.gaps[0]
    assert not gap.unexplained
    assert "collector.resync" in gap.explanation
    assert crosscheck_errors._exit_code(report, crosscheck_errors._DEFAULT_FAIL_ON) == 0


def test_gap_across_restart_is_explained_as_restart(catalog_path: Path, errors_dir: Path) -> None:
    _write_snapshots(catalog_path, [0, 1, 2, 10, 11, 12])  # gap between 2 and 10
    _write_ledger(
        errors_dir, "collector", [{"ts_ns": _DAY0 + 5 * _NS_PER_S, "site": "process_start"}]
    )
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert len(report.gaps) == 1
    assert report.gaps[0].explanation == "restart"
    assert crosscheck_errors._exit_code(report, crosscheck_errors._DEFAULT_FAIL_ON) == 0
    assert [svc.restarts for svc in report.services] == [1]


def test_gap_with_neither_is_unexplained_and_exits_nonzero(
    catalog_path: Path, errors_dir: Path
) -> None:
    _write_snapshots(catalog_path, [0, 1, 2, 10, 11, 12])  # gap between 2 and 10
    # No ledger file at all for "collector".
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert len(report.gaps) == 1
    assert report.gaps[0].unexplained
    assert crosscheck_errors._exit_code(report, crosscheck_errors._DEFAULT_FAIL_ON) == 1


def test_a_ledger_entry_outside_the_skew_bound_does_not_explain_a_gap(
    catalog_path: Path, errors_dir: Path
) -> None:
    """The matcher's tolerance is the 300 s skew bound, not "anywhere in the window"."""
    _write_snapshots(catalog_path, [0, 1, 2, 10, 11, 12])
    far = _DAY0 + 5 * _NS_PER_S + crosscheck_errors._MAX_TS_INIT_SKEW_NS + 10 * _NS_PER_S
    _write_ledger(errors_dir, "collector", [{"ts_ns": far, "site": "collector.resync"}])
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, far + _NS_PER_S, venue=None
    )
    assert len(report.gaps) == 1
    assert report.gaps[0].unexplained


def test_fail_on_site_with_nonzero_count_exits_nonzero(
    catalog_path: Path, errors_dir: Path
) -> None:
    _write_snapshots(catalog_path, list(range(61)))  # no gaps at all
    _write_ledger(
        errors_dir,
        "collector",
        [{"ts_ns": _DAY0 + 5 * _NS_PER_S, "site": "collector.book_sequence", "detail": "gap"}],
    )
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert report.gaps == []
    assert crosscheck_errors._exit_code(report, crosscheck_errors._DEFAULT_FAIL_ON) == 1
    # A site outside the fail-on list does not affect the exit code.
    assert crosscheck_errors._exit_code(report, ("some.other.site",)) == 0


def test_suppressed_carries_are_folded_into_the_printed_counts(
    catalog_path: Path, errors_dir: Path
) -> None:
    """`lines + sum(suppressed)` is the true count a storm produced (AC #3, AC #5)."""
    _write_snapshots(catalog_path, list(range(61)))
    errors_dir.mkdir(parents=True, exist_ok=True)
    (errors_dir / "collector.jsonl").write_text(
        json.dumps({"ts_ns": _DAY0 + _NS_PER_S, "site": "collector.late_trade", "suppressed": 7})
        + "\n"
    )
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert report.services[0].site_counts == {"collector.late_trade": 8}


def _iso(ns: int) -> str:
    return datetime.fromtimestamp(ns / _NS_PER_S, tz=UTC).isoformat().replace("+00:00", "Z")


def _printed_iso(ns: int) -> str:
    """How the report itself renders a timestamp (`+00:00`, not `Z`)."""
    return datetime.fromtimestamp(ns / _NS_PER_S, tz=UTC).isoformat()


def _argv(catalog_path: Path, errors_dir: Path, *extra: str) -> list[str]:
    return [
        "--catalog",
        str(catalog_path),
        "--errors-dir",
        str(errors_dir),
        "--since",
        _iso(_WINDOW_START),
        "--until",
        _iso(_WINDOW_END),
        *extra,
    ]


def _write_started_before_the_window(errors_dir: Path, service: str = "collector") -> None:
    """
    Write a realistic ledger: the service booted an hour before the window and ran since.

    An hour is well outside `_MAX_TS_INIT_SKEW_NS`, so this line never explains an in-window
    gap -- it exists so `main()`'s "nothing was checked" guard sees a real ledger file.
    """
    _write_ledger(
        errors_dir, service, [{"ts_ns": _DAY0 - 3600 * _NS_PER_S, "site": "process_start"}]
    )


def test_main_cli_end_to_end_unexplained_gap_exits_nonzero(
    catalog_path: Path, errors_dir: Path
) -> None:
    """
    Exercises the real argv wiring through main() -> build_report -> _exit_code, not just the
    functions called directly, so a regression in argument plumbing (e.g. --fail-on never
    reaching _exit_code, or --since/--until swapped) fails a test.
    """
    _write_snapshots(catalog_path, [0, 1, 2, 10, 11, 12])  # gap between 2 and 10
    _write_started_before_the_window(errors_dir)  # no entry anywhere near the gap
    assert crosscheck_errors.main(_argv(catalog_path, errors_dir)) == 1


def test_main_cli_end_to_end_clean_window_exits_zero(catalog_path: Path, errors_dir: Path) -> None:
    _write_snapshots(catalog_path, list(range(61)))
    _write_started_before_the_window(errors_dir)
    assert crosscheck_errors.main(_argv(catalog_path, errors_dir)) == 0


def test_main_exits_nonzero_when_no_ledger_file_was_found(
    catalog_path: Path, errors_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An absent `errors_dir` mount must not certify a clean day (P9, DATA-07)."""
    _write_snapshots(catalog_path, list(range(61)))  # perfect data, no ledger at all
    assert crosscheck_errors.main(_argv(catalog_path, errors_dir)) == 1
    assert "no ledger file" in capsys.readouterr().err


def test_main_exits_nonzero_when_no_instrument_was_selected(
    catalog_path: Path, errors_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A `--catalog`/`--venue` that selects nothing must not certify a clean day either."""
    _write_snapshots(catalog_path, list(range(61)))  # a dYdX instrument only
    _write_started_before_the_window(errors_dir)
    argv = _argv(catalog_path, errors_dir, "--venue", "bybit")
    assert crosscheck_errors.main(argv) == 1
    assert "no second-snapshot instrument" in capsys.readouterr().err


def test_main_prints_an_instruments_section_with_row_coverage(
    catalog_path: Path, errors_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """§6's dead-instrument check needs row counts, which a gap list alone cannot give (P11)."""
    _write_snapshots(catalog_path, list(range(61)))
    _write_started_before_the_window(errors_dir)
    assert crosscheck_errors.main(_argv(catalog_path, errors_dir)) == 0
    out = capsys.readouterr().out
    assert "== Instruments ==" in out
    assert f"  {_IID}: rows=61 " in out
    assert f"first={_printed_iso(_DAY0)} " in out
    assert f"last={_printed_iso(_WINDOW_END)}" in out


def test_restarts_fail_the_exit_code_only_when_process_start_is_named(
    catalog_path: Path, errors_dir: Path
) -> None:
    """A crash-loop must not certify a clean day just because every gap prints `restart` (P2)."""
    _write_snapshots(catalog_path, [0, 1, 2, 10, 11, 12])  # gap between 2 and 10
    _write_ledger(
        errors_dir, "collector", [{"ts_ns": _DAY0 + 5 * _NS_PER_S, "site": "process_start"}]
    )
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert report.gaps[0].explanation == "restart"
    assert crosscheck_errors._exit_code(report, crosscheck_errors._DEFAULT_FAIL_ON) == 0
    assert crosscheck_errors._exit_code(report, ("process_start",)) == 1


def test_process_start_in_fail_on_passes_when_there_was_no_restart(
    catalog_path: Path, errors_dir: Path
) -> None:
    _write_snapshots(catalog_path, list(range(61)))
    _write_started_before_the_window(errors_dir)  # boot is outside the window
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert [svc.restarts for svc in report.services] == [0]
    assert crosscheck_errors._exit_code(report, ("process_start",)) == 0


def test_a_mid_gap_entry_does_not_explain_a_gap_longer_than_twice_the_skew(
    catalog_path: Path, errors_dir: Path
) -> None:
    """
    Match only a gap's edges, never its interior (P3).

    One chatty site must not blanket a multi-hour window with a single mid-gap entry.
    """
    skew_s = crosscheck_errors._MAX_TS_INIT_SKEW_NS // _NS_PER_S
    gap_s = 4 * skew_s  # comfortably longer than 2x skew, so the edge windows do not overlap
    _write_snapshots(catalog_path, [0, 1, 2, 2 + gap_s, 3 + gap_s])
    mid = _DAY0 + (2 + gap_s // 2) * _NS_PER_S
    _write_ledger(errors_dir, "collector", [{"ts_ns": mid, "site": "collector.late_trade"}])
    report = crosscheck_errors.build_report(
        str(catalog_path),
        str(errors_dir),
        _WINDOW_START,
        _DAY0 + (10 + gap_s) * _NS_PER_S,
        venue=None,
    )
    assert len(report.gaps) == 1
    assert report.gaps[0].unexplained


def test_an_entry_at_a_long_gaps_edge_still_explains_it(
    catalog_path: Path, errors_dir: Path
) -> None:
    """The edge match is the whole point: an entry at the gap's start still explains it."""
    skew_s = crosscheck_errors._MAX_TS_INIT_SKEW_NS // _NS_PER_S
    gap_s = 4 * skew_s
    _write_snapshots(catalog_path, [0, 1, 2, 2 + gap_s, 3 + gap_s])
    edge = _DAY0 + 3 * _NS_PER_S  # one second after the last row before the gap
    _write_ledger(errors_dir, "collector", [{"ts_ns": edge, "site": "collector.resync"}])
    report = crosscheck_errors.build_report(
        str(catalog_path),
        str(errors_dir),
        _WINDOW_START,
        _DAY0 + (10 + gap_s) * _NS_PER_S,
        venue=None,
    )
    assert report.gaps[0].explanation == "explained: collector.resync"


def test_a_malformed_site_is_skipped_not_fatal(catalog_path: Path, errors_dir: Path) -> None:
    """One bad line must not abort a 24 h check (P12)."""
    _write_snapshots(catalog_path, list(range(61)))
    errors_dir.mkdir(parents=True, exist_ok=True)
    (errors_dir / "collector.jsonl").write_text(
        json.dumps({"ts_ns": _DAY0 + _NS_PER_S, "site": 42, "suppressed": 0})
        + "\n"
        + json.dumps({"ts_ns": _DAY0 + 2 * _NS_PER_S, "site": "collector.x", "suppressed": 0})
        + "\n"
    )
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert report.services[0].site_counts == {"collector.x": 1}


def test_parse_ts_keeps_the_sub_second_part() -> None:
    """`--since ...00.750Z` must not silently become `...00.000Z` (P13)."""
    assert crosscheck_errors._parse_ts("1970-01-01T00:00:00.750Z") == 750_000_000
    assert crosscheck_errors._parse_ts("2026-09-21T00:00:00Z") % _NS_PER_S == 0


def test_main_cli_fail_on_from_argv_reaches_exit_code(catalog_path: Path, errors_dir: Path) -> None:
    _write_snapshots(catalog_path, list(range(61)))  # no gaps
    _write_ledger(
        errors_dir,
        "collector",
        [{"ts_ns": _DAY0 + 5 * _NS_PER_S, "site": "custom.site", "detail": "x"}],
    )
    argv = _argv(catalog_path, errors_dir, "--fail-on", "custom.site")
    assert crosscheck_errors.main(argv) == 1


def test_main_rejects_an_inverted_or_unparseable_window(
    catalog_path: Path, errors_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inverted = [
        "--catalog",
        str(catalog_path),
        "--errors-dir",
        str(errors_dir),
        "--since",
        _iso(_WINDOW_END),
        "--until",
        _iso(_WINDOW_START),
    ]
    assert crosscheck_errors.main(inverted) == 1
    assert "error:" in capsys.readouterr().err
    bad = [
        "--catalog",
        str(catalog_path),
        "--errors-dir",
        str(errors_dir),
        "--since",
        "not-an-instant",
    ]
    assert crosscheck_errors.main(bad) == 1
    assert "error:" in capsys.readouterr().err


def test_venue_filter_still_checks_other_services_fail_on_sites(
    catalog_path: Path, errors_dir: Path
) -> None:
    """
    A --venue narrows which instruments' gaps are checked, never which services' --fail-on
    sites are checked: `ranking_engine.volume24h` must still fail the exit code under
    `--venue bybit`, since the operator explicitly asked for that site.
    """
    _write_snapshots(catalog_path, list(range(61)))  # dYdX instrument, no gaps
    _write_ledger(
        errors_dir,
        "ranking_engine",
        [{"ts_ns": _DAY0 + 5 * _NS_PER_S, "site": "ranking_engine.volume24h", "detail": "x"}],
    )
    report = crosscheck_errors.build_report(
        str(catalog_path), str(errors_dir), _WINDOW_START, _WINDOW_END, venue="bybit"
    )
    assert any(svc.service == "ranking_engine" for svc in report.services)
    assert report.gaps == []  # the dYdX instrument is out of scope for --venue bybit
    assert crosscheck_errors._exit_code(report, ("ranking_engine.volume24h",)) == 1


def test_venue_filter_selects_only_that_venues_instruments(catalog_path: Path) -> None:
    _write_snapshots(catalog_path, [0])
    assert crosscheck_errors._instruments_for_venue(str(catalog_path), None) == [_IID]
    assert crosscheck_errors._instruments_for_venue(str(catalog_path), "dydx") == [_IID]
    assert crosscheck_errors._instruments_for_venue(str(catalog_path), "bybit") == []


def test_missing_catalog_or_errors_dir_is_an_empty_report(tmp_path: Path) -> None:
    report = crosscheck_errors.build_report(
        str(tmp_path / "nope"), str(tmp_path / "also-nope"), _WINDOW_START, _WINDOW_END, venue=None
    )
    assert report.services == []
    assert report.gaps == []
    assert crosscheck_errors._exit_code(report, crosscheck_errors._DEFAULT_FAIL_ON) == 0


def test_venue_service_map_covers_every_registered_venue() -> None:
    assert set(crosscheck_errors._VENUE_SERVICE) == set(VENUE_KINDS)
