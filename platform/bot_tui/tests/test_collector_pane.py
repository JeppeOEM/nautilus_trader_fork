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
"""Tests for bot_tui.collector_pane -- Stories 6.1, 25.1b, 29.2. Pure-logic only, no urwid/I/O."""

import datetime as dt

from bot_tui import collector_pane


def _status(iid: str, **overrides: object) -> dict:
    base = {
        "id": iid,
        "liquid": True,
        "last_trade_ts": 1_000_000_000,
    }
    base.update(overrides)
    return base


def test_collector_rows_sorted_by_id() -> None:
    statuses = {
        "ETH-USD-PERP.DYDX": _status("ETH-USD-PERP.DYDX"),
        "BTC-USD-PERP.DYDX": _status("BTC-USD-PERP.DYDX"),
        "AAA-USD-PERP.DYDX": _status("AAA-USD-PERP.DYDX"),
    }
    rows = collector_pane.collector_rows(statuses)
    assert [row["id"] for row in rows] == [
        "AAA-USD-PERP.DYDX",
        "BTC-USD-PERP.DYDX",
        "ETH-USD-PERP.DYDX",
    ]


def test_collector_rows_empty_dict_returns_empty_list() -> None:
    assert collector_pane.collector_rows({}) == []


def test_format_collector_line_liquid() -> None:
    row = _status("BTC-USD-PERP.DYDX", liquid=True)
    line = collector_pane.format_collector_line(row, stale=False)
    assert "BTC-USD-PERP.DYDX" in line
    assert "illiquid" not in line
    assert "liquid" in line


def test_format_collector_line_illiquid() -> None:
    row = _status("BTC-USD-PERP.DYDX", liquid=False)
    line = collector_pane.format_collector_line(row, stale=False)
    assert "illiquid" in line


def test_format_collector_line_stale_has_marker() -> None:
    row = _status("BTC-USD-PERP.DYDX")
    stale_line = collector_pane.format_collector_line(row, stale=True)
    fresh_line = collector_pane.format_collector_line(row, stale=False)
    assert stale_line.startswith("~ ")
    assert fresh_line.startswith("  ")


def test_format_unpinned_line_empty_is_blank() -> None:
    assert collector_pane.format_unpinned_line([]) == ""


def test_format_unpinned_line_lists_sorted_ids() -> None:
    line = collector_pane.format_unpinned_line(["ETH-USD-PERP.DYDX", "AAA-USD-PERP.DYDX"])
    assert line == "unpinned (add back with :start <ID>): AAA-USD-PERP.DYDX, ETH-USD-PERP.DYDX"


# --- Story 29.2: per-venue sections ---


def _plan(venue: str, **overrides: object) -> dict:
    base: dict = {
        "unpinned_ids": [],
        "venue": venue,
        "cap": 4,
        "accepts_commands": False,
        "min_liquidity_usd": None,
        "last_apply": None,
    }
    base.update(overrides)
    return base


def test_venue_sections_group_rows_by_id_suffix_sorted() -> None:
    statuses = {
        iid: _status(iid)
        for iid in ("SOL-USD-PERP.DYDX", "ETHUSDT-LINEAR.BYBIT", "BTC-USD-PERP.DYDX", "BTCUSDT")
    }
    sections = collector_pane.venue_sections(statuses, {})
    assert [(s.venue, [r["id"] for r in s.rows]) for s in sections] == [
        ("BYBIT", ["ETHUSDT-LINEAR.BYBIT"]),
        ("DYDX", ["BTC-USD-PERP.DYDX", "SOL-USD-PERP.DYDX"]),
        ("UNKNOWN", ["BTCUSDT"]),
    ]


def test_a_venue_with_only_an_aggregate_gets_an_empty_section() -> None:
    sections = collector_pane.venue_sections({}, {"HYPERLIQUID": _plan("HYPERLIQUID", cap=0)})
    assert sections == [collector_pane.VenueSection("HYPERLIQUID", [], _plan("HYPERLIQUID", cap=0))]
    assert collector_pane.format_section_header(sections[0], stale=False) == (
        "HYPERLIQUID: 0 collected +0 pending · cap 0"
    )


