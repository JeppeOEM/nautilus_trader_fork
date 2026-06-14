# Phase 3: Reliability for 24/7 Operation - Research

**Researched:** 2026-06-14
**Domain:** Live-trading process lifecycle (SIGTERM graceful shutdown), streaming persistence flush/convert on stop, adapter-driven WebSocket reconnect, native Clock-timer heartbeat/stale-stream detection
**Confidence:** HIGH

## Summary

This phase makes the existing Bybit recorder survive unattended `Restart=always` operation. All three requirements (REL-02 shutdown flush/convert, REL-03 heartbeat/stale detection, REL-04 adapter reconnect) are served by mechanisms that already exist inside the pinned `nautilus_trader` build — **no new external packages, no custom reconnect code, and no framework patching are required**. The work is almost entirely within `scripts/bybit_recorder/strategy.py` plus unit tests.

Three findings dominate the plan. **(1) REL-04 is already solved by the adapter.** The Bybit Rust WebSocket client (`crates/adapters/bybit/src/websocket/client.rs`) has built-in exponential-backoff reconnect (initial 500ms, max 5s, factor 1.5, jitter 250ms, unlimited attempts) and automatically replays every tracked subscription via `resubscribe_all()` on reconnect. For a public market-data (unauthenticated) session it resubscribes immediately. The Python strategy must contain **zero** reconnect logic. `[VERIFIED: crates/adapters/bybit/src/websocket/client.rs]`

