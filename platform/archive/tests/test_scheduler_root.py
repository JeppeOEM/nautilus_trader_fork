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
The `archive` service's composition root (Story 26.1b): the backup chain exists exactly when
`backup_enabled`, and an enabled backup with no rclone target refuses start instead of failing
every night. No Redis: nothing here publishes or subscribes.
"""

from pathlib import Path

import pytest
from observability import error_ledger

from archive import scheduler
from archive.scheduler import build_chains
from archive.scheduler import build_scheduler


_COMMITTED = Path(scheduler.__file__).resolve().parent / "config.toml"
_TARGET = {"RCLONE_REMOTE": "r2", "RCLONE_BUCKET": "catalog-bucket"}


def _config(tmp_path: Path, backup_enabled: bool) -> Path:
    committed = _COMMITTED.read_text()
    assert committed.count("\nbackup_enabled = false\n") == 1, "committed key line changed"
    text = committed.replace(
        "\nbackup_enabled = false\n", f"\nbackup_enabled = {str(backup_enabled).lower()}\n"
    )
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def _env(tmp_path: Path, backup_enabled: bool, **extra: str) -> dict[str, str]:
    return {
        "ARCHIVE_CONFIG": str(_config(tmp_path, backup_enabled)),
        "ARCHIVE_STATE_DIR": str(tmp_path / "state"),
        "CATALOG_PATH": str(tmp_path / "catalog"),
        **extra,
    }


def test_the_backup_chain_exists_only_when_enabled() -> None:
    assert build_chains("/c", "/d", None, backup_enabled=False).backup is None
    backup = build_chains("/c", "/d", None, backup_enabled=True).backup
    assert backup is not None
    [step] = backup()
    assert step.name == "backup_catalog"
    assert step.argv[1:] == ["-m", "archive.backup_catalog", "--catalog", "/c"]


def test_the_committed_config_starts_without_a_target_and_reports_the_backup_off(
    tmp_path: Path,
) -> None:
    env = {"ARCHIVE_STATE_DIR": str(tmp_path / "state"), "CATALOG_PATH": str(tmp_path)}
    built, _ = build_scheduler(env)
    assert built.status()["backup"] == "disabled"


def test_an_enabled_backup_with_a_target_starts(tmp_path: Path) -> None:
    built, _ = build_scheduler(_env(tmp_path, True, **_TARGET))
    assert built.status()["backup"] == "enabled"


@pytest.mark.parametrize(
    "target",
    [{}, {"RCLONE_REMOTE": "r2"}, {"RCLONE_REMOTE": "r2", "RCLONE_BUCKET": " "}],
)
def test_an_enabled_backup_without_a_target_refuses_to_build(
    tmp_path: Path, target: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="RCLONE_REMOTE or RCLONE_BUCKET"):
        build_scheduler(_env(tmp_path, True, **target))


def _main_refuses(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    error_ledger.reset()
    for key in ("RCLONE_REMOTE", "RCLONE_BUCKET", "ERROR_LEDGER_DIR"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert scheduler.main([]) == 1
    assert error_ledger.counts() == {"archive.config_invalid": 1}


def test_main_refuses_an_enabled_backup_without_a_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _main_refuses(monkeypatch, _env(tmp_path, True))
    assert "RCLONE_REMOTE" in error_ledger.last_details()["archive.config_invalid"]


def test_main_refuses_a_backup_enabled_that_is_not_a_bool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path, False)
    config = Path(env["ARCHIVE_CONFIG"])
    config.write_text(config.read_text().replace("backup_enabled = false", 'backup_enabled = "no"'))
    _main_refuses(monkeypatch, env)


def test_main_refuses_a_missing_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _env(tmp_path, False)
    env["ARCHIVE_CONFIG"] = str(tmp_path / "absent.toml")
    _main_refuses(monkeypatch, env)
