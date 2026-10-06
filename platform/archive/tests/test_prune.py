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
`archive.prune_catalog` / `archive.application.prune`: the age rule, the verification-gated trade
policy and the plan rules, executed end to end on a real catalog tree (the rules themselves are
`test_retention.py`'s).
"""

import logging
import sqlite3
import time
from pathlib import Path

import pytest
from candles.infrastructure.sqlite_store import CandleStore
from candles.infrastructure.sqlite_store import db_path_for_venue
from candles.infrastructure.verified_days import VerifiedDaysDir
from kernel.clocks import MAX_TS_INIT_SKEW_NS
from kernel.clocks import NS_PER_S
from kernel.clocks import CatalogFileSpan
from observability import error_ledger

from archive import prune_catalog
from archive.application.prune import decide
from archive.application.prune import execute
from archive.application.prune import log_summary
from archive.application.prune import pruned_marker_span
from archive.domain.gaps import Coverage
from archive.domain.retention import DEFINITION_HELD
from archive.domain.retention import PlanRetention
from archive.domain.retention import RetentionPolicy
from archive.infrastructure.catalog_files import CatalogFiles
from archive.infrastructure.gap_markers import GapMarkerFiles
from archive.infrastructure.gap_markers import load_gaps
from archive.infrastructure.maintenance_lock import maintenance
from archive.prune_catalog import DydxPlanFile
from archive.prune_catalog import main


_DAY_S = 86_400
_IID = "BTCUSDT-LINEAR.BYBIT"
_OLD = ("2020-01-01T00-00-00-000000000Z", "2020-01-01T01-00-00-000000000Z")
_FUTURE = ("2099-01-01T00-00-00-000000000Z", "2099-01-01T01-00-00-000000000Z")


def _write_parquet(data_dir: Path, start: str, end: str) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{start}_{end}.parquet"
    path.write_bytes(b"x")
    return path


def test_age_rule_deletes_old_files_and_keeps_new(tmp_path: Path) -> None:
    data_dir = tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX"
    old, new = _write_parquet(data_dir, *_OLD), _write_parquet(data_dir, *_FUTURE)
    args = ["--catalog", str(tmp_path), "--types", "order_book_deltas", "--days", "14"]
    assert main([*args, "--apply"]) == 0
    assert not old.exists()
    assert new.exists()


def test_age_rule_dry_run_deletes_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX"
    old = _write_parquet(data_dir, *_OLD)
    assert main(["--catalog", str(tmp_path), "--types", "order_book_deltas", "--dry-run"]) == 0
    assert old.exists()


# -- trade policy (story 22.13) --------------------------------------------------------------------


def _day(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * _DAY_S))


def _trade_file(catalog: Path, days_ago: int, iid: str = _IID) -> Path:
    day = _day(days_ago)
    return _write_parquet(
        catalog / "data" / "trade_tick" / iid,
        f"{day}T00-10-00-000000000Z",  # past the skew bound of either midnight: one day's trades
        f"{day}T23-50-00-000000000Z",
    )


def _verify(candles_dir: Path, days_ago: int, status: str, iid: str = _IID) -> None:
    store = CandleStore(db_path_for_venue(candles_dir, "BYBIT"))
    store.mark_verified(iid, _day(days_ago), status, 0 if status == "pass" else 3, 0)
    store.close()


def _plan(catalog: Path, candles: Path, retention_days: int = 7) -> tuple[list, list]:
    """Decide the trade prune through the `VerifiedDays` port, as `main` wires it."""
    policy = RetentionPolicy(time.time_ns(), trade_retention_days=retention_days)
    with VerifiedDaysDir(candles) as verified:
        decision = decide(str(catalog), policy, verified)
    return [Path(d.file.path) for d in decision.delete], decision.kept


def test_trade_policy_deletes_only_old_passed_days(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    unverified, failed = _trade_file(catalog, 10), _trade_file(catalog, 9)
    passed_old, passed_young = _trade_file(catalog, 8), _trade_file(catalog, 3)
    _verify(candles, 9, "fail")
    _verify(candles, 8, "pass")
    _verify(candles, 3, "pass")

    delete, kept = _plan(catalog, candles)

    assert delete == [passed_old]
    assert kept == [(_IID, _day(10), "unverified"), (_IID, _day(9), "failed")]
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 0
    assert (unverified.exists(), failed.exists(), passed_old.exists(), passed_young.exists()) == (
        True,
        True,
        False,
        True,
    )


def test_a_file_spanning_two_days_needs_both_passed(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    first, second = _day(11), _day(10)
    spanning = _write_parquet(
        catalog / "data" / "trade_tick" / _IID,
        f"{first}T23-59-02-000000000Z",
        f"{second}T00-00-01-000000000Z",
    )
    _verify(candles, 11, "pass")
    delete, kept = _plan(catalog, candles)
    assert (delete, kept) == ([], [(_IID, second, "unverified")])
    assert spanning.exists()


def test_trade_policy_is_report_only_by_default_and_honours_venue(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    passed = _trade_file(catalog, 8)
    _verify(candles, 8, "pass")
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles)]) == 0
    assert passed.exists()
    assert (
        main(
            ["--catalog", str(catalog), "--candles-dir", str(candles), "--venue", "DYDX", "--apply"]
        )
        == 0
    )
    assert passed.exists()


def test_types_report_only_by_default(tmp_path: Path) -> None:
    old = _write_parquet(tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX", *_OLD)
    assert main(["--catalog", str(tmp_path), "--types", "order_book_deltas", "--days", "14"]) == 0
    assert old.exists()
    assert main(["--catalog", str(tmp_path), "--types", "order_book_deltas", "--apply"]) == 0
    assert not old.exists()


def test_trade_tick_in_types_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--catalog", str(tmp_path), "--types", "trade_tick", "--apply"])


def test_apply_and_dry_run_conflict(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--catalog", str(tmp_path), "--types", "order_book_deltas", "--apply", "--dry-run"])


def test_a_file_starting_just_after_midnight_also_needs_the_previous_day(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    day = _day(9)
    early = _write_parquet(
        catalog / "data" / "trade_tick" / _IID,
        f"{day}T00-02-00-000000000Z",  # within 5 min of midnight: may hold the day before's trades
        f"{day}T00-59-00-000000000Z",
    )
    _verify(candles, 9, "pass")
    delete, kept = _plan(catalog, candles)
    assert (delete, kept) == ([], [(_IID, _day(10), "unverified")])
    _verify(candles, 10, "pass")
    assert _plan(catalog, candles)[0] == [early]


def test_every_pruned_trade_file_is_recorded_as_a_skew_widened_archive_gap(
    tmp_path: Path,
) -> None:
    """
    The marker is read on `ts_event`, the name spans `ts_init`: a trade up to the bound either
    side of the name span was in the deleted file, so its second must keep its live values.
    """
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    passed = _trade_file(catalog, 8)
    _verify(candles, 8, "pass")
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 0
    span = CatalogFileSpan.from_path(passed)
    assert not passed.exists()
    gaps = load_gaps(str(catalog), _IID)
    assert gaps == [(span.start_ns - MAX_TS_INIT_SKEW_NS, span.end_ns + MAX_TS_INIT_SKEW_NS)]
    coverage = Coverage(0, tuple(gaps))
    assert not coverage.covers(span.start_ns - 100 * NS_PER_S)
    assert not coverage.covers(span.end_ns + 100 * NS_PER_S)


def test_the_pruned_marker_span_is_widened_by_exactly_the_bound_and_clamped_at_zero() -> None:
    assert pruned_marker_span((10**12, 2 * 10**12)) == (
        10**12 - MAX_TS_INIT_SKEW_NS,
        2 * 10**12 + MAX_TS_INIT_SKEW_NS,
    )
    assert pruned_marker_span((NS_PER_S, 2 * NS_PER_S)) == (0, 2 * NS_PER_S + MAX_TS_INIT_SKEW_NS)


def test_a_leaf_that_is_not_an_instrument_id_is_kept_and_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No venue can be parsed, so no candle store can prove its days: retained, never pruned."""
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    odd = _trade_file(catalog, 10, iid="notanid")
    with caplog.at_level(logging.WARNING):
        delete, kept = _plan(catalog, candles)
    assert delete == []
    assert kept == [("notanid", _day(10), "unverified")]
    assert "not an instrument id" in caplog.text
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 0
    assert odd.exists()


def test_an_unparseable_trade_file_name_is_skipped_not_deleted(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    odd = catalog / "data" / "trade_tick" / _IID / "not-a-span.parquet"
    odd.parent.mkdir(parents=True)
    odd.write_bytes(b"x")
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 0
    assert odd.exists()


def test_nothing_to_do_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--catalog", str(tmp_path)])


# -- plan retention (the deleted `DydxCollector._prune_loop`'s cases, Story 25.1) -----------------


@pytest.fixture(autouse=True)
def _no_plan_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prune_catalog, "PLAN_SETTLE_SECONDS", 0.0)


def _dydx_plan(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(body)
    return path


_PLAN = """
network = "mainnet"
catalog_path = "/unused"
non_config_retain_hours = 4.0

[[instruments]]
id = "BTC-USD-PERP.DYDX"
store_order_book_deltas = true

[[instruments]]
id = "ETH-USD-PERP.DYDX"
store_order_book_deltas = true
retain_hours = 48.0
"""


def test_the_plan_prunes_a_dropped_instrument_and_finite_delta_retention(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    data = catalog / "data"
    unlimited = _write_parquet(data / "order_book_deltas" / "BTC-USD-PERP.DYDX", *_OLD)
    finite = _write_parquet(data / "order_book_deltas" / "ETH-USD-PERP.DYDX", *_OLD)
    finite_bar = _write_parquet(data / "custom_dydx_second_snapshot" / "ETH-USD-PERP.DYDX", *_OLD)
    dropped = _write_parquet(data / "custom_dydx_second_snapshot" / "STOPPED-USD-PERP.DYDX", *_OLD)
    dropped_trades = _write_parquet(data / "trade_tick" / "STOPPED-USD-PERP.DYDX", *_OLD)
    other_venue = _write_parquet(data / "custom_dydx_second_snapshot" / _IID, *_OLD)
    plan = _dydx_plan(tmp_path, _PLAN)
    assert main(["--catalog", str(catalog), "--dydx-plan", str(plan), "--apply"]) == 0
    assert unlimited.exists()  # retain_hours None: unlimited
    assert not finite.exists()
    assert finite_bar.exists()  # delta retention touches order_book_deltas only
    assert not dropped.exists()
    assert dropped_trades.exists()  # trades leave only through the verified trade policy
    assert other_venue.exists()  # plan rules are dYdX's alone


def test_the_age_rule_cedes_plan_governed_dydx_deltas(tmp_path: Path) -> None:
    """`make prune` (DW-215): with the plan, an unlimited entry's old deltas survive `--days 14`."""
    catalog = tmp_path / "catalog"
    data = catalog / "data" / "order_book_deltas"
    unlimited = _write_parquet(data / "BTC-USD-PERP.DYDX", *_OLD)
    not_in_plan = _write_parquet(data / "SOL-USD-PERP.DYDX", *_OLD)
    plan = _dydx_plan(tmp_path, _PLAN)
    args = ["--types", "order_book_deltas", "--days", "14", "--dydx-plan", str(plan)]
    assert main(["--catalog", str(catalog), *args, "--apply"]) == 0
    assert unlimited.exists()  # retain_hours None: the plan wins over the age rule
    assert not not_in_plan.exists()


def test_the_plan_is_ignored_for_another_venue(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog"
    dropped = _write_parquet(
        catalog / "data" / "custom_dydx_second_snapshot" / "STOPPED-USD-PERP.DYDX", *_OLD
    )
    plan = _dydx_plan(tmp_path, _PLAN)
    args = ["--catalog", str(catalog), "--dydx-plan", str(plan), "--venue", "BYBIT", "--apply"]
    assert main(args) == 0
    assert dropped.exists()


def test_an_empty_plan_file_is_refused_not_read_as_collect_nothing(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog = tmp_path / "catalog"
    dropped = _write_parquet(
        catalog / "data" / "custom_dydx_second_snapshot" / "BTC-USD-PERP.DYDX", *_OLD
    )
    plan = _dydx_plan(tmp_path, "")
    assert main(["--catalog", str(catalog), "--dydx-plan", str(plan), "--apply"]) == 1
    assert dropped.exists()
    assert error_ledger.counts()["prune.bad_plan"] == 1


def test_a_plan_saved_while_it_is_read_is_refused(tmp_path: Path) -> None:
    plan = _dydx_plan(tmp_path, _PLAN)
    # A save in flight: the second read sees a truncated plan listing only BTC.
    truncated = _PLAN[: _PLAN.index('[[instruments]]\nid = "ETH')]
    source = DydxPlanFile(plan, settle=lambda: plan.write_text(truncated))
    with pytest.raises(ValueError, match="changed while being read"):
        source.retention()
    assert DydxPlanFile(plan, settle=lambda: None).retention().collected == {"BTC-USD-PERP.DYDX"}


def test_age_windows_below_one_day_are_refused(tmp_path: Path) -> None:
    for flag in ("--days", "--trade-retention-days"):
        with pytest.raises(SystemExit):
            main(["--catalog", str(tmp_path), "--types", "order_book_deltas", flag, "0"])


def test_a_trade_file_whose_pruned_marker_failed_is_kept(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    error_ledger.reset()
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    passed = _trade_file(catalog, 8)
    _verify(candles, 8, "pass")
    (catalog / "_archive_gaps").write_text("a file where the marker directory should be")
    with caplog.at_level(logging.INFO):
        # A decided deletion that did not happen is a finding: exit 2, never a silent 0.
        assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 2
    assert passed.exists()  # without its marker a later rebuild would zero those rows
    assert error_ledger.counts() == {"archive_gaps.write": 1}
    assert f"kept {_IID} {_day(8)}: marker_failed" in caplog.text


def test_an_unknown_verified_status_keeps_the_files_and_the_run_goes_on(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    odd, passed = _trade_file(catalog, 9), _trade_file(catalog, 8)
    _verify(candles, 9, "maybe")
    _verify(candles, 8, "pass")
    delete, kept = _plan(catalog, candles)
    assert delete == [passed]
    assert kept == [(_IID, _day(9), "unknown_status:maybe")]
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 0
    assert odd.exists()
    assert not passed.exists()


def test_a_file_gone_before_its_deletion_is_ledgered_and_the_run_goes_on(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    error_ledger.reset()
    data = tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX"
    first = _write_parquet(data, *_OLD)
    second = _write_parquet(
        data, "2020-01-02T00-00-00-000000000Z", "2020-01-02T01-00-00-000000000Z"
    )
    policy = RetentionPolicy(time.time_ns(), age_types=frozenset({"order_book_deltas"}))
    decision = decide(str(tmp_path), policy, None)
    first.unlink()  # e.g. removed by hand between the decision and its execution
    with caplog.at_level(logging.INFO):
        report = execute(decision, CatalogFiles(), GapMarkerFiles(tmp_path))
        log_summary(report, policy, apply=True)
    assert not second.exists()
    assert (report.errors, report.files["age"]) == (1, 1)
    assert report.has_findings()
    assert error_ledger.counts() == {"prune.error": 1}
    assert "0 file(s) skipped for the open UTC day, 1 per-file error(s)" in caplog.text


def test_a_bad_plan_is_ledgered_and_nothing_is_pruned(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog = tmp_path / "catalog"
    dropped = _write_parquet(
        catalog / "data" / "custom_dydx_second_snapshot" / "GONE-USD-PERP.DYDX", *_OLD
    )
    plan = _dydx_plan(
        tmp_path, _PLAN.replace("non_config_retain_hours = 4.0", "non_config_retain_hours = -1.0")
    )
    assert main(["--catalog", str(catalog), "--dydx-plan", str(plan), "--apply"]) == 1
    assert dropped.exists()
    assert error_ledger.counts() == {"prune.bad_plan": 1}


def test_a_plan_listing_no_instruments_prunes_nothing(tmp_path: Path) -> None:
    """A half-saved plan must never read as "every dYdX instrument was dropped"."""
    catalog = tmp_path / "catalog"
    dropped = _write_parquet(
        catalog / "data" / "custom_dydx_second_snapshot" / "GONE-USD-PERP.DYDX", *_OLD
    )
    for text in (
        _PLAN.split("[[instruments]]")[0],
        "[[instruments]]\nstore_order_book_deltas = 1\n",
    ):
        error_ledger.reset()
        plan = _dydx_plan(tmp_path, text)
        assert main(["--catalog", str(catalog), "--dydx-plan", str(plan), "--apply"]) == 1
        assert dropped.exists()
        assert error_ledger.counts() == {"prune.bad_plan": 1}


def test_a_report_only_run_takes_no_lock(tmp_path: Path) -> None:
    """`make prune-dry` can run beside a nightly: it deletes nothing, so it locks nothing."""
    data_dir = tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX"
    old = _write_parquet(data_dir, *_OLD)
    args = ["--catalog", str(tmp_path), "--types", "order_book_deltas"]
    with maintenance(tmp_path) as held:
        assert held is not None
        assert main([*args, "--dry-run"]) == 0
        assert main([*args, "--apply"]) == 1  # a deleting run still waits its turn
    assert old.exists()


def test_venue_must_be_a_known_venue_code(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["--catalog", str(tmp_path), "--types", "order_book_deltas", "--venue", "dydx"])


def test_a_missing_catalog_is_ledgered_and_exit_one(tmp_path: Path) -> None:
    error_ledger.reset()
    assert main(["--catalog", str(tmp_path / "nope"), "--types", "order_book_deltas"]) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}


# -- an unreadable status store (DW-188) -----------------------------------------------------------


class _UnreadableBybit:
    """A `VerifiedDays` whose BYBIT store raises like a corrupt file; other venues read normally."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        self.reads.append(instrument_id)
        if instrument_id.endswith(".BYBIT"):
            raise sqlite3.DatabaseError("file is not a database")
        return "pass"

    def mark_verified(self, instrument_id: str, day: str, s: str, m: int, at: int) -> None:
        raise AssertionError("prune never writes a verdict")

    def clear_verified(self, instrument_id: str, day: str) -> None:
        raise AssertionError("prune never clears a verdict")


def test_an_unreadable_store_keeps_that_venues_days_and_decides_the_others(
    tmp_path: Path,
) -> None:
    error_ledger.reset()
    catalog, dydx = tmp_path / "catalog", "BTC-USD-PERP.DYDX"
    bybit_files = [_trade_file(catalog, 8), _trade_file(catalog, 9)]
    dydx_file = _trade_file(catalog, 8, dydx)
    verified = _UnreadableBybit()
    policy = RetentionPolicy(time.time_ns(), trade_retention_days=7)
    decision = decide(str(catalog), policy, verified)
    assert [Path(d.file.path) for d in decision.delete] == [dydx_file]
    assert sorted(decision.kept) == [
        (_IID, _day(9), "status_unreadable"),
        (_IID, _day(8), "status_unreadable"),
    ]
    assert verified.reads.count(_IID) == 1  # the venue is not queried again this run
    assert error_ledger.counts() == {"prune.verified_days": 1}
    report = execute(decision, None, GapMarkerFiles(catalog))
    assert report.has_findings()
    assert all(f.exists() for f in bybit_files)


class _UnreadableStore(_UnreadableBybit):
    """A `VerifiedDays` whose every store raises like a corrupt file."""

    def verified_status(self, instrument_id: str, day: str) -> str | None:
        raise sqlite3.DatabaseError("file is not a database")


def test_a_held_definition_keeps_its_reason_beside_an_unreadable_trade_day(tmp_path: Path) -> None:
    """DW-208: the trade day's fault reason never relabels the held definition of the same day."""
    error_ledger.reset()
    catalog, gone = tmp_path / "catalog", "GONE-USD-PERP.DYDX"
    _trade_file(catalog, 8, gone)
    day = _day(8)
    definition = _write_parquet(
        catalog / "data" / "crypto_perpetual" / gone,
        f"{day}T00-10-00-000000000Z",
        f"{day}T23-50-00-000000000Z",
    )
    plan = PlanRetention(frozenset({"BTC-USD-PERP.DYDX"}), 4.0)
    policy = RetentionPolicy(time.time_ns(), trade_retention_days=7, plan=plan)
    decision = decide(str(catalog), policy, _UnreadableStore())
    assert decision.delete == []
    assert sorted(decision.kept) == [
        (gone, day, DEFINITION_HELD),
        (gone, day, "status_unreadable"),
    ]
    assert definition.exists()


def test_the_summary_counts_held_definitions_on_the_plan_line_not_the_trade_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """DW-208: a held definition is a plan outcome, never an old instrument-day the trades kept."""
    error_ledger.reset()
    catalog, gone = tmp_path / "catalog", "GONE-USD-PERP.DYDX"
    _trade_file(catalog, 8, gone)
    day = _day(8)
    definition = _write_parquet(
        catalog / "data" / "crypto_perpetual" / gone,
        f"{day}T00-10-00-000000000Z",
        f"{day}T23-50-00-000000000Z",
    )
    plan = PlanRetention(frozenset({"BTC-USD-PERP.DYDX"}), 4.0)
    policy = RetentionPolicy(time.time_ns(), trade_retention_days=7, plan=plan)
    decision = decide(str(catalog), policy, _UnreadableStore())
    with caplog.at_level(logging.INFO):
        report = execute(decision, CatalogFiles(), GapMarkerFiles(catalog))
        log_summary(report, policy, apply=True)
    assert definition.exists()
    assert "1 old instrument-day(s) kept" in caplog.text  # the unreadable trade day alone
    assert "1 dropped-coin definition day(s) held for their trades" in caplog.text


def test_a_corrupt_store_file_is_ledgered_and_the_run_exits_with_findings(tmp_path: Path) -> None:
    error_ledger.reset()
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    kept = _trade_file(catalog, 8)
    Path(db_path_for_venue(candles, "BYBIT")).write_bytes(b"not a database, just garbage " * 64)
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 2
    assert kept.exists()
    assert error_ledger.counts() == {"prune.verified_days": 1}
