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
"""Tests for prune_catalog: the filename guard, age retention and the verification-gated trade policy."""

import logging
import tempfile
import time
from pathlib import Path

import pytest
from kernel.clocks import CatalogFileSpan
from ml_signals import candle_store

from collector_core.archive_gaps import load_gaps
from collector_core.prune_catalog import _filename_end_ns
from collector_core.prune_catalog import main
from collector_core.prune_catalog import plan_trade_prune
from collector_core.prune_catalog import prune
from collector_core.prune_catalog import prune_instrument


def test_parses_valid_catalog_filename() -> None:
    p = Path("2026-06-01T10-00-00-000000000Z_2026-06-01T10-05-00-000000000Z.parquet")
    end = _filename_end_ns(p)
    assert end == "2026-06-01T10-05-00-000000000Z"


def test_returns_none_for_non_catalog_filename() -> None:
    assert _filename_end_ns(Path("somedata.parquet")) is None
    assert _filename_end_ns(Path("README.md")) is None


def test_end_string_sorts_after_start_string() -> None:
    # The prune logic depends on lexicographic order matching chronological order.
    p = Path("2026-01-01T00-00-00Z_2026-06-15T12-00-00Z.parquet")
    end = _filename_end_ns(p)
    assert end is not None
    assert end > "2026-01-01T00-00-00Z"


def test_prune_deletes_old_files_and_keeps_new(tmp_path: Path) -> None:
    data_dir = tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX"
    data_dir.mkdir(parents=True)

    # Old file: end timestamp 40 days ago (will be pruned)
    old = data_dir / "2020-01-01T00-00-00Z_2020-01-01T01-00-00Z.parquet"
    # New file: end timestamp in the future (kept)
    new = data_dir / "2099-01-01T00-00-00Z_2099-01-01T01-00-00Z.parquet"
    old.write_bytes(b"x")
    new.write_bytes(b"x")

    prune(str(tmp_path), ["order_book_deltas"], retain_days=14, dry_run=False)

    assert not old.exists()
    assert new.exists()


def test_prune_dry_run_deletes_nothing(tmp_path: Path) -> None:
    data_dir = tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX"
    data_dir.mkdir(parents=True)

    old = data_dir / "2020-01-01T00-00-00Z_2020-01-01T01-00-00Z.parquet"
    old.write_bytes(b"x")

    prune(str(tmp_path), ["order_book_deltas"], retain_days=14, dry_run=True)

    assert old.exists()


def _write_parquet(data_dir: Path, start: str, end: str) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{start}_{end}.parquet"
    path.write_bytes(b"x")
    return path


def test_prune_instrument_scoped_to_data_types_only_prunes_listed_type(tmp_path: Path) -> None:
    deltas_dir = tmp_path / "data" / "order_book_deltas" / "BTC-USD-PERP.DYDX"
    bars_dir = tmp_path / "data" / "dydx_minute_bar" / "BTC-USD-PERP.DYDX"
    old_delta = _write_parquet(deltas_dir, "2020-01-01T00-00-00Z", "2020-01-01T01-00-00Z")
    old_bar = _write_parquet(bars_dir, "2020-01-01T00-00-00Z", "2020-01-01T01-00-00Z")

    prune_instrument(
        str(tmp_path), "BTC-USD-PERP.DYDX", retain_hours=1.0, data_types=["order_book_deltas"]
    )

    assert not old_delta.exists()
    assert old_bar.exists()  # untouched: not in data_types, mirrors "unlimited" for other data


def test_prune_instrument_without_data_types_prunes_every_type(tmp_path: Path) -> None:
    deltas_dir = tmp_path / "data" / "order_book_deltas" / "BTC-USD-PERP.DYDX"
    bars_dir = tmp_path / "data" / "dydx_minute_bar" / "BTC-USD-PERP.DYDX"
    old_delta = _write_parquet(deltas_dir, "2020-01-01T00-00-00Z", "2020-01-01T01-00-00Z")
    old_bar = _write_parquet(bars_dir, "2020-01-01T00-00-00Z", "2020-01-01T01-00-00Z")

    prune_instrument(str(tmp_path), "BTC-USD-PERP.DYDX", retain_hours=1.0)

    assert not old_delta.exists()
    assert not old_bar.exists()


