# Phase 3: Collector/Dashboard Split - Research

**Researched:** 2026-06-29
**Domain:** Redis pub/sub, aiohttp SSE, asyncio fan-out, Docker bridge networking
**Confidence:** MEDIUM

## Summary

Phase 3 decouples the dashboard from the collector by inserting Redis as a message bus. The
collector publishes a JSON snapshot batch to channel `snapshots:1s` once per second; the
dashboard subscribes, updates its in-memory metric state, and pushes computed rankings to
browser clients via Server-Sent Events (SSE). This eliminates the shared-process coupling
(`serve_in_background` call, shared `_second_rolling` deque) that ties the dashboard threads
to the collector's GIL and asyncio loop.

The standard Python async Redis client is `redis.asyncio` (ships inside the `redis` package —
`aioredis` was deprecated when merged into redis-py at v4.2). The aiohttp SSE implementation
does not need an extra library: a raw `web.StreamResponse` with `Content-Type: text/event-stream`
plus a per-client `asyncio.Queue` for fan-out is ~25 lines and correctly handles disconnect
detection and slow-client backpressure.

The Docker networking change is surgical: define one named bridge network (`troll_net`), move
all three services to it, drop `network_mode: host` from the collector. The collector only
makes outbound connections to dYdX so bridge mode is functionally identical to host mode.

**Primary recommendation:** `redis.asyncio` for pub/sub + raw `aiohttp.web.StreamResponse` for
SSE + named bridge network for all services.

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Snapshot sampling (1s loop) | Collector | — | Owns the book state and rolling counters |
| Serialization to Redis | Collector | — | Publisher must own the message format |
| Metric computation (OFI/OBI/microprice) | Dashboard | — | Stateful indicators live in dashboard process |
| SSE fan-out to browser | Dashboard | — | HTTP server lives in dashboard only |
| Slow-path Parquet reads (60s) | Dashboard | — | price/pct/vol data from catalog |
| Redis message bus | Redis service | — | No-persistence in-memory relay only |

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `redis` | 8.0.1 | `redis.asyncio` pub/sub client | Official redis-py; `aioredis` merged here at v4.2 [VERIFIED: PyPI registry] |
| `aiohttp` | 3.14.1 | Async HTTP server + SSE responses | Only production-grade Python async web framework that ships its own event loop integration [VERIFIED: PyPI registry] |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `redis:7-alpine` | Docker image | Redis server | Smallest image (~10 MB), no persistence needed here |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `redis.asyncio` | `aioredis` | aioredis is unmaintained (archived 2022); redis-py is the canonical replacement |
| Raw `StreamResponse` | `aiohttp-sse` (2.2.0) | aiohttp-sse adds automatic keepalive ping (needed behind nginx/proxies) and clean `is_connected()` check; ponytail prefers zero extra dep when the raw impl is trivial |
| Named bridge network | `network_mode: host` for all | Host mode leaks all container ports to the host; bridge is more isolated |

**Installation (add to `troll/troll-requirements.txt`):**
```
redis>=8.0.1
aiohttp>=3.14.1
```

**Version verification:**
```bash
pip index versions redis    # 8.0.1 confirmed [VERIFIED: PyPI registry, 2026-06-29]
pip index versions aiohttp  # 3.14.1 confirmed [VERIFIED: PyPI registry, 2026-06-29]
```

## Package Legitimacy Audit

| Package | Registry | Repo | Verdict | Disposition |
|---------|----------|------|---------|-------------|
| `redis` 8.0.1 | PyPI | github.com/redis/redis-py | SUS (download count unavailable; tool limitation) | Approved — official redis org, 8+ years on PyPI |
| `aiohttp` 3.14.1 | PyPI | github.com/aio-libs/aiohttp | SUS (download count unavailable; tool limitation) | Approved — top-5 Python async library, aio-libs org |

**Packages removed due to SLOP verdict:** none

