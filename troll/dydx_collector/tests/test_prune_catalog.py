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
"""Unit tests for prune_catalog._filename_end_ns: the guard for file-deletion logic."""

import logging
import tempfile
from pathlib import Path

import pytest

from dydx_collector.prune_catalog import _filename_end_ns
from dydx_collector.prune_catalog import prune
from dydx_collector.prune_catalog import prune_instrument


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