def test_section_header_counts_pending_rows_apart() -> None:
    rows = [_status("A.BYBIT"), _status("B.BYBIT", pending=True), _status("C.BYBIT")]
    section = collector_pane.VenueSection("BYBIT", rows, _plan("BYBIT"))
    assert collector_pane.format_section_header(section, stale=False) == (
        "BYBIT: 2 collected +1 pending · cap 4"
    )


def test_section_header_cap_is_unknown_without_one_and_marks_staleness() -> None:
    section = collector_pane.VenueSection("DYDX", [], {"unpinned_ids": ["X"]})
    assert collector_pane.format_section_header(section, stale=True) == (
        "~ DYDX: 0 collected +0 pending · cap ?"
    )


def test_last_apply_line_lists_counts_and_failed_ids() -> None:
    plan = _plan(
        "DYDX",
        last_apply={
            "ts": 1_790_000_000_000_000_000,
            "subscribed": ["A", "B"],
            "unsubscribed": [],
            "failed": ["C"],
        },
    )
    assert collector_pane.format_last_apply_line(plan) == (
        "last apply 2026-09-21 14:13:20Z: 2 subscribed, 0 unsubscribed, 1 failed (C)"
    )


def test_last_apply_line_before_any_apply_and_for_an_older_aggregate() -> None:
    assert collector_pane.format_last_apply_line(_plan("BYBIT")) == "last apply: none yet"
    assert collector_pane.format_last_apply_line({"unpinned_ids": []}) == ""
    assert collector_pane.format_last_apply_line(None) == ""


def test_liquidity_shows_only_for_a_plan_with_a_threshold() -> None:
    assert collector_pane.shows_liquidity("DYDX", _plan("DYDX", min_liquidity_usd=20000.0))
    assert not collector_pane.shows_liquidity("BYBIT", _plan("BYBIT"))
    # No aggregate yet, or one that predates the key: the pre-29.2 contract (dYdX only).
    assert collector_pane.shows_liquidity("DYDX", {"unpinned_ids": []})
    assert collector_pane.shows_liquidity("DYDX", None)
    assert not collector_pane.shows_liquidity("BYBIT", None)


def test_format_collector_line_without_liquidity_and_pending() -> None:
    row = _status("BTCUSDT-LINEAR.BYBIT", liquid=False, pending=True)
    line = collector_pane.format_collector_line(row, stale=False, show_liquidity=False)
    assert line == "  BTCUSDT-LINEAR.BYBIT         pending"


def test_format_collector_line_truncates_a_long_id() -> None:
    row = _status("A-VERY-LONG-INSTRUMENT-ID-PERP.HYPERLIQUID", liquid=True)
    line = collector_pane.format_collector_line(row, stale=False)
    assert line == "  A-VERY-LONG-INSTRUMENT-ID-P… liquid  "


# --- Story 25.1b: format_archive_line ---

_NOW = dt.datetime(2026, 9, 26, 14, 27, tzinfo=dt.UTC)


def _step(name: str, exit_code: int, venue: str | None = None) -> dict:
    return {"venue": venue, "name": name, "exit": exit_code, "duration_s": 1.0}


def _run(steps: list[dict], **overrides: object) -> dict:
    run = {
        "run_id": "r-1",
        "kind": "nightly",
        "day": "2026-09-25",
        "days": ["2026-09-25"],
        "started": "2026-09-26T03:07:00Z",
        "finished": "2026-09-26T03:41:00Z",
        "steps": steps,
    }
    run.update(overrides)
    return run


def _archive(**overrides: object) -> dict:
    status = {
        "next_run": "2026-09-27T03:07:00Z",
        "next_intraday": "2026-09-26T16:07:00Z",
        "running": None,
        "last_run": _run([_step("reconcile", 0, "BYBIT"), _step("consolidate", 0)]),
        "last_intraday": None,
    }
    status.update(overrides)
    return status


def test_archive_line_before_any_status() -> None:
    assert collector_pane.format_archive_line(None, _NOW) == "archive: no status yet"


def test_archive_line_last_run_ok_with_times_and_next_run() -> None:
    line = collector_pane.format_archive_line(_archive(), _NOW)
    assert line == ("archive: last 2026-09-25 ok 03:07-03:41Z · next 2026-09-27 03:07Z (in 12h40m)")


