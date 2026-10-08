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
Seed the preference directory's missing files from the shipped defaults (DW-219/DW-291).

The live preference files are runtime state the UI rewrites in place, so they are untracked: a
tracked copy made every VPS `git pull` refuse the rewritten file. A default that differs from
its loader's missing-file state ships instead as `seeds/<name>.default.toml`, here under
`data_api/` because `.dockerignore` keeps `platform/data/` out of the image. At startup each seed
whose `<name>.toml` is missing is placed; an existing file is never touched, whatever it holds.
"""

import contextlib
import logging
import os
import secrets
from pathlib import Path


logger = logging.getLogger(__name__)

SEEDS_DIR = Path(__file__).parent / "seeds"
_SEED_SUFFIX = ".default.toml"


def seed_missing(target_dir: Path, seeds_dir: Path = SEEDS_DIR) -> list[str]:
    """
    Place every seed whose live file is missing in `target_dir`; return the placed file names.

    A missing `target_dir` is warned about and left missing: it is the bind mount, so creating it
    here would put the preferences in the container's own filesystem and lose every save.
    """
    if not target_dir.is_dir():
        logger.warning("Preferences directory %s does not exist; no defaults seeded", target_dir)
        return []
    seeded = []
    for seed in sorted(seeds_dir.glob(f"*{_SEED_SUFFIX}")):
        name = seed.name.removesuffix(_SEED_SUFFIX) + ".toml"
        if _link_if_missing(seed.read_bytes(), target_dir / name):
            seeded.append(name)
    return seeded


def _link_if_missing(data: bytes, target: Path) -> bool:
    """
    Publish `data` as `target` only if `target` does not exist; return whether it was placed.

    The bytes go to a fsynced sibling temp that is then hard-linked into place: the link either
    places the complete file or fails because `target` exists, so neither a crash nor a file that
    appeared meanwhile is ever left partial or overwritten. `open(..., "x")` would not do: a crash
    mid-write leaves a partial file that is never re-seeded and makes the loader fail for good.
    """
    if target.is_dir():
        # Left by a retired single-file bind mount: the loader would fail on it, so it is a fault
        # to surface, not a live file to keep.
        raise IsADirectoryError(f"{target} is a directory, not a preference file")
    if target.exists() or target.is_symlink():
        return False  # the common case after the first start: no temp written at all
    # A random, exclusively created temp, so two processes seeding one directory (two containers
    # may share a pid) never write each other's; not `mkstemp`, whose 0600 mode the placed file
    # would keep, unlike the umask mode every other preference writer leaves.
    temp = target.with_name(f".{target.name}.{secrets.token_hex(8)}.seed.tmp")
    try:
        with temp.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # the bytes are on disk before the link publishes them
        # Known limit: a filesystem without hard links (vfat, some network or Docker Desktop
        # shares) fails `os.link` with an OSError on every start, which the caller ledgers, and the
        # file reads as its loader's missing-file state; the VPS mount is ext4. Upgrade path: an
        # `O_EXCL` create of the target written in place, accepting a partial file on a crash.
        try:
            os.link(temp, target)
        except FileExistsError:
            return False
        _fsync_dir(target.parent)  # the new entry survives a crash
        return True
    finally:
        # A failing cleanup must not mask the error the caller needs to see.
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
