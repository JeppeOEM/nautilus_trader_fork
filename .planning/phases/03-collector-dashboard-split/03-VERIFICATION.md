---
phase: 03-collector-dashboard-split
verified: 2026-06-29T18:30:00Z
status: human_needed
score: 6/7 must-haves verified
behavior_unverified: 1
overrides_applied: 0
behavior_unverified_items:
  - truth: "Rankings updates arrive in the browser within ~100ms of the collector publish (ARCH-02)"
    test: "Open browser DevTools → Network → EventStream tab while running `make -C troll up`; publish timing observable from collector log vs browser frame timestamps"
    expected: "SSE data frames appear in DevTools within ~100ms of collector's 1s tick log line"
    why_human: "End-to-end latency (collector publish → Redis → dashboard ingest → SSE write → browser receive) is a runtime measurement; code inspection can only confirm the architecture removes blocking paths, not that wall-clock latency stays under 100ms"
human_verification:
  - test: "Measure SSE delivery latency with running services"
    expected: "Rankings table cells update within ~100ms of each 1s collector tick (visible in browser DevTools EventStream)"
    why_human: "Cannot be measured from code inspection; requires a live collector + Redis + dashboard + browser session"
---

# Phase 03: Collector/Dashboard Split — Verification Report

**Phase Goal:** Collector becomes a pure data pipeline (no HTTP server, no dashboard threads). Dashboard becomes a standalone aiohttp service that subscribes to Redis pub/sub for 1s snapshots, maintains its own in-memory metric cache, and pushes updates to the browser via Server-Sent Events — eliminating polling latency and GIL competition between collection and rendering.

**Verified:** 2026-06-29T18:30:00Z
**Status:** human_needed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | Collector process has zero dashboard imports — `serve_in_background` removed, `_second_rolling` deque removed (ARCH-01) | VERIFIED | `grep -r "ml_signals.dashboard\|serve_in_background" troll/dydx_collector/` returns 0; collector.py imports only `redis.asyncio`, no `ml_signals.*`; no `_second_rolling` field in `Collector.__init__` |
| 2 | `_second_loop` publishes a JSON array of `DydxSecondSnapshot.to_dict()` dicts to Redis channel `snapshots:1s` once per second (ARCH-01) | VERIFIED | collector.py line 270: `await _publish_snapshot_batch(self._redis, batch)`; `_publish_snapshot_batch` (lines 71–83) serializes via `json.dumps([DydxSecondSnapshot.to_dict(s) for s in snapshots])` then `await redis_client.publish("snapshots:1s", payload)` |
| 3 | Dashboard runs as a separate Docker service using aiohttp; no threading module used (ARCH-02) | VERIFIED | docker-compose.yml has standalone `dashboard` service (container `dydx-dashboard`); `grep -c "threading\|ThreadingMixIn\|HTTPServer" troll/ml_signals/dashboard.py` returns 0; `from aiohttp import web` is the HTTP framework |
| 4 | Browser rankings page connects via `EventSource('/stream')` — `setInterval` polling is gone (ARCH-02) | VERIFIED | dashboard.py lines 244–267: `poll_script` uses `new EventSource('/stream')` with `es.onmessage` handler; `grep -c "setInterval.*fetch.*rankings"` returns 0 |
| 5 | Rankings updates arrive in the browser within ~100ms of the collector publish (ARCH-02) | PRESENT_BEHAVIOR_UNVERIFIED | Architecture is correct: collector publishes → Redis → `_redis_listener` feeds `_ingest_batch` → `_fanout_to_sse_clients` → `q.put_nowait` → browser SSE; no blocking steps added. Actual wall-clock latency requires runtime measurement — see Human Verification |
| 6 | Dashboard reconnects to Redis automatically after a collector restart (ARCH-03) | VERIFIED | `_redis_listener` (dashboard.py lines 767–784) has outer `while True`; inner `except Exception` catches all non-cancellation errors and `await asyncio.sleep(2)` before retrying; `except asyncio.CancelledError: raise` propagates cleanly. Collector restart leaves Redis running — dashboard pubsub stays alive and automatically resumes when collector re-publishes |
| 7 | Restarting the dashboard service does not affect the collector in any way (ARCH-03) | VERIFIED | Collector imports zero dashboard symbols (grep confirms 0); Redis pub/sub is strictly one-directional (collector publishes, dashboard subscribes); no inbound connection from dashboard to collector possible |

