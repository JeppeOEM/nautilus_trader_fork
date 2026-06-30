---
phase: 03-collector-dashboard-split
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - troll/troll-requirements.txt
  - troll/Makefile
  - troll/docker-compose.yml
  - troll/dydx_collector/config.py
  - troll/dydx_collector/collector.py
  - troll/dydx_collector/tests/test_redis_pub.py
  - troll/ml_signals/dashboard.py
  - troll/ml_signals/tests/test_dashboard_metrics.py
  - troll/ml_signals/tests/test_dashboard_ingest.py
autonomous: true
requirements: [ARCH-01, ARCH-02, ARCH-03]

must_haves:
  truths:
    - "Collector process contains zero dashboard imports — serve_in_background call removed, _second_rolling deque removed (ARCH-01)"
    - "_second_loop publishes a JSON array of DydxSecondSnapshot.to_dict() dicts to Redis channel snapshots:1s once per second (ARCH-01)"
    - "Dashboard runs as a separate Docker service using aiohttp; no threading module used (ARCH-02)"
    - "Browser rankings page connects via EventSource('/stream') — setInterval polling is gone (ARCH-02)"
    - "Rankings updates arrive in the browser within ~100ms of the collector publish (ARCH-02)"
    - "Dashboard reconnects to Redis automatically after a collector restart (ARCH-03)"
    - "Restarting the dashboard service does not affect the collector in any way (ARCH-03)"
  artifacts:
    - path: "troll/troll-requirements.txt"
      provides: "redis and aiohttp dependencies"
      contains: "redis"
    - path: "troll/docker-compose.yml"
      provides: "redis + dashboard services alongside collector"
    - path: "troll/dydx_collector/collector.py"
      provides: "Pure data pipeline — no HTTP server, no dashboard import"
      exports: ["_publish_snapshot_batch"]
    - path: "troll/dydx_collector/tests/test_redis_pub.py"
      provides: "Unit tests for Redis publish function"
    - path: "troll/ml_signals/dashboard.py"
      provides: "Standalone aiohttp app — SSE at /stream, Redis subscriber, asyncio slow loop"
      exports: ["make_app"]
    - path: "troll/ml_signals/tests/test_dashboard_ingest.py"
      provides: "Unit tests for _ingest_batch including gap detection"
  key_links:
    - from: "troll/dydx_collector/collector.py _second_loop"
      to: "Redis channel snapshots:1s"
      via: "_publish_snapshot_batch(self._redis, batch) at end of _second_loop"
      pattern: "publish.*snapshots:1s"
    - from: "troll/ml_signals/dashboard.py _redis_listener"
      to: "_ingest_batch then _fanout_to_sse_clients"
      via: "async for message in pubsub.listen() → _ingest_batch(json.loads(message['data']))"
    - from: "troll/ml_signals/dashboard.py sse_handler"
      to: "browser EventSource"
      via: "Content-Type: text/event-stream; drain per-client asyncio.Queue with 15s keepalive"
---

<objective>
Decouple the dYdX dashboard from the collector process by inserting Redis pub/sub as the IPC layer.

Purpose: Eliminate GIL contention between collection threads and HTTP handler threads; enable independent restarts of collector and dashboard without interrupting each other.

Output: Collector becomes a pure data pipeline that publishes 1s snapshot batches to Redis. Dashboard becomes a standalone aiohttp service that subscribes to Redis, maintains its own rolling state, and pushes rankings to browsers via Server-Sent Events. Two separate Docker services, zero shared Python process.

Decision coverage: ARCH-01 (collector decoupling), ARCH-02 (aiohttp SSE server + EventSource), ARCH-03 (independent restarts via Redis reconnect logic).
</objective>

<execution_context>
@/home/mrqdt/code/nautilus_trader_fork/.claude/gsd-core/workflows/execute-plan.md
@/home/mrqdt/code/nautilus_trader_fork/.claude/gsd-core/templates/summary.md
</execution_context>

<context>
@/home/mrqdt/code/nautilus_trader_fork/.planning/ROADMAP.md
@/home/mrqdt/code/nautilus_trader_fork/troll/CLAUDE.md
@/home/mrqdt/code/nautilus_trader_fork/.planning/phases/03-collector-dashboard-split/03-RESEARCH.md

