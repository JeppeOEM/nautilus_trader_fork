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

import os
import threading
import tomllib
from dataclasses import asdict
from pathlib import Path

import tomli_w
from observability import error_ledger

from alerting.domain.alert import Alert
from alerting.domain.policy import FiringPolicy


# Frozen (AD-D12): the env var, its default and the compose bind mount of this file are unchanged
# by Story 24.3's move, so a deployed `alerts.toml` keeps being read from the same place.
ALERTS_PATH: str = os.environ.get("ALERTS_PATH", "platform/data_api/alerts.toml")


class AlertStore:
    """
    In-memory list mirrored to a TOML file on every change. A corrupt file raises on load
    rather than starting empty -- the next save would otherwise silently destroy it.

    Persistence mirrors `views/preferences.py` (TOML, full rewrite); the key set is `Alert`'s
    fields, frozen.
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
        # TOML has no null: drop None fields, Alert's defaults restore them on load.
        entries = [{k: v for k, v in asdict(a).items() if v is not None} for a in self._alerts]
        with self._path.open("wb") as f:
            tomli_w.dump({"alerts": entries}, f)

    def list(self) -> list[Alert]:
        with self._lock:
            return list(self._alerts)

    def add(self, alert: Alert) -> None:
        with self._lock:
            self._alerts.append(alert)
            self._save()

    def delete(self, alert_id: str) -> bool:
        with self._lock:
            kept = [a for a in self._alerts if a.id != alert_id]
            if len(kept) == len(self._alerts):
                return False
            self._alerts = kept
            self._save()
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
