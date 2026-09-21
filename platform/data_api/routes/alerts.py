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
"""
Story 20.1: `GET`/`POST /api/alerts`, `DELETE /api/alerts/{id}` -- CRUD over `data_api.alerts`'
store. The write path is a sanctioned AD-F2 exception (config persistence), same category as
`PUT /api/coin/{iid}/indicators`. `status` (active/triggered/expired) is derived per request so
the Alerts list view (Story 20.3) needs no extra route.
"""

import math
import time
from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Response
from pydantic import BaseModel
from pydantic import field_validator

from data_api import alerts


router = APIRouter()

_MAX_BAR_SECONDS = 86_400
_MAX_TEMPLATE_LEN = 1_000


class AlertCreate(BaseModel):
    instrument_id: str
    level: float
    frequency: Literal["once_per_bar_close", "once_per_bar", "only_once"]
    bar_seconds: int
    expires_at_ns: int | None = None
    template: str
    webhook_url: str

    @field_validator("level")
    @classmethod
    def _finite_level(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("level must be finite")
        return v

    @field_validator("bar_seconds")
    @classmethod
    def _bar_seconds_range(cls, v: int) -> int:
        if not 0 < v <= _MAX_BAR_SECONDS:
            raise ValueError(f"bar_seconds must be in 1..{_MAX_BAR_SECONDS}")
        return v

    @field_validator("instrument_id")
    @classmethod
    def _instrument_id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instrument_id must not be empty")
        return v

    @field_validator("template")
    @classmethod
    def _template_len(cls, v: str) -> str:
        if len(v) > _MAX_TEMPLATE_LEN:
            raise ValueError(f"template must be at most {_MAX_TEMPLATE_LEN} characters")
        return v

    @field_validator("webhook_url")
    @classmethod
    def _looks_like_url(cls, v: str) -> str:
        if not v:
            return v  # Telegram-only alert; checked against the server config in create_alert
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("webhook_url must be an http(s) URL")
        return v


class AlertResponse(AlertCreate):
    id: str
    status: Literal["active", "triggered", "expired"]
    created_ns: int
    last_fired_ns: int | None = None


def _to_response(alert: alerts.Alert, now_ns: int) -> AlertResponse:
    return AlertResponse(
        **{k: v for k, v in alert.__dict__.items() if k != "triggered"},
        status=alerts.status_of(alert, now_ns),
    )


@router.get("/api/alerts")
def list_alerts() -> list[AlertResponse]:
    now_ns = time.time_ns()
    return [_to_response(a, now_ns) for a in alerts.store.list()]


@router.post("/api/alerts", status_code=201)
def create_alert(body: AlertCreate) -> AlertResponse:
    if not body.webhook_url and not alerts.telegram_configured():
        raise HTTPException(
            status_code=422,
            detail="no delivery channel: set a webhook URL or configure TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID",
        )
    alert = alerts.new_alert(**body.model_dump())
    alerts.store.add(alert)
    return _to_response(alert, time.time_ns())


@router.delete("/api/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: str) -> Response:
    if not alerts.store.delete(alert_id):
        raise HTTPException(status_code=404, detail="alert not found")
    alerts.engine.forget(alert_id)
    return Response(status_code=204)