**Packages flagged SUS:** both flagged due to PyPI download-stats being unavailable to the
seam (not a reflection of legitimacy). Both have authoritative GitHub repos from their
official maintainer orgs. No `postinstall` scripts. Safe to add.

## Architecture Patterns

### System Architecture Diagram

```
dYdX WebSocket/REST
        |
        v
  [Collector process]          [Dashboard process]
  asyncio loop                 aiohttp web server
  ├─ _second_loop              ├─ cleanup_ctx: redis_subscriber_ctx
  │   └─ builds DydxSecondSnapshot  │   └─ subscribes to snapshots:1s
  │   └─ PUBLISH snapshots:1s ──────┤   └─ feeds _OFI_INDS, updates _LIVE
  ├─ _flush_loop               │   └─ puts rankings JSON into SSE queues
  │   └─ writes to Parquet     ├─ cleanup_ctx: slow_loop_ctx
  └─ (no more dashboard code)  │   └─ reads Parquet every 60s
                               ├─ GET /           → rankings HTML
                               ├─ GET /data/stream → SSE endpoint
                               │      EventSource  ←── browser
                               ├─ GET /coin/{id}
                               ├─ GET /chart/{id}
                               └─ GET /history/{id}

     [Redis 7-alpine]
     channel: snapshots:1s
     no persistence, pure relay
```

### Recommended Project Structure

```
troll/
├─ dydx_collector/
│   ├─ collector.py       # remove serve_in_background; add _publish_snapshots
│   ├─ redis_pub.py       # thin wrapper: get_redis_client(), publish_snapshot_batch()
│   └─ config.py          # add redis_url field
├─ ml_signals/
│   ├─ dashboard.py       # rewrite: aiohttp app, Redis subscriber, SSE handler
│   └─ dashboard_main.py  # new entrypoint: python -m ml_signals.dashboard_main
├─ docker-compose.yml     # add redis + dashboard services; named bridge network
└─ troll-requirements.txt # add redis, aiohttp
```

### Pattern 1: Redis Pub/Sub — Publisher (Collector Side)

**What:** Publish one JSON message per second containing all active instrument snapshots.
**When to use:** At the end of `_second_loop`, after appending each snapshot to the local deque (which can now be removed) and calling `_on_data` for Parquet write.

```python
# Source: redis.io/docs/latest/develop/clients/redis-py/async/
import json
import redis.asyncio as aioredis

async def _publish_snapshot_batch(
    redis_client: aioredis.Redis,
    snapshots: list[dict],
) -> None:
    """Publish all 1s snapshots for this tick as one JSON message."""
    if not snapshots:
        return
    payload = json.dumps(snapshots)
    await redis_client.publish("snapshots:1s", payload)
```

Collector holds one persistent `aioredis.Redis` instance (created in `run()`, closed in
`finally`). On connection failure, `redis.asyncio` raises `ConnectionError` — catch it in
`_second_loop` and log; missing one tick's publish is acceptable.

### Pattern 2: Redis Pub/Sub — Subscriber (Dashboard Side)

**What:** Background coroutine that drives the dashboard's in-memory state from Redis messages.
**When to use:** Registered via `cleanup_ctx` so aiohttp starts it on startup and cancels on shutdown.

```python
# Source: redis.io/docs/latest/develop/clients/redis-py/async/
import contextlib
import json
import asyncio
import redis.asyncio as aioredis
from aiohttp import web

@contextlib.asynccontextmanager
async def redis_subscriber_ctx(app: web.Application):
    task = asyncio.create_task(_redis_listener(app))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

async def _redis_listener(app: web.Application) -> None:
    redis_url = app["redis_url"]  # e.g. "redis://redis:6379"
    async with aioredis.Redis.from_url(redis_url, decode_responses=True) as r:
        async with r.pubsub() as pubsub:
            await pubsub.subscribe("snapshots:1s")
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    batch: list[dict] = json.loads(message["data"])
                    _ingest_batch(batch)      # update _OFI_INDS, _LIVE
                    _fanout_to_sse_clients()  # put_nowait into each SSE queue
                except Exception:
                    logger.exception("Failed to process snapshot batch")
```