**(2) REL-02 has a precise, framework-supported shutdown seam.** On SIGTERM the live runner calls `TradingNode.stop()` → `kernel.stop_async()`, which calls `self._trader.stop()` (firing every strategy's `on_stop()` hook) **before** `self._close_writer()` closes the kernel `StreamingFeatherWriter`. Inside `Strategy._stop()`, `on_stop()` runs **before** the strategy's own timers are cancelled. So an `on_stop()` override is the correct place to (a) flush the strategy-owned funding writer and (b) run a final `_run_conversion()` pass while the kernel writer's feather data is still on disk. **Superseded (2026-06-14):** the original non-disjoint-intervals fix (commit `c98b1c0f80`, trimming inside `_convert_feather_table_to_parquet`) was reverted; `nautilus_trader/persistence/catalog/parquet.py` is unmodified. The shipped fix (commit `69219cca51`, Approach B) is `RecorderStrategy._convert_finalized_feather_files`: it converts only feather files that are no longer the most-recently-created file for their identifier (`files[:-1]`), skipping the still-open active file entirely. Since `StreamingFeatherWriter` stamps each new file with the creation timestamp (`writer.py` `_create_writer`/`_create_identifier_writer`), every process restart creates a NEW active file, which finalizes the PREVIOUS process's file — so the previous session's tail becomes convertible on the new process's first conversion cycle. `on_stop()`'s `_run_conversion()` call therefore mainly catches files already finalized mid-session by a `SCHEDULED_DATES` rotation; the CURRENT session's active-file tail is picked up one cycle after the NEXT restart (accepted in 03-CONTEXT.md D-03 — no data loss, one-cycle parquet-visibility delay). `[VERIFIED: nautilus_trader/system/kernel.py, nautilus_trader/common/actor.pyx, nautilus_trader/persistence/writer.py, scripts/bybit_recorder/strategy.py]`

**(3) REL-03 uses the same native `Clock.set_timer` pattern already in the recorder.** The strategy maintains a per-stream "last-seen" timestamp dict updated in the existing `on_*` handlers, and a periodic heartbeat timer compares `self.clock.timestamp_ns() - last_seen_ns` against a per-data-type threshold, logging an INFO heartbeat and a WARNING when a stream is stale. This is the Nautilus-idiomatic approach — there is no dedicated "stale stream" framework helper, but the building blocks (timer + clock + handlers) are all present and already used by `_convert_stream`. `[VERIFIED: nautilus_trader/common/component.pyx, scripts/bybit_recorder/strategy.py]`

**Primary recommendation:** Implement all three requirements inside `RecorderStrategy` using only native hooks — add `on_stop()` (final flush + convert, REL-02), per-stream last-seen tracking + a heartbeat timer (REL-03), and add nothing for reconnect (REL-04, adapter-driven). Verify with unit tests that exercise the `on_stop` convert path and the stale-threshold logic; do the disconnect/restart proof as a manual/integration check.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| REL-02 | On shutdown (SIGTERM), the recorder flushes/converts buffered data before exiting, so a restart does not lose recent data | `kernel.stop_async()` fires `on_stop()` (via `_trader.stop()`) BEFORE `_close_writer()`; `Strategy._stop()` runs `on_stop()` BEFORE cancelling timers; `on_stop()` calls `_run_conversion()` (= `_convert_finalized_feather_files` per type, commit `69219cca51`), which converts any already-finalized files. Each restart creates a new active feather file, finalizing the previous session's file, so the previous tail converts on the new process's first cycle (03-CONTEXT.md D-02/D-03 — accepted one-cycle delay, no data loss). Pattern 1 + Pitfall 1/2. |
| REL-02 (gap visibility) | On restart, log a WARNING when the gap since the last recorded `ts_init` per identifier exceeds a threshold, so operators can alert on restart-induced gaps via journald | New `on_start` check (03-CONTEXT.md D-05/D-06): query the catalog for the last converted `ts_init` per identifier and compare to `clock.timestamp_ns()` at startup. Pattern 4. |
| REL-03 | Recorder logs per-stream heartbeats and warns if any subscribed stream goes quiet beyond an expected threshold | Native `Clock.set_timer` (already used by `_convert_stream`) + last-seen dict updated in `on_trade_tick`/`on_quote_tick`/`on_order_book_deltas`/`on_bar`/`on_mark_price`/`on_index_price`/`on_funding_rate`. Pattern 2 + Pitfall 3. |
| REL-04 | Recorder relies entirely on the Bybit adapter's built-in WebSocket reconnect/resubscribe — no custom reconnect logic | Rust WS client auto-reconnect (backoff config at `client.rs:366-371`) + `resubscribe_all()` replays tracked subscriptions on `Reconnected`; unauthenticated public session resubscribes immediately. Pattern 3 + Don't-Hand-Roll. |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| WebSocket reconnect + resubscribe (REL-04) | Adapter (Rust WS client) | — | Connection lifecycle is owned by `crates/adapters/bybit/src/websocket/client.rs`; reconnect/resubscribe is below the Python boundary and must not be duplicated in the strategy. |
| Graceful-shutdown flush/convert (REL-02) | Strategy (`on_stop`) | Kernel lifecycle | The kernel orders `on_stop()` before `_close_writer()`; the strategy owns the final conversion call (it already owns `_convert_stream` and the funding writer). |
| Stale-stream detection + heartbeat (REL-03) | Strategy (timer + handlers) | Clock | "Last seen per stream" is application-level knowledge; the strategy is the only component that sees every `on_*` event and can own a heartbeat timer. |
| Signal handling (SIGTERM→stop) | Kernel / live runner | — | `NautilusKernel._setup_loop()` installs asyncio signal handlers; `TradingNode._loop_sig_handler` calls `self.stop()`. No phase code touches this. |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `nautilus_trader` (in-repo build) | pinned (this repo) | `Strategy.on_stop`, `Clock.set_timer`, `StreamingFeatherWriter`, `ParquetDataCatalog._list_feather_data_files`/`_read_feather_file`/`_convert_feather_table_to_parquet`, Bybit adapter | This is the project framework; all Phase 3 mechanisms ship inside it. `[VERIFIED: codebase]` |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `pandas` | already a dep | `pd.Timedelta` for `set_timer(interval=...)` | Heartbeat timer interval (mirrors existing `_convert_stream` timer). `[VERIFIED: scripts/bybit_recorder/strategy.py]` |
| `pytest` / `pytest-mock` | 7.4.4 | Unit tests for `on_stop` convert path and stale-threshold logic | Phase 3 test scaffolding. `[VERIFIED: CLAUDE.md]` |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Native `Clock.set_timer` heartbeat | OS-level watchdog / external monitor (systemd `WatchdogSec`) | External monitor can't see *per-stream* staleness (the WS connection stays up while one symbol goes quiet). Native timer is the only thing with per-stream visibility. Defer external watchdog to OPS phase. |
| `on_stop()` final convert | Rely solely on next-process first-conversion-cycle to recover the tail | Approach B already makes next-process recovery safe even on SIGKILL (D-02/D-03), but REL-02 explicitly requires a flush/convert *on shutdown*; `on_stop` satisfies the requirement literally (catches mid-session rotations) and shrinks the recovery window. Do both (belt-and-suspenders). |

**Installation:**
```bash
# None. No external packages are installed in this phase.
```

**Version verification:** Not applicable — no external packages. All symbols (`Strategy.on_stop`, `Clock.set_timer`, `_convert_finalized_feather_files`/`_list_feather_data_files`, Bybit WS reconnect) are verified present in the in-repo build by direct source read (see Sources).

## Package Legitimacy Audit

> Not applicable. This phase installs **no** external packages. All functionality ships in the pinned in-repo `nautilus_trader` build (same disposition as Phase 2 plan 02-01, threat `T-2-SC`: "No package installs this phase"). No legitimacy gate required.

**Packages removed due to [SLOP] verdict:** none
**Packages flagged as suspicious [SUS]:** none

## Architecture Patterns

### System Architecture Diagram

```
                              SIGTERM (systemd stop / restart)
                                        |
                                        v
              NautilusKernel._setup_loop  -> asyncio signal handler
                                        |
                          TradingNode._loop_sig_handler
                                        |
                                  TradingNode.stop()
                                        |
                                kernel.stop_async()
                                        |
            +---------------------------+----------------------------+
            | 1. self._trader.stop()    |                            |
            |    -> Strategy._stop()    |                            |
            |       -> on_stop()  <=== REL-02 HOOK (writer STILL OPEN)|
            |          - flush funding writer                        |
            |          - _run_conversion() final pass (finalized only)|
            |       -> cancel strategy timers (AFTER on_stop)        |
            | 2. _stop_clients / _disconnect_clients                 |
            | 3. _close_writer()  (kernel "*" writer closed here)    |
            +--------------------------------------------------------+

   LIVE DATA PATH (unchanged)                       RECONNECT (REL-04, adapter-owned)
   Bybit WS --> Rust WS client --> DataEngine        WS drop detected in Rust client
        |                              |                     |
        |                       msgbus "*" topic       exp-backoff reconnect (500ms..5s)
        |                              |                     |
        v                              v             resubscribe_all() replays topics
   RecorderStrategy.on_* handlers   StreamingFeatherWriter   |
        |  (REL-03: update last_seen[stream])    |       (no Python code involved)
        v                              v
   heartbeat timer (Clock.set_timer)  feather files on disk
        |                              |
   log INFO heartbeat / WARN stale   periodic _convert_stream -> ParquetDataCatalog
```

### Recommended Project Structure
```
scripts/bybit_recorder/
├── strategy.py      # ADD on_stop() (REL-02); ADD last-seen dict + heartbeat timer (REL-03)
├── config.py        # MAYBE add heartbeat_interval_seconds + per-type stale thresholds
└── recorder.py      # unchanged (signal handling is framework-owned; pass new config through)

tests/unit_tests/persistence/recorder/
├── test_recorder_strategy.py     # ADD on_stop convert + heartbeat/stale tests
├── test_recorder_conversion.py        # already covers repeated-conversion idempotency
└── test_recorder_rotation_conversion.py  # covers _convert_finalized_feather_files (Approach B)
```

### Pattern 1: Graceful-shutdown flush + final convert via `on_stop()` (REL-02)
**What:** Override `Strategy.on_stop()` to flush the strategy-owned funding writer and run a final `_run_conversion()` pass (= `_convert_finalized_feather_files` per type).
**When to use:** Always for a streaming recorder that must lose no data on restart.
**Why it is safe here:** `kernel.stop_async()` runs `self._trader.stop()` (→ every strategy `on_stop()`) BEFORE `self._close_writer()`. And `Actor._stop()` runs `on_stop()` BEFORE cancelling the strategy's own timers. So the kernel `"*"` writer's feather files are still present and flushed (the writer flushes on a ≤1s interval on every `write()`), and the strategy's funding writer is still open. A final convert here picks up any files that became finalized mid-session (e.g. a `SCHEDULED_DATES` rotation) but weren't yet caught by the periodic timer; the CURRENT session's still-active file is converted on the NEXT restart's first cycle (D-02/D-03).
```python
# Source: nautilus_trader/system/kernel.py stop_async (lines 1067-1106) +
#         nautilus_trader/common/actor.pyx _stop (lines 1245-1259) +
#         scripts/bybit_recorder/strategy.py _convert_stream/_convert_finalized_feather_files
#         (commit 69219cca51, Approach B)
def on_stop(self) -> None:
    # Flush the strategy-owned funding writer so its latest deduped rows are on
    # disk before the final convert (the kernel "*" writer self-flushes ≤1s and
    # is closed by the kernel AFTER this hook returns).
    if self._funding_writer is not None:
        self._funding_writer.flush()
    # Reuse the same per-type conversion the periodic timer uses. Converts only
    # already-finalized (rotated-out) feather files via
    # _convert_finalized_feather_files; the active file for THIS session is
    # picked up on the NEXT restart's first cycle (03-CONTEXT.md D-02/D-03). A
    # transient per-type error must be swallowed so it never blocks shutdown.
    self._run_conversion()  # extract the try/except loop body of _convert_stream
```
**Note:** `on_stop()` must NOT call `_convert_finalized_feather_files` in a way that raises out of the hook — wrap per-type in try/except exactly as `_convert_stream` already does, so a single type's transient error cannot stall the shutdown sequence. `[VERIFIED: kernel.py, actor.pyx, strategy.py]`

### Pattern 2: Native heartbeat / stale-stream detection (REL-03)
**What:** Per-stream last-seen tracking updated in the `on_*` handlers, plus a periodic heartbeat timer (a second `Clock.set_timer`, identical mechanism to `_convert_stream`).
**When to use:** When you must detect a *single quiet stream* while the WS connection stays up.
```python
# Source: nautilus_trader/common/component.pyx set_timer (419) + utc_now/timestamp_ns
#         (212, 227); mirrors the existing _convert_stream timer in strategy.py (163).
def on_start(self) -> None:
    ...  # existing subscriptions + convert-stream timer
    self.clock.set_timer(
        name="heartbeat",
        interval=pd.Timedelta(seconds=self.config.heartbeat_interval_seconds),
        callback=self._heartbeat,
    )

def on_trade_tick(self, tick: TradeTick) -> None:
    self._last_seen[("trade", tick.instrument_id)] = self.clock.timestamp_ns()
    # (same one-liner in on_quote_tick/on_order_book_deltas/on_bar/
    #  on_mark_price/on_index_price/on_funding_rate, keyed by (stream, instrument))

def _heartbeat(self, event: TimeEvent) -> None:
    now_ns = self.clock.timestamp_ns()
    for (stream, instrument_id), last_ns in self._last_seen.items():
        idle_s = (now_ns - last_ns) / 1e9
        threshold_s = self._stale_threshold_s(stream)
        if idle_s > threshold_s:
            logger.warning(
                "Stale stream: %s %s idle %.1fs (> %.0fs threshold)",
                stream, instrument_id, idle_s, threshold_s,
            )
    logger.info("Heartbeat: %d active streams", len(self._last_seen))
```
**Threshold guidance (per data type):** trades/quotes can legitimately be quiet for tens of seconds on thin spot books, so use a *generous* threshold (e.g. 60–120s) to avoid false positives; order-book deltas and bars are more regular; mark/index/funding for linear perps arrive via the **ticker channel** which pushes roughly every ~100ms, so a tight threshold (e.g. 10–30s) is appropriate for those. Make the thresholds configurable rather than hardcoded. `[ASSUMED]` for the exact second values — see Assumptions Log A1. `[VERIFIED: nautilus_trader/adapters/bybit/data.py]` for "mark/index/funding all derive from the ticker channel."

### Pattern 3: Adapter-driven reconnect (REL-04) — do nothing
**What:** Rely entirely on the Bybit Rust WS client's built-in reconnect + resubscribe.
**When to use:** Always — the strategy must contain no reconnect/resubscribe code.
```rust
// Source: crates/adapters/bybit/src/websocket/client.rs:366-371 (default config)
reconnect_timeout_ms:        Some(5_000),
reconnect_delay_initial_ms:  Some(500),
reconnect_delay_max_ms:      Some(5_000),
reconnect_backoff_factor:    Some(1.5),
reconnect_jitter_ms:         Some(250),
reconnect_max_attempts:      None,   // unlimited
// On `BybitWsMessage::Reconnected` the client marks confirmed subscriptions
// pending and, for an UNAUTHENTICATED (public market-data) session, calls
// resubscribe_all() immediately (client.rs:590-595).
```
`[VERIFIED: crates/adapters/bybit/src/websocket/client.rs]`

### Pattern 4: Gap-log line on restart (REL-02 gap visibility, D-05/D-06)
**What:** On `on_start`, for each recorded identifier, compare the last `ts_init` already in the catalog (from the previous session's finalized files) against `clock.timestamp_ns()` at startup. If the gap exceeds a threshold, log a WARNING so operators can grep journald / alert on restart-induced gaps.
**When to use:** Always — this is the only ACTIVE signal of a restart-induced gap (D-06); passive parquet-interval inspection (D-05) is not sufficient for log-based alerting.
**Why it is safe here:** `on_start` runs once at startup, before subscriptions begin delivering new data, so "last `ts_init` in the catalog" reflects exactly the previous session's tail. The check is read-only against the catalog and independent of REL-03's in-session `_last_seen` dict (which starts empty and cannot see pre-startup gaps).
```python
# Source: new on_start check, 03-CONTEXT.md D-06. Uses the same catalog query
# primitives already used by _convert_finalized_feather_files.
def on_start(self) -> None:
    ...  # existing instrument validation + subscriptions
    self._log_restart_gaps()
    ...  # existing convert-stream timer setup

def _log_restart_gaps(self) -> None:
    catalog = ParquetDataCatalog(self.config.catalog_path)
    now_ns = self.clock.timestamp_ns()
    for instrument_id in self._instrument_ids:
        last_ns = self._last_ts_init_in_catalog(catalog, instrument_id)
        if last_ns is None:
            continue  # first-ever run for this identifier, nothing to compare
        gap_s = (now_ns - last_ns) / 1e9
        if gap_s > self.config.restart_gap_threshold_seconds:
            logger.warning(
                "Resuming after gap of %.1fs for %s (last data: %s)",
                gap_s, instrument_id, pd.Timestamp(last_ns, unit="ns"),
            )
```
**Threshold guidance:** A single uniform `restart_gap_threshold_seconds` (e.g. ~60s, comfortably above normal flush/rotation cadences) is sufficient — this check fires once per process start, not per message, so false-positive cost is low and per-type granularity adds complexity without much benefit. `[ASSUMED]` exact value — Assumptions Log A1.
**Note:** This is self-contained and does NOT depend on REL-03's `_last_seen` dict (03-02-PLAN.md) — that dict is populated only by live `on_*` handlers during the current session and is empty at startup, so it cannot detect a gap that occurred before this process started.

### Anti-Patterns to Avoid
- **Hand-rolling reconnect in the Python strategy:** Duplicates the Rust client's logic, risks double-subscription, and directly violates REL-04. The adapter already does this correctly.
- **Closing/flushing the kernel `"*"` writer from `on_stop`:** The strategy does not own and cannot reach `kernel._writer`. The kernel closes it itself in `_close_writer()` AFTER `on_stop`. Only flush the *strategy-owned* funding writer; rely on the final `_run_conversion()` pass to pick up any already-finalized feather files (the kernel writer's still-active file is converted on the next restart, D-02/D-03).
- **Letting a convert error escape `on_stop`:** A raised exception in `on_stop` can disrupt the orderly stop sequence. Wrap per-type conversions in try/except (same as `_convert_stream`).
- **Treating "no persisted funding rows" as a stale funding stream:** Funding is deduped to value-changes (D-01), so persisted rows are rare. Heartbeat must track the *raw on-message-bus* arrival in `on_funding_rate` (which fires ~100ms), not persisted-row count.
- **Subscribing inside `on_stop` or assuming timers still fire after it:** Timers are cancelled right after `on_stop` returns; do all final work synchronously inside the hook.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| WebSocket reconnect/backoff | Custom asyncio reconnect loop in Python | Bybit Rust WS client (automatic) | Already implemented with exp-backoff + jitter + unlimited attempts; REL-04 forbids custom code. |
| Resubscribe after reconnect | Re-issuing `subscribe_*` calls on a reconnect callback | `resubscribe_all()` in Rust client | Subscriptions are tracked in the Rust client and replayed automatically; manual replay risks duplicates. |
| Same-day restart de-dup of converted intervals | Manual "what did I already convert?" bookkeeping, or trimming inside `parquet.py` | `_convert_finalized_feather_files` (Approach B, `69219cca51`): convert only `files[:-1]` per identifier, skip the still-open active file | `parquet.py` stays unmodified; a finalized file's content never changes again, so converting it once is naturally idempotent. No app-level interval bookkeeping needed. |
| Signal handling (SIGTERM→graceful stop) | Custom `signal.signal` handler in `recorder.py` | `NautilusKernel._setup_loop()` (installs asyncio handlers) + `TradingNode._loop_sig_handler` | Framework already wires SIGTERM/SIGINT/SIGABRT to a clean `stop()`; a custom handler would race the framework's. |
| Periodic callbacks (heartbeat) | `threading.Timer` / asyncio task | `Clock.set_timer` | Runs on the single-threaded event loop, consistent with the existing `_convert_stream` timer; deterministic and testable with `TestClock`. |

**Key insight:** Phase 3 is almost entirely *wiring into existing hooks*, not building infrastructure. The only genuinely new application logic is the per-stream last-seen dict + stale-threshold comparison (REL-03).

## Runtime State Inventory

> This is not a rename/refactor phase, but it has restart-correctness implications, so the relevant runtime state is inventoried below.

| Category | Items Found | Action Required |
|----------|-------------|------------------|
| Stored data | `catalog/streaming/live/{instance_id}/{type}/{identifier}_{timestamp}.feather` (kernel "*" writer) and `.../funding/...feather` (strategy funding writer). On restart with the FIXED `instance_id` (`8f1b9c2e-...`), each writer stamps NEW filenames with the new process's start time (`writer.py` `_create_writer`/`_create_identifier_writer`), so the previous run's files become "finalized" (no longer last) as soon as the new process writes anything for that identifier. | Verify (test) that `_convert_finalized_feather_files` converts the now-finalized previous-run files on the new process's first cycle — no gap, no overlap, no non-disjoint error. Phase 3 adds the `on_stop` pass (catches mid-session rotations) and the gap-log line (D-06, surfaces the restart boundary in logs). |
| Live service config | None — recorder is single-process; no externally-stored subscription config. Subscriptions are re-issued by `on_start` each process, and the Rust client tracks them in-memory for reconnect resubscribe. | None. |
| OS-registered state | systemd unit (added in Phase 5 OPS-02, not this phase). `Restart=always` + default `SIGTERM` stop signal will drive the `on_stop` path. | None this phase; note for Phase 5 that the default systemd `KillSignal=SIGTERM` is what triggers `on_stop`. |
| Secrets/env vars | Public market data only — no API keys required for the recorded streams. | None. |
| Build artifacts | Bybit reconnect logic is in compiled Rust (`crates/adapters/bybit`), already built into the installed wheel. | None — no rebuild needed; behavior is verified by source read. |

**The canonical question (same-day restart):** After SIGTERM→`on_stop` final convert (which generally cannot convert this session's still-active file, D-02/D-03), the next process restart creates new active feather files, finalizing the previous run's files; `_convert_finalized_feather_files` converts those finalized files on the new process's first cycle. **Verified by source read of `writer.py` `_create_writer`/`_create_identifier_writer` (filename = `{identifier}_{timestamp}.feather`, new timestamp per process start) and `strategy.py` `_convert_finalized_feather_files`/`_list_feather_data_files`.** A SIGKILL (no `on_stop`) is also safe because the next process recovers the previous file's full tail on its first conversion cycle (the file is finalized the moment the new process creates its own file) — the only true loss window is the <1s feather-write buffer not yet flushed to disk at SIGKILL, which a clean SIGTERM's `on_stop` does not change either (the kernel writer self-flushes on its own ≤1s cadence regardless).

## Common Pitfalls

### Pitfall 1: Expecting the kernel "*" writer to be flushed/closed by `on_stop`
**What goes wrong:** Developer tries to call `self.kernel._writer.flush()`/`.close()` from the strategy and finds the attribute is unreachable, or assumes the writer is already closed when `on_stop` runs.
**Why it happens:** The strategy has no reference to `kernel._writer`; and the kernel closes it AFTER `on_stop` (in `_close_writer()`).
**How to avoid:** In `on_stop`, only flush the *strategy-owned* funding writer, then call `_run_conversion()`. The kernel writer self-flushes on a ≤1s cadence (every `write()` calls `check_flush()` with `flush_interval_ms=1000`), so its feather tail is on disk (though not necessarily convertible until the next restart finalizes the file — D-02/D-03). `[VERIFIED: writer.py:276, 568-576; kernel.py:1101, 1454-1456]`
**Warning signs:** `AttributeError` on `kernel`, or expecting `on_stop`'s conversion to include this session's still-active file.

### Pitfall 2: Expecting `on_stop`'s conversion to cover THIS session's active file (it generally can't — by design)
**What goes wrong:** A test or operator expects `on_stop()`'s `_run_conversion()` to convert the rows written during the CURRENT session into parquet immediately.
**Why it happens:** `_convert_finalized_feather_files` deliberately skips the most-recently-created (`files[:-1]` excludes the last) file per identifier — that file is still "active" until the NEXT process creates a newer one for the same identifier.
**How to avoid:** Accept the one-cycle delay (03-CONTEXT.md D-03): the active file's tail becomes convertible on the NEXT restart's first conversion cycle, when a new file is created and finalizes the old one. `on_stop()`'s `_run_conversion()` call still has value — it converts any file ALREADY finalized mid-session by a `SCHEDULED_DATES` rotation that the periodic timer hasn't caught yet. Do NOT re-introduce trimming/interval-bookkeeping inside `parquet.py` to try to make the active file convertible at stop time (out of scope, reverted approach).
**Warning signs:** Tests asserting `on_stop` converts rows written in the same test/session without simulating a restart (new `StreamingFeatherWriter` instance with a later creation timestamp). `[VERIFIED: strategy.py _convert_finalized_feather_files, writer.py file-naming, resolved debug note]`

### Pitfall 3: False-positive stale warnings from too-tight thresholds (esp. thin spot)
**What goes wrong:** A blanket short threshold (e.g. 5s) fires spurious WARNINGs for trades/quotes on a low-volume spot symbol that is simply quiet.
**Why it happens:** Trade/quote arrival is bursty and venue/volume dependent; absence of a tick is not necessarily a fault. Mark/index/funding (linear ticker) are far more regular.
**How to avoid:** Use *per-data-type* thresholds, generous for trades/quotes (tens of seconds to minutes), tighter for the linear ticker-derived streams. Make them configurable in `recorder.toml`. `[ASSUMED]` exact values — Assumptions Log A1.
**Warning signs:** Log spam of stale warnings during normal low-volume periods.

### Pitfall 4: `on_funding_rate` stale tracking using persisted rows instead of raw arrivals
**What goes wrong:** Heartbeat marks funding "stale" because few rows were *persisted*, when in fact the ticker is flowing fine.
**Why it happens:** Funding is deduped to value-changes (D-01), so persisted rows are intentionally rare while the underlying ticker pushes ~100ms.
**How to avoid:** Update the funding last-seen timestamp at the TOP of `on_funding_rate` (before the dedup gate returns), so staleness reflects stream liveness, not value-change frequency. `[VERIFIED: strategy.py:231-248]`
**Warning signs:** Funding flagged stale while trades/quotes/marks for the same linear instrument are healthy.

## Code Examples

### Final convert on shutdown (refactor the existing `_convert_stream` body)
```python
# Source: scripts/bybit_recorder/strategy.py _convert_stream/_convert_finalized_feather_files
# (commit 69219cca51, Approach B) — extract the conversion loop so both the timer
# callback and on_stop can call it.
def _run_conversion(self) -> None:
    catalog = ParquetDataCatalog(self.config.catalog_path)
    if self._funding_writer is not None:
        self._funding_writer.flush()
    for data_cls in [*_RECORDED_TYPES, FundingRateUpdate]:
        try:
            self._convert_finalized_feather_files(catalog, data_cls)
        except Exception:
            logger.exception("Failed to convert %s stream to catalog", data_cls.__name__)

def _convert_stream(self, event: TimeEvent) -> None:
    self._run_conversion()

def on_stop(self) -> None:
    self._run_conversion()  # converts any files already finalized mid-session;
    # this session's active file is picked up on the NEXT restart (D-02/D-03)
```

### Heartbeat timer + last-seen update (see Pattern 2 for the handler one-liners)
```python
# Source: mirrors existing strategy.py on_start timer setup (163-167).
self.clock.set_timer(
    name="heartbeat",
    interval=pd.Timedelta(seconds=self.config.heartbeat_interval_seconds),
    callback=self._heartbeat,
)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Same-day restart conversion raised non-disjoint ValueError | Trimming inside `_convert_feather_table_to_parquet` (reverted) | commit `c98b1c0f80` (2026-06-14, reverted) | Superseded by Approach B (below) — `parquet.py` is unmodified. |
| Trimming-based fix (above) | `RecorderStrategy._convert_finalized_feather_files` converts only `files[:-1]` per identifier, skipping the still-active file | commit `69219cca51` (2026-06-14) | Same-day restart and periodic mid-day conversion are both safe; REL-02 accepts a one-cycle parquet-visibility delay on restart (D-02/D-03) with no data loss; `parquet.py` stays unmodified. |
| (general) custom reconnect loops in adapters | Centralized Rust WS reconnect + resubscribe | current build | REL-04 is satisfied by configuration/defaults, not code. |

**Deprecated/outdated:**
- Any pattern that hand-rolls reconnect or interval-dedup in the Python strategy — both are now handled below the strategy layer.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | Suggested stale thresholds (~60–120s for trades/quotes; ~10–30s for linear ticker-derived mark/index/funding) | Pattern 2, Pitfall 3 | If too tight → false-positive WARN spam; if too loose → slow detection of a genuinely dead stream. Mitigated by making thresholds configurable and letting the user tune after observing real cadence. Recommend confirming exact values during discuss-phase or via a short live observation. |
| A2 | systemd default stop signal is SIGTERM, which is the signal `_setup_loop` handles | Runtime State Inventory | If the Phase 5 unit overrides `KillSignal`, the `on_stop` path might not fire. Low risk — SIGTERM is the systemd default and is explicitly handled; flag for Phase 5 unit-file authoring. |

## Open Questions

1. **Exact per-stream stale thresholds**
   - What we know: linear mark/index/funding derive from the ~100ms ticker channel (regular); trades/quotes are bursty and volume-dependent.
   - What's unclear: the precise second values that minimize false positives for the user's actual instrument set.
   - Recommendation: make thresholds configurable in `recorder.toml` with sensible defaults (A1) and let the operator tune; optionally derive a default trade/quote threshold from the bar interval.

2. **Should `on_stop` also persist a final funding `flush` for instruments that never changed?**
   - What we know: funding writer is lazily created on first persisted (changed) rate; if a value never changed in a session, `_funding_writer` may be `None`.
   - What's unclear: nothing blocking — a `None` writer simply means no funding rows to flush; the guard already handles it.
   - Recommendation: keep the `if self._funding_writer is not None` guard; no extra work.

## Environment Availability

> The phase's runtime mechanisms are all in-process Python/Rust already present. External-service availability matters only for the manual reconnect/restart proof.

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| `nautilus_trader` in-repo build (on_stop, set_timer, `_convert_finalized_feather_files`/`_list_feather_data_files`, Bybit WS) | REL-02/03/04 | ✓ | this repo (pinned) | — |
| Bybit mainnet WS reachability | REL-04 manual disconnect proof, REL-02 same-day-restart proof | ✓ (used in Phase 2 live smoke, PASSED) | — | Unit tests cover logic deterministically with `TestClock`; live proof is a manual/integration step. |
| `pytest` / `pytest-mock` | Unit tests | ✓ | 7.4.4 | — |

**Missing dependencies with no fallback:** none
**Missing dependencies with fallback:** Live Bybit WS for the disconnect/restart proof — fall back to deterministic unit tests (`TestClock`) for the `on_stop` convert path and the stale-threshold logic; treat the forced-disconnect and stop-then-start checks as manual integration verification (mirrors Phase 2's "live mainnet smoke" task).

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 7.4.4 (+ pytest-mock) |
| Config file | repo-level `pyproject.toml` (ruff/mypy/pytest config); recorder tests under `tests/unit_tests/persistence/recorder/` |
| Quick run command | `python -m pytest tests/unit_tests/persistence/recorder/ -q` |
| Full suite command | `python -m pytest tests/unit_tests/persistence/recorder/ tests/unit_tests/persistence/test_catalog.py -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REL-02 | `on_stop()` runs a final flush + `_run_conversion()` (`_convert_finalized_feather_files` per type); already-finalized feather files land in parquet | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k on_stop -x` | ❌ Wave 0 |
| REL-02 | Restart-shaped scenario: a previous-session file (older creation timestamp) becomes finalized once a new-session file exists for the same identifier; `_convert_finalized_feather_files` converts the previous file's full content, no gap/overlap/raise | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_rotation_conversion.py -k "skips_active_file or finalized" -x` | ✅ (`test_recorder_rotation_conversion.py` covers the core contract; add an explicit two-process-restart-shaped case if not already present) |
| REL-02 (gap visibility) | `on_start` logs a WARNING when the gap since the last catalog `ts_init` per identifier exceeds `restart_gap_threshold_seconds`; no WARNING when the gap is within threshold or no prior data exists | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k "restart_gap or gap_log" -x` | ❌ Wave 0 |
| REL-03 | Heartbeat timer logs and WARNs when a stream's idle time exceeds its per-type threshold; no WARN when fresh | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k "heartbeat or stale" -x` | ❌ Wave 0 |
| REL-03 | Each `on_*` handler updates `last_seen[(stream, instrument)]`; `on_funding_rate` updates BEFORE the dedup gate | unit | `pytest tests/unit_tests/persistence/recorder/test_recorder_strategy.py -k "last_seen or heartbeat" -x` | ❌ Wave 0 |
| REL-04 | Adapter-driven — no Python reconnect code exists | structural | `! grep -rn "reconnect\|resubscribe" scripts/bybit_recorder/` (must find nothing) + manual forced-disconnect proof | ✅ (assertion is "absence"); live proof manual |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/unit_tests/persistence/recorder/ -q`
- **Per wave merge:** `python -m pytest tests/unit_tests/persistence/recorder/ tests/unit_tests/persistence/test_catalog.py -q`
- **Phase gate:** Full recorder suite green + the manual forced-disconnect and stop-then-start (same UTC day) integration checks confirmed before `/gsd-verify-work`.

### Wave 0 Gaps
- [ ] `tests/unit_tests/persistence/recorder/test_recorder_strategy.py` — add `on_stop` final-convert test (spy/assert `_convert_finalized_feather_files` called per type via `_run_conversion`; assert funding writer flushed) and heartbeat/stale-threshold tests using `TestClock` to advance time past/under thresholds.
- [ ] Test for `on_funding_rate` updating last-seen BEFORE the dedup early-return (Pitfall 4).
- [ ] (optional) An explicit two-process-restart-shaped conversion test (two `StreamingFeatherWriter` instances with different creation timestamps) confirming the first writer's file is converted in full once finalized — extends `test_recorder_rotation_conversion.py`'s coverage.
- [ ] New `on_start` gap-log test (D-06): seed the catalog with a finalized interval ending at `T`, advance `TestClock` past `T + restart_gap_threshold_seconds`, call `on_start`, assert a WARNING is logged with the gap and identifier; also assert NO warning when the gap is below threshold or no prior data exists.
- [ ] No framework install needed — pytest infra already present.

## Security Domain

> `security_enforcement` defaults to enabled; included for completeness. This phase processes only public market data and operator-controlled config — no new credential surface vs. Phase 2.

### Applicable ASVS Categories
| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Public market-data WS; no auth on recorded streams (Bybit public channels resubscribe without re-auth). |
| V3 Session Management | no | No user sessions. |
| V4 Access Control | no | No multi-user surface. |
| V5 Input Validation | yes (carried) | New config (`heartbeat_interval_seconds`, per-type thresholds) parsed from `recorder.toml`; validate as PositiveInt/PositiveFloat at load (mirror existing depth fail-fast). |
| V6 Cryptography | no | None introduced. |
| V7 Error/Log handling | yes | Heartbeat/stale logs must not leak secrets (none present) and must avoid log spam (Pitfall 3); keep count-only/identifier logging consistent with D-09. |

### Known Threat Patterns for the recorder
| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Operator sets an absurd heartbeat interval / threshold (DoS-of-logs or never-warns) | Tampering / DoS-of-observability | Validate new config values as positive at load time (fail fast), same pattern as Phase 2 depth validation. |
| Silent data loss on ungraceful kill (SIGKILL) | Repudiation / data integrity | `on_stop` shrinks the loss window on SIGTERM; Approach B (`_convert_finalized_feather_files`) makes next-process recovery of the previous session's full file safe even on SIGKILL (D-02/D-03). Document the residual <1s un-flushed window. |
| Stale-stream false negatives (dead stream undetected) | Information disclosure (operational blindness) | Per-type thresholds tuned to real cadence; funding last-seen tracked pre-dedup (Pitfall 4). |

## Sources

### Primary (HIGH confidence)
- `nautilus_trader/system/kernel.py` — `stop_async` (1067-1106) orders `_trader.stop()` before `_close_writer()`; `_setup_loop`/`_loop_sig_handler` signal wiring (558-582); `_flush_writer`/`_close_writer` (1450-1456); `_setup_streaming` (587-611).
- `nautilus_trader/common/actor.pyx` — `_stop` (1245-1259) calls `on_stop()` before cancelling timers; `on_stop` hook docstring (269).
- `nautilus_trader/live/node.py` — `_loop_sig_handler` → `stop()` (491-493); `stop`/`stop_async`/`dispose` (381-473).
- `nautilus_trader/persistence/writer.py` — `write`→`check_flush` (276), `flush`/`close` (568-601), `flush_interval_ms` default 1000 (157).
- `nautilus_trader/persistence/catalog/parquet.py` — `convert_stream_to_data` (2523-2573), `_list_feather_data_files`, `_read_feather_file`, `_convert_feather_table_to_parquet` (2597-2630) — unmodified primitives reused by `_convert_finalized_feather_files`.
- `scripts/bybit_recorder/strategy.py` (commit `69219cca51`) — `_convert_stream`, `_convert_finalized_feather_files` (Approach B implementation).
- `crates/adapters/bybit/src/websocket/client.rs` — reconnect defaults (366-371), `resubscribe_all` + `Reconnected` handling (481-611).
- `nautilus_trader/adapters/bybit/data.py` — mark/index/funding sourced from the shared `ticker` channel (345-412).
- `nautilus_trader/common/component.pyx` — `set_timer` (419), `timestamp_ns` (212), `utc_now` (227).
- `scripts/bybit_recorder/strategy.py`, `config.py`, `recorder.py` — current recorder implementation.
- `.planning/debug/resolved/non-disjoint-intervals.md` — root cause + fix for the same-day-restart/periodic-conversion contract.

### Secondary (MEDIUM confidence)
- Phase 2 plan `02-01-PLAN.md` and STATE.md decisions (funding dedup via separate writer; fixed `instance_id`; single shared streaming root).

### Tertiary (LOW confidence)
- Bybit linear ticker push cadence (~100ms) and exact stale-threshold second values — training knowledge, not re-confirmed via web (web providers disabled). Tagged `[ASSUMED]` (A1).

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all mechanisms verified by direct source read; no external packages.
- Architecture (lifecycle ordering for REL-02): HIGH — `stop_async` and `_stop` ordering read directly.
- Reconnect (REL-04): HIGH — Rust client reconnect + resubscribe read directly.
- Heartbeat (REL-03): HIGH for the mechanism (native timer/clock), MEDIUM for the exact thresholds (A1).
- Pitfalls: HIGH — each grounded in a specific source line or the resolved debug note.

**Research date:** 2026-06-14
**Valid until:** ~2026-07-14 (stable in-repo APIs; re-check if `nautilus_trader` is re-pinned or the Bybit adapter is upgraded).
