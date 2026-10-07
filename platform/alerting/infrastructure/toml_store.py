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
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
    fields, frozen, with Story 33.8's `condition` table and `invalid_reason` appended. Every loaded
    condition is validated (`Alert.__post_init__`): a bad one raises naming the entry, never
    dropped.
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
        alerts = []
        for index, entry in enumerate(raw.get("alerts", [])):
            try:
                # `Alert.__post_init__` validates the condition and its level mirror (Story 33.8).
                # A non-level kind stores no `level` (TOML has no null): it reads back as None.
                alerts.append(Alert(**{"level": None, **entry}))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"{self._path}: alerts[{index}] is not a valid alert: {exc}"
                ) from exc
        return alerts

    def _save(self) -> None:
        # TOML has no null: drop None fields (recursively: a condition's absent optional field),
        # Alert's defaults and the condition's normalisation restore them on load.
        entries = [_without_none(asdict(a)) for a in self._alerts]
        with self._path.open("wb") as f:
            tomli_w.dump({"alerts": entries}, f)

    def _persist_or_ledger(self, alert: Alert, what: str) -> None:
        try:
            self._save()
        except OSError as exc:
            # The in-memory state still holds this run; a failed save must never swallow the
            # change itself -- but it is ledgered, not just logged (DATA-07): a lost `triggered`
            # flag would let an only_once alert refire after a restart, a lost `invalid_reason`
            # would evaluate a dead alert again.
            error_ledger.record(
                "alerting.store.persist", f"alert {alert.id} {what} but was not persisted", exc
            )

    def list(self) -> list[Alert]:
        with self._lock:
            return list(self._alerts)

    def add(self, alert: Alert) -> None:
        with self._lock:
            self._alerts.append(alert)
            self._save()

    def update(self, alert_id: str, edit: Callable[[Alert], Alert]) -> Alert | None:
        with self._lock:
            index = self._index(alert_id)
            if index is None:
                return None
            previous = self._alerts[index]
            edited = edit(previous)  # raises before anything changes
            self._alerts[index] = edited
            try:
                self._save()
            except Exception:
                self._alerts[index] = previous  # not acknowledged: the stored alert stands
                raise
            return edited

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
            stored = self._stored(alert.id)
            if stored is not None and stored is not alert:
                stored.last_fired_ns = ts_ns  # an edit replaced it while it fired
            self._persist_or_ledger(alert, "fired")

    def mark_invalid(self, alert: Alert, reason: str) -> bool:
        with self._lock:
            alert.invalid_reason = reason
            if self._stored(alert.id) is not alert:
                return False
            self._persist_or_ledger(alert, "was marked invalid")
            return True

    def _index(self, alert_id: str) -> int | None:
        return next((i for i, a in enumerate(self._alerts) if a.id == alert_id), None)

    def _stored(self, alert_id: str) -> Alert | None:
        index = self._index(alert_id)
        return None if index is None else self._alerts[index]


def _without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _without_none(v) for k, v in value.items() if v is not None}
    return value