@/home/mrqdt/code/nautilus_trader_fork/troll/dydx_collector/second_snapshot.py
@/home/mrqdt/code/nautilus_trader_fork/troll/dydx_collector/collector.py
@/home/mrqdt/code/nautilus_trader_fork/troll/ml_signals/dashboard.py
@/home/mrqdt/code/nautilus_trader_fork/troll/docker-compose.yml
@/home/mrqdt/code/nautilus_trader_fork/troll/troll-requirements.txt
@/home/mrqdt/code/nautilus_trader_fork/troll/Makefile
@/home/mrqdt/code/nautilus_trader_fork/troll/ml_signals/tests/test_dashboard_metrics.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add redis and aiohttp dependencies; add logs-dash Makefile target</name>
  <files>troll/troll-requirements.txt, troll/Makefile</files>
  <action>
troll-requirements.txt: append two lines — redis>=8.0.1 and aiohttp>=3.14.1. Both packages are from the RESEARCH.md Package Legitimacy Audit (approved, see audit table in RESEARCH.md). Do NOT add aioredis — it is archived; the asyncio client ships inside the redis package.

Makefile: add a logs-dash target that tails the dydx-dashboard container log. Pattern exactly like the existing logs target but with container name dydx-dashboard. Update the .PHONY line to include logs-dash.
  </action>
  <verify>
    <automated>grep -c "redis" troll/troll-requirements.txt && grep -c "aiohttp" troll/troll-requirements.txt && grep -c "logs-dash" troll/Makefile</automated>
  </verify>
  <done>troll-requirements.txt contains redis and aiohttp lines; Makefile has logs-dash target.</done>
</task>

<task type="auto">
  <name>Task 2: Add redis and dashboard services to docker-compose.yml</name>
  <files>troll/docker-compose.yml</files>
  <action>
Replace the current docker-compose.yml with a version that adds two new services while keeping the existing collector and dozzle services intact.

Redis service (container name dydx-redis):
- image: redis:7-alpine
- ports: bind 127.0.0.1:6379 to container port 6379 (localhost only — not exposed on 0.0.0.0)
- restart: unless-stopped
- No persistence flags, no volumes, no network_mode override (bridge mode is fine for Redis since it just needs its port exposed to the host)

Collector service changes:
- Keep network_mode: host (unchanged — assumption A1 per RESEARCH.md: dYdX Rust client may need it)
- Add environment variable REDIS_URL with value redis://127.0.0.1:6379 (host-mode containers reach Redis via the host loopback where Redis port is mapped)
- All other fields (build, user, volumes, restart) remain unchanged

Dashboard service (container name dydx-dashboard):
- Build: same context and dockerfile as collector (no second Dockerfile needed — same image, different command). Per architecture decision, one Dockerfile serves both services.
- command: python3 -m ml_signals.dashboard
- environment: PYTHONUNBUFFERED=1, REDIS_URL=redis://127.0.0.1:6379, CATALOG_PATH=/app/catalog, DASHBOARD_PORT=8765
- user: 1000:1000
- volumes: mount ./dydx_collector/catalog at /app/catalog read-only (dashboard never writes to catalog); mount ./dydx_collector/metrics.db at /app/metrics.db (slow loop writes here)
- network_mode: host (dashboard serves on port 8765 via the host network — no ports: mapping needed with host mode)
- restart: unless-stopped

Dozzle service: unchanged.

Note: with both collector and dashboard using network_mode: host, they cannot use named Docker networks or ports: mappings. The Redis service uses bridge mode with a host port binding so that host-mode containers can reach it at 127.0.0.1:6379. Host mode is retained for the collector rather than the bridge network recommended in RESEARCH.md Pattern 6 because Assumption A1 (that the dYdX Rust PyO3 client does not require host networking) is unverified — bridge migration is deferred until A1 is confirmed in a test run.
  </action>
  <verify>
    <automated>docker compose -f troll/docker-compose.yml config --quiet && echo "valid"</automated>
  </verify>
  <done>docker compose config validates; services redis, collector, dashboard, dozzle all present; dashboard has REDIS_URL and CATALOG_PATH env vars; Redis port bound to 127.0.0.1:6379.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Decouple collector from dashboard; add Redis publish in _second_loop (ARCH-01)</name>
  <files>troll/dydx_collector/collector.py, troll/dydx_collector/config.py</files>
  <behavior>
    - _publish_snapshot_batch with empty list does not call redis.publish
    - _publish_snapshot_batch with one snapshot calls redis.publish("snapshots:1s", payload) where payload is valid JSON
    - JSON payload is a list with one dict whose "instrument_id" matches the snapshot's instrument_id.value
    - ConnectionError from redis.publish is caught and logged, not re-raised (missing one tick is acceptable)
  </behavior>
  <action>
