---
status: awaiting_human_verify
trigger: "you need to debug why memory leaks/excalation computer just crashed trying to run the dydx recorder, after i tried to shut it down with control c it kept haning and flooded the memory. I need to be able to collect data and save it in Paraquet. Investigate"
created: 2026-06-19T19:51:54Z
updated: 2026-06-20T07:10:00Z
---

<!-- PAUSED 2026-06-20T07:10:00Z: user has not yet live-verified cycle 5 (the feather-deletion fix) and instead requested a strategic pivot — moving the data collector to a standalone service outside Nautilus's live TradingNode, while staying output-compatible with ParquetDataCatalog for backtesting. This is being handled as a separate planning effort (EnterPlanMode), not inside this debug session. The cycle 1-5 fixes (executor-offload conversion, early WS disconnect, mem-watch instrumentation, shutdown watchdog + durable kill record, gap-visibility self.log re-routing, feather-deletion-after-conversion) remain applied and are believed to be genuine improvements regardless of the architecture decision, but are UNVERIFIED live. Resume with `/gsd-debug continue dydx-recorder-memory-leak` if/when the user wants to keep iterating on the current Nautilus-based recorder instead of or alongside the standalone-service path. -->

## Current Focus
<!-- OVERWRITE on each update - always reflects NOW -->

hypothesis: "(cycle 4 — STRONG, code-confirmed, NOT yet live-verified) Two DISTINCT, compounding root causes, both rooted in nautilus_trader CORE code (off-limits to edit) that the recorder must work AROUND: (A) THE UNKILLABLE HANG: nautilus_trader/system/kernel.py's `_loop_sig_handler` (line 574-582) REPLACES the SIGINT handler with a no-op lambda (`self._loop.add_signal_handler(signal.SIGINT, lambda: None)`) the INSTANT the first Ctrl+C is processed — this is intentional (prevents re-entrant shutdown) but means EVERY subsequent Ctrl+C the user presses does ABSOLUTELY NOTHING, by design, for as long as the loop runs. This fully explains 'I CANNOT on multiple ctrl c even close the process AT ALL'. The real question is why the FIRST stop sequence sometimes never completes: nautilus_trader/live/data_engine.py's `_on_stop` -> `_enqueue_sentinels()` (line 368-373) inserts shutdown sentinels via `queue.put_nowait(sentinel)` scheduled through `call_soon_threadsafe` — `put_nowait` RAISES `asyncio.QueueFull` (silently swallowed by asyncio's default loop exception handler, just logged-and-continued) if the queue is AT MAXSIZE at that exact instant. The data_queue is exactly the queue that gets saturated under load (per cycle-2/3 evidence: ThrottledEnqueuer in live/enqueue.py falls back to unbounded `create_task(queue.put(msg))` once `_queue.qsize() >= maxsize`). If the sentinel attempt loses this race, `_run_data_queue()`'s consumer loop (which awaits `queue.get()` forever, looking for the sentinel to break) NEVER terminates — `asyncio.gather(*tasks)` inside `TradingNode.run_async()` never completes — `node.run()` (blocking on `loop.run_until_complete`) NEVER RETURNS — the process hangs forever at exactly that point, with NO further log output and NO way to interrupt it (per finding A). This is a RACE: it depends on real-time load at the precise moment Ctrl+C is pressed, which explains the 'always been flaky' / inconsistent symptom (the ONE clean full-DISPOSED capture this session was a case where the queue happened to have room when the sentinel attempt ran). (B) THE INVISIBLE GAP-VISIBILITY: confirmed via grep that EVERY recorder-authored diagnostic the user actually needs for the stated TOP PRIORITY ('visible where there are holes in the data') — the REL-02 restart-gap WARNING (strategy.py:592), the stale-stream WARNING (:647-648), the heartbeat (:655), and all hot-reload/config-diff logs (:883,933,948,953,986) — are emitted via the STDLIB `logger` (module-level `logging.getLogger(__name__)`, strategy.py:51), which the pyo3 (`use_pyo3=True`) logging backend does NOT capture into logs/dydx_recorder.log. Only WARNING+ stdlib messages leak through Python's logging 'handler of last resort' to stderr as bare unformatted lines (exactly matching the unprefixed 'Stale stream: ...' line the user pasted, vs the fully-formatted '[INFO] ...' mem-watch lines which ARE pyo3-routed via the cycle-3b fix). INFO-level diagnostics (heartbeat, hot-reload) are below stderr's default WARNING threshold and are LOST ENTIRELY. This means the recorder's existing gap-detection features have likely never actually been visible to the user in any persistent, reliable way."
test: "(A) Implement a recorder-side watchdog/safety-valve in scripts/dydx_recorder/recorder.py: install our OWN signal handler for a SECOND SIGINT (or a separate timer-based force-exit) that, if the node has not fully disposed within a bounded grace period after the first Ctrl+C, calls os._exit() (or equivalent) so the user always has a way to actually stop the process — this cannot fix nautilus's internal sentinel race (core, off-limits) but guarantees the symptom 'cannot close it AT ALL' is eliminated. Also consider raising LiveDataEngineConfig.qsize (recorder-side config) to reduce the PROBABILITY of the data_queue being exactly full at the stop instant (does not eliminate the race, reduces incidence). (B) Re-route EVERY diagnostic logger.* call in scripts/common_recorder/strategy.py listed above through self.log (the pyo3-routed Nautilus Logger, same fix already proven for mem-watch in cycle 3b) so restart-gap/stale-stream/heartbeat/hot-reload output actually lands in logs/dydx_recorder.log persistently and queryably — directly serving the user's stated top priority."
expecting: "(A) With a watchdog in place, the user should be able to ALWAYS terminate the process within a bounded time, even if nautilus's internal sentinel race is hit (graceful path may still occasionally hang, but the process no longer requires kill -9 from another terminal). (B) After re-routing, logs/dydx_recorder.log should show real restart-gap WARNINGs / stale-stream WARNINGs / heartbeats with full timestamp/instrument context after a fix lands and the user restarts — verify by re-running and confirming these lines now appear with the same '[INFO]'/'[WARNING]' pyo3-formatted prefix as the mem-watch lines."
next_action: "(cycle 5 — NEW, strong, code-confirmed root cause for the SURVIVING memory growth) User asked, while pasting repeated 'File .../quote_tick/.../<various time ranges hours apart>.parquet already exists, skipping write' lines seen at every shutdown: does this relate to the memory issue? YES — confirmed via code read of nautilus_trader/persistence/catalog/parquet.py (core, read-only) cross-referenced with scripts/common_recorder/strategy.py `_convert_finalized_feather_files` (:1392-1429, recorder-side, EDITABLE). `_convert_finalized_feather_files` calls `catalog._list_feather_data_files(...)` EVERY conversion pass (periodic every conversion_interval_minutes=60 AND on EVERY on_stop/Ctrl+C) and, for every file except the most-recent-per-directory (still-active) one, ALWAYS calls `catalog._read_feather_file(feather_file.path)` — which reads the ENTIRE feather file into an in-memory pyarrow Table — REGARDLESS of whether that file was already successfully converted to parquet in a PRIOR cycle. The 'already exists, skipping write' message (parquet.py:2603) fires INSIDE `_convert_feather_table_to_parquet`, which only runs AFTER the expensive read already happened — so the skip avoids a redundant WRITE but NOT the redundant READ. Feather source files are NEVER deleted/archived after conversion, so they accumulate forever in the streaming directory; every single conversion pass (including the one forced at every Ctrl+C, per MEM-01/cycle-1) re-reads the ENTIRE growing backlog of already-converted historical feather files into memory, every time. This means conversion memory cost scales with TOTAL ELAPSED RECORDER UPTIME (more history = more redundant files re-read every cycle), not just with the data accumulated since the last conversion — explaining why memory growth has SURVIVED cycles 1-4 (none of them touched this — cycle 1 only moved the work off the event loop thread, it did not reduce the amount of redundant work or its memory footprint) and explaining the user's literal observed symptom (the SAME historical files, spanning hours back, being reprocessed at every single shutdown). FIX (not yet implemented): in `_convert_finalized_feather_files`'s loop, after a feather_file has been read+converted (regardless of whether the parquet write was skipped because it already existed), DELETE the feather source file via `catalog.fs.rm(feather_file.path)` (catalog.fs is a standard fsspec.AbstractFileSystem, confirmed parquet.py:157) so it is never listed or re-read again. This is safe: nautilus's own interval-tracking (`_get_directory_intervals`, parquet.py:2606) reads from the PARQUET output directory only, never depends on feather sources surviving; the recorder's own contract already treats converted data as durably captured in parquet (cycle-1 evidence: 'no data loss: finalized files convert on next cycle/restart'); the loop already explicitly never touches the still-open most-recent file (`files[:-1]`), so nothing live is ever at risk of deletion. This is fully recorder-side (scripts/common_recorder/strategy.py only) — no core file touched."
reasoning_checkpoint:
  hypothesis: "Two recorder-side workarounds eliminate the two code-confirmed core-level failures: (A) a daemon watchdog thread armed at the FIRST SIGINT (via a low-level signal.signal handler registered BEFORE node.run, which asyncio's loop.add_signal_handler then chains/overrides at the loop level but does NOT prevent the C-level handler from having already recorded the first-SIGINT timestamp) that calls os._exit(N) after grace_seconds if the node has not disposed — guaranteeing bounded termination even when the core sentinel-loss race wedges the loop. (B) routing every gap-visibility diagnostic through self.log (pyo3) instead of stdlib logger makes restart-gap/stale/heartbeat/hot-reload lines land in logs/dydx_recorder.log."
  confirming_evidence:
    - "kernel.py:579 replaces the asyncio loop-level SIGINT handler with a no-op lambda after first SIGINT — so a SECOND asyncio-level handler is unreachable; a separate OS-signal handler + daemon thread is the only mechanism that survives a wedged loop (evidence 2026-06-20T02:20:00Z finding A)."
    - "data_engine.py _enqueue_sentinels uses put_nowait which raises QueueFull (swallowed) under the exact full-data_queue load this recorder produces -> _run_data_queue never breaks -> node.run() never returns (finding C). A thread calling os._exit bypasses the wedged loop entirely."
    - "grep-confirmed every diagnostic (strategy.py restart-gap :592, stale :647, heartbeat :655, hot-reload :883/933/948/953/986) uses stdlib `logger` not self.log; only the cycle-3b mem-watch line (self.log) ever reached the pyo3 log file (finding D + evidence 2026-06-20T00:10:00Z)."
  falsification_test: "(A) Unit: with the node-disposed flag never set, the watchdog's deadline-check function must return the os._exit decision once monotonic time passes the deadline (os._exit mocked); and must NOT exit before the deadline or when disposed flag is set. Live: a real Ctrl+C on a wedged run must terminate within grace_seconds without a second terminal's kill -9. (B) Unit: the diagnostic methods must NOT emit on the stdlib `scripts.*.strategy` logger (caplog empty) and must call the extracted message-builders; live: the lines appear in logs/dydx_recorder.log with the [INFO]/[WARNING] pyo3 prefix."
  fix_rationale: "Cannot patch the core sentinel race (off-limits), so (A) provides an INDEPENDENT escape hatch at the OS/thread level that does not depend on the asyncio loop making progress — directly addresses 'cannot close it AT ALL'. (B) addresses the root cause of invisible gaps (wrong logger backend), not a symptom."
  blind_spots: "The live wedge scenario is inherently hard to reproduce in CI (it is a load-dependent race in core). Unit tests prove the watchdog's arming/deadline/exit-decision logic and the logger routing, but ONLY a live Ctrl+C under load can confirm the watchdog actually fires against a real wedged loop — this is the 4th cycle and 3 prior fixes passed units but failed live, so a CHECKPOINT for live verification is mandatory before declaring resolved. Also: os._exit skips the conversion-executor drain in main()'s finally, so a watchdog-forced exit may leave the most-recent feather tail unconverted until next restart (acceptable per D-02/D-03 no-data-loss: finalized files convert next start)."