### Pattern 3: aiohttp SSE — Fan-out via Per-Client Queue

**What:** SSE handler that holds open the HTTP connection and drains a per-client `asyncio.Queue`.
**When to use:** `/data/stream` route; browser connects with `new EventSource('/data/stream')`.

```python
# Source: docs.aiohttp.org/en/stable/web_reference.html (StreamResponse)
import asyncio
from aiohttp import web

_SSE_QUEUES: set[asyncio.Queue[bytes]] = set()

async def sse_handler(request: web.Request) -> web.StreamResponse:
    """Hold SSE connection open; drain queue; clean up on disconnect."""
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
    _SSE_QUEUES.add(q)
    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"   # disable nginx response buffering
    await resp.prepare(request)
    try:
        while True:
            payload = await asyncio.wait_for(q.get(), timeout=15.0)
            await resp.write(payload)
    except asyncio.TimeoutError:
        # send keepalive comment so proxy/browser doesn't close idle connection
        try:
            await resp.write(b": keepalive\n\n")
        except (ConnectionResetError, ConnectionAbortedError):
            pass
    except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
        pass  # client disconnected
    finally:
        _SSE_QUEUES.discard(q)
    return resp

def _fanout_to_sse_clients() -> None:
    """Non-blocking push to all connected SSE queues; drop if queue is full."""
    payload = _rankings_sse_payload()  # b"data: {...}\n\n"
    for q in list(_SSE_QUEUES):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # slow client — skip this tick, next tick overwrites
```

The `maxsize=2` buffer means a slow client gets at most 2 queued updates before new ones are
dropped. For a live dashboard this is correct: fresh data beats completeness.

### Pattern 4: aiohttp App Wiring

```python
# Source: docs.aiohttp.org/en/stable/web_advanced.html (cleanup_ctx)
import contextlib
import asyncio
from aiohttp import web

@contextlib.asynccontextmanager
async def slow_loop_ctx(app: web.Application):
    task = asyncio.create_task(_slow_loop(app["catalog_path"]))
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

def make_app(redis_url: str, catalog_path: str) -> web.Application:
    app = web.Application()
    app["redis_url"] = redis_url
    app["catalog_path"] = catalog_path

    app.cleanup_ctx.append(redis_subscriber_ctx)
    app.cleanup_ctx.append(slow_loop_ctx)

    app.router.add_get("/", rankings_handler)
    app.router.add_get("/data/stream", sse_handler)
    app.router.add_get("/coin/{id}", coin_handler)
    app.router.add_get("/chart/{id}", chart_handler)
    app.router.add_get("/history/{id}", history_handler)
    app.router.add_get("/data/coin/{id}", coin_json_handler)
    return app

if __name__ == "__main__":
    import os
    app = make_app(
        redis_url=os.environ.get("REDIS_URL", "redis://localhost:6379"),
        catalog_path=os.environ.get("CATALOG_PATH", "/app/catalog"),
    )
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("DASHBOARD_PORT", 8765)))
```

`web.run_app` handles `SIGTERM`/`SIGINT` and calls the cleanup_ctx teardown in order.

### Pattern 5: Snapshot JSON Schema

The collector serializes each snapshot using `DydxSecondSnapshot.to_dict()` (which already
exists). The published message is a JSON array of snapshot dicts, one per active instrument.

```python
# Collector side — in _second_loop, after building all snapshots
batch = []
for iid in self._pinned | self._liquid:
    # ... build snapshot as before ...
    self._on_data(snapshot)              # Parquet write path unchanged
    batch.append(DydxSecondSnapshot.to_dict(snapshot))

await _publish_snapshot_batch(self._redis, batch)
```