config.py: Remove the dashboard_port field from the CollectorConfig frozen dataclass and remove its corresponding raw.get("dashboard_port", 8765) line from load_config(). The collector no longer serves HTTP. The config.toml may still contain this key — that is fine, tomllib.load silently ignores unknown keys.

collector.py — imports: Add import json, import os, and import redis.asyncio as aioredis. Remove the line importing serve_in_background from ml_signals.dashboard.

collector.py — Collector.__init__: Remove self._second_rolling dict (the defaultdict of deques). Remove all three references: the field declaration, the deque append in _second_loop, and the serve_in_background call. Add self._redis: aioredis.Redis | None = None (initialized in run, not __init__).

collector.py — module-level function _publish_snapshot_batch(redis_client: aioredis.Redis, snapshots: list) -> None (async): If snapshots is empty, return immediately. Otherwise, serialize to JSON: json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots]) and call await redis_client.publish("snapshots:1s", payload). Wrap the publish call in try/except Exception — on failure, call logger.warning("Redis publish failed: %s", e) and return. Never re-raise — missing one publish tick is acceptable per the architecture.

collector.py — _second_loop: Build a batch list before the per-instrument for loop (batch: list = []). Inside the loop, after creating the snapshot object and calling self._on_data(snapshot), append the snapshot to batch (replacing the removed self._second_rolling[iid].append line). After the for loop, call await _publish_snapshot_batch(self._redis, batch).

collector.py — run(): Initialize self._redis before creating background tasks: self._redis = aioredis.Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:6379")). In the finally block, after cancelling tasks and disconnecting the client, call await self._redis.aclose() if self._redis is not None.

Remove the serve_in_background call and all three arguments it took (port, catalog_path, rolling) from run(). The self._config.dashboard_port reference is also gone.
  </action>
  <verify>
    <automated>grep -rn "ml_signals.dashboard" troll/dydx_collector/ | wc -l</automated>
  </verify>
  <done>grep returns 0 (zero dashboard imports in collector). troll/dydx_collector/config.py has no dashboard_port field. pytest troll/dydx_collector/tests/ -x -q passes (existing tests unaffected).</done>
</task>

<task type="tdd">
  <name>Task 4: Unit tests for _publish_snapshot_batch (ARCH-01)</name>
  <files>troll/dydx_collector/tests/test_redis_pub.py</files>
  <feature>
    <name>Redis snapshot batch publisher</name>
    <files>troll/dydx_collector/tests/test_redis_pub.py, troll/dydx_collector/collector.py</files>
    <behavior>
Empty batch: _publish_snapshot_batch does not call redis_client.publish at all.
Single snapshot: publish called exactly once with positional args ("snapshots:1s", payload).
Payload is valid JSON that json.loads() can parse without error.
Parsed payload is a list with exactly one element.
The element's "instrument_id" key equals the snapshot's instrument_id string value.
Redis ConnectionError: caught silently — no exception propagates to the caller.
    </behavior>
    <implementation>
Include the LGPL copyright header (same as existing test files in dydx_collector/tests/).
Use unittest.mock.AsyncMock for the redis_client argument.
Build test snapshots using DydxSecondSnapshot directly with InstrumentId.from_str("BTC-USD-PERP.DYDX") — no mocking of Nautilus types per TEST-03.
Use pytest.mark.asyncio (install pytest-asyncio if needed) OR wrap in asyncio.run() inside each test function. Prefer asyncio.run() since pytest-asyncio may not be in the project.
Import _publish_snapshot_batch from dydx_collector.collector.
One test function per behavioral claim above — no class-based tests per TEST-03.
    </implementation>
  </feature>
  <verify>
    <automated>cd /home/mrqdt/code/nautilus_trader_fork && PYTHONPATH=troll python -m pytest troll/dydx_collector/tests/test_redis_pub.py -x -q</automated>
  </verify>
  <done>All tests in test_redis_pub.py pass.</done>
</task>

