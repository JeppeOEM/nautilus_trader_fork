---
phase: 03-collector-dashboard-split
plan: 01
subsystem: troll/dydx_collector + troll/ml_signals
tags: [redis, aiohttp, sse, pub-sub, decoupling, asyncio]
status: complete

dependency_graph:
  requires: []
  provides:
    - Redis pub/sub IPC between collector and dashboard
    - Standalone aiohttp dashboard with SSE at /stream
    - Independent restart of collector and dashboard (ARCH-03)
  affects:
    - troll/dydx_collector/collector.py
    - troll/dydx_collector/config.py
    - troll/ml_signals/dashboard.py
    - troll/docker-compose.yml

tech_stack:
  added:
    - redis>=8.0.1 (redis.asyncio for async pub/sub)
    - aiohttp>=3.14.1 (standalone HTTP server + SSE)
  patterns:
    - Redis pub/sub channel (snapshots:1s) as IPC layer between two separate processes
    - Server-Sent Events (SSE) for browser push instead of setInterval polling
    - asyncio.Queue(maxsize=2) per client with put_nowait drop for slow browsers

key_files:
  created:
    - troll/dydx_collector/tests/test_redis_pub.py
    - troll/ml_signals/tests/test_dashboard_ingest.py
  modified:
    - troll/troll-requirements.txt
    - troll/Makefile
    - troll/docker-compose.yml
    - troll/dydx_collector/config.py
    - troll/dydx_collector/collector.py
    - troll/ml_signals/dashboard.py
    - troll/ml_signals/tests/test_dashboard_metrics.py

decisions:
  - Retained network_mode: host for both collector and dashboard (Assumption A1 unverified — dYdX Rust PyO3 client may require host networking; bridge migration deferred until A1 is confirmed)
  - Redis service uses bridge mode with 127.0.0.1:6379 port binding so host-mode containers can reach it via loopback
  - One collector.dockerfile serves both collector and dashboard services (no second Dockerfile needed per architecture decision)
  - _ingest_batch() called synchronously per Redis message (no asyncio.to_thread needed — computation is fast and GIL contention is eliminated by the process split)

metrics:
  duration: "13 minutes"
  completed: "2026-06-29T18:05:00Z"
  tasks_completed: 6
  files_modified: 8
  tests_added: 13
---

# Phase 03 Plan 01: Collector/Dashboard Split Summary

Decoupled the dYdX collector from the dashboard via Redis pub/sub, enabling independent restarts of each service without interrupting the other.

## What Was Built

**ARCH-01 — Collector decoupled from dashboard:** Removed `serve_in_background` call and all threading artifacts from `collector.py`. Added `_publish_snapshot_batch()` async function that publishes 1s snapshot batches as JSON to Redis channel `snapshots:1s`. The `_second_rolling` deque shared between collector and dashboard is gone — each process now owns its own state. Removed `dashboard_port` field from `CollectorConfig` (no longer serves HTTP).

**ARCH-02 — Standalone aiohttp dashboard with SSE:** Rewrote `dashboard.py` from scratch. `threading`, `http.server`, `BaseHTTPRequestHandler`, `HTTPServer` removed. New `make_app()` returns a configured `aiohttp.web.Application` with all routes and two background task contexts (`redis_subscriber_ctx`, `slow_loop_ctx`). Rankings page now uses `EventSource('/stream')` instead of `setInterval`/`fetch('/data/rankings')` polling. Each browser connection gets its own `asyncio.Queue(maxsize=2)`; slow clients are silently dropped (T-03-02).

**ARCH-03 — Independent restart resilience:** `_redis_listener()` wraps the subscribe loop in an outer `while True` with 2s reconnect on any non-cancellation exception. Restarting the collector causes the dashboard to reconnect within 2s. Restarting the dashboard has zero effect on the collector.

## Commits

| Hash | Type | Description |
|------|------|-------------|
| 121e11dc1c | chore | add redis/aiohttp deps; add logs-dash Makefile target |
| b6c9780bec | feat | add redis + dashboard services to docker-compose.yml |
| f556e2d53c | test | failing tests for _publish_snapshot_batch (RED) |
| c18e54abef | feat | decouple collector; add Redis publish in _second_loop |
| d3f238ca15 | feat | rewrite dashboard.py as standalone aiohttp SSE service |
| baa34776b5 | test | unit tests for _ingest_batch including gap detection |

## Deviations from Plan

### Pre-existing Test Failure (Out of Scope)

`test_ofi_strategy.py::test_ofi_strategy_generates_long_entry_on_bid_pressure` was already failing on the branch before any changes in this plan. Verified by running the test against the initial commit (fa2533f71e). Out of scope per deviation rules — not caused by changes in this plan.

None of the code I changed is related to OFI strategy backtest execution.

### Auto-fixed Issues

None — plan executed as written.

## Test Results

| Test File | Tests | Status |
|-----------|-------|--------|
| troll/dydx_collector/tests/test_redis_pub.py | 5 | PASS |
| troll/ml_signals/tests/test_dashboard_ingest.py | 8 | PASS |
| troll/ml_signals/tests/test_dashboard_metrics.py | 2 | PASS |
| Full suite (excluding pre-existing failure) | 96 | PASS |

## Threat Mitigations Applied

| ID | Mitigation |
|----|-----------|
| T-03-01 | json.loads wrapped in try/except in _redis_listener; malformed messages logged and skipped |
| T-03-02 | asyncio.Queue(maxsize=2) per SSE client; put_nowait drops for slow clients |
| T-03-03 | _redis_listener outer while True with asyncio.sleep(2) reconnect |
| T-03-SC | redis and aiohttp packages approved in RESEARCH.md Package Legitimacy Audit; aioredis excluded |

## Known Stubs

None — all routes wired to real implementations.

## Self-Check: PASSED

- SUMMARY.md: FOUND
- test_redis_pub.py: FOUND
- test_dashboard_ingest.py: FOUND
- All 6 task commits present in git log
- 96 tests pass (1 pre-existing unrelated failure)