**Wire JSON schema (one element per instrument per second):**
```json
[
  {
    "instrument_id": "BTC-USD-PERP.DYDX",
    "bid_prices": [50000.0, 49999.5, 49998.0],
    "bid_sizes": [1.5, 2.0, 3.0],
    "ask_prices": [50001.0, 50001.5, 50002.0],
    "ask_sizes": [0.8, 1.2, 2.5],
    "buy_volume": 10.5,
    "sell_volume": 8.2,
    "buy_count": 15,
    "sell_count": 12,
    "ts_event": 1751226000000000000,
    "ts_init": 1751226000000000000
  }
]
```

All fields match `DydxSecondSnapshot` exactly. The dashboard reconstructs a snapshot-like
object (or just uses the dict directly) to feed `_OFI_INDS` and compute metrics.
`instrument_id` is already a plain string in `to_dict()` — no Nautilus type needed on the
dashboard side.

### Pattern 6: Docker Compose Networking

**Replace `network_mode: host` with a named bridge network.** The collector only makes
outbound TCP connections to dYdX APIs — bridge networking handles outbound connections
identically to host mode. [ASSUMED — host mode was added for convenience, not a hard
requirement of the dYdX Rust client]

```yaml
# troll/docker-compose.yml
networks:
  troll_net:
    driver: bridge

services:
  redis:
    image: redis:7-alpine
    container_name: dydx-redis
    networks: [troll_net]
    restart: unless-stopped
    # No ports: — Redis is internal; no external exposure needed.
    # Add ports: ["127.0.0.1:6379:6379"] only for local dev/debugging.

  collector:
    container_name: dydx-collector
    build:
      context: ..
      dockerfile: troll/collector.dockerfile
      network: host      # build-time still uses host (no change)
    environment:
      PYTHONUNBUFFERED: "1"
      REDIS_URL: "redis://redis:6379"
    user: "1000:1000"
    volumes:
      - ./config.toml:/app/dydx_collector/config.toml:ro
      - ./dydx_collector/catalog:/app/catalog
      - ./dydx_collector/metrics.db:/app/metrics.db
    networks: [troll_net]
    restart: unless-stopped
    # network_mode: host removed — outbound-only dYdX connections work fine on bridge

  dashboard:
    container_name: dydx-dashboard
    build:
      context: ..
      dockerfile: troll/collector.dockerfile   # same image, different CMD
    command: ["python3", "-m", "ml_signals.dashboard_main"]
    environment:
      PYTHONUNBUFFERED: "1"
      REDIS_URL: "redis://redis:6379"
      CATALOG_PATH: "/app/catalog"
      DASHBOARD_PORT: "8765"
    user: "1000:1000"
    volumes:
      - ./dydx_collector/catalog:/app/catalog:ro  # read-only; only collector writes
      - ./dydx_collector/metrics.db:/app/metrics.db
    ports:
      - "127.0.0.1:8765:8765"
    networks: [troll_net]
    restart: unless-stopped

  dozzle:
    container_name: dydx-collector-dozzle
    image: amir20/dozzle:latest
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    ports:
      - "127.0.0.1:8080:8080"
    restart: unless-stopped
```

Note: the dashboard can reuse the same Docker image as the collector (it already has all
dependencies), just with a different `command`. No second Dockerfile needed.

### Pattern 7: Browser EventSource (JS Change)

Replace the `setInterval` polling block in the rankings page:

```javascript
// BEFORE (polling)
setInterval(function(){
  fetch('/data/rankings').then(r => r.json()).then(updateCells);
}, 1000);

// AFTER (SSE EventSource)
(function(){
  var es = new EventSource('/data/stream');
  es.onmessage = function(e){
    var rows = JSON.parse(e.data);
    rows.forEach(function(row){
      // update cells — same logic as before
    });
  };
  es.onerror = function(){
    // browser auto-reconnects; no action needed
  };
})();
```

`EventSource` auto-reconnects on connection drop with ~3s backoff. No custom reconnect logic
needed.

### Anti-Patterns to Avoid