<task type="auto" tdd="true">
  <name>Task 5: Rewrite dashboard.py as standalone aiohttp service with SSE and Redis subscriber (ARCH-02, ARCH-03)</name>
  <files>troll/ml_signals/dashboard.py, troll/ml_signals/tests/test_dashboard_metrics.py</files>
  <behavior>
    - _ingest_batch(batch: list[dict]) appends each snap dict to module-level _second_rolling[iid] deque and feeds _OFI_INDS[iid]
    - Gap detection: if ts_event - _LAST_FED[iid] > 3_000_000_000 ns (and _LAST_FED[iid] > 0), call clear_prev_state() on _OFI_INDS[iid] and each indicator in _OFI_RAW_INDS[iid]
    - _fanout_to_sse_clients() builds SSE payload via _rankings_json() and put_nowait to all queues in _SSE_QUEUES; full queues are silently dropped
    - SSE handler at /stream sets Content-Type text/event-stream, waits on per-client Queue with 15s timeout for keepalive comment
    - make_app(redis_url, catalog_path) returns a configured aiohttp.web.Application with all routes and cleanup_ctx registered
  </behavior>
  <action>
dashboard.py is a complete file replacement. All threading, http.server, BaseHTTPRequestHandler, HTTPServer, and ThreadingMixIn code is removed. The file becomes a pure asyncio + aiohttp module.

COPYRIGHT HEADER: Preserve the LGPL header (same as current).

MODULE DOCSTRING: Update to describe the new architecture (aiohttp, SSE, Redis subscriber).

IMPORTS to add: asyncio, contextlib, os, from contextlib import asynccontextmanager, import redis.asyncio as aioredis, from aiohttp import web. Keep existing: html, json, logging, statistics, time, defaultdict, deque, Path, parse_qs, unquote, urlparse, pd, go, make_subplots, and all ml_signals imports.
IMPORTS to remove: threading, http.server (BaseHTTPRequestHandler, HTTPServer).

MODULE GLOBALS to keep unchanged: LIVE_INTERVAL_SECONDS, DB_WRITE_INTERVAL_SECONDS, RANKING_COLS, _CSS, _NAV, _SERIES, _OFI_ZSCORE_WINDOW, _OFI_INDS, _OFI_RAW_INDS, _LAST_FED, _LIVE.
MODULE GLOBALS to remove: _LOCK, _METRICS_LOCK, _ROLLING (threading artifacts).
MODULE GLOBALS to add: _SSE_QUEUES: set[asyncio.Queue[bytes]] = set() and _second_rolling: dict[str, deque] = defaultdict(lambda: deque(maxlen=300)). Note: _second_rolling stores plain dicts (from DydxSecondSnapshot.to_dict()), not DydxSecondSnapshot objects.
MODULE GLOBALS to change: CATALOG_PATH is set at module load time from os.environ.get("CATALOG_PATH", "troll/dydx_collector/catalog") instead of being mutated in serve_in_background.

FUNCTIONS TO KEEP UNCHANGED: record(), _page(), _render_history_page(), _render_chart_page(), _render_live_page(), _trade_aggregates(), _rankings_json(), _split_tiers().

FUNCTIONS TO MODIFY:
_render_rankings_page(sort_col, direction): Keep all HTML generation logic unchanged. Replace the poll_script variable's content: change the setInterval/fetch('/data/rankings') block to an EventSource block. The new script builds the same cells lookup dict from td[data-iid] elements. Creates new EventSource('/stream'). The onmessage handler parses e.data as JSON and patches each cell's textContent and style.color — same cell-patching logic as the current fetch handler. No onerror handler needed (browser auto-reconnects).

_coin_chart_json(iid: str) -> str: Remove the rolling: dict | None parameter. Read from module-level _second_rolling[iid] instead. Change all attribute accesses (s.bid_prices, s.ask_prices, s.bid_sizes, s.ask_sizes, s.ts_event) to dict key accesses (s["bid_prices"], etc.). When _second_rolling[iid] is empty (or iid absent), return the same empty JSON structure as the current None path.

_render_live_coin_page(symbol: str) -> str: Remove the rolling: dict | None parameter. Call _coin_chart_json(symbol) without second argument. Remove the standalone_note block (no longer relevant).

FUNCTIONS TO REMOVE: _metrics_from_rolling(), _fast_loop(), _slow_loop(), serve_in_background(), _Handler class.