**Score:** 6/7 truths verified (1 present, behavior not exercised)

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `troll/troll-requirements.txt` | redis and aiohttp dependencies | VERIFIED | Contains `redis>=8.0.1` and `aiohttp>=3.14.1` |
| `troll/docker-compose.yml` | redis + dashboard services alongside collector + dozzle | VERIFIED | All 4 services present: `dydx-redis`, `dydx-collector`, `dydx-dashboard`, `dydx-collector-dozzle`; `docker compose config` validates |
| `troll/dydx_collector/collector.py` | Pure data pipeline; no HTTP server; exports `_publish_snapshot_batch` | VERIFIED | No `ml_signals` imports; `_publish_snapshot_batch` is a module-level async function; `self._redis` initialized in `run()`, closed in `finally` block |
| `troll/dydx_collector/tests/test_redis_pub.py` | Unit tests for Redis publish function | VERIFIED | 5 tests pass: empty batch (no publish call), single snapshot (publish called once with correct args), JSON validity, payload structure, ConnectionError swallowed |
| `troll/ml_signals/dashboard.py` | Standalone aiohttp app; SSE at /stream; Redis subscriber; exports `make_app` | VERIFIED | `make_app()`, `_ingest_batch()`, `_fanout_to_sse_clients()` all importable; `/stream` route registered; `redis_subscriber_ctx` and `slow_loop_ctx` registered as cleanup_ctx |
| `troll/ml_signals/tests/test_dashboard_ingest.py` | Unit tests for `_ingest_batch` including gap detection | VERIFIED | 8 tests pass: rolling append, OFI init, LAST_FED update, LIVE keys, no-gap (clear_prev_state not called), gap detection (clear_prev_state called), CVD sum, volume_delta (last snap only) |

---

### Key Link Verification

| From | To | Via | Status | Evidence |
|------|----|-----|--------|---------|
| `collector.py _second_loop` | Redis channel `snapshots:1s` | `await _publish_snapshot_batch(self._redis, batch)` at line 270 | WIRED | `_publish_snapshot_batch` calls `redis_client.publish("snapshots:1s", payload)` at line 81 |
| `dashboard.py _redis_listener` | `_ingest_batch` then `_fanout_to_sse_clients` | `async for message in pubsub.listen()` → `_ingest_batch(batch)` at line 777 | WIRED | `_ingest_batch` calls `_fanout_to_sse_clients()` as its last statement (line 652) |
| `dashboard.py sse_handler` | Browser EventSource | `Content-Type: text/event-stream`; per-client `asyncio.Queue` drained with 15s keepalive | WIRED | `sse_handler` at lines 665–685; `/stream` route registered at line 837; `stream_handler` delegates to `sse_handler` |

---

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| All key exports importable | `python -c "from ml_signals.dashboard import make_app, _ingest_batch, _fanout_to_sse_clients; from dydx_collector.collector import _publish_snapshot_batch; print('OK')"` | `All key exports: OK` | PASS |
| No dashboard imports in collector | `grep -r "ml_signals.dashboard\|serve_in_background" troll/dydx_collector/ \| wc -l` | `0` | PASS |
| No threading in dashboard | `grep -c "threading\|ThreadingMixIn\|HTTPServer" troll/ml_signals/dashboard.py` | `0` | PASS |
| Docker compose validates | `docker compose -f troll/docker-compose.yml config --quiet` | exit 0 | PASS |
| New unit tests pass (13 tests across 2 files) | `PYTHONPATH=troll python -m pytest troll/dydx_collector/tests/test_redis_pub.py troll/ml_signals/tests/test_dashboard_ingest.py -q` | `13 passed in 0.76s` | PASS |
| Full test suite (excl. pre-existing failure) | `PYTHONPATH=troll python -m pytest troll/ -q --ignore=troll/ml_signals/tests/test_ofi_strategy.py` | `94 passed in 4.74s` | PASS |

The pre-existing failure (`test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure`) was confirmed in SUMMARY.md to exist before any phase-03 changes. It is out of scope.

---

### Requirements Coverage

| Requirement | Description | Status | Evidence |
|-------------|-------------|--------|---------|
| ARCH-01 | Collector decoupled from dashboard — publishes to Redis, no HTTP server | SATISFIED | Zero dashboard imports; `_publish_snapshot_batch` wired at end of `_second_loop` |
| ARCH-02 | aiohttp SSE server + EventSource in browser | SATISFIED | aiohttp routes wired; `/stream` handler present; `EventSource('/stream')` in rankings HTML |
| ARCH-03 | Independent restarts via Redis reconnect logic | SATISFIED | `_redis_listener` outer `while True` + `asyncio.sleep(2)` reconnect; collector has no inbound dep on dashboard |

---

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| — | — | — | — | None found |

Scanned all 7 files modified by this phase for `TBD`, `FIXME`, `XXX`, `TODO`, `HACK`, `PLACEHOLDER`, `return null`, `return []`, `return {}`. No matches.

---

### Human Verification Required

#### 1. SSE Delivery Latency Under 100ms

**Test:** With `make -C troll up` running, open the rankings page at `http://localhost:8765` in a browser. Open DevTools → Network → filter for `stream` (the SSE connection). Watch the EventStream tab for incoming data frames. Cross-reference frame timestamps with collector log lines showing each 1s tick publish.

**Expected:** Data frames arrive in DevTools within ~100ms of the collector's publish log entry for the same second. No frame should be delayed by >100ms absent network congestion.

**Why human:** End-to-end latency (process publish → Redis deliver → dashboard async receive → SSE write → TCP deliver → browser receive) is a runtime measurement. Code inspection confirms no artificial blocking steps exist (synchronous `_ingest_batch`, `put_nowait` SSE fanout), but actual wall-clock latency under real load requires a live session.

---

### Gaps Summary

No gaps found. All code artifacts exist, are substantive, and are correctly wired. The single human-verification item (100ms latency) is an observable runtime property that the architecture is designed to achieve; it is not a code defect.

---

_Verified: 2026-06-29T18:30:00Z_
_Verifier: Claude (gsd-verifier)_
