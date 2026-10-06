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
"""`AlertStore`: the `AlertRepository` adapter over the frozen `alerts.toml`."""

import contextlib
import os
import threading
import tomllib
from dataclasses import asdict
from pathlib import Path

import tomli_w
from observability import error_ledger

from alerting.domain.alert import Alert
from alerting.domain.policy import FiringPolicy


# Frozen (AD-D12): the env var name and the file's text. The path moved from
# `platform/data_api/alerts.toml` to its own directory (DW-197): the atomic save renames a temp
# file over the target, and a rename cannot replace a single-file bind mount (EBUSY), so compose
# mounts `platform/data/alerts/` instead. A deployed file is moved by the DEPLOY_CHECKLIST entry.
ALERTS_PATH: str = os.environ.get("ALERTS_PATH", "platform/data/alerts/alerts.toml")


class AlertStore:
    """
    In-memory list mirrored to a TOML file on every change. A corrupt file raises on load
    rather than starting empty -- the next save would otherwise silently destroy it.

    Persistence mirrors `views/preferences.py` (TOML, full rewrite); the key set is `Alert`'s
    fields, frozen.

    Invariants (DW-197/DW-199): a save is atomic -- serialized first, written to a sibling temp
    file, fsynced and renamed over the target, then the directory fsynced (a failure of that last
    step is ledgered at `alerting.store.fsync_dir`, not raised: the file is already published) --
    so a failed or interrupted save leaves the previous file byte-identical and no temp behind.
    `add`/`delete` restore the previous in-memory list when their save raises and re-raise, so
    memory never holds an alert the file lacks (or lacks one the file holds). `record_fire` alone
    keeps its in-memory change on a failed save (see there).
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._alerts: list[Alert] = self._load()

    def _load(self) -> list[Alert]:
        if not self._path.exists():
            return []
        with self._path.open("rb") as f:
            raw = tomllib.load(f)
        return [Alert(**entry) for entry in raw.get("alerts", [])]

    def _save(self) -> None:
        self._publish()
        self._confirm_durable()

    def _publish(self) -> None:
        # TOML has no null: drop None fields, Alert's defaults restore them on load.
        entries = [{k: v for k, v in asdict(a).items() if v is not None} for a in self._alerts]
        # Serialized before touching the disk: an unencodable value fails with the file untouched.
        _write_atomic(self._path, tomli_w.dumps({"alerts": entries}).encode())

    def _confirm_durable(self) -> None:
        try:
            _fsync_dir(self._path.parent)
        except OSError as exc:
            # The rename already published the new file and memory matches it, so this is not a
            # failed save (raising would roll memory back behind the file); only the rename's
            # crash-durability is unconfirmed, which is ledgered, never dropped (DATA-07).
            error_ledger.record(
                "alerting.store.fsync_dir",
                f"{self._path.parent}: rename not confirmed durable",
                exc,
            )

    def _save_or_restore(self, previous: list[Alert]) -> None:
        # A failed save must not leave memory ahead of the file: the API answers 500, so an added
        # alert that kept firing (or a deleted one that stayed gone until restart) would contradict
        # what the caller was told. Only the publish is guarded: once the rename has run, memory
        # matches the file and must stay. BaseException: a KeyboardInterrupt mid-write is a failed
        # save too.
        try:
            self._publish()
        except BaseException:
            self._alerts = previous
            raise
        self._confirm_durable()

    def list(self) -> list[Alert]:
        with self._lock:
            return list(self._alerts)

    def add(self, alert: Alert) -> None:
        with self._lock:
            previous = self._alerts
            self._alerts = [*previous, alert]
            self._save_or_restore(previous)

    def delete(self, alert_id: str) -> bool:
        with self._lock:
            kept = [a for a in self._alerts if a.id != alert_id]
            if len(kept) == len(self._alerts):
                return False
            previous = self._alerts
            self._alerts = kept
            self._save_or_restore(previous)
            return True

    def record_fire(self, alert: Alert, ts_ns: int) -> None:
        with self._lock:
            alert.last_fired_ns = ts_ns
            alert.triggered = alert.frequency == FiringPolicy.ONLY_ONCE
            try:
                self._save()
            except OSError as exc:
                # The in-memory state still stops an only_once repeat this run; a failed save
                # must never swallow the fire itself -- but it is ledgered, not just logged
                # (DATA-07): a lost `triggered` flag would let an only_once alert refire after a
                # restart.
                error_ledger.record(
                    "alerting.store.persist", f"alert {alert.id} fired but was not persisted", exc
                )


def _write_atomic(path: Path, data: bytes) -> None:
    """
    Publish `data` as `path`'s whole content: a sibling temp file, fsynced, renamed over the
    target. Any failure removes the temp and leaves the target as it was. The caller fsyncs the
    parent directory afterwards (as `archive.infrastructure.catalog_files.write_json_atomic` does)
    so the rename itself survives a crash. A process killed mid-write can leave the temp behind;
    the next save truncates and reuses it, and `_load` never reads it.

    Known limit: duplicated from `views/preferences.py`'s writer because `alerting` may not import
    `views`; the upgrade path is one shared atomic-file helper both contexts may import (e.g. in
    `kernel`), together with `archive`'s `write_json_atomic`.
    """
    temp = path.with_name(f".{path.name}.tmp")
    try:
        with temp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())  # the bytes are on disk before the rename publishes them
        os.replace(temp, path)
    except BaseException:
        # A failing cleanup must not mask the save error the caller needs to see.
        with contextlib.suppress(OSError):
            temp.unlink(missing_ok=True)
        raise


def _fsync_dir(directory: Path) -> None:
    """Make the directory's entries (the rename) durable."""
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