def test_prune_instrument_warns_and_skips_nonexistent_data_type(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    deltas_dir = tmp_path / "data" / "order_book_deltas" / "BTC-USD-PERP.DYDX"
    old_delta = _write_parquet(deltas_dir, "2020-01-01T00-00-00Z", "2020-01-01T01-00-00Z")

    with caplog.at_level(logging.WARNING):
        prune_instrument(
            str(tmp_path),
            "BTC-USD-PERP.DYDX",
            retain_hours=1.0,
            data_types=["order_book_deltas", "typo_data_type"],
        )

    assert not old_delta.exists()  # the valid entry in data_types still gets pruned
    assert "typo_data_type" in caplog.text


def test_prune_instrument_without_data_types_leaves_the_trade_archive_alone(tmp_path: Path) -> None:
    trades = _write_parquet(
        tmp_path / "data" / "trade_tick" / "BTC-USD-PERP.DYDX",
        "2020-01-01T00-00-00Z",
        "2020-01-01T01-00-00Z",
    )
    prune_instrument(str(tmp_path), "BTC-USD-PERP.DYDX", retain_hours=1.0)
    assert trades.exists()  # released only by the verification-gated trade policy


# -- trade policy (story 22.13) --------------------------------------------------------------------

_DAY_S = 86_400
_IID = "BTCUSDT-LINEAR.BYBIT"


def _day(days_ago: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * _DAY_S))


def _trade_file(catalog: Path, days_ago: int, iid: str = _IID) -> Path:
    day = _day(days_ago)
    return _write_parquet(
        catalog / "data" / "trade_tick" / iid,
        f"{day}T00-10-00-000000000Z",  # past the arrival margin: no previous-day trades
        f"{day}T23-59-50-000000000Z",
    )


def _verify(candles_dir: Path, days_ago: int, status: str, iid: str = _IID) -> None:
    db = candle_store.connect_rw(str(candles_dir / "candles_bybit.db"))
    candle_store.mark_verified(db, iid, _day(days_ago), status, 0 if status == "pass" else 3, 0)
    db.close()


def test_trade_policy_deletes_only_old_passed_days(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    unverified, failed = _trade_file(catalog, 10), _trade_file(catalog, 9)
    passed_old, passed_young = _trade_file(catalog, 8), _trade_file(catalog, 3)
    _verify(candles, 9, "fail")
    _verify(candles, 8, "pass")
    _verify(candles, 3, "pass")

    delete, kept = plan_trade_prune(str(catalog), str(candles), 7, time.time_ns())

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
    delete, kept = plan_trade_prune(str(catalog), str(candles), 7, time.time_ns())
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
    old = _write_parquet(
        tmp_path / "data" / "order_book_deltas" / "ETH-USD-PERP.DYDX",
        "2020-01-01T00-00-00Z",
        "2020-01-01T01-00-00Z",
    )
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
    delete, kept = plan_trade_prune(str(catalog), str(candles), 7, time.time_ns())
    assert (delete, kept) == ([], [(_IID, _day(10), "unverified")])
    _verify(candles, 10, "pass")
    assert plan_trade_prune(str(catalog), str(candles), 7, time.time_ns())[0] == [early]


def test_every_pruned_trade_file_is_recorded_as_an_archive_gap(tmp_path: Path) -> None:
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    passed = _trade_file(catalog, 8)
    _verify(candles, 8, "pass")
    assert main(["--catalog", str(catalog), "--candles-dir", str(candles), "--apply"]) == 0
    span = CatalogFileSpan.from_path(passed)
    assert not passed.exists()
    assert load_gaps(str(catalog), _IID) == [(span.start_ns, span.end_ns)]


def test_a_leaf_that_is_not_an_instrument_id_is_kept_and_reported(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No venue can be parsed, so no candle store can prove its days: retained, never pruned."""
    catalog, candles = tmp_path / "catalog", tmp_path / "candles"
    candles.mkdir()
    odd = _trade_file(catalog, 10, iid="notanid")
    with caplog.at_level(logging.WARNING):
        delete, kept = plan_trade_prune(str(catalog), str(candles), 7, time.time_ns())
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


if __name__ == "__main__":
    test_parses_valid_catalog_filename()
    test_returns_none_for_non_catalog_filename()
    test_end_string_sorts_after_start_string()
    with tempfile.TemporaryDirectory() as d:
        test_prune_deletes_old_files_and_keeps_new(Path(d) / "a")
        test_prune_dry_run_deletes_nothing(Path(d) / "b")
        test_prune_instrument_scoped_to_data_types_only_prunes_listed_type(Path(d) / "c")
        test_prune_instrument_without_data_types_prunes_every_type(Path(d) / "d")
    print("ok")