tdd_checkpoint: null
cycle5_reasoning_checkpoint:
  hypothesis: "The memory growth that SURVIVED cycles 1-4 is caused by `_convert_finalized_feather_files` (scripts/common_recorder/strategy.py) re-reading the ENTIRE growing backlog of already-converted feather source files into in-memory pyarrow Tables on EVERY conversion pass, because feather sources are never deleted after conversion. The fix deletes each finalized feather source via `catalog.fs.rm(...)` after a successful read+convert so it is never re-listed/re-read again."
  confirming_evidence:
    - "parquet.py:2602-2604 — the 'already exists, skipping write' print + early return lives INSIDE `_convert_feather_table_to_parquet`, AFTER `_read_feather_file` (called at strategy.py:1420) has already materialized the whole feather file into a pyarrow Table. The skip spares the WRITE, never the READ. Confirmed by direct code read."
    - "parquet.py:2370-2382 `_get_directory_intervals` globs `*.parquet` only (`self.fs.glob(.../*.parquet)`); nautilus's interval bookkeeping has ZERO dependency on feather sources surviving — so deleting them is safe for the no-overlap invariant. Confirmed by direct code read."
    - "strategy.py:1419 the loop already iterates `files[:-1]` per directory, so the still-active (most-recent) file per identifier is NEVER a deletion candidate — only known-finalized, immutable files can be deleted. Confirmed by direct code read + a real-fs test asserting the active file survives on disk."
    - "fsspec local filesystem exposes a standard `.rm(path)` (verified at runtime: `inspect.signature(fs.rm) == (path, recursive=False, maxdepth=None)`); `catalog.fs` is `fsspec.AbstractFileSystem` (parquet.py:157)."
  falsification_test: "Unit (FakeCatalog, deterministic): (a) a finalized file IS rm'd after a non-raising convert; (b) a finalized file IS rm'd even when convert returns normally via the already-exists/skip-write path (user's exact scenario); (c) convert raising -> file NOT rm'd; (c') read->None -> file NOT rm'd and never converted; (d) the most-recent file per directory is NEVER read/converted/rm'd; (e) an rm exception is logged and does NOT crash the pass (later files still convert+delete). PLUS a real-filesystem restart-shaped test asserting the finalized feather file is gone from disk after conversion while the active file remains. ALL PASS. Live falsification (mandatory): if RSS still climbs at conversion time after this ships, OR old feather files still accumulate in the streaming dir after a conversion cycle, the hypothesis is wrong/incomplete."
  fix_rationale: "Deleting the converted source removes the redundant per-pass read (the actual memory cost), so conversion RAM stops scaling with total uptime and instead scales only with the data accumulated since the last conversion. This addresses the ROOT mechanism (unbounded re-read of an unbounded backlog), not a symptom. Cycle 1 only moved this same read off the loop thread — it never reduced the read's footprint, which is why growth survived."
  blind_spots: "Code-confirmed + unit-tested, but (per the 4-cycle norm) NOT yet live-verified: only a real extended run across >=1 conversion cycle can confirm (i) RSS no longer climbs at conversion time and (ii) old feather files are actually removed from the streaming directory. The FakeCatalog tests assert the deletion CONTRACT; the real-fs test asserts deletion against an actual filesystem; but neither measures live RSS under the real dYdX full-depth load. Also: this fix is orthogonal to (and retains) cycles 1-4 (watchdog, WS early-disconnect, self.log re-route) — if live RSS growth persists, the remaining suspect reverts to the steady-state ThrottledEnqueuer overflow (cycle 3, never refuted at scale), not this conversion path."
_superseded_reasoning_checkpoint:
  hypothesis: "pending_tasks logs 'n/a' on every heartbeat because _pending_task_count() calls asyncio.get_running_loop(), but the heartbeat callback executes on a Nautilus-global tokio runtime worker thread (LiveTimer), where no asyncio loop is bound, so get_running_loop() raises RuntimeError and is swallowed to None."
  confirming_evidence:
    - "crates/common/src/live/timer.rs:195-234 — LiveTimer fires via get_runtime().spawn(async{...}); for a Python callback with no `sender` it calls callback.call(event) directly on that tokio worker thread (with_gil), not on the asyncio loop thread."
    - "Cython LiveClock.set_timer_ns (component.pyx) calls live_clock_set_timer with NO TimeEventSender wired, so the Python-callback branch (the on-tokio-thread call) is taken — not the event-loop-registry sender branch."
    - "Symptom asymmetry: rss_mb (from /proc/self/status, thread-independent) populates on every sample while pending_tasks is 'n/a' on every sample — exactly what a thread-context failure of get_running_loop()/all_tasks() produces."
  falsification_test: "If the fix is correct, _pending_task_count(explicit_loop) returns a real int when called from a thread that is NOT running that loop (the production condition). The added unit test test_pending_task_count_returns_int_for_explicit_loop_with_tasks does exactly this (counts a task on a loop from the synchronous test thread) and passes."
  fix_rationale: "asyncio.all_tasks(loop) only reads the loop's internal task weakset; it does not require the loop to be running on the calling thread. Passing the kernel loop explicitly (captured via set_loop(node.kernel.loop)) removes the dependency on get_running_loop() entirely, so the probe works from the tokio worker thread. This addresses the root cause (wrong loop-discovery mechanism for the callback's thread context), not a symptom."
  blind_spots: "Not yet observed on a LIVE dYdX run that the line now prints a real integer end-to-end (unit tests prove the mechanism but not the live wiring under the real LiveTimer). The user reproduction in the checkpoint validates this. Also: asyncio.all_tasks reading the loop's task set concurrently from a non-loop thread is a sampling read of a WeakSet — fine for a diagnostic count, but a torn read could momentarily under/over-count by a few; acceptable for trend evidence."
tdd_checkpoint: null

## Symptoms
<!-- Written during gathering, then immutable -->

expected: dYdX recorder runs continuously, recording market data to a Nautilus ParquetDataCatalog, with stable memory usage, and shuts down cleanly within a reasonable time on Ctrl+C (SIGINT)
actual: Memory usage grows gradually during normal operation (over minutes to hours of active recording), then escalates further/rapidly when Ctrl+C is pressed to stop the process — the shutdown hangs and memory keeps climbing until the system runs out of memory and crashes
errors: None observed/logged by the user; no tracebacks or warnings seen in terminal output prior to or during the crash. Logs have not yet been checked (journald/log files not yet inspected by user)
reproduction: Start the dYdX recorder, let it run for minutes to hours while actively recording (not idle), then attempt to stop it with Ctrl+C — the hang and memory flood occurs at/after this shutdown attempt
started: Always been flaky on shutdown — Ctrl+C has never reliably stopped this recorder cleanly (not a recent regression from commit 9b8a5a1 "fix(07): dydx config-reload crash + stale threshold tuning")

## Eliminated
<!-- APPEND only - prevents re-investigating after /clear -->

- hypothesis: "Offloading _run_conversion off the asyncio event loop (via a registered ThreadPoolExecutor + run_in_executor, with on_stop no longer blocking on conversion) fully eliminates the Ctrl+C memory growth/hang."
  evidence: "Fix was implemented and unit-tested (89 tests pass, 3 new regression tests prove conversion runs off-thread), but human verification on a real dYdX run shows the Ctrl+C memory growth STILL occurs ('on ctrl c, memory starts to grow again, still'). The falsification_test recorded in cycle 1 explicitly predicted this outcome would mean the leak is not (solely) the conversion-blocking mechanism."
  timestamp: 2026-06-19T21:15:00Z

- hypothesis: "Halting the dYdX WS producer at the very start of on_stop (MEM-02 shutdown hook scheduling ws_client.disconnect() immediately, instead of waiting ~3-4s for the kernel's own delayed disconnect) eliminates or measurably reduces the Ctrl+C RAM escalation."
  evidence: "Human verification on a real dYdX run: user reports 'still grows the same way' — zero observable improvement, not just insufficient. Code-level re-check (see Evidence below) confirms the disconnect mechanism is NOT broken (cmd_tx/signal are genuinely shared via Arc, so the hook's clone-based disconnect() call does reach and stop the real handler). A working fix producing no visible change means the shutdown window was never the dominant contributor to what the user observes."
  timestamp: 2026-06-19T22:30:00Z

