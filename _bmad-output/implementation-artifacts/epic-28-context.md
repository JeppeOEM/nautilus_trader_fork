# Epic 28 Context: Capture on a measured CPU budget

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Make every collector process cheaper on each measured Python cost and give capture CPU priority over the box's batch services, so one collector carries 30+ instruments across several venues on a small box without `_second_loop` stalls. On the old 2 vCPU VPS the dYdX collector sat at ~105 % CPU and the second loop woke 2–21.5 s late about every 3 minutes. A late wake-up no longer breaks the archive, but it delays every live consumer (chart, rankings, alerts), pauses the crossed-book check and, on dYdX, leaves the missed second without a row. The current production fleet (Bybit BTC/ETH linear + spot, Hyperliquid SOL) is light: 8.9 % and 1.5 % of one dev-box core. So the epic targets the regime the operator is scaling to: about 1,500 book messages/s on Bybit alone at 30 instruments. The work is Python-only by decision. A Rust capture binary is the recorded upgrade path above this epic, not scheduled work.

## Stories

- Story 28.1: Measure the capture hot path and give capture CPU priority
- Story 28.2: Remove the measured Python overhead and lower the hot-path baseline

## Requirements & Constraints

- **Root cause before mitigation.** No fix lands without a measurement that names its cost. For 28.2 that measurement is the scaled hot-path burst. A fix that does not lower its number is not merged, and its figure is recorded.
- **Hot-path budget.** `tests/test_hotpath.py` gates every change: allocations must stay ≤ baseline and wall time ≤ baseline on the recording CPU. This epic only removes cost, so the 2× wall-time allowance does not apply. When the epic ends, the baselines are re-recorded lower, so later refactors cannot quietly give the gain back.
- **Archive unchanged.** Kernel schemas, row values and file names stay byte-identical, and the Parquet schema tests pass unchanged. Any new encoder must prove byte-identical output with a round-trip test against the existing encoder.
- **No data loss.** The ingest queue gets no drop policy. Every counter stays visible at flush time, never silent.
- **No new dependency.** `uvloop` is already pinned through `nautilus_trader`. `py-spy` is a host tool, never a project dependency.
- **Fork safety.** `nautilus_trader/` and `crates/` stay untouched.
- **Warnings are failures (TEST-04).** An abstraction is admitted only if it names the invariant it protects (DESIGN-01).
- **Docs travel in the same commit.** Docs, dockerfile `COPY` sets, compose `command:` lines and the Makefile test lists are updated in the commit that changes them.
- **Operator steps are deferred, never parked.** VPS steps (the py-spy profile, redeploy checks) go to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions" as an entry headed with the story key and commit. The story still finalizes `done`. The VPS profile is a post-hoc check, not a gate for 28.2.
- **Deliberate simplifications** are documented in code as `Known limit:` with the ceiling and the upgrade path.

## Technical Decisions

- **Flush metrics (28.1).** Each flush reports, per collector:
  - max ingest-queue depth since the last flush, sampled per `_process_data`;
  - messages processed;
  - max and p99 `_second_loop`/`_venue_second_loop` wake-up lag;
  - last `write_data` wall time.

  The same figures are published on the existing per-flush Redis channel, so no new endpoint is needed. Channel and key names are recorded in `docs/DATA_DICTIONARY.md`.
- **CPU priority (28.1).**
  - Collectors get `cpu_shares: 1024`; batch services (`ranking_engine`, `data_api`, `bot_tui`, `live-paper`, `dozzle`) get `256`.
  - No `cpus:` hard cap, because a cap would starve capture during a burst.
  - Each collector gets a `mem_limit` set from `docker stats` evidence plus 50 % headroom, with that evidence written in a compose comment.
- **Scale burst (28.2).** A second `scale` burst covers 30 instruments per venue with 200-level books. It has its own baseline, `tests/fixtures/hotpath_baseline_scale.json`, and `make hotpath-baseline` records both baselines. The first commit records both baselines on unchanged code. Each later fix is its own commit, and its message carries the before/after figures for both bursts.
- **Fix order in 28.2:**
  1. **Columnar flush encoder.** Replace the per-row dict encoder with one `pa.array` per column, registered through the same `register_arrow` call. Target: ≤ 20 ms for a 30 × 60 batch (today 291 ms).
  2. **Plain ingest hand-off.** Replace the per-message `asyncio.wait_for(queue.get(), 1.0)` with a plain `await get()` plus a stop sentinel from `stop()`. Keep the yield-every-64 rule. Measure it on a queued replay variant that drives `_on_data` + `_ingest_loop`.
  3. **uvloop.** Select it through one helper in `capture/application`, with a fallback to the default loop and a startup log line naming which loop runs.
  4. **Book path.** One number from the scale burst decides it: the Python book path's share of one core at 1,500 messages/s.
     - At ≥ 1 %: switch `LiveBook` to `nautilus_pyo3.OrderBook`, with one `apply_deltas` per message and depth-bounded `bids(20)`/`asks(20)` reads. Venue hooks and their DATA-04/DATA-08 tests stay unchanged, and a test proves a 500-level and a 20-level book give identical snapshots.
     - Below 1 %: record it as "not taken" with the figure.
- **Seams.** `LiveBook` is the only module allowed to name the concrete book class, and `ArchiveWriter` owns the batch encoder. Changing either touches no caller.
- **dYdX `[WS_RAW]` sink** is out of scope, because it is already off by default (`ws_raw_sink`). The incident-scoped ring design stays OPEN in the audit.
- **Closing records.**
  - Audit D-65 gets before/after figures per venue for both bursts, plus the projected cost of 30 instruments on one VPS vCPU. That projection uses the profile ratio if one exists, else "× 3, assumed".
  - The DDD spine's Deferred section gets a "Capture in Rust" entry (Go ruled out: no Nautilus bindings).
  - `platform/CLAUDE.md` "Adding a venue" gets a line: check a new venue's expected messages/s per instrument against the scale burst's per-message figure before deploying it.

## Cross-Story Dependencies

- 28.1 comes before 28.2. 28.1's flush metrics report the new encoder's wall time, and its deferred VPS profile (committed under `platform/.planning/debug/capture-profile-<date>/` and cited as audit D-66) is cited by 28.2 only if it exists.
- The epic builds on Epic 26's seams (`LiveBook`, `ArchiveWriter`) and is independent of Epic 27.