NEW FUNCTION _ingest_batch(batch: list[dict]) -> None:
For each snap_dict in batch: extract iid = snap_dict["instrument_id"]. Append snap_dict to _second_rolling[iid]. Initialize _OFI_INDS[iid] and _OFI_RAW_INDS[iid] if not present (same MultiLevelOFI config as the removed _metrics_from_rolling). Gap detection: if _LAST_FED.get(iid, 0) > 0 and (snap_dict["ts_event"] - _LAST_FED[iid]) > 3_000_000_000, call clear_prev_state() on _OFI_INDS[iid] and all values in _OFI_RAW_INDS[iid]. Feed OFI: call _OFI_INDS[iid].update_raw and each _OFI_RAW_INDS[iid][N].update_raw using snap_dict["bid_prices"], snap_dict["bid_sizes"], snap_dict["ask_prices"], snap_dict["ask_sizes"]. Set _LAST_FED[iid] = snap_dict["ts_event"]. Then compute full window metrics from all dicts in _second_rolling[iid]: OBI (stateless, from latest snap_dict only), CVD (sum buy_volume minus sum sell_volume over window), volatility, microprice, spread, etc. — same formulas as the removed _metrics_from_rolling but using dict field access. Update _LIVE[iid] with the computed metrics dict. After the outer for loop (all instruments processed), call _fanout_to_sse_clients().

NEW FUNCTION _fanout_to_sse_clients() -> None: Build payload bytes: b"data: " + _rankings_json().encode() + b"\n\n". For each queue in list(_SSE_QUEUES), call q.put_nowait(payload); catch asyncio.QueueFull and pass (drop for slow clients).

NEW ASYNC FUNCTION sse_handler(request: web.Request) -> web.StreamResponse: Create q = asyncio.Queue(maxsize=2). Add q to _SSE_QUEUES. Create resp = web.StreamResponse(). Set headers: Content-Type text/event-stream, Cache-Control no-cache, X-Accel-Buffering no. Call await resp.prepare(request). Loop: try await asyncio.wait_for(q.get(), timeout=15.0); on success write the payload; on asyncio.TimeoutError write b": keepalive\n\n". Catch ConnectionResetError, ConnectionAbortedError, asyncio.CancelledError and break/pass. Finally: _SSE_QUEUES.discard(q). Return resp.

NEW AIOHTTP ROUTE HANDLERS (all async, return web.Response or web.StreamResponse):
- rankings_handler(request): read sort/dir from request.rel_url.query; return web.Response(text=_render_rankings_page(...), content_type="text/html")
- stream_handler(request): return await sse_handler(request)
- coin_handler(request): symbol = request.match_info["id"]; check symbol exists in list_instruments(CATALOG_PATH); return 404 if not; return web.Response(text=_render_live_coin_page(symbol), content_type="text/html")
- coin_json_handler(request): symbol = request.match_info["id"]; return web.Response(text=_coin_chart_json(symbol), content_type="application/json")
- chart_handler(request): symbol = request.match_info["id"]; parse start/end from query; run _render_chart_page in asyncio.to_thread (it reads Parquet); return web.Response(text=html_str, content_type="text/html")
- history_handler(request): symbol = request.match_info["id"]; run _render_history_page in asyncio.to_thread; return HTML response
- live_handler(request): return web.Response(text=_render_live_page(), content_type="text/html")

NEW @asynccontextmanager async def redis_subscriber_ctx(app: web.Application): Create and await task running _redis_listener(app["redis_url"]). On teardown: cancel the task, suppress CancelledError.

NEW async def _redis_listener(redis_url: str) -> None: Outer while True loop for reconnect. Inner: create aioredis.Redis.from_url(redis_url, decode_responses=True) as async context manager, create pubsub, subscribe to "snapshots:1s", async for message in pubsub.listen(): if message["type"] != "message" continue; try json.loads(message["data"]) then _ingest_batch; except Exception log and continue. Outer except asyncio.CancelledError: re-raise (propagate cancellation). Outer except Exception: log "Redis subscriber error — reconnecting in 2s" then await asyncio.sleep(2).

NEW @asynccontextmanager async def slow_loop_ctx(app: web.Application): Create and await task running _slow_loop_task(app["catalog_path"]). On teardown: cancel, suppress CancelledError.

