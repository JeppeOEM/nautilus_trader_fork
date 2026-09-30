# Epic 28 Context: Capture on a measured CPU budget

<!-- Generated from planning artifacts. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Make every capture collector cheaper on each measured Python cost and give it CPU priority over the box's batch services, so one collector process can carry 30+ instruments across several venues on a small VPS without `_second_loop` stalls. Stalls do not corrupt the archive any more, but they delay every live consumer (chart, rankings, alerts), suspend the crossed-book check and, on dYdX, lose the missed second's row. The original evidence was the nifelheim box (2 vCPU / 3.7 GB, dYdX collector at ~105 % CPU, wake-ups 2 to 21.5 s late). The 2026-09-30 rescope is authoritative. Production now runs Bybit BTC/ETH linear + spot plus Hyperliquid SOL, at 8.9 % and 1.5 % of one dev-box core. The target regime is 30+ instruments per venue, about 1,500 Bybit book messages/s, so fixes are measured on a scaled synthetic burst and do not wait for a VPS profile. The epic is Python-only by decision. A Rust capture binary is the recorded upgrade path above it, not scheduled work.

## Stories

- Story 28.1: Measure the capture hot path and give capture CPU priority
- Story 28.2: Remove the measured Python overhead and lower the hot-path baseline

## Requirements & Constraints

- **Measure before fixing (DATA-02).** No fix lands without a number that names its cost: the scaled `test_hotpath.py` burst, and the VPS profile when one exists. A fix that does not lower its number is not merged; it is recorded with its figure instead.
- **Hot-path budget (audit D-65).** Every change keeps `test_hotpath.py` allocations per message ≤ baseline and wall time ≤ baseline. For Epic 28 that means ≤ baseline, not the general 2× allowance, because these stories only remove cost. At the end of the epic, baselines are re-recorded lower on the same CPU the current baselines name, so later refactors cannot give the gain back.
- **No archive-visible behaviour change.** `kernel` schemas, row values and file names stay byte-identical, and the existing schema tests pass unchanged. Output still goes through `ParquetDataCatalog.write_data()`.
- **Queue semantics.** The ingest queue gets metrics but no drop policy (DATA-05). No error is filtered or muted.
- **No new dependency (NFR12).** `uvloop` is already pinned through `nautilus_trader`. `py-spy` is a host tool and never a project dependency.
- **Fork safety (FORK-01).** Never modify `nautilus_trader/` or `crates/`.
- **Other binding rules:** TEST-04 (warnings are not noise), DESIGN-01, MR4 (audit and documentation updated with every finding).
- **VPS work is deferred, never parked (OPS-01).** VPS steps (profiling, redeploy checks) are appended to `docs/DEPLOY_CHECKLIST.md` "Deferred operator actions", and the story still finalizes `done` through normal review. The VPS profile is a post-hoc check, not a gate.
- **Documentation obligations:** channel and key names of new metrics go in `docs/DATA_DICTIONARY.md`; audit rows go in `docs/DATA_INTEGRITY_AUDIT.md` (D-07 struck, D-65 updated, D-66 when the profile lands).

## Technical Decisions

- **Per-flush metrics.** Each flush reports the ingest queue's max depth since the last flush, the messages processed, max and p99 second-loop wake-up lag, and the wall time of the last `write_data`. They are published on the existing per-flush Redis channel, with no new endpoint.
- **CPU priority through relative shares, not caps.** Collectors get `cpu_shares: 1024` and batch services get 256. There is no `cpus:` hard cap, because a cap would starve capture in a burst. Collector `mem_limit` is evidence-based (observed + 50 %), with the evidence recorded in a compose comment.
- **Scale burst.** A second `scale` burst (30 instruments per venue, 200-level books) sits beside the existing one and has its own baseline, `tests/fixtures/hotpath_baseline_scale.json`. `make hotpath-baseline` records both. The first commit of the fix work records both baselines on unchanged code. Each later fix is its own commit, with before/after figures for both bursts in the message.
- **Fix order in 28.2:**
  1. **Columnar flush encoder.** One `pa.array` per column, replacing per-row dicts. It is registered through the same `register_arrow` call, with byte-identical Parquet proven by a round-trip test. Target: ≤ 20 ms for 30 × 60 rows.
  2. **Ingest hand-off.** A plain `await queue.get()` with a stop sentinel replaces the per-message `wait_for`. The yield-every-64 rule is kept. It is measured on a queued replay variant.
  3. **uvloop.** Selected through one helper in `capture/application`, with a fallback to the default loop and a startup log line naming the loop that runs.
  4. **Book swap to `nautilus_pyo3.OrderBook`.** Taken only if the Python book path costs ≥ 1 % of a dev-box core at 1,500 messages/s. Otherwise it is recorded as "not taken" with the figure.
- **Seams from Story 26.1.** `LiveBook` is the only module that names the concrete order-book class, and `ArchiveWriter` is the only module that names the batch encoder. Swaps must stay behind those seams and touch no caller.
- **Book-swap correctness.** If the swap is taken, venue hooks (dYdX per-level tagging, Bybit `u` canary, cross-check captures) stay correct and their DATA-04/DATA-08 tests stay unchanged. Snapshots from a 500-level book and a 20-level book must be identical. `capsule_to_data` stays only for message types `write_data` still needs as Cython objects; record which ones, per venue.
- **dYdX `[WS_RAW]` sink is out of scope.** It is already off by default (`ws_raw_sink`). The incident-scoped ring design stays OPEN in the audit.
- **Epic close-out:**
  - Audit D-65 gets before/after figures per venue for both bursts, plus a projected cost for one collector at 30 instruments on one VPS vCPU. The projection uses the profile's dev-box-to-VPS ratio when it exists; otherwise it uses "× 3, assumed", marked as such.
  - The DDD spine's Deferred section gains the "Capture in Rust" upgrade-path entry.
  - `platform/CLAUDE.md` "Adding a venue" gains a messages/s-per-instrument check against the scale burst.

## Cross-Story Dependencies

- 28.1 → 28.2. The flush-time metrics from 28.1 report the new encoder's wall time in 28.2.
- 28.2 does not wait for 28.1's VPS profile. It cites the profile if the profile exists; otherwise it says so in its `Auto Run Result` and the deferred entry stays.
- The epic relies on Story 26.1/26.2's `LiveBook` and `ArchiveWriter` seams. It is independent of Epic 27.