- **Sharing a PubSub object across asyncio tasks:** `redis.asyncio` explicitly documents that
  a single PubSub is not task-safe. Dashboard uses one PubSub in one dedicated listener task.
- **Writing to SSE clients directly from the subscriber task:** This blocks the subscriber if
  a client is slow. The queue-per-client pattern decouples them.
- **`asyncio.create_task()` without awaiting:** The aiohttp docs flag this as hiding
  exceptions. Always register long-running tasks via `cleanup_ctx` so they're tracked and
  cancelled cleanly.
- **`network_mode: host` for dashboard:** The dashboard exposes an HTTP port, so it needs
  port mapping (`ports:` key) which is incompatible with host mode in docker-compose.
- **Making `_OFI_INDS` shared state between the Redis listener task and HTTP handlers without
  a lock:** The listener task writes `_OFI_INDS` and `_LIVE`; HTTP handlers read `_LIVE`. Use
  `asyncio.Lock` (not `threading.Lock`) since everything is now in one asyncio process.
  Actually, asyncio is single-threaded — a `threading.Lock` is wrong here and a plain
  `asyncio.Lock` is not needed either unless you `await` inside the critical section. Since
  `_LIVE` updates are a simple dict assignment (atomic in CPython), no lock is needed.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Async Redis connection | Custom TCP socket to Redis | `redis.asyncio.Redis` | Connection pooling, reconnect, protocol parsing |
| SSE keepalive | Custom sleep-ping loop | `asyncio.wait_for(q.get(), timeout=15)` → write `b": keepalive\n\n"` | One idiom covers both data push and keepalive |
| SSE client tracking | Global list of response objects | Per-client `asyncio.Queue` + set | Decouples slow clients from publisher; `put_nowait` drops stale data |
| Redis reconnect on crash | Retry loop in subscriber | Let `redis.asyncio` handle it — the `async for` loop raises on disconnect; restart the task in a retry wrapper | Redis-py has built-in retry/backoff |
| Custom Docker networking | Manual IP addresses in config | Named bridge network + service-name DNS | Service names resolve automatically; no hardcoded IPs |

**Key insight:** The fan-out pattern (queue per SSE client) is the single most important
architectural decision here. Without it, one slow browser tab blocks the Redis subscriber
from consuming the next message, cascading into backpressure against the collector's publish.

## Common Pitfalls

### Pitfall 1: `redis.asyncio` vs `aioredis`

**What goes wrong:** Importing `aioredis` fails (`ModuleNotFoundError`) or uses a deprecated
API with silent differences.
**Why it happens:** `aioredis` was a separate package until merged into redis-py at v4.2. Both
package names exist on PyPI; `aioredis` is now a stub that warns about deprecation.
**How to avoid:** Always `import redis.asyncio as aioredis` (or just `redis.asyncio`). Never
add `aioredis` to requirements.txt.
**Warning signs:** `pip show aioredis` succeeds but the README says "deprecated".

### Pitfall 2: `network_mode: host` vs bridge — service DNS

**What goes wrong:** Collector can't connect to Redis at `redis:6379` because host-mode
containers cannot use Docker's internal DNS.
**Why it happens:** `network_mode: host` bypasses all Docker networking, including DNS for
container service names.
**How to avoid:** Remove `network_mode: host` from collector in docker-compose.yml; use the
named bridge network instead.
**Warning signs:** `redis.exceptions.ConnectionError: Error 111 connecting to redis:6379`
in collector logs.

### Pitfall 3: SSE Fan-out Blocking the Redis Subscriber

**What goes wrong:** One slow/hung browser client causes the Redis subscriber to stop
consuming messages, which eventually fills Redis's client output buffer and drops the
subscriber connection.
**Why it happens:** Writing directly to `web.StreamResponse` inside the subscriber task
blocks awaiting the slow client's TCP send buffer.
**How to avoid:** Per-client `asyncio.Queue` with `put_nowait` (see Pattern 3). Never `await`
a write to a client response inside the subscriber task.
**Warning signs:** Rankings page freezes for all clients when one tab is left open in
background.

