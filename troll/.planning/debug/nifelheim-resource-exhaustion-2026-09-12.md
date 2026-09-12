# nifelheim resource exhaustion — stale orderbooks + ranking_engine restart loop

**Date:** 2026-09-12
**Status:** Mechanism identified (DATA-02 standard met). No fix applied — infra decision deferred by user.

## Symptom reported

Widespread "Stale book" warnings in `dydx-collector` logs across nearly every subscribed
instrument (BTC/ETH/SOL included).

## Investigation

Pulled `dydx-collector` logs for 2026-09-12 12:00–13:35 (95 min window). Found 31
`_second_loop tick arrived Ns late` events (2–21.5s late), each immediately followed by a
burst of "Stale book" warnings across ~27-28 of the ~28 subscribed instruments
simultaneously. This is the whole `_second_loop`/event-loop starving, not per-instrument
network flakiness — confirmed because staleness hits nearly the full instrument set at
once, including instruments that should never go quiet (BTC/ETH/SOL).

One incidental Rust-side error found in the window, not the general cause:
`Failed to parse orderbook snapshot for BONK-USD: ... Raw value 384766118000000000000000000000
exceeds QUANTITY_RAW_MAX=340282366920930000000000000000 for Quantity` (12:28:10). Only
coincides with one of six bursts — a real edge case (BONK's raw size in the WS snapshot
overflows `Quantity`'s max raw value) worth a separate ticket, but not the driver of the
pattern.

Crossed-book resolution (UNI/BTC/ETH/NEAR/SOL/FIL) fired correctly during the window —
expected DATA-04 behavior, not a bug.

### Root cause: host CPU + memory oversubscription on nifelheim (2 vCPU / 3.7GB, 0 swap)

- `uptime`: load average 10.85 / 9.03 / 7.60 on a 2-core box (4–5x oversubscribed).
- `docker stats`: `dydx-collector` alone at 105% CPU (continuous, >1 full core);
  `dydx-ranking-engine` at 77.85% on top of that.
- `dydx-ranking-engine` had RestartCount=430, cycling every ~2 minutes.
- Caught a live cycle via `docker events --filter container=dydx-ranking-engine`:
  ```
  container oom   dydx-ranking-engine
  container die   dydx-ranking-engine   exitCode=137   (SIGKILL)
  container start dydx-ranking-engine
  ```
  This is the **host's global OOM-killer**, not a container memory limit (`docker inspect`
  shows `Memory=0` — no cap set) and not a code-level `sys.exit`/unhandled exception (grepped
  `ranking_engine/engine.py` + deps: no `os._exit`/`sys.exit`/signal handling exists; every
  loop in `engine.py` catches `Exception` and continues).
- `free -h` at time of capture: 3.7Gi total, **0B swap**, 119Mi free / 1.0Gi available.
  Container RSS already sums close to "used" at rest (`dydx-collector` 1.1GiB +
  `dydx-dashboard` 769MiB + `bot_tui` 114MiB + others) — the box has essentially no slack
  before `ranking_engine` even accumulates its rolling-window state, so it gets OOM-killed
  a couple minutes into every restart, forever.

### Why this wasn't caught earlier

`ranking_engine/engine.py`'s `_slow_loop_task` docstring (~line 484) already documents a
prior instance of this exact failure class on 2026-09-11 (`compute_all()` defaulting to
~300 instruments × 25h lookback × 32-way concurrency was OOM-killing the container; fixed
by scoping to only the ~29 pinned/live instruments). That fix reduced per-cycle memory but
didn't add headroom — and a later commit (`180a754c6a`, "bump collector's instrument cap to
30") grew both collector's and ranking_engine's per-instrument footprint again on the same
fixed-capacity box, re-triggering the same class of OOM under a new guise (silent restart
loop instead of an obvious one-time crash).

### Conclusion

Both reported symptoms (collector stale-book bursts, ranking_engine's silent ~2min restart
loop) share one root cause: **nifelheim no longer has enough CPU/RAM headroom for the
current workload** (collector at 30 instruments + dashboard + ranking_engine + bot_tui on
2 vCPU / 3.7GB / 0 swap). This is a capacity problem, not a logic bug — per DATA-03, tuning
detection thresholds or catching-and-retrying faster would hide the symptom without fixing
the cause.

## Options discussed (not yet chosen)

1. Add swap on nifelheim — quick, reversible mitigation; doesn't fix CPU oversubscription,
   and swapping under sustained pressure trades OOM-kills for thrashing/latency.
2. Resize the VM (more RAM/vCPU) — addresses actual root cause; provider-side action.
3. Reduce load — lower collector's instrument count back down, or move
   dashboard/bot_tui/ranking_engine off this box.
4. (Chosen for now) Document and defer — no change made this session.

## Addendum (Epic 13, Story 13.1)

A follow-on session shipped `ranking_engine/engine.py`'s `_slow_loop_task` passing
`max_workers=4` to `metrics_computer.compute_all()` (previously defaulting to 32) —
bounding the number of concurrent `ParquetDataCatalog` reads on the 60s cycle. This is
an explicit, small mitigation of `ranking_engine`'s own peak memory contribution, not a
resolution of this writeup's root cause: the box's underlying CPU/RAM oversubscription
(option 4 above, "reduce load"/"resize the VM", remains unaddressed). Real before/after
`docker stats`/`free -h` evidence on nifelheim itself was not collected for this change —
this development environment had no reachable access to that host (SSH returned
`Permission denied`) — so whether this mitigation measurably reduces the OOM-restart rate
in production is still an open, deferred verification, not a confirmed result. Story 13.2
(same epic) is the actual fix: it removes the recurring Parquet re-scan from the hot path
entirely rather than just narrowing its concurrency.

## Evidence trail

- `docker logs dydx-collector --since '2026-09-12T12:00:00' --until '2026-09-12T13:35:00'`
- `docker stats --no-stream`, `uptime`, `free -h`, `ps aux --sort=-%cpu` on nifelheim
- `docker inspect dydx-ranking-engine` / `dydx-collector` (Memory, NanoCpus, RestartCount)
- `docker events --filter container=dydx-ranking-engine` (live-captured oom→die→start cycle)
- `ranking_engine/engine.py` read in full for exit paths (none found outside OOM)
