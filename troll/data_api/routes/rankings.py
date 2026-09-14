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
`GET /api/rankings` -- verbatim passthrough of `ranking_engine`'s cached `rankings:live`
message (AD-F2: never recompute rank/volatility), with exactly one sanctioned reshape:
`ranks` -> `items` (epics AC1's literal `{"items": [...], "updated_at": ...}` shape).
Every other field (`mode`, `stale_instrument_ids`, and every field inside each ranking
entry) passes through byte-for-byte from the cached Redis payload.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from data_api import redis_bus


router = APIRouter()


class RankingsResponse(BaseModel):
    items: list[dict]
    updated_at: int
    mode: str
    stale_instrument_ids: list[str]


@router.get("/api/rankings")
def get_rankings() -> RankingsResponse:
    """
    503 before the bus has cached its first message -- an honest transient state, not a
    fabricated empty snapshot (I/O matrix). Referencing `redis_bus.bus` via the module
    (not importing the `bus` name directly) so tests can `monkeypatch.setattr(redis_bus,
    "bus", RankingsBus())` to exercise this route against an isolated cache.
    """
    latest = redis_bus.bus.latest
    if latest is None:
        raise HTTPException(status_code=503, detail="Rankings not yet available")
    return RankingsResponse(
        items=latest["ranks"],
        updated_at=latest["updated_at"],
        mode=latest["mode"],
        stale_instrument_ids=latest.get("stale_instrument_ids", []),
    )