### Pitfall 4: Missing `asyncio.Lock` vs Unnecessary Lock

**What goes wrong:** Either a race condition on `_LIVE` (reads old data) or a deadlock from
using `threading.Lock` in an asyncio context.
**Why it happens:** The dashboard is now fully asyncio — no threads. `threading.Lock` acquired
inside `async def` will not release until the `async def` returns (blocking the event loop).
**How to avoid:** For `_LIVE` (plain dict updates), no lock is needed in CPython's asyncio
single-threaded model. If the slow-loop uses `asyncio.to_thread()` for Parquet reads, any
shared state it writes needs `asyncio.Lock`.
**Warning signs:** Deadlock during slow-loop Parquet read; stale `_LIVE` values visible only
during Parquet read windows.

### Pitfall 5: OFI Indicator State on Collector Restart

**What goes wrong:** After the collector restarts (e.g., reconnect to dYdX), the dashboard's
`_OFI_INDS` have stale `_prev` state that generates a spurious large OFI spike.
**Why it happens:** The collector's `_second_loop` already handles the >3s gap detection and
calls `clear_prev_state()`. After the split, the dashboard needs to replicate this gap
detection from `ts_event` values in the received snapshots.
**How to avoid:** In `_ingest_batch()`, track `_LAST_FED` per instrument. If the gap since the
last received snapshot exceeds 3s, call `clear_prev_state()` on `_OFI_INDS[iid]` — same
logic as `_metrics_from_rolling()` in the current dashboard.
**Warning signs:** OFI spikes to extreme values (thousands) immediately after a collector
restart.

### Pitfall 6: Dashboard Slow-Loop Reads Parquet While Collector Writes

**What goes wrong:** `metrics_computer.compute_all()` reads Parquet files that the collector's
flush loop is simultaneously writing → Arrow reader sees a partial file → exception.
**Why it happens:** Parquet files are not transactional across processes.
**How to avoid:** The catalog uses append-only `write_data()` calls. The dashboard only reads
completed row groups. Arrow's Parquet reader is safe for concurrent access as long as neither
side truncates a file. The current design already handles this (it was the same issue in the
embedded mode). No change needed — just confirm the slow-loop wraps its catalog reads in
try/except as it already does.
**Warning signs:** Sporadic `ArrowInvalid: Parquet file size is 0 bytes` errors in dashboard
logs.

### Pitfall 7: Redis `decode_responses=True` vs bytes

**What goes wrong:** Dashboard receives `bytes` instead of `str` for `message["data"]`,
causing `json.loads()` to fail with `TypeError`.
**Why it happens:** `redis.asyncio.Redis` returns bytes by default unless `decode_responses=True`.
**How to avoid:** Create the Redis client with `decode_responses=True` in the dashboard
subscriber. The collector can use either (it publishes `str` via `json.dumps()` which
redis-py accepts either way).
**Warning signs:** `TypeError: the JSON object must be str, bytes or bytearray, not NoneType`
or `json.decoder.JSONDecodeError` in the subscriber task.

## Code Examples

### Full Redis Subscriber Loop with Reconnect

```python
# Source: redis.io/docs/latest/develop/clients/redis-py/async/
import asyncio
import json
import logging
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

async def _redis_listener(redis_url: str) -> None:
    """Reconnecting Redis subscriber — runs until CancelledError."""
    while True:
        try:
            async with aioredis.Redis.from_url(
                redis_url, decode_responses=True
            ) as r:
                async with r.pubsub() as pubsub:
                    await pubsub.subscribe("snapshots:1s")
                    logger.info("Redis subscriber connected to %s", redis_url)
                    async for message in pubsub.listen():
                        if message["type"] != "message":
                            continue
                        batch: list[dict] = json.loads(message["data"])
                        _ingest_batch(batch)
                        _fanout_to_sse_clients()
        except asyncio.CancelledError:
            raise  # propagate cancellation
        except Exception:
            logger.exception("Redis subscriber error — reconnecting in 2s")
            await asyncio.sleep(2)
```

