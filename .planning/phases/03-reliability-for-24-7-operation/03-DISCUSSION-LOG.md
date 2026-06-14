# Phase 3: Reliability for 24/7 Operation - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-06-14
**Phase:** 03-reliability-for-24-7-operation
**Areas discussed:** REL-02 tail handling on restart, permanent-stop edge case, gap visibility/logging, stale-docs update approach

---

## REL-02 tail handling on restart

| Option | Description | Selected |
|--------|-------------|----------|
| Delay is fine | Accept the active feather file's tail becomes parquet-visible on the next process start's first conversion cycle (each restart timestamps a new file, finalizing the previous one); `on_stop()` still flushes the funding writer and runs `_run_conversion()` for belt-and-suspenders | ✓ |
| Need immediate visibility at stop | Force-convert the still-active file's current snapshot at `on_stop` time | |

**User's choice:** Delay is fine — "as long as data is not lost and the data flushing cycle is kept it should be good."
**Notes:** User additionally wants visible evidence of gaps caused by restarts (see "Gap visibility" below) — this emerged directly from this discussion.

---

## Permanent-stop edge case

| Option | Description | Selected |
|--------|-------------|----------|
| Out of scope | 24/7 Restart=always means this doesn't happen in practice; defer a manual final-convert utility to a future OPS task if ever needed | ✓ |
| In scope — manual convert path | Add a documented manual step/script for operators to run before permanent decommissioning | |

**User's choice:** Out of scope.
**Notes:** None.

---

## Gap visibility / logging

| Option | Description | Selected |
|--------|-------------|----------|
| Passive | Gaps are visible by inspecting consecutive parquet interval boundaries per identifier; no new code | |
| Active | On `on_start`, compare last known `ts_init` per identifier against "now" and log a WARNING gap line; ties into REL-03's heartbeat last-seen mechanism | ✓ |

**User's choice:** Active gap-log line.
**Notes:** "yes gap log line then log that a gap happened so i later can get notified by my logs" — must be a real journald-visible log line for log-based alerting, not just an inferable artifact.

---

## Stale-docs update approach

| Option | Description | Selected |
|--------|-------------|----------|
| Amend directly | Claude rewrites the relevant sections of 03-RESEARCH.md and replans 03-01-PLAN.md using already-verified details (no new research agent) | ✓ |
| Re-run phase research | Spawn gsd-phase-researcher again from scratch | |

**User's choice:** Amend directly.
**Notes:** None.

---

## Claude's Discretion

- Exact gap-log WARNING threshold(s), per-data-type or uniform
- Whether the gap-log check lives in `on_start` directly or shares code with the REL-03 heartbeat timer
- Exact wording/format of the gap-log message
- Mechanical rewrite of 03-01-PLAN.md's `convert_stream_to_data` references to `_convert_finalized_feather_files` semantics

## Deferred Ideas

- Manual final-convert utility for permanent decommissioning — future OPS task if ever needed
- Shared rotation-constants dedup between `config.py: build_streaming_config` and `strategy.py: _persist_funding_rate` — deferred from prior debug session, still pending