## Evidence
<!-- APPEND only - facts discovered during investigation -->

- timestamp: 2026-06-19T19:51:54Z
  checked: git status at session start
  found: scripts/common_recorder/strategy.py, scripts/dydx_recorder/config.py, and scripts/dydx_recorder/recorder.py are all currently modified (uncommitted) in the working tree. Most recent commit (9b8a5a1) was "fix(07): dydx config-reload crash + stale threshold tuning".
  implication: The bug may be in code paths already mid-edit; investigation must account for uncommitted changes (diff against HEAD) not just the committed version. The previous fix targeted config-reload crashes, which may be related to or distinct from this memory/shutdown issue.

- timestamp: 2026-06-19T20:10:00Z
  checked: uncommitted git diff of the three modified recorder files
  found: The local edits change the strategy's subscribe_order_book_deltas/_apply_param_change to pass managed=False (was default managed=True), and recorder.py changes instrument_depths from 50 to 0 (full depth). config.py adds DydxInstrumentEntry.depth=0 / product_type="linear" properties so reload diffs are stable.
  implication: managed=False stops the DataEngine from maintaining an in-memory cache OrderBook + subscribing _update_order_book. This is a partial mitigation already in flight, but does NOT remove the adapter's OWN per-instrument book used for quote synthesis (see below). The bug predates these edits (user says always flaky).

- timestamp: 2026-06-19T20:10:00Z
  checked: nautilus_trader/adapters/dydx/data.py _handle_orderbook_deltas + _handle_msg + _disconnect
  found: The dYdX data client maintains its OWN self._order_books[instrument_id] OrderBook and applies EVERY L2 delta to it whenever the instrument has an active QUOTE subscription (_active_quote_subs). The recorder subscribes quote ticks for every instrument, so this book runs regardless of the strategy-side managed flag. Books are cleared on _disconnect.
  implication: There are TWO book copies per instrument when managed=True (adapter quote-synth book + DataEngine cache book); managed=False removes the cache one. Neither is inherently unbounded though — dYdX emits BookAction::Delete for zero-size levels (crates/adapters/dydx/src/data.rs), so an L2 book stays bounded. Book maintenance alone does not explain unbounded growth.

- timestamp: 2026-06-19T20:10:00Z
  checked: logs/dydx_recorder.log (1060 lines, 5 runs incl. a ~4.7h run 16:33->21:13)
  found: ZERO occurrences of "Heartbeat", "Config reload", "Stale", "STOPPING", "STOPPED", "DISPOSED", "Disconnecting", "on_stop" across the ENTIRE log and all runs. The strategy's heartbeat (30s) and config-reload (30s) timers log via logger.info but NONE appear. Every run's log simply STOPS after the StreamingFeatherWriter "Created ... writer" lines with no shutdown sequence — consistent with the process being hard-killed (OOM) rather than shutting down cleanly.
  implication: (1) The strategy uses Python stdlib logging (logging.getLogger(__name__)) while the node uses use_pyo3=True logging; the strategy's logger.* output is NOT routed to the pyo3 log file, so strategy-side INFO/WARNING (heartbeat, stale, config-reload, restart-gap) is INVISIBLE in production logs — a serious observability gap. (2) No clean shutdown was ever recorded, matching "Ctrl+C hangs / never stops cleanly". Need to confirm whether timers actually fire (logging is just not visible) vs timers not firing at all.

- timestamp: 2026-06-19T20:30:00Z
  checked: crates/adapters/dydx/src/python/websocket.rs py_connect + handler loop; crates/adapters/dydx/src/websocket/client.rs channels; nautilus_trader/system/kernel.py stop_async ordering; nautilus_trader/live/data_engine.py queue bounds; scripts/common_recorder/strategy.py on_stop.
  found: (1) dYdX WS delivery has NO backpressure: the Rust handler task runs on a SEPARATE tokio runtime, drains an UNBOUNDED mpsc channel (out_tx/out_rx = tokio unbounded_channel), and for EVERY parsed datum calls loop_.call_soon_threadsafe(callback, ...) to schedule the Python _handle_msg on the asyncio loop. call_soon_threadsafe appends to the loop's UNBOUNDED _ready deque. If the single asyncio loop cannot drain _handle_msg as fast as dYdX produces L2 deltas, the _ready deque (and upstream mpsc) grows without bound -> RAM climbs. (2) kernel.stop_async() order is: self._trader.stop() [fires strategy on_stop()] FIRST, THEN _stop_clients()/_disconnect_clients(). So on_stop() runs BEFORE the dYdX WS producer is stopped. (3) The shared strategy on_stop() calls _run_conversion() SYNCHRONOUSLY on the event loop: it opens a ParquetDataCatalog and reads+converts every finalized feather file. While that blocking conversion runs, the dYdX Rust WS task keeps calling call_soon_threadsafe -> the loop _ready deque floods -> "hang + memory balloon on Ctrl+C". (4) Per-delta Python cost is amplified because the recorder subscribes quote ticks per instrument, so the dYdX adapter applies every L2 delta to its own _order_books book for quote synthesis (_handle_orderbook_deltas), on top of the strategy/writer path.
  implication: Originally treated as ROOT CAUSE for the Ctrl+C symptom, but human verification (see below) disproved this as the sole/complete cause — fix based on this evidence reduced but did not eliminate the shutdown memory growth.

- timestamp: 2026-06-19T21:15:00Z
  checked: human verification of cycle-1 fix on a real dYdX run
  found: User reports "on ctrl c, memory starts to grow again, still" — after the conversion-offload fix (executor-based on_stop), Ctrl+C still triggers memory growth.
  implication: The on_stop._run_conversion blocking call was not the sole cause of the Ctrl+C memory escalation, or the fix did not take effect in the run tested. Must re-walk the shutdown path for other blocking work or a slow/delayed WS teardown window, and confirm the fix is actually active in the tested process before assuming the mechanism is wrong.

- timestamp: 2026-06-19T22:00:00Z
  checked: (cycle 2) confirmed cycle-1 fix is LIVE and correctly wired — scripts/dydx_recorder/recorder.py:165-169 (ThreadPoolExecutor created + strategy.register_executor(node.kernel.loop, executor)); nautilus_trader/common/actor.pyx:1081-1140 run_in_executor; scripts/common_recorder/strategy.py:1092-1129 on_stop.
  found: The wiring is correct and reached without a swallowed exception. register_executor sets self._executor = ActorExecutor(loop, executor); run_in_executor with a non-None _executor genuinely dispatches to executor.run_in_executor (off the loop thread). on_stop flushes funding on-loop then run_in_executor(self._run_conversion) — non-blocking. So the cycle-1 fix IS active in the tested run; the offload genuinely happens. The conversion was therefore NOT the (sole) cause — consistent with the human verification still showing growth.
  implication: Eliminates "fix didn't take effect" branch (a). The cycle-1 mechanism is real but addresses the wrong thing. Move to the shutdown-window mechanism (c).

- timestamp: 2026-06-19T22:00:00Z
  checked: (cycle 2) FULL shutdown ordering — nautilus_trader/system/kernel.py stop_async (1067-1106), _await_trader_residuals (1369-1375), _disconnect_clients (1289-1295); nautilus_trader/live/data_engine.py disconnect (144-155); nautilus_trader/live/data_client.py disconnect (544-558) + dydx _disconnect (nautilus_trader/adapters/dydx/data.py:192-220).
  found: stop_async order is (1) _trader.stop() -> on_stop() [now non-blocking]; (2) `await self._await_trader_residuals()` which is `await asyncio.sleep(timeout_post_stop)` (recorder.py sets timeout_post_stop=1.0); (3) _stop_clients(); (4) _disconnect_clients() -> data_engine.disconnect() -> client.disconnect() which only does `self._loop.create_task(_disconnect_with_cleanup())` — NON-BLOCKING, the WS teardown is merely SCHEDULED as a coroutine; (5) the dYdX _disconnect() coroutine itself begins with `await asyncio.sleep(1.0)` BEFORE `await self._ws_client.disconnect()`.
  implication: There is a multi-second window between SIGINT and the WS producer actually stopping: ~1.0s (post-stop residual sleep, during which the loop is ACTIVELY draining its _ready deque) + task-schedule latency + 1.0s (adapter _disconnect pre-delay) + up to 2.0s (Rust handler drain/abort, see below). The cycle-1 conversion offload does NOT touch this window at all. The loop is NOT blocked during most of it — it is busily servicing every queued WS callback.

- timestamp: 2026-06-19T22:00:00Z
  checked: (cycle 2) Rust WS producer path — crates/adapters/dydx/src/python/websocket.rs handler loop (210-303), py_disconnect (800-815); crates/adapters/dydx/src/websocket/client.rs disconnect (530-555).
  found: The Rust handler task runs on a SEPARATE tokio runtime (get_runtime().spawn) and, for EVERY parsed datum (`while let Some(msg) = rx.recv().await`), calls call_python_threadsafe(py, &call_soon, &callback, py_obj) i.e. loop.call_soon_threadsafe(_handle_msg, capsule). There is NO subscription-state gate and NO backpressure on this producer — it keeps scheduling Python callbacks onto the loop's UNBOUNDED _ready deque until rx is closed. rx only closes when client.disconnect() completes, and disconnect() awaits the handler task with a 2.0s timeout before aborting (client.rs:544). So from SIGINT the producer keeps flooding call_soon_threadsafe for the entire ~1.0s + ~1.0s + up to 2.0s window above.
  implication: ROOT CAUSE of the Ctrl+C escalation is this teardown WINDOW, not loop-blocking. During the ~1.0s post-stop sleep the loop runs every queued _handle_msg -> applies L2 deltas to the adapter book + msgbus.send to DataEngine.process. Full-depth (depth=0) L2 across all instruments is high-volume; the loop cannot keep up, so the bounded _data_queue fills and ThrottledEnqueuer.enqueue (nautilus_trader/live/enqueue.py:95-117) starts `self._loop.create_task(self._queue.put(msg))` for EVERY further datum — unbounded Task creation -> unbounded RAM. The conversion-offload fix could never have fixed this because the flood is independent of whether on_stop blocks.

