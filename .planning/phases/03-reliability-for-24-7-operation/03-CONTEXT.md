# Phase 3: Reliability for 24/7 Operation - Context

**Gathered:** 2026-06-14
**Status:** Ready for replanning

<domain>
## Phase Boundary

The recorder survives unattended `Restart=always` operation: it flushes/converts buffered data on SIGTERM so a stop-then-start loses no recorded data, leans entirely on the Bybit adapter's built-in reconnect/resubscribe (no custom reconnect code), and surfaces stale streams and restart-induced gaps via logging.

</domain>

<decisions>
## Implementation Decisions

### Conversion approach is now Approach B (supersedes 03-RESEARCH.md's framing)
- **D-01 [informational]:** Persistence/conversion no longer relies on a trimming fix inside `nautilus_trader/persistence/catalog/parquet.py` (the old approach at commit `c98b1c0f80`, since reverted). `parquet.py` is unmodified. Conversion now goes through `RecorderStrategy._convert_finalized_feather_files` (added in commit `69219cca51`), which lists feather files per identifier via `catalog._list_feather_data_files(...)`, converts all-but-the-last file per identifier via unmodified `catalog._read_feather_file` + `catalog._convert_feather_table_to_parquet`, and skips the active/last file. Both the kernel `"*"` writer and the strategy-owned funding writer already share `rotation_interval_minutes` (default 1440, `SCHEDULED_DATES`, `rotation_time=00:00 UTC`) so "finalized" is consistent across types. 03-RESEARCH.md and 03-01-PLAN.md reference the old approach and must be amended to describe `_convert_finalized_feather_files` instead of `convert_stream_to_data`/trimming.

