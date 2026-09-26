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
`archive.backup_catalog`, the module form of `make backup-catalog` (Story 25.1b): the exact rclone
command, and every guard refusing -- ledgered -- before rclone runs. rclone itself is faked.
"""

from pathlib import Path

import pytest
from observability import error_ledger

from archive.backup_catalog import backup
from archive.backup_catalog import backup_argv
from archive.backup_catalog import configured_target
from archive.backup_catalog import main
from archive.backup_catalog import remote_target
from archive.infrastructure.maintenance_lock import maintenance


# 2026-09-26T03:07:09Z
_NOW_NS = 1_790_392_029 * 1_000_000_000
_ENV = {"RCLONE_REMOTE": "r2", "RCLONE_BUCKET": "catalog-bucket"}


def _catalog(
    tmp_path: Path, names: tuple[str, ...] = ("2026-09-25T00-00-00-000000000Z_x.parquet",)
) -> Path:
    leaf = tmp_path / "data" / "mark_price_update" / "BTCUSDT-LINEAR.BYBIT"
    leaf.mkdir(parents=True)
    for name in names:
        (leaf / name).write_bytes(b"")
    return tmp_path


class _Rclone:
    def __init__(self, code: int = 0) -> None:
        self.code = code
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(argv)
        return self.code


def _which(name: str) -> str | None:
    return f"/usr/bin/{name}"


def test_the_argv_is_the_make_targets_sync() -> None:
    assert backup_argv(Path("/app/catalog/data"), "r2:b", "2026-09-26", "2026-09-26T03-07-09Z") == [
        "rclone",
        "sync",
        "/app/catalog/data",
        "r2:b/catalog/data",
        "--backup-dir",
        "r2:b/catalog-replaced/2026-09-26T03-07-09Z",
        "--exclude",
        "**/2026-09-26T*",
        "--exclude",
        "*.tmp",
        "--transfers",
        "8",
        "--fast-list",
    ]


def test_a_trailing_colon_on_the_remote_is_tolerated() -> None:
    assert remote_target("r2:", "b") == remote_target("r2", "b") == "r2:b"


def test_a_configured_backup_syncs_the_catalog_data(tmp_path: Path) -> None:
    error_ledger.reset()
    rclone = _Rclone()
    assert backup(str(_catalog(tmp_path)), _ENV, rclone, _NOW_NS, _which) == 0
    (argv,) = rclone.calls
    assert argv[2:4] == [str(tmp_path / "data"), "r2:catalog-bucket/catalog/data"]
    assert "r2:catalog-bucket/catalog-replaced/2026-09-26T03-07-09Z" in argv
    assert "**/2026-09-26T*" in argv
    assert error_ledger.counts() == {}


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"RCLONE_REMOTE": "r2"},
        {"RCLONE_BUCKET": "b"},
        {"RCLONE_REMOTE": " ", "RCLONE_BUCKET": "b"},
    ],
)
def test_an_unset_target_is_not_configured(tmp_path: Path, env: dict[str, str]) -> None:
    error_ledger.reset()
    rclone = _Rclone()
    assert backup(str(_catalog(tmp_path)), env, rclone, _NOW_NS, _which) == 1
    assert rclone.calls == []
    assert error_ledger.counts() == {"archive.backup_not_configured": 1}


@pytest.mark.parametrize(
    ("env", "target"),
    [
        (_ENV, "r2:catalog-bucket"),
        ({"RCLONE_REMOTE": " r2: ", "RCLONE_BUCKET": " b "}, "r2:b"),
        ({}, None),
        ({"RCLONE_REMOTE": "r2", "RCLONE_BUCKET": "  "}, None),
        ({"RCLONE_REMOTE": "", "RCLONE_BUCKET": "b"}, None),
    ],
)
def test_the_configured_target_needs_both_values_after_strip(
    env: dict[str, str], target: str | None
) -> None:
    assert configured_target(env) == target


def test_a_manual_run_without_a_target_still_exits_1_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    error_ledger.reset()
    monkeypatch.delenv("RCLONE_REMOTE", raising=False)
    monkeypatch.delenv("RCLONE_BUCKET", raising=False)
    assert main(["--catalog", str(_catalog(tmp_path))]) == 1
    assert error_ledger.counts() == {"archive.backup_not_configured": 1}


def test_a_missing_catalog_is_refused(tmp_path: Path) -> None:
    error_ledger.reset()
    assert backup(str(tmp_path / "absent"), _ENV, _Rclone(), _NOW_NS, _which) == 1
    assert error_ledger.counts() == {"archive.catalog_missing": 1}


def test_no_rclone_an_absent_data_dir_or_only_todays_files_are_refused(tmp_path: Path) -> None:
    rclone = _Rclone()
    cases = [
        (str(_catalog(tmp_path / "a")), lambda name: None),
        (str(tmp_path / "b"), _which),
        (str(_catalog(tmp_path / "c", ("2026-09-26T00-00-00-000000000Z_x.parquet",))), _which),
    ]
    (tmp_path / "b").mkdir()
    for catalog, which in cases:
        error_ledger.reset()
        assert backup(catalog, _ENV, rclone, _NOW_NS, which) == 1
        assert error_ledger.counts() == {"archive.backup_failed": 1}
    assert rclone.calls == []


def test_a_failed_rclone_run_is_ledgered(tmp_path: Path) -> None:
    error_ledger.reset()
    assert backup(str(_catalog(tmp_path)), _ENV, _Rclone(code=5), _NOW_NS, _which) == 1
    assert error_ledger.counts() == {"archive.backup_failed": 1}


def test_a_backup_refuses_while_another_maintenance_run_holds_the_lock(tmp_path: Path) -> None:
    error_ledger.reset()
    with maintenance(tmp_path) as writer:
        assert writer is not None
        assert main(["--catalog", str(tmp_path)]) == 1
    assert error_ledger.counts() == {"archive.backup_failed": 1}
