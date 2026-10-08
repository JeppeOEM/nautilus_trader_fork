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
Story 20.1: `GET`/`POST /api/alerts`, `DELETE /api/alerts/{id}` -- a thin adapter over
`alerting.application.service.AlertService` (Story 24.3), reached as
`data_api.alert_wiring.service`: this module validates the request, maps `NoDeliveryChannel` to 422
and formats the response. The request/response JSON and every `detail` string are frozen (AD-D12).
The write path is a sanctioned AD-F2 exception (config persistence), same category as
`PUT /api/coin/{iid}/indicators`. `status` (active/triggered/expired) is derived per request so
the Alerts list view (Story 20.3) needs no extra route.

Story 33.8 appended, keys only: a request's optional `condition` (the `Condition` union, one model
per kind, mirrored from `alerting.domain.conditions.CONDITION_FIELDS` and held to it by a test),
the response's `condition`, `condition_text` (`conditions.describe`, the one describer: the page
never composes its own) and `invalid_reason`, the status `invalid`, and `PUT /api/alerts/{id}`. A
request without `condition` behaves exactly as before; `level` with `condition` is a 422 unless the
condition is a price-level kind at an equal level. The domain validator decides every field and
names it in the 422 `detail` (`condition.upper must be greater than lower`); the route also checks
an `indicator` against the picker's catalog (`check_params`, `check_source`, its `outputs`) and a
`trendline_cross`'s drawing against the coin's `chart_drawings.toml`.
"""

import math
import time
import tomllib
from pathlib import Path
from typing import Annotated
from typing import Any
from typing import Literal
from urllib.parse import urlparse

from alerting.application.service import NoDeliveryChannel
from alerting.domain.alert import Alert
from alerting.domain.alert import status_of
from alerting.domain.conditions import ConditionError
from alerting.domain.conditions import describe
from alerting.domain.conditions import resolve_condition
from alerting.domain.geometry import trendline_price_at
from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Response
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import field_validator
from views import indicator_picker
from views import preferences

from data_api import alert_wiring
from data_api import settings


router = APIRouter()

_MAX_BAR_SECONDS = 86_400
_MAX_TEMPLATE_LEN = 1_000
_Frequency = Literal["once_per_bar_close", "once_per_bar", "only_once"]


# --- the condition kinds: one model each, `kind` the discriminator --------------------------------
# Shapes only (they type the OpenAPI schema and the generated client); every range rule is the
# domain's `validate_condition`, applied once by the route, so the 422 names the field the same way
# for every writer.


class _ConditionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LevelCondition(_ConditionModel):
    kind: Literal["price_cross", "price_cross_up", "price_cross_down", "price_above", "price_below"]
    level: float


class PctMoveCondition(_ConditionModel):
    kind: Literal["pct_move"]
    pct: float
    bars: int


class ChannelExitCondition(_ConditionModel):
    kind: Literal["channel_exit"]
    upper: float
    lower: float


class IndicatorCondition(_ConditionModel):
    kind: Literal["indicator"]
    name: str
    params: dict[str, bool | int | float | str]
    source: str = "close"
    output: str
    op: Literal[">", "<", "crosses_up", "crosses_down"]
    value: float


class TrendlineCrossCondition(_ConditionModel):
    kind: Literal["trendline_cross"]
    drawing_id: str


class FundingCondition(_ConditionModel):
    kind: Literal["funding_above", "funding_below"]
    rate: float


class OiChangeCondition(_ConditionModel):
    kind: Literal["oi_change"]
    pct: float
    window_s: int


class LiquidationNotionalCondition(_ConditionModel):
    kind: Literal["liquidation_notional"]
    notional: float
    window_s: int
    side: Literal["long", "short"] | None = None


class ForcedShareCondition(_ConditionModel):
    kind: Literal["forced_share"]
    share: float
    window_s: int


CONDITION_MODELS: tuple[type[_ConditionModel], ...] = (
    LevelCondition,
    PctMoveCondition,
    ChannelExitCondition,
    IndicatorCondition,
    TrendlineCrossCondition,
    FundingCondition,
    OiChangeCondition,
    LiquidationNotionalCondition,
    ForcedShareCondition,
)

Condition = Annotated[
    LevelCondition
    | PctMoveCondition
    | ChannelExitCondition
    | IndicatorCondition
    | TrendlineCrossCondition
    | FundingCondition
    | OiChangeCondition
    | LiquidationNotionalCondition
    | ForcedShareCondition,
    Field(discriminator="kind"),
]


# --- the request and response bodies --------------------------------------------------------------


def _check_webhook_url(v: str) -> str:
    if not v:
        # Telegram-only alert; checked against the server config by AlertService.create/update.
        return v
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("webhook_url must be an http(s) URL")
    return v


def _check_template(v: str) -> str:
    if len(v) > _MAX_TEMPLATE_LEN:
        raise ValueError(f"template must be at most {_MAX_TEMPLATE_LEN} characters")
    return v


class AlertFields(BaseModel):
    """The pre-33.8 keys, in their frozen order (the request's and the response's prefix)."""

    instrument_id: str
    level: float | None = None
    frequency: _Frequency
    bar_seconds: int
    expires_at_ns: int | None = None
    template: str
    webhook_url: str

    @field_validator("level")
    @classmethod
    def _finite_level(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
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
        return _check_template(v)

    @field_validator("webhook_url")
    @classmethod
    def _looks_like_url(cls, v: str) -> str:
        return _check_webhook_url(v)


class AlertCreate(AlertFields):
    # Story 33.8: absent, `level` alone is a `price_cross` exactly as before.
    condition: Condition | None = None


class AlertUpdate(BaseModel):
    """
    `PUT /api/alerts/{id}`: the editable fields (the instrument and bar width are the alert's
    identity and stay). `rearm` clears a triggered `only_once` alert's `triggered`.
    """

    condition: Condition
    frequency: _Frequency
    expires_at_ns: int | None = None
    template: str
    webhook_url: str
    rearm: bool = False

    @field_validator("template")
    @classmethod
    def _template_len(cls, v: str) -> str:
        return _check_template(v)

    @field_validator("webhook_url")
    @classmethod
    def _looks_like_url(cls, v: str) -> str:
        return _check_webhook_url(v)


class AlertResponse(AlertFields):
    id: str
    status: Literal["active", "triggered", "expired", "invalid"]
    created_ns: int
    last_fired_ns: int | None = None
    condition: Condition
    condition_text: str
    invalid_reason: str | None = None


def _to_response(alert: Alert, now_ns: int) -> AlertResponse:
    return AlertResponse(
        **{k: v for k, v in alert.__dict__.items() if k != "triggered"},
        status=status_of(alert, now_ns),
        condition_text=describe(alert.rule, alert.bar_seconds),
    )


# --- the inputs a condition names: the picker's catalog, the coin's drawings ----------------------


def _unprocessable(field: str, reason: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"{field} {reason}")


def _check_indicator(condition: dict[str, Any]) -> None:
    """Refuse an indicator the chart could not replay (422 naming the condition field)."""
    name = condition["name"]
    entry = indicator_picker.merged_catalog().get(name)
    if entry is None:
        raise _unprocessable("condition.name", f"{name!r} is not an indicator of the catalog")
    try:
        indicator_picker.check_params(name, condition["params"])
    except ValueError as exc:
        raise _unprocessable("condition.params", str(exc)) from exc
    try:
        indicator_picker.check_source(name, condition["source"])
    except ValueError as exc:
        raise _unprocessable("condition.source", str(exc)) from exc
    if condition["output"] not in entry["outputs"]:
        raise _unprocessable("condition.output", f"must be one of {entry['outputs']}")


def _check_trendline(instrument_id: str, drawing_id: str) -> None:
    """Refuse a drawing id that is not a non-vertical trendline of this coin (422)."""
    try:
        drawings = preferences.load_chart_drawings(Path(settings.CHART_DRAWINGS_PATH))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, preferences.DrawingError) as exc:
        # The drawings route's rule (DATA-02): a corrupt file is the server's fault, seen loudly.
        raise HTTPException(
            status_code=500, detail=f"chart_drawings.toml is corrupt: {exc}"
        ) from exc
    item = next((d for d in drawings.get(instrument_id, []) if d["id"] == drawing_id), None)
    if item is None or item["kind"] != "trendline":
        raise _unprocessable(
            "condition.drawing_id", f"{drawing_id!r} is not a trendline of {instrument_id}"
        )
    if trendline_price_at(item["anchors"], 0) is None:
        raise _unprocessable("condition.drawing_id", f"{drawing_id!r} is vertical")


def _resolved(instrument_id: str, level: float | None, raw: BaseModel | None) -> dict[str, Any]:
    """Return the normalised condition, its fields and named inputs checked, else raise 422."""
    try:
        condition = resolve_condition(
            level, None if raw is None else raw.model_dump(exclude_none=True)
        )
    except ConditionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if condition["kind"] == "indicator":
        _check_indicator(condition)
    elif condition["kind"] == "trendline_cross":
        _check_trendline(instrument_id, condition["drawing_id"])
    return condition


# --- the routes -----------------------------------------------------------------------------------


@router.get("/api/alerts")
def list_alerts() -> list[AlertResponse]:
    now_ns = time.time_ns()
    return [_to_response(a, now_ns) for a in alert_wiring.service.list()]


@router.post("/api/alerts", status_code=201)
def create_alert(body: AlertCreate) -> AlertResponse:
    fields = body.model_dump(exclude={"level", "condition"})
    condition = _resolved(body.instrument_id, body.level, body.condition)
    try:
        alert = alert_wiring.service.create(**fields, condition=condition)
    except NoDeliveryChannel as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_response(alert, time.time_ns())


@router.put("/api/alerts/{alert_id}")
def update_alert(alert_id: str, body: AlertUpdate) -> AlertResponse:
    current = alert_wiring.service.get(alert_id)
    if current is None:
        raise HTTPException(status_code=404, detail="alert not found")
    condition = _resolved(current.instrument_id, None, body.condition)
    try:
        alert = alert_wiring.service.update(
            alert_id, **body.model_dump(exclude={"condition"}), condition=condition
        )
    except NoDeliveryChannel as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")  # deleted meanwhile
    return _to_response(alert, time.time_ns())


@router.delete("/api/alerts/{alert_id}", status_code=204)
def delete_alert(alert_id: str) -> Response:
    if not alert_wiring.service.delete(alert_id):
        raise HTTPException(status_code=404, detail="alert not found")
    return Response(status_code=204)