def test_archive_line_findings_when_only_exit_2() -> None:
    last = _run([_step("reconcile", 2, "BYBIT"), _step("consolidate", 0)])
    line = collector_pane.format_archive_line(_archive(last_run=last), _NOW)
    assert "last 2026-09-25 findings (BYBIT reconcile=2) 03:07-03:41Z" in line


def test_archive_line_failed_names_every_non_zero_step() -> None:
    last = _run([_step("reconcile", 2, "BYBIT"), _step("backup", 1)])
    line = collector_pane.format_archive_line(_archive(last_run=last), _NOW)
    assert "last 2026-09-25 FAILED (BYBIT reconcile=2, backup=1)" in line


def test_archive_line_running_shows_the_current_step() -> None:
    running = _run([_step("rebuild", 0, "DYDX"), _step("candles", 0, "DYDX")], finished=None)
    line = collector_pane.format_archive_line(_archive(running=running), _NOW)
    assert line.startswith("archive: running nightly 2026-09-25 (step 3) · next")


def test_archive_line_appends_a_failed_intraday_merge() -> None:
    intraday = _run([_step("consolidate", 1)], kind="intraday", day="2026-09-26")
    line = collector_pane.format_archive_line(_archive(last_intraday=intraday), _NOW)
    assert "· intraday 2026-09-26 FAILED (consolidate=1) 03:07-03:41Z ·" in line


def test_archive_line_omits_a_clean_intraday_merge() -> None:
    intraday = _run([_step("consolidate", 0)], kind="intraday", day="2026-09-26")
    line = collector_pane.format_archive_line(_archive(last_intraday=intraday), _NOW)
    assert "intraday" not in line


def test_archive_line_no_run_yet_and_stale_marker() -> None:
    line = collector_pane.format_archive_line(_archive(last_run=None), _NOW, stale=True)
    assert line.startswith("~ archive: no run yet · next 2026-09-27 03:07Z")


def test_archive_line_says_backup_off_when_the_backup_is_disabled() -> None:
    line = collector_pane.format_archive_line(_archive(backup="disabled"), _NOW)
    assert line.endswith("· next 2026-09-27 03:07Z (in 12h40m) · backup off")


def test_archive_line_says_nothing_of_an_enabled_backup() -> None:
    line = collector_pane.format_archive_line(_archive(backup="enabled"), _NOW)
    assert "backup" not in line


def test_archive_line_multi_day_catch_up_without_day() -> None:
    last = _run([_step("x", 0)], kind="catch_up", day=None, days=["2026-09-23", "2026-09-24"])
    line = collector_pane.format_archive_line(_archive(last_run=last), _NOW)
    assert "last 2026-09-23,2026-09-24 ok" in line


def test_archive_line_tolerates_malformed_nested_fields() -> None:
    last = {"steps": "nope", "started": 5}
    line = collector_pane.format_archive_line(_archive(last_run=last, next_run="soon"), _NOW)
    assert line == "archive: last ? ok ?-?Z · next 'soon'"


def test_archive_line_tolerates_a_non_string_venue() -> None:
    last = {"steps": [{"venue": 7, "name": "backup_catalog", "exit": 1}], "day": "2026-09-25"}
    line = collector_pane.format_archive_line(_archive(last_run=last), _NOW)
    assert "FAILED (backup_catalog=1)" in line


def test_archive_line_next_run_in_the_past_is_zero_minutes() -> None:
    line = collector_pane.format_archive_line(_archive(next_run="2026-09-26T03:07:00Z"), _NOW)
    assert line.endswith("next 2026-09-26 03:07Z (in 0h00m)")


def test_last_apply_line_survives_an_out_of_range_ts_and_a_malformed_value() -> None:
    far = {"ts": 10**30, "subscribed": [], "unsubscribed": [], "failed": []}
    assert collector_pane.format_last_apply_line(_plan("BYBIT", last_apply=far)).startswith(
        "last apply ?: "
    )
    malformed = _plan("BYBIT", last_apply=["not", "a", "dict"])
    assert collector_pane.format_last_apply_line(malformed) == "last apply: ? (malformed)"


def test_section_header_reads_no_cap_for_an_explicit_null_cap() -> None:
    section = collector_pane.VenueSection("BYBIT", [_status("A.BYBIT")], _plan("BYBIT", cap=None))
    assert collector_pane.format_section_header(section, stale=False) == (
        "BYBIT: 1 collected +0 pending · no cap"
    )