- timestamp: 2026-06-19T22:00:00Z
  checked: (cycle 2) ThrottledEnqueuer — nautilus_trader/live/enqueue.py:95-157 and its use in nautilus_trader/live/data_engine.py:106 (process -> _data_enqueuer.enqueue at 324-343).
  found: enqueue() puts onto a BOUNDED asyncio.Queue (maxsize=config.qsize, default 100_000) when there is room; once full it does `self._loop.create_task(self._queue.put(msg))` and tracks the task in a WeakSet `_pending_tasks`. Pending put() tasks are only cancelled on graceful client shutdown (cancel_pending_tasks). During the SIGINT window the producer floods FASTER than the consumer drains, so the queue saturates and unbounded create_task(put) coroutines accumulate — each holding a Data object reference — which is the memory escalation. This is ALSO the mechanism behind the slower steady-state growth the user saw "over minutes to hours" whenever the loop briefly falls behind.
  implication: The durable fix must STOP the dYdX WS producer from flooding the loop BEFORE/at the start of shutdown (and ideally apply backpressure during normal operation), rather than relying on draining/offloading. Since nautilus_trader/ is untouchable, the recorder must proactively disconnect the data client (or otherwise halt WS message scheduling) at the very start of its own shutdown, ahead of the kernel's delayed teardown — e.g. install a SIGINT handler / on_stop hook that triggers the WS disconnect immediately, OR reduce the teardown window. Need to identify a recorder-side lever that does not edit core.