### REL-02 final-convert / restart tail behavior
- **D-02:** `StreamingFeatherWriter` stamps each file's name with a timestamp at creation time (`_create_writer`/`_create_identifier_writer`, `nautilus_trader/persistence/writer.py` ~line 424/463). Every process restart therefore creates a brand-new feather file per identifier, which immediately makes the PREVIOUS process's file "finalized" (no longer last). Consequence: `on_stop()`'s call to `_run_conversion()` (= `_convert_finalized_feather_files` per type) generally CANNOT convert the current session's still-active tail — but the NEXT process's first periodic conversion cycle will convert it automatically (the old file is now `files[:-1]`).
- **D-03 (decided):** This one-cycle delay in parquet-visibility after a restart is ACCEPTED as satisfying REL-02 ("no data loss on restart") — data is never lost, just not immediately parquet-visible at the moment of SIGTERM. `on_stop()` still calls `_run_conversion()` for belt-and-suspenders (catches any files that were already finalized mid-session by a `SCHEDULED_DATES` rotation but not yet converted by the periodic timer) and still flushes the strategy-owned funding writer. No mechanism is needed to force-convert the active file's current contents at stop time.
- **D-04 (decided):** The permanent-decommission edge case (recorder stopped for good, last file's tail never finalized/converted because no "next" process ever creates a newer file) is OUT OF SCOPE for this phase. No manual final-convert script/utility is built now; defer to a future OPS task if it's ever actually needed.

### Gap visibility (NEW requirement, not in original REL-02/03 wording but emerged from this discussion)
- **D-05:** Each restart already produces a structurally-visible gap in the catalog: the old file's interval `(start_A, end_A)` and the new file's interval `(start_B, end_B)` are separate parquet files for the same identifier, with `start_B > end_A` reflecting the downtime. This is sufficient for ad-hoc inspection but not for operational alerting.
- **D-06 (decided):** ADD an ACTIVE gap-log line. On `on_start`, for each identifier/data type, compare the last known `ts_init` recorded in the catalog (from the previous session, i.e. the now-finalized prior file) against "now" (`clock.timestamp_ns()` at startup). If the gap exceeds some threshold, log a WARNING such as `"Resuming after gap of Xs (last data: <ts>) for <identifier>"` so operators can grep journald / set up log-based alerting for restart-induced gaps. This ties into the same per-stream "last seen" mechanism already planned for REL-03's heartbeat (03-02-PLAN.md) — reuse/extend that mechanism rather than building a separate one. Exact threshold and per-type granularity are Claude's discretion during planning, but it must be a real log line emitted at startup (not just passively inferable from parquet intervals).

### Replanning approach
- **D-07 (decided):** No new research agent run. Amend `03-RESEARCH.md` and `03-01-PLAN.md` directly to reflect D-01 through D-06 — the verified details (file-naming/timestamp-on-restart behavior, `_convert_finalized_feather_files`, `_list_feather_data_files`, the new gap-log-line requirement) are already established in this discussion and in `.planning/debug/resolved/non-disjoint-intervals.md`.

### Claude's Discretion
- Exact gap-log WARNING threshold(s) and whether they're per-data-type or uniform.
- Whether the gap-log check lives in `on_start` directly or as part of a shared helper also used by the REL-03 heartbeat timer.
- Exact wording/format of the gap-log message.
- How 03-01-PLAN.md's task/acceptance-criteria references to `convert_stream_to_data` get updated to `_convert_finalized_feather_files` (mechanical rewrite, single source of truth is `strategy.py` as currently committed).

</decisions>

<specifics>
## Specific Ideas

- User: "as long as data is not lost and the data flushing cycle is kept it should be good... there will be one file that just have a few gaps in the data (there should be some way of seeing later in the file that something happened that left gaps) like a system reboot or something" — drove D-05/D-06.
- User wants to be "notified by my logs" of restart-induced gaps — confirms D-06 must be an active log line (journald-visible), not just a passive artifact.

</specifics>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before replanning or implementing.**

### Conversion approach (Approach B, supersedes old research)
- `.planning/debug/resolved/non-disjoint-intervals.md` — Resolution section describes `_convert_finalized_feather_files`, the shared `rotation_interval_minutes` config knob, and why `parquet.py` stays unmodified
- `scripts/bybit_recorder/strategy.py` (as currently committed, `69219cca51`) — `_convert_stream`, `_convert_finalized_feather_files`, `_persist_funding_rate`
- `scripts/bybit_recorder/config.py` — `build_streaming_config` (kernel `"*"` writer rotation config), `RecorderConfig.rotation_interval_minutes`
- `nautilus_trader/persistence/writer.py` — `_create_writer`/`_create_identifier_writer` (~lines 407-466): confirms per-process-start timestamped filenames, the mechanism behind D-02

### Existing plans needing amendment
- `.planning/phases/03-reliability-for-24-7-operation/03-RESEARCH.md` — references the old trimming fix (`c98b1c0f80`); needs Pattern 1 / `on_stop` description updated to Approach B + D-03/D-06
- `.planning/phases/03-reliability-for-24-7-operation/03-01-PLAN.md` — Task 1/Task 2 acceptance criteria reference `convert_stream_to_data` (1 occurrence expected) and the trimming fix; needs updating to `_convert_finalized_feather_files` semantics, plus a new task/sub-task for D-06 gap-log line
- `.planning/phases/03-reliability-for-24-7-operation/03-02-PLAN.md` — REL-03 heartbeat plan; D-06's gap-log mechanism should reuse/extend its `_last_seen` dict — check for overlap when replanning 03-01 vs 03-02

### Project-level
- `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/ROADMAP.md` (Phase 3 section, lines 63-75) — REL-02/03/04 wording and success criteria (unchanged by this context)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `RecorderStrategy._convert_finalized_feather_files` (already implemented) — the conversion primitive both the periodic timer and `on_stop` should call via `_run_conversion()`
- `catalog._list_feather_data_files(kind="live", instance_id=..., data_cls=...)` — already used to enumerate per-identifier feather files; the gap-log check (D-06) can use the same listing + `catalog.query`/`catalog.trade_ticks` etc. to find the last converted `ts_init` per identifier

### Established Patterns
- Per-type `try/except Exception: logger.exception(...)` swallow in the conversion loop (must be preserved for `_run_conversion`)
- `clock.set_timer` for periodic work (existing `_convert_stream` timer; REL-03 heartbeat timer planned similarly)

### Integration Points
- `on_start` is where instrument validation + subscriptions + timers are set up (D-05/D-06 from Phase 1/2) — the new gap-log check (D-06) belongs here, run once at startup before/alongside the heartbeat timer setup

</code_context>

<deferred>
## Deferred Ideas

- Manual final-convert utility for permanent decommissioning (D-04) — defer to a future OPS task if ever needed.
- Shared rotation-constants dedup between `config.py: build_streaming_config` and `strategy.py: _persist_funding_rate` (rotation_mode=SCHEDULED_DATES, rotation_time=00:00, rotation_timezone="UTC") — deferred from the prior debug session, unrelated to this phase's scope but still pending "next time we work."

</deferred>

---

*Phase: 03-reliability-for-24-7-operation*
*Context gathered: 2026-06-14*
