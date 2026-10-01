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
Every nightly step's `main()` opens its own durable error ledger (Story 31.8).

The saga runs each step as a child process; none of them called `error_ledger.start()`, so every
`reconcile.kline_mismatch` of D-51 reached stdout only. Each main here takes its cheapest refusal
(a missing catalog) and must leave `process_start` and that refusal in `archive.<step>.jsonl`
(`archive.<step>_<venue>.jsonl` for one venue's run), never in the scheduler's own `archive.jsonl`.
"""

import json
from collections.abc import Callable
from collections.abc import Iterator
from pathlib import Path

import pytest
from candles import rebuild as candles_rebuild
from observability import error_ledger

from archive import compare_klines
from archive import consolidate_catalog
from archive import nightly
from archive import prune_catalog
from archive import rebuild_seconds
from archive import verify_day
from archive.application import catalog_check


_CLOSED_DAY = "2026-01-05"


@pytest.fixture
def errors_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    error_ledger.reset()
    monkeypatch.setenv("ERROR_LEDGER_DIR", str(tmp_path / "errors"))
    monkeypatch.setenv("ERROR_LEDGER_SERVICE", "archive")  # the scheduler's, inherited
    yield tmp_path / "errors"
    error_ledger.reset()


def _sites(path: Path) -> list[str]:
    return [json.loads(line)["site"] for line in path.read_text().splitlines()]


def _missing(tmp_path: Path) -> str:
    return str(tmp_path / "no-catalog")


_MAINS: dict[str, Callable[[str], int]] = {
    "rebuild_seconds": lambda catalog: rebuild_seconds.main(
        ["--catalog", catalog, "--day", _CLOSED_DAY]
    ),
    "consolidate_catalog": lambda catalog: consolidate_catalog.main(["--catalog", catalog]),
    "compare_klines": lambda catalog: compare_klines.main(
        ["--catalog", catalog, "--db", "x.db", "--venue", "BYBIT", "--day", _CLOSED_DAY]
    ),
    "prune_catalog": lambda catalog: prune_catalog.main(
        ["--catalog", catalog, "--types", "funding_rate"]
    ),
    "nightly": lambda catalog: nightly.main(
        ["--catalog", catalog, "--candles-dir", "c", "--venue", "BYBIT", "--day", _CLOSED_DAY]
    ),
}
# The steps whose argv above names one venue (`--venue BYBIT`): their run ledgers per venue.
_VENUE_SUFFIX = {"compare_klines": "_bybit", "nightly": "_bybit"}


@pytest.mark.parametrize("step", sorted(_MAINS))
def test_each_step_main_opens_its_own_durable_ledger(
    step: str, tmp_path: Path, errors_dir: Path
) -> None:
    assert _MAINS[step](_missing(tmp_path)) == 1

    ledger = errors_dir / f"archive.{step}{_VENUE_SUFFIX.get(step, '')}.jsonl"
    assert _sites(ledger) == ["process_start", "archive.catalog_missing"]
    assert not (errors_dir / "archive.jsonl").exists()  # the scheduler's window is untouched


def test_a_step_run_by_hand_defaults_its_parent_to_archive(
    tmp_path: Path, errors_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ERROR_LEDGER_SERVICE")

    assert _MAINS["compare_klines"](_missing(tmp_path)) == 1

    assert _sites(errors_dir / "archive.compare_klines_bybit.jsonl")[0] == "process_start"
    assert error_ledger.is_job_service("archive.compare_klines_bybit")
    assert not error_ledger.is_job_service("archive")


def test_candles_rebuild_refuses_a_missing_catalog_at_the_archive_site() -> None:
    # The candles context restates the site rather than importing `archive`; the nightly's
    # `build_candles` step must refuse at the same site as its sibling steps.
    assert candles_rebuild.CATALOG_MISSING_SITE == catalog_check.CATALOG_MISSING_SITE


def test_each_venues_run_of_a_step_keeps_its_own_ledger(tmp_path: Path, errors_dir: Path) -> None:
    # The scheduler runs Bybit's nightly then Hyperliquid's: in one shared file the second run's
    # `process_start` would hide the first run's findings from `/api/errors`' `since_start`.
    for venue in ("BYBIT", "HYPERLIQUID"):
        error_ledger.reset()  # each run is its own process in the saga
        argv = ["--catalog", _missing(tmp_path), "--db", "x.db", "--venue", venue]
        assert compare_klines.main([*argv, "--day", _CLOSED_DAY]) == 1

    for venue in ("bybit", "hyperliquid"):
        ledger = errors_dir / f"archive.compare_klines_{venue}.jsonl"
        assert _sites(ledger) == ["process_start", "archive.catalog_missing"]
    assert not (errors_dir / "archive.compare_klines.jsonl").exists()


def test_verify_day_opens_its_own_durable_ledger_per_venue(
    tmp_path: Path, errors_dir: Path
) -> None:
    # It never exits 1, so it has no missing-catalog refusal: a tool that answers nothing is its
    # cheapest ledgered path (one `archive.verify_day` entry per refused type).
    root = tmp_path / "verify_data"
    (root / "raw" / "bybit" / "publicTrade").mkdir(parents=True)
    (root / "raw" / "bybit" / "publicTrade" / f"{_CLOSED_DAY}T00.jsonl.zst").write_bytes(b"")
    argv = ["--catalog", _missing(tmp_path), "--candles-dir", "c", "--venue", "BYBIT"]
    argv += ["--day", _CLOSED_DAY, "--result-file", str(tmp_path / "verify_result.json")]

    code = verify_day.main(
        argv, lambda argv, timeout_s: (1, ""), environ={"VERIFY_DATA_DIR": str(root)}
    )

    assert code == 2
    sites = _sites(errors_dir / "archive.verify_day_bybit.jsonl")
    assert sites == ["process_start", *["archive.verify_day"] * len(verify_day.TOOLS)]
    assert not (errors_dir / "archive.jsonl").exists()