NEW async def _slow_loop_task(catalog_path: str) -> None: db_path = str(Path(catalog_path).parent / "metrics.db"). while True: try: snapshots = await asyncio.to_thread(metrics_computer.compute_all, catalog_path); if snapshots: update _LIVE for each snapshot and await asyncio.to_thread(metrics_store.write, snapshots, db_path); except Exception: log. await asyncio.sleep(DB_WRITE_INTERVAL_SECONDS).

NEW def make_app(redis_url: str, catalog_path: str) -> web.Application: Pre-populate _LIVE from metrics.db (same try/except pattern as current serve_in_background). Create app = web.Application(). Set app["redis_url"] = redis_url and app["catalog_path"] = catalog_path. Register cleanup_ctx: redis_subscriber_ctx, slow_loop_ctx. Add routes: GET / → rankings_handler, GET /stream → stream_handler, GET /coin/{id} → coin_handler, GET /data/coin/{id} → coin_json_handler, GET /chart/{id} → chart_handler, GET /history/{id} → history_handler, GET /live → live_handler. Return app.

if __name__ == "__main__" block: read redis_url from REDIS_URL env (default redis://127.0.0.1:6379), catalog_path from CATALOG_PATH env (default troll/dydx_collector/catalog), port from DASHBOARD_PORT env (default 8765). Call web.run_app(make_app(redis_url, catalog_path), host="0.0.0.0", port=port).

UPDATE test_dashboard_metrics.py:
This file tests _metrics_from_rolling and _coin_chart_json with the old signatures — both are breaking changes.
Remove all test functions that import or call _metrics_from_rolling (test_cvd, test_microprice_lean, test_avg_trade_size_no_trades, test_avg_trade_size_value, test_metrics_keys). That function no longer exists.
Update test_chart_json_no_rolling: call _coin_chart_json("ETH-USD-PERP.DYDX") with no second argument. The module-level _second_rolling will be empty for this iid (test isolation: clear it at start of test with ml_signals.dashboard._second_rolling.clear()). Assert result equals the empty JSON structure.
Update test_chart_json_ts_conversion: instead of passing a rolling dict, pre-populate ml_signals.dashboard._second_rolling["ETH-USD-PERP.DYDX"] with a deque containing one snap dict (matching DydxSecondSnapshot.to_dict() structure: instrument_id as string, bid_prices list, ask_prices list, etc.). Clear _second_rolling before and after the test. Assert ts[0] == ts_ns // 1_000_000.
Update docstring to reflect the new scope.
  </action>
  <verify>
    <automated>cd /home/mrqdt/code/nautilus_trader_fork && PYTHONPATH=troll python -m pytest troll/ml_signals/tests/test_dashboard_metrics.py -x -q</automated>
  </verify>
  <done>test_dashboard_metrics.py passes with updated signatures. No threading imports remain in dashboard.py. make_app() is importable. /stream route registered. _ingest_batch() and _fanout_to_sse_clients() exist as module-level functions.</done>
</task>

<task type="tdd">
  <name>Task 6: Unit tests for _ingest_batch including gap detection (ARCH-01, ARCH-03)</name>
  <files>troll/ml_signals/tests/test_dashboard_ingest.py</files>
  <feature>
    <name>Dashboard snapshot ingestion and gap detection</name>
    <files>troll/ml_signals/tests/test_dashboard_ingest.py, troll/ml_signals/dashboard.py</files>
    <behavior>
_ingest_batch with one snap dict appends to _second_rolling[iid] (len becomes 1).
_ingest_batch with one snap dict initializes _OFI_INDS[iid] and _OFI_RAW_INDS[iid].
_ingest_batch with one snap dict sets _LAST_FED[iid] to the snap's ts_event value.
_ingest_batch with one snap dict updates _LIVE[iid] with at least the keys: instrument_id, microprice, spread, cvd, buy_count, sell_count.
Gap detection — no gap: two consecutive snaps 1s apart → _OFI_INDS[iid].clear_prev_state is NOT called.
Gap detection — gap: two snaps with ts_event gap > 3_000_000_000 ns → _OFI_INDS[iid].clear_prev_state is called before the second snap is fed (verify by checking that OFI prev state is reset: after the second snap, OFI indicator has no _prev reference carried from the first snap).
CVD: _LIVE[iid]["cvd"] after ingesting two snaps equals sum(buy_volume) - sum(sell_volume) over the window.
volume_delta: _LIVE[iid]["volume_delta"] equals the LAST snap's buy_volume - sell_volume (not the sum).
    </behavior>
    <implementation>
Include the LGPL copyright header.
Import _ingest_batch, _LIVE, _OFI_INDS, _OFI_RAW_INDS, _LAST_FED, _second_rolling from ml_signals.dashboard.
Each test must clear module-level state (_LIVE.clear(), _OFI_INDS.clear(), _OFI_RAW_INDS.clear(), _LAST_FED.clear(), _second_rolling.clear()) at the start using a helper _reset_state() function with underscore prefix.
Build snap dicts directly as plain Python dicts matching DydxSecondSnapshot.to_dict() structure — do NOT create DydxSecondSnapshot objects for these tests (the function under test receives dicts, not Nautilus types).
Helper function _snap_dict(iid, bid_prices, bid_sizes, ask_prices, ask_sizes, buy_volume, sell_volume, buy_count, sell_count, ts_event) -> dict to reduce boilerplate.
No class-based tests, no mocking of Nautilus internals, no pytest fixtures — helper functions with underscore prefix only.
Use pytest.approx for float comparisons.
    </implementation>
  </feature>
  <verify>
    <automated>cd /home/mrqdt/code/nautilus_trader_fork && PYTHONPATH=troll python -m pytest troll/ml_signals/tests/test_dashboard_ingest.py -x -q</automated>
  </verify>
  <done>All tests in test_dashboard_ingest.py pass. Gap detection test confirms clear_prev_state behavior.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| Redis → dashboard | Redis messages contain JSON serialized by the collector — treated as semi-trusted (same host, not internet-exposed) but malformed payloads are possible after a code change or collector crash |
| Browser → dashboard | HTTP requests to aiohttp; bound to 127.0.0.1 so only local callers |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-03-01 | Tampering | _redis_listener json.loads | mitigate | json.loads call wrapped in try/except; malformed messages logged and skipped, subscriber continues |
| T-03-02 | Denial of Service | SSE fan-out | mitigate | asyncio.Queue(maxsize=2) per client; put_nowait drops for slow clients — subscriber never blocks on a hung browser tab |
| T-03-03 | Denial of Service | Redis subscriber disconnect | mitigate | _redis_listener outer while True with asyncio.sleep(2) reconnect on any non-CancelledError exception |
| T-03-04 | Information Disclosure | Dashboard port | accept | Port bound to 127.0.0.1 via network_mode host on the host — not reachable externally; same exposure as current |
| T-03-SC | Tampering | pip install redis aioredis | mitigate | Package Legitimacy Audit in RESEARCH.md completed — both packages approved; aioredis explicitly excluded from requirements |
</threat_model>

<verification>
Full suite after all tasks complete:

cd /home/mrqdt/code/nautilus_trader_fork && PYTHONPATH=troll python -m pytest troll/ -x -q

Expected: all existing tests pass + new test_redis_pub.py and test_dashboard_ingest.py pass.

Functional smoke test (requires running services):
1. make -C troll up  (builds image, starts redis + collector + dashboard)
2. curl -s http://localhost:8765/ | grep -c "EventSource"  # should return 1
3. Browser: open http://localhost:8765 → network DevTools → EventStream tab → see events arriving at 1s intervals
4. docker compose -f troll/docker-compose.yml restart collector  # dashboard keeps serving; SSE reconnects within ~2s
5. docker compose -f troll/docker-compose.yml restart dashboard  # collector keeps publishing; new dashboard reconnects to Redis
</verification>

<success_criteria>
1. pytest troll/ -x -q passes (all tests including two new test files)
2. grep -r "serve_in_background" troll/dydx_collector/ | wc -l returns 0
3. grep -r "threading" troll/ml_signals/dashboard.py | wc -l returns 0
4. docker compose config validates with redis + collector + dashboard + dozzle services
5. After make up: curl http://localhost:8765/ succeeds and response body contains EventSource
6. Browser EventStream DevTools shows data frames arriving at 1s cadence
7. docker compose restart collector does not interrupt dashboard — SSE stream resumes within ~2s
</success_criteria>

<output>
Create .planning/phases/03-collector-dashboard-split/03-SUMMARY.md when done.
</output>
