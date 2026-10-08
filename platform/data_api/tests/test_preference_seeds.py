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
DW-219/DW-291: the preference defaults are seeded only where the live file is missing.

Real files in `tmp_path`, the real shipped seed and the real `views.preferences` loader.
"""

import asyncio
import logging
from pathlib import Path

import pytest
from observability import error_ledger
from views.preferences import load_screener_columns

from data_api import app as app_module
from data_api import buses
from data_api.preference_seeds import SEEDS_DIR
from data_api.preference_seeds import seed_missing


_SEED_BYTES = b'[[columns]]\nname = "RelativeStrengthIndex"\n'


def _seeds(tmp_path: Path) -> Path:
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    (seeds / "screener_columns.default.toml").write_bytes(_SEED_BYTES)
    return seeds


def _target(tmp_path: Path) -> Path:
    target = tmp_path / "preferences"
    target.mkdir()
    return target


def _temps(directory: Path) -> list[Path]:
    return list(directory.glob(".*.seed.tmp"))


def test_missing_file_is_seeded_with_the_seed_bytes(tmp_path: Path) -> None:
    target = _target(tmp_path)
    seed_missing(target, _seeds(tmp_path))
    assert (target / "screener_columns.toml").read_bytes() == _SEED_BYTES


def test_missing_file_is_reported_as_seeded(tmp_path: Path) -> None:
    assert seed_missing(_target(tmp_path), _seeds(tmp_path)) == ["screener_columns.toml"]


def test_seeding_leaves_no_temp(tmp_path: Path) -> None:
    target = _target(tmp_path)
    seed_missing(target, _seeds(tmp_path))
    assert _temps(target) == []


@pytest.mark.parametrize(
    "live",
    [b'[[columns]]\nname = "BollingerBands"\n', b"", b"[[columns\nnot toml"],
    ids=["customised", "empty", "malformed"],
)
def test_existing_file_is_never_overwritten(tmp_path: Path, live: bytes) -> None:
    target = _target(tmp_path)
    (target / "screener_columns.toml").write_bytes(live)
    seed_missing(target, _seeds(tmp_path))
    assert (target / "screener_columns.toml").read_bytes() == live


def test_existing_file_is_not_reported_as_seeded(tmp_path: Path) -> None:
    target = _target(tmp_path)
    (target / "screener_columns.toml").write_bytes(b"")
    assert seed_missing(target, _seeds(tmp_path)) == []


def test_existing_file_leaves_no_temp(tmp_path: Path) -> None:
    target = _target(tmp_path)
    (target / "screener_columns.toml").write_bytes(b"")
    seed_missing(target, _seeds(tmp_path))
    assert _temps(target) == []


def test_directory_in_place_of_the_file_is_raised_not_kept(tmp_path: Path) -> None:
    target = _target(tmp_path)
    (target / "screener_columns.toml").mkdir()
    with pytest.raises(IsADirectoryError):
        seed_missing(target, _seeds(tmp_path))


def test_dangling_symlink_is_never_replaced(tmp_path: Path) -> None:
    target = _target(tmp_path)
    live = target / "screener_columns.toml"
    live.symlink_to(tmp_path / "elsewhere.toml")
    seed_missing(target, _seeds(tmp_path))
    assert live.is_symlink()


def test_missing_directory_seeds_nothing(tmp_path: Path) -> None:
    assert seed_missing(tmp_path / "absent", _seeds(tmp_path)) == []


def test_missing_directory_is_not_created(tmp_path: Path) -> None:
    seed_missing(tmp_path / "absent", _seeds(tmp_path))
    assert not (tmp_path / "absent").exists()


def test_missing_directory_warns_naming_it(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    absent = tmp_path / "absent"
    with caplog.at_level(logging.WARNING, logger="data_api.preference_seeds"):
        seed_missing(absent, _seeds(tmp_path))
    assert [r.getMessage() for r in caplog.records if str(absent) in r.getMessage()] != []


def test_file_without_the_default_suffix_is_not_a_seed(tmp_path: Path) -> None:
    seeds = _seeds(tmp_path)
    (seeds / "notes.toml").write_bytes(b"")
    target = _target(tmp_path)
    seed_missing(target, seeds)
    assert sorted(p.name for p in target.iterdir()) == ["screener_columns.toml"]


def test_shipped_seed_parses_into_the_four_default_columns() -> None:
    columns = load_screener_columns(SEEDS_DIR / "screener_columns.default.toml")
    assert [c.name for c in columns] == [
        "RelativeStrengthIndex",
        "MovingAverageConvergenceDivergence",
        "BollingerBands",
        "AverageTrueRange",
    ]


def test_shipped_seeds_are_only_the_screener_columns() -> None:
    # The other preference files and alerts default to empty, which their loaders already produce
    # for a missing file; a seed for them would only be a second statement of that default.
    assert sorted(p.name for p in SEEDS_DIR.iterdir()) == ["screener_columns.default.toml"]


def test_startup_seeds_the_configured_preferences_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)
    monkeypatch.setattr(app_module, "CHART_PREFERENCES_DIR", str(target))
    app_module._seed_preferences()
    assert load_screener_columns(target / "screener_columns.toml") != []


def test_startup_seed_failure_is_ledgered_not_raised(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _target(tmp_path)
    (target / "screener_columns.toml").mkdir()
    monkeypatch.setattr(app_module, "CHART_PREFERENCES_DIR", str(target))
    error_ledger.reset()
    app_module._seed_preferences()
    assert error_ledger.counts() == {"data_api.preference_seed": 1}
    error_ledger.reset()


async def _idle(_redis_url: str) -> None:
    await asyncio.Event().wait()  # stands in for a bus's Redis loop until the lifespan cancels it


@pytest.mark.asyncio
async def test_lifespan_seeds_before_the_first_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ERROR_LEDGER_DIR", raising=False)
    for bus in (
        buses.bus,
        buses.live_candle_bus,
        buses.live_derivs_bus,
        buses.archive_bus,
        buses.markets_bus,
    ):
        monkeypatch.setattr(bus, "run", _idle)
    target = _target(tmp_path)
    monkeypatch.setattr(app_module, "CHART_PREFERENCES_DIR", str(target))
    async with app_module.lifespan(app_module.app):
        assert (target / "screener_columns.toml").read_bytes() == (
            SEEDS_DIR / "screener_columns.default.toml"
        ).read_bytes()