- timestamp: 2026-06-19T22:30:00Z
  checked: (cycle 3) re-verified the MEM-02 shutdown-hook wiring (scripts/dydx_recorder/recorder.py _shutdown_hook, scripts/common_recorder/strategy.py on_stop) and the dYdX disconnect mechanism's Clone semantics (crates/adapters/dydx/src/websocket/client.rs DydxWebSocketClient::clone + disconnect, crates/adapters/dydx/src/python/websocket.rs py_disconnect which clones self before calling disconnect().await on the clone).
  found: The hook is correctly wired (attribute name `_ws_client` matches nautilus_trader/adapters/dydx/data.py; `data_client` is captured right after node.build(), before node.run()). `py_disconnect` operates on a CLONE of the client (required for the 'static future), and Clone sets `out_rx: None` / `handler_task: None` ("cannot clone receiver/task handle") — BUT `cmd_tx` (Arc<RwLock<UnboundedSender>>), `signal` (Arc<AtomicBool>), and `connection_mode` (Arc<ArcSwap<AtomicU8>>) are all genuinely shared via Arc, not deep-copied. `disconnect()` sends `HandlerCommand::Disconnect` via the shared cmd_tx and sets the shared `signal`; the real running handler's `tokio::select!` loop (handler.rs run()) checks `cmd_rx.recv()` each iteration and `handle_command` returns true on Disconnect, breaking the loop — this works even when called from a clone, because only the task-await/abort confirmation step (lines using `self.handler_task.take()`) is skipped on a clone, not the actual stop signaling.
  implication: The hook's disconnect call is NOT a no-op — it should genuinely cause the real handler to stop noticeably sooner than waiting for the kernel's ~3-4s delayed path (modulo tokio::select! fairness under high raw_rx volume, which is a minor, bounded delay, not multi-second). Combined with the human verification showing literally NO change, this points away from "the fix is broken" and toward "the shutdown window was a minor/non-dominant contributor" — the bulk of the growth most likely happens during normal steady-state operation (consistent with the user's original symptom report: growth over minutes-to-hours BEFORE any Ctrl+C), and Ctrl+C is probably just when the user happens to check, not a distinct triggering mechanism.

- timestamp: 2026-06-19T23:05:00Z
  checked: (cycle 3) scripts/dydx_recorder/recorder.py:main launch path; scripts/common_recorder/strategy.py _subscribe_instrument (317-368) and on_quote_tick (929-938); whether quote ticks are native or synthesized on dYdX.
  found: Launch is `main()` -> TradingNode (no separate worker process — single PID). _subscribe_instrument subscribes trade ticks, quote ticks, L2 order-book deltas (depth from config = 0/full), bars, and (linear-only) mark/index/funding PER instrument. on_quote_tick does NO manual persistence but quotes AUTO-FLOW to the StreamingFeatherWriter via the kernel's '*' msgbus subscription, so quotes ARE being written to parquet. CRITICAL: on dYdX, quote ticks are NOT a native exchange feed — the adapter SYNTHESIZES them by maintaining its own self._order_books[instrument_id] and applying every L2 delta (adapters/dydx/data.py _handle_orderbook_deltas, prior evidence at 22:10/line 53). The raw L2 deltas are ALSO recorded independently.
  implication: The per-instrument quote-tick subscription is the single largest controllable per-message cost AND its output (best bid/ask) is fully reconstructible offline from the L2 deltas already in the catalog. This makes "drop quote subscription on dYdX" a high-value lever: it removes the adapter's redundant per-delta book maintenance + the extra QuoteTick object/msgbus/write path per delta, with ZERO loss of recoverable information for the user's stated Parquet-recording goal. HELD pending runtime confirmation (per cycle-3 directive: no third static-only fix). The checkpoint asks the user to confirm whether growth is steady-state (which would make per-message cost the prime suspect and justify this lever) before implementing.

- timestamp: 2026-06-20T00:10:00Z
  checked: (cycle 3, post-user-decision) the strategy's logging path vs the node logging config, and whether the existing 30s heartbeat is visible. nautilus_trader/common/actor.pyx:191 (self.log = self._log, the pyo3-routed Nautilus Logger); scripts/common_recorder/strategy.py logger = logging.getLogger(__name__) (stdlib) + _heartbeat logging.info "Heartbeat: N active streams"; recorder.py LoggingConfig(use_pyo3=True).
  found: CONFIRMS the cycle-1 observability gap with the exact mechanism. The strategy emits ALL its INFO/WARNING (heartbeat, stale, restart-gap, hot-reload) via the STDLIB logger, which the pyo3 logging backend does NOT capture — so logs/dydx_recorder.log has ZERO heartbeat lines across all 5 historical runs (prior evidence 20:10). This is the ROOT reason the user has never been able to supply RSS-over-time numbers: there was no in-process probe whose output actually reaches the log file. self.log (the Cython Logger) IS pyo3-routed and visible, but it is immutable (attributes read-only — confirmed by a failed mocker.spy/setattr in tests).
  implication: Before ANY memory fix, the recorder needs an in-process memory probe that lands in the visible pyo3 log. Implemented MEM-03: _heartbeat now also calls self.log.info(self._mem_watch_message()), where _mem_watch_message samples (a) process RSS via /proc/self/status VmRSS (stdlib-only, Linux; returns None elsewhere) and (b) asyncio pending-task count via len(asyncio.all_tasks(get_running_loop())) (None when no loop). Output line: "mem-watch: rss_mb=<f> pending_tasks=<n> active_streams=<n>", every heartbeat_interval_seconds (default 30s). This is evidence-gathering instrumentation, NOT a fix — it directly tests the steady-state ThrottledEnqueuer-overflow hypothesis: that theory predicts rss_mb AND pending_tasks climb in lockstep during a NORMAL (no-Ctrl+C) run. Falsifiable: flat rss_mb during normal operation refutes steady-state growth and redirects to the shutdown window. Unit-tested (4 new tests, 42 total pass; ruff clean). NO core files touched — change is entirely in scripts/common_recorder/strategy.py + its test.

- timestamp: 2026-06-20T00:35:00Z
  checked: (cycle 3b) human-supplied mem-watch log output from a REAL normal run (2 instruments, BTC-USD-PERP/ETH-USD-PERP, 14 active streams), ~20:48-20:51 (about 3 minutes), NO Ctrl+C pressed during the sample.
  found: "rss_mb=367.3" at 20:48:31, climbing only to "rss_mb=367.5" by 20:51:31 — essentially FLAT over ~3 minutes. "pending_tasks=n/a" on EVERY sample (the new instrumentation has a bug — get_running_loop()/all_tasks() is not returning a value in this callback context). Also observed (unrelated to memory): a repeating "Stale stream: trade ETH-USD-PERP.DYDX idle Ns" warning incrementing each heartbeat (117s -> 207s), suggesting low/no trade activity on that instrument during the sample window, not necessarily a bug.
  implication: Directly weakens the cycle-3 steady-state-overflow-during-normal-operation hypothesis, at least at this scale (2 instruments) and duration (~3 min) — RSS shows no sign of climbing. Does NOT yet rule it out: the user's original crash reports involved minutes-to-HOURS of runtime and an unknown (possibly larger) instrument count from the full recorder.toml. The pending_tasks "n/a" bug means the single most direct test of the ThrottledEnqueuer unbounded-task hypothesis has STILL never produced a real data point — must be fixed. Highest-value next step: capture mem-watch lines bracketing an ACTUAL Ctrl+C attempt (the literal reported symptom), which has never been directly observed with numeric RSS data before — all prior verification was qualitative ("still grows the same way").

- timestamp: 2026-06-20T01:35:00Z
  checked: (cycle 3b/c) human-supplied log tail from a real Ctrl+C the user performed using the pre-pending_tasks-fix code (user's own words: "THIS IS BEFORE YOUR FIX that you just did" — i.e. ran with cycle-1+cycle-2 fixes already in place but before the pending_tasks/set_loop fix, so no mem-watch lines were captured in what was pasted).
  found: For the FIRST time in this entire debug session, the log shows a COMPLETE, CLEAN shutdown sequence reaching all the way to disposal: "...DISPOSED" (OrderEmulator), "Cleared actors", "...DISPOSED" (RecorderStrategy), "Cleared trading strategies", "Cleared execution algorithms", "DISPOSED" (trader), "Cache: Reset", "MessageBus: Closed message bus", "TradingNode: Shutting down executor" — followed by several "File .../quote_tick/ETH-USD-PERP.DYDX/<range>.parquet already exists, skipping write" lines (idempotent catalog conversion skip, from the offloaded _run_conversion). ALL 5 historical pre-fix runs (earlier evidence, 20:10) showed ZERO shutdown-sequence lines ever (consistent with a hard OOM kill). This run reached full DISPOSED.
  implication: Cycles 1+2 appear to have fixed the "process hangs forever / never disposes, hard-killed by OOM" failure mode — shutdown now completes. This does NOT by itself prove memory stayed bounded DURING that shutdown (no RSS data was captured in this excerpt — no mem-watch lines, and the user did not state whether they watched memory this time). The user's "still grows the same way" reports for cycles 1 and 2 PRE-DATE this particular clean-shutdown observation, so it's unclear whether: (a) this run's memory also grew/spiked but happened to still finish disposing before OOM (process completed despite a large but sub-fatal spike), or (b) this run's memory was actually fine and the user's "still grows" reports were about different runs/conditions. Needs direct clarification + a fresh run with the NOW-FIXED mem-watch active and RSS actively watched during the Ctrl+C, to resolve.

- timestamp: 2026-06-20T01:45:00Z
  checked: direct user follow-up question on whether RAM actually grew during the clean-disposing run above; and scripts/dydx_recorder/recorder.toml + strategy.py/config.py defaults for conversion_interval_minutes / rotation_interval_minutes.
  found: User confirmed "Yes, RAM still grew/spiked this time" — so memory growth on Ctrl+C is CONFIRMED still happening even on a run that now disposes cleanly (cycles 1+2 active). Separately: recorder.toml sets conversion_interval_minutes=60 (matches the documented default); rotation_interval_minutes defaults to 1440 (24h) and is not overridden in recorder.toml.
  implication: NEW UNTESTED HYPOTHESIS for cycle 4, not yet investigated: on_stop ALWAYS forces a conversion (via the cycle-1 executor offload) regardless of how long since the last periodic one. With a 60-minute periodic interval, a backlog of up to ~1 hour of finalized feather data (across ALL recorded types: order_book_deltas at full L2 depth, quote_ticks, trade_ticks, bars, funding, OI, x ALL instruments) could be sitting unconverted at any moment. If _run_conversion / convert_stream_to_data / ParquetDataCatalog's read+write path materializes data in memory (e.g. full pandas/pyarrow tables per file) rather than streaming, converting a large backlog in one shot at shutdown could itself be a substantial memory spike — INDEPENDENT of the WS-flood mechanism cycles 1+2 targeted. This would explain ALL evidence so far: (a) steady-state 3-min test was flat because no periodic conversion fired in that short window; (b) Ctrl+C consistently spikes because on_stop unconditionally forces a conversion of whatever backlog has accumulated; (c) cycle 1 (offload to a thread) didn't fix it because moving memory-hungry work off the loop doesn't shrink its memory footprint; (d) cycle 2 (early WS disconnect) didn't fix it because the spike isn't from the WS/queue at all. NOT YET CONFIRMED — needs code-level inspection of the actual conversion read/write memory profile, and/or a live test correlating backlog size at shutdown with spike magnitude (e.g. immediately-after-periodic-conversion Ctrl+C vs near-end-of-interval Ctrl+C).

- timestamp: 2026-06-20T01:20:00Z
  checked: (cycle 3b instrumentation fix) ROOT-CAUSED the pending_tasks='n/a' bug by tracing the heartbeat callback's actual execution thread. crates/common/src/live/timer.rs LiveTimer::start (195-244): the live timer is driven by get_runtime().spawn(async move {...}) on the GLOBAL Nautilus tokio runtime; inside the loop, for a TimeEventCallback::Python with no `sender` set, it calls `callback.call(event)` DIRECTLY on that tokio worker thread (acquiring the GIL), not via the asyncio event loop. nautilus_trader/common/component.pyx LiveClock.set_timer_ns calls live_clock_set_timer with NO TimeEventSender wired (the Cython path), so the on-worker-thread Python-callback branch (timer.rs:229-234) is taken, NOT the event-loop-registry sender branch (223-227). Also confirmed nautilus_trader/common/executor.py ActorExecutor stores the kernel loop as `_loop`, but Actor._executor is `cdef object` (actor.pxd:61) so it is NOT reachable via getattr across the Python boundary (verified: getattr returns None).
  found: The heartbeat (_heartbeat -> _mem_watch_message -> _pending_task_count) runs on a tokio worker thread that has NO asyncio loop bound to it. asyncio.get_running_loop() therefore raises RuntimeError, which `except RuntimeError: return None` swallowed -> pending_tasks=n/a on EVERY sample. rss_mb populated fine because /proc/self/status is thread-independent. This exactly matches the cycle-3b user log (rss_mb populated, pending_tasks=n/a on all 6 samples).
  implication: ROOT CAUSE of the instrumentation bug is a wrong loop-DISCOVERY mechanism for the callback's thread context — not a logic error in the count. FIX: _pending_task_count(loop) now takes an explicit loop; recorder.py injects node.kernel.loop via a new set_loop() (parallel to set_data_client/set_shutdown_hook); _mem_watch_message passes self._resolve_loop() in. asyncio.all_tasks(loop) only READS the loop's task registry (does not require the loop to run on the calling thread), so it returns a real integer from the tokio worker thread. Unit-tested: 48 tests pass (added test_pending_task_count_returns_int_for_explicit_loop_with_tasks — counts a task on a loop from a DIFFERENT thread, mirroring the tokio worker; degrade-to-None tests for no-loop/closed-loop; _resolve_loop precedence + executor-not-a-fallback; _mem_watch_message numeric-not-n/a end-to-end). ruff check + format clean. NO core file touched; changes are scripts/common_recorder/strategy.py + scripts/dydx_recorder/recorder.py + the test file. NO memory fix attempted (per cycle directive). The steady-state-vs-shutdown question remains OPEN, now awaiting the first quantitative RSS+pending_tasks trace bracketing a real Ctrl+C.

- timestamp: 2026-06-20T02:20:00Z
  checked: (cycle 4, user-directed: "look into concurrency and locks" + "the code we did not make but we are dependent on from nautilus") nautilus_trader/system/kernel.py _setup_loop/_loop_sig_handler (566-582); nautilus_trader/live/node.py run/run_async/stop/stop_async/dispose/_loop_sig_handler (283-494); nautilus_trader/live/data_engine.py _enqueue_sentinels/_on_stop/_run_data_queue (368-400+); nautilus_trader/live/enqueue.py ThrottledEnqueuer.enqueue (95-125). All core, read-only — confirms NONE of this can be edited per project constraints, only worked around recorder-side.
  found: (A) kernel.py's loop-level SIGINT handler is REPLACED with a no-op lambda (`add_signal_handler(SIGINT, lambda: None)`) the instant the FIRST SIGINT is processed (line 579) — by design, to prevent re-entrant shutdown, but it means every SUBSEQUENT Ctrl+C does nothing at all for as long as the loop is alive. (B) DataEngine._on_stop -> _enqueue_sentinels uses `queue.put_nowait(sentinel)` (not `await put`) scheduled via call_soon_threadsafe for cmd/req/res/data queues. put_nowait RAISES asyncio.QueueFull if the queue is at maxsize at that instant; this exception is NOT caught in _enqueue_sentinels, so it is swallowed by asyncio's default loop exception handler (logged, not crashed) and the sentinel for that queue is LOST. The data_queue is exactly the queue ThrottledEnqueuer (live/enqueue.py) reports as "at capacity" once qsize>=maxsize (config default 100_000) under sustained producer load — i.e. precisely the condition this recorder hits under the dYdX full-depth flood documented in cycles 2/3. (C) If the data_queue's sentinel is lost, `_run_data_queue()`'s consumer coroutine (awaiting `queue.get()` in a loop, looking for the sentinel to `break`) never terminates even after the queue drains to empty — it just blocks on the next `get()` forever. `TradingNode.run_async()`'s `await asyncio.gather(*tasks)` (node.py:377) therefore never completes, so `node.run()` (blocking via `loop.run_until_complete`, node.py:298) never RETURNS — the entire process hangs indefinitely at that exact point, with no further log output possible and (per A) no way to interrupt via any further SIGINT. (D) Separately confirmed via grep: ALL of strategy.py's recorder-authored gap-visibility diagnostics — REL-02 restart-gap WARNING (:592), stale-stream WARNING (:647-648), heartbeat (:655), hot-reload/config-diff logs (:883,933,948,953,986) — go through the STDLIB `logger` (module-level, :51), NOT the pyo3-routed `self.log` used by the cycle-3b mem-watch fix. Only WARNING+ stdlib records leak to stderr via Python's logging "handler of last resort" as bare unformatted lines (exactly matching the un-prefixed "Stale stream: ..." line the user pasted, vs the fully `[INFO] ...`-prefixed mem-watch lines that ARE pyo3-routed); INFO-level diagnostics (heartbeat, hot-reload) are below that default threshold and are lost entirely, never reaching logs/dydx_recorder.log.
  implication: (A)+(B)+(C) together form a CODE-CONFIRMED (not yet live-verified) explanation for "always been flaky" + "I CANNOT on multiple ctrl c even close the process AT ALL": a genuine, load-dependent RACE in nautilus_trader CORE (off-limits to edit) where the graceful-shutdown sentinel for the data queue can be silently dropped under the exact load condition this recorder already produces, after which the process is provably unable to exit AND provably unable to be interrupted further via SIGINT — it would need SIGKILL from another terminal to end, matching the reported crash behavior. This explains the "one clean DISPOSED capture" too (lucky timing: queue had room when the sentinel attempt ran). (D) directly explains why the user has never been able to see evidence of data gaps despite REL-02 already being built for that purpose — it was never actually reaching the log file. Fix path for cycle 4: (A)+(B)+(C) need a recorder-side WATCHDOG safety-valve (force-exit after a bounded grace period post-first-SIGINT) since the underlying race lives in core and cannot be patched directly; optionally also raise LiveDataEngineConfig.qsize (recorder-side config) to reduce (not eliminate) how often the data_queue is at maxsize at the exact stop instant. (D) needs every diagnostic logger.* call in strategy.py re-routed to self.log, mirroring the proven cycle-3b mem-watch fix.

- timestamp: 2026-06-20T03:30:00Z
  checked: (cycle 4 IMPLEMENTATION) shipped the two recorder-side workarounds for the code-confirmed core failures, with unit tests + ruff + mypy clean. Investigated the asyncio signal-handling model to pick a watchdog mechanism that genuinely works.
  found: |
    SIGNAL-MODEL INVESTIGATION (decides watchdog design): nautilus_trader/system/kernel.py
    _setup_loop runs at line 287 INSIDE kernel __init__ (i.e. during node.build(), BEFORE
    node.run()), and does signal.signal(SIGINT, SIG_DFL) then loop.add_signal_handler(SIGINT,...)
    for each signal. add_signal_handler installs a C-level handler writing to the loop self-pipe.
    CONCLUSION: a low-level signal.signal(SIGINT, my_handler) registered in main() AFTER build()
    would (a) be too late / fight asyncio's wakeup-fd machinery and (b) break the graceful SIGINT
    delivery. A SECOND asyncio-level handler is unreachable (kernel replaces it with no-op lambda
    after first SIGINT, kernel.py:579). THEREFORE the only mechanism that survives a wedged loop is
    a SEPARATE DAEMON THREAD armed by a recorder-side signal that fires BEFORE the wedge. The
    reliable arm-trigger is the existing on_stop shutdown hook: on_stop runs via _trader.stop() as
    the FIRST step of stop_async (node.py:493 stop()->stop_async), which executes BEFORE the
    data-engine queue gather that actually wedges (node.py:357-377). So arm() in the hook always
    runs, then the loop may wedge, then the thread force-exits. Confirmed end-to-end mechanism.

    SHIPPED (MEM-04, watchdog): new module scripts/common_recorder/shutdown_watchdog.py —
    ShutdownWatchdog(grace_seconds, exit_func=os._exit, monotonic, log). Daemon thread, idle until
    arm(); on arm() computes a monotonic deadline (idempotent — second Ctrl+C does NOT shorten it);
    if mark_completed() is not called within grace_seconds it calls os._exit(75) (distinct exit code
    so journald distinguishes a watchdog kill from a clean exit), after emitting a bare stderr line
    (the asyncio logger may itself be wedged). Pure should_force_exit(now) predicate factored out for
    deterministic testing. Wired in recorder.py: constructed with grace from new config knob, started
    before node.run(); the on_stop shutdown hook now calls watchdog.arm() FIRST (then the MEM-02 WS
    disconnect); the finally drains the executor, disposes the node, THEN mark_completed()+stop() —
    so the watchdog stays armed across the ENTIRE shutdown (loop wedge OR a hung dispose both still
    force-exit within grace). New config knob shutdown_watchdog_grace_seconds (PositiveInt, default
    20) on DydxRecorderConfig + recorder.toml + loader. 11 watchdog unit tests (arming/idempotency,
    deadline predicate, completion-disarms, real-thread timeout force-exit via injected exit_func,
    clean-completion no-exit, stop-without-arm, log-sink-before-exit, default-grace sanity) + 2 config
    knob tests (default + custom round-trip). All pass; ruff + mypy clean.

    SHIPPED (MEM-05, gap-visibility re-route): re-routed EVERY recorder-authored gap diagnostic in
    scripts/common_recorder/strategy.py from the stdlib `logger` (invisible under use_pyo3) to
    self.log (the pyo3-routed Nautilus Logger that lands in logs/dydx_recorder.log): restart-gap
    WARNING, stale-stream WARNING, heartbeat INFO, hot-add-threshold WARNING, all hot-reload
    INFO/ERROR (Hot-loading/Hot-added/venue-unknown/No-data-client), config-reload summary INFO,
    and the conversion/flush/shutdown-hook exception logs (now self.log.exception(msg, exc) — note
    the pyo3 logger requires an explicit exception arg and a PRE-FORMATTED string, unlike stdlib %s
    args, so all call sites were converted to f-strings). The per-message on_*_tick logger.debug
    "Received %s" traces were INTENTIONALLY LEFT on the stdlib logger (high-frequency, debug-level,
    not gap-visibility diagnostics). Because self.log is an immutable Cython attribute that cannot be
    patched/spied (confirmed: both setattr and patching Logger.warning raise), the WARNING/INFO
    CONTENT was made testable by extracting pure message-builder helpers (_restart_gap_message,
    _stale_stream_message, _hot_added_threshold_message) mirroring the cycle-3b _mem_watch_message
    pattern; tests spy the builders + assert the stdlib logger is now SILENT. Updated 6 existing
    tests that previously asserted on the stdlib logger; added 3 new content tests. Full recorder
    suite 118 passed; ruff + mypy clean.
  implication: |
    Both cycle-4 fixes are CODE-CONFIRMED and UNIT-TESTED but NOT YET LIVE-VERIFIED. Per the
    established norm (3 prior fixes passed units, failed live), a human checkpoint is REQUIRED before
    marking resolved. The watchdog's actual fire-against-a-wedged-loop cannot be reproduced in CI
    (the underlying sentinel race is a load-dependent core race), so live confirmation is the only
    proof for MEM-04. MEM-05 is lower-risk (pure logger-backend swap) but still needs a live run to
    confirm the lines appear in logs/dydx_recorder.log with the [INFO]/[WARNING] pyo3 prefix.
    KNOWN TRADEOFF (documented): a watchdog-forced os._exit skips the conversion-executor drain, so
    a forced exit may leave the most-recent feather tail unconverted until next restart — acceptable
    per D-02/D-03 (finalized files convert on next start; no data loss).

- timestamp: 2026-06-20T04:10:00Z
  checked: (cycle 4 PRE-CHECKPOINT code review + fix) read the actual cycle-4 diff in scripts/dydx_recorder/recorder.py's try/finally (238-264) and scripts/common_recorder/shutdown_watchdog.py to verify the watchdog disarm timing against the documented arm-at-first-Ctrl+C semantics.
  found: |
    BUG (cycle-4 fix introduced a NEW data-integrity risk): the `finally` block
    called `watchdog.mark_completed()` + `watchdog.stop()` only AFTER
    `conversion_executor.shutdown(wait=True)` and `node.dispose()`. The watchdog
    grace deadline (shutdown_watchdog_grace_seconds, default 20) is armed at the
    FIRST Ctrl+C via the on_stop shutdown hook (recorder.py:223 watchdog.arm()),
    and arm() is idempotent so a second Ctrl+C does NOT extend it
    (shutdown_watchdog.py:144-155). on_stop ALSO triggers the final
    ParquetDataCatalog conversion offloaded to conversion_executor. For a large
    backlog (conversion_interval_minutes=60, full L2 depth, multiple instruments)
    that conversion can legitimately take LONGER than 20s — so a perfectly healthy
    but slow shutdown would let should_force_exit() pass its deadline and os._exit(75)
    fire MID-CONVERSION, truncating an in-progress parquet/feather write. A stale
    in-code comment (old recorder.py:250-259) actively DEFENDED the buggy ordering
    ("the watchdog stays ARMED across this drain + dispose ... covering the whole
    shutdown"), conflating the evidenced loop-wedge race with an unevidenced slow-
    drain/dispose hang and thereby justifying the data-integrity risk.
    FIX APPLIED: moved `watchdog.mark_completed()` + `watchdog.stop()` to be the
    FIRST statements in `finally`, before the executor drain and dispose. Rationale:
    reaching `finally` at all already proves `loop.run_until_complete()` returned —
    i.e. the data-queue sentinel-loss loop-wedge (the ONLY race this watchdog is
    evidenced to guard, findings A/B/C 2026-06-20T02:20:00Z) did NOT happen — so the
    watchdog has done its job and must stand down. This narrowly scopes the watchdog
    to the loop-wedge race and removes the slow-conversion truncation risk WITHOUT
    expanding scope to a hypothetical drain/dispose hang (unevidenced; covering it
    would reintroduce the exact force-kill-a-slow-operation problem). The defending
    comment was rewritten to state the new (correct) reasoning. Verified by
    temporarily reverting the order: the regression test fails on the old order and
    passes on the fix.
    REGRESSION TEST ADDED: tests/unit_tests/persistence/recorder/test_dydx_recorder_main.py
    (NEW) — mocks main()'s heavy collaborators onto a shared parent Mock so cross-
    object call ORDER is recorded in parent.mock_calls; asserts
    watchdog.mark_completed AND watchdog.stop both precede executor.shutdown and
    node.dispose, on BOTH the clean node.run()-returns path and the
    node.run()-raises (on_start failure / raise_exception=True) path.
  implication: |
    The cycle-4 watchdog fix no longer endangers a legitimately slow final
    conversion. Cycle 4 is now CODE-CONFIRMED, UNIT-TESTED (120 recorder tests
    incl. 2 new ordering regressions; ruff + mypy clean), and READY for the
    mandatory live human-verification checkpoint. The watchdog mechanism, the
    MEM-05 self.log re-routing, and the qsize knob are otherwise UNCHANGED — this
    was a narrowly-scoped ordering bugfix, not a redesign. Live verification must
    confirm BOTH that a wedged-loop Ctrl+C force-exits within grace (no kill -9) AND
    that a healthy slow shutdown is NOT force-killed, plus that gap diagnostics now
    land in logs/dydx_recorder.log with the pyo3 [INFO]/[WARNING] prefix.

- timestamp: 2026-06-20T05:30:00Z
  checked: (cycle 4c IMPLEMENTATION — durable watchdog record per new user guidance) extended the MEM-04 watchdog so a force-exit leaves a PERMANENT on-disk trace, not just an ephemeral stderr line. User guidance: "process just needs to be killed reliable, and instantly the missing data will be obivous afterwards if a somehow a log with the process killed could be saved permanently to show it" — i.e. reliability over speed, accept the data gap, but make it traceable after the fact.
  found: |
    SHIPPED (cycle 4c, additive — watchdog arming/deadline/force-exit mechanism and
    the 20s default grace UNCHANGED):
    - scripts/common_recorder/shutdown_watchdog.py: added an OPTIONAL `record_file`
      constructor param (str | Path | None). On force-exit the watcher thread now
      writes the SAME diagnostic message to BOTH sinks: the existing ephemeral
      stderr `log` callback AND a new durable on-disk record. Extracted
      `_force_exit_message()` so the line content is shared; added
      `_write_durable_record(message)` which builds a wall-clock ISO-8601 UTC
      timestamp (time.strftime + time.gmtime — NOT monotonic, which is useless for
      correlating with a catalog gap) and appends `"{timestamp} {message}\n"` to
      record_file via a BARE-STDLIB write: open(append) / write / flush / os.fsync /
      close. os.fsync forces the OS buffer to physical disk so the line survives an
      immediate os._exit. The whole write is wrapped in contextlib.suppress(Exception)
      (same guard pattern as the stderr sink) so an unwritable path / full disk can
      NEVER block the exit_func kill. It deliberately does NOT route through the
      pyo3/Nautilus logging pipeline — that pipeline may itself be wedged at force-exit
      (the original reason bare stderr was used), and uses a DEDICATED sibling file to
      avoid file-handle contention with the pyo3 writer's own log file. The message was
      also enriched with "The catalog likely has a data gap starting around this time."
    - scripts/dydx_recorder/recorder.py: derives the path from the EXISTING
      log_directory="logs" used by TradingNodeConfig -> logs/dydx_recorder_watchdog.log
      (dedicated sibling to logs/dydx_recorder.log which the pyo3 writer owns),
      mkdir(parents=True, exist_ok=True) on its parent, and injects it via the new
      record_file param. Path is NOT hardcoded inside the watchdog module (stays
      testable; module takes the path as a param).
    - tests/unit_tests/persistence/recorder/test_shutdown_watchdog.py: +3 tests —
      (1) force-exit writes a durable timestamped line (tmp_path file + injected
      exit_func so the test process survives; asserts "force-exit"/"data gap" content
      and an ISO-8601-Z timestamp prefix, file ends with newline); (2) two separate
      watchdog instances writing the same file APPEND (persist across restarts, 2 lines
      not 1 — no truncation); (3) an unwritable record path (a file masquerading as a
      directory -> open() raises NotADirectoryError) does NOT block exit_func (suppress
      guard works) and produces no file.
    VERIFICATION: full recorder suite 123 passed (was 120; +3 new). ruff check + ruff
    format clean on all changed files; mypy clean (3 source files, no issues). NO core
    files touched.
  implication: |
    The user's explicit ask is now satisfied at the code level: a watchdog force-exit
    leaves a PERMANENT, durable, fsync'd line in logs/dydx_recorder_watchdog.log that
    survives a fresh process restart, with a wall-clock timestamp to correlate against
    a catalog data gap — independent of (and more reliable than) the ephemeral stderr
    line, and independent of the possibly-wedged pyo3 pipeline. The arming/deadline/
    force-exit mechanism and the 20s default grace are UNCHANGED (additive feature
    only). STILL UNPROVEN (live-only, per the established 4-cycle norm): (a) a real
    force-exit actually lands the line in the file and it survives restart; (b) Ctrl+C
    on a genuinely wedged loop force-exits within grace without kill -9; (c) gap
    diagnostics appear in logs/dydx_recorder.log with the pyo3 [INFO]/[WARNING] prefix.
    These require the mandatory live human-verification checkpoint.

- timestamp: 2026-06-20T06:40:00Z
  checked: (cycle 5 — INDEPENDENT CONFIRMATION + IMPLEMENTATION) Re-verified the cycle-5 root-cause analysis against the actual core code before implementing, then shipped the recorder-side feather-deletion fix with unit tests + the full recorder suite + ruff + mypy.
  found: |
    ANALYSIS CONFIRMED (independent code read, NOT just trusting the directive):
    - scripts/common_recorder/strategy.py:1410-1429 `_convert_finalized_feather_files`
      calls `catalog._list_feather_data_files(...)` every pass and, for every file
      except `files[:-1]`'s last (the still-active one), UNCONDITIONALLY calls
      `_read_feather_file(feather_file.path)` (line 1420) which reads the ENTIRE
      feather into an in-memory pyarrow Table — BEFORE any skip can fire.
    - parquet.py:2602-2604: the "already exists, skipping write" `print` + bare
      `return` is INSIDE `_convert_feather_table_to_parquet`, reached only AFTER the
      read at line 1420 already materialized the table. So the skip spares the WRITE,
      never the READ — exactly as the directive claimed. The already-exists branch
      returns WITHOUT raising, so from the caller it is indistinguishable from a real
      successful write: both mean the data is durably in parquet.
    - parquet.py:2370-2382 `_get_directory_intervals` globs `*.parquet` ONLY — zero
      dependency on feather sources surviving; deleting them cannot break the
      non-disjoint-interval invariant.
    - parquet.py:2707-2719 `_read_feather_file` returns `None` on missing/corrupt
      (caller already `continue`s on None) — the correct guard for "data not durable,
      keep the source".
    - parquet.py:157 `catalog.fs` is `fsspec.AbstractFileSystem`; runtime-verified the
      local fs `.rm(path, recursive=False, maxdepth=None)` exists. No core edit needed.
    SHIPPED (MEM-06, recorder-side ONLY — scripts/common_recorder/strategy.py):
    in `_convert_finalized_feather_files`'s loop, after `_convert_feather_table_to_parquet`
    returns NORMALLY (incl. the already-exists/skip-write success), `catalog.fs.rm(
    feather_file.path)` deletes the now-durable source so it is never listed/re-read
    again. The rm is wrapped in try/except -> `self.log.exception(...)` (MEM-05 pyo3
    routing) so a deletion failure is visible but NEVER crashes the pass nor loses
    track of the already-converted data. A `None` read is `continue`d before convert
    (kept). A raising convert is swallowed by `_run_conversion`'s per-type try/except
    BEFORE the rm runs (kept). Extensive WHY-comment added documenting the mechanism.
    TESTS (tests/unit_tests/persistence/recorder/test_recorder_conversion.py): +6 new
    — (a) delete after successful convert; (b) delete even when parquet write SKIPPED
    (user's exact observed scenario); (c) NOT deleted when convert raises; (c') NOT
    deleted + never converted when read returns None; (d) the still-active most-recent
    file per directory is never read/converted/deleted (2 directories); (e) an rm
    exception does not crash the pass and later files still convert+delete. PLUS the
    existing real-filesystem restart-shaped test was extended to assert (on a REAL fs)
    the finalized feather file is gone from disk post-conversion while the active file
    remains. Deterministic FakeCatalog used for (a)-(e) to assert the deletion contract.
    VERIFICATION: test_recorder_conversion.py 15 passed; full recorder suite 129 passed
    (was 123; +6). ruff check + ruff format clean on both changed files. mypy: the
    changed source file reports only PRE-EXISTING project-convention `[no-untyped-def]`
    errors on unrelated methods (confirmed identical kind present at HEAD via git stash;
    MEM-06 added NO new function and NO new mypy error). NO core file touched.
  implication: |
    Cycle 5 is CODE-CONFIRMED and UNIT-TESTED but — per the established 4-cycle norm
    (4 prior fixes passed units; 2 initially failed live) — NOT yet live-verified. This
    is the FIRST cycle to attack the redundant-re-read mechanism, which uniquely
    explains why growth scaled with TOTAL UPTIME and survived cycles 1-4 (cycle 1 only
    moved this same read off-thread; it never shrank the read's footprint). Live
    verification is mandatory before declaring resolved: a real extended run across
    >=1 conversion cycle must confirm (i) RSS no longer climbs at conversion time and
    (ii) old feather files are actually removed from the streaming directory after a
    conversion cycle (e.g. via `ls`). If live RSS growth persists, the remaining
    suspect is the cycle-3 steady-state ThrottledEnqueuer overflow (never refuted at
    full scale), not this conversion path. Status -> awaiting_human_verify; checkpoint
    returned for the live ask.

## Resolution
<!-- OVERWRITE as understanding evolves -->

root_cause: |
  CYCLE 5 (CODE-CONFIRMED, awaiting live verification) — THE SURVIVING MEMORY
  GROWTH: `_convert_finalized_feather_files` (scripts/common_recorder/strategy.py,
  recorder-side, EDITABLE) re-reads the ENTIRE growing backlog of already-converted
  feather SOURCE files into in-memory pyarrow Tables on EVERY conversion pass
  (periodic every conversion_interval_minutes=60 AND every Ctrl+C/on_stop), because
  feather sources were NEVER deleted after conversion. `_read_feather_file`
  (strategy.py:1420) materializes each full file BEFORE the "already exists, skipping
  write" parquet skip can fire (that skip is INSIDE `_convert_feather_table_to_parquet`,
  parquet.py:2602-2604, AFTER the read), so the skip spared the WRITE but never the
  READ. Conversion RAM therefore scaled with TOTAL RECORDER UPTIME (more history =
  more redundant files re-read every cycle) — uniquely explaining why growth SURVIVED
  cycles 1-4 (cycle 1 only moved this same read off the loop thread; it never reduced
  the read's footprint) and matching the user's literal symptom (the SAME historical
  files, spanning hours back, reprocessed and printing "already exists, skipping
  write" at every shutdown). FIX (MEM-06): after a successful read+convert (incl. the
  already-exists/skip-write success), DELETE the feather source via `catalog.fs.rm(...)`
  so it is never listed/re-read again. Safe: `_get_directory_intervals` (parquet.py:
  2370) reads the PARQUET output dir only and never depends on feather sources; the
  loop's `files[:-1]` guarantees the still-active file is never a deletion candidate;
  a failed read (None) or raising convert keeps the source. Fully recorder-side; NO
  core file touched.

  --- CYCLE 4 (CODE-CONFIRMED, retained) — TWO distinct core-level root causes for
  the SHUTDOWN HANG + INVISIBLE GAPS, both in nautilus_trader/ (off-limits), worked
  around recorder-side: ---
  (A) THE UNKILLABLE HANG: data_engine.py _enqueue_sentinels uses put_nowait, which
  raises QueueFull (swallowed) when the data_queue is at maxsize at the stop instant
  — the exact full-queue condition this recorder produces under the dYdX full-depth
  L2 flood. Losing the data-queue sentinel means _run_data_queue never breaks ->
  run_async's gather never completes -> node.run() never returns (process wedged);
  and kernel.py:579 replaces the loop SIGINT handler with a no-op after the first
  Ctrl+C, so no further Ctrl+C can interrupt it -> "cannot close it AT ALL". This is
  a load-dependent RACE, explaining the always-flaky / occasionally-clean behavior.
  (B) INVISIBLE GAP-VISIBILITY: every recorder gap diagnostic used the stdlib logger,
  which the use_pyo3 backend does not capture -> data-hole warnings never reached the
  log file (the user's stated TOP PRIORITY was therefore unserved).
  FIX (A): recorder-side daemon-thread force-exit watchdog (MEM-04), armed at first
  Ctrl+C via on_stop hook, os._exit after a bounded grace if the node has not
  disposed — guarantees the process is ALWAYS terminable. FIX (B): re-route all gap
  diagnostics to self.log (MEM-05) so they land in logs/dydx_recorder.log.

  --- prior cycles (retained as context) ---
  Earlier leading theory (cycle 3, unconfirmed): a STEADY-STATE leak during NORMAL operation
  (matches the user's original report of growth "over minutes to hours" before any
  Ctrl+C). The single asyncio loop cannot fully drain the dYdX full-depth (depth=0)
  L2 delta rate across all subscribed instruments, amplified by the adapter applying
  every delta to its own quote-synthesis book (triggered by the recorder's per-
  instrument quote-tick subscription). ThrottledEnqueuer's bounded data queue
  periodically saturates and spawns unbounded create_task(put) coroutines during
  ordinary running, independent of shutdown. Ctrl+C is suspected to be incidental —
  the point where the user checks/intervenes — rather than a distinct trigger.

  CYCLE 2 (real mechanism, confirmed NOT the dominant cause by human verification):
  the dYdX WS teardown window (~3-4s of kernel.stop_async post-stop sleep + adapter
  pre-disconnect sleep + Rust handler drain timeout) lets the WS producer keep
  flooding call_soon_threadsafe before actually disconnecting. Fix (early WS
  disconnect from on_stop) is correctly wired and the disconnect mechanism is
  genuinely shared (Arc-based cmd_tx/signal), so it should work — but a real run
  showed ZERO observable improvement, meaning this window is not where most of the
  growth happens.

  CYCLE 1 (real mechanism, confirmed secondary): synchronous _run_conversion in
  on_stop blocked the loop, worsening any concurrent flood; offloading it was
  correct and is retained, but alone did not fix the Ctrl+C symptom either.
fix: |
  CYCLE 5 (CODE-CONFIRMED, unit-tested, NOT yet live-verified) — the SURVIVING
  memory-growth fix:
  (MEM-06) scripts/common_recorder/strategy.py `_convert_finalized_feather_files`:
      after each finalized feather file is successfully read AND converted (incl.
      the already-exists/skip-write case, which returns without raising — data is
      durably in parquet either way), DELETE the source via `catalog.fs.rm(
      feather_file.path)`, wrapped in try/except -> self.log.exception (MEM-05 pyo3)
      so a deletion failure is visible but never crashes the pass. A None read is
      `continue`d before convert (source kept); a raising convert is swallowed
      upstream BEFORE the rm runs (source kept). This stops the unbounded per-pass
      re-read of the already-converted backlog — the actual memory cost — so
      conversion RAM scales only with data since the last conversion, not total
      uptime. 6 new unit tests (delete-on-success; delete-on-skip-write [user's exact
      scenario]; no-delete-on-convert-raise; no-delete-on-None-read; active-file-never-
      touched; rm-failure-does-not-crash) + extended the real-fs restart-shaped test to
      assert on-disk deletion of the finalized file and survival of the active file.
      NO core file touched.

  --- CYCLE 4 (CODE-CONFIRMED, retained) — the shutdown-hang + gap-visibility fixes: ---
  (MEM-04) scripts/common_recorder/shutdown_watchdog.py (NEW) + recorder.py: a
      daemon-thread force-exit safety valve. Armed at the first Ctrl+C via the
      on_stop shutdown hook; calls os._exit(75) after a configurable grace period
      (shutdown_watchdog_grace_seconds, default 20) if the loop wedges (node.run()
      never returns) — guaranteeing the process is ALWAYS terminable even when the
      core data-queue sentinel race wedges the loop. New config knob on
      DydxRecorderConfig + recorder.toml + loader.
      ORDERING FIX (cycle-4 pre-checkpoint review, 2026-06-20T04:10:00Z): the
      `finally` block now DISARMS the watchdog (mark_completed + stop) as its FIRST
      action — before conversion_executor.shutdown(wait=True) and node.dispose() —
      because reaching `finally` already proves node.run() returned (the loop-wedge
      race, the only thing the watchdog guards, did not happen). The prior order
      (disarm AFTER the drain+dispose) could os._exit() MID-CONVERSION during a
      legitimately slow but healthy final conversion, truncating a parquet/feather
      write. Regression-locked by tests/.../test_dydx_recorder_main.py.
      DURABLE RECORD (cycle 4c, 2026-06-20T05:30:00Z — additive, per user guidance
      "a log with the process killed could be saved permanently"): the watchdog now
      takes an optional `record_file` path and, on force-exit, ALSO appends a
      wall-clock-timestamped line to it via a bare-stdlib durable write
      (open/write/flush/os.fsync/close) wrapped in the same suppress-Exception guard
      (a write failure never blocks os._exit). recorder.py derives the path from the
      existing log_directory="logs" as logs/dydx_recorder_watchdog.log — a DEDICATED
      sibling to the pyo3-owned dydx_recorder.log (no file-handle contention) — and
      injects it. This gives the operator a PERMANENT trace of the kill (and the data
      gap it implies) that survives a process restart, NOT routed through the
      possibly-wedged pyo3 pipeline. The arming/deadline/force-exit mechanism and the
      20s default grace are UNCHANGED.
  (MEM-05) scripts/common_recorder/strategy.py: re-routed every recorder gap
      diagnostic (restart-gap, stale-stream, heartbeat, hot-add-threshold, all
      hot-reload INFO/ERROR, config-reload summary, conversion/flush/hook exception
      logs) from the stdlib logger to self.log (pyo3) so they land in
      logs/dydx_recorder.log — serving the user's TOP PRIORITY ("visible where there
      are holes in the data"). Per-message debug "Received" traces intentionally left
      on stdlib (high-frequency, debug-level). Message-builder helpers extracted for
      testability (self.log is immutable/unspyable).

  RETAINED FROM PRIOR CYCLES (sound hardening, kept):
  (1) _run_conversion offloaded to a worker executor (never blocks the loop).
  (2) on_stop shutdown hook proactively disconnects the dYdX WS (now also arms the
      watchdog) before the kernel's own delayed teardown.
verification: |
  CYCLE 5 (MEM-06): unit-tested — test_recorder_conversion.py 15 passed (9 original
  + 6 new MEM-06); full recorder suite 129 passed (was 123; +6). ruff check + ruff
  format clean on the 2 changed files. mypy: only PRE-EXISTING project-convention
  [no-untyped-def] errors on unrelated methods (identical kind present at HEAD,
  confirmed via git stash; MEM-06 introduced NO new function and NO new mypy error).
  NO core file touched. AWAITING LIVE human verification — see checkpoint. Two things
  can only be confirmed on a real extended run across >=1 conversion cycle: (i) RSS no
  longer climbs at conversion time, and (ii) old feather source files are actually
  removed from the streaming directory after a conversion cycle (verify via `ls`).
  CYCLE 4c: unit-tested (123 recorder tests pass — incl. 11 original watchdog
  tests + 3 NEW durable-record tests [timestamped line written on force-exit;
  append-across-restarts; write-failure-does-not-block-exit], 2 config-knob tests,
  and the 2 finally-block ordering regression tests in test_dydx_recorder_main.py;
  ruff check + format clean; mypy clean — 3 source files, no issues). NO core files
  touched. AWAITING LIVE human verification — see checkpoint. Three things can only
  be confirmed on a real run: (a) a force-exit actually lands a PERMANENT line in
  logs/dydx_recorder_watchdog.log that survives a fresh restart; (b) the watchdog
  fires against a genuinely wedged loop within grace (no kill -9) — the underlying
  sentinel race is a load-dependent CORE race not reproducible in CI; (c) gap
  diagnostics appear in logs/dydx_recorder.log with the pyo3 [INFO]/[WARNING] prefix.
  CYCLE 4 (prior): unit-tested (120 recorder tests; ordering regression tests
  confirmed to FAIL against the pre-fix order and PASS against the fix).
  CYCLE 1: unit-tested, FAILED real-run human verification ("on ctrl c, memory
  starts to grow again, still").
  CYCLE 2: unit-tested, FAILED real-run human verification with ZERO observable
  change ("still grows the same way") despite the fix being correctly wired and the
  underlying disconnect mechanism confirmed genuinely effective at the code level.
  Two independent reasoned-from-code fixes failing live verification means cycle 3
  must lead with runtime evidence (RSS sampling during normal operation, task-count
  sampling) rather than a third static-analysis-only theory.
files_changed:
  - scripts/common_recorder/strategy.py            # CYCLE 5 MEM-06 feather-source deletion + MEM-05 self.log re-route + builders
  - tests/unit_tests/persistence/recorder/test_recorder_conversion.py  # CYCLE 5 MEM-06 tests (6 new + extended real-fs deletion assert)
  - scripts/common_recorder/shutdown_watchdog.py  # NEW (MEM-04 watchdog) + cycle-4c durable record_file
  - scripts/dydx_recorder/recorder.py              # MEM-04 watchdog wiring + cycle-4c record_file derivation
  - scripts/dydx_recorder/config.py                # MEM-04 grace-period config knob
  - scripts/dydx_recorder/recorder.toml            # MEM-04 grace-period default
  - tests/unit_tests/persistence/recorder/test_shutdown_watchdog.py  # NEW (MEM-04) + cycle-4c durable-record tests
  - tests/unit_tests/persistence/recorder/test_recorder_strategy.py  # MEM-05 tests
  - tests/unit_tests/persistence/recorder/test_recorder_hot_reload.py  # MEM-05 tests
  - tests/unit_tests/persistence/recorder/test_dydx_recorder_config.py  # MEM-04 knob tests
  - tests/unit_tests/persistence/recorder/test_dydx_recorder_main.py  # NEW (MEM-04 finally-block ordering regression)