### Minimal SSE Endpoint (No Extra Library)

```python
# Source: docs.aiohttp.org/en/stable/web_reference.html
import asyncio
from aiohttp import web

_SSE_QUEUES: set[asyncio.Queue[bytes]] = set()

async def sse_handler(request: web.Request) -> web.StreamResponse:
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=2)
    _SSE_QUEUES.add(q)
    resp = web.StreamResponse()
    resp.headers["Content-Type"] = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    await resp.prepare(request)
    try:
        while True:
            try:
                payload = await asyncio.wait_for(q.get(), timeout=15.0)
                await resp.write(payload)
            except asyncio.TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError):
        pass
    finally:
        _SSE_QUEUES.discard(q)
    return resp

def _sse_push(rankings_json: str) -> None:
    """Call from within the asyncio event loop — puts to all client queues."""
    payload = b"data: " + rankings_json.encode() + b"\n\n"
    for q in list(_SSE_QUEUES):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass  # drop for slow clients
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `aioredis` (separate package) | `redis.asyncio` (in `redis` package) | redis-py 4.2, 2022 | aioredis archived; don't add it as a dependency |
| SSE via `http.server` threads | `aiohttp` StreamResponse | Phase 3 | Removes GIL pressure from dashboard threads |
| Shared `_second_rolling` deque | Redis pub/sub | Phase 3 | Processes fully decoupled |
| `setInterval` fetch polling | `EventSource` SSE | Phase 3 | Sub-100ms latency vs ~1s polling cycle |

**Deprecated/outdated:**

- `aioredis`: Do not use. Archived 2022, replaced by `redis.asyncio`.
- `http.server.HTTPServer` in a daemon thread: replaced by `aiohttp.web.run_app`.
- `threading.Thread` for fast_loop/slow_loop: replaced by asyncio tasks in `cleanup_ctx`.
- `network_mode: host` for collector: remove — bridge networking handles all outbound connections.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `network_mode: host` was added for convenience, not a hard requirement of the dYdX Rust PyO3 client | Docker Networking | If the Rust client has a hard dependency on host networking (e.g. raw socket binding), moving to bridge breaks the collector. Verify by running the collector on the bridge network and checking dYdX connect logs. |
| A2 | The dashboard image can reuse the collector Dockerfile with a different CMD | Docker Compose | True if both need the same Python deps. If dashboard-only deps (e.g. aiohttp) bloat the collector image, a second Dockerfile may be warranted. |
| A3 | One Redis message per second per batch (all instruments in one JSON array) is sufficient throughput | Snapshot serialization | At 100 active instruments with 20 bid/ask levels each, one message ≈ 100 × (20+20) × 2 floats × 8 bytes ≈ ~130 KB. This is well within Redis pub/sub limits (default 1 MB client buffer). |

## Open Questions (RESOLVED)

1. **Does the dYdX Rust/PyO3 client require `network_mode: host`?**
   - RESOLVED: Plan retains `network_mode: host` for the collector as the conservative default (Assumption A1 per Assumptions Log). Switching to bridge is deferred until A1 is verified by a production test run. Dashboard also uses host mode so it can bind port 8765 directly without a ports: mapping conflict.

2. **Should `/data/coin/{id}` (live per-coin chart data) also use SSE or stay as XHR polling?**
   - RESOLVED: `/data/coin/{id}` stays as XHR polling per YAGNI. Rankings SSE is the high-frequency path; per-coin chart data is low-frequency enough that 1s XHR is fine.

3. **Redis `maxmemory` and client buffer limits with 100+ instruments at 1 Hz?**
   - RESOLVED: Not an issue at this throughput. One message ≈ 50–130 KB/s with 1 subscriber is well within Redis defaults (32 MB hard limit). No custom Redis config needed.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Docker | Container services | Yes | 29.4.3 | — |
| Docker Compose | Multi-service orchestration | Yes | v5.1.3 | — |
| `redis` (PyPI) | Collector + dashboard | Not installed locally | 8.0.1 on PyPI | — |
| `aiohttp` (PyPI) | Dashboard | Not installed locally | 3.14.1 on PyPI | — |
| Redis server | Message bus | Not running locally | `redis:7-alpine` via Docker | — |
| Python 3.13 | Runtime | Yes | 3.13.13 | — |

**Missing dependencies with no fallback:** None — all dependencies are available via PyPI
and Docker Hub.

**Missing dependencies with fallback:** None blocking execution.

## Validation Architecture

> `workflow.nyquist_validation` key is absent from `.planning/config.json` — treated as enabled.

### Test Framework

| Property | Value |
|----------|-------|
| Framework | pytest (already in project) |
| Config file | none (discover by convention) |
| Quick run command | `pytest troll/dydx_collector/tests/ troll/ml_signals/tests/ -x -q` |
| Full suite command | `pytest troll/ -x -q` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ARCH-01 | Collector publishes JSON batch to Redis; no dashboard imports remain | unit | `pytest troll/dydx_collector/tests/test_redis_pub.py -x` | No — Wave 0 |
| ARCH-02 | Dashboard SSE pushes within 100ms of receiving Redis message | integration | Manual — start both services, check DevTools EventStream timing | N/A |
| ARCH-03 | Collector restart does not crash/hang dashboard | integration | `docker compose restart collector` while dashboard is running | Manual |

### Wave 0 Gaps

- [ ] `troll/dydx_collector/tests/test_redis_pub.py` — tests that `_publish_snapshot_batch` serializes a `DydxSecondSnapshot` list to valid JSON and publishes to the right channel (mock `redis.asyncio.Redis`)
- [ ] `troll/ml_signals/tests/test_dashboard_ingest.py` — tests that `_ingest_batch()` feeds `_OFI_INDS` correctly and detects the >3s gap (same logic as existing `_metrics_from_rolling`, just different input format)

## Security Domain

> No external exposure beyond `127.0.0.1:8765` (same as current). Redis port not exposed to
> host. No auth tokens or secrets involved. ASVS categories V2/V3/V6 do not apply.

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V5 Input Validation | Yes — Redis messages | `json.loads()` in try/except; malformed messages logged and skipped |
| V4 Access Control | No — `127.0.0.1` binding | Ports bound to loopback only |

## Sources

### Primary (MEDIUM confidence)

- redis.io/docs/latest/develop/clients/redis-py/async/ — asyncio pub/sub subscribe, listen, publish patterns [CITED]
- redis.readthedocs.io/en/stable/examples/asyncio_examples.html — get_message and listen async examples [CITED]
- docs.aiohttp.org/en/stable/web_advanced.html — cleanup_ctx, on_startup, graceful shutdown [CITED]
- docs.aiohttp.org/en/stable/web_reference.html — StreamResponse write/prepare/content_type [CITED]
- github.com/aio-libs/aiohttp-sse — SSE headers, is_connected, keepalive ping pattern [CITED]

### Secondary (LOW confidence)

- docs.docker.com/compose/how-tos/networking/ — bridge network service DNS, host-mode DNS limitation [CITED]
- PyPI registry — `pip index versions redis` (8.0.1), `pip index versions aiohttp` (3.14.1) [VERIFIED: PyPI registry]

## Metadata

**Confidence breakdown:**
- Redis pub/sub pattern: MEDIUM — verified against official redis.io docs and PyPI
- aiohttp SSE pattern: MEDIUM — verified against official aiohttp docs and aio-libs source
- Docker networking: LOW — single official docs source; A1 assumption not yet verified
- Package legitimacy: MEDIUM — official GitHub repos confirmed; download counts unavailable

**Research date:** 2026-06-29
**Valid until:** 2026-07-29 (30 days — both redis and aiohttp are stable, slow-moving APIs)
