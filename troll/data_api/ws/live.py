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
`/ws/live` -- relays every `rankings:live` message completely verbatim (including the
raw `ranks` key -- epics AC2: "no reshaping beyond channel subscription"). Unlike
`GET /api/rankings`, this endpoint does NOT rename `ranks` to `items`.

On connect, immediately sends the bus's cached `latest` message (if any) before waiting
on the listener queue, so a new client doesn't wait a full heartbeat for its first paint
(I/O matrix: "New WS client connects mid-stream").
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from data_api import redis_bus


logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    # Subscribe before reading/sending `latest`: a message published between reading
    # `.latest` and registering the listener queue would otherwise be missed entirely
    # for this connection (a narrow but real race). Subscribing first means that message
    # instead arrives twice (via the initial send below and the queue) -- harmless for a
    # full-snapshot relay, unlike a silent drop.
    queue = redis_bus.bus.subscribe()
    try:
        if redis_bus.bus.latest is not None:
            await websocket.send_json(redis_bus.bus.latest)
        while True:
            message = await queue.get()
            await websocket.send_json(message)
    except WebSocketDisconnect:
        logger.info("/ws/live client disconnected")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # A network-level drop (e.g. connection reset mid-send) can raise something
        # other than WebSocketDisconnect depending on the ASGI server -- treat any of
        # these as a disconnect rather than crashing the route.
        logger.info("/ws/live connection closed: %s", exc)
    finally:
        redis_bus.bus.unsubscribe(queue)
